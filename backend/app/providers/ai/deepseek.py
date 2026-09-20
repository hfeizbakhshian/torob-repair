"""The live provider: ChatDeepSeek through LangChain.

Deliberately thin. It prepares messages, makes exactly *one* model request per attempt,
converts the output, and reports usage — including when the output turns out to be
unusable. Every retry decision, every budget reservation and every deadline belongs to
`AiService`, not here:

* `max_retries=0` on the client and on the HTTP layer beneath it, so nothing retries
  invisibly;
* no `with_retry`, no automatic output-fixing wrapper, no provider fallback;
* the thinking parameter is always sent explicitly through `extra_body` rather than left
  to the provider default;
* the client is built once for the process and each call's settings are independent of
  any concurrent request.

Reference: https://docs.langchain.com/oss/python/integrations/chat/deepseek and
https://api-docs.deepseek.com/guides/thinking_mode/
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.config import settings
from app.providers.ai.base import AiRequest, AiResponse, AiUsage


class DeepSeekConfigError(RuntimeError):
    """Live mode is misconfigured. This is raised loudly, never softened into a fake answer."""


class DeepSeekProvider:
    """One attempt, one request."""

    name = "deepseek"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model or settings.ai_model
        self._api_key = api_key if api_key is not None else settings.ai_api_key
        self._base_url = base_url if base_url is not None else settings.ai_base_url
        self._client = client
        if client is None and not self._api_key:
            raise DeepSeekConfigError(
                "حالت live بدون کلید مدل قابل اجرا نیست؛ AI_API_KEY را تنظیم کنید."
            )

    def _build_client(self) -> Any:
        from langchain_deepseek import ChatDeepSeek

        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self._api_key,
            "max_retries": 0,
            "streaming": False,
        }
        if self._base_url:
            kwargs["api_base"] = self._base_url
        return ChatDeepSeek(**kwargs)

    def _get_client(self) -> Any:
        # Reused for the lifetime of the process; per-call settings are bound per call.
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @staticmethod
    def _usage_from(raw: Any) -> AiUsage:
        """Read usage off the response metadata.

        A missing figure stays `None` — it is never read as zero. Billable output already
        contains the reasoning tokens, so the reasoning count is not added again.
        """
        metadata: dict[str, Any] = {}
        for attribute in ("usage_metadata", "response_metadata"):
            value = getattr(raw, attribute, None)
            if isinstance(value, dict):
                metadata = {**(value.get("token_usage") or {}), **value, **metadata}

        def pick(*names: str) -> int | None:
            for name in names:
                found = metadata.get(name)
                if isinstance(found, int):
                    return found
            return None

        return AiUsage(
            input_tokens=pick("input_tokens", "prompt_tokens"),
            output_tokens=pick("output_tokens", "completion_tokens"),
            is_estimated=False,
        )

    async def complete(self, request: AiRequest) -> AiResponse:
        from langchain_core.messages import HumanMessage, SystemMessage

        started = time.perf_counter()
        messages = [
            SystemMessage(content=message.content)
            if message.role == "system"
            else HumanMessage(content=message.content)
            for message in request.messages
        ]

        extra_body: dict[str, Any] = {
            "thinking": {"type": "enabled" if request.thinking_enabled else "disabled"}
        }
        if request.thinking_enabled and request.reasoning_effort:
            extra_body["reasoning_effort"] = request.reasoning_effort

        client = self._get_client().bind(
            max_tokens=request.max_output_tokens,
            extra_body=extra_body,
        )
        structured = client.with_structured_output(
            request.json_schema, method="json_mode", include_raw=True
        )

        usage = AiUsage()
        try:
            result = await asyncio.wait_for(
                structured.ainvoke(messages), timeout=request.timeout_seconds
            )
        except TimeoutError:
            return AiResponse(
                error_code="timeout",
                # Usage is unknown here, which is different from zero: the caller keeps
                # the reservation held until the outcome is settled.
                usage=AiUsage(is_estimated=False),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as error:  # noqa: BLE001 - reported, never silently retried
            return AiResponse(
                error_code="network_error",
                error_detail=type(error).__name__,
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        latency = int((time.perf_counter() - started) * 1000)
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else result
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
        if raw is not None:
            usage = self._usage_from(raw)

        finish_reason = None
        if raw is not None and isinstance(getattr(raw, "response_metadata", None), dict):
            finish_reason = raw.response_metadata.get("finish_reason")

        if parsing_error is not None or parsed is None:
            # Usage is still recorded for an unusable answer — the call was paid for.
            return AiResponse(
                error_code="invalid_json",
                error_detail=type(parsing_error).__name__ if parsing_error else "empty_output",
                usage=usage,
                latency_ms=latency,
                finish_reason=finish_reason,
            )
        if finish_reason == "length":
            return AiResponse(
                error_code="truncated",
                usage=usage,
                latency_ms=latency,
                finish_reason=finish_reason,
            )

        payload = parsed if isinstance(parsed, dict) else json.loads(json.dumps(parsed))
        # `include_raw` exists only to read usage and status in memory. The raw message and
        # any internal reasoning are dropped here and never stored or displayed.
        return AiResponse(
            payload=payload,
            usage=usage,
            latency_ms=latency,
            finish_reason=finish_reason,
        )
