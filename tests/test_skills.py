from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.hypotheses import HypothesisEngine
from backend.app.agent.workflow import InvestigationWorkflow
from backend.app.api.app import create_app
from backend.app.context.manager import ContextManager
from backend.app.context.models import EvidenceItem, EvidenceType, IncidentContext
from backend.app.skills.loader import SkillLoader
from backend.app.skills.models import Skill
from backend.app.skills.selector import (
    AUTH_SKILL,
    DEPLOYMENT_SKILL,
    EXTERNAL_SKILL,
    MAX_SELECTED_SKILLS,
    POSTGRES_SKILL,
    SkillSelector,
)
from backend.app.tools.diagnostics import DiagnosticTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

CHECKOUT_ID = "checkout-db-pool-regression"
CHECKOUT_SERVICE = "checkout-api"
AUTH_ID = "auth-token-validation-regression"
AUTH_SERVICE = "auth-service"
PAYMENTS_ID = "payments-provider-timeout-regression"
PAYMENTS_SERVICE = "payments-service"
FORBIDDEN = ("known_root_cause", "expected_remediation")
SKILL_NAMES = (
    DEPLOYMENT_SKILL,
    POSTGRES_SKILL,
    AUTH_SKILL,
    EXTERNAL_SKILL,
)
DEPLOYMENT_TITLE = "Skill: Deployment Regression"
POSTGRES_TITLE = "Skill: PostgreSQL Diagnostics"
AUTH_TITLE = "Skill: Authentication Failure"
EXTERNAL_TITLE = "Skill: External API Failure"
POSTGRES_GUIDANCE = (
    "Distinguish pool exhaustion from general database unavailability"
)
AUTH_GUIDANCE = "Never disable authentication"
EXTERNAL_GUIDANCE = "Do not silently disable critical provider calls"


def _loader() -> SkillLoader:
    return SkillLoader()


def _tools(scenario_id: str) -> tuple[SimulatedEnvironment, DiagnosticTools]:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    return environment, DiagnosticTools(environment)


def _context(scenario_id: str, service: str, incident_id: str) -> IncidentContext:
    _, tools = _tools(scenario_id)
    return ContextManager().build(
        incident_id=incident_id,
        affected_service=service,
        metrics=tools.query_metrics(service),
        deployments=tools.get_recent_deployments(service),
        logs=tools.get_service_logs(service),
    )


def _checkout_context() -> IncidentContext:
    return _context(CHECKOUT_ID, CHECKOUT_SERVICE, "inc-checkout-001")


def _auth_context() -> IncidentContext:
    return _context(AUTH_ID, AUTH_SERVICE, "inc-auth-001")


def _payments_context() -> IncidentContext:
    return _context(PAYMENTS_ID, PAYMENTS_SERVICE, "inc-payments-001")


def _analyze(scenario_id: str, service: str, incident_id: str):
    environment, tools = _tools(scenario_id)
    provider = FakeModelProvider()
    engine = HypothesisEngine(provider)
    context = engine.build_context(
        incident_id=incident_id,
        affected_service=service,
        metrics=tools.query_metrics(service),
        deployments=tools.get_recent_deployments(service),
        logs=tools.get_service_logs(service),
    )
    result = engine.analyze_context(context)
    return environment, provider, engine, context, result


def _kitchen_sink_context() -> IncidentContext:
    timestamp = datetime(2026, 8, 30, 14, 3)
    return IncidentContext(
        incident_id="inc-all-signals",
        affected_service=AUTH_SERVICE,
        symptom_summary="multiple overlapping failure signals",
        evidence=[
            EvidenceItem(
                evidence_id="metric-all",
                evidence_type=EvidenceType.METRIC,
                source=AUTH_SERVICE,
                summary="p95 latency is 900 ms and error rate is 12%.",
                relevance_score=0.9,
                timestamp=timestamp,
            ),
            EvidenceItem(
                evidence_id="deployment-all",
                evidence_type=EvidenceType.DEPLOYMENT,
                source=AUTH_SERVICE,
                summary="Deployment v9.9.9 occurred at 14:00.",
                relevance_score=0.85,
                timestamp=timestamp,
            ),
            EvidenceItem(
                evidence_id="log-db",
                evidence_type=EvidenceType.LOG,
                source=AUTH_SERVICE,
                summary="ERROR: database connection pool postgres SQL timeout",
                relevance_score=0.8,
                timestamp=timestamp,
            ),
            EvidenceItem(
                evidence_id="log-auth",
                evidence_type=EvidenceType.LOG,
                source=AUTH_SERVICE,
                summary="ERROR: token signature 401 403 auth failure",
                relevance_score=0.8,
                timestamp=timestamp,
            ),
            EvidenceItem(
                evidence_id="log-upstream",
                evidence_type=EvidenceType.LOG,
                source=AUTH_SERVICE,
                summary="ERROR: upstream provider deadline exceeded external timeout",
                relevance_score=0.8,
                timestamp=timestamp,
            ),
        ],
        recent_changes=[],
    )


def test_loader_loads_deployment_regression() -> None:
    skill = _loader().load(DEPLOYMENT_SKILL)

    assert isinstance(skill, Skill)
    assert skill.name == "Deployment Regression"
    assert "deployment" in skill.description.lower()
    assert skill.source_path.endswith("skills/deployment-regression/SKILL.md")


def test_loader_loads_postgres_diagnostics() -> None:
    skill = _loader().load(POSTGRES_SKILL)

    assert skill.name == "PostgreSQL Diagnostics"
    assert "database" in skill.description.lower() or "postgresql" in skill.description.lower()
    assert skill.source_path.endswith("skills/postgres-diagnostics/SKILL.md")


def test_loader_loads_authentication_failure() -> None:
    skill = _loader().load(AUTH_SKILL)

    assert skill.name == "Authentication Failure"
    assert "token" in skill.description.lower() or "authentication" in skill.description.lower()
    assert skill.source_path.endswith("skills/authentication-failure/SKILL.md")


def test_loader_loads_external_api_failure() -> None:
    skill = _loader().load(EXTERNAL_SKILL)

    assert skill.name == "External API Failure"
    assert "provider" in skill.description.lower() or "upstream" in skill.description.lower()
    assert skill.source_path.endswith("skills/external-api-failure/SKILL.md")


def test_unknown_skill_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown skill: does-not-exist"):
        _loader().load("does-not-exist")


def test_parsed_skills_contain_diagnostic_steps() -> None:
    loader = _loader()
    for name in SKILL_NAMES:
        skill = loader.load(name)
        assert skill.diagnostic_steps
        assert all(step for step in skill.diagnostic_steps)


def test_parsed_skills_contain_safety_rules() -> None:
    loader = _loader()
    for name in SKILL_NAMES:
        skill = loader.load(name)
        assert skill.safety_rules
        assert all(rule for rule in skill.safety_rules)


def test_parsed_skills_contain_verification_steps() -> None:
    loader = _loader()
    for name in SKILL_NAMES:
        skill = loader.load(name)
        assert skill.verification_steps
        assert all(step for step in skill.verification_steps)


def test_checkout_context_selects_deployment_and_postgres() -> None:
    selected = SkillSelector().select(_checkout_context())

    assert selected == [DEPLOYMENT_SKILL, POSTGRES_SKILL]


def test_auth_context_selects_deployment_and_authentication() -> None:
    selected = SkillSelector().select(_auth_context())

    assert selected == [DEPLOYMENT_SKILL, AUTH_SKILL]


def test_payments_context_selects_deployment_and_external() -> None:
    selected = SkillSelector().select(_payments_context())

    assert selected == [DEPLOYMENT_SKILL, EXTERNAL_SKILL]


def test_selector_returns_at_most_two_skills() -> None:
    selected = SkillSelector().select(_kitchen_sink_context())

    assert len(selected) <= MAX_SELECTED_SKILLS
    assert len(selected) == 2
    assert selected == [DEPLOYMENT_SKILL, POSTGRES_SKILL]


def test_checkout_prompt_contains_only_selected_skills() -> None:
    _, provider, _, _, _ = _analyze(CHECKOUT_ID, CHECKOUT_SERVICE, "inc-checkout-001")
    prompt = provider.recorded_prompt()

    assert DEPLOYMENT_TITLE in prompt
    assert POSTGRES_TITLE in prompt
    assert AUTH_TITLE not in prompt
    assert EXTERNAL_TITLE not in prompt
    assert POSTGRES_GUIDANCE in prompt
    assert "Relevant diagnostic skills:" in prompt


def test_checkout_prompt_excludes_authentication_failure_guidance() -> None:
    _, provider, _, _, _ = _analyze(CHECKOUT_ID, CHECKOUT_SERVICE, "inc-checkout-001")
    prompt = provider.recorded_prompt()

    assert AUTH_GUIDANCE not in prompt
    assert AUTH_TITLE not in prompt


def test_auth_prompt_excludes_postgres_diagnostics_guidance() -> None:
    _, provider, _, _, _ = _analyze(AUTH_ID, AUTH_SERVICE, "inc-auth-001")
    prompt = provider.recorded_prompt()

    assert AUTH_TITLE in prompt
    assert DEPLOYMENT_TITLE in prompt
    assert POSTGRES_TITLE not in prompt
    assert POSTGRES_GUIDANCE not in prompt


def test_payments_prompt_excludes_authentication_failure_guidance() -> None:
    _, provider, _, _, _ = _analyze(PAYMENTS_ID, PAYMENTS_SERVICE, "inc-payments-001")
    prompt = provider.recorded_prompt()

    assert EXTERNAL_TITLE in prompt
    assert DEPLOYMENT_TITLE in prompt
    assert AUTH_TITLE not in prompt
    assert AUTH_GUIDANCE not in prompt
    assert EXTERNAL_GUIDANCE in prompt


def test_skill_text_contains_no_simulator_ground_truth() -> None:
    loader = _loader()
    for name in SKILL_NAMES:
        skill = loader.load(name)
        blob = (
            f"{skill.name}\n{skill.description}\n"
            + "\n".join(skill.diagnostic_steps)
            + "\n".join(skill.safety_rules)
            + "\n".join(skill.verification_steps)
        )
        source = Path(skill.source_path).read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in blob
            assert token not in source
        assert "rollback_deployment" not in blob
        assert "rollback_deployment" not in source


def test_selected_skills_do_not_mutate_environment() -> None:
    environment, tools = _tools(CHECKOUT_ID)
    before_metrics = environment.query_metrics(CHECKOUT_SERVICE)
    before_logs = environment.get_logs(CHECKOUT_SERVICE)
    before_deployments = environment.get_recent_deployments(CHECKOUT_SERVICE)
    engine = HypothesisEngine(FakeModelProvider())
    context = engine.build_context(
        incident_id="inc-checkout-001",
        affected_service=CHECKOUT_SERVICE,
        metrics=tools.query_metrics(CHECKOUT_SERVICE),
        deployments=tools.get_recent_deployments(CHECKOUT_SERVICE),
        logs=tools.get_service_logs(CHECKOUT_SERVICE),
    )

    selected = engine.select_skills(context)
    loaded = [SkillLoader().load(name) for name in selected]
    engine.analyze_context(context)

    assert selected == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
    assert loaded
    assert environment.is_resolved is False
    assert environment.get_audit_events() == []
    assert environment.query_metrics(CHECKOUT_SERVICE) == before_metrics
    assert environment.get_logs(CHECKOUT_SERVICE) == before_logs
    assert environment.get_recent_deployments(CHECKOUT_SERVICE) == before_deployments


def test_investigation_workflow_stores_selected_skill_names() -> None:
    environment, tools = _tools(CHECKOUT_ID)
    result = InvestigationWorkflow(
        tools=tools,
        hypothesis_engine=HypothesisEngine(FakeModelProvider()),
    ).run("inc-checkout-001", CHECKOUT_SERVICE)

    assert result["selected_skills"] == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
    assert environment.is_resolved is False


def test_api_exposes_selected_skill_names() -> None:
    client = TestClient(create_app(provider=FakeModelProvider()))
    response = client.post("/api/incidents/start", json={"scenario_id": CHECKOUT_ID})

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_skills"] == [DEPLOYMENT_SKILL, POSTGRES_SKILL]
    assert "source_path" not in payload
    assert "diagnostic_steps" not in str(payload["selected_skills"])


def test_list_skills_returns_registered_skill_directories() -> None:
    assert _loader().list_skills() == sorted(SKILL_NAMES)
