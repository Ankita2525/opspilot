from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from opentelemetry import _logs
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

_initialized_services: set[str] = set()


def setup_otel_logging(service_name: str) -> None:
    """Configure OTLP log export when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    if service_name in _initialized_services:
        return
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    resource = Resource.create({"service.name": service_name})
    provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(endpoint=f"{endpoint.rstrip('/')}/v1/logs")
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    _logs.set_logger_provider(provider)
    _initialized_services.add(service_name)


def emit_otel_log(
    *,
    service: str,
    revision: str,
    level: str,
    message: str,
    correlation_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if service not in _initialized_services:
        return
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": service,
        "revision": revision,
        "severity": level,
        "message": message,
    }
    if correlation_id:
        payload["correlation_id"] = correlation_id
    if extra:
        payload.update(extra)
    now_ns = time.time_ns()
    _logs.get_logger(service).emit(
        timestamp=now_ns,
        observed_timestamp=now_ns,
        severity_text=level,
        body=json.dumps(payload, default=str),
        attributes={"revision": revision},
    )
