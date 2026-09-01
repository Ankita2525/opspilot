from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.events.models import LiveIncidentEventType
from backend.app.telemetry.clients import LokiClient, LokiConfig, PrometheusClient, PrometheusConfig
from backend.app.telemetry.evidence import EvidenceReadiness, assess_readiness
from backend.app.telemetry.live import LiveTelemetryBackend
from backend.app.telemetry.sandbox_remediation import SandboxRemediationBackend
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
    current_revision: str | None = None
    evidence_readiness: EvidenceReadiness | None = None
    blocked: bool = False
    blocked_reason: str | None = None
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
    ) -> None:
        self._workload = workload_driver or WorkloadDriver()
        self._warmup_seconds = warmup_seconds
        self._baseline_seconds = baseline_seconds
        self._observation_seconds = observation_seconds

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
        loki = LokiClient(
            LokiConfig(
                base_url=os.environ.get(
                    "OPSPILOT_LOKI_URL",
                    "http://localhost:3100",
                )
            )
        )
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
            readiness = assess_readiness(telemetry.source_health())
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
                    {"partial_evidence": readiness.partial_evidence},
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
        duration = observation_seconds or self._observation_seconds
        self._emit(
            events,
            LiveIncidentEventType.VERIFICATION_STARTED,
            "Collecting post-remediation verification telemetry.",
        )
        samples: list[WorkloadSample] = []
        for _ in range(2):
            batch = session.workload.collect_baseline(
                session.mapping,
                session.incident_id,
                duration_seconds=duration / 2,
            )
            samples.extend(batch)
            summary = session.workload.summarize_samples(batch)
            self._emit(
                events,
                LiveIncidentEventType.VERIFICATION_SAMPLE,
                "Recorded verification sample.",
                summary,
            )
        session.verification_samples = samples
        summary = session.workload.summarize_samples(samples)
        baseline = session.baseline_summary
        recovered = (
            summary["error_rate_percent"] <= baseline.get("error_rate_percent", 100) + 1.0
            and summary["p95_latency_ms"] <= baseline.get("p95_latency_ms", 10_000) * 2
        )
        readiness = assess_readiness(session.telemetry.source_health())
        if not readiness.can_verify_recovery():
            self._emit(
                events,
                LiveIncidentEventType.VERIFICATION_PENDING,
                "Remediation executed but telemetry verification is unavailable.",
            )
            return {
                "status": "verification_pending",
                "summary": summary,
                "recovered": False,
            }
        status = "resolved" if recovered else "remediation_failed"
        self._emit(
            events,
            LiveIncidentEventType.VERIFICATION_COMPLETED,
            "Verification window completed.",
            {"status": status, **summary},
        )
        return {
            "status": status,
            "summary": summary,
            "recovered": recovered,
            "recovered_p95_latency_ms": summary["p95_latency_ms"],
            "recovered_error_rate_percent": summary["error_rate_percent"],
        }

    def cleanup(self, session: LiveIncidentSession) -> None:
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
