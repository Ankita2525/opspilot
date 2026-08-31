from backend.app.context.models import EvidenceType, IncidentContext

DEPLOYMENT_SKILL = "deployment-regression"
POSTGRES_SKILL = "postgres-diagnostics"
AUTH_SKILL = "authentication-failure"
EXTERNAL_SKILL = "external-api-failure"
MAX_SELECTED_SKILLS = 2

_POSTGRES_KEYWORDS = ("database", "connection pool", "postgres", "sql")
_AUTH_KEYWORDS = ("auth", "token", "signature", "401", "403")
_EXTERNAL_KEYWORDS = ("upstream", "provider", "deadline exceeded", "external")
_SPECIALIZED_SKILLS = (
    (POSTGRES_SKILL, _POSTGRES_KEYWORDS),
    (AUTH_SKILL, _AUTH_KEYWORDS),
    (EXTERNAL_SKILL, _EXTERNAL_KEYWORDS),
)


class SkillSelector:
    """Deterministic skill choice from bounded incident evidence."""

    def select(self, context: IncidentContext) -> list[str]:
        haystack = _haystack(context)
        selected: list[str] = []
        if _has_deployment_evidence(context):
            selected.append(DEPLOYMENT_SKILL)
        for skill_name, keywords in _SPECIALIZED_SKILLS:
            if len(selected) >= MAX_SELECTED_SKILLS:
                break
            if _contains_any(haystack, keywords):
                selected.append(skill_name)
        return selected


def _has_deployment_evidence(context: IncidentContext) -> bool:
    items = (*context.evidence, *context.recent_changes)
    return any(item.evidence_type == EvidenceType.DEPLOYMENT for item in items)


def _haystack(context: IncidentContext) -> str:
    parts = [context.affected_service]
    for item in (*context.evidence, *context.recent_changes):
        parts.append(item.summary)
    return " ".join(parts).lower()


def _contains_any(haystack: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in haystack for keyword in keywords)
