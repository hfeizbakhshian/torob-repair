"""AvalAI Chat Completions adapter: explicit limits, one HTTP call and no retries."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from app.config import settings
from app.providers.ai.base import AiRequest, AiResponse, AiUsage


class AvalAiConfigError(RuntimeError):
    """Missing gateway configuration; never silently fall back to mock."""


class AvalAiProvider:
    name = "avalai"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model or settings.ai_model
        self._key = api_key if api_key is not None else settings.ai_api_key
        self._base_url = (base_url or settings.ai_base_url or "").rstrip("/")
        self._transport = transport
        if not self._key or not self._base_url:
            raise AvalAiConfigError("برای AvalAI، مقادیر AI_API_KEY و AI_BASE_URL لازم‌اند.")
        url = httpx.URL(self._base_url)
        if url.scheme not in {"http", "https"} or not url.host or url.path != "/v1":
            raise AvalAiConfigError("آدرس AvalAI باید http/https و دارای مسیر /v1 باشد.")
        if url.userinfo or url.query or url.fragment:
            raise AvalAiConfigError("آدرس AvalAI نباید شامل کلید، query یا fragment باشد.")

    @staticmethod
    def _usage(body: dict[str, Any]) -> AiUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return AiUsage()

        def count(key: str) -> int | None:
            value = usage.get(key)
            return value if type(value) is int and value >= 0 else None

        input_tokens, output_tokens = count("prompt_tokens"), count("completion_tokens")
        # AvalAI substitutes 0/0 when a failed upstream run supplied no usage.
        if input_tokens == 0 and output_tokens == 0:
            return AiUsage()
        return AiUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    async def complete(self, request: AiRequest) -> AiResponse:
        started = time.perf_counter()
        usage = AiUsage()
        finish: str | None = None

        def result(error: str | None = None, payload: dict[str, Any] | None = None) -> AiResponse:
            return AiResponse(
                payload=payload,
                usage=usage,
                error_code=error,
                finish_reason=finish,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        # AiService already includes the schema in its counted/reserved messages.
        # No tools, session continuation, JSON mode or automatic upstream routing.
        # The thinking mode is always explicit: a reasoning model left on its default
        # spends the reserved output budget on reasoning and returns a truncated answer.
        # Both configured models read `thinking`; an OpenAI model would not.
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [message.model_dump() for message in request.messages],
            "max_tokens": request.max_output_tokens,
            "thinking": {"type": "enabled" if request.thinking_enabled else "disabled"},
            "stream": False,
        }
        if request.thinking_enabled and request.reasoning_effort:
            body["reasoning_effort"] = request.reasoning_effort
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=request.timeout_seconds,
            ) as client:
                async with asyncio.timeout(request.timeout_seconds):
                    response = await client.post(
                        self._base_url + "/chat/completions",
                        headers={"Authorization": "Bearer " + str(self._key)},
                        json=body,
                    )
        except (TimeoutError, httpx.TimeoutException):
            return result("timeout")
        except httpx.RequestError:
            return result("network_error")

        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            usage = self._usage(data)
        if response.status_code in {401, 403}:
            return result("authentication_error")
        if response.status_code == 429:
            return result("rate_limited")
        if response.status_code >= 500:
            return result("network_error")
        if response.status_code != 200:
            return result("request_rejected")
        if not isinstance(data, dict):
            return result("invalid_response")

        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            return result("invalid_response")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if not isinstance(finish, str):
            finish = None
        avalai = data.get("avalai")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        upstream_error = avalai.get("error") if isinstance(avalai, dict) else None
        failure_code = (
            "content_blocked"
            if any(
                isinstance(value, str) and "content-blocked" in value
                for value in (content, upstream_error)
            )
            else "incomplete_response"
        )
        if finish == "length":
            return result("truncated")
        if finish != "stop" or data.get("error"):
            return result(failure_code)
        if avalai is not None and (
            not isinstance(avalai, dict)
            or avalai.get("partial")
            or avalai.get("failed")
            or avalai.get("completed") is False
            or avalai.get("error")
            or avalai.get("error_code")
        ):
            return result(failure_code)
        # A gateway that ignores max_tokens must not get a response applied over the
        # reservation; the tokens are already paid for either way.
        if usage.output_tokens is not None and usage.output_tokens > request.max_output_tokens:
            return result("output_limit_exceeded")
        if not isinstance(content, str):
            return result("invalid_json")
        try:
            payload = json.loads(content)
        except ValueError:
            return result("invalid_json")
        if not isinstance(payload, dict):
            return result("invalid_json")
        return result(payload=payload)
