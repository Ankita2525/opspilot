from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.tools.remediation import ROLLBACK_ACTION
from backend.app.tools.schemas import DeploymentResponse
from langgraph.types import Interrupt


class IncidentResponseStartResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    affected_service: str
    status: str
    investigation: dict[str, Any]
    recommended_action: str | None
    proposed_version: str | None
    approval_request: dict[str, Any] | None


class IncidentResponseResumeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    execution_success: bool
    recovered_p95_latency_ms: int | None = None
    recovered_error_rate_percent: float | None = None
    approval_status: str | None = None


class IncidentResponseCoordinator:
    """Composes investigation and human-approved remediation without new graph logic."""

    def __init__(
        self,
        investigation_workflow: InvestigationWorkflow,
        remediation_workflow: RemediationApprovalWorkflow,
    ) -> None:
        self._investigation_workflow = investigation_workflow
        self._remediation_workflow = remediation_workflow

    def start(
        self,
        *,
        incident_id: str,
        affected_service: str,
        remediation_thread_id: str,
        proposal_id: str,
    ) -> IncidentResponseStartResult:
        investigation = self._investigation_workflow.run(incident_id, affected_service)
        hypothesis = investigation["hypothesis_result"]
        if hypothesis is None:
            raise ValueError("Investigation completed without a hypothesis result.")

        recommended_action = hypothesis.recommended_next_action
        if recommended_action != ROLLBACK_ACTION:
            return IncidentResponseStartResult(
                incident_id=incident_id,
                affected_service=affected_service,
                status="unsupported_recommendation",
                investigation=dict(investigation),
                recommended_action=recommended_action,
                proposed_version=None,
                approval_request=None,
            )

        proposed_version = _most_recent_deployment_version(
            investigation["deployments"],
            affected_service,
        )
        if proposed_version is None:
            return IncidentResponseStartResult(
                incident_id=incident_id,
                affected_service=affected_service,
                status="insufficient_evidence",
                investigation=dict(investigation),
                recommended_action=recommended_action,
                proposed_version=None,
                approval_request=None,
            )

        remediation_result = self._remediation_workflow.start(
            thread_id=remediation_thread_id,
            proposal_id=proposal_id,
            incident_id=incident_id,
            service=affected_service,
            version=proposed_version,
        )
        return IncidentResponseStartResult(
            incident_id=incident_id,
            affected_service=affected_service,
            status="approval_required",
            investigation=dict(investigation),
            recommended_action=recommended_action,
            proposed_version=proposed_version,
            approval_request=_interrupt_payload(remediation_result),
        )

    def resume(
        self,
        *,
        remediation_thread_id: str,
        approved: bool,
    ) -> IncidentResponseResumeResult:
        result = self._remediation_workflow.resume(
            thread_id=remediation_thread_id,
            approved=approved,
        )
        return IncidentResponseResumeResult.model_validate(result)


def _most_recent_deployment_version(
    deployments: list[DeploymentResponse],
    affected_service: str,
) -> str | None:
    matching = [event for event in deployments if event.service == affected_service]
    if not matching:
        return None
    latest = max(matching, key=lambda event: event.timestamp)
    return latest.version


def _interrupt_payload(result: dict) -> dict[str, Any]:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        raise ValueError("Remediation workflow did not reach an approval interrupt.")
    first = interrupts[0]
    if isinstance(first, Interrupt):
        return first.value
    if isinstance(first, dict) and "value" in first:
        return first["value"]
    return first
