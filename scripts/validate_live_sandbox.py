#!/usr/bin/env python3
"""Local live sandbox validation for all three incident classes."""

from __future__ import annotations

import os
import sys

from backend.app.live.orchestrator import LiveIncidentOrchestrator
from sandbox.scenarios import (
    AUTH_TOKEN_VALIDATION_REGRESSION_ID,
    CHECKOUT_DB_POOL_REGRESSION_ID,
    PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID,
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


def validate_scenario(scenario_id: str) -> dict:
    orchestrator = LiveIncidentOrchestrator()
    incident_id = f"validate-{scenario_id}"
    session = orchestrator.prepare(incident_id=incident_id, scenario_id=scenario_id)
    degraded = session.workload.summarize_samples(session.post_fault_samples)
    baseline = session.baseline_summary
    print(f"\n=== {scenario_id} ===")
    print(f"baseline: {baseline}")
    print(f"degraded: {degraded}")
    if degraded["p95_latency_ms"] <= baseline.get("p95_latency_ms", 0):
        print("WARN: degraded latency did not exceed baseline")
    session.control.rollback(session.mapping.faulty_revision)
    verification = orchestrator.verify_recovery(session)
    print(f"verification: {verification}")
    orchestrator.cleanup(session)
    return {
        "scenario_id": scenario_id,
        "baseline": baseline,
        "degraded": degraded,
        "verification": verification,
    }


def main() -> None:
    _require_env()
    os.environ.setdefault("OPSPILOT_MODEL_PROVIDER", "deterministic")
    results = [
        validate_scenario(CHECKOUT_DB_POOL_REGRESSION_ID),
        validate_scenario(AUTH_TOKEN_VALIDATION_REGRESSION_ID),
        validate_scenario(PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID),
    ]
    print("\nValidation complete.")
    for item in results:
        recovered = item["verification"].get("recovered")
        print(f"- {item['scenario_id']}: recovered={recovered}")


if __name__ == "__main__":
    main()
