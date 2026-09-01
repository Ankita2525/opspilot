from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TelemetryMode(str, Enum):
    REFERENCE = "reference"
    LIVE = "live"


class TelemetrySourceKind(str, Enum):
    METRICS = "metrics"
    LOGS = "logs"
    DEPLOYMENTS = "deployments"
    SERVICE_HEALTH = "service_health"


class TelemetrySourceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class TelemetrySourceHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: TelemetrySourceKind
    status: TelemetrySourceStatus
    observed_at: datetime
    freshness_seconds: float = Field(ge=0)
    error_category: str | None = None


class TelemetryObservation(BaseModel):
    """Wrapper for telemetry data with source health metadata."""

    model_config = ConfigDict(frozen=True)

    health: TelemetrySourceHealth
    partial_evidence: bool = False


class ServiceThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_p95_latency_ms: int
    max_error_rate_percent: float


SANDBOX_SERVICE_THRESHOLDS: dict[str, ServiceThresholds] = {
    "checkout-api": ServiceThresholds(max_p95_latency_ms=400, max_error_rate_percent=1.0),
    "auth-service": ServiceThresholds(max_p95_latency_ms=350, max_error_rate_percent=1.0),
    "payments-service": ServiceThresholds(max_p95_latency_ms=500, max_error_rate_percent=1.0),
}
