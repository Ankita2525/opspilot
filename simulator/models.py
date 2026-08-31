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


class Remediation(str, Enum):
    ROLLBACK_DEPLOYMENT = "rollback_deployment"


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
class IncidentScenario:
    id: str
    title: str
    affected_service: str
    incident_start: datetime
    known_root_cause: RootCause
    expected_remediation: Remediation
    incident_metrics: MetricSnapshot
    recovered_metrics: MetricSnapshot
    logs: list[LogEvent]
    deployments: list[DeploymentEvent]
