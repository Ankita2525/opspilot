/**
 * Display helpers for live-run provenance and incident revision labels.
 * Prefer real backend / session state; never invent revision or approval values.
 */

export function incidentRevisionMetaLabel(): string {
  // service_revision / approval.version are the incident (faulty) revision.
  // No separate current-runtime revision field exists in the UI contract yet.
  return "Incident revision";
}

export type HumanApprovalLabel =
  | "REQUIRED"
  | "APPROVED"
  | "REJECTED"
  | "N/A";

/**
 * Map provenance remediation fields + known frontend approval outcome.
 *
 * Backend RemediationProvenance has no rejected marker: after reject it still
 * has approval_required=true and approved_at=null. Use phase / approval_status
 * from the approval API when available so the panel is not stuck on REQUIRED.
 */
export function humanApprovalLabel(input: {
  approvalRequired?: boolean | null;
  approvedAt?: string | null;
  /** IncidentApprovalResponse.approval_status when known */
  approvalStatus?: string | null;
  /** Command-center phase after approve/reject */
  phase?: string | null;
}): HumanApprovalLabel {
  const status = (input.approvalStatus ?? "").toLowerCase();
  if (input.phase === "rejected" || status === "rejected") {
    return "REJECTED";
  }
  if (
    input.phase === "resolved" ||
    status === "approved" ||
    Boolean(input.approvedAt)
  ) {
    return "APPROVED";
  }
  if (input.approvalRequired) {
    return "REQUIRED";
  }
  return "N/A";
}

/** Ignore provenance payloads that belong to a previous incident. */
export function provenanceMatchesIncident(
  provenanceIncidentId: string | null | undefined,
  activeIncidentId: string | null | undefined,
): boolean {
  if (!provenanceIncidentId || !activeIncidentId) {
    return false;
  }
  return provenanceIncidentId === activeIncidentId;
}

type ProvenanceIdentity = {
  incident_id: string;
  service?: string | null;
  service_revision?: string | null;
  evidence_manifest_hash?: string | null;
  diagnosis?: { model?: string | null } | null;
  remediation?: {
    approval_required?: boolean;
    approved_at?: string | null;
  } | null;
};

/**
 * Renderable provenance for the active incident only.
 * Early failures (no incident id yet / no provenance yet) must return null —
 * never the prior incident's Checkout (or any other) payload.
 */
export function selectRenderableProvenance<T extends ProvenanceIdentity>(
  provenance: T | null | undefined,
  activeIncidentId: string | null | undefined,
): T | null {
  if (!provenance) {
    return null;
  }
  if (!provenanceMatchesIncident(provenance.incident_id, activeIncidentId)) {
    return null;
  }
  return provenance;
}
