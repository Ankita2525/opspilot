#!/usr/bin/env python3
"""Validate local Cloud Run topology without GCP credentials."""

from __future__ import annotations

import os
import sys
import time

import httpx

API_BASE = os.environ.get("OPSPILOT_API_BASE", "http://localhost:8000")
SCENARIOS = (
    "checkout-db-pool-regression",
    "auth-token-validation-regression",
    "payments-provider-timeout-regression",
)


def wait_ready(client: httpx.Client, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{API_BASE}/ready", timeout=5.0)
            if response.status_code == 200:
                payload = response.json()
                if payload.get("status") in {"ready", "degraded"}:
                    return
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    raise RuntimeError("OpsPilot /ready did not become ready in time")


def main() -> int:
    with httpx.Client(timeout=30.0) as client:
        wait_ready(client)
        runtime = client.get(f"{API_BASE}/api/runtime").json()
        if runtime.get("telemetry_mode") != "live":
            print("SKIP: telemetry_mode is not live", file=sys.stderr)
            return 0
        for scenario_id in SCENARIOS:
            response = client.post(
                f"{API_BASE}/api/incidents/start",
                json={"scenario_id": scenario_id},
            )
            if response.status_code != 200:
                print(f"FAIL {scenario_id}: {response.status_code} {response.text}")
                return 1
            body = response.json()
            print(f"OK {scenario_id}: status={body.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
