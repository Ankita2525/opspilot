from dataclasses import dataclass
from datetime import datetime

from backend.app.agent.incident_response import IncidentResponseCoordinator
from simulator.environment import SimulatedEnvironment


@dataclass
class IncidentSession:
    environment: SimulatedEnvironment
    coordinator: IncidentResponseCoordinator
    remediation_thread_id: str
    proposal_id: str
    affected_service: str
    scenario_id: str
    created_at: datetime


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
            del self._sessions[incident_id]
        except KeyError as exc:
            raise ValueError(f"Unknown incident: {incident_id}") from exc
