from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    EvaluationRecord,
    IncidentRecord,
)
from backend.app.persistence.repository import OpsPilotRepository

CREATED = datetime(2026, 8, 31, 8, 30)
UPDATED = datetime(2026, 8, 31, 8, 45)
LATER = datetime(2026, 8, 31, 9, 0)
FORBIDDEN_FIELDS = (
    "chain_of_thought",
    "chain-of-thought",
    "prompt",
    "system_prompt",
    "user_prompt",
    "api_key",
    "GROQ_API_KEY",
    "known_root_cause",
    "expected_remediation",
)
VENDOR_TOKENS = (
    "sqlalchemy",
    "psycopg",
    "postgres",
    "postgresql",
    "redis",
    "sqlite",
    "asyncpg",
)
PERSISTENCE_DIR = Path(__file__).resolve().parents[1] / "backend" / "app" / "persistence"


def _repo() -> InMemoryOpsPilotRepository:
    return InMemoryOpsPilotRepository()


def _incident(
    *,
    incident_id: str = "inc-checkout-001",
    scenario_id: str = "checkout-db-pool-regression",
    status: str = "approval_required",
    updated_at: datetime = CREATED,
    recommended_action: str | None = "rollback_deployment",
    selected_skills: list[str] | None = None,
    resolved: bool = False,
) -> IncidentRecord:
    return IncidentRecord(
        incident_id=incident_id,
        scenario_id=scenario_id,
        affected_service="checkout-api",
        status=status,
        created_at=CREATED,
        updated_at=updated_at,
        recommended_action=recommended_action,
        selected_skills=list(
            selected_skills
            if selected_skills is not None
            else ["deployment-regression", "postgres-diagnostics"]
        ),
        resolved=resolved,
    )


def _approval(
    *,
    proposal_id: str = "prop-rollback-001",
    incident_id: str = "inc-checkout-001",
    status: str = "pending",
    updated_at: datetime = CREATED,
) -> ApprovalRecord:
    return ApprovalRecord(
        proposal_id=proposal_id,
        incident_id=incident_id,
        action="rollback_deployment",
        service="checkout-api",
        version="v1.18.3",
        risk_level="high_risk",
        status=status,
        created_at=CREATED,
        updated_at=updated_at,
    )


def _audit(
    *,
    audit_id: str,
    incident_id: str = "inc-checkout-001",
    message: str = "rollback proposed",
    timestamp: datetime = CREATED,
    metadata: dict | None = None,
) -> AuditRecord:
    return AuditRecord(
        audit_id=audit_id,
        incident_id=incident_id,
        event_type="remediation",
        message=message,
        timestamp=timestamp,
        metadata=dict(metadata or {"action": "propose_rollback"}),
    )


def _evaluation(
    *,
    evaluation_id: str,
    incident_id: str = "inc-checkout-001",
    resolution_success: bool = True,
) -> EvaluationRecord:
    return EvaluationRecord(
        evaluation_id=evaluation_id,
        incident_id=incident_id,
        resolution_success=resolution_success,
        root_cause_correct=True,
        recommended_action_correct=True,
        unsafe_action_attempted=False,
        investigation_steps=5,
        created_at=CREATED,
    )


def test_save_and_get_incident_record() -> None:
    repo = _repo()
    record = _incident()

    repo.save_incident(record)
    loaded = repo.get_incident(record.incident_id)

    assert loaded == record
    assert loaded is not None
    assert loaded.created_at == CREATED
    assert loaded.updated_at == CREATED


def test_saving_same_incident_id_updates_record() -> None:
    repo = _repo()
    repo.save_incident(_incident(status="approval_required", resolved=False))
    repo.save_incident(
        _incident(
            status="resolved",
            updated_at=UPDATED,
            resolved=True,
        )
    )

    loaded = repo.get_incident("inc-checkout-001")
    assert loaded is not None
    assert loaded.status == "resolved"
    assert loaded.resolved is True
    assert loaded.updated_at == UPDATED
    assert loaded.created_at == CREATED
    assert len(repo.list_incidents()) == 1


def test_list_incidents_preserves_insertion_order() -> None:
    repo = _repo()
    repo.save_incident(_incident(incident_id="inc-a"))
    repo.save_incident(
        _incident(
            incident_id="inc-b",
            scenario_id="auth-token-validation-regression",
        )
    )

    listed = repo.list_incidents()
    assert [item.incident_id for item in listed] == ["inc-a", "inc-b"]


def test_missing_incident_returns_none() -> None:
    assert _repo().get_incident("missing-incident") is None


def test_save_and_get_approval_record() -> None:
    repo = _repo()
    record = _approval()

    repo.save_approval(record)
    loaded = repo.get_approval(record.proposal_id)

    assert loaded == record
    assert loaded is not None
    assert loaded.status == "pending"


def test_approval_can_move_from_pending_to_approved() -> None:
    repo = _repo()
    repo.save_approval(_approval(status="pending"))
    repo.save_approval(_approval(status="approved", updated_at=UPDATED))

    loaded = repo.get_approval("prop-rollback-001")
    assert loaded is not None
    assert loaded.status == "approved"
    assert loaded.updated_at == UPDATED
    assert len(repo.list_approvals("inc-checkout-001")) == 1


def test_list_approvals_filters_by_incident_id() -> None:
    repo = _repo()
    repo.save_approval(_approval(proposal_id="prop-a", incident_id="inc-a"))
    repo.save_approval(_approval(proposal_id="prop-b", incident_id="inc-b"))
    repo.save_approval(_approval(proposal_id="prop-c", incident_id="inc-a"))

    listed = repo.list_approvals("inc-a")
    assert [item.proposal_id for item in listed] == ["prop-a", "prop-c"]
    assert repo.list_approvals("missing-incident") == []
    assert repo.get_approval("missing-proposal") is None


def test_append_audit_preserves_multiple_events_in_order() -> None:
    repo = _repo()
    repo.append_audit(_audit(audit_id="aud-1", message="proposed", timestamp=CREATED))
    repo.append_audit(_audit(audit_id="aud-2", message="approved", timestamp=UPDATED))
    repo.append_audit(_audit(audit_id="aud-3", message="executed", timestamp=LATER))

    events = repo.list_audit_events("inc-checkout-001")
    assert [item.audit_id for item in events] == ["aud-1", "aud-2", "aud-3"]
    assert [item.message for item in events] == ["proposed", "approved", "executed"]


def test_audit_events_are_isolated_by_incident() -> None:
    repo = _repo()
    repo.append_audit(_audit(audit_id="aud-a", incident_id="inc-a", message="a"))
    repo.append_audit(_audit(audit_id="aud-b", incident_id="inc-b", message="b"))

    assert [item.audit_id for item in repo.list_audit_events("inc-a")] == ["aud-a"]
    assert [item.audit_id for item in repo.list_audit_events("inc-b")] == ["aud-b"]
    assert repo.list_audit_events("inc-missing") == []


def test_save_and_list_evaluation_record() -> None:
    repo = _repo()
    record = _evaluation(evaluation_id="eval-1")

    repo.save_evaluation(record)
    listed = repo.list_evaluations("inc-checkout-001")

    assert listed == [record]


def test_multiple_evaluation_runs_may_exist_for_one_incident() -> None:
    repo = _repo()
    repo.save_evaluation(_evaluation(evaluation_id="eval-1", resolution_success=True))
    repo.save_evaluation(_evaluation(evaluation_id="eval-2", resolution_success=False))
    repo.save_evaluation(
        _evaluation(
            evaluation_id="eval-other",
            incident_id="inc-other",
            resolution_success=True,
        )
    )

    listed = repo.list_evaluations("inc-checkout-001")
    assert [item.evaluation_id for item in listed] == ["eval-1", "eval-2"]
    assert listed[0].resolution_success is True
    assert listed[1].resolution_success is False
    assert repo.list_evaluations("inc-missing") == []


def test_returned_records_do_not_mutate_repository_state() -> None:
    repo = _repo()
    original_skills = ["deployment-regression"]
    original_metadata = {"action": "propose_rollback"}
    incident = _incident(selected_skills=original_skills)
    audit = _audit(audit_id="aud-1", metadata=original_metadata)

    repo.save_incident(incident)
    repo.append_audit(audit)
    incident.selected_skills.append("mutated-after-save")
    original_skills.append("mutated-source-list")
    original_metadata["leaked"] = True

    loaded_incident = repo.get_incident(incident.incident_id)
    assert loaded_incident is not None
    loaded_incident.selected_skills.append("mutated-after-get")
    listed_incident = repo.list_incidents()[0]
    listed_incident.selected_skills.append("mutated-after-list")

    loaded_audit = repo.list_audit_events(audit.incident_id)[0]
    loaded_audit.metadata["mutated"] = True

    stored_incident = repo.get_incident(incident.incident_id)
    stored_audit = repo.list_audit_events(audit.incident_id)[0]
    assert stored_incident is not None
    assert stored_incident.selected_skills == ["deployment-regression"]
    assert stored_audit.metadata == {"action": "propose_rollback"}


def test_records_serialize_with_pydantic() -> None:
    incident = _incident()
    approval = _approval()
    audit = _audit(audit_id="aud-1")
    evaluation = _evaluation(evaluation_id="eval-1")

    for record in (incident, approval, audit, evaluation):
        payload = record.model_dump(mode="json")
        json.dumps(payload)
        assert isinstance(payload, dict)


def test_records_contain_no_chain_of_thought_fields() -> None:
    models = (IncidentRecord, ApprovalRecord, AuditRecord, EvaluationRecord)
    for model in models:
        field_names = set(model.model_fields)
        for token in FORBIDDEN_FIELDS:
            assert token not in field_names
        assert "chain_of_thought" not in field_names
        assert "chain-of-thought" not in field_names


VENDOR_FREE_FILES = ("models.py", "repository.py", "memory.py")


def test_repository_contains_no_database_vendor_implementation() -> None:
    assert isinstance(_repo(), OpsPilotRepository)
    for name in VENDOR_FREE_FILES:
        source = (PERSISTENCE_DIR / name).read_text(encoding="utf-8").lower()
        for token in VENDOR_TOKENS:
            assert token not in source, f"{token} found in {name}"
