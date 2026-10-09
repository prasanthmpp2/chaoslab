from __future__ import annotations


class PlatformError(Exception):
    status_code = 400
    code = "platform_error"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationRejected(PlatformError):
    status_code = 422
    code = "validation_rejected"


class SafetyViolation(PlatformError):
    status_code = 403
    code = "safety_violation"


class NotFound(PlatformError):
    status_code = 404
    code = "not_found"


class Conflict(PlatformError):
    status_code = 409
    code = "conflict"


class Unauthorized(PlatformError):
    status_code = 401
    code = "unauthorized"


class Forbidden(PlatformError):
    status_code = 403
    code = "forbidden"


class RateLimited(PlatformError):
    status_code = 429
    code = "rate_limited"


class DependencyUnavailable(PlatformError):
    status_code = 503
    code = "dependency_unavailable"


class EngineError(PlatformError):
    """An engine adapter failed. `category` feeds structured logs."""

    status_code = 502
    code = "engine_error"

    def __init__(self, message: str, category: str = "engine", details: dict | None = None) -> None:
        super().__init__(message, details)
        self.category = category
