from __future__ import annotations

import time
from typing import TYPE_CHECKING

from opentelemetry import metrics

if TYPE_CHECKING:
    pass

_meter = metrics.get_meter("opspilot")

live_incidents_started = _meter.create_counter(
    "opspilot.live_incidents_started",
    description="Live incidents started",
)
live_incidents_completed = _meter.create_counter(
    "opspilot.live_incidents_completed",
    description="Live incidents completed",
)
model_calls = _meter.create_counter(
    "opspilot.model_calls",
    description="Model provider calls",
)
approval_count = _meter.create_counter(
    "opspilot.approval_actions",
    description="Approval and rejection actions",
)
lease_failures = _meter.create_counter(
    "opspilot.lease_failures",
    description="Sandbox lease acquisition failures",
)
cleanup_count = _meter.create_counter(
    "opspilot.cleanup_runs",
    description="Expired incident cleanup runs",
)
investigation_duration = _meter.create_histogram(
    "opspilot.investigation_duration_seconds",
    description="Investigation duration in seconds",
    unit="s",
)
model_call_latency = _meter.create_histogram(
    "opspilot.model_call_latency_seconds",
    description="Model provider call latency",
    unit="s",
)


class LatencyTracker:
    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start
