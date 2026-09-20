"""The `AiProvider` contract.

The contract and its models belong to this application. No LangChain object ever reaches
the API layer, the domain services or the database models.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AiMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user"]
    content: str


class AiRequest(BaseModel):
    """One model call. An adapter attempt sends at most one request to the model."""

    model_config = ConfigDict(frozen=True)

    messages: list[AiMessage]
    schema_name: str
    json_schema: dict[str, Any]
    max_output_tokens: int
    thinking_enabled: bool = False
    """Off for question and extraction work; on, at low effort, for judgement work."""
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    timeout_seconds: float = 45.0


class AiUsage(BaseModel):
    """Usage as the service reported it.

    A missing usage figure is never turned into zero — `is_estimated` says so and the
    reservation stays held until the outcome is settled. Billable output already includes
    reasoning tokens, so reasoning detail is never added on top again.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    is_estimated: bool = False


class AiResponse(BaseModel):
    """What one attempt produced. Usage is reported even when the output was unusable."""

    model_config = ConfigDict(frozen=True)

    payload: dict[str, Any] | None = None
    usage: AiUsage = Field(default_factory=AiUsage)
    error_code: str | None = None
    """`timeout`, `rate_limited`, `invalid_json`, `network_error`, `refused`, …"""
    error_detail: str | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.payload is not None and self.error_code is None


class AiProvider(Protocol):
    """A single attempt. Providers never retry internally and never fall back."""

    name: str
    model: str

    async def complete(self, request: AiRequest) -> AiResponse: ...
