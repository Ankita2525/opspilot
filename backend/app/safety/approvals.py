from collections.abc import Callable
from datetime import datetime, timezone

from backend.app.persistence.models import ApprovalRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.safety.models import ApprovalStatus, RemediationProposal, RiskLevel

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalService:
    """Explicit human approval of remediation proposals.

    When a repository is supplied, it is the durable source of proposal
    authority. Otherwise proposals are kept in memory.
    """

    def __init__(
        self,
        repository: OpsPilotRepository | None = None,
        now: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or utc_now
        self._proposals: dict[str, RemediationProposal] = {}

    def submit(self, proposal: RemediationProposal) -> RemediationProposal:
        if self._exists(proposal.proposal_id):
            raise ValueError(f"Proposal already exists: {proposal.proposal_id}")
        stored = proposal.model_copy(
            update={
                "approval_status": ApprovalStatus.PENDING,
                "parameters": dict(proposal.parameters),
            }
        )
        self._write(stored, created_at=self._now())
        return self._snapshot(stored)

    def approve(self, proposal_id: str) -> RemediationProposal:
        proposal = self._require(proposal_id)
        if proposal.approval_status == ApprovalStatus.REJECTED:
            raise ValueError(f"Rejected proposal cannot be approved: {proposal_id}")
        if proposal.approval_status == ApprovalStatus.APPROVED:
            return self._snapshot(proposal)
        updated = proposal.model_copy(update={"approval_status": ApprovalStatus.APPROVED})
        self._write(updated)
        return self._snapshot(updated)

    def reject(self, proposal_id: str) -> RemediationProposal:
        proposal = self._require(proposal_id)
        if proposal.approval_status == ApprovalStatus.APPROVED:
            raise ValueError(f"Approved proposal cannot be rejected: {proposal_id}")
        if proposal.approval_status == ApprovalStatus.REJECTED:
            return self._snapshot(proposal)
        updated = proposal.model_copy(update={"approval_status": ApprovalStatus.REJECTED})
        self._write(updated)
        return self._snapshot(updated)

    def get(self, proposal_id: str) -> RemediationProposal:
        return self._snapshot(self._require(proposal_id))

    def _exists(self, proposal_id: str) -> bool:
        if self._repository is not None:
            return self._repository.get_approval(proposal_id) is not None
        return proposal_id in self._proposals

    def _require(self, proposal_id: str) -> RemediationProposal:
        if self._repository is not None:
            record = self._repository.get_approval(proposal_id)
            if record is None:
                raise ValueError(f"Unknown proposal: {proposal_id}")
            return proposal_from_record(record)
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise ValueError(f"Unknown proposal: {proposal_id}") from exc

    def _write(
        self,
        proposal: RemediationProposal,
        created_at: datetime | None = None,
    ) -> None:
        if self._repository is not None:
            existing = self._repository.get_approval(proposal.proposal_id)
            timestamp = self._now()
            stored_created_at = (
                created_at
                if created_at is not None
                else existing.created_at if existing is not None else timestamp
            )
            self._repository.save_approval(
                record_from_proposal(
                    proposal,
                    created_at=stored_created_at,
                    updated_at=timestamp,
                )
            )
            return
        self._proposals[proposal.proposal_id] = proposal

    def _snapshot(self, proposal: RemediationProposal) -> RemediationProposal:
        return proposal.model_copy(update={"parameters": dict(proposal.parameters)})


def record_from_proposal(
    proposal: RemediationProposal,
    *,
    created_at: datetime,
    updated_at: datetime,
) -> ApprovalRecord:
    return ApprovalRecord(
        proposal_id=proposal.proposal_id,
        incident_id=proposal.incident_id,
        action=proposal.action,
        service=proposal.service,
        version=proposal.parameters.get("version"),
        risk_level=proposal.risk_level.value,
        status=proposal.approval_status.value.lower(),
        created_at=created_at,
        updated_at=updated_at,
    )


def proposal_from_record(record: ApprovalRecord) -> RemediationProposal:
    parameters: dict[str, str] = {}
    if record.version is not None:
        parameters["version"] = record.version
    return RemediationProposal(
        proposal_id=record.proposal_id,
        incident_id=record.incident_id,
        action=record.action,
        service=record.service,
        parameters=parameters,
        risk_level=_parse_risk_level(record.risk_level),
        approval_status=_parse_approval_status(record.status),
    )


def _parse_risk_level(value: str) -> RiskLevel:
    normalized = value.strip().upper().replace("-", "_")
    try:
        return RiskLevel(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown risk level: {value}") from exc


def _parse_approval_status(value: str) -> ApprovalStatus:
    normalized = value.strip().upper()
    try:
        return ApprovalStatus(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown approval status: {value}") from exc
