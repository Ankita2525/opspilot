from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.app.config import OpsPilotSettings
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.telemetry.models import TelemetryMode

if TYPE_CHECKING:
    from backend.app.runtime import RuntimeResources


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    status: str


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    database: str
    model_provider: str
    lease_subsystem: str
    live_sandbox: str
    prometheus: str
    loki: str
    ai_capacity: str

    def to_response(self) -> dict[str, str]:
        overall = "ready" if self.ready else "not_ready"
        return {
            "status": overall,
            "database": self.database,
            "model_provider": self.model_provider,
            "lease_subsystem": self.lease_subsystem,
            "live_sandbox": self.live_sandbox,
            "prometheus": self.prometheus,
            "loki": self.loki,
            "ai_capacity": self.ai_capacity,
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

    if settings is not None and settings.telemetry_mode is TelemetryMode.LIVE:
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

    if hardening is not None:
        try:
            hardening.lease_store.inspect()
            lease_subsystem = "ready"
        except Exception:
            lease_subsystem = "unavailable"

    ready = database in {"ready", "in_memory"} and lease_subsystem == "ready"
    if settings is not None and settings.telemetry_mode is TelemetryMode.LIVE:
        ready = ready and prometheus in {"ready", "degraded"}

    return ReadinessReport(
        ready=ready,
        database=database,
        model_provider=model_provider,
        lease_subsystem=lease_subsystem,
        live_sandbox=live_sandbox,
        prometheus=prometheus,
        loki=loki,
        ai_capacity=ai_capacity,
    )


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
