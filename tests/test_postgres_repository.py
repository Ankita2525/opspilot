from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    EvaluationRecord,
    IncidentRecord,
)
from backend.app.persistence.postgres import (
    APPEND_AUDIT_SQL,
    GET_APPROVAL_SQL,
    GET_INCIDENT_SQL,
    LIST_APPROVALS_SQL,
    LIST_AUDIT_EVENTS_SQL,
    LIST_EVALUATIONS_SQL,
    LIST_INCIDENTS_SQL,
    PARAMETERIZED_SQL,
    SAVE_APPROVAL_SQL,
    SAVE_EVALUATION_SQL,
    SAVE_INCIDENT_SQL,
    PostgresOpsPilotRepository,
    approval_from_row,
    audit_from_row,
    evaluation_from_row,
    from_json,
    incident_from_row,
    to_json,
    to_utc,
)
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.persistence.schema import SCHEMA_STATEMENTS, initialize_schema

CREATED = datetime(2026, 8, 31, 8, 30)
UPDATED = datetime(2026, 8, 31, 8, 45)
LATER = datetime(2026, 8, 31, 9, 0)
TEST_DATABASE_ENV = "OPSPILOT_TEST_DATABASE_URL"
PERSISTENCE_DIR = Path(__file__).resolve().parents[1] / "backend" / "app" / "persistence"
POSTGRES_FILES = ("postgres.py", "schema.py")
REQUIRED_METHODS = (
    "save_incident",
    "get_incident",
    "list_incidents",
    "save_approval",
    "get_approval",
    "list_approvals",
    "append_audit",
    "list_audit_events",
    "save_evaluation",
    "list_evaluations",
)
FORBIDDEN_SOURCE_TOKENS = (
    "DATABASE_URL",
    "os.environ",
    "os.getenv",
    "password=",
    "postgres:postgres",
)
FORBIDDEN_RECORD_FIELDS = (
    "chain_of_thought",
    "prompt",
    "system_prompt",
    "user_prompt",
    "api_key",
    "GROQ_API_KEY",
    "known_root_cause",
    "expected_remediation",
)


def _incident(
    *,
    incident_id: str = "inc-checkout-001",
    status: str = "approval_required",
    created_at: datetime = CREATED,
    updated_at: datetime = CREATED,
    selected_skills: list[str] | None = None,
    resolved: bool = False,
) -> IncidentRecord:
    return IncidentRecord(
        incident_id=incident_id,
        scenario_id="checkout-db-pool-regression",
        affected_service="checkout-api",
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        recommended_action="rollback_deployment",
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
    created_at: datetime = CREATED,
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
        created_at=created_at,
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
    created_at: datetime = CREATED,
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
        created_at=created_at,
    )


def test_postgres_repository_implements_contract() -> None:
    repository = PostgresOpsPilotRepository("postgresql://example.invalid/opspilot")

    assert isinstance(repository, OpsPilotRepository)
    for method in REQUIRED_METHODS:
        assert callable(getattr(repository, method))


def test_constructor_does_not_require_a_live_database() -> None:
    repository = PostgresOpsPilotRepository("postgresql://example.invalid/opspilot")

    assert repository._database_url == "postgresql://example.invalid/opspilot"


def test_empty_database_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="database_url must not be empty"):
        PostgresOpsPilotRepository("")
    with pytest.raises(ValueError, match="database_url must not be empty"):
        PostgresOpsPilotRepository("   ")


def test_schema_creates_required_tables_and_indexes() -> None:
    sql = "\n".join(SCHEMA_STATEMENTS)

    assert "CREATE TABLE IF NOT EXISTS incidents" in sql
    assert "CREATE TABLE IF NOT EXISTS approvals" in sql
    assert "CREATE TABLE IF NOT EXISTS audit_events" in sql
    assert "CREATE TABLE IF NOT EXISTS evaluations" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_approvals_incident_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_audit_events_incident_id_timestamp" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_evaluations_incident_id_created_at" in sql
    assert "selected_skills JSONB NOT NULL" in sql
    assert "metadata JSONB NOT NULL" in sql


def test_dml_sql_is_parameterized() -> None:
    for statement in PARAMETERIZED_SQL:
        assert "%s" in statement or statement == LIST_INCIDENTS_SQL
        assert "{" not in statement
        assert "}" not in statement

    assert SAVE_INCIDENT_SQL.count("%s") == 11
    assert GET_INCIDENT_SQL.count("%s") == 1
    assert LIST_INCIDENTS_SQL.count("%s") == 0
    assert SAVE_APPROVAL_SQL.count("%s") == 9
    assert GET_APPROVAL_SQL.count("%s") == 1
    assert LIST_APPROVALS_SQL.count("%s") == 1
    assert APPEND_AUDIT_SQL.count("%s") == 6
    assert LIST_AUDIT_EVENTS_SQL.count("%s") == 1
    assert SAVE_EVALUATION_SQL.count("%s") == 8
    assert LIST_EVALUATIONS_SQL.count("%s") == 1


def test_repository_source_does_not_interpolate_sql_values() -> None:
    source = (PERSISTENCE_DIR / "postgres.py").read_text(encoding="utf-8")

    assert "execute(f" not in source
    assert ".format(" not in source
    assert "execute(sql, parameters)" in source


def test_postgres_modules_do_not_hardcode_credentials_or_env() -> None:
    for name in POSTGRES_FILES:
        source = (PERSISTENCE_DIR / name).read_text(encoding="utf-8")
        for token in FORBIDDEN_SOURCE_TOKENS:
            assert token not in source, f"{token} found in {name}"


def test_json_helpers_round_trip_skills_and_metadata() -> None:
    skills = ["deployment-regression", "postgres-diagnostics"]
    metadata = {"action": "propose_rollback", "attempts": 1}

    assert from_json(to_json(skills)) == skills
    assert from_json(to_json(metadata)) == metadata
    assert from_json(skills) == skills
    assert from_json(metadata) == metadata


def test_incident_row_mapping_uses_pydantic_record() -> None:
    record = incident_from_row(
        {
            "incident_id": "inc-checkout-001",
            "scenario_id": "checkout-db-pool-regression",
            "affected_service": "checkout-api",
            "status": "approval_required",
            "created_at": CREATED.replace(tzinfo=timezone.utc),
            "updated_at": UPDATED.replace(tzinfo=timezone.utc),
            "recommended_action": "rollback_deployment",
            "selected_skills": ["deployment-regression", "postgres-diagnostics"],
            "resolved": False,
        }
    )

    assert isinstance(record, IncidentRecord)
    assert record.selected_skills == [
        "deployment-regression",
        "postgres-diagnostics",
    ]
    assert record.created_at == CREATED
    assert record.updated_at == UPDATED
    assert record.recommended_action == "rollback_deployment"


def test_incident_row_mapping_accepts_json_string_skills() -> None:
    record = incident_from_row(
        {
            "incident_id": "inc-checkout-001",
            "scenario_id": "checkout-db-pool-regression",
            "affected_service": "checkout-api",
            "status": "approval_required",
            "created_at": CREATED,
            "updated_at": CREATED,
            "recommended_action": None,
            "selected_skills": to_json(["deployment-regression"]),
            "resolved": True,
        }
    )

    assert record.selected_skills == ["deployment-regression"]
    assert record.recommended_action is None
    assert record.resolved is True


def test_audit_row_mapping_copies_json_metadata() -> None:
    source_metadata = {"action": "propose_rollback"}
    record = audit_from_row(
        {
            "audit_id": "aud-1",
            "incident_id": "inc-checkout-001",
            "event_type": "remediation",
            "message": "proposed",
            "timestamp": CREATED,
            "metadata": source_metadata,
        }
    )

    record.metadata["mutated"] = True
    assert "mutated" not in source_metadata
    assert record.metadata["action"] == "propose_rollback"


def test_approval_and_evaluation_row_mapping() -> None:
    approval = approval_from_row(
        {
            "proposal_id": "prop-rollback-001",
            "incident_id": "inc-checkout-001",
            "action": "rollback_deployment",
            "service": "checkout-api",
            "version": None,
            "risk_level": "high_risk",
            "status": "pending",
            "created_at": CREATED,
            "updated_at": CREATED,
        }
    )
    evaluation = evaluation_from_row(
        {
            "evaluation_id": "eval-1",
            "incident_id": "inc-checkout-001",
            "resolution_success": True,
            "root_cause_correct": True,
            "recommended_action_correct": True,
            "unsafe_action_attempted": False,
            "investigation_steps": 5,
            "created_at": CREATED,
        }
    )

    assert approval.version is None
    assert evaluation.investigation_steps == 5
    assert evaluation.unsafe_action_attempted is False


def test_naive_datetimes_are_stored_as_utc() -> None:
    assert to_utc(CREATED) == CREATED.replace(tzinfo=timezone.utc)


def test_mapped_records_have_no_secret_or_ground_truth_fields() -> None:
    for model in (IncidentRecord, ApprovalRecord, AuditRecord, EvaluationRecord):
        field_names = set(model.model_fields)
        for token in FORBIDDEN_RECORD_FIELDS:
            assert token not in field_names


def test_initialize_schema_is_explicit() -> None:
    assert callable(initialize_schema)


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_ENV} is not set")
    production_url = os.environ.get("DATABASE_URL")
    if production_url and url == production_url:
        pytest.skip("refusing to run destructive tests against DATABASE_URL")
    return url


@pytest.fixture
def postgres_repo(postgres_url: str):
    initialize_schema(postgres_url)
    created = {
        "incidents": [],
        "approvals": [],
        "audit": [],
        "evaluations": [],
    }
    try:
        yield PostgresOpsPilotRepository(postgres_url), created, postgres_url
    finally:
        _cleanup(postgres_url, created)


def _cleanup(database_url: str, created: dict[str, list[str]]) -> None:
    import psycopg

    with psycopg.connect(database_url) as connection:
        for evaluation_id in created["evaluations"]:
            connection.execute(
                "DELETE FROM evaluations WHERE evaluation_id = %s",
                (evaluation_id,),
            )
        for audit_id in created["audit"]:
            connection.execute(
                "DELETE FROM audit_events WHERE audit_id = %s",
                (audit_id,),
            )
        for proposal_id in created["approvals"]:
            connection.execute(
                "DELETE FROM approvals WHERE proposal_id = %s",
                (proposal_id,),
            )
        for incident_id in created["incidents"]:
            connection.execute(
                "DELETE FROM incidents WHERE incident_id = %s",
                (incident_id,),
            )


def test_integration_save_get_and_upsert_incident(postgres_repo) -> None:
    repo, created, _ = postgres_repo
    incident_id = f"opspilot-test-inc-{uuid4().hex}"
    created["incidents"].append(incident_id)
    repo.save_incident(_incident(incident_id=incident_id, status="approval_required"))
    repo.save_incident(
        _incident(
            incident_id=incident_id,
            status="resolved",
            updated_at=UPDATED,
            resolved=True,
        )
    )

    loaded = repo.get_incident(incident_id)
    assert loaded is not None
    assert loaded.status == "resolved"
    assert loaded.resolved is True
    assert loaded.updated_at == UPDATED
    assert loaded.selected_skills == [
        "deployment-regression",
        "postgres-diagnostics",
    ]
    assert repo.get_incident(f"opspilot-test-missing-{uuid4().hex}") is None


def test_integration_approval_audit_and_evaluation_records(postgres_repo) -> None:
    repo, created, _ = postgres_repo
    incident_id = f"opspilot-test-inc-{uuid4().hex}"
    other_incident_id = f"opspilot-test-inc-{uuid4().hex}"
    proposal_id = f"opspilot-test-prop-{uuid4().hex}"
    other_proposal_id = f"opspilot-test-prop-{uuid4().hex}"
    audit_one = f"opspilot-test-aud-{uuid4().hex}"
    audit_two = f"opspilot-test-aud-{uuid4().hex}"
    audit_other = f"opspilot-test-aud-{uuid4().hex}"
    eval_one = f"opspilot-test-eval-{uuid4().hex}"
    eval_two = f"opspilot-test-eval-{uuid4().hex}"
    created["incidents"].extend([incident_id, other_incident_id])
    created["approvals"].extend([proposal_id, other_proposal_id])
    created["audit"].extend([audit_one, audit_two, audit_other])
    created["evaluations"].extend([eval_one, eval_two])

    repo.save_incident(_incident(incident_id=incident_id))
    repo.save_incident(_incident(incident_id=other_incident_id))
    repo.save_approval(_approval(proposal_id=proposal_id, incident_id=incident_id))
    repo.save_approval(
        _approval(
            proposal_id=proposal_id,
            incident_id=incident_id,
            status="approved",
            updated_at=UPDATED,
        )
    )
    repo.save_approval(
        _approval(proposal_id=other_proposal_id, incident_id=other_incident_id)
    )
    repo.append_audit(
        _audit(
            audit_id=audit_two,
            incident_id=incident_id,
            timestamp=LATER,
            message="executed",
        )
    )
    repo.append_audit(
        _audit(
            audit_id=audit_one,
            incident_id=incident_id,
            timestamp=CREATED,
            message="proposed",
        )
    )
    repo.append_audit(_audit(audit_id=audit_other, incident_id=other_incident_id))
    repo.save_evaluation(
        _evaluation(evaluation_id=eval_two, incident_id=incident_id, created_at=LATER)
    )
    repo.save_evaluation(
        _evaluation(evaluation_id=eval_one, incident_id=incident_id, created_at=CREATED)
    )

    loaded_approval = repo.get_approval(proposal_id)
    assert loaded_approval is not None
    assert loaded_approval.status == "approved"
    assert [item.proposal_id for item in repo.list_approvals(incident_id)] == [
        proposal_id
    ]
    assert [item.audit_id for item in repo.list_audit_events(incident_id)] == [
        audit_one,
        audit_two,
    ]
    assert [item.evaluation_id for item in repo.list_evaluations(incident_id)] == [
        eval_one,
        eval_two,
    ]


def test_integration_list_ordering_and_durability(postgres_repo) -> None:
    repo, created, database_url = postgres_repo
    first_id = f"opspilot-test-inc-{uuid4().hex}"
    second_id = f"opspilot-test-inc-{uuid4().hex}"
    created["incidents"].extend([first_id, second_id])
    repo.save_incident(
        _incident(incident_id=second_id, created_at=LATER, updated_at=LATER)
    )
    repo.save_incident(
        _incident(incident_id=first_id, created_at=CREATED, updated_at=CREATED)
    )

    listed = [
        item.incident_id
        for item in repo.list_incidents()
        if item.incident_id in {first_id, second_id}
    ]
    assert listed == [first_id, second_id]

    PostgresOpsPilotRepository(database_url).save_incident(
        _incident(
            incident_id=first_id,
            status="resolved",
            updated_at=UPDATED,
            resolved=True,
        )
    )
    reloaded = PostgresOpsPilotRepository(database_url).get_incident(first_id)
    assert reloaded is not None
    assert reloaded.status == "resolved"
    assert reloaded.resolved is True
    assert reloaded.updated_at == UPDATED
