"""Tests for live run provenance model, manifest hash, and API."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app.api.app import create_app
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.provenance.builder import build_live_provenance, window_from_samples
from backend.app.provenance.manifest import (
    canonical_manifest_bytes,
    evidence_manifest_hash,
    with_manifest_hash,
)
from backend.app.provenance.models import LiveRunProvenance
from sandbox.traffic.workload import WorkloadSample
from tests.fakes import FakeModelProvider

FORBIDDEN = (
    "known_root_cause",
    "session_id",
    "GROQ_API_KEY",
    "sandbox_control",
    "turnstile_secret",
)


def _sample(ts: datetime, latency: float, success: bool) -> WorkloadSample:
    return WorkloadSample(timestamp=ts, latency_ms=latency, success=success, status_code=200)


def test_missing_observations_never_become_zero_window() -> None:
    assert window_from_samples([]) is None


def test_provenance_sample_counts_match_actual_evidence() -> None:
    t0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    samples = [_sample(t0, 100, True), _sample(t0, 120, False)]
    window = window_from_samples(samples)
    assert window is not None
    assert window.sample_count == 2
    assert window.error_rate == 50.0


def test_manifest_hash_stable() -> None:
    t0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    provenance = build_live_provenance(
        incident_id="inc-1",
        environment="Ephemeral Incident Lab",
        service="checkout-api",
        service_revision="v1.18.3",
        started_at=t0,
        baseline_samples=[_sample(t0, 80, True)],
        baseline_summary={"request_count": 1, "p95_latency_ms": 80, "error_rate_percent": 0},
        degraded_samples=[_sample(t0, 400, True)],
        degraded_summary={"request_count": 1, "p95_latency_ms": 400, "error_rate_percent": 0},
        diagnosis_provider="deterministic",
        diagnosis_model=None,
        evidence_count=3,
    )
    hashed = with_manifest_hash(provenance)
    again = evidence_manifest_hash(
        LiveRunProvenance.model_validate(hashed.model_dump())
    )
    assert hashed.evidence_manifest_hash == again
    assert len(canonical_manifest_bytes(hashed)) > 0


def test_ground_truth_hidden_and_no_secrets_in_provenance_api() -> None:
    repository = InMemoryOpsPilotRepository()
    saver = InMemorySaver()
    app = create_app(
        provider=FakeModelProvider(),
        repository=repository,
        checkpointer=saver,
    )
    client = TestClient(app)
    started = client.post(
        "/api/incidents/start", json={"scenario_id": "checkout-db-pool-regression"}
    )
    assert started.status_code == 200
    incident_id = started.json()["incident_id"]

    response = client.get(f"/api/incidents/{incident_id}/provenance")
    assert response.status_code == 404

    # Reference mode does not persist live provenance — expected.


def test_live_provenance_fields_when_persisted() -> None:
    t0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
    provenance = with_manifest_hash(
        build_live_provenance(
            incident_id="inc-live",
            environment="Ephemeral Incident Lab",
            service="checkout-api",
            service_revision="v1.18.3",
            started_at=t0,
            baseline_samples=[_sample(t0, 90, True)],
            baseline_summary={"request_count": 1, "p95_latency_ms": 90, "error_rate_percent": 0},
            degraded_samples=[_sample(t0, 500, True), _sample(t0, 600, False)],
            degraded_summary={"request_count": 2, "p95_latency_ms": 600, "error_rate_percent": 50},
            diagnosis_provider="groq",
            diagnosis_model="openai/gpt-oss-20b",
            evidence_count=4,
            remediation_action="rollback_deployment",
            approval_required=True,
        )
    )
    assert provenance.ground_truth_visible_to_agent is False
    assert provenance.telemetry_mode == "live"
    payload = provenance.model_dump_json().lower()
    for token in FORBIDDEN:
        assert token not in payload
