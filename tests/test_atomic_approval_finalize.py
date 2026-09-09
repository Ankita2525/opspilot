from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.app.persistence.incident_status import (
    APPROVAL_PROCESSING_INCIDENT_STATUS,
    TIMEOUT_PROCESSING_INCIDENT_STATUS,
)
from backend.app.persistence.lifecycle import IncidentLifecyclePersistence
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from backend.app.persistence.postgres import (
    FINALIZE_INCIDENT_AFTER_APPROVAL_SQL,
)


NOW = datetime(2026, 9, 9, 19, 50, tzinfo=UTC)
INCIDENT_ID = "inc-finalize-race"


def _incident(status: str) -> IncidentRecord:
    return IncidentRecord(
        incident_id=INCIDENT_ID,
        scenario_id="auth-token-validation-regression",
        affected_service="auth-service",
        status=status,
        created_at=NOW - timedelta(minutes=2),
        updated_at=NOW - timedelta(minutes=1),
        recommended_action="rollback_deployment",
        selected_skills=["deployment-regression"],
        resolved=False,
        session_id="sess-owner",
        expires_at=NOW + timedelta(minutes=4),
    )


def test_finalize_succeeds_from_approval_processing() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(_incident(APPROVAL_PROCESSING_INCIDENT_STATUS))

    assert repo.finalize_incident_after_approval(
        INCIDENT_ID,
        status="resolved",
        updated_at=NOW,
        resolved=True,
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == "resolved"
    assert stored.resolved is True


def test_finalize_fails_if_timeout_already_won() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(_incident(TIMEOUT_PROCESSING_INCIDENT_STATUS))

    assert not repo.finalize_incident_after_approval(
        INCIDENT_ID,
        status="resolved",
        updated_at=NOW,
        resolved=True,
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == TIMEOUT_PROCESSING_INCIDENT_STATUS
    assert stored.resolved is False


def test_finalize_fails_for_terminal_incident() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(_incident("failed"))

    assert not repo.finalize_incident_after_approval(
        INCIDENT_ID,
        status="resolved",
        updated_at=NOW,
        resolved=True,
    )

    assert repo.get_incident(INCIDENT_ID).status == "failed"


def test_postgres_finalize_is_atomic_compare_and_set() -> None:
    sql = " ".join(FINALIZE_INCIDENT_AFTER_APPROVAL_SQL.split())

    assert sql.startswith("UPDATE incidents SET")
    assert "status = %s" in sql
    assert "resolved = %s" in sql
    assert "WHERE incident_id = %s" in sql
    assert "AND status = %s" in sql


def test_lifecycle_result_cannot_resurrect_timeout_state() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(_incident(TIMEOUT_PROCESSING_INCIDENT_STATUS))

    persistence = IncidentLifecyclePersistence(repo, now=lambda: NOW)
    resumed = SimpleNamespace(
        status="resolved",
        approval_status="approved",
        execution_success=True,
        recovered_p95_latency_ms=55,
        recovered_error_rate_percent=0.0,
    )

    assert (
        persistence.record_resume_result(
            incident_id=INCIDENT_ID,
            proposal_id="prop-timeout-race",
            resumed=resumed,
        )
        is False
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == TIMEOUT_PROCESSING_INCIDENT_STATUS
    assert stored.resolved is False
    assert repo.list_audit_events(INCIDENT_ID) == []
