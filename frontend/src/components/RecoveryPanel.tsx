import { formatErrorRate, formatLatency } from "@/lib/labels";
import type { IncidentApprovalResponse, Metrics } from "@/lib/types";

type RecoveryPanelProps = {
  original: Metrics;
  approval: IncidentApprovalResponse;
  freshTelemetryVerified?: boolean | null;
};

export function RecoveryPanel({
  original,
  approval,
  freshTelemetryVerified = null,
}: RecoveryPanelProps) {
  if (approval.status === "resolved") {
    const hasAfter =
      approval.recovered_p95_latency_ms !== null &&
      approval.recovered_p95_latency_ms !== undefined;
    const recoveredLatency = approval.recovered_p95_latency_ms;
    const recoveredErrorRate = approval.recovered_error_rate_percent;

    return (
      <section
        className={`panel recovery-panel ${hasAfter ? "recovery-panel-verified" : "recovery-panel-verifying"}`}
        aria-labelledby="recovery-heading"
      >
        <p className="type-kicker">
          {hasAfter ? "Rollback executed" : "Verifying recovery"}
        </p>
        <h2 id="recovery-heading" className="recovery-title">
          {hasAfter ? "Recovery verified" : "Collecting fresh telemetry"}
        </h2>
        <div className="recovery-compare">
          <div className="recovery-before">
            <p className="skills-heading">Before</p>
            <p className="recovery-metric type-mono">
              p95 {formatLatency(original.p95_latency_ms)}
            </p>
            <p className="recovery-metric type-mono">
              errors {formatErrorRate(original.error_rate_percent)}
            </p>
          </div>
          <div className="recovery-after">
            <p className="skills-heading">After</p>
            {hasAfter && recoveredLatency !== null ? (
              <>
                <p className="recovery-metric type-mono">
                  p95 {formatLatency(recoveredLatency)}
                </p>
                <p className="recovery-metric type-mono">
                  errors{" "}
                  {formatErrorRate(
                    recoveredErrorRate ?? original.error_rate_percent,
                  )}
                </p>
              </>
            ) : (
              <p className="recovery-verifying">Waiting for post-action samples…</p>
            )}
          </div>
        </div>
        <p className="recovery-fresh type-mono">
          Fresh telemetry{" "}
          {freshTelemetryVerified || hasAfter ? "VERIFIED" : "PENDING"}
        </p>
      </section>
    );
  }

  return (
    <section
      className="panel recovery-panel rejected"
      aria-labelledby="recovery-heading"
    >
      <p className="type-kicker">Remediation rejected</p>
      <h2 id="recovery-heading" className="recovery-title">
        No production changes were made
      </h2>
      <p className="recovery-lead">
        {original.service} remains degraded at{" "}
        {formatLatency(original.p95_latency_ms)} p95 latency and{" "}
        {formatErrorRate(original.error_rate_percent)} error rate.
      </p>
    </section>
  );
}
