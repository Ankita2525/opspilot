from __future__ import annotations

import pytest

from simulator.environment import SimulatedEnvironment
from simulator.models import (
    Remediation,
    RootCause,
    ServiceHealthThresholds,
    evaluate_service_health,
)
from simulator.scenarios import get_scenario, list_scenarios

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"

SCENARIO_CASES = [
    (
        "checkout-db-pool-regression",
        "checkout-api",
        "v1.18.3",
        1940,
        8.2,
        218,
        0.3,
        RootCause.DB_CONNECTION_POOL_REGRESSION,
    ),
    (
        "auth-token-validation-regression",
        "auth-service",
        "v2.7.1",
        870,
        14.6,
        165,
        0.4,
        RootCause.AUTH_TOKEN_VALIDATION_REGRESSION,
    ),
    (
        "payments-provider-timeout-regression",
        "payments-service",
        "v3.4.2",
        2680,
        11.1,
        295,
        0.6,
        RootCause.PAYMENT_PROVIDER_TIMEOUT_REGRESSION,
    ),
]


def _loaded_env(scenario_id: str = SCENARIO_ID) -> SimulatedEnvironment:
    env = SimulatedEnvironment()
    env.load_scenario(scenario_id)
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


def test_unknown_scenario_raises_value_error() -> None:
    env = SimulatedEnvironment()

    with pytest.raises(ValueError, match="Unknown scenario"):
        env.load_scenario("does-not-exist")
    with pytest.raises(ValueError, match="Unknown scenario"):
        get_scenario("does-not-exist")


@pytest.mark.parametrize(
    "scenario_id, service, version, incident_p95, incident_error, recovered_p95, recovered_error, root_cause",
    SCENARIO_CASES,
)
def test_registered_scenarios_load(
    scenario_id: str,
    service: str,
    version: str,
    incident_p95: int,
    incident_error: float,
    recovered_p95: int,
    recovered_error: float,
    root_cause: RootCause,
) -> None:
    env = SimulatedEnvironment()
    scenario = env.load_scenario(scenario_id)

    assert scenario.id == scenario_id
    assert scenario.affected_service == service
    assert scenario.known_root_cause == root_cause
    assert scenario.expected_remediation == Remediation.ROLLBACK_DEPLOYMENT
    assert any(event.version == version for event in scenario.deployments)


@pytest.mark.parametrize(
    "scenario_id, service, version, incident_p95, incident_error, recovered_p95, recovered_error, root_cause",
    SCENARIO_CASES,
)
def test_each_scenario_starts_unhealthy(
    scenario_id: str,
    service: str,
    version: str,
    incident_p95: int,
    incident_error: float,
    recovered_p95: int,
    recovered_error: float,
    root_cause: RootCause,
) -> None:
    env = _loaded_env(scenario_id)
    health = env.get_service_health(service)
    metrics = env.query_metrics(service)

    assert metrics.p95_latency_ms == incident_p95
    assert metrics.error_rate_percent == incident_error
    assert health.healthy is False
    assert health.p95_latency_ms == incident_p95
    assert health.error_rate_percent == incident_error


@pytest.mark.parametrize(
    "scenario_id, service, version, incident_p95, incident_error, recovered_p95, recovered_error, root_cause",
    SCENARIO_CASES,
)
def test_correct_rollback_makes_each_scenario_healthy(
    scenario_id: str,
    service: str,
    version: str,
    incident_p95: int,
    incident_error: float,
    recovered_p95: int,
    recovered_error: float,
    root_cause: RootCause,
) -> None:
    env = _loaded_env(scenario_id)
    env.rollback_deployment(service, version)
    health = env.get_service_health(service)
    metrics = env.query_metrics(service)

    assert env.is_resolved is True
    assert health.healthy is True
    assert metrics.p95_latency_ms == recovered_p95
    assert metrics.error_rate_percent == recovered_error
    assert health.p95_latency_ms <= health.max_p95_latency_ms
    assert health.error_rate_percent <= health.max_error_rate_percent


def test_checkout_recovery_metrics() -> None:
    env = _loaded_env()
    env.rollback_deployment(SERVICE, BAD_VERSION)
    metrics = env.query_metrics(SERVICE)

    assert metrics.p95_latency_ms == 218
    assert metrics.error_rate_percent == 0.3


def test_auth_recovery_metrics() -> None:
    env = _loaded_env("auth-token-validation-regression")
    env.rollback_deployment("auth-service", "v2.7.1")
    metrics = env.query_metrics("auth-service")

    assert metrics.p95_latency_ms == 165
    assert metrics.error_rate_percent == 0.4


def test_payments_recovery_metrics() -> None:
    env = _loaded_env("payments-provider-timeout-regression")
    env.rollback_deployment("payments-service", "v3.4.2")
    metrics = env.query_metrics("payments-service")

    assert metrics.p95_latency_ms == 295
    assert metrics.error_rate_percent == 0.6


def test_health_uses_thresholds_not_exact_recovered_numbers() -> None:
    thresholds = ServiceHealthThresholds(
        max_p95_latency_ms=400,
        max_error_rate_percent=1.0,
    )

    assert evaluate_service_health(218, 0.3, thresholds) is True
    assert evaluate_service_health(300, 0.8, thresholds) is True
    assert evaluate_service_health(400, 1.0, thresholds) is True
    assert evaluate_service_health(401, 0.2, thresholds) is False
    assert evaluate_service_health(200, 1.01, thresholds) is False
    assert evaluate_service_health(1940, 8.2, thresholds) is False


def test_get_service_health_unknown_service_raises_value_error() -> None:
    env = _loaded_env()

    with pytest.raises(ValueError, match="Unknown service"):
        env.get_service_health("inventory-api")


def test_list_scenarios_returns_all_three() -> None:
    ids = [scenario.id for scenario in list_scenarios()]

    assert ids == [
        "checkout-db-pool-regression",
        "auth-token-validation-regression",
        "payments-provider-timeout-regression",
    ]


def test_auth_logs_contain_token_evidence_without_hidden_label() -> None:
    env = _loaded_env("auth-token-validation-regression")
    combined = " ".join(event.message.lower() for event in env.get_logs("auth-service"))

    assert "signature verification" in combined
    assert "401" in combined
    assert "auth_token_validation_regression" not in combined


def test_payments_logs_contain_timeout_evidence_without_hidden_label() -> None:
    env = _loaded_env("payments-provider-timeout-regression")
    combined = " ".join(
        event.message.lower() for event in env.get_logs("payments-service")
    )

    assert "timeout" in combined
    assert "deadline" in combined
    assert "payment_provider_timeout_regression" not in combined
