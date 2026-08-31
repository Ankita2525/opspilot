from __future__ import annotations

import pytest

from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse
from simulator.environment import SimulatedEnvironment

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"


def _loaded_tools() -> tuple[SimulatedEnvironment, DiagnosticTools]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    return environment, DiagnosticTools(environment)


def test_query_metrics_returns_metric_response() -> None:
    _, tools = _loaded_tools()

    metrics = tools.query_metrics(SERVICE)

    assert isinstance(metrics, MetricResponse)


def test_checkout_api_latency_before_remediation() -> None:
    _, tools = _loaded_tools()

    metrics = tools.query_metrics(SERVICE)

    assert metrics.p95_latency_ms == 1940


def test_error_rate_before_remediation() -> None:
    _, tools = _loaded_tools()

    metrics = tools.query_metrics(SERVICE)

    assert metrics.error_rate_percent == 8.2


def test_get_service_logs_returns_log_responses() -> None:
    _, tools = _loaded_tools()

    logs = tools.get_service_logs(SERVICE)

    assert logs
    assert all(isinstance(event, LogResponse) for event in logs)


def test_logs_include_database_connection_pool_timeout() -> None:
    _, tools = _loaded_tools()

    combined = " ".join(event.message.lower() for event in tools.get_service_logs(SERVICE))

    assert "database connection pool timeout" in combined


def test_get_recent_deployments_returns_deployment_responses() -> None:
    _, tools = _loaded_tools()

    deployments = tools.get_recent_deployments(SERVICE)

    assert deployments
    assert all(isinstance(event, DeploymentResponse) for event in deployments)


def test_deployment_v1_18_3_appears() -> None:
    _, tools = _loaded_tools()

    versions = [event.version for event in tools.get_recent_deployments(SERVICE)]

    assert BAD_VERSION in versions


def test_unknown_service_raises_value_error() -> None:
    _, tools = _loaded_tools()

    with pytest.raises(ValueError, match="Unknown service"):
        tools.query_metrics("inventory-api")


def test_diagnostic_reads_do_not_mutate_incident() -> None:
    environment, tools = _loaded_tools()

    tools.query_metrics(SERVICE)
    tools.get_service_logs(SERVICE)
    tools.get_recent_deployments(SERVICE)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []

    metrics = tools.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2
