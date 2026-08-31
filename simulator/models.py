from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class LogLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class RootCause(str, Enum):
    DB_CONNECTION_POOL_REGRESSION = "db_connection_pool_regression"
    AUTH_TOKEN_VALIDATION_REGRESSION = "auth_token_validation_regression"
    PAYMENT_PROVIDER_TIMEOUT_REGRESSION = "payment_provider_timeout_regression"


class Remediation(str, Enum):
    ROLLBACK_DEPLOYMENT = "rollback_deployment"


@dataclass(frozen=True)
class ServiceHealthThresholds:
    max_p95_latency_ms: int
    max_error_rate_percent: float


@dataclass(frozen=True)
class MetricSnapshot:
    service: str
    p95_latency_ms: int
    error_rate_percent: float
    timestamp: datetime


@dataclass(frozen=True)
class LogEvent:
    service: str
    timestamp: datetime
    level: LogLevel
    message: str


@dataclass(frozen=True)
class DeploymentEvent:
    service: str
    version: str
    timestamp: datetime


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    action: str
    details: str


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    p95_latency_ms: int
    error_rate_percent: float
    max_p95_latency_ms: int
    max_error_rate_percent: float
    healthy: bool


@dataclass(frozen=True)
class IncidentScenario:
    id: str
    title: str
    affected_service: str
    incident_start: datetime
    known_root_cause: RootCause
    expected_remediation: Remediation
    incident_metrics: MetricSnapshot
    recovered_metrics: MetricSnapshot
    health_thresholds: ServiceHealthThresholds
    logs: list[LogEvent]
    deployments: list[DeploymentEvent]


def evaluate_service_health(
    p95_latency_ms: int,
    error_rate_percent: float,
    thresholds: ServiceHealthThresholds,
) -> bool:
    return (
        p95_latency_ms <= thresholds.max_p95_latency_ms
        and error_rate_percent <= thresholds.max_error_rate_percent
    )
