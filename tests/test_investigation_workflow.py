from __future__ import annotations

from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import LogResponse, MetricResponse
from simulator.environment import SimulatedEnvironment

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
EXPECTED_STEPS = [
    "inspect_metrics",
    "inspect_deployments",
    "inspect_logs",
    "complete_investigation",
]


def _loaded_workflow() -> tuple[SimulatedEnvironment, InvestigationWorkflow]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    tools = DiagnosticTools(environment)
    return environment, InvestigationWorkflow(tools)


def test_workflow_completes_successfully() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["incident_id"] == INCIDENT_ID
    assert result["affected_service"] == SERVICE
    assert result["metrics"] is not None
    assert result["deployments"]
    assert result["logs"]


def test_final_status_is_investigation_complete() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["status"] == "investigation_complete"


def test_completed_steps_are_in_deterministic_order() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["completed_steps"] == EXPECTED_STEPS


def test_metrics_contain_p95_latency_1940() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert isinstance(result["metrics"], MetricResponse)
    assert result["metrics"].p95_latency_ms == 1940


def test_metrics_contain_error_rate_8_2() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert result["metrics"] is not None
    assert result["metrics"].error_rate_percent == 8.2


def test_deployments_contain_v1_18_3() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    versions = [event.version for event in result["deployments"]]
    assert BAD_VERSION in versions


def test_logs_contain_database_connection_pool_timeout() -> None:
    _, workflow = _loaded_workflow()

    result = workflow.run(INCIDENT_ID, SERVICE)

    assert all(isinstance(event, LogResponse) for event in result["logs"])
    combined = " ".join(event.message.lower() for event in result["logs"])
    assert "database connection pool timeout" in combined


def test_workflow_does_not_resolve_incident() -> None:
    environment, workflow = _loaded_workflow()

    workflow.run(INCIDENT_ID, SERVICE)

    assert environment.is_resolved is False


def test_environment_remains_unchanged_after_investigation() -> None:
    environment, workflow = _loaded_workflow()
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
