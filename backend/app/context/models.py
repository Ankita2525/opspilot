from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EvidenceType(str, Enum):
    METRIC = "metric"
    DEPLOYMENT = "deployment"
    LOG = "log"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    evidence_type: EvidenceType
    source: str
    summary: str
    relevance_score: float = Field(ge=0, le=1)
    timestamp: datetime | None = None
    suspicious_instruction_content: bool = False


class IncidentContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str
    affected_service: str
    symptom_summary: str
    evidence: list[EvidenceItem]
    recent_changes: list[EvidenceItem]
    context_version: int = 1
