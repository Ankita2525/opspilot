from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvestigationEventType(str, Enum):
    INCIDENT_STARTED = "incident_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    CONTEXT_BUILT = "context_built"
    SKILLS_SELECTED = "skills_selected"
    HYPOTHESIS_GENERATED = "hypothesis_generated"
    APPROVAL_REQUIRED = "approval_required"
    INCIDENT_COMPLETED = "incident_completed"
    INCIDENT_FAILED = "incident_failed"
    SANDBOX_WARMING = "sandbox_warming"
    BASELINE_COLLECTION_STARTED = "baseline_collection_started"
    BASELINE_COLLECTED = "baseline_collected"
    FAULT_ACTIVATED = "fault_activated"
    WORKLOAD_STARTED = "workload_started"
    TELEMETRY_SOURCE_DEGRADED = "telemetry_source_degraded"
    TELEMETRY_SOURCE_RECOVERED = "telemetry_source_recovered"
    LIVE_EVIDENCE_COLLECTED = "live_evidence_collected"
    INVESTIGATION_BLOCKED = "investigation_blocked"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_SAMPLE = "verification_sample"
    VERIFICATION_PENDING = "verification_pending"
    VERIFICATION_COMPLETED = "verification_completed"
    WORKLOAD_STOPPED = "workload_stopped"


LiveIncidentEventType = InvestigationEventType


class InvestigationEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: InvestigationEventType
    incident_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime
    step: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
