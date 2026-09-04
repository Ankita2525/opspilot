from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TelemetryModeValue = Literal["live", "reference"]


class TelemetryWindow(BaseModel):
    """Aggregate telemetry window derived from actual runtime samples."""

    model_config = ConfigDict(frozen=True)

    sample_count: int = Field(ge=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    error_rate: float | None = None


class DiagnosisProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str | None = None
    evidence_count: int = Field(ge=0)
    generated_at: datetime | None = None
    primary_model_attempted: str | None = None
    fallback_used: bool = False
    fallback_model: str | None = None
    fallback_reason: str | None = None
    final_model: str | None = None


class RemediationProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    typed_action: str | None = None
    approval_required: bool = False
    approved_at: datetime | None = None
    executed_at: datetime | None = None


class RecoveryProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_count: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    error_rate: float | None = None
    latest_metric_timestamp: datetime | None = None
    latest_log_timestamp: datetime | None = None
    all_samples_post_remediation: bool | None = None
    verified: bool | None = None


class LiveRunProvenance(BaseModel):
    """Bounded summary proving displayed values came from this actual run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    incident_id: str
    telemetry_mode: TelemetryModeValue
    environment: str
    service: str
    service_revision: str | None = None
    started_at: datetime
    baseline: TelemetryWindow | None = None
    degraded: TelemetryWindow | None = None
    diagnosis: DiagnosisProvenance | None = None
    remediation: RemediationProvenance | None = None
    recovery: RecoveryProvenance | None = None
    ground_truth_visible_to_agent: bool = False
    evidence_manifest_hash: str | None = None
