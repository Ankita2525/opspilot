from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.requests import Request
from starlette.responses import Response

SERVICE_NAME = os.environ.get("SANDBOX_SERVICE_NAME", "sandbox-service")
SERVICE_REVISION = os.environ.get("SANDBOX_INITIAL_REVISION", "v1.0.0")
CONTROL_TOKEN = os.environ.get("SANDBOX_CONTROL_TOKEN", "sandbox-control-test-token")

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
    """Mutable sandbox release state with rollback support."""

    def __init__(
        self,
        *,
        service: str,
        healthy_revision: str,
        faulty_revision: str,
        initial_revision: str | None = None,
    ) -> None:
        self.service = service
        self.healthy_revision = healthy_revision
        self.faulty_revision = faulty_revision
        self.current_revision = initial_revision or healthy_revision
        self.previous_revision = healthy_revision
        self.activated_at = datetime.now(UTC)
        self.deployment_history: list[dict[str, Any]] = [
            {
                "service": service,
                "version": self.current_revision,
                "timestamp": self.activated_at.isoformat(),
            }
        ]

    @property
    def is_faulty(self) -> bool:
        return self.current_revision == self.faulty_revision

    def activate_faulty(self) -> None:
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

    def rollback(self, version: str) -> None:
        if version not in {self.faulty_revision, self.current_revision, self.healthy_revision}:
            raise ValueError(f"Unknown deployment version: {version}")
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

    def status(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "current_revision": self.current_revision,
            "previous_revision": self.previous_revision,
            "healthy_revision": self.healthy_revision,
            "faulty_revision": self.faulty_revision,
            "activated_at": self.activated_at.isoformat(),
            "is_faulty": self.is_faulty,
        }


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
