"""Error handling.

Standard error categories per TSD §39. Errors are actionable internally
(logged with full context) but safe externally (no stack traces, no
sensitive data in responses). Every response carries a request_id
(correlation id) injected by middleware.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error with a stable external error code."""

    error_code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self, request_id: str | None) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details or None,
            "request_id": request_id,
        }


class AuthenticationError(AppError):
    error_code = "AUTHENTICATION_ERROR"
    http_status = 401


class AuthorizationError(AppError):
    error_code = "AUTHORIZATION_ERROR"
    http_status = 403


class ValidationError(AppError):
    error_code = "VALIDATION_ERROR"
    http_status = 422


class FileError(AppError):
    error_code = "FILE_ERROR"
    http_status = 400


class OcrError(AppError):
    error_code = "OCR_ERROR"
    http_status = 502


class ExtractionError(AppError):
    error_code = "EXTRACTION_ERROR"
    http_status = 502


class ProductMatchError(AppError):
    error_code = "PRODUCT_MATCH_ERROR"
    http_status = 404


class WarrantySourceError(AppError):
    error_code = "WARRANTY_SOURCE_ERROR"
    http_status = 502


class WarrantyMappingError(AppError):
    error_code = "WARRANTY_MAPPING_ERROR"
    http_status = 409


class ProcessingError(AppError):
    error_code = "PROCESSING_ERROR"
    http_status = 500


class NotificationError(AppError):
    error_code = "NOTIFICATION_ERROR"
    http_status = 502


class ClaimError(AppError):
    error_code = "CLAIM_ERROR"
    http_status = 409


class ConflictError(AppError):
    error_code = "CONFLICT"
    http_status = 409


class NotFoundError(AppError):
    error_code = "NOT_FOUND"
    http_status = 404


class RateLimitError(AppError):
    error_code = "RATE_LIMITED"
    http_status = 429


def log_error(exc: Exception, *, request_id: str | None = None, context: dict[str, Any] | None = None) -> None:
    """Log an error with full context internally; safe externally by design."""
    extra = {"request_id": request_id, **(context or {})}
    if isinstance(exc, AppError):
        logger.warning("app_error %s: %s", exc.error_code, exc.message, extra=extra)
    else:
        logger.exception("unhandled_error: %s", exc, extra=extra)
