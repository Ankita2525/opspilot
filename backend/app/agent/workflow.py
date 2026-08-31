from typing import cast

from langgraph.graph import END, START, StateGraph

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.state import InvestigationState
from backend.app.tools.diagnostics import DiagnosticTools


class InvestigationWorkflow:
    """Deterministic investigation graph over DiagnosticTools and HypothesisEngine."""

    def __init__(
        self,
        tools: DiagnosticTools,
        hypothesis_engine: HypothesisEngine,
    ) -> None:
        self._tools = tools
        self._hypothesis_engine = hypothesis_engine
        self._graph = self._build_graph()

    def run(self, incident_id: str, affected_service: str) -> InvestigationState:
        initial_state: InvestigationState = {
            "incident_id": incident_id,
            "affected_service": affected_service,
            "metrics": None,
            "deployments": [],
            "logs": [],
            "hypothesis_result": None,
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
        graph.add_node("generate_hypothesis", self._generate_hypothesis)
        graph.add_node("complete_investigation", self._complete_investigation)
        graph.add_edge(START, "inspect_metrics")
        graph.add_edge("inspect_metrics", "inspect_deployments")
        graph.add_edge("inspect_deployments", "inspect_logs")
        graph.add_edge("inspect_logs", "generate_hypothesis")
        graph.add_edge("generate_hypothesis", "complete_investigation")
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

    def _generate_hypothesis(self, state: InvestigationState) -> dict:
        metrics = state["metrics"]
        if metrics is None:
            raise ValueError("Cannot generate a hypothesis before metrics are collected.")
        if not state["deployments"]:
            raise ValueError(
                "Cannot generate a hypothesis before deployments are collected."
            )
        if not state["logs"]:
            raise ValueError("Cannot generate a hypothesis before logs are collected.")

        hypothesis_result = self._hypothesis_engine.analyze(
            incident_id=state["incident_id"],
            affected_service=state["affected_service"],
            metrics=metrics,
            deployments=state["deployments"],
            logs=state["logs"],
        )
        return {
            "hypothesis_result": hypothesis_result,
            "completed_steps": [*state["completed_steps"], "generate_hypothesis"],
        }

    def _complete_investigation(self, state: InvestigationState) -> dict:
        return {
            "status": "investigation_complete",
            "completed_steps": [*state["completed_steps"], "complete_investigation"],
        }
