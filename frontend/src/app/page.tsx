"use client";

import { useCallback, useEffect, useState } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { HypothesisPanel } from "@/components/HypothesisPanel";
import { IncidentHeader } from "@/components/IncidentHeader";
import { InvestigationTimeline } from "@/components/InvestigationTimeline";
import { MetricCard } from "@/components/MetricCard";
import { RecoveryPanel } from "@/components/RecoveryPanel";
import { ScenarioCard } from "@/components/ScenarioCard";
import { getScenarios, startIncident, submitApproval } from "@/lib/api";
import {
  formatErrorRate,
  formatLatency,
  humanizeServiceName,
} from "@/lib/labels";
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
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(
    null,
  );
  const [incident, setIncident] = useState<IncidentStartResponse | null>(null);
  const [approval, setApproval] = useState<IncidentApprovalResponse | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<
    "load" | "start" | "approve" | "reject"
  >("load");

  const selectedScenario =
    scenarios.find((item) => item.id === selectedScenarioId) ?? null;
  const activeScenario =
    scenarios.find((item) => item.id === incident?.scenario_id) ??
    selectedScenario;

  const loadScenarios = useCallback(async () => {
    setRetryAction("load");
    try {
      const loaded = await getScenarios();
      if (!loaded[0]) {
        throw new Error("No demo scenarios are available.");
      }
      setScenarios(loaded);
      setSelectedScenarioId((current) => current ?? loaded[0].id);
      setError(null);
      setPhase("ready");
    } catch (cause) {
      setScenarios([]);
      setSelectedScenarioId(null);
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
        const loaded = await getScenarios();
        if (cancelled) {
          return;
        }
        if (!loaded[0]) {
          throw new Error("No demo scenarios are available.");
        }
        setScenarios(loaded);
        setSelectedScenarioId(loaded[0].id);
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
    if (!selectedScenario) {
      return;
    }
    setError(null);
    setRetryAction("start");
    setBusy(true);
    setPhase("investigating");
    try {
      const started = await startIncident(selectedScenario.id);
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

  function resetToSelection() {
    setIncident(null);
    setApproval(null);
    setError(null);
    setBusy(false);
    setPhase("ready");
  }

  const inIncident =
    phase === "active" || phase === "resolved" || phase === "rejected";
  const headerPhase =
    phase === "resolved" || phase === "rejected" ? phase : "active";

  return (
    <div className="page-shell">
      <div className="topbar">
        <p className="brand">OpsPilot</p>
        <p className="topbar-meta">Incident command center</p>
      </div>

      <main className="workspace">
        {phase === "ready" || phase === "loading" || phase === "investigating"
          ? null
          : activeScenario && (
              <IncidentHeader
                phase={headerPhase}
                service={incident?.affected_service ?? activeScenario.affected_service}
                title={activeScenario.title}
              />
            )}

        {inIncident || phase === "investigating" ? (
          <StoryRail phase={phase} />
        ) : null}

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
            Loading incidents…
          </p>
        ) : null}

        {phase === "ready" ? (
          <section className="hero" aria-labelledby="product-heading">
            <p className="hero-brand">OpsPilot</p>
            <h1 id="product-heading">Autonomous Production Engineering Agent</h1>
            <p className="hero-copy">
              Choose a production incident and watch OpsPilot investigate
              evidence, form a hypothesis, request approval for risky
              remediation, and verify recovery.
            </p>
          </section>
        ) : null}

        {phase === "ready" && scenarios.length > 0 ? (
          <section aria-labelledby="scenario-heading">
            <h2 id="scenario-heading" className="section-heading">
              Production incidents
            </h2>
            <div
              className="scenario-grid"
              role="radiogroup"
              aria-label="Incident scenarios"
            >
              {scenarios.map((scenario) => (
                <ScenarioCard
                  key={scenario.id}
                  scenario={scenario}
                  selected={scenario.id === selectedScenarioId}
                  onSelect={() => setSelectedScenarioId(scenario.id)}
                />
              ))}
            </div>
            <div className="start-row">
              <button
                type="button"
                className="button-primary"
                onClick={() => void handleStart()}
                disabled={busy || !selectedScenario}
              >
                Start Investigation
              </button>
              {selectedScenario ? (
                <p className="start-hint">
                  Selected: {humanizeServiceName(selectedScenario.affected_service)}
                </p>
              ) : null}
            </div>
          </section>
        ) : null}

        {phase === "investigating" ? (
          <p className="status-copy" aria-live="polite" aria-busy="true">
            Investigating{" "}
            {selectedScenario
              ? humanizeServiceName(selectedScenario.affected_service)
              : "service"}
            …
          </p>
        ) : null}

        {incident && inIncident ? (
          <>
            <div className="metric-grid">
              <MetricCard
                label="p95 latency"
                value={formatLatency(incident.metrics.p95_latency_ms)}
                hint={
                  phase === "resolved" || phase === "rejected"
                    ? "At detection"
                    : undefined
                }
                tone="incident"
              />
              <MetricCard
                label="Error rate"
                value={formatErrorRate(incident.metrics.error_rate_percent)}
                hint={
                  phase === "resolved" || phase === "rejected"
                    ? "At detection"
                    : undefined
                }
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
              <>
                <RecoveryPanel original={incident.metrics} approval={approval} />
                <div className="reset-row">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={resetToSelection}
                  >
                    Investigate another incident
                  </button>
                </div>
              </>
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
