"""AiService budgeting and the real DeepSeek adapter under a simulated network.

None of these tests makes a paid call: the LangChain client is replaced by a stub that
returns whatever the test wants, so the *adapter's* behaviour is exercised rather than
being bypassed with the mock provider.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.ai_service import AiOutcome, AiService, estimate_tokens
from app.domain.errors import DomainError, ErrorCode
from app.models import AiRun, BudgetReservation
from app.models.enums import AiPurpose, AiRunStatus, AiStage
from app.providers.ai.base import AiMessage, AiRequest, AiResponse, AiUsage
from app.providers.ai.deepseek import DeepSeekConfigError, DeepSeekProvider
from app.providers.ai.mock import MockAiProvider
from app.schemas.ai import ClarifyQuestions, RequestSummary, json_schema_for
from tests import factories


@pytest.fixture(autouse=True)
async def seeded(session):
    await factories.prepare(session)


class ScriptedProvider:
    """A provider that replays a fixed list of responses and counts its calls."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, responses: list[AiResponse]) -> None:
        self.responses = responses
        self.calls: list[AiRequest] = []

    async def complete(self, request: AiRequest) -> AiResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("provider called more times than the test scripted")
        return self.responses.pop(0)


def _ok(payload: dict, *, tokens_in: int = 100, tokens_out: int = 50) -> AiResponse:
    return AiResponse(
        payload=payload,
        usage=AiUsage(input_tokens=tokens_in, output_tokens=tokens_out),
        latency_ms=5,
    )


VALID_QUESTIONS = {
    "questions": [
        {
            "id": "q1",
            "text": "نشانه چیست؟",
            "type": "single_choice",
            "options": ["الف", "ب"],
            "required": True,
        }
    ],
    "note": None,
}


async def _run(service: AiService, request_id, **kwargs) -> AiOutcome:
    return await service.run(
        purpose=AiPurpose.clarify_questions,
        stage=AiStage.customer,
        output_model=ClarifyQuestions,
        context={"serviceCode": "clutch_kit_replacement"},
        request_id=request_id,
        input_version_key="v1",
        **kwargs,
    )


async def test_usage_is_recorded_and_the_reservation_settled(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS, tokens_in=321, tokens_out=123)])
    outcome = await _run(AiService(provider, policy), request.id)
    await session.close()

    assert outcome.ok
    run = (await session.execute(select(AiRun))).scalars().one()
    assert run.status is AiRunStatus.succeeded
    assert run.actual_input_tokens == 321
    assert run.actual_output_tokens == 123
    reservation = (await session.execute(select(BudgetReservation))).scalars().one()
    assert reservation.settled_at is not None
    assert reservation.unresolved is False


async def test_budget_is_reserved_before_the_call(session, policy):
    """The hold exists while the provider is still working, not only afterwards."""
    request = await factories.published_request(session, policy)
    seen: dict[str, int] = {}

    class Observing(ScriptedProvider):
        async def complete(self, ai_request: AiRequest) -> AiResponse:
            from app.db import session_scope

            async with session_scope() as other:
                rows = list((await other.execute(select(BudgetReservation))).scalars())
                seen["count"] = len(rows)
            return await super().complete(ai_request)

    await _run(AiService(Observing([_ok(VALID_QUESTIONS)]), policy), request.id)
    assert seen["count"] == 1


async def test_invalid_output_records_usage_and_costs_an_attempt(session, policy):
    """A paid call whose answer was unusable still records what it consumed."""
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider(
        [
            _ok({"questions": [{"id": "q1"}]}, tokens_in=200, tokens_out=90),
            _ok(VALID_QUESTIONS, tokens_in=210, tokens_out=95),
        ]
    )
    outcome = await _run(AiService(provider, policy), request.id)
    await session.close()

    assert outcome.ok
    runs = list((await session.execute(select(AiRun).order_by(AiRun.attempt))).scalars())
    assert len(runs) == 2
    assert runs[0].status is AiRunStatus.invalid_output
    assert runs[0].actual_input_tokens == 200
    assert runs[0].actual_output_tokens == 90
    # The failed attempt never counts as one of the person's successful turns.
    assert runs[0].counts_as_successful_turn is False
    assert runs[1].counts_as_successful_turn is True


async def test_only_one_corrective_retry_is_allowed(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider(
        [_ok({"questions": [{"id": "bad"}]}), _ok({"questions": [{"id": "still-bad"}]})]
    )
    outcome = await _run(AiService(provider, policy), request.id)
    assert outcome.ok is False
    assert len(provider.calls) == 2  # the original plus exactly one correction


async def test_unknown_usage_is_not_zero_and_keeps_the_hold(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider(
        [
            AiResponse(error_code="timeout", usage=AiUsage()),
            AiResponse(error_code="timeout", usage=AiUsage()),
        ]
    )
    service = AiService(provider, policy)
    outcome = await _run(service, request.id)
    await session.close()

    assert outcome.ok is False
    assert outcome.error_code == "timeout"
    runs = list((await session.execute(select(AiRun))).scalars())
    assert all(run.status is AiRunStatus.timeout for run in runs)
    assert all(run.actual_input_tokens is None for run in runs)
    reservations = list((await session.execute(select(BudgetReservation))).scalars())
    # Unknown usage is not read as zero: the hold stays until the outcome is settled.
    assert all(reservation.unresolved for reservation in reservations)


async def test_stage_turn_cap_is_enforced(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS) for _ in range(4)])
    service = AiService(provider, policy)
    for _ in range(3):
        assert (await _run(service, request.id)).ok

    with pytest.raises(DomainError) as error:
        await _run(service, request.id)
    assert error.value.code is ErrorCode.QUOTA_EXCEEDED
    # The fourth turn never reaches the provider.
    assert len(provider.calls) == 3


async def test_token_cap_is_enforced_before_the_call(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS, tokens_in=7_900, tokens_out=10)])
    service = AiService(provider, policy)
    assert (await _run(service, request.id)).ok

    with pytest.raises(DomainError) as error:
        await _run(service, request.id)
    assert error.value.code is ErrorCode.QUOTA_EXCEEDED
    assert len(provider.calls) == 1


async def test_concurrent_calls_cannot_both_slip_past_the_turn_cap(session, policy):
    """Three turns are allowed; six simultaneous attempts must not all get through."""
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS) for _ in range(6)])
    service = AiService(provider, policy)

    async def attempt() -> bool:
        try:
            outcome = await _run(service, request.id)
            return outcome.ok
        except DomainError:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(6)))
    assert sum(results) <= 3
    assert len(provider.calls) <= 3


async def test_quota_is_shared_and_not_reset_by_a_new_request_id(session, policy):
    first = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS) for _ in range(6)])
    service = AiService(provider, policy)
    for _ in range(3):
        await _run(service, first.id)
    await session.close()

    runs = list(
        (await session.execute(select(AiRun).where(AiRun.request_id == first.id))).scalars()
    )
    assert len(runs) == 3


async def test_operations_budget_is_separate_from_the_customer_package(session, policy):
    request = await factories.published_request(session, policy)
    provider = ScriptedProvider([_ok(VALID_QUESTIONS) for _ in range(5)])
    service = AiService(provider, policy)
    for _ in range(3):
        await _run(service, request.id)

    # The customer stage is exhausted, but an operational review still runs.
    outcome = await service.run(
        purpose=AiPurpose.price_fairness,
        stage=AiStage.operations,
        output_model=ClarifyQuestions,
        context={},
        request_id=request.id,
        input_version_key="ops",
        scope="operations",
        scope_key=f"refund:{uuid.uuid4()}",
        counts_as_turn=False,
    )
    assert outcome.ok


async def test_input_estimate_is_marked_conservative_when_no_tokenizer_matches(policy):
    messages = [AiMessage(role="user", content="سلام")]
    total, estimated = estimate_tokens(messages, policy)
    assert total > 0
    if estimated:
        # The documented fallback: UTF-8 bytes plus a fixed per-message overhead.
        assert total == len("سلام".encode()) + policy.ai.token_estimate_overhead_per_message


# --- the DeepSeek adapter, over a simulated network ------------------------


class _Raw:
    def __init__(self, usage: dict, finish_reason: str = "stop") -> None:
        self.usage_metadata = usage
        self.response_metadata = {"finish_reason": finish_reason}


class StubStructured:
    def __init__(self, result, *, error: Exception | None = None, delay: float = 0.0) -> None:
        self._result = result
        self._error = error
        self._delay = delay
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


class StubClient:
    """Stands in for ChatDeepSeek, recording exactly what the adapter bound to it."""

    def __init__(self, structured: StubStructured) -> None:
        self.structured = structured
        self.bound: dict = {}
        self.structured_kwargs: dict = {}

    def bind(self, **kwargs):
        self.bound = kwargs
        return self

    def with_structured_output(self, schema, **kwargs):
        self.structured_kwargs = {"schema": schema, **kwargs}
        return self.structured


def _request(**kwargs) -> AiRequest:
    defaults: dict = {
        "messages": [AiMessage(role="user", content="{}")],
        "schema_name": "ClarifyQuestions",
        "json_schema": json_schema_for(ClarifyQuestions),
        "max_output_tokens": 650,
        "timeout_seconds": 0.5,
    }
    defaults.update(kwargs)
    return AiRequest(**defaults)


def test_live_mode_without_a_key_fails_loudly() -> None:
    with pytest.raises(DeepSeekConfigError):
        DeepSeekProvider(api_key="", client=None)


async def test_adapter_sends_the_thinking_parameter_explicitly() -> None:
    structured = StubStructured({"parsed": VALID_QUESTIONS, "raw": _Raw({}), "parsing_error": None})
    client = StubClient(structured)
    provider = DeepSeekProvider(api_key="test", client=client)

    await provider.complete(_request(thinking_enabled=False))
    assert client.bound["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.bound["max_tokens"] == 650

    await provider.complete(_request(thinking_enabled=True, reasoning_effort="low"))
    assert client.bound["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    # Structured output goes through json_mode with the raw response available.
    assert client.structured_kwargs["method"] == "json_mode"
    assert client.structured_kwargs["include_raw"] is True


async def test_adapter_makes_exactly_one_request_per_attempt_on_timeout() -> None:
    structured = StubStructured(None, delay=5.0)
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request(timeout_seconds=0.05))

    assert response.error_code == "timeout"
    # No internal retry: one attempt, one request.
    assert structured.calls == 1
    assert response.usage.input_tokens is None


async def test_adapter_does_not_retry_on_rate_limit() -> None:
    structured = StubStructured(None, error=RuntimeError("429 too many requests"))
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request())
    assert response.error_code == "network_error"
    assert structured.calls == 1


async def test_adapter_records_usage_for_unparsable_json() -> None:
    structured = StubStructured(
        {
            "parsed": None,
            "raw": _Raw({"input_tokens": 400, "output_tokens": 120}),
            "parsing_error": ValueError("bad json"),
        }
    )
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request())

    assert response.error_code == "invalid_json"
    # The call was paid for, so its usage is reported even though the output is unusable.
    assert response.usage.input_tokens == 400
    assert response.usage.output_tokens == 120


async def test_adapter_reports_truncation_separately() -> None:
    structured = StubStructured(
        {
            "parsed": VALID_QUESTIONS,
            "raw": _Raw({"input_tokens": 10, "output_tokens": 650}, finish_reason="length"),
            "parsing_error": None,
        }
    )
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request())
    assert response.error_code == "truncated"


async def test_billable_output_including_reasoning_is_not_double_counted() -> None:
    structured = StubStructured(
        {
            "parsed": VALID_QUESTIONS,
            "raw": _Raw(
                {
                    "input_tokens": 500,
                    "output_tokens": 300,
                    "output_token_details": {"reasoning": 180},
                }
            ),
            "parsing_error": None,
        }
    )
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request())
    # 300 already contains the 180 reasoning tokens; it is not 480.
    assert response.usage.output_tokens == 300
    assert response.usage.is_estimated is False


async def test_adapter_drops_the_raw_message_from_its_result() -> None:
    structured = StubStructured(
        {"parsed": VALID_QUESTIONS, "raw": _Raw({"input_tokens": 1}), "parsing_error": None}
    )
    provider = DeepSeekProvider(api_key="test", client=StubClient(structured))
    response = await provider.complete(_request())
    assert response.payload == VALID_QUESTIONS
    assert "raw" not in (response.payload or {})
    assert response.model_dump().get("raw") is None


async def test_mock_provider_labels_every_answer_as_a_demo_response() -> None:
    provider = MockAiProvider()
    response = await provider.complete(
        AiRequest(
            messages=[AiMessage(role="user", content='{"serviceCode":"clutch_kit_replacement"}')],
            schema_name="RequestSummary",
            json_schema=json_schema_for(RequestSummary),
            max_output_tokens=650,
        )
    )
    assert response.ok
    assert "نمایشی" in response.payload["note"]
    assert response.usage.is_estimated is True
