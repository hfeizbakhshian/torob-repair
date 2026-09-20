"""Rendering every failure as the one shared error shape."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import DomainError, ErrorCode

_FIELD_MESSAGES = {
    "missing": "این فیلد الزامی است.",
    "string_too_long": "طول متن بیش از حد مجاز است.",
    "string_too_short": "متن کوتاه‌تر از حد مجاز است.",
    "greater_than_equal": "مقدار کمتر از حد مجاز است.",
    "less_than_equal": "مقدار بیشتر از حد مجاز است.",
    "int_parsing": "مقدار باید عدد صحیح باشد.",
}


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's 422 is converted into the same contract, without leaking internal
        # details or the submitted values back to the client.
        field_errors: dict[str, str] = {}
        for item in error.errors():
            location = [part for part in item.get("loc", ()) if part not in ("body", "query")]
            key = ".".join(str(part) for part in location) or "body"
            field_errors[key] = _FIELD_MESSAGES.get(
                str(item.get("type")), "مقدار واردشده معتبر نیست."
            )
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "ورودی ارسال‌شده معتبر نیست.",
                "fieldErrors": field_errors,
            },
        )
