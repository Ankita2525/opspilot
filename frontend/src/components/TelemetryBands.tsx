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
  const points: number[] = [];
  if (baseline) {
    points.push(baseline.p95_latency_ms);
  }
  if (degraded) {
    points.push(degraded.p95_latency_ms);
  }
  if (recovery) {
    points.push(recovery.p95_latency_ms);
  }
  if (points.length < 2) {
    return null;
  }

  const width = 220;
  const height = 48;
  const pad = 4;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = Math.max(max - min, 1);
  const coords = points.map((value, index) => {
    const x =
      pad + (index * (width - pad * 2)) / Math.max(points.length - 1, 1);
    const y = height - pad - ((value - min) / span) * (height - pad * 2);
    return `${x},${y}`;
  });

  return (
    <div className="telemetry-trend" aria-label="p95 trend across collected windows">
      <p className="telemetry-trend-label">p95 across collected windows</p>
      <svg
        className="telemetry-sparkline"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-hidden="true"
      >
        <polyline
          points={coords.join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        {coords.map((point, index) => {
          const [x, y] = point.split(",").map(Number);
          return <circle key={point} cx={x} cy={y} r="3.2" className={`spark-point spark-point-${index}`} />;
        })}
      </svg>
      <p className="telemetry-trend-caption type-mono">
        {points.map((value) => formatLatency(value)).join(" → ")}
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
