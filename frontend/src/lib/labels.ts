const STEP_LABELS: Record<string, string> = {
  inspect_metrics: "Queried service metrics",
  inspect_deployments: "Checked recent deployments",
  inspect_logs: "Inspected application logs",
  generate_hypothesis: "Generated root-cause hypothesis",
  complete_investigation: "Investigation complete",
};

const ACRONYMS = new Set(["db", "api", "http", "slo", "p95"]);

export function labelForStep(step: string): string {
  return STEP_LABELS[step] ?? humanizeIdentifier(step);
}

export function humanizeIdentifier(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (ACRONYMS.has(lower)) {
        return lower.toUpperCase();
      }
      if (index === 0) {
        return lower.charAt(0).toUpperCase() + lower.slice(1);
      }
      return lower;
    })
    .join(" ");
}

export function formatConfidence(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export function formatLatency(ms: number): string {
  return `${ms} ms`;
}

export function formatErrorRate(percent: number): string {
  return `${percent}%`;
}

export function riskLabel(riskLevel: string): string {
  if (riskLevel.toLowerCase() === "high_risk" || riskLevel.toLowerCase() === "high") {
    return "HIGH";
  }
  return humanizeIdentifier(riskLevel).toUpperCase();
}
