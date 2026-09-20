"""Domain enumerations.

Stored as native PostgreSQL text with a CHECK-style Enum so that adding a value is a
migration, not a silent data change.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    customer = "customer"
    specialist = "specialist"
    support = "support"


class RequestStatus(StrEnum):
    draft = "draft"
    open = "open"
    selecting = "selecting"
    assigned = "assigned"
    in_progress = "in_progress"
    completion_review = "completion_review"
    completed = "completed"
    closed_unselected = "closed_unselected"
    cancelled = "cancelled"
    dispute_open = "dispute_open"
    closed_settled = "closed_settled"
    closed_adjudicated = "closed_adjudicated"


TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.completed,
        RequestStatus.closed_unselected,
        RequestStatus.cancelled,
        RequestStatus.closed_settled,
        RequestStatus.closed_adjudicated,
    }
)


class CloseReason(StrEnum):
    no_valid_offer = "no_valid_offer"
    customer_cancelled = "customer_cancelled"
    specialist_cancelled = "specialist_cancelled"
    abandoned_before_publish = "abandoned_before_publish"
    settled_by_agreement = "settled_by_agreement"
    settled_by_adjudication = "settled_by_adjudication"
    completed_normally = "completed_normally"
    replaced_specialist = "replaced_specialist"


class OfferType(StrEnum):
    fixed = "fixed"
    conditional = "conditional"
    diagnostic = "diagnostic"


class OfferStatus(StrEnum):
    active = "active"
    withdrawn = "withdrawn"
    selected = "selected"
    expired = "expired"
    unselectable = "unselectable"


class SelectionStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    expired = "expired"
    ended = "ended"


class AgreementStatus(StrEnum):
    proposed = "proposed"
    active = "active"
    rejected = "rejected"
    expired = "expired"
    void = "void"
    superseded = "superseded"
    ended = "ended"


class Party(StrEnum):
    customer = "customer"
    specialist = "specialist"


class LineType(StrEnum):
    part = "part"
    labor = "labor"
    extra = "extra"


class ReceiptStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class DisputeStatus(StrEnum):
    dispute_open = "dispute_open"
    reviewing = "reviewing"
    needs_evidence = "needs_evidence"
    awaiting_ai = "awaiting_ai"
    resolved_by_ai = "resolved_by_ai"
    resolved_by_agreement = "resolved_by_agreement"


class SettlementSource(StrEnum):
    agreement = "agreement"
    ai_decision = "ai_decision"


class EvaluationStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"


class ChangeVerdict(StrEnum):
    justified = "justified"
    unjustified = "unjustified"
    insufficient_evidence = "insufficient_evidence"


class FairnessVerdict(StrEnum):
    fair = "fair"
    unfair = "unfair"
    insufficient_evidence = "insufficient_evidence"


class LineVerdict(StrEnum):
    accepted = "accepted"
    partially_accepted = "partially_accepted"
    rejected = "rejected"


class PaymentStatus(StrEnum):
    created = "created"
    succeeded = "succeeded"
    failed = "failed"


class RefundStatus(StrEnum):
    requested = "requested"
    reviewing = "reviewing"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"
    transfer_pending = "transfer_pending"
    paid = "paid"
    transfer_failed = "transfer_failed"


class RefundReason(StrEnum):
    no_valid_offer = "no_valid_offer"
    bootstrap_policy = "bootstrap_policy"
    price_complaint = "price_complaint"
    review_not_completed_in_time = "review_not_completed_in_time"
    abandoned_before_publish = "abandoned_before_publish"


class RefundPolicyMode(StrEnum):
    bootstrap = "bootstrap"
    reference_based = "reference_based"


class ReferenceQuality(StrEnum):
    self_reported = "self_reported"
    documented = "documented"
    verified = "verified"


class AiStage(StrEnum):
    """Quota buckets. The first three are billed to the customer package."""

    customer = "customer"
    specialist = "specialist"
    onsite = "onsite"
    auxiliary = "auxiliary"
    operations = "operations"


class AiPurpose(StrEnum):
    clarify_questions = "clarify_questions"
    request_summary = "request_summary"
    case_guidance = "case_guidance"
    comparison_explanation = "comparison_explanation"
    expense_extraction = "expense_extraction"
    final_evaluation = "final_evaluation"
    price_fairness = "price_fairness"
    dispute_adjudication = "dispute_adjudication"


class AiRunStatus(StrEnum):
    reserved = "reserved"
    succeeded = "succeeded"
    invalid_output = "invalid_output"
    failed = "failed"
    timeout = "timeout"
    stale = "stale"


class JobStatus(StrEnum):
    pending = "pending"
    leased = "leased"
    done = "done"
    failed = "failed"


class JobKind(StrEnum):
    close_offer_window = "close_offer_window"
    expire_selection = "expire_selection"
    expire_agreement = "expire_agreement"
    run_evaluation = "run_evaluation"
    run_refund_review = "run_refund_review"
    run_dispute_adjudication = "run_dispute_adjudication"
    close_dispute_statement_window = "close_dispute_statement_window"
    refund_transfer = "refund_transfer"
    retention_cleanup = "retention_cleanup"


class PartSourceKind(StrEnum):
    manual = "manual"
    sample = "sample"


class PriceCheckVerdict(StrEnum):
    within_range = "within_range"
    overpriced = "overpriced"
    not_assessable = "not_assessable"
