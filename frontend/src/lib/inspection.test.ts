import assert from "node:assert/strict";
import { test } from "node:test";

import {
  baselineModeLabel,
  DETERMINISTIC_BASELINE_LABEL,
  evaluationScenarioRows,
  formatAverageInvestigationSteps,
  formatPassedScenarios,
  formatUnitInterval,
  formatUnsafeActionRate,
  preserveAuditOrder,
} from "./inspection.ts";
import type { AuditEvent, BaselineScenarioEvaluation } from "./types.ts";

function auditEvent(
  eventType: string,
  extras?: Partial<AuditEvent>,
): AuditEvent {
  return {
    event_type: eventType,
    message: eventType,
    timestamp: "2026-08-31T10:43:04+00:00",
    metadata: {},
    ...extras,
  };
}

test("preserveAuditOrder keeps backend event sequence", () => {
  const events = [
    auditEvent("incident_started"),
    auditEvent("investigation_completed"),
    auditEvent("approval_requested"),
    auditEvent("approval_rejected"),
  ];

  const ordered = preserveAuditOrder(events);

  assert.deepEqual(
    ordered.map((item) => item.event_type),
    [
      "incident_started",
      "investigation_completed",
      "approval_requested",
      "approval_rejected",
    ],
  );
  assert.ok(
    !ordered.some((item) => item.event_type === "remediation_executed"),
  );
});

test("evaluation rows use scenario_id, not incident_id", () => {
  const results: BaselineScenarioEvaluation[] = [
    {
      scenario_id: "checkout-db-pool-regression",
      root_cause_correct: true,
      recommended_action_correct: true,
      approval_required: true,
      unsafe_action_attempted: false,
      remediation_executed: true,
      incident_resolved: true,
      latency_recovered: true,
      error_rate_recovered: true,
      investigation_steps: 5,
      predicted_root_cause: "connection_pool_exhaustion",
      recommended_action: "rollback_deployment",
      final_p95_latency_ms: 218,
      final_error_rate_percent: 0.3,
      resolution_success: true,
    },
  ];

  const rows = evaluationScenarioRows(results);

  assert.equal(rows[0]?.scenarioId, "checkout-db-pool-regression");
  assert.equal("incident_id" in rows[0], false);
  assert.equal(
    Object.prototype.hasOwnProperty.call(rows[0], "incidentId"),
    false,
  );
});

test("unsafe action rate 0 formats as 0%", () => {
  assert.equal(formatUnsafeActionRate(0), "0%");
  assert.equal(formatUnsafeActionRate(0.0), "0%");
});

test("aggregate percentage formatting uses unit intervals", () => {
  assert.equal(formatUnitInterval(1.0), "100%");
  assert.equal(formatUnitInterval(0.0), "0%");
  assert.equal(formatPassedScenarios(3, 3), "3 / 3");
  assert.equal(formatAverageInvestigationSteps(5), "5.0");
});

test("deterministic baseline label comes from evaluation_mode", () => {
  assert.equal(
    baselineModeLabel("deterministic_baseline"),
    DETERMINISTIC_BASELINE_LABEL,
  );
  assert.equal(DETERMINISTIC_BASELINE_LABEL, "Deterministic evaluation baseline");
});
