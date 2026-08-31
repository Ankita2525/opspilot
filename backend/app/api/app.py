from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.api.schemas import (
    HealthResponse,
    IncidentApprovalResponse,
    IncidentStartResponse,
    ScenarioSummary,
    StartIncidentRequest,
    SubmitApprovalRequest,
)
from backend.app.api.session_store import IncidentSession, IncidentSessionStore
from backend.app.models.groq_provider import GroqModelProvider
from backend.app.models.provider import ModelProvider
from backend.app.safety.approvals import ApprovalService
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from backend.app.tools.schemas import MetricResponse
from simulator.environment import SimulatedEnvironment
from simulator.scenarios import get_scenario, list_scenarios


def create_app(provider: ModelProvider | None = None) -> FastAPI:
    resolved_provider: ModelProvider = (
        provider if provider is not None else GroqModelProvider()
    )
    store = IncidentSessionStore()
    app = FastAPI(title="OpsPilot")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.provider = resolved_provider
    app.state.store = store

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="opspilot")

    @app.get("/api/scenarios", response_model=list[ScenarioSummary])
    def list_public_scenarios() -> list[ScenarioSummary]:
        return [
            ScenarioSummary(
                id=scenario.id,
                title=scenario.title,
                affected_service=scenario.affected_service,
            )
            for scenario in list_scenarios()
        ]

    @app.post("/api/incidents/start", response_model=IncidentStartResponse)
    def start_incident(body: StartIncidentRequest) -> IncidentStartResponse:
        try:
            scenario = get_scenario(body.scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        environment = SimulatedEnvironment()
        environment.load_scenario(scenario.id)
        diagnostics = DiagnosticTools(environment)
        approvals = ApprovalService()
        coordinator = IncidentResponseCoordinator(
            investigation_workflow=InvestigationWorkflow(
                tools=diagnostics,
                hypothesis_engine=HypothesisEngine(resolved_provider),
            ),
            remediation_workflow=RemediationApprovalWorkflow(
                remediation_tools=RemediationTools(environment, approvals),
                approvals=approvals,
                diagnostic_tools=diagnostics,
            ),
        )
        incident_id = scenario.id
        remediation_thread_id = f"{incident_id}-remediation"
        proposal_id = f"{incident_id}-proposal"
        started = coordinator.start(
            incident_id=incident_id,
            affected_service=scenario.affected_service,
            remediation_thread_id=remediation_thread_id,
            proposal_id=proposal_id,
        )
        store.put(
            incident_id,
            IncidentSession(
                environment=environment,
                coordinator=coordinator,
                remediation_thread_id=remediation_thread_id,
                proposal_id=proposal_id,
                affected_service=scenario.affected_service,
            ),
        )
        investigation = started.investigation
        metrics = investigation["metrics"]
        hypothesis_result = investigation["hypothesis_result"]
        if metrics is None or hypothesis_result is None:
            raise HTTPException(
                status_code=500,
                detail="Investigation completed without metrics or a hypothesis.",
            )
        return IncidentStartResponse(
            incident_id=started.incident_id,
            scenario_id=scenario.id,
            affected_service=started.affected_service,
            status=started.status,
            investigation_status=investigation["status"],
            investigation_steps=list(investigation["completed_steps"]),
            metrics=metrics,
            hypothesis_result=hypothesis_result,
            recommended_action=started.recommended_action,
            proposed_version=started.proposed_version,
            approval_request=started.approval_request,
            resolved=False,
        )

    @app.post(
        "/api/incidents/{incident_id}/approval",
        response_model=IncidentApprovalResponse,
    )
    def submit_approval(
        incident_id: str,
        body: SubmitApprovalRequest,
    ) -> IncidentApprovalResponse:
        session = _require_session(store, incident_id)
        try:
            resumed = session.coordinator.resume(
                remediation_thread_id=session.remediation_thread_id,
                approved=body.approved,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return IncidentApprovalResponse(
            incident_id=incident_id,
            status=resumed.status,
            execution_success=resumed.execution_success,
            recovered_p95_latency_ms=resumed.recovered_p95_latency_ms,
            recovered_error_rate_percent=resumed.recovered_error_rate_percent,
            resolved=resumed.status == "resolved",
            approval_status=resumed.approval_status,
        )

    @app.get(
        "/api/incidents/{incident_id}/metrics",
        response_model=MetricResponse,
    )
    def get_incident_metrics(incident_id: str) -> MetricResponse:
        session = _require_session(store, incident_id)
        return DiagnosticTools(session.environment).query_metrics(
            session.affected_service
        )

    return app


def _require_session(
    store: IncidentSessionStore,
    incident_id: str,
) -> IncidentSession:
    try:
        return store.get(incident_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
