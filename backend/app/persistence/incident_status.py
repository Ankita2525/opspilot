"""Canonical incident lifecycle status sets.

Keep LIST_EXPIRED_INCIDENTS exclusion lists in sync with cleanup worker
terminal handling so durable quarantine recovery is lease-driven, not
driven by forever-retrying terminal incidents.
"""

from __future__ import annotations

# Terminal outcomes that must never re-enter expiry cleanup.
TERMINAL_INCIDENT_STATUSES: frozenset[str] = frozenset(
    {
        "resolved",
        "rejected",
        "remediation_failed",
        "blocked_by_telemetry",
        "abandoned",
        "expired",
        "cleanup_failed",
        "failed",
        "timed_out",
        "cancelled",
    }
)


def list_expired_status_exclusion_sql() -> str:
    """Comma-separated SQL string literals for NOT IN (...)."""
    return ", ".join(f"'{status}'" for status in sorted(TERMINAL_INCIDENT_STATUSES))
