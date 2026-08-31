import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyInvestigationEvent,
  createLiveIncidentState,
} from "./live-incident.ts";
import type { InvestigationEvent } from "./types.ts";

function event(
  partial: Partial<InvestigationEvent> &
    Pick<InvestigationEvent, "event_type" | "sequence">,
): InvestigationEvent {
  return {
    incident_id: "inc-1",
    timestamp: "2026-08-31T12:00:00+00:00",
    step: null,
    message: "ok",
    data: {},
    ...partial,
  };
}

test("ignores duplicate and older sequence numbers", () => {
  let state = createLiveIncidentState();
  state = applyInvestigationEvent(
    state,
    event({
      event_type: "incident_started",
      sequence: 2,
      data: { affected_service: "checkout-api" },
    }),
  );
  const duplicate = applyInvestigationEvent(
    state,
    event({
      event_type: "incident_started",
      sequence: 2,
      data: { affected_service: "auth-api" },
    }),
  );
  const older = applyInvestigationEvent(
    state,
    event({
      event_type: "incident_started",
      sequence: 1,
      data: { affected_service: "payments-api" },
    }),
  );

  assert.equal(duplicate.affectedService, "checkout-api");
  assert.equal(older.affectedService, "checkout-api");
  assert.equal(duplicate.eventCount, 1);
  assert.equal(older.eventCount, 1);
});

test("completes timeline steps only after matching events", () => {
  let state = createLiveIncidentState();
  state = applyInvestigationEvent(
    state,
    event({ event_type: "step_started", sequence: 1, step: "inspect_metrics" }),
  );
  assert.equal(state.timeline.inspect_metrics, "running");
  assert.equal(state.timeline.inspect_deployments, "pending");

  state = applyInvestigationEvent(
    state,
    event({
      event_type: "step_completed",
      sequence: 2,
      step: "inspect_metrics",
      data: { p95_latency_ms: 1234, error_rate_percent: 3.5 },
    }),
  );
  assert.equal(state.timeline.inspect_metrics, "completed");
  assert.equal(state.metrics?.p95_latency_ms, 1234);
  assert.equal(state.timeline.inspect_logs, "pending");
});

test("context_built stores bounded evidence in backend order", () => {
  let state = createLiveIncidentState();
  state = applyInvestigationEvent(
    state,
    event({
      event_type: "context_built",
      sequence: 1,
      data: {
        symptom_summary:
          "checkout-api is experiencing p95 latency of 1940 ms and error rate of 8.2%",
        evidence: [
          {
            evidence_type: "log",
            summary: "Database connection pool timeout",
          },
          {
            evidence_type: "deployment",
            summary: "Deployment v1.18.3 occurred at 13:58",
          },
          {
            evidence_type: "metric",
            summary: "p95 latency is 1940 ms and error rate is 8.2%",
          },
        ],
      },
    }),
  );

  assert.equal(
    state.symptomSummary,
    "checkout-api is experiencing p95 latency of 1940 ms and error rate of 8.2%",
  );
  assert.deepEqual(
    state.evidence.map((item) => item.evidenceType),
    ["log", "deployment", "metric"],
  );
  assert.equal(state.evidence[0]?.summary, "Database connection pool timeout");
});

test("incident_completed is a terminal investigation without approval", () => {
  let state = createLiveIncidentState();
  state = applyInvestigationEvent(
    state,
    event({
      event_type: "hypothesis_generated",
      sequence: 1,
      data: {
        root_cause: "db_connection_pool_regression",
        confidence: 0.8,
        recommended_action: "no_supported_action",
        recommendation_summary:
          "Connection pool exhaustion followed deployment v1.18.3.",
      },
    }),
  );
  state = applyInvestigationEvent(
    state,
    event({
      event_type: "incident_completed",
      sequence: 2,
      data: {
        status: "investigation_complete",
        recommended_action: "no_supported_action",
      },
    }),
  );

  assert.equal(state.streaming, false);
  assert.equal(state.investigationComplete, true);
  assert.equal(state.failed, false);
  assert.equal(state.approval, null);
  assert.equal(state.hypothesis?.recommendedAction, "no_supported_action");
  assert.equal(
    state.hypothesis?.recommendationSummary,
    "Connection pool exhaustion followed deployment v1.18.3.",
  );
});
