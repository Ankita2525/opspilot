from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator


class RiskLevel(str, Enum):
    READ = "READ"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK = "HIGH_RISK"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RemediationProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    incident_id: str
    action: str
    service: str
    parameters: dict[str, str]
    risk_level: RiskLevel
    approval_status: ApprovalStatus

    @field_validator("parameters")
    @classmethod
    def _copy_parameters(cls, value: dict[str, str]) -> dict[str, str]:
        return dict(value)
