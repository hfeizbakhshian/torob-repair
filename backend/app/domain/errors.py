"""The shared error contract: `{code, message, fieldErrors?, currentRevision?}`."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_STATE = "INVALID_STATE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFLICT = "CONFLICT"


_STATUS_BY_CODE = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.CONFLICT: 409,
}


class DomainError(Exception):
    """Raised by domain services. The API layer renders it as the shared error shape.

    The message is written for the person using the product; internal details and
    confidential input never go into it.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        current_revision: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.current_revision = current_revision
        self.data = data

    @property
    def status_code(self) -> int:
        return _STATUS_BY_CODE[self.code]

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.field_errors:
            payload["fieldErrors"] = self.field_errors
        if self.current_revision is not None:
            payload["currentRevision"] = self.current_revision
        if self.data:
            payload["data"] = self.data
        return payload


def validation_error(message: str, **field_errors: str) -> DomainError:
    return DomainError(
        ErrorCode.VALIDATION_ERROR, message, field_errors=field_errors or None
    )


def forbidden(message: str = "دسترسی به این بخش برای حساب شما مجاز نیست.") -> DomainError:
    return DomainError(ErrorCode.FORBIDDEN, message)


def not_found(message: str = "مورد خواسته‌شده پیدا نشد.") -> DomainError:
    return DomainError(ErrorCode.NOT_FOUND, message)


def invalid_state(message: str) -> DomainError:
    return DomainError(ErrorCode.INVALID_STATE, message)


def version_conflict(current_revision: int) -> DomainError:
    return DomainError(
        ErrorCode.VERSION_CONFLICT,
        "این مورد در فاصلهٔ کار شما تغییر کرده است. نسخهٔ تازه را ببینید و دوباره اقدام کنید.",
        current_revision=current_revision,
    )


def quota_exceeded(message: str) -> DomainError:
    return DomainError(ErrorCode.QUOTA_EXCEEDED, message)


def service_unavailable(message: str) -> DomainError:
    return DomainError(ErrorCode.SERVICE_UNAVAILABLE, message)
