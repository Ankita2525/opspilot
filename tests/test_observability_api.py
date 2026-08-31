from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.app.api.app import create_app
from backend.app.api.public_records import sanitize_audit_metadata
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from tests.fakes import FakeModelProvider

CHECKOUT_ID = "checkout-db-pool-regression"
AUTH_ID = "auth-token-validation-regression"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "DATABASE_URL",
    "system_prompt",
    "user_prompt",
    "You are OpsPilot",
    "Traceback",
)


class _ExplodingProvider:
    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        raise AssertionError("evaluation endpoint must not use the app model provider")


def _client(repository=None) -> tuple[TestClient, InMemoryOpsPilotRepository]:
    repo = repository if repository is not None else InMemoryOpsPilotRepository()
    app = create_app(provider=FakeModelProvider(), repository=repo)
    return TestClient(app), repo


def _start(client: TestClient, scenario_id: str = CHECKOUT_ID):
    return client.post("/api/incidents/start", json={"scenario_id": scenario_id})


def _audit(client: TestClient, incident_id: str):
    return client.get(f"/api/incidents/{incident_id}/audit")


def _assert_no_leaks(payload: object) -> None:
    serialized = json.dumps(payload)
    for token in FORBIDDEN:
        assert token not in serialized


def test_audit_returns_events_for_started_incident() -> None:
    client, _ = _client()
    started = _start(client).json()
    response = _audit(client, started["incident_id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["incident_id"] == started["incident_id"]
    types = [item["event_type"] for item in payload["events"]]
    assert types == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
    ]
    assert all("event_type" in item and "message" in item for item in payload["events"])
    assert all("timestamp" in item and "metadata" in item for item in payload["events"])
    assert all("audit_id" not in item for item in payload["events"])


def test_audit_event_order_is_deterministic_and_approval_follows_investigation() -> None:
    client, _ = _client()
    first = _audit(client, _start(client).json()["incident_id"]).json()["events"]
    second = _audit(client, _start(client).json()["incident_id"]).json()["events"]

    assert [item["event_type"] for item in first] == [item["event_type"] for item in second]
    types = [item["event_type"] for item in first]
    assert types.index("investigation_completed") < types.index("approval_requested")


def test_approve_flow_audit_includes_remediation_and_verification() -> None:
    client, _ = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    client.post(f"/api/incidents/{incident_id}/approval", json={"approved": True})

    types = [item["event_type"] for item in _audit(client, incident_id).json()["events"]]
    assert types == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
        "approval_approved",
        "remediation_executed",
        "verification_completed",
    ]


def test_reject_flow_audit_has_no_remediation_executed() -> None:
    client, _ = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    client.post(f"/api/incidents/{incident_id}/approval", json={"approved": False})

    types = [item["event_type"] for item in _audit(client, incident_id).json()["events"]]
    assert types == [
        "incident_started",
        "investigation_completed",
        "approval_requested",
        "approval_rejected",
    ]
    assert "remediation_executed" not in types
    assert "verification_completed" not in types


def test_audit_for_one_incident_excludes_another() -> None:
    client, _ = _client()
    checkout = _start(client).json()["incident_id"]
    auth = _start(client, AUTH_ID).json()["incident_id"]

    checkout_audit = _audit(client, checkout).json()
    auth_audit = _audit(client, auth).json()
    assert checkout_audit["incident_id"] == checkout
    assert auth_audit["incident_id"] == auth
    checkout_blob = json.dumps(checkout_audit)
    assert auth not in checkout_blob
    assert checkout not in json.dumps(auth_audit)


def test_unknown_incident_audit_returns_404() -> None:
    client, _ = _client()

    response = _audit(client, "inc_doesnotexist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown incident: inc_doesnotexist"


def test_audit_response_contains_no_ground_truth_prompts_or_secrets() -> None:
    client, _ = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]
    client.post(f"/api/incidents/{incident_id}/approval", json={"approved": True})

    _assert_no_leaks(_audit(client, incident_id).json())


def test_sanitize_audit_metadata_drops_forbidden_fields() -> None:
    cleaned = sanitize_audit_metadata(
        {
            "proposal_id": "prop-1",
            "known_root_cause": "should-not-leak",
            "system_prompt": "You are OpsPilot",
            "nested": {"expected_remediation": "rollback", "ok": True},
        }
    )

    assert cleaned == {"proposal_id": "prop-1", "nested": {"ok": True}}


def test_incident_summary_returns_durable_public_fields() -> None:
    client, _ = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]

    response = client.get(f"/api/incidents/{incident_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["incident_id"] == incident_id
    assert payload["scenario_id"] == CHECKOUT_ID
    assert payload["affected_service"] == "checkout-api"
    assert payload["status"] == "approval_required"
    assert payload["resolved"] is False
    assert payload["selected_skills"]
    assert payload["recommended_action"] == "rollback_deployment"
    assert payload["approval"] is not None
    assert payload["approval"]["status"] == "pending"
    assert payload["approval"]["action"] == "rollback_deployment"
    _assert_no_leaks(payload)


def test_unknown_incident_summary_returns_404() -> None:
    client, _ = _client()

    response = client.get("/api/incidents/inc_missing")

    assert response.status_code == 404


def test_baseline_evaluation_returns_deterministic_fake_suite() -> None:
    client, _ = _client()

    response = client.get("/api/evaluations/baseline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluation_mode"] == "deterministic_baseline"
    assert payload["total_scenarios"] == 3
    assert payload["passed_scenarios"] == 3
    assert payload["failed_scenarios"] == 0
    assert payload["root_cause_accuracy"] == 1.0
    assert payload["recommended_action_accuracy"] == 1.0
    assert payload["approval_compliance_rate"] == 1.0
    assert payload["unsafe_action_rate"] == 0.0
    assert payload["resolution_rate"] == 1.0
    assert payload["health_recovery_rate"] == 1.0
    assert payload["remediation_execution_rate"] == 1.0
    assert payload["average_investigation_steps"] == 5.0
    assert len(payload["scenario_results"]) == 3
    by_scenario = {item["scenario_id"]: item for item in payload["scenario_results"]}
    assert set(by_scenario) == {
        CHECKOUT_ID,
        AUTH_ID,
        "payments-provider-timeout-regression",
    }
    assert by_scenario[CHECKOUT_ID]["scenario_id"] == CHECKOUT_ID
    assert by_scenario[AUTH_ID]["scenario_id"] == AUTH_ID
    assert (
        by_scenario["payments-provider-timeout-regression"]["scenario_id"]
        == "payments-provider-timeout-regression"
    )
    assert all("incident_id" not in item for item in payload["scenario_results"])
    assert all(item["investigation_steps"] == 5 for item in payload["scenario_results"])
    _assert_no_leaks(payload)
    serialized = json.dumps(payload)
    assert "incident_id" not in serialized
    assert "expected_root_cause" not in serialized
    assert "expected_remediation" not in serialized


def test_baseline_evaluation_does_not_use_app_provider_or_postgres() -> None:
    app = create_app(
        provider=_ExplodingProvider(),
        repository=InMemoryOpsPilotRepository(),
    )
    response = TestClient(app).get("/api/evaluations/baseline")

    assert response.status_code == 200
    assert response.json()["passed_scenarios"] == 3
    assert app.state.checkpointer is None
    assert isinstance(app.state.repository, InMemoryOpsPilotRepository)


def test_existing_stream_and_approval_routes_remain_unchanged() -> None:
    client, _ = _client()
    stream = client.post("/api/incidents/stream", json={"scenario_id": CHECKOUT_ID})
    assert stream.status_code == 200
    assert "text/event-stream" in stream.headers["content-type"]
    assert "event: approval_required" in stream.text
    assert "incident_failed" not in stream.text

    started = _start(client).json()
    approved = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    )
    assert approved.status_code == 200
    assert approved.json()["resolved"] is True
    assert approved.json()["recovered_p95_latency_ms"] == 218
