import { formatErrorRate, formatLatency } from "@/lib/labels";

type Window = {
  p95_latency_ms: number;
  error_rate_percent: number;
  sample_count?: number;
} | null;

type Props = {
  baseline: Window;
  degraded: Window;
  recovery: Window;
  mode: string;
};

function Band({
  label,
  window,
  tone,
}: {
  label: string;
  window: Window;
  tone: "neutral" | "incident" | "success";
}) {
  return (
    <div className={`telemetry-band telemetry-band-${tone}`}>
      <p className="telemetry-band-label">{label}</p>
      {window ? (
        <>
          <p className="telemetry-band-metric">
            p95 {formatLatency(window.p95_latency_ms)}
          </p>
          <p className="telemetry-band-metric">
            errors {formatErrorRate(window.error_rate_percent)}
          </p>
          {window.sample_count !== undefined ? (
            <p className="telemetry-band-samples">{window.sample_count} samples</p>
          ) : null}
        </>
      ) : (
        <p className="telemetry-band-unavailable">Not collected</p>
      )}
    </div>
  );
}

export function TelemetryBands({ baseline, degraded, recovery, mode }: Props) {
  if (mode !== "live") {
    return (
      <section className="panel" aria-label="Telemetry">
        <h2>Telemetry</h2>
        <p className="inspection-caption">
          Deterministic reference evaluation uses fixture telemetry — not live runtime
          observations.
        </p>
      </section>
    );
  }
  return (
    <section className="panel" aria-label="Live telemetry">
      <h2>Live telemetry</h2>
      <div className="telemetry-bands">
        <Band label="Baseline" window={baseline} tone="neutral" />
        <Band label="Degraded" window={degraded} tone="incident" />
        <Band label="Recovery" window={recovery} tone="success" />
      </div>
    </section>
  );
}
