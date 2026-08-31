from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.agent.hypotheses import (
    HypothesisEngine,
    HypothesisResult,
    RootCauseHypothesis,
)
from backend.app.tools.diagnostics import DiagnosticTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"


def _analyze() -> tuple[
    SimulatedEnvironment,
    FakeModelProvider,
    HypothesisResult,
]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    tools = DiagnosticTools(environment)
    provider = FakeModelProvider()
    engine = HypothesisEngine(provider)
    result = engine.analyze(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        metrics=tools.query_metrics(SERVICE),
        deployments=tools.get_recent_deployments(SERVICE),
        logs=tools.get_service_logs(SERVICE),
    )
    return environment, provider, result


def test_analyze_returns_hypothesis_result() -> None:
    _, _, result = _analyze()

    assert isinstance(result, HypothesisResult)
    assert result.hypotheses


def test_highest_hypothesis_cause() -> None:
    _, _, result = _analyze()

    highest = max(result.hypotheses, key=lambda item: item.confidence)
    assert highest.cause == "db_connection_pool_regression"


def test_confidence_equals_0_91() -> None:
    _, _, result = _analyze()

    highest = max(result.hypotheses, key=lambda item: item.confidence)
    assert highest.confidence == 0.91


def test_confidence_rejects_values_outside_0_1() -> None:
    with pytest.raises(ValidationError):
        RootCauseHypothesis(cause="invalid", confidence=1.5, evidence=[])

    with pytest.raises(ValidationError):
        RootCauseHypothesis(cause="invalid", confidence=-0.01, evidence=[])


def test_prompt_includes_affected_service() -> None:
    _, provider, _ = _analyze()

    assert SERVICE in provider.recorded_prompt()


def test_prompt_includes_latency_1940() -> None:
    _, provider, _ = _analyze()

    assert "1940" in provider.recorded_prompt()


def test_prompt_includes_error_rate_8_2() -> None:
    _, provider, _ = _analyze()

    assert "8.2" in provider.recorded_prompt()


def test_prompt_includes_deployment_v1_18_3() -> None:
    _, provider, _ = _analyze()

    assert BAD_VERSION in provider.recorded_prompt()


def test_prompt_includes_database_timeout_evidence() -> None:
    _, provider, _ = _analyze()

    assert "database connection pool timeout" in provider.recorded_prompt().lower()


def test_prompt_does_not_include_known_root_cause() -> None:
    _, provider, _ = _analyze()
    prompt = provider.recorded_prompt()

    assert "known_root_cause" not in prompt
    assert "db_connection_pool_regression" not in prompt


def test_prompt_does_not_include_expected_remediation() -> None:
    _, provider, _ = _analyze()
    prompt = provider.recorded_prompt()

    assert "expected_remediation" not in prompt
    assert "rollback_deployment" not in prompt


def test_prompt_is_built_from_incident_context() -> None:
    _, provider, _ = _analyze()
    prompt = provider.recorded_prompt()

    assert "Incident:" in prompt
    assert INCIDENT_ID in prompt
    assert "Service:" in prompt
    assert "Symptoms:" in prompt
    assert "p95 latency of 1940 ms" in prompt
    assert "Recent changes:" in prompt
    assert "Ranked evidence:" in prompt
    assert "ERROR:" in prompt


def test_analysis_does_not_mutate_or_resolve_incident() -> None:
    environment, _, _ = _analyze()

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2
