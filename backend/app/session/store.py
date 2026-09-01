from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import psycopg
from psycopg.rows import dict_row

from backend.app.session.models import DemoSession


@runtime_checkable
class DemoSessionStore(Protocol):
    def get_or_create(self, session_id: str | None) -> DemoSession: ...

    def touch(self, session_id: str) -> None: ...

    def increment_live_incident_count(self, session_id: str) -> int: ...

    def get(self, session_id: str) -> DemoSession | None: ...


def _session_from_row(row: dict[str, object]) -> DemoSession:
    created_at = row["created_at"]
    last_seen_at = row["last_seen_at"]
    if not isinstance(created_at, datetime) or not isinstance(last_seen_at, datetime):
        raise TypeError("expected datetime values")
    return DemoSession(
        session_id=str(row["session_id"]),
        created_at=created_at,
        last_seen_at=last_seen_at,
        live_incident_count=int(row.get("live_incident_count", 0)),
    )


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


class PostgresDemoSessionStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_or_create(self, session_id: str | None) -> DemoSession:
        if session_id:
            existing = self.get(session_id)
            if existing is not None:
                self.touch(session_id)
                return existing
        new_id = new_session_id()
        now = datetime.now(UTC)
        with psycopg.connect(self._database_url) as conn:
            conn.execute(
                """
                INSERT INTO demo_sessions (session_id, created_at, last_seen_at, live_incident_count)
                VALUES (%s, %s, %s, 0)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (new_id, now, now),
            )
        created = self.get(new_id)
        if created is None:
            raise RuntimeError("Failed to create demo session")
        return created

    def touch(self, session_id: str) -> None:
        now = datetime.now(UTC)
        with psycopg.connect(self._database_url) as conn:
            conn.execute(
                "UPDATE demo_sessions SET last_seen_at = %s WHERE session_id = %s",
                (now, session_id),
            )

    def increment_live_incident_count(self, session_id: str) -> int:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                UPDATE demo_sessions
                SET live_incident_count = live_incident_count + 1,
                    last_seen_at = %s
                WHERE session_id = %s
                RETURNING live_incident_count
                """,
                (datetime.now(UTC), session_id),
            ).fetchone()
            if row is None:
                return 0
            return int(row["live_incident_count"])

    def get(self, session_id: str) -> DemoSession | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT session_id, created_at, last_seen_at, live_incident_count
                FROM demo_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return _session_from_row(row)


class InMemoryDemoSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, DemoSession] = {}

    def get_or_create(self, session_id: str | None) -> DemoSession:
        if session_id and session_id in self._sessions:
            self.touch(session_id)
            return self._sessions[session_id]
        new_id = new_session_id()
        now = datetime.now(UTC)
        session = DemoSession(
            session_id=new_id,
            created_at=now,
            last_seen_at=now,
            live_incident_count=0,
        )
        self._sessions[new_id] = session
        return session

    def touch(self, session_id: str) -> None:
        existing = self._sessions.get(session_id)
        if existing is None:
            return
        self._sessions[session_id] = DemoSession(
            session_id=existing.session_id,
            created_at=existing.created_at,
            last_seen_at=datetime.now(UTC),
            live_incident_count=existing.live_incident_count,
        )

    def increment_live_incident_count(self, session_id: str) -> int:
        existing = self._sessions.get(session_id)
        if existing is None:
            return 0
        count = existing.live_incident_count + 1
        self._sessions[session_id] = DemoSession(
            session_id=existing.session_id,
            created_at=existing.created_at,
            last_seen_at=datetime.now(UTC),
            live_incident_count=count,
        )
        return count

    def get(self, session_id: str) -> DemoSession | None:
        return self._sessions.get(session_id)
