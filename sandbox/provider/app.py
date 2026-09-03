from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import BaseModel

from sandbox.common.otel_logging import setup_otel_logging
from sandbox.common.telemetry import (
    RevisionState,
    StructuredLogger,
    metrics_endpoint,
    record_request,
    verify_control_token,
)

SERVICE_NAME = "provider-service"
HEALTHY_REVISION = os.environ.get("PROVIDER_HEALTHY_REVISION", "v0.9.0")
FAULTY_REVISION = os.environ.get("PROVIDER_FAULTY_REVISION", "v0.9.1")
HEALTHY_LATENCY_SECONDS = float(os.environ.get("PROVIDER_HEALTHY_LATENCY_SECONDS", "0.15"))
SLOW_LATENCY_SECONDS = float(os.environ.get("PROVIDER_SLOW_LATENCY_SECONDS", "8.0"))

revision_state = RevisionState(
    service=SERVICE_NAME,
    healthy_revision=HEALTHY_REVISION,
    faulty_revision=FAULTY_REVISION,
    initial_revision=HEALTHY_REVISION,
)
logger = StructuredLogger(SERVICE_NAME, revision_state.current_revision)


class AuthorizeRequest(BaseModel):
    amount_cents: int
    currency: str = "USD"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_otel_logging(SERVICE_NAME)
    yield


app = FastAPI(title="Provider Service", lifespan=lifespan)


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


def _authorize(body: AuthorizeRequest, request: Request, delay_seconds: float) -> dict:
    correlation_id = request.headers.get("X-Correlation-Id")
    time.sleep(delay_seconds)
    return {
        "status": "authorized",
        "amount_cents": body.amount_cents,
        "currency": body.currency,
        "provider_revision": revision_state.current_revision,
        "correlation_id": correlation_id,
    }


@app.post("/authorize")
def authorize(body: AuthorizeRequest, request: Request) -> dict:
    return _authorize(body, request, HEALTHY_LATENCY_SECONDS)


@app.post("/authorize-slow")
def authorize_slow(body: AuthorizeRequest, request: Request) -> dict:
    correlation_id = request.headers.get("X-Correlation-Id")
    logger.log(
        "INFO",
        f"Slow authorization path invoked for amount={body.amount_cents}",
        correlation_id=correlation_id,
    )
    return _authorize(body, request, SLOW_LATENCY_SECONDS)


@app.get("/internal/revision")
def get_revision() -> dict:
    return revision_state.status()


@app.post("/internal/control/activate-fault")
def activate_fault(request: Request) -> dict:
    verify_control_token(request)
    revision_state.activate_faulty()
    logger._revision = revision_state.current_revision
    return revision_state.status()


@app.post("/internal/control/rollback")
def rollback(request: Request, body: dict) -> dict:
    verify_control_token(request)
    version = body.get("version")
    if not isinstance(version, str):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="version is required")
    try:
        revision_state.rollback(version)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger._revision = revision_state.current_revision
    return revision_state.status()


@app.post("/internal/control/clear-fault")
def clear_fault(request: Request) -> dict:
    verify_control_token(request)
    status = revision_state.clear_fault()
    logger._revision = revision_state.current_revision
    return status


@app.get("/internal/deployments")
def deployments() -> list[dict]:
    return revision_state.deployment_history
