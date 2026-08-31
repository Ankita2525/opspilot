from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app.api.app import create_app
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "system_prompt",
    "user_prompt",
)


def _start(client: TestClient, scenario_id: str = SCENARIO_ID):
    return client.post("/api/incidents/start", json={"scenario_id": scenario_id})


def test_create_app_accepts_checkpointer_and_defaults_to_memory() -> None:
    app = create_app(provider=FakeModelProvider())

    assert app.state.checkpointer is None
    started = _start(TestClient(app))
    assert started.status_code == 200
    assert started.json()["status"] == "approval_required"


def test_api_uses_incident_id_as_remediation_thread_id() -> None:
    saver = InMemorySaver()
    app = create_app(provider=FakeModelProvider(), checkpointer=saver)
    client = TestClient(app)
    started = _start(client).json()
    incident_id = started["incident_id"]

    session = app.state.store.get(incident_id)
    assert session.remediation_thread_id == incident_id
    assert saver.get_tuple({"configurable": {"thread_id": incident_id}}) is not None


def test_approval_reconstructs_from_shared_checkpointer_without_rerunning_investigation() -> None:
    saver = InMemorySaver()
    repository = InMemoryOpsPilotRepository()
    provider_one = FakeModelProvider()
    app_one = create_app(
        provider=provider_one,
        repository=repository,
        checkpointer=saver,
    )
    started = _start(TestClient(app_one)).json()
    incident_id = started["incident_id"]
    proposal_id = started["approval_request"]["proposal_id"]
    assert started["status"] == "approval_required"
    assert app_one.state.store.has(incident_id)
    assert len(provider_one.user_prompts) == 1

    provider_two = FakeModelProvider()
    app_two = create_app(
        provider=provider_two,
        repository=repository,
        checkpointer=saver,
    )
    assert not app_two.state.store.has(incident_id)
    assert app_two.state.store is not app_one.state.store

    response = TestClient(app_two).post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resolved"] is True
    assert payload["execution_success"] is True
    assert payload["status"] == "resolved"
    assert provider_two.user_prompts == []
    assert provider_two.system_prompts == []
    approvals = repository.list_approvals(incident_id)
    assert len(approvals) == 1
    assert approvals[0].proposal_id == proposal_id
    assert approvals[0].status == "approved"
    assert approvals[0].action == "rollback_deployment"
    assert approvals[0].service == SERVICE
    assert approvals[0].version == BAD_VERSION
    incident = repository.get_incident(incident_id)
    assert incident is not None
    assert incident.resolved is True


def test_rejection_reconstructs_without_executing_remediation() -> None:
    saver = InMemorySaver()
    repository = InMemoryOpsPilotRepository()
    app_one = create_app(
        provider=FakeModelProvider(),
        repository=repository,
        checkpointer=saver,
    )
    incident_id = _start(TestClient(app_one)).json()["incident_id"]
    proposal_id = repository.list_approvals(incident_id)[0].proposal_id

    provider_two = FakeModelProvider()
    app_two = create_app(
        provider=provider_two,
        repository=repository,
        checkpointer=saver,
    )
    payload = TestClient(app_two).post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": False},
    ).json()

    assert payload["resolved"] is False
    assert payload["execution_success"] is False
    assert payload["status"] == "rejected"
    assert provider_two.user_prompts == []
    stored = repository.get_approval(proposal_id)
    assert stored is not None
    assert stored.status == "rejected"
    incident = repository.get_incident(incident_id)
    assert incident is not None
    assert incident.resolved is False
    metrics = TestClient(app_two).get(f"/api/incidents/{incident_id}/metrics").json()
    assert metrics["p95_latency_ms"] == 1940
    assert metrics["error_rate_percent"] == 8.2


def test_approval_request_cannot_override_persisted_proposal_fields() -> None:
    repository = InMemoryOpsPilotRepository()
    client = TestClient(
        create_app(provider=FakeModelProvider(), repository=repository)
    )
    started = _start(client).json()
    proposal_id = started["approval_request"]["proposal_id"]

    response = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={
            "approved": True,
            "action": "restart_service",
            "service": "auth-service",
            "version": "v9.9.9",
            "risk_level": "low_risk",
        },
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is True
    stored = repository.get_approval(proposal_id)
    assert stored is not None
    assert stored.action == "rollback_deployment"
    assert stored.service == SERVICE
    assert stored.version == BAD_VERSION
    assert stored.risk_level == "HIGH_RISK"


def test_missing_incident_approval_returns_404() -> None:
    response = TestClient(create_app(provider=FakeModelProvider())).post(
        "/api/incidents/missing-incident/approval",
        json={"approved": True},
    )

    assert response.status_code == 404


def test_non_resumable_incident_returns_409() -> None:
    repository = InMemoryOpsPilotRepository()
    timestamp = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    repository.save_incident(
        IncidentRecord(
            incident_id="already-resolved",
            scenario_id=SCENARIO_ID,
            affected_service=SERVICE,
            status="resolved",
            created_at=timestamp,
            updated_at=timestamp,
            recommended_action="rollback_deployment",
            selected_skills=[],
            resolved=True,
        )
    )
    response = TestClient(
        create_app(provider=FakeModelProvider(), repository=repository)
    ).post("/api/incidents/already-resolved/approval", json={"approved": True})

    assert response.status_code == 409


def test_reconstructed_approval_does_not_create_a_second_proposal() -> None:
    saver = InMemorySaver()
    repository = InMemoryOpsPilotRepository()
    app_one = create_app(
        provider=FakeModelProvider(),
        repository=repository,
        checkpointer=saver,
    )
    started = _start(TestClient(app_one)).json()
    incident_id = started["incident_id"]
    original = started["approval_request"]["proposal_id"]

    TestClient(
        create_app(
            provider=FakeModelProvider(),
            repository=repository,
            checkpointer=saver,
        )
    ).post(f"/api/incidents/{incident_id}/approval", json={"approved": True})

    approvals = repository.list_approvals(incident_id)
    assert [item.proposal_id for item in approvals] == [original]


def test_checkpoint_resume_records_contain_no_secrets_or_ground_truth() -> None:
    saver = InMemorySaver()
    repository = InMemoryOpsPilotRepository()
    app = create_app(
        provider=FakeModelProvider(),
        repository=repository,
        checkpointer=saver,
    )
    started = _start(TestClient(app)).json()
    incident_id = started["incident_id"]
    checkpoint = saver.get_tuple({"configurable": {"thread_id": incident_id}})
    assert checkpoint is not None
    blob = repr(checkpoint.checkpoint)
    for token in FORBIDDEN:
        assert token not in blob
    for record in (
        repository.get_incident(incident_id),
        *repository.list_approvals(incident_id),
        *repository.list_audit_events(incident_id),
    ):
        serialized = record.model_dump_json() if record is not None else ""
        for token in FORBIDDEN:
            assert token not in serialized
