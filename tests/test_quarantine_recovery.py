from __future__ import annotations

import ast
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.cleanup.worker import IncidentCleanupWorker
from backend.app.config import OpsPilotSettings
from backend.app.persistence.incident_status import TERMINAL_INCIDENT_STATUSES
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import IncidentRecord
from backend.app.quotas.guard import InMemoryQuotaCounterStore, QuotaConfig, QuotaGuard
from backend.app.sandbox.fault_reconcile import safe_expire_stale_leases
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.lease_store import InMemoryGlobalSandboxLeaseStore
from backend.app.sandbox.models import LeaseState
from backend.app.sandbox.quarantine_recovery import (
    recover_quarantined_sandbox,
)
from backend.app.session.store import InMemoryDemoSessionStore
from backend.app.turnstile.verifier import NoOpTurnstileVerifier
from tests.fakes import FakeModelProvider

SOURCE_INCIDENT = "inc_da6c2851f0144934922c1e50d4fed326"


def _hardening(lease_store: InMemoryGlobalSandboxLeaseStore) -> SandboxHardening:
    return SandboxHardening(
        lease_store=lease_store,
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(store=InMemoryQuotaCounterStore(), config=QuotaConfig()),
        turnstile=NoOpTurnstileVerifier(),
        enforce_live_guards=True,
        lease_ttl_seconds=240,
        incident_ttl_seconds=240,
        approval_timeout_seconds=240,
        fault_ttl_seconds=300,
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        cleanup_interval_seconds=30.0,
    )


def _baseline_revision(
    *,
    faulty: bool = False,
    ttl_armed: bool = False,
    revision: str = "v1.18.2",
) -> dict:
    return {
        "service": "checkout-api",
        "current_revision": "v1.18.3" if faulty else revision,
        "healthy_revision": "v1.18.2",
        "faulty_revision": "v1.18.3",
        "is_faulty": faulty,
        "fault_expires_at": (
            (datetime.now(UTC) + timedelta(seconds=60)).isoformat() if ttl_armed else None
        ),
        "fault_ttl_seconds": 300,
    }


class FakeCheckoutClient:
    def __init__(
        self,
        *,
        initially_faulty: bool = False,
        fail_clear: bool = False,
        timeouts_before_success: int = 0,
        always_timeout: bool = False,
        keep_faulty_after_clear: bool = False,
        ttl_armed_after_clear: bool = False,
        unhealthy: bool = False,
    ) -> None:
        self.initially_faulty = initially_faulty
        self.fail_clear = fail_clear
        self.timeouts_before_success = timeouts_before_success
        self.always_timeout = always_timeout
        self.keep_faulty_after_clear = keep_faulty_after_clear
        self.ttl_armed_after_clear = ttl_armed_after_clear
        self.unhealthy = unhealthy
        self.faulty = initially_faulty
        self.clear_calls = 0
        self.revision_calls = 0
        self.health_calls = 0
        self._timeouts_seen = 0

    def clear_fault(self) -> dict:
        self.clear_calls += 1
        if self.fail_clear:
            raise RuntimeError("clear failed")
        if self.always_timeout or self._timeouts_seen < self.timeouts_before_success:
            self._timeouts_seen += 1
            raise httpx.ReadTimeout("timed out")
        if not self.keep_faulty_after_clear:
            self.faulty = False
        return _baseline_revision(faulty=self.faulty, ttl_armed=self.ttl_armed_after_clear)

    def get_revision(self) -> dict:
        self.revision_calls += 1
        if self.always_timeout:
            raise httpx.ReadTimeout("timed out")
        return _baseline_revision(
            faulty=self.faulty,
            ttl_armed=self.ttl_armed_after_clear and self.faulty is False,
        )

    def health(self) -> dict:
        self.health_calls += 1
        if self.unhealthy:
            return {"status": "down"}
        return {"status": "ok", "orders": 0}


@pytest.fixture(autouse=True)
def _skip_sidecar_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.app.sandbox.quarantine_recovery._probe_sidecar_health",
        lambda _mapping: "ok",
    )
    monkeypatch.setattr(
        "backend.app.sandbox.quarantine_recovery._sleep_backoff",
        lambda _i: None,
    )


def test_a_startup_quarantined_physically_clean_goes_idle() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    repo = InMemoryOpsPilotRepository()
    result = recover_quarantined_sandbox(
        _hardening(store),
        repository=repo,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert result.recovered is True
    assert result.state == "idle"
    assert store.is_quarantined() is False
    assert store.inspect() is None


def test_b_active_fault_cleared_then_idle() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    repo = InMemoryOpsPilotRepository()
    client = FakeCheckoutClient(initially_faulty=True)
    result = recover_quarantined_sandbox(
        _hardening(store),
        repository=repo,
        checkout_client=client,  # type: ignore[arg-type]
    )
    assert result.recovered is True
    assert client.clear_calls >= 1
    assert client.faulty is False
    assert store.is_quarantined() is False


def test_c_one_timeout_then_success_retries() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    client = FakeCheckoutClient(timeouts_before_success=1)
    result = recover_quarantined_sandbox(
        _hardening(store),
        repository=InMemoryOpsPilotRepository(),
        checkout_client=client,  # type: ignore[arg-type]
    )
    assert result.recovered is True
    assert result.attempt_count == 2
    assert store.is_quarantined() is False


def test_d_all_timeouts_remain_quarantined() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    repo = InMemoryOpsPilotRepository()
    client = FakeCheckoutClient(always_timeout=True)
    result = recover_quarantined_sandbox(
        _hardening(store),
        repository=repo,
        checkout_client=client,  # type: ignore[arg-type]
    )
    assert result.recovered is False
    assert store.is_quarantined() is True
    events = [e.event_type for e in repo.list_audit_events(SOURCE_INCIDENT)]
    assert "sandbox_recovery_started" in events
    assert "sandbox_recovery_failed" in events
    assert "sandbox_recovered" not in events


def test_e_clear_fault_fails_remains_quarantined() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    result = recover_quarantined_sandbox(
        _hardening(store),
        repository=InMemoryOpsPilotRepository(),
        checkout_client=FakeCheckoutClient(fail_clear=True),  # type: ignore[arg-type]
    )
    assert result.recovered is False
    assert result.failure_category == "clear_fault_failed"
    assert store.is_quarantined() is True


def test_f_never_idle_before_successful_verification() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    transitions: list[bool] = []
    original = store.try_transition_quarantined_to_idle

    def _guarded(*, expected_incident_id: str | None) -> bool:
        transitions.append(True)
        return original(expected_incident_id=expected_incident_id)

    store.try_transition_quarantined_to_idle = _guarded  # type: ignore[method-assign]
    recover_quarantined_sandbox(
        _hardening(store),
        checkout_client=FakeCheckoutClient(always_timeout=True),  # type: ignore[arg-type]
    )
    assert transitions == []
    assert store.is_quarantined() is True


def test_g_failed_terminal_excluded_from_list_expired() -> None:
    repo = InMemoryOpsPilotRepository()
    now = datetime.now(UTC)
    repo.save_incident(
        IncidentRecord(
            incident_id=SOURCE_INCIDENT,
            scenario_id="checkout-db-pool-regression",
            affected_service="checkout-api",
            status="failed",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
            recommended_action=None,
            selected_skills=[],
            resolved=False,
            session_id="sess-1",
            expires_at=now - timedelta(minutes=1),
        )
    )
    assert repo.list_expired_incidents(now) == []


def test_h_cleanup_failed_terminal_excluded() -> None:
    repo = InMemoryOpsPilotRepository()
    now = datetime.now(UTC)
    repo.save_incident(
        IncidentRecord(
            incident_id="inc_cleanup_failed",
            scenario_id="checkout-db-pool-regression",
            affected_service="checkout-api",
            status="cleanup_failed",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
            recommended_action=None,
            selected_skills=[],
            resolved=False,
            session_id="sess-2",
            expires_at=now - timedelta(minutes=1),
        )
    )
    assert repo.list_expired_incidents(now) == []
    assert "cleanup_failed" in TERMINAL_INCIDENT_STATUSES


def test_i_no_repeated_incident_expired_for_terminal_failed() -> None:
    repo = InMemoryOpsPilotRepository()
    now = datetime.now(UTC)
    repo.save_incident(
        IncidentRecord(
            incident_id=SOURCE_INCIDENT,
            scenario_id="checkout-db-pool-regression",
            affected_service="checkout-api",
            status="failed",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
            recommended_action=None,
            selected_skills=[],
            resolved=False,
            session_id="sess-1",
            expires_at=now - timedelta(minutes=1),
        )
    )
    lease_store = InMemoryGlobalSandboxLeaseStore()
    worker = IncidentCleanupWorker(
        lease_store=lease_store,
        session_store=MagicMock(list_live_sessions=MagicMock(return_value=[])),
        live_orchestrator=MagicMock(),
        repository=repo,
        list_expired_incidents=repo.list_expired_incidents,
        hardening=_hardening(lease_store),
    )
    assert worker._cleanup_sync() == 0
    assert repo.list_audit_events(SOURCE_INCIDENT) == []


def test_j_source_incident_status_remains_failed() -> None:
    repo = InMemoryOpsPilotRepository()
    now = datetime.now(UTC)
    repo.save_incident(
        IncidentRecord(
            incident_id=SOURCE_INCIDENT,
            scenario_id="checkout-db-pool-regression",
            affected_service="checkout-api",
            status="failed",
            created_at=now,
            updated_at=now,
            recommended_action=None,
            selected_skills=[],
            resolved=False,
            session_id=None,
            expires_at=None,
        )
    )
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    recover_quarantined_sandbox(
        _hardening(store),
        repository=repo,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert repo.get_incident(SOURCE_INCIDENT).status == "failed"


def test_k_recovery_audits_emitted() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    repo = InMemoryOpsPilotRepository()
    recover_quarantined_sandbox(
        _hardening(store),
        repository=repo,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    events = [e.event_type for e in repo.list_audit_events(SOURCE_INCIDENT)]
    assert events == ["sandbox_recovery_started", "sandbox_recovered"]
    recovered = repo.list_audit_events(SOURCE_INCIDENT)[1]
    assert recovered.metadata["source_incident_id"] == SOURCE_INCIDENT
    assert recovered.metadata["verification"]["ok"] is True


def test_l_full_lease_reset_to_idle_semantics() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    recover_quarantined_sandbox(
        _hardening(store),
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert store.peek() is None
    assert store.inspect() is None
    assert store.is_quarantined() is False
    acquired = store.acquire(session_id="s-new", incident_id="inc-new", ttl_seconds=60)
    assert acquired.acquired is True
    assert acquired.quarantined is False


def test_m_concurrent_recovery_single_effective_transition() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    hardening = _hardening(store)
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []

    class SyncClient(FakeCheckoutClient):
        def clear_fault(self) -> dict:
            barrier.wait(timeout=2)
            return super().clear_fault()

    def _run() -> None:
        result = recover_quarantined_sandbox(
            hardening,
            checkout_client=SyncClient(),  # type: ignore[arg-type]
        )
        outcomes.append(result.transitioned)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert store.is_quarantined() is False
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


def test_n_die_before_db_transition_keeps_quarantine_for_retry() -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    hardening = _hardening(store)
    original = store.try_transition_quarantined_to_idle

    def _no_transition(*, expected_incident_id: str | None) -> bool:
        del expected_incident_id
        return False

    store.try_transition_quarantined_to_idle = _no_transition  # type: ignore[method-assign]
    first = recover_quarantined_sandbox(
        hardening,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert first.recovered is False
    assert store.is_quarantined() is True

    store.try_transition_quarantined_to_idle = original  # type: ignore[method-assign]
    second = recover_quarantined_sandbox(
        hardening,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert second.recovered is True
    assert store.is_quarantined() is False


def test_o_status_and_ready_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    hardening = _hardening(store)
    repo = InMemoryOpsPilotRepository()
    recovered = recover_quarantined_sandbox(
        hardening,
        repository=repo,
        checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
    )
    assert recovered.recovered is True
    assert store.is_quarantined() is False

    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_ENV": "test",
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_TELEMETRY_MODE": "live",
            "OPSPILOT_PROMETHEUS_URL": "http://127.0.0.1:9",
            "OPSPILOT_LOKI_URL": "http://127.0.0.1:9",
            "SANDBOX_CONTROL_TOKEN": "test-token",
            "CHECKOUT_API_URL": "http://127.0.0.1:9",
        }
    )

    async def _ok_http(url: str, *, label: str):
        from backend.app.readiness import CheckResult

        del url, label
        return CheckResult(ok=True, latency_ms=1.0, detail="ready")

    async def _ok_loki(_url: str | None):
        from backend.app.readiness import CheckResult

        return CheckResult(ok=True, latency_ms=1.0, detail="ready")

    monkeypatch.setattr("backend.app.readiness._check_http", _ok_http)
    monkeypatch.setattr("backend.app.readiness._check_loki", _ok_loki)

    # Lifespan must not re-quarantine an already-idle lease.
    monkeypatch.setattr(
        "backend.app.sandbox.fault_reconcile.recover_quarantined_sandbox",
        lambda *args, **kwargs: recovered,
    )

    with TestClient(
        create_app(
            provider=FakeModelProvider(),
            settings=settings,
            hardening=hardening,
            repository=repo,
        )
    ) as client:
        status = client.get("/api/sandbox/status").json()
        assert status["state"] == "live_sandbox_available"
        ready = client.get("/ready").json()
        assert ready["status"] == "ready"
        assert ready["degraded"] is False
        assert ready["checks"]["sandbox_operational"]["detail"] == "available"


def test_p_no_unverified_clear_quarantine_code_path() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in (root / "backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "clear_quarantine":
                offenders.append(f"{path}:{node.lineno}")
            if isinstance(node, ast.FunctionDef) and node.name == "clear_quarantine":
                offenders.append(f"{path}:{node.lineno}:def")
    assert offenders == []


def test_safe_expire_uses_recovery_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGlobalSandboxLeaseStore()
    store.quarantine(incident_id=SOURCE_INCIDENT, reason="prior")
    called = {"n": 0}

    def _recover(hardening, repository=None, **kwargs):
        del kwargs
        called["n"] += 1
        return recover_quarantined_sandbox(
            hardening,
            repository=repository,
            checkout_client=FakeCheckoutClient(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        "backend.app.sandbox.fault_reconcile.recover_quarantined_sandbox",
        _recover,
    )
    result = safe_expire_stale_leases(_hardening(store))
    assert called["n"] == 1
    assert result["state"] == "idle"


def test_list_expired_sql_excludes_failed_and_cleanup_failed() -> None:
    from backend.app.persistence.postgres import LIST_EXPIRED_INCIDENTS_SQL

    assert "'failed'" in LIST_EXPIRED_INCIDENTS_SQL
    assert "'cleanup_failed'" in LIST_EXPIRED_INCIDENTS_SQL
    assert "'timed_out'" in LIST_EXPIRED_INCIDENTS_SQL
