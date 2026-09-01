from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.config import Environment, ModelProviderKind, OpsPilotSettings
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from backend.app.quotas.guard import (
    InMemoryQuotaCounterStore,
    InMemoryRateLimiter,
    QuotaConfig,
    QuotaExceeded,
    QuotaGuard,
    RateLimitConfig,
)
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore
from backend.app.session.store import InMemoryDemoSessionStore
from backend.app.telemetry.models import TelemetryMode
from backend.app.turnstile.verifier import FakeTurnstileVerifier
from tests.fakes import FakeModelProvider

FORBIDDEN = (
    "SANDBOX_CONTROL_TOKEN",
    "sandbox-control-test-token",
    "secret-control-token-do-not-leak",
    "GROQ_API_KEY",
    "DATABASE_URL",
    "OPSPILOT_TURNSTILE_SECRET",
    "turnstile-secret-do-not-leak",
)


def _live_settings(**overrides) -> OpsPilotSettings:
    base = {
        "OPSPILOT_ENV": "test",
        "OPSPILOT_MODEL_PROVIDER": "deterministic",
        "OPSPILOT_TELEMETRY_MODE": "live",
        "OPSPILOT_PROMETHEUS_URL": "http://localhost:9090",
        "OPSPILOT_LOKI_URL": "http://localhost:3100",
        "SANDBOX_CONTROL_TOKEN": "secret-control-token-do-not-leak",
        "OPSPILOT_TURNSTILE_SECRET_KEY": "turnstile-secret-do-not-leak",
        "OPSPILOT_RATE_LIMIT_BURST_PER_IP": "3",
        "OPSPILOT_QUOTA_MAX_LIVE_INCIDENTS_PER_SESSION": "2",
        "OPSPILOT_QUOTA_MAX_MODEL_CALLS_PER_INCIDENT": "2",
        "OPSPILOT_QUOTA_GLOBAL_DAILY_MODEL_CALL_CAP": "5",
    }
    base.update(overrides)
    return OpsPilotSettings.from_env(base)


def _hardening(settings: OpsPilotSettings) -> SandboxHardening:
    return SandboxHardening(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(
            store=InMemoryQuotaCounterStore(),
            config=QuotaConfig(
                max_live_incidents_per_session=settings.quota_max_live_incidents_per_session,
                max_model_calls_per_incident=settings.quota_max_model_calls_per_incident,
                max_model_calls_per_session_per_day=settings.quota_max_model_calls_per_session_per_day,
                global_daily_model_call_cap=settings.quota_global_daily_model_call_cap,
            ),
            rate_limiter=InMemoryRateLimiter(
                RateLimitConfig(
                    burst_per_ip=settings.rate_limit_burst_per_ip,
                    window_seconds=settings.rate_limit_window_seconds,
                )
            ),
        ),
        turnstile=FakeTurnstileVerifier(),
        enforce_live_guards=True,
        lease_ttl_seconds=settings.lease_ttl_seconds,
        incident_ttl_seconds=settings.incident_ttl_seconds,
        session_cookie_secure=False,
        cleanup_interval_seconds=30.0,
    )


def _client(
    *,
    hardening: SandboxHardening | None = None,
    settings: OpsPilotSettings | None = None,
) -> TestClient:
    resolved_settings = settings or _live_settings()
    return TestClient(
        create_app(
            provider=FakeModelProvider(),
            repository=InMemoryOpsPilotRepository(),
            settings=resolved_settings,
            hardening=hardening or _hardening(resolved_settings),
        )
    )


def _assert_no_secrets(payload: object) -> None:
    text = json.dumps(payload)
    for token in FORBIDDEN:
        assert token not in text


def test_concurrent_lease_acquire_exactly_one_owner() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    results = []

    def attempt(i: int):
        return store.acquire(
            session_id=f"session-{i}",
            incident_id=f"incident-{i}",
            ttl_seconds=600,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(attempt, i) for i in range(12)]
        for future in as_completed(futures):
            results.append(future.result())

    acquired = [r for r in results if r.acquired]
    assert len(acquired) == 1


def test_rate_limiter_blocks_burst() -> None:
    limiter = InMemoryRateLimiter(RateLimitConfig(burst_per_ip=2, window_seconds=60))
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(),
        rate_limiter=limiter,
    )
    guard.check_ip_burst("1.2.3.4")
    guard.check_ip_burst("1.2.3.4")
    with pytest.raises(Exception):
        guard.check_ip_burst("1.2.3.4")


def test_cross_session_approval_denied() -> None:
    from fastapi import HTTPException

    from backend.app.api.public_guard import require_incident_owner

    with pytest.raises(HTTPException) as exc:
        require_incident_owner(
            owner_session_id="owner-session",
            requester_session_id="other-session",
            enforce=True,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "session_not_authorized"


def test_cross_session_approval_allowed_when_not_enforced() -> None:
    from backend.app.api.public_guard import require_incident_owner

    require_incident_owner(
        owner_session_id="owner-session",
        requester_session_id="other-session",
        enforce=False,
    )


def test_sandbox_status_never_leaks_secrets() -> None:
    payload = _client().get("/api/sandbox/status").json()
    _assert_no_secrets(payload)


def test_runtime_never_leaks_secrets() -> None:
    payload = _client().get("/api/runtime").json()
    _assert_no_secrets(payload)
    assert "turnstile-secret" not in json.dumps(payload)


def test_ready_never_leaks_secrets() -> None:
    payload = _client().get("/ready").json()
    _assert_no_secrets(payload)


def test_reference_eval_unaffected_by_live_guards() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TELEMETRY_MODE": "reference",
        }
    )
    client = TestClient(
        create_app(
            provider=FakeModelProvider(),
            settings=settings,
        )
    )
    response = client.get("/api/evaluations/baseline")
    assert response.status_code == 200
    assert response.json()["passed_scenarios"] == 3


def test_turnstile_required_blocks_without_token() -> None:
    settings = _live_settings(OPSPILOT_TURNSTILE_REQUIRED="true")
    hardening = _hardening(settings)
  # Fake verifier still needs valid token when we wire required flag
    client = _client(settings=settings, hardening=hardening)
    response = client.post(
        "/api/incidents/start",
        json={"scenario_id": "checkout-db-pool-regression"},
    )
    assert response.status_code == 403


def test_lease_release_on_inspect_after_expire() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store._ttl_seconds = 0  # type: ignore[attr-defined]
    result = store.acquire(
        session_id="s1",
        incident_id="i1",
        ttl_seconds=0,
    )
    assert result.acquired
    store._expire_stale_locked(datetime.now(UTC))  # type: ignore[attr-defined]
    assert store.inspect() is None


def test_quota_guard_blocks_excessive_model_calls() -> None:
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(
            max_live_incidents_per_session=10,
            max_model_calls_per_incident=2,
            max_model_calls_per_session_per_day=100,
            global_daily_model_call_cap=100,
        ),
    )
    guard.reserve_model_call(session_id="s", incident_id="i")
    guard.reserve_model_call(session_id="s", incident_id="i")
    with pytest.raises(QuotaExceeded):
        guard.reserve_model_call(session_id="s", incident_id="i")


def test_prod_compose_does_not_publish_internal_ports() -> None:
    content = open(
        os.path.join(os.path.dirname(__file__), "..", "docker-compose.prod.yml")
    ).read()
    for service in (
        "checkout-api",
        "auth-service",
        "payments-service",
        "provider-service",
        "prometheus",
        "otel-collector",
    ):
        assert f"  {service}:" in content
    assert "8081:8081" not in content
    assert "9090:9090" not in content
    assert "4318:4318" not in content
