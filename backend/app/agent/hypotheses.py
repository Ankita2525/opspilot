from pydantic import BaseModel, ConfigDict, Field

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
    """Turns collected diagnostic evidence into a structured hypothesis result."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def analyze(
        self,
        incident_id: str,
        affected_service: str,
        metrics: MetricResponse,
        deployments: list[DeploymentResponse],
        logs: list[LogResponse],
    ) -> HypothesisResult:
        user_prompt = _build_user_prompt(
            incident_id=incident_id,
            affected_service=affected_service,
            metrics=metrics,
            deployments=deployments,
            logs=logs,
        )
        with get_tracer().start_as_current_span("opspilot.hypothesis.generate") as span:
            span.set_attribute("opspilot.incident_id", incident_id)
            span.set_attribute("opspilot.service", affected_service)
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


def _build_user_prompt(
    incident_id: str,
    affected_service: str,
    metrics: MetricResponse,
    deployments: list[DeploymentResponse],
    logs: list[LogResponse],
) -> str:
    lines = [
        f"Incident ID: {incident_id}",
        f"Affected service: {affected_service}",
        "",
        "Metrics:",
        f"- p95 latency: {metrics.p95_latency_ms} ms",
        f"- error rate: {metrics.error_rate_percent}%",
        f"- timestamp: {metrics.timestamp.isoformat()}",
        "",
        "Recent deployments:",
    ]
    if deployments:
        for event in deployments:
            lines.append(
                f"- {event.service} {event.version} at {event.timestamp.isoformat()}"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Logs:")
    if logs:
        for event in logs:
            lines.append(
                f"- [{event.level}] {event.timestamp.isoformat()} {event.message}"
            )
    else:
        lines.append("- none")

    return "\n".join(lines)
