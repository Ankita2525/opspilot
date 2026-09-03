from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderErrorCategory(str, Enum):
    BAD_REQUEST = "bad_request"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    PROVIDER_5XX = "provider_5xx"
    TIMEOUT = "timeout"
    NETWORK = "network"
    EMPTY_RESPONSE = "empty_response"
    JSON_PARSE = "json_parse"
    VALIDATION = "validation"
    REFUSAL = "refusal"
    UNKNOWN = "unknown"


# Transient categories may be retried at the HTTP attempt layer.
RETRYABLE_CATEGORIES = frozenset(
    {
        ProviderErrorCategory.RATE_LIMITED,
        ProviderErrorCategory.PROVIDER_5XX,
        ProviderErrorCategory.TIMEOUT,
        ProviderErrorCategory.NETWORK,
    }
)

# Completed-but-unusable responses still consume demo quota (abuse resistance).
CONSUME_QUOTA_CATEGORIES = frozenset(
    {
        ProviderErrorCategory.EMPTY_RESPONSE,
        ProviderErrorCategory.JSON_PARSE,
        ProviderErrorCategory.VALIDATION,
        ProviderErrorCategory.REFUSAL,
    }
)

# Request construction / transport failures refund the logical reservation.
REFUND_QUOTA_CATEGORIES = frozenset(
    {
        ProviderErrorCategory.BAD_REQUEST,
        ProviderErrorCategory.AUTH,
        ProviderErrorCategory.RATE_LIMITED,
        ProviderErrorCategory.PROVIDER_5XX,
        ProviderErrorCategory.TIMEOUT,
        ProviderErrorCategory.NETWORK,
        ProviderErrorCategory.UNKNOWN,
    }
)


@dataclass(frozen=True)
class ProviderFailureMeta:
    """Sanitized diagnostic metadata — never prompts, keys, or raw model output."""

    category: ProviderErrorCategory
    exception_class: str
    http_status: int | None = None
    provider_code: str | None = None
    provider_type: str | None = None
    retry_attempt: int | None = None
    stage: str | None = None
    incident_id: str | None = None
    output_length: int | None = None
    parse_error_position: int | None = None
    validation_fields: tuple[str, ...] = ()
    token_usage: dict[str, int] | None = None
    retry_after_seconds: float | None = None

    def public_reason(self) -> str:
        return self.category.value

    def safe_log_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "category": self.category.value,
            "exception_class": self.exception_class,
        }
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.provider_code:
            payload["provider_code"] = self.provider_code
        if self.provider_type:
            payload["provider_type"] = self.provider_type
        if self.retry_attempt is not None:
            payload["retry_attempt"] = self.retry_attempt
        if self.stage:
            payload["stage"] = self.stage
        if self.incident_id:
            payload["incident_id"] = self.incident_id
        if self.output_length is not None:
            payload["output_length"] = self.output_length
        if self.parse_error_position is not None:
            payload["parse_error_position"] = self.parse_error_position
        if self.validation_fields:
            payload["validation_fields"] = list(self.validation_fields)
        if self.token_usage:
            payload["token_usage"] = dict(self.token_usage)
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


class ModelCallError(Exception):
    """Typed model-call failure with safe metadata and quota accounting hints."""

    def __init__(self, meta: ProviderFailureMeta, *, message: str | None = None) -> None:
        self.meta = meta
        super().__init__(message or meta.category.value)

    @property
    def category(self) -> ProviderErrorCategory:
        return self.meta.category

    @property
    def retryable(self) -> bool:
        return self.meta.category in RETRYABLE_CATEGORIES

    @property
    def consume_quota(self) -> bool:
        return self.meta.category in CONSUME_QUOTA_CATEGORIES

    @property
    def refund_quota(self) -> bool:
        return self.meta.category in REFUND_QUOTA_CATEGORIES
