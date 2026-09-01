from collections.abc import Mapping
from datetime import datetime, timezone
from json import dumps, loads

import psycopg
from psycopg.rows import dict_row

from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    EvaluationRecord,
    IncidentRecord,
)

SAVE_INCIDENT_SQL = """
INSERT INTO incidents (
    incident_id,
    scenario_id,
    affected_service,
    status,
    created_at,
    updated_at,
    recommended_action,
    selected_skills,
    resolved,
    session_id,
    expires_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
ON CONFLICT (incident_id) DO UPDATE SET
    scenario_id = EXCLUDED.scenario_id,
    affected_service = EXCLUDED.affected_service,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    updated_at = EXCLUDED.updated_at,
    recommended_action = EXCLUDED.recommended_action,
    selected_skills = EXCLUDED.selected_skills,
    resolved = EXCLUDED.resolved,
    session_id = EXCLUDED.session_id,
    expires_at = EXCLUDED.expires_at
"""

GET_INCIDENT_SQL = """
SELECT
    incident_id,
    scenario_id,
    affected_service,
    status,
    created_at,
    updated_at,
    recommended_action,
    selected_skills,
    resolved,
    session_id,
    expires_at
FROM incidents
WHERE incident_id = %s
"""

LIST_INCIDENTS_SQL = """
SELECT
    incident_id,
    scenario_id,
    affected_service,
    status,
    created_at,
    updated_at,
    recommended_action,
    selected_skills,
    resolved,
    session_id,
    expires_at
FROM incidents
ORDER BY created_at ASC, incident_id ASC
"""

SAVE_APPROVAL_SQL = """
INSERT INTO approvals (
    proposal_id,
    incident_id,
    action,
    service,
    version,
    risk_level,
    status,
    created_at,
    updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (proposal_id) DO UPDATE SET
    incident_id = EXCLUDED.incident_id,
    action = EXCLUDED.action,
    service = EXCLUDED.service,
    version = EXCLUDED.version,
    risk_level = EXCLUDED.risk_level,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    updated_at = EXCLUDED.updated_at
"""

GET_APPROVAL_SQL = """
SELECT
    proposal_id,
    incident_id,
    action,
    service,
    version,
    risk_level,
    status,
    created_at,
    updated_at
FROM approvals
WHERE proposal_id = %s
"""

LIST_APPROVALS_SQL = """
SELECT
    proposal_id,
    incident_id,
    action,
    service,
    version,
    risk_level,
    status,
    created_at,
    updated_at
FROM approvals
WHERE incident_id = %s
ORDER BY created_at ASC, proposal_id ASC
"""

APPEND_AUDIT_SQL = """
INSERT INTO audit_events (
    audit_id,
    incident_id,
    event_type,
    message,
    timestamp,
    metadata
) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
"""

LIST_AUDIT_EVENTS_SQL = """
SELECT
    audit_id,
    incident_id,
    event_type,
    message,
    timestamp,
    metadata
FROM audit_events
WHERE incident_id = %s
ORDER BY timestamp ASC, audit_id ASC
"""

SAVE_EVALUATION_SQL = """
INSERT INTO evaluations (
    evaluation_id,
    incident_id,
    resolution_success,
    root_cause_correct,
    recommended_action_correct,
    unsafe_action_attempted,
    investigation_steps,
    created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (evaluation_id) DO UPDATE SET
    incident_id = EXCLUDED.incident_id,
    resolution_success = EXCLUDED.resolution_success,
    root_cause_correct = EXCLUDED.root_cause_correct,
    recommended_action_correct = EXCLUDED.recommended_action_correct,
    unsafe_action_attempted = EXCLUDED.unsafe_action_attempted,
    investigation_steps = EXCLUDED.investigation_steps,
    created_at = EXCLUDED.created_at
"""

LIST_EVALUATIONS_SQL = """
SELECT
    evaluation_id,
    incident_id,
    resolution_success,
    root_cause_correct,
    recommended_action_correct,
    unsafe_action_attempted,
    investigation_steps,
    created_at
FROM evaluations
WHERE incident_id = %s
ORDER BY created_at ASC, evaluation_id ASC
"""

LIST_EXPIRED_INCIDENTS_SQL = """
SELECT incident_id, session_id
FROM incidents
WHERE expires_at IS NOT NULL
  AND expires_at <= %s
  AND status NOT IN ('resolved', 'rejected', 'remediation_failed', 'blocked_by_telemetry', 'abandoned', 'expired')
ORDER BY expires_at ASC
"""

PARAMETERIZED_SQL = (
    SAVE_INCIDENT_SQL,
    GET_INCIDENT_SQL,
    LIST_INCIDENTS_SQL,
    SAVE_APPROVAL_SQL,
    GET_APPROVAL_SQL,
    LIST_APPROVALS_SQL,
    APPEND_AUDIT_SQL,
    LIST_AUDIT_EVENTS_SQL,
    SAVE_EVALUATION_SQL,
    LIST_EVALUATIONS_SQL,
)


class PostgresOpsPilotRepository:
    """Durable PostgreSQL implementation of OpsPilotRepository."""

    def __init__(self, database_url: str) -> None:
        if not database_url or not database_url.strip():
            raise ValueError("database_url must not be empty")
        self._database_url = database_url

    def save_incident(self, record: IncidentRecord) -> None:
        self._execute(
            SAVE_INCIDENT_SQL,
            (
                record.incident_id,
                record.scenario_id,
                record.affected_service,
                record.status,
                to_utc(record.created_at),
                to_utc(record.updated_at),
                record.recommended_action,
                to_json(list(record.selected_skills)),
                record.resolved,
                record.session_id,
                to_utc(record.expires_at) if record.expires_at else None,
            ),
        )

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        row = self._fetchone(GET_INCIDENT_SQL, (incident_id,))
        if row is None:
            return None
        return incident_from_row(row)

    def list_incidents(self) -> list[IncidentRecord]:
        return [incident_from_row(row) for row in self._fetchall(LIST_INCIDENTS_SQL)]

    def save_approval(self, record: ApprovalRecord) -> None:
        self._execute(
            SAVE_APPROVAL_SQL,
            (
                record.proposal_id,
                record.incident_id,
                record.action,
                record.service,
                record.version,
                record.risk_level,
                record.status,
                to_utc(record.created_at),
                to_utc(record.updated_at),
            ),
        )

    def get_approval(self, proposal_id: str) -> ApprovalRecord | None:
        row = self._fetchone(GET_APPROVAL_SQL, (proposal_id,))
        if row is None:
            return None
        return approval_from_row(row)

    def list_approvals(self, incident_id: str) -> list[ApprovalRecord]:
        return [
            approval_from_row(row)
            for row in self._fetchall(LIST_APPROVALS_SQL, (incident_id,))
        ]

    def append_audit(self, record: AuditRecord) -> None:
        self._execute(
            APPEND_AUDIT_SQL,
            (
                record.audit_id,
                record.incident_id,
                record.event_type,
                record.message,
                to_utc(record.timestamp),
                to_json(dict(record.metadata)),
            ),
        )

    def list_audit_events(self, incident_id: str) -> list[AuditRecord]:
        return [
            audit_from_row(row)
            for row in self._fetchall(LIST_AUDIT_EVENTS_SQL, (incident_id,))
        ]

    def save_evaluation(self, record: EvaluationRecord) -> None:
        self._execute(
            SAVE_EVALUATION_SQL,
            (
                record.evaluation_id,
                record.incident_id,
                record.resolution_success,
                record.root_cause_correct,
                record.recommended_action_correct,
                record.unsafe_action_attempted,
                record.investigation_steps,
                to_utc(record.created_at),
            ),
        )

    def list_evaluations(self, incident_id: str) -> list[EvaluationRecord]:
        return [
            evaluation_from_row(row)
            for row in self._fetchall(LIST_EVALUATIONS_SQL, (incident_id,))
        ]

    def list_expired_incidents(self, as_of: datetime) -> list[tuple[str, str | None]]:
        rows = self._fetchall(LIST_EXPIRED_INCIDENTS_SQL, (to_utc(as_of),))
        return [
            (str(row["incident_id"]), _optional_str(row.get("session_id")))
            for row in rows
        ]

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def _execute(self, sql: str, parameters: tuple[object, ...]) -> None:
        with self._connect() as connection:
            connection.execute(sql, parameters)

    def _fetchone(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> Mapping[str, object] | None:
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchone()

    def _fetchall(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> list[Mapping[str, object]]:
        with self._connect() as connection:
            return list(connection.execute(sql, parameters).fetchall())


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def from_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def to_json(value: object) -> str:
    return dumps(value)


def from_json(value: object) -> object:
    if isinstance(value, str):
        return loads(value)
    return value


def incident_from_row(row: Mapping[str, object]) -> IncidentRecord:
    skills = from_json(row["selected_skills"])
    if not isinstance(skills, list):
        raise TypeError("selected_skills must be a JSON list")
    return IncidentRecord(
        incident_id=str(row["incident_id"]),
        scenario_id=str(row["scenario_id"]),
        affected_service=str(row["affected_service"]),
        status=str(row["status"]),
        created_at=from_utc(_require_datetime(row["created_at"])),
        updated_at=from_utc(_require_datetime(row["updated_at"])),
        recommended_action=_optional_str(row["recommended_action"]),
        selected_skills=[str(item) for item in skills],
        resolved=bool(row["resolved"]),
        session_id=_optional_str(row.get("session_id")),
        expires_at=(
            from_utc(_require_datetime(row["expires_at"]))
            if row.get("expires_at") is not None
            else None
        ),
    )


def approval_from_row(row: Mapping[str, object]) -> ApprovalRecord:
    return ApprovalRecord(
        proposal_id=str(row["proposal_id"]),
        incident_id=str(row["incident_id"]),
        action=str(row["action"]),
        service=str(row["service"]),
        version=_optional_str(row["version"]),
        risk_level=str(row["risk_level"]),
        status=str(row["status"]),
        created_at=from_utc(_require_datetime(row["created_at"])),
        updated_at=from_utc(_require_datetime(row["updated_at"])),
    )


def audit_from_row(row: Mapping[str, object]) -> AuditRecord:
    metadata = from_json(row["metadata"])
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a JSON object")
    return AuditRecord(
        audit_id=str(row["audit_id"]),
        incident_id=str(row["incident_id"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        timestamp=from_utc(_require_datetime(row["timestamp"])),
        metadata=dict(metadata),
    )


def evaluation_from_row(row: Mapping[str, object]) -> EvaluationRecord:
    return EvaluationRecord(
        evaluation_id=str(row["evaluation_id"]),
        incident_id=str(row["incident_id"]),
        resolution_success=bool(row["resolution_success"]),
        root_cause_correct=bool(row["root_cause_correct"]),
        recommended_action_correct=bool(row["recommended_action_correct"]),
        unsafe_action_attempted=bool(row["unsafe_action_attempted"]),
        investigation_steps=int(row["investigation_steps"]),
        created_at=from_utc(_require_datetime(row["created_at"])),
    )


def _require_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected a datetime value")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
