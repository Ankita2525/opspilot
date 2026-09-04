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
        <dl className="telemetry-band-metrics">
          <div>
            <dt>p95</dt>
            <dd>{formatLatency(window.p95_latency_ms)}</dd>
          </div>
          <div>
            <dt>Error rate</dt>
            <dd>{formatErrorRate(window.error_rate_percent)}</dd>
          </div>
          {window.sample_count !== undefined ? (
            <div>
              <dt>Samples</dt>
              <dd>{window.sample_count}</dd>
            </div>
          ) : null}
        </dl>
      ) : (
        <p className="telemetry-band-unavailable">Not collected</p>
      )}
    </div>
  );
}

function TrendSparkline({
  baseline,
  degraded,
  recovery,
}: {
  baseline: Window;
  degraded: Window;
  recovery: Window;
}) {
  const labeled: Array<{ label: string; value: number }> = [];
  if (baseline) {
    labeled.push({ label: "Baseline", value: baseline.p95_latency_ms });
  }
  if (degraded) {
    labeled.push({ label: "Degraded", value: degraded.p95_latency_ms });
  }
  if (recovery) {
    labeled.push({ label: "Recovery", value: recovery.p95_latency_ms });
  }
  if (labeled.length < 2) {
    return null;
  }

  const width = 220;
  const height = 48;
  const pad = 4;
  const values = labeled.map((item) => item.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  const coords = labeled.map((item, index) => {
    const x =
      pad + (index * (width - pad * 2)) / Math.max(labeled.length - 1, 1);
    const y = height - pad - ((item.value - min) / span) * (height - pad * 2);
    return { ...item, x, y };
  });

  return (
    <div className="telemetry-trend">
      <p className="telemetry-trend-label" id="telemetry-trend-label">
        p95 across collected windows
      </p>
      <svg
        className="telemetry-sparkline"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby="telemetry-trend-label"
      >
        <title>p95 across collected windows</title>
        <polyline
          points={coords.map((point) => `${point.x},${point.y}`).join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        {coords.map((point, index) => (
          <g key={point.label} className="spark-hit">
            <circle
              cx={point.x}
              cy={point.y}
              r="8"
              className="spark-hit-area"
              tabIndex={0}
              role="img"
              aria-label={`${point.label}: ${formatLatency(point.value)}`}
            >
              <title>
                {point.label}: {formatLatency(point.value)}
              </title>
            </circle>
            <circle
              cx={point.x}
              cy={point.y}
              r="3.2"
              className={`spark-point spark-point-${index}`}
              aria-hidden="true"
            />
          </g>
        ))}
      </svg>
      <p className="telemetry-trend-caption type-mono">
        {coords
          .map((point) => `${point.label} ${formatLatency(point.value)}`)
          .join(" → ")}
      </p>
    </div>
  );
}

export function TelemetryBands({ baseline, degraded, recovery, mode }: Props) {
  if (mode !== "live") {
    return (
      <section className="panel telemetry-panel" aria-label="Telemetry">
        <h2>Telemetry</h2>
        <p className="inspection-caption">
          Deterministic reference evaluation uses fixture telemetry — not live
          runtime observations.
        </p>
      </section>
    );
  }
  return (
    <section className="panel telemetry-panel" aria-label="Live telemetry">
      <div className="telemetry-panel-head">
        <h2>Live telemetry</h2>
        <TrendSparkline
          baseline={baseline}
          degraded={degraded}
          recovery={recovery}
        />
      </div>
      <div className="telemetry-bands">
        <Band label="Baseline" window={baseline} tone="neutral" />
        <Band label="Degraded" window={degraded} tone="incident" />
        <Band label="Recovery" window={recovery} tone="success" />
      </div>
    </section>
  );
}
