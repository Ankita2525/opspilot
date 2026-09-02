from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from backend.app.telemetry.clients import LokiClient, LokiConfig, PrometheusClient, PrometheusConfig, _parse_loki_entry
from backend.app.telemetry.live import LiveTelemetryBackend
from backend.app.telemetry.verification import (
    RecoveryVerifier,
    filter_samples_after,
    meets_baseline_recovery,
    summarize_samples,
)
from sandbox.payments.app import app as payments_app, revision_state as payments_revision_state
from sandbox.provider.app import app as provider_app
from sandbox.traffic.workload import WorkloadSample


def _start_provider_server() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(provider_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                return base_url
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("provider test server did not become ready")


def _reset_payments_revision() -> None:
    payments_revision_state.current_revision = payments_revision_state.healthy_revision
    payments_revision_state.previous_revision = payments_revision_state.healthy_revision


def test_parse_loki_json_log_entry() -> None:
    line = json.dumps(
        {
            "timestamp": "2026-09-01T02:00:00+00:00",
            "service": "payments-service",
            "revision": "v3.4.2",
            "severity": "ERROR",
            "message": "UpstreamTimeout: payment provider did not respond within 3000ms",
        }
    )
    entry = _parse_loki_entry(line, {"service": "payments-service", "severity": "ERROR"}, 1_700_000_000_000_000_000)
    assert entry["service"] == "payments-service"
    assert entry["level"] == "ERROR"
    assert "UpstreamTimeout" in entry["message"]
    assert entry["revision"] == "v3.4.2"


def test_filter_samples_after_rejects_pre_remediation() -> None:
    remediation_at = datetime(2026, 9, 1, 2, 0, 0, tzinfo=UTC)
    samples = [
        WorkloadSample(remediation_at - timedelta(seconds=5), 100, True, 200),
        WorkloadSample(remediation_at + timedelta(seconds=5), 120, True, 200),
    ]
    filtered = filter_samples_after(samples, remediation_at)
    assert len(filtered) == 1
    assert filtered[0].latency_ms == 120


def test_verification_requires_fresh_prometheus_timestamp(monkeypatch) -> None:
    remediation_at = datetime.now(UTC)
    prometheus = PrometheusClient(PrometheusConfig(base_url="http://prometheus.test"))
    old_observation = (
        100,
        remediation_at - timedelta(seconds=30),
    )

    monkeypatch.setattr(
        prometheus,
        "query_p95_latency_ms_with_timestamp",
        lambda service, window="2m": old_observation,
    )
    monkeypatch.setattr(
        prometheus,
        "query_error_rate_percent_with_timestamp",
        lambda service, window="2m": (0.0, old_observation[1]),
    )

    verifier = RecoveryVerifier(max_wait_seconds=1.0, required_consecutive=1)
    workload = MagicMock()
    mapping = MagicMock()
    mapping.affected_service = "checkout-api"
    result = verifier.verify(
        prometheus=prometheus,
        workload=workload,
        mapping=mapping,
        incident_id="inc-1",
        baseline_summary={"p95_latency_ms": 50, "error_rate_percent": 0.0},
        remediation_at=remediation_at,
        sample_duration_seconds=0.1,
    )
    assert result["status"] == "verification_pending"
    workload.collect_baseline.assert_not_called()


def test_meets_baseline_recovery_uses_relative_thresholds() -> None:
    assert meets_baseline_recovery(
        {"request_count": 10, "p95_latency_ms": 180, "error_rate_percent": 0.5},
        {"p95_latency_ms": 90, "error_rate_percent": 0.0},
    )


def test_live_backend_never_imports_simulator_environment() -> None:
    import inspect

    source = inspect.getsource(LiveTelemetryBackend)
    assert "SimulatedEnvironment" not in source
    assert "simulator.scenarios" not in source


def test_payments_faulty_revision_uses_slow_provider_path(monkeypatch) -> None:
    _reset_payments_revision()
    provider_url = _start_provider_server()
    monkeypatch.setattr("sandbox.payments.app.PROVIDER_URL", provider_url)
    with TestClient(payments_app) as payments_client:
        payments_client.post(
            "/internal/control/activate-fault",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
        )
        assert payments_client.get("/health").json()["provider_path"] == "/authorize-slow"
        response = payments_client.post("/v1/charges", json={"amount_cents": 500})
        assert response.status_code == 504


def test_payments_rollback_restores_healthy_provider_path(monkeypatch) -> None:
    _reset_payments_revision()
    provider_url = _start_provider_server()
    monkeypatch.setattr("sandbox.payments.app.PROVIDER_URL", provider_url)
    with TestClient(payments_app) as payments_client:
        payments_client.post(
            "/internal/control/activate-fault",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
        )
        provider_revision_before = httpx.get(f"{provider_url}/internal/revision").json()["current_revision"]
        payments_client.post(
            "/internal/control/rollback",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
            json={"version": "v3.4.2"},
        )
        provider_revision_after = httpx.get(f"{provider_url}/internal/revision").json()["current_revision"]
        assert provider_revision_before == provider_revision_after
        assert payments_client.get("/health").json()["provider_path"] == "/authorize"
        response = payments_client.post("/v1/charges", json={"amount_cents": 500})
        assert response.status_code == 200


def test_loki_client_query_range_parses_entries(monkeypatch) -> None:
    loki = LokiClient(LokiConfig(base_url="http://loki.test"))

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {"service": "auth-service", "severity": "ERROR"},
                            "values": [
                                [
                                    "1700000000000000000",
                                    json.dumps(
                                        {
                                            "service": "auth-service",
                                            "severity": "ERROR",
                                            "message": "TokenValidationError",
                                            "revision": "v2.7.1",
                                            "timestamp": "2026-09-01T02:00:00+00:00",
                                        }
                                    ),
                                ]
                            ],
                        }
                    ]
                },
            }

    monkeypatch.setattr(httpx.Client, "get", lambda self, *args, **kwargs: FakeResponse())
    entries = loki.query_logs_since("auth-service", since=datetime.now(UTC) - timedelta(minutes=5))
    assert entries
    assert "TokenValidationError" in entries[0]["message"]


def test_prometheus_zero_errors_returns_zero_rate_with_timestamp(monkeypatch) -> None:
    prometheus = PrometheusClient(PrometheusConfig(base_url="http://prometheus.test"))
    now = datetime.now(UTC)

    def fake_query(promql: str) -> tuple[float, datetime] | None:
        if "http_errors_total" in promql:
            return None
        if "http_requests_total" in promql:
            return 1.5, now
        return None

    monkeypatch.setattr(prometheus, "_query_instant", fake_query)
    observation = prometheus.query_error_rate_percent_with_timestamp("checkout-api")
    assert observation == (0.0, now)


def test_loki_wait_until_ready_returns_false_when_never_ready(monkeypatch) -> None:
    loki = LokiClient(LokiConfig(base_url="http://loki.test"))
    monkeypatch.setattr(loki, "is_api_ready", lambda: False)
    monkeypatch.setattr("backend.app.telemetry.clients.time.sleep", lambda *_: None)
    assert loki.wait_until_ready(max_attempts=2, base_delay_seconds=0.01) is False


def test_telemetry_source_health_marks_metrics_unavailable_without_fresh_samples(monkeypatch) -> None:
    from backend.app.telemetry.live import LiveTelemetryBackend
    from backend.app.telemetry.models import TelemetrySourceStatus
    from unittest.mock import MagicMock

    prometheus = PrometheusClient(PrometheusConfig(base_url="http://prometheus.test"))
    loki = LokiClient(LokiConfig(base_url="http://loki.test"))
    control = MagicMock()
    backend = LiveTelemetryBackend(
        service="checkout-api",
        prometheus=prometheus,
        loki=loki,
        control=control,
    )
    monkeypatch.setattr(
        "backend.app.telemetry.live.check_metrics_pipeline",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "backend.app.telemetry.live.wait_for_loki_ready",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(backend, "get_service_logs", lambda _service: [])
    states = backend.refresh_pipeline_health()
    assert states["metrics"] == TelemetrySourceStatus.UNAVAILABLE.value
    assert states["logs"] == TelemetrySourceStatus.UNAVAILABLE.value


def test_checkout_fault_increases_latency_under_pressure() -> None:
    database_url = os.environ.get(
        "CHECKOUT_DATABASE_URL",
        "postgresql://opspilot:opspilot@localhost:5432/opspilot",
    )
    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=1):
            pass
    except Exception:
        pytest.skip("checkout database unavailable for pool-pressure test")

    os.environ["CHECKOUT_DATABASE_URL"] = database_url
    from sandbox.checkout.app import app as checkout_app
    from sandbox.checkout.app import revision_state as checkout_revision_state

    checkout_revision_state.current_revision = checkout_revision_state.healthy_revision
    with TestClient(checkout_app) as client:
        healthy_samples: list[float] = []
        for _ in range(5):
            start = time.perf_counter()
            response = client.post("/v1/checkout", json={"cart_id": "cart-1"})
            healthy_samples.append((time.perf_counter() - start) * 1000)
            assert response.status_code in {200, 503}
        client.post(
            "/internal/control/activate-fault",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
        )
        degraded_samples: list[float] = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    client.post,
                    "/v1/checkout",
                    json={"cart_id": f"cart-{index}"},
                )
                for index in range(16)
            ]
            for future in as_completed(futures):
                start = time.perf_counter()
                response = future.result()
                degraded_samples.append((time.perf_counter() - start) * 1000)
                assert response.status_code in {200, 503}
        client.post(
            "/internal/control/rollback",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
            json={"version": checkout_revision_state.faulty_revision},
        )
    healthy_p95 = sorted(healthy_samples)[max(0, int(len(healthy_samples) * 0.95) - 1)]
    degraded_p95 = sorted(degraded_samples)[max(0, int(len(degraded_samples) * 0.95) - 1)]
    assert degraded_p95 > healthy_p95 * 2


@pytest.mark.integration
def test_real_logs_reach_loki_when_stack_running() -> None:
    loki_url = os.environ.get("OPSPILOT_LOKI_URL", "http://localhost:3100")
    auth_url = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8082")
    loki = LokiClient(LokiConfig(base_url=loki_url))
    if not loki.wait_until_ready(max_attempts=3, base_delay_seconds=1.0):
        pytest.skip("Loki API is not reachable")
    since = datetime.now(UTC)
    with httpx.Client(timeout=5.0) as client:
        try:
            response = client.post(
                f"{auth_url}/internal/control/activate-fault",
                headers={"X-Sandbox-Control-Token": os.environ.get("SANDBOX_CONTROL_TOKEN", "sandbox-control-test-token")},
            )
        except httpx.ConnectError:
            pytest.skip("auth-service is not reachable from the test host")
        if response.status_code == 401:
            pytest.skip("sandbox control token mismatch for live stack")
        response.raise_for_status()
        client.post(
            f"{auth_url}/oauth/validate",
            headers={"Authorization": "Bearer invalid-token"},
        )
        client.post(
            f"{auth_url}/internal/control/rollback",
            headers={"X-Sandbox-Control-Token": os.environ.get("SANDBOX_CONTROL_TOKEN", "sandbox-control-test-token")},
            json={"version": response.json().get("faulty_revision", "v2.7.1")},
        )
    from backend.app.telemetry.pipeline_health import verify_log_ingestion

    assert verify_log_ingestion(
        loki,
        service="auth-service",
        marker="TokenValidationError",
        since=since,
    )
    entries = loki.query_logs_since("auth-service", since=since, search_text="TokenValidationError")
    assert entries
    assert entries[0].get("revision")
