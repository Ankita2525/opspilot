#!/usr/bin/env python3
"""Pre-deploy probe-replay gate for Cloud Run lifecycle vs deep readiness.

Catches the revision 00003 failure class:
  Cloud Run startupProbe must hit process-local /healthz, not deep /ready.
  /ready must degrade under dependency failures without hanging or starving /healthz.

This script is intentionally runnable without Neon/Grafana/Prometheus.

Exit 0 on success.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from httpx import ASGITransport

from backend.app.api.app import create_app
from backend.app.config import OpsPilotSettings
from backend.app.models.deterministic_provider import DeterministicModelProvider
from backend.app.readiness import clear_readiness_cache


def _settings() -> OpsPilotSettings:
    return OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TELEMETRY_MODE": "live",
            "OPSPILOT_PROMETHEUS_URL": "http://127.0.0.1:9",
            "OPSPILOT_LOKI_URL": "http://127.0.0.1:9",
            "SANDBOX_CONTROL_TOKEN": "preflight-probe-token",
            "CHECKOUT_API_URL": "http://127.0.0.1:9",
        }
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


async def _run() -> None:
    clear_readiness_cache()
    app = create_app(provider=DeterministicModelProvider(), settings=_settings())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health_latencies: list[float] = []
        for _ in range(25):
            started = time.perf_counter()
            response = await asyncio.wait_for(client.get("/healthz"), timeout=1.0)
            health_latencies.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200 or response.json() != {"status": "ok"}:
                raise SystemExit(f"/healthz failed: {response.status_code} {response.text}")

        health_p95 = _p95(health_latencies)
        if health_p95 >= 500.0:
            raise SystemExit(f"/healthz p95 too high: {health_p95:.1f}ms")

        started = time.perf_counter()
        ready = await asyncio.wait_for(client.get("/ready"), timeout=6.0)
        ready_elapsed = time.perf_counter() - started
        payload = ready.json()
        if ready.status_code != 200:
            raise SystemExit(f"/ready status {ready.status_code}")
        if not payload.get("degraded"):
            raise SystemExit(f"/ready expected degraded with dead deps: {payload}")
        if ready_elapsed >= 5.5:
            raise SystemExit(f"/ready too slow: {ready_elapsed:.2f}s")

        ready_tasks = [asyncio.create_task(client.get("/ready")) for _ in range(4)]
        await asyncio.sleep(0.05)
        concurrent_health: list[float] = []
        for _ in range(10):
            started = time.perf_counter()
            response = await client.get("/healthz")
            concurrent_health.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                raise SystemExit("/healthz failed under concurrent /ready")
        await asyncio.gather(*ready_tasks)
        max_health = max(concurrent_health)
        if max_health >= 200.0:
            raise SystemExit(f"/healthz starved under /ready load: max={max_health:.1f}ms")

    print("probe_replay_ok")
    print(f"healthz_p95_ms={health_p95:.1f}")
    print(f"ready_degraded_s={ready_elapsed:.2f}")
    print(f"healthz_under_load_max_ms={max_health:.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — preflight reports failure clearly
        print(f"probe_replay_failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
