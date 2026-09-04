from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredGenerationMeta:
    """Safe metadata about the last structured generation attempt."""

    provider: str
    primary_model: str
    final_model: str
    fallback_used: bool = False
    fallback_model: str | None = None
    fallback_reason: str | None = None

    def as_diagnosis_fields(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.final_model,
            "primary_model_attempted": self.primary_model,
            "fallback_used": self.fallback_used,
            "fallback_model": self.fallback_model,
            "fallback_reason": self.fallback_reason,
            "final_model": self.final_model,
        }


def generation_meta_from_provider(provider: object) -> StructuredGenerationMeta | None:
    """Unwrap budget guards and read last generation metadata when present."""
    current: object | None = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        meta = getattr(current, "last_generation_meta", None)
        if isinstance(meta, StructuredGenerationMeta):
            return meta
        current = getattr(current, "_inner", None)
    return None
