from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class IncidentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str
    scenario_id: str
    affected_service: str
    status: str
    created_at: datetime
    updated_at: datetime
    recommended_action: str | None
    selected_skills: list[str]
    resolved: bool

    @field_validator("selected_skills")
    @classmethod
    def _copy_selected_skills(cls, value: list[str]) -> list[str]:
        return list(value)


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    incident_id: str
    action: str
    service: str
    version: str | None
    risk_level: str
    status: str
    created_at: datetime
    updated_at: datetime


class AuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    incident_id: str
    event_type: str
    message: str
    timestamp: datetime
    metadata: dict[str, JsonValue]

    @field_validator("metadata")
    @classmethod
    def _copy_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return dict(value)


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str
    incident_id: str
    resolution_success: bool
    root_cause_correct: bool
    recommended_action_correct: bool
    unsafe_action_attempted: bool
    investigation_steps: int
    created_at: datetime
