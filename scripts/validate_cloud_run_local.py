#!/usr/bin/env python3
"""Full local Cloud Run-style live E2E validation (deterministic AI, real telemetry)."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

API_BASE = os.environ.get("OPSPILOT_API_BASE", "http://localhost:8000")
SCENARIOS = (
    "checkout-db-pool-regression",
    "auth-token-validation-regression",
    "payments-provider-timeout-regression",
)
REQUEST_TIMEOUT = float(os.environ.get("OPSPILOT_E2E_TIMEOUT", "300"))


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    incident_id: str | None = None
    status: str | None = None
    baseline: dict[str, Any] | None = None
    degraded: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    provenance_hash_before: str | None = None
    provenance_hash_after: str | None = None
    approval_status: str | None = None
    resolved: bool = False
    lease_idle: bool = False
    errors: list[str] = field(default_factory=list)


def _log(msg: str) -> None:
    print(msg, flush=True)


def wait_ready(client: httpx.Client, timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            healthz = client.get(f"{API_BASE}/healthz", timeout=2.0)
            if healthz.status_code != 200:
                time.sleep(3.0)
                continue
            response = client.get(f"{API_BASE}/ready", timeout=10.0)
            if response.status_code == 200:
                payload = response.json()
                last = payload
                live_ok = payload.get("live_sandbox") == "ready"
                checks = payload.get("checks") or {}
                if isinstance(checks.get("live_sandbox"), dict):
                    live_ok = live_ok or checks["live_sandbox"].get("ok") is True
                if payload.get("status") in {"ready", "degraded"} and live_ok:
                    return payload
        except httpx.HTTPError:
            pass
        time.sleep(3.0)
    raise RuntimeError(f"/ready not live-sandbox ready: {last}")


def _assert_live_runtime(client: httpx.Client) -> None:
    runtime = client.get(f"{API_BASE}/api/runtime", timeout=10.0).json()
    if runtime.get("telemetry_mode") != "live":
        raise RuntimeError(f"Expected live telemetry_mode, got {runtime!r}")
    if runtime.get("model_provider") != "deterministic":
        raise RuntimeError(
            f"Expected deterministic model_provider for E2E, got {runtime!r}"
        )


def _window(provenance: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = provenance.get(key)
    return value if isinstance(value, dict) else None


def run_scenario(client: httpx.Client, scenario_id: str) -> ScenarioResult:
    result = ScenarioResult(scenario_id=scenario_id, passed=False)
    _log(f"\n=== E2E {scenario_id} ===")

    start = client.post(
        f"{API_BASE}/api/incidents/start",
        json={"scenario_id": scenario_id},
        timeout=REQUEST_TIMEOUT,
    )
    if start.status_code != 200:
        result.errors.append(f"start failed: {start.status_code} {start.text}")
        return result

    body = start.json()
    result.incident_id = body["incident_id"]
    result.status = body.get("status")
    _log(f"investigation status: {result.status}")

    if result.status != "approval_required":
        result.errors.append(f"expected approval_required, got {result.status}")
        return result

    if body.get("approval_request") is None:
        result.errors.append("missing approval_request")

    prov_resp = client.get(
        f"{API_BASE}/api/incidents/{result.incident_id}/provenance",
        timeout=30.0,
    )
    if prov_resp.status_code != 200:
        result.errors.append(f"provenance missing: {prov_resp.status_code}")
        return result

    provenance = prov_resp.json()
    result.provenance_hash_before = provenance.get("evidence_manifest_hash")
    result.baseline = _window(provenance, "baseline")
    result.degraded = _window(provenance, "degraded")

    _log(f"baseline: {json.dumps(result.baseline, default=str)}")
    _log(f"degraded: {json.dumps(result.degraded, default=str)}")

    if provenance.get("telemetry_mode") != "live":
        result.errors.append("provenance telemetry_mode is not live")
    if provenance.get("ground_truth_visible_to_agent") is not False:
        result.errors.append("ground_truth_visible_to_agent must be false")

    for phase, window in (("baseline", result.baseline), ("degraded", result.degraded)):
        if not window:
            result.errors.append(f"{phase} window missing")
            continue
        count = window.get("sample_count", 0)
        if count <= 0:
            result.errors.append(f"{phase} sample_count must be > 0, got {count}")

    approval = client.post(
        f"{API_BASE}/api/incidents/{result.incident_id}/approval",
        json={"approved": True},
        timeout=REQUEST_TIMEOUT,
    )
    if approval.status_code != 200:
        result.errors.append(f"approval failed: {approval.status_code} {approval.text}")
        return result

    approval_body = approval.json()
    result.approval_status = approval_body.get("status")
    result.resolved = approval_body.get("resolved", False)
    _log(
        f"approval: status={result.approval_status} "
        f"p95={approval_body.get('recovered_p95_latency_ms')} "
        f"error_rate={approval_body.get('recovered_error_rate_percent')}"
    )

    if not result.resolved:
        result.errors.append(f"expected resolved, got {result.approval_status}")

    prov_after = client.get(
        f"{API_BASE}/api/incidents/{result.incident_id}/provenance",
        timeout=30.0,
    ).json()
    result.provenance_hash_after = prov_after.get("evidence_manifest_hash")
    result.recovery = _window(prov_after, "recovery")
    _log(f"recovery: {json.dumps(result.recovery, default=str)}")

    remediation = prov_after.get("remediation") or {}
    executed_at = remediation.get("executed_at")
    if result.recovery:
        latest = result.recovery.get("latest_metric_timestamp")
        if latest and executed_at:
            try:
                latest_dt = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
                exec_dt = datetime.fromisoformat(str(executed_at).replace("Z", "+00:00"))
                if latest_dt <= exec_dt:
                    result.errors.append("recovery metric timestamp not after remediation")
            except ValueError:
                pass
        if result.recovery.get("verified") is not True:
            result.errors.append("recovery.verified is not true")

    if result.provenance_hash_before == result.provenance_hash_after:
        result.errors.append("evidence manifest hash unchanged after recovery")

    sandbox = client.get(f"{API_BASE}/api/sandbox/status", timeout=10.0).json()
    result.lease_idle = sandbox.get("state") in {"live_sandbox_available", "idle"}
    _log(f"sandbox lease: {sandbox.get('state')}")
    if sandbox.get("state") == "sandbox_busy":
        result.errors.append(f"lease still busy: {sandbox}")

    result.passed = not result.errors
    return result


def run_restart_e2e(client: httpx.Client, scenario_id: str) -> ScenarioResult:
    """Run through approval_required, then require external stack restart before approval."""
    result = ScenarioResult(scenario_id=f"{scenario_id}-restart", passed=False)
    _log(f"\n=== RESTART E2E {scenario_id} ===")

    start = client.post(
        f"{API_BASE}/api/incidents/start",
        json={"scenario_id": scenario_id},
        timeout=REQUEST_TIMEOUT,
    )
    if start.status_code != 200:
        result.errors.append(f"start failed: {start.status_code}")
        return result

    body = start.json()
    if body.get("status") != "approval_required":
        result.errors.append(f"expected approval_required before restart, got {body.get('status')}")
        return result

    result.incident_id = body["incident_id"]
    pre = client.get(
        f"{API_BASE}/api/incidents/{result.incident_id}/provenance",
        timeout=30.0,
    ).json()
    result.baseline = _window(pre, "baseline")
    result.degraded = _window(pre, "degraded")
    result.provenance_hash_before = pre.get("evidence_manifest_hash")
    _log(f"pre-restart provenance hash: {result.provenance_hash_before}")
    _log(f"pre-restart degraded p95: {(result.degraded or {}).get('p95_latency_ms')}")

    marker = os.environ.get("OPSPILOT_RESTART_MARKER", "/tmp/opspilot-restart-requested")
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "incident_id": result.incident_id,
                    "scenario_id": scenario_id,
                    "cookies": dict(client.cookies),
                    "provenance_hash": result.provenance_hash_before,
                    "degraded_p95": (result.degraded or {}).get("p95_latency_ms"),
                    "requested_at": datetime.now(UTC).isoformat(),
                }
            )
        )
    _log(f"RESTART_REQUIRED: wrote {marker}")
    _log("External step: restart Cloud Run-style stack, then run with --resume-restart")
    result.errors.append("pending external restart")
    return result


def resume_restart_e2e(client: httpx.Client) -> ScenarioResult:
    marker = os.environ.get("OPSPILOT_RESTART_MARKER", "/tmp/opspilot-restart-requested")
    with open(marker, encoding="utf-8") as handle:
        payload = json.loads(handle.read())

    for name, value in (payload.get("cookies") or {}).items():
        client.cookies.set(name, value)

    incident_id = payload["incident_id"]
    result = ScenarioResult(
        scenario_id=f"{payload['scenario_id']}-restart-resume",
        passed=False,
        incident_id=incident_id,
        provenance_hash_before=payload.get("provenance_hash"),
    )

    wait_ready(client, timeout=180.0)

    approval = client.post(
        f"{API_BASE}/api/incidents/{incident_id}/approval",
        json={"approved": True},
        timeout=REQUEST_TIMEOUT,
    )
    if approval.status_code != 200:
        result.errors.append(f"post-restart approval failed: {approval.status_code} {approval.text}")
        return result

    approval_body = approval.json()
    result.resolved = approval_body.get("resolved", False)
    result.approval_status = approval_body.get("status")
    _log(
        f"post-restart approval: status={result.approval_status} "
        f"p95={approval_body.get('recovered_p95_latency_ms')}"
    )

    post = client.get(
        f"{API_BASE}/api/incidents/{incident_id}/provenance",
        timeout=30.0,
    ).json()
    result.provenance_hash_after = post.get("evidence_manifest_hash")
    result.recovery = _window(post, "recovery")
    result.baseline = _window(post, "baseline")
    result.degraded = _window(post, "degraded")

    pre_degraded_p95 = payload.get("degraded_p95")
    post_recovery_p95 = (result.recovery or {}).get("p95_latency_ms")
    _log(f"pre-restart degraded p95 (persisted): {pre_degraded_p95}")
    _log(f"post-restart recovery p95 (fresh): {post_recovery_p95}")
    _log(f"recovery verified: {(result.recovery or {}).get('verified')}")

    if not result.resolved:
        result.errors.append("post-restart incident not resolved")
    if result.recovery is None:
        result.errors.append("post-restart recovery window missing")
    elif result.recovery.get("verified") is not True:
        result.errors.append("post-restart recovery not verified")
    if result.provenance_hash_before == result.provenance_hash_after:
        result.errors.append("manifest hash unchanged after recovery (expected change)")

    # Pre-restart baseline/degraded must still be present in provenance
    if not result.baseline or not result.degraded:
        result.errors.append("pre-restart evidence missing from post-restart provenance")

    sandbox = client.get(f"{API_BASE}/api/sandbox/status", timeout=10.0).json()
    result.lease_idle = sandbox.get("state") in {"live_sandbox_available", "idle"}
    if sandbox.get("state") == "sandbox_busy":
        result.errors.append(f"lease still busy after restart resume: {sandbox}")

    result.passed = not result.errors
    return result


def main() -> int:
    resume = "--resume-restart" in sys.argv
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        client.get(f"{API_BASE}/api/scenarios", timeout=30.0)

        if resume:
            result = resume_restart_e2e(client)
            _log(f"\nRESTART RESUME: {'PASS' if result.passed else 'FAIL'}")
            for err in result.errors:
                _log(f"  - {err}")
            return 0 if result.passed else 1

        ready = wait_ready(client)
        _log(f"ready: {ready}")
        _assert_live_runtime(client)

        restart_only = os.environ.get("OPSPILOT_RESTART_ONLY")
        if restart_only:
            result = run_restart_e2e(client, restart_only)
            _log(f"\nRESTART MARKER: {'written' if result.incident_id else 'failed'}")
            for err in result.errors:
                if err != "pending external restart":
                    _log(f"  ERROR: {err}")
            return 0 if result.incident_id else 1

        results: list[ScenarioResult] = []
        for scenario_id in SCENARIOS:
            results.append(run_scenario(client, scenario_id))

        print("\n=== SUMMARY ===")
        exit_code = 0
        for item in results:
            status = "PASS" if item.passed else "FAIL"
            print(f"{item.scenario_id}: {status}")
            if item.baseline:
                print(f"  baseline p95={item.baseline.get('p95_latency_ms')} samples={item.baseline.get('sample_count')}")
            if item.degraded:
                print(f"  degraded p95={item.degraded.get('p95_latency_ms')} error={item.degraded.get('error_rate')}")
            if item.recovery:
                print(f"  recovery p95={item.recovery.get('p95_latency_ms')} verified={item.recovery.get('verified')}")
            for err in item.errors:
                print(f"  ERROR: {err}")
            if not item.passed:
                exit_code = 1
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
