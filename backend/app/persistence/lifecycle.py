from collections.abc import Callable
from datetime import datetime, timezone

from backend.app.agent.incident_response import (
    IncidentResponseResumeResult,
    IncidentResponseStartResult,
)
from backend.app.persistence.models import AuditRecord, IncidentRecord
from backend.app.persistence.repository import OpsPilotRepository

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentLifecyclePersistence:
    """Write public incident lifecycle records through OpsPilotRepository."""

    def __init__(
        self,
        repository: OpsPilotRepository,
        now: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or utc_now

    def record_incident_created(
        self,
        *,
        incident_id: str,
        scenario_id: str,
        affected_service: str,
        session_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> datetime:
        timestamp = self._now()
        self._repository.save_incident(
            IncidentRecord(
                incident_id=incident_id,
                scenario_id=scenario_id,
                affected_service=affected_service,
                status="in_progress",
                created_at=timestamp,
                updated_at=timestamp,
                recommended_action=None,
                selected_skills=[],
                resolved=False,
                session_id=session_id,
                expires_at=expires_at,
            )
        )
        self._append_audit(
            incident_id=incident_id,
            event_type="incident_started",
            message="Incident investigation started.",
            timestamp=timestamp,
            metadata={
                "affected_service": affected_service,
                "scenario_id": scenario_id,
            },
        )
        return timestamp

    def record_incident_failed(
        self,
        *,
        incident_id: str,
        reason: str,
        stage: str | None = None,
        selected_skills: list[str] | None = None,
        diagnostic: dict | None = None,
    ) -> None:
        """Idempotent terminal transition to failed.

        Never invents hypothesis/remediation/recovery. Preserves partial skills
        only when the caller supplies skills that were genuinely selected.
        """
        timestamp = self._now()
        existing = self._repository.get_incident(incident_id)
        if existing is None:
            return
        terminal = {
            "failed",
            "resolved",
            "rejected",
            "remediation_failed",
            "blocked_by_telemetry",
            "abandoned",
            "expired",
            "cleanup_failed",
            "timed_out",
        }
        if existing.status in terminal:
            return
        metadata: dict = {
            "reason": reason,
            "stage": stage,
        }
        if diagnostic:
            metadata["diagnostic"] = diagnostic
        update: dict = {
            "status": "failed",
            "updated_at": timestamp,
            "resolved": False,
            "recommended_action": None,
        }
        if selected_skills is not None:
            update["selected_skills"] = list(selected_skills)
        self._repository.save_incident(existing.model_copy(update=update))
        # Cancel any pending approval rows for this incident.
        for approval in self._repository.list_approvals(incident_id):
            if approval.status == "pending":
                self._repository.save_approval(
                    approval.model_copy(
                        update={"status": "cancelled", "updated_at": timestamp}
                    )
                )
        self._append_audit(
            incident_id=incident_id,
            event_type="incident_failed",
            message="Investigation could not be completed.",
            timestamp=timestamp,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    def record_start_result(
        self,
        *,
        incident_id: str,
        started: IncidentResponseStartResult,
    ) -> None:
        timestamp = self._now()
        existing = self._require_incident(incident_id)
        selected_skills = list(started.investigation.get("selected_skills") or [])
        self._repository.save_incident(
            existing.model_copy(
                update={
                    "status": started.status,
                    "updated_at": timestamp,
                    "recommended_action": started.recommended_action,
                    "selected_skills": selected_skills,
                    "resolved": False,
                }
            )
        )
        self._append_audit(
            incident_id=incident_id,
            event_type="investigation_completed",
            message="Investigation completed.",
            timestamp=timestamp,
            metadata={
                "selected_skills": selected_skills,
                "recommended_action": started.recommended_action,
            },
        )
        if started.status != "approval_required" or started.approval_request is None:
            return
        request = started.approval_request
        proposal_id = str(request["proposal_id"])
        self._append_audit(
            incident_id=incident_id,
            event_type="approval_requested",
            message="Remediation approval requested.",
            timestamp=timestamp,
            metadata={
                "proposal_id": proposal_id,
                "recommended_action": started.recommended_action,
            },
        )

    def record_resume_result(
        self,
        *,
        incident_id: str,
        proposal_id: str,
        resumed: IncidentResponseResumeResult,
    ) -> None:
        timestamp = self._now()
        approval_status = resumed.approval_status or (
            "approved" if resumed.status == "resolved" else "rejected"
        )
        existing_incident = self._require_incident(incident_id)
        resolved = resumed.status == "resolved"
        self._repository.save_incident(
            existing_incident.model_copy(
                update={
                    "status": resumed.status,
                    "updated_at": timestamp,
                    "resolved": resolved,
                }
            )
        )
        if approval_status == "approved":
            self._append_audit(
                incident_id=incident_id,
                event_type="approval_approved",
                message="Remediation approved.",
                timestamp=timestamp,
                metadata={"proposal_id": proposal_id},
            )
        else:
            self._append_audit(
                incident_id=incident_id,
                event_type="approval_rejected",
                message="Remediation rejected.",
                timestamp=timestamp,
                metadata={"proposal_id": proposal_id, "resolved": False},
            )
            return
        if resumed.execution_success:
            self._append_audit(
                incident_id=incident_id,
                event_type="remediation_executed",
                message="Remediation executed.",
                timestamp=timestamp,
                metadata={
                    "proposal_id": proposal_id,
                    "execution_success": True,
                },
            )
        self._append_audit(
            incident_id=incident_id,
            event_type="verification_completed",
            message="Recovery verification completed.",
            timestamp=timestamp,
            metadata={
                "resolved": resolved,
                "recovered_p95_latency_ms": resumed.recovered_p95_latency_ms,
                "recovered_error_rate_percent": resumed.recovered_error_rate_percent,
            },
        )

    def _require_incident(self, incident_id: str) -> IncidentRecord:
        record = self._repository.get_incident(incident_id)
        if record is None:
            raise ValueError(f"Unknown incident: {incident_id}")
        return record

    def _append_audit(
        self,
        *,
        incident_id: str,
        event_type: str,
        message: str,
        timestamp: datetime,
        metadata: dict,
    ) -> None:
        sequence = len(self._repository.list_audit_events(incident_id)) + 1
        self._repository.append_audit(
            AuditRecord(
                audit_id=f"{incident_id}-{sequence:02d}-{event_type}",
                incident_id=incident_id,
                event_type=event_type,
                message=message,
                timestamp=timestamp,
                metadata=metadata,
            )
        )
