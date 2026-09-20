"""Rendering every failure as the one shared error shape."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

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

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, __: IntegrityError) -> JSONResponse:
        """A database invariant refused the write.

        These are the last line of defence — one live offer per specialist, one open
        selection, one successful payment, one refund per payment — and a losing race
        should read as a conflict, never as a server crash. The driver's message is not
        forwarded, because it can quote the submitted values.
        """
        return JSONResponse(
            status_code=409,
            content={
                "code": ErrorCode.CONFLICT.value,
                "message": (
                    "این اقدام با وضعیت فعلی پرونده سازگار نیست؛ "
                    "صفحه را تازه کنید و نسخهٔ به‌روز را ببینید."
                ),
            },
        )

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
