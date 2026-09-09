from datetime import UTC, datetime, timedelta

from backend.app.persistence.incident_status import (
    APPROVAL_PROCESSING_INCIDENT_STATUS,
)
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from backend.app.persistence.postgres import CLAIM_INCIDENT_FOR_APPROVAL_SQL


NOW = datetime(2026, 9, 9, 19, 30, tzinfo=UTC)


def _incident(
    *,
    status: str = "approval_required",
    resolved: bool = False,
    expires_at: datetime | None = None,
) -> IncidentRecord:
    return IncidentRecord(
        incident_id="inc-atomic-approval",
        scenario_id="auth-token-validation-regression",
        affected_service="auth-service",
        status=status,
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
        recommended_action="rollback_deployment",
        selected_skills=["deployment-regression"],
        resolved=resolved,
        session_id="sess-owner",
        expires_at=expires_at or NOW + timedelta(minutes=2),
    )


def test_claim_succeeds_once_and_enters_processing_state() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(_incident())

    processing_deadline = NOW + timedelta(minutes=4)

    assert repo.claim_incident_for_approval(
        "inc-atomic-approval",
        claimed_at=NOW,
        processing_expires_at=processing_deadline,
    )

    stored = repo.get_incident("inc-atomic-approval")
    assert stored is not None
    assert stored.status == APPROVAL_PROCESSING_INCIDENT_STATUS
    assert stored.resolved is False
    assert stored.expires_at == processing_deadline

    assert not repo.claim_incident_for_approval(
        "inc-atomic-approval",
        claimed_at=NOW + timedelta(seconds=1),
        processing_expires_at=processing_deadline,
    )


def test_claim_fails_closed_after_expiry() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW - timedelta(seconds=1))
    )

    assert not repo.claim_incident_for_approval(
        "inc-atomic-approval",
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    stored = repo.get_incident("inc-atomic-approval")
    assert stored is not None
    assert stored.status == "approval_required"


def test_claim_fails_closed_for_terminal_incident() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(
            status="failed",
            resolved=False,
            expires_at=NOW + timedelta(minutes=2),
        )
    )

    assert not repo.claim_incident_for_approval(
        "inc-atomic-approval",
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    stored = repo.get_incident("inc-atomic-approval")
    assert stored is not None
    assert stored.status == "failed"


def test_claim_never_shortens_existing_expiry() -> None:
    repo = InMemoryOpsPilotRepository()
    existing_deadline = NOW + timedelta(minutes=10)
    repo.save_incident(_incident(expires_at=existing_deadline))

    assert repo.claim_incident_for_approval(
        "inc-atomic-approval",
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    stored = repo.get_incident("inc-atomic-approval")
    assert stored is not None
    assert stored.expires_at == existing_deadline


def test_postgres_claim_is_single_atomic_conditional_update() -> None:
    sql = " ".join(CLAIM_INCIDENT_FOR_APPROVAL_SQL.split())

    assert sql.startswith("UPDATE incidents SET")
    assert "status = %s" in sql
    assert "AND status = %s" in sql
    assert "AND resolved = FALSE" in sql
    assert "expires_at IS NULL OR expires_at > %s" in sql
