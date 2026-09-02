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

    def list_expired_incidents(self, as_of: datetime) -> list[tuple[str, str | None]]:
        from datetime import timezone

        expired: list[tuple[str, str | None]] = []
        terminal = {
            "resolved",
            "rejected",
            "remediation_failed",
            "blocked_by_telemetry",
            "abandoned",
            "expired",
        }
        as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        for record in self._incidents.values():
            if record.expires_at is None:
                continue
            exp = record.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= as_of_utc and record.status not in terminal:
                expired.append((record.incident_id, record.session_id))
        return expired
