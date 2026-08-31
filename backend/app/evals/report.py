from backend.app.evals.models import EvaluationSuiteResult


def render_evaluation_report(result: EvaluationSuiteResult) -> str:
    """Render a deterministic plain-text evaluation report."""
    lines = [
        "OpsPilot Evaluation Suite",
        "-------------------------",
        f"Scenarios: {result.total_scenarios}",
        f"Passed: {result.passed_scenarios} / {result.total_scenarios}",
        f"Root cause accuracy: {_percent(result.root_cause_accuracy)}",
        f"Action accuracy: {_percent(result.recommended_action_accuracy)}",
        f"Approval compliance: {_percent(result.approval_compliance_rate)}",
        f"Unsafe action rate: {_percent(result.unsafe_action_rate)}",
        f"Resolution rate: {_percent(result.resolution_rate)}",
        f"Health recovery rate: {_percent(result.health_recovery_rate)}",
        f"Average investigation steps: {result.average_investigation_steps:.1f}",
        "",
        "Per scenario:",
    ]
    for evaluation in result.scenario_results:
        outcome = "PASS" if evaluation.resolution_success else "FAIL"
        lines.append(f"{evaluation.incident_id}: {outcome}")
    return "\n".join(lines) + "\n"


def _percent(rate: float) -> str:
    return f"{rate * 100:.1f}%"
