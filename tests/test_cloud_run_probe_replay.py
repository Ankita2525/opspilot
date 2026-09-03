"""Probe-replay / readiness contract tests that would have caught revision 00003."""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path

import httpx
from httpx import ASGITransport

from backend.app.api.app import create_app
from backend.app.config import OpsPilotSettings
from backend.app.readiness import clear_readiness_cache
from tests.fakes import FakeModelProvider

ROOT = Path(__file__).resolve().parents[1]


def _live_settings_unroutable() -> OpsPilotSettings:
    return OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TELEMETRY_MODE": "live",
            # Unroutable / closed ports — dependency failures without real egress.
            # Intentionally no DATABASE_URL: app lifespan must still start; DB
            # preflight remains the deploy-time gate, not /health.
            "OPSPILOT_PROMETHEUS_URL": "http://127.0.0.1:9",
            "OPSPILOT_LOKI_URL": "http://127.0.0.1:9",
            "SANDBOX_CONTROL_TOKEN": "probe-replay-token",
            "CHECKOUT_API_URL": "http://127.0.0.1:9",
        }
    )


def setup_function() -> None:
    clear_readiness_cache()


def teardown_function() -> None:
    clear_readiness_cache()


def test_readiness_module_has_no_blocking_urllib_or_requests() -> None:
    source = (ROOT / "backend" / "app" / "readiness.py").read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "requests." not in source
    assert "import requests" not in source
    assert "httpx" in source
    assert "/loki/api/v1/labels" in source
    assert inspect.iscoroutinefunction(
        __import__("backend.app.readiness", fromlist=["assess_readiness"]).assess_readiness
    )


def test_health_isolated_from_dependency_failures() -> None:
    async def _run() -> None:
        app = create_app(provider=FakeModelProvider(), settings=_live_settings_unroutable())
        transport = ASGITransport(app=app)
        latencies: list[float] = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(20):
                started = time.perf_counter()
                response = await asyncio.wait_for(client.get("/health"), timeout=1.0)
                latencies.append((time.perf_counter() - started) * 1000)
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
        ordered = sorted(latencies)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        assert p95 < 500.0, f"health p95={p95:.1f}ms latencies={ordered}"

    asyncio.run(_run())


def test_ready_degrades_without_hanging() -> None:
    async def _run() -> None:
        app = create_app(provider=FakeModelProvider(), settings=_live_settings_unroutable())
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = time.perf_counter()
            response = await asyncio.wait_for(client.get("/ready"), timeout=6.0)
            elapsed = time.perf_counter() - started
            assert response.status_code == 200
            payload = response.json()
            assert payload["degraded"] is True
            assert payload["status"] == "degraded"
            assert "checks" in payload
            assert elapsed < 5.5
            blob = str(payload)
            assert "opspilot:opspilot@" not in blob
            assert "probe-replay-token" not in blob

    asyncio.run(_run())


def test_concurrent_ready_does_not_starve_health() -> None:
    async def _run() -> None:
        app = create_app(provider=FakeModelProvider(), settings=_live_settings_unroutable())
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def hammer_ready() -> None:
                await client.get("/ready")

            ready_tasks = [asyncio.create_task(hammer_ready()) for _ in range(4)]
            await asyncio.sleep(0.05)
            health_latencies: list[float] = []
            for _ in range(8):
                started = time.perf_counter()
                response = await client.get("/health")
                health_latencies.append((time.perf_counter() - started) * 1000)
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
            await asyncio.gather(*ready_tasks)
        assert max(health_latencies) < 200.0, health_latencies

    asyncio.run(_run())
