from pydantic import BaseModel, ConfigDict


class IncidentEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    root_cause_correct: bool
    recommended_action_correct: bool
    approval_required: bool
    unsafe_action_attempted: bool
    remediation_executed: bool
    incident_resolved: bool
    latency_recovered: bool
    error_rate_recovered: bool
    investigation_steps: int
    expected_root_cause: str
    predicted_root_cause: str | None
    expected_remediation: str
    recommended_action: str | None
    final_p95_latency_ms: int
    final_error_rate_percent: float
    resolution_success: bool
