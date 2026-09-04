import { humanizeIdentifier, riskLabel } from "@/lib/labels";
import type { ApprovalRequest } from "@/lib/types";

type ApprovalPanelProps = {
  approvalRequest: ApprovalRequest;
  recommendedAction: string | null;
  proposedVersion: string | null;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
};

export function ApprovalPanel({
  approvalRequest,
  recommendedAction,
  proposedVersion,
  busy,
  onApprove,
  onReject,
}: ApprovalPanelProps) {
  const actionLabel = humanizeIdentifier(
    recommendedAction ?? approvalRequest.action,
  );
  const version = proposedVersion ?? approvalRequest.version;

  return (
    <section
      className="panel approval-panel"
      aria-labelledby="approval-heading"
    >
      <div className="approval-layout">
        <div className="approval-copy">
          <p className="approval-kicker">Human approval required</p>
          <h2 id="approval-heading" className="sr-only">
            Human approval required
          </h2>
          <p className="skills-heading">Proposed remediation</p>
          <p className="approval-action">
            {actionLabel} {version}
          </p>
          <p className="approval-risk">
            <span className="risk-badge">
              {riskLabel(approvalRequest.risk_level)}
            </span>
            <span>Risk</span>
          </p>
          <p className="approval-explain">
            Production state will not change until this action is explicitly
            approved.
          </p>
          {approvalRequest.message ? (
            <p className="approval-message">{approvalRequest.message}</p>
          ) : null}
        </div>
        <div className="approval-actions">
          <button
            type="button"
            className="button-approve"
            onClick={onApprove}
            disabled={busy}
            aria-busy={busy}
          >
            {busy ? "Submitting…" : "Approve rollback"}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={onReject}
            disabled={busy}
          >
            Reject
          </button>
        </div>
      </div>
    </section>
  );
}
