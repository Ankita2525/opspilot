import { humanizeServiceName } from "@/lib/labels";

export type LabStatus =
  | "offline"
  | "starting"
  | "warming"
  | "ready"
  | "busy"
  | "degraded"
  | "investigating";

type Props = {
  labStatus: LabStatus;
  telemetryMode: string;
  service?: string;
  title?: string;
  revision?: string | null;
  phase?: string;
};

const STATUS_LABEL: Record<LabStatus, string> = {
  offline: "LIVE ENVIRONMENT UNAVAILABLE",
  starting: "WARMING UP LIVE INCIDENT LAB",
  warming: "WARMING SANDBOX",
  ready: "LIVE LAB READY",
  busy: "BUSY",
  degraded: "DEGRADED",
  investigating: "INVESTIGATING",
};

function statusTone(labStatus: LabStatus): "healthy" | "info" | "warn" | "error" | "muted" {
  if (labStatus === "ready") {
    return "healthy";
  }
  if (labStatus === "degraded" || labStatus === "busy") {
    return "error";
  }
  if (labStatus === "offline") {
    return "muted";
  }
  if (labStatus === "investigating" || labStatus === "warming" || labStatus === "starting") {
    return "info";
  }
  return "info";
}

export function CommandCenterHeader({
  labStatus,
  telemetryMode,
  service,
  title,
  revision,
  phase,
}: Props) {
  const isLive = telemetryMode === "live";
  const tone = statusTone(labStatus);

  return (
    <header className="command-header">
      <div className="command-header-top">
        <div className="command-brand">
          <span className="command-brand-mark" aria-hidden="true">
            <svg viewBox="0 0 28 28" width="28" height="28" fill="none">
              <rect
                x="2"
                y="2"
                width="24"
                height="24"
                rx="7"
                stroke="currentColor"
                strokeWidth="1.5"
              />
              <circle cx="14" cy="14" r="4.5" fill="currentColor" />
              <path
                d="M14 6v3.2M14 18.8V22M6 14h3.2M18.8 14H22"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <p className="command-brand-name">OPSPILOT</p>
        </div>
        <div className="command-status-pills" role="status" aria-live="polite">
          <span className={`status-pill status-pill-${tone}`}>
            <span className="status-pill-dot status-pill-dot-lab" aria-hidden />
            {STATUS_LABEL[labStatus]}
          </span>
          <span
            className={
              isLive
                ? "status-pill status-pill-live"
                : "status-pill status-pill-muted"
            }
          >
            <span className="status-pill-dot status-pill-dot-telemetry" aria-hidden />
            {isLive ? "LIVE TELEMETRY" : "REFERENCE EVALUATION"}
          </span>
        </div>
      </div>
      {service && title ? (
        <div className="command-hero">
          <p className="command-hero-kicker">Selected incident</p>
          <h2 className="command-hero-title">{title}</h2>
          <dl className="command-hero-meta">
            <div>
              <dt>Service</dt>
              <dd>{humanizeServiceName(service)}</dd>
            </div>
            {revision ? (
              <div>
                <dt>Revision</dt>
                <dd>
                  <code>{revision}</code>
                </dd>
              </div>
            ) : null}
            {phase ? (
              <div>
                <dt>Phase</dt>
                <dd>{phase}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : null}
    </header>
  );
}
