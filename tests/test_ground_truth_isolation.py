from __future__ import annotations

import json

import pytest

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.context.manager import ContextManager
from backend.app.models.deterministic_provider import DeterministicModelProvider
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse
from backend.app.telemetry.health import utc_now


def test_live_context_payload_excludes_hidden_fault_identity() -> None:
    engine = HypothesisEngine(DeterministicModelProvider())
    now = utc_now()
    context = engine.build_context(
        incident_id="inc-live-001",
        affected_service="checkout-api",
        metrics=MetricResponse(
            service="checkout-api",
            p95_latency_ms=900,
            error_rate_percent=5.0,
            timestamp=now,
        ),
        deployments=[
            DeploymentResponse(
                service="checkout-api",
                version="v1.18.3",
                timestamp=now,
            )
        ],
        logs=[
            LogResponse(
                service="checkout-api",
                timestamp=now,
                level="ERROR",
                message="database connection pool timeout",
            )
        ],
    )
    payload = json.dumps(
        {
            "incident_id": context.incident_id,
            "symptom_summary": context.symptom_summary,
            "evidence": [item.summary for item in context.evidence],
        }
    )
    for forbidden in (
        "injected_fault",
        "hidden_root_cause",
        "expected_recovery",
        "known_root_cause",
        "checkout-db-pool-regression",
    ):
        assert forbidden not in payload
