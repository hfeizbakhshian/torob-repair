"""Expense reports, receipt review, private evidence files and case completion.

Two rules shape this module. First, the customer's verdict always belongs to one exact
expense version: any edit of amounts, lines or files creates a new version and does not
carry the previous confirmation over. Second, confirming a receipt is not permission to
raise the agreed amount — extra cost has to go through an agreement change first.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.config import settings
from app.domain import audit
from app.domain.agreements import active_agreement
from app.domain.errors import forbidden, invalid_state, not_found, validation_error
from app.domain.money import LineItem, compute_totals, require_unique_ids
from app.models import (
    Attachment,
    Completion,
    Expense,
    ExpenseVersion,
    ReceiptReview,
    Request,
    Selection,
)
from app.models.enums import Party, ReceiptStatus, RequestStatus, SelectionStatus
from app.policy import Policy
from app.repository.locking import check_revision, lock_row

_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


def sniff_content_type(data: bytes) -> str | None:
    """Decide the type from the bytes, not from the declared name or header."""
    for magic, content_type in _MAGIC.items():
        if data.startswith(magic):
            return content_type
    return None


async def _load_collaboration(
    session: AsyncSession, selection_id: uuid.UUID
) -> tuple[Selection, Request]:
    selection = await lock_row(session, Selection, selection_id)
    request = await lock_row(session, Request, selection.request_id)
    return selection, request


async def get_or_create_expense(session: AsyncSession, selection_id: uuid.UUID) -> Expense:
    expense = (
        await session.execute(
            select(Expense).where(Expense.selection_id == selection_id).with_for_update()
        )
    ).scalar_one_or_none()
    if expense is None:
        expense = Expense(selection_id=selection_id)
        session.add(expense)
        await session.flush()
    return expense


async def submit_expense_version(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    specialist_id: uuid.UUID,
    lines: list[LineItem],
    source_text: str | None,
    actual_minutes: int | None,
    extracted_by_ai: bool,
    expected_revision: int | None = None,
) -> ExpenseVersion:
    """Record a new expense version. Any previous confirmation stops applying to it."""
    selection, request = await _load_collaboration(session, selection_id)
    if selection.specialist_id != specialist_id:
        raise forbidden("ثبت مخارج بر عهدهٔ متخصص منتخب است.")
    if selection.status is not SelectionStatus.accepted:
        raise invalid_state(
            "ثبت مخارج فقط در همکاری جاری ممکن است؛ همکاری پایان‌یافته مدرک تازه نمی‌پذیرد."
        )

    require_unique_ids(lines)
    expense = await get_or_create_expense(session, selection_id)
    check_revision(expense, expected_revision)

    totals = compute_totals(lines)
    version_number = (
        await session.scalar(
            select(ExpenseVersion.version_number)
            .where(ExpenseVersion.expense_id == expense.id)
            .order_by(ExpenseVersion.version_number.desc())
            .limit(1)
        )
        or 0
    ) + 1

    version = ExpenseVersion(
        expense_id=expense.id,
        version_number=version_number,
        source_text=source_text,
        lines=[line.model_dump(mode="json", by_alias=True) for line in lines],
        actual_minutes=actual_minutes,
        total_toman=totals.total_toman,
        specialist_payable_toman=totals.specialist_payable_toman,
        extracted_by_ai=extracted_by_ai,
        # AI-extracted lines wait for the specialist's own review before the customer
        # is ever asked to judge them.
        specialist_reviewed_at=None if extracted_by_ai else now(),
        submitted_at=None if extracted_by_ai else now(),
    )
    session.add(version)
    await session.flush()
    expense.current_version_id = version.id
    expense.bump()

    session.add(ReceiptReview(expense_version_id=version.id, status=ReceiptStatus.pending))

    await audit.record(
        session,
        "expense_version_submitted",
        request_id=request.id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=version.id,
        data={
            "versionNumber": version_number,
            "totalToman": totals.total_toman,
            "extractedByAi": extracted_by_ai,
        },
    )
    return version


async def specialist_review_extraction(
    session: AsyncSession,
    *,
    expense_version_id: uuid.UUID,
    specialist_id: uuid.UUID,
    lines: list[LineItem] | None,
) -> ExpenseVersion:
    """The specialist checks what the model extracted before the customer sees it."""
    version = await lock_row(session, ExpenseVersion, expense_version_id)
    expense = await lock_row(session, Expense, version.expense_id)
    selection, request = await _load_collaboration(session, expense.selection_id)
    if selection.specialist_id != specialist_id:
        raise forbidden("بازبینی اقلام بر عهدهٔ متخصص منتخب است.")
    if version.submitted_at is not None:
        raise invalid_state("این نسخه قبلاً برای مشتری ارسال شده است.")

    if lines is not None:
        require_unique_ids(lines)
        totals = compute_totals(lines)
        version.lines = [line.model_dump(mode="json", by_alias=True) for line in lines]
        version.total_toman = totals.total_toman
        version.specialist_payable_toman = totals.specialist_payable_toman
    version.specialist_reviewed_at = now()
    version.submitted_at = now()

    await audit.record(
        session,
        "expense_extraction_reviewed",
        request_id=request.id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=version.id,
    )
    return version


async def review_receipt(
    session: AsyncSession,
    *,
    expense_version_id: uuid.UUID,
    customer_id: uuid.UUID,
    approve: bool,
    reason: str | None,
    satisfaction_score: int | None = None,
    satisfaction_note: str | None = None,
    reference_consent: bool = False,
) -> ReceiptReview:
    """The customer's explicit verdict on this exact version, item list and files.

    Approving an invoice the specialist marked as final also closes the case: to the
    customer that is one decision, and the confirmation still lands on this exact version.
    Rejection opens the correction or dispute path; it never forces a confirmation, and an
    AI ruling later on cannot turn a rejected receipt into a confirmed one.
    """
    version = await lock_row(session, ExpenseVersion, expense_version_id)
    expense = await lock_row(session, Expense, version.expense_id)
    _, request = await _load_collaboration(session, expense.selection_id)
    if request.customer_id != customer_id:
        raise forbidden("تأیید رسید بر عهدهٔ مشتری همین پرونده است.")
    if version.submitted_at is None:
        raise invalid_state("این نسخه هنوز برای شما ارسال نشده است.")
    if expense.current_version_id != version.id:
        raise invalid_state(
            "این نسخهٔ رسید ویرایش شده است؛ نسخهٔ تازه را ببینید و دربارهٔ همان تصمیم بگیرید."
        )

    review = (
        await session.execute(
            select(ReceiptReview)
            .where(ReceiptReview.expense_version_id == version.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if review is None:
        raise not_found("رکورد بررسی این رسید پیدا نشد.")
    if review.status is not ReceiptStatus.pending:
        raise invalid_state("دربارهٔ این نسخه قبلاً تصمیم ثبت شده است.")
    if not approve and not reason:
        raise validation_error(
            "برای رد رسید باید دلیل بنویسید.", reason="دلیل رد الزامی است."
        )

    review.status = ReceiptStatus.confirmed if approve else ReceiptStatus.rejected
    review.reviewed_by = customer_id
    review.reviewed_at = now()
    review.reason = reason

    await audit.record(
        session,
        "receipt_confirmed" if approve else "receipt_rejected",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=version.id,
        reason=reason,
    )

    if approve:
        completion = (
            await session.execute(
                select(Completion).where(Completion.selection_id == expense.selection_id)
            )
        ).scalar_one_or_none()
        if completion is not None and completion.requested_at is not None:
            # confirm_completion re-reads the confirmed version, so make this one visible.
            await session.flush()
            await confirm_completion(
                session,
                selection_id=expense.selection_id,
                customer_id=customer_id,
                expected_revision=None,
                satisfaction_score=satisfaction_score,
                satisfaction_note=satisfaction_note,
                reference_consent=reference_consent,
            )
    return review


async def confirmed_receipt_version(
    session: AsyncSession, expense_id: uuid.UUID
) -> ExpenseVersion | None:
    row = (
        await session.execute(
            select(ExpenseVersion)
            .join(ReceiptReview, ReceiptReview.expense_version_id == ExpenseVersion.id)
            .where(
                ExpenseVersion.expense_id == expense_id,
                ReceiptReview.status == ReceiptStatus.confirmed,
            )
            .order_by(ExpenseVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def store_attachment(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    expense_version_id: uuid.UUID | None,
    uploader_id: uuid.UUID,
    original_name: str,
    data: bytes,
    policy: Policy,
) -> Attachment:
    """Save a private evidence image.

    The real type is sniffed from the bytes, the stored name is random, the file lives
    outside anything the web server publishes, and a repeat of the same content is flagged
    as a duplicate rather than silently accepted.
    """
    if len(data) > policy.attachments.max_bytes:
        raise validation_error(
            "حجم فایل بیش از حد مجاز است.", file="حداکثر اندازهٔ هر فایل ۲ مگابایت است."
        )
    content_type = sniff_content_type(data)
    if content_type is None or content_type not in policy.attachments.allowed_content_types:
        raise validation_error(
            "فقط تصویر JPEG یا PNG پذیرفته می‌شود.", file="نوع واقعی فایل مجاز نیست."
        )

    if expense_version_id is not None:
        count = len(
            list(
                (
                    await session.execute(
                        select(Attachment).where(
                            Attachment.expense_version_id == expense_version_id,
                            Attachment.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
        )
        if count >= policy.attachments.max_files_per_expense:
            raise validation_error(
                "تعداد تصاویر این رسید به حد مجاز رسیده است.",
                file="حداکثر ۳ تصویر برای هر رسید مجاز است.",
            )

    digest = hashlib.sha256(data).hexdigest()
    duplicate = (
        await session.execute(
            select(Attachment).where(
                Attachment.request_id == request_id,
                Attachment.sha256 == digest,
                Attachment.deleted_at.is_(None),
            )
        )
    ).first()

    suffix = ".jpg" if content_type == "image/jpeg" else ".png"
    stored_name = f"{secrets.token_hex(16)}{suffix}"
    directory = settings.attachment_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / stored_name).write_bytes(data)

    attachment = Attachment(
        request_id=request_id,
        expense_version_id=expense_version_id,
        uploaded_by=uploader_id,
        stored_name=stored_name,
        original_name=original_name[:200],
        content_type=content_type,
        byte_size=len(data),
        sha256=digest,
        is_duplicate=duplicate is not None,
    )
    session.add(attachment)
    await session.flush()

    await audit.record(
        session,
        "attachment_stored",
        request_id=request_id,
        actor_id=uploader_id,
        subject_id=attachment.id,
        data={"isDuplicate": attachment.is_duplicate, "byteSize": len(data)},
    )
    return attachment


async def request_completion(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    specialist_id: uuid.UUID,
) -> Completion:
    """The specialist asks the customer to close the case on the current invoice."""
    selection, request = await _load_collaboration(session, selection_id)
    if selection.specialist_id != specialist_id:
        raise forbidden("درخواست پایان کار بر عهدهٔ متخصص منتخب است.")
    if selection.work_started_at is None:
        raise invalid_state("کاری شروع نشده که پایان آن ثبت شود.")

    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection_id))
    ).scalar_one_or_none()
    if expense is None or expense.current_version_id is None:
        raise invalid_state("ابتدا مخارج و ریز اقلام را ثبت کنید.")
    version = await session.get(ExpenseVersion, expense.current_version_id)
    if version is None or version.submitted_at is None:
        raise invalid_state("نسخهٔ مخارج هنوز برای مشتری ارسال نشده است.")

    agreement = await active_agreement(session, selection_id)
    completion = (
        await session.execute(
            select(Completion).where(Completion.selection_id == selection_id).with_for_update()
        )
    ).scalar_one_or_none()
    if completion is None:
        completion = Completion(selection_id=selection_id)
        session.add(completion)
        await session.flush()

    completion.requested_at = now()
    completion.invoice_total_toman = version.total_toman
    completion.invoice_specialist_payable_toman = version.specialist_payable_toman
    completion.based_on_expense_version_id = version.id
    completion.based_on_agreement_version_id = agreement.id if agreement else None
    completion.bump()

    request.status = RequestStatus.completion_review
    request.bump()

    await audit.record(
        session,
        "completion_requested",
        request_id=request.id,
        actor_id=specialist_id,
        actor_role="specialist",
        subject_id=completion.id,
        data={"invoiceTotalToman": version.total_toman},
    )
    return completion


async def confirm_completion(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    customer_id: uuid.UUID,
    expected_revision: int | None,
    satisfaction_score: int | None,
    satisfaction_note: str | None,
    reference_consent: bool,
) -> Completion:
    """Normal close.

    Only possible when the invoice matches the active agreement and the receipts it rests
    on were confirmed by this customer. Anything above the agreement needs an agreement
    change first; a disputed receipt or amount opens the dispute path instead of pushing
    the customer into confirming.
    """
    selection, request = await _load_collaboration(session, selection_id)
    if request.customer_id != customer_id:
        raise forbidden("تأیید پایان کار بر عهدهٔ مشتری همین پرونده است.")

    completion = (
        await session.execute(
            select(Completion).where(Completion.selection_id == selection_id).with_for_update()
        )
    ).scalar_one_or_none()
    if completion is None or completion.requested_at is None:
        raise invalid_state("متخصص هنوز پایان کار را ثبت نکرده است.")
    check_revision(completion, expected_revision)
    if completion.customer_confirmed_at is not None:
        return completion

    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection_id))
    ).scalar_one_or_none()
    if expense is None:
        raise invalid_state("مخارجی برای این پرونده ثبت نشده است.")

    confirmed = await confirmed_receipt_version(session, expense.id)
    if confirmed is None or confirmed.id != expense.current_version_id:
        raise invalid_state(
            "تأیید صورت‌حساب فقط پس از تأیید همین نسخهٔ رسید توسط شما ممکن است. "
            "اگر با رسید موافق نیستید، مسیر اختلاف را باز کنید."
        )

    agreement = await active_agreement(session, selection_id)
    if agreement is None:
        raise invalid_state("توافق فعالی برای تطبیق صورت‌حساب وجود ندارد.")
    if (
        confirmed.total_toman is None
        or agreement.total_toman is None
        or confirmed.total_toman > agreement.total_toman
    ):
        raise invalid_state(
            "صورت‌حساب با توافق فعال منطبق نیست؛ هزینهٔ اضافی ابتدا باید از مسیر تغییر "
            "توافق تأیید شود."
        )

    if satisfaction_score is not None and not 1 <= satisfaction_score <= 5:
        raise validation_error(
            "امتیاز رضایت باید بین ۱ و ۵ باشد.", satisfactionScore="خارج از محدوده"
        )

    completion.customer_confirmed_at = now()
    completion.satisfaction_score = satisfaction_score
    completion.satisfaction_note = satisfaction_note
    completion.reference_consent = reference_consent
    completion.invoice_total_toman = confirmed.total_toman
    completion.invoice_specialist_payable_toman = confirmed.specialist_payable_toman
    completion.based_on_expense_version_id = confirmed.id
    completion.based_on_agreement_version_id = agreement.id
    completion.bump()

    selection.status = SelectionStatus.ended
    selection.ended_at = now()
    selection.bump()
    request.status = RequestStatus.completed
    request.close_reason = None
    request.closed_at = now()
    request.bump()

    # Closing the case is what starts the score aggregation for this collaboration.
    from app.domain.evaluation import queue_for_closed_case

    await queue_for_closed_case(session, request=request, selection=selection)

    await audit.record(
        session,
        "completion_confirmed",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=completion.id,
        data={
            "invoiceTotalToman": completion.invoice_total_toman,
            "specialistPayableToman": completion.invoice_specialist_payable_toman,
            "satisfactionScore": satisfaction_score,
            "referenceConsent": reference_consent,
        },
    )
    return completion


async def report_mismatch(
    session: AsyncSession,
    *,
    selection_id: uuid.UUID,
    customer_id: uuid.UUID,
    reason: str,
) -> Completion:
    """The customer disagrees with the invoice. Nothing about the receipts is rewritten."""
    _, request = await _load_collaboration(session, selection_id)
    if request.customer_id != customer_id:
        raise forbidden("گزارش مغایرت بر عهدهٔ مشتری همین پرونده است.")
    completion = (
        await session.execute(
            select(Completion).where(Completion.selection_id == selection_id).with_for_update()
        )
    ).scalar_one_or_none()
    if completion is None:
        raise invalid_state("صورت‌حسابی برای گزارش مغایرت وجود ندارد.")

    completion.customer_reported_mismatch_at = now()
    completion.bump()
    await audit.record(
        session,
        "completion_mismatch_reported",
        request_id=request.id,
        actor_id=customer_id,
        actor_role="customer",
        subject_id=completion.id,
        reason=reason,
    )
    return completion


def party_of(request: Request, selection: Selection, user_id: uuid.UUID) -> Party:
    if user_id == request.customer_id:
        return Party.customer
    if user_id == selection.specialist_id:
        return Party.specialist
    raise forbidden("شما طرف این همکاری نیستید.")
