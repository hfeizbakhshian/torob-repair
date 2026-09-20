"""The demonstration provider.

It needs no key, no network and no model run, and it never claims to be one. Answers come
from the sample scenarios and a few narrow rules; anything it cannot ground is pushed to an
in-person check or a manual form rather than guessed. Its templates are deliberately
independent of the live output so a passing demo is never mistaken for a tested model.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.providers.ai.base import AiRequest, AiResponse, AiUsage

DEMO_NOTE = "پاسخ نمایشی — این متن با قواعد نمونه ساخته شده و اجرای مدل واقعی نیست."

_CLUTCH_QUESTIONS = [
    {
        "id": "symptom_slip",
        "text": "هنگام گاز دادن، دور موتور بالا می‌رود ولی سرعت خودرو متناسب با آن زیاد نمی‌شود؟",
        "type": "single_choice",
        "options": ["بله، مشخص است", "گاهی", "خیر"],
        "required": True,
    },
    {
        "id": "pedal_feel",
        "text": "حس پدال کلاچ چگونه است؟",
        "type": "single_choice",
        "options": ["سنگین", "خیلی سبک", "بدون تغییر"],
        "required": True,
    },
    {
        "id": "noise",
        "text": "هنگام گرفتن کلاچ صدای غیرعادی می‌شنوید؟",
        "type": "single_choice",
        "options": ["بله", "خیر", "مطمئن نیستم"],
        "required": True,
    },
    {
        "id": "mileage",
        "text": "کارکرد تقریبی خودرو چند کیلومتر است؟",
        "type": "number",
        "options": [],
        "required": True,
    },
]

_BRAKE_QUESTIONS = [
    {
        "id": "brake_noise",
        "text": "هنگام ترمز صدای سایش یا جیغ می‌شنوید؟",
        "type": "single_choice",
        "options": ["بله", "خیر"],
        "required": True,
    },
    {
        "id": "brake_vibration",
        "text": "هنگام ترمز، لرزش در فرمان یا پدال حس می‌کنید؟",
        "type": "single_choice",
        "options": ["بله", "خیر"],
        "required": True,
    },
    {
        "id": "last_change",
        "text": "آخرین تعویض لنت جلو تقریباً چند کیلومتر پیش بوده است؟",
        "type": "number",
        "options": [],
        "required": True,
    },
]

_COOLING_QUESTIONS = [
    {
        "id": "temp_gauge",
        "text": "عقربهٔ دمای موتور در چه شرایطی بالا می‌رود؟",
        "type": "single_choice",
        "options": ["در ترافیک", "در سرعت بالا", "همیشه", "نامنظم"],
        "required": True,
    },
    {
        "id": "coolant_loss",
        "text": "افت سطح مایع خنک‌کننده دیده‌اید؟",
        "type": "single_choice",
        "options": ["بله", "خیر", "بررسی نکرده‌ام"],
        "required": True,
    },
    {
        "id": "fan_works",
        "text": "فن خنک‌کننده هنگام داغ‌شدن کار می‌کند؟",
        "type": "single_choice",
        "options": ["بله", "خیر", "نمی‌دانم"],
        "required": True,
    },
]

_QUESTIONS_BY_SERVICE = {
    "clutch_kit_replacement": _CLUTCH_QUESTIONS,
    "front_brake_pad_replacement": _BRAKE_QUESTIONS,
    "cooling_system_diagnosis": _COOLING_QUESTIONS,
}

_AMOUNT_PATTERN = re.compile(r"(\d[\d,٬،]*)\s*(?:تومان|تومن)")
_MINUTES_PATTERN = re.compile(r"(\d+)\s*(?:دقیقه|ساعت)")


def _digits(raw: str) -> int:
    translated = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return int(re.sub(r"[^0-9]", "", translated) or 0)


class MockAiProvider:
    """Deterministic, rule-based, offline."""

    name = "mock"

    def __init__(self, model: str = "demo-rules") -> None:
        self.model = model

    async def complete(self, request: AiRequest) -> AiResponse:
        started = time.perf_counter()
        context = self._context(request)
        builder = getattr(self, f"_build_{request.schema_name}", None)
        payload = builder(context) if builder else None
        latency = int((time.perf_counter() - started) * 1000)

        if payload is None:
            return AiResponse(
                error_code="unsupported_schema",
                error_detail=f"mock provider has no template for {request.schema_name}",
                usage=self._usage(request, 0),
                latency_ms=latency,
            )
        rendered = json.dumps(payload, ensure_ascii=False)
        return AiResponse(
            payload=payload,
            usage=self._usage(request, len(rendered)),
            latency_ms=latency,
            finish_reason="stop",
        )

    @staticmethod
    def _context(request: AiRequest) -> dict[str, Any]:
        for message in reversed(request.messages):
            if message.role == "user":
                try:
                    parsed = json.loads(message.content)
                except json.JSONDecodeError:
                    return {"text": message.content}
                if isinstance(parsed, dict):
                    return parsed
                return {"text": message.content}
        return {}

    @staticmethod
    def _usage(request: AiRequest, output_chars: int) -> AiUsage:
        input_chars = sum(len(message.content) for message in request.messages)
        return AiUsage(
            input_tokens=max(1, input_chars // 3),
            output_tokens=max(1, output_chars // 3),
            is_estimated=True,
        )

    # --- templates ------------------------------------------------------

    def _build_ClarifyQuestions(self, context: dict[str, Any]) -> dict[str, Any]:
        service = str(context.get("serviceCode", ""))
        questions = _QUESTIONS_BY_SERVICE.get(service)
        if questions is None:
            return {
                "questions": [
                    {
                        "id": "describe",
                        "text": "لطفاً نشانه‌ای که دیده‌اید را کوتاه بنویسید.",
                        "type": "short_text",
                        "options": [],
                        "required": True,
                    }
                ],
                "note": (
                    f"{DEMO_NOTE} این خدمت در قالب‌های نمونه نیست؛ برای تعیین دقیق، "
                    "بررسی حضوری لازم است."
                ),
            }
        return {"questions": questions, "note": DEMO_NOTE}

    def _build_RequestSummary(self, context: dict[str, Any]) -> dict[str, Any]:
        service = str(context.get("serviceCode", ""))
        answers = context.get("answers") or {}
        facts: list[str] = []
        unknowns: list[str] = []

        if context.get("vehicleCode"):
            facts.append(f"خودرو: {context['vehicleCode']}")
        if context.get("city"):
            facts.append(f"شهر: {context['city']}")
        for key, value in answers.items():
            if value in (None, ""):
                unknowns.append(key)
            else:
                facts.append(f"{key}: {value}")

        if service == "cooling_system_diagnosis":
            suggested = "diagnostic"
            needs_check = True
            unknowns.append("علت دقیق داغ‌کردن پیش از بررسی حضوری معلوم نیست.")
        elif service == "clutch_kit_replacement" and answers.get("symptom_slip") == "گاهی":
            suggested = "conditional"
            needs_check = False
        elif service:
            suggested = "fixed"
            needs_check = False
        else:
            suggested = "diagnostic"
            needs_check = True

        return {
            "facts": facts or ["اطلاعات ثبت‌شده برای ساخت خلاصه کافی نبود."],
            "unknowns": unknowns,
            "suggestedOfferType": suggested,
            "needsInPersonCheck": needs_check,
            "note": DEMO_NOTE,
        }

    def _build_ExpenseExtraction(self, context: dict[str, Any]) -> dict[str, Any]:
        text = str(context.get("text", ""))
        items: list[dict[str, Any]] = []
        for index, raw_line in enumerate(
            [line.strip() for line in text.splitlines() if line.strip()]
        ):
            amount_match = _AMOUNT_PATTERN.search(raw_line)
            minutes_match = _MINUTES_PATTERN.search(raw_line)
            is_labor = any(word in raw_line for word in ("اجرت", "دستمزد", "کار"))
            items.append(
                {
                    "sourceText": raw_line[:400],
                    "title": raw_line.split("،")[0][:200] or f"قلم {index + 1}",
                    "type": "labor" if is_labor else "part",
                    "amountToman": _digits(amount_match.group(1)) if amount_match else None,
                    "quantity": None if is_labor else 1,
                    "minutes": _digits(minutes_match.group(1)) if minutes_match else None,
                    "payer": "specialist",
                    # Nothing extracted from free text is treated as settled by itself.
                    "needsConfirmation": True,
                }
            )
        return {"items": items, "note": DEMO_NOTE}

    def _build_EvaluationResult(self, context: dict[str, Any]) -> dict[str, Any]:
        changes = []
        for change in context.get("changes", []):
            evidence = list(change.get("evidenceIds") or [])
            reason_text = (change.get("reason") or "").strip()
            delta = change.get("delta")
            if delta is None or delta <= 0:
                verdict, reason = (
                    "justified",
                    "این تغییر افزایش مبلغ ندارد، بنابراین اثر منفی ثبت نمی‌شود.",
                )
            elif evidence and reason_text:
                verdict, reason = (
                    "justified",
                    "برای افزایش، دلیل و شاهد ثبت‌شده در پرونده موجود است.",
                )
            elif reason_text and not evidence:
                verdict, reason = (
                    "insufficient_evidence",
                    "دلیل نوشته شده ولی شاهدی به آن پیوست نشده است.",
                )
            else:
                verdict, reason = (
                    "unjustified",
                    "افزایش مبلغ بدون دلیل و شاهد ثبت شده است.",
                )
            changes.append(
                {
                    "changeId": change.get("changeId", ""),
                    "verdict": verdict,
                    "reason": f"{reason} ({DEMO_NOTE})",
                    "evidenceIds": evidence,
                    "missingFields": [] if evidence else ["evidenceIds"],
                }
            )
        return {"changes": changes}

    def _build_PriceFairnessReview(self, context: dict[str, Any]) -> dict[str, Any]:
        snapshot = context.get("referenceSnapshot") or {}
        mean = snapshot.get("meanToman")
        factor = float(context.get("fairPriceFactor") or 1.2)
        findings = []
        for offer in context.get("offers", []):
            total = offer.get("totalToman")
            if not offer.get("comparable", True) or mean is None or total is None:
                findings.append(
                    {
                        "offerVersionId": offer.get("offerVersionId", ""),
                        "comparable": False,
                        "verdict": "insufficient_evidence",
                        "referenceSnapshotId": snapshot.get("id"),
                        "reason": f"هم‌ارزی دامنه یا مبلغ قابل اثبات نیست. ({DEMO_NOTE})",
                        "evidenceIds": [],
                    }
                )
                continue
            fair = total <= mean * factor
            findings.append(
                {
                    "offerVersionId": offer.get("offerVersionId", ""),
                    "comparable": True,
                    "verdict": "fair" if fair else "unfair",
                    "referenceSnapshotId": snapshot.get("id"),
                    "reason": (
                        f"مبلغ کل هم‌دامنه نسبت به میانگین {mean:,} تومانی گروه بررسی شد. "
                        f"({DEMO_NOTE})"
                    ),
                    "evidenceIds": [snapshot.get("id")] if snapshot.get("id") else [],
                }
            )
        return {"findings": findings}

    def _build_DisputeDecisionOutput(self, context: dict[str, Any]) -> dict[str, Any]:
        claims = context.get("claimItems", [])
        evidence_ids = set(context.get("evidenceIds", []))
        decisions = []
        missing: list[str] = []

        for claim in claims:
            claim_evidence = [e for e in (claim.get("evidenceIds") or []) if e in evidence_ids]
            claimed_amount = int(claim.get("claimedAmountToman") or 0)
            claimed_quantity = int(claim.get("claimedQuantity") or 0)
            in_agreement = bool(claim.get("inActiveAgreement"))
            receipt_confirmed = bool(claim.get("receiptConfirmedByCustomer"))

            if claim_evidence and (in_agreement or receipt_confirmed):
                verdict, quantity, amount, reason = (
                    "accepted",
                    claimed_quantity,
                    claimed_amount,
                    "این قلم در دامنهٔ توافق‌شده است و هزینهٔ آن مستند شده است.",
                )
            elif claim_evidence:
                verdict, quantity, amount, reason = (
                    "partially_accepted",
                    claimed_quantity,
                    claimed_amount // 2,
                    "شاهد هزینه موجود است ولی مجوز کامل این قلم در توافق ثبت نشده است.",
                )
            elif in_agreement:
                verdict, quantity, amount, reason = (
                    "partially_accepted",
                    claimed_quantity,
                    claimed_amount // 2,
                    "قلم در دامنهٔ توافق هست ولی مدرک هزینهٔ آن ارائه نشده است.",
                )
            else:
                verdict, quantity, amount, reason = (
                    "rejected",
                    0,
                    0,
                    "برای این قلم نه مجوز توافق و نه شاهد هزینه ثبت نشده است.",
                )
                missing.append(f"evidence:{claim.get('claimItemId')}")

            decisions.append(
                {
                    "claimItemId": claim.get("claimItemId", ""),
                    "verdict": verdict,
                    "acceptedQuantity": quantity,
                    "acceptedAmountToman": amount,
                    "reason": f"{reason} ({DEMO_NOTE})",
                    "evidenceIds": claim_evidence,
                }
            )

        needs_evidence = bool(context.get("requestEvidenceRound")) and bool(missing)
        return {
            "disputeId": context.get("disputeId", ""),
            "inputSnapshotId": context.get("inputSnapshotId", ""),
            "status": "needs_evidence" if needs_evidence else "decided",
            "lineDecisions": [] if needs_evidence else decisions,
            "reason": (
                "برای تصمیم‌گیری به اطلاعات تکمیلی نیاز است."
                if needs_evidence
                else "حکم بر اساس دامنهٔ توافق، شواهد ثبت‌شده و وضعیت تأیید رسیدها صادر شد."
            )
            + f" ({DEMO_NOTE})",
            "evidenceIds": sorted(evidence_ids),
            "missingFields": missing if needs_evidence else [],
        }
