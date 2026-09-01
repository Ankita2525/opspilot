from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SESSION_COOKIE_NAME = "opspilot_session"


@dataclass(frozen=True)
class DemoSession:
    session_id: str
    created_at: datetime
    last_seen_at: datetime
    live_incident_count: int = 0
