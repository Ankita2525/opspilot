from __future__ import annotations

from simulator.models import (
    AuditEvent,
    DeploymentEvent,
    IncidentScenario,
    LogEvent,
    MetricSnapshot,
    Remediation,
)
from simulator.scenarios import get_scenario


class SimulatedEnvironment:
    """In-memory production snapshot for a single loaded incident scenario."""

    def __init__(self) -> None:
        self._scenario: IncidentScenario | None = None
        self._resolved = False
        self._audit_events: list[AuditEvent] = []

    def load_scenario(self, scenario_id: str) -> IncidentScenario:
        self._scenario = get_scenario(scenario_id)
        self._resolved = False
        self._audit_events = []
        return self._scenario

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    def query_metrics(self, service: str) -> MetricSnapshot:
        scenario = self._require_known_service(service)
        if self._resolved:
            return scenario.recovered_metrics
        return scenario.incident_metrics

    def get_logs(self, service: str) -> list[LogEvent]:
        scenario = self._require_known_service(service)
        return list(scenario.logs)

    def get_recent_deployments(self, service: str) -> list[DeploymentEvent]:
        scenario = self._require_known_service(service)
        return list(scenario.deployments)

    def rollback_deployment(self, service: str, version: str) -> None:
        scenario = self._require_scenario()
        if service != scenario.affected_service:
            raise ValueError(f"Cannot roll back unknown service: {service}")

        matching_versions = {event.version for event in scenario.deployments}
        if version not in matching_versions:
            raise ValueError(f"Cannot roll back unknown deployment version: {version}")

        if scenario.expected_remediation != Remediation.ROLLBACK_DEPLOYMENT:
            raise ValueError(
                f"Rollback is not the expected remediation for {scenario.id}"
            )

        self._resolved = True
        self._audit_events.append(
            AuditEvent(
                timestamp=scenario.recovered_metrics.timestamp,
                action="rollback_deployment",
                details=f"Rolled back {service} deployment {version}",
            )
        )

    def get_audit_events(self) -> list[AuditEvent]:
        return list(self._audit_events)

    def _require_scenario(self) -> IncidentScenario:
        if self._scenario is None:
            raise ValueError("No scenario loaded")
        return self._scenario

    def _require_known_service(self, service: str) -> IncidentScenario:
        scenario = self._require_scenario()
        if service != scenario.affected_service:
            raise ValueError(f"Unknown service: {service}")
        return scenario
