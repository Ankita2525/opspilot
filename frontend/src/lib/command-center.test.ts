import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { lifecycleSteps, resolveLabStatus } from "./command-center.ts";

describe("resolveLabStatus", () => {
  it("maps cold start loading to starting", () => {
    assert.equal(
      resolveLabStatus({
        phase: "loading",
        sandboxState: null,
        telemetryMode: "live",
        investigating: false,
      }),
      "starting",
    );
  });

  it("maps live investigating to warming", () => {
    assert.equal(
      resolveLabStatus({
        phase: "investigating",
        sandboxState: null,
        telemetryMode: "live",
        investigating: true,
      }),
      "warming",
    );
  });

  it("distinguishes reference mode label path", () => {
    assert.equal(
      resolveLabStatus({
        phase: "ready",
        sandboxState: null,
        telemetryMode: "reference",
        investigating: false,
      }),
      "ready",
    );
  });

  it("maps offline sandbox", () => {
    assert.equal(
      resolveLabStatus({
        phase: "ready",
        sandboxState: "live_environment_offline",
        telemetryMode: "live",
        investigating: false,
      }),
      "offline",
    );
  });
});

describe("lifecycleSteps", () => {
  it("marks recovery done when resolved", () => {
    const steps = lifecycleSteps({
      phase: "resolved",
      hasBaseline: true,
      hasHypothesis: true,
      hasApproval: true,
      resolved: true,
    });
    assert.equal(steps.at(-1)?.state, "done");
  });
});
