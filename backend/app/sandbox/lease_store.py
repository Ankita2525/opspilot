from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import psycopg
from psycopg.rows import dict_row

from backend.app.ids import new_incident_id
from backend.app.sandbox.models import GlobalSandboxLease, LeaseAcquireResult, LeaseState

SINGLETON_LEASE_ID = 1


@runtime_checkable
class GlobalSandboxLeaseStore(Protocol):
    def acquire(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> LeaseAcquireResult: ...

    def renew(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> bool: ...

    def release(self, *, session_id: str, incident_id: str) -> bool: ...

    def inspect(self) -> GlobalSandboxLease | None: ...

    def peek(self) -> GlobalSandboxLease | None: ...

    def expire_stale(self) -> int: ...

    def quarantine(self, *, incident_id: str, reason: str) -> None: ...

    def is_quarantined(self) -> bool: ...

    def clear_quarantine(self) -> bool: ...


def _lease_from_row(row: dict[str, object]) -> GlobalSandboxLease | None:
    state_raw = str(row.get("state", "idle"))
    if state_raw == LeaseState.IDLE.value:
        return None
    lease_id = row.get("lease_id")
    session_id = row.get("session_id")
    if state_raw == LeaseState.QUARANTINED.value:
        lease_id = lease_id or "quarantined"
        session_id = session_id or "quarantined"
    elif lease_id is None or session_id is None:
        return None
    acquired_at = row.get("acquired_at")
    expires_at = row.get("expires_at")
    renewed_at = row.get("renewed_at")
    if not isinstance(acquired_at, datetime) or not isinstance(expires_at, datetime):
        return None
    if not isinstance(renewed_at, datetime):
        renewed_at = acquired_at
    incident_id = row.get("incident_id")
    return GlobalSandboxLease(
        lease_id=str(lease_id),
        session_id=str(session_id),
        incident_id=str(incident_id) if incident_id else None,
        acquired_at=acquired_at,
        expires_at=expires_at,
        renewed_at=renewed_at,
        state=LeaseState(state_raw),
    )


class PostgresGlobalSandboxLeaseStore:
    """Durable global sandbox lease backed by a singleton PostgreSQL row."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def acquire(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> LeaseAcquireResult:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        lease_id = f"lease-{secrets.token_hex(8)}"
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT lease_id, session_id, incident_id, acquired_at,
                           expires_at, renewed_at, state
                    FROM sandbox_lease_holder
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (SINGLETON_LEASE_ID,),
                ).fetchone()
                if row is None:
                    return LeaseAcquireResult(acquired=False, lease=None)
                state_raw = str(row.get("state", "idle"))
                if state_raw == LeaseState.QUARANTINED.value:
                    lease = _lease_from_row(row)
                    return LeaseAcquireResult(
                        acquired=False,
                        lease=lease,
                        quarantined=True,
                    )
                current = _lease_from_row(row)
                if current is not None and current.state is LeaseState.ACTIVE:
                    if current.expires_at > now:
                        if current.session_id == session_id and current.incident_id == incident_id:
                            return LeaseAcquireResult(acquired=True, lease=current)
                        retry = (current.expires_at - now).total_seconds()
                        return LeaseAcquireResult(
                            acquired=False,
                            lease=current,
                            retry_after_seconds=max(retry, 1.0),
                            busy=True,
                        )
                conn.execute(
                    """
                    UPDATE sandbox_lease_holder
                    SET lease_id = %s,
                        session_id = %s,
                        incident_id = %s,
                        acquired_at = %s,
                        expires_at = %s,
                        renewed_at = %s,
                        state = %s
                    WHERE id = %s
                    """,
                    (
                        lease_id,
                        session_id,
                        incident_id,
                        now,
                        expires_at,
                        now,
                        LeaseState.ACTIVE.value,
                        SINGLETON_LEASE_ID,
                    ),
                )
        lease = GlobalSandboxLease(
            lease_id=lease_id,
            session_id=session_id,
            incident_id=incident_id,
            acquired_at=now,
            expires_at=expires_at,
            renewed_at=now,
            state=LeaseState.ACTIVE,
        )
        return LeaseAcquireResult(acquired=True, lease=lease)

    def renew(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE sandbox_lease_holder
                    SET expires_at = %s, renewed_at = %s
                    WHERE id = %s
                      AND state = %s
                      AND session_id = %s
                      AND incident_id = %s
                    """,
                    (
                        expires_at,
                        now,
                        SINGLETON_LEASE_ID,
                        LeaseState.ACTIVE.value,
                        session_id,
                        incident_id,
                    ),
                )
                return result.rowcount == 1

    def release(self, *, session_id: str, incident_id: str) -> bool:
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE sandbox_lease_holder
                    SET state = %s,
                        lease_id = NULL,
                        session_id = NULL,
                        incident_id = NULL
                    WHERE id = %s
                      AND state = %s
                      AND session_id = %s
                      AND incident_id = %s
                    """,
                    (
                        LeaseState.RELEASED.value,
                        SINGLETON_LEASE_ID,
                        LeaseState.ACTIVE.value,
                        session_id,
                        incident_id,
                    ),
                )
                if result.rowcount == 1:
                    conn.execute(
                        """
                        UPDATE sandbox_lease_holder
                        SET state = %s
                        WHERE id = %s AND state = %s
                        """,
                        (LeaseState.IDLE.value, SINGLETON_LEASE_ID, LeaseState.RELEASED.value),
                    )
                    return True
                return False

    def inspect(self) -> GlobalSandboxLease | None:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT lease_id, session_id, incident_id, acquired_at,
                       expires_at, renewed_at, state
                FROM sandbox_lease_holder
                WHERE id = %s
                """,
                (SINGLETON_LEASE_ID,),
            ).fetchone()
            if row is None:
                return None
            return _lease_from_row(row)

    def peek(self) -> GlobalSandboxLease | None:
        """Inspect without mutating expired→idle transitions."""
        return self.inspect()

    def expire_stale(self) -> int:
        now = datetime.now(UTC)
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE sandbox_lease_holder
                    SET state = %s,
                        lease_id = NULL,
                        session_id = NULL,
                        incident_id = NULL
                    WHERE id = %s
                      AND state = %s
                      AND expires_at <= %s
                    """,
                    (
                        LeaseState.EXPIRED.value,
                        SINGLETON_LEASE_ID,
                        LeaseState.ACTIVE.value,
                        now,
                    ),
                )
                expired = result.rowcount
                if expired:
                    conn.execute(
                        """
                        UPDATE sandbox_lease_holder
                        SET state = %s
                        WHERE id = %s AND state = %s
                        """,
                        (LeaseState.IDLE.value, SINGLETON_LEASE_ID, LeaseState.EXPIRED.value),
                    )
                return expired

    def quarantine(self, *, incident_id: str, reason: str) -> None:
        now = datetime.now(UTC)
        with psycopg.connect(self._database_url) as conn:
            conn.execute(
                """
                UPDATE sandbox_lease_holder
                SET state = %s,
                    incident_id = %s,
                    renewed_at = %s
                WHERE id = %s
                """,
                (
                    LeaseState.QUARANTINED.value,
                    incident_id,
                    now,
                    SINGLETON_LEASE_ID,
                ),
            )

    def is_quarantined(self) -> bool:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT state FROM sandbox_lease_holder WHERE id = %s",
                (SINGLETON_LEASE_ID,),
            ).fetchone()
            if row is None:
                return False
            return str(row["state"]) == LeaseState.QUARANTINED.value

    def clear_quarantine(self) -> bool:
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE sandbox_lease_holder
                    SET state = %s,
                        lease_id = NULL,
                        session_id = NULL,
                        incident_id = NULL
                    WHERE id = %s AND state = %s
                    """,
                    (
                        LeaseState.IDLE.value,
                        SINGLETON_LEASE_ID,
                        LeaseState.QUARANTINED.value,
                    ),
                )
                return result.rowcount == 1


class InMemoryGlobalSandboxLeaseStore:
    """Process-local lease store for tests and in-memory runtimes."""

    def __init__(self) -> None:
        self._lease: GlobalSandboxLease | None = None
        self._lock = threading.Lock()

    def acquire(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> LeaseAcquireResult:
        with self._lock:
            now = datetime.now(UTC)
            if self._lease is not None and self._lease.state is LeaseState.QUARANTINED:
                return LeaseAcquireResult(
                    acquired=False,
                    lease=self._lease,
                    quarantined=True,
                )
            self._expire_stale_locked(now)
            if self._lease is not None and self._lease.state is LeaseState.ACTIVE:
                if self._lease.session_id == session_id and self._lease.incident_id == incident_id:
                    return LeaseAcquireResult(acquired=True, lease=self._lease)
                retry = (self._lease.expires_at - now).total_seconds()
                return LeaseAcquireResult(
                    acquired=False,
                    lease=self._lease,
                    retry_after_seconds=max(retry, 1.0),
                    busy=True,
                )
            lease_id = f"lease-{new_incident_id()}"
            self._lease = GlobalSandboxLease(
                lease_id=lease_id,
                session_id=session_id,
                incident_id=incident_id,
                acquired_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                renewed_at=now,
                state=LeaseState.ACTIVE,
            )
            return LeaseAcquireResult(acquired=True, lease=self._lease)

    def renew(
        self,
        *,
        session_id: str,
        incident_id: str,
        ttl_seconds: int,
    ) -> bool:
        with self._lock:
            now = datetime.now(UTC)
            if self._lease is None:
                return False
            if (
                self._lease.session_id != session_id
                or self._lease.incident_id != incident_id
                or self._lease.state is not LeaseState.ACTIVE
            ):
                return False
            self._lease = GlobalSandboxLease(
                lease_id=self._lease.lease_id,
                session_id=session_id,
                incident_id=incident_id,
                acquired_at=self._lease.acquired_at,
                expires_at=now + timedelta(seconds=ttl_seconds),
                renewed_at=now,
                state=LeaseState.ACTIVE,
            )
            return True

    def release(self, *, session_id: str, incident_id: str) -> bool:
        with self._lock:
            if self._lease is None:
                return False
            if (
                self._lease.session_id != session_id
                or self._lease.incident_id != incident_id
            ):
                return False
            self._lease = None
            return True

    def inspect(self) -> GlobalSandboxLease | None:
        with self._lock:
            self._expire_stale_locked(datetime.now(UTC))
            return self._lease

    def peek(self) -> GlobalSandboxLease | None:
        """Return current lease without auto-expiring stale rows."""
        with self._lock:
            return self._lease

    def expire_stale(self) -> int:
        with self._lock:
            before = self._lease
            self._expire_stale_locked(datetime.now(UTC))
            if before is not None and self._lease is None:
                return 1
            return 0

    def _expire_stale_locked(self, now: datetime) -> None:
        if self._lease is None:
            return
        if self._lease.state is LeaseState.QUARANTINED:
            return
        if self._lease.expires_at <= now:
            self._lease = None

    def quarantine(self, *, incident_id: str, reason: str) -> None:
        del reason
        now = datetime.now(UTC)
        with self._lock:
            self._lease = GlobalSandboxLease(
                lease_id="quarantined",
                session_id="quarantined",
                incident_id=incident_id,
                acquired_at=now,
                expires_at=now + timedelta(days=365),
                renewed_at=now,
                state=LeaseState.QUARANTINED,
            )

    def is_quarantined(self) -> bool:
        with self._lock:
            return (
                self._lease is not None
                and self._lease.state is LeaseState.QUARANTINED
            )

    def clear_quarantine(self) -> bool:
        with self._lock:
            if self._lease is None or self._lease.state is not LeaseState.QUARANTINED:
                return False
            self._lease = None
            return True
