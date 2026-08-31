from __future__ import annotations

from datetime import datetime

from simulator.models import (
    DeploymentEvent,
    IncidentScenario,
    LogEvent,
    LogLevel,
    MetricSnapshot,
    Remediation,
    RootCause,
)

CHECKOUT_API = "checkout-api"
CHECKOUT_DB_POOL_REGRESSION_ID = "checkout-db-pool-regression"

# Fixed incident timeline so every run is identical.
_DAY = datetime(2026, 8, 30)
DEPLOYMENT_AT = _DAY.replace(hour=13, minute=58)
INCIDENT_START = _DAY.replace(hour=14, minute=3)
RECOVERED_AT = _DAY.replace(hour=14, minute=12)


def _checkout_db_pool_regression() -> IncidentScenario:
    return IncidentScenario(
        id=CHECKOUT_DB_POOL_REGRESSION_ID,
        title="Checkout API latency after deployment",
        affected_service=CHECKOUT_API,
        incident_start=INCIDENT_START,
        known_root_cause=RootCause.DB_CONNECTION_POOL_REGRESSION,
        expected_remediation=Remediation.ROLLBACK_DEPLOYMENT,
        incident_metrics=MetricSnapshot(
            service=CHECKOUT_API,
            p95_latency_ms=1940,
            error_rate_percent=8.2,
            timestamp=INCIDENT_START,
        ),
        recovered_metrics=MetricSnapshot(
            service=CHECKOUT_API,
            p95_latency_ms=218,
            error_rate_percent=0.3,
            timestamp=RECOVERED_AT,
        ),
        logs=[
            LogEvent(
                service=CHECKOUT_API,
                timestamp=_DAY.replace(hour=14, minute=3, second=4),
                level=LogLevel.ERROR,
                message=(
                    "TimeoutError: database connection pool timeout after 5000ms "
                    "waiting for a free connection"
                ),
            ),
            LogEvent(
                service=CHECKOUT_API,
                timestamp=_DAY.replace(hour=14, minute=3, second=8),
                level=LogLevel.ERROR,
                message="Request timeout: POST /api/v1/checkout exceeded 30000ms deadline",
            ),
            LogEvent(
                service=CHECKOUT_API,
                timestamp=_DAY.replace(hour=14, minute=3, second=11),
                level=LogLevel.ERROR,
                message=(
                    "Checkout failed for order_id=ord_918273: "
                    "could not complete payment reservation"
                ),
            ),
            LogEvent(
                service=CHECKOUT_API,
                timestamp=_DAY.replace(hour=14, minute=4, second=2),
                level=LogLevel.WARN,
                message="Connection pool exhausted (active=20, idle=0, max=20)",
            ),
        ],
        deployments=[
            DeploymentEvent(
                service=CHECKOUT_API,
                version="v1.18.3",
                timestamp=DEPLOYMENT_AT,
            ),
        ],
    )


_SCENARIO_BUILDERS = {
    CHECKOUT_DB_POOL_REGRESSION_ID: _checkout_db_pool_regression,
}


def get_scenario(scenario_id: str) -> IncidentScenario:
    try:
        builder = _SCENARIO_BUILDERS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario: {scenario_id}") from exc
    return builder()
