#!/usr/bin/env python3
"""Isolated Groq structured-output contract probe (manual / pre-deploy).

Does NOT touch sandbox, leases, incidents, Prometheus, Loki, or Postgres.
Uses GROQ_API_KEY from the environment. Never prints the key or prompts.

Exit 0 on success, non-zero on failure.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.models.groq_provider import DEFAULT_GROQ_MODEL, GroqModelProvider


def main() -> int:
    if not os.environ.get("GROQ_API_KEY"):
        print("SKIP: GROQ_API_KEY not set (CI should use mocked contract tests)")
        return 0
    provider = GroqModelProvider(model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL))
    system = "Return only the HypothesisResult JSON object."
    user = (
        "SYNTHETIC NON-SENSITIVE PROBE. service=probe-service. "
        "p95_latency_ms=100. error_rate_percent=0. "
        "recommended_action must be no_supported_action."
    )
    try:
        result = provider.generate_structured(system, user, HypothesisResult)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "exception_class": type(exc).__name__,
                    "category": getattr(getattr(exc, "meta", None), "category", None),
                },
                default=str,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "model": provider.model,
                "recommended_action": result.recommended_action.value,
                "hypothesis_count": len(result.hypotheses),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
