from backend.app.evals.hosted import HostedEvaluationRunner
from backend.app.models.provider_errors import (
    ModelCallError,
    ProviderErrorCategory,
    ProviderFailureMeta,
)
from tests.fakes import FakeModelProvider


SCENARIO_IDS = (
    "checkout-db-pool-regression",
    "auth-token-validation-regression",
    "payments-provider-timeout-regression",
)


class FailFirstCallProvider(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model,
    ):
        self.calls += 1
        if self.calls == 1:
            raise ModelCallError(
                ProviderFailureMeta(
                    category=ProviderErrorCategory.RATE_LIMITED,
                    exception_class="RateLimitError",
                    http_status=429,
                )
            )
        return super().generate_structured(
            system_prompt,
            user_prompt,
            response_model,
        )


def test_hosted_runner_repeats_every_scenario_for_each_trial() -> None:
    result = HostedEvaluationRunner(
        provider=FakeModelProvider(),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=SCENARIO_IDS,
        trials=2,
    ).run()

    assert result.provider == "test-provider"
    assert result.model == "test-model"
    assert result.trials_per_scenario == 2
    assert result.total_evaluations == 6
    assert result.successful_evaluations == 6
    assert result.failed_evaluations == 0

    assert [item.scenario_id for item in result.scenario_results] == list(SCENARIO_IDS)
    assert all(item.trials == 2 for item in result.scenario_results)
    assert all(item.successful_trials == 2 for item in result.scenario_results)
    assert all(item.failed_trials == 0 for item in result.scenario_results)
    assert all(item.pass_rate == 1.0 for item in result.scenario_results)


def test_provider_failure_is_recorded_and_remaining_trials_continue() -> None:
    result = HostedEvaluationRunner(
        provider=FailFirstCallProvider(),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=("checkout-db-pool-regression",),
        trials=2,
    ).run()

    assert result.total_evaluations == 2
    assert result.successful_evaluations == 1
    assert result.failed_evaluations == 1

    scenario = result.scenario_results[0]
    assert scenario.trials == 2
    assert scenario.successful_trials == 1
    assert scenario.failed_trials == 1
    assert scenario.passed_trials == 1
    assert scenario.pass_rate == 0.5

    assert len(result.provider_failures) == 1
    failure = result.provider_failures[0]
    assert failure.scenario_id == "checkout-db-pool-regression"
    assert failure.trial == 1
    assert failure.category == "rate_limited"
    assert failure.http_status == 429


def test_provider_failure_does_not_corrupt_model_quality_metrics() -> None:
    result = HostedEvaluationRunner(
        provider=FailFirstCallProvider(),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=("checkout-db-pool-regression",),
        trials=2,
    ).run()

    # One of two attempts reached and completed the model/workflow successfully.
    assert result.provider_success_rate == 0.5
    assert result.end_to_end_pass_rate == 0.5

    # Model-quality metrics use completed evaluations as their denominator.
    # The provider outage must not be misclassified as a wrong model answer.
    assert result.root_cause_accuracy == 1.0
    assert result.recommended_action_accuracy == 1.0
    assert result.approval_compliance_rate == 1.0
    assert result.unsafe_action_rate == 0.0
    assert result.remediation_execution_rate == 1.0
    assert result.resolution_rate == 1.0
    assert result.health_recovery_rate == 1.0


class AlwaysFailProvider(FakeModelProvider):
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model,
    ):
        raise ModelCallError(
            ProviderFailureMeta(
                category=ProviderErrorCategory.PROVIDER_5XX,
                exception_class="InternalServerError",
                http_status=503,
            )
        )


def test_all_provider_failures_leave_model_quality_unmeasured() -> None:
    result = HostedEvaluationRunner(
        provider=AlwaysFailProvider(),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=("checkout-db-pool-regression",),
        trials=2,
    ).run()

    assert result.total_evaluations == 2
    assert result.successful_evaluations == 0
    assert result.failed_evaluations == 2

    assert result.provider_success_rate == 0.0
    assert result.end_to_end_pass_rate == 0.0

    # No model/workflow evaluation completed, so model quality is unknown,
    # not zero.
    assert result.root_cause_accuracy is None
    assert result.recommended_action_accuracy is None
    assert result.approval_compliance_rate is None
    assert result.unsafe_action_rate is None
    assert result.remediation_execution_rate is None
    assert result.resolution_rate is None
    assert result.health_recovery_rate is None
    assert result.average_investigation_steps is None

    scenario = result.scenario_results[0]
    assert scenario.successful_trials == 0
    assert scenario.failed_trials == 2
    assert scenario.passed_trials == 0
    assert scenario.pass_rate == 0.0

    assert len(result.provider_failures) == 2
    assert all(
        failure.category == "provider_5xx"
        for failure in result.provider_failures
    )


def test_hosted_result_preserves_safe_per_trial_model_outcomes() -> None:
    result = HostedEvaluationRunner(
        provider=FakeModelProvider(
            cause="cpu_saturation",
            recommended_next_action="rollback_deployment",
        ),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=("checkout-db-pool-regression",),
        trials=1,
    ).run()

    assert len(result.trial_results) == 1

    trial = result.trial_results[0]
    assert trial.scenario_id == "checkout-db-pool-regression"
    assert trial.trial == 1

    assert trial.predicted_root_cause == "cpu_saturation"
    assert trial.root_cause_correct is False

    assert trial.recommended_action == "rollback_deployment"
    assert trial.recommended_action_correct is True

    assert trial.approval_required is True
    assert trial.remediation_executed is True
    assert trial.unsafe_action_attempted is False
    assert trial.health_recovered is True
    assert trial.resolution_success is False
    assert trial.investigation_steps == 5

    # Hosted artifacts expose outcomes, not hidden evaluator ground truth.
    serialized = trial.model_dump_json()
    assert "expected_root_cause" not in serialized
    assert "expected_remediation" not in serialized
    assert "system_prompt" not in serialized
    assert "user_prompt" not in serialized
    assert "GROQ_API_KEY" not in serialized
