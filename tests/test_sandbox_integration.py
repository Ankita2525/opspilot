from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from sandbox.auth.app import app as auth_app
from sandbox.checkout.app import app as checkout_app
from sandbox.common.lease import SandboxLeaseManager
from sandbox.control import SandboxControlClient
from sandbox.payments.app import app as payments_app
from sandbox.provider.app import app as provider_app
from sandbox.traffic.workload import WorkloadDriver


@pytest.fixture
def auth_client():
    with TestClient(auth_app) as client:
        yield client


@pytest.fixture
def checkout_client():
    with TestClient(checkout_app) as client:
        yield client


def test_sandbox_control_rejects_unauthorized(auth_client) -> None:
    response = auth_client.post("/internal/control/activate-fault")
    assert response.status_code == 401


def test_auth_fault_produces_real_validation_failures(auth_client) -> None:
    token = auth_client.post("/oauth/token", json={"client_id": "checkout-web"}).json()[
        "access_token"
    ]
    healthy = auth_client.post(
        "/oauth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert healthy.status_code == 200
    auth_client.post(
        "/internal/control/activate-fault",
        headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
    )
    faulty = auth_client.post(
        "/oauth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert faulty.status_code == 401


def test_lease_prevents_overlapping_sessions() -> None:
    manager = SandboxLeaseManager(ttl_seconds=30)
    assert manager.try_acquire("auth-service", "inc-1") is not None
    assert manager.is_busy("auth-service", "inc-2") is True
    assert manager.try_acquire("auth-service", "inc-2") is None


def test_workload_driver_sends_http_requests(auth_client) -> None:
    driver = WorkloadDriver(concurrency=2)
    from sandbox.scenarios import get_live_scenario_mapping

    mapping = get_live_scenario_mapping("auth-token-validation-regression")
    samples = driver.collect_baseline(mapping, "test-incident", duration_seconds=1.0)
    assert samples
    assert all(sample.latency_ms >= 0 for sample in samples)


def test_provider_timeout_under_fault() -> None:
    import os

    os.environ["PROVIDER_SERVICE_URL"] = "http://testserver"
    with TestClient(provider_app) as provider_client, TestClient(payments_app) as payments_client:
        provider_client.post(
            "/internal/control/activate-fault",
            headers={"X-Sandbox-Control-Token": "sandbox-control-test-token"},
        )
        # payments uses httpx to provider URL; patch via shared testserver is non-trivial.
        # Validate provider delay directly and payments timeout classification separately.
        start = __import__("time").perf_counter()
        provider_client.post("/authorize", json={"amount_cents": 500})
        elapsed = __import__("time").perf_counter() - start
        assert elapsed >= 7.0
