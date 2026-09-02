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

export function lifecycleSteps(input: {
  phase: Phase;
  hasBaseline: boolean;
  hasHypothesis: boolean;
  hasApproval: boolean;
  resolved: boolean;
}): Array<{ id: string; label: string; state: "pending" | "active" | "done" }> {
  const done = (cond: boolean): "pending" | "done" =>
    cond ? "done" : "pending";
  const active = (cond: boolean): "pending" | "active" =>
    cond ? "active" : "pending";
  return [
    { id: "baseline", label: "Baseline", state: done(input.hasBaseline) },
    { id: "fault", label: "Fault active", state: done(input.hasBaseline) },
    { id: "degraded", label: "Degraded", state: done(input.hasHypothesis) },
    { id: "diagnosis", label: "Diagnosis", state: done(input.hasHypothesis) },
    {
      id: "approval",
      label: "Awaiting approval",
      state: input.hasApproval
        ? active(input.phase === "active")
        : done(input.phase === "resolved" || input.phase === "rejected"),
    },
    {
      id: "rollback",
      label: "Rollback",
      state: done(input.resolved),
    },
    {
      id: "recovery",
      label: "Recovery verified",
      state: done(input.resolved),
    },
  ];
}
