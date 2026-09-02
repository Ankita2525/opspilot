import psycopg

CREATE_INCIDENTS_TABLE = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    affected_service TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    recommended_action TEXT NULL,
    selected_skills JSONB NOT NULL,
    resolved BOOLEAN NOT NULL
)
"""

CREATE_APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS approvals (
    proposal_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    action TEXT NOT NULL,
    service TEXT NOT NULL,
    version TEXT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

CREATE_AUDIT_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL
)
"""

CREATE_EVALUATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    resolution_success BOOLEAN NOT NULL,
    root_cause_correct BOOLEAN NOT NULL,
    recommended_action_correct BOOLEAN NOT NULL,
    unsafe_action_attempted BOOLEAN NOT NULL,
    investigation_steps INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""

CREATE_APPROVALS_INCIDENT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_approvals_incident_id
ON approvals (incident_id)
"""

CREATE_AUDIT_EVENTS_INCIDENT_TIMESTAMP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_audit_events_incident_id_timestamp
ON audit_events (incident_id, timestamp)
"""

CREATE_EVALUATIONS_INCIDENT_CREATED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_evaluations_incident_id_created_at
ON evaluations (incident_id, created_at)
"""

CREATE_SANDBOX_LEASE_HOLDER_TABLE = """
CREATE TABLE IF NOT EXISTS sandbox_lease_holder (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    lease_id TEXT,
    session_id TEXT,
    incident_id TEXT,
    acquired_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    renewed_at TIMESTAMPTZ,
    state TEXT NOT NULL DEFAULT 'idle'
)
"""

SEED_SANDBOX_LEASE_HOLDER = """
INSERT INTO sandbox_lease_holder (id, state)
VALUES (1, 'idle')
ON CONFLICT (id) DO NOTHING
"""

CREATE_DEMO_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS demo_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    live_incident_count INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_QUOTA_COUNTERS_TABLE = """
CREATE TABLE IF NOT EXISTS quota_counters (
    counter_key TEXT NOT NULL,
    counter_date DATE NOT NULL,
    counter_value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (counter_key, counter_date)
)
"""

ALTER_INCIDENTS_ADD_SESSION_ID = """
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS session_id TEXT
"""

ALTER_INCIDENTS_ADD_EXPIRES_AT = """
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
"""

CREATE_INCIDENTS_EXPIRES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_incidents_expires_at
ON incidents (expires_at)
WHERE expires_at IS NOT NULL
"""

CREATE_INCIDENT_PROVENANCE_TABLE = """
CREATE TABLE IF NOT EXISTS incident_provenance (
    incident_id TEXT PRIMARY KEY,
    manifest JSONB NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

SCHEMA_STATEMENTS = (
    CREATE_INCIDENTS_TABLE,
    CREATE_APPROVALS_TABLE,
    CREATE_AUDIT_EVENTS_TABLE,
    CREATE_EVALUATIONS_TABLE,
    CREATE_APPROVALS_INCIDENT_INDEX,
    CREATE_AUDIT_EVENTS_INCIDENT_TIMESTAMP_INDEX,
    CREATE_EVALUATIONS_INCIDENT_CREATED_INDEX,
    CREATE_SANDBOX_LEASE_HOLDER_TABLE,
    SEED_SANDBOX_LEASE_HOLDER,
    CREATE_DEMO_SESSIONS_TABLE,
    CREATE_QUOTA_COUNTERS_TABLE,
    ALTER_INCIDENTS_ADD_SESSION_ID,
    ALTER_INCIDENTS_ADD_EXPIRES_AT,
    CREATE_INCIDENTS_EXPIRES_INDEX,
    CREATE_INCIDENT_PROVENANCE_TABLE,
)


def initialize_schema(database_url: str) -> None:
    """Create version-1 tables and indexes if they do not already exist."""
    with psycopg.connect(database_url) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
