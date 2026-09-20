"""Demo sign-in, sign-out and the current session."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select

from app import clock
from app.api.deps import PrincipalDep, SessionDep
from app.config import settings
from app.domain import auth
from app.domain.errors import forbidden
from app.models import SpecialistProfile
from app.models.enums import Role
from app.schemas.api import CurrentUser, DemoAccount, SignInRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/demo-accounts", response_model=list[DemoAccount])
async def demo_accounts(session: SessionDep) -> list[DemoAccount]:
    """The seeded account picker. No phone number, address or real person is used."""
    users = await auth.list_demo_accounts(session)
    result: list[DemoAccount] = []
    for user in users:
        shop = None
        city = None
        if user.role is Role.specialist:
            profile = (
                await session.execute(
                    select(SpecialistProfile).where(SpecialistProfile.user_id == user.id)
                )
            ).scalar_one_or_none()
            if profile is not None and profile.is_archive_only:
                # Archive specialists exist only to back the seeded reference data.
                continue
            shop = profile.shop_name if profile else None
            city = profile.city if profile else None
        result.append(
            DemoAccount(
                login_key=user.login_key,
                display_name=user.display_name,
                role=user.role,
                shop_name=shop,
                city=city,
            )
        )
    return result


@router.post("/sign-in", response_model=CurrentUser)
async def sign_in(
    payload: SignInRequest, session: SessionDep, response: Response
) -> CurrentUser:
    _, raw_token = await auth.sign_in(session, payload.login_key)
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
        max_age=settings.session_ttl_hours * 3600,
    )
    principal = await auth.resolve(session, raw_token)
    if principal is None:
        raise forbidden("ایجاد نشست ناموفق بود.")
    return _current_user(principal)


@router.post("/sign-out", status_code=204)
async def sign_out(principal: PrincipalDep, session: SessionDep, response: Response) -> None:
    if principal is not None:
        await auth.sign_out(session, principal.session_id)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=CurrentUser | None)
async def me(principal: PrincipalDep) -> CurrentUser | None:
    return _current_user(principal) if principal else None


def _current_user(principal: auth.Principal) -> CurrentUser:
    return CurrentUser(
        user_id=principal.user_id,
        display_name=principal.display_name,
        role=principal.role,
        app_mode=settings.app_mode,
        ai_mode=settings.ai_mode,
        server_time=clock.now(),
        clock_offset_seconds=clock.offset().total_seconds(),
    )
