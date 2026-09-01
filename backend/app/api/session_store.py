from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.agent.incident_response import IncidentResponseCoordinator
from backend.app.live.orchestrator import LiveIncidentSession
from simulator.environment import SimulatedEnvironment


@dataclass
class IncidentSession:
    coordinator: IncidentResponseCoordinator
    remediation_thread_id: str
    proposal_id: str
    affected_service: str
    scenario_id: str
    created_at: datetime
    telemetry_mode: str = "reference"
    owner_session_id: str | None = None
    environment: SimulatedEnvironment | None = None
    live_session: LiveIncidentSession | None = None

    def cleanup(self) -> None:
        if self.live_session is not None:
            from backend.app.live.orchestrator import LiveIncidentOrchestrator

            LiveIncidentOrchestrator().cleanup(self.live_session)


class IncidentSessionStore:
    """In-memory store for active incident-response sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, IncidentSession] = {}

    def put(self, incident_id: str, session: IncidentSession) -> None:
        self._sessions[incident_id] = session

    def get(self, incident_id: str) -> IncidentSession:
        session = self.get_optional(incident_id)
        if session is None:
            raise ValueError(f"Unknown incident: {incident_id}")
        return session

    def get_optional(self, incident_id: str) -> IncidentSession | None:
        return self._sessions.get(incident_id)

    def has(self, incident_id: str) -> bool:
        return incident_id in self._sessions

    def remove(self, incident_id: str) -> None:
        try:
            session = self._sessions.pop(incident_id)
        except KeyError as exc:
            raise ValueError(f"Unknown incident: {incident_id}") from exc
        session.cleanup()
