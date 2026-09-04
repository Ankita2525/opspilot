import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  canStartLiveIncident,
  consumeTurnstileToken,
  planStartRetry,
} from "./turnstile-start.ts";

describe("canStartLiveIncident", () => {
  it("requires a token when Turnstile is configured", () => {
    assert.equal(
      canStartLiveIncident({
        turnstileRequired: true,
        turnstileToken: null,
      }),
      false,
    );
    assert.equal(
      canStartLiveIncident({
        turnstileRequired: true,
        turnstileToken: "fresh-token",
      }),
      true,
    );
  });

  it("allows start when Turnstile is not configured", () => {
    assert.equal(
      canStartLiveIncident({
        turnstileRequired: false,
        turnstileToken: null,
      }),
      true,
    );
  });
});

describe("planStartRetry", () => {
  it("does not call stream immediately after a pre-incident failure", () => {
    const plan = planStartRetry();
    assert.equal(plan.callStream, false);
    assert.equal(plan.nextAction, "return_to_start");
    assert.equal(plan.remountTurnstile, true);
    assert.equal(plan.clearTurnstileToken, true);
  });

  it("preserves selected scenario", () => {
    assert.equal(planStartRetry().preserveSelectedScenario, true);
  });

  it("clears failed visual / live / provenance state", () => {
    const plan = planStartRetry();
    assert.equal(plan.clearFailedWorkspace, true);
    assert.equal(plan.clearLiveIncidentState, true);
    assert.equal(plan.clearProvenance, true);
  });
});

describe("consumeTurnstileToken", () => {
  it("does not reuse a consumed token", () => {
    const first = consumeTurnstileToken("tok-1");
    assert.equal(first.captured, "tok-1");
    assert.equal(first.remaining, null);

    // Retry sees null remaining — fresh Turnstile required before stream.
    assert.equal(
      canStartLiveIncident({
        turnstileRequired: true,
        turnstileToken: first.remaining,
      }),
      false,
    );

    const plan = planStartRetry();
    assert.equal(plan.callStream, false);

    // After a fresh challenge:
    assert.equal(
      canStartLiveIncident({
        turnstileRequired: true,
        turnstileToken: "tok-2",
      }),
      true,
    );
  });
});
