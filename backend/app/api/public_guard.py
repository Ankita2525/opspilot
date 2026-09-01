from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response

from backend.app.observability.metrics import lease_failures, live_incidents_started
from backend.app.quotas.guard import QuotaExceeded, RateLimitExceeded
from backend.app.sandbox.hardening import SandboxHardening
from backend.app.session.models import SESSION_COOKIE_NAME, DemoSession


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def resolve_demo_session(
    request: Request,
    hardening: SandboxHardening,
    response: Response | None = None,
) -> DemoSession:
    cookie_id = request.cookies.get(SESSION_COOKIE_NAME)
    session = hardening.session_store.get_or_create(cookie_id)
    if response is not None and cookie_id != session.session_id:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.session_id,
            httponly=True,
            secure=hardening.session_cookie_secure,
            samesite="lax",
            max_age=86400 * 7,
        )
    return session


def check_rate_limit(request: Request, hardening: SandboxHardening) -> None:
    try:
        hardening.quota_guard.check_ip_burst(client_ip(request))
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from exc


def verify_turnstile(
    *,
    token: str | None,
    request: Request,
    hardening: SandboxHardening,
) -> None:
    if not hardening.enforce_live_guards:
        return
    if not hardening.turnstile.verify(token, remote_ip=client_ip(request)):
        raise HTTPException(
            status_code=403,
            detail={"error": "turnstile_verification_failed"},
        )


def acquire_global_lease(
    *,
    hardening: SandboxHardening,
    session: DemoSession,
    incident_id: str,
) -> None:
    if not hardening.enforce_live_guards:
        return
    try:
        hardening.quota_guard.check_session_live_incident_limit(
            session.session_id,
            session.live_incident_count,
        )
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"error": exc.reason, "retry_after_seconds": exc.retry_after_seconds},
        ) from exc

    result = hardening.lease_store.acquire(
        session_id=session.session_id,
        incident_id=incident_id,
        ttl_seconds=hardening.lease_ttl_seconds,
    )
    if not result.acquired:
        lease_failures.add(1)
        detail: dict[str, object] = {"error": "sandbox_busy", "busy": True}
        if result.retry_after_seconds is not None:
            detail["retry_after_seconds"] = result.retry_after_seconds
        raise HTTPException(status_code=409, detail=detail)
    live_incidents_started.add(1)
    hardening.session_store.increment_live_incident_count(session.session_id)


def release_global_lease(
    *,
    hardening: SandboxHardening,
    session_id: str,
    incident_id: str,
) -> None:
    if not hardening.enforce_live_guards:
        return
    hardening.lease_store.release(session_id=session_id, incident_id=incident_id)


def require_incident_owner(
    *,
    owner_session_id: str | None,
    requester_session_id: str,
    enforce: bool = True,
) -> None:
    if not enforce:
        return
    if owner_session_id is None:
        return
    if owner_session_id != requester_session_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "session_not_authorized"},
        )


def incident_expires_at(hardening: SandboxHardening) -> datetime | None:
    if not hardening.enforce_live_guards:
        return None
    return datetime.now(UTC) + timedelta(seconds=hardening.incident_ttl_seconds)


def sandbox_status(hardening: SandboxHardening) -> dict[str, object]:
    if not hardening.enforce_live_guards:
        return {"state": "live_sandbox_available"}
    lease = hardening.lease_store.inspect()
    if hardening.quota_guard.is_global_budget_exhausted():
        return {"state": "ai_provider_unavailable"}
    if lease is not None:
        retry = max((lease.expires_at - datetime.now(UTC)).total_seconds(), 1.0)
        return {
            "state": "sandbox_busy",
            "retry_after_seconds": retry,
        }
    return {"state": "live_sandbox_available"}
