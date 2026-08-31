from pydantic import BaseModel

from backend.app.agent.hypotheses import (
    EvidenceReference,
    HypothesisResult,
    RootCauseHypothesis,
)


class FakeModelProvider:
    """Test-only provider that records prompts and returns a fixed hypothesis."""

    def __init__(
        self,
        recommended_next_action: str | None = None,
        cause: str | None = None,
    ) -> None:
        self.recommended_next_action = recommended_next_action
        self.cause = cause
        self.system_prompts: list[str] = []
        self.user_prompts: list[str] = []

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        diagnosis = _diagnosis_for_prompt(user_prompt)
        result = HypothesisResult(
            hypotheses=[
                RootCauseHypothesis(
                    cause=self.cause or diagnosis.cause,
                    confidence=diagnosis.confidence,
                    evidence=list(diagnosis.evidence),
                )
            ],
            recommended_next_action=(
                self.recommended_next_action or diagnosis.recommended_next_action
            ),
            reasoning_summary=diagnosis.reasoning_summary,
        )
        return response_model.model_validate(result.model_dump())

    def recorded_prompt(self) -> str:
        return "\n".join([*self.system_prompts, *self.user_prompts])


class _Diagnosis:
    def __init__(
        self,
        *,
        cause: str,
        confidence: float,
        recommended_next_action: str,
        evidence: list[EvidenceReference],
        reasoning_summary: str,
    ) -> None:
        self.cause = cause
        self.confidence = confidence
        self.recommended_next_action = recommended_next_action
        self.evidence = evidence
        self.reasoning_summary = reasoning_summary


def _diagnosis_for_prompt(user_prompt: str) -> _Diagnosis:
    haystack = user_prompt.lower()
    if "auth-service" in haystack:
        return _Diagnosis(
            cause="auth_token_validation_regression",
            confidence=0.93,
            recommended_next_action="rollback_deployment",
            evidence=[
                EvidenceReference(
                    source_type="deployment",
                    summary="deployment v2.7.1 occurred shortly before incident",
                ),
                EvidenceReference(
                    source_type="log",
                    summary="logs contain signature and token validation errors",
                ),
                EvidenceReference(
                    source_type="metrics",
                    summary="authentication failures are elevated",
                ),
            ],
            reasoning_summary=(
                "Evidence strongly correlates the recent deployment with "
                "token validation failures and authentication degradation."
            ),
        )
    if "payments-service" in haystack:
        return _Diagnosis(
            cause="payment_provider_timeout_regression",
            confidence=0.90,
            recommended_next_action="rollback_deployment",
            evidence=[
                EvidenceReference(
                    source_type="deployment",
                    summary="deployment v3.4.2 occurred shortly before incident",
                ),
                EvidenceReference(
                    source_type="log",
                    summary="logs contain payment provider timeout errors",
                ),
                EvidenceReference(
                    source_type="metrics",
                    summary="payments latency and error rate are elevated",
                ),
            ],
            reasoning_summary=(
                "Evidence strongly correlates the recent deployment with "
                "upstream payment timeouts and payments degradation."
            ),
        )
    return _Diagnosis(
        cause="db_connection_pool_regression",
        confidence=0.91,
        recommended_next_action="rollback_deployment",
        evidence=[
            EvidenceReference(
                source_type="deployment",
                summary="deployment v1.18.3 occurred shortly before incident",
            ),
            EvidenceReference(
                source_type="log",
                summary="logs contain database connection pool timeout errors",
            ),
            EvidenceReference(
                source_type="metrics",
                summary="checkout latency and error rate are elevated",
            ),
        ],
        reasoning_summary=(
            "Evidence strongly correlates the recent deployment with "
            "database pool failures and checkout degradation."
        ),
    )
