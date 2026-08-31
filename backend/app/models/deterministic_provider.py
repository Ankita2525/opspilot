from __future__ import annotations

from pydantic import BaseModel

from backend.app.agent.hypotheses import (
    EvidenceReference,
    HypothesisResult,
    RecommendedAction,
    RootCauseHypothesis,
)


class DeterministicModelProvider:
    """Reference/demo provider for deterministic local behavior without external APIs.

    Used for local development, Docker demo mode, and the deterministic baseline
    evaluation endpoint. This is not a live Groq model.
    """

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        del system_prompt
        diagnosis = _diagnosis_for_prompt(user_prompt)
        result = HypothesisResult(
            hypotheses=[
                RootCauseHypothesis(
                    cause=diagnosis.cause,
                    confidence=diagnosis.confidence,
                    evidence=list(diagnosis.evidence),
                )
            ],
            recommended_action=diagnosis.recommended_action,
            recommendation_summary=diagnosis.recommendation_summary,
            reasoning_summary=diagnosis.reasoning_summary,
        )
        return response_model.model_validate(result.model_dump())


class _Diagnosis:
    def __init__(
        self,
        *,
        cause: str,
        confidence: float,
        recommended_action: RecommendedAction,
        evidence: list[EvidenceReference],
        recommendation_summary: str,
        reasoning_summary: str,
    ) -> None:
        self.cause = cause
        self.confidence = confidence
        self.recommended_action = recommended_action
        self.evidence = evidence
        self.recommendation_summary = recommendation_summary
        self.reasoning_summary = reasoning_summary


def _diagnosis_for_prompt(user_prompt: str) -> _Diagnosis:
    haystack = user_prompt.lower()
    if "auth-service" in haystack:
        return _Diagnosis(
            cause="auth_token_validation_regression",
            confidence=0.93,
            recommended_action=RecommendedAction.ROLLBACK_DEPLOYMENT,
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
            recommendation_summary=(
                "Token validation failures followed deployment v2.7.1. "
                "Rolling back the deployment is the safest currently available remediation."
            ),
            reasoning_summary=(
                "Evidence strongly correlates the recent deployment with "
                "token validation failures and authentication degradation."
            ),
        )
    if "payments-service" in haystack:
        return _Diagnosis(
            cause="payment_provider_timeout_regression",
            confidence=0.90,
            recommended_action=RecommendedAction.ROLLBACK_DEPLOYMENT,
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
            recommendation_summary=(
                "Payment provider timeouts followed deployment v3.4.2. "
                "Rolling back the deployment is the safest currently available remediation."
            ),
            reasoning_summary=(
                "Evidence strongly correlates the recent deployment with "
                "upstream payment timeouts and payments degradation."
            ),
        )
    return _Diagnosis(
        cause="db_connection_pool_regression",
        confidence=0.91,
        recommended_action=RecommendedAction.ROLLBACK_DEPLOYMENT,
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
        recommendation_summary=(
            "Connection pool exhaustion followed deployment v1.18.3. "
            "Rolling back the deployment is the safest currently available remediation."
        ),
        reasoning_summary=(
            "Evidence strongly correlates the recent deployment with "
            "database pool failures and checkout degradation."
        ),
    )
