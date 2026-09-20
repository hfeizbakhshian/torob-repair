"""Every tunable threshold, quota and deadline, in one place.

These are starting *policy* values chosen to remove ambiguity, not validated market
findings. They are snapshotted into a `PolicyVersion` row and every decision records the
version it was made under, so a later calibration never rewrites an earlier outcome.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

POLICY_VERSION = 1
POLICY_LABEL = "MVP demo defaults"


class TimingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    offer_window_hours: int = 24
    """Minimum time a published request collects offers. The MVP never extends it."""
    visit_window_max_days: int = 7
    selection_acceptance_hours: int = 2
    offer_validity_min_hours_after_deadline: int = 24
    agreement_draft_validity_hours: int = 24
    dispute_statement_hours: int = 24
    dispute_evidence_hours: int = 48
    refund_review_hours: int = 48
    refund_price_complaint_days: int = 7
    refund_reopen_days: int = 7
    appeal_window_days: int = 7
    appeal_resolution_hours: int = 48
    session_ttl_hours: int = 8


class PricePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    fair_price_factor: Decimal = Decimal("1.20")
    """Refund review only: a same-scope total up to 20% above the group mean is numerically
    acceptable. It never constrains what a specialist may quote or agree to."""
    invoice_overprice_factor: Decimal = Decimal("1.30")
    """Part-invoice check only, kept independent of `fair_price_factor`."""
    invoice_min_reference_sellers: int = 3
    invoice_price_max_age_hours: int = 24
    """A reference price must have been observed at most this long before the purchase."""


class ReferencePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_days: int = 60
    min_cases: int = 10
    min_specialists: int = 3
    max_specialist_share: Decimal = Decimal("0.40")


class ScorePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_days: int = 180
    max_cases: int = 50
    min_cases_for_number: int = 5
    """Below this, the profile shows "not enough history" and the sample count, never a
    number. Negative events are still recorded from the very first one."""


class StageQuota(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int
    max_turns: int = 3
    max_output_per_call: int


class AiPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    stages: dict[str, StageQuota] = Field(
        default_factory=lambda: {
            "customer": StageQuota(
                input_tokens=8_000, output_tokens=2_000, max_output_per_call=650
            ),
            "specialist": StageQuota(
                input_tokens=10_000, output_tokens=2_000, max_output_per_call=650
            ),
            "onsite": StageQuota(
                input_tokens=10_000, output_tokens=3_000, max_output_per_call=900
            ),
            "auxiliary": StageQuota(
                input_tokens=12_000, output_tokens=3_000, max_turns=3, max_output_per_call=1_000
            ),
        }
    )
    case_input_tokens: int = 40_000
    case_output_tokens: int = 10_000
    max_normal_calls_per_case: int = 12
    max_attempts_per_case: int = 16
    """Normal calls plus technical retries. A paid failed call consumes technical budget but
    never a person's successful turn."""
    max_attempts_per_call: int = 2
    """One original attempt plus at most one retry, decided by AiService, never by the client."""
    retry_delay_seconds: float = 2.0
    request_timeout_seconds: float = 45.0
    max_free_input_chars: int = 4_000
    max_free_input_tokens: int = 1_000
    max_short_field_chars: int = 200
    token_estimate_overhead_per_message: int = 256
    """Conservative reserve when no compatible tokenizer is available."""

    operations_input_tokens: int = 8_000
    operations_output_tokens: int = 2_000
    operations_max_attempts: int = 3
    operations_max_output_per_call: int = 1_000
    """Refund review, score re-review and dispute adjudication each get their own
    operational budget; none of it is billed to the customer."""
    dispute_budget_extensions: int = 1
    dispute_evidence_rounds: int = 1

    auxiliary_task_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "comparison_explanation": 1,
            "expense_extraction": 1,
            "final_evaluation": 1,
        }
    )


class AttachmentPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_files_per_expense: int = 3
    max_bytes: int = 2 * 1024 * 1024
    allowed_content_types: tuple[str, ...] = ("image/jpeg", "image/png")


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_days_after_close: int = 30
    """Evidence files and chat text are deleted this long after a case closes."""
    anonymous_event_days: int = 180


class Policy(BaseModel):
    """The full immutable policy snapshot."""

    model_config = ConfigDict(frozen=True)

    version: int = POLICY_VERSION
    label: str = POLICY_LABEL
    timing: TimingPolicy = Field(default_factory=TimingPolicy)
    price: PricePolicy = Field(default_factory=PricePolicy)
    reference: ReferencePolicy = Field(default_factory=ReferencePolicy)
    score: ScorePolicy = Field(default_factory=ScorePolicy)
    ai: AiPolicy = Field(default_factory=AiPolicy)
    attachments: AttachmentPolicy = Field(default_factory=AttachmentPolicy)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    registration_fee_toman: int = 2_000
    """Sample amount for the demo payment screen — not a real price for any service."""
    max_intake_questions: int = 8
    max_specialist_replacements: int = 1

    def to_jsonable(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


DEFAULT_POLICY = Policy()


def policy_from_values(values: dict[str, Any]) -> Policy:
    """Rebuild a policy exactly as it was stored, so old decisions stay reproducible."""
    return Policy.model_validate(values)
