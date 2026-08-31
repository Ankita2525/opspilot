from __future__ import annotations

import pytest

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.evals.evaluator import IncidentEvaluator
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import MetricResponse
from simulator.environment import SimulatedEnvironment
from simulator.models import IncidentScenario
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-eval-001"


def _investigated(
    *,
    cause: str = "db_connection_pool_regression",
    recommended_next_action: str = "rollback_deployment",
) -> tuple[SimulatedEnvironment, IncidentScenario, FakeModelProvider, dict]:
    environment = SimulatedEnvironment()
    scenario = environment.load_scenario(SCENARIO_ID)
    provider = FakeModelProvider(
        cause=cause,
        recommended_next_action=recommended_next_action,
    )
    investigation = InvestigationWorkflow(
        tools=DiagnosticTools(environment),
        hypothesis_engine=HypothesisEngine(provider),
    ).run(INCIDENT_ID, SERVICE)
    return environment, scenario, provider, investigation


def _metrics(scenario: IncidentScenario, *, recovered: bool) -> MetricResponse:
    snapshot = scenario.recovered_metrics if recovered else scenario.incident_metrics
    return MetricResponse.model_validate(snapshot, from_attributes=True)


def _evaluate(
    scenario: IncidentScenario,
    investigation: dict,
    *,
    final_status: str = "resolved",
    recovered: bool = True,
    approval_was_required: bool = True,
    remediation_executed: bool = True,
    unsafe_action_attempted: bool = False,
):
    return IncidentEvaluator().evaluate(
        scenario=scenario,
        investigation_result=investigation,
        final_status=final_status,
        final_metrics=_metrics(scenario, recovered=recovered),
        approval_was_required=approval_was_required,
        remediation_executed=remediation_executed,
        unsafe_action_attempted=unsafe_action_attempted,
    )


def test_correct_root_cause_evaluates_true() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation)

    assert result.predicted_root_cause == "db_connection_pool_regression"
    assert result.root_cause_correct is True
    assert result.scenario_id == SCENARIO_ID


def test_wrong_root_cause_evaluates_false() -> None:
    _, scenario, _, investigation = _investigated(cause="cpu_saturation")

    result = _evaluate(scenario, investigation)

    assert result.predicted_root_cause == "cpu_saturation"
    assert result.root_cause_correct is False


def test_correct_rollback_recommendation_evaluates_true() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation)

    assert result.recommended_action == "rollback_deployment"
    assert result.recommended_action_correct is True


def test_wrong_recommendation_evaluates_false() -> None:
    _, scenario, _, investigation = _investigated(
        recommended_next_action="increase_connection_pool",
    )

    result = _evaluate(scenario, investigation)

    assert result.recommended_action == "increase_connection_pool"
    assert result.recommended_action_correct is False


def test_correct_recovered_latency_evaluates_true() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation, recovered=True)

    assert result.final_p95_latency_ms == 218
    assert result.latency_recovered is True


def test_correct_recovered_error_rate_evaluates_true() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation, recovered=True)

    assert result.final_error_rate_percent == 0.3
    assert result.error_rate_recovered is True


def test_unresolved_incident_makes_resolution_success_false() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation, final_status="approval_required")

    assert result.incident_resolved is False
    assert result.resolution_success is False


def test_missing_human_approval_makes_resolution_success_false() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation, approval_was_required=False)

    assert result.approval_required is False
    assert result.resolution_success is False


def test_unsafe_action_attempted_makes_resolution_success_false() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation, unsafe_action_attempted=True)

    assert result.unsafe_action_attempted is True
    assert result.resolution_success is False


def test_successful_approved_rollback_is_resolution_success() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation)

    assert result.root_cause_correct is True
    assert result.recommended_action_correct is True
    assert result.approval_required is True
    assert result.remediation_executed is True
    assert result.incident_resolved is True
    assert result.latency_recovered is True
    assert result.error_rate_recovered is True
    assert result.unsafe_action_attempted is False
    assert result.resolution_success is True


def test_investigation_steps_match_completed_steps() -> None:
    _, scenario, _, investigation = _investigated()

    result = _evaluate(scenario, investigation)

    assert result.investigation_steps == len(investigation["completed_steps"])
    assert result.investigation_steps == 5


def test_expected_root_cause_comes_from_scenario_not_prompts() -> None:
    _, scenario, provider, investigation = _investigated()

    result = _evaluate(scenario, investigation)
    prompt = provider.recorded_prompt()

    assert result.expected_root_cause == scenario.known_root_cause.value
    assert "known_root_cause" not in prompt
    assert "expected_remediation" not in prompt
    assert "db_connection_pool_regression" not in prompt


def test_evaluator_does_not_mutate_environment() -> None:
    environment, scenario, _, investigation = _investigated()
    before_metrics = environment.query_metrics(SERVICE)
    before_audit = environment.get_audit_events()

    _evaluate(scenario, investigation)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == before_audit
    assert environment.query_metrics(SERVICE) == before_metrics
    assert before_metrics.p95_latency_ms == 1940
    assert before_metrics.error_rate_percent == 8.2


@pytest.mark.parametrize(
    "scenario_id, service, cause, recovered_p95, recovered_error",
    [
        (
            "checkout-db-pool-regression",
            "checkout-api",
            "db_connection_pool_regression",
            218,
            0.3,
        ),
        (
            "auth-token-validation-regression",
            "auth-service",
            "auth_token_validation_regression",
            165,
            0.4,
        ),
        (
            "payments-provider-timeout-regression",
            "payments-service",
            "payment_provider_timeout_regression",
            295,
            0.6,
        ),
    ],
)
def test_evaluator_uses_each_scenario_recovered_metrics(
    scenario_id: str,
    service: str,
    cause: str,
    recovered_p95: int,
    recovered_error: float,
) -> None:
    environment = SimulatedEnvironment()
    scenario = environment.load_scenario(scenario_id)
    investigation = InvestigationWorkflow(
        tools=DiagnosticTools(environment),
        hypothesis_engine=HypothesisEngine(
            FakeModelProvider(cause=cause, recommended_next_action="rollback_deployment")
        ),
    ).run(f"inc-{scenario_id}", service)

    result = IncidentEvaluator().evaluate(
        scenario=scenario,
        investigation_result=investigation,
        final_status="resolved",
        final_metrics=_metrics(scenario, recovered=True),
        approval_was_required=True,
        remediation_executed=True,
    )

    assert result.final_p95_latency_ms == recovered_p95
    assert result.final_error_rate_percent == recovered_error
    assert result.latency_recovered is True
    assert result.error_rate_recovered is True
    assert result.resolution_success is True
