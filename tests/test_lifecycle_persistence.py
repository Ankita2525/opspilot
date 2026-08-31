from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import ApprovalRecord, IncidentRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.skills.selector import (
    AUTH_SKILL,
    DEPLOYMENT_SKILL,
    EXTERNAL_SKILL,
    POSTGRES_SKILL,
)
from tests.fakes import FakeModelProvider

CHECKOUT_ID = "checkout-db-pool-regression"
AUTH_ID = "auth-token-validation-regression"
PAYMENTS_ID = "payments-provider-timeout-regression"
CHECKOUT_SERVICE = "checkout-api"
AUTH_SERVICE = "auth-service"
PAYMENTS_SERVICE = "payments-service"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "system_prompt",
    "user_prompt",
)
TEST_DATABASE_ENV = "OPSPILOT_TEST_DATABASE_URL"


def _clock() -> Callable[[], datetime]:
    current = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return now


def _app(
    repository: InMemoryOpsPilotRepository | None = None,
    now=None,
):
    repo = repository if repository is not None else InMemoryOpsPilotRepository()
    app = create_app(
        provider=FakeModelProvider(),
        repository=repo,
        now=now or _clock(),
    )
    return app, repo


def _client(repository: InMemoryOpsPilotRepository | None = None, now=None):
    app, repo = _app(repository=repository, now=now)
    return TestClient(app), repo


def _start(client: TestClient, scenario_id: str = CHECKOUT_ID):
    return client.post("/api/incidents/start", json={"scenario_id": scenario_id})


def _serialized(*records: object) -> str:
    blobs = []
    for record in records:
        if hasattr(record, "model_dump"):
            blobs.append(record.model_dump(mode="json"))
        else:
            blobs.append(record)
    return json.dumps(blobs)


def test_starting_checkout_creates_incident_record() -> None:
    client, repo = _client()
    payload = _start(client).json()
    incident_id = payload["incident_id"]

    record = repo.get_incident(incident_id)
    assert isinstance(record, IncidentRecord)
    assert record.incident_id == incident_id
    assert record.incident_id != CHECKOUT_ID
    assert record.scenario_id == CHECKOUT_ID
    assert payload["scenario_id"] == CHECKOUT_ID


def test_checkout_incident_record_has_public_lifecycle_fields() -> None:
    client, repo = _client()
    payload = _start(client).json()
    record = repo.get_incident(payload["incident_id"])

    assert record is not None
    assert record.scenario_id == CHECKOUT_ID
    assert record.affected_service == CHECKOUT_SERVICE
    assert record.selected_skills == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
    assert record.recommended_action == "rollback_deployment"
    assert record.resolved is False
    assert record.status == "approval_required"
    assert payload["selected_skills"] == record.selected_skills
    assert payload["resolved"] is False


def test_auth_scenario_persists_selected_skills() -> None:
    client, repo = _client()
    payload = _start(client, AUTH_ID).json()

    record = repo.get_incident(payload["incident_id"])
    assert record is not None
    assert record.scenario_id == AUTH_ID
    assert record.affected_service == AUTH_SERVICE
    assert record.selected_skills == [DEPLOYMENT_SKILL, AUTH_SKILL]


def test_payments_scenario_persists_selected_skills() -> None:
    client, repo = _client()
    payload = _start(client, PAYMENTS_ID).json()

    record = repo.get_incident(payload["incident_id"])
    assert record is not None
    assert record.scenario_id == PAYMENTS_ID
    assert record.affected_service == PAYMENTS_SERVICE
    assert record.selected_skills == [DEPLOYMENT_SKILL, EXTERNAL_SKILL]


def test_approval_required_flow_persists_pending_approval() -> None:
    client, repo = _client()
    payload = _start(client).json()
    proposal_id = payload["approval_request"]["proposal_id"]
    incident_id = payload["incident_id"]

    approval = repo.get_approval(proposal_id)
    assert isinstance(approval, ApprovalRecord)
    assert approval.incident_id == incident_id
    assert approval.incident_id != CHECKOUT_ID
    assert approval.status == "pending"
    assert approval.action == "rollback_deployment"
    assert approval.service == CHECKOUT_SERVICE
    assert approval.version == "v1.18.3"


def test_approving_remediation_updates_approval_to_approved() -> None:
    client, repo = _client()
    started = _start(client).json()
    proposal_id = started["approval_request"]["proposal_id"]

    response = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    )

    assert response.status_code == 200
    approval = repo.get_approval(proposal_id)
    assert approval is not None
    assert approval.status == "approved"
    assert approval.created_at < approval.updated_at


def test_rejecting_remediation_updates_approval_to_rejected() -> None:
    client, repo = _client()
    started = _start(client).json()
    proposal_id = started["approval_request"]["proposal_id"]

    client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": False},
    )

    approval = repo.get_approval(proposal_id)
    assert approval is not None
    assert approval.status == "rejected"


def test_approved_remediation_marks_incident_resolved() -> None:
    client, repo = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    before = repo.get_incident(incident_id)
    assert before is not None
    assert before.resolved is False
    created_at = before.created_at

    client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": True},
    )

    record = repo.get_incident(incident_id)
    assert record is not None
    assert record.resolved is True
    assert record.status == "resolved"
    assert record.created_at == created_at
    assert record.updated_at > created_at


def test_rejected_remediation_keeps_incident_unresolved() -> None:
    client, repo = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]

    payload = client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": False},
    ).json()

    record = repo.get_incident(incident_id)
    assert record is not None
    assert record.resolved is False
    assert record.status == "rejected"
    assert payload["resolved"] is False


def test_lifecycle_audit_events_are_deterministic() -> None:
    client, repo = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]

    after_start = [event.event_type for event in repo.list_audit_events(incident_id)]
    assert after_start == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
    ]

    client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": True},
    )
    after_approve = [event.event_type for event in repo.list_audit_events(incident_id)]
    assert after_approve == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
        "approval_approved",
        "remediation_executed",
        "verification_completed",
    ]

    reject_client, reject_repo = _client()
    rejected = _start(reject_client).json()
    reject_id = rejected["incident_id"]
    reject_client.post(
        f"/api/incidents/{reject_id}/approval",
        json={"approved": False},
    )
    assert [event.event_type for event in reject_repo.list_audit_events(reject_id)] == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
        "approval_rejected",
    ]


def test_persisted_records_do_not_contain_simulator_ground_truth() -> None:
    client, repo = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": True},
    )

    incident = repo.get_incident(incident_id)
    approval = repo.get_approval(started["approval_request"]["proposal_id"])
    audits = repo.list_audit_events(incident_id)
    blob = _serialized(incident, approval, *audits)
    for token in FORBIDDEN:
        assert token not in blob
    assert "db_connection_pool_regression" not in blob


def test_persisted_records_contain_no_prompts_or_chain_of_thought() -> None:
    client, repo = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    incident = repo.get_incident(incident_id)
    assert incident is not None
    field_names = set(IncidentRecord.model_fields)
    assert "prompt" not in field_names
    assert "chain_of_thought" not in field_names
    for event in repo.list_audit_events(incident_id):
        assert "prompt" not in event.metadata
        assert "chain_of_thought" not in event.metadata


def test_repository_injection_works_through_create_app() -> None:
    repo = InMemoryOpsPilotRepository()
    app = create_app(provider=FakeModelProvider(), repository=repo)

    assert app.state.repository is repo
    client = TestClient(app)
    started = _start(client).json()
    assert repo.get_incident(started["incident_id"]) is not None


def test_default_create_app_uses_in_memory_repository() -> None:
    app = create_app(provider=FakeModelProvider())

    assert isinstance(app.state.repository, InMemoryOpsPilotRepository)
    assert isinstance(app.state.repository, OpsPilotRepository)
    response = TestClient(app).post(
        "/api/incidents/start",
        json={"scenario_id": CHECKOUT_ID},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approval_required"
    assert app.state.repository.get_incident(response.json()["incident_id"]) is not None


def test_api_start_contract_remains_compatible() -> None:
    payload = _start(_client()[0]).json()

    assert set(payload) >= {
        "incident_id",
        "scenario_id",
        "affected_service",
        "status",
        "investigation_status",
        "investigation_steps",
        "metrics",
        "hypothesis_result",
        "recommended_action",
        "proposed_version",
        "approval_request",
        "resolved",
        "selected_skills",
    }
    assert payload["investigation_status"] == "investigation_complete"
    assert payload["status"] == "approval_required"
    assert payload["incident_id"] != payload["scenario_id"]
    assert payload["scenario_id"] == CHECKOUT_ID


def test_timestamps_are_timezone_aware_utc() -> None:
    client, repo = _client()
    started = _start(client).json()
    record = repo.get_incident(started["incident_id"])
    assert record is not None
    assert record.created_at.tzinfo is not None
    assert record.created_at.utcoffset() == timezone.utc.utcoffset(record.created_at)
    assert record.updated_at >= record.created_at


def test_optional_postgres_start_is_durable_across_repository_instances() -> None:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        import pytest

        pytest.skip(f"{TEST_DATABASE_ENV} is not set")
    production_url = os.environ.get("DATABASE_URL")
    if production_url and url == production_url:
        import pytest

        pytest.skip("refusing to run destructive tests against DATABASE_URL")

    import psycopg

    from backend.app.persistence.postgres import PostgresOpsPilotRepository
    from backend.app.persistence.schema import initialize_schema

    initialize_schema(url)
    first = PostgresOpsPilotRepository(url)
    app = create_app(provider=FakeModelProvider(), repository=first)
    client = TestClient(app)
    incident_id = CHECKOUT_ID
    try:
        response = _start(client)
        assert response.status_code == 200
        incident_id = response.json()["incident_id"]
        second = PostgresOpsPilotRepository(url)
        loaded = second.get_incident(incident_id)
        assert loaded is not None
        assert loaded.incident_id != CHECKOUT_ID
        assert loaded.scenario_id == CHECKOUT_ID
        assert loaded.affected_service == CHECKOUT_SERVICE
        assert loaded.selected_skills == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
        assert loaded.status == "approval_required"
        assert loaded.resolved is False
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(
                "DELETE FROM audit_events WHERE incident_id = %s",
                (incident_id,),
            )
            connection.execute(
                "DELETE FROM approvals WHERE incident_id = %s",
                (incident_id,),
            )
            connection.execute(
                "DELETE FROM incidents WHERE incident_id = %s",
                (incident_id,),
            )
