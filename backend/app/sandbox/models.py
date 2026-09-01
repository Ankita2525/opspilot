from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class LeaseState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class GlobalSandboxLease:
    lease_id: str
    session_id: str
    incident_id: str | None
    acquired_at: datetime
    expires_at: datetime
    renewed_at: datetime
    state: LeaseState


@dataclass(frozen=True)
class LeaseAcquireResult:
    acquired: bool
    lease: GlobalSandboxLease | None
    retry_after_seconds: float | None = None
    busy: bool = False
    quarantined: bool = False
