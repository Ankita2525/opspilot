import { humanizeServiceName } from "@/lib/labels";

type IncidentPhase =
  | "investigating"
  | "active"
  | "resolved"
  | "rejected"
  | "failed";

type IncidentHeaderProps = {
  phase: IncidentPhase;
  service: string;
  title: string;
  live?: boolean;
  eventCount?: number;
};

const PHASE_COPY: Record<
  IncidentPhase,
  { eyebrow: string; summary: string }
> = {
  investigating: {
    eyebrow: "Investigating",
    summary: "OpsPilot is gathering evidence. Production is unchanged.",
  },
  active: {
    eyebrow: "Awaiting human approval",
    summary: "High-risk rollback is ready. Production is unchanged until you decide.",
  },
  resolved: {
    eyebrow: "Incident resolved",
    summary: "Service health restored after approved rollback.",
  },
  rejected: {
    eyebrow: "Remediation rejected",
    summary: "No production changes were made.",
  },
  failed: {
    eyebrow: "Investigation incomplete",
    summary: "Investigation could not be completed.",
  },
};

export function IncidentHeader({
  phase,
  service,
  title,
  live = false,
  eventCount,
}: IncidentHeaderProps) {
  const copy = PHASE_COPY[phase];

  return (
    <header className={`incident-header incident-header-${phase}`}>
      <p className="incident-kicker">
        {copy.eyebrow}
        {live ? (
          <span className="live-badge" aria-label="Live investigation">
            LIVE
          </span>
        ) : null}
      </p>
      <h1 className="incident-title">{title}</h1>
      <p className="incident-service">
        <span className="sr-only">Affected service: </span>
        <span className="incident-service-name">
          {humanizeServiceName(service)}
        </span>
        <code>{service}</code>
      </p>
      <p className="incident-summary">{copy.summary}</p>
      {eventCount !== undefined && eventCount > 0 ? (
        <p className="incident-meta">
          {eventCount} {eventCount === 1 ? "event" : "events"}
        </p>
      ) : null}
    </header>
  );
}
