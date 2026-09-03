from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.events.models import LiveIncidentEventType
from backend.app.telemetry.clients import (
    LokiClient,
    PrometheusClient,
    PrometheusConfig,
    loki_config_from_environ,
)
from backend.app.telemetry.evidence import EvidenceReadiness, assess_readiness
from backend.app.telemetry.live import LiveTelemetryBackend
from backend.app.telemetry.pipeline_health import (
    check_metrics_pipeline,
    verify_log_ingestion,
    wait_for_loki_ready,
)
from backend.app.telemetry.sandbox_remediation import SandboxRemediationBackend
from backend.app.telemetry.verification import RecoveryVerifier
from sandbox.control import SandboxControlClient
from sandbox.scenarios import LiveScenarioMapping, get_live_scenario_mapping
from sandbox.traffic.workload import WorkloadDriver, WorkloadSample


@dataclass
class LiveIncidentSession:
    incident_id: str
    scenario_id: str
    mapping: LiveScenarioMapping
    control: SandboxControlClient
    telemetry: LiveTelemetryBackend
    remediation: SandboxRemediationBackend
    workload: WorkloadDriver
    baseline_samples: list[WorkloadSample] = field(default_factory=list)
    post_fault_samples: list[WorkloadSample] = field(default_factory=list)
    verification_samples: list[WorkloadSample] = field(default_factory=list)
    baseline_summary: dict[str, Any] = field(default_factory=dict)
    degraded_summary: dict[str, Any] = field(default_factory=dict)
    current_revision: str | None = None
    evidence_readiness: EvidenceReadiness | None = None
    telemetry_source_states: dict[str, str] = field(default_factory=dict)
    observed_logs: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str | None = None
    remediation_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class LiveIncidentOrchestrator:
    """Prepares a live sandbox incident before OpsPilot investigation."""

    def __init__(
        self,
        *,
        workload_driver: WorkloadDriver | None = None,
        warmup_seconds: float = 2.0,
        baseline_seconds: float = 3.0,
        observation_seconds: float = 4.0,
        recovery_verifier: RecoveryVerifier | None = None,
    ) -> None:
        self._workload = workload_driver or WorkloadDriver()
        self._warmup_seconds = warmup_seconds
        self._baseline_seconds = baseline_seconds
        self._observation_seconds = observation_seconds
        self._recovery_verifier = recovery_verifier or RecoveryVerifier()

    def _build_clients(
        self, mapping: LiveScenarioMapping
    ) -> tuple[SandboxControlClient, LiveTelemetryBackend, SandboxRemediationBackend]:
        control = SandboxControlClient.from_mapping(mapping)
        prometheus = PrometheusClient(
            PrometheusConfig(
                base_url=os.environ.get(
                    "OPSPILOT_PROMETHEUS_URL",
                    "http://localhost:9090",
                )
            )
        )
        loki = LokiClient(loki_config_from_environ())
        telemetry = LiveTelemetryBackend(
            service=mapping.affected_service,
            prometheus=prometheus,
            loki=loki,
            control=control,
        )
        remediation = SandboxRemediationBackend(control)
        return control, telemetry, remediation

    def prepare(
        self,
        *,
        incident_id: str,
        scenario_id: str,
        events: InvestigationEventEmitter | None = None,
    ) -> LiveIncidentSession:
        mapping = get_live_scenario_mapping(scenario_id)
        if self._workload.is_sandbox_busy(mapping.affected_service, incident_id):
            raise RuntimeError(
                f"Sandbox service {mapping.affected_service} is busy with another session."
            )
        if not self._workload.acquire_lease(mapping.affected_service, incident_id):
            raise RuntimeError(
                f"Unable to acquire sandbox lease for {mapping.affected_service}."
            )
        control, telemetry, remediation = self._build_clients(mapping)
        session = LiveIncidentSession(
            incident_id=incident_id,
            scenario_id=scenario_id,
            mapping=mapping,
            control=control,
            telemetry=telemetry,
            remediation=remediation,
            workload=self._workload,
        )
        try:
            self._emit(events, LiveIncidentEventType.SANDBOX_WARMING, "Warming sandbox service.")
            self._workload.warm_service(mapping, incident_id)
            self._emit(
                events,
                LiveIncidentEventType.BASELINE_COLLECTION_STARTED,
                "Collecting healthy baseline telemetry.",
            )
            session.baseline_samples = self._workload.collect_baseline(
                mapping,
                incident_id,
                duration_seconds=self._baseline_seconds,
            )
            session.baseline_summary = self._workload.summarize_samples(
                session.baseline_samples
            )
            self._emit(
                events,
                LiveIncidentEventType.BASELINE_COLLECTED,
                "Baseline telemetry collected.",
                {"baseline": session.baseline_summary},
            )
            control.activate_fault()
            session.current_revision = mapping.faulty_revision
            self._emit(
                events,
                LiveIncidentEventType.FAULT_ACTIVATED,
                "Activated faulty sandbox revision.",
                {"revision": mapping.faulty_revision},
            )
            self._workload.start_continuous(mapping, incident_id)
            self._emit(
                events,
                LiveIncidentEventType.WORKLOAD_STARTED,
                "Started on-demand workload.",
            )
            session.post_fault_samples = self._workload.collect_baseline(
                mapping,
                incident_id,
                duration_seconds=self._observation_seconds,
            )
            session.degraded_summary = self._workload.summarize_samples(
                session.post_fault_samples
            )
            session.telemetry_source_states = telemetry.refresh_pipeline_health()
            try:
                session.observed_logs = telemetry.get_service_logs(mapping.affected_service)
            except Exception:
                session.observed_logs = []
            readiness = assess_readiness(
                telemetry.source_health(),
                require_metrics=False,
                require_logs=False,
            )
            session.evidence_readiness = readiness
            if readiness.blocked:
                session.blocked = True
                session.blocked_reason = readiness.blocked_reason
                self._emit(
                    events,
                    LiveIncidentEventType.INVESTIGATION_BLOCKED,
                    readiness.blocked_reason or "Investigation blocked by telemetry.",
                )
            else:
                self._emit(
                    events,
                    LiveIncidentEventType.LIVE_EVIDENCE_COLLECTED,
                    "Collected live incident evidence.",
                    {
                        "partial_evidence": readiness.partial_evidence,
                        "telemetry_source_states": session.telemetry_source_states,
                    },
                )
        except Exception:
            self.cleanup(session)
            raise
        return session

    def verify_recovery(
        self,
        session: LiveIncidentSession,
        *,
        events: InvestigationEventEmitter | None = None,
        observation_seconds: float | None = None,
    ) -> dict[str, Any]:
        remediation_at = session.remediation_at or datetime.now(UTC)
        self._emit(
            events,
            LiveIncidentEventType.VERIFICATION_STARTED,
            "Collecting post-remediation verification telemetry.",
            {"remediation_at": remediation_at.isoformat()},
        )
        result = self._recovery_verifier.verify(
            prometheus=session.telemetry.prometheus_client,
            workload=session.workload,
            mapping=session.mapping,
            incident_id=session.incident_id,
            baseline_summary=session.baseline_summary,
            remediation_at=remediation_at,
            sample_duration_seconds=observation_seconds or self._observation_seconds,
        )
        session.telemetry_source_states = session.telemetry.refresh_pipeline_health()
        for observation in result.get("observations", []):
            self._emit(
                events,
                LiveIncidentEventType.VERIFICATION_SAMPLE,
                "Recorded verification sample.",
                observation,
            )
        if result["status"] == "verification_pending":
            self._emit(
                events,
                LiveIncidentEventType.VERIFICATION_PENDING,
                result.get("reason", "Remediation executed but telemetry verification is unavailable."),
            )
            return result
        self._emit(
            events,
            LiveIncidentEventType.VERIFICATION_COMPLETED,
            "Verification window completed.",
            result,
        )
        return result

    def rollback(self, session: LiveIncidentSession, version: str) -> dict[str, Any]:
        session.remediation_at = datetime.now(UTC)
        status = session.control.rollback(version)
        session.current_revision = status.get("current_revision")
        return status

    def cleanup(self, session: LiveIncidentSession) -> None:
        # Explicit rollback is the fast path; sidecar TTL remains the safety net.
        try:
            if session.mapping is not None:
                session.control.clear_fault()
                session.current_revision = session.mapping.healthy_revision
        except Exception:
            # Leave TTL to self-revert; still stop workload/lease below.
            pass
        session.workload.stop(session.incident_id)
        session.workload.release_lease(
            session.mapping.affected_service,
            session.incident_id,
        )

    def _emit(
        self,
        events: InvestigationEventEmitter | None,
        event_type: LiveIncidentEventType,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if events is None:
            return
        events.emit_live(event_type, message=message, data=data)
