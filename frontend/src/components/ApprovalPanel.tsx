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
    <section className="panel approval-panel" aria-labelledby="approval-heading">
      <h2 id="approval-heading">Recommended action</h2>
      <p className="approval-action">
        {actionLabel} {version}
      </p>
      <p className="approval-risk">
        <span className="risk-badge">{riskLabel(approvalRequest.risk_level)}</span>
        <span>Risk</span>
      </p>
      <p className="approval-explain">
        This action changes production state and requires human approval.
      </p>
      <p className="approval-message">{approvalRequest.message}</p>
      <div className="approval-actions">
        <button
          type="button"
          className="button-primary"
          onClick={onApprove}
          disabled={busy}
          aria-busy={busy}
        >
          {busy ? "Submitting…" : "Approve Rollback"}
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
    </section>
  );
}
