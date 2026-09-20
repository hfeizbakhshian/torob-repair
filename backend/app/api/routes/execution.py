"""Expenses, receipts, evidence files, parts and case completion."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CustomerDep, PolicyDep, SessionDep, SpecialistDep, UserDep
from app.api.serializers import (
    completion_out,
    expense_version_out,
    part_option_out,
    price_check_out,
    price_snapshot_out,
)
from app.config import settings
from app.domain import expenses as service
from app.domain import parts as parts_service
from app.domain.errors import forbidden, not_found
from app.domain.money import LineItem
from app.models import (
    Attachment,
    Completion,
    Expense,
    ExpenseVersion,
    PartOption,
    PartPriceSnapshot,
    Request,
    Selection,
)
from app.models.enums import Role
from app.schemas.api import (
    CompletionOut,
    ConfirmCompletionInput,
    ExpenseVersionOut,
    MismatchInput,
    PartOptionInput,
    PartOptionOut,
    PartSearchOut,
    PriceCheckOut,
    PriceSnapshotInput,
    PriceSnapshotOut,
    ReceiptDecisionInput,
    ReviewExtractionInput,
    SubmitExpenseInput,
)

router = APIRouter(tags=["execution"])


async def _party_or_support(
    session: SessionDep, selection: Selection, user: UserDep
) -> None:
    if user.role is Role.support:
        return
    request = await session.get(Request, selection.request_id)
    if request is None:
        raise not_found()
    if user.user_id not in (request.customer_id, selection.specialist_id):
        raise forbidden("شما طرف این همکاری نیستید.")


@router.post(
    "/selections/{selection_id}/expenses", response_model=ExpenseVersionOut, status_code=201
)
async def submit_expense(
    selection_id: uuid.UUID,
    payload: SubmitExpenseInput,
    session: SessionDep,
    specialist: SpecialistDep,
) -> ExpenseVersionOut:
    """A new version. Any earlier confirmation stops applying to it."""
    version = await service.submit_expense_version(
        session,
        selection_id=selection_id,
        specialist_id=specialist.user_id,
        lines=list(payload.lines),
        source_text=payload.source_text,
        actual_minutes=payload.actual_minutes,
        extracted_by_ai=payload.extracted_by_ai,
        expected_revision=payload.expected_revision,
    )
    return await expense_version_out(session, version)


@router.post("/expense-versions/{version_id}/review-extraction", response_model=ExpenseVersionOut)
async def review_extraction(
    version_id: uuid.UUID,
    payload: ReviewExtractionInput,
    session: SessionDep,
    specialist: SpecialistDep,
) -> ExpenseVersionOut:
    """The specialist checks the extracted lines before the customer ever sees them."""
    version = await service.specialist_review_extraction(
        session,
        expense_version_id=version_id,
        specialist_id=specialist.user_id,
        lines=list(payload.lines) if payload.lines is not None else None,
    )
    return await expense_version_out(session, version)


@router.get("/selections/{selection_id}/expenses", response_model=list[ExpenseVersionOut])
async def list_expense_versions(
    selection_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[ExpenseVersionOut]:
    selection = await session.get(Selection, selection_id)
    if selection is None:
        raise not_found("همکاری پیدا نشد.")
    await _party_or_support(session, selection, user)
    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection_id))
    ).scalar_one_or_none()
    if expense is None:
        return []
    rows = await session.execute(
        select(ExpenseVersion)
        .where(ExpenseVersion.expense_id == expense.id)
        .order_by(ExpenseVersion.version_number)
    )
    return [await expense_version_out(session, row) for row in rows.scalars()]


@router.post("/expense-versions/{version_id}/review", response_model=ExpenseVersionOut)
async def review_receipt(
    version_id: uuid.UUID,
    payload: ReceiptDecisionInput,
    session: SessionDep,
    customer: CustomerDep,
) -> ExpenseVersionOut:
    """The customer's verdict on this exact version. Rejection opens the dispute path."""
    await service.review_receipt(
        session,
        expense_version_id=version_id,
        customer_id=customer.user_id,
        approve=payload.approve,
        reason=payload.reason,
    )
    version = await session.get(ExpenseVersion, version_id)
    if version is None:
        raise not_found()
    return await expense_version_out(session, version)


@router.post("/expense-versions/{version_id}/attachments", status_code=201)
async def upload_attachment(
    version_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    policy: PolicyDep,
    file: UploadFile = File(...),
    request_id: uuid.UUID = Form(...),
) -> dict[str, object]:
    """A private evidence image. No OCR, and the image is never sent to the model."""
    version = await session.get(ExpenseVersion, version_id)
    if version is None:
        raise not_found("نسخهٔ مخارج پیدا نشد.")
    data = await file.read()
    attachment = await service.store_attachment(
        session,
        request_id=request_id,
        expense_version_id=version_id,
        uploader_id=user.user_id,
        original_name=file.filename or "receipt",
        data=data,
        policy=policy,
    )
    return {
        "id": str(attachment.id),
        "isDuplicate": attachment.is_duplicate,
        "byteSize": attachment.byte_size,
    }


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: uuid.UUID, session: SessionDep, user: UserDep
) -> FileResponse:
    """Served by the backend from a private directory — never from a public web path."""
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None or attachment.deleted_at is not None:
        raise not_found("این فایل در دسترس نیست.")
    request = await session.get(Request, attachment.request_id)
    if request is None:
        raise not_found()
    if user.role is not Role.support and user.user_id != request.customer_id:
        # The customer and support see the image; the model only ever gets the text items
        # and their confirmation status.
        selection = (
            await session.execute(
                select(Selection)
                .where(Selection.request_id == request.id)
                .order_by(Selection.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if selection is None or selection.specialist_id != user.user_id:
            raise forbidden("دسترسی به این مدرک مجاز نیست.")

    path = settings.attachment_dir / attachment.stored_name
    if not path.exists():
        raise not_found("فایل این مدرک موجود نیست.")
    return FileResponse(path, media_type=attachment.content_type)


@router.post("/selections/{selection_id}/completion", response_model=CompletionOut)
async def request_completion(
    selection_id: uuid.UUID, session: SessionDep, specialist: SpecialistDep
) -> CompletionOut:
    completion = await service.request_completion(
        session, selection_id=selection_id, specialist_id=specialist.user_id
    )
    return completion_out(completion)


@router.get("/selections/{selection_id}/completion", response_model=CompletionOut | None)
async def get_completion(
    selection_id: uuid.UUID, session: SessionDep, user: UserDep
) -> CompletionOut | None:
    selection = await session.get(Selection, selection_id)
    if selection is None:
        raise not_found("همکاری پیدا نشد.")
    await _party_or_support(session, selection, user)
    completion = (
        await session.execute(
            select(Completion).where(Completion.selection_id == selection_id)
        )
    ).scalar_one_or_none()
    return completion_out(completion) if completion else None


@router.post("/selections/{selection_id}/completion/confirm", response_model=CompletionOut)
async def confirm_completion(
    selection_id: uuid.UUID,
    payload: ConfirmCompletionInput,
    session: SessionDep,
    customer: CustomerDep,
) -> CompletionOut:
    """Only when the invoice matches the active agreement and the receipt was confirmed."""
    completion = await service.confirm_completion(
        session,
        selection_id=selection_id,
        customer_id=customer.user_id,
        expected_revision=payload.expected_revision,
        satisfaction_score=payload.satisfaction_score,
        satisfaction_note=payload.satisfaction_note,
        reference_consent=payload.reference_consent,
    )
    return completion_out(completion)


@router.post("/selections/{selection_id}/completion/mismatch", response_model=CompletionOut)
async def report_mismatch(
    selection_id: uuid.UUID,
    payload: MismatchInput,
    session: SessionDep,
    customer: CustomerDep,
) -> CompletionOut:
    completion = await service.report_mismatch(
        session,
        selection_id=selection_id,
        customer_id=customer.user_id,
        reason=payload.reason,
    )
    return completion_out(completion)


# --- parts ----------------------------------------------------------------


@router.get("/parts/search", response_model=PartSearchOut)
async def part_search(_: UserDep, query: str = Query(max_length=200)) -> PartSearchOut:
    return PartSearchOut(query=query, search_url=parts_service.search_url(query))


@router.post("/requests/{request_id}/parts", response_model=PartOptionOut, status_code=201)
async def record_part(
    request_id: uuid.UUID,
    payload: PartOptionInput,
    session: SessionDep,
    user: UserDep,
) -> PartOptionOut:
    """Manually recorded. The server never fetches the link or claims live stock."""
    option = await parts_service.record_part_option(
        session,
        request_id=request_id,
        actor_id=user.user_id,
        part_title=payload.part_title,
        part_number=payload.part_number,
        brand=payload.brand,
        condition=payload.condition,
        warranty_note=payload.warranty_note,
        product_url=payload.product_url,
        seller_name=payload.seller_name,
        price_toman=payload.price_toman,
        delivery_note=payload.delivery_note,
        delivery_cost_toman=payload.delivery_cost_toman,
    )
    return part_option_out(option)


@router.get("/requests/{request_id}/parts", response_model=list[PartOptionOut])
async def list_parts(
    request_id: uuid.UUID, session: SessionDep, _: UserDep
) -> list[PartOptionOut]:
    rows = await session.execute(
        select(PartOption)
        .where(PartOption.request_id == request_id)
        .order_by(PartOption.created_at)
    )
    return [part_option_out(row) for row in rows.scalars()]


@router.post("/parts/{option_id}/confirm-compatibility", response_model=PartOptionOut)
async def confirm_compatibility(
    option_id: uuid.UUID, session: SessionDep, specialist: SpecialistDep
) -> PartOptionOut:
    option = await parts_service.confirm_compatibility(
        session, option_id=option_id, specialist_id=specialist.user_id
    )
    return part_option_out(option)


@router.post(
    "/requests/{request_id}/price-snapshots", response_model=PriceSnapshotOut, status_code=201
)
async def record_price_snapshot(
    request_id: uuid.UUID,
    payload: PriceSnapshotInput,
    session: SessionDep,
    user: UserDep,
) -> PriceSnapshotOut:
    """One recorded seller price. It counts only once support has vouched for it."""
    snapshot = await parts_service.record_price_snapshot(
        session,
        request_id=request_id,
        actor_id=user.user_id,
        expense_version_id=payload.expense_version_id,
        line_id=payload.line_id,
        product_url=payload.product_url,
        seller_name=payload.seller_name,
        brand=payload.brand,
        part_number=payload.part_number,
        condition=payload.condition,
        warranty_note=payload.warranty_note,
        unit_price_toman=payload.unit_price_toman,
        delivery_cost_toman=payload.delivery_cost_toman,
        payment_terms=payload.payment_terms,
        evidence_note=payload.evidence_note,
    )
    return price_snapshot_out(snapshot)


@router.get("/requests/{request_id}/price-snapshots", response_model=list[PriceSnapshotOut])
async def list_price_snapshots(
    request_id: uuid.UUID, session: SessionDep, _: UserDep
) -> list[PriceSnapshotOut]:
    rows = await session.execute(
        select(PartPriceSnapshot)
        .where(PartPriceSnapshot.request_id == request_id)
        .order_by(PartPriceSnapshot.created_at)
    )
    return [price_snapshot_out(row) for row in rows.scalars()]


@router.get("/requests/{request_id}/price-checks", response_model=list[PriceCheckOut])
async def list_price_checks(
    request_id: uuid.UUID, session: SessionDep, _: UserDep
) -> list[PriceCheckOut]:
    checks = await parts_service.list_price_checks(session, request_id)
    return [price_check_out(check) for check in checks]


@router.post("/expense-versions/{version_id}/price-checks", response_model=list[PriceCheckOut])
async def run_price_checks(
    version_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    policy: PolicyDep,
) -> list[PriceCheckOut]:
    """Compare each invoiced part line with the recorded Torob prices.

    This is pure arithmetic over recorded data — it makes no AI call of its own, and on its
    own it changes no agreed amount, no ruling and no refund.
    """
    version = await session.get(ExpenseVersion, version_id)
    if version is None:
        raise not_found("نسخهٔ مخارج پیدا نشد.")
    expense = await session.get(Expense, version.expense_id)
    if expense is None:
        raise not_found()
    selection = await session.get(Selection, expense.selection_id)
    if selection is None:
        raise not_found()
    await _party_or_support(session, selection, user)

    established = await _receipt_confirmed(session, version_id)
    results = []
    for raw in version.lines:
        line = LineItem.model_validate(raw)
        check = await parts_service.evaluate_invoice_price(
            session,
            expense_version_id=version_id,
            line=line,
            policy=policy,
            established=established,
        )
        results.append(price_check_out(check))
    return results


async def _receipt_confirmed(session: SessionDep, version_id: uuid.UUID) -> bool:
    from app.models import ReceiptReview
    from app.models.enums import ReceiptStatus

    review = (
        await session.execute(
            select(ReceiptReview).where(ReceiptReview.expense_version_id == version_id)
        )
    ).scalar_one_or_none()
    return review is not None and review.status is ReceiptStatus.confirmed
