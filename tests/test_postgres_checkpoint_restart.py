from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver

from backend.app.api.app import create_app
from backend.app.persistence.postgres import PostgresOpsPilotRepository
from backend.app.persistence.schema import initialize_schema
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"
TEST_DATABASE_ENV = "OPSPILOT_TEST_DATABASE_URL"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "system_prompt",
    "user_prompt",
    "DATABASE_URL",
)


def _postgres_url() -> str:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_ENV} is not set")
    production_url = os.environ.get("DATABASE_URL")
    if production_url and url == production_url:
        pytest.skip("refusing to run tests against DATABASE_URL")
    return url


def _cleanup_incident(database_url: str, incident_id: str) -> None:
    import psycopg

    with psycopg.connect(database_url) as connection:
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


def _thread_config(incident_id: str) -> dict:
    return {"configurable": {"thread_id": incident_id}}


def _assert_checkpoint_exists(saver: PostgresSaver, incident_id: str) -> None:
    checkpoint = saver.get_tuple(_thread_config(incident_id))
    assert checkpoint is not None
    listed = list(saver.list(_thread_config(incident_id)))
    assert listed


def _assert_no_leaks(*values: object) -> None:
    blob = json.dumps(values, default=str)
    for token in FORBIDDEN:
        assert token not in blob


def test_approve_after_postgres_checkpointer_restart_resolves_checkout() -> None:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    url = _postgres_url()
    initialize_schema(url)
    incident_id = SCENARIO_ID
    _cleanup_incident(url, incident_id)

    provider_one = FakeModelProvider()
    with PostgresSaver.from_conn_string(url) as saver_one:
        saver_one.setup()
        saver_one.delete_thread(incident_id)
        repo_one = PostgresOpsPilotRepository(url)
        app_one = create_app(
            provider=provider_one,
            repository=repo_one,
            checkpointer=saver_one,
        )
        started = TestClient(app_one).post(
            "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
        )
        assert started.status_code == 200
        payload = started.json()
        assert payload["status"] == "approval_required"
        incident_id = payload["incident_id"]
        proposal_id = payload["approval_request"]["proposal_id"]
        pending = repo_one.get_approval(proposal_id)
        assert pending is not None
        assert pending.status == "pending"
        assert pending.action == "rollback_deployment"
        assert pending.service == SERVICE
        assert pending.version == BAD_VERSION
        _assert_checkpoint_exists(saver_one, incident_id)
        _assert_no_leaks(payload, pending.model_dump(mode="json"))
        assert app_one.state.store.has(incident_id)
        assert saver_one.serde._allowed_msgpack_modules is None

    provider_two = FakeModelProvider()
    try:
        with PostgresSaver.from_conn_string(url) as saver_two:
            assert saver_two is not saver_one
            repo_two = PostgresOpsPilotRepository(url)
            app_two = create_app(
                provider=provider_two,
                repository=repo_two,
                checkpointer=saver_two,
            )
            assert app_two is not app_one
            assert not app_two.state.store.has(incident_id)
            assert app_two.state.checkpointer is saver_two
            _assert_checkpoint_exists(saver_two, incident_id)

            response = TestClient(app_two).post(
                f"/api/incidents/{incident_id}/approval",
                json={"approved": True},
            )
            assert response.status_code == 200
            resumed = response.json()
            assert resumed["resolved"] is True
            assert resumed["execution_success"] is True
            assert resumed["status"] == "resolved"
            assert resumed["recovered_p95_latency_ms"] == 218
            assert resumed["recovered_error_rate_percent"] == 0.3
            assert provider_two.user_prompts == []
            assert provider_two.system_prompts == []

            approvals = repo_two.list_approvals(incident_id)
            assert [item.proposal_id for item in approvals] == [proposal_id]
            stored = repo_two.get_approval(proposal_id)
            assert stored is not None
            assert stored.status == "approved"
            assert stored.action == "rollback_deployment"
            assert stored.service == SERVICE
            assert stored.version == BAD_VERSION
            assert stored.risk_level == "HIGH_RISK"
            incident = repo_two.get_incident(incident_id)
            assert incident is not None
            assert incident.resolved is True
            assert incident.status == "resolved"
            events = [item.event_type for item in repo_two.list_audit_events(incident_id)]
            assert "approval_approved" in events
            assert "remediation_executed" in events
            assert "verification_completed" in events
            _assert_no_leaks(
                resumed,
                stored.model_dump(mode="json"),
                incident.model_dump(mode="json"),
                [item.model_dump(mode="json") for item in repo_two.list_audit_events(incident_id)],
            )
            saver_two.delete_thread(incident_id)
    finally:
        _cleanup_incident(url, incident_id)


def test_reject_after_postgres_checkpointer_restart_does_not_remediate() -> None:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    url = _postgres_url()
    initialize_schema(url)
    incident_id = SCENARIO_ID
    _cleanup_incident(url, incident_id)

    with PostgresSaver.from_conn_string(url) as saver_one:
        saver_one.setup()
        saver_one.delete_thread(incident_id)
        app_one = create_app(
            provider=FakeModelProvider(),
            repository=PostgresOpsPilotRepository(url),
            checkpointer=saver_one,
        )
        started = TestClient(app_one).post(
            "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
        )
        assert started.status_code == 200
        incident_id = started.json()["incident_id"]
        proposal_id = started.json()["approval_request"]["proposal_id"]

    provider_two = FakeModelProvider()
    try:
        with PostgresSaver.from_conn_string(url) as saver_two:
            repo_two = PostgresOpsPilotRepository(url)
            app_two = create_app(
                provider=provider_two,
                repository=repo_two,
                checkpointer=saver_two,
            )
            assert not app_two.state.store.has(incident_id)
            response = TestClient(app_two).post(
                f"/api/incidents/{incident_id}/approval",
                json={"approved": False},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["resolved"] is False
            assert payload["execution_success"] is False
            assert payload["status"] == "rejected"
            assert provider_two.user_prompts == []
            stored = repo_two.get_approval(proposal_id)
            assert stored is not None
            assert stored.status == "rejected"
            incident = repo_two.get_incident(incident_id)
            assert incident is not None
            assert incident.resolved is False
            metrics = TestClient(app_two).get(
                f"/api/incidents/{incident_id}/metrics"
            ).json()
            assert metrics["p95_latency_ms"] == 1940
            assert metrics["error_rate_percent"] == 8.2
            events = [item.event_type for item in repo_two.list_audit_events(incident_id)]
            assert "approval_rejected" in events
            assert "remediation_executed" not in events
            saver_two.delete_thread(incident_id)
    finally:
        _cleanup_incident(url, incident_id)


def test_restart_approval_cannot_mutate_persisted_proposal() -> None:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    url = _postgres_url()
    initialize_schema(url)
    incident_id = SCENARIO_ID
    _cleanup_incident(url, incident_id)

    with PostgresSaver.from_conn_string(url) as saver_one:
        saver_one.setup()
        saver_one.delete_thread(incident_id)
        app_one = create_app(
            provider=FakeModelProvider(),
            repository=PostgresOpsPilotRepository(url),
            checkpointer=saver_one,
        )
        started = TestClient(app_one).post(
            "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
        )
        incident_id = started.json()["incident_id"]
        proposal_id = started.json()["approval_request"]["proposal_id"]

    try:
        with PostgresSaver.from_conn_string(url) as saver_two:
            repo_two = PostgresOpsPilotRepository(url)
            app_two = create_app(
                provider=FakeModelProvider(),
                repository=repo_two,
                checkpointer=saver_two,
            )
            client = TestClient(app_two)
            approved = client.post(
                f"/api/incidents/{incident_id}/approval",
                json={
                    "approved": True,
                    "action": "restart_service",
                    "service": "auth-service",
                    "version": "v9.9.9",
                    "risk_level": "low_risk",
                },
            )
            assert approved.status_code == 200
            assert approved.json()["resolved"] is True
            after = repo_two.get_approval(proposal_id)
            assert after is not None
            assert after.status == "approved"
            assert after.action == "rollback_deployment"
            assert after.service == SERVICE
            assert after.version == BAD_VERSION
            assert after.risk_level == "HIGH_RISK"
            saver_two.delete_thread(incident_id)
    finally:
        _cleanup_incident(url, incident_id)


def test_missing_and_non_resumable_incidents_fail_safely_with_postgres() -> None:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    url = _postgres_url()
    initialize_schema(url)
    missing_id = f"opspilot-missing-{uuid4().hex}"
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        app = create_app(
            provider=FakeModelProvider(),
            repository=PostgresOpsPilotRepository(url),
            checkpointer=saver,
        )
        client = TestClient(app)
        missing = client.post(
            f"/api/incidents/{missing_id}/approval",
            json={"approved": True},
        )
        assert missing.status_code == 404

        started = client.post(
            "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
        ).json()
        incident_id = started["incident_id"]
        approved = client.post(
            f"/api/incidents/{incident_id}/approval",
            json={"approved": True},
        )
        assert approved.status_code == 200
        app.state.store.remove(incident_id)
        again = client.post(
            f"/api/incidents/{incident_id}/approval",
            json={"approved": True},
        )
        assert again.status_code == 409
        saver.delete_thread(incident_id)
        _cleanup_incident(url, incident_id)
