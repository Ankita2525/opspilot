export const INVESTIGATION_EVENT_TYPES = [
  "incident_started",
  "step_started",
  "step_completed",
  "context_built",
  "skills_selected",
  "hypothesis_generated",
  "approval_required",
  "incident_completed",
  "incident_failed",
  "sandbox_warming",
  "baseline_collection_started",
  "baseline_collected",
  "fault_activated",
  "workload_started",
  "telemetry_source_degraded",
  "telemetry_source_recovered",
  "live_evidence_collected",
  "investigation_blocked",
  "verification_started",
  "verification_sample",
  "verification_pending",
  "verification_completed",
  "workload_stopped",
] as const;

export type InvestigationEventType = (typeof INVESTIGATION_EVENT_TYPES)[number];

export type InvestigationEvent = {
  event_type: InvestigationEventType;
  incident_id: string;
  sequence: number;
  timestamp: string;
  step: string | null;
  message: string;
  data: Record<string, unknown>;
};

export type Scenario = {
  id: string;
  title: string;
  affected_service: string;
};

export type Metrics = {
  service: string;
  p95_latency_ms: number;
  error_rate_percent: number;
  timestamp: string;
};

export type EvidenceReference = {
  source_type: string;
  summary: string;
};

export type Hypothesis = {
  cause: string;
  confidence: number;
  evidence: EvidenceReference[];
};

export type HypothesisResult = {
  hypotheses: Hypothesis[];
  recommended_action: string;
  recommendation_summary: string;
  reasoning_summary: string;
};

export type ApprovalRequest = {
  type: string;
  proposal_id: string;
  incident_id: string;
  action: string;
  service: string;
  version: string;
  risk_level: string;
  message: string;
};

export type IncidentStartResponse = {
  incident_id: string;
  scenario_id: string;
  affected_service: string;
  status: string;
  investigation_status: string;
  investigation_steps: string[];
  metrics: Metrics;
  hypothesis_result: HypothesisResult;
  recommended_action: string | null;
  proposed_version: string | null;
  approval_request: ApprovalRequest | null;
  resolved: boolean;
};

export type IncidentApprovalResponse = {
  incident_id: string;
  status: string;
  execution_success: boolean;
  recovered_p95_latency_ms: number | null;
  recovered_error_rate_percent: number | null;
  resolved: boolean;
  approval_status: string | null;
};

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type AuditEvent = {
  event_type: string;
  message: string;
  timestamp: string;
  metadata: Record<string, JsonValue>;
};

export type IncidentAudit = {
  incident_id: string;
  events: AuditEvent[];
};

export type BaselineScenarioEvaluation = {
  scenario_id: string;
  root_cause_correct: boolean;
  recommended_action_correct: boolean;
  approval_required: boolean;
  unsafe_action_attempted: boolean;
  remediation_executed: boolean;
  incident_resolved: boolean;
  latency_recovered: boolean;
  error_rate_recovered: boolean;
  investigation_steps: number;
  predicted_root_cause: string | null;
  recommended_action: string | null;
  final_p95_latency_ms: number;
  final_error_rate_percent: number;
  resolution_success: boolean;
};

export type BaselineEvaluation = {
  evaluation_mode: "deterministic_baseline";
  total_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  root_cause_accuracy: number;
  recommended_action_accuracy: number;
  approval_compliance_rate: number;
  unsafe_action_rate: number;
  remediation_execution_rate: number;
  resolution_rate: number;
  health_recovery_rate: number;
  average_investigation_steps: number;
  scenario_results: BaselineScenarioEvaluation[];
};

export type TelemetryWindow = {
  sample_count: number;
  window_start?: string | null;
  window_end?: string | null;
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
  error_rate?: number | null;
};

export type LiveProvenance = {
  run_id: string;
  incident_id: string;
  telemetry_mode: "live" | "reference";
  environment: string;
  service: string;
  service_revision: string | null;
  started_at: string;
  baseline: TelemetryWindow | null;
  degraded: TelemetryWindow | null;
  diagnosis: {
    provider: string;
    model: string | null;
    evidence_count: number;
    generated_at: string | null;
    primary_model_attempted?: string | null;
    fallback_used?: boolean;
    fallback_model?: string | null;
    fallback_reason?: string | null;
    final_model?: string | null;
  } | null;
  remediation: {
    typed_action: string | null;
    approval_required: boolean;
    approved_at: string | null;
    executed_at: string | null;
  } | null;
  recovery: {
    sample_count: number | null;
    p95_latency_ms: number | null;
    error_rate: number | null;
    latest_metric_timestamp: string | null;
    latest_log_timestamp: string | null;
    all_samples_post_remediation: boolean | null;
    verified: boolean | null;
  } | null;
  ground_truth_visible_to_agent: boolean;
  evidence_manifest_hash: string | null;
};
