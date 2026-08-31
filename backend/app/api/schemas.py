from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.tools.schemas import MetricResponse


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    service: str


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    affected_service: str


class StartIncidentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str


class SubmitApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    approved: bool


class IncidentStartResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    scenario_id: str
    affected_service: str
    status: str
    investigation_status: str
    investigation_steps: list[str]
    metrics: MetricResponse
    hypothesis_result: HypothesisResult
    recommended_action: str | None
    proposed_version: str | None
    approval_request: dict[str, Any] | None
    resolved: bool
    selected_skills: list[str]


class IncidentApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    status: str
    execution_success: bool
    recovered_p95_latency_ms: int | None
    recovered_error_rate_percent: float | None
    resolved: bool
    approval_status: str | None
