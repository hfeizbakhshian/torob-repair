"""Reproducible demo data.

Everything here is explicitly sample data: the names are invented, no real phone number or
address is used, and the prices are interface examples rather than real service prices. The
archive cases exist to exercise the reference and refund policies — they never make a real
comparison group ready.

Running this again is safe: it fills in what is missing and does not delete anything.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now
from app.db import dispose, session_scope
from app.domain.policy_service import ensure_active_policy
from app.domain.reference import group_key
from app.models import (
    Coverage,
    ReferenceCase,
    ServiceTemplate,
    SpecialistProfile,
    User,
)
from app.models.enums import Party, ReferenceQuality, Role

CITY = "تهران"
VEHICLE = "peugeot_206_type_5"
VEHICLE_LABEL = "پژو ۲۰۶ تیپ ۵"

SERVICE_TEMPLATES: list[dict[str, Any]] = [
    {
        "code": "clutch_kit_replacement",
        "title_fa": "بررسی و تعویض کیت کلاچ",
        "summary_fa": (
            "بررسی عملکرد کلاچ و در صورت نیاز تعویض کیت کامل (دیسک، صفحه و بلبرینگ)."
        ),
        "allowed_offer_types": ["fixed", "conditional"],
        "required_facts": ["کارکرد خودرو", "نشانهٔ لغزش کلاچ", "حس پدال"],
        "reference_labor_minutes": 210,
        "part_specs": [
            {
                "key": "clutch_kit",
                "title": "کیت کلاچ کامل",
                "searchQuery": "کیت کلاچ پژو ۲۰۶ تیپ ۵",
            }
        ],
        "intake_questions": [
            {"id": "symptom_slip", "type": "single_choice"},
            {"id": "pedal_feel", "type": "single_choice"},
            {"id": "noise", "type": "single_choice"},
            {"id": "mileage", "type": "number"},
        ],
    },
    {
        "code": "front_brake_pad_replacement",
        "title_fa": "بررسی و تعویض لنت ترمز جلو",
        "summary_fa": "بررسی ضخامت لنت و دیسک جلو و تعویض لنت در صورت نیاز.",
        "allowed_offer_types": ["fixed", "conditional"],
        "required_facts": ["صدای ترمز", "لرزش هنگام ترمز", "آخرین تعویض"],
        "reference_labor_minutes": 60,
        "part_specs": [
            {
                "key": "front_pads",
                "title": "لنت ترمز جلو",
                "searchQuery": "لنت ترمز جلو پژو ۲۰۶ تیپ ۵",
            }
        ],
        "intake_questions": [
            {"id": "brake_noise", "type": "single_choice"},
            {"id": "brake_vibration", "type": "single_choice"},
            {"id": "last_change", "type": "number"},
        ],
    },
    {
        "code": "cooling_system_diagnosis",
        "title_fa": "عیب‌یابی سیستم خنک‌کاری",
        "summary_fa": (
            "بررسی علت داغ‌کردن موتور. این خدمت فقط مجوز بررسی است و تعمیر یا تعویض "
            "قطعه باید جداگانه به توافق برسد."
        ),
        "allowed_offer_types": ["diagnostic"],
        "required_facts": ["شرایط داغ‌کردن", "افت مایع خنک‌کننده", "کارکرد فن"],
        "reference_labor_minutes": 90,
        "part_specs": [],
        "intake_questions": [
            {"id": "temp_gauge", "type": "single_choice"},
            {"id": "coolant_loss", "type": "single_choice"},
            {"id": "fan_works", "type": "single_choice"},
        ],
    },
]

CUSTOMERS = [
    ("customer-sahar", "سحر نمونه"),
    ("customer-omid", "امید نمونه"),
]
SUPPORT = ("support-mina", "مینا نمونه (پشتیبانی)")

# Three specialists are relevant to the main scenario; three are deliberately not —
# by city, by vehicle or by specialism.
RELEVANT_SPECIALISTS = [
    ("specialist-arya", "آریا نمونه", "تعمیرگاه نمونهٔ آریا", "تهرانسر", 380_000),
    ("specialist-behnam", "بهنام نمونه", "تعمیرگاه نمونهٔ بهنام", "شهرزیبا", 420_000),
    ("specialist-kaveh", "کاوه نمونه", "تعمیرگاه نمونهٔ کاوه", "جنت‌آباد", 350_000),
]
IRRELEVANT_SPECIALISTS = [
    # wrong city
    ("specialist-rasht", "نیما نمونه", "تعمیرگاه نمونهٔ نیما", "رشت", "رشت", None, None),
    # wrong vehicle
    ("specialist-heavy", "سعید نمونه", "تعمیرگاه نمونهٔ سعید", CITY, "پیروزی", "pride_111", None),
    # wrong specialism
    (
        "specialist-body",
        "پریسا نمونه",
        "تعمیرگاه نمونهٔ پریسا",
        CITY,
        "نارمک",
        None,
        "body_paint",
    ),
]
# Four independent archive specialists back the reference groups; three cases each keeps
# every one of them under the 40% share cap.
ARCHIVE_SPECIALISTS = [
    ("archive-1", "تعمیرگاه آرشیوی ۱"),
    ("archive-2", "تعمیرگاه آرشیوی ۲"),
    ("archive-3", "تعمیرگاه آرشیوی ۳"),
    ("archive-4", "تعمیرگاه آرشیوی ۴"),
]

REFERENCE_TOTALS = {
    "clutch_kit_replacement": [7_200_000, 7_500_000, 7_800_000],
    "front_brake_pad_replacement": [2_100_000, 2_250_000, 2_400_000],
    "cooling_system_diagnosis": [900_000, 950_000, 1_000_000],
}


async def _user(
    session: AsyncSession, login_key: str, display_name: str, role: Role
) -> tuple[User, bool]:
    """Returns the account and whether this run created it."""
    user = (
        await session.execute(select(User).where(User.login_key == login_key))
    ).scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(login_key=login_key, display_name=display_name, role=role)
    session.add(user)
    await session.flush()
    return user, True


async def _profile(
    session: AsyncSession,
    user: User,
    *,
    shop_name: str,
    city: str,
    district: str,
    service_codes: list[str],
    vehicle_codes: list[str],
    hourly_rate: int | None,
    archive_only: bool = False,
) -> SpecialistProfile:
    profile = (
        await session.execute(
            select(SpecialistProfile).where(SpecialistProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = SpecialistProfile(
            user_id=user.id,
            shop_name=shop_name,
            city=city,
            district=district,
            service_codes=service_codes,
            vehicle_codes=vehicle_codes,
            visit_modes=["shop"],
            hourly_rate_toman=hourly_rate,
            is_archive_only=archive_only,
        )
        session.add(profile)
        await session.flush()
    return profile


async def seed(session: AsyncSession) -> dict[str, int]:
    counts = {"users": 0, "templates": 0, "coverage": 0, "reference_cases": 0}
    await ensure_active_policy(session)

    for template in SERVICE_TEMPLATES:
        existing = (
            await session.execute(
                select(ServiceTemplate).where(ServiceTemplate.code == template["code"])
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(ServiceTemplate(**template, reference_labor_source="نمونهٔ دمو"))
            counts["templates"] += 1

        existing_coverage = (
            await session.execute(
                select(Coverage).where(
                    Coverage.city == CITY,
                    Coverage.vehicle_code == VEHICLE,
                    Coverage.service_code == template["code"],
                    Coverage.visit_mode == "shop",
                )
            )
        ).scalar_one_or_none()
        if existing_coverage is None:
            session.add(
                Coverage(
                    city=CITY,
                    vehicle_code=VEHICLE,
                    service_code=template["code"],
                    visit_mode="shop",
                    is_supported=True,
                )
            )
            counts["coverage"] += 1
    await session.flush()

    all_services = [template["code"] for template in SERVICE_TEMPLATES]

    for login_key, name in CUSTOMERS:
        _, created = await _user(session, login_key, name, Role.customer)
        counts["users"] += int(created)
    _, created = await _user(session, SUPPORT[0], SUPPORT[1], Role.support)
    counts["users"] += int(created)

    for login_key, name, shop, district, rate in RELEVANT_SPECIALISTS:
        user, created = await _user(session, login_key, name, Role.specialist)
        await _profile(
            session,
            user,
            shop_name=shop,
            city=CITY,
            district=district,
            service_codes=all_services,
            vehicle_codes=[VEHICLE],
            hourly_rate=rate,
        )
        counts["users"] += int(created)

    for login_key, name, shop, city, district, vehicle, service in IRRELEVANT_SPECIALISTS:
        user, created = await _user(session, login_key, name, Role.specialist)
        await _profile(
            session,
            user,
            shop_name=shop,
            city=city,
            district=district,
            service_codes=[service] if service else all_services,
            vehicle_codes=[vehicle] if vehicle else [VEHICLE],
            hourly_rate=300_000,
        )
        counts["users"] += int(created)

    archive_users: list[User] = []
    for login_key, name in ARCHIVE_SPECIALISTS:
        user, created = await _user(session, login_key, name, Role.specialist)
        await _profile(
            session,
            user,
            shop_name=name,
            city=CITY,
            district="—",
            service_codes=all_services,
            vehicle_codes=[VEHICLE],
            hourly_rate=360_000,
            archive_only=True,
        )
        archive_users.append(user)
        counts["users"] += int(created)

    counts["reference_cases"] += await _seed_reference_cases(session, archive_users)
    return counts


async def _seed_reference_cases(
    session: AsyncSession, archive_users: list[User]
) -> int:
    """Twelve archive cases per template, three from each of four independent specialists."""
    created = 0
    moment = now()
    for template in SERVICE_TEMPLATES:
        code = template["code"]
        key = group_key(
            city=CITY,
            vehicle_code=VEHICLE,
            service_code=code,
            scenario_code=None,
            part_spec=None,
            part_condition="new",
            warranty_level="standard",
            buyer=Party.specialist,
        )
        existing = len(
            list(
                (
                    await session.execute(
                        select(ReferenceCase).where(ReferenceCase.group_key == key)
                    )
                ).scalars()
            )
        )
        if existing >= 12:
            continue

        totals = REFERENCE_TOTALS[code]
        for index, user in enumerate(archive_users):
            for offset, total in enumerate(totals):
                completed_at = moment - timedelta(days=5 + index * 3 + offset)
                session.add(
                    ReferenceCase(
                        id=uuid.uuid4(),
                        request_id=None,
                        group_key=key,
                        specialist_id=user.id,
                        city=CITY,
                        vehicle_code=VEHICLE,
                        service_code=code,
                        scenario_code=None,
                        part_spec=None,
                        part_condition="new",
                        warranty_level="standard",
                        buyer=Party.specialist,
                        total_toman=total,
                        part_toman=int(total * 0.6),
                        labor_toman=total - int(total * 0.6),
                        quality=ReferenceQuality.documented,
                        completed_at=completed_at,
                        consent_for_anonymous_use=True,
                        support_approved_at=completed_at,
                        # Demo rows: counted and shown, never enough to make a real group
                        # ready. Only the demo_reference_ready flag switches the policy.
                        is_sample=True,
                    )
                )
                created += 1
    return created


async def main() -> None:
    async with session_scope() as session:
        counts = await seed(session)
    print(
        "دادهٔ نمونه آماده شد: "
        f"{counts['users']} حساب، {counts['templates']} قالب خدمت، "
        f"{counts['coverage']} ردیف پوشش، {counts['reference_cases']} پروندهٔ آرشیوی."
    )
    print("همهٔ نام‌ها، قیمت‌ها و پرداخت‌ها نمونه‌اند و دادهٔ واقعی بازار نیستند.")
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
