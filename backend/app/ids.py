from uuid import uuid4

_INCIDENT_ID_PREFIX = "inc_"


def new_incident_id() -> str:
    """Return a unique execution ID. Not derived from scenario_id."""
    return f"{_INCIDENT_ID_PREFIX}{uuid4().hex}"
