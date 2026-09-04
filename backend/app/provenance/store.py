from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from backend.app.agent.incident_response import (
    IncidentResponseResumeResult,
    IncidentResponseStartResult,
)
from backend.app.config import DeploymentProfile, OpsPilotSettings
from backend.app.live.orchestrator import LiveIncidentSession
from backend.app.persistence.models import ProvenanceRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.provenance.builder import build_live_provenance
from backend.app.provenance.manifest import with_manifest_hash
from backend.app.provenance.models import LiveRunProvenance

Clock = Callable[[], datetime]


def deployment_environment_label(settings: OpsPilotSettings | None) -> str:
    if settings is None:
        return "development"
    if settings.deployment_profile is DeploymentProfile.EPHEMERAL_LIVE_LAB:
        return "Ephemeral Incident Lab"
    if settings.environment.value == "production":
        return "Production"
    return settings.environment.value


class ProvenanceStore:
    """Persist bounded live-run provenance summaries."""

    def __init__(
        self,
        repository: OpsPilotRepository,
        settings: OpsPilotSettings | None = None,
        now: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))

    def load(self, incident_id: str) -> LiveRunProvenance | None:
        record = self._repository.get_provenance(incident_id)
        if record is None:
            return None
        return LiveRunProvenance.model_validate(record.manifest)

    def save_after_investigation(
        self,
        *,
        live_session: LiveIncidentSession,
        started: IncidentResponseStartResult,
        model_provider: str,
        model_name: str | None,
        evidence_count: int,
        primary_model_attempted: str | None = None,
        fallback_used: bool = False,
        fallback_model: str | None = None,
        fallback_reason: str | None = None,
        final_model: str | None = None,
    ) -> LiveRunProvenance:
        provenance = build_live_provenance(
            incident_id=live_session.incident_id,
            environment=deployment_environment_label(self._settings),
            service=live_session.mapping.affected_service,
            service_revision=live_session.current_revision,
            started_at=live_session.created_at,
            baseline_samples=live_session.baseline_samples,
            baseline_summary=live_session.baseline_summary,
            degraded_samples=live_session.post_fault_samples,
            degraded_summary=live_session.degraded_summary,
            diagnosis_provider=model_provider,
            diagnosis_model=model_name,
            evidence_count=evidence_count,
            remediation_action=started.recommended_action,
            approval_required=started.status == "approval_required",
            primary_model_attempted=primary_model_attempted,
            fallback_used=fallback_used,
            fallback_model=fallback_model,
            fallback_reason=fallback_reason,
            final_model=final_model,
        )
        return self._persist(with_manifest_hash(provenance))

    def save_after_resume(
        self,
        *,
        incident_id: str,
        resumed: IncidentResponseResumeResult,
        recovery_result: dict | None = None,
        remediation_at: datetime | None = None,
        approved_at: datetime | None = None,
        executed_at: datetime | None = None,
    ) -> LiveRunProvenance | None:
        existing = self.load(incident_id)
        if existing is None:
            return None
        remediation = (
            existing.remediation.model_copy(
                update={
                    "approved_at": approved_at or existing.remediation.approved_at,
                    "executed_at": executed_at or existing.remediation.executed_at,
                }
            )
            if existing.remediation
            else None
        )
        recovery = None
        if recovery_result is not None:
            from backend.app.provenance.builder import recovery_from_verification

            recovery = recovery_from_verification(
                recovery_result,
                remediation_at=remediation_at,
            )
        elif resumed.recovered_p95_latency_ms is not None:
            from backend.app.provenance.models import RecoveryProvenance

            recovery = RecoveryProvenance(
                sample_count=None,
                p95_latency_ms=resumed.recovered_p95_latency_ms,
                error_rate=resumed.recovered_error_rate_percent,
                verified=resumed.status == "resolved",
                all_samples_post_remediation=True if remediation_at else None,
            )
        updated = existing.model_copy(
            update={
                "remediation": remediation,
                "recovery": recovery,
            }
        )
        return self._persist(with_manifest_hash(updated))

    def _persist(self, provenance: LiveRunProvenance) -> LiveRunProvenance:
        manifest = provenance.model_dump(mode="json")
        self._repository.save_provenance(
            ProvenanceRecord(
                incident_id=provenance.incident_id,
                manifest=manifest,
                evidence_manifest_hash=provenance.evidence_manifest_hash or "",
                updated_at=self._now(),
            )
        )
        return provenance
