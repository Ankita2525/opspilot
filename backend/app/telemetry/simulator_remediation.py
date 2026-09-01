from __future__ import annotations

from simulator.environment import SimulatedEnvironment
from simulator.models import AuditEvent

from backend.app.telemetry.remediation import RemediationBackend


class SimulatorRemediationBackend:
    """Reference-mode remediation backed by the in-memory simulator."""

    def __init__(self, environment: SimulatedEnvironment) -> None:
        self._environment = environment

    @property
    def environment(self) -> SimulatedEnvironment:
        return self._environment

    @property
    def is_resolved(self) -> bool:
        return self._environment.is_resolved

    def rollback_deployment(self, service: str, version: str) -> None:
        self._environment.rollback_deployment(service, version)

    def get_audit_events(self) -> list[AuditEvent]:
        return self._environment.get_audit_events()
