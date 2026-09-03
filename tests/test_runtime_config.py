from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app.api.app import create_app
from backend.app.agent.remediation_workflow import RemediationApprovalWorkflow
from backend.app.config import ConfigurationError, OpsPilotSettings
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.runtime import (
    ProductionCheckpointerError,
    RuntimeResources,
    build_runtime_from_settings,
)
from backend.app.safety.approvals import ApprovalService
from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.remediation import RemediationTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

FORBIDDEN_VALUES = (
    "super-secret",
    "postgresql://opspilot:super-secret",
    "sk-live",
)


def _assert_no_secret_values(message: str) -> None:
    lowered = message.lower()
    for token in FORBIDDEN_VALUES:
        assert token.lower() not in lowered


def test_groq_provider_requires_api_key() -> None:
    with pytest.raises(ConfigurationError) as exc:
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "groq",
                "OPSPILOT_ENV": "development",
            }
        )
    _assert_no_secret_values(str(exc.value))


def test_deterministic_provider_does_not_require_groq_key() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_ENV": "development",
        }
    )
    assert settings.model_provider.value == "deterministic"
    provider = settings.create_provider()
    assert provider.__class__.__name__ == "DeterministicModelProvider"


def test_production_requires_database_url() -> None:
    with pytest.raises(ConfigurationError) as exc:
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "deterministic",
                "OPSPILOT_ENV": "production",
            }
        )
    _assert_no_secret_values(str(exc.value))


def test_configuration_errors_never_include_secret_values() -> None:
    secret_db = "postgresql://opspilot:super-secret@db:5432/opspilot"
    with pytest.raises(ConfigurationError) as exc:
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "groq",
                "OPSPILOT_ENV": "production",
                "DATABASE_URL": secret_db,
            }
        )
    message = str(exc.value)
    _assert_no_secret_values(message)
    assert secret_db not in message
    assert "super-secret" not in message


def test_cors_origins_parse_from_comma_separated_env() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_CORS_ORIGINS": "http://localhost:3000, https://demo.example.com ",
        }
    )
    assert settings.cors_origins == (
        "http://localhost:3000",
        "https://demo.example.com",
    )


def test_injected_provider_overrides_production_construction() -> None:
    provider = FakeModelProvider()
    app = create_app(
        provider=provider,
        settings=OpsPilotSettings.from_env(
            {
                "OPSPILOT_MODEL_PROVIDER": "deterministic",
                "OPSPILOT_ENV": "development",
            }
        ),
    )
    assert app.state.provider is provider


def test_injected_repository_and_checkpointer_continue_working() -> None:
    repository = InMemoryOpsPilotRepository()
    checkpointer = InMemorySaver()
    app = create_app(
        provider=FakeModelProvider(),
        repository=repository,
        checkpointer=checkpointer,
    )
    assert app.state.repository is repository
    assert app.state.checkpointer is checkpointer


def test_health_remains_ok_for_injected_app() -> None:
    client = TestClient(create_app(provider=FakeModelProvider()))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "opspilot"}


def test_healthz_is_process_local() -> None:
    client = TestClient(create_app(provider=FakeModelProvider()))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_response_contains_no_credentials() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_ENV": "development",
        }
    )
    client = TestClient(create_app(settings=settings))
    response = client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["degraded"] is False
    assert payload["model_provider"] == "deterministic"
    assert payload["database"] == "in_memory"
    assert "checks" in payload
    assert payload["checks"]["database"]["ok"] is True
    blob = json.dumps(payload)
    _assert_no_secret_values(blob)


def test_production_settings_report_configured_provider_safely() -> None:
    settings = OpsPilotSettings(
        environment="production",
        model_provider="groq",
        groq_api_key="super-secret",
        database_url="postgresql://opspilot:localdev@postgres:5432/opspilot",
        cors_origins=("http://localhost:3000",),
    )
    summary = settings.safe_summary()
    assert summary["model_provider"] == "groq"
    assert summary["environment"] == "production"
    assert summary["database"] == "configured"
    _assert_no_secret_values(json.dumps(summary))


def test_settings_repr_hides_secret_fields() -> None:
    settings = OpsPilotSettings(
        environment="development",
        model_provider="groq",
        groq_api_key="super-secret",
        database_url="postgresql://user:pass@localhost:5432/opspilot",
    )
    rendered = repr(settings)
    assert "super-secret" not in rendered
    assert "postgresql://" not in rendered


def test_missing_model_provider_is_rejected_for_runtime_path() -> None:
    with pytest.raises(ConfigurationError, match="OPSPILOT_MODEL_PROVIDER is required"):
        OpsPilotSettings.from_env(
            {
                "OPSPILOT_ENV": "development",
            }
        )


def test_deterministic_provider_is_used_only_when_explicitly_selected() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "deterministic",
            "OPSPILOT_ENV": "development",
        }
    )
    assert settings.model_provider.value == "deterministic"
    assert settings.create_provider().__class__.__name__ == "DeterministicModelProvider"

    groq_settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "groq",
            "OPSPILOT_ENV": "development",
            "GROQ_API_KEY": "test-key",
        }
    )
    assert groq_settings.model_provider.value == "groq"
    assert groq_settings.create_provider().__class__.__name__ == "GroqModelProvider"


def test_production_rejects_missing_postgres_checkpointer() -> None:
    settings = OpsPilotSettings(
        environment="production",
        model_provider="deterministic",
        database_url="postgresql://opspilot:localdev@postgres:5432/opspilot",
    )
    runtime = build_runtime_from_settings(settings)
    assert runtime.requires_postgres_checkpointer is True

    with pytest.raises(ProductionCheckpointerError, match="Postgres checkpointer"):
        runtime.ensure_production_checkpointer_configured()


def test_production_workflow_rejects_in_memory_fallback() -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario("checkout-db-pool-regression")
    approvals = ApprovalService()

    with pytest.raises(ProductionCheckpointerError, match="Postgres checkpointer"):
        RemediationApprovalWorkflow(
            remediation_tools=RemediationTools(environment, approvals),
            approvals=approvals,
            diagnostic_tools=DiagnosticTools(environment),
            checkpointer=None,
            allow_in_memory_checkpointer=False,
        )


def test_injected_development_workflow_still_uses_in_memory_checkpointer() -> None:
    environment = SimulatedEnvironment()
    environment.load_scenario("checkout-db-pool-regression")
    approvals = ApprovalService()
    workflow = RemediationApprovalWorkflow(
        remediation_tools=RemediationTools(environment, approvals),
        approvals=approvals,
        diagnostic_tools=DiagnosticTools(environment),
    )

    assert workflow._checkpointer.__class__.__name__ == "InMemorySaver"


def test_injected_app_runtime_allows_in_memory_checkpointer() -> None:
    runtime = RuntimeResources(
        provider=FakeModelProvider(),
        repository=InMemoryOpsPilotRepository(),
        checkpointer=None,
        settings=None,
    )
    assert runtime.allow_in_memory_checkpointer is True
    runtime.ensure_production_checkpointer_configured()
