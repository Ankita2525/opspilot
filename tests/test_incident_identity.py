from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app.api.app import create_app
from backend.app.ids import new_incident_id
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from tests.fakes import FakeModelProvider

CHECKOUT_ID = "checkout-db-pool-regression"
AUTH_ID = "auth-token-validation-regression"
PAYMENTS_ID = "payments-provider-timeout-regression"
INCIDENT_ID_PATTERN = re.compile(r"^inc_[0-9a-f]{32}$")


def _client(repository=None, checkpointer=None):
    repo = repository if repository is not None else InMemoryOpsPilotRepository()
    app = create_app(
        provider=FakeModelProvider(),
        repository=repo,
        checkpointer=checkpointer,
    )
    return TestClient(app), repo, app


def _start(client: TestClient, scenario_id: str = CHECKOUT_ID):
    return client.post("/api/incidents/start", json={"scenario_id": scenario_id})


def _stream(client: TestClient, scenario_id: str = CHECKOUT_ID):
    return client.post("/api/incidents/stream", json={"scenario_id": scenario_id})


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        payload = None
        for line in block.split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        assert payload is not None
        events.append(payload)
    return events


def _assert_unique_incident_id(incident_id: str, scenario_id: str) -> None:
    assert INCIDENT_ID_PATTERN.match(incident_id)
    assert incident_id != scenario_id


def test_new_incident_id_is_compact_uuid_and_not_scenario_derived() -> None:
    first = new_incident_id()
    second = new_incident_id()

    assert first != second
    assert INCIDENT_ID_PATTERN.match(first)
    assert INCIDENT_ID_PATTERN.match(second)
    assert CHECKOUT_ID not in first
    assert AUTH_ID not in second


def test_two_start_requests_for_same_scenario_produce_different_incident_ids() -> None:
    client, repo, _ = _client()
    first = _start(client).json()
    second = _start(client).json()

    assert first["scenario_id"] == CHECKOUT_ID
    assert second["scenario_id"] == CHECKOUT_ID
    _assert_unique_incident_id(first["incident_id"], CHECKOUT_ID)
    _assert_unique_incident_id(second["incident_id"], CHECKOUT_ID)
    assert first["incident_id"] != second["incident_id"]
    assert repo.get_incident(first["incident_id"]) is not None
    assert repo.get_incident(second["incident_id"]) is not None


def test_two_checkout_streams_on_same_app_both_end_approval_required() -> None:
    client, repo, app = _client()
    first = _parse_sse(_stream(client).text)
    second = _parse_sse(_stream(client).text)

    assert first[-1]["event_type"] == "approval_required"
    assert second[-1]["event_type"] == "approval_required"
    assert "incident_failed" not in [item["event_type"] for item in second]
    first_id = first[0]["incident_id"]
    second_id = second[0]["incident_id"]
    assert first_id != second_id
    assert {item["incident_id"] for item in first} == {first_id}
    assert {item["incident_id"] for item in second} == {second_id}
    _assert_unique_incident_id(first_id, CHECKOUT_ID)
    _assert_unique_incident_id(second_id, CHECKOUT_ID)

    record_one = repo.get_incident(first_id)
    record_two = repo.get_incident(second_id)
    assert record_one is not None
    assert record_two is not None
    assert record_one.scenario_id == CHECKOUT_ID
    assert record_two.scenario_id == CHECKOUT_ID
    assert record_one.status == "approval_required"
    assert record_two.status == "approval_required"

    proposal_one = first[-1]["data"]["proposal_id"]
    proposal_two = second[-1]["data"]["proposal_id"]
    assert proposal_one != proposal_two
    approval_one = repo.get_approval(proposal_one)
    approval_two = repo.get_approval(proposal_two)
    assert approval_one is not None
    assert approval_two is not None
    assert approval_one.incident_id == first_id
    assert approval_two.incident_id == second_id
    assert approval_one.status == "pending"
    assert approval_two.status == "pending"
    assert app.state.store.has(first_id)
    assert app.state.store.has(second_id)


def test_approving_first_checkout_run_does_not_execute_second_proposal() -> None:
    client, repo, _ = _client()
    first = _parse_sse(_stream(client).text)
    second = _parse_sse(_stream(client).text)
    first_id = first[-1]["incident_id"]
    second_id = second[-1]["incident_id"]
    first_proposal = first[-1]["data"]["proposal_id"]
    second_proposal = second[-1]["data"]["proposal_id"]

    approved = client.post(
        f"/api/incidents/{first_id}/approval",
        json={"approved": True},
    ).json()

    assert approved["resolved"] is True
    assert repo.get_approval(first_proposal) is not None
    assert repo.get_approval(first_proposal).status == "approved"
    stored_second = repo.get_approval(second_proposal)
    assert stored_second is not None
    assert stored_second.status == "pending"
    second_record = repo.get_incident(second_id)
    assert second_record is not None
    assert second_record.resolved is False
    assert second_record.status == "approval_required"
    metrics = client.get(f"/api/incidents/{second_id}/metrics").json()
    assert metrics["p95_latency_ms"] == 1940


def test_rejecting_second_checkout_run_does_not_affect_first() -> None:
    client, repo, _ = _client()
    first = _parse_sse(_stream(client).text)
    second = _parse_sse(_stream(client).text)
    first_id = first[-1]["incident_id"]
    second_id = second[-1]["incident_id"]

    first_result = client.post(
        f"/api/incidents/{first_id}/approval",
        json={"approved": True},
    ).json()
    second_result = client.post(
        f"/api/incidents/{second_id}/approval",
        json={"approved": False},
    ).json()

    assert first_result["resolved"] is True
    assert second_result["resolved"] is False
    first_record = repo.get_incident(first_id)
    second_record = repo.get_incident(second_id)
    assert first_record is not None
    assert second_record is not None
    assert first_record.resolved is True
    assert first_record.status == "resolved"
    assert second_record.resolved is False
    assert second_record.status == "rejected"
    recovered = client.get(f"/api/incidents/{first_id}/metrics").json()
    degraded = client.get(f"/api/incidents/{second_id}/metrics").json()
    assert recovered["p95_latency_ms"] == 218
    assert degraded["p95_latency_ms"] == 1940


def test_session_store_holds_multiple_runs_of_same_scenario() -> None:
    client, _, app = _client()
    first = _start(client).json()["incident_id"]
    second = _start(client).json()["incident_id"]

    assert first != second
    assert app.state.store.has(first)
    assert app.state.store.has(second)
    assert app.state.store.get(first).scenario_id == CHECKOUT_ID
    assert app.state.store.get(second).scenario_id == CHECKOUT_ID
    assert app.state.store.get(first).remediation_thread_id == first
    assert app.state.store.get(second).remediation_thread_id == second


def test_repeated_auth_and_payments_streams_succeed() -> None:
    client, _, _ = _client()
    for scenario_id in (AUTH_ID, PAYMENTS_ID):
        first = _parse_sse(_stream(client, scenario_id).text)
        second = _parse_sse(_stream(client, scenario_id).text)
        assert first[-1]["event_type"] == "approval_required"
        assert second[-1]["event_type"] == "approval_required"
        assert first[0]["incident_id"] != second[0]["incident_id"]
        assert first[0]["data"]["scenario_id"] == scenario_id
        assert second[0]["data"]["scenario_id"] == scenario_id
        assert first[-1]["data"]["proposal_id"] != second[-1]["data"]["proposal_id"]


def test_langgraph_thread_id_matches_unique_incident_id() -> None:
    saver = InMemorySaver()
    client, _, app = _client(checkpointer=saver)
    first = _start(client).json()
    second = _start(client).json()
    first_id = first["incident_id"]
    second_id = second["incident_id"]

    assert first_id != second_id
    assert app.state.store.get(first_id).remediation_thread_id == first_id
    assert app.state.store.get(second_id).remediation_thread_id == second_id
    assert saver.get_tuple({"configurable": {"thread_id": first_id}}) is not None
    assert saver.get_tuple({"configurable": {"thread_id": second_id}}) is not None
    assert saver.get_tuple({"configurable": {"thread_id": CHECKOUT_ID}}) is None
