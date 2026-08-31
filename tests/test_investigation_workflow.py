from __future__ import annotations

from backend.app.agent.hypotheses import HypothesisEngine, HypothesisResult
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import LogResponse, MetricResponse
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
EXPECTED_STEPS = [
    "inspect_metrics",
    "inspect_deployments",
    "inspect_logs",
    "generate_hypothesis",
    "complete_investigation",
]


def _loaded_workflow() -> tuple[
    SimulatedEnvironment, InvestigationWorkflow, FakeModelProvider
]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    tools = DiagnosticTools(environment)
    provider = FakeModelProvider()
    workflow = InvestigationWorkflow(
        tools=tools,
        hypothesis_engine=HypothesisEngine(provider),
    )
    return environment, workflow, provider


def test_workflow_completes_successfully() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["incident_id"] == INCIDENT_ID
    assert result["affected_service"] == SERVICE
    assert result["metrics"] is not None
    assert result["deployments"]
    assert result["logs"]
    assert result["hypothesis_result"] is not None
    assert result["status"] == "investigation_complete"


def test_final_status_is_investigation_complete() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["status"] == "investigation_complete"


def test_completed_steps_are_in_deterministic_order() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["completed_steps"] == EXPECTED_STEPS


def test_metrics_contain_p95_latency_1940() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert isinstance(result["metrics"], MetricResponse)
    assert result["metrics"].p95_latency_ms == 1940


def test_metrics_contain_error_rate_8_2() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["metrics"] is not None
    assert result["metrics"].error_rate_percent == 8.2


def test_deployments_contain_v1_18_3() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    versions = [event.version for event in result["deployments"]]
    assert BAD_VERSION in versions


def test_logs_contain_database_connection_pool_timeout() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert all(isinstance(event, LogResponse) for event in result["logs"])
    combined = " ".join(event.message.lower() for event in result["logs"])
    assert "database connection pool timeout" in combined


def test_hypothesis_result_is_populated() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert isinstance(result["hypothesis_result"], HypothesisResult)
    assert result["hypothesis_result"].hypotheses


def test_top_cause_is_db_connection_pool_regression() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)
    hypothesis = result["hypothesis_result"]
    assert hypothesis is not None
    highest = max(hypothesis.hypotheses, key=lambda item: item.confidence)
    assert highest.cause == "db_connection_pool_regression"


def test_hypothesis_confidence_equals_0_91() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)
    hypothesis = result["hypothesis_result"]
    assert hypothesis is not None
    highest = max(hypothesis.hypotheses, key=lambda item: item.confidence)
    assert highest.confidence == 0.91


def test_recommended_next_action_is_rollback_deployment() -> None:
    _, workflow, _ = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)
    hypothesis = result["hypothesis_result"]
    assert hypothesis is not None
    assert hypothesis.recommended_action == "rollback_deployment"


def test_hypothesis_receives_collected_evidence() -> None:
    _, workflow, provider = _loaded_workflow()

    workflow.run(INCIDENT_ID, SERVICE)
    prompt = provider.recorded_prompt()

    assert SERVICE in prompt
    assert "1940" in prompt
    assert "8.2" in prompt
    assert BAD_VERSION in prompt
    assert "database connection pool timeout" in prompt.lower()
    assert "Symptoms:" in prompt
    assert "Ranked evidence:" in prompt


def test_hypothesis_prompt_does_not_leak_simulator_ground_truth() -> None:
    _, workflow, provider = _loaded_workflow()

    workflow.run(INCIDENT_ID, SERVICE)
    prompt = provider.recorded_prompt()

    assert "known_root_cause" not in prompt
    assert "expected_remediation" not in prompt
    assert "db_connection_pool_regression" not in prompt
    assert "rollback_deployment" not in provider.user_prompts[0]


def test_workflow_does_not_resolve_incident() -> None:
    environment, workflow, _ = _loaded_workflow()

    workflow.run(INCIDENT_ID, SERVICE)

    assert environment.is_resolved is False


def test_environment_remains_unchanged_after_investigation() -> None:
    environment, workflow, _ = _loaded_workflow()
    before_metrics = environment.query_metrics(SERVICE)
    before_logs = environment.get_logs(SERVICE)
    before_deployments = environment.get_recent_deployments(SERVICE)

    workflow.run(INCIDENT_ID, SERVICE)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    after_metrics = environment.query_metrics(SERVICE)
    assert after_metrics == before_metrics
    assert environment.get_logs(SERVICE) == before_logs
    assert environment.get_recent_deployments(SERVICE) == before_deployments
    assert after_metrics.p95_latency_ms == 1940
    assert after_metrics.error_rate_percent == 8.2


def test_workflow_stores_incident_context_used_for_hypothesis() -> None:
    _, workflow, provider = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)
    context = result["incident_context"]

    assert context is not None
    assert context.incident_id == INCIDENT_ID
    assert context.affected_service == SERVICE
    assert "1940" in context.symptom_summary
    assert any("v1.18.3" in item.summary for item in context.evidence)
    prompt = provider.recorded_prompt()
    assert context.symptom_summary in prompt
    for item in context.evidence:
        assert item.summary in prompt
