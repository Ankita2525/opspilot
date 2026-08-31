from __future__ import annotations

import json

import pytest
from langgraph.types import Interrupt

from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from simulator.environment import SimulatedEnvironment

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
PROPOSAL_ID = "prop-rollback-001"
THREAD_ID = "thread-remediation-001"


def _loaded() -> tuple[
    SimulatedEnvironment, ApprovalService, RemediationApprovalWorkflow
]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    approvals = ApprovalService()
    workflow = RemediationApprovalWorkflow(
        remediation_tools=RemediationTools(environment, approvals),
        approvals=approvals,
        diagnostic_tools=DiagnosticTools(environment),
    )
    return environment, approvals, workflow


def _start(workflow: RemediationApprovalWorkflow) -> dict:
    return workflow.start(
        thread_id=THREAD_ID,
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )


def _interrupt_payload(result: dict) -> dict:
    interrupts = result["__interrupt__"]
    assert interrupts
    first = interrupts[0]
    if isinstance(first, Interrupt):
        return first.value
    return first["value"] if isinstance(first, dict) else first.value


def test_workflow_compiles() -> None:
    _, _, workflow = _loaded()

    assert workflow._graph is not None


def test_start_reaches_langgraph_interrupt() -> None:
    _, _, workflow = _loaded()

    result = _start(workflow)

    assert "__interrupt__" in result
    assert result["__interrupt__"]
    payload = workflow.pending_interrupt(THREAD_ID)
    assert payload["type"] == "approval_required"


def test_interrupt_payload_contains_approval_details() -> None:
    _, _, workflow = _loaded()

    payload = _interrupt_payload(_start(workflow))

    assert payload["type"] == "approval_required"
    assert payload["proposal_id"] == PROPOSAL_ID
    assert payload["action"] == "rollback_deployment"
    assert payload["service"] == SERVICE
    assert payload["version"] == BAD_VERSION
    assert payload["risk_level"] == "high_risk"


def test_interrupt_payload_does_not_contain_simulator_ground_truth() -> None:
    _, _, workflow = _loaded()

    payload = json.dumps(_interrupt_payload(_start(workflow)))

    assert "known_root_cause" not in payload
    assert "expected_remediation" not in payload
    assert "db_connection_pool_regression" not in payload


def test_incident_unresolved_while_paused() -> None:
    environment, _, workflow = _loaded()

    _start(workflow)

    assert environment.is_resolved is False


def test_proposal_is_pending_while_paused() -> None:
    _, approvals, workflow = _loaded()

    _start(workflow)

    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.PENDING
    assert _start_status(workflow) == "approval_required"


def _start_status(workflow: RemediationApprovalWorkflow) -> str:
    snapshot = workflow._graph.get_state({"configurable": {"thread_id": THREAD_ID}})
    return snapshot.values["status"]


def test_unapproved_remediation_has_not_executed() -> None:
    environment, _, workflow = _loaded()

    _start(workflow)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_resume_approved_continues_same_graph_execution() -> None:
    _, _, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=True)

    assert "__interrupt__" not in result
    assert result["proposal_id"] == PROPOSAL_ID
    assert result["status"] == "resolved"


def test_approved_proposal_becomes_approved() -> None:
    _, approvals, workflow = _loaded()
    _start(workflow)

    workflow.resume(thread_id=THREAD_ID, approved=True)

    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.APPROVED


def test_approved_rollback_executes_successfully() -> None:
    _, _, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=True)

    assert result["execution_success"] is True


def test_approved_execution_recovers_metrics() -> None:
    _, _, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=True)

    assert result["recovered_p95_latency_ms"] == 218
    assert result["recovered_error_rate_percent"] == 0.3


def test_approved_flow_final_status_is_resolved() -> None:
    _, _, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=True)

    assert result["status"] == "resolved"


def test_simulator_audit_event_after_successful_execution() -> None:
    environment, _, workflow = _loaded()
    _start(workflow)
    workflow.resume(thread_id=THREAD_ID, approved=True)

    events = environment.get_audit_events()
    assert len(events) == 1
    assert events[0].action == "rollback_deployment"
    assert SERVICE in events[0].details
    assert BAD_VERSION in events[0].details


def test_resume_false_rejects_proposal() -> None:
    _, approvals, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=False)

    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.REJECTED
    assert result["approval_status"] == "rejected"


def test_rejected_flow_final_status_is_rejected() -> None:
    _, _, workflow = _loaded()
    _start(workflow)

    result = workflow.resume(thread_id=THREAD_ID, approved=False)

    assert result["status"] == "rejected"
    assert result["execution_success"] is False


def test_rejected_flow_does_not_resolve_incident() -> None:
    environment, _, workflow = _loaded()
    _start(workflow)
    workflow.resume(thread_id=THREAD_ID, approved=False)

    assert environment.is_resolved is False
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_rejected_flow_creates_no_rollback_audit_event() -> None:
    environment, _, workflow = _loaded()
    _start(workflow)
    workflow.resume(thread_id=THREAD_ID, approved=False)

    assert environment.get_audit_events() == []


def test_different_thread_id_does_not_resume_pending_workflow() -> None:
    environment, approvals, workflow = _loaded()
    _start(workflow)

    with pytest.raises(Exception):
        workflow.resume(thread_id="thread-other", approved=True)

    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.PENDING
    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    payload = workflow.pending_interrupt(THREAD_ID)
    assert payload["proposal_id"] == PROPOSAL_ID
