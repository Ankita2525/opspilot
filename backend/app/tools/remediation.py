from pydantic import BaseModel, ConfigDict

from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus, RemediationProposal, RiskLevel
from backend.app.safety.policy import ActionPolicy
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
        environment: SimulatedEnvironment,
        approvals: ApprovalService,
    ) -> None:
        self._environment = environment
        self._approvals = approvals
        self._policy = ActionPolicy()

    def propose_rollback(
        self,
        proposal_id: str,
        incident_id: str,
        service: str,
        version: str,
    ) -> RemediationProposal:
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
        proposal = self._approvals.get(proposal_id)
        if proposal.action != ROLLBACK_ACTION:
            raise PermissionError("Proposal action is not rollback_deployment.")
        if proposal.risk_level != RiskLevel.HIGH_RISK:
            raise PermissionError("Proposal is not classified as HIGH_RISK.")
        if proposal.approval_status != ApprovalStatus.APPROVED:
            raise PermissionError("Rollback has not been approved.")

        version = proposal.parameters["version"]
        self._environment.rollback_deployment(proposal.service, version)
        return RollbackExecutionResult(
            success=True,
            proposal_id=proposal.proposal_id,
            service=proposal.service,
            version=version,
        )
