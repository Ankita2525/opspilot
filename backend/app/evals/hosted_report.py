from backend.app.evals.hosted_models import HostedEvaluationResult


def render_hosted_evaluation_report(result: HostedEvaluationResult) -> str:
    """Render a human-readable hosted-model evaluation report."""

    lines = [
        "OpsPilot Hosted-Model Evaluation",
        "--------------------------------",
        f"Provider: {result.provider}",
        f"Model: {result.model}",
        f"Trials per scenario: {result.trials_per_scenario}",
        f"Total evaluations: {result.total_evaluations}",
        "",
        "Provider reliability:",
        f"Provider success rate: {_percent(result.provider_success_rate)}",
        f"Provider failures: {result.failed_evaluations}",
        "",
        "End-to-end:",
        f"End-to-end pass rate: {_percent(result.end_to_end_pass_rate)}",
        "",
        "Model/workflow quality (completed evaluations only):",
        f"Root cause accuracy: {_percent(result.root_cause_accuracy)}",
        f"Action accuracy: {_percent(result.recommended_action_accuracy)}",
        f"Approval compliance: {_percent(result.approval_compliance_rate)}",
        f"Unsafe action rate: {_percent(result.unsafe_action_rate)}",
        f"Remediation execution rate: {_percent(result.remediation_execution_rate)}",
        f"Resolution rate: {_percent(result.resolution_rate)}",
        f"Health recovery rate: {_percent(result.health_recovery_rate)}",
        f"Average investigation steps: {_number(result.average_investigation_steps)}",
        "",
        "Per scenario:",
    ]

    for scenario in result.scenario_results:
        lines.append(
            f"{scenario.scenario_id}: "
            f"{scenario.passed_trials}/{scenario.trials} passed "
            f"({_percent(scenario.pass_rate)})"
        )

    if result.trial_results:
        lines.extend(["", "Trial details:"])
        for trial in result.trial_results:
            lines.append(
                f"{trial.scenario_id} trial {trial.trial}: "
                f"predicted_root_cause={trial.predicted_root_cause}, "
                f"root_cause_correct={_bool(trial.root_cause_correct)}, "
                f"recommended_action={trial.recommended_action}, "
                f"action_correct={_bool(trial.recommended_action_correct)}, "
                f"resolution_success={_bool(trial.resolution_success)}"
            )

    if result.provider_failures:
        lines.extend(["", "Provider failure details:"])
        for failure in result.provider_failures:
            detail = (
                f"{failure.scenario_id} trial {failure.trial}: "
                f"{failure.category} ({failure.exception_class})"
            )
            if failure.http_status is not None:
                detail += f" HTTP {failure.http_status}"
            lines.append(detail)

    return "\n".join(lines) + "\n"


def _percent(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.1f}%"


def _number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def _bool(value: bool) -> str:
    return "true" if value else "false"
