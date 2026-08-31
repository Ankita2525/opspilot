from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime

from fastapi.responses import StreamingResponse

from backend.app.agent.incident_response import IncidentResponseStartResult
from backend.app.events.emitter import InvestigationEventEmitter
from backend.app.events.models import InvestigationEvent, InvestigationEventType
from backend.app.events.sse import encode_sse
from backend.app.safety.models import RiskLevel
from simulator.models import IncidentScenario

TERMINAL_EVENT_TYPES = {
    InvestigationEventType.APPROVAL_REQUIRED,
    InvestigationEventType.INCIDENT_COMPLETED,
    InvestigationEventType.INCIDENT_FAILED,
}

BeginIncident = Callable[
    [IncidentScenario, InvestigationEventEmitter | None],
    IncidentResponseStartResult,
]
Clock = Callable[[], datetime]


def streamed_incident_response(
    *,
    scenario: IncidentScenario,
    incident_id: str,
    begin_incident: BeginIncident,
    now: Clock | None = None,
) -> StreamingResponse:
    queue: asyncio.Queue[InvestigationEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def publish(event: InvestigationEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    emitter = InvestigationEventEmitter(
        incident_id=incident_id,
        publish=publish,
        now=now,
    )

    def run_workflow() -> None:
        try:
            _execute_streamed_incident(
                scenario=scenario,
                begin_incident=begin_incident,
                emitter=emitter,
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def generate() -> AsyncIterator[str]:
        worker = asyncio.create_task(asyncio.to_thread(run_workflow))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield encode_sse(item)
                if item.event_type in TERMINAL_EVENT_TYPES:
                    break
        finally:
            await worker

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _execute_streamed_incident(
    *,
    scenario: IncidentScenario,
    begin_incident: BeginIncident,
    emitter: InvestigationEventEmitter,
) -> None:
    emitter.emit(
        InvestigationEventType.INCIDENT_STARTED,
        message="Incident investigation started.",
        data={
            "scenario_id": scenario.id,
            "affected_service": scenario.affected_service,
        },
    )
    try:
        started = begin_incident(scenario, emitter)
    except Exception:
        emitter.emit(
            InvestigationEventType.INCIDENT_FAILED,
            message="Investigation could not be completed.",
            data={
                "error": "investigation_failed",
                "message": "Investigation could not be completed.",
            },
        )
        return
    if started.status == "approval_required" and started.approval_request is not None:
        request = started.approval_request
        emitter.emit(
            InvestigationEventType.APPROVAL_REQUIRED,
            message="Rollback requires human approval.",
            data={
                "proposal_id": request["proposal_id"],
                "action": request["action"],
                "service": request["service"],
                "version": request["version"],
                "risk_level": RiskLevel.HIGH_RISK.value,
            },
        )
        return
    emitter.emit(
        InvestigationEventType.INCIDENT_COMPLETED,
        message=(
            "Investigation complete. No supported automated remediation was selected. "
            "Production remains unchanged."
        ),
        data={
            "status": started.status,
            "recommended_action": started.recommended_action,
        },
    )
