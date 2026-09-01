from __future__ import annotations

import os
import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from backend.app.api.app import create_app
from backend.app.api.public_guard import verify_turnstile
from backend.app.api.trusted_proxy import client_ip
from backend.app.cleanup.worker import IncidentCleanupWorker
from backend.app.config import OpsPilotSettings
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.quotas.budget_provider import BudgetGuardedModelProvider
from backend.app.quotas.guard import InMemoryQuotaCounterStore, QuotaConfig, QuotaGuard
from backend.app.readiness import assess_readiness
from backend.app.runtime import RuntimeResources
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore
from backend.app.sandbox.models import LeaseState
from backend.app.session.store import InMemoryDemoSessionStore
from backend.app.telemetry.models import TelemetryMode
from backend.app.turnstile.verifier import FakeTurnstileVerifier
from tests.fakes import FakeModelProvider

ROOT = os.path.join(os.path.dirname(__file__), "..")
PROD_COMPOSE = os.path.join(ROOT, "docker-compose.prod.yml")

EGRESS_SERVICES = {"caddy", "backend", "otel-collector", "checkout-api"}
SANDBOX_ONLY_SERVICES = {
    "auth-service",
    "payments-service",
    "provider-service",
    "prometheus",
}
INTERNAL_PORTS = ("8081:8081", "8082:8082", "8083:8083", "8084:8084", "9090:9090", "4318:4318")


def _compose_text() -> str:
    return open(PROD_COMPOSE).read()


def test_only_caddy_publishes_host_ports() -> None:
    text = _compose_text()
    caddy_ports = re.search(r"caddy:.*?ports:\n((?:\s+-\s+.+\n)+)", text, re.S)
    assert caddy_ports is not None
    assert "80:80" in caddy_ports.group(1)
    assert re.search(r"backend:.*?ports:", text, re.S) is None


def test_network_topology_egress_and_isolation() -> None:
    text = _compose_text()
    assert "sandbox:\n    driver: bridge\n    internal: true" in text
    assert "egress:\n    driver: bridge" in text
    egress_section = text.split("egress:")[1].split("volumes:")[0]
    assert "internal: true" not in egress_section
    for service in EGRESS_SERVICES:
        assert re.search(
            rf"\n  {service}:[\s\S]*?\n    networks:\n(?:      - .+\n)+",
            text,
        )
        assert re.search(rf"\n  {service}:[\s\S]*?\n      - egress\n", text)
    for service in SANDBOX_ONLY_SERVICES:
        assert re.search(
            rf"\n  {service}:[\s\S]*?\n    networks:\n      - sandbox\n",
            text,
        )
        assert not re.search(
            rf"\n  {service}:[\s\S]*?\n    networks:\n      - egress",
            text,
        )


def test_no_internal_ports_published() -> None:
    for token in INTERNAL_PORTS:
        assert token not in _compose_text()


def test_cookie_attributes_on_session_create() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_SESSION_COOKIE_SECURE": "true",
            "OPSPILOT_SESSION_COOKIE_SAMESITE": "none",
            "OPSPILOT_SESSION_COOKIE_DOMAIN": ".example.com",
        }
    )
    hardening = SandboxHardening(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(store=InMemoryQuotaCounterStore(), config=QuotaConfig()),
        turnstile=FakeTurnstileVerifier(),
        enforce_live_guards=False,
        lease_ttl_seconds=600,
        incident_ttl_seconds=1800,
        session_cookie_secure=settings.session_cookie_secure,
        session_cookie_samesite=settings.session_cookie_samesite,
        session_cookie_domain=settings.session_cookie_domain,
        cleanup_interval_seconds=30.0,
    )
    client = TestClient(
        create_app(provider=FakeModelProvider(), settings=settings, hardening=hardening)
    )
    response = client.get("/api/sandbox/status")
    cookie = response.headers.get("set-cookie", "")
    assert "opspilot_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "samesite=none" in cookie.lower()


def test_cors_allows_credentials_not_wildcard() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_CORS_ORIGINS": "https://app.example.com",
        }
    )
    client = TestClient(create_app(provider=FakeModelProvider(), settings=settings))
    response = client.options(
        "/api/scenarios",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert response.headers.get("access-control-allow-credentials") == "true"
    evil = client.options(
        "/api/scenarios",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert evil.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_client_ip_trusts_forwarded_only_from_private_peer() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TRUST_PROXY_HEADERS": "false",
        }
    )

    def _request(peer: str, headers: dict[str, str] | None = None) -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": Headers(headers or {}).raw,
            "client": (peer, 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
        return Request(scope)

    assert client_ip(_request("172.18.0.2", {"x-forwarded-for": "8.8.8.8"}), settings) == "8.8.8.8"
    assert client_ip(_request("8.8.8.8", {"x-forwarded-for": "1.2.3.4"}), settings) == "8.8.8.8"


def test_cleanup_rollback_failure_quarantines_and_blocks_acquire() -> None:
    lease_store = InMemoryGlobalSandboxLeaseStore()
    session_store = MagicMock()
    live = MagicMock()
    live.current_revision = "bad"
    live.mapping.healthy_revision = "good"
    live.control.rollback.side_effect = RuntimeError("rollback failed")
    session = MagicMock()
    session.live_session = live
    session_store.get_optional.return_value = session
    worker = IncidentCleanupWorker(
        lease_store=lease_store,
        session_store=session_store,
        live_orchestrator=MagicMock(),
        repository=InMemoryOpsPilotRepository(),
        list_expired_incidents=lambda _: [("inc-1", "sess-1")],
    )
    assert worker._cleanup_incident("inc-1", "sess-1") is False
    assert lease_store.is_quarantined()
    result = lease_store.acquire(
        session_id="other",
        incident_id="inc-2",
        ttl_seconds=600,
    )
    assert result.quarantined


def test_lease_renewal_extends_active_lease() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    first = store.acquire(session_id="s1", incident_id="i1", ttl_seconds=10)
    assert first.acquired
    assert store.renew(session_id="s1", incident_id="i1", ttl_seconds=600)
    lease = store.inspect()
    assert lease is not None
    assert lease.state is LeaseState.ACTIVE
    assert store.renew(session_id="s2", incident_id="i2", ttl_seconds=600) is False


def test_readiness_unready_when_prometheus_unavailable() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TELEMETRY_MODE": "live",
            "OPSPILOT_PROMETHEUS_URL": "http://prometheus:9090",
            "OPSPILOT_LOKI_URL": "http://loki:3100",
            "SANDBOX_CONTROL_TOKEN": "test-token",
        }
    )
    runtime = RuntimeResources(
        provider=FakeModelProvider(),
        repository=InMemoryOpsPilotRepository(),
        checkpointer=None,
        settings=settings,
    )
    hardening = SandboxHardening(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(store=InMemoryQuotaCounterStore(), config=QuotaConfig()),
        turnstile=FakeTurnstileVerifier(),
        enforce_live_guards=True,
        lease_ttl_seconds=600,
        incident_ttl_seconds=1800,
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        cleanup_interval_seconds=30.0,
    )

    def _check(url: str | None, path: str = "") -> str:
        if path == "/-/ready":
            return "unavailable"
        return "ready"

    with patch("backend.app.readiness._check_url", side_effect=_check):
        report = assess_readiness(runtime, hardening)
    assert report.status == "unready"
    assert report.prometheus == "unavailable"


def test_budget_reconciles_failed_incident_call() -> None:
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(max_model_calls_per_incident=3),
    )
    inner = MagicMock()
    inner.generate_structured.side_effect = [RuntimeError("provider down"), {"ok": True}]
    provider = BudgetGuardedModelProvider(
        inner,
        guard,
        session_id="s",
        incident_id="i",
    )
    with pytest.raises(RuntimeError):
        provider.generate_structured("sys", "user", dict)  # type: ignore[arg-type]
    provider.generate_structured("sys", "user", dict)  # type: ignore[arg-type]
    assert inner.generate_structured.call_count == 2


def test_turnstile_failure_raises_before_lease() -> None:
    hardening = SandboxHardening(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(store=InMemoryQuotaCounterStore(), config=QuotaConfig()),
        turnstile=FakeTurnstileVerifier(),
        enforce_live_guards=True,
        lease_ttl_seconds=600,
        incident_ttl_seconds=1800,
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        cleanup_interval_seconds=30.0,
    )
    request = MagicMock()
    with pytest.raises(HTTPException) as exc:
        verify_turnstile(token=None, request=request, hardening=hardening)
    assert exc.value.status_code == 403
