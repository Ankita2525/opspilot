from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.persistence.models import JsonValue
from backend.app.provenance.models import LiveRunProvenance
from backend.app.tools.schemas import MetricResponse


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    service: str


class ReadyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "degraded", "unready"]
    database: str
    model_provider: str
    lease_subsystem: str = "not_configured"
    live_sandbox: str = "not_required"
    prometheus: str = "not_required"
    loki: str = "not_required"
    ai_capacity: str = "available"
    sandbox_operational: str = "available"


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    affected_service: str


class StartIncidentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    turnstile_token: str | None = None


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


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    message: str
    timestamp: datetime
    metadata: dict[str, JsonValue]


class IncidentAuditResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    events: list[AuditEventResponse]


class IncidentApprovalSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    action: str
    service: str
    version: str | None
    risk_level: str
    status: str


class IncidentSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    scenario_id: str
    affected_service: str
    status: str
    selected_skills: list[str]
    recommended_action: str | None
    resolved: bool
    created_at: datetime
    updated_at: datetime
    approval: IncidentApprovalSummary | None


class IncidentProvenanceResponse(LiveRunProvenance):
    """Public read-only provenance for a live incident run."""


class BaselineScenarioEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    root_cause_correct: bool
    recommended_action_correct: bool
    approval_required: bool
    unsafe_action_attempted: bool
    remediation_executed: bool
    incident_resolved: bool
    latency_recovered: bool
    error_rate_recovered: bool
    investigation_steps: int
    predicted_root_cause: str | None
    recommended_action: str | None
    final_p95_latency_ms: int
    final_error_rate_percent: float
    resolution_success: bool


class BaselineEvaluationResponse(BaseModel):
    """Public aggregate for the deterministic FakeModelProvider evaluation suite.

    This is a local baseline, not a Groq/production accuracy claim.
    """

    model_config = ConfigDict(frozen=True)

    evaluation_mode: Literal["deterministic_baseline"]
    total_scenarios: int = Field(ge=0)
    passed_scenarios: int = Field(ge=0)
    failed_scenarios: int = Field(ge=0)
    root_cause_accuracy: float = Field(ge=0.0, le=1.0)
    recommended_action_accuracy: float = Field(ge=0.0, le=1.0)
    approval_compliance_rate: float = Field(ge=0.0, le=1.0)
    unsafe_action_rate: float = Field(ge=0.0, le=1.0)
    remediation_execution_rate: float = Field(ge=0.0, le=1.0)
    resolution_rate: float = Field(ge=0.0, le=1.0)
    health_recovery_rate: float = Field(ge=0.0, le=1.0)
    average_investigation_steps: float = Field(ge=0.0)
    scenario_results: list[BaselineScenarioEvaluation]
