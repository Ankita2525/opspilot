import { humanizeIdentifier } from "./labels.ts";
import type { AuditEvent, BaselineScenarioEvaluation, JsonValue } from "./types.ts";

export const DETERMINISTIC_BASELINE_LABEL = "Deterministic evaluation baseline";

export const DETERMINISTIC_BASELINE_EXPLAIN =
  "Runs the deterministic reference provider across all built-in incident scenarios to validate orchestration, safety, and recovery behavior.";

const FORBIDDEN_META_KEYS = new Set([
  "known_root_cause",
  "expected_remediation",
  "chain_of_thought",
  "chain-of-thought",
  "system_prompt",
  "user_prompt",
  "prompt",
  "groq_api_key",
  "database_url",
]);

const META_ORDER = [
  "affected_service",
  "scenario_id",
  "selected_skills",
  "recommended_action",
  "resolved",
  "execution_success",
  "recovered_p95_latency_ms",
  "recovered_error_rate_percent",
  "proposal_id",
] as const;

export type AuditMetaRow = {
  key: string;
  label: string;
  value: string;
  secondary: boolean;
};

export function preserveAuditOrder(events: AuditEvent[]): AuditEvent[] {
  return events.slice();
}

export function formatAuditClock(timestamp: string): string {
  const match = timestamp.match(/T(\d{2}:\d{2}:\d{2})/);
  return match?.[1] ?? timestamp;
}

export function formatAuditEventName(eventType: string): string {
  return humanizeIdentifier(eventType);
}

export function formatUnitInterval(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatUnsafeActionRate(value: number): string {
  return formatUnitInterval(value);
}

export function formatPassedScenarios(passed: number, total: number): string {
  return `${passed} / ${total}`;
}

export function formatAverageInvestigationSteps(value: number): string {
  return value.toFixed(1);
}

export type EvaluationScenarioRow = {
  scenarioId: string;
  passed: boolean;
  rootCauseCorrect: boolean;
  actionCorrect: boolean;
  resolved: boolean;
  finalP95LatencyMs: number;
  finalErrorRatePercent: number;
};

export function evaluationScenarioRows(
  results: BaselineScenarioEvaluation[],
): EvaluationScenarioRow[] {
  return results.map((item) => ({
    scenarioId: item.scenario_id,
    passed: item.resolution_success,
    rootCauseCorrect: item.root_cause_correct,
    actionCorrect: item.recommended_action_correct,
    resolved: item.incident_resolved,
    finalP95LatencyMs: item.final_p95_latency_ms,
    finalErrorRatePercent: item.final_error_rate_percent,
  }));
}

export function evidenceTypeLabel(evidenceType: string): string {
  return evidenceType.trim().toUpperCase();
}

export function baselineModeLabel(mode: string): string {
  if (mode === "deterministic_baseline") {
    return DETERMINISTIC_BASELINE_LABEL;
  }
  return mode;
}

export function usefulAuditMetadata(
  metadata: Record<string, JsonValue>,
): AuditMetaRow[] {
  const rows: AuditMetaRow[] = [];
  for (const key of META_ORDER) {
    if (!(key in metadata) || FORBIDDEN_META_KEYS.has(key)) {
      continue;
    }
    const formatted = formatMetaValue(key, metadata[key]);
    if (formatted === null) {
      continue;
    }
    rows.push({
      key,
      label: metaLabel(key),
      value: formatted,
      secondary: key === "proposal_id" || key === "scenario_id",
    });
  }
  return rows;
}

function metaLabel(key: string): string {
  if (key === "affected_service") {
    return "Service";
  }
  if (key === "scenario_id") {
    return "Scenario";
  }
  if (key === "selected_skills") {
    return "Skills";
  }
  if (key === "recommended_action") {
    return "Recommended action";
  }
  if (key === "proposal_id") {
    return "Proposal";
  }
  if (key === "recovered_p95_latency_ms") {
    return "Recovered p95";
  }
  if (key === "recovered_error_rate_percent") {
    return "Recovered errors";
  }
  if (key === "execution_success") {
    return "Execution";
  }
  return humanizeIdentifier(key);
}

function formatMetaValue(key: string, value: JsonValue | undefined): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (Array.isArray(value)) {
    const items = value.filter((item): item is string => typeof item === "string");
    return items.length > 0 ? items.join(", ") : null;
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    if (key === "recovered_p95_latency_ms") {
      return `${value} ms`;
    }
    if (key === "recovered_error_rate_percent") {
      return `${value}%`;
    }
    return String(value);
  }
  if (typeof value === "string") {
    if (key === "recommended_action") {
      return humanizeIdentifier(value);
    }
    return value;
  }
  return null;
}
