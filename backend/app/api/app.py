import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.api.incident_stream import streamed_incident_response
from backend.app.api.public_records import (
    public_baseline_evaluation,
    public_incident_audit,
    public_incident_summary,
)
from backend.app.api.schemas import (
    BaselineEvaluationResponse,
    HealthResponse,
    ReadyResponse,
    IncidentApprovalResponse,
    IncidentAuditResponse,
    IncidentStartResponse,
    IncidentSummaryResponse,
    ScenarioSummary,
    StartIncidentRequest,
    SubmitApprovalRequest,
)
from backend.app.api.session_store import IncidentSession, IncidentSessionStore
from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.ids import new_incident_id
from backend.app.config import DEFAULT_CORS_ORIGINS, OpsPilotSettings
from backend.app.models.groq_provider import GroqModelProvider
from backend.app.models.provider import ModelProvider
from backend.app.runtime import RuntimeResources, build_runtime_from_settings
from backend.app.observability.tracing import get_tracer
from backend.app.persistence.lifecycle import IncidentLifecyclePersistence
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import ApprovalRecord, IncidentRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.safety.approvals import ApprovalService
from backend.app.security.untrusted_text import sanitize_public_instance
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
    settings: OpsPilotSettings | None = None,
) -> FastAPI:
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    runtime = _resolve_runtime(
        provider=provider,
        repository=repository,
        checkpointer=checkpointer,
        settings=settings,
    )
    resolved_provider = runtime.provider
    resolved_repository = runtime.repository
    resolved_checkpointer = runtime.checkpointer
    resolved_settings = runtime.settings
    cors_origins = list(
        resolved_settings.cors_origins
        if resolved_settings is not None
        else DEFAULT_CORS_ORIGINS
    )
    persistence = IncidentLifecyclePersistence(resolved_repository, now=now)
    store = IncidentSessionStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime.startup()
        app.state.checkpointer = runtime.checkpointer
        runtime.ensure_production_checkpointer_configured()
        try:
            yield
        finally:
            runtime.shutdown()

    app = FastAPI(title="OpsPilot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.provider = resolved_provider
    app.state.store = store
    app.state.repository = resolved_repository
    app.state.checkpointer = resolved_checkpointer
    app.state.runtime = runtime
    app.state.settings = resolved_settings

    def _runtime_for_scenario(
        scenario_id: str,
        events: InvestigationEventEmitter | None = None,
    ) -> tuple[SimulatedEnvironment, IncidentResponseCoordinator]:
        environment = SimulatedEnvironment()
        environment.load_scenario(scenario_id)
        diagnostics = DiagnosticTools(environment)
        approvals = ApprovalService(repository=resolved_repository, now=now)
        coordinator = IncidentResponseCoordinator(
            investigation_workflow=InvestigationWorkflow(
                tools=diagnostics,
                hypothesis_engine=HypothesisEngine(resolved_provider),
                events=events,
            ),
            remediation_workflow=RemediationApprovalWorkflow(
                remediation_tools=RemediationTools(environment, approvals),
                approvals=approvals,
                diagnostic_tools=diagnostics,
                checkpointer=app.state.checkpointer,
                allow_in_memory_checkpointer=runtime.allow_in_memory_checkpointer,
            ),
        )
        return environment, coordinator

    def _begin_incident(
        scenario,
        events: InvestigationEventEmitter | None = None,
        *,
        incident_id: str,
    ):
        environment, coordinator = _runtime_for_scenario(scenario.id, events=events)
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
        return started

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="opspilot")

    @app.get("/ready", response_model=ReadyResponse)
    def ready() -> ReadyResponse:
        database_status = runtime.database_status
        model_provider = runtime.model_provider_name
        is_ready = database_status in {"ready", "in_memory"}
        return ReadyResponse(
            status="ready" if is_ready else "not_ready",
            database=database_status,
            model_provider=model_provider,
        )

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

        started = _begin_incident(scenario, incident_id=new_incident_id())
        investigation = started.investigation
        metrics = investigation["metrics"]
        hypothesis_result = investigation["hypothesis_result"]
        if metrics is None or hypothesis_result is None:
            raise HTTPException(
                status_code=500,
                detail="Investigation completed without metrics or a hypothesis.",
            )
        return sanitize_public_instance(
            IncidentStartResponse(
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
        )

    @app.post("/api/incidents/stream")
    async def stream_incident(body: StartIncidentRequest):
        try:
            scenario = get_scenario(body.scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        incident_id = new_incident_id()
        return streamed_incident_response(
            scenario=scenario,
            incident_id=incident_id,
            begin_incident=lambda loaded, events, _incident_id=incident_id: (
                _begin_incident(loaded, events, incident_id=_incident_id)
            ),
            now=now,
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
        return sanitize_public_instance(
            IncidentApprovalResponse(
                incident_id=incident_id,
                status=resumed.status,
                execution_success=resumed.execution_success,
                recovered_p95_latency_ms=resumed.recovered_p95_latency_ms,
                recovered_error_rate_percent=resumed.recovered_error_rate_percent,
                resolved=resumed.status == "resolved",
                approval_status=resumed.approval_status,
            )
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

    @app.get(
        "/api/incidents/{incident_id}",
        response_model=IncidentSummaryResponse,
    )
    def get_incident(incident_id: str) -> IncidentSummaryResponse:
        record = _require_incident_record(resolved_repository, incident_id)
        return public_incident_summary(
            record,
            resolved_repository.list_approvals(incident_id),
        )

    @app.get(
        "/api/incidents/{incident_id}/audit",
        response_model=IncidentAuditResponse,
    )
    def get_incident_audit(incident_id: str) -> IncidentAuditResponse:
        _require_incident_record(resolved_repository, incident_id)
        return public_incident_audit(
            incident_id,
            resolved_repository.list_audit_events(incident_id),
        )

    @app.get(
        "/api/evaluations/baseline",
        response_model=BaselineEvaluationResponse,
    )
    def get_baseline_evaluation() -> BaselineEvaluationResponse:
        from backend.app.evals.suite import run_deterministic_baseline_evaluation

        result = run_deterministic_baseline_evaluation()
        return public_baseline_evaluation(result)

    return app


def _resolve_runtime(
    *,
    provider: ModelProvider | None,
    repository: OpsPilotRepository | None,
    checkpointer: BaseCheckpointSaver | None,
    settings: OpsPilotSettings | None,
) -> RuntimeResources:
    has_injections = (
        provider is not None
        or repository is not None
        or checkpointer is not None
    )
    if has_injections:
        return RuntimeResources(
            provider=provider if provider is not None else GroqModelProvider(),
            repository=(
                repository
                if repository is not None
                else InMemoryOpsPilotRepository()
            ),
            checkpointer=checkpointer,
            settings=settings,
        )

    resolved_settings = settings or OpsPilotSettings.from_env()
    runtime = build_runtime_from_settings(resolved_settings)
    if checkpointer is not None:
        runtime.checkpointer = checkpointer
    return runtime


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


def _require_incident_record(
    repository: OpsPilotRepository, incident_id: str
) -> IncidentRecord:
    record = repository.get_incident(incident_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown incident: {incident_id}"
        )
    return record


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
