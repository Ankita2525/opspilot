from pydantic import BaseModel, ConfigDict, Field

from backend.app.context.manager import ContextManager
from backend.app.context.models import IncidentContext
from backend.app.models.provider import ModelProvider
from backend.app.observability.tracing import get_tracer
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    summary: str


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cause: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceReference]


class HypothesisResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypotheses: list[RootCauseHypothesis]
    recommended_next_action: str
    reasoning_summary: str


_SYSTEM_PROMPT = """You are OpsPilot, a production incident investigator.

Use only the evidence supplied in the user prompt.
Do not invent facts.
Rank likely root causes from most to least likely.
Cite evidence summaries for each hypothesis.
Produce a concise reasoning_summary. Do not include chain-of-thought or hidden reasoning.
Recommend the next investigation or remediation action.
"""


class HypothesisEngine:
    """Turns bounded IncidentContext into a structured hypothesis result."""

    def __init__(
        self,
        provider: ModelProvider,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._provider = provider
        self._context_manager = context_manager or ContextManager()

    def build_context(
        self,
        *,
        incident_id: str,
        affected_service: str,
        metrics: MetricResponse,
        deployments: list[DeploymentResponse],
        logs: list[LogResponse],
    ) -> IncidentContext:
        return self._context_manager.build(
            incident_id=incident_id,
            affected_service=affected_service,
            metrics=metrics,
            deployments=deployments,
            logs=logs,
        )

    def analyze(
        self,
        incident_id: str,
        affected_service: str,
        metrics: MetricResponse,
        deployments: list[DeploymentResponse],
        logs: list[LogResponse],
    ) -> HypothesisResult:
        context = self.build_context(
            incident_id=incident_id,
            affected_service=affected_service,
            metrics=metrics,
            deployments=deployments,
            logs=logs,
        )
        return self.analyze_context(context)

    def analyze_context(self, context: IncidentContext) -> HypothesisResult:
        user_prompt = _prompt_from_context(context)
        with get_tracer().start_as_current_span("opspilot.hypothesis.generate") as span:
            span.set_attribute("opspilot.incident_id", context.incident_id)
            span.set_attribute("opspilot.service", context.affected_service)
            result = self._provider.generate_structured(
                _SYSTEM_PROMPT,
                user_prompt,
                HypothesisResult,
            )
            span.set_attribute("opspilot.hypothesis_count", len(result.hypotheses))
            span.set_attribute(
                "opspilot.recommended_action",
                result.recommended_next_action,
            )
            return result


def _prompt_from_context(context: IncidentContext) -> str:
    lines = [
        "Incident:",
        context.incident_id,
        "",
        "Service:",
        context.affected_service,
        "",
        "Symptoms:",
        context.symptom_summary,
        "",
        "Recent changes:",
    ]
    if context.recent_changes:
        for item in context.recent_changes:
            lines.append(f"- {item.summary}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Ranked evidence:")
    if context.evidence:
        for item in context.evidence:
            lines.append(f"- {item.summary}")
    else:
        lines.append("- none")

    return "\n".join(lines)
