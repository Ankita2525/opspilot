from __future__ import annotations

from typing import Protocol

from simulator.models import AuditEvent


class RemediationBackend(Protocol):
    """Controlled environment mutation for approved remediations."""

    @property
    def is_resolved(self) -> bool:
        ...

    def rollback_deployment(self, service: str, version: str) -> None:
        ...

    def get_audit_events(self) -> list[AuditEvent]:
        ...
