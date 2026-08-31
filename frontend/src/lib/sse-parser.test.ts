import assert from "node:assert/strict";
import { test } from "node:test";

import {
  consumeSseBuffer,
  IncidentStreamError,
  parseInvestigationEvent,
  parseSseFields,
} from "./sse-parser.ts";

const SAMPLE_EVENT = {
  event_type: "incident_started",
  incident_id: "inc-1",
  sequence: 1,
  timestamp: "2026-08-31T12:00:00+00:00",
  step: null,
  message: "Incident investigation started.",
  data: {
    scenario_id: "checkout-db-pool-regression",
    affected_service: "checkout-api",
  },
};

function frameFor(
  event: Record<string, unknown>,
  eventName = String(event.event_type),
): string {
  return `event: ${eventName}\ndata: ${JSON.stringify(event)}\n\n`;
}

test("parses one complete SSE frame", () => {
  const consumed = consumeSseBuffer(frameFor(SAMPLE_EVENT));
  assert.equal(consumed.frames.length, 1);
  assert.equal(consumed.rest, "");
  assert.deepEqual(parseInvestigationEvent(consumed.frames[0] ?? ""), SAMPLE_EVENT);
});

test("reassembles an event split across multiple chunks", () => {
  const full = frameFor(SAMPLE_EVENT);
  const first = consumeSseBuffer(full.slice(0, 18));
  assert.deepEqual(first.frames, []);
  assert.ok(first.rest.length > 0);

  const second = consumeSseBuffer(first.rest + full.slice(18));
  assert.equal(second.frames.length, 1);
  assert.equal(second.rest, "");
  assert.equal(
    parseInvestigationEvent(second.frames[0] ?? "").event_type,
    "incident_started",
  );
});

test("parses multiple events in one chunk", () => {
  const started = frameFor(SAMPLE_EVENT);
  const failed = frameFor({
    ...SAMPLE_EVENT,
    event_type: "incident_failed",
    sequence: 2,
    message: "Investigation could not be completed.",
    data: {
      error: "investigation_failed",
      message: "Investigation could not be completed.",
    },
  });
  const consumed = consumeSseBuffer(started + failed);
  assert.equal(consumed.frames.length, 2);
  assert.equal(parseInvestigationEvent(consumed.frames[0] ?? "").sequence, 1);
  assert.equal(
    parseInvestigationEvent(consumed.frames[1] ?? "").event_type,
    "incident_failed",
  );
});

test("parses event and data fields including a leading space", () => {
  const fields = parseSseFields(
    "event: skills_selected\ndata: {\"event_type\":\"skills_selected\"}",
  );
  assert.equal(fields.event, "skills_selected");
  assert.equal(fields.data, '{"event_type":"skills_selected"}');
});

test("malformed JSON fails without exposing payload details", () => {
  assert.throws(
    () => parseInvestigationEvent("event: incident_started\ndata: {not-json}\n"),
    (error: unknown) => {
      assert.ok(error instanceof IncidentStreamError);
      assert.equal(error.message, "Investigation could not be completed.");
      assert.equal(String(error).includes("not-json"), false);
      return true;
    },
  );
});
