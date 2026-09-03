from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager

import psycopg
import psycopg_pool
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Gauge
from psycopg.rows import dict_row

from sandbox.common.otel_logging import setup_otel_logging
from sandbox.common.telemetry import (
    RevisionState,
    StructuredLogger,
    metrics_endpoint,
    record_request,
    verify_control_token,
)

SERVICE_NAME = "checkout-api"
HEALTHY_REVISION = os.environ.get("CHECKOUT_HEALTHY_REVISION", "v1.18.2")
FAULTY_REVISION = os.environ.get("CHECKOUT_FAULTY_REVISION", "v1.18.3")
DATABASE_URL = os.environ.get(
    "CHECKOUT_DATABASE_URL",
    "postgresql://opspilot:opspilot@localhost:5432/opspilot",
)

revision_state = RevisionState(
    service=SERVICE_NAME,
    healthy_revision=HEALTHY_REVISION,
    faulty_revision=FAULTY_REVISION,
    initial_revision=HEALTHY_REVISION,
)
logger = StructuredLogger(SERVICE_NAME, revision_state.current_revision)

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured database pool size",
    ["service", "revision"],
)
DB_POOL_IN_USE = Gauge(
    "db_pool_connections_in_use",
    "Database pool connections in use",
    ["service", "revision"],
)
DB_POOL_WAIT_FAILURES = Counter(
    "db_pool_wait_failures_total",
    "Database pool acquisition failures",
    ["service", "revision"],
)

_pool: psycopg_pool.ConnectionPool | None = None
_holder_connections: list[psycopg.Connection] = []
_holder_lock = threading.Lock()


def _pool_max_size() -> int:
    return 2 if revision_state.is_faulty else 20


def _rebuild_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
    max_size = _pool_max_size()
    _pool = psycopg_pool.ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=max_size,
        timeout=5.0,
        kwargs={"row_factory": dict_row},
    )
    DB_POOL_SIZE.labels(
        service=SERVICE_NAME,
        revision=revision_state.current_revision,
    ).set(max_size)


def _ensure_schema() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkout_orders (
                id SERIAL PRIMARY KEY,
                order_ref TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _hold_one_connection() -> None:
    try:
        conn = psycopg.connect(DATABASE_URL)
        conn.execute("BEGIN")
        conn.execute("SELECT pg_sleep(300)")
        with _holder_lock:
            _holder_connections.append(conn)
    except Exception:
        return


def _hold_connections_if_faulty() -> None:
    global _holder_connections
    with _holder_lock:
        for conn in _holder_connections:
            try:
                conn.close()
            except Exception:
                pass
        _holder_connections = []
        if not revision_state.is_faulty:
            return
        max_hold = max(0, _pool_max_size() - 1)
    for _ in range(max_hold):
        threading.Thread(target=_hold_one_connection, daemon=True).start()


def _update_pool_gauges() -> None:
    if _pool is None:
        return
    stats = _pool.get_stats()
    pool_size = _pool_max_size()
    available = stats.get("pool_available", 0)
    in_use = max(0, pool_size - available)
    DB_POOL_IN_USE.labels(
        service=SERVICE_NAME,
        revision=revision_state.current_revision,
    ).set(in_use)


def _on_fault_auto_revert() -> None:
    logger._revision = revision_state.current_revision
    _rebuild_pool()
    _hold_connections_if_faulty()
    logger.log(
        "WARN",
        f"Fault TTL expired; restored baseline revision {revision_state.current_revision}",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_otel_logging(SERVICE_NAME)
    _ensure_schema()
    _rebuild_pool()
    revision_state.set_auto_revert_callback(_on_fault_auto_revert)
    yield
    revision_state.set_auto_revert_callback(None)
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


app = FastAPI(title="Checkout API", lifespan=lifespan)


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
    _update_pool_gauges()
    return await metrics_endpoint(request)


@app.post("/api/v1/checkout")
def checkout(request: Request) -> dict:
    correlation_id = request.headers.get("X-Correlation-Id")
    if _pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")
    try:
        with _pool.connection() as conn:
            if revision_state.is_faulty:
                conn.execute("SELECT pg_sleep(0.2)")
            conn.execute(
                "INSERT INTO checkout_orders (order_ref) VALUES (%s) RETURNING id",
                (f"ord_{int(time.time() * 1000)}",),
            )
            row = conn.execute("SELECT COUNT(*) AS count FROM checkout_orders").fetchone()
    except psycopg_pool.PoolTimeout:
        DB_POOL_WAIT_FAILURES.labels(
            service=SERVICE_NAME,
            revision=revision_state.current_revision,
        ).inc()
        logger.log(
            "ERROR",
            "TimeoutError: database connection pool timeout after 5000ms waiting for a free connection",
            correlation_id=correlation_id,
        )
        raise HTTPException(status_code=503, detail="Database connection pool timeout") from None
    except Exception as exc:
        logger.log("ERROR", f"Checkout failed: {exc}", correlation_id=correlation_id)
        raise HTTPException(status_code=500, detail="Checkout failed") from exc
    _update_pool_gauges()
    stats = _pool.get_stats() if _pool is not None else {}
    pool_size = _pool_max_size()
    available = stats.get("pool_available", 0)
    if revision_state.is_faulty and available == 0:
        logger.log(
            "WARN",
            f"Connection pool exhausted (active={pool_size}, idle=0, max={pool_size})",
            correlation_id=correlation_id,
        )
    return {"status": "ok", "orders": row["count"] if row else 0}


@app.get("/internal/revision")
def get_revision() -> dict:
    return revision_state.status()


@app.post("/internal/control/activate-fault")
def activate_fault(request: Request) -> dict:
    verify_control_token(request)
    revision_state.activate_faulty()
    logger._revision = revision_state.current_revision
    _rebuild_pool()
    _hold_connections_if_faulty()
    logger.log("WARN", f"Activated faulty revision {revision_state.current_revision}")
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
    _rebuild_pool()
    _hold_connections_if_faulty()
    logger.log("INFO", f"Rolled back to revision {revision_state.current_revision}")
    return revision_state.status()


@app.post("/internal/control/clear-fault")
def clear_fault(request: Request) -> dict:
    verify_control_token(request)
    status = revision_state.clear_fault()
    logger._revision = revision_state.current_revision
    _rebuild_pool()
    _hold_connections_if_faulty()
    logger.log("INFO", f"Cleared fault; baseline revision {revision_state.current_revision}")
    return status


@app.get("/internal/deployments")
def deployments() -> list[dict]:
    return revision_state.deployment_history
