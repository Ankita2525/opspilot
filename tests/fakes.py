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
        recommended_next_action: str = "rollback_deployment",
        cause: str = "db_connection_pool_regression",
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
        result = HypothesisResult(
            hypotheses=[
                RootCauseHypothesis(
                    cause=self.cause,
                    confidence=0.91,
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
                )
            ],
            recommended_next_action=self.recommended_next_action,
            reasoning_summary=(
                "Evidence strongly correlates the recent deployment with "
                "database pool failures and checkout degradation."
            ),
        )
        return response_model.model_validate(result.model_dump())

    def recorded_prompt(self) -> str:
        return "\n".join([*self.system_prompts, *self.user_prompts])
