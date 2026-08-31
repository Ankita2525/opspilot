from __future__ import annotations

import pytest

from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import (
    DeploymentResponse,
    LogResponse,
    MetricResponse,
    ServiceHealthResponse,
)
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
    tools.get_service_health(SERVICE)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []

    metrics = tools.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_get_service_health_returns_typed_response() -> None:
    _, tools = _loaded_tools()

    health = tools.get_service_health(SERVICE)

    assert isinstance(health, ServiceHealthResponse)
    assert health.service == SERVICE
    assert health.healthy is False
    assert health.p95_latency_ms == 1940
    assert health.error_rate_percent == 8.2
    assert health.max_p95_latency_ms == 400
    assert health.max_error_rate_percent == 1.0


def test_get_service_health_is_healthy_after_rollback() -> None:
    environment, tools = _loaded_tools()
    environment.rollback_deployment(SERVICE, BAD_VERSION)

    health = tools.get_service_health(SERVICE)

    assert health.healthy is True
    assert health.p95_latency_ms == 218
    assert health.error_rate_percent == 0.3


def test_get_service_health_unknown_service_raises_value_error() -> None:
    _, tools = _loaded_tools()

    with pytest.raises(ValueError, match="Unknown service"):
        tools.get_service_health("inventory-api")
