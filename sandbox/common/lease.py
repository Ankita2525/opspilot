from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DEFAULT_LEASE_TTL_SECONDS = 600


@dataclass
class SandboxLease:
    service: str
    incident_id: str
    acquired_at: datetime
    expires_at: datetime


class SandboxLeaseManager:
    """One active fault session per sandbox service."""

    def __init__(self, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._leases: dict[str, SandboxLease] = {}
        self._lock = threading.Lock()

    def try_acquire(self, service: str, incident_id: str) -> SandboxLease | None:
        with self._lock:
            self._expire_stale_locked()
            existing = self._leases.get(service)
            if existing is not None and existing.incident_id != incident_id:
                return None
            now = datetime.now(UTC)
            lease = SandboxLease(
                service=service,
                incident_id=incident_id,
                acquired_at=now,
                expires_at=now + timedelta(seconds=self._ttl_seconds),
            )
            self._leases[service] = lease
            return lease

    def release(self, service: str, incident_id: str) -> bool:
        with self._lock:
            existing = self._leases.get(service)
            if existing is None or existing.incident_id != incident_id:
                return False
            del self._leases[service]
            return True

    def get(self, service: str) -> SandboxLease | None:
        with self._lock:
            self._expire_stale_locked()
            return self._leases.get(service)

    def is_busy(self, service: str, incident_id: str) -> bool:
        with self._lock:
            self._expire_stale_locked()
            existing = self._leases.get(service)
            return existing is not None and existing.incident_id != incident_id

    def _expire_stale_locked(self) -> None:
        now = datetime.now(UTC)
        expired = [
            service
            for service, lease in self._leases.items()
            if lease.expires_at <= now
        ]
        for service in expired:
            del self._leases[service]

    def renew(self, service: str, incident_id: str) -> bool:
        with self._lock:
            existing = self._leases.get(service)
            if existing is None or existing.incident_id != incident_id:
                return False
            now = datetime.now(UTC)
            self._leases[service] = SandboxLease(
                service=service,
                incident_id=incident_id,
                acquired_at=existing.acquired_at,
                expires_at=now + timedelta(seconds=self._ttl_seconds),
            )
            return True

    def sleep_until_expired(self, service: str, timeout: float = 0.1) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                lease = self._leases.get(service)
                if lease is None:
                    return
            time.sleep(0.01)
