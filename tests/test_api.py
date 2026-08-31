from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "GROQ_API_KEY",
    "chain-of-thought",
    "chain_of_thought",
)


def _client(provider: FakeModelProvider | None = None) -> TestClient:
    return TestClient(create_app(provider=provider or FakeModelProvider()))


def _start(client: TestClient, scenario_id: str = SCENARIO_ID):
    return client.post("/api/incidents/start", json={"scenario_id": scenario_id})


def _blob(*payloads: object) -> str:
    return json.dumps(payloads)


def _assert_no_leaks(*payloads: object) -> None:
    serialized = _blob(*payloads)
    for token in FORBIDDEN:
        assert token not in serialized


def test_health_returns_ok() -> None:
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "opspilot"}


def test_scenarios_returns_checkout_db_pool_regression() -> None:
    response = _client().get("/api/scenarios")

    assert response.status_code == 200
    payload = response.json()
    assert any(item["id"] == SCENARIO_ID for item in payload)


def test_scenario_response_does_not_expose_known_root_cause() -> None:
    payload = _client().get("/api/scenarios").json()

    assert "known_root_cause" not in json.dumps(payload)
    for item in payload:
        assert set(item) == {"id", "title", "affected_service"}


def test_scenario_response_does_not_expose_expected_remediation() -> None:
    payload = _client().get("/api/scenarios").json()

    assert "expected_remediation" not in json.dumps(payload)


def test_start_incident_returns_200() -> None:
    response = _start(_client())

    assert response.status_code == 200


def test_start_contains_investigation_complete_evidence() -> None:
    payload = _start(_client()).json()

    assert payload["investigation_status"] == "investigation_complete"
    assert "complete_investigation" in payload["investigation_steps"]
    assert payload["investigation_steps"] == [
        "inspect_metrics",
        "inspect_deployments",
        "inspect_logs",
        "generate_hypothesis",
        "complete_investigation",
    ]


def test_start_contains_incident_metrics() -> None:
    metrics = _start(_client()).json()["metrics"]

    assert metrics["p95_latency_ms"] == 1940
    assert metrics["error_rate_percent"] == 8.2
    assert metrics["service"] == SERVICE


def test_start_contains_structured_hypothesis() -> None:
    hypothesis = _start(_client()).json()["hypothesis_result"]

    assert hypothesis["hypotheses"]
    assert hypothesis["hypotheses"][0]["cause"]
    assert hypothesis["hypotheses"][0]["evidence"]
    assert hypothesis["reasoning_summary"]
    assert "chain-of-thought" not in hypothesis["reasoning_summary"]


def test_fake_rollback_recommendation_requires_approval() -> None:
    payload = _start(_client()).json()

    assert payload["status"] == "approval_required"
    assert payload["recommended_action"] == "rollback_deployment"
    assert payload["proposed_version"] == BAD_VERSION


def test_approval_request_contains_rollback_details() -> None:
    approval = _start(_client()).json()["approval_request"]

    assert approval is not None
    assert approval["action"] == "rollback_deployment"
    assert approval["service"] == SERVICE
    assert approval["version"] == BAD_VERSION
    assert approval["risk_level"] == "high_risk"


def test_incident_unresolved_before_approval() -> None:
    client = _client()
    started = _start(client).json()

    assert started["resolved"] is False
    assert started["status"] != "resolved"
    metrics = client.get(f"/api/incidents/{started['incident_id']}/metrics").json()
    assert metrics["p95_latency_ms"] == 1940
    assert metrics["error_rate_percent"] == 8.2


def test_approved_true_executes_rollback() -> None:
    client = _client()
    started = _start(client).json()

    response = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_success"] is True
    assert payload["status"] == "resolved"


def test_approved_response_reports_resolved() -> None:
    client = _client()
    started = _start(client).json()

    payload = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    ).json()

    assert payload["resolved"] is True
    assert payload["status"] == "resolved"


def test_approved_response_recovers_metrics() -> None:
    client = _client()
    started = _start(client).json()

    payload = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    ).json()

    assert payload["recovered_p95_latency_ms"] == 218
    assert payload["recovered_error_rate_percent"] == 0.3


def test_approved_false_produces_rejected_status() -> None:
    client = _client()
    started = _start(client).json()

    payload = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": False},
    ).json()

    assert payload["status"] == "rejected"
    assert payload["execution_success"] is False
    assert payload["resolved"] is False


def test_rejected_incident_remains_unresolved() -> None:
    client = _client()
    started = _start(client).json()
    client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": False},
    )
    metrics = client.get(f"/api/incidents/{started['incident_id']}/metrics").json()

    assert metrics["p95_latency_ms"] == 1940
    assert metrics["error_rate_percent"] == 8.2


def test_unknown_scenario_returns_404() -> None:
    response = _start(_client(), scenario_id="does-not-exist")

    assert response.status_code == 404


def test_unknown_incident_returns_404() -> None:
    client = _client()

    metrics = client.get("/api/incidents/unknown-incident/metrics")
    approval = client.post(
        "/api/incidents/unknown-incident/approval",
        json={"approved": True},
    )

    assert metrics.status_code == 404
    assert approval.status_code == 404


def test_get_incident_metrics_returns_current_metrics() -> None:
    client = _client()
    started = _start(client).json()
    incident_id = started["incident_id"]

    before = client.get(f"/api/incidents/{incident_id}/metrics")
    assert before.status_code == 200
    assert before.json()["p95_latency_ms"] == 1940
    assert before.json()["error_rate_percent"] == 8.2

    client.post(f"/api/incidents/{incident_id}/approval", json={"approved": True})
    after = client.get(f"/api/incidents/{incident_id}/metrics")
    assert after.json()["p95_latency_ms"] == 218
    assert after.json()["error_rate_percent"] == 0.3


def test_api_responses_do_not_leak_ground_truth_or_secrets() -> None:
    client = _client()
    health = client.get("/health").json()
    scenarios = client.get("/api/scenarios").json()
    started = _start(client).json()
    metrics = client.get(f"/api/incidents/{started['incident_id']}/metrics").json()
    approved = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    ).json()

    _assert_no_leaks(health, scenarios, started, metrics, approved)
    unknown = client.post(
        "/api/incidents/start",
        json={"scenario_id": "missing"},
    ).json()
    _assert_no_leaks(unknown)
