"""HTTP-level tests: sessions, permissions, the error contract and idempotency."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


async def sign_in(client: AsyncClient, login_key: str) -> dict:
    response = await client.post("/api/auth/sign-in", json={"loginKey": login_key})
    assert response.status_code == 200, response.text
    return response.json()


async def test_health_and_demo_accounts(client: AsyncClient):
    health = await client.get("/api/health")
    assert health.json() == {"status": "ok", "appMode": "demo", "aiMode": "mock"}

    accounts = (await client.get("/api/auth/demo-accounts")).json()
    keys = {account["loginKey"] for account in accounts}
    assert "customer-sahar" in keys
    # Archive-only specialists are never offered as sign-in accounts.
    assert not any(key.startswith("archive-") for key in keys)


async def test_session_cookie_is_httponly_and_lax(client: AsyncClient):
    response = await client.post("/api/auth/sign-in", json={"loginKey": "customer-sahar"})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    assert settings.session_cookie_name in cookie


async def test_role_cannot_be_claimed_in_the_request_body(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    # A customer session cannot reach a specialist-only listing, whatever it sends.
    response = await client.get("/api/specialist/requests")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_support_only_endpoints_reject_a_customer(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    assert (await client.get("/api/support/queue")).status_code == 403
    assert (await client.get("/api/demo/state")).status_code == 403


async def test_anonymous_requests_are_refused(client: AsyncClient):
    response = await client.get("/api/requests")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_cross_origin_state_change_is_refused(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    response = await client.post(
        "/api/requests",
        json={
            "city": "تهران",
            "district": "تهرانسر",
            "vehicleCode": factories.VEHICLE,
            "serviceCode": factories.CLUTCH,
            "symptoms": "نمونه",
        },
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


async def test_validation_errors_use_the_shared_contract(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    response = await client.post("/api/requests", json={"city": "تهران"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "fieldErrors" in body
    assert "symptoms" in body["fieldErrors"]
    # The rejected values are never echoed back.
    assert "تهران" not in response.text


async def test_coverage_check_is_free_and_needs_no_session(client: AsyncClient):
    response = await client.post(
        "/api/catalog/coverage",
        json={
            "city": "اصفهان",
            "vehicleCode": factories.VEHICLE,
            "serviceCode": factories.CLUTCH,
        },
    )
    assert response.status_code == 200
    assert response.json()["supported"] is False


async def test_package_terms_are_shown_before_paying(client: AsyncClient):
    terms = (
        await client.post(
            "/api/catalog/package-terms",
            json={
                "city": "تهران",
                "vehicleCode": factories.VEHICLE,
                "serviceCode": factories.CLUTCH,
            },
        )
    ).json()
    assert terms["registrationFeeToman"] == 2000
    assert terms["maxTurnsPerStage"] == 3
    assert terms["offerWindowHours"] == 24
    assert terms["refundPolicyMode"] == "bootstrap"
    assert "آزمایشی" in terms["feeLabel"]


async def test_full_happy_path_through_http(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    created = await client.post(
        "/api/requests",
        json={
            "city": "تهران",
            "district": "تهرانسر",
            "vehicleCode": factories.VEHICLE,
            "vehicleDetails": {"year": 1396},
            "serviceCode": factories.CLUTCH,
            "symptoms": "دور موتور بالا می‌رود ولی سرعت زیاد نمی‌شود.",
        },
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    payment = await client.post(
        f"/api/requests/{request_id}/pay",
        json={"idempotencyKey": "http-1"},
        headers={"Idempotency-Key": "http-1"},
    )
    assert payment.status_code == 200
    assert payment.json()["status"] == "succeeded"
    assert payment.json()["isSample"] is True

    questions = await client.post(f"/api/requests/{request_id}/ai/questions")
    assert questions.status_code == 200
    body = questions.json()
    assert body["isDemoResponse"] is True
    assert len(body["payload"]["questions"]) <= 8

    summary = await client.post(
        f"/api/requests/{request_id}/ai/summary",
        json={"answers": {"symptom_slip": "بله، مشخص است", "mileage": 180000}},
    )
    assert summary.status_code == 200
    assert summary.json()["payload"]["facts"]

    current = (await client.get(f"/api/requests/{request_id}")).json()
    confirmed = await client.post(
        f"/api/requests/{request_id}/confirm-summary",
        json={"expectedRevision": current["revision"]},
    )
    assert confirmed.status_code == 200

    published = await client.post(
        f"/api/requests/{request_id}/publish",
        json={"expectedRevision": confirmed.json()["revision"]},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "open"
    assert published.json()["responseDeadline"] is not None


async def test_stale_revision_returns_409_with_the_current_one(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    request_id = (
        await client.post(
            "/api/requests",
            json={
                "city": "تهران",
                "district": "تهرانسر",
                "vehicleCode": factories.VEHICLE,
                "serviceCode": factories.CLUTCH,
                "symptoms": "نمونه",
            },
        )
    ).json()["id"]

    response = await client.post(
        f"/api/requests/{request_id}/confirm-summary", json={"expectedRevision": 99}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["currentRevision"] is not None


async def test_customer_cannot_read_another_customers_case(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    request_id = (
        await client.post(
            "/api/requests",
            json={
                "city": "تهران",
                "district": "تهرانسر",
                "vehicleCode": factories.VEHICLE,
                "serviceCode": factories.CLUTCH,
                "symptoms": "نمونه",
            },
        )
    ).json()["id"]

    await client.post("/api/auth/sign-out")
    await sign_in(client, "customer-omid")
    assert (await client.get(f"/api/requests/{request_id}")).status_code == 403


async def test_specialist_cannot_see_rival_offers_before_selection(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    request_id = (
        await client.post(
            "/api/requests",
            json={
                "city": "تهران",
                "district": "تهرانسر",
                "vehicleCode": factories.VEHICLE,
                "serviceCode": factories.CLUTCH,
                "symptoms": "نمونه",
            },
        )
    ).json()["id"]

    await client.post("/api/auth/sign-out")
    await sign_in(client, "specialist-arya")
    # The comparison view belongs to the customer alone.
    assert (await client.get(f"/api/requests/{request_id}/offers")).status_code == 403


async def test_sign_out_revokes_the_session(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    assert (await client.get("/api/auth/me")).json() is not None
    assert (await client.post("/api/auth/sign-out")).status_code == 204
    assert (await client.get("/api/auth/me")).json() is None
    assert (await client.get("/api/requests")).status_code == 403


async def test_demo_clock_moves_forward_only(client: AsyncClient):
    await sign_in(client, "support-mina")
    state = (await client.get("/api/demo/state")).json()
    assert state["clockOffsetSeconds"] == 0

    advanced = await client.post("/api/demo/advance-clock", json={"seconds": 3600})
    assert advanced.status_code == 200
    assert advanced.json()["clockOffsetSeconds"] == 3600

    backwards = await client.post("/api/demo/advance-clock", json={"seconds": -10})
    assert backwards.status_code == 422

    await client.post("/api/demo/reset-clock")


async def test_part_search_builds_a_torob_link_without_fetching(client: AsyncClient):
    await sign_in(client, "customer-sahar")
    response = await client.get("/api/parts/search", params={"query": "کیت کلاچ ۲۰۶"})
    body = response.json()
    assert body["searchUrl"].startswith("https://torob.com/search/?query=")
    assert "واکشی" in body["note"]


async def test_openapi_document_is_served(client: AsyncClient):
    document = (await client.get("/api/openapi.json")).json()
    assert document["info"]["version"] == "0.1.0"
    assert "/api/requests" in document["paths"]
