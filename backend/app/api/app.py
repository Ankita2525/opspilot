import os
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver

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
from backend.app.observability.tracing import get_tracer
from backend.app.persistence.lifecycle import IncidentLifecyclePersistence
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import ApprovalRecord, IncidentRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.safety.approvals import ApprovalService
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from backend.app.tools.schemas import MetricResponse
from simulator.environment import SimulatedEnvironment
from simulator.scenarios import get_scenario, list_scenarios

RESUMABLE_INCIDENT_STATUS = "approval_required"


def create_app(
    provider: ModelProvider | None = None,
    repository: OpsPilotRepository | None = None,
    now: Callable[[], datetime] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> FastAPI:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    resolved_provider: ModelProvider = (
        provider if provider is not None else GroqModelProvider()
    )
    resolved_repository: OpsPilotRepository = (
        repository if repository is not None else InMemoryOpsPilotRepository()
    )
    persistence = IncidentLifecyclePersistence(resolved_repository, now=now)
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
    app.state.repository = resolved_repository
    app.state.checkpointer = checkpointer

    def _runtime_for_scenario(
        scenario_id: str,
    ) -> tuple[SimulatedEnvironment, IncidentResponseCoordinator]:
        environment = SimulatedEnvironment()
        environment.load_scenario(scenario_id)
        diagnostics = DiagnosticTools(environment)
        approvals = ApprovalService(repository=resolved_repository, now=now)
        coordinator = IncidentResponseCoordinator(
            investigation_workflow=InvestigationWorkflow(
                tools=diagnostics,
                hypothesis_engine=HypothesisEngine(resolved_provider),
            ),
            remediation_workflow=RemediationApprovalWorkflow(
                remediation_tools=RemediationTools(environment, approvals),
                approvals=approvals,
                diagnostic_tools=diagnostics,
                checkpointer=checkpointer,
            ),
        )
        return environment, coordinator

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

        environment, coordinator = _runtime_for_scenario(scenario.id)
        incident_id = scenario.id
        remediation_thread_id = incident_id
        proposal_id = f"{incident_id}-proposal"
        created_at = persistence.record_incident_created(
            incident_id=incident_id,
            scenario_id=scenario.id,
            affected_service=scenario.affected_service,
        )
        started = coordinator.start(
            incident_id=incident_id,
            affected_service=scenario.affected_service,
            remediation_thread_id=remediation_thread_id,
            proposal_id=proposal_id,
        )
        persistence.record_start_result(incident_id=incident_id, started=started)
        store.put(
            incident_id,
            IncidentSession(
                environment=environment,
                coordinator=coordinator,
                remediation_thread_id=remediation_thread_id,
                proposal_id=proposal_id,
                affected_service=scenario.affected_service,
                scenario_id=scenario.id,
                created_at=created_at,
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
            selected_skills=list(investigation.get("selected_skills") or []),
        )

    @app.post(
        "/api/incidents/{incident_id}/approval",
        response_model=IncidentApprovalResponse,
    )
    def submit_approval(
        incident_id: str,
        body: SubmitApprovalRequest,
    ) -> IncidentApprovalResponse:
        session = store.get_optional(incident_id)
        if session is None:
            session = _reconstruct_approval_session(
                incident_id=incident_id,
                store=store,
                repository=resolved_repository,
                runtime_factory=_runtime_for_scenario,
            )
        try:
            resumed = session.coordinator.resume(
                remediation_thread_id=session.remediation_thread_id,
                approved=body.approved,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        persistence.record_resume_result(
            incident_id=incident_id,
            proposal_id=session.proposal_id,
            resumed=resumed,
        )
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


def _reconstruct_approval_session(
    *,
    incident_id: str,
    store: IncidentSessionStore,
    repository: OpsPilotRepository,
    runtime_factory: Callable[
        [str], tuple[SimulatedEnvironment, IncidentResponseCoordinator]
    ],
) -> IncidentSession:
    with get_tracer().start_as_current_span("opspilot.checkpoint.resume") as span:
        span.set_attribute("opspilot.incident_id", incident_id)
        record = repository.get_incident(incident_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown incident: {incident_id}"
            )
        _require_resumable_incident(record)
        approval = _require_pending_approval(repository, incident_id)
        try:
            environment, coordinator = runtime_factory(record.scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        thread_id = incident_id
        try:
            coordinator.pending_interrupt(remediation_thread_id=thread_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"No resumable approval checkpoint for incident {incident_id}",
            ) from exc
        session = IncidentSession(
            environment=environment,
            coordinator=coordinator,
            remediation_thread_id=thread_id,
            proposal_id=approval.proposal_id,
            affected_service=record.affected_service,
            scenario_id=record.scenario_id,
            created_at=record.created_at,
        )
        store.put(incident_id, session)
        return session


def _require_resumable_incident(record: IncidentRecord) -> None:
    if record.status != RESUMABLE_INCIDENT_STATUS or record.resolved:
        raise HTTPException(
            status_code=409,
            detail=f"Incident cannot be resumed from status: {record.status}",
        )


def _require_pending_approval(
    repository: OpsPilotRepository, incident_id: str
) -> ApprovalRecord:
    pending = [
        item
        for item in repository.list_approvals(incident_id)
        if item.status == "pending"
    ]
    if len(pending) != 1:
        raise HTTPException(
            status_code=409,
            detail="Incident is not awaiting a pending approval.",
        )
    return pending[0]
