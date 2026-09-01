from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.app.telemetry.models import TelemetrySourceStatus


class MetricResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    p95_latency_ms: int
    error_rate_percent: float
    timestamp: datetime
    telemetry_status: TelemetrySourceStatus = TelemetrySourceStatus.HEALTHY
    observed_at: datetime | None = None


class LogResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    timestamp: datetime
    level: str
    message: str
    telemetry_status: TelemetrySourceStatus = TelemetrySourceStatus.HEALTHY
    observed_at: datetime | None = None


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    version: str
    timestamp: datetime
    telemetry_status: TelemetrySourceStatus = TelemetrySourceStatus.HEALTHY
    observed_at: datetime | None = None


class ServiceHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    p95_latency_ms: int
    error_rate_percent: float
    max_p95_latency_ms: int
    max_error_rate_percent: float
    healthy: bool
    telemetry_status: TelemetrySourceStatus = TelemetrySourceStatus.HEALTHY
    observed_at: datetime | None = None
