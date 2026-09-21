"""Selection, acceptance, agreements, appointment changes and cancellation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CustomerDep, PolicyDep, SessionDep, SpecialistDep, UserDep
from app.api.serializers import agreement_out, request_out, selection_out
from app.domain import agreements as agreement_service
from app.domain import selection as selection_service
from app.domain.errors import forbidden, not_found
from app.models import AgreementVersion, Request, Selection
from app.models.enums import Role
from app.schemas.api import (
    AcceptSelectionInput,
    AgreementOut,
    CancelInput,
    ProposeAgreementInput,
    RejectSelectionInput,
    ReplaceSpecialistInput,
    RequestOut,
    RevisionInput,
    SelectionOut,
    SelectOfferInput,
)

router = APIRouter(tags=["collaboration"])


async def _party_check(session: SessionDep, selection: Selection, user: UserDep) -> None:
    if user.role is Role.support:
        return
    request = await session.get(Request, selection.request_id)
    if request is None:
        raise not_found()
    if user.user_id not in (request.customer_id, selection.specialist_id):
        raise forbidden("شما طرف این همکاری نیستید.")


@router.post("/requests/{request_id}/selection", response_model=SelectionOut, status_code=201)
async def select_offer(
    request_id: uuid.UUID,
    payload: SelectOfferInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
) -> SelectionOut:
    """Pick one specialist. The binding-arbitration clause must be accepted first."""
    selection = await selection_service.select_offer(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        offer_version_id=payload.offer_version_id,
        expected_revision=payload.expected_revision,
        policy=policy,
        accepted_arbitration=payload.accepted_arbitration,
    )
    return selection_out(selection)


@router.get("/requests/{request_id}/selection", response_model=SelectionOut | None)
async def get_selection(
    request_id: uuid.UUID, session: SessionDep, user: UserDep
) -> SelectionOut | None:
    selection = (
        await session.execute(
            select(Selection)
            .where(Selection.request_id == request_id)
            .order_by(Selection.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if selection is None:
        return None
    await _party_check(session, selection, user)
    return selection_out(selection)


@router.post("/selections/{selection_id}/accept", response_model=SelectionOut)
async def accept_selection(
    selection_id: uuid.UUID,
    payload: AcceptSelectionInput,
    session: SessionDep,
    specialist: SpecialistDep,
    policy: PolicyDep,
) -> SelectionOut:
    """Acceptance opens the working chat, and puts a fixed offer's terms in force."""
    selection = await selection_service.accept_selection(
        session,
        selection_id=selection_id,
        specialist_id=specialist.user_id,
        expected_revision=payload.expected_revision,
        accepted_arbitration=payload.accepted_arbitration,
        policy=policy,
    )
    return selection_out(selection)


@router.post("/selections/{selection_id}/reject", response_model=SelectionOut)
async def reject_selection(
    selection_id: uuid.UUID,
    payload: RejectSelectionInput,
    session: SessionDep,
    specialist: SpecialistDep,
) -> SelectionOut:
    selection = await selection_service.reject_selection(
        session,
        selection_id=selection_id,
        specialist_id=specialist.user_id,
        expected_revision=payload.expected_revision,
        cannot_perform=payload.cannot_perform,
        reason=payload.reason,
    )
    return selection_out(selection)


@router.post("/requests/{request_id}/replace-specialist", response_model=RequestOut)
async def replace_specialist(
    request_id: uuid.UUID,
    payload: ReplaceSpecialistInput,
    session: SessionDep,
    customer: CustomerDep,
    policy: PolicyDep,
) -> RequestOut:
    """Once, and only before work starts. The previous specialist loses access at once."""
    request = await selection_service.replace_specialist(
        session,
        request_id=request_id,
        customer_id=customer.user_id,
        expected_revision=payload.expected_revision,
        policy=policy,
        reason=payload.reason,
    )
    return await request_out(session, request)


@router.post(
    "/selections/{selection_id}/agreements", response_model=AgreementOut, status_code=201
)
async def propose_agreement(
    selection_id: uuid.UUID,
    payload: ProposeAgreementInput,
    session: SessionDep,
    user: UserDep,
    policy: PolicyDep,
) -> AgreementOut:
    agreement = await agreement_service.propose_version(
        session,
        selection_id=selection_id,
        actor_id=user.user_id,
        lines=list(payload.lines),
        scenarios=[
            scenario.model_dump(mode="json", by_alias=True) for scenario in payload.scenarios
        ],
        scheduled_at=payload.scheduled_at,
        warranty_note=payload.warranty_note,
        change_reason=payload.change_reason,
        evidence_ids=payload.evidence_ids,
        policy=policy,
        expected_revision=payload.expected_revision,
    )
    return await agreement_out(session, agreement)


@router.get("/selections/{selection_id}/agreements", response_model=list[AgreementOut])
async def list_agreements(
    selection_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[AgreementOut]:
    selection = await session.get(Selection, selection_id)
    if selection is None:
        raise not_found("همکاری پیدا نشد.")
    await _party_check(session, selection, user)
    rows = await session.execute(
        select(AgreementVersion)
        .where(AgreementVersion.selection_id == selection_id)
        .order_by(AgreementVersion.version_number)
    )
    return [await agreement_out(session, row) for row in rows.scalars()]


@router.post("/agreements/{agreement_id}/approve", response_model=AgreementOut)
async def approve_agreement(
    agreement_id: uuid.UUID,
    payload: RevisionInput,
    session: SessionDep,
    user: UserDep,
) -> AgreementOut:
    """The second approval of this exact version activates it."""
    agreement = await agreement_service.approve_version(
        session,
        agreement_id=agreement_id,
        actor_id=user.user_id,
        expected_revision=payload.expected_revision,
    )
    return await agreement_out(session, agreement)


@router.post("/agreements/{agreement_id}/reject", response_model=AgreementOut)
async def reject_agreement(
    agreement_id: uuid.UUID,
    payload: CancelInput,
    session: SessionDep,
    user: UserDep,
) -> AgreementOut:
    agreement = await agreement_service.reject_version(
        session,
        agreement_id=agreement_id,
        actor_id=user.user_id,
        expected_revision=payload.expected_revision,
        reason=payload.reason,
    )
    return await agreement_out(session, agreement)


@router.post("/selections/{selection_id}/start", response_model=SelectionOut)
async def start_work(
    selection_id: uuid.UUID,
    payload: RevisionInput,
    session: SessionDep,
    specialist: SpecialistDep,
) -> SelectionOut:
    selection = await agreement_service.start_work(
        session,
        selection_id=selection_id,
        actor_id=specialist.user_id,
        expected_revision=payload.expected_revision,
    )
    return selection_out(selection)


@router.post("/selections/{selection_id}/cancel", response_model=SelectionOut)
async def cancel_collaboration(
    selection_id: uuid.UUID,
    payload: CancelInput,
    session: SessionDep,
    user: UserDep,
) -> SelectionOut:
    """Either side may walk away; a claim is recorded, not owed."""
    selection = await agreement_service.cancel_collaboration(
        session,
        selection_id=selection_id,
        actor_id=user.user_id,
        expected_revision=payload.expected_revision,
        reason=payload.reason,
        claimed_amount_toman=payload.claimed_amount_toman,
    )
    return selection_out(selection)
