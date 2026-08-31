from __future__ import annotations

from backend.app.agent.hypotheses import HypothesisResult
from tests.fakes import FakeModelProvider

SYSTEM_PROMPT = "Investigate using only supplied evidence."


def _analyze(user_prompt: str, provider: FakeModelProvider | None = None) -> HypothesisResult:
    fake = provider or FakeModelProvider()
    return fake.generate_structured(SYSTEM_PROMPT, user_prompt, HypothesisResult)


def test_checkout_prompt_returns_db_pool_hypothesis() -> None:
    result = _analyze("Affected service: checkout-api\nMetrics show elevated latency.")
    top = result.hypotheses[0]

    assert top.cause == "db_connection_pool_regression"
    assert top.confidence == 0.91
    assert result.recommended_next_action == "rollback_deployment"
    combined = " ".join(item.summary.lower() for item in top.evidence)
    assert "deployment" in combined
    assert "database connection pool timeout" in combined
    assert "checkout" in combined


def test_auth_prompt_returns_token_validation_hypothesis() -> None:
    result = _analyze("Affected service: auth-service\nAuthentication errors after deploy.")
    top = result.hypotheses[0]

    assert top.cause == "auth_token_validation_regression"
    assert top.confidence == 0.93
    assert result.recommended_next_action == "rollback_deployment"
    combined = " ".join(item.summary.lower() for item in top.evidence)
    assert "v2.7.1" in combined
    assert "token validation" in combined or "signature" in combined
    assert "authentication" in combined


def test_payments_prompt_returns_timeout_hypothesis() -> None:
    result = _analyze("Affected service: payments-service\nProvider calls are timing out.")
    top = result.hypotheses[0]

    assert top.cause == "payment_provider_timeout_regression"
    assert top.confidence == 0.90
    assert result.recommended_next_action == "rollback_deployment"
    combined = " ".join(item.summary.lower() for item in top.evidence)
    assert "v3.4.2" in combined
    assert "timeout" in combined
    assert "payments" in combined


def test_explicit_cause_override_still_wins() -> None:
    provider = FakeModelProvider(cause="cpu_saturation")
    result = _analyze(
        "Affected service: checkout-api",
        provider=provider,
    )

    assert result.hypotheses[0].cause == "cpu_saturation"
    assert result.recommended_next_action == "rollback_deployment"


def test_explicit_action_override_still_wins() -> None:
    provider = FakeModelProvider(recommended_next_action="increase_connection_pool")
    result = _analyze(
        "Affected service: checkout-api",
        provider=provider,
    )

    assert result.recommended_next_action == "increase_connection_pool"
    assert result.hypotheses[0].cause == "db_connection_pool_regression"
