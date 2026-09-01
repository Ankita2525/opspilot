from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from simulator.scenarios import (
    AUTH_SERVICE,
    AUTH_TOKEN_VALIDATION_REGRESSION_ID,
    CHECKOUT_API,
    CHECKOUT_DB_POOL_REGRESSION_ID,
    PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID,
    PAYMENTS_SERVICE,
)

CHECKOUT_DB_POOL_REGRESSION = CHECKOUT_DB_POOL_REGRESSION_ID
AUTH_TOKEN_VALIDATION_REGRESSION = AUTH_TOKEN_VALIDATION_REGRESSION_ID
PAYMENTS_PROVIDER_TIMEOUT_REGRESSION = PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID


@dataclass(frozen=True)
class LiveScenarioMapping:
    scenario_id: str
    affected_service: str
    healthy_revision: str
    faulty_revision: str
    service_base_url_env: str
    default_base_url: str


LIVE_SCENARIO_MAPPINGS: dict[str, LiveScenarioMapping] = {
    CHECKOUT_DB_POOL_REGRESSION_ID: LiveScenarioMapping(
        scenario_id=CHECKOUT_DB_POOL_REGRESSION_ID,
        affected_service=CHECKOUT_API,
        healthy_revision="v1.18.2",
        faulty_revision="v1.18.3",
        service_base_url_env="CHECKOUT_API_URL",
        default_base_url="http://localhost:8081",
    ),
    AUTH_TOKEN_VALIDATION_REGRESSION_ID: LiveScenarioMapping(
        scenario_id=AUTH_TOKEN_VALIDATION_REGRESSION_ID,
        affected_service=AUTH_SERVICE,
        healthy_revision="v2.7.0",
        faulty_revision="v2.7.1",
        service_base_url_env="AUTH_SERVICE_URL",
        default_base_url="http://localhost:8082",
    ),
    PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID: LiveScenarioMapping(
        scenario_id=PAYMENTS_PROVIDER_TIMEOUT_REGRESSION_ID,
        affected_service=PAYMENTS_SERVICE,
        healthy_revision="v3.4.1",
        faulty_revision="v3.4.2",
        service_base_url_env="PAYMENTS_SERVICE_URL",
        default_base_url="http://localhost:8083",
    ),
}


def get_live_scenario_mapping(scenario_id: str) -> LiveScenarioMapping:
    try:
        return LIVE_SCENARIO_MAPPINGS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"Unknown live scenario: {scenario_id}") from exc


def parse_deployment_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
