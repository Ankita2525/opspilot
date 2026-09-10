from backend.app.evals.hosted import HostedEvaluationRunner
from backend.app.evals.hosted_report import render_hosted_evaluation_report
from tests.fakes import FakeModelProvider


def test_hosted_report_separates_provider_reliability_from_model_quality() -> None:
    result = HostedEvaluationRunner(
        provider=FakeModelProvider(),
        provider_name="test-provider",
        model_name="test-model",
        scenario_ids=("checkout-db-pool-regression",),
        trials=2,
    ).run()

    report = render_hosted_evaluation_report(result)

    assert "OpsPilot Hosted-Model Evaluation" in report
    assert "Provider: test-provider" in report
    assert "Model: test-model" in report
    assert "Trials per scenario: 2" in report
    assert "Total evaluations: 2" in report

    assert "Provider success rate: 100.0%" in report
    assert "End-to-end pass rate: 100.0%" in report

    assert "Root cause accuracy: 100.0%" in report
    assert "Action accuracy: 100.0%" in report
    assert "Approval compliance: 100.0%" in report
    assert "Unsafe action rate: 0.0%" in report
    assert "Remediation execution rate: 100.0%" in report
    assert "Resolution rate: 100.0%" in report
    assert "Health recovery rate: 100.0%" in report

    assert "checkout-db-pool-regression: 2/2 passed (100.0%)" in report
    assert "Provider failures: 0" in report


def test_hosted_report_shows_safe_per_trial_prediction_details() -> None:
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

    report = render_hosted_evaluation_report(result)

    assert "Trial details:" in report
    assert "checkout-db-pool-regression trial 1" in report
    assert "predicted_root_cause=cpu_saturation" in report
    assert "root_cause_correct=false" in report
    assert "recommended_action=rollback_deployment" in report
    assert "action_correct=true" in report
    assert "resolution_success=false" in report

    # Report must not expose evaluator ground truth or sensitive internals.
    assert "expected_root_cause" not in report
    assert "expected_remediation" not in report
    assert "system_prompt" not in report
    assert "user_prompt" not in report
    assert "GROQ_API_KEY" not in report
