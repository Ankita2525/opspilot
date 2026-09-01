from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

from sandbox.common.telemetry import (
    RevisionState,
    StructuredLogger,
    metrics_endpoint,
    record_request,
    verify_control_token,
)

SERVICE_NAME = "auth-service"
HEALTHY_REVISION = os.environ.get("AUTH_HEALTHY_REVISION", "v2.7.0")
FAULTY_REVISION = os.environ.get("AUTH_FAULTY_REVISION", "v2.7.1")
ISSUER = "opspilot-sandbox-auth"
HEALTHY_SIGNING_KEY = os.environ.get(
    "AUTH_HEALTHY_SIGNING_KEY",
    "sandbox-auth-healthy-signing-key-2026",
)
FAULTY_SIGNING_KEY = os.environ.get(
    "AUTH_FAULTY_SIGNING_KEY",
    "sandbox-auth-faulty-signing-key-2026",
)

revision_state = RevisionState(
    service=SERVICE_NAME,
    healthy_revision=HEALTHY_REVISION,
    faulty_revision=FAULTY_REVISION,
    initial_revision=HEALTHY_REVISION,
)
logger = StructuredLogger(SERVICE_NAME, revision_state.current_revision)

VALIDATION_SUCCESS = Counter(
    "auth_validations_success_total",
    "Successful token validations",
    ["service", "revision"],
)
VALIDATION_FAILURE = Counter(
    "auth_validations_failed_total",
    "Failed token validations",
    ["service", "revision"],
)
VALIDATION_LATENCY = Histogram(
    "auth_validation_duration_seconds",
    "Token validation latency",
    ["service", "revision"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


class TokenRequest(BaseModel):
    client_id: str


def _signing_key() -> str:
    return FAULTY_SIGNING_KEY if revision_state.is_faulty else HEALTHY_SIGNING_KEY


def _issue_token(client_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": ISSUER,
        "sub": client_id,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "kid": "auth-signing-2026-08",
    }
    return jwt.encode(payload, HEALTHY_SIGNING_KEY, algorithm="HS256")


def _validate_token(token: str) -> dict:
    start = time.perf_counter()
    try:
        payload = jwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            issuer=ISSUER,
        )
        VALIDATION_SUCCESS.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        return payload
    except jwt.PyJWTError as exc:
        VALIDATION_FAILURE.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        logger.log(
            "ERROR",
            f"TokenValidationError: JWT signature verification failed for kid=auth-signing-2026-08 ({exc})",
        )
        raise HTTPException(status_code=401, detail="Token validation failed") from exc
    finally:
        VALIDATION_LATENCY.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).observe(time.perf_counter() - start)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="Auth Service", lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    record_request(
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_seconds=duration,
        revision=revision_state.current_revision,
    )
    return response


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "revision": revision_state.current_revision,
    }


@app.get("/metrics")
async def metrics(request: Request):
    return await metrics_endpoint(request)


@app.post("/oauth/token")
def issue_token(body: TokenRequest) -> dict:
    token = _issue_token(body.client_id)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/oauth/validate")
def validate_token(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.log("ERROR", "POST /oauth/token returned 401 Unauthorized for missing bearer token")
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()
    payload = _validate_token(token)
    return {"valid": True, "client_id": payload.get("sub")}


@app.get("/internal/revision")
def get_revision() -> dict:
    return revision_state.status()


@app.post("/internal/control/activate-fault")
def activate_fault(request: Request) -> dict:
    verify_control_token(request)
    revision_state.activate_faulty()
    logger._revision = revision_state.current_revision
    logger.log(
        "WARN",
        f"Authentication failure rate elevated after deployment {revision_state.current_revision}",
    )
    return revision_state.status()


@app.post("/internal/control/rollback")
def rollback(request: Request, body: dict) -> dict:
    verify_control_token(request)
    version = body.get("version")
    if not isinstance(version, str):
        raise HTTPException(status_code=400, detail="version is required")
    try:
        revision_state.rollback(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger._revision = revision_state.current_revision
    return revision_state.status()


@app.get("/internal/deployments")
def deployments() -> list[dict]:
    return revision_state.deployment_history
