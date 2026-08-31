from typing import cast

from langgraph.graph import END, START, StateGraph

from backend.app.agent.hypotheses import HypothesisEngine, HypothesisResult
from backend.app.agent.state import InvestigationState
from backend.app.context.models import IncidentContext
from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.events.models import InvestigationEventType
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import DeploymentResponse, MetricResponse


class InvestigationWorkflow:
    """Deterministic investigation graph over DiagnosticTools and HypothesisEngine."""

    def __init__(
        self,
        tools: DiagnosticTools,
        hypothesis_engine: HypothesisEngine,
        events: InvestigationEventEmitter | None = None,
    ) -> None:
        self._tools = tools
        self._hypothesis_engine = hypothesis_engine
        self._events = events
        self._graph = self._build_graph()

    def run(self, incident_id: str, affected_service: str) -> InvestigationState:
        initial_state: InvestigationState = {
            "incident_id": incident_id,
            "affected_service": affected_service,
            "metrics": None,
            "deployments": [],
            "logs": [],
            "incident_context": None,
            "hypothesis_result": None,
            "selected_skills": [],
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
        self._step_started("inspect_metrics", "Inspecting service metrics.")
        metrics = self._tools.query_metrics(state["affected_service"])
        self._step_completed(
            "inspect_metrics",
            "Collected service metrics.",
            _metrics_payload(metrics),
        )
        return {
            "metrics": metrics,
            "completed_steps": [*state["completed_steps"], "inspect_metrics"],
        }

    def _inspect_deployments(self, state: InvestigationState) -> dict:
        self._step_started("inspect_deployments", "Inspecting recent deployments.")
        deployments = self._tools.get_recent_deployments(state["affected_service"])
        self._step_completed(
            "inspect_deployments",
            "Collected recent deployments.",
            _deployments_payload(deployments),
        )
        return {
            "deployments": deployments,
            "completed_steps": [*state["completed_steps"], "inspect_deployments"],
        }

    def _inspect_logs(self, state: InvestigationState) -> dict:
        self._step_started("inspect_logs", "Inspecting service logs.")
        logs = self._tools.get_service_logs(state["affected_service"])
        self._step_completed(
            "inspect_logs",
            "Collected service logs.",
            {"log_count": len(logs)},
        )
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

        self._step_started("generate_hypothesis", "Generating root-cause hypothesis.")
        context = self._hypothesis_engine.build_context(
            incident_id=state["incident_id"],
            affected_service=state["affected_service"],
            metrics=metrics,
            deployments=state["deployments"],
            logs=state["logs"],
        )
        self._emit(
            InvestigationEventType.CONTEXT_BUILT,
            step="generate_hypothesis",
            message="Built bounded incident context.",
            data=_context_payload(context),
        )
        selected_skills = self._hypothesis_engine.select_skills(context)
        self._emit(
            InvestigationEventType.SKILLS_SELECTED,
            step="generate_hypothesis",
            message="Selected diagnostic skills.",
            data={"selected_skills": list(selected_skills)},
        )
        hypothesis_result = self._hypothesis_engine.analyze_context(context)
        self._emit(
            InvestigationEventType.HYPOTHESIS_GENERATED,
            step="generate_hypothesis",
            message="Generated a root-cause hypothesis.",
            data=_hypothesis_payload(hypothesis_result),
        )
        self._step_completed(
            "generate_hypothesis",
            "Completed hypothesis generation.",
        )
        return {
            "incident_context": context,
            "hypothesis_result": hypothesis_result,
            "selected_skills": selected_skills,
            "completed_steps": [*state["completed_steps"], "generate_hypothesis"],
        }

    def _complete_investigation(self, state: InvestigationState) -> dict:
        self._step_started("complete_investigation", "Completing investigation.")
        self._step_completed("complete_investigation", "Investigation graph completed.")
        return {
            "status": "investigation_complete",
            "completed_steps": [*state["completed_steps"], "complete_investigation"],
        }

    def _step_started(self, step: str, message: str) -> None:
        self._emit(
            InvestigationEventType.STEP_STARTED,
            step=step,
            message=message,
        )

    def _step_completed(self, step: str, message: str, data: dict | None = None) -> None:
        self._emit(
            InvestigationEventType.STEP_COMPLETED,
            step=step,
            message=message,
            data=data,
        )

    def _emit(
        self,
        event_type: InvestigationEventType,
        *,
        message: str,
        step: str | None = None,
        data: dict | None = None,
    ) -> None:
        if self._events is None:
            return
        self._events.emit(event_type, message=message, step=step, data=data)


def _metrics_payload(metrics: MetricResponse) -> dict:
    return {
        "p95_latency_ms": metrics.p95_latency_ms,
        "error_rate_percent": metrics.error_rate_percent,
    }


def _deployments_payload(deployments: list[DeploymentResponse]) -> dict:
    return {
        "deployment_count": len(deployments),
        "versions": [event.version for event in deployments],
    }


def _context_payload(context: IncidentContext) -> dict:
    return {
        "symptom_summary": context.symptom_summary,
        "evidence": [
            {
                "evidence_type": item.evidence_type.value,
                "summary": item.summary,
                "suspicious_instruction_content": item.suspicious_instruction_content,
            }
            for item in context.evidence
        ],
    }


def _hypothesis_payload(result: HypothesisResult) -> dict:
    payload: dict = {
        "recommended_action": result.recommended_action.value,
        "recommendation_summary": result.recommendation_summary,
    }
    if not result.hypotheses:
        return payload
    top = max(result.hypotheses, key=lambda item: item.confidence)
    payload["root_cause"] = top.cause
    payload["confidence"] = top.confidence
    return payload
