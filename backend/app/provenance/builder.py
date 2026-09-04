from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.provenance.models import (
    DiagnosisProvenance,
    LiveRunProvenance,
    RecoveryProvenance,
    RemediationProvenance,
    TelemetryWindow,
)
from sandbox.traffic.workload import WorkloadSample


def _percentile(latencies: list[float], percentile: float) -> int | None:
    if not latencies:
        return None
    ordered = sorted(latencies)
    index = max(0, int(len(ordered) * percentile) - 1)
    return int(ordered[index])


def window_from_samples(
    samples: list[WorkloadSample],
    summary: dict[str, Any] | None = None,
) -> TelemetryWindow | None:
    if not samples:
        return None
    summary = summary or {}
    count = summary.get("request_count", len(samples))
    if not count:
        return None
    latencies = [sample.latency_ms for sample in samples]
    failures = sum(1 for sample in samples if not sample.success)
    window_start = min(sample.timestamp for sample in samples)
    window_end = max(sample.timestamp for sample in samples)
    p95 = summary.get("p95_latency_ms")
    if p95 is None:
        p95 = _percentile(latencies, 0.95)
    error_rate = summary.get("error_rate_percent")
    if error_rate is None and samples:
        error_rate = round((failures / len(samples)) * 100, 2)
    return TelemetryWindow(
        sample_count=int(count),
        window_start=window_start,
        window_end=window_end,
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=int(p95) if p95 is not None else None,
        error_rate=float(error_rate) if error_rate is not None else None,
    )


def recovery_from_verification(
    result: dict[str, Any],
    *,
    remediation_at: datetime | None,
) -> RecoveryProvenance | None:
    summary = result.get("summary") or {}
    count = summary.get("request_count")
    if not count:
        return None
    observations = result.get("observations") or []
    all_post = True
    if remediation_at is not None and observations:
        for observation in observations:
            workload = observation.get("workload") or {}
            newest = workload.get("newest_sample_at")
            if newest is None:
                all_post = False
                break
            newest_at = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
            if newest_at <= remediation_at:
                all_post = False
                break
    prometheus = result.get("prometheus") or {}
    latest_metric = prometheus.get("observed_at")
    return RecoveryProvenance(
        sample_count=int(count),
        window_start=None,
        window_end=parse_iso(summary.get("newest_sample_at")),
        p95_latency_ms=summary.get("p95_latency_ms"),
        error_rate=summary.get("error_rate_percent"),
        latest_metric_timestamp=parse_iso(latest_metric),
        latest_log_timestamp=None,
        all_samples_post_remediation=all_post if remediation_at else None,
        verified=result.get("status") == "resolved",
    )


def parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def build_live_provenance(
    *,
    incident_id: str,
    environment: str,
    service: str,
    service_revision: str | None,
    started_at: datetime,
    baseline_samples: list[WorkloadSample],
    baseline_summary: dict[str, Any],
    degraded_samples: list[WorkloadSample],
    degraded_summary: dict[str, Any],
    diagnosis_provider: str,
    diagnosis_model: str | None,
    evidence_count: int,
    diagnosis_generated_at: datetime | None = None,
    remediation_action: str | None = None,
    approval_required: bool = False,
    approved_at: datetime | None = None,
    executed_at: datetime | None = None,
    recovery_result: dict[str, Any] | None = None,
    remediation_at: datetime | None = None,
    primary_model_attempted: str | None = None,
    fallback_used: bool = False,
    fallback_model: str | None = None,
    fallback_reason: str | None = None,
    final_model: str | None = None,
) -> LiveRunProvenance:
    return LiveRunProvenance(
        run_id=incident_id,
        incident_id=incident_id,
        telemetry_mode="live",
        environment=environment,
        service=service,
        service_revision=service_revision,
        started_at=started_at,
        baseline=window_from_samples(baseline_samples, baseline_summary),
        degraded=window_from_samples(degraded_samples, degraded_summary),
        diagnosis=DiagnosisProvenance(
            provider=diagnosis_provider,
            model=final_model or diagnosis_model,
            evidence_count=evidence_count,
            generated_at=diagnosis_generated_at or datetime.now(UTC),
            primary_model_attempted=primary_model_attempted or diagnosis_model,
            fallback_used=fallback_used,
            fallback_model=fallback_model,
            fallback_reason=fallback_reason,
            final_model=final_model or diagnosis_model,
        ),
        remediation=RemediationProvenance(
            typed_action=remediation_action,
            approval_required=approval_required,
            approved_at=approved_at,
            executed_at=executed_at,
        ),
        recovery=(
            recovery_from_verification(recovery_result, remediation_at=remediation_at)
            if recovery_result
            else None
        ),
        ground_truth_visible_to_agent=False,
    )
