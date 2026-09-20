"""Validated AI output contracts.

Model text is untrusted input. Every field below is re-validated here, and any evidence id
the model cites has to exist in the very input that review was given — a reason without a
real piece of evidence behind it is not enough.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.domain.money import ApiModel
from app.models.enums import ChangeVerdict, FairnessVerdict, LineVerdict, Party

MAX_REASON_CHARS = 600


class AiQuestion(ApiModel):
    id: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=300)
    type: Literal["single_choice", "multi_choice", "short_text", "number"]
    options: list[str] = Field(default_factory=list, max_length=8)
    required: bool = True

    @model_validator(mode="after")
    def _options_present(self) -> AiQuestion:
        if self.type in ("single_choice", "multi_choice") and len(self.options) < 2:
            raise ValueError("پرسش چندگزینه‌ای باید حداقل دو گزینه داشته باشد.")
        return self


class ClarifyQuestions(ApiModel):
    """The first turn: every necessary question asked at once, mostly multiple choice."""

    questions: list[AiQuestion] = Field(max_length=8)
    note: str | None = Field(default=None, max_length=MAX_REASON_CHARS)

    @field_validator("questions")
    @classmethod
    def _unique_ids(cls, value: list[AiQuestion]) -> list[AiQuestion]:
        if len({question.id for question in value}) != len(value):
            raise ValueError("شناسهٔ پرسش‌ها باید یکتا باشد.")
        return value


class RequestSummary(ApiModel):
    """What is known, what is not, and which offer types that implies."""

    facts: list[str] = Field(max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    suggested_offer_type: Literal["fixed", "conditional", "diagnostic"]
    needs_in_person_check: bool = False
    """Missing information leads to an in-person check or a manual form — never to a
    confident diagnosis or an invented price."""
    note: str | None = Field(default=None, max_length=MAX_REASON_CHARS)


class ExtractedExpenseLine(ApiModel):
    source_text: str = Field(min_length=1, max_length=400)
    title: str = Field(min_length=1, max_length=200)
    type: Literal["part", "labor", "extra"]
    amount_toman: int | None = Field(default=None, ge=0)
    quantity: int | None = Field(default=None, ge=0, le=1000)
    minutes: int | None = Field(default=None, ge=0, le=100_000)
    payer: Party = Party.specialist
    needs_confirmation: bool = True


class ExpenseExtraction(ApiModel):
    """Text turned into line items. The specialist reviews this before the customer sees it."""

    items: list[ExtractedExpenseLine] = Field(max_length=40)
    note: str | None = Field(default=None, max_length=MAX_REASON_CHARS)


class ChangeJudgement(ApiModel):
    change_id: str = Field(min_length=1, max_length=60)
    verdict: ChangeVerdict
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_fields: list[str] = Field(default_factory=list, max_length=20)


class EvaluationResult(ApiModel):
    """Judgement of every price increase along the chain, with its evidence."""

    changes: list[ChangeJudgement] = Field(max_length=40)


class FairnessFinding(ApiModel):
    offer_version_id: str = Field(min_length=1, max_length=60)
    comparable: bool
    verdict: FairnessVerdict
    reference_snapshot_id: str | None = Field(default=None, max_length=60)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class PriceFairnessReview(ApiModel):
    """Offers reviewed in one batch, never one model call per offer."""

    findings: list[FairnessFinding] = Field(max_length=40)


class LineDecision(ApiModel):
    claim_item_id: str = Field(min_length=1, max_length=60)
    verdict: LineVerdict
    accepted_quantity: int = Field(ge=0, le=1000)
    accepted_amount_toman: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class DisputeDecisionOutput(ApiModel):
    """The adjudication contract.

    The ruling may only decide the items it was given. It cannot invent a line, a rate or
    an operation, and it cannot accept more quantity or money than the claim itself asked
    for. `needs_evidence` applies no settlement at all.
    """

    dispute_id: str = Field(min_length=1, max_length=60)
    input_snapshot_id: str = Field(min_length=1, max_length=60)
    status: Literal["decided", "needs_evidence"]
    line_decisions: list[LineDecision] = Field(default_factory=list, max_length=40)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_fields: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _no_duplicate_items(self) -> DisputeDecisionOutput:
        ids = [decision.claim_item_id for decision in self.line_decisions]
        if len(set(ids)) != len(ids):
            raise ValueError("هر قلم مورد اختلاف فقط یک بار تعیین تکلیف می‌شود.")
        if self.status == "needs_evidence" and not self.missing_fields:
            raise ValueError("درخواست تکمیل شواهد باید فهرست اطلاعات لازم را داشته باشد.")
        return self


def json_schema_for(model: type[ApiModel]) -> dict[str, Any]:
    """The schema sent to the model, and counted as part of the input tokens."""
    return model.model_json_schema(by_alias=True)
