from collections.abc import Sequence

from backend.app.evals.hosted_models import (
    HostedEvaluationResult,
    HostedProviderFailure,
    HostedScenarioResult,
    HostedTrialResult,
)
from backend.app.evals.models import EvaluationSuiteResult
from backend.app.evals.suite import EvaluationSuiteRunner, default_scenario_ids
from backend.app.models.provider import ModelProvider
from backend.app.models.provider_errors import ModelCallError


class HostedEvaluationRunner:
    """Repeat controlled incident evaluations with an injected model provider."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        provider_name: str,
        model_name: str,
        scenario_ids: Sequence[str] | None = None,
        trials: int = 3,
    ) -> None:
        if trials < 1:
            raise ValueError("Hosted evaluation requires at least one trial.")

        self._provider = provider
        self._provider_name = provider_name
        self._model_name = model_name
        self._scenario_ids = tuple(
            scenario_ids if scenario_ids is not None else default_scenario_ids()
        )
        self._trials = trials

    def run(self) -> HostedEvaluationResult:
        if not self._scenario_ids:
            raise ValueError("Hosted evaluation requires at least one scenario.")

        scenario_results: list[HostedScenarioResult] = []
        trial_results: list[HostedTrialResult] = []
        provider_failures: list[HostedProviderFailure] = []
        completed_results: list[EvaluationSuiteResult] = []

        for scenario_id in self._scenario_ids:
            passed_trials = 0
            failed_trials = 0

            for trial in range(1, self._trials + 1):
                try:
                    result = EvaluationSuiteRunner(
                        provider=self._provider,
                        scenario_ids=(scenario_id,),
                    ).run()
                except ModelCallError as exc:
                    failed_trials += 1
                    provider_failures.append(
                        HostedProviderFailure(
                            scenario_id=scenario_id,
                            trial=trial,
                            category=exc.meta.category.value,
                            exception_class=exc.meta.exception_class,
                            http_status=exc.meta.http_status,
                        )
                    )
                    continue

                completed_results.append(result)
                evaluation = result.scenario_results[0]

                trial_results.append(
                    HostedTrialResult(
                        scenario_id=scenario_id,
                        trial=trial,
                        predicted_root_cause=evaluation.predicted_root_cause,
                        root_cause_correct=evaluation.root_cause_correct,
                        recommended_action=evaluation.recommended_action,
                        recommended_action_correct=(
                            evaluation.recommended_action_correct
                        ),
                        approval_required=evaluation.approval_required,
                        remediation_executed=evaluation.remediation_executed,
                        unsafe_action_attempted=evaluation.unsafe_action_attempted,
                        health_recovered=result.health_recovery_rate == 1.0,
                        resolution_success=evaluation.resolution_success,
                        investigation_steps=evaluation.investigation_steps,
                    )
                )

                if evaluation.resolution_success:
                    passed_trials += 1

            scenario_results.append(
                HostedScenarioResult(
                    scenario_id=scenario_id,
                    trials=self._trials,
                    successful_trials=self._trials - failed_trials,
                    failed_trials=failed_trials,
                    passed_trials=passed_trials,
                    pass_rate=passed_trials / self._trials,
                )
            )

        total_evaluations = len(self._scenario_ids) * self._trials
        successful_evaluations = len(completed_results)
        failed_evaluations = len(provider_failures)
        passed_evaluations = sum(item.passed_scenarios for item in completed_results)

        return HostedEvaluationResult(
            provider=self._provider_name,
            model=self._model_name,
            trials_per_scenario=self._trials,
            total_evaluations=total_evaluations,
            successful_evaluations=successful_evaluations,
            failed_evaluations=failed_evaluations,
            provider_success_rate=successful_evaluations / total_evaluations,
            end_to_end_pass_rate=passed_evaluations / total_evaluations,
            root_cause_accuracy=_completed_mean(
                completed_results,
                "root_cause_accuracy",
            ),
            recommended_action_accuracy=_completed_mean(
                completed_results,
                "recommended_action_accuracy",
            ),
            approval_compliance_rate=_completed_mean(
                completed_results,
                "approval_compliance_rate",
            ),
            unsafe_action_rate=_completed_mean(
                completed_results,
                "unsafe_action_rate",
            ),
            remediation_execution_rate=_completed_mean(
                completed_results,
                "remediation_execution_rate",
            ),
            resolution_rate=_completed_mean(
                completed_results,
                "resolution_rate",
            ),
            health_recovery_rate=_completed_mean(
                completed_results,
                "health_recovery_rate",
            ),
            average_investigation_steps=_completed_mean(
                completed_results,
                "average_investigation_steps",
            ),
            scenario_results=scenario_results,
            trial_results=trial_results,
            provider_failures=provider_failures,
        )


def _completed_mean(
    results: Sequence[EvaluationSuiteResult],
    field: str,
) -> float | None:
    """Mean over completed model/workflow evaluations only."""

    if not results:
        return None

    return sum(float(getattr(result, field)) for result in results) / len(results)
