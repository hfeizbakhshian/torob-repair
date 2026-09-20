"""Disputes: claims, statements, voluntary settlement and the binding ruling."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AiDep, PolicyDep, SessionDep, UserDep
from app.api.serializers import dispute_out
from app.domain import ai_flows
from app.domain import disputes as service
from app.domain.errors import invalid_state, not_found
from app.models import Dispute, DisputeResolutionProposal
from app.models.enums import DisputeStatus
from app.schemas.api import (
    DisputeOut,
    OpenDisputeInput,
    ResolutionProposalInput,
    RevisionInput,
    StatementInput,
)

router = APIRouter(tags=["disputes"])


@router.post("/requests/{request_id}/dispute", response_model=DisputeOut, status_code=201)
async def open_dispute(
    request_id: uuid.UUID,
    payload: OpenDisputeInput,
    session: SessionDep,
    user: UserDep,
    policy: PolicyDep,
) -> DisputeOut:
    """One process per case; re-submitting the same dispute never creates a second one."""
    dispute = await service.open_dispute(
        session,
        request_id=request_id,
        actor_id=user.user_id,
        claim_items=[
            item.model_dump(mode="json", by_alias=True) for item in payload.claim_items
        ],
        policy=policy,
    )
    return await dispute_out(session, dispute)


@router.get("/requests/{request_id}/dispute", response_model=DisputeOut | None)
async def get_dispute(
    request_id: uuid.UUID, session: SessionDep, user: UserDep
) -> DisputeOut | None:
    dispute = (
        await session.execute(select(Dispute).where(Dispute.request_id == request_id))
    ).scalar_one_or_none()
    if dispute is None:
        return None
    await service.require_party(session, dispute, user.user_id, is_support=user.is_support)
    return await dispute_out(session, dispute)


@router.post("/disputes/{dispute_id}/statements", response_model=DisputeOut)
async def submit_statement(
    dispute_id: uuid.UUID,
    payload: StatementInput,
    session: SessionDep,
    user: UserDep,
) -> DisputeOut:
    """Editing a statement expires the frozen input, so a late ruling cannot land."""
    await service.submit_statement(
        session,
        dispute_id=dispute_id,
        actor_id=user.user_id,
        body=payload.body,
        item_positions=payload.item_positions,
        evidence_ids=payload.evidence_ids,
    )
    dispute = await session.get(Dispute, dispute_id)
    if dispute is None:
        raise not_found()
    return await dispute_out(session, dispute)


@router.post("/disputes/{dispute_id}/close-statements", response_model=DisputeOut)
async def close_statements(
    dispute_id: uuid.UUID, session: SessionDep, user: UserDep
) -> DisputeOut:
    """When both sides declare they are finished, adjudication may start early."""
    dispute = await service.close_statements(
        session, dispute_id=dispute_id, actor_id=user.user_id
    )
    return await dispute_out(session, dispute)


@router.post("/disputes/{dispute_id}/proposals", response_model=DisputeOut, status_code=201)
async def propose_resolution(
    dispute_id: uuid.UUID,
    payload: ResolutionProposalInput,
    session: SessionDep,
    user: UserDep,
) -> DisputeOut:
    await service.propose_resolution(
        session,
        dispute_id=dispute_id,
        actor_id=user.user_id,
        lines=list(payload.lines),
        note=payload.note,
    )
    dispute = await session.get(Dispute, dispute_id)
    if dispute is None:
        raise not_found()
    return await dispute_out(session, dispute)


@router.post("/dispute-proposals/{proposal_id}/approve", response_model=DisputeOut)
async def approve_resolution(
    proposal_id: uuid.UUID,
    payload: RevisionInput,
    session: SessionDep,
    user: UserDep,
) -> DisputeOut:
    """The second approval applies the settlement and expires any pending ruling."""
    proposal = await session.get(DisputeResolutionProposal, proposal_id)
    if proposal is None:
        raise not_found("پیشنهاد حل اختلاف پیدا نشد.")
    await service.approve_resolution(
        session,
        proposal_id=proposal_id,
        actor_id=user.user_id,
        expected_revision=payload.expected_revision,
    )
    dispute = await session.get(Dispute, proposal.dispute_id)
    if dispute is None:
        raise not_found()
    return await dispute_out(session, dispute)


@router.post("/disputes/{dispute_id}/adjudicate", response_model=DisputeOut)
async def adjudicate(
    dispute_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    ai: AiDep,
) -> DisputeOut:
    """Run the binding ruling.

    Allowed once the statement window has closed or both sides declared they are finished.
    A valid ruling applies without asking either party again.
    """
    dispute = await session.get(Dispute, dispute_id)
    if dispute is None:
        raise not_found("اختلاف پیدا نشد.")
    await service.require_party(session, dispute, user.user_id, is_support=user.is_support)
    if dispute.status in (
        DisputeStatus.resolved_by_ai,
        DisputeStatus.resolved_by_agreement,
    ):
        return await dispute_out(session, dispute)
    if not await service.can_enter_adjudication(dispute):
        raise invalid_state(
            "تا پایان فرصت اظهارات یا اعلام پایان اظهارات هر دو طرف، داوری آغاز نمی‌شود."
        )
    await session.commit()

    await ai_flows.adjudicate_dispute(ai, dispute_id=dispute_id)

    refreshed = await session.get(Dispute, dispute_id)
    if refreshed is None:
        raise not_found()
    await session.refresh(refreshed)
    return await dispute_out(session, refreshed)
