import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  classifyPreIncidentRejection,
  extractBackendErrorCode,
  PreIncidentStartError,
} from "./start-rejection.ts";

describe("classifyPreIncidentRejection", () => {
  it("maps 429 session_live_incident_limit to start-screen quota UX", () => {
    const plan = classifyPreIncidentRejection({
      status: 429,
      errorCode: "session_live_incident_limit",
    });
    assert.equal(plan.enterFailedWorkspace, false);
    assert.equal(plan.disableStart, true);
    assert.equal(plan.showRetry, false);
    assert.equal(plan.remountTurnstile, false);
    assert.equal(plan.preserveSelectedScenario, true);
    assert.match(plan.message, /Live demo limit reached/i);
    assert.ok(plan.detail);
  });

  it("maps 409 sandbox_busy to pre-incident busy message", () => {
    const plan = classifyPreIncidentRejection({
      status: 409,
      errorCode: "sandbox_busy",
    });
    assert.equal(plan.enterFailedWorkspace, false);
    assert.equal(plan.code, "sandbox_busy");
    assert.match(plan.message, /busy/i);
    assert.equal(plan.remountTurnstile, true);
  });

  it("maps 403 Turnstile failure to fresh-challenge UX", () => {
    const plan = classifyPreIncidentRejection({
      status: 403,
      errorCode: "turnstile_verification_failed",
    });
    assert.equal(plan.enterFailedWorkspace, false);
    assert.equal(plan.remountTurnstile, true);
    assert.equal(plan.disableStart, false);
    assert.match(plan.message, /Cloudflare/i);
  });

  it("maps 429 rate_limit_exceeded to pre-incident rate-limit UX", () => {
    const plan = classifyPreIncidentRejection({
      status: 429,
      errorCode: "rate_limit_exceeded",
    });
    assert.equal(plan.enterFailedWorkspace, false);
    assert.equal(plan.code, "rate_limit_exceeded");
    assert.match(plan.message, /Too many requests/i);
    assert.equal(plan.remountTurnstile, true);
  });

  it("never marks known start gates as Failed lifecycle", () => {
    for (const input of [
      { status: 429, errorCode: "session_live_incident_limit" },
      { status: 429, errorCode: "rate_limit_exceeded" },
      { status: 409, errorCode: "sandbox_busy" },
      { status: 403, errorCode: "turnstile_verification_failed" },
    ] as const) {
      assert.equal(
        classifyPreIncidentRejection(input).enterFailedWorkspace,
        false,
      );
    }
  });
});

describe("extractBackendErrorCode", () => {
  it("reads FastAPI detail.error", () => {
    assert.equal(
      extractBackendErrorCode({
        detail: { error: "session_live_incident_limit", retry_after_seconds: 3600 },
      }),
      "session_live_incident_limit",
    );
  });
});

describe("PreIncidentStartError", () => {
  it("retains status and backend code", () => {
    const plan = classifyPreIncidentRejection({
      status: 429,
      errorCode: "session_live_incident_limit",
    });
    const err = new PreIncidentStartError(plan);
    assert.equal(err.status, 429);
    assert.equal(err.code, "session_live_incident_limit");
    assert.equal(err.kind, "pre_incident");
  });
});

describe("actual incident failure vs pre-incident", () => {
  it("keeps enterFailedWorkspace false for start gates only", () => {
    assert.equal(
      classifyPreIncidentRejection({
        status: 429,
        errorCode: "session_live_incident_limit",
      }).enterFailedWorkspace,
      false,
    );
  });
});
