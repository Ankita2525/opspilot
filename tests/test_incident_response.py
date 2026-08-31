from __future__ import annotations

import json
from datetime import datetime

import pytest

from backend.app.agent.hypotheses import (
    EvidenceReference,
    HypothesisEngine,
    HypothesisResult,
    RootCauseHypothesis,
)
from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
PROPOSAL_ID = "prop-rollback-001"
THREAD_ID = "thread-remediation-001"


def _stack(
    provider: FakeModelProvider | None = None,
    investigation: InvestigationWorkflow | None = None,
) -> tuple[
    SimulatedEnvironment,
    ApprovalService,
    FakeModelProvider,
    IncidentResponseCoordinator,
]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    diagnostics = DiagnosticTools(environment)
    approvals = ApprovalService()
    fake = provider or FakeModelProvider()
    investigation_workflow = investigation or InvestigationWorkflow(
        tools=diagnostics,
        hypothesis_engine=HypothesisEngine(fake),
    )
    coordinator = IncidentResponseCoordinator(
        investigation_workflow=investigation_workflow,
        remediation_workflow=RemediationApprovalWorkflow(
            remediation_tools=RemediationTools(environment, approvals),
            approvals=approvals,
            diagnostic_tools=diagnostics,
        ),
    )
    return environment, approvals, fake, coordinator


def _start(coordinator: IncidentResponseCoordinator):
    return coordinator.start(
        incident_id=INCIDENT_ID,
        affected_service=SERVICE,
        remediation_thread_id=THREAD_ID,
        proposal_id=PROPOSAL_ID,
    )


def _serialized(started) -> str:
    investigation = dict(started.investigation)
    hypothesis = investigation.get("hypothesis_result")
    if hasattr(hypothesis, "model_dump"):
        investigation["hypothesis_result"] = hypothesis.model_dump(mode="json")
    return json.dumps(
        {
            "incident_id": started.incident_id,
            "affected_service": started.affected_service,
            "status": started.status,
            "recommended_action": started.recommended_action,
            "proposed_version": started.proposed_version,
            "approval_request": started.approval_request,
            "investigation": {
                "status": investigation.get("status"),
                "completed_steps": investigation.get("completed_steps"),
            },
        }
    )


class _InvestigationStub:
    def __init__(self, deployments: list[DeploymentResponse]) -> None:
        self.deployments = deployments

    def run(self, incident_id: str, affected_service: str) -> dict:
        return {
            "incident_id": incident_id,
            "affected_service": affected_service,
            "metrics": MetricResponse(
                service=affected_service,
                p95_latency_ms=1940,
                error_rate_percent=8.2,
                timestamp=datetime(2026, 8, 30, 14, 3),
            ),
            "deployments": self.deployments,
            "logs": [
                LogResponse(
                    service=affected_service,
                    timestamp=datetime(2026, 8, 30, 14, 3, 4),
                    level="ERROR",
                    message="database connection pool timeout",
                )
            ],
            "hypothesis_result": HypothesisResult(
                hypotheses=[
                    RootCauseHypothesis(
                        cause="unknown",
                        confidence=0.5,
                        evidence=[
                            EvidenceReference(
                                source_type="metrics",
                                summary="latency is elevated",
                            )
                        ],
                    )
                ],
                recommended_next_action="rollback_deployment",
                reasoning_summary="Rollback is recommended from supplied evidence.",
            ),
            "completed_steps": [
                "inspect_metrics",
                "inspect_deployments",
                "inspect_logs",
                "generate_hypothesis",
                "complete_investigation",
            ],
            "status": "investigation_complete",
        }


def test_coordinator_runs_existing_investigation_workflow() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.investigation["status"] == "investigation_complete"
    assert started.investigation["completed_steps"] == [
        "inspect_metrics",
        "inspect_deployments",
        "inspect_logs",
        "generate_hypothesis",
        "complete_investigation",
    ]


def test_investigation_completes_before_remediation_is_proposed() -> None:
    _, approvals, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.investigation["completed_steps"][-1] == "complete_investigation"
    assert started.investigation["status"] == "investigation_complete"
    assert started.status == "approval_required"
    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.PENDING


def test_hypothesis_result_is_present() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.investigation["hypothesis_result"] is not None
    assert started.investigation["hypothesis_result"].hypotheses


def test_recommended_action_is_rollback_deployment() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.recommended_action == "rollback_deployment"


def test_selects_v1_18_3_from_collected_deployments() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    versions = [event.version for event in started.investigation["deployments"]]
    assert BAD_VERSION in versions
    assert started.proposed_version == BAD_VERSION


def test_reaches_real_remediation_approval_interrupt() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.approval_request is not None
    assert started.approval_request["type"] == "approval_required"
    assert started.approval_request["action"] == "rollback_deployment"
    assert started.approval_request["proposal_id"] == PROPOSAL_ID


def test_start_status_is_approval_required() -> None:
    _, _, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.status == "approval_required"


def test_environment_unresolved_while_waiting_for_approval() -> None:
    environment, _, _, coordinator = _stack()

    _start(coordinator)

    assert environment.is_resolved is False


def test_proposal_is_pending_while_paused() -> None:
    _, approvals, _, coordinator = _stack()

    _start(coordinator)

    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.PENDING


def test_coordinator_does_not_approve_anything() -> None:
    _, approvals, _, coordinator = _stack()

    started = _start(coordinator)

    assert started.approval_request is not None
    assert approvals.get(PROPOSAL_ID).approval_status != ApprovalStatus.APPROVED


def test_resume_approved_delegates_to_remediation_workflow() -> None:
    _, _, _, coordinator = _stack()
    _start(coordinator)

    resumed = coordinator.resume(remediation_thread_id=THREAD_ID, approved=True)

    assert resumed.status == "resolved"
    assert resumed.execution_success is True


def test_approved_flow_executes_rollback() -> None:
    environment, _, _, coordinator = _stack()
    _start(coordinator)

    coordinator.resume(remediation_thread_id=THREAD_ID, approved=True)

    assert environment.is_resolved is True


def test_approved_flow_resolves_incident() -> None:
    environment, _, _, coordinator = _stack()
    _start(coordinator)

    resumed = coordinator.resume(remediation_thread_id=THREAD_ID, approved=True)

    assert resumed.status == "resolved"
    assert environment.is_resolved is True


def test_approved_flow_recovers_metrics() -> None:
    _, _, _, coordinator = _stack()
    _start(coordinator)

    resumed = coordinator.resume(remediation_thread_id=THREAD_ID, approved=True)

    assert resumed.recovered_p95_latency_ms == 218
    assert resumed.recovered_error_rate_percent == 0.3


def test_approved_flow_creates_audit_event() -> None:
    environment, _, _, coordinator = _stack()
    _start(coordinator)
    coordinator.resume(remediation_thread_id=THREAD_ID, approved=True)

    events = environment.get_audit_events()
    assert len(events) == 1
    assert events[0].action == "rollback_deployment"


def test_resume_false_produces_rejected_status() -> None:
    _, _, _, coordinator = _stack()
    _start(coordinator)

    resumed = coordinator.resume(remediation_thread_id=THREAD_ID, approved=False)

    assert resumed.status == "rejected"
    assert resumed.execution_success is False


def test_rejected_flow_leaves_incident_unresolved() -> None:
    environment, _, _, coordinator = _stack()
    _start(coordinator)
    coordinator.resume(remediation_thread_id=THREAD_ID, approved=False)

    assert environment.is_resolved is False


def test_rejected_flow_creates_no_rollback_audit_event() -> None:
    environment, _, _, coordinator = _stack()
    _start(coordinator)
    coordinator.resume(remediation_thread_id=THREAD_ID, approved=False)

    assert environment.get_audit_events() == []


def test_unsupported_recommendation_status() -> None:
    provider = FakeModelProvider(recommended_next_action="increase_connection_pool")
    _, _, _, coordinator = _stack(provider=provider)

    started = _start(coordinator)

    assert started.status == "unsupported_recommendation"
    assert started.recommended_action == "increase_connection_pool"
    assert started.proposed_version is None
    assert started.approval_request is None


def test_unsupported_recommendation_creates_no_proposal() -> None:
    provider = FakeModelProvider(recommended_next_action="increase_connection_pool")
    _, approvals, _, coordinator = _stack(provider=provider)

    _start(coordinator)

    with pytest.raises(ValueError, match="Unknown proposal"):
        approvals.get(PROPOSAL_ID)


def test_unsupported_recommendation_does_not_mutate_environment() -> None:
    provider = FakeModelProvider(recommended_next_action="increase_connection_pool")
    environment, _, _, coordinator = _stack(provider=provider)

    _start(coordinator)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_rollback_without_deployment_evidence_is_insufficient() -> None:
    environment, approvals, _, coordinator = _stack(
        investigation=_InvestigationStub(deployments=[]),
    )

    started = _start(coordinator)

    assert started.status == "insufficient_evidence"
    assert started.proposed_version is None
    assert started.approval_request is None
    with pytest.raises(ValueError, match="Unknown proposal"):
        approvals.get(PROPOSAL_ID)
    assert environment.is_resolved is False


def test_no_simulator_ground_truth_in_prompts_or_output() -> None:
    _, _, provider, coordinator = _stack()

    started = _start(coordinator)
    prompt = provider.recorded_prompt()
    output = _serialized(started)

    for leak in ("known_root_cause", "expected_remediation"):
        assert leak not in prompt
        assert leak not in output
    assert "db_connection_pool_regression" not in prompt
    assert started.approval_request is not None
    approval = json.dumps(started.approval_request)
    assert "known_root_cause" not in approval
    assert "expected_remediation" not in approval
    assert "db_connection_pool_regression" not in approval
