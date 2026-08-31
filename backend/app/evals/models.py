from pydantic import BaseModel, ConfigDict, Field


class IncidentEvaluationResult(BaseModel):
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
    expected_root_cause: str
    predicted_root_cause: str | None
    expected_remediation: str
    recommended_action: str | None
    final_p95_latency_ms: int
    final_error_rate_percent: float
    resolution_success: bool


class EvaluationSuiteResult(BaseModel):
    """Aggregate reliability metrics for a deterministic evaluation suite.

    Every rate is a unit interval: count_of_true_outcomes / total_scenarios.
    """

    model_config = ConfigDict(frozen=True)

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
    scenario_results: list[IncidentEvaluationResult]
