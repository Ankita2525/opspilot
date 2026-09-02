import type { LiveProvenance } from "@/lib/types";

type Props = {
  provenance: LiveProvenance | null;
  loading?: boolean;
};

function row(label: string, value: string | number | boolean | null | undefined) {
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
      <dd>{display}</dd>
    </div>
  );
}

export function LiveProvenancePanel({ provenance, loading }: Props) {
  return (
    <section className="panel provenance-panel" aria-label="Live run provenance">
      <h2>Live run provenance</h2>
      {loading ? (
        <p className="status-copy">Loading provenance…</p>
      ) : !provenance ? (
        <p className="status-copy">Provenance available after live investigation starts.</p>
      ) : (
        <dl className="provenance-grid">
          {row("Telemetry mode", provenance.telemetry_mode.toUpperCase())}
          {row("Environment", provenance.environment)}
          {row("Service", provenance.service)}
          {row("Revision", provenance.service_revision)}
          {row("Baseline samples", provenance.baseline?.sample_count ?? null)}
          {row("Degraded samples", provenance.degraded?.sample_count ?? null)}
          {row("Recovery samples", provenance.recovery?.sample_count ?? null)}
          {row(
            "Latest metric",
            provenance.recovery?.latest_metric_timestamp ?? null,
          )}
          {row("Diagnosis provider", provenance.diagnosis?.provider ?? null)}
          {row(
            "Ground truth",
            provenance.ground_truth_visible_to_agent ? "VISIBLE" : "HIDDEN FROM AGENT ✓",
          )}
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
          )}
        </dl>
      )}
    </section>
  );
}
