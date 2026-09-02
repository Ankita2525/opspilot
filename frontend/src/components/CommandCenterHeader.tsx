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
  starting: "STARTING LIVE LAB",
  warming: "WARMING SANDBOX",
  ready: "LIVE LAB READY",
  busy: "BUSY",
  degraded: "DEGRADED",
  investigating: "INVESTIGATING",
};

export function CommandCenterHeader({
  labStatus,
  telemetryMode,
  service,
  title,
  revision,
  phase,
}: Props) {
  const isLive = telemetryMode === "live";
  return (
    <header className="command-header">
      <div className="command-header-top">
        <div>
          <p className="command-kicker">OpsPilot</p>
          <h1 className="command-title">Autonomous Production Engineering Agent</h1>
        </div>
        <div className="command-status-block">
          <p className="command-status-label">Live lab status</p>
          <p className={`command-status command-status-${labStatus}`}>
            <span className="status-pulse" aria-hidden />
            {STATUS_LABEL[labStatus]}
          </p>
          <p className="command-mode">
            {isLive ? "LIVE TELEMETRY" : "REFERENCE EVALUATION"}
          </p>
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
