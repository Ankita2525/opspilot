type MetricTone = "incident" | "neutral" | "success";

type MetricCardProps = {
  label: string;
  value: string;
  hint?: string;
  tone?: MetricTone;
};

export function MetricCard({
  label,
  value,
  hint,
  tone = "neutral",
}: MetricCardProps) {
  return (
    <article className={`metric-card metric-card-${tone}`}>
      <h3 className="metric-label">{label}</h3>
      <p className="metric-value">{value}</p>
      {hint ? <p className="metric-hint">{hint}</p> : null}
    </article>
  );
}
