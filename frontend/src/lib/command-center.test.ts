import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  lifecycleSteps,
  resolveLabStatus,
  resolveLifecycleFailureAnchor,
} from "./command-center.ts";

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

  it("includes investigation stage and approval tone", () => {
    const steps = lifecycleSteps({
      phase: "active",
      hasBaseline: true,
      hasHypothesis: true,
      hasApproval: true,
      resolved: false,
    });
    assert.equal(steps.some((step) => step.id === "investigation"), true);
    const approval = steps.find((step) => step.id === "approval");
    assert.equal(approval?.state, "active");
    assert.equal(approval?.tone, "approval");
  });

  it("early failure before baseline uses generic Failed terminal, not approval", () => {
    const steps = lifecycleSteps({
      phase: "failed",
      hasBaseline: false,
      hasHypothesis: false,
      hasApproval: false,
      resolved: false,
    });
    const approval = steps.find((step) => step.id === "approval");
    const failed = steps.find((step) => step.id === "failed");
    assert.equal(approval?.state, "pending");
    assert.equal(failed?.state, "failed");
    assert.equal(failed?.tone, "failed");
  });

  it("failure during investigation marks investigation failed, not approval", () => {
    const steps = lifecycleSteps({
      phase: "failed",
      hasBaseline: true,
      hasHypothesis: false,
      hasApproval: false,
      resolved: false,
      failureStage: "generate_hypothesis",
    });
    assert.equal(
      steps.find((step) => step.id === "investigation")?.state,
      "failed",
    );
    assert.equal(steps.find((step) => step.id === "approval")?.state, "pending");
    assert.equal(steps.find((step) => step.id === "baseline")?.state, "done");
    assert.equal(
      steps.some((step) => step.id === "failed"),
      false,
    );
  });

  it("failure after diagnosis keeps later stages pending with Failed terminal", () => {
    const steps = lifecycleSteps({
      phase: "failed",
      hasBaseline: true,
      hasHypothesis: true,
      hasApproval: false,
      resolved: false,
    });
    assert.equal(steps.find((step) => step.id === "diagnosis")?.state, "done");
    assert.equal(steps.find((step) => step.id === "approval")?.state, "pending");
    assert.equal(steps.find((step) => step.id === "failed")?.state, "failed");
  });
});

describe("resolveLifecycleFailureAnchor", () => {
  it("defaults to generic failed when no progress exists", () => {
    assert.equal(
      resolveLifecycleFailureAnchor({
        hasBaseline: false,
        hasHypothesis: false,
        hasApproval: false,
      }),
      "failed",
    );
  });
});
