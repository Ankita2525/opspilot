from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from backend.app.sandbox.hardening import SandboxHardening
from backend.app.sandbox.models import LeaseState
from backend.app.sandbox.quarantine_recovery import (
    SupportsAppendAudit,
    recover_quarantined_sandbox,
)
from sandbox.control import SandboxControlClient
from sandbox.scenarios import LIVE_SCENARIO_MAPPINGS, LiveScenarioMapping

logger = logging.getLogger(__name__)


def _client_for_mapping(mapping: LiveScenarioMapping) -> SandboxControlClient:
    return SandboxControlClient.from_mapping(mapping)


def clear_fault_for_mapping(mapping: LiveScenarioMapping) -> dict[str, Any]:
    client = _client_for_mapping(mapping)
    return client.clear_fault()


def verify_baseline(mapping: LiveScenarioMapping) -> bool:
    client = _client_for_mapping(mapping)
    revision = client.get_revision()
    return not bool(revision.get("is_faulty"))


def restore_all_sandbox_baselines() -> tuple[bool, list[str]]:
    """Idempotently clear faults across known live sandbox services.

    Returns (all_ok, diagnostic_notes).
    """
    notes: list[str] = []
    all_ok = True
    seen_services: set[str] = set()
    for mapping in LIVE_SCENARIO_MAPPINGS.values():
        if mapping.affected_service in seen_services:
            continue
        seen_services.add(mapping.affected_service)
        try:
            clear_fault_for_mapping(mapping)
            if not verify_baseline(mapping):
                all_ok = False
                notes.append(f"{mapping.affected_service}: still_faulty_after_clear")
            else:
                notes.append(f"{mapping.affected_service}: baseline")
        except Exception as exc:
            all_ok = False
            notes.append(f"{mapping.affected_service}: clear_failed:{type(exc).__name__}")
            logger.exception("Failed to clear sandbox fault for %s", mapping.affected_service)
    return all_ok, notes


def restore_baseline_for_scenario(scenario_id: str) -> tuple[bool, str]:
    mapping = LIVE_SCENARIO_MAPPINGS.get(scenario_id)
    if mapping is None:
        ok, notes = restore_all_sandbox_baselines()
        return ok, ";".join(notes)
    try:
        clear_fault_for_mapping(mapping)
        if verify_baseline(mapping):
            return True, f"{mapping.affected_service}:baseline"
        return False, f"{mapping.affected_service}:still_faulty"
    except Exception as exc:
        logger.exception("Failed clearing fault for scenario %s", scenario_id)
        return False, f"{mapping.affected_service}:{type(exc).__name__}"


def safe_expire_stale_leases(
    hardening: SandboxHardening,
    *,
    repository: SupportsAppendAudit | None = None,
) -> dict[str, Any]:
    """Expire stale leases only after sandbox baseline is verified.

    Hard invariant: lease must never become IDLE while a controlled fault may
    still be active. Quarantined leases use the verifying recovery contract.
    """
    if hardening.lease_store.is_quarantined():
        result = recover_quarantined_sandbox(hardening, repository=repository)
        return {
            "state": "idle" if result.recovered else "quarantined",
            "expired": 0,
            "recovery": {
                "recovered": result.recovered,
                "attempt_count": result.attempt_count,
                "transitioned": result.transitioned,
                "failure_category": result.failure_category,
            },
        }

    peek = getattr(hardening.lease_store, "peek", None)
    lease = peek() if callable(peek) else hardening.lease_store.inspect()
    now = datetime.now(UTC)

    if lease is None:
        expired = hardening.lease_store.expire_stale()
        return {"state": "idle", "expired": expired}

    if lease.state is LeaseState.ACTIVE and lease.expires_at > now:
        return {
            "state": "active",
            "expired": 0,
            "retry_after_seconds": max((lease.expires_at - now).total_seconds(), 1.0),
        }

    if lease.state is LeaseState.QUARANTINED:
        result = recover_quarantined_sandbox(hardening, repository=repository)
        return {
            "state": "idle" if result.recovered else "quarantined",
            "expired": 0,
            "recovery": {
                "recovered": result.recovered,
                "attempt_count": result.attempt_count,
                "transitioned": result.transitioned,
                "failure_category": result.failure_category,
            },
        }

    # Stale/expired active lease: clear faults BEFORE idling.
    ok, notes = restore_all_sandbox_baselines()
    if not ok:
        hardening.lease_store.quarantine(
            incident_id=lease.incident_id or "unknown",
            reason="expire_stale_fault_clear_failed",
        )
        logger.error(
            "Refusing to idle lease; sandbox baseline not verified: %s",
            ";".join(notes),
        )
        return {
            "state": "quarantined",
            "expired": 0,
            "diagnostics": notes,
        }

    expired = hardening.lease_store.expire_stale()
    if lease.session_id and lease.incident_id:
        hardening.lease_store.release(
            session_id=lease.session_id,
            incident_id=lease.incident_id,
        )
    return {"state": "idle", "expired": max(expired, 1), "diagnostics": notes}
