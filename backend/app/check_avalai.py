"""Explicit AvalAI live smoke tests; one request per selected model, no retries."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.ai.avalai import AvalAiProvider
from app.providers.ai.base import AiMessage, AiRequest, AiResponse
from app.schemas.ai import ClarifyQuestions, json_schema_for


def build_request(input_language: Literal["en", "fa"] = "en") -> AiRequest:
    schema = json_schema_for(ClarifyQuestions)
    if input_language == "fa":
        instruction = (
            "فقط یک شیء JSON مطابق ساختار زیر برگردان. برای روشن‌شدن درخواست بررسی خودرو، "
            "یک یا حداکثر دو سؤال کوتاه فارسی بپرس. متن سؤال‌ها فارسی باشد؛ "
            "کلیدها طبق ساختار، مقدار type برابر short_text و options آرایهٔ خالی باشد. "
            "تشخیص قطعی یا قیمت نده. پاسخ را کوتاه نگه دار. ساختار خروجی:\n"
        )
        user_text = (
            "پژو ۲۰۶ تیپ ۵ دارم. هنگام ترمز گرفتن صدای سایش می‌شنوم. "
            "برای ثبت درخواست بررسی خودرو چه اطلاعاتی لازم است؟"
        )
    else:
        instruction = (
            "Return only a JSON object matching this schema. Ask at most two short "
            "clarifying questions in Persian for a car repair request. "
            "Do not diagnose or invent prices.\n"
        )
        user_text = (
            "My Peugeot 206 makes a scraping noise when braking. "
            "What details are needed to request an inspection?"
        )
    return AiRequest(
        messages=[
            AiMessage(role="system", content=instruction + json.dumps(schema, ensure_ascii=False)),
            AiMessage(role="user", content=user_text),
        ],
        schema_name="ClarifyQuestions",
        json_schema=schema,
        max_output_tokens=650,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )


COMPARISON_MODELS = ("deepseek-v4.1-flash", "glm-5.3-flash")


async def discover_models() -> set[str] | None:
    async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
        try:
            response = await client.get(
                str(settings.ai_base_url).rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + str(settings.ai_api_key)},
            )
            response.raise_for_status()
            ids = {item["id"] for item in response.json()["data"]}
            if not all(isinstance(model, str) for model in ids):
                raise ValueError("invalid model IDs")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            print("فهرست مدل‌ها دریافت نشد؛ آدرس، کلید و دسترسی شبکه را بررسی کنید.")
            return None
    return ids


def build_report(
    provider: AvalAiProvider,
    request: AiRequest,
    result: AiResponse,
    input_language: Literal["en", "fa"],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "inputLanguage": input_language,
        "provider": provider.name,
        "model": provider.model,
        "finishReason": result.finish_reason,
        "errorCode": result.error_code,
        "usage": result.usage.model_dump(),
        "latencyMs": result.latency_ms,
        "schemaValid": False,
        "costToman": None,
    }
    if input_language == "fa":
        report.update(
            sampleInput=request.messages[-1].content,
            response=None,
            persianScriptDetected=False,
            questionCountValid=False,
        )
    if result.ok:
        try:
            parsed = ClarifyQuestions.model_validate(result.payload)
            report["schemaValid"] = True
            if input_language == "fa":
                report["response"] = parsed.model_dump(mode="json", by_alias=True)
                report["questionCountValid"] = 1 <= len(parsed.questions) <= 2
                # Script presence is a smoke check, not proof of Persian fluency or quality.
                report["persianScriptDetected"] = bool(parsed.questions) and all(
                    any(char.isalpha() and "\u0600" <= char <= "\u06ff" for char in question.text)
                    for question in parsed.questions
                )
        except ValidationError:
            report["errorCode"] = "schema_validation_failed"
    passed = bool(report["schemaValid"])
    if input_language == "fa":
        passed = passed and report["questionCountValid"] and report["persianScriptDetected"]
    report["smokePassed"] = passed
    return report


async def check(input_language: Literal["en", "fa"] = "en", *, model: str | None = None) -> int:
    provider = AvalAiProvider(model=model) if model else AvalAiProvider()
    ids = await discover_models()
    if ids is None:
        return 1
    if provider.model not in ids:
        print("مدل تنظیم‌شده در فهرست درگاه نیست؛ فراخوانی انجام نشد.")
        return 1
    request = build_request(input_language)
    report = build_report(provider, request, await provider.complete(request), input_language)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("این آزمون یک فراخوانی واقعی دارد؛ سنجش کیفیت ۲۴ پرونده‌ای یا تست کامل محصول نیست.")
    return 0 if report["smokePassed"] else 1


async def compare_models(
    input_language: Literal["en", "fa"] = "fa",
    *,
    models: tuple[str, ...] = COMPARISON_MODELS,
) -> int:
    # Validate connection settings before discovery, without sending a completion.
    AvalAiProvider()
    ids = await discover_models()
    if ids is None:
        return 1
    request = build_request(input_language)
    reports: list[dict[str, Any]] = []
    for model in models:
        if model not in ids:
            report = {
                "model": model,
                "inputLanguage": input_language,
                "status": "unavailable",
                "errorCode": "model_not_advertised",
                "requestSent": False,
                "smokePassed": False,
                "message": "این شناسه در فهرست درگاه نیست؛ درخواست به مدل پیش‌فرض ارسال نشد.",
            }
        else:
            print(f"در حال آزمون {model} با ورودی یکسان...", flush=True)
            provider = AvalAiProvider(model=model)
            report = build_report(
                provider, request, await provider.complete(request), input_language
            )
            report["requestSent"] = True
            report["status"] = "passed" if report["smokePassed"] else "failed"
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

    all_passed = bool(reports) and all(report["smokePassed"] for report in reports)
    summary = {
        "requestedModels": list(models),
        "requestsSent": sum(report["requestSent"] for report in reports),
        "passedModels": [r["model"] for r in reports if r["status"] == "passed"],
        "failedModels": [r["model"] for r in reports if r["status"] == "failed"],
        "unavailableModels": [r["model"] for r in reports if r["status"] == "unavailable"],
        "allPassed": all_passed,
    }
    print("خلاصهٔ مقایسه:", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if all_passed else 1


def main(*, input_language: Literal["en", "fa"] = "en", compare: bool = False) -> int:
    parser = argparse.ArgumentParser(description="آزمون مدل‌های AvalAI با ورودی نمونهٔ یکسان")
    parser.add_argument("--live", action="store_true", help="اجازهٔ ارسال درخواست‌های واقعی مدل")
    parser.add_argument("--model", help="فقط این شناسهٔ مدل آزموده شود؛ بدون تغییر تنظیمات برنامه")
    args = parser.parse_args()
    if not args.live:
        parser.error("برای ارسال درخواست واقعی و مصرف اعتبار، --live لازم است.")
    if settings.ai_provider != "avalai" or settings.ai_mode != "live":
        parser.error("AI_MODE=live و AI_PROVIDER=avalai لازم‌اند.")
    if compare:
        models = (args.model,) if args.model else COMPARISON_MODELS
        return asyncio.run(compare_models(input_language, models=models))
    return asyncio.run(check(input_language, model=args.model))


if __name__ == "__main__":
    raise SystemExit(main())
