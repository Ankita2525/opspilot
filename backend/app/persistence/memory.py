from datetime import datetime

from pydantic import BaseModel

from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    EvaluationRecord,
    IncidentRecord,
    ProvenanceRecord,
)


def _copy[T: BaseModel](record: T) -> T:
    return record.model_copy(deep=True)


class InMemoryOpsPilotRepository:
    """Deterministic in-memory implementation of OpsPilotRepository."""

    def __init__(self) -> None:
        self._incidents: dict[str, IncidentRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._audit_events: dict[str, list[AuditRecord]] = {}
        self._evaluations: dict[str, EvaluationRecord] = {}
        self._provenance: dict[str, ProvenanceRecord] = {}

    def save_incident(self, record: IncidentRecord) -> None:
        self._incidents[record.incident_id] = _copy(record)

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        stored = self._incidents.get(incident_id)
        if stored is None:
            return None
        return _copy(stored)

    def list_incidents(self) -> list[IncidentRecord]:
        return [_copy(record) for record in self._incidents.values()]

    def save_approval(self, record: ApprovalRecord) -> None:
        self._approvals[record.proposal_id] = _copy(record)

    def get_approval(self, proposal_id: str) -> ApprovalRecord | None:
        stored = self._approvals.get(proposal_id)
        if stored is None:
            return None
        return _copy(stored)

    def list_approvals(self, incident_id: str) -> list[ApprovalRecord]:
        return [
            _copy(record)
            for record in self._approvals.values()
            if record.incident_id == incident_id
        ]

    def append_audit(self, record: AuditRecord) -> None:
        self._audit_events.setdefault(record.incident_id, []).append(_copy(record))

    def list_audit_events(self, incident_id: str) -> list[AuditRecord]:
        return [_copy(record) for record in self._audit_events.get(incident_id, ())]

    def save_evaluation(self, record: EvaluationRecord) -> None:
        self._evaluations[record.evaluation_id] = _copy(record)

    def list_evaluations(self, incident_id: str) -> list[EvaluationRecord]:
        return [
            _copy(record)
            for record in self._evaluations.values()
            if record.incident_id == incident_id
        ]

    def save_provenance(self, record: ProvenanceRecord) -> None:
        self._provenance[record.incident_id] = _copy(record)

    def get_provenance(self, incident_id: str) -> ProvenanceRecord | None:
        stored = self._provenance.get(incident_id)
        if stored is None:
            return None
        return _copy(stored)

    def claim_incident_for_approval(
        self,
        incident_id: str,
        *,
        claimed_at: datetime,
        processing_expires_at: datetime,
    ) -> bool:
        from datetime import timezone

        from backend.app.persistence.incident_status import (
            APPROVAL_PROCESSING_INCIDENT_STATUS,
        )

        record = self._incidents.get(incident_id)
        if record is None:
            return False
        if record.status != "approval_required" or record.resolved:
            return False

        claimed_at_utc = (
            claimed_at
            if claimed_at.tzinfo is not None
            else claimed_at.replace(tzinfo=timezone.utc)
        )

        current_expires_at = record.expires_at
        if current_expires_at is not None:
            current_expires_at_utc = (
                current_expires_at
                if current_expires_at.tzinfo is not None
                else current_expires_at.replace(tzinfo=timezone.utc)
            )
            if current_expires_at_utc <= claimed_at_utc:
                return False
        else:
            current_expires_at_utc = None

        processing_expires_at_utc = (
            processing_expires_at
            if processing_expires_at.tzinfo is not None
            else processing_expires_at.replace(tzinfo=timezone.utc)
        )

        new_expires_at = current_expires_at
        if (
            current_expires_at_utc is None
            or current_expires_at_utc < processing_expires_at_utc
        ):
            new_expires_at = processing_expires_at

        self._incidents[incident_id] = _copy(
            record.model_copy(
                update={
                    "status": APPROVAL_PROCESSING_INCIDENT_STATUS,
                    "updated_at": claimed_at,
                    "expires_at": new_expires_at,
                }
            )
        )
        return True

    def claim_incident_for_timeout(
        self,
        incident_id: str,
        *,
        claimed_at: datetime,
        processing_expires_at: datetime,
    ) -> bool:
        from datetime import timezone

        from backend.app.persistence.incident_status import (
            TERMINAL_INCIDENT_STATUSES,
            TIMEOUT_PROCESSING_INCIDENT_STATUS,
        )

        record = self._incidents.get(incident_id)
        if record is None:
            return False
        if record.status in TERMINAL_INCIDENT_STATUSES:
            return False
        if record.expires_at is None:
            return False

        claimed_at_utc = (
            claimed_at
            if claimed_at.tzinfo is not None
            else claimed_at.replace(tzinfo=timezone.utc)
        )
        expires_at_utc = (
            record.expires_at
            if record.expires_at.tzinfo is not None
            else record.expires_at.replace(tzinfo=timezone.utc)
        )

        # The durable deadline must still be expired at claim time.
        # This closes the stale-list race with approval claiming.
        if expires_at_utc > claimed_at_utc:
            return False

        self._incidents[incident_id] = _copy(
            record.model_copy(
                update={
                    "status": TIMEOUT_PROCESSING_INCIDENT_STATUS,
                    "updated_at": claimed_at,
                    "expires_at": processing_expires_at,
                }
            )
        )
        return True

    def finalize_incident_after_approval(
        self,
        incident_id: str,
        *,
        status: str,
        updated_at: datetime,
        resolved: bool,
    ) -> bool:
        from backend.app.persistence.incident_status import (
            APPROVAL_PROCESSING_INCIDENT_STATUS,
        )

        record = self._incidents.get(incident_id)
        if record is None:
            return False
        if record.status != APPROVAL_PROCESSING_INCIDENT_STATUS:
            return False

        self._incidents[incident_id] = _copy(
            record.model_copy(
                update={
                    "status": status,
                    "updated_at": updated_at,
                    "resolved": resolved,
                }
            )
        )
        return True

    def list_expired_incidents(self, as_of: datetime) -> list[tuple[str, str | None]]:
        from datetime import timezone

        from backend.app.persistence.incident_status import TERMINAL_INCIDENT_STATUSES

        expired: list[tuple[str, str | None]] = []
        as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        for record in self._incidents.values():
            if record.expires_at is None:
                continue
            exp = record.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= as_of_utc and record.status not in TERMINAL_INCIDENT_STATUSES:
                expired.append((record.incident_id, record.session_id))
        return expired
