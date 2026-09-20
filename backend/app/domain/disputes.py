"""Disputes, voluntary settlement, and the binding AI adjudication.

Two paths end a dispute and they are recorded differently. A voluntary settlement needs
both parties to approve the same proposal version. An adjudication does not need anyone's
re-confirmation — but the *domain service* applies it, after validating the ruling against
the frozen input, and it applies it exactly once. The model never moves money, never grants
a permission and never turns a receipt the customer rejected into a confirmed one.

In this MVP, applying a settlement means recording and notifying the binding result and
amount. No repair money is collected.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.domain import audit, jobs
from app.domain.agreements import active_agreement
from app.domain.errors import forbidden, invalid_state, not_found, validation_error
from app.domain.expenses import confirmed_receipt_version, party_of
from app.domain.money import LineItem, compute_totals
from app.models import (
    Attachment,
    Dispute,
    DisputeDecision,
    DisputeResolutionProposal,
    DisputeSnapshot,
    DisputeStatement,
    Expense,
    ExpenseVersion,
    ReceiptReview,
    Request,
    Selection,
    Settlement,
)
from app.models.enums import (
    CloseReason,
    DisputeStatus,
    JobKind,
    Party,
    RequestStatus,
    SelectionStatus,
    SettlementSource,
)
from app.policy import Policy
from app.repository.locking import check_revision, lock_row
from app.schemas.ai import DisputeDecisionOutput


async def open_dispute(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    claim_items: list[dict[str, Any]],
    policy: Policy,
) -> Dispute:
    """Either party may open one, about an accepted collaboration.

    The MVP scope of adjudication is this case only: the agreed scope of work, whether the
    operations were performed, the invoice lines and amount, and any cancellation cost.
    """
    request = await lock_row(session, Request, request_id)
    selection = (
        await session.execute(
            select(Selection)
            .where(
                Selection.request_id == request_id,
                Selection.status.in_([SelectionStatus.accepted, SelectionStatus.ended]),
            )
            .order_by(Selection.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if selection is None:
        raise invalid_state("اختلاف فقط دربارهٔ همکاری پذیرفته‌شده ثبت می‌شود.")
    party_of(request, selection, actor_id)

    existing = (
        await session.execute(select(Dispute).where(Dispute.request_id == request_id))
    ).scalar_one_or_none()
    if existing is not None:
        # One process per case: resubmitting the same dispute never creates a second
        # ruling or a fresh budget.
        return existing

    if not claim_items:
        raise validation_error("اقلام مورد اختلاف را مشخص کنید.", claimItems="فهرست خالی است.")

    dispute = Dispute(
        request_id=request_id,
        selection_id=selection.id,
        opened_by=actor_id,
        claim_items=claim_items,
        statement_deadline=now() + timedelta(hours=policy.timing.dispute_statement_hours),
    )
    session.add(dispute)
    request.status = RequestStatus.dispute_open
    request.bump()
    await session.flush()

    await jobs.schedule(
        session,
        JobKind.close_dispute_statement_window,
        dispute.statement_deadline,
        subject_id=dispute.id,
        dedupe_key=f"dispute_statements:{dispute.id}",
    )
    await audit.record(
        session,
        "dispute_opened",
        request_id=request_id,
        actor_id=actor_id,
        subject_id=dispute.id,
        data={"claimItemCount": len(claim_items)},
    )
    return dispute


async def submit_statement(
    session: AsyncSession,
    *,
    dispute_id: uuid.UUID,
    actor_id: uuid.UUID,
    body: str,
    item_positions: list[dict[str, Any]],
    evidence_ids: list[str],
) -> DisputeStatement:
    """Each side keeps one current statement bundle, editable until the input is locked.

    Editing it expires any snapshot already taken, so a late model answer built on the old
    input can no longer be applied.
    """
    dispute = await lock_row(session, Dispute, dispute_id)
    request = await lock_row(session, Request, dispute.request_id)
    selection = await session.get(Selection, dispute.selection_id)
    if selection is None:
        raise not_found("همکاری این اختلاف پیدا نشد.")
    party = party_of(request, selection, actor_id)

    if dispute.status not in (DisputeStatus.dispute_open, DisputeStatus.needs_evidence):
        raise invalid_state("ورودی این اختلاف قفل شده و اظهارات تازه پذیرفته نمی‌شود.")

    previous = (
        await session.execute(
            select(DisputeStatement).where(
                DisputeStatement.dispute_id == dispute_id,
                DisputeStatement.party == party,
                DisputeStatement.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    version_number = 1
    if previous is not None:
        previous.is_current = False
        version_number = previous.version_number + 1

    statement = DisputeStatement(
        dispute_id=dispute_id,
        party=party,
        version_number=version_number,
        body=body,
        item_positions=item_positions,
        evidence_ids=evidence_ids,
    )
    session.add(statement)
    await _expire_snapshots(session, dispute)

    await audit.record(
        session,
        "dispute_statement_submitted",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=dispute.id,
        data={"party": party.value, "versionNumber": version_number},
    )
    return statement


async def _expire_snapshots(session: AsyncSession, dispute: Dispute) -> None:
    rows = (
        await session.execute(
            select(DisputeSnapshot).where(
                DisputeSnapshot.dispute_id == dispute.id,
                DisputeSnapshot.expired_at.is_(None),
            )
        )
    ).scalars()
    for snapshot in rows:
        snapshot.expired_at = now()
    dispute.current_snapshot_id = None


async def close_statements(
    session: AsyncSession, *, dispute_id: uuid.UUID, actor_id: uuid.UUID
) -> Dispute:
    """Declaring statements finished. When both do, adjudication may start early."""
    dispute = await lock_row(session, Dispute, dispute_id)
    request = await lock_row(session, Request, dispute.request_id)
    selection = await session.get(Selection, dispute.selection_id)
    if selection is None:
        raise not_found("همکاری این اختلاف پیدا نشد.")
    party = party_of(request, selection, actor_id)

    if party is Party.customer:
        dispute.customer_closed_statements_at = now()
    else:
        dispute.specialist_closed_statements_at = now()
    dispute.bump()

    if (
        dispute.customer_closed_statements_at is not None
        and dispute.specialist_closed_statements_at is not None
    ):
        await jobs.schedule(
            session,
            JobKind.run_dispute_adjudication,
            now(),
            subject_id=dispute.id,
            dedupe_key=f"adjudicate:{dispute.id}",
        )
    return dispute


async def propose_resolution(
    session: AsyncSession,
    *,
    dispute_id: uuid.UUID,
    actor_id: uuid.UUID,
    lines: list[LineItem],
    note: str | None,
) -> DisputeResolutionProposal:
    """A voluntary settlement offer. It applies only with two approvals of this version."""
    dispute = await lock_row(session, Dispute, dispute_id)
    request = await lock_row(session, Request, dispute.request_id)
    selection = await session.get(Selection, dispute.selection_id)
    if selection is None:
        raise not_found("همکاری این اختلاف پیدا نشد.")
    party = party_of(request, selection, actor_id)
    if dispute.status in (DisputeStatus.resolved_by_ai, DisputeStatus.resolved_by_agreement):
        raise invalid_state("این اختلاف بسته شده است.")

    previous = (
        await session.execute(
            select(DisputeResolutionProposal).where(
                DisputeResolutionProposal.dispute_id == dispute_id,
                DisputeResolutionProposal.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    version_number = 1
    if previous is not None:
        previous.is_current = False
        version_number = previous.version_number + 1

    totals = compute_totals(lines)
    proposal = DisputeResolutionProposal(
        dispute_id=dispute_id,
        version_number=version_number,
        proposed_by=party,
        lines=[line.model_dump(mode="json", by_alias=True) for line in lines],
        total_toman=totals.total_toman,
        specialist_payable_toman=totals.specialist_payable_toman,
        note=note,
        customer_approved_at=now() if party is Party.customer else None,
        specialist_approved_at=now() if party is Party.specialist else None,
    )
    session.add(proposal)
    await session.flush()
    await audit.record(
        session,
        "dispute_resolution_proposed",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=proposal.id,
        data={"totalToman": totals.total_toman},
    )
    return proposal


async def approve_resolution(
    session: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int | None = None,
) -> Settlement | None:
    """The second approval applies the settlement and expires any pending adjudication."""
    proposal = await lock_row(session, DisputeResolutionProposal, proposal_id)
    dispute = await lock_row(session, Dispute, proposal.dispute_id)
    request = await lock_row(session, Request, dispute.request_id)
    selection = await lock_row(session, Selection, dispute.selection_id)
    party = party_of(request, selection, actor_id)
    check_revision(dispute, expected_revision)

    if not proposal.is_current:
        raise invalid_state("این نسخهٔ پیشنهاد حل اختلاف دیگر جاری نیست.")
    if dispute.status in (DisputeStatus.resolved_by_ai, DisputeStatus.resolved_by_agreement):
        raise invalid_state("این اختلاف قبلاً بسته شده است.")

    if party is Party.customer:
        proposal.customer_approved_at = proposal.customer_approved_at or now()
    else:
        proposal.specialist_approved_at = proposal.specialist_approved_at or now()

    if proposal.customer_approved_at is None or proposal.specialist_approved_at is None:
        await audit.record(
            session,
            "dispute_resolution_approved_by_party",
            request_id=request.id,
            actor_id=actor_id,
            actor_role=party.value,
            subject_id=proposal.id,
        )
        return None

    # Agreement reached before any ruling was applied: the frozen input is expired so a
    # late model answer cannot land afterwards.
    await _expire_snapshots(session, dispute)
    settlement = await _apply_settlement(
        session,
        dispute=dispute,
        request=request,
        selection=selection,
        source=SettlementSource.agreement,
        lines=proposal.lines,
        proposal_id=proposal.id,
        decision_id=None,
    )
    dispute.status = DisputeStatus.resolved_by_agreement
    dispute.resolved_at = now()
    dispute.bump()
    request.status = RequestStatus.closed_settled
    request.close_reason = CloseReason.settled_by_agreement
    request.closed_at = now()
    request.bump()
    await audit.record(
        session,
        "dispute_resolved_by_agreement",
        request_id=request.id,
        actor_id=actor_id,
        actor_role=party.value,
        subject_id=settlement.id,
        data={
            "totalToman": settlement.total_toman,
            "specialistPayableToman": settlement.specialist_payable_toman,
        },
    )
    return settlement


async def _apply_settlement(
    session: AsyncSession,
    *,
    dispute: Dispute,
    request: Request,
    selection: Selection,
    source: SettlementSource,
    lines: list[dict[str, Any]],
    proposal_id: uuid.UUID | None,
    decision_id: uuid.UUID | None,
) -> Settlement:
    """Write the one settlement for this collaboration.

    The totals are computed here from the lines, keeping each line's payer, so a part the
    customer bought is never added to the specialist's side again. A unique constraint on
    the selection makes a repeat application impossible.
    """
    existing = (
        await session.execute(
            select(Settlement).where(Settlement.selection_id == selection.id).with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    items = [LineItem.model_validate(line) for line in lines]
    totals = compute_totals(items)
    settlement = Settlement(
        request_id=request.id,
        selection_id=selection.id,
        dispute_id=dispute.id,
        source=source,
        decision_id=decision_id,
        proposal_id=proposal_id,
        lines=lines,
        total_toman=totals.total_toman or 0,
        specialist_payable_toman=totals.specialist_payable_toman or 0,
        applied_at=now(),
    )
    session.add(settlement)
    if selection.status is SelectionStatus.accepted:
        selection.status = SelectionStatus.ended
        selection.ended_at = now()
        selection.bump()
    await session.flush()

    from app.domain.evaluation import queue_for_closed_case

    await queue_for_closed_case(session, request=request, selection=selection)
    return settlement


async def build_snapshot(
    session: AsyncSession, dispute: Dispute, *, round_number: int | None = None
) -> DisputeSnapshot:
    """Freeze exactly what the model is allowed to see.

    Disputed and undisputed items are kept apart, the receipts carry their confirmation
    status, and the evidence list is the only set of ids a ruling may cite. Image content
    is never sent — only the text items and their confirmation state.
    """
    if round_number is None:
        # Numbered per snapshot taken, not per evidence round: a retry after a technical
        # failure freezes the input again and must not collide with the earlier attempt.
        round_number = (
            await session.scalar(
                select(DisputeSnapshot.round_number)
                .where(DisputeSnapshot.dispute_id == dispute.id)
                .order_by(DisputeSnapshot.round_number.desc())
                .limit(1)
            )
            or 0
        ) + 1

    request = await session.get(Request, dispute.request_id)
    selection = await session.get(Selection, dispute.selection_id)
    if request is None or selection is None:
        raise not_found("پروندهٔ این اختلاف پیدا نشد.")

    agreement = await active_agreement(session, selection.id)
    agreement_line_ids = {line.get("id") for line in (agreement.lines if agreement else [])}

    expense = (
        await session.execute(select(Expense).where(Expense.selection_id == selection.id))
    ).scalar_one_or_none()
    expense_versions: list[dict[str, Any]] = []
    confirmed_version_id: uuid.UUID | None = None
    if expense is not None:
        confirmed = await confirmed_receipt_version(session, expense.id)
        confirmed_version_id = confirmed.id if confirmed else None
        rows = (
            await session.execute(
                select(ExpenseVersion, ReceiptReview)
                .outerjoin(
                    ReceiptReview, ReceiptReview.expense_version_id == ExpenseVersion.id
                )
                .where(ExpenseVersion.expense_id == expense.id)
                .order_by(ExpenseVersion.version_number)
            )
        ).all()
        for version, review in rows:
            expense_versions.append(
                {
                    "expenseVersionId": str(version.id),
                    "versionNumber": version.version_number,
                    "lines": version.lines,
                    "totalToman": version.total_toman,
                    "receiptStatus": review.status.value if review else "pending",
                    "reviewedAt": review.reviewed_at.isoformat()
                    if review and review.reviewed_at
                    else None,
                    "rejectionReason": review.reason if review else None,
                }
            )

    statements = list(
        (
            await session.execute(
                select(DisputeStatement).where(
                    DisputeStatement.dispute_id == dispute.id,
                    DisputeStatement.is_current.is_(True),
                )
            )
        ).scalars()
    )
    attachments = list(
        (
            await session.execute(
                select(Attachment).where(
                    Attachment.request_id == request.id, Attachment.deleted_at.is_(None)
                )
            )
        ).scalars()
    )

    evidence: set[str] = {str(attachment.id) for attachment in attachments}
    evidence |= {str(version["expenseVersionId"]) for version in expense_versions}
    if agreement is not None:
        evidence.add(str(agreement.id))
    for statement in statements:
        # Only ids that already exist in the case become citable evidence.
        evidence |= {str(item) for item in statement.evidence_ids}
    for item in dispute.support_evidence:
        evidence.add(str(item["id"]))
        evidence |= {str(attachment) for attachment in item.get("attachmentIds", [])}
    evidence_ids = sorted(evidence)

    disputed_ids = [str(item.get("claimItemId")) for item in dispute.claim_items]
    undisputed_ids = [
        str(line_id) for line_id in agreement_line_ids if str(line_id) not in disputed_ids
    ]

    payload = {
        "disputeId": str(dispute.id),
        "requestStatus": request.status.value,
        "agreement": {
            "agreementVersionId": str(agreement.id) if agreement else None,
            "lines": agreement.lines if agreement else [],
            "totalToman": agreement.total_toman if agreement else None,
            "cancellationTerms": agreement.cancellation_terms if agreement else None,
        },
        "expenseVersions": expense_versions,
        "confirmedExpenseVersionId": str(confirmed_version_id)
        if confirmed_version_id
        else None,
        "claimItems": [
            {
                **item,
                "inActiveAgreement": item.get("claimItemId") in agreement_line_ids,
                "receiptConfirmedByCustomer": confirmed_version_id is not None,
            }
            for item in dispute.claim_items
        ],
        "statements": [
            {
                "party": statement.party.value,
                "body": statement.body,
                "itemPositions": statement.item_positions,
                "evidenceIds": statement.evidence_ids,
            }
            for statement in statements
        ],
        "respondedParties": [statement.party.value for statement in statements],
        "supportEvidence": [
            {"id": item["id"], "note": item["note"]} for item in dispute.support_evidence
        ],
        "evidenceIds": evidence_ids,
        "workStarted": selection.work_started_at is not None,
    }

    snapshot = DisputeSnapshot(
        dispute_id=dispute.id,
        round_number=round_number,
        payload=payload,
        disputed_item_ids=disputed_ids,
        undisputed_item_ids=undisputed_ids,
        evidence_ids=evidence_ids,
    )
    session.add(snapshot)
    await session.flush()
    dispute.current_snapshot_id = snapshot.id
    return snapshot


def validate_decision(
    output: DisputeDecisionOutput, snapshot: DisputeSnapshot, dispute: Dispute
) -> None:
    """Reject a ruling that exceeds the model's authority.

    It may only decide the items it was given, may not invent or repeat one, may not accept
    more quantity or money than the claim asked for, and every evidence id it cites must
    exist in this very snapshot.
    """
    if output.dispute_id != str(dispute.id):
        raise ValueError("حکم برای پروندهٔ دیگری صادر شده است.")
    if output.input_snapshot_id != str(snapshot.id):
        raise ValueError("حکم بر اساس ورودی جاری صادر نشده است.")

    allowed_evidence = set(snapshot.evidence_ids)
    for evidence_id in output.evidence_ids:
        if evidence_id not in allowed_evidence:
            raise ValueError("حکم به شاهدی استناد کرده که در ورودی همین بررسی نیست.")

    claims = {str(item.get("claimItemId")): item for item in dispute.claim_items}
    if output.status == "needs_evidence":
        if output.line_decisions:
            raise ValueError("در حالت نیاز به شواهد، تصمیم قلمی صادر نمی‌شود.")
        return

    decided = {decision.claim_item_id for decision in output.line_decisions}
    if decided != set(claims):
        raise ValueError("حکم باید دربارهٔ همهٔ اقلام مورد اختلاف تعیین تکلیف کند.")

    for decision in output.line_decisions:
        claim = claims[decision.claim_item_id]
        max_amount = int(claim.get("claimedAmountToman") or 0)
        max_quantity = int(claim.get("claimedQuantity") or 0)
        if decision.accepted_amount_toman > max_amount:
            raise ValueError("مبلغ پذیرفته‌شده از مبلغ ادعای همان قلم بیشتر است.")
        if decision.accepted_quantity > max_quantity:
            raise ValueError("مقدار پذیرفته‌شده از مقدار ادعای همان قلم بیشتر است.")
        for evidence_id in decision.evidence_ids:
            if evidence_id not in allowed_evidence:
                raise ValueError("شناسهٔ شاهد این قلم در ورودی بررسی وجود ندارد.")


def settlement_lines_from(
    output: DisputeDecisionOutput, snapshot: DisputeSnapshot
) -> list[dict[str, Any]]:
    """Build the settlement's line list in code.

    Undisputed performed items keep their own amounts and payers; disputed items are
    replaced by exactly what the ruling accepted. Nothing here comes from a number the
    model wrote for a line it was not given.
    """
    agreement_lines = {
        str(line.get("id")): line for line in snapshot.payload["agreement"]["lines"]
    }
    result: list[dict[str, Any]] = []

    for line_id in snapshot.undisputed_item_ids:
        line = agreement_lines.get(line_id)
        if line is not None:
            result.append(line)

    for decision in output.line_decisions:
        if decision.accepted_amount_toman <= 0:
            continue
        base = agreement_lines.get(decision.claim_item_id, {})
        claim: dict[str, Any] = next(
            (
                item
                for item in snapshot.payload["claimItems"]
                if str(item.get("claimItemId")) == decision.claim_item_id
            ),
            {},
        )
        result.append(
            {
                "id": decision.claim_item_id,
                "type": base.get("type") or claim.get("type") or "extra",
                "title": base.get("title") or claim.get("title") or "قلم مورد اختلاف",
                "quantity": decision.accepted_quantity or base.get("quantity") or 1,
                "minutes": base.get("minutes"),
                "amountToman": decision.accepted_amount_toman,
                "amountKnown": True,
                # The payer of each line is preserved from the original record, so a part
                # the customer bought is not moved onto the specialist's side.
                "suppliedBy": base.get("suppliedBy") or claim.get("suppliedBy") or "specialist",
                "paidTo": base.get("paidTo") or claim.get("paidTo") or "specialist",
            }
        )
    return result


async def apply_decision(
    session: AsyncSession,
    *,
    dispute_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> Settlement | None:
    """Apply a valid ruling exactly once, without asking either party again.

    A repeat call, a late ruling built on an expired snapshot, or a dispute already settled
    by agreement all return without creating a second settlement.
    """
    dispute = await lock_row(session, Dispute, dispute_id)
    decision = await lock_row(session, DisputeDecision, decision_id)
    if decision.applied_at is not None:
        return (
            await session.execute(
                select(Settlement).where(Settlement.dispute_id == dispute_id)
            )
        ).scalar_one_or_none()
    if decision.status != "decided":
        raise invalid_state("فقط حکم دارای وضعیت «صادرشده» قابل اعمال است.")
    if dispute.status in (DisputeStatus.resolved_by_agreement, DisputeStatus.resolved_by_ai):
        return None

    snapshot = await session.get(DisputeSnapshot, decision.snapshot_id)
    if snapshot is None or snapshot.expired_at is not None:
        raise invalid_state("ورودی این حکم منقضی شده و قابل اعمال نیست.")
    if dispute.current_snapshot_id != snapshot.id:
        raise invalid_state("این حکم بر اساس ورودی جاری پرونده صادر نشده است.")

    request = await lock_row(session, Request, dispute.request_id)
    selection = await lock_row(session, Selection, dispute.selection_id)

    output = DisputeDecisionOutput.model_validate(
        {
            "disputeId": str(dispute.id),
            "inputSnapshotId": str(snapshot.id),
            "status": decision.status,
            "lineDecisions": decision.line_decisions,
            "reason": decision.reason,
            "evidenceIds": decision.evidence_ids,
            "missingFields": decision.missing_fields,
        }
    )
    validate_decision(output, snapshot, dispute)

    settlement = await _apply_settlement(
        session,
        dispute=dispute,
        request=request,
        selection=selection,
        source=SettlementSource.ai_decision,
        lines=settlement_lines_from(output, snapshot),
        proposal_id=None,
        decision_id=decision.id,
    )
    decision.applied_at = now()
    dispute.status = DisputeStatus.resolved_by_ai
    dispute.resolved_at = now()
    dispute.bump()
    request.status = RequestStatus.closed_adjudicated
    request.close_reason = CloseReason.settled_by_adjudication
    request.closed_at = now()
    request.bump()

    await audit.record(
        session,
        "dispute_decision_applied",
        request_id=request.id,
        actor_role="system",
        subject_id=settlement.id,
        data={
            "source": "ai_decision",
            "totalToman": settlement.total_toman,
            "specialistPayableToman": settlement.specialist_payable_toman,
            # Recording and notifying the binding amount; no repair money moves in the MVP.
            "moneyMoved": False,
        },
    )
    return settlement


async def add_support_evidence(
    session: AsyncSession,
    *,
    dispute_id: uuid.UUID,
    support_id: uuid.UUID,
    note: str,
    attachment_ids: list[str],
) -> Dispute:
    """Support completes the record while the ruling waits for evidence.

    This is the spec's support role and its limit: support fills gaps in the material and
    never issues, rewrites or overrides the ruling. Adding evidence expires the frozen
    input, so the next round sees the completed record rather than the old one.
    """
    dispute = await lock_row(session, Dispute, dispute_id)
    if dispute.status not in (
        DisputeStatus.needs_evidence,
        DisputeStatus.awaiting_ai,
        DisputeStatus.dispute_open,
    ):
        raise invalid_state("تکمیل شواهد فقط تا پیش از صدور حکم ممکن است.")
    if not note:
        raise validation_error("متن شاهد تکمیلی الزامی است.", note="متن الزامی است.")

    dispute.support_evidence = [
        *dispute.support_evidence,
        {
            "id": f"support-{uuid.uuid4().hex[:12]}",
            "at": now().isoformat(),
            "note": note[:2000],
            "attachmentIds": attachment_ids,
        },
    ]
    dispute.bump()
    await _expire_snapshots(session, dispute)

    await audit.record(
        session,
        "dispute_support_evidence_added",
        request_id=dispute.request_id,
        actor_id=support_id,
        actor_role="support",
        subject_id=dispute.id,
        reason=note[:400],
    )
    return dispute


async def extend_budget(
    session: AsyncSession,
    *,
    dispute_id: uuid.UUID,
    support_id: uuid.UUID,
    reason: str,
    policy: Policy,
) -> Dispute:
    """Support grants this dispute one more go at its own operational budget.

    Allowed at most once, only after the automatic work stopped, and only with a recorded
    reason. The earlier runs stay on record; their holds simply stop counting against the
    cap. A party asking again never extends anything, and the installation-wide daily
    spend cap still applies.
    """
    dispute = await lock_row(session, Dispute, dispute_id)
    if dispute.status is not DisputeStatus.awaiting_ai:
        raise invalid_state("تمدید بودجه فقط برای پروندهٔ متوقف‌شده ممکن است.")
    if dispute.budget_extensions_used >= policy.ai.dispute_budget_extensions:
        raise invalid_state("بودجهٔ این اختلاف قبلاً یک بار تمدید شده است.")
    if not reason:
        raise validation_error("ثبت دلیل تمدید الزامی است.", reason="دلیل الزامی است.")

    from app.models import AiRun, BudgetReservation

    holds = (
        await session.execute(
            select(BudgetReservation)
            .join(AiRun, AiRun.id == BudgetReservation.ai_run_id)
            .where(
                BudgetReservation.scope == "operations",
                BudgetReservation.scope_key == f"dispute:{dispute_id}",
                BudgetReservation.released_at.is_(None),
            )
        )
    ).scalars()
    for hold in holds:
        hold.released_at = now()

    dispute.budget_extensions_used += 1
    dispute.status = DisputeStatus.reviewing
    dispute.bump()

    await audit.record(
        session,
        "dispute_budget_extended",
        request_id=dispute.request_id,
        actor_id=support_id,
        actor_role="support",
        subject_id=dispute.id,
        reason=reason,
    )
    return dispute


async def can_enter_adjudication(dispute: Dispute) -> bool:
    both_closed = (
        dispute.customer_closed_statements_at is not None
        and dispute.specialist_closed_statements_at is not None
    )
    return both_closed or dispute.statement_deadline <= now()


async def require_party(
    session: AsyncSession, dispute: Dispute, user_id: uuid.UUID, *, is_support: bool
) -> None:
    if is_support:
        return
    request = await session.get(Request, dispute.request_id)
    selection = await session.get(Selection, dispute.selection_id)
    if request is None or selection is None:
        raise not_found()
    if user_id not in (request.customer_id, selection.specialist_id):
        raise forbidden("شما طرف این اختلاف نیستید.")
