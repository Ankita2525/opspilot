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


def test_workflow_module_has_no_checkout_specific_recovery_numbers() -> None:
    import inspect

    from backend.app.agent import remediation_workflow

    source = inspect.getsource(remediation_workflow)
    assert "218" not in source
    assert "0.3" not in source
    assert "RECOVERED_P95" not in source


SCENARIO_CASES = [
    ("checkout-db-pool-regression", "checkout-api", "v1.18.3", 218, 0.3, 1940, 8.2),
    ("auth-token-validation-regression", "auth-service", "v2.7.1", 165, 0.4, 870, 14.6),
    (
        "payments-provider-timeout-regression",
        "payments-service",
        "v3.4.2",
        295,
        0.6,
        2680,
        11.1,
    ),
]


@pytest.mark.parametrize(
    "scenario_id, service, version, recovered_p95, recovered_error, incident_p95, incident_error",
    SCENARIO_CASES,
)
def test_approved_rollback_resolves_each_scenario_via_health_policy(
    scenario_id: str,
    service: str,
    version: str,
    recovered_p95: int,
    recovered_error: float,
    incident_p95: int,
    incident_error: float,
) -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    approvals = ApprovalService()
    workflow = RemediationApprovalWorkflow(
        remediation_tools=RemediationTools(environment, approvals),
        approvals=approvals,
        diagnostic_tools=DiagnosticTools(environment),
    )
    thread_id = f"{scenario_id}-thread"
    proposal_id = f"{scenario_id}-proposal"
    workflow.start(
        thread_id=thread_id,
        proposal_id=proposal_id,
        incident_id=scenario_id,
        service=service,
        version=version,
    )
    assert environment.get_service_health(service).healthy is False

    result = workflow.resume(thread_id=thread_id, approved=True)

    assert result["status"] == "resolved"
    assert result["recovered_p95_latency_ms"] == recovered_p95
    assert result["recovered_error_rate_percent"] == recovered_error
    assert environment.get_service_health(service).healthy is True


@pytest.mark.parametrize(
    "scenario_id, service, version, recovered_p95, recovered_error, incident_p95, incident_error",
    SCENARIO_CASES,
)
def test_rejected_rollback_leaves_each_scenario_degraded(
    scenario_id: str,
    service: str,
    version: str,
    recovered_p95: int,
    recovered_error: float,
    incident_p95: int,
    incident_error: float,
) -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    approvals = ApprovalService()
    workflow = RemediationApprovalWorkflow(
        remediation_tools=RemediationTools(environment, approvals),
        approvals=approvals,
        diagnostic_tools=DiagnosticTools(environment),
    )
    thread_id = f"{scenario_id}-reject-thread"
    proposal_id = f"{scenario_id}-reject-proposal"
    workflow.start(
        thread_id=thread_id,
        proposal_id=proposal_id,
        incident_id=scenario_id,
        service=service,
        version=version,
    )

    result = workflow.resume(thread_id=thread_id, approved=False)

    assert result["status"] == "rejected"
    assert environment.is_resolved is False
    metrics = environment.query_metrics(service)
    assert metrics.p95_latency_ms == incident_p95
    assert metrics.error_rate_percent == incident_error
    assert environment.get_service_health(service).healthy is False


def test_default_checkpointer_is_in_memory() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    _, _, workflow = _loaded()

    assert isinstance(workflow._checkpointer, InMemorySaver)


def test_supplied_checkpointer_is_used() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    approvals = ApprovalService()
    saver = InMemorySaver()
    workflow = RemediationApprovalWorkflow(
        remediation_tools=RemediationTools(environment, approvals),
        approvals=approvals,
        diagnostic_tools=DiagnosticTools(environment),
        checkpointer=saver,
    )

    assert workflow._checkpointer is saver
    _start(workflow)
    checkpoint = saver.get_tuple({"configurable": {"thread_id": THREAD_ID}})
    assert checkpoint is not None


def test_checkpointer_uses_strict_msgpack_deserialization() -> None:
    _, _, workflow = _loaded()

    assert workflow._checkpointer.serde._allowed_msgpack_modules is None

