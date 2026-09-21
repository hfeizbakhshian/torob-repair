"""AvalAI protocol regressions; no database, credentials or paid requests."""

import json

import httpx
import pytest

from app.config import settings
from app.domain.ai_service import AiService, build_provider
from app.domain.errors import DomainError
from app.policy import DEFAULT_POLICY
from app.providers.ai.avalai import AvalAiConfigError, AvalAiProvider
from app.providers.ai.base import AiMessage, AiRequest
from app.providers.ai.mock import MockAiProvider


def request():
    return AiRequest(
        messages=[AiMessage(role="user", content='فقط JSON: {"ok": true}')],
        schema_name="Test",
        json_schema={"type": "object"},
        max_output_tokens=100,
    )


def envelope(content='{"ok": true}', finish="stop"):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 10},
    }


async def invoke(body, status=200):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json=body)

    provider = AvalAiProvider(
        api_key="test-secret",
        base_url="http://avalai.test/v1/",
        model="deepseek-v4.1-flash",
        transport=httpx.MockTransport(handler),
    )
    response = await provider.complete(request())
    assert len(calls) == 1
    assert calls[0].url.path == "/v1/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer test-secret"
    assert "X-AvalAI-Session-Id" not in calls[0].headers
    sent = json.loads(calls[0].content)
    assert set(sent) == {"model", "messages", "max_tokens", "stream"}
    assert sent["max_tokens"] == 100
    assert sent["model"] == "deepseek-v4.1-flash"
    assert sent["stream"] is False
    return response


async def test_success_and_usage():
    result = await invoke(envelope())
    assert result.ok and result.payload == {"ok": True}
    assert result.usage.input_tokens == 25
    assert result.usage.output_tokens == 10


@pytest.mark.parametrize("content", ["{broken", "[]", "null", "```json\n{}\n```", None])
async def test_invalid_json_keeps_usage(content):
    result = await invoke(envelope(content))
    assert result.error_code == "invalid_json"
    assert result.usage.output_tokens == 10


@pytest.mark.parametrize("finish", ["length", "error", None, "tool_calls"])
async def test_incomplete_json_object_cannot_be_applied(finish):
    result = await invoke(envelope(finish=finish))
    assert not result.ok
    assert result.usage.input_tokens == 25


@pytest.mark.parametrize(
    "metadata",
    [
        {"partial": True},
        {"failed": True},
        {"completed": False},
        {"error": "private upstream details"},
        {"error_code": "agent_error"},
        "invalid",
    ],
)
async def test_http_200_with_avalai_failure(metadata):
    body = envelope()
    body["avalai"] = metadata
    result = await invoke(body)
    assert result.error_code == "incomplete_response"
    assert result.error_detail is None


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, "authentication_error"),
        (403, "authentication_error"),
        (400, "request_rejected"),
        (429, "rate_limited"),
        (502, "network_error"),
        (302, "request_rejected"),
    ],
)
async def test_http_errors_do_not_retry_or_leak_details(status, error):
    result = await invoke({"error": "private upstream details"}, status)
    assert result.error_code == error
    assert result.error_detail is None
    assert result.usage.input_tokens is None


async def test_missing_usage_is_unknown_and_reasoning_not_double_counted():
    body = envelope()
    del body["usage"]
    result = await invoke(body)
    assert result.usage.input_tokens is None and result.usage.output_tokens is None
    body = envelope()
    body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 5}
    assert (await invoke(body)).usage.output_tokens == 10


async def test_response_over_reserved_output_is_rejected_and_accounted():
    body = envelope()
    body["usage"]["completion_tokens"] = 101
    result = await invoke(body)
    assert result.error_code == "output_limit_exceeded"
    assert result.usage.output_tokens == 101


async def test_gateway_zero_placeholder_is_unknown_usage():
    body = envelope(finish="error")
    body["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
    result = await invoke(body)
    assert result.usage.input_tokens is None and result.usage.output_tokens is None


async def test_upstream_content_block_is_reported_without_raw_error():
    body = envelope(content="HTTP 400: content-blocked (private request id)", finish="error")
    result = await invoke(body)
    assert result.error_code == "content_blocked"
    assert result.error_detail is None
    body = envelope(content='{"note": "content-blocked"}')
    assert (await invoke(body)).ok


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_failure_is_one_attempt(error_type):
    calls = []

    def handler(req):
        calls.append(req)
        raise error_type("secret upstream text", request=req)

    provider = AvalAiProvider(
        api_key="test",
        base_url="http://avalai.test/v1",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.complete(request())
    assert len(calls) == 1 and not result.ok
    assert result.error_detail is None and result.usage.output_tokens is None


def test_missing_config_and_provider_selection(monkeypatch):
    monkeypatch.setattr(settings, "ai_api_key", None)
    with pytest.raises(AvalAiConfigError):
        AvalAiProvider()
    monkeypatch.setattr(settings, "ai_api_key", "test")
    monkeypatch.setattr(settings, "ai_base_url", "http://avalai.test/v1")
    monkeypatch.setattr(settings, "ai_mode", "live")
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    assert build_provider().name == "avalai"
    monkeypatch.setattr(settings, "ai_mode", "mock")
    assert build_provider().name == "mock"


@pytest.mark.parametrize(
    "url",
    [
        "http://host",
        "http://host/v1/v1",
        "ftp://host/v1",
        "http://user:secret@host/v1",
        "http://host/v1?key=x",
    ],
)
def test_invalid_base_url(url):
    with pytest.raises(AvalAiConfigError):
        AvalAiProvider(api_key="test", base_url=url)


async def test_explicit_unlimited_cost_mode_keeps_unknown_cost(monkeypatch):
    monkeypatch.setattr(settings, "ai_mode", "live")
    monkeypatch.setattr(settings, "ai_enforce_cost_cap", False)
    monkeypatch.setattr(settings, "ai_input_price_per_million", None)
    monkeypatch.setattr(settings, "ai_output_price_per_million", None)
    service = AiService(MockAiProvider(), DEFAULT_POLICY)
    await service._check_daily_cap(None)
    assert service._cost(1000, 200) is None


async def test_enforced_zero_cap_still_blocks_calls(monkeypatch):
    monkeypatch.setattr(settings, "ai_mode", "live")
    monkeypatch.setattr(settings, "ai_enforce_cost_cap", True)
    monkeypatch.setattr(settings, "ai_daily_cost_cap_toman", 0)
    service = AiService(MockAiProvider(), DEFAULT_POLICY)
    with pytest.raises(DomainError):
        await service._check_daily_cap(None)
