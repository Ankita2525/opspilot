import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  approvalStateBelongsToIncident,
  approvalTerminalKind,
  incidentHeaderSummaryForApproval,
  phaseFromApprovalResponse,
} from "./approval-outcome.ts";
import type { IncidentApprovalResponse } from "./types.ts";

function approval(
  overrides: Partial<IncidentApprovalResponse>,
): IncidentApprovalResponse {
  return {
    incident_id: "inc_b",
    status: "resolved",
    execution_success: true,
    recovered_p95_latency_ms: 100,
    recovered_error_rate_percent: 0.1,
    resolved: true,
    approval_status: "approved",
    ...overrides,
  };
}

describe("phaseFromApprovalResponse", () => {
  it("maps true human rejection to rejected", () => {
    assert.equal(
      phaseFromApprovalResponse(
        approval({
          status: "rejected",
          approval_status: "rejected",
          execution_success: false,
          resolved: false,
          recovered_p95_latency_ms: null,
          recovered_error_rate_percent: null,
        }),
      ),
      "rejected",
    );
  });

  it("maps verified recovery to resolved", () => {
    assert.equal(phaseFromApprovalResponse(approval({})), "resolved");
  });

  it("does not map approved+remediation_failed to rejected", () => {
    const result = approval({
      incident_id: "inc_auth",
      status: "remediation_failed",
      approval_status: "approved",
      execution_success: true,
      resolved: false,
      recovered_p95_latency_ms: null,
      recovered_error_rate_percent: null,
    });
    assert.equal(approvalTerminalKind(result), "approved_unverified");
    assert.notEqual(phaseFromApprovalResponse(result), "rejected");
    assert.equal(phaseFromApprovalResponse(result), "failed");
  });
});

describe("cross-incident approval isolation", () => {
  it("rejected A cannot leak into approved B", () => {
    const incidentA = approval({
      incident_id: "inc_a",
      status: "rejected",
      approval_status: "rejected",
      execution_success: false,
      resolved: false,
    });
    const incidentB = approval({
      incident_id: "inc_b",
      status: "remediation_failed",
      approval_status: "approved",
      execution_success: true,
      resolved: false,
      recovered_p95_latency_ms: null,
      recovered_error_rate_percent: null,
    });

    assert.equal(phaseFromApprovalResponse(incidentA), "rejected");
    assert.notEqual(phaseFromApprovalResponse(incidentB), "rejected");
    assert.equal(approvalTerminalKind(incidentB), "approved_unverified");
    assert.equal(
      approvalStateBelongsToIncident(incidentA.incident_id, incidentB.incident_id),
      false,
    );
    assert.equal(
      approvalStateBelongsToIncident(incidentB.incident_id, "inc_b"),
      true,
    );
  });

  it("approved A cannot leak into rejected B", () => {
    const incidentA = approval({ incident_id: "inc_a" });
    const incidentB = approval({
      incident_id: "inc_b",
      status: "rejected",
      approval_status: "rejected",
      execution_success: false,
      resolved: false,
    });
    assert.equal(phaseFromApprovalResponse(incidentA), "resolved");
    assert.equal(phaseFromApprovalResponse(incidentB), "rejected");
    assert.equal(
      approvalStateBelongsToIncident(incidentA.incident_id, incidentB.incident_id),
      false,
    );
  });
});

describe("header copy for approved_unverified", () => {
  it("never claims no production changes after executed approve", () => {
    const result = approval({
      status: "remediation_failed",
      approval_status: "approved",
      execution_success: true,
      resolved: false,
      recovered_p95_latency_ms: null,
      recovered_error_rate_percent: null,
    });
    const summary = incidentHeaderSummaryForApproval(
      phaseFromApprovalResponse(result),
      result,
    );
    assert.ok(summary);
    assert.match(summary!, /Approved rollback was executed/);
    assert.doesNotMatch(summary!, /No production changes/);
  });
});
