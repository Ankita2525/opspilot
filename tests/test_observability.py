from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.observability.tracing import get_tracer
from backend.app.safety.approvals import ApprovalService
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-trace-001"
PROPOSAL_ID = "prop-trace-001"
THREAD_ID = "thread-trace-001"
FORBIDDEN_ATTRS = ("known_root_cause", "expected_remediation", "GROQ_API_KEY")

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


@pytest.fixture(autouse=True)
def _clear_spans():
    _EXPORTER.clear()
    yield
    _EXPORTER.clear()


def _loaded_env() -> SimulatedEnvironment:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    return environment


def _span(name: str):
    matches = [span for span in _EXPORTER.get_finished_spans() if span.name == name]
    assert matches, (
        f"Expected span {name!r}, got {[s.name for s in _EXPORTER.get_finished_spans()]}"
    )
    return matches[0]


def _all_attribute_text() -> str:
    parts: list[str] = []
    for span in _EXPORTER.get_finished_spans():
        attributes = span.attributes or {}
        for key, value in attributes.items():
            parts.append(str(key))
            parts.append(str(value))
    return " ".join(parts)


def test_query_metrics_emits_span() -> None:
    tools = DiagnosticTools(_loaded_env())

    tools.query_metrics(SERVICE)

    assert _span("opspilot.tool.query_metrics").name == "opspilot.tool.query_metrics"


def test_get_service_logs_emits_span() -> None:
    tools = DiagnosticTools(_loaded_env())

    tools.get_service_logs(SERVICE)

    assert _span("opspilot.tool.get_service_logs").name == "opspilot.tool.get_service_logs"


def test_get_recent_deployments_emits_span() -> None:
    tools = DiagnosticTools(_loaded_env())

    tools.get_recent_deployments(SERVICE)

    assert (
        _span("opspilot.tool.get_recent_deployments").name
        == "opspilot.tool.get_recent_deployments"
    )


def test_diagnostic_spans_contain_checkout_api_service() -> None:
    tools = DiagnosticTools(_loaded_env())
    tools.query_metrics(SERVICE)
    tools.get_service_logs(SERVICE)
    tools.get_recent_deployments(SERVICE)

    for name in (
        "opspilot.tool.query_metrics",
        "opspilot.tool.get_service_logs",
        "opspilot.tool.get_recent_deployments",
    ):
        assert _span(name).attributes["opspilot.service"] == SERVICE


def test_hypothesis_engine_emits_generate_span() -> None:
    environment = _loaded_env()
    tools = DiagnosticTools(environment)
    engine = HypothesisEngine(FakeModelProvider())

    engine.analyze(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        metrics=tools.query_metrics(SERVICE),
        deployments=tools.get_recent_deployments(SERVICE),
        logs=tools.get_service_logs(SERVICE),
    )

    assert _span("opspilot.hypothesis.generate").name == "opspilot.hypothesis.generate"


def test_hypothesis_span_contains_incident_id() -> None:
    environment = _loaded_env()
    tools = DiagnosticTools(environment)
    engine = HypothesisEngine(FakeModelProvider())

    engine.analyze(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        metrics=tools.query_metrics(SERVICE),
        deployments=tools.get_recent_deployments(SERVICE),
        logs=tools.get_service_logs(SERVICE),
    )

    assert (
        _span("opspilot.hypothesis.generate").attributes["opspilot.incident_id"]
        == INCIDENT_ID
    )


def test_hypothesis_span_records_recommended_action() -> None:
    environment = _loaded_env()
    tools = DiagnosticTools(environment)
    engine = HypothesisEngine(FakeModelProvider())

    engine.analyze(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        metrics=tools.query_metrics(SERVICE),
        deployments=tools.get_recent_deployments(SERVICE),
        logs=tools.get_service_logs(SERVICE),
    )

    attributes = _span("opspilot.hypothesis.generate").attributes
    assert attributes["opspilot.recommended_action"] == "rollback_deployment"
    assert "opspilot.hypothesis_count" in attributes


def test_coordinator_emits_incident_response_start_span() -> None:
    environment = _loaded_env()
    diagnostics = DiagnosticTools(environment)
    approvals = ApprovalService()
    coordinator = IncidentResponseCoordinator(
        investigation_workflow=InvestigationWorkflow(
            tools=diagnostics,
            hypothesis_engine=HypothesisEngine(FakeModelProvider()),
        ),
        remediation_workflow=RemediationApprovalWorkflow(
            remediation_tools=RemediationTools(environment, approvals),
            approvals=approvals,
            diagnostic_tools=diagnostics,
        ),
    )

    coordinator.start(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        remediation_thread_id=THREAD_ID,
        proposal_id=PROPOSAL_ID,
    )

    assert (
        _span("opspilot.incident_response.start").name
        == "opspilot.incident_response.start"
    )


def test_coordinator_span_records_final_start_status() -> None:
    environment = _loaded_env()
    diagnostics = DiagnosticTools(environment)
    approvals = ApprovalService()
    coordinator = IncidentResponseCoordinator(
        investigation_workflow=InvestigationWorkflow(
            tools=diagnostics,
            hypothesis_engine=HypothesisEngine(FakeModelProvider()),
        ),
        remediation_workflow=RemediationApprovalWorkflow(
            remediation_tools=RemediationTools(environment, approvals),
            approvals=approvals,
            diagnostic_tools=diagnostics,
        ),
    )

    started = coordinator.start(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        remediation_thread_id=THREAD_ID,
        proposal_id=PROPOSAL_ID,
    )

    attributes = _span("opspilot.incident_response.start").attributes
    assert attributes["opspilot.status"] == started.status
    assert attributes["opspilot.status"] == "approval_required"


def test_propose_rollback_emits_span() -> None:
    environment = _loaded_env()
    tools = RemediationTools(environment, ApprovalService())

    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version="v1.18.3",
    )

    span = _span("opspilot.remediation.propose")
    assert span.attributes["opspilot.action"] == "rollback_deployment"
    assert span.attributes["opspilot.risk_level"] == "high_risk"


def test_execute_rollback_emits_span() -> None:
    environment = _loaded_env()
    approvals = ApprovalService()
    tools = RemediationTools(environment, approvals)
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version="v1.18.3",
    )
    approvals.approve(PROPOSAL_ID)

    tools.execute_rollback(PROPOSAL_ID)

    span = _span("opspilot.remediation.execute")
    assert span.attributes["opspilot.proposal_id"] == PROPOSAL_ID
    assert span.attributes["opspilot.action"] == "rollback_deployment"


def test_successful_execution_span_records_success() -> None:
    environment = _loaded_env()
    approvals = ApprovalService()
    tools = RemediationTools(environment, approvals)
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version="v1.18.3",
    )
    approvals.approve(PROPOSAL_ID)

    result = tools.execute_rollback(PROPOSAL_ID)

    assert result.success is True
    assert (
        _span("opspilot.remediation.execute").attributes["opspilot.execution_success"]
        is True
    )


def test_span_attributes_do_not_contain_secrets_or_ground_truth() -> None:
    environment = _loaded_env()
    diagnostics = DiagnosticTools(environment)
    approvals = ApprovalService()
    coordinator = IncidentResponseCoordinator(
        investigation_workflow=InvestigationWorkflow(
            tools=diagnostics,
            hypothesis_engine=HypothesisEngine(FakeModelProvider()),
        ),
        remediation_workflow=RemediationApprovalWorkflow(
            remediation_tools=RemediationTools(environment, approvals),
            approvals=approvals,
            diagnostic_tools=diagnostics,
        ),
    )
    coordinator.start(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        remediation_thread_id=THREAD_ID,
        proposal_id=PROPOSAL_ID,
    )

    blob = _all_attribute_text()
    for forbidden in FORBIDDEN_ATTRS:
        assert forbidden not in blob


def test_tracing_does_not_alter_business_behavior() -> None:
    tools = DiagnosticTools(_loaded_env())

    metrics = tools.query_metrics(SERVICE)

    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_default_tracer_is_usable() -> None:
    tracer = get_tracer()

    with tracer.start_as_current_span("opspilot.test.noop"):
        tools = DiagnosticTools(_loaded_env())
        metrics = tools.query_metrics(SERVICE)

    assert metrics.service == SERVICE
    assert metrics.p95_latency_ms == 1940
