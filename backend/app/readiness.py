from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from backend.app.telemetry.models import TelemetryMode

if TYPE_CHECKING:
    from backend.app.runtime import RuntimeResources
    from backend.app.sandbox.hardening import SandboxHardening

CHECK_TIMEOUT_SECONDS = 2.0
OVERALL_BUDGET_SECONDS = 4.0
CACHE_TTL_SECONDS = 4.0

StatusLiteral = Literal["ready", "degraded"]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    latency_ms: float | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReadinessReport:
    status: StatusLiteral
    degraded: bool
    checks: dict[str, CheckResult]
    database: str
    model_provider: str
    lease_subsystem: str
    live_sandbox: str
    prometheus: str
    loki: str
    ai_capacity: str
    sandbox_operational: str

    def to_response(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "degraded": self.degraded,
            "checks": {name: check.to_dict() for name, check in self.checks.items()},
            "database": self.database,
            "model_provider": self.model_provider,
            "lease_subsystem": self.lease_subsystem,
            "live_sandbox": self.live_sandbox,
            "prometheus": self.prometheus,
            "loki": self.loki,
            "ai_capacity": self.ai_capacity,
            "sandbox_operational": self.sandbox_operational,
        }


_cache_lock = asyncio.Lock()
_cached_report: ReadinessReport | None = None
_cached_at_monotonic: float = 0.0
_cached_fingerprint: str | None = None


def clear_readiness_cache() -> None:
    """Test helper: drop the short TTL readiness cache."""
    global _cached_report, _cached_at_monotonic, _cached_fingerprint
    _cached_report = None
    _cached_at_monotonic = 0.0
    _cached_fingerprint = None


def _fingerprint(
    runtime: RuntimeResources,
    hardening: SandboxHardening | None,
) -> str:
    settings = runtime.settings
    if settings is None:
        return f"injected|{hardening is not None}"
    return "|".join(
        (
            settings.telemetry_mode.value,
            settings.prometheus_url or "",
            settings.loki_url or "",
            "pg" if settings.uses_postgres else "mem",
            os.environ.get("CHECKOUT_API_URL", ""),
            "hard" if hardening is not None else "nohard",
        )
    )


async def assess_readiness(
    runtime: RuntimeResources,
    hardening: SandboxHardening | None,
    *,
    use_cache: bool = True,
) -> ReadinessReport:
    """Deep diagnostic readiness. Never used as a Cloud Run lifecycle probe."""
    global _cached_report, _cached_at_monotonic, _cached_fingerprint

    fingerprint = _fingerprint(runtime, hardening)
    if use_cache:
        async with _cache_lock:
            if (
                _cached_report is not None
                and _cached_fingerprint == fingerprint
                and (time.monotonic() - _cached_at_monotonic) < CACHE_TTL_SECONDS
            ):
                return _cached_report

    report = await _assess_readiness_uncached(runtime, hardening)

    if use_cache:
        async with _cache_lock:
            _cached_report = report
            _cached_fingerprint = fingerprint
            _cached_at_monotonic = time.monotonic()
    return report


async def _assess_readiness_uncached(
    runtime: RuntimeResources,
    hardening: SandboxHardening | None,
) -> ReadinessReport:
    settings = runtime.settings
    model_provider = runtime.model_provider_name
    live_mode = settings is not None and settings.telemetry_mode is TelemetryMode.LIVE

    try:
        async with asyncio.timeout(OVERALL_BUDGET_SECONDS):
            checks = await _run_checks(runtime, hardening, live_mode=live_mode)
    except TimeoutError:
        checks = {
            "database": CheckResult(
                ok=False,
                latency_ms=None,
                detail="readiness_budget_exceeded",
            ),
            "lease_subsystem": CheckResult(
                ok=False,
                latency_ms=None,
                detail="readiness_budget_exceeded",
            ),
            "sandbox_operational": CheckResult(
                ok=False,
                latency_ms=None,
                detail="readiness_budget_exceeded",
            ),
            "live_sandbox": CheckResult(
                ok=False if live_mode else True,
                latency_ms=None,
                detail="readiness_budget_exceeded" if live_mode else "not_required",
            ),
            "prometheus": CheckResult(
                ok=False if live_mode else True,
                latency_ms=None,
                detail="readiness_budget_exceeded" if live_mode else "not_required",
            ),
            "loki": CheckResult(
                ok=False if live_mode else True,
                latency_ms=None,
                detail="readiness_budget_exceeded" if live_mode else "not_required",
            ),
            "ai_capacity": CheckResult(
                ok=True,
                latency_ms=None,
                detail="unknown",
            ),
        }

    database = _legacy_status(checks["database"], in_memory_ok=True)
    lease_subsystem = _legacy_status(checks["lease_subsystem"], not_configured=True)
    sandbox_operational = checks["sandbox_operational"].detail
    live_sandbox = _legacy_status(checks["live_sandbox"], not_required=True)
    prometheus = _legacy_status(checks["prometheus"], not_required=True)
    loki = _legacy_status(checks["loki"], not_required=True)
    ai_capacity = checks["ai_capacity"].detail

    degraded = _is_degraded(checks, live_mode=live_mode)
    status: StatusLiteral = "degraded" if degraded else "ready"

    return ReadinessReport(
        status=status,
        degraded=degraded,
        checks=checks,
        database=database,
        model_provider=model_provider,
        lease_subsystem=lease_subsystem,
        live_sandbox=live_sandbox,
        prometheus=prometheus,
        loki=loki,
        ai_capacity=ai_capacity,
        sandbox_operational=sandbox_operational,
    )


async def _run_checks(
    runtime: RuntimeResources,
    hardening: SandboxHardening | None,
    *,
    live_mode: bool,
) -> dict[str, CheckResult]:
    settings = runtime.settings
    tasks: dict[str, asyncio.Task[CheckResult]] = {
        "database": asyncio.create_task(_check_database(runtime)),
        "lease_subsystem": asyncio.create_task(_check_lease(hardening)),
        "sandbox_operational": asyncio.create_task(_check_sandbox_operational(hardening)),
        "ai_capacity": asyncio.create_task(_check_ai_capacity(hardening)),
    }

    if live_mode and settings is not None:
        checkout_url = _checkout_health_url(settings.prometheus_url)
        prometheus_url = (
            f"{settings.prometheus_url.rstrip('/')}/-/ready"
            if settings.prometheus_url
            else None
        )
        tasks["live_sandbox"] = asyncio.create_task(
            _check_http(checkout_url, label="checkout_health")
        )
        tasks["prometheus"] = asyncio.create_task(
            _check_http(prometheus_url, label="prometheus_ready")
        )
        tasks["loki"] = asyncio.create_task(_check_loki(settings.loki_url))
    else:
        tasks["live_sandbox"] = asyncio.create_task(
            _immediate(CheckResult(ok=True, latency_ms=None, detail="not_required"))
        )
        tasks["prometheus"] = asyncio.create_task(
            _immediate(CheckResult(ok=True, latency_ms=None, detail="not_required"))
        )
        tasks["loki"] = asyncio.create_task(
            _immediate(CheckResult(ok=True, latency_ms=None, detail="not_required"))
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    checks: dict[str, CheckResult] = {}
    for name, result in zip(tasks.keys(), results, strict=True):
        if isinstance(result, CheckResult):
            checks[name] = result
        else:
            checks[name] = CheckResult(
                ok=False,
                latency_ms=None,
                detail="check_error",
            )
    return checks


async def _immediate(result: CheckResult) -> CheckResult:
    return result


async def _check_database(runtime: RuntimeResources) -> CheckResult:
    settings = runtime.settings
    if settings is None or not settings.uses_postgres:
        return CheckResult(ok=True, latency_ms=None, detail="in_memory")
    started = time.perf_counter()
    try:
        ok = await asyncio.wait_for(
            asyncio.to_thread(runtime.ping_database),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if ok:
            return CheckResult(ok=True, latency_ms=latency_ms, detail="ready")
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")
    except TimeoutError:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


async def _check_lease(hardening: SandboxHardening | None) -> CheckResult:
    if hardening is None:
        return CheckResult(ok=True, latency_ms=None, detail="not_configured")
    started = time.perf_counter()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(hardening.lease_store.inspect),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=True, latency_ms=latency_ms, detail="ready")
    except TimeoutError:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


async def _check_sandbox_operational(
    hardening: SandboxHardening | None,
) -> CheckResult:
    if hardening is None:
        return CheckResult(ok=True, latency_ms=None, detail="available")
    started = time.perf_counter()
    try:
        quarantined = await asyncio.wait_for(
            asyncio.to_thread(hardening.lease_store.is_quarantined),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if quarantined:
            return CheckResult(ok=False, latency_ms=latency_ms, detail="quarantined")
        return CheckResult(ok=True, latency_ms=latency_ms, detail="available")
    except TimeoutError:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


async def _check_ai_capacity(hardening: SandboxHardening | None) -> CheckResult:
    if hardening is None:
        return CheckResult(ok=True, latency_ms=None, detail="available")
    started = time.perf_counter()
    try:
        exhausted = await asyncio.wait_for(
            asyncio.to_thread(hardening.quota_guard.is_global_budget_exhausted),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if exhausted:
            return CheckResult(ok=False, latency_ms=latency_ms, detail="exhausted")
        return CheckResult(ok=True, latency_ms=latency_ms, detail="available")
    except TimeoutError:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


async def _check_http(url: str | None, *, label: str) -> CheckResult:
    del label  # label reserved for future structured logging; never log URLs
    if not url:
        return CheckResult(ok=False, latency_ms=None, detail="not_configured")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if 200 <= response.status_code < 300:
            return CheckResult(ok=True, latency_ms=latency_ms, detail="ready")
        return CheckResult(ok=False, latency_ms=latency_ms, detail="degraded")
    except httpx.TimeoutException:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


async def _check_loki(loki_base_url: str | None) -> CheckResult:
    """Grafana Cloud Loki readiness via authenticated data API (/labels).

    Hosted Grafana Cloud does not expose upstream `/status/buildinfo` usefully.
    Failures stay degraded (never a Cloud Run lifecycle probe).
    """
    if not loki_base_url:
        return CheckResult(ok=False, latency_ms=None, detail="not_configured")
    authorization = (os.environ.get("OPSPILOT_LOKI_AUTHORIZATION") or "").strip()
    if not authorization:
        return CheckResult(ok=False, latency_ms=None, detail="not_configured")
    url = f"{loki_base_url.rstrip('/')}/loki/api/v1/labels"
    headers = {"Authorization": authorization}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if 200 <= response.status_code < 300:
            return CheckResult(ok=True, latency_ms=latency_ms, detail="ready")
        if response.status_code in {401, 403}:
            return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")
        return CheckResult(ok=False, latency_ms=latency_ms, detail="degraded")
    except httpx.TimeoutException:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="timeout")
    except Exception:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return CheckResult(ok=False, latency_ms=latency_ms, detail="unavailable")


def _checkout_health_url(prometheus_url: str | None) -> str | None:
    explicit = os.environ.get("CHECKOUT_API_URL")
    if explicit and explicit.strip():
        return f"{explicit.strip().rstrip('/')}/health"
    if not prometheus_url:
        return None
    # Prefer port remap over hostname rewrite; Prometheus URL is typically loopback.
    base = prometheus_url.replace(":9090", ":8081").rstrip("/")
    return f"{base}/health"


def _legacy_status(
    check: CheckResult,
    *,
    not_required: bool = False,
    not_configured: bool = False,
    in_memory_ok: bool = False,
) -> str:
    if check.detail in {
        "ready",
        "unavailable",
        "degraded",
        "timeout",
        "not_required",
        "not_configured",
        "in_memory",
        "quarantined",
        "available",
        "exhausted",
        "check_error",
        "readiness_budget_exceeded",
        "unknown",
    }:
        if check.detail == "timeout":
            return "unavailable"
        if check.detail == "check_error":
            return "unavailable"
        if check.detail == "readiness_budget_exceeded":
            return "unavailable"
        return check.detail
    if check.ok:
        if in_memory_ok and check.detail == "in_memory":
            return "in_memory"
        if not_required:
            return "not_required"
        if not_configured:
            return "not_configured"
        return "ready"
    return "unavailable"


def _is_degraded(checks: dict[str, CheckResult], *, live_mode: bool) -> bool:
    if not checks["database"].ok:
        return True
    if not checks["lease_subsystem"].ok:
        return True
    if checks["sandbox_operational"].detail == "quarantined":
        return True
    if not checks["sandbox_operational"].ok:
        return True
    if live_mode and not checks["prometheus"].ok:
        return True
    if live_mode and not checks["live_sandbox"].ok:
        return True
    if live_mode and not checks["loki"].ok:
        return True
    if checks["ai_capacity"].detail == "exhausted":
        return True
    return False
