import type { Phase } from "@/lib/command-center-types";

export type { Phase };

export type LabStatus =
  | "offline"
  | "starting"
  | "warming"
  | "ready"
  | "busy"
  | "degraded"
  | "investigating";

export type LifecycleStepState = "pending" | "active" | "done" | "failed";

export type LifecycleTone =
  | "default"
  | "investigate"
  | "degraded"
  | "approval"
  | "recovery"
  | "failed";

export type LifecycleStep = {
  id: string;
  label: string;
  state: LifecycleStepState;
  tone: LifecycleTone;
};

export function resolveLabStatus(input: {
  phase: Phase;
  sandboxState: string | null;
  telemetryMode: string;
  investigating: boolean;
}): LabStatus {
  if (input.sandboxState === "live_environment_offline") {
    return "offline";
  }
  if (input.phase === "loading") {
    return "starting";
  }
  if (input.investigating && input.telemetryMode === "live") {
    return input.phase === "investigating" ? "warming" : "investigating";
  }
  if (input.sandboxState === "sandbox_busy") {
    return "busy";
  }
  if (input.phase === "active") {
    return "degraded";
  }
  if (input.phase === "resolved") {
    return "ready";
  }
  return "ready";
}

/**
 * Infer where failure landed from observed progress only.
 * Prefer an explicit backend stage when the UI contract exposes one; otherwise
 * never invent approval/rollback progress.
 */
export function resolveLifecycleFailureAnchor(input: {
  hasBaseline: boolean;
  hasHypothesis: boolean;
  hasApproval: boolean;
  /** Optional backend/SSE stage when available (e.g. generate_hypothesis). */
  failureStage?: string | null;
}): string {
  const stage = (input.failureStage ?? "").toLowerCase();
  if (
    stage === "approval" ||
    stage === "awaiting_approval" ||
    stage === "approval_timeout"
  ) {
    return "approval";
  }
  if (
    stage === "generate_hypothesis" ||
    stage === "diagnosis" ||
    stage === "hypothesis"
  ) {
    return input.hasHypothesis ? "failed" : "investigation";
  }
  if (
    stage === "baseline" ||
    stage === "baseline_collection" ||
    stage === "inspect_metrics"
  ) {
    return input.hasBaseline ? "investigation" : "failed";
  }
  if (input.hasApproval) {
    return "approval";
  }
  if (input.hasHypothesis) {
    return "failed";
  }
  if (input.hasBaseline) {
    return "investigation";
  }
  return "failed";
}

export function lifecycleSteps(input: {
  phase: Phase;
  hasBaseline: boolean;
  hasHypothesis: boolean;
  hasApproval: boolean;
  resolved: boolean;
  failureStage?: string | null;
}): LifecycleStep[] {
  const failed = input.phase === "failed";
  const failureAnchor = failed
    ? resolveLifecycleFailureAnchor({
        hasBaseline: input.hasBaseline,
        hasHypothesis: input.hasHypothesis,
        hasApproval: input.hasApproval,
        failureStage: input.failureStage,
      })
    : null;
  const investigating =
    !failed &&
    (input.phase === "investigating" ||
      (input.hasBaseline && !input.hasHypothesis));
  const awaitingApproval = input.phase === "active" && input.hasApproval;

  const steps: LifecycleStep[] = [
    {
      id: "baseline",
      label: "Baseline",
      state: input.hasBaseline ? "done" : investigating ? "active" : "pending",
      tone: "default",
    },
    {
      id: "fault",
      label: "Fault active",
      state: input.hasBaseline ? "done" : "pending",
      tone: "degraded",
    },
    {
      id: "degraded",
      label: "Degraded",
      state:
        input.hasHypothesis || input.hasApproval || input.resolved
          ? "done"
          : input.hasBaseline && !failed
            ? "active"
            : input.hasBaseline
              ? "done"
              : "pending",
      tone: "degraded",
    },
    {
      id: "investigation",
      label: "Investigation",
      state:
        failureAnchor === "investigation"
          ? "failed"
          : input.hasHypothesis
            ? "done"
            : input.hasBaseline && !failed
              ? "active"
              : "pending",
      tone: failureAnchor === "investigation" ? "failed" : "investigate",
    },
    {
      id: "diagnosis",
      label: "Diagnosis",
      state: input.hasHypothesis ? "done" : "pending",
      tone: "investigate",
    },
    {
      id: "approval",
      label: "Awaiting approval",
      state:
        failureAnchor === "approval"
          ? "failed"
          : awaitingApproval
            ? "active"
            : input.phase === "resolved" || input.phase === "rejected"
              ? "done"
              : input.hasApproval
                ? "done"
                : "pending",
      tone: failureAnchor === "approval" ? "failed" : "approval",
    },
    {
      id: "rollback",
      label: "Rollback",
      state: input.resolved
        ? "done"
        : input.phase === "rejected"
          ? "failed"
          : "pending",
      tone: "approval",
    },
    {
      id: "recovery",
      label: "Recovery verified",
      state: input.resolved ? "done" : "pending",
      tone: "recovery",
    },
  ];

  if (failureAnchor === "failed") {
    steps.push({
      id: "failed",
      label: "Failed",
      state: "failed",
      tone: "failed",
    });
  }

  return steps;
}

export function incidentPhaseLabel(phase: Phase): string {
  switch (phase) {
    case "investigating":
      return "INVESTIGATING";
    case "active":
      return "AWAITING APPROVAL";
    case "complete":
      return "COMPLETE";
    case "resolved":
      return "RESOLVED";
    case "rejected":
      return "REJECTED";
    case "failed":
      return "FAILED";
    case "blocked":
      return "BLOCKED";
    default:
      return phase.toUpperCase();
  }
}

export function incidentPhaseTone(
  phase: Phase,
): "healthy" | "investigate" | "approval" | "degraded" | "muted" {
  if (phase === "resolved") {
    return "healthy";
  }
  if (phase === "active") {
    return "approval";
  }
  if (phase === "investigating" || phase === "complete") {
    return "investigate";
  }
  if (phase === "failed" || phase === "rejected" || phase === "blocked") {
    return "degraded";
  }
  return "muted";
}
