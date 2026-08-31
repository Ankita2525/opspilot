from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

DEFAULT_MAX_CHARS = 2000
REDACTED = "[REDACTED]"

_CONTROL_CHARS = dict.fromkeys(range(32))
del _CONTROL_CHARS[ord("\t")]
del _CONTROL_CHARS[ord("\n")]
_CONTROL_CHARS[127] = None

_PATHOLOGICAL_NEWLINES = re.compile(r"\n{3,}")
_PATHOLOGICAL_SPACES = re.compile(r"[^\S\n]{8,}")

_BEARER_TOKEN = re.compile(
    r"(?i)(\b(?:authorization:\s*)?bearer\s+)([A-Za-z0-9._\-+=/]{8,})"
)
_GROQ_API_KEY = re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9_-]{8,}")
_OPENAI_PROJECT_KEY = re.compile(r"(?<![A-Za-z0-9])sk-proj-[A-Za-z0-9_-]{8,}")
_OPENAI_API_KEY = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD))\s*[:=]\s*"
    r"(?:(['\"])([^'\"\s]+)\2|(\S+))"
)

INTERNAL_LEAK_TOKENS = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "GROQ_API_KEY",
    "DATABASE_URL",
    "system_prompt",
    "user_prompt",
    "Traceback",
    "You are OpsPilot",
)

_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore previous instruction",
    "system prompt",
    "developer message",
    "reveal hidden prompt",
    "reveal your system prompt",
    "reveal the system prompt",
    "execute this shell",
    "execute this command",
    "call rollback_deployment immediately",
    "bypass approval",
    "bypass human approval",
    "override safety policy",
    "ignore safety policy",
)


def normalize_untrusted_text(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return a bounded copy of operational text with unsafe controls removed."""

    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.translate(_CONTROL_CHARS)
    normalized = _PATHOLOGICAL_NEWLINES.sub("\n\n", normalized)
    normalized = _PATHOLOGICAL_SPACES.sub(" ", normalized)
    return _bound(normalized, max_chars)


def redact_secrets(text: str) -> str:
    """Redact obvious secret values from operational text. Not a complete DLP system."""

    redacted = _BEARER_TOKEN.sub(rf"\1{REDACTED}", text)
    redacted = _GROQ_API_KEY.sub(REDACTED, redacted)
    redacted = _OPENAI_PROJECT_KEY.sub(REDACTED, redacted)
    redacted = _OPENAI_API_KEY.sub(REDACTED, redacted)
    return _SECRET_ASSIGNMENT.sub(_replace_secret_assignment, redacted)


def detect_prompt_injection(text: str) -> bool:
    """Return True when text contains obvious instruction-style injection phrases."""

    compressed = re.sub(r"\s+", " ", text).strip().lower()
    return any(phrase in compressed for phrase in _INJECTION_PHRASES)


def prepare_untrusted_text(
    text: str, *, max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[str, bool]:
    """Normalize and redact untrusted text; report injection-style content as metadata."""

    normalized = normalize_untrusted_text(text, max_chars=max(max_chars, len(text) or 1))
    suspicious = detect_prompt_injection(normalized)
    return _bound(redact_secrets(normalized), max_chars), suspicious


def sanitize_public_text(text: str) -> str:
    """Redact secrets and internal prompt material from a public-facing string."""

    redacted = redact_secrets(text)
    if any(token in redacted for token in INTERNAL_LEAK_TOKENS):
        return REDACTED
    return redacted


def sanitize_public_value(value: Any) -> Any:
    """Recursively sanitize public JSON-like values without changing types of non-strings."""

    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, list):
        return [sanitize_public_value(item) for item in value]
    if isinstance(value, tuple):
        return type(value)(sanitize_public_value(item) for item in value)
    if isinstance(value, dict):
        return {key: sanitize_public_value(item) for key, item in value.items()}
    return value


def sanitize_public_instance[T: BaseModel](model: T) -> T:
    """Return a sanitized copy of a Pydantic model for public serialization."""

    dumped = model.model_dump(mode="json")
    return type(model).model_validate(sanitize_public_value(dumped))


def redact_json_value(value: Any) -> Any:
    return sanitize_public_value(value)


def _replace_secret_assignment(match: re.Match[str]) -> str:
    name = match.group(1)
    quote = match.group(2)
    if quote:
        return f"{name}={quote}{REDACTED}{quote}"
    return f"{name}={REDACTED}"


def _bound(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]
