from typing import TypedDict

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.context.models import IncidentContext
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse


class InvestigationState(TypedDict):
    incident_id: str
    affected_service: str
    metrics: MetricResponse | None
    deployments: list[DeploymentResponse]
    logs: list[LogResponse]
    incident_context: IncidentContext | None
    hypothesis_result: HypothesisResult | None
    completed_steps: list[str]
    status: str
