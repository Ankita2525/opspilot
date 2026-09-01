from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from backend.app.telemetry.models import TelemetrySourceKind, TelemetrySourceStatus


@dataclass
class EvidenceReadiness:
    metrics_available: bool
    logs_available: bool
    deployments_available: bool
    service_health_available: bool
    partial_evidence: bool
    blocked: bool
    blocked_reason: str | None = None
    source_statuses: dict[TelemetrySourceKind, TelemetrySourceStatus] = field(
        default_factory=dict
    )

    def can_investigate(self) -> bool:
        return not self.blocked

    def can_execute_remediation(self) -> bool:
        return self.deployments_available and not self.blocked

    def can_verify_recovery(self) -> bool:
        return self.metrics_available and self.service_health_available and not self.blocked


STALE_THRESHOLD_SECONDS = 120.0


def assess_readiness(
    source_health: list,
    *,
    require_metrics: bool = True,
    require_logs: bool = False,
) -> EvidenceReadiness:
    statuses = {item.source: item.status for item in source_health}
    metrics_available = (
        statuses.get(TelemetrySourceKind.METRICS) == TelemetrySourceStatus.HEALTHY
    )
    logs_available = statuses.get(TelemetrySourceKind.LOGS) in {
        TelemetrySourceStatus.HEALTHY,
        TelemetrySourceStatus.DEGRADED,
    }
    deployments_available = (
        statuses.get(TelemetrySourceKind.DEPLOYMENTS) == TelemetrySourceStatus.HEALTHY
    )
    service_health_available = (
        statuses.get(TelemetrySourceKind.SERVICE_HEALTH)
        == TelemetrySourceStatus.HEALTHY
    )
    partial = any(
        status in {TelemetrySourceStatus.DEGRADED, TelemetrySourceStatus.STALE}
        for status in statuses.values()
    )
    blocked = False
    blocked_reason = None
    if require_metrics and not metrics_available:
        if not logs_available and not deployments_available:
            blocked = True
            blocked_reason = "All critical telemetry sources are unavailable."
        elif not deployments_available:
            blocked = True
            blocked_reason = "Deployments and metrics are unavailable."
    if require_logs and not logs_available and not metrics_available:
        blocked = True
        blocked_reason = "Required telemetry sources are unavailable."
    return EvidenceReadiness(
        metrics_available=metrics_available,
        logs_available=logs_available,
        deployments_available=deployments_available,
        service_health_available=service_health_available,
        partial_evidence=partial,
        blocked=blocked,
        blocked_reason=blocked_reason,
        source_statuses=statuses,
    )


def is_stale(observed_at: datetime, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    return (current - observed_at).total_seconds() > STALE_THRESHOLD_SECONDS
