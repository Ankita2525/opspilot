from backend.app.safety.models import RiskLevel

_ACTION_RISK = {
    "query_metrics": RiskLevel.READ,
    "get_service_logs": RiskLevel.READ,
    "get_recent_deployments": RiskLevel.READ,
    "get_service_health": RiskLevel.READ,
    "rollback_deployment": RiskLevel.HIGH_RISK,
}


class ActionPolicy:
    """Deterministic risk classification for known OpsPilot actions."""

    def classify(self, action: str) -> RiskLevel:
        try:
            return _ACTION_RISK[action]
        except KeyError as exc:
            raise ValueError(f"Unknown action: {action}") from exc
