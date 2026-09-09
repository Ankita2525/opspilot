from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from backend.app.telemetry.clients import PrometheusClient, PrometheusConfig
from backend.app.telemetry.verification import RecoveryVerifier
from sandbox.traffic.workload import WorkloadSample


def test_prometheus_source_timestamp_uses_raw_sample_timestamp(monkeypatch) -> None:
    prometheus = PrometheusClient(
        PrometheusConfig(base_url="http://prometheus.test")
    )
    evaluated_at = datetime(2026, 9, 9, 20, 0, 10, tzinfo=UTC)
    source_sample_at = evaluated_at - timedelta(seconds=7)
    queries: list[str] = []

    def fake_query(promql: str) -> tuple[float, datetime] | None:
        queries.append(promql)
        return source_sample_at.timestamp(), evaluated_at

    monkeypatch.setattr(prometheus, "_query_instant", fake_query)

    observed_at = prometheus.query_latest_source_sample_timestamp("auth-service")

    assert observed_at == source_sample_at
    assert queries == [
        'max(timestamp(http_requests_total{service="auth-service"}))'
    ]


def test_recovery_rejects_fresh_evaluation_with_stale_source_sample(
    monkeypatch,
) -> None:
    remediation_at = datetime.now(UTC)
    evaluated_at = remediation_at + timedelta(seconds=10)
    stale_source_at = remediation_at - timedelta(seconds=5)

    prometheus = PrometheusClient(
        PrometheusConfig(base_url="http://prometheus.test")
    )

    monkeypatch.setattr(
        prometheus,
        "query_p95_latency_ms_with_timestamp",
        lambda service, window="2m": (100, evaluated_at),
    )
    monkeypatch.setattr(
        prometheus,
        "query_error_rate_percent_with_timestamp",
        lambda service, window="2m": (0.0, evaluated_at),
    )
    monkeypatch.setattr(
        prometheus,
        "query_latest_source_sample_timestamp",
        lambda service: stale_source_at,
        raising=False,
    )

    workload = MagicMock()
    workload.collect_baseline.return_value = [
        WorkloadSample(
            remediation_at + timedelta(seconds=2),
            100,
            True,
            200,
        )
    ]

    mapping = MagicMock()
    mapping.affected_service = "auth-service"

    verifier = RecoveryVerifier(
        scrape_interval_seconds=0.01,
        max_wait_seconds=0.05,
        required_consecutive=1,
    )

    result = verifier.verify(
        prometheus=prometheus,
        workload=workload,
        mapping=mapping,
        incident_id="inc-stale-prometheus-source",
        baseline_summary={
            "p95_latency_ms": 80,
            "error_rate_percent": 0.0,
        },
        remediation_at=remediation_at,
        sample_duration_seconds=0.01,
    )

    assert result["status"] == "verification_pending"
    assert result["recovered"] is False
    assert result["reason"] == "fresh_prometheus_metrics_unavailable"
    workload.collect_baseline.assert_not_called()


def test_metrics_pipeline_rejects_stale_source_scrape_even_when_query_is_fresh(
    monkeypatch,
) -> None:
    from backend.app.telemetry.pipeline_health import check_metrics_pipeline

    prometheus = PrometheusClient(
        PrometheusConfig(base_url="http://prometheus.test")
    )

    now = datetime.now(UTC)
    stale_source_at = now - timedelta(seconds=60)

    # Existing instant expression appears freshly evaluated.
    monkeypatch.setattr(
        prometheus,
        "query_request_rate_with_timestamp",
        lambda service, window="2m": (1.0, now),
    )

    # But the underlying Prometheus scrape is stale.
    monkeypatch.setattr(
        prometheus,
        "query_latest_source_sample_timestamp",
        lambda service: stale_source_at,
    )

    # Avoid retry delays; one failed freshness probe is enough for this test.
    monkeypatch.setattr(
        "backend.app.telemetry.pipeline_health.with_bounded_retry",
        lambda function, **kwargs: function(),
    )

    assert (
        check_metrics_pipeline(
            prometheus,
            "auth-service",
            max_age_seconds=15.0,
        )
        is False
    )


def test_live_metric_response_uses_source_scrape_timestamp(
    monkeypatch,
) -> None:
    from backend.app.telemetry.clients import LokiClient, LokiConfig
    from backend.app.telemetry.live import LiveTelemetryBackend

    prometheus = PrometheusClient(
        PrometheusConfig(base_url="http://prometheus.test")
    )
    loki = LokiClient(LokiConfig(base_url="http://loki.test"))

    backend = LiveTelemetryBackend(
        service="auth-service",
        prometheus=prometheus,
        loki=loki,
        control=MagicMock(),
    )

    source_observed_at = datetime(2026, 9, 9, 20, 0, 5, tzinfo=UTC)
    evaluated_at = source_observed_at + timedelta(seconds=7)

    monkeypatch.setattr(
        "backend.app.telemetry.live.check_metrics_pipeline",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        prometheus,
        "query_latest_source_sample_timestamp",
        lambda service: source_observed_at,
    )
    monkeypatch.setattr(
        prometheus,
        "query_p95_latency_ms_with_timestamp",
        lambda service, window="2m": (120, evaluated_at),
    )
    monkeypatch.setattr(
        prometheus,
        "query_error_rate_percent_with_timestamp",
        lambda service, window="2m": (0.0, evaluated_at),
    )

    metrics = backend.query_metrics("auth-service")

    assert metrics.timestamp == source_observed_at
    assert metrics.observed_at == source_observed_at
    assert metrics.p95_latency_ms == 120
    assert metrics.error_rate_percent == 0.0
