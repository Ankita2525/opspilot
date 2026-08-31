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
    ServiceHealthThresholds,
)

CHECKOUT_API = "checkout-api"
AUTH_SERVICE = "auth-service"
PAYMENTS_SERVICE = "payments-service"

CHECKOUT_DB_POOL_REGRESSION_ID = "checkout-db-pool-regression"
AUTH_TOKEN_VALIDATION_REGRESSION_ID = "auth-token-validation-regression"
PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID = "payments-provider-timeout-regression"

# Fixed incident timeline so every run is identical.
_DAY = datetime(2026, 8, 30)


def _checkout_db_pool_regression() -> IncidentScenario:
    incident_start = _DAY.replace(hour=14, minute=3)
    return IncidentScenario(
        id=CHECKOUT_DB_POOL_REGRESSION_ID,
        title="Checkout API latency after deployment",
        affected_service=CHECKOUT_API,
        incident_start=incident_start,
        known_root_cause=RootCause.DB_CONNECTION_POOL_REGRESSION,
        expected_remediation=Remediation.ROLLBACK_DEPLOYMENT,
        incident_metrics=MetricSnapshot(
            service=CHECKOUT_API,
            p95_latency_ms=1940,
            error_rate_percent=8.2,
            timestamp=incident_start,
        ),
        recovered_metrics=MetricSnapshot(
            service=CHECKOUT_API,
            p95_latency_ms=218,
            error_rate_percent=0.3,
            timestamp=_DAY.replace(hour=14, minute=12),
        ),
        health_thresholds=ServiceHealthThresholds(
            max_p95_latency_ms=400,
            max_error_rate_percent=1.0,
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
                timestamp=_DAY.replace(hour=13, minute=58),
            ),
        ],
    )


def _auth_token_validation_regression() -> IncidentScenario:
    incident_start = _DAY.replace(hour=9, minute=48)
    return IncidentScenario(
        id=AUTH_TOKEN_VALIDATION_REGRESSION_ID,
        title="Authentication failures after deployment",
        affected_service=AUTH_SERVICE,
        incident_start=incident_start,
        known_root_cause=RootCause.AUTH_TOKEN_VALIDATION_REGRESSION,
        expected_remediation=Remediation.ROLLBACK_DEPLOYMENT,
        incident_metrics=MetricSnapshot(
            service=AUTH_SERVICE,
            p95_latency_ms=870,
            error_rate_percent=14.6,
            timestamp=incident_start,
        ),
        recovered_metrics=MetricSnapshot(
            service=AUTH_SERVICE,
            p95_latency_ms=165,
            error_rate_percent=0.4,
            timestamp=_DAY.replace(hour=9, minute=57),
        ),
        health_thresholds=ServiceHealthThresholds(
            max_p95_latency_ms=350,
            max_error_rate_percent=1.0,
        ),
        logs=[
            LogEvent(
                service=AUTH_SERVICE,
                timestamp=_DAY.replace(hour=9, minute=48, second=6),
                level=LogLevel.ERROR,
                message=(
                    "TokenValidationError: JWT signature verification failed "
                    "for kid=auth-signing-2026-08"
                ),
            ),
            LogEvent(
                service=AUTH_SERVICE,
                timestamp=_DAY.replace(hour=9, minute=48, second=11),
                level=LogLevel.ERROR,
                message="POST /oauth/token returned 401 Unauthorized for client_id=checkout-web",
            ),
            LogEvent(
                service=AUTH_SERVICE,
                timestamp=_DAY.replace(hour=9, minute=48, second=19),
                level=LogLevel.ERROR,
                message=(
                    "Rejecting request: access token failed validation "
                    "(invalid signature)"
                ),
            ),
            LogEvent(
                service=AUTH_SERVICE,
                timestamp=_DAY.replace(hour=9, minute=49, second=2),
                level=LogLevel.WARN,
                message=(
                    "Authentication failure rate elevated after 09:48 "
                    "following deployment v2.7.1"
                ),
            ),
        ],
        deployments=[
            DeploymentEvent(
                service=AUTH_SERVICE,
                version="v2.7.1",
                timestamp=_DAY.replace(hour=9, minute=42),
            ),
        ],
    )


def _payments_provider_timeout_regression() -> IncidentScenario:
    incident_start = _DAY.replace(hour=16, minute=27)
    return IncidentScenario(
        id=PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID,
        title="Payment requests timing out after deployment",
        affected_service=PAYMENTS_SERVICE,
        incident_start=incident_start,
        known_root_cause=RootCause.PAYMENT_PROVIDER_TIMEOUT_REGRESSION,
        expected_remediation=Remediation.ROLLBACK_DEPLOYMENT,
        incident_metrics=MetricSnapshot(
            service=PAYMENTS_SERVICE,
            p95_latency_ms=2680,
            error_rate_percent=11.1,
            timestamp=incident_start,
        ),
        recovered_metrics=MetricSnapshot(
            service=PAYMENTS_SERVICE,
            p95_latency_ms=295,
            error_rate_percent=0.6,
            timestamp=_DAY.replace(hour=16, minute=36),
        ),
        health_thresholds=ServiceHealthThresholds(
            max_p95_latency_ms=500,
            max_error_rate_percent=1.0,
        ),
        logs=[
            LogEvent(
                service=PAYMENTS_SERVICE,
                timestamp=_DAY.replace(hour=16, minute=27, second=5),
                level=LogLevel.ERROR,
                message=(
                    "UpstreamTimeout: payment provider did not respond within 8000ms"
                ),
            ),
            LogEvent(
                service=PAYMENTS_SERVICE,
                timestamp=_DAY.replace(hour=16, minute=27, second=9),
                level=LogLevel.ERROR,
                message="Request deadline exceeded for POST /v1/charges",
            ),
            LogEvent(
                service=PAYMENTS_SERVICE,
                timestamp=_DAY.replace(hour=16, minute=27, second=14),
                level=LogLevel.WARN,
                message="Provider call duration p95 exceeded 7000ms for processor=card-network",
            ),
            LogEvent(
                service=PAYMENTS_SERVICE,
                timestamp=_DAY.replace(hour=16, minute=28, second=3),
                level=LogLevel.ERROR,
                message=(
                    "Payment capture failed after deployment of v3.4.2: "
                    "context deadline exceeded"
                ),
            ),
        ],
        deployments=[
            DeploymentEvent(
                service=PAYMENTS_SERVICE,
                version="v3.4.2",
                timestamp=_DAY.replace(hour=16, minute=21),
            ),
        ],
    )


def _scenario_registry() -> dict[str, IncidentScenario]:
    scenarios = [
        _checkout_db_pool_regression(),
        _auth_token_validation_regression(),
        _payments_provider_timeout_regression(),
    ]
    return {scenario.id: scenario for scenario in scenarios}


SCENARIOS = _scenario_registry()


def list_scenarios() -> list[IncidentScenario]:
    return list(_scenario_registry().values())


def get_scenario(scenario_id: str) -> IncidentScenario:
    try:
        return _scenario_registry()[scenario_id]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario: {scenario_id}") from exc
