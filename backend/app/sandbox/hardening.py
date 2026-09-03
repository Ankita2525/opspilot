from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from backend.app.config import OpsPilotSettings
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.postgres import PostgresOpsPilotRepository
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.quotas.budget_provider import BudgetGuardedModelProvider
from backend.app.quotas.guard import (
    InMemoryQuotaCounterStore,
    InMemoryRateLimiter,
    QuotaConfig,
    QuotaGuard,
    RateLimitConfig,
)
from backend.app.sandbox.lease_store import (
    GlobalSandboxLeaseStore,
    InMemoryGlobalSandboxLeaseStore,
    PostgresGlobalSandboxLeaseStore,
)
from backend.app.session.store import (
    DemoSessionStore,
    InMemoryDemoSessionStore,
    PostgresDemoSessionStore,
)
from backend.app.turnstile.verifier import (
    CloudflareTurnstileVerifier,
    NoOpTurnstileVerifier,
    TurnstileVerifier,
)

if TYPE_CHECKING:
    from backend.app.models.provider import ModelProvider


@dataclass
class SandboxHardening:
    lease_store: GlobalSandboxLeaseStore
    session_store: DemoSessionStore
    quota_guard: QuotaGuard
    turnstile: TurnstileVerifier
    enforce_live_guards: bool
    lease_ttl_seconds: int
    incident_ttl_seconds: int
    approval_timeout_seconds: int
    fault_ttl_seconds: int
    session_cookie_secure: bool
    session_cookie_samesite: str
    session_cookie_domain: str | None
    cleanup_interval_seconds: float

    def wrap_provider(
        self,
        provider: ModelProvider,
        *,
        session_id: str | None = None,
        incident_id: str | None = None,
        enforce_budget: bool | None = None,
    ) -> ModelProvider:
        if enforce_budget is None:
            enforce_budget = self.enforce_live_guards
        if not enforce_budget:
            return provider
        guarded = BudgetGuardedModelProvider(
            provider,
            self.quota_guard,
            session_id=session_id,
            incident_id=incident_id,
            enforce_budget=True,
        )
        return guarded


def build_sandbox_hardening(
    settings: OpsPilotSettings | None,
    repository: OpsPilotRepository,
) -> SandboxHardening:
    enforce = settings is not None and settings.is_live_telemetry_mode
    if settings is None:
        return _in_memory_hardening(enforce_live_guards=False)

    rate_config = RateLimitConfig(
        burst_per_ip=settings.rate_limit_burst_per_ip,
        window_seconds=settings.rate_limit_window_seconds,
    )
    quota_config = QuotaConfig(
        max_live_incidents_per_session=settings.quota_max_live_incidents_per_session,
        max_model_calls_per_incident=settings.quota_max_model_calls_per_incident,
        max_model_calls_per_session_per_day=settings.quota_max_model_calls_per_session_per_day,
        global_daily_model_call_cap=settings.quota_global_daily_model_call_cap,
    )

    if settings.uses_postgres and settings.database_url:
        database_url = settings.database_url
        lease_store: GlobalSandboxLeaseStore = PostgresGlobalSandboxLeaseStore(
            database_url
        )
        session_store: DemoSessionStore = PostgresDemoSessionStore(database_url)
        quota_store = PostgresQuotaCounterStore(database_url)
    else:
        lease_store = InMemoryGlobalSandboxLeaseStore()
        session_store = InMemoryDemoSessionStore()
        quota_store = InMemoryQuotaCounterStore()

    turnstile = _build_turnstile(settings)
    return SandboxHardening(
        lease_store=lease_store,
        session_store=session_store,
        quota_guard=QuotaGuard(
            store=quota_store,
            config=quota_config,
            rate_limiter=InMemoryRateLimiter(rate_config),
        ),
        turnstile=turnstile,
        enforce_live_guards=enforce,
        lease_ttl_seconds=settings.lease_ttl_seconds,
        incident_ttl_seconds=settings.incident_ttl_seconds,
        approval_timeout_seconds=settings.approval_timeout_seconds,
        fault_ttl_seconds=settings.fault_ttl_seconds,
        session_cookie_secure=settings.session_cookie_secure,
        session_cookie_samesite=settings.session_cookie_samesite,
        session_cookie_domain=settings.session_cookie_domain,
        cleanup_interval_seconds=settings.cleanup_interval_seconds,
    )


def _in_memory_hardening(*, enforce_live_guards: bool) -> SandboxHardening:
    return SandboxHardening(
        lease_store=InMemoryGlobalSandboxLeaseStore(),
        session_store=InMemoryDemoSessionStore(),
        quota_guard=QuotaGuard(
            store=InMemoryQuotaCounterStore(),
            config=QuotaConfig(
                max_live_incidents_per_session=100,
                max_model_calls_per_incident=100,
                max_model_calls_per_session_per_day=1000,
                global_daily_model_call_cap=10000,
            ),
            rate_limiter=InMemoryRateLimiter(RateLimitConfig(burst_per_ip=1000)),
        ),
        turnstile=NoOpTurnstileVerifier(),
        enforce_live_guards=enforce_live_guards,
        lease_ttl_seconds=240,
        incident_ttl_seconds=240,
        approval_timeout_seconds=240,
        fault_ttl_seconds=300,
        session_cookie_secure=False,
        session_cookie_samesite="lax",
        session_cookie_domain=None,
        cleanup_interval_seconds=30.0,
    )


def _build_turnstile(settings: OpsPilotSettings) -> TurnstileVerifier:
    if not settings.turnstile_required:
        return NoOpTurnstileVerifier()
    if not settings.turnstile_secret_key:
        return NoOpTurnstileVerifier()
    return CloudflareTurnstileVerifier(secret_key=settings.turnstile_secret_key)


def list_expired_incidents(
    repository: OpsPilotRepository,
    as_of: datetime,
) -> list[tuple[str, str | None]]:
    if hasattr(repository, "list_expired_incidents"):
        return repository.list_expired_incidents(as_of)  # type: ignore[attr-defined]
    return []


# Re-export for hardening module
from backend.app.quotas.guard import PostgresQuotaCounterStore  # noqa: E402
