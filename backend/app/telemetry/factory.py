from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from backend.app.config import OpsPilotSettings
from backend.app.telemetry.live import LiveTelemetryBackend
from backend.app.telemetry.models import TelemetryMode
from backend.app.telemetry.simulator import SimulatorTelemetryBackend
from backend.app.telemetry.simulator_remediation import SimulatorRemediationBackend
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from backend.app.safety.approvals import ApprovalService
from simulator.environment import SimulatedEnvironment


@dataclass(frozen=True)
class IncidentRuntime:
    diagnostics: DiagnosticTools
    remediation: RemediationTools
    simulator_environment: SimulatedEnvironment | None = None
    telemetry_mode: str = "reference"


def build_reference_runtime(
    scenario_id: str,
    approvals: ApprovalService,
) -> IncidentRuntime:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    telemetry = SimulatorTelemetryBackend(environment)
    remediation_backend = SimulatorRemediationBackend(environment)
    return IncidentRuntime(
        diagnostics=DiagnosticTools(telemetry),
        remediation=RemediationTools(remediation_backend, approvals),
        simulator_environment=environment,
        telemetry_mode="reference",
    )


def build_live_runtime(
    *,
    service: str,
    telemetry: LiveTelemetryBackend,
    remediation_backend,
    approvals: ApprovalService,
) -> IncidentRuntime:
    return IncidentRuntime(
        diagnostics=DiagnosticTools(telemetry),
        remediation=RemediationTools(remediation_backend, approvals),
        simulator_environment=None,
        telemetry_mode="live",
    )
