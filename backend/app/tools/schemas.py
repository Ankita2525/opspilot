from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MetricResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    p95_latency_ms: int
    error_rate_percent: float
    timestamp: datetime


class LogResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    timestamp: datetime
    level: str
    message: str


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    version: str
    timestamp: datetime


class ServiceHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    p95_latency_ms: int
    error_rate_percent: float
    max_p95_latency_ms: int
    max_error_rate_percent: float
    healthy: bool
