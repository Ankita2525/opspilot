"""Tests for live run provenance model, manifest hash, and API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app.agent.incident_response import IncidentResponseResumeResult
from backend.app.api.app import create_app
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.provenance.builder import (
    build_live_provenance,
    recovery_from_verification,
    window_from_samples,
)
from backend.app.provenance.store import ProvenanceStore
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



def test_recovery_provenance_requires_actual_post_remediation_observations() -> None:
    remediation_at = datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC)
    source_at = remediation_at.replace(second=10)

    recovery = recovery_from_verification(
        {
            "status": "resolved",
            "summary": {
                "request_count": 2,
                "p95_latency_ms": 100,
                "error_rate_percent": 0.0,
                "newest_sample_at": source_at.isoformat(),
            },
            "observations": [],
            "prometheus": {
                "observed_at": source_at.isoformat(),
            },
        },
        remediation_at=remediation_at,
    )

    assert recovery is not None
    assert recovery.all_samples_post_remediation is False


def test_recovery_provenance_rejects_stale_prometheus_source_timestamp() -> None:
    remediation_at = datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC)
    workload_at = remediation_at.replace(second=10)
    stale_metric_at = remediation_at.replace(second=0) - timedelta(seconds=5)

    recovery = recovery_from_verification(
        {
            "status": "resolved",
            "summary": {
                "request_count": 2,
                "p95_latency_ms": 100,
                "error_rate_percent": 0.0,
                "newest_sample_at": workload_at.isoformat(),
            },
            "observations": [
                {
                    "workload": {
                        "newest_sample_at": workload_at.isoformat(),
                    }
                }
            ],
            "prometheus": {
                "observed_at": stale_metric_at.isoformat(),
            },
        },
        remediation_at=remediation_at,
    )

    assert recovery is not None
    assert recovery.latest_metric_timestamp == stale_metric_at
    assert recovery.all_samples_post_remediation is False


def test_recovery_provenance_accepts_workload_and_prometheus_after_remediation() -> None:
    remediation_at = datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC)
    workload_at = remediation_at.replace(second=10)
    metric_at = remediation_at.replace(second=8)

    recovery = recovery_from_verification(
        {
            "status": "resolved",
            "summary": {
                "request_count": 2,
                "p95_latency_ms": 100,
                "error_rate_percent": 0.0,
                "newest_sample_at": workload_at.isoformat(),
            },
            "observations": [
                {
                    "workload": {
                        "newest_sample_at": workload_at.isoformat(),
                    }
                }
            ],
            "prometheus": {
                "observed_at": metric_at.isoformat(),
            },
        },
        remediation_at=remediation_at,
    )

    assert recovery is not None
    assert recovery.all_samples_post_remediation is True



def test_resume_freshness_cannot_predate_public_execution_timestamp() -> None:
    repository = InMemoryOpsPilotRepository()
    store = ProvenanceStore(repository)

    started_at = datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC)
    remediation_at = datetime(2026, 9, 9, 20, 1, 0, tzinfo=UTC)
    source_at = remediation_at + timedelta(seconds=5)
    executed_at = remediation_at + timedelta(seconds=10)

    existing = with_manifest_hash(
        build_live_provenance(
            incident_id="inc-execution-boundary",
            environment="Ephemeral Incident Lab",
            service="auth-service",
            service_revision="v1",
            started_at=started_at,
            baseline_samples=[_sample(started_at, 80, True)],
            baseline_summary={
                "request_count": 1,
                "p95_latency_ms": 80,
                "error_rate_percent": 0.0,
            },
            degraded_samples=[_sample(started_at, 500, False)],
            degraded_summary={
                "request_count": 1,
                "p95_latency_ms": 500,
                "error_rate_percent": 100.0,
            },
            diagnosis_provider="groq",
            diagnosis_model="test-model",
            evidence_count=2,
            remediation_action="rollback_deployment",
            approval_required=True,
        )
    )
    store._persist(existing)

    resumed = IncidentResponseResumeResult(
        status="resolved",
        execution_success=True,
        recovered_p95_latency_ms=100,
        recovered_error_rate_percent=0.0,
        approval_status="approved",
    )

    recovery_result = {
        "status": "resolved",
        "summary": {
            "request_count": 2,
            "p95_latency_ms": 100,
            "error_rate_percent": 0.0,
            "newest_sample_at": source_at.isoformat(),
        },
        "observations": [
            {
                "workload": {
                    "newest_sample_at": source_at.isoformat(),
                }
            }
        ],
        "prometheus": {
            "observed_at": source_at.isoformat(),
        },
    }

    updated = store.save_after_resume(
        incident_id="inc-execution-boundary",
        resumed=resumed,
        recovery_result=recovery_result,
        remediation_at=remediation_at,
        approved_at=remediation_at - timedelta(seconds=1),
        executed_at=executed_at,
    )

    assert updated is not None
    assert updated.remediation is not None
    assert updated.remediation.executed_at == executed_at
    assert updated.recovery is not None
    assert updated.recovery.latest_metric_timestamp == source_at

    # Telemetry is newer than the verifier boundary but older than the
    # publicly persisted execution timestamp, so freshness must fail closed.
    assert updated.recovery.all_samples_post_remediation is False


def test_resume_fallback_never_invents_post_remediation_freshness() -> None:
    repository = InMemoryOpsPilotRepository()
    store = ProvenanceStore(repository)

    started_at = datetime(2026, 9, 9, 19, 59, 0, tzinfo=UTC)
    remediation_at = datetime(2026, 9, 9, 20, 0, 0, tzinfo=UTC)

    existing = with_manifest_hash(
        build_live_provenance(
            incident_id="inc-fallback-freshness",
            environment="Ephemeral Incident Lab",
            service="auth-service",
            service_revision="v1",
            started_at=started_at,
            baseline_samples=[_sample(started_at, 80, True)],
            baseline_summary={
                "request_count": 1,
                "p95_latency_ms": 80,
                "error_rate_percent": 0.0,
            },
            degraded_samples=[_sample(started_at, 500, False)],
            degraded_summary={
                "request_count": 1,
                "p95_latency_ms": 500,
                "error_rate_percent": 100.0,
            },
            diagnosis_provider="groq",
            diagnosis_model="test-model",
            evidence_count=2,
            remediation_action="rollback_deployment",
            approval_required=True,
        )
    )
    store._persist(existing)

    resumed = IncidentResponseResumeResult(
        status="resolved",
        execution_success=True,
        recovered_p95_latency_ms=100,
        recovered_error_rate_percent=0.0,
        approval_status="approved",
    )

    updated = store.save_after_resume(
        incident_id="inc-fallback-freshness",
        resumed=resumed,
        remediation_at=remediation_at,
    )

    assert updated is not None
    assert updated.recovery is not None
    assert updated.recovery.all_samples_post_remediation is not True
