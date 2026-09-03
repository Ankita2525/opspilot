from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from typing import TYPE_CHECKING

from backend.app.ids import new_incident_id
if TYPE_CHECKING:
    from backend.app.api.session_store import IncidentSessionStore
    from backend.app.live.orchestrator import LiveIncidentOrchestrator
    from backend.app.persistence.lifecycle import IncidentLifecyclePersistence
    from backend.app.sandbox.hardening import SandboxHardening

from backend.app.persistence.models import AuditRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.sandbox.fault_reconcile import (
    restore_baseline_for_scenario,
    restore_all_sandbox_baselines,
    safe_expire_stale_leases,
)
from backend.app.sandbox.lease_store import GlobalSandboxLeaseStore

logger = logging.getLogger(__name__)

TERMINAL_INCIDENT_STATUSES = frozenset(
    {
        "resolved",
        "rejected",
        "remediation_failed",
        "blocked_by_telemetry",
        "abandoned",
        "expired",
        "cleanup_failed",
        "failed",
        "timed_out",
    }
)


class IncidentCleanupWorker:
    """Periodic cleanup for abandoned live incidents and expired leases."""

    def __init__(
        self,
        *,
        lease_store: GlobalSandboxLeaseStore,
        session_store: IncidentSessionStore,
        live_orchestrator: LiveIncidentOrchestrator,
        repository: OpsPilotRepository,
        list_expired_incidents: Callable[[datetime], list[tuple[str, str | None]]],
        lease_ttl_seconds: int = 240,
        interval_seconds: float = 30.0,
        hardening: SandboxHardening | None = None,
        persistence: IncidentLifecyclePersistence | None = None,
    ) -> None:
        self._lease_store = lease_store
        self._session_store = session_store
        self._live_orchestrator = live_orchestrator
        self._repository = repository
        self._list_expired = list_expired_incidents
        self._lease_ttl_seconds = lease_ttl_seconds
        self._interval_seconds = interval_seconds
        self._hardening = hardening
        self._persistence = persistence
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self) -> int:
        return await asyncio.to_thread(self._cleanup_sync)

    async def _run_loop(self) -> None:
        while not self._stopped:
            try:
                cleaned = await self.run_once()
                if cleaned:
                    logger.info("Cleaned up %d expired live incidents", cleaned)
            except Exception:
                logger.exception("Incident cleanup worker failed")
            await asyncio.sleep(self._interval_seconds)

    def _cleanup_sync(self) -> int:
        cleaned = 0
        self._renew_active_leases()
        if self._hardening is not None and not self._lease_store.is_quarantined():
            safe_expire_stale_leases(self._hardening)
        elif not self._lease_store.is_quarantined():
            self._lease_store.expire_stale()
        now = datetime.now(UTC)
        for incident_id, session_id in self._list_expired(now):
            if self._cleanup_incident(incident_id, session_id):
                cleaned += 1
        return cleaned

    def _renew_active_leases(self) -> None:
        for incident_id, session in self._session_store.list_live_sessions():
            if session.owner_session_id is None:
                continue
            self._lease_store.renew(
                session_id=session.owner_session_id,
                incident_id=incident_id,
                ttl_seconds=self._lease_ttl_seconds,
            )

    def _cleanup_incident(self, incident_id: str, session_id: str | None) -> bool:
        rollback_ok = True
        session = self._session_store.get_optional(incident_id)
        record = self._repository.get_incident(incident_id)
        scenario_id = record.scenario_id if record is not None else None

        if session is not None and session.live_session is not None:
            live = session.live_session
            try:
                live.workload.stop(incident_id)
            except Exception:
                logger.exception("Failed to stop workload for %s", incident_id)
                rollback_ok = False
            if live.mapping and rollback_ok:
                try:
                    live.control.clear_fault()
                except Exception:
                    logger.exception(
                        "Failed to clear sandbox fault for incident %s", incident_id
                    )
                    rollback_ok = False
            if rollback_ok:
                try:
                    self._live_orchestrator.cleanup(live)
                except Exception:
                    logger.exception("Failed to cleanup live session for %s", incident_id)
                    rollback_ok = False
            try:
                self._session_store.remove(incident_id)
            except ValueError:
                pass
        else:
            # No in-memory session — still attempt sidecar clear via scenario mapping.
            if scenario_id:
                ok, _note = restore_baseline_for_scenario(scenario_id)
                rollback_ok = ok
            else:
                ok, _notes = restore_all_sandbox_baselines()
                rollback_ok = ok

        if not rollback_ok:
            self._lease_store.quarantine(
                incident_id=incident_id,
                reason="cleanup_rollback_failed",
            )
            self._repository.append_audit(
                AuditRecord(
                    audit_id=f"audit-{new_incident_id()}",
                    incident_id=incident_id,
                    event_type="cleanup_failed",
                    message="Sandbox cleanup failed; sandbox quarantined.",
                    timestamp=datetime.now(UTC),
                    metadata={"session_id": session_id or "unknown"},
                )
            )
            if record is not None and record.status not in TERMINAL_INCIDENT_STATUSES:
                self._repository.save_incident(
                    record.model_copy(
                        update={
                            "status": "cleanup_failed",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
            return False

        if session_id:
            self._lease_store.release(session_id=session_id, incident_id=incident_id)
        if self._persistence is not None and (
            record is None or record.status not in TERMINAL_INCIDENT_STATUSES
        ):
            self._persistence.record_incident_failed(
                incident_id=incident_id,
                reason="timed_out",
                stage="approval_timeout",
            )
        else:
            self._repository.append_audit(
                AuditRecord(
                    audit_id=f"audit-{new_incident_id()}",
                    incident_id=incident_id,
                    event_type="incident_expired",
                    message="Live incident expired and sandbox was released.",
                    timestamp=datetime.now(UTC),
                    metadata={"session_id": session_id or "unknown"},
                )
            )
            if record is not None and record.status not in TERMINAL_INCIDENT_STATUSES:
                self._repository.save_incident(
                    record.model_copy(
                        update={"status": "failed", "updated_at": datetime.now(UTC)}
                    )
                )
        return True
