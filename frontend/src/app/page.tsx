"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { HypothesisPanel } from "@/components/HypothesisPanel";
import { IncidentHeader } from "@/components/IncidentHeader";
import { InspectionSection } from "@/components/InspectionSection";
import { InvestigationTimeline } from "@/components/InvestigationTimeline";
import { MetricCard } from "@/components/MetricCard";
import { RecoveryPanel } from "@/components/RecoveryPanel";
import { ScenarioCard } from "@/components/ScenarioCard";
import { getRuntimeSummary, getSandboxStatus, getScenarios, submitApproval } from "@/lib/api";
import { streamIncident } from "@/lib/incident-stream";
import {
  formatErrorRate,
  formatLatency,
  humanizeServiceName,
} from "@/lib/labels";
import {
  applyInvestigationEvent,
  createLiveIncidentState,
  timelineSteps,
  type LiveIncidentState,
} from "@/lib/live-incident";
import { isAbortError, STREAM_FAILURE_MESSAGE } from "@/lib/sse-parser";
import type { ApprovalRequest, IncidentApprovalResponse, Scenario } from "@/lib/types";

type Phase =
  | "loading"
  | "ready"
  | "investigating"
  | "active"
  | "complete"
  | "resolved"
  | "rejected"
  | "failed"
  | "blocked"
  | "sandbox_busy"
  | "capacity_exhausted";

export default function Home() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(
    null,
  );
  const [live, setLive] = useState<LiveIncidentState | null>(null);
  const [approval, setApproval] = useState<IncidentApprovalResponse | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sandboxState, setSandboxState] = useState<string | null>(null);
  const [telemetryMode, setTelemetryMode] = useState<string>("reference");
  const [retryAction, setRetryAction] = useState<
    "load" | "start" | "approve" | "reject"
  >("load");
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const selectedScenario =
    scenarios.find((item) => item.id === selectedScenarioId) ?? null;
  const activeScenario =
    scenarios.find((item) => item.id === live?.scenarioId) ?? selectedScenario;

  const abortActiveStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const supersedeStream = useCallback(() => {
    generationRef.current += 1;
    abortActiveStream();
    return generationRef.current;
  }, [abortActiveStream]);

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
        const [loaded, runtime, status] = await Promise.all([
          getScenarios(),
          getRuntimeSummary().catch(() => null),
          getSandboxStatus().catch(() => null),
        ]);
        if (cancelled) {
          return;
        }
        if (!loaded[0]) {
          throw new Error("No demo scenarios are available.");
        }
        setScenarios(loaded);
        setSelectedScenarioId(loaded[0].id);
        if (runtime?.telemetry_mode) {
          setTelemetryMode(runtime.telemetry_mode);
        }
        if (status?.state) {
          setSandboxState(status.state);
        }
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
      generationRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  async function handleStart() {
    if (!selectedScenario) {
      return;
    }
    const generation = supersedeStream();
    const controller = new AbortController();
    abortRef.current = controller;

    setError(null);
    setRetryAction("start");
    setBusy(true);
    setApproval(null);
    setLive(createLiveIncidentState());
    setPhase("investigating");

    try {
      await streamIncident({
        scenarioId: selectedScenario.id,
        signal: controller.signal,
        onEvent: (event) => {
          if (generation !== generationRef.current) {
            return;
          }
          setLive((current) =>
            applyInvestigationEvent(current ?? createLiveIncidentState(), event),
          );
          if (event.event_type === "approval_required") {
            setPhase("active");
            setBusy(false);
          }
          if (event.event_type === "incident_completed") {
            setPhase("complete");
            setBusy(false);
          }
          if (event.event_type === "incident_failed") {
            setError(STREAM_FAILURE_MESSAGE);
            setPhase("failed");
            setBusy(false);
          }
          if (event.event_type === "investigation_blocked") {
            setError("Investigation blocked — telemetry unavailable for live mode.");
            setPhase("blocked");
            setBusy(false);
          }
        },
      });
      if (generation !== generationRef.current) {
        return;
      }
      setLive((current) =>
        current ? { ...current, streaming: false } : current,
      );
    } catch (cause) {
      if (generation !== generationRef.current || isAbortError(cause)) {
        return;
      }
      setError(STREAM_FAILURE_MESSAGE);
      setPhase("failed");
      setLive((current) =>
        current
          ? { ...current, streaming: false, failed: true }
          : current,
      );
    } finally {
      if (generation === generationRef.current) {
        setBusy(false);
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    }
  }

  async function handleApproval(approved: boolean) {
    if (!live?.incidentId) {
      return;
    }
    setError(null);
    setRetryAction(approved ? "approve" : "reject");
    setBusy(true);
    try {
      const result = await submitApproval(live.incidentId, approved);
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
    supersedeStream();
    setLive(null);
    setApproval(null);
    setError(null);
    setBusy(false);
    setPhase("ready");
  }

  const inWorkspace =
    phase === "investigating" ||
    phase === "active" ||
    phase === "complete" ||
    phase === "resolved" ||
    phase === "rejected" ||
    phase === "failed";
  const headerPhase:
    | "investigating"
    | "active"
    | "complete"
    | "resolved"
    | "rejected"
    | "failed" =
    phase === "ready" || phase === "loading"
      ? "investigating"
      : phase === "blocked" ||
          phase === "sandbox_busy" ||
          phase === "capacity_exhausted"
        ? "failed"
        : phase;
  const sandboxBanner =
    sandboxState === "sandbox_busy"
      ? "Live sandbox is busy — another session is active."
      : sandboxState === "ai_provider_unavailable" ||
          sandboxState === "ai_capacity_exhausted"
        ? "Live AI capacity is temporarily unavailable."
        : sandboxState === "live_environment_offline"
          ? "Live environment is offline."
          : null;
  const service =
    live?.affectedService ??
    activeScenario?.affected_service ??
    selectedScenario?.affected_service ??
    "";
  const title = activeScenario?.title ?? selectedScenario?.title ?? "Incident";
  const approvalRequest = live?.approval
    ? toApprovalRequest(live.incidentId ?? "", live.approval)
    : null;
  const originalMetrics = live?.metrics
    ? {
        ...live.metrics,
        service: live.metrics.service || service,
      }
    : null;

  return (
    <div className="page-shell">
      <div className="topbar">
        <p className="brand">OpsPilot</p>
        <p className="topbar-meta">Incident command center</p>
      </div>

      <main className="workspace">
        {inWorkspace && service ? (
          <IncidentHeader
            phase={headerPhase}
            service={service}
            title={title}
            live={live?.streaming === true}
            telemetryMode={telemetryMode}
            eventCount={live?.eventCount}
          />
        ) : null}

        {inWorkspace ? <StoryRail phase={phase} /> : null}

        {error ? (
          <div className="error-banner" role="alert">
            <p>{error}</p>
            <div className="error-actions">
              <button type="button" className="button-secondary" onClick={retry}>
                Retry
              </button>
              {phase !== "ready" ? (
                <button
                  type="button"
                  className="button-secondary"
                  onClick={resetToSelection}
                >
                  Back to incidents
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {phase === "loading" ? (
          <p className="status-copy" aria-live="polite">
            Loading incidents…
          </p>
        ) : null}

        {sandboxBanner && phase === "ready" ? (
          <div className="error-banner" role="status">
            <p>{sandboxBanner}</p>
          </div>
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
              {telemetryMode === "live"
                ? "Live incident sandbox"
                : "Deterministic reference evaluation"}
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
                Investigate
              </button>
              {selectedScenario ? (
                <p className="start-hint">
                  Selected: {humanizeServiceName(selectedScenario.affected_service)}
                </p>
              ) : null}
            </div>
          </section>
        ) : null}

        {live && inWorkspace ? (
          <>
            {live.baseline && telemetryMode === "live" ? (
              <div className="metric-grid">
                <MetricCard
                  label="Baseline p95"
                  value={formatLatency(live.baseline.p95_latency_ms)}
                  hint="Measured before fault"
                  tone="neutral"
                />
                <MetricCard
                  label="Baseline error rate"
                  value={formatErrorRate(live.baseline.error_rate_percent)}
                  hint="Measured before fault"
                  tone="neutral"
                />
              </div>
            ) : null}
            {originalMetrics ? (
              <div className="metric-grid">
                <MetricCard
                  label="p95 latency"
                  value={formatLatency(originalMetrics.p95_latency_ms)}
                  hint={
                    phase === "resolved" ||
                    phase === "rejected" ||
                    phase === "complete"
                      ? "At detection"
                      : undefined
                  }
                  tone="incident"
                />
                <MetricCard
                  label="Error rate"
                  value={formatErrorRate(originalMetrics.error_rate_percent)}
                  hint={
                    phase === "resolved" ||
                    phase === "rejected" ||
                    phase === "complete"
                      ? "At detection"
                      : undefined
                  }
                  tone="incident"
                />
              </div>
            ) : null}

              <div
                className={
                  live.hypothesis
                    ? "workspace-grid"
                    : "workspace-grid workspace-grid-solo"
                }
              >
              <InvestigationTimeline
                steps={timelineSteps(live)}
                skills={live.selectedSkills}
                symptomSummary={live.symptomSummary}
              />
              {live.hypothesis ? (
                <HypothesisPanel hypothesis={live.hypothesis} />
              ) : null}
            </div>

            {phase === "active" && approvalRequest ? (
              <ApprovalPanel
                approvalRequest={approvalRequest}
                recommendedAction={
                  live.hypothesis?.recommendedAction ?? approvalRequest.action
                }
                proposedVersion={approvalRequest.version}
                busy={busy}
                onApprove={() => void handleApproval(true)}
                onReject={() => void handleApproval(false)}
              />
            ) : null}

            {approval &&
            originalMetrics &&
            (phase === "resolved" || phase === "rejected") ? (
              <RecoveryPanel original={originalMetrics} approval={approval} />
            ) : null}

            <InspectionSection
              symptomSummary={live.symptomSummary}
              evidence={live.evidence}
              incidentId={live.incidentId}
              lifecyclePhase={headerPhase}
            />

            {phase === "resolved" || phase === "rejected" || phase === "complete" ? (
              <div className="reset-row">
                <button
                  type="button"
                  className="button-secondary"
                  onClick={resetToSelection}
                >
                  Investigate another incident
                </button>
              </div>
            ) : null}

            {phase === "investigating" ? (
              <div className="reset-row">
                <button
                  type="button"
                  className="button-secondary"
                  onClick={resetToSelection}
                >
                  Back to incidents
                </button>
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}

function toApprovalRequest(
  incidentId: string,
  approval: NonNullable<LiveIncidentState["approval"]>,
): ApprovalRequest {
  return {
    type: "approval_required",
    proposal_id: approval.proposalId,
    incident_id: incidentId,
    action: approval.action,
    service: approval.service,
    version: approval.version,
    risk_level: approval.riskLevel,
    message: approval.message,
  };
}

function StoryRail({ phase }: { phase: Phase }) {
  const investigated =
    phase === "active" ||
    phase === "complete" ||
    phase === "resolved" ||
    phase === "rejected";
  const decided = phase === "resolved" || phase === "rejected" || phase === "complete";
  const steps = [
    {
      id: "broke",
      label: "Something broke",
      done: phase !== "ready" && phase !== "loading",
    },
    {
      id: "investigated",
      label: "OpsPilot investigated",
      done: investigated,
    },
    {
      id: "approval",
      label: "Human approval",
      done: phase === "resolved" || phase === "rejected",
    },
    {
      id: "outcome",
      label:
        phase === "rejected" || phase === "complete"
          ? "Unchanged"
          : "Service recovers",
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
