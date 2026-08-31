from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.safety.approvals import ApprovalService
from backend.app.safety.models import ApprovalStatus, RemediationProposal, RiskLevel
from backend.app.safety.policy import ActionPolicy
from backend.app.tools.remediation import RemediationTools
from simulator.environment import SimulatedEnvironment
from tests.fakes import FakeModelProvider

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
OTHER_SERVICE = "auth-service"
INCIDENT_ID = "inc-checkout-001"
BAD_VERSION = "v1.18.3"
OTHER_VERSION = "v9.9.9"
PROPOSAL_ID = "prop-rollback-001"
TEST_DATABASE_ENV = "OPSPILOT_TEST_DATABASE_URL"
FORBIDDEN = (
    "known_root_cause",
    "expected_remediation",
    "chain_of_thought",
    "chain-of-thought",
    "system_prompt",
    "user_prompt",
    "GROQ_API_KEY",
)


def _environment(scenario_id: str = SCENARIO_ID) -> SimulatedEnvironment:
    environment = SimulatedEnvironment()
    environment.load_scenario(scenario_id)
    return environment


def _proposal(
    *,
    proposal_id: str = PROPOSAL_ID,
    service: str = SERVICE,
    version: str = BAD_VERSION,
) -> RemediationProposal:
    return RemediationProposal(
        proposal_id=proposal_id,
        incident_id=INCIDENT_ID,
        action="rollback_deployment",
        service=service,
        parameters={"version": version},
        risk_level=ActionPolicy().classify("rollback_deployment"),
        approval_status=ApprovalStatus.PENDING,
    )


def test_pending_proposal_persists() -> None:
    repository = InMemoryOpsPilotRepository()
    service = ApprovalService(repository=repository)

    stored = service.submit(_proposal())

    record = repository.get_approval(PROPOSAL_ID)
    assert record is not None
    assert record.status == "pending"
    assert stored.approval_status == ApprovalStatus.PENDING


def test_new_service_loads_pending_proposal_from_same_repository() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())

    recovered = ApprovalService(repository=repository).get(PROPOSAL_ID)

    assert recovered.proposal_id == PROPOSAL_ID
    assert recovered.approval_status == ApprovalStatus.PENDING
    assert recovered.service == SERVICE
    assert recovered.parameters == {"version": BAD_VERSION}


def test_approve_after_recreation_works() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())

    approved = ApprovalService(repository=repository).approve(PROPOSAL_ID)

    assert approved.approval_status == ApprovalStatus.APPROVED
    assert ApprovalService(repository=repository).get(PROPOSAL_ID).approval_status == (
        ApprovalStatus.APPROVED
    )


def test_reject_after_recreation_works() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())

    rejected = ApprovalService(repository=repository).reject(PROPOSAL_ID)

    assert rejected.approval_status == ApprovalStatus.REJECTED
    assert ApprovalService(repository=repository).get(PROPOSAL_ID).approval_status == (
        ApprovalStatus.REJECTED
    )


def test_approved_proposal_preserves_immutable_fields() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())
    approved = ApprovalService(repository=repository).approve(PROPOSAL_ID)

    assert approved.action == "rollback_deployment"
    assert approved.service == SERVICE
    assert approved.parameters == {"version": BAD_VERSION}
    assert approved.risk_level == RiskLevel.HIGH_RISK
    record = repository.get_approval(PROPOSAL_ID)
    assert record is not None
    assert record.action == "rollback_deployment"
    assert record.service == SERVICE
    assert record.version == BAD_VERSION
    assert record.risk_level == RiskLevel.HIGH_RISK.value


def test_execution_after_recreation_uses_original_service_and_version() -> None:
    repository = InMemoryOpsPilotRepository()
    environment = _environment()
    ApprovalService(repository=repository).submit(_proposal())
    approvals = ApprovalService(repository=repository)
    approvals.approve(PROPOSAL_ID)
    tools = RemediationTools(environment, approvals)

    result = tools.execute_rollback(PROPOSAL_ID)

    assert result.service == SERVICE
    assert result.version == BAD_VERSION
    assert environment.is_resolved is True


def test_caller_cannot_substitute_service_by_resubmitting_same_proposal() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())

    with pytest.raises(ValueError, match="Proposal already exists"):
        ApprovalService(repository=repository).submit(
            _proposal(service=OTHER_SERVICE, version=OTHER_VERSION)
        )

    stored = ApprovalService(repository=repository).get(PROPOSAL_ID)
    assert stored.service == SERVICE
    assert stored.parameters == {"version": BAD_VERSION}


def test_mutated_snapshot_cannot_change_stored_execution_parameters() -> None:
    repository = InMemoryOpsPilotRepository()
    environment = _environment()
    first = ApprovalService(repository=repository)
    snapshot = first.submit(_proposal())
    first.approve(PROPOSAL_ID)
    snapshot.parameters["version"] = OTHER_VERSION
    snapshot.parameters["service"] = OTHER_SERVICE

    result = RemediationTools(
        environment, ApprovalService(repository=repository)
    ).execute_rollback(PROPOSAL_ID)

    assert result.service == SERVICE
    assert result.version == BAD_VERSION


def test_pending_proposal_cannot_execute() -> None:
    repository = InMemoryOpsPilotRepository()
    environment = _environment()
    ApprovalService(repository=repository).submit(_proposal())
    tools = RemediationTools(environment, ApprovalService(repository=repository))

    with pytest.raises(PermissionError):
        tools.execute_rollback(PROPOSAL_ID)
    assert environment.is_resolved is False


def test_rejected_proposal_cannot_execute_after_recreation() -> None:
    repository = InMemoryOpsPilotRepository()
    environment = _environment()
    ApprovalService(repository=repository).submit(_proposal())
    ApprovalService(repository=repository).reject(PROPOSAL_ID)
    tools = RemediationTools(environment, ApprovalService(repository=repository))

    with pytest.raises(PermissionError):
        tools.execute_rollback(PROPOSAL_ID)
    assert environment.is_resolved is False


def test_rejected_proposal_cannot_become_approved() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())
    ApprovalService(repository=repository).reject(PROPOSAL_ID)

    with pytest.raises(ValueError, match="Rejected proposal cannot be approved"):
        ApprovalService(repository=repository).approve(PROPOSAL_ID)


def test_missing_proposal_fails_safely() -> None:
    repository = InMemoryOpsPilotRepository()
    service = ApprovalService(repository=repository)
    environment = _environment()

    with pytest.raises(ValueError, match="Unknown proposal"):
        service.get("missing-proposal")
    with pytest.raises(ValueError, match="Unknown proposal"):
        RemediationTools(environment, service).execute_rollback("missing-proposal")
    assert environment.is_resolved is False


def test_repository_backed_proposal_obeys_action_policy() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())

    stored = ApprovalService(repository=repository).get(PROPOSAL_ID)
    assert stored.risk_level == ActionPolicy().classify("rollback_deployment")
    assert stored.risk_level == RiskLevel.HIGH_RISK


def test_execute_rollback_does_not_accept_replacement_parameters() -> None:
    import inspect

    parameters = list(inspect.signature(RemediationTools.execute_rollback).parameters)
    assert parameters == ["self", "proposal_id"]


def test_durable_records_contain_no_secrets_or_ground_truth() -> None:
    repository = InMemoryOpsPilotRepository()
    ApprovalService(repository=repository).submit(_proposal())
    ApprovalService(repository=repository).approve(PROPOSAL_ID)
    record = repository.get_approval(PROPOSAL_ID)
    blob = json.dumps(record.model_dump(mode="json") if record is not None else {})
    for token in FORBIDDEN:
        assert token not in blob


def test_api_approval_path_still_resolves_checkout() -> None:
    from fastapi.testclient import TestClient

    from backend.app.api.app import create_app

    repository = InMemoryOpsPilotRepository()
    client = TestClient(
        create_app(provider=FakeModelProvider(), repository=repository)
    )
    started = client.post(
        "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
    ).json()
    proposal_id = started["approval_request"]["proposal_id"]

    response = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": True},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is True
    stored = repository.get_approval(proposal_id)
    assert stored is not None
    assert stored.status == "approved"
    assert stored.service == SERVICE
    assert stored.version == BAD_VERSION
    assert stored.action == "rollback_deployment"


def test_api_rejection_keeps_checkout_unresolved() -> None:
    from fastapi.testclient import TestClient

    from backend.app.api.app import create_app

    repository = InMemoryOpsPilotRepository()
    client = TestClient(
        create_app(provider=FakeModelProvider(), repository=repository)
    )
    started = client.post(
        "/api/incidents/start", json={"scenario_id": SCENARIO_ID}
    ).json()
    proposal_id = started["approval_request"]["proposal_id"]

    payload = client.post(
        f"/api/incidents/{started['incident_id']}/approval",
        json={"approved": False},
    ).json()

    assert payload["resolved"] is False
    stored = repository.get_approval(proposal_id)
    assert stored is not None
    assert stored.status == "rejected"
    metrics = client.get(f"/api/incidents/{started['incident_id']}/metrics").json()
    assert metrics["p95_latency_ms"] == 1940


def test_auth_and_payments_api_flows_still_require_approval() -> None:
    from fastapi.testclient import TestClient

    from backend.app.api.app import create_app

    client = TestClient(create_app(provider=FakeModelProvider()))
    for scenario_id in (
        "auth-token-validation-regression",
        "payments-provider-timeout-regression",
    ):
        started = client.post(
            "/api/incidents/start", json={"scenario_id": scenario_id}
        ).json()
        assert started["status"] == "approval_required"
        assert started["approval_request"]["action"] == "rollback_deployment"


def test_optional_postgres_proposal_survives_repository_recreation() -> None:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_ENV} is not set")
    production_url = os.environ.get("DATABASE_URL")
    if production_url and url == production_url:
        pytest.skip("refusing to run destructive tests against DATABASE_URL")

    import psycopg

    from backend.app.persistence.postgres import PostgresOpsPilotRepository
    from backend.app.persistence.schema import initialize_schema

    initialize_schema(url)
    proposal_id = f"opspilot-test-prop-{uuid4().hex}"
    first = PostgresOpsPilotRepository(url)
    try:
        ApprovalService(repository=first).submit(_proposal(proposal_id=proposal_id))
        second = PostgresOpsPilotRepository(url)
        recovered = ApprovalService(repository=second).get(proposal_id)
        assert recovered.service == SERVICE
        assert recovered.parameters == {"version": BAD_VERSION}
        assert recovered.action == "rollback_deployment"
        assert recovered.risk_level == RiskLevel.HIGH_RISK
        approved = ApprovalService(repository=second).approve(proposal_id)
        assert approved.approval_status == ApprovalStatus.APPROVED
        assert approved.service == SERVICE
        assert approved.parameters == {"version": BAD_VERSION}
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(
                "DELETE FROM approvals WHERE proposal_id = %s",
                (proposal_id,),
            )
