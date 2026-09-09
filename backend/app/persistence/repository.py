from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    EvaluationRecord,
    IncidentRecord,
    ProvenanceRecord,
)


@runtime_checkable
class OpsPilotRepository(Protocol):
    """Persistence contract for incident, approval, audit, and evaluation records."""

    def save_incident(self, record: IncidentRecord) -> None: ...

    def get_incident(self, incident_id: str) -> IncidentRecord | None: ...


    def claim_incident_for_approval(
        self,
        incident_id: str,
        *,
        claimed_at: datetime,
        processing_expires_at: datetime,
    ) -> bool: ...

    def claim_incident_for_timeout(
        self,
        incident_id: str,
        *,
        claimed_at: datetime,
        processing_expires_at: datetime,
    ) -> bool: ...

    def finalize_incident_after_approval(
        self,
        incident_id: str,
        *,
        status: str,
        updated_at: datetime,
        resolved: bool,
    ) -> bool: ...


    def list_incidents(self) -> list[IncidentRecord]: ...

    def save_approval(self, record: ApprovalRecord) -> None: ...

    def get_approval(self, proposal_id: str) -> ApprovalRecord | None: ...

    def list_approvals(self, incident_id: str) -> list[ApprovalRecord]: ...

    def append_audit(self, record: AuditRecord) -> None: ...

    def list_audit_events(self, incident_id: str) -> list[AuditRecord]: ...

    def save_evaluation(self, record: EvaluationRecord) -> None: ...

    def list_evaluations(self, incident_id: str) -> list[EvaluationRecord]: ...

    def save_provenance(self, record: ProvenanceRecord) -> None: ...

    def get_provenance(self, incident_id: str) -> ProvenanceRecord | None: ...
