"use client";

import { useCallback, useEffect, useState } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { HypothesisPanel } from "@/components/HypothesisPanel";
import { IncidentHeader } from "@/components/IncidentHeader";
import { InvestigationTimeline } from "@/components/InvestigationTimeline";
import { MetricCard } from "@/components/MetricCard";
import { RecoveryPanel } from "@/components/RecoveryPanel";
import { getScenarios, startIncident, submitApproval } from "@/lib/api";
import { formatErrorRate, formatLatency } from "@/lib/labels";
import type {
  IncidentApprovalResponse,
  IncidentStartResponse,
  Scenario,
} from "@/lib/types";

type Phase =
  | "loading"
  | "ready"
  | "investigating"
  | "active"
  | "resolved"
  | "rejected";

export default function Home() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [incident, setIncident] = useState<IncidentStartResponse | null>(null);
  const [approval, setApproval] = useState<IncidentApprovalResponse | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<
    "load" | "start" | "approve" | "reject"
  >("load");

  const loadScenarios = useCallback(async () => {
    setRetryAction("load");
    try {
      const scenarios = await getScenarios();
      const selected = scenarios[0];
      if (!selected) {
        throw new Error("No demo scenarios are available.");
      }
      setScenario(selected);
      setError(null);
      setPhase("ready");
    } catch (cause) {
      setScenario(null);
      setError(
        cause instanceof Error ? cause.message : "Unable to load scenarios.",
      );
      setPhase("ready");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadOnMount() {
      try {
        const scenarios = await getScenarios();
        if (cancelled) {
          return;
        }
        const selected = scenarios[0];
        if (!selected) {
          throw new Error("No demo scenarios are available.");
        }
        setScenario(selected);
        setPhase("ready");
      } catch (cause) {
        if (cancelled) {
          return;
        }
        setError(
          cause instanceof Error ? cause.message : "Unable to load scenarios.",
        );
        setPhase("ready");
      }
    }

    void loadOnMount();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleStart() {
    if (!scenario) {
      return;
    }
    setError(null);
    setRetryAction("start");
    setBusy(true);
    setPhase("investigating");
    try {
      const started = await startIncident(scenario.id);
      setIncident(started);
      setPhase("active");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Investigation failed. Try again.",
      );
      setPhase("ready");
    } finally {
      setBusy(false);
    }
  }

  async function handleApproval(approved: boolean) {
    if (!incident) {
      return;
    }
    setError(null);
    setRetryAction(approved ? "approve" : "reject");
    setBusy(true);
    try {
      const result = await submitApproval(incident.incident_id, approved);
      setApproval(result);
      setPhase(result.status === "resolved" ? "resolved" : "rejected");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Approval request failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  function retry() {
    if (retryAction === "load") {
      setError(null);
      setPhase("loading");
      void loadScenarios();
      return;
    }
    if (retryAction === "start") {
      void handleStart();
      return;
    }
    void handleApproval(retryAction === "approve");
  }

  const headerPhase =
    phase === "resolved" || phase === "rejected"
      ? phase
      : phase === "ready" || phase === "loading" || phase === "investigating"
        ? "ready"
        : "active";

  return (
    <div className="page-shell">
      <div className="topbar">
        <p className="brand">OpsPilot</p>
        <p className="topbar-meta">Incident command center</p>
      </div>

      <main className="workspace">
        {scenario ? (
          <IncidentHeader
            phase={headerPhase}
            service={scenario.affected_service}
            title={scenario.title}
          />
        ) : (
          <IncidentHeader
            phase="ready"
            service="—"
            title="OpsPilot incident demo"
          />
        )}

        <StoryRail phase={phase} />

        {error ? (
          <div className="error-banner" role="alert">
            <p>{error}</p>
            <button type="button" className="button-secondary" onClick={retry}>
              Retry
            </button>
          </div>
        ) : null}

        {phase === "loading" ? (
          <p className="status-copy" aria-live="polite">
            Loading demo incident…
          </p>
        ) : null}

        {phase === "ready" && scenario ? (
          <section className="panel briefing" aria-labelledby="briefing-heading">
            <h2 id="briefing-heading" className="sr-only">
              Start investigation
            </h2>
            <p>
              A production incident is ready to investigate. OpsPilot will
              collect metrics, deployments, and logs, then propose a
              remediation. High-risk changes still require your approval.
            </p>
            <button
              type="button"
              className="button-primary"
              onClick={() => void handleStart()}
              disabled={busy}
            >
              Start Investigation
            </button>
          </section>
        ) : null}

        {phase === "investigating" ? (
          <p className="status-copy" aria-live="polite" aria-busy="true">
            Investigating {scenario?.affected_service ?? "service"}…
          </p>
        ) : null}

        {incident &&
        (phase === "active" || phase === "resolved" || phase === "rejected") ? (
          <>
            <div className="metric-grid">
              <MetricCard
                label="p95 latency"
                value={formatLatency(incident.metrics.p95_latency_ms)}
                hint={phase === "resolved" || phase === "rejected" ? "At detection" : undefined}
                tone="incident"
              />
              <MetricCard
                label="Error rate"
                value={formatErrorRate(incident.metrics.error_rate_percent)}
                hint={phase === "resolved" || phase === "rejected" ? "At detection" : undefined}
                tone="incident"
              />
            </div>

            <div className="workspace-grid">
              <InvestigationTimeline steps={incident.investigation_steps} />
              <HypothesisPanel result={incident.hypothesis_result} />
            </div>

            {phase === "active" &&
            incident.status === "approval_required" &&
            incident.approval_request ? (
              <ApprovalPanel
                approvalRequest={incident.approval_request}
                recommendedAction={incident.recommended_action}
                proposedVersion={incident.proposed_version}
                busy={busy}
                onApprove={() => void handleApproval(true)}
                onReject={() => void handleApproval(false)}
              />
            ) : null}

            {approval && (phase === "resolved" || phase === "rejected") ? (
              <RecoveryPanel original={incident.metrics} approval={approval} />
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}

function StoryRail({ phase }: { phase: Phase }) {
  const investigated =
    phase === "active" || phase === "resolved" || phase === "rejected";
  const decided = phase === "resolved" || phase === "rejected";
  const steps = [
    {
      id: "broke",
      label: "Something broke",
      done: investigated || phase === "investigating",
    },
    {
      id: "investigated",
      label: "OpsPilot investigated",
      done: investigated,
    },
    {
      id: "approval",
      label: "Human approval",
      done: decided,
    },
    {
      id: "outcome",
      label: phase === "rejected" ? "Unchanged" : "Service recovers",
      done: decided,
    },
  ];

  return (
    <ol className="story-rail" aria-label="Incident workflow">
      {steps.map((step) => (
        <li key={step.id} className={step.done ? "done" : undefined}>
          {step.label}
        </li>
      ))}
    </ol>
  );
}
