from pydantic import BaseModel, ConfigDict, Field


class HostedProviderFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    trial: int = Field(ge=1)
    category: str
    exception_class: str
    http_status: int | None = None


class HostedTrialResult(BaseModel):
    """Safe outcome from one completed hosted-model evaluation trial."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    trial: int = Field(ge=1)

    predicted_root_cause: str | None
    root_cause_correct: bool

    recommended_action: str | None
    recommended_action_correct: bool

    approval_required: bool
    remediation_executed: bool
    unsafe_action_attempted: bool
    health_recovered: bool
    resolution_success: bool
    investigation_steps: int = Field(ge=0)


class HostedScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    trials: int = Field(ge=1)
    successful_trials: int = Field(ge=0)
    failed_trials: int = Field(ge=0)
    passed_trials: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)


class HostedEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    trials_per_scenario: int = Field(ge=1)

    total_evaluations: int = Field(ge=0)
    successful_evaluations: int = Field(ge=0)
    failed_evaluations: int = Field(ge=0)

    provider_success_rate: float = Field(ge=0.0, le=1.0)
    end_to_end_pass_rate: float = Field(ge=0.0, le=1.0)

    root_cause_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    recommended_action_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    approval_compliance_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    unsafe_action_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    remediation_execution_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    resolution_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    health_recovery_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_investigation_steps: float | None = Field(default=None, ge=0.0)

    scenario_results: list[HostedScenarioResult]
    trial_results: list[HostedTrialResult]
    provider_failures: list[HostedProviderFailure]
