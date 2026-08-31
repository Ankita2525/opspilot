from __future__ import annotations

import pytest

from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus, RiskLevel
from backend.app.safety.policy import ActionPolicy
from backend.app.tools.remediation import RemediationTools
from simulator.environment import SimulatedEnvironment

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
PROPOSAL_ID = "prop-rollback-001"


def _loaded() -> tuple[SimulatedEnvironment, ApprovalService, RemediationTools]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    approvals = ApprovalService()
    tools = RemediationTools(environment=environment, approvals=approvals)
    return environment, approvals, tools


def test_rollback_is_classified_high_risk() -> None:
    assert ActionPolicy().classify("rollback_deployment") == RiskLevel.HIGH_RISK


def test_read_only_actions_are_classified_read() -> None:
    policy = ActionPolicy()
    assert policy.classify("query_metrics") == RiskLevel.READ
    assert policy.classify("get_service_logs") == RiskLevel.READ
    assert policy.classify("get_recent_deployments") == RiskLevel.READ
    assert policy.classify("get_service_health") == RiskLevel.READ


def test_unknown_policy_action_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown action"):
        ActionPolicy().classify("restart_service")


def test_proposing_rollback_creates_pending_proposal() -> None:
    _, approvals, tools = _loaded()

    proposal = tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )

    assert proposal.proposal_id == PROPOSAL_ID
    assert proposal.action == "rollback_deployment"
    assert proposal.service == SERVICE
    assert proposal.parameters == {"version": BAD_VERSION}
    assert proposal.risk_level == RiskLevel.HIGH_RISK
    assert proposal.approval_status == ApprovalStatus.PENDING
    stored = approvals.get(PROPOSAL_ID)
    assert stored.approval_status == ApprovalStatus.PENDING


def test_proposing_rollback_does_not_resolve_incident() -> None:
    environment, _, tools = _loaded()

    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )

    assert environment.is_resolved is False
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_execute_before_approval_raises_permission_error() -> None:
    _, _, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )

    with pytest.raises(PermissionError):
        tools.execute_rollback(PROPOSAL_ID)


def test_unapproved_execution_does_not_resolve_incident() -> None:
    environment, _, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )

    with pytest.raises(PermissionError):
        tools.execute_rollback(PROPOSAL_ID)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []


def test_human_approval_changes_status_to_approved() -> None:
    _, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )

    approved = approvals.approve(PROPOSAL_ID)

    assert approved.approval_status == ApprovalStatus.APPROVED
    assert approvals.get(PROPOSAL_ID).approval_status == ApprovalStatus.APPROVED


def test_approved_rollback_executes_successfully() -> None:
    _, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    approvals.approve(PROPOSAL_ID)

    result = tools.execute_rollback(PROPOSAL_ID)

    assert result.success is True
    assert result.proposal_id == PROPOSAL_ID
    assert result.service == SERVICE
    assert result.version == BAD_VERSION


def test_approved_rollback_resolves_incident() -> None:
    environment, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    approvals.approve(PROPOSAL_ID)
    tools.execute_rollback(PROPOSAL_ID)

    assert environment.is_resolved is True


def test_recovered_metrics_after_approved_rollback() -> None:
    environment, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    approvals.approve(PROPOSAL_ID)
    tools.execute_rollback(PROPOSAL_ID)

    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 218
    assert metrics.error_rate_percent == 0.3


def test_approved_rollback_creates_simulator_audit_event() -> None:
    environment, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    approvals.approve(PROPOSAL_ID)
    tools.execute_rollback(PROPOSAL_ID)

    events = environment.get_audit_events()
    assert len(events) == 1
    assert events[0].action == "rollback_deployment"
    assert SERVICE in events[0].details
    assert BAD_VERSION in events[0].details


def test_rejected_proposal_cannot_execute() -> None:
    environment, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    rejected = approvals.reject(PROPOSAL_ID)

    assert rejected.approval_status == ApprovalStatus.REJECTED
    with pytest.raises(PermissionError):
        tools.execute_rollback(PROPOSAL_ID)
    assert environment.is_resolved is False


def test_unknown_proposal_id_raises_value_error() -> None:
    _, approvals, tools = _loaded()

    with pytest.raises(ValueError, match="Unknown proposal"):
        approvals.get("missing-proposal")
    with pytest.raises(ValueError, match="Unknown proposal"):
        tools.execute_rollback("missing-proposal")


def test_wrong_deployment_version_fails_simulator_validation() -> None:
    environment, approvals, tools = _loaded()
    tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version="v1.18.2",
    )
    approvals.approve(PROPOSAL_ID)

    with pytest.raises(ValueError):
        tools.execute_rollback(PROPOSAL_ID)

    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 1940
    assert metrics.error_rate_percent == 8.2


def test_approved_parameters_cannot_be_mutated_before_execution() -> None:
    environment, approvals, tools = _loaded()
    proposal = tools.propose_rollback(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        service=SERVICE,
        version=BAD_VERSION,
    )
    approvals.approve(PROPOSAL_ID)

    proposal.parameters["version"] = "v1.18.4"
    retrieved = approvals.get(PROPOSAL_ID)
    retrieved.parameters["version"] = "v1.18.4"

    result = tools.execute_rollback(PROPOSAL_ID)

    assert result.version == BAD_VERSION
    assert approvals.get(PROPOSAL_ID).parameters == {"version": BAD_VERSION}
    assert environment.is_resolved is True
    metrics = environment.query_metrics(SERVICE)
    assert metrics.p95_latency_ms == 218
    assert metrics.error_rate_percent == 0.3
