from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

# Initialize the API module first, matching the repository's existing
# cleanup tests and avoiding the package's pre-existing import-order cycle.
from backend.app.api.app import create_app  # noqa: F401
from backend.app.cleanup.worker import IncidentCleanupWorker
from backend.app.persistence.incident_status import (
    APPROVAL_PROCESSING_INCIDENT_STATUS,
    TIMEOUT_PROCESSING_INCIDENT_STATUS,
)
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from backend.app.persistence.postgres import CLAIM_INCIDENT_FOR_TIMEOUT_SQL
from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore


NOW = datetime(2026, 9, 9, 19, 45, tzinfo=UTC)
INCIDENT_ID = "inc-timeout-race"


def _incident(
    *,
    status: str = "approval_required",
    expires_at: datetime,
) -> IncidentRecord:
    return IncidentRecord(
        incident_id=INCIDENT_ID,
        scenario_id="auth-token-validation-regression",
        affected_service="auth-service",
        status=status,
        created_at=NOW - timedelta(minutes=5),
        updated_at=NOW - timedelta(minutes=1),
        recommended_action="rollback_deployment",
        selected_skills=["deployment-regression"],
        resolved=False,
        session_id="sess-owner",
        expires_at=expires_at,
    )


def test_timeout_claim_succeeds_for_still_expired_incident() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW - timedelta(seconds=1))
    )

    deadline = NOW + timedelta(minutes=4)

    assert repo.claim_incident_for_timeout(
        INCIDENT_ID,
        claimed_at=NOW,
        processing_expires_at=deadline,
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == TIMEOUT_PROCESSING_INCIDENT_STATUS
    assert stored.expires_at == deadline


def test_timeout_claim_fails_for_unexpired_incident() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW + timedelta(seconds=1))
    )

    assert not repo.claim_incident_for_timeout(
        INCIDENT_ID,
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    assert repo.get_incident(INCIDENT_ID).status == "approval_required"


def test_approval_wins_then_stale_timeout_claim_fails() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW + timedelta(seconds=1))
    )

    assert repo.claim_incident_for_approval(
        INCIDENT_ID,
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    # Simulates cleanup acting from a stale expired-candidate list.
    assert not repo.claim_incident_for_timeout(
        INCIDENT_ID,
        claimed_at=NOW + timedelta(milliseconds=1),
        processing_expires_at=NOW + timedelta(minutes=5),
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == APPROVAL_PROCESSING_INCIDENT_STATUS


def test_timeout_wins_then_late_approval_claim_fails() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW - timedelta(seconds=1))
    )

    assert repo.claim_incident_for_timeout(
        INCIDENT_ID,
        claimed_at=NOW,
        processing_expires_at=NOW + timedelta(minutes=4),
    )

    assert not repo.claim_incident_for_approval(
        INCIDENT_ID,
        claimed_at=NOW + timedelta(milliseconds=1),
        processing_expires_at=NOW + timedelta(minutes=5),
    )

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == TIMEOUT_PROCESSING_INCIDENT_STATUS


def test_cleanup_skips_stale_candidate_after_approval_claim() -> None:
    repo = InMemoryOpsPilotRepository()
    repo.save_incident(
        _incident(expires_at=NOW - timedelta(seconds=1))
    )

    session_store = MagicMock()
    session_store.list_live_sessions.return_value = []

    def stale_list(_as_of):
        # Cleanup discovered this row as expired, but approval wins before
        # cleanup attempts its durable timeout claim.
        claim_now = datetime.now(UTC)

        current = repo.get_incident(INCIDENT_ID)
        assert current is not None
        repo.save_incident(
            current.model_copy(
                update={"expires_at": claim_now + timedelta(seconds=1)}
            )
        )
        assert repo.claim_incident_for_approval(
            INCIDENT_ID,
            claimed_at=claim_now,
            processing_expires_at=claim_now + timedelta(minutes=4),
        )
        return [(INCIDENT_ID, "sess-owner")]

    live_orchestrator = MagicMock()
    worker = IncidentCleanupWorker(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=session_store,
        live_orchestrator=live_orchestrator,
        repository=repo,
        list_expired_incidents=stale_list,
    )

    assert worker._cleanup_sync() == 0
    session_store.get_optional.assert_not_called()
    live_orchestrator.cleanup.assert_not_called()

    stored = repo.get_incident(INCIDENT_ID)
    assert stored is not None
    assert stored.status == APPROVAL_PROCESSING_INCIDENT_STATUS


def test_postgres_timeout_claim_is_atomic_conditional_update() -> None:
    sql = " ".join(CLAIM_INCIDENT_FOR_TIMEOUT_SQL.split())

    assert sql.startswith("UPDATE incidents SET")
    assert "expires_at <= %s" in sql
    assert "status NOT IN" in sql
    assert "status = %s" in sql
