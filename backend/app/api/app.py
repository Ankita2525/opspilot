from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow, RemediationState
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.api.incident_stream import streamed_incident_response
from backend.app.api.public_records import (
    public_baseline_evaluation,
    public_incident_audit,
    public_incident_provenance,
    public_incident_summary,
)
from backend.app.api.schemas import (
    BaselineEvaluationResponse,
    HealthResponse,
    HealthzResponse,
    IncidentApprovalResponse,
    IncidentAuditResponse,
    IncidentProvenanceResponse,
    IncidentStartResponse,
    IncidentSummaryResponse,
    ReadyResponse,
    ScenarioSummary,
    StartIncidentRequest,
    SubmitApprovalRequest,
)
from backend.app.api.public_guard import (
    acquire_global_lease,
    check_rate_limit,
    incident_expires_at,
    release_global_lease,
    renew_global_lease,
    require_incident_owner,
    resolve_demo_session,
    sandbox_status,
    verify_turnstile,
)
from backend.app.api.session_store import IncidentSession, IncidentSessionStore
from backend.app.cleanup.worker import IncidentCleanupWorker
from backend.app.readiness import assess_readiness
from backend.app.sandbox.fault_reconcile import (
    restore_all_sandbox_baselines,
    safe_expire_stale_leases,
)
from backend.app.sandbox.hardening import SandboxHardening, build_sandbox_hardening, list_expired_incidents
from backend.app.config import DEFAULT_CORS_ORIGINS, OpsPilotSettings
from backend.app.models.provider_errors import ModelCallError
from backend.app.observability.metrics import (
    approval_count,
    cleanup_count,
    live_incidents_completed,
)
from backend.app.telemetry.factory import build_live_runtime, build_reference_runtime
from backend.app.telemetry.models import TelemetryMode
from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.ids import new_incident_id
from backend.app.live.orchestrator import LiveIncidentOrchestrator
from backend.app.live.reconcile import LiveSessionReconciler
from backend.app.models.groq_provider import GroqModelProvider
from backend.app.models.provider import ModelProvider
from backend.app.observability.tracing import get_tracer
from backend.app.persistence.lifecycle import IncidentLifecyclePersistence
from backend.app.provenance.store import ProvenanceStore
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.models import ApprovalRecord, IncidentRecord
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.runtime import RuntimeResources, build_runtime_from_settings
from backend.app.safety.approvals import ApprovalService
from backend.app.security.untrusted_text import sanitize_public_instance
from backend.app.tools.diagnostics import DiagnosticTools
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
    hardening: SandboxHardening | None = None,
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
    provenance_store = ProvenanceStore(resolved_repository, resolved_settings, now=now)
    store = IncidentSessionStore()
    live_orchestrator = LiveIncidentOrchestrator()
    live_reconciler = LiveSessionReconciler(orchestrator=live_orchestrator)
    resolved_hardening = hardening or build_sandbox_hardening(
        resolved_settings,
        resolved_repository,
    )
    if provider is not None:
        resolved_provider = provider
    elif resolved_hardening.enforce_live_guards:
        resolved_provider = resolved_hardening.wrap_provider(runtime.provider)
    else:
        resolved_provider = runtime.provider

    cleanup_worker: IncidentCleanupWorker | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal cleanup_worker
        runtime.startup()
        app.state.checkpointer = runtime.checkpointer
        runtime.ensure_production_checkpointer_configured()
        if resolved_hardening.enforce_live_guards:
            safe_expire_stale_leases(resolved_hardening)
            _reconcile_stale_incidents_on_startup()
            cleanup_worker = IncidentCleanupWorker(
                lease_store=resolved_hardening.lease_store,
                session_store=store,
                live_orchestrator=live_orchestrator,
                repository=resolved_repository,
                list_expired_incidents=lambda as_of: list_expired_incidents(
                    resolved_repository,
                    as_of,
                ),
                lease_ttl_seconds=resolved_hardening.lease_ttl_seconds,
                interval_seconds=resolved_hardening.cleanup_interval_seconds,
                hardening=resolved_hardening,
                persistence=persistence,
            )
            cleanup_worker.start()
        else:
            resolved_hardening.lease_store.expire_stale()
        try:
            yield
        finally:
            if cleanup_worker is not None:
                await cleanup_worker.stop()
            runtime.shutdown()

    def _reconcile_stale_incidents_on_startup() -> None:
        """Persistent-state backstop: terminalize stale in_progress + clear faults."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        for incident_id, session_id in list_expired_incidents(resolved_repository, now):
            record = resolved_repository.get_incident(incident_id)
            scenario_id = record.scenario_id if record is not None else None
            if scenario_id:
                from backend.app.sandbox.fault_reconcile import restore_baseline_for_scenario

                restore_baseline_for_scenario(scenario_id)
            else:
                restore_all_sandbox_baselines()
            persistence.record_incident_failed(
                incident_id=incident_id,
                reason="timed_out",
                stage="startup_reconcile",
            )
            if session_id:
                release_global_lease(
                    hardening=resolved_hardening,
                    session_id=session_id,
                    incident_id=incident_id,
                )
            in_memory = store.get_optional(incident_id)
            if in_memory is not None and in_memory.live_session is not None:
                try:
                    live_orchestrator.cleanup(in_memory.live_session)
                except Exception:
                    pass
                try:
                    store.remove(incident_id)
                except ValueError:
                    pass
        safe_expire_stale_leases(resolved_hardening)

    app = FastAPI(title="OpsPilot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.provider = resolved_provider
    app.state.store = store
    app.state.repository = resolved_repository
    app.state.checkpointer = resolved_checkpointer
    app.state.runtime = runtime
    app.state.settings = resolved_settings
    app.state.hardening = resolved_hardening

    def _telemetry_mode() -> TelemetryMode:
        if resolved_settings is None:
            return TelemetryMode.REFERENCE
        return resolved_settings.telemetry_mode

    def _build_coordinator(
        *,
        diagnostics: DiagnosticTools,
        remediation_tools,
        approvals: ApprovalService,
        events: InvestigationEventEmitter | None = None,
        verify_recovery_fn=None,
        session_id: str | None = None,
        incident_id: str | None = None,
    ) -> IncidentResponseCoordinator:
        hypothesis_provider = resolved_provider
        if session_id and incident_id and hasattr(resolved_provider, "with_context"):
            hypothesis_provider = resolved_provider.with_context(
                session_id=session_id,
                incident_id=incident_id,
            )
        elif resolved_hardening.enforce_live_guards and incident_id:
            hypothesis_provider = resolved_hardening.wrap_provider(
                runtime.provider,
                session_id=session_id,
                incident_id=incident_id,
            )
        return IncidentResponseCoordinator(
            investigation_workflow=InvestigationWorkflow(
                tools=diagnostics,
                hypothesis_engine=HypothesisEngine(hypothesis_provider),
                events=events,
            ),
            remediation_workflow=RemediationApprovalWorkflow(
                remediation_tools=remediation_tools,
                approvals=approvals,
                diagnostic_tools=diagnostics,
                checkpointer=app.state.checkpointer,
                allow_in_memory_checkpointer=runtime.allow_in_memory_checkpointer,
                verify_recovery_fn=verify_recovery_fn,
            ),
        )

    def _runtime_for_scenario(
        scenario_id: str,
        events: InvestigationEventEmitter | None = None,
        *,
        live_session=None,
        verify_recovery_fn=None,
        session_id: str | None = None,
        incident_id: str | None = None,
    ):
        approvals = ApprovalService(repository=resolved_repository, now=now)
        if _telemetry_mode() is TelemetryMode.LIVE:
            if live_session is None:
                raise ValueError("Live telemetry mode requires a prepared live session.")
            incident_runtime = build_live_runtime(
                service=live_session.mapping.affected_service,
                telemetry=live_session.telemetry,
                remediation_backend=live_session.remediation,
                approvals=approvals,
            )
            return (
                None,
                _build_coordinator(
                    diagnostics=incident_runtime.diagnostics,
                    remediation_tools=incident_runtime.remediation,
                    approvals=approvals,
                    events=events,
                    verify_recovery_fn=verify_recovery_fn,
                    session_id=session_id,
                    incident_id=incident_id,
                ),
            )
        incident_runtime = build_reference_runtime(scenario_id, approvals)
        return (
            incident_runtime.simulator_environment,
            _build_coordinator(
                diagnostics=incident_runtime.diagnostics,
                remediation_tools=incident_runtime.remediation,
                approvals=approvals,
                events=events,
                verify_recovery_fn=verify_recovery_fn,
                session_id=session_id,
                incident_id=incident_id,
            ),
        )

    def _begin_incident(
        scenario,
        events: InvestigationEventEmitter | None = None,
        *,
        incident_id: str,
        live_session=None,
        owner_session_id: str | None = None,
        expires_at: datetime | None = None,
    ):
        verify_fn = None
        if live_session is not None:

            def verify_fn(state: RemediationState) -> dict:
                live_session.remediation_at = datetime.now(UTC)
                return live_orchestrator.verify_recovery(live_session, events=events)

        environment, coordinator = _runtime_for_scenario(
            scenario.id,
            events=events,
            live_session=live_session,
            verify_recovery_fn=verify_fn,
            session_id=owner_session_id,
            incident_id=incident_id,
        )
        if live_session is not None and live_session.blocked:
            created_at = persistence.record_incident_created(
                incident_id=incident_id,
                scenario_id=scenario.id,
                affected_service=scenario.affected_service,
                session_id=owner_session_id,
                expires_at=expires_at,
            )
            started = _blocked_live_start(
                incident_id=incident_id,
                scenario=scenario,
                live_session=live_session,
            )
            persistence.record_start_result(incident_id=incident_id, started=started)
            store.put(
                incident_id,
                IncidentSession(
                    coordinator=coordinator,
                    remediation_thread_id=incident_id,
                    proposal_id=f"{incident_id}-proposal",
                    affected_service=scenario.affected_service,
                    scenario_id=scenario.id,
                    created_at=created_at,
                    telemetry_mode="live",
                    live_session=live_session,
                    owner_session_id=owner_session_id,
                ),
            )
            return started

        remediation_thread_id = incident_id
        proposal_id = f"{incident_id}-proposal"
        created_at = persistence.record_incident_created(
            incident_id=incident_id,
            scenario_id=scenario.id,
            affected_service=scenario.affected_service,
            session_id=owner_session_id,
            expires_at=expires_at,
        )
        started = coordinator.start(
            incident_id=incident_id,
            affected_service=scenario.affected_service,
            remediation_thread_id=remediation_thread_id,
            proposal_id=proposal_id,
        )
        persistence.record_start_result(incident_id=incident_id, started=started)
        if live_session is not None:
            evidence_count = len(live_session.observed_logs) + (
                1 if live_session.degraded_summary.get("request_count") else 0
            )
            provenance_store.save_after_investigation(
                live_session=live_session,
                started=started,
                model_provider=_model_provider_label(resolved_settings),
                model_name=_model_name(resolved_settings),
                evidence_count=evidence_count,
            )
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
                telemetry_mode="live" if live_session is not None else "reference",
                live_session=live_session,
                owner_session_id=owner_session_id,
            ),
        )
        return started

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Process-local probe target for Cloud Run + public warm-up.

        No DB, HTTP, Loki, Prometheus, model, or lease I/O. Prefer this over
        exact `/healthz`, which Google Frontend may intercept publicly.
        """
        return HealthResponse(status="ok", service="opspilot")

    @app.get("/healthz", response_model=HealthzResponse)
    def healthz() -> HealthzResponse:
        """Internal alias of process-local health. Do not use for public checks.

        Exact path `/healthz` is intercepted by Google Frontend on this Cloud Run
        service (public HTML 404; request never reaches the container). Kept only
        as a low-churn alias for any leftover internal callers.
        """
        return HealthzResponse(status="ok")

    @app.get("/ready", response_model=ReadyResponse)
    async def ready() -> ReadyResponse:
        """Deep dependency diagnostic. Not used as a Cloud Run lifecycle probe."""
        report = await assess_readiness(runtime, resolved_hardening)
        payload = report.to_response()
        return ReadyResponse(**payload)

    @app.get("/api/sandbox/status")
    def get_sandbox_status(request: Request, response: Response) -> dict:
        resolve_demo_session(request, resolved_hardening, response)
        return sandbox_status(resolved_hardening)

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

    @app.get("/api/runtime")
    def runtime_summary() -> dict:
        if resolved_settings is None:
            return {"telemetry_mode": "reference"}
        return resolved_settings.safe_summary()

    @app.post("/api/incidents/start", response_model=IncidentStartResponse)
    def start_incident(
        body: StartIncidentRequest,
        request: Request,
        response: Response,
    ) -> IncidentStartResponse:
        check_rate_limit(request, resolved_hardening)
        demo_session = resolve_demo_session(request, resolved_hardening, response)
        try:
            scenario = get_scenario(body.scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        incident_id = new_incident_id()
        live_session = None
        if _telemetry_mode() is TelemetryMode.LIVE:
            verify_turnstile(
                token=body.turnstile_token,
                request=request,
                hardening=resolved_hardening,
            )
            acquire_global_lease(
                hardening=resolved_hardening,
                session=demo_session,
                incident_id=incident_id,
            )
            try:
                live_session = live_orchestrator.prepare(
                    incident_id=incident_id,
                    scenario_id=scenario.id,
                )
            except RuntimeError as exc:
                release_global_lease(
                    hardening=resolved_hardening,
                    session_id=demo_session.session_id,
                    incident_id=incident_id,
                )
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        try:
            started = _begin_incident(
                scenario,
                incident_id=incident_id,
                live_session=live_session,
                owner_session_id=demo_session.session_id,
                expires_at=incident_expires_at(resolved_hardening),
            )
        except Exception as exc:
            if live_session is not None:
                _finalize_failed_live_incident(
                    incident_id=incident_id,
                    live_session=live_session,
                    owner_session_id=demo_session.session_id,
                    exc=exc,
                )
            raise
        investigation = started.investigation
        metrics = investigation["metrics"]
        hypothesis_result = investigation["hypothesis_result"]
        if metrics is None or hypothesis_result is None:
            if live_session is not None:
                live_orchestrator.cleanup(live_session)
                release_global_lease(
                    hardening=resolved_hardening,
                    session_id=demo_session.session_id,
                    incident_id=incident_id,
                )
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
    async def stream_incident(
        body: StartIncidentRequest,
        request: Request,
        response: Response,
    ):
        check_rate_limit(request, resolved_hardening)
        demo_session = resolve_demo_session(request, resolved_hardening, response)
        try:
            scenario = get_scenario(body.scenario_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        incident_id = new_incident_id()
        expires_at = incident_expires_at(resolved_hardening)

        if _telemetry_mode() is TelemetryMode.LIVE:
            verify_turnstile(
                token=body.turnstile_token,
                request=request,
                hardening=resolved_hardening,
            )
            acquire_global_lease(
                hardening=resolved_hardening,
                session=demo_session,
                incident_id=incident_id,
            )

        def begin(loaded, events, _incident_id=incident_id):
            live_session = None
            if _telemetry_mode() is TelemetryMode.LIVE:
                try:
                    live_session = live_orchestrator.prepare(
                        incident_id=_incident_id,
                        scenario_id=loaded.id,
                        events=events,
                    )
                except RuntimeError:
                    release_global_lease(
                        hardening=resolved_hardening,
                        session_id=demo_session.session_id,
                        incident_id=_incident_id,
                    )
                    raise
            try:
                return _begin_incident(
                    loaded,
                    events,
                    incident_id=_incident_id,
                    live_session=live_session,
                    owner_session_id=demo_session.session_id,
                    expires_at=expires_at,
                )
            except Exception as exc:
                _finalize_failed_live_incident(
                    incident_id=_incident_id,
                    live_session=live_session,
                    owner_session_id=demo_session.session_id,
                    exc=exc,
                )
                raise

        def on_failure(exc: Exception, _incident_id=incident_id) -> None:
            # Idempotent terminal persistence if begin did not reach finalize.
            reason = "investigation_failed"
            stage = "generate_hypothesis"
            diagnostic = None
            if isinstance(exc, ModelCallError):
                reason = exc.meta.public_reason()
                stage = exc.meta.stage or stage
                diagnostic = exc.meta.safe_log_dict()
            persistence.record_incident_failed(
                incident_id=_incident_id,
                reason=reason,
                stage=stage,
                diagnostic=diagnostic,
            )

        return streamed_incident_response(
            scenario=scenario,
            incident_id=incident_id,
            begin_incident=begin,
            now=now,
            on_failure=on_failure,
        )

    def _finalize_failed_live_incident(
        *,
        incident_id: str,
        live_session,
        owner_session_id: str | None,
        exc: Exception,
    ) -> None:
        reason = "investigation_failed"
        stage = "unknown"
        diagnostic = None
        selected_skills = None
        if isinstance(exc, ModelCallError):
            reason = exc.meta.public_reason()
            stage = exc.meta.stage or "generate_hypothesis"
            diagnostic = exc.meta.safe_log_dict()
        persistence.record_incident_failed(
            incident_id=incident_id,
            reason=reason,
            stage=stage,
            selected_skills=selected_skills,
            diagnostic=diagnostic,
        )
        if live_session is not None:
            try:
                live_orchestrator.cleanup(live_session)
            except Exception:
                pass
        if owner_session_id is not None and _telemetry_mode() is TelemetryMode.LIVE:
            release_global_lease(
                hardening=resolved_hardening,
                session_id=owner_session_id,
                incident_id=incident_id,
            )

    @app.post(
        "/api/incidents/{incident_id}/approval",
        response_model=IncidentApprovalResponse,
    )
    def submit_approval(
        incident_id: str,
        body: SubmitApprovalRequest,
        request: Request,
        response: Response,
    ) -> IncidentApprovalResponse:
        check_rate_limit(request, resolved_hardening)
        demo_session = resolve_demo_session(request, resolved_hardening, response)
        session = store.get_optional(incident_id)
        if session is None:
            session = _reconstruct_approval_session(
                incident_id=incident_id,
                store=store,
                repository=resolved_repository,
                runtime_factory=lambda scenario_id, **kwargs: _runtime_for_scenario(
                    scenario_id, **kwargs
                ),
                telemetry_mode=_telemetry_mode(),
                live_reconciler=live_reconciler,
                provenance_store=provenance_store,
            )
        record = resolved_repository.get_incident(incident_id)
        owner_id = session.owner_session_id or (
            record.session_id if record is not None else None
        )
        require_incident_owner(
            owner_session_id=owner_id,
            requester_session_id=demo_session.session_id,
            enforce=resolved_hardening.enforce_live_guards,
        )
        renew_global_lease(
            hardening=resolved_hardening,
            session_id=demo_session.session_id,
            incident_id=incident_id,
        )
        try:
            resumed = session.coordinator.resume(
                remediation_thread_id=session.remediation_thread_id,
                approved=body.approved,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        approval_count.add(1, {"action": "approved" if body.approved else "rejected"})
        persistence.record_resume_result(
            incident_id=incident_id,
            proposal_id=session.proposal_id,
            resumed=resumed,
        )
        if session.live_session is not None:
            provenance_store.save_after_resume(
                incident_id=incident_id,
                resumed=resumed,
                remediation_at=session.live_session.remediation_at,
                approved_at=datetime.now(UTC) if body.approved else None,
                executed_at=datetime.now(UTC) if resumed.execution_success else None,
            )
        if session.live_session is not None and resumed.status in {
            "resolved",
            "remediation_failed",
            "rejected",
            "verification_pending",
        }:
            live_orchestrator.cleanup(session.live_session)
            release_global_lease(
                hardening=resolved_hardening,
                session_id=demo_session.session_id,
                incident_id=incident_id,
            )
            live_incidents_completed.add(1)
            cleanup_count.add(1)
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
    def get_incident_metrics(
        incident_id: str,
        request: Request,
        response: Response,
    ) -> MetricResponse:
        demo_session = resolve_demo_session(request, resolved_hardening, response)
        session = _require_session(store, incident_id)
        record = resolved_repository.get_incident(incident_id)
        owner_id = session.owner_session_id or (
            record.session_id if record is not None else None
        )
        require_incident_owner(
            owner_session_id=owner_id,
            requester_session_id=demo_session.session_id,
            enforce=resolved_hardening.enforce_live_guards,
        )
        if session.live_session is not None:
            return session.live_session.telemetry.query_metrics(session.affected_service)
        if session.environment is None:
            raise HTTPException(status_code=404, detail="Incident telemetry unavailable.")
        from backend.app.telemetry.simulator import SimulatorTelemetryBackend

        return DiagnosticTools(SimulatorTelemetryBackend(session.environment)).query_metrics(
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
        "/api/incidents/{incident_id}/provenance",
        response_model=IncidentProvenanceResponse,
    )
    def get_incident_provenance(incident_id: str) -> IncidentProvenanceResponse:
        provenance = provenance_store.load(incident_id)
        if provenance is None:
            raise HTTPException(
                status_code=404,
                detail=f"No provenance for incident: {incident_id}",
            )
        return public_incident_provenance(provenance)

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


def _blocked_live_start(*, incident_id, scenario, live_session):
    from backend.app.agent.incident_response import IncidentResponseStartResult

    return IncidentResponseStartResult(
        incident_id=incident_id,
        affected_service=scenario.affected_service,
        status="blocked_by_telemetry",
        investigation={
            "status": "blocked_by_telemetry",
            "completed_steps": [],
            "metrics": None,
            "hypothesis_result": None,
            "selected_skills": [],
        },
        recommended_action=None,
        proposed_version=None,
        approval_request=None,
    )


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
    runtime_factory: Callable,
    telemetry_mode: TelemetryMode = TelemetryMode.REFERENCE,
    live_reconciler: LiveSessionReconciler | None = None,
    provenance_store: ProvenanceStore | None = None,
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
        live_session = None
        verify_fn = None
        if telemetry_mode is TelemetryMode.LIVE:
            if provenance_store is None or live_reconciler is None:
                raise HTTPException(
                    status_code=409,
                    detail="Live incident cannot be resumed on this instance.",
                )
            provenance = provenance_store.load(incident_id)
            if provenance is None:
                raise HTTPException(
                    status_code=409,
                    detail="Live incident provenance unavailable for resume.",
                )
            try:
                live_session = live_reconciler.reconcile_for_approval(
                    incident_id=incident_id,
                    scenario_id=record.scenario_id,
                    provenance=provenance,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            def verify_fn(state: RemediationState) -> dict:
                assert live_session is not None
                live_session.remediation_at = datetime.now(UTC)
                return LiveIncidentOrchestrator().verify_recovery(live_session)

        if live_session is not None:
            environment, coordinator = runtime_factory(
                record.scenario_id,
                live_session=live_session,
                verify_recovery_fn=verify_fn,
                incident_id=incident_id,
            )
        else:
            environment, coordinator = runtime_factory(record.scenario_id)
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
            telemetry_mode="live" if live_session is not None else "reference",
            live_session=live_session,
            owner_session_id=record.session_id,
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


def _model_provider_label(settings: OpsPilotSettings | None) -> str:
    if settings is None:
        return "deterministic"
    return settings.model_provider.value


def _model_name(settings: OpsPilotSettings | None) -> str | None:
    if settings is None:
        return None
    if settings.model_provider.value == "groq":
        return settings.groq_model
    return None
