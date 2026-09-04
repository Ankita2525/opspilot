/**
 * Map approval API results to truthful terminal UI — never conflate
 * "approved but recovery unverified" with human rejection.
 */

import type { Phase } from "@/lib/command-center-types";
import type { IncidentApprovalResponse } from "@/lib/types";

export type ApprovalTerminalKind =
  | "rejected"
  | "resolved"
  | "approved_unverified"
  | "unknown";

export function approvalTerminalKind(
  result: Pick<
    IncidentApprovalResponse,
    "status" | "approval_status" | "resolved" | "execution_success"
  >,
): ApprovalTerminalKind {
  const approval = (result.approval_status ?? "").toLowerCase();
  const status = (result.status ?? "").toLowerCase();

  if (approval === "rejected" || status === "rejected") {
    return "rejected";
  }
  if (result.resolved === true || status === "resolved") {
    return "resolved";
  }
  if (
    approval === "approved" ||
    result.execution_success === true ||
    status === "remediation_failed" ||
    status === "remediation_executed"
  ) {
    return "approved_unverified";
  }
  return "unknown";
}

/**
 * Terminal command-center phase after submitApproval.
 * Rejected ONLY for true human rejection — never for remediation_failed.
 */
export function phaseFromApprovalResponse(
  result: IncidentApprovalResponse,
): Phase {
  switch (approvalTerminalKind(result)) {
    case "rejected":
      return "rejected";
    case "resolved":
      return "resolved";
    case "approved_unverified":
      // Keep workspace open with recovery/failure context; not a rejection.
      return "failed";
    default:
      return "failed";
  }
}

export function incidentHeaderSummaryForApproval(
  phase: Phase,
  result: IncidentApprovalResponse | null,
): string | null {
  if (!result) {
    return null;
  }
  const kind = approvalTerminalKind(result);
  if (kind === "approved_unverified" || (phase === "failed" && kind !== "rejected")) {
    if (result.execution_success) {
      return "Approved rollback was executed, but recovery was not verified.";
    }
    return "Approval was recorded, but remediation did not complete successfully.";
  }
  return null;
}

/** Cross-incident invariant helper for tests and UI guards. */
export function approvalStateBelongsToIncident(
  approvalIncidentId: string | null | undefined,
  activeIncidentId: string | null | undefined,
): boolean {
  if (!approvalIncidentId || !activeIncidentId) {
    return false;
  }
  return approvalIncidentId === activeIncidentId;
}
