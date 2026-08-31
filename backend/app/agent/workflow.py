from typing import cast

from langgraph.graph import END, START, StateGraph

from backend.app.agent.state import InvestigationState
from backend.app.tools.diagnostics import DiagnosticTools


class InvestigationWorkflow:
    """Deterministic evidence-collection graph over DiagnosticTools."""

    def __init__(self, tools: DiagnosticTools) -> None:
        self._tools = tools
        self._graph = self._build_graph()

    def run(self, incident_id: str, affected_service: str) -> InvestigationState:
        initial_state: InvestigationState = {
            "incident_id": incident_id,
            "affected_service": affected_service,
            "metrics": None,
            "deployments": [],
            "logs": [],
            "completed_steps": [],
            "status": "in_progress",
        }
        result = self._graph.invoke(initial_state)
        return cast(InvestigationState, result)

    def _build_graph(self):
        graph = StateGraph(InvestigationState)
        graph.add_node("inspect_metrics", self._inspect_metrics)
        graph.add_node("inspect_deployments", self._inspect_deployments)
        graph.add_node("inspect_logs", self._inspect_logs)
        graph.add_node("complete_investigation", self._complete_investigation)
        graph.add_edge(START, "inspect_metrics")
        graph.add_edge("inspect_metrics", "inspect_deployments")
        graph.add_edge("inspect_deployments", "inspect_logs")
        graph.add_edge("inspect_logs", "complete_investigation")
        graph.add_edge("complete_investigation", END)
        return graph.compile()

    def _inspect_metrics(self, state: InvestigationState) -> dict:
        metrics = self._tools.query_metrics(state["affected_service"])
        return {
            "metrics": metrics,
            "completed_steps": [*state["completed_steps"], "inspect_metrics"],
        }

    def _inspect_deployments(self, state: InvestigationState) -> dict:
        deployments = self._tools.get_recent_deployments(state["affected_service"])
        return {
            "deployments": deployments,
            "completed_steps": [*state["completed_steps"], "inspect_deployments"],
        }

    def _inspect_logs(self, state: InvestigationState) -> dict:
        logs = self._tools.get_service_logs(state["affected_service"])
        return {
            "logs": logs,
            "completed_steps": [*state["completed_steps"], "inspect_logs"],
        }

    def _complete_investigation(self, state: InvestigationState) -> dict:
        return {
            "status": "investigation_complete",
            "completed_steps": [*state["completed_steps"], "complete_investigation"],
        }
