from backend.app.api.schemas import (
    AuditEventResponse,
    BaselineEvaluationResponse,
    BaselineScenarioEvaluation,
    IncidentApprovalSummary,
    IncidentAuditResponse,
    IncidentSummaryResponse,
)
from backend.app.evals.models import EvaluationSuiteResult, IncidentEvaluationResult
from backend.app.persistence.models import (
    ApprovalRecord,
    AuditRecord,
    IncidentRecord,
    JsonValue,
)
from backend.app.security.untrusted_text import (
    sanitize_public_instance,
    sanitize_public_text,
)

FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "known_root_cause",
        "expected_remediation",
        "chain_of_thought",
        "chain-of-thought",
        "system_prompt",
        "user_prompt",
        "prompt",
        "groq_api_key",
        "database_url",
        "stack_trace",
        "traceback",
    }
)


def public_incident_audit(
    incident_id: str, records: list[AuditRecord]
) -> IncidentAuditResponse:
    ordered = sorted(records, key=lambda item: (item.timestamp, item.audit_id))
    return sanitize_public_instance(
        IncidentAuditResponse(
            incident_id=incident_id,
            events=[_public_audit_event(item) for item in ordered],
        )
    )


def public_incident_summary(
    record: IncidentRecord,
    approvals: list[ApprovalRecord],
) -> IncidentSummaryResponse:
    return sanitize_public_instance(
        IncidentSummaryResponse(
            incident_id=record.incident_id,
            scenario_id=record.scenario_id,
            affected_service=record.affected_service,
            status=record.status,
            selected_skills=list(record.selected_skills),
            recommended_action=record.recommended_action,
            resolved=record.resolved,
            created_at=record.created_at,
            updated_at=record.updated_at,
            approval=_public_approval_summary(approvals),
        )
    )


def public_baseline_evaluation(
    result: EvaluationSuiteResult,
) -> BaselineEvaluationResponse:
    return sanitize_public_instance(
        BaselineEvaluationResponse(
            evaluation_mode="deterministic_baseline",
            total_scenarios=result.total_scenarios,
            passed_scenarios=result.passed_scenarios,
            failed_scenarios=result.failed_scenarios,
            root_cause_accuracy=result.root_cause_accuracy,
            recommended_action_accuracy=result.recommended_action_accuracy,
            approval_compliance_rate=result.approval_compliance_rate,
            unsafe_action_rate=result.unsafe_action_rate,
            remediation_execution_rate=result.remediation_execution_rate,
            resolution_rate=result.resolution_rate,
            health_recovery_rate=result.health_recovery_rate,
            average_investigation_steps=result.average_investigation_steps,
            scenario_results=[_public_scenario_evaluation(item) for item in result.scenario_results],
        )
    )


def sanitize_audit_metadata(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        cleaned: dict[str, JsonValue] = {}
        for key, item in value.items():
            if _forbidden_metadata_key(key):
                continue
            cleaned[key] = sanitize_audit_metadata(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_audit_metadata(item) for item in value]
    if isinstance(value, str):
        return sanitize_public_text(value)
    return value


def _public_audit_event(record: AuditRecord) -> AuditEventResponse:
    metadata = sanitize_audit_metadata(record.metadata)
    if not isinstance(metadata, dict):
        metadata = {}
    return AuditEventResponse(
        event_type=record.event_type,
        message=sanitize_public_text(record.message),
        timestamp=record.timestamp,
        metadata=metadata,
    )


def _public_approval_summary(
    approvals: list[ApprovalRecord],
) -> IncidentApprovalSummary | None:
    if not approvals:
        return None
    latest = max(approvals, key=lambda item: (item.updated_at, item.proposal_id))
    return IncidentApprovalSummary(
        proposal_id=latest.proposal_id,
        action=latest.action,
        service=latest.service,
        version=latest.version,
        risk_level=latest.risk_level,
        status=latest.status,
    )


def _public_scenario_evaluation(
    item: IncidentEvaluationResult,
) -> BaselineScenarioEvaluation:
    return BaselineScenarioEvaluation(
        scenario_id=item.scenario_id,
        root_cause_correct=item.root_cause_correct,
        recommended_action_correct=item.recommended_action_correct,
        approval_required=item.approval_required,
        unsafe_action_attempted=item.unsafe_action_attempted,
        remediation_executed=item.remediation_executed,
        incident_resolved=item.incident_resolved,
        latency_recovered=item.latency_recovered,
        error_rate_recovered=item.error_rate_recovered,
        investigation_steps=item.investigation_steps,
        predicted_root_cause=item.predicted_root_cause,
        recommended_action=item.recommended_action,
        final_p95_latency_ms=item.final_p95_latency_ms,
        final_error_rate_percent=item.final_error_rate_percent,
        resolution_success=item.resolution_success,
    )


def _forbidden_metadata_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in {item.replace("-", "_") for item in FORBIDDEN_METADATA_KEYS}:
        return True
    return "prompt" in normalized or "traceback" in normalized
