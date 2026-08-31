from typing import TypedDict, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.app.safety.approvals import ApprovalService
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import ROLLBACK_ACTION, RemediationTools


class RemediationState(TypedDict):
    proposal_id: str
    incident_id: str
    service: str
    version: str
    approval_status: str | None
    status: str
    execution_success: bool
    recovered_p95_latency_ms: int | None
    recovered_error_rate_percent: float | None


class RemediationApprovalWorkflow:
    """Human-in-the-loop rollback approval over RemediationTools."""

    def __init__(
        self,
        remediation_tools: RemediationTools,
        approvals: ApprovalService,
        diagnostic_tools: DiagnosticTools,
    ) -> None:
        self._remediation_tools = remediation_tools
        self._approvals = approvals
        self._diagnostic_tools = diagnostic_tools
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()

    def start(
        self,
        *,
        thread_id: str,
        proposal_id: str,
        incident_id: str,
        service: str,
        version: str,
    ) -> dict:
        initial_state: RemediationState = {
            "proposal_id": proposal_id,
            "incident_id": incident_id,
            "service": service,
            "version": version,
            "approval_status": None,
            "status": "started",
            "execution_success": False,
            "recovered_p95_latency_ms": None,
            "recovered_error_rate_percent": None,
        }
        return cast(dict, self._graph.invoke(initial_state, self._config(thread_id)))

    def resume(self, *, thread_id: str, approved: bool) -> dict:
        return cast(
            dict,
            self._graph.invoke(Command(resume=approved), self._config(thread_id)),
        )

    def pending_interrupt(self, thread_id: str) -> dict:
        snapshot = self._graph.get_state(self._config(thread_id))
        if not snapshot.interrupts:
            raise ValueError(f"No pending interrupt for thread {thread_id}")
        return snapshot.interrupts[0].value

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _build_graph(self):
        graph = StateGraph(RemediationState)
        graph.add_node("create_proposal", self._create_proposal)
        graph.add_node("request_approval", self._request_approval)
        graph.add_node("execute_remediation", self._execute_remediation)
        graph.add_node("verify_recovery", self._verify_recovery)
        graph.add_node("reject_remediation", self._reject_remediation)
        graph.add_edge(START, "create_proposal")
        graph.add_edge("create_proposal", "request_approval")
        graph.add_conditional_edges(
            "request_approval",
            self._route_after_approval,
            {
                "execute_remediation": "execute_remediation",
                "reject_remediation": "reject_remediation",
            },
        )
        graph.add_edge("execute_remediation", "verify_recovery")
        graph.add_edge("verify_recovery", END)
        graph.add_edge("reject_remediation", END)
        return graph.compile(checkpointer=self._checkpointer)

    def _create_proposal(self, state: RemediationState) -> dict:
        self._remediation_tools.propose_rollback(
            proposal_id=state["proposal_id"],
            incident_id=state["incident_id"],
            service=state["service"],
            version=state["version"],
        )
        return {
            "status": "approval_required",
            "approval_status": "pending",
        }

    def _request_approval(self, state: RemediationState) -> dict:
        payload = {
            "type": "approval_required",
            "proposal_id": state["proposal_id"],
            "incident_id": state["incident_id"],
            "action": ROLLBACK_ACTION,
            "service": state["service"],
            "version": state["version"],
            "risk_level": "high_risk",
            "message": "Rollback requires human approval.",
        }
        approved = interrupt(payload)
        if approved is True:
            self._approvals.approve(state["proposal_id"])
            return {"approval_status": "approved"}
        if approved is False:
            self._approvals.reject(state["proposal_id"])
            return {"approval_status": "rejected"}
        raise ValueError("Approval resume value must be True or False.")

    def _route_after_approval(self, state: RemediationState) -> str:
        if state["approval_status"] == "approved":
            return "execute_remediation"
        return "reject_remediation"

    def _execute_remediation(self, state: RemediationState) -> dict:
        self._remediation_tools.execute_rollback(state["proposal_id"])
        return {
            "execution_success": True,
            "status": "remediation_executed",
        }

    def _verify_recovery(self, state: RemediationState) -> dict:
        health = self._diagnostic_tools.get_service_health(state["service"])
        return {
            "recovered_p95_latency_ms": health.p95_latency_ms,
            "recovered_error_rate_percent": health.error_rate_percent,
            "status": "resolved" if health.healthy else "remediation_failed",
        }

    def _reject_remediation(self, state: RemediationState) -> dict:
        return {
            "execution_success": False,
            "status": "rejected",
        }
