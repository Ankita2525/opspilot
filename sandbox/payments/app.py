from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

import httpx
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

SERVICE_NAME = "payments-service"
HEALTHY_REVISION = os.environ.get("PAYMENTS_HEALTHY_REVISION", "v3.4.1")
FAULTY_REVISION = os.environ.get("PAYMENTS_FAULTY_REVISION", "v3.4.2")
PROVIDER_URL = os.environ.get("PROVIDER_SERVICE_URL", "http://localhost:8084")
CLIENT_TIMEOUT_SECONDS = float(os.environ.get("PAYMENTS_PROVIDER_TIMEOUT_SECONDS", "3.0"))

revision_state = RevisionState(
    service=SERVICE_NAME,
    healthy_revision=HEALTHY_REVISION,
    faulty_revision=FAULTY_REVISION,
    initial_revision=HEALTHY_REVISION,
)
logger = StructuredLogger(SERVICE_NAME, revision_state.current_revision)

PROVIDER_DURATION = Histogram(
    "payments_provider_duration_seconds",
    "Upstream provider call duration",
    ["service", "revision"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)
PROVIDER_TIMEOUTS = Counter(
    "payments_provider_timeouts_total",
    "Provider timeout count",
    ["service", "revision"],
)
PAYMENT_FAILURES = Counter(
    "payments_request_failures_total",
    "Payment request failures",
    ["service", "revision"],
)


class ChargeRequest(BaseModel):
    amount_cents: int
    currency: str = "USD"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="Payments Service", lifespan=lifespan)


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


@app.post("/v1/charges")
def create_charge(body: ChargeRequest, request: Request) -> dict:
    correlation_id = request.headers.get("X-Correlation-Id")
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=CLIENT_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{PROVIDER_URL}/authorize",
                json={"amount_cents": body.amount_cents, "currency": body.currency},
                headers={"X-Correlation-Id": correlation_id or ""},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        duration = time.perf_counter() - start
        PROVIDER_DURATION.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).observe(duration)
        PROVIDER_TIMEOUTS.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        PAYMENT_FAILURES.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        logger.log(
            "ERROR",
            f"UpstreamTimeout: payment provider did not respond within {int(CLIENT_TIMEOUT_SECONDS * 1000)}ms",
            correlation_id=correlation_id,
        )
        raise HTTPException(status_code=504, detail="Provider timeout") from exc
    except Exception as exc:
        PAYMENT_FAILURES.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        logger.log("ERROR", f"Payment capture failed: {exc}", correlation_id=correlation_id)
        raise HTTPException(status_code=502, detail="Payment failed") from exc
    duration = time.perf_counter() - start
    PROVIDER_DURATION.labels(
        service=SERVICE_NAME,
        revision=revision_state.current_revision,
    ).observe(duration)
    return payload


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
