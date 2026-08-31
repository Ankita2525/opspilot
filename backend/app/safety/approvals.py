from backend.app.safety.models import ApprovalStatus, RemediationProposal


class ApprovalService:
    """In-memory store for explicit human approval of remediation proposals."""

    def __init__(self) -> None:
        self._proposals: dict[str, RemediationProposal] = {}

    def submit(self, proposal: RemediationProposal) -> RemediationProposal:
        if proposal.proposal_id in self._proposals:
            raise ValueError(f"Proposal already exists: {proposal.proposal_id}")
        stored = proposal.model_copy(
            update={
                "approval_status": ApprovalStatus.PENDING,
                "parameters": dict(proposal.parameters),
            }
        )
        self._proposals[stored.proposal_id] = stored
        return self._snapshot(stored)

    def approve(self, proposal_id: str) -> RemediationProposal:
        proposal = self._require(proposal_id)
        updated = proposal.model_copy(update={"approval_status": ApprovalStatus.APPROVED})
        self._proposals[proposal_id] = updated
        return self._snapshot(updated)

    def reject(self, proposal_id: str) -> RemediationProposal:
        proposal = self._require(proposal_id)
        updated = proposal.model_copy(update={"approval_status": ApprovalStatus.REJECTED})
        self._proposals[proposal_id] = updated
        return self._snapshot(updated)

    def get(self, proposal_id: str) -> RemediationProposal:
        return self._snapshot(self._require(proposal_id))

    def _require(self, proposal_id: str) -> RemediationProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise ValueError(f"Unknown proposal: {proposal_id}") from exc

    def _snapshot(self, proposal: RemediationProposal) -> RemediationProposal:
        return proposal.model_copy(update={"parameters": dict(proposal.parameters)})
