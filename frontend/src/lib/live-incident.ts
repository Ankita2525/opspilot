import type { InvestigationEvent, Metrics } from "./types";

export type TimelineStepStatus = "pending" | "running" | "completed";

export type TimelineStepId =
  | "inspect_metrics"
  | "inspect_deployments"
  | "inspect_logs"
  | "build_context"
  | "load_skills"
  | "generate_hypothesis";

export type TimelineStep = {
  id: TimelineStepId;
  label: string;
  status: TimelineStepStatus;
};

export const TIMELINE_STEP_ORDER: TimelineStepId[] = [
  "inspect_metrics",
  "inspect_deployments",
  "inspect_logs",
  "build_context",
  "load_skills",
  "generate_hypothesis",
];

export const TIMELINE_STEP_LABELS: Record<TimelineStepId, string> = {
  inspect_metrics: "Inspect metrics",
  inspect_deployments: "Inspect deployments",
  inspect_logs: "Inspect logs",
  build_context: "Build incident context",
  load_skills: "Load diagnostic skills",
  generate_hypothesis: "Generate root-cause hypothesis",
};

const GRAPH_STEP_TO_TIMELINE: Record<string, TimelineStepId> = {
  inspect_metrics: "inspect_metrics",
  inspect_deployments: "inspect_deployments",
  inspect_logs: "inspect_logs",
};

export type LiveHypothesis = {
  rootCause: string;
  confidence: number;
  recommendedAction: string;
};

export type LiveApproval = {
  proposalId: string;
  action: string;
  service: string;
  version: string;
  riskLevel: string;
  message: string;
};

export type BoundedEvidence = {
  evidenceType: string;
  summary: string;
};

export type LiveIncidentState = {
  lastSequence: number;
  eventCount: number;
  incidentId: string | null;
  scenarioId: string | null;
  affectedService: string | null;
  streaming: boolean;
  failed: boolean;
  metrics: Metrics | null;
  selectedSkills: string[];
  symptomSummary: string | null;
  evidence: BoundedEvidence[];
  hypothesis: LiveHypothesis | null;
  approval: LiveApproval | null;
  investigationComplete: boolean;
  timeline: Record<TimelineStepId, TimelineStepStatus>;
};

export function createLiveIncidentState(): LiveIncidentState {
  return {
    lastSequence: 0,
    eventCount: 0,
    incidentId: null,
    scenarioId: null,
    affectedService: null,
    streaming: true,
    failed: false,
    metrics: null,
    selectedSkills: [],
    symptomSummary: null,
    evidence: [],
    hypothesis: null,
    approval: null,
    investigationComplete: false,
    timeline: {
      inspect_metrics: "pending",
      inspect_deployments: "pending",
      inspect_logs: "pending",
      build_context: "pending",
      load_skills: "pending",
      generate_hypothesis: "pending",
    },
  };
}

export function timelineSteps(state: LiveIncidentState): TimelineStep[] {
  return TIMELINE_STEP_ORDER.map((id) => ({
    id,
    label: TIMELINE_STEP_LABELS[id],
    status: state.timeline[id],
  }));
}

export function applyInvestigationEvent(
  state: LiveIncidentState,
  event: InvestigationEvent,
): LiveIncidentState {
  if (event.sequence <= state.lastSequence) {
    return state;
  }

  const next: LiveIncidentState = {
    ...state,
    lastSequence: event.sequence,
    eventCount: state.eventCount + 1,
    incidentId: event.incident_id,
    timeline: { ...state.timeline },
  };

  switch (event.event_type) {
    case "incident_started":
      next.scenarioId = readString(event.data, "scenario_id") ?? next.scenarioId;
      next.affectedService =
        readString(event.data, "affected_service") ?? next.affectedService;
      break;
    case "step_started":
      applyStepStarted(next, event.step);
      break;
    case "step_completed":
      applyStepCompleted(next, event);
      break;
    case "context_built":
      completeStep(next, "build_context");
      startIfPending(next, "load_skills");
      next.symptomSummary =
        readString(event.data, "symptom_summary") ?? next.symptomSummary;
      next.evidence = readEvidence(event.data);
      break;
    case "skills_selected":
      completeStep(next, "load_skills");
      startIfPending(next, "generate_hypothesis");
      next.selectedSkills = readStringArray(event.data, "selected_skills");
      break;
    case "hypothesis_generated":
      completeStep(next, "generate_hypothesis");
      next.hypothesis = readHypothesis(event.data) ?? next.hypothesis;
      break;
    case "approval_required":
      next.streaming = false;
      next.investigationComplete = true;
      next.approval = readApproval(event);
      break;
    case "incident_completed":
      next.streaming = false;
      next.investigationComplete = true;
      break;
    case "incident_failed":
      next.streaming = false;
      next.failed = true;
      break;
    default:
      break;
  }

  return next;
}

function applyStepStarted(state: LiveIncidentState, step: string | null): void {
  if (step === "generate_hypothesis") {
    startIfPending(state, "build_context");
    return;
  }
  const mapped = step ? GRAPH_STEP_TO_TIMELINE[step] : undefined;
  if (mapped) {
    startIfPending(state, mapped);
  }
}

function applyStepCompleted(
  state: LiveIncidentState,
  event: InvestigationEvent,
): void {
  if (event.step === "inspect_metrics") {
    completeStep(state, "inspect_metrics");
    const latency = readNumber(event.data, "p95_latency_ms");
    const errorRate = readNumber(event.data, "error_rate_percent");
    if (latency !== null && errorRate !== null) {
      state.metrics = {
        service: state.affectedService ?? "",
        p95_latency_ms: latency,
        error_rate_percent: errorRate,
        timestamp: event.timestamp,
      };
    }
    return;
  }
  if (event.step === "inspect_deployments") {
    completeStep(state, "inspect_deployments");
    return;
  }
  if (event.step === "inspect_logs") {
    completeStep(state, "inspect_logs");
  }
}

function startIfPending(state: LiveIncidentState, id: TimelineStepId): void {
  if (state.timeline[id] === "pending") {
    state.timeline[id] = "running";
  }
}

function completeStep(state: LiveIncidentState, id: TimelineStepId): void {
  state.timeline[id] = "completed";
}

function readHypothesis(data: Record<string, unknown>): LiveHypothesis | null {
  const rootCause = readString(data, "root_cause");
  const confidence = readNumber(data, "confidence");
  const recommendedAction = readString(data, "recommended_action");
  if (rootCause === null || confidence === null || recommendedAction === null) {
    return null;
  }
  return { rootCause, confidence, recommendedAction };
}

function readApproval(event: InvestigationEvent): LiveApproval | null {
  const proposalId = readString(event.data, "proposal_id");
  const action = readString(event.data, "action");
  const service = readString(event.data, "service");
  const version = readString(event.data, "version");
  const riskLevel = readString(event.data, "risk_level");
  if (
    proposalId === null ||
    action === null ||
    service === null ||
    version === null ||
    riskLevel === null
  ) {
    return null;
  }
  return {
    proposalId,
    action,
    service,
    version,
    riskLevel,
    message: event.message,
  };
}

function readString(
  data: Record<string, unknown>,
  key: string,
): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

function readNumber(
  data: Record<string, unknown>,
  key: string,
): number | null {
  const value = data[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStringArray(
  data: Record<string, unknown>,
  key: string,
): string[] {
  const value = data[key];
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function readEvidence(data: Record<string, unknown>): BoundedEvidence[] {
  const value = data.evidence;
  if (!Array.isArray(value)) {
    return [];
  }
  const items: BoundedEvidence[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const evidenceType = record.evidence_type;
    const summary = record.summary;
    if (typeof evidenceType !== "string" || typeof summary !== "string") {
      continue;
    }
    items.push({ evidenceType, summary });
  }
  return items;
}
