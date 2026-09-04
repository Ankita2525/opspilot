from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.app.api.app import create_app
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.skills.selector import (
    AUTH_SKILL,
    DEPLOYMENT_SKILL,
    EXTERNAL_SKILL,
    POSTGRES_SKILL,
)
from backend.app.tools.diagnostics import DiagnosticTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

CHECKOUT_ID = "checkout-db-pool-regression"
AUTH_ID = "auth-token-validation-regression"
PAYMENTS_ID = "payments-provider-timeout-regression"
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
)


class _ExplodingProvider:
    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        raise RuntimeError(
            "stack trace GROQ_API_KEY=secret DATABASE_URL=postgres://x Traceback"
        )


def _client(provider=None, repository=None) -> tuple[TestClient, object]:
    repo = repository if repository is not None else InMemoryOpsPilotRepository()
    app = create_app(
        provider=provider or FakeModelProvider(),
        repository=repo,
        now=lambda: datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
    )
    return TestClient(app), repo


def _stream(client: TestClient, scenario_id: str = CHECKOUT_ID):
    return client.post("/api/incidents/stream", json={"scenario_id": scenario_id})


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        payload = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        assert event_name is not None
        assert payload is not None
        assert payload["event_type"] == event_name
        events.append(payload)
    return events


def _event_types(events: list[dict]) -> list[str]:
    return [item["event_type"] for item in events]


def _first(events: list[dict], event_type: str, *, step: str | None = None) -> dict:
    for item in events:
        if item["event_type"] != event_type:
            continue
        if step is not None and item.get("step") != step:
            continue
        return item
    raise AssertionError(f"missing event {event_type} step={step}")


def test_stream_returns_event_stream_content_type() -> None:
    client, _ = _client()
    response = _stream(client)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


def test_checkout_emits_incident_started_first() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client).text)

    assert events[0]["event_type"] == "incident_started"
    assert events[0]["sequence"] == 1
    assert events[0]["data"]["scenario_id"] == CHECKOUT_ID
    assert events[0]["data"]["affected_service"] == "checkout-api"


def test_sequence_numbers_are_monotonic() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client).text)
    sequences = [item["sequence"] for item in events]

    assert sequences == list(range(1, len(events) + 1))


def test_checkout_represents_real_investigation_steps() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client).text)
    environment = SimulatedEnvironment()
    environment.load_scenario(CHECKOUT_ID)
    tools = DiagnosticTools(environment)
    metrics = tools.query_metrics("checkout-api")
    deployments = tools.get_recent_deployments("checkout-api")
    logs = tools.get_service_logs("checkout-api")
    metrics_event = _first(events, "step_completed", step="inspect_metrics")
    deployments_event = _first(events, "step_completed", step="inspect_deployments")
    logs_event = _first(events, "step_completed", step="inspect_logs")

    assert metrics_event["data"]["p95_latency_ms"] == metrics.p95_latency_ms
    assert metrics_event["data"]["error_rate_percent"] == metrics.error_rate_percent
    assert deployments_event["data"]["deployment_count"] == len(deployments)
    assert deployments_event["data"]["versions"] == [event.version for event in deployments]
    assert logs_event["data"]["log_count"] == len(logs)
    assert any(item.get("step") == "inspect_metrics" for item in events)
    assert any(item.get("step") == "inspect_deployments" for item in events)
    assert any(item.get("step") == "inspect_logs" for item in events)


def test_context_and_skills_and_hypothesis_are_emitted() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client).text)
    context = _first(events, "context_built")
    skills = _first(events, "skills_selected")
    hypothesis = _first(events, "hypothesis_generated")

    assert context["data"]["evidence"]
    assert "summary" in context["data"]["evidence"][0]
    assert skills["data"]["selected_skills"] == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
    assert hypothesis["data"]["root_cause"] == "db_connection_pool_regression"
    assert hypothesis["data"]["recommended_action"] == "rollback_deployment"
    assert hypothesis["data"]["confidence"] == 0.91


def test_checkout_ends_at_approval_required() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client).text)
    terminal = events[-1]

    assert terminal["event_type"] == "approval_required"
    assert terminal["data"]["action"] == "rollback_deployment"
    assert terminal["data"]["service"] == "checkout-api"
    assert terminal["data"]["version"] == "v1.18.3"
    assert terminal["data"]["risk_level"] == "HIGH_RISK"
    assert "incident_completed" not in _event_types(events)


def test_no_supported_action_stream_reaches_terminal_completed_event() -> None:
    client, repo = _client(
        provider=FakeModelProvider(recommended_next_action="no_supported_action")
    )
    events = _parse_sse(_stream(client).text)
    terminal = events[-1]
    incident_id = terminal["incident_id"]

    assert terminal["event_type"] == "incident_completed"
    assert terminal["data"]["status"] == "investigation_complete"
    assert terminal["data"]["recommended_action"] == "no_supported_action"
    assert "approval_required" not in _event_types(events)
    stored = repo.get_incident(incident_id)
    assert stored is not None
    assert stored.status == "investigation_complete"
    assert stored.recommended_action == "no_supported_action"
    assert stored.resolved is False
    assert repo.list_approvals(incident_id) == []
    assert not any(
        item.event_type == "remediation_executed" for item in repo.list_audit_events(incident_id)
    )


def test_auth_selects_authentication_skills() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client, AUTH_ID).text)

    assert _first(events, "skills_selected")["data"]["selected_skills"] == [
        DEPLOYMENT_SKILL,
        AUTH_SKILL,
    ]
    assert events[-1]["event_type"] == "approval_required"


def test_payments_selects_external_api_skills() -> None:
    client, _ = _client()
    events = _parse_sse(_stream(client, PAYMENTS_ID).text)

    assert _first(events, "skills_selected")["data"]["selected_skills"] == [
        DEPLOYMENT_SKILL,
        EXTERNAL_SKILL,
    ]
    assert events[-1]["event_type"] == "approval_required"


def test_existing_start_endpoint_remains_unchanged() -> None:
    client, _ = _client()
    response = client.post("/api/incidents/start", json={"scenario_id": CHECKOUT_ID})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approval_required"
    assert payload["incident_id"] != CHECKOUT_ID
    assert payload["scenario_id"] == CHECKOUT_ID
    assert payload["metrics"]["p95_latency_ms"] == 1940
    assert payload["selected_skills"] == [DEPLOYMENT_SKILL, POSTGRES_SKILL]


def test_stream_persists_incident_and_pending_approval() -> None:
    client, repo = _client()
    events = _parse_sse(_stream(client).text)
    incident_id = events[0]["incident_id"]
    proposal_id = events[-1]["data"]["proposal_id"]

    incident = repo.get_incident(incident_id)
    approval = repo.get_approval(proposal_id)
    assert incident is not None
    assert incident.incident_id != CHECKOUT_ID
    assert incident.scenario_id == CHECKOUT_ID
    assert incident.status == "approval_required"
    assert incident.resolved is False
    assert approval is not None
    assert approval.status == "pending"
    assert approval.action == "rollback_deployment"


def test_approval_after_stream_resolves_checkout() -> None:
    client, repo = _client()
    events = _parse_sse(_stream(client).text)
    incident_id = events[-1]["incident_id"]
    proposal_id = events[-1]["data"]["proposal_id"]

    response = client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": True},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is True
    stored = repo.get_approval(proposal_id)
    incident = repo.get_incident(incident_id)
    assert stored is not None
    assert stored.status == "approved"
    assert incident is not None
    assert incident.resolved is True
    assert [item.proposal_id for item in repo.list_approvals(incident_id)] == [
        proposal_id
    ]


def test_rejection_after_stream_leaves_checkout_unresolved() -> None:
    client, repo = _client()
    events = _parse_sse(_stream(client).text)
    incident_id = events[-1]["incident_id"]
    proposal_id = events[-1]["data"]["proposal_id"]

    payload = client.post(
        f"/api/incidents/{incident_id}/approval",
        json={"approved": False},
    ).json()

    assert payload["resolved"] is False
    assert payload["execution_success"] is False
    stored = repo.get_approval(proposal_id)
    incident = repo.get_incident(incident_id)
    assert stored is not None
    assert stored.status == "rejected"
    assert incident is not None
    assert incident.resolved is False
    metrics = client.get(f"/api/incidents/{incident_id}/metrics").json()
    assert metrics["p95_latency_ms"] == 1940
    assert [item.proposal_id for item in repo.list_approvals(incident_id)] == [
        proposal_id
    ]


def test_stream_contains_no_secrets_prompts_or_ground_truth() -> None:
    client, _ = _client()
    body = _stream(client).text
    serialized = json.dumps(_parse_sse(body))

    for token in FORBIDDEN:
        assert token not in body
        assert token not in serialized


def test_invalid_scenario_fails_before_stream() -> None:
    client, _ = _client()
    response = _stream(client, "does-not-exist")

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_failure_event_does_not_expose_stack_trace() -> None:
    client, _ = _client(provider=_ExplodingProvider())
    response = _stream(client)
    body = response.text
    events = _parse_sse(body)

    assert response.status_code == 200
    assert events[-1]["event_type"] == "incident_failed"
    assert events[-1]["data"]["error"] == "diagnosis_unavailable"
    assert "Traceback" not in body
    assert "GROQ_API_KEY" not in body
    assert "DATABASE_URL" not in body
    assert "stack trace" not in body


def test_forced_hypothesis_failure_persists_failed_not_in_progress() -> None:
    client, repo = _client(provider=_ExplodingProvider())
    response = _stream(client)
    events = _parse_sse(response.text)
    incident_id = events[0]["incident_id"]

    assert events[-1]["event_type"] == "incident_failed"
    record = repo.get_incident(incident_id)
    assert record is not None
    assert record.status == "failed"
    assert record.status != "in_progress"
    audits = repo.list_audit_events(incident_id)
    assert any(item.event_type == "incident_failed" for item in audits)


def test_stream_fallback_success_reaches_approval_required() -> None:
    """20b json_validate_failed → 120b success → investigation continues."""
    import json as json_lib
    from unittest.mock import MagicMock

    import groq

    from backend.app.models.groq_provider import GroqModelProvider

    sample = {
        "hypotheses": [
            {
                "cause": "checkout connection pool regression after deployment",
                "confidence": 0.91,
                "evidence": [
                    {"source_type": "metric", "summary": "p95 latency rose sharply"},
                    {"source_type": "log", "summary": "connection pool exhausted"},
                    {"source_type": "deployment", "summary": "v1.18.3 deployed"},
                ],
            }
        ],
        "recommended_action": "rollback_deployment",
        "recommendation_summary": "Rollback checkout-api to the prior healthy revision.",
        "reasoning_summary": "Latency and pool exhaustion began after the checkout revision change.",
    }
    client_mock = MagicMock()
    response = MagicMock()
    response.status_code = 400
    response.headers = {}
    primary_err = groq.BadRequestError(
        message="bad request",
        response=response,
        body={
            "error": {
                "code": "json_validate_failed",
                "type": "invalid_request_error",
            }
        },
    )
    message = type("M", (), {"content": json_lib.dumps(sample), "refusal": None})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    completion = type("R", (), {"choices": [choice], "usage": None})()
    client_mock.chat.completions.create.side_effect = [primary_err, completion]
    provider = GroqModelProvider(
        api_key="k",
        client=client_mock,
        model="openai/gpt-oss-20b",
        fallback_model="openai/gpt-oss-120b",
        max_attempts=3,
        base_delay_seconds=0.01,
    )
    client, repo = _client(provider=provider)
    events = _parse_sse(_stream(client).text)
    assert client_mock.chat.completions.create.call_count == 2
    models = [c.kwargs["model"] for c in client_mock.chat.completions.create.call_args_list]
    assert models == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    assert events[-1]["event_type"] == "approval_required"
    incident_id = events[0]["incident_id"]
    record = repo.get_incident(incident_id)
    assert record is not None
    assert record.status == "approval_required"
    meta = provider.last_generation_meta
    assert meta is not None
    assert meta.fallback_used is True
    assert meta.final_model == "openai/gpt-oss-120b"


def test_stream_both_models_fail_persists_failed_safely() -> None:
    from unittest.mock import MagicMock

    import groq

    from backend.app.models.groq_provider import GroqModelProvider
    from backend.app.models.provider_errors import ModelCallError

    client_mock = MagicMock()
    response = MagicMock()
    response.status_code = 400
    response.headers = {}

    def _err():
        return groq.BadRequestError(
            message="bad request",
            response=response,
            body={
                "error": {
                    "code": "json_validate_failed",
                    "type": "invalid_request_error",
                }
            },
        )

    client_mock.chat.completions.create.side_effect = [_err(), _err()]
    provider = GroqModelProvider(
        api_key="k",
        client=client_mock,
        model="openai/gpt-oss-20b",
        fallback_model="openai/gpt-oss-120b",
        max_attempts=3,
        base_delay_seconds=0.01,
    )
    client, repo = _client(provider=provider)
    response_http = _stream(client)
    events = _parse_sse(response_http.text)
    body = response_http.text
    assert client_mock.chat.completions.create.call_count == 2
    assert events[-1]["event_type"] == "incident_failed"
    assert events[-1]["data"]["error"] == "diagnosis_unavailable"
    incident_id = events[0]["incident_id"]
    record = repo.get_incident(incident_id)
    assert record is not None
    assert record.status == "failed"
    assert "failed_generation" not in body
    assert "You are OpsPilot" not in body
    assert repo.get_provenance(incident_id) is None
    assert provider.last_generation_meta is not None
    assert provider.last_generation_meta.fallback_used is True


def test_independent_streams_do_not_share_event_state() -> None:
    client, _ = _client()
    checkout = _parse_sse(_stream(client, CHECKOUT_ID).text)
    auth = _parse_sse(_stream(client, AUTH_ID).text)

    assert checkout[0]["incident_id"] != auth[0]["incident_id"]
    assert checkout[0]["sequence"] == 1
    assert auth[0]["sequence"] == 1
    assert _first(checkout, "skills_selected")["data"]["selected_skills"] != _first(
        auth, "skills_selected"
    )["data"]["selected_skills"]


def test_workflow_without_events_is_unchanged() -> None:
    from backend.app.agent.hypotheses import HypothesisEngine
    from backend.app.agent.workflow import InvestigationWorkflow

    environment = SimulatedEnvironment()
    environment.load_scenario(CHECKOUT_ID)
    result = InvestigationWorkflow(
        tools=DiagnosticTools(environment),
        hypothesis_engine=HypothesisEngine(FakeModelProvider()),
    ).run("inc-no-events", "checkout-api")

    assert result["status"] == "investigation_complete"
    assert result["completed_steps"][-1] == "complete_investigation"
