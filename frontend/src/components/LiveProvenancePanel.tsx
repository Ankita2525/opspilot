import type { LiveProvenance } from "@/lib/types";

type Props = {
  provenance: LiveProvenance | null;
  loading?: boolean;
};

type ProvenanceRecord = LiveProvenance & {
  fallback_used?: boolean;
  primary_model?: string | null;
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

export function LiveProvenancePanel({ provenance, loading }: Props) {
  const extended = provenance as ProvenanceRecord | null;
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
            {row("Diagnosis provider", provenance.diagnosis?.provider ?? null)}
            {row("Model", provenance.diagnosis?.model ?? null, true)}
            {extended?.fallback_used
              ? row("Primary model", extended.primary_model ?? null, true)
              : null}
            {extended?.fallback_used
              ? row("Fallback model", extended.fallback_model ?? null, true)
              : null}
            {extended?.fallback_used
              ? row("Fallback reason", extended.fallback_reason ?? null)
              : null}
            {extended?.fallback_used
              ? row(
                  "Final model",
                  extended.final_model ?? provenance.diagnosis?.model ?? null,
                  true,
                )
              : null}
          </dl>
          <dl className="provenance-group">
            {row(
              "Human approval",
              provenance.remediation?.approval_required
                ? provenance.remediation.approved_at
                  ? "APPROVED"
                  : "REQUIRED"
                : "N/A",
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
