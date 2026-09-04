"""Verified quarantine recovery for durable sandbox leases.

Quarantine is recoverable uncertainty. Idle transitions are allowed only after
idempotent clear-fault + bounded live baseline verification for checkout.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from backend.app.ids import new_incident_id
from backend.app.persistence.models import AuditRecord
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.models import LeaseState
from sandbox.control import SandboxControlClient
from sandbox.scenarios import (
    CHECKOUT_DB_POOL_REGRESSION,
    LIVE_SCENARIO_MAPPINGS,
    LiveScenarioMapping,
)

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 3
VERIFICATION_TIMEOUT_SECONDS = 2.5
BACKOFF_SECONDS = (0.25, 0.5, 1.0)
SANDBOX_OPERATIONAL_AUDIT_INCIDENT = "sandbox_operational"


class SupportsAppendAudit(Protocol):
    def append_audit(self, record: AuditRecord) -> None: ...


@dataclass(frozen=True)
class QuarantineRecoveryResult:
    recovered: bool
    state: str
    attempt_count: int
    transitioned: bool
    failure_category: str | None = None
    source_incident_id: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)


def _checkout_mapping() -> LiveScenarioMapping:
    return LIVE_SCENARIO_MAPPINGS[CHECKOUT_DB_POOL_REGRESSION]


def _audit_incident_id(source_incident_id: str | None) -> str:
    return source_incident_id or SANDBOX_OPERATIONAL_AUDIT_INCIDENT


def _append_recovery_audit(
    repository: SupportsAppendAudit | None,
    *,
    event_type: str,
    message: str,
    source_incident_id: str | None,
    metadata: dict[str, Any],
) -> None:
    if repository is None:
        return
    safe_meta: dict[str, Any] = {
        "source_incident_id": source_incident_id,
        "previous_lease_state": LeaseState.QUARANTINED.value,
        **metadata,
    }
    repository.append_audit(
        AuditRecord(
            audit_id=f"audit-{new_incident_id()}",
            incident_id=_audit_incident_id(source_incident_id),
            event_type=event_type,
            message=message,
            timestamp=datetime.now(UTC),
            metadata=safe_meta,
        )
    )


def _sleep_backoff(attempt_index: int) -> None:
    base = BACKOFF_SECONDS[min(attempt_index, len(BACKOFF_SECONDS) - 1)]
    jitter = random.uniform(0.0, base * 0.2)
    time.sleep(base + jitter)


def _build_checkout_client(
    *,
    client: SandboxControlClient | None = None,
) -> SandboxControlClient:
    if client is not None:
        return client
    return SandboxControlClient.from_mapping(
        _checkout_mapping(),
        timeout_seconds=VERIFICATION_TIMEOUT_SECONDS,
    )


def _is_transient(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            TimeoutError,
            ConnectionError,
        ),
    )


def _probe_sidecar_health(mapping: LiveScenarioMapping) -> str:
    try:
        client = SandboxControlClient.from_mapping(
            mapping,
            timeout_seconds=VERIFICATION_TIMEOUT_SECONDS,
        )
        payload = client.health()
        status = str(payload.get("status") or "")
        return "ok" if status in {"ok", "healthy", "ready"} else f"status:{status or 'unknown'}"
    except Exception as exc:  # noqa: BLE001 — advisory only
        return f"error:{type(exc).__name__}"


def verify_checkout_baseline(
    client: SandboxControlClient,
    *,
    expected_revision: str,
) -> dict[str, Any]:
    """Live checkout baseline proof. Raises on transient transport failures."""
    try:
        client.clear_fault()
        clear_fault_ok = True
    except Exception as exc:
        if _is_transient(exc):
            raise
        return {
            "ok": False,
            "clear_fault_ok": False,
            "fault_inactive": False,
            "revision_baseline": False,
            "ttl_disarmed": False,
            "health_ok": False,
            "current_revision": None,
            "expected_revision": expected_revision,
            "failure_category": "clear_fault_failed",
            "exception_class": type(exc).__name__,
        }

    revision = client.get_revision()
    current_revision = str(revision.get("current_revision") or "")
    fault_inactive = not bool(revision.get("is_faulty"))
    revision_baseline = current_revision == expected_revision
    ttl_disarmed = revision.get("fault_expires_at") is None

    health = client.health()
    health_status = str(health.get("status") or "")
    health_ok = health_status in {"ok", "healthy", "ready"}

    ok = bool(
        clear_fault_ok
        and fault_inactive
        and revision_baseline
        and ttl_disarmed
        and health_ok
    )
    failure_category: str | None = None
    if not clear_fault_ok:
        failure_category = "clear_fault_failed"
    elif not fault_inactive:
        failure_category = "fault_still_active"
    elif not revision_baseline:
        failure_category = "revision_mismatch"
    elif not ttl_disarmed:
        failure_category = "fault_ttl_armed"
    elif not health_ok:
        failure_category = "health_failed"

    return {
        "ok": ok,
        "clear_fault_ok": bool(clear_fault_ok),
        "fault_inactive": fault_inactive,
        "revision_baseline": revision_baseline,
        "ttl_disarmed": ttl_disarmed,
        "health_ok": health_ok,
        "current_revision": current_revision,
        "expected_revision": expected_revision,
        "failure_category": failure_category,
    }


def recover_quarantined_sandbox(
    hardening: SandboxHardening,
    *,
    repository: SupportsAppendAudit | None = None,
    checkout_client: SandboxControlClient | None = None,
    max_attempts: int = MAX_VERIFICATION_ATTEMPTS,
) -> QuarantineRecoveryResult:
    """Run the only supported quarantined→idle recovery path."""
    peek = getattr(hardening.lease_store, "peek", None)
    lease = peek() if callable(peek) else hardening.lease_store.inspect()
    if lease is None or lease.state is not LeaseState.QUARANTINED:
        if not hardening.lease_store.is_quarantined():
            return QuarantineRecoveryResult(
                recovered=True,
                state="idle",
                attempt_count=0,
                transitioned=False,
                verification={"ok": True, "note": "not_quarantined"},
            )
        return QuarantineRecoveryResult(
            recovered=False,
            state="quarantined",
            attempt_count=0,
            transitioned=False,
            failure_category="quarantine_unreadable",
        )

    source_incident_id = lease.incident_id
    expected_revision = _checkout_mapping().healthy_revision
    client = _build_checkout_client(client=checkout_client)

    _append_recovery_audit(
        repository,
        event_type="sandbox_recovery_started",
        message="Sandbox quarantine recovery started.",
        source_incident_id=source_incident_id,
        metadata={"attempt_count": 0},
    )

    last_verification: dict[str, Any] = {}
    failure_category = "verification_failed"
    attempt_count = 0

    for attempt_index in range(max_attempts):
        attempt_count = attempt_index + 1
        try:
            last_verification = verify_checkout_baseline(
                client,
                expected_revision=expected_revision,
            )
            if last_verification["ok"]:
                break
            failure_category = str(
                last_verification.get("failure_category") or "verification_failed"
            )
        except Exception as exc:
            transient = _is_transient(exc)
            failure_category = (
                "verification_timeout" if transient else f"verification_error:{type(exc).__name__}"
            )
            last_verification = {
                "ok": False,
                "clear_fault_ok": False,
                "fault_inactive": False,
                "revision_baseline": False,
                "ttl_disarmed": False,
                "health_ok": False,
                "failure_category": failure_category,
                "exception_class": type(exc).__name__,
            }
            logger.warning(
                "Sandbox quarantine verification attempt %s failed: %s",
                attempt_count,
                type(exc).__name__,
            )
        if attempt_index < max_attempts - 1:
            _sleep_backoff(attempt_index)

    if not last_verification.get("ok"):
        _append_recovery_audit(
            repository,
            event_type="sandbox_recovery_failed",
            message="Sandbox quarantine recovery failed; lease remains quarantined.",
            source_incident_id=source_incident_id,
            metadata={
                "attempt_count": attempt_count,
                "failure_category": failure_category,
                "verification": {
                    key: value
                    for key, value in last_verification.items()
                    if key
                    in {
                        "ok",
                        "clear_fault_ok",
                        "fault_inactive",
                        "revision_baseline",
                        "ttl_disarmed",
                        "health_ok",
                        "current_revision",
                        "expected_revision",
                        "failure_category",
                        "exception_class",
                    }
                },
            },
        )
        return QuarantineRecoveryResult(
            recovered=False,
            state="quarantined",
            attempt_count=attempt_count,
            transitioned=False,
            failure_category=failure_category,
            source_incident_id=source_incident_id,
            verification=last_verification,
        )

    # Advisory health for other fault-capable scenario services (non-blocking).
    sidecar_health: dict[str, str] = {}
    seen: set[str] = set()
    for mapping in LIVE_SCENARIO_MAPPINGS.values():
        if mapping.affected_service in seen:
            continue
        if mapping.affected_service == _checkout_mapping().affected_service:
            continue
        seen.add(mapping.affected_service)
        sidecar_health[mapping.affected_service] = _probe_sidecar_health(mapping)
    if sidecar_health:
        last_verification = {**last_verification, "sidecar_health": sidecar_health}

    # Baseline proven — only now attempt atomic idle transition.
    transitioned = hardening.lease_store.try_transition_quarantined_to_idle(
        expected_incident_id=source_incident_id,
    )
    if not transitioned:
        # Concurrent recovery may have already idled the lease.
        if not hardening.lease_store.is_quarantined():
            _append_recovery_audit(
                repository,
                event_type="sandbox_recovered",
                message="Sandbox quarantine already cleared by concurrent recovery.",
                source_incident_id=source_incident_id,
                metadata={
                    "attempt_count": attempt_count,
                    "transitioned": False,
                    "verification": {
                        "ok": True,
                        "clear_fault_ok": True,
                        "fault_inactive": True,
                        "revision_baseline": True,
                        "ttl_disarmed": True,
                        "health_ok": True,
                    },
                },
            )
            return QuarantineRecoveryResult(
                recovered=True,
                state="idle",
                attempt_count=attempt_count,
                transitioned=False,
                source_incident_id=source_incident_id,
                verification=last_verification,
            )
        _append_recovery_audit(
            repository,
            event_type="sandbox_recovery_failed",
            message="Sandbox baseline verified but lease transition failed.",
            source_incident_id=source_incident_id,
            metadata={
                "attempt_count": attempt_count,
                "failure_category": "lease_transition_failed",
                "verification": {"ok": True},
            },
        )
        return QuarantineRecoveryResult(
            recovered=False,
            state="quarantined",
            attempt_count=attempt_count,
            transitioned=False,
            failure_category="lease_transition_failed",
            source_incident_id=source_incident_id,
            verification=last_verification,
        )

    _append_recovery_audit(
        repository,
        event_type="sandbox_recovered",
        message="Sandbox quarantine recovered after verified baseline.",
        source_incident_id=source_incident_id,
        metadata={
            "attempt_count": attempt_count,
            "transitioned": True,
            "verification": {
                "ok": True,
                "clear_fault_ok": bool(last_verification.get("clear_fault_ok")),
                "fault_inactive": bool(last_verification.get("fault_inactive")),
                "revision_baseline": bool(last_verification.get("revision_baseline")),
                "ttl_disarmed": bool(last_verification.get("ttl_disarmed")),
                "health_ok": bool(last_verification.get("health_ok")),
                "current_revision": last_verification.get("current_revision"),
            },
        },
    )
    return QuarantineRecoveryResult(
        recovered=True,
        state="idle",
        attempt_count=attempt_count,
        transitioned=True,
        source_incident_id=source_incident_id,
        verification=last_verification,
    )
