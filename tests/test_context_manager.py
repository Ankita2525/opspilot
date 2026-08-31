from __future__ import annotations

from datetime import datetime

from backend.app.context.manager import ContextManager
from backend.app.context.models import EvidenceType, IncidentContext
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse
from simulator.environment import SimulatedEnvironment

CHECKOUT_ID = "checkout-db-pool-regression"
CHECKOUT_SERVICE = "checkout-api"
AUTH_ID = "auth-token-validation-regression"
AUTH_SERVICE = "auth-service"
PAYMENTS_ID = "payments-provider-timeout-regression"
PAYMENTS_SERVICE = "payments-service"
FORBIDDEN = ("known_root_cause", "expected_remediation", "recovered_metrics")


def _tools(scenario_id: str) -> tuple[SimulatedEnvironment, DiagnosticTools]:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    return environment, DiagnosticTools(environment)


def _build_checkout(manager: ContextManager | None = None) -> IncidentContext:
    _, tools = _tools(CHECKOUT_ID)
    return (manager or ContextManager()).build(
        incident_id="inc-checkout-001",
        affected_service=CHECKOUT_SERVICE,
        metrics=tools.query_metrics(CHECKOUT_SERVICE),
        deployments=tools.get_recent_deployments(CHECKOUT_SERVICE),
        logs=tools.get_service_logs(CHECKOUT_SERVICE),
    )


def test_build_returns_incident_context() -> None:
    context = _build_checkout()

    assert isinstance(context, IncidentContext)
    assert context.incident_id == "inc-checkout-001"
    assert context.affected_service == CHECKOUT_SERVICE
    assert context.context_version == 1


def test_symptom_summary_contains_checkout_metrics() -> None:
    context = _build_checkout()

    assert "checkout-api" in context.symptom_summary
    assert "1940" in context.symptom_summary
    assert "8.2" in context.symptom_summary


def test_metric_evidence_exists() -> None:
    context = _build_checkout()
    metrics = [
        item for item in context.evidence if item.evidence_type == EvidenceType.METRIC
    ]

    assert metrics
    assert metrics[0].evidence_id == "metric-checkout-api"
    assert "1940" in metrics[0].summary
    assert "8.2" in metrics[0].summary


def test_deployment_evidence_includes_v1_18_3() -> None:
    context = _build_checkout()
    deployments = [
        item
        for item in context.evidence
        if item.evidence_type == EvidenceType.DEPLOYMENT
    ]

    assert any("v1.18.3" in item.summary for item in deployments)
    assert any(item.evidence_id == "deployment-checkout-api-v1.18.3" for item in deployments)


def test_db_timeout_log_evidence_exists() -> None:
    context = _build_checkout()
    logs = [item for item in context.evidence if item.evidence_type == EvidenceType.LOG]
    combined = " ".join(item.summary.lower() for item in logs)

    assert "database connection pool timeout" in combined


def test_error_logs_rank_above_info_logs() -> None:
    manager = ContextManager()
    metrics = MetricResponse(
        service=CHECKOUT_SERVICE,
        p95_latency_ms=1940,
        error_rate_percent=8.2,
        timestamp=datetime(2026, 8, 30, 14, 3),
    )
    context = manager.build(
        incident_id="inc-rank",
        affected_service=CHECKOUT_SERVICE,
        metrics=metrics,
        deployments=[],
        logs=[
            LogResponse(
                service=CHECKOUT_SERVICE,
                timestamp=datetime(2026, 8, 30, 14, 3, 1),
                level="INFO",
                message="health check passed",
            ),
            LogResponse(
                service=CHECKOUT_SERVICE,
                timestamp=datetime(2026, 8, 30, 14, 3, 2),
                level="ERROR",
                message="database connection pool timeout",
            ),
        ],
    )
    log_items = [
        item for item in context.evidence if item.evidence_type == EvidenceType.LOG
    ]
    error = next(item for item in log_items if "timeout" in item.summary.lower())
    info = next(item for item in log_items if "health check" in item.summary.lower())

    assert error.relevance_score > info.relevance_score
    assert context.evidence.index(error) < context.evidence.index(info)


def test_evidence_is_sorted_deterministically() -> None:
    first = _build_checkout()
    second = _build_checkout()

    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]
    scores = [item.relevance_score for item in first.evidence]
    assert scores == sorted(scores, reverse=True)


def test_max_evidence_items_is_respected() -> None:
    manager = ContextManager(max_evidence_items=3)
    metrics = MetricResponse(
        service=CHECKOUT_SERVICE,
        p95_latency_ms=1940,
        error_rate_percent=8.2,
        timestamp=datetime(2026, 8, 30, 14, 3),
    )
    logs = [
        LogResponse(
            service=CHECKOUT_SERVICE,
            timestamp=datetime(2026, 8, 30, 14, 3, index),
            level="INFO",
            message=f"routine heartbeat {index}",
        )
        for index in range(10)
    ]
    context = manager.build(
        incident_id="inc-bound",
        affected_service=CHECKOUT_SERVICE,
        metrics=metrics,
        deployments=[],
        logs=logs,
    )

    assert len(context.evidence) == 3
    assert any(item.evidence_type == EvidenceType.METRIC for item in context.evidence)


def test_deterministic_ids_are_stable() -> None:
    first = _build_checkout()
    second = _build_checkout()

    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]
    assert first.recent_changes[0].evidence_id == "deployment-checkout-api-v1.18.3"


def test_recent_changes_contains_deployment_evidence() -> None:
    context = _build_checkout()

    assert context.recent_changes
    assert all(
        item.evidence_type == EvidenceType.DEPLOYMENT for item in context.recent_changes
    )
    assert any("v1.18.3" in item.summary for item in context.recent_changes)


def test_context_does_not_contain_simulator_ground_truth() -> None:
    context = _build_checkout()
    payload = context.model_dump_json()

    for token in FORBIDDEN:
        assert token not in payload


def test_auth_context_includes_token_evidence() -> None:
    _, tools = _tools(AUTH_ID)
    context = ContextManager().build(
        incident_id="inc-auth-001",
        affected_service=AUTH_SERVICE,
        metrics=tools.query_metrics(AUTH_SERVICE),
        deployments=tools.get_recent_deployments(AUTH_SERVICE),
        logs=tools.get_service_logs(AUTH_SERVICE),
    )
    combined = " ".join(item.summary.lower() for item in context.evidence)

    assert "v2.7.1" in combined
    assert "signature" in combined or "token" in combined
    assert "auth_token_validation_regression" not in combined


def test_payments_context_includes_timeout_evidence() -> None:
    _, tools = _tools(PAYMENTS_ID)
    context = ContextManager().build(
        incident_id="inc-payments-001",
        affected_service=PAYMENTS_SERVICE,
        metrics=tools.query_metrics(PAYMENTS_SERVICE),
        deployments=tools.get_recent_deployments(PAYMENTS_SERVICE),
        logs=tools.get_service_logs(PAYMENTS_SERVICE),
    )
    combined = " ".join(item.summary.lower() for item in context.evidence)

    assert "v3.4.2" in combined
    assert "timeout" in combined
    assert "payment_provider_timeout_regression" not in combined


def test_context_generation_does_not_mutate_environment() -> None:
    environment, tools = _tools(CHECKOUT_ID)
    before_metrics = environment.query_metrics(CHECKOUT_SERVICE)
    before_logs = environment.get_logs(CHECKOUT_SERVICE)
    before_deployments = environment.get_recent_deployments(CHECKOUT_SERVICE)

    ContextManager().build(
        incident_id="inc-checkout-001",
        affected_service=CHECKOUT_SERVICE,
        metrics=tools.query_metrics(CHECKOUT_SERVICE),
        deployments=tools.get_recent_deployments(CHECKOUT_SERVICE),
        logs=tools.get_service_logs(CHECKOUT_SERVICE),
    )

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    assert environment.query_metrics(CHECKOUT_SERVICE) == before_metrics
    assert environment.get_logs(CHECKOUT_SERVICE) == before_logs
    assert environment.get_recent_deployments(CHECKOUT_SERVICE) == before_deployments
