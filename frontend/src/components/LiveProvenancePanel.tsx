import { humanApprovalLabel } from "@/lib/provenance-display";
import type { LiveProvenance } from "@/lib/types";

type Props = {
  provenance: LiveProvenance | null;
  loading?: boolean;
  /** Command-center phase after approve/reject (backend provenance lacks rejected). */
  phase?: string | null;
  /** IncidentApprovalResponse.approval_status when known. */
  approvalStatus?: string | null;
};

type DiagnosisExtras = {
  fallback_used?: boolean;
  primary_model_attempted?: string | null;
  fallback_model?: string | null;
  fallback_reason?: string | null;
  final_model?: string | null;
};

function row(
  label: string,
  value: string | number | boolean | null | undefined,
  mono = false,
) {
  const display =
    value === null || value === undefined
      ? "Unavailable"
      : typeof value === "boolean"
        ? value
          ? "✓"
          : "—"
        : String(value);
  return (
    <div className="provenance-row">
      <dt>{label}</dt>
      <dd className={mono ? "type-mono" : undefined}>{display}</dd>
    </div>
  );
}

export function LiveProvenancePanel({
  provenance,
  loading,
  phase = null,
  approvalStatus = null,
}: Props) {
  const diagnosis = provenance?.diagnosis as
    | (NonNullable<LiveProvenance["diagnosis"]> & DiagnosisExtras)
    | null
    | undefined;
  const fallbackUsed = diagnosis?.fallback_used === true;

  return (
    <section className="panel provenance-panel" aria-label="Live run provenance">
      <h2>Live run provenance</h2>
      {loading ? (
        <p className="status-copy">Loading provenance…</p>
      ) : !provenance ? (
        <p className="status-copy">
          Provenance available after live investigation starts.
        </p>
      ) : (
        <div className="provenance-groups">
          <dl className="provenance-group">
            {row("Telemetry mode", provenance.telemetry_mode.toUpperCase())}
            {row("Environment", provenance.environment)}
            {row("Service", provenance.service, true)}
          </dl>
          <dl className="provenance-group">
            {row("Incident revision", provenance.service_revision, true)}
            {row("Diagnosis provider", diagnosis?.provider ?? null)}
            {row("Model", diagnosis?.model ?? null, true)}
            {fallbackUsed
              ? row(
                  "Primary model",
                  diagnosis?.primary_model_attempted ?? null,
                  true,
                )
              : null}
            {fallbackUsed
              ? row("Fallback model", diagnosis?.fallback_model ?? null, true)
              : null}
            {fallbackUsed
              ? row("Fallback reason", diagnosis?.fallback_reason ?? null)
              : null}
            {fallbackUsed
              ? row(
                  "Final model",
                  diagnosis?.final_model ?? diagnosis?.model ?? null,
                  true,
                )
              : null}
          </dl>
          <dl className="provenance-group">
            {row(
              "Human approval",
              humanApprovalLabel({
                approvalRequired: provenance.remediation?.approval_required,
                approvedAt: provenance.remediation?.approved_at,
                approvalStatus,
                phase,
              }),
            )}
            {row(
              "Recovery evidence",
              provenance.recovery?.all_samples_post_remediation
                ? "FRESH POST-ACTION TELEMETRY ✓"
                : provenance.recovery?.verified
                  ? "VERIFIED"
                  : null,
            )}
            {row(
              "Evidence manifest",
              provenance.evidence_manifest_hash
                ? `${provenance.evidence_manifest_hash.slice(0, 12)}…`
                : null,
              true,
            )}
            {row(
              "Ground truth",
              provenance.ground_truth_visible_to_agent
                ? "VISIBLE"
                : "HIDDEN FROM AGENT ✓",
            )}
          </dl>
        </div>
      )}
    </section>
  );
}
