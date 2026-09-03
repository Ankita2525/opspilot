from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.requests import Request
from starlette.responses import Response

SERVICE_NAME = os.environ.get("SANDBOX_SERVICE_NAME", "sandbox-service")
SERVICE_REVISION = os.environ.get("SANDBOX_INITIAL_REVISION", "v1.0.0")
CONTROL_TOKEN = os.environ.get("SANDBOX_CONTROL_TOKEN", "sandbox-control-test-token")
# Dead-man TTL for intentionally activated faults. Independent of OpsPilot.
DEFAULT_FAULT_TTL_SECONDS = int(os.environ.get("SANDBOX_FAULT_TTL_SECONDS", "300"))

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "revision", "method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["service", "revision", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
ERROR_COUNT = Counter(
    "http_errors_total",
    "HTTP error responses",
    ["service", "revision", "status"],
)


class StructuredLogger:
    def __init__(self, service: str, revision: str) -> None:
        self._service = service
        self._revision = revision
        self._logger = logging.getLogger(service)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

    def log(
        self,
        level: str,
        message: str,
        *,
        correlation_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self._service,
            "revision": self._revision,
            "severity": level,
            "message": message,
        }
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if extra:
            payload.update(extra)
        line = json.dumps(payload, default=str)
        if level == "ERROR":
            self._logger.error(line)
        elif level == "WARN":
            self._logger.warning(line)
        else:
            self._logger.info(line)
        from sandbox.common.otel_logging import emit_otel_log

        emit_otel_log(
            service=self._service,
            revision=self._revision,
            level=level,
            message=message,
            correlation_id=correlation_id,
            extra=extra,
        )


class RevisionState:
    """Mutable sandbox release state with rollback and self-reverting fault TTL.

    The TTL is owned by the sidecar process. OpsPilot disappearance cannot leave
    an intentional fault active beyond the bounded window. Explicit rollback
    cancels the timer. Heartbeat refresh is intentionally not required when
    approval_timeout < fault_TTL.
    """

    def __init__(
        self,
        *,
        service: str,
        healthy_revision: str,
        faulty_revision: str,
        initial_revision: str | None = None,
        fault_ttl_seconds: int | None = None,
    ) -> None:
        self.service = service
        self.healthy_revision = healthy_revision
        self.faulty_revision = faulty_revision
        self.current_revision = initial_revision or healthy_revision
        self.previous_revision = healthy_revision
        self.activated_at = datetime.now(UTC)
        self.fault_ttl_seconds = (
            fault_ttl_seconds
            if fault_ttl_seconds is not None
            else DEFAULT_FAULT_TTL_SECONDS
        )
        self.fault_expires_at: datetime | None = None
        self.deployment_history: list[dict[str, Any]] = [
            {
                "service": service,
                "version": self.current_revision,
                "timestamp": self.activated_at.isoformat(),
            }
        ]
        self._lock = threading.RLock()
        self._ttl_timer: threading.Timer | None = None
        self._on_auto_revert: Callable[[], None] | None = None
        self._generation = 0

    def set_auto_revert_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_auto_revert = callback

    @property
    def is_faulty(self) -> bool:
        return self.current_revision == self.faulty_revision

    def activate_faulty(self, *, ttl_seconds: int | None = None) -> None:
        with self._lock:
            if self.current_revision != self.faulty_revision:
                self.previous_revision = self.current_revision
                self.current_revision = self.faulty_revision
                self.activated_at = datetime.now(UTC)
                self.deployment_history.insert(
                    0,
                    {
                        "service": self.service,
                        "version": self.faulty_revision,
                        "timestamp": self.activated_at.isoformat(),
                    },
                )
            ttl = self.fault_ttl_seconds if ttl_seconds is None else max(1, ttl_seconds)
            self.fault_expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
            self._arm_timer(ttl)

    def rollback(self, version: str) -> None:
        with self._lock:
            if version not in {
                self.faulty_revision,
                self.current_revision,
                self.healthy_revision,
            }:
                raise ValueError(f"Unknown deployment version: {version}")
            self._cancel_timer()
            self.fault_expires_at = None
            if self.current_revision == self.healthy_revision:
                return
            self.previous_revision = self.current_revision
            self.current_revision = self.healthy_revision
            self.activated_at = datetime.now(UTC)
            self.deployment_history.insert(
                0,
                {
                    "service": self.service,
                    "version": self.healthy_revision,
                    "timestamp": self.activated_at.isoformat(),
                },
            )

    def clear_fault(self) -> dict[str, Any]:
        """Idempotent return to healthy baseline; cancels TTL."""
        self.rollback(self.healthy_revision)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service": self.service,
                "current_revision": self.current_revision,
                "previous_revision": self.previous_revision,
                "healthy_revision": self.healthy_revision,
                "faulty_revision": self.faulty_revision,
                "activated_at": self.activated_at.isoformat(),
                "is_faulty": self.is_faulty,
                "fault_ttl_seconds": self.fault_ttl_seconds,
                "fault_expires_at": (
                    self.fault_expires_at.isoformat() if self.fault_expires_at else None
                ),
            }

    def _arm_timer(self, ttl_seconds: float) -> None:
        self._cancel_timer()
        self._generation += 1
        generation = self._generation

        def _fire() -> None:
            self._auto_revert(generation)

        timer = threading.Timer(ttl_seconds, _fire)
        timer.daemon = True
        self._ttl_timer = timer
        timer.start()

    def _cancel_timer(self) -> None:
        if self._ttl_timer is not None:
            self._ttl_timer.cancel()
            self._ttl_timer = None
        self._generation += 1

    def _auto_revert(self, generation: int) -> None:
        callback: Callable[[], None] | None = None
        with self._lock:
            if generation != self._generation:
                return
            if not self.is_faulty:
                self.fault_expires_at = None
                return
            now = datetime.now(UTC)
            if self.fault_expires_at is not None and now < self.fault_expires_at:
                remaining = (self.fault_expires_at - now).total_seconds()
                self._arm_timer(max(remaining, 0.05))
                return
            self._cancel_timer()
            self.fault_expires_at = None
            self.previous_revision = self.current_revision
            self.current_revision = self.healthy_revision
            self.activated_at = now
            self.deployment_history.insert(
                0,
                {
                    "service": self.service,
                    "version": self.healthy_revision,
                    "timestamp": now.isoformat(),
                    "reason": "fault_ttl_expired",
                },
            )
            callback = self._on_auto_revert
        if callback is not None:
            try:
                callback()
            except Exception:
                logging.getLogger(self.service).exception(
                    "sandbox auto-revert callback failed"
                )


def verify_control_token(request: Request) -> None:
    token = request.headers.get("X-Sandbox-Control-Token")
    if token != CONTROL_TOKEN:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Unauthorized sandbox control request")


async def metrics_endpoint(_request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_request(
    *,
    method: str,
    path: str,
    status: int,
    duration_seconds: float,
    revision: str,
) -> None:
    labels = {
        "service": SERVICE_NAME,
        "revision": revision,
        "method": method,
        "path": path,
    }
    REQUEST_COUNT.labels(**labels, status=str(status)).inc()
    REQUEST_DURATION.labels(**labels).observe(duration_seconds)
    if status >= 400:
        ERROR_COUNT.labels(
            service=SERVICE_NAME,
            revision=revision,
            status=str(status),
        ).inc()
