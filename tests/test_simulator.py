from __future__ import annotations

import pytest

from simulator.environment import SimulatedEnvironment
from simulator.models import Remediation, RootCause

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"


def _loaded_env() -> SimulatedEnvironment:
    env = SimulatedEnvironment()
    env.load_scenario(SCENARIO_ID)
    return env


def test_scenario_loads_successfully() -> None:
    env = SimulatedEnvironment()
    scenario = env.load_scenario(SCENARIO_ID)

    assert scenario.id == SCENARIO_ID
    assert scenario.title == "Checkout API latency after deployment"
    assert scenario.affected_service == SERVICE
    assert scenario.known_root_cause == RootCause.DB_CONNECTION_POOL_REGRESSION
    assert scenario.expected_remediation == Remediation.ROLLBACK_DEPLOYMENT
    assert env.is_resolved is False


def test_incident_metrics_before_remediation() -> None:
    env = _loaded_env()

    metrics = env.query_metrics(SERVICE)

    assert metrics.service == SERVICE
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_logs_contain_database_timeout_evidence() -> None:
    env = _loaded_env()

    messages = [event.message.lower() for event in env.get_logs(SERVICE)]
    combined = " ".join(messages)

    assert "database connection pool timeout" in combined
    assert any("request timeout" in message for message in messages)
    assert any("checkout failed" in message for message in messages)


def test_deployment_v1_18_3_exists() -> None:
    env = _loaded_env()

    deployments = env.get_recent_deployments(SERVICE)

    assert any(event.version == BAD_VERSION for event in deployments)
    assert all(event.service == SERVICE for event in deployments)


def test_wrong_rollback_does_not_resolve_incident() -> None:
    env = _loaded_env()

    with pytest.raises(ValueError):
        env.rollback_deployment("payments-api", BAD_VERSION)
    assert env.is_resolved is False
    assert env.get_audit_events() == []

    with pytest.raises(ValueError):
        env.rollback_deployment(SERVICE, "v1.18.2")
    assert env.is_resolved is False
    assert env.get_audit_events() == []

    metrics = env.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_correct_rollback_resolves_incident() -> None:
    env = _loaded_env()

    env.rollback_deployment(SERVICE, BAD_VERSION)

    assert env.is_resolved is True


def test_recovered_metrics_after_correct_rollback() -> None:
    env = _loaded_env()
    env.rollback_deployment(SERVICE, BAD_VERSION)

    metrics = env.query_metrics(SERVICE)

    assert metrics.p95_latency_ms == 218
    assert metrics.error_rate_percent == 0.3


def test_correct_rollback_creates_audit_event() -> None:
    env = _loaded_env()
    env.rollback_deployment(SERVICE, BAD_VERSION)

    events = env.get_audit_events()

    assert len(events) == 1
    assert events[0].action == "rollback_deployment"
    assert SERVICE in events[0].details
    assert BAD_VERSION in events[0].details


def test_query_unknown_service_raises_value_error() -> None:
    env = _loaded_env()

    with pytest.raises(ValueError, match="Unknown service"):
        env.query_metrics("inventory-api")
