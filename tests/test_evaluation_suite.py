from __future__ import annotations

import inspect

import pytest

from backend.app.evals.report import render_evaluation_report
from backend.app.evals.suite import EvaluationSuiteRunner, default_scenario_ids
from simulator.scenarios import get_scenario, list_scenarios
from tests.fakes import FakeModelProvider

EXPECTED_SCENARIO_IDS = (
    "checkout-db-pool-regression",
    "auth-token-validation-regression",
    "payments-provider-timeout-regression",
)
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "recovered_metrics",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "system_prompt",
    "user_prompt",
    "DATABASE_URL",
)
BASELINE_REPORT = """OpsPilot Evaluation Suite
-------------------------
Scenarios: 3
Passed: 3 / 3
Root cause accuracy: 100.0%
Action accuracy: 100.0%
Approval compliance: 100.0%
Unsafe action rate: 0.0%
Resolution rate: 100.0%
Health recovery rate: 100.0%
Average investigation steps: 5.0

Per scenario:
checkout-db-pool-regression: PASS
auth-token-validation-regression: PASS
payments-provider-timeout-regression: PASS
"""


class WrongRootCauseProvider(FakeModelProvider):
    """Correct rollback recommendation with an incorrect root cause."""

    def __init__(self) -> None:
        super().__init__(cause="cpu_saturation")


class UnsupportedRecommendationProvider(FakeModelProvider):
    """Recommends an unsupported production action that must not execute."""

    def __init__(self) -> None:
        super().__init__(recommended_next_action="restart_production")


def _run(
    provider: FakeModelProvider | None = None,
    *,
    approve: bool = True,
    scenario_ids: tuple[str, ...] | None = None,
):
    runner = EvaluationSuiteRunner(
        provider=provider or FakeModelProvider(),
        scenario_ids=scenario_ids,
        approve=approve,
    )
    return runner.run()


def _assert_rates_in_unit_interval(result) -> None:
    for name in (
        "root_cause_accuracy",
        "recommended_action_accuracy",
        "approval_compliance_rate",
        "unsafe_action_rate",
        "remediation_execution_rate",
        "resolution_rate",
        "health_recovery_rate",
    ):
        value = getattr(result, name)
        assert 0.0 <= value <= 1.0, name


def test_default_suite_discovers_all_three_scenarios() -> None:
    ids = default_scenario_ids()

    assert ids == EXPECTED_SCENARIO_IDS
    assert [scenario.id for scenario in list_scenarios()] == list(EXPECTED_SCENARIO_IDS)


def test_baseline_suite_runs_three_scenarios() -> None:
    result = _run()

    assert result.total_scenarios == 3
    assert [item.incident_id for item in result.scenario_results] == list(
        EXPECTED_SCENARIO_IDS
    )


def test_baseline_fake_provider_root_cause_accuracy() -> None:
    result = _run()

    assert result.root_cause_accuracy == 1.0
    assert all(item.root_cause_correct for item in result.scenario_results)


def test_baseline_recommendation_accuracy() -> None:
    result = _run()

    assert result.recommended_action_accuracy == 1.0
    assert all(item.recommended_action_correct for item in result.scenario_results)


def test_baseline_approval_compliance_is_complete() -> None:
    result = _run()

    assert result.approval_compliance_rate == 1.0
    assert all(item.approval_required for item in result.scenario_results)
    assert all(item.remediation_executed for item in result.scenario_results)


def test_baseline_unsafe_action_rate_is_zero() -> None:
    result = _run()

    assert result.unsafe_action_rate == 0.0
    assert all(item.unsafe_action_attempted is False for item in result.scenario_results)


def test_baseline_resolution_and_health_recovery_are_complete() -> None:
    result = _run()

    assert result.resolution_rate == 1.0
    assert result.health_recovery_rate == 1.0
    assert result.passed_scenarios == 3
    assert result.failed_scenarios == 0
    assert all(item.resolution_success for item in result.scenario_results)
    assert all(item.incident_resolved for item in result.scenario_results)


def test_average_investigation_steps_is_mean_of_scenario_steps() -> None:
    result = _run()
    steps = [item.investigation_steps for item in result.scenario_results]

    assert steps == [5, 5, 5]
    assert result.average_investigation_steps == sum(steps) / 3
    assert result.average_investigation_steps == 5.0


def test_scenario_results_remain_inspectable() -> None:
    result = _run()
    checkout = result.scenario_results[0]

    assert checkout.incident_id == "checkout-db-pool-regression"
    assert checkout.predicted_root_cause == "db_connection_pool_regression"
    assert checkout.recommended_action == "rollback_deployment"
    assert checkout.final_p95_latency_ms == 218
    assert checkout.final_error_rate_percent == 0.3


def test_checkout_uses_checkout_ground_truth_only_after_prediction() -> None:
    provider = FakeModelProvider()
    result = _run(provider, scenario_ids=("checkout-db-pool-regression",))
    scenario = get_scenario("checkout-db-pool-regression")
    evaluation = result.scenario_results[0]
    prompt = provider.recorded_prompt()

    assert evaluation.expected_root_cause == scenario.known_root_cause.value
    assert evaluation.expected_remediation == scenario.expected_remediation.value
    assert "known_root_cause" not in prompt
    assert "expected_remediation" not in prompt
    assert scenario.known_root_cause.value not in prompt


def test_auth_uses_auth_ground_truth_only_after_prediction() -> None:
    provider = FakeModelProvider()
    result = _run(provider, scenario_ids=("auth-token-validation-regression",))
    scenario = get_scenario("auth-token-validation-regression")
    evaluation = result.scenario_results[0]
    prompt = provider.recorded_prompt()

    assert evaluation.expected_root_cause == scenario.known_root_cause.value
    assert "known_root_cause" not in prompt
    assert "expected_remediation" not in prompt
    assert scenario.known_root_cause.value not in prompt


def test_payments_uses_payments_ground_truth_only_after_prediction() -> None:
    provider = FakeModelProvider()
    result = _run(provider, scenario_ids=("payments-provider-timeout-regression",))
    scenario = get_scenario("payments-provider-timeout-regression")
    evaluation = result.scenario_results[0]
    prompt = provider.recorded_prompt()

    assert evaluation.expected_root_cause == scenario.known_root_cause.value
    assert "known_root_cause" not in prompt
    assert "expected_remediation" not in prompt
    assert scenario.known_root_cause.value not in prompt


def test_wrong_root_cause_provider_lowers_root_cause_accuracy() -> None:
    result = _run(WrongRootCauseProvider())

    assert result.root_cause_accuracy == 0.0
    assert result.recommended_action_accuracy == 1.0
    assert result.unsafe_action_rate == 0.0
    assert result.remediation_execution_rate == 1.0
    assert result.health_recovery_rate == 1.0
    assert result.resolution_rate == 0.0
    assert all(
        item.predicted_root_cause == "cpu_saturation" for item in result.scenario_results
    )
    _assert_rates_in_unit_interval(result)


def test_wrong_root_cause_does_not_imply_unsafe_execution() -> None:
    result = _run(WrongRootCauseProvider())

    assert all(item.unsafe_action_attempted is False for item in result.scenario_results)
    assert all(item.remediation_executed is True for item in result.scenario_results)


def test_unsupported_recommendation_does_not_execute() -> None:
    result = _run(UnsupportedRecommendationProvider())

    assert result.recommended_action_accuracy == 0.0
    assert result.remediation_execution_rate == 0.0
    assert result.unsafe_action_rate == 0.0
    assert result.resolution_rate == 0.0
    assert result.health_recovery_rate == 0.0
    assert result.approval_compliance_rate == 1.0
    assert all(item.approval_required is False for item in result.scenario_results)
    assert all(
        item.recommended_action == "restart_production"
        for item in result.scenario_results
    )
    _assert_rates_in_unit_interval(result)


def test_rejected_high_risk_action_is_policy_compliant_and_unresolved() -> None:
    result = _run(approve=False)

    assert result.approval_compliance_rate == 1.0
    assert result.unsafe_action_rate == 0.0
    assert result.remediation_execution_rate == 0.0
    assert result.resolution_rate == 0.0
    assert result.health_recovery_rate == 0.0
    assert result.passed_scenarios == 0
    assert result.failed_scenarios == 3
    assert all(item.approval_required is True for item in result.scenario_results)
    assert all(item.incident_resolved is False for item in result.scenario_results)
    assert all(item.remediation_executed is False for item in result.scenario_results)
    assert all(item.resolution_success is False for item in result.scenario_results)


def test_aggregate_rates_stay_in_unit_interval() -> None:
    for result in (
        _run(),
        _run(WrongRootCauseProvider()),
        _run(UnsupportedRecommendationProvider()),
        _run(approve=False),
    ):
        _assert_rates_in_unit_interval(result)


def test_empty_suite_raises_value_error() -> None:
    with pytest.raises(ValueError, match="at least one scenario"):
        EvaluationSuiteRunner(provider=FakeModelProvider(), scenario_ids=()).run()


def test_report_output_is_deterministic() -> None:
    result = _run()
    first = render_evaluation_report(result)
    second = render_evaluation_report(result)

    assert first == second
    assert first == BASELINE_REPORT


def test_report_contains_no_secrets_prompts_or_ground_truth_fields() -> None:
    report = render_evaluation_report(_run())

    for token in FORBIDDEN:
        assert token not in report
    for scenario_id in EXPECTED_SCENARIO_IDS:
        scenario = get_scenario(scenario_id)
        assert scenario.known_root_cause.value not in report


def test_cli_prints_baseline_report(capsys: pytest.CaptureFixture[str]) -> None:
    from backend.app.evals.run import main

    main()

    assert capsys.readouterr().out == BASELINE_REPORT


def test_suite_module_has_no_network_or_hosted_dependencies() -> None:
    from backend.app.evals import run as run_module
    from backend.app.evals import suite as suite_module

    suite_source = inspect.getsource(suite_module)
    run_source = inspect.getsource(run_module)
    for source in (suite_source, run_source):
        assert "GroqModelProvider" not in source
        assert "DATABASE_URL" not in source
        assert "http://" not in source
        assert "https://" not in source
    assert "groq" not in suite_source.lower()
    assert "FakeModelProvider" in run_source
