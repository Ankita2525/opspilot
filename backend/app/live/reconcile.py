"""Live session reconciliation after Cloud Run scale-to-zero or instance replacement."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from backend.app.live.orchestrator import LiveIncidentOrchestrator, LiveIncidentSession
from backend.app.provenance.models import LiveRunProvenance
from backend.app.telemetry.clients import LokiClient, LokiConfig, PrometheusClient, PrometheusConfig
from backend.app.telemetry.live import LiveTelemetryBackend
from backend.app.telemetry.sandbox_remediation import SandboxRemediationBackend
from sandbox.control import SandboxControlClient
from sandbox.scenarios import LiveScenarioMapping, get_live_scenario_mapping
from sandbox.traffic.workload import WorkloadDriver


class LiveSessionReconciler:
    """Rebuild an in-memory live session from durable provenance + sandbox state."""

    def __init__(
        self,
        *,
        workload_driver: WorkloadDriver | None = None,
        orchestrator: LiveIncidentOrchestrator | None = None,
    ) -> None:
        self._workload = workload_driver or WorkloadDriver()
        self._orchestrator = orchestrator or LiveIncidentOrchestrator(
            workload_driver=self._workload
        )

    def reconcile_for_approval(
        self,
        *,
        incident_id: str,
        scenario_id: str,
        provenance: LiveRunProvenance,
    ) -> LiveIncidentSession:
        mapping = get_live_scenario_mapping(scenario_id)
        control, telemetry, remediation = self._build_clients(mapping)
        revision = self._ensure_fault_active(control, mapping, provenance)
        if not self._workload.acquire_lease(mapping.affected_service, incident_id):
            raise RuntimeError(
                f"Unable to reacquire workload lease for {mapping.affected_service}."
            )
        session = LiveIncidentSession(
            incident_id=incident_id,
            scenario_id=scenario_id,
            mapping=mapping,
            control=control,
            telemetry=telemetry,
            remediation=remediation,
            workload=self._workload,
            current_revision=revision,
            created_at=provenance.started_at,
        )
        session.baseline_summary = self._summary_from_window(provenance.baseline)
        session.degraded_summary = self._summary_from_window(provenance.degraded)
        self._workload.start_continuous(mapping, incident_id)
        return session

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

    def _ensure_fault_active(
        self,
        control: SandboxControlClient,
        mapping: LiveScenarioMapping,
        provenance: LiveRunProvenance,
    ) -> str:
        status = control.get_revision()
        current = status.get("current_revision")
        expected_faulty = provenance.service_revision or mapping.faulty_revision
        if current != expected_faulty:
            control.activate_fault()
            status = control.get_revision()
            current = status.get("current_revision")
        if current != mapping.faulty_revision:
            raise RuntimeError(
                "Sandbox baseline could not be reconciled to expected faulty revision."
            )
        return str(current)

    def _summary_from_window(self, window: Any) -> dict[str, Any]:
        if window is None:
            return {}
        return {
            "request_count": window.sample_count,
            "p95_latency_ms": window.p95_latency_ms or 0,
            "error_rate_percent": window.error_rate or 0.0,
            "newest_sample_at": (
                window.window_end.isoformat() if window.window_end else None
            ),
        }
