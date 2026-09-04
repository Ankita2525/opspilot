import {
  incidentPhaseLabel,
  incidentPhaseTone,
  type Phase,
} from "@/lib/command-center";
import {
  approvalTerminalKind,
  incidentHeaderSummaryForApproval,
} from "@/lib/approval-outcome";
import { humanizeServiceName } from "@/lib/labels";
import { incidentRevisionMetaLabel } from "@/lib/provenance-display";
import type { IncidentApprovalResponse } from "@/lib/types";

type IncidentPhase =
  | "investigating"
  | "active"
  | "complete"
  | "resolved"
  | "rejected"
  | "failed";

type IncidentHeaderProps = {
  phase: IncidentPhase;
  service: string;
  title: string;
  live?: boolean;
  telemetryMode?: string | null;
  eventCount?: number;
  revision?: string | null;
  approvalResult?: IncidentApprovalResponse | null;
};

const PHASE_SUMMARY: Record<IncidentPhase, string> = {
  investigating: "OpsPilot is gathering evidence. Production is unchanged.",
  active:
    "High-risk rollback is ready. Production is unchanged until you decide.",
  complete:
    "Investigation complete. No supported automated remediation was selected. Production remains unchanged.",
  resolved: "Service health restored after approved rollback.",
  rejected: "No production changes were made.",
  failed: "Investigation could not be completed.",
};

export function IncidentHeader({
  phase,
  service,
  title,
  live = false,
  telemetryMode = null,
  eventCount,
  revision = null,
  approvalResult = null,
}: IncidentHeaderProps) {
  const tone = incidentPhaseTone(phase as Phase);
  const liveSandbox =
    telemetryMode === "live" ? "LIVE SANDBOX" : live ? "LIVE" : null;
  const kind = approvalResult ? approvalTerminalKind(approvalResult) : null;
  const summary =
    incidentHeaderSummaryForApproval(phase as Phase, approvalResult) ??
    PHASE_SUMMARY[phase];
  const phaseLabel =
    kind === "approved_unverified"
      ? "RECOVERY UNVERIFIED"
      : incidentPhaseLabel(phase as Phase);

  return (
    <header className={`incident-command incident-command-${tone}`}>
      <div className="incident-command-main">
        <p className="incident-command-service">
          {humanizeServiceName(service)}
        </p>
        <h1 className="incident-command-title">{title}</h1>
        <p className="incident-command-summary">{summary}</p>
      </div>
      <dl className="incident-command-meta">
        <div>
          <dt>Service</dt>
          <dd>
            <code>{service}</code>
          </dd>
        </div>
        {revision ? (
          <div>
            <dt>{incidentRevisionMetaLabel()}</dt>
            <dd>
              <code>{revision}</code>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Phase</dt>
          <dd className={`incident-phase-pill incident-phase-${tone}`}>
            {phaseLabel}
          </dd>
        </div>
        {liveSandbox ? (
          <div>
            <dt>Runtime</dt>
            <dd className="incident-live-pill">{liveSandbox}</dd>
          </div>
        ) : null}
        {telemetryMode === "live" ? (
          <div>
            <dt>Telemetry</dt>
            <dd className="incident-live-pill incident-live-pill-blue">
              LIVE TELEMETRY
            </dd>
          </div>
        ) : null}
        {eventCount !== undefined && eventCount > 0 ? (
          <div>
            <dt>Events</dt>
            <dd className="type-mono">{eventCount}</dd>
          </div>
        ) : null}
      </dl>
    </header>
  );
}
