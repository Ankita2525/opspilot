import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  humanApprovalLabel,
  incidentRevisionMetaLabel,
  provenanceMatchesIncident,
  selectRenderableProvenance,
} from "./provenance-display.ts";

describe("incidentRevisionMetaLabel", () => {
  it("always labels the field as Incident revision", () => {
    assert.equal(incidentRevisionMetaLabel(), "Incident revision");
  });

  it("does not use Current revision for resolved incidents", () => {
    assert.notEqual(incidentRevisionMetaLabel(), "Current revision");
  });
});

describe("humanApprovalLabel", () => {
  it("shows REQUIRED while waiting for human decision", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: null,
        phase: "active",
      }),
      "REQUIRED",
    );
  });

  it("shows APPROVED when provenance has approved_at", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: "2026-09-04T06:26:53Z",
        phase: "resolved",
      }),
      "APPROVED",
    );
  });

  it("shows APPROVED from approval_status even before approved_at lands", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: null,
        approvalStatus: "approved",
        phase: "resolved",
      }),
      "APPROVED",
    );
  });

  it("shows REJECTED from phase when backend still has REQUIRED shape", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: null,
        phase: "rejected",
      }),
      "REJECTED",
    );
  });

  it("keeps APPROVED when approved_at exists even if phase is rejected/failed", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: "2026-09-04T06:26:53Z",
        approvalStatus: "approved",
        phase: "rejected",
      }),
      "APPROVED",
    );
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: "2026-09-04T06:26:53Z",
        approvalStatus: "approved",
        phase: "failed",
      }),
      "APPROVED",
    );
  });

  it("shows REJECTED from approval_status", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: true,
        approvedAt: null,
        approvalStatus: "rejected",
        phase: "active",
      }),
      "REJECTED",
    );
  });

  it("shows N/A when approval was not required", () => {
    assert.equal(
      humanApprovalLabel({
        approvalRequired: false,
        approvedAt: null,
        phase: "complete",
      }),
      "N/A",
    );
  });
});

describe("cross-incident provenance invariants", () => {
  it("clears A when B becomes the active incident", () => {
    const incidentA = {
      incident_id: "inc_a",
      service: "checkout-api",
      service_revision: "v1.18.3",
    };
    assert.equal(
      provenanceMatchesIncident(incidentA.incident_id, "inc_b"),
      false,
    );
  });

  it("accepts only B provenance for B", () => {
    assert.equal(provenanceMatchesIncident("inc_b", "inc_b"), true);
    assert.equal(provenanceMatchesIncident("inc_a", "inc_b"), false);
  });

  it("covers resolved/rejected/complete → new run transitions", () => {
    for (const priorPhase of ["resolved", "rejected", "complete"] as const) {
      assert.equal(
        provenanceMatchesIncident("inc_prior", null),
        false,
        `stale provenance must not render after ${priorPhase} → new run`,
      );
    }
  });

  it("early Payments failure never renders prior Checkout provenance", () => {
    const checkoutProvenance = {
      incident_id: "inc_checkout",
      service: "checkout-api",
      service_revision: "v1.18.3",
      evidence_manifest_hash: "abcdef1234567890",
      diagnosis: { model: "openai/gpt-oss-120b" },
      remediation: {
        approval_required: true,
        approved_at: "2026-09-04T06:26:53Z",
      },
    };

    assert.equal(selectRenderableProvenance(checkoutProvenance, null), null);
    assert.equal(
      selectRenderableProvenance(checkoutProvenance, "inc_payments"),
      null,
    );
    assert.equal(selectRenderableProvenance(null, "inc_payments"), null);
    assert.equal(selectRenderableProvenance(null, null), null);
  });
});
