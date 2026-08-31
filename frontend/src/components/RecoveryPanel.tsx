import { formatErrorRate, formatLatency } from "@/lib/labels";
import type { IncidentApprovalResponse, Metrics } from "@/lib/types";

type RecoveryPanelProps = {
  original: Metrics;
  approval: IncidentApprovalResponse;
};

export function RecoveryPanel({ original, approval }: RecoveryPanelProps) {
  if (approval.status === "resolved") {
    const recoveredLatency =
      approval.recovered_p95_latency_ms ?? original.p95_latency_ms;
    const recoveredErrorRate =
      approval.recovered_error_rate_percent ?? original.error_rate_percent;

    return (
      <section className="panel recovery-panel" aria-labelledby="recovery-heading">
        <h2 id="recovery-heading">Recovery</h2>
        <p className="recovery-lead">Rollback executed successfully.</p>
        <dl className="recovery-metrics">
          <div>
            <dt>p95 latency</dt>
            <dd>
              <span className="sr-only">
                changed from {formatLatency(original.p95_latency_ms)} to{" "}
                {formatLatency(recoveredLatency)}
              </span>
              <span aria-hidden="true">
                {formatLatency(original.p95_latency_ms)} →{" "}
                {formatLatency(recoveredLatency)}
              </span>
            </dd>
          </div>
          <div>
            <dt>Error rate</dt>
            <dd>
              <span className="sr-only">
                changed from {formatErrorRate(original.error_rate_percent)} to{" "}
                {formatErrorRate(recoveredErrorRate)}
              </span>
              <span aria-hidden="true">
                {formatErrorRate(original.error_rate_percent)} →{" "}
                {formatErrorRate(recoveredErrorRate)}
              </span>
            </dd>
          </div>
        </dl>
      </section>
    );
  }

  return (
    <section className="panel recovery-panel rejected" aria-labelledby="recovery-heading">
      <h2 id="recovery-heading">Remediation rejected</h2>
      <p className="recovery-lead">No production changes were made.</p>
      <p>
        {original.service} remains degraded at{" "}
        {formatLatency(original.p95_latency_ms)} p95 latency and{" "}
        {formatErrorRate(original.error_rate_percent)} error rate.
      </p>
    </section>
  );
}
