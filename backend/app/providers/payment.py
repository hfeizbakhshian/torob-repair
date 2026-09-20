"""A payment simulator.

Nothing here talks to a bank. It exists so the *domain* rules around payment — one
successful charge per request, one full refund per payment, safe retry of a failed
transfer — are real and testable. No card number is ever collected.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    succeed = "succeed"
    fail = "fail"


@dataclass(frozen=True)
class GatewayResult:
    succeeded: bool
    reference: str
    receipt_label: str
    failure_reason: str | None = None
    replayed: bool = False


class MockPaymentGateway:
    """In-process gateway.

    `queue_outcome` lets the demo control panel and the tests drive the failure and retry
    paths deterministically. Replaying the same reference returns the same result rather
    than charging twice.
    """

    def __init__(self) -> None:
        self._results: dict[str, GatewayResult] = {}
        self._forced: list[Outcome] = []

    def queue_outcome(self, outcome: Outcome) -> None:
        self._forced.append(outcome)

    def reset(self) -> None:
        self._results.clear()
        self._forced.clear()

    def _next_outcome(self) -> Outcome:
        return self._forced.pop(0) if self._forced else Outcome.succeed

    def _run(self, key: str, amount_toman: int, kind: str) -> GatewayResult:
        if key in self._results:
            previous = self._results[key]
            return GatewayResult(
                succeeded=previous.succeeded,
                reference=previous.reference,
                receipt_label=previous.receipt_label,
                failure_reason=previous.failure_reason,
                replayed=True,
            )

        outcome = self._next_outcome()
        reference = f"{kind}-{secrets.token_hex(8)}"
        result = GatewayResult(
            succeeded=outcome is Outcome.succeed,
            reference=reference,
            receipt_label=f"رسید آزمایشی — بدون انتقال وجه — {amount_toman:,} تومان",
            failure_reason=None if outcome is Outcome.succeed else "شبیه‌سازی خطای درگاه",
        )
        # A failed attempt is not memoised: retrying a failed transfer must be possible.
        if result.succeeded:
            self._results[key] = result
        return result

    def charge(self, idempotency_key: str, amount_toman: int) -> GatewayResult:
        return self._run(idempotency_key, amount_toman, "pay")

    def refund(self, transfer_reference: str, amount_toman: int) -> GatewayResult:
        return self._run(transfer_reference, amount_toman, "refund")


_gateway = MockPaymentGateway()


def get_gateway() -> MockPaymentGateway:
    return _gateway
