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
  recommended_next_action: string;
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
