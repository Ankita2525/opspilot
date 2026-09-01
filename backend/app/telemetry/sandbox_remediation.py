from __future__ import annotations

from datetime import UTC, datetime

from simulator.models import AuditEvent

from backend.app.telemetry.remediation import RemediationBackend
from sandbox.control import SandboxControlClient


class SandboxRemediationBackend:
    """Live sandbox rollback adapter — no simulator mutation."""

    def __init__(self, control: SandboxControlClient) -> None:
        self._control = control
        self._resolved = False
        self._audit_events: list[AuditEvent] = []

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    def rollback_deployment(self, service: str, version: str) -> None:
        status = self._control.rollback(version)
        if status.get("service") != service:
            raise ValueError(f"Cannot roll back unknown service: {service}")
        self._resolved = True
        self._audit_events.append(
            AuditEvent(
                timestamp=datetime.now(UTC),
                action="rollback_deployment",
                details=f"Rolled back {service} deployment {version}",
            )
        )

    def get_audit_events(self) -> list[AuditEvent]:
        return list(self._audit_events)
