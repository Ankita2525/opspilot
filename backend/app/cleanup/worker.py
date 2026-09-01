from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from backend.app.ids import new_incident_id
from backend.app.persistence.models import AuditRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.sandbox.lease_store import GlobalSandboxLeaseStore

if TYPE_CHECKING:
    from backend.app.api.session_store import IncidentSessionStore
    from backend.app.live.orchestrator import LiveIncidentOrchestrator

logger = logging.getLogger(__name__)

TERMINAL_INCIDENT_STATUSES = frozenset(
    {
        "resolved",
        "rejected",
        "remediation_failed",
        "blocked_by_telemetry",
        "abandoned",
        "expired",
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
        interval_seconds: float = 30.0,
    ) -> None:
        self._lease_store = lease_store
        self._session_store = session_store
        self._live_orchestrator = live_orchestrator
        self._repository = repository
        self._list_expired = list_expired_incidents
        self._interval_seconds = interval_seconds
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
        self._lease_store.expire_stale()
        now = datetime.now(UTC)
        for incident_id, session_id in self._list_expired(now):
            if self._cleanup_incident(incident_id, session_id):
                cleaned += 1
        return cleaned

    def _cleanup_incident(self, incident_id: str, session_id: str | None) -> bool:
        session = self._session_store.get_optional(incident_id)
        if session is not None and session.live_session is not None:
            try:
                live = session.live_session
                if live.current_revision and live.mapping:
                    try:
                        live.control.rollback(live.mapping.healthy_revision)
                    except Exception:
                        logger.exception(
                            "Failed to rollback sandbox for incident %s", incident_id
                        )
                self._live_orchestrator.cleanup(live)
            except Exception:
                logger.exception("Failed to cleanup live session for %s", incident_id)
            self._session_store.remove(incident_id)
        if session_id:
            self._lease_store.release(session_id=session_id, incident_id=incident_id)
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
        record = self._repository.get_incident(incident_id)
        if record is not None and record.status not in TERMINAL_INCIDENT_STATUSES:
            updated = record.model_copy(
                update={"status": "expired", "updated_at": datetime.now(UTC)}
            )
            self._repository.save_incident(updated)
        return True
