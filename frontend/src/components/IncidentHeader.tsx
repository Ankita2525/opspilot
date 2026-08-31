import { humanizeServiceName } from "@/lib/labels";

type IncidentPhase = "ready" | "active" | "resolved" | "rejected";

type IncidentHeaderProps = {
  phase: IncidentPhase;
  service: string;
  title: string;
};

const PHASE_COPY: Record<
  IncidentPhase,
  { eyebrow: string; summary: string }
> = {
  ready: {
    eyebrow: "Ready to investigate",
    summary: "OpsPilot will gather evidence before anything changes in production.",
  },
  active: {
    eyebrow: "Incident active",
    summary: "Elevated latency and error rate",
  },
  resolved: {
    eyebrow: "Incident resolved",
    summary: "Service health restored after approved rollback.",
  },
  rejected: {
    eyebrow: "Remediation rejected",
    summary: "No production changes were made.",
  },
};

export function IncidentHeader({ phase, service, title }: IncidentHeaderProps) {
  const copy = PHASE_COPY[phase];

  return (
    <header className={`incident-header incident-header-${phase}`}>
      <p className="incident-kicker">{copy.eyebrow}</p>
      <h1 className="incident-title">{title}</h1>
      <p className="incident-service">
        <span className="sr-only">Affected service: </span>
        <span className="incident-service-name">
          {humanizeServiceName(service)}
        </span>
        <code>{service}</code>
      </p>
      <p className="incident-summary">{copy.summary}</p>
    </header>
  );
}
