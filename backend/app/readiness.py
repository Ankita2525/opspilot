from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from backend.app.telemetry.models import TelemetryMode

if TYPE_CHECKING:
    from backend.app.runtime import RuntimeResources
    from backend.app.sandbox.hardening import SandboxHardening


@dataclass(frozen=True)
class ReadinessReport:
    status: Literal["ready", "degraded", "unready"]
    database: str
    model_provider: str
    lease_subsystem: str
    live_sandbox: str
    prometheus: str
    loki: str
    ai_capacity: str
    sandbox_operational: str

    def to_response(self) -> dict[str, str]:
        return {
            "status": self.status,
            "database": self.database,
            "model_provider": self.model_provider,
            "lease_subsystem": self.lease_subsystem,
            "live_sandbox": self.live_sandbox,
            "prometheus": self.prometheus,
            "loki": self.loki,
            "ai_capacity": self.ai_capacity,
            "sandbox_operational": self.sandbox_operational,
        }


def assess_readiness(
    runtime: RuntimeResources,
    hardening: SandboxHardening | None,
) -> ReadinessReport:
    settings = runtime.settings
    database = runtime.database_status
    model_provider = runtime.model_provider_name
    lease_subsystem = "ready" if hardening is not None else "not_configured"
    live_sandbox = "not_required"
    prometheus = "not_required"
    loki = "not_required"
    ai_capacity = "available"
    sandbox_operational = "available"

    if hardening is not None:
        try:
            if hardening.lease_store.is_quarantined():
                sandbox_operational = "quarantined"
            else:
                hardening.lease_store.inspect()
            lease_subsystem = "ready"
        except Exception:
            lease_subsystem = "unavailable"

    live_mode = (
        settings is not None and settings.telemetry_mode is TelemetryMode.LIVE
    )
    if live_mode and settings is not None:
        live_sandbox = _check_url(
            settings.prometheus_url.replace("prometheus", "checkout-api").replace(
                ":9090", ":8081"
            )
            if settings.prometheus_url
            else None,
            path="/health",
        )
        prometheus = _check_url(settings.prometheus_url, path="/-/ready")
        loki = _check_url(
            f"{settings.loki_url.rstrip('/')}/loki/api/v1/status/buildinfo"
            if settings.loki_url
            else None,
        )
        if hardening is not None and hardening.quota_guard.is_global_budget_exhausted():
            ai_capacity = "exhausted"

    status = _overall_status(
        database=database,
        lease_subsystem=lease_subsystem,
        sandbox_operational=sandbox_operational,
        live_mode=live_mode,
        prometheus=prometheus,
        loki=loki,
        ai_capacity=ai_capacity,
    )

    return ReadinessReport(
        status=status,
        database=database,
        model_provider=model_provider,
        lease_subsystem=lease_subsystem,
        live_sandbox=live_sandbox,
        prometheus=prometheus,
        loki=loki,
        ai_capacity=ai_capacity,
        sandbox_operational=sandbox_operational,
    )


def _overall_status(
    *,
    database: str,
    lease_subsystem: str,
    sandbox_operational: str,
    live_mode: bool,
    prometheus: str,
    loki: str,
    ai_capacity: str,
) -> Literal["ready", "degraded", "unready"]:
    if database not in {"ready", "in_memory"}:
        return "unready"
    if lease_subsystem != "ready":
        return "unready"
    if sandbox_operational == "quarantined":
        return "unready"
    if live_mode and prometheus == "unavailable":
        return "unready"
    if live_mode and prometheus == "degraded":
        return "degraded"
    if loki == "unavailable":
        return "degraded"
    if ai_capacity == "exhausted":
        return "degraded"
    return "ready"


def _check_url(base_url: str | None, path: str = "") -> str:
    if not base_url:
        return "not_configured"
    url = base_url if not path else f"{base_url.rstrip('/')}{path}"
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            if 200 <= response.status < 300:
                return "ready"
            return "degraded"
    except Exception:
        return "unavailable"
