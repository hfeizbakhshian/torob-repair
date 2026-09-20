"""Expenses, receipt review, attachments, completion and the Torob invoice check."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import now
from app.domain import agreements as agreement_service
from app.domain import expenses as service
from app.domain import parts
from app.domain.errors import DomainError, ErrorCode
from app.domain.money import LineItem
from app.models import Attachment, Request
from app.models.enums import Party, PriceCheckVerdict, ReceiptStatus, RequestStatus
from tests import factories

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff" + b"0" * 64


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


async def started_case(session, policy, advance, *, lines=None):
    request = await factories.published_request(session, policy)
    await factories.submit_offer(session, policy, request, "specialist-arya")
    selection = await factories.accepted_collaboration(
        session, policy, request, "specialist-arya"
    )
    await factories.activate_agreement(
        session, policy, request, selection, lines=lines
    )
    advance(days=3)
    await agreement_service.start_work(
        session, selection_id=selection.id, actor_id=selection.specialist_id,
        expected_revision=None,
    )
    await session.commit()
    return request, selection


async def test_editing_an_expense_drops_the_previous_confirmation(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    first = await service.submit_expense_version(
        session,
        selection_id=selection.id,
        specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(),
        source_text=None,
        actual_minutes=190,
        extracted_by_ai=False,
    )
    await session.commit()
    await service.review_receipt(
        session, expense_version_id=first.id, customer_id=request.customer_id,
        approve=True, reason=None,
    )
    await session.commit()

    second = await service.submit_expense_version(
        session,
        selection_id=selection.id,
        specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=5_500_000),
        source_text=None,
        actual_minutes=200,
        extracted_by_ai=False,
    )
    await session.commit()

    # The new version starts pending: the earlier confirmation does not carry over.
    review = await service.confirmed_receipt_version(session, first.expense_id)
    assert review is not None and review.id == first.id
    from app.models import ReceiptReview

    new_review = (
        await session.execute(
            select(ReceiptReview).where(ReceiptReview.expense_version_id == second.id)
        )
    ).scalar_one()
    assert new_review.status is ReceiptStatus.pending


async def test_customer_cannot_judge_a_superseded_version(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    first = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
        extracted_by_ai=False,
    )
    await session.commit()
    await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=5_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()

    with pytest.raises(DomainError) as error:
        await service.review_receipt(
            session, expense_version_id=first.id, customer_id=request.customer_id,
            approve=True, reason=None,
        )
    assert error.value.code is ErrorCode.INVALID_STATE


async def test_rejecting_a_receipt_requires_a_reason(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
        extracted_by_ai=False,
    )
    await session.commit()
    with pytest.raises(DomainError):
        await service.review_receipt(
            session, expense_version_id=version.id, customer_id=request.customer_id,
            approve=False, reason=None,
        )


async def test_ai_extracted_lines_wait_for_the_specialist_review(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text="متن خام", actual_minutes=None,
        extracted_by_ai=True,
    )
    await session.commit()
    assert version.submitted_at is None

    with pytest.raises(DomainError):
        await service.review_receipt(
            session, expense_version_id=version.id, customer_id=request.customer_id,
            approve=True, reason=None,
        )

    await service.specialist_review_extraction(
        session, expense_version_id=version.id,
        specialist_id=selection.specialist_id, lines=None,
    )
    await session.commit()
    await session.refresh(version)
    assert version.submitted_at is not None


async def test_completion_requires_a_confirmed_receipt(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
        extracted_by_ai=False,
    )
    await session.commit()
    await service.request_completion(
        session, selection_id=selection.id, specialist_id=selection.specialist_id
    )
    await session.commit()

    with pytest.raises(DomainError) as error:
        await service.confirm_completion(
            session, selection_id=selection.id, customer_id=request.customer_id,
            expected_revision=None, satisfaction_score=None, satisfaction_note=None,
            reference_consent=False,
        )
    # The customer is pointed at the dispute path, not pushed into confirming.
    assert "اختلاف" in error.value.message


async def test_invoice_above_the_agreement_cannot_be_confirmed_normally(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000),
        source_text=None, actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    await service.review_receipt(
        session, expense_version_id=version.id, customer_id=request.customer_id,
        approve=True, reason=None,
    )
    await session.commit()
    await service.request_completion(
        session, selection_id=selection.id, specialist_id=selection.specialist_id
    )
    await session.commit()

    with pytest.raises(DomainError) as error:
        await service.confirm_completion(
            session, selection_id=selection.id, customer_id=request.customer_id,
            expected_revision=None, satisfaction_score=5, satisfaction_note=None,
            reference_consent=False,
        )
    # Confirming a receipt is not permission to raise the agreed amount.
    assert "تغییر توافق" in error.value.message


async def test_happy_path_completion_closes_the_case(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=195,
        extracted_by_ai=False,
    )
    await session.commit()
    await service.review_receipt(
        session, expense_version_id=version.id, customer_id=request.customer_id,
        approve=True, reason=None,
    )
    await session.commit()
    await service.request_completion(
        session, selection_id=selection.id, specialist_id=selection.specialist_id
    )
    await session.commit()
    completion = await service.confirm_completion(
        session, selection_id=selection.id, customer_id=request.customer_id,
        expected_revision=None, satisfaction_score=4,
        satisfaction_note="راضی بودم", reference_consent=True,
    )
    await session.commit()

    assert completion.customer_confirmed_at is not None
    refreshed = await session.get(Request, request.id)
    await session.refresh(refreshed)
    assert refreshed.status is RequestStatus.completed


async def test_customer_part_stays_out_of_the_specialist_payable_end_to_end(
    session, policy, advance
):
    lines = factories.clutch_lines(part_supplied_by="customer")
    _, selection = await started_case(session, policy, advance, lines=lines)
    agreement = await agreement_service.active_agreement(session, selection.id)
    assert agreement is not None
    assert agreement.total_toman == 5_400_000
    assert agreement.specialist_payable_toman == 1_200_000


async def test_attachment_type_is_sniffed_and_duplicates_are_flagged(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
        extracted_by_ai=False,
    )
    await session.commit()

    first = await service.store_attachment(
        session, request_id=request.id, expense_version_id=version.id,
        uploader_id=selection.specialist_id, original_name="a.png", data=PNG, policy=policy,
    )
    await session.commit()
    assert first.content_type == "image/png"
    assert first.is_duplicate is False

    # Same bytes under a different name: flagged, not silently accepted.
    again = await service.store_attachment(
        session, request_id=request.id, expense_version_id=version.id,
        uploader_id=selection.specialist_id, original_name="b.png", data=PNG, policy=policy,
    )
    await session.commit()
    assert again.is_duplicate is True

    # A .png name over non-image bytes is refused on the real type.
    with pytest.raises(DomainError):
        await service.store_attachment(
            session, request_id=request.id, expense_version_id=version.id,
            uploader_id=selection.specialist_id, original_name="c.png",
            data=b"this is not an image", policy=policy,
        )

    stored = await session.get(Attachment, first.id)
    from app.config import settings

    path = settings.attachment_dir / stored.stored_name
    assert path.exists()
    # The stored name is random and reveals nothing about what was uploaded, and the file
    # lives outside anything the web server publishes.
    assert re.fullmatch(r"[0-9a-f]{32}\.png", stored.stored_name)
    assert stored.original_name == "a.png"
    assert settings.attachment_dir.is_absolute()


async def test_fourth_attachment_is_refused(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(), source_text=None, actual_minutes=None,
        extracted_by_ai=False,
    )
    await session.commit()
    for index in range(3):
        await service.store_attachment(
            session, request_id=request.id, expense_version_id=version.id,
            uploader_id=selection.specialist_id, original_name=f"{index}.jpg",
            data=JPEG + bytes([index]), policy=policy,
        )
        await session.commit()
    with pytest.raises(DomainError):
        await service.store_attachment(
            session, request_id=request.id, expense_version_id=version.id,
            uploader_id=selection.specialist_id, original_name="x.jpg",
            data=JPEG + b"\x09", policy=policy,
        )


async def _snapshot(session, request, line_id, seller, price, *, verified=True, age_hours=1):
    snapshot = await parts.record_price_snapshot(
        session,
        request_id=request.id,
        actor_id=request.customer_id,
        expense_version_id=None,
        line_id=line_id,
        product_url="https://torob.com/p/sample",
        seller_name=seller,
        brand="نمونه",
        part_number="CL-206-T5",
        condition="new",
        warranty_note=None,
        unit_price_toman=price,
        delivery_cost_toman=0,
        payment_terms="نقدی",
        evidence_note="ثبت دستی نمونه",
        observed_at_override=now() - timedelta(hours=age_hours),
    )
    if verified:
        snapshot.verified_by_support_at = now()
    await session.commit()
    return snapshot


async def test_invoice_overprice_needs_three_verified_recent_sources(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])

    # Two sources only: not assessable, and no negative event.
    await _snapshot(session, request, line.id, "فروشندهٔ نمونه ۱", 4_000_000)
    await _snapshot(session, request, line.id, "فروشندهٔ نمونه ۲", 4_200_000)
    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.not_assessable

    await _snapshot(session, request, line.id, "فروشندهٔ نمونه ۳", 4_100_000)
    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.overpriced
    assert check.median_reference_toman == 4_100_000
    assert check.established_at is not None


async def test_cheaper_invoice_creates_no_negative_event(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=3_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])
    for index, price in enumerate((4_000_000, 4_100_000, 4_200_000), start=1):
        await _snapshot(session, request, line.id, f"فروشندهٔ نمونه {index}", price)

    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.within_range


async def test_customer_supplied_part_is_never_penalised(session, policy, advance):
    lines = factories.clutch_lines(part_toman=9_000_000, part_supplied_by="customer")
    request, selection = await started_case(session, policy, advance, lines=lines)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=lines, source_text=None, actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])
    for index, price in enumerate((4_000_000, 4_100_000, 4_200_000), start=1):
        await _snapshot(session, request, line.id, f"فروشندهٔ نمونه {index}", price)

    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.not_assessable
    assert check.payer is Party.customer


async def test_unverified_and_stale_sources_do_not_count(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])

    await _snapshot(session, request, line.id, "تأییدنشده", 4_000_000, verified=False)
    await _snapshot(session, request, line.id, "کهنه", 4_000_000, age_hours=48)
    await _snapshot(session, request, line.id, "معتبر", 4_100_000)

    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.not_assessable


async def test_unestablished_check_writes_no_score_event(session, policy, advance):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])
    for index, price in enumerate((4_000_000, 4_100_000, 4_200_000), start=1):
        await _snapshot(session, request, line.id, f"فروشندهٔ نمونه {index}", price)

    check = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=False
    )
    await session.commit()
    assert check.verdict is PriceCheckVerdict.overpriced
    # Still under review: no established event, so no score effect yet.
    assert check.established_at is None

    from app.domain.evaluation import negative_event_count

    assert await negative_event_count(session, request.id) == 0


async def test_only_https_torob_links_are_accepted(session, policy):
    for bad in (
        "http://torob.com/p/x",
        "https://evil.example/torob.com",
        "https://sub.torob.com.evil/p",
        "ftp://torob.com/p",
    ):
        with pytest.raises(DomainError):
            parts.validate_product_url(bad)
    assert parts.validate_product_url("https://www.torob.com/p/ok")
    assert "torob.com/search" in parts.search_url("لنت ترمز")


async def test_repeating_the_check_does_not_count_the_event_twice(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])
    for index, price in enumerate((4_000_000, 4_100_000, 4_200_000), start=1):
        await _snapshot(session, request, line.id, f"فروشندهٔ نمونه {index}", price)

    first = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()
    second = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=True
    )
    await session.commit()

    assert second.id == first.id
    from app.domain.evaluation import negative_event_count

    assert await negative_event_count(session, request.id) == 1


async def test_rechecking_before_establishment_makes_a_new_version(
    session, policy, advance
):
    request, selection = await started_case(session, policy, advance)
    version = await service.submit_expense_version(
        session, selection_id=selection.id, specialist_id=selection.specialist_id,
        lines=factories.clutch_lines(part_toman=9_000_000), source_text=None,
        actual_minutes=None, extracted_by_ai=False,
    )
    await session.commit()
    line = LineItem.model_validate(version.lines[0])

    first = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=False
    )
    await session.commit()
    assert first.verdict is PriceCheckVerdict.not_assessable

    for index, price in enumerate((4_000_000, 4_100_000, 4_200_000), start=1):
        await _snapshot(session, request, line.id, f"فروشندهٔ نمونه {index}", price)
    second = await parts.evaluate_invoice_price(
        session, expense_version_id=version.id, line=line, policy=policy, established=False
    )
    await session.commit()

    assert second.id != first.id
    assert second.version_number == first.version_number + 1
    await session.refresh(first)
    assert first.is_superseded is True

    live = await parts.list_price_checks(session, request.id)
    assert [check.id for check in live] == [second.id]
