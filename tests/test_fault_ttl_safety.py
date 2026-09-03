from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.app.sandbox.fault_reconcile import safe_expire_stale_leases
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore
from backend.app.sandbox.models import LeaseState
from backend.app.session.store import InMemoryDemoSessionStore
from backend.app.quotas.guard import InMemoryQuotaCounterStore, QuotaConfig, QuotaGuard
from backend.app.turnstile.verifier import NoOpTurnstileVerifier
from sandbox.common.telemetry import RevisionState


def _hardening(lease_store: InMemoryGlobalSandboxLeaseStore) -> SandboxHardening:
    return SandboxHardening(
        lease_store=lease_store,
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(store=InMemoryQuotaCounterStore(), config=QuotaConfig()),
        turnstile=NoOpTurnstileVerifier(),
        enforce_live_guards=True,
        lease_ttl_seconds=2,
        incident_ttl_seconds=2,
        approval_timeout_seconds=2,
        fault_ttl_seconds=5,
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        cleanup_interval_seconds=30.0,
    )


def test_fault_ttl_auto_reverts_without_opspilot() -> None:
    state = RevisionState(
        service="checkout-api",
        healthy_revision="v1.18.2",
        faulty_revision="v1.18.3",
        fault_ttl_seconds=1,
    )
    reverted = {"ok": False}

    def _cb() -> None:
        reverted["ok"] = True

    state.set_auto_revert_callback(_cb)
    state.activate_faulty()
    assert state.is_faulty
    deadline = time.time() + 3
    while time.time() < deadline and state.is_faulty:
        time.sleep(0.05)
    assert not state.is_faulty
    assert state.current_revision == "v1.18.2"
    assert reverted["ok"] is True
    assert state.fault_expires_at is None


def test_sidecar_restart_defaults_to_baseline() -> None:
    state = RevisionState(
        service="checkout-api",
        healthy_revision="v1.18.2",
        faulty_revision="v1.18.3",
        fault_ttl_seconds=300,
    )
    assert not state.is_faulty
    assert state.current_revision == "v1.18.2"


def test_explicit_rollback_cancels_ttl() -> None:
    state = RevisionState(
        service="checkout-api",
        healthy_revision="v1.18.2",
        faulty_revision="v1.18.3",
        fault_ttl_seconds=5,
    )
    state.activate_faulty()
    assert state.is_faulty
    state.rollback("v1.18.2")
    assert not state.is_faulty
    assert state.fault_expires_at is None
    # Later timer must not reactivate.
    time.sleep(0.2)
    assert not state.is_faulty
    assert state.current_revision == "v1.18.2"


def test_clear_fault_is_idempotent() -> None:
    state = RevisionState(
        service="checkout-api",
        healthy_revision="v1.18.2",
        faulty_revision="v1.18.3",
        fault_ttl_seconds=30,
    )
    state.activate_faulty()
    first = state.clear_fault()
    second = state.clear_fault()
    assert first["is_faulty"] is False
    assert second["is_faulty"] is False


def test_expire_stale_clears_fault_before_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    hardening = _hardening(store)
    acquired = store.acquire(session_id="s1", incident_id="inc1", ttl_seconds=1)
    assert acquired.acquired
    # Force expiry.
    lease = store.inspect()
    assert lease is not None
    store._lease = type(lease)(
        lease_id=lease.lease_id,
        session_id=lease.session_id,
        incident_id=lease.incident_id,
        acquired_at=lease.acquired_at,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        renewed_at=lease.renewed_at,
        state=LeaseState.ACTIVE,
    )

    cleared = {"n": 0}

    def _restore():
        cleared["n"] += 1
        return True, ["checkout-api:baseline"]

    monkeypatch.setattr(
        "backend.app.sandbox.fault_reconcile.restore_all_sandbox_baselines",
        _restore,
    )
    result = safe_expire_stale_leases(hardening)
    assert cleared["n"] == 1
    assert result["state"] == "idle"
    assert store.inspect() is None or store.inspect().state != LeaseState.ACTIVE


def test_expire_stale_quarantines_when_clear_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    hardening = _hardening(store)
    store.acquire(session_id="s1", incident_id="inc1", ttl_seconds=1)
    lease = store.inspect()
    assert lease is not None
    store._lease = type(lease)(
        lease_id=lease.lease_id,
        session_id=lease.session_id,
        incident_id=lease.incident_id,
        acquired_at=lease.acquired_at,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        renewed_at=lease.renewed_at,
        state=LeaseState.ACTIVE,
    )

    monkeypatch.setattr(
        "backend.app.sandbox.fault_reconcile.restore_all_sandbox_baselines",
        lambda: (False, ["checkout-api:still_faulty"]),
    )
    result = safe_expire_stale_leases(hardening)
    assert result["state"] == "quarantined"
    assert store.is_quarantined()


def test_approval_timeout_less_than_fault_ttl_config() -> None:
    from backend.app.config import OpsPilotSettings, ConfigurationError

    with pytest.raises(ConfigurationError):
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_ENV": "test",
                "OPSPILOT_MODEL_PROVIDER": "deterministic",
                "OPSPILOT_TELEMETRY_MODE": "live",
                "OPSPILOT_PROMETHEUS_URL": "http://localhost:9090",
                "OPSPILOT_LOKI_URL": "http://localhost:3100",
                "SANDBOX_CONTROL_TOKEN": "t",
                "OPSPILOT_APPROVAL_TIMEOUT_SECONDS": "300",
                "OPSPILOT_FAULT_TTL_SECONDS": "300",
            }
        )
