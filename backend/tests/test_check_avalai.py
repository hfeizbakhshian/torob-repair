"""AvalAI diagnostic CLI checks with simulated HTTP/model responses; never paid calls."""

import json

import httpx
import pytest

from app import check_avalai
from app.config import settings
from app.providers.ai.base import AiResponse, AiUsage


@pytest.mark.parametrize(
    ("text", "error", "exit_code", "schema_valid", "script_detected"),
    [
        ("صدا از کدام چرخ شنیده می‌شود؟", None, 0, True, True),
        ("Which wheel makes the noise?", None, 1, True, False),
        (None, None, 1, True, False),
        ("صدا از کدام چرخ شنیده می‌شود؟", "content_blocked", 1, False, False),
    ],
)
async def test_persian_smoke_reports_actual_result(
    monkeypatch, capsys, text, error, exit_code, schema_valid, script_detected
):
    calls = []
    discovery_calls = []
    payload = {
        "questions": []
        if text is None
        else [{"id": "q1", "text": text, "type": "short_text", "options": [], "required": True}]
    }

    class Provider:
        name = "avalai"
        model = "deepseek-v4.1-flash"

        async def complete(self, request):
            calls.append(request)
            return AiResponse(
                payload=None if error else payload,
                error_code=error,
                usage=AiUsage(input_tokens=123, output_tokens=45),
                finish_reason="error" if error else "stop",
            )

    def discovery(request):
        discovery_calls.append(request)
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": Provider.model}]})

    client_type = httpx.AsyncClient

    def client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(discovery))

    monkeypatch.setattr(settings, "ai_base_url", "http://avalai.test/v1")
    monkeypatch.setattr(settings, "ai_api_key", "private-test-key")
    monkeypatch.setattr(check_avalai, "AvalAiProvider", Provider)
    monkeypatch.setattr(check_avalai.httpx, "AsyncClient", client)
    assert await check_avalai.check("fa") == exit_code
    assert len(calls) == len(discovery_calls) == 1
    assert calls[0].messages[-1].content.startswith("پژو ۲۰۶")
    assert calls[0].messages[0].content.startswith("فقط یک شیء JSON")
    output = capsys.readouterr().out
    report = json.loads(output.split("\nاین آزمون", 1)[0])
    assert report["inputLanguage"] == "fa"
    assert report["smokePassed"] is (exit_code == 0)
    assert report["schemaValid"] is schema_valid
    assert report["persianScriptDetected"] is script_detected
    assert report["errorCode"] == error
    assert report["usage"]["input_tokens"] == 123
    assert report["response"] is None if error else report["response"] == {**payload, "note": None}
    assert "private-test-key" not in output


def test_existing_smoke_still_uses_english_input():
    request = check_avalai.build_request()
    assert request.messages[-1].content.startswith("My Peugeot")


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("all_advertised", [False, True])
async def test_comparison_continues_after_failure_and_never_falls_back(
    monkeypatch, capsys, blocked, all_advertised
):
    models = check_avalai.COMPARISON_MODELS
    advertised = models if all_advertised else models[:-1]
    calls = []
    discoveries = []
    original_model = settings.ai_model

    class Provider:
        name = "avalai"

        def __init__(self, *, model=None):
            self.model = model or settings.ai_model

        async def complete(self, request):
            calls.append((self.model, request.model_dump()))
            if blocked and self.model == models[0]:
                return AiResponse(error_code="content_blocked", finish_reason="error")
            return AiResponse(
                payload={
                    "questions": [
                        {
                            "id": "q1",
                            "text": "صدا از کدام چرخ است؟",
                            "type": "short_text",
                        }
                    ]
                },
                usage=AiUsage(input_tokens=123, output_tokens=45),
                finish_reason="stop",
            )

    def discovery(request):
        discoveries.append(request)
        return httpx.Response(200, json={"data": [{"id": model} for model in advertised]})

    client_type = httpx.AsyncClient

    def client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(discovery))

    monkeypatch.setattr(settings, "ai_base_url", "http://avalai.test/v1")
    monkeypatch.setattr(settings, "ai_api_key", "private-test-key")
    monkeypatch.setattr(check_avalai, "AvalAiProvider", Provider)
    monkeypatch.setattr(check_avalai.httpx, "AsyncClient", client)
    exit_code = await check_avalai.compare_models()
    assert exit_code == (0 if all_advertised and not blocked else 1)
    assert [model for model, _ in calls] == list(advertised)
    assert len(discoveries) == 1
    assert all(payload == calls[0][1] for _, payload in calls)
    assert settings.ai_model == original_model
    output = capsys.readouterr().out
    assert "private-test-key" not in output
    summary = json.loads(output.rsplit("خلاصهٔ مقایسه:\n", 1)[1])
    assert summary["requestsSent"] == len(advertised)
    assert summary["requestedModels"] == list(models)
    assert summary["unavailableModels"] == ([] if all_advertised else [models[-1]])
    assert summary["failedModels"] == ([models[0]] if blocked else [])
    if not all_advertised:
        assert '"errorCode": "model_not_advertised"' in output


def test_persian_command_defaults_to_comparison_and_allows_one_model(monkeypatch):
    import sys

    calls = []

    async def compare(language, *, models):
        calls.append((language, models))
        return 0

    monkeypatch.setattr(settings, "ai_mode", "live")
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(check_avalai, "compare_models", compare)
    monkeypatch.setattr(sys, "argv", ["check_avalai_fa", "--live"])
    assert check_avalai.main(input_language="fa", compare=True) == 0
    assert calls[-1] == ("fa", check_avalai.COMPARISON_MODELS)
    monkeypatch.setattr(sys, "argv", ["check_avalai_fa", "--live", "--model", "glm-5.3-flash"])
    assert check_avalai.main(input_language="fa", compare=True) == 0
    assert calls[-1] == ("fa", ("glm-5.3-flash",))
