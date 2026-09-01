from __future__ import annotations

import pytest

from backend.app.config import OpsPilotSettings
from backend.app.telemetry.models import TelemetryMode, TelemetrySourceKind, TelemetrySourceStatus
from backend.app.telemetry.evidence import assess_readiness, is_stale
from backend.app.telemetry.health import unavailable_source, utc_now
from backend.app.telemetry.simulator import SimulatorTelemetryBackend
from backend.app.tools.diagnostics import DiagnosticTools
from simulator.environment import SimulatedEnvironment


def test_reference_mode_uses_simulator_backend() -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario("checkout-db-pool-regression")
    tools = DiagnosticTools(environment)
    assert tools.telemetry_mode == "reference"


def test_live_mode_config_requires_observability_endpoints() -> None:
    with pytest.raises(Exception, match="OPSPILOT_PROMETHEUS_URL"):
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "deterministic",
                "OPSPILOT_TELEMETRY_MODE": "live",
                "OPSPILOT_LOKI_URL": "http://localhost:3100",
                "SANDBOX_CONTROL_TOKEN": "token",
            }
        )


def test_invalid_live_configuration_fails_closed() -> None:
    with pytest.raises(Exception, match="OPSPILOT_TELEMETRY_MODE"):
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "deterministic",
                "OPSPILOT_TELEMETRY_MODE": "invalid",
            }
        )


def test_stale_data_marked_stale() -> None:
    observed = utc_now()
    from datetime import timedelta

    assert is_stale(observed - timedelta(seconds=300))


def test_unavailable_source_blocks_when_all_critical_missing() -> None:
    health = [
        unavailable_source(TelemetrySourceKind.METRICS, error_category="down"),
        unavailable_source(TelemetrySourceKind.LOGS, error_category="down"),
        unavailable_source(TelemetrySourceKind.DEPLOYMENTS, error_category="down"),
    ]
    readiness = assess_readiness(health)
    assert readiness.blocked is True


def test_partial_evidence_allows_investigation_when_logs_and_deployments_available() -> None:
    from backend.app.telemetry.health import healthy_source

    health = [
        unavailable_source(TelemetrySourceKind.METRICS, error_category="down"),
        healthy_source(TelemetrySourceKind.LOGS),
        healthy_source(TelemetrySourceKind.DEPLOYMENTS),
    ]
    readiness = assess_readiness(health, require_metrics=False)
    assert readiness.blocked is False
    assert readiness.metrics_available is False
    assert readiness.logs_available is True


def test_simulator_backend_never_reports_live_mode() -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario("auth-token-validation-regression")
    backend = SimulatorTelemetryBackend(environment)
    assert backend.mode == "reference"
