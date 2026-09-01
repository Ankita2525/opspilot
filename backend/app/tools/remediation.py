from pydantic import BaseModel, ConfigDict

from backend.app.observability.tracing import get_tracer
from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus, RemediationProposal, RiskLevel
from backend.app.safety.policy import ActionPolicy
from backend.app.telemetry.remediation import RemediationBackend
from simulator.environment import SimulatedEnvironment

ROLLBACK_ACTION = "rollback_deployment"


class RollbackExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    proposal_id: str
    service: str
    version: str


class RemediationTools:
    """High-risk writes that execute only from an approved proposal."""

    def __init__(
        self,
        backend: RemediationBackend | SimulatedEnvironment,
        approvals: ApprovalService,
    ) -> None:
        if isinstance(backend, SimulatedEnvironment):
            from backend.app.telemetry.simulator_remediation import (
                SimulatorRemediationBackend,
            )

            backend = SimulatorRemediationBackend(backend)
        self._backend = backend
        self._approvals = approvals
        self._policy = ActionPolicy()

    def propose_rollback(
        self,
        proposal_id: str,
        incident_id: str,
        service: str,
        version: str,
    ) -> RemediationProposal:
        with get_tracer().start_as_current_span("opspilot.remediation.propose") as span:
            span.set_attribute("opspilot.incident_id", incident_id)
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.action", ROLLBACK_ACTION)
            span.set_attribute("opspilot.risk_level", "high_risk")
            risk_level = self._policy.classify(ROLLBACK_ACTION)
            proposal = RemediationProposal(
                proposal_id=proposal_id,
                incident_id=incident_id,
                action=ROLLBACK_ACTION,
                service=service,
                parameters={"version": version},
                risk_level=risk_level,
                approval_status=ApprovalStatus.PENDING,
            )
            return self._approvals.submit(proposal)

    def execute_rollback(self, proposal_id: str) -> RollbackExecutionResult:
        with get_tracer().start_as_current_span("opspilot.remediation.execute") as span:
            span.set_attribute("opspilot.action", ROLLBACK_ACTION)
            span.set_attribute("opspilot.proposal_id", proposal_id)
            proposal = self._approvals.get(proposal_id)
            if proposal.action != ROLLBACK_ACTION:
                raise PermissionError("Proposal action is not rollback_deployment.")
            policy_risk = self._policy.classify(proposal.action)
            if proposal.risk_level != RiskLevel.HIGH_RISK:
                raise PermissionError("Proposal is not classified as HIGH_RISK.")
            if policy_risk != proposal.risk_level:
                raise PermissionError("Proposal risk level does not match current policy.")
            if proposal.approval_status != ApprovalStatus.APPROVED:
                raise PermissionError("Rollback has not been approved.")

            version = proposal.parameters["version"]
            self._backend.rollback_deployment(proposal.service, version)
            result = RollbackExecutionResult(
                success=True,
                proposal_id=proposal.proposal_id,
                service=proposal.service,
                version=version,
            )
            span.set_attribute("opspilot.execution_success", True)
            return result
