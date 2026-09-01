#!/usr/bin/env python3
"""Local live sandbox validation for all three incident classes."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from backend.app.live.orchestrator import LiveIncidentOrchestrator
from backend.app.telemetry.clients import LokiClient, LokiConfig, PrometheusClient, PrometheusConfig
from backend.app.telemetry.pipeline_health import wait_for_loki_ready
from sandbox.scenarios import (
    AUTH_TOKEN_VALIDATION_REGRESSION_ID,
    CHECKOUT_DB_POOL_REGRESSION_ID,
    PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID,
    get_live_scenario_mapping,
)


def _require_env() -> None:
    required = [
        "OPSPILOT_PROMETHEUS_URL",
        "OPSPILOT_LOKI_URL",
        "SANDBOX_CONTROL_TOKEN",
    ]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)


def _print_logs(logs: list) -> None:
    for entry in logs[:5]:
        if hasattr(entry, "model_dump"):
            payload = entry.model_dump()
        elif isinstance(entry, dict):
            payload = entry
        else:
            payload = {
                "timestamp": getattr(entry, "timestamp", None),
                "level": getattr(entry, "level", None),
                "message": getattr(entry, "message", None),
                "revision": getattr(entry, "revision", None),
            }
        print(
            "  log:",
            json.dumps(
                {
                    "timestamp": payload.get("timestamp"),
                    "level": payload.get("level"),
                    "message": payload.get("message"),
                    "revision": payload.get("revision"),
                },
                default=str,
            ),
        )


def validate_scenario(scenario_id: str) -> dict:
    mapping = get_live_scenario_mapping(scenario_id)
    orchestrator = LiveIncidentOrchestrator()
    incident_id = f"validate-{scenario_id}"
    print(f"\n=== {scenario_id} ===")
    print(f"healthy_revision: {mapping.healthy_revision}")
    print(f"faulty_revision: {mapping.faulty_revision}")

    session = orchestrator.prepare(incident_id=incident_id, scenario_id=scenario_id)
    print(f"measured_baseline: {session.baseline_summary}")
    print(f"faulty_revision_active: {session.current_revision}")
    print(f"measured_degraded: {session.degraded_summary}")
    print(f"telemetry_source_states: {session.telemetry_source_states}")
    _print_logs(session.observed_logs)

    rollback_status = orchestrator.rollback(session, mapping.faulty_revision)
    print(f"rollback_result: {rollback_status}")
    print(f"post_remediation_revision: {rollback_status.get('current_revision')}")

    verification = orchestrator.verify_recovery(session)
    print(f"verification: {verification}")
    orchestrator.cleanup(session)
    return {
        "scenario_id": scenario_id,
        "baseline": session.baseline_summary,
        "degraded": session.degraded_summary,
        "telemetry_source_states": session.telemetry_source_states,
        "verification": verification,
    }


def main() -> None:
    _require_env()
    os.environ.setdefault("OPSPILOT_MODEL_PROVIDER", "deterministic")

    prometheus = PrometheusClient(
        PrometheusConfig(base_url=os.environ["OPSPILOT_PROMETHEUS_URL"])
    )
    loki = LokiClient(LokiConfig(base_url=os.environ["OPSPILOT_LOKI_URL"]))
    print("Prometheus ready:", prometheus.is_ready())
    print("Loki API ready:", wait_for_loki_ready(loki))

    results = [
        validate_scenario(CHECKOUT_DB_POOL_REGRESSION_ID),
        validate_scenario(AUTH_TOKEN_VALIDATION_REGRESSION_ID),
        validate_scenario(PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID),
    ]
    print("\nValidation complete.")
    for item in results:
        status = item["verification"].get("status")
        recovered = item["verification"].get("recovered")
        print(f"- {item['scenario_id']}: status={status} recovered={recovered}")


if __name__ == "__main__":
    main()
