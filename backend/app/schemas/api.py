"""API request and response contracts.

These Pydantic models are the single source of truth for the wire format: OpenAPI is
generated from them and the frontend's TypeScript types are generated from that. Python
stays snake_case, JSON is camelCase through the shared alias generator.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.domain.money import ApiModel, LineItem
from app.models.enums import (
    AgreementStatus,
    DisputeStatus,
    EvaluationStatus,
    LineType,
    OfferStatus,
    OfferType,
    Party,
    PaymentStatus,
    PriceCheckVerdict,
    ReceiptStatus,
    RefundPolicyMode,
    RefundStatus,
    RequestStatus,
    Role,
    SelectionStatus,
)

SAMPLE_DATA_NOTICE = "دادهٔ نمونهٔ دمو — قیمت واقعی خدمت نیست."


# --- errors ---------------------------------------------------------------


class ErrorResponse(ApiModel):
    code: str
    message: str
    field_errors: dict[str, str] | None = None
    current_revision: int | None = None
    data: dict[str, Any] | None = None


# --- identity -------------------------------------------------------------


class DemoAccount(ApiModel):
    login_key: str
    display_name: str
    role: Role
    shop_name: str | None = None
    city: str | None = None


class SignInRequest(ApiModel):
    login_key: str = Field(max_length=60)


class CurrentUser(ApiModel):
    user_id: uuid.UUID
    display_name: str
    role: Role
    app_mode: str
    ai_mode: str
    server_time: datetime
    clock_offset_seconds: float


# --- catalog --------------------------------------------------------------


class ServiceTemplateOut(ApiModel):
    code: str
    title_fa: str
    summary_fa: str
    allowed_offer_types: list[str]
    reference_labor_minutes: int | None
    reference_labor_source: str
    part_specs: list[dict[str, Any]]
    is_sample: bool = True


class CoverageQuery(ApiModel):
    city: str = Field(max_length=80)
    vehicle_code: str = Field(max_length=80)
    service_code: str = Field(max_length=60)
    visit_mode: str = Field(default="shop", max_length=30)


class CoverageResult(ApiModel):
    supported: bool
    message: str | None = None


class PolicySummary(ApiModel):
    """What the customer is shown *before* the first paid AI call."""

    version: int
    registration_fee_toman: int
    fee_label: str = "پرداخت آزمایشی — بدون انتقال وجه واقعی"
    max_turns_per_stage: int
    offer_window_hours: int
    refund_policy_mode: RefundPolicyMode
    refund_summary: str


# --- requests -------------------------------------------------------------


class CreateRequestInput(ApiModel):
    city: str = Field(max_length=80)
    district: str = Field(max_length=80)
    vehicle_code: str = Field(max_length=80)
    vehicle_details: dict[str, Any] = Field(default_factory=dict)
    service_code: str = Field(max_length=60)
    symptoms: str = Field(min_length=1, max_length=2000)
    visit_mode: str = Field(default="shop", max_length=30)
    idempotency_key: str | None = Field(default=None, max_length=120)
    """When given, the registration fee is settled in the same call."""


class PayInput(ApiModel):
    idempotency_key: str = Field(max_length=120)


class PaymentOut(ApiModel):
    id: uuid.UUID
    amount_toman: int
    status: PaymentStatus
    receipt_label: str
    is_sample: bool = True
    refund_policy_mode: RefundPolicyMode


class AnswersInput(ApiModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class RevisionInput(ApiModel):
    expected_revision: int | None = None


class TypoNoteInput(ApiModel):
    note: str = Field(min_length=1, max_length=400)


class RequestVersionOut(ApiModel):
    id: uuid.UUID
    version_number: int
    city: str
    district: str
    vehicle_code: str
    vehicle_details: dict[str, Any]
    service_code: str
    visit_mode: str
    symptoms: str
    answers: dict[str, Any]
    allowed_offer_types: list[str]
    summary_facts: list[str]
    summary_unknowns: list[str]
    summary_confirmed_at: datetime | None
    typo_notes: list[dict[str, Any]]


class RequestOut(ApiModel):
    id: uuid.UUID
    status: RequestStatus
    revision: int
    close_reason: str | None
    published_at: datetime | None
    response_deadline: datetime | None
    offers_closed_at: datetime | None
    visit_window_start: datetime | None
    visit_window_end: datetime | None
    refund_policy_mode: RefundPolicyMode
    specialist_replacements_used: int
    closed_at: datetime | None
    version: RequestVersionOut | None = None
    has_paid: bool = False


# --- offers ---------------------------------------------------------------


class ScenarioInput(ApiModel):
    code: str = Field(max_length=60)
    title: str = Field(max_length=200)
    total_toman: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=400)


class SubmitOfferInput(ApiModel):
    offer_type: OfferType
    lines: list[LineItem] = Field(default_factory=list, max_length=40)
    scenarios: list[ScenarioInput] = Field(default_factory=list, max_length=8)
    scheduled_at: datetime
    valid_until: datetime
    estimated_minutes: int | None = Field(default=None, ge=0, le=100_000)
    warranty_note: str | None = Field(default=None, max_length=400)
    conditions_note: str | None = Field(default=None, max_length=400)
    diagnostic_scope: str | None = Field(default=None, max_length=400)
    diagnostic_fee_toman: int | None = Field(default=None, ge=0)
    diagnostic_fee_credited: bool = False
    expected_revision: int | None = None


class OfferVersionOut(ApiModel):
    id: uuid.UUID
    version_number: int
    offer_type: OfferType
    lines: list[LineItem]
    scenarios: list[dict[str, Any]]
    total_toman: int | None
    specialist_payable_toman: int | None
    warranty_note: str | None
    conditions_note: str | None
    diagnostic_scope: str | None
    diagnostic_fee_toman: int | None
    diagnostic_fee_credited: bool
    estimated_minutes: int | None
    scheduled_at: datetime
    valid_until: datetime


class SpecialistPublicOut(ApiModel):
    """What a customer may see about a bidder. No contact details before acceptance."""

    specialist_id: uuid.UUID
    display_name: str
    shop_name: str
    district: str
    score: int | None = None
    has_enough_history: bool = False
    assessable_cases: int = 0
    negative_cases: int = 0
    unjustified_increase_cases: int = 0
    invoice_overprice_cases: int = 0
    satisfaction_average: float | None = None
    satisfaction_responses: int = 0


class OfferOut(ApiModel):
    id: uuid.UUID
    request_id: uuid.UUID
    status: OfferStatus
    revision: int
    specialist: SpecialistPublicOut
    version: OfferVersionOut
    selectable: bool
    not_selectable_reason: str | None = None
    scenario_total_toman: int | None = None
    """Inside a scenario group, what this offer costs *for that scenario*. The whole-offer
    total covers every scenario at once and is not what the group is ranked by."""
    scenario_specialist_payable_toman: int | None = None


class OfferComparisonOut(ApiModel):
    sort_key: Literal["price", "time", "score"]
    scenario_groups: list[ScenarioGroupOut]


class ScenarioGroupOut(ApiModel):
    scenario_code: str | None
    title: str
    offers: list[OfferOut]
    note: str | None = None


# --- selection ------------------------------------------------------------


class SelectOfferInput(ApiModel):
    offer_version_id: uuid.UUID
    accepted_arbitration: bool = False
    expected_revision: int | None = None


class AcceptSelectionInput(ApiModel):
    accepted_arbitration: bool = False
    expected_revision: int | None = None


class RejectSelectionInput(ApiModel):
    cannot_perform: bool = False
    reason: str | None = Field(default=None, max_length=400)
    expected_revision: int | None = None


class SelectionOut(ApiModel):
    id: uuid.UUID
    status: SelectionStatus
    revision: int
    specialist_id: uuid.UUID
    offer_version_id: uuid.UUID
    acceptance_deadline: datetime
    accepted_at: datetime | None
    scheduled_at: datetime
    work_started_at: datetime | None
    arbitration_terms_version: int | None


class ReplaceSpecialistInput(ApiModel):
    reason: str = Field(min_length=1, max_length=400)
    expected_revision: int | None = None


# --- agreements -----------------------------------------------------------


class ProposeAgreementInput(ApiModel):
    lines: list[LineItem] = Field(default_factory=list, max_length=40)
    scenarios: list[ScenarioInput] = Field(default_factory=list, max_length=8)
    scheduled_at: datetime
    warranty_note: str | None = Field(default=None, max_length=400)
    change_reason: str | None = Field(default=None, max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_revision: int | None = None


class AgreementOut(ApiModel):
    id: uuid.UUID
    version_number: int
    status: AgreementStatus
    revision: int
    lines: list[LineItem]
    scenarios: list[dict[str, Any]]
    total_toman: int | None
    specialist_payable_toman: int | None
    scheduled_at: datetime
    warranty_note: str | None
    cancellation_terms: str
    arbitration_clause: str
    change_reason: str | None
    change_diff: dict[str, Any]
    evidence_ids: list[str]
    proposed_by: Party
    expires_at: datetime
    activated_at: datetime | None
    approved_by: list[Party]
    base_offer_version_id: uuid.UUID | None


class CancelInput(ApiModel):
    reason: str = Field(min_length=1, max_length=600)
    claimed_amount_toman: int | None = Field(default=None, ge=0)
    expected_revision: int | None = None


# --- expenses and completion ---------------------------------------------


class CaseQuestionInput(ApiModel):
    question: str = Field(min_length=1, max_length=1000)


class ExtractExpensesInput(ApiModel):
    text: str = Field(min_length=1, max_length=4000)


class SubmitExpenseInput(ApiModel):
    lines: list[LineItem] = Field(default_factory=list, max_length=40)
    source_text: str | None = Field(default=None, max_length=4000)
    actual_minutes: int | None = Field(default=None, ge=0, le=100_000)
    extracted_by_ai: bool = False
    final: bool = False
    """A final invoice also ends the work and asks the customer to close the case."""
    expected_revision: int | None = None


class ReviewExtractionInput(ApiModel):
    lines: list[LineItem] | None = None


class ReceiptDecisionInput(ApiModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=600)
    satisfaction_score: int | None = Field(default=None, ge=1, le=5)
    satisfaction_note: str | None = Field(default=None, max_length=600)
    reference_consent: bool = False
    """Carried through when approving a final invoice, which also closes the case."""


class ExpenseVersionOut(ApiModel):
    id: uuid.UUID
    version_number: int
    source_text: str | None
    lines: list[LineItem]
    actual_minutes: int | None
    total_toman: int | None
    specialist_payable_toman: int | None
    extracted_by_ai: bool
    specialist_reviewed_at: datetime | None
    submitted_at: datetime | None
    receipt_status: ReceiptStatus
    receipt_reason: str | None
    receipt_reviewed_at: datetime | None
    attachment_ids: list[uuid.UUID]


class CompletionOut(ApiModel):
    id: uuid.UUID
    revision: int
    requested_at: datetime | None
    invoice_total_toman: int | None
    invoice_specialist_payable_toman: int | None
    customer_confirmed_at: datetime | None
    customer_reported_mismatch_at: datetime | None
    satisfaction_score: int | None
    reference_consent: bool


class ConfirmCompletionInput(ApiModel):
    satisfaction_score: int | None = Field(default=None, ge=1, le=5)
    satisfaction_note: str | None = Field(default=None, max_length=600)
    reference_consent: bool = False
    expected_revision: int | None = None


class MismatchInput(ApiModel):
    reason: str = Field(min_length=1, max_length=600)


# --- parts ----------------------------------------------------------------


class PartOptionInput(ApiModel):
    part_title: str = Field(min_length=1, max_length=200)
    part_number: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=120)
    condition: str = Field(default="new", max_length=30)
    warranty_note: str | None = Field(default=None, max_length=400)
    product_url: str = Field(max_length=1000)
    seller_name: str | None = Field(default=None, max_length=160)
    price_toman: int | None = Field(default=None, ge=0)
    delivery_note: str | None = Field(default=None, max_length=400)
    delivery_cost_toman: int | None = Field(default=None, ge=0)


class PartOptionOut(ApiModel):
    id: uuid.UUID
    part_title: str
    part_number: str | None
    brand: str | None
    condition: str
    warranty_note: str | None
    product_url: str
    seller_name: str | None
    price_toman: int | None
    delivery_note: str | None
    delivery_cost_toman: int | None
    observed_at: datetime
    source_label: str
    compatibility_confirmed_at: datetime | None


class PartSearchOut(ApiModel):
    query: str
    search_url: str
    note: str = "جست‌وجو در ترب باز می‌شود؛ سرور لینک را واکشی نمی‌کند."


class PriceSnapshotInput(ApiModel):
    expense_version_id: uuid.UUID | None = None
    line_id: str | None = Field(default=None, max_length=60)
    product_url: str = Field(max_length=1000)
    seller_name: str = Field(min_length=1, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    part_number: str | None = Field(default=None, max_length=80)
    condition: str = Field(default="new", max_length=30)
    warranty_note: str | None = Field(default=None, max_length=400)
    unit_price_toman: int = Field(ge=0)
    delivery_cost_toman: int | None = Field(default=None, ge=0)
    payment_terms: str | None = Field(default=None, max_length=160)
    evidence_note: str | None = Field(default=None, max_length=400)


class PriceSnapshotOut(ApiModel):
    id: uuid.UUID
    seller_name: str
    brand: str | None
    part_number: str | None
    unit_price_toman: int
    observed_at: datetime
    verified_by_support_at: datetime | None
    rejected_reason: str | None
    source_label: str


class VerifySnapshotInput(ApiModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=400)


class PriceCheckOut(ApiModel):
    id: uuid.UUID
    line_id: str
    invoice_unit_price_toman: int | None
    median_reference_toman: int | None
    ratio: float | None
    comparable: bool | None
    verdict: PriceCheckVerdict
    equivalence_note: str | None
    established: bool
    snapshot_ids: list[str]
    display_label: str


# --- disputes -------------------------------------------------------------


class ClaimItemInput(ApiModel):
    claim_item_id: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    type: LineType = LineType.extra
    claimed_quantity: int = Field(default=1, ge=0, le=1000)
    claimed_amount_toman: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    supplied_by: Party = Party.specialist
    paid_to: Literal["specialist", "third_party"] = "specialist"


class OpenDisputeInput(ApiModel):
    claim_items: list[ClaimItemInput] = Field(min_length=1, max_length=40)


class StatementInput(ApiModel):
    body: str = Field(min_length=1, max_length=4000)
    item_positions: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class ResolutionProposalInput(ApiModel):
    lines: list[LineItem] = Field(default_factory=list, max_length=40)
    note: str | None = Field(default=None, max_length=600)


class DisputeOut(ApiModel):
    id: uuid.UUID
    status: DisputeStatus
    revision: int
    claim_items: list[dict[str, Any]]
    statement_deadline: datetime
    evidence_deadline: datetime | None
    customer_closed_statements_at: datetime | None
    specialist_closed_statements_at: datetime | None
    evidence_rounds_used: int
    support_evidence: list[dict[str, Any]] = Field(default_factory=list)
    resolved_at: datetime | None
    statements: list[dict[str, Any]]
    current_proposal: dict[str, Any] | None
    decision: dict[str, Any] | None
    settlement: dict[str, Any] | None


# --- refunds --------------------------------------------------------------


class RefundInput(ApiModel):
    note: str | None = Field(default=None, max_length=600)


class RefundOut(ApiModel):
    id: uuid.UUID
    status: RefundStatus
    amount_toman: int
    reason: str | None
    policy_mode: RefundPolicyMode
    customer_note: str | None
    decision_note: str | None
    review_deadline: datetime | None
    transferred_at: datetime | None
    transfer_attempts: int
    findings: list[dict[str, Any]]
    is_sample: bool = True


# --- evaluation -----------------------------------------------------------


class EvaluationOut(ApiModel):
    id: uuid.UUID
    status: EvaluationStatus
    attempt: int
    change_verdicts: list[dict[str, Any]]
    has_unjustified_increase: bool
    has_invoice_overprice: bool
    is_assessable: bool
    note: str | None
    model_label: str | None
    display_state: str


class AppealInput(ApiModel):
    reason: str = Field(min_length=1, max_length=600)
    evidence_note: str | None = Field(default=None, max_length=600)


class SupportEvidenceInput(ApiModel):
    note: str = Field(min_length=1, max_length=2000)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class ExtendBudgetInput(ApiModel):
    reason: str = Field(min_length=1, max_length=600)


class ResolveAppealInput(ApiModel):
    note: str = Field(min_length=1, max_length=600)
    new_verdicts: list[dict[str, Any]] | None = None


# --- AI -------------------------------------------------------------------


class AiRunOut(ApiModel):
    run_id: uuid.UUID
    status: str
    payload: dict[str, Any] | None
    error_code: str | None = None
    message: str | None = None
    is_demo_response: bool


class AiBudgetOut(ApiModel):
    stage: str
    turns_used: int
    max_turns: int
    input_tokens_used: int
    input_tokens_limit: int
    output_tokens_used: int
    output_tokens_limit: int


class AiUsageOut(ApiModel):
    provider: str
    model: str
    is_demo: bool
    stages: list[AiBudgetOut]
    case_calls_used: int
    case_calls_limit: int
    note: str


# --- demo control and support --------------------------------------------


class AdvanceClockInput(ApiModel):
    seconds: int = Field(ge=0, le=60 * 60 * 24 * 30)


class DemoStateOut(ApiModel):
    app_mode: str
    ai_mode: str
    server_time: datetime
    clock_offset_seconds: float
    demo_reference_ready: bool


class DemoFlagInput(ApiModel):
    enabled: bool


class GatewayOutcomeInput(ApiModel):
    outcome: Literal["succeed", "fail"]


class SupportQueueOut(ApiModel):
    refunds: list[RefundOut]
    disputes_awaiting_ai: list[DisputeOut]
    price_sources_pending: list[PriceSnapshotOut]
    appeals_open: list[dict[str, Any]]


class MetricsOut(ApiModel):
    """Every ratio is shown with its denominator; an empty denominator says so."""

    first_valid_offer_median_minutes: float | None
    first_valid_offer_count: int
    comparable_offers_average: float | None
    comparable_offers_denominator: int
    no_offer_ratio: float | None
    no_offer_denominator: int
    payment_after_cost_shown_ratio: float | None
    payment_denominator: int
    transparency_negative_ratio: float | None
    transparency_denominator: int
    satisfaction_average: float | None
    satisfaction_responses: int
    ai_tokens_average: float | None
    ai_tokens_p95: float | None
    ai_denominator: int
    dispute_count: int
    dispute_median_hours_to_decision: float | None
    refund_count: int
    refund_median_hours_to_transfer: float | None
    is_sample_data: bool = True


OfferComparisonOut.model_rebuild()
