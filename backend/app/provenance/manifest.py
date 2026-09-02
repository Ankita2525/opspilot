from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from backend.app.provenance.models import LiveRunProvenance


def canonical_manifest_bytes(provenance: LiveRunProvenance) -> bytes:
    """Serialize provenance to a stable canonical form for hashing."""
    payload = provenance.model_dump(mode="json", exclude={"evidence_manifest_hash"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_manifest_hash(provenance: LiveRunProvenance) -> str:
    digest = hashlib.sha256(canonical_manifest_bytes(provenance)).hexdigest()
    return digest


def with_manifest_hash(provenance: LiveRunProvenance) -> LiveRunProvenance:
    return provenance.model_copy(
        update={"evidence_manifest_hash": evidence_manifest_hash(provenance)}
    )


def parse_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None
