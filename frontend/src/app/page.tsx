"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { ArchitectureSection } from "@/components/ArchitectureSection";
import { CommandCenterHeader } from "@/components/CommandCenterHeader";
import { HypothesisPanel } from "@/components/HypothesisPanel";
import { IncidentHeader } from "@/components/IncidentHeader";
import { InspectionSection } from "@/components/InspectionSection";
import { InvestigationTimeline } from "@/components/InvestigationTimeline";
import { LandingHero } from "@/components/LandingHero";
import { LifecycleRail } from "@/components/LifecycleRail";
import { LiveProvenancePanel } from "@/components/LiveProvenancePanel";
import { RecoveryPanel } from "@/components/RecoveryPanel";
import { SafetyCallout } from "@/components/SafetyCallout";
import { ScenarioCard } from "@/components/ScenarioCard";
import { ServiceTopology } from "@/components/ServiceTopology";
import { TelemetryBands } from "@/components/TelemetryBands";
import { TurnstileWidget } from "@/components/TurnstileWidget";
import {
  getHealth,
  getIncidentProvenance,
  getRuntimeSummary,
  getSandboxStatus,
  getScenarios,
  submitApproval,
} from "@/lib/api";
import { lifecycleSteps, resolveLabStatus } from "@/lib/command-center";
import type { Phase } from "@/lib/command-center-types";
import { streamIncident } from "@/lib/incident-stream";
import { humanizeServiceName } from "@/lib/labels";
import {
  applyInvestigationEvent,
  createLiveIncidentState,
  timelineSteps,
  type LiveIncidentState,
} from "@/lib/live-incident";
import {
  provenanceMatchesIncident,
  selectRenderableProvenance,
} from "@/lib/provenance-display";
import {
  canStartLiveIncident,
  consumeTurnstileToken,
  planStartRetry,
} from "@/lib/turnstile-start";
import {
  isPreIncidentStartError,
  type PreIncidentCode,
} from "@/lib/start-rejection";
import { isAbortError, STREAM_FAILURE_MESSAGE } from "@/lib/sse-parser";
import type { ApprovalRequest, IncidentApprovalResponse, LiveProvenance, Scenario } from "@/lib/types";

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
  const [turnstileSiteKey, setTurnstileSiteKey] = useState(
    process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "",
  );
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileStatus, setTurnstileStatus] = useState<
    "loading" | "ready" | "error" | "expired" | "idle"
  >("idle");
  const [turnstileReset, setTurnstileReset] = useState(0);
  const [provenance, setProvenance] = useState<LiveProvenance | null>(null);
  const [provenanceLoading, setProvenanceLoading] = useState(false);
  const [retryAction, setRetryAction] = useState<
    "load" | "start" | "approve" | "reject"
  >("load");
  const [startGateDetail, setStartGateDetail] = useState<string | null>(null);
  const [sessionStartBlocked, setSessionStartBlocked] = useState(false);
  const [startGateCode, setStartGateCode] = useState<PreIncidentCode | null>(
    null,
  );
  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const provenanceIncidentRef = useRef<string | null>(null);

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

  const clearProvenance = useCallback(() => {
    provenanceIncidentRef.current = null;
    setProvenance(null);
    setProvenanceLoading(false);
  }, []);

  const loadProvenance = useCallback(
    async (incidentId: string, generation: number) => {
      provenanceIncidentRef.current = incidentId;
      setProvenance(null);
      setProvenanceLoading(true);
      try {
        const loaded = await getIncidentProvenance(incidentId);
        if (generation !== generationRef.current) {
          return;
        }
        if (
          provenanceIncidentRef.current !== incidentId ||
          !provenanceMatchesIncident(loaded.incident_id, incidentId)
        ) {
          return;
        }
        setProvenance(loaded);
      } catch {
        if (
          generation === generationRef.current &&
          provenanceIncidentRef.current === incidentId
        ) {
          setProvenance(null);
        }
      } finally {
        if (
          generation === generationRef.current &&
          provenanceIncidentRef.current === incidentId
        ) {
          setProvenanceLoading(false);
        }
      }
    },
    [],
  );

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
      const maxAttempts = 20;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
          // Prefer process-local /health during Cloud Run cold start; tolerate
          // temporary deep /ready degradation without inventing telemetry.
          // Do not call /healthz: public GFE intercepts that exact path.
          await getHealth();
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
          if (runtime?.turnstile_site_key) {
            setTurnstileSiteKey(runtime.turnstile_site_key);
          }
          if (status?.state) {
            setSandboxState(status.state);
          }
          setError(null);
          setPhase("ready");
          return;
        } catch (cause) {
          if (cancelled) {
            return;
          }
          const message =
            cause instanceof Error ? cause.message : "Unable to load scenarios.";
          const isReachability =
            message.includes("Unable to reach OpsPilot") ||
            message.includes("Failed to fetch");
          if (isReachability && attempt < maxAttempts) {
            setPhase("loading");
            setError(null);
            await new Promise((resolve) => setTimeout(resolve, 1500));
            continue;
          }
          setError(message);
          setPhase("ready");
          return;
        }
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
    if (
      !canStartLiveIncident({
        turnstileRequired: Boolean(turnstileSiteKey),
        turnstileToken,
      })
    ) {
      // Missing/expired token is a pre-incident gate — never a stream failure.
      returnToStartForFreshTurnstile();
      setError("Complete the Cloudflare check before starting a live incident.");
      setStartGateDetail(null);
      setStartGateCode(null);
      return;
    }
    const { captured, remaining } = consumeTurnstileToken(turnstileToken);
    setTurnstileToken(remaining);
    const generation = supersedeStream();
    const controller = new AbortController();
    abortRef.current = controller;

    setError(null);
    setStartGateDetail(null);
    setStartGateCode(null);
    setRetryAction("start");
    setBusy(true);
    setApproval(null);
    clearProvenance();
    setLive(createLiveIncidentState());
    setPhase("investigating");

    let remountTurnstile = true;
    try {
      await streamIncident({
        scenarioId: selectedScenario.id,
        turnstileToken: captured,
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
            if (telemetryMode === "live" && event.incident_id) {
              void loadProvenance(event.incident_id, generation);
            }
          }
          if (event.event_type === "incident_completed") {
            setPhase("complete");
            setBusy(false);
            if (telemetryMode === "live" && event.incident_id) {
              void loadProvenance(event.incident_id, generation);
            }
          }
          if (event.event_type === "incident_failed") {
            setError(STREAM_FAILURE_MESSAGE);
            setPhase("failed");
            setBusy(false);
            if (telemetryMode === "live" && event.incident_id) {
              void loadProvenance(event.incident_id, generation);
            }
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
      if (isPreIncidentStartError(cause)) {
        // No incident existed — return to start screen with truthful gate UX.
        remountTurnstile = cause.remountTurnstile;
        setLive(null);
        setApproval(null);
        clearProvenance();
        setPhase("ready");
        setError(cause.message);
        setStartGateDetail(cause.detail);
        setStartGateCode(cause.code);
        setRetryAction("start");
        if (cause.disableStart) {
          setSessionStartBlocked(true);
        }
        setTurnstileToken(null);
        return;
      }
      setStartGateDetail(null);
      setStartGateCode(null);
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
        if (remountTurnstile) {
          setTurnstileReset((current) => current + 1);
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
      if (telemetryMode === "live") {
        await loadProvenance(live.incidentId, generationRef.current);
      }
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

  function returnToStartForFreshTurnstile() {
    if (sessionStartBlocked) {
      // Session remains capped — do not remount or imply another start is available.
      setLive(null);
      setApproval(null);
      clearProvenance();
      setBusy(false);
      setPhase("ready");
      return;
    }
    const plan = planStartRetry();
    supersedeStream();
    if (plan.clearLiveIncidentState) {
      setLive(null);
      setApproval(null);
    }
    if (plan.clearProvenance) {
      clearProvenance();
    }
    if (plan.clearTurnstileToken) {
      setTurnstileToken(null);
    }
    if (plan.remountTurnstile) {
      setTurnstileReset((current) => current + 1);
    }
    setError(null);
    setStartGateDetail(null);
    setStartGateCode(null);
    setBusy(false);
    if (plan.clearFailedWorkspace) {
      setPhase("ready");
    }
    // selectedScenarioId intentionally preserved (plan.preserveSelectedScenario).
  }

  function retry() {
    if (retryAction === "load") {
      setError(null);
      setPhase("loading");
      void loadScenarios();
      return;
    }
    if (retryAction === "start") {
      // Pre-incident or start failure: never re-POST with a consumed token.
      returnToStartForFreshTurnstile();
      return;
    }
    void handleApproval(retryAction === "approve");
  }

  function resetToSelection() {
    supersedeStream();
    setLive(null);
    setApproval(null);
    clearProvenance();
    setError(null);
    setBusy(false);
    setTurnstileToken(null);
    setTurnstileReset((current) => current + 1);
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
  const labStatus = resolveLabStatus({
    phase,
    sandboxState,
    telemetryMode,
    investigating: phase === "investigating",
  });
  const topologyPhase =
    phase === "resolved"
      ? "rollback"
      : phase === "active" || live?.degraded
        ? "degraded"
        : phase === "investigating"
          ? "traffic"
          : "idle";
  const activeProvenance = selectRenderableProvenance(
    provenance,
    live?.incidentId,
  );
  const recoveryWindow =
    approval && approval.recovered_p95_latency_ms !== null
      ? {
          p95_latency_ms: approval.recovered_p95_latency_ms ?? 0,
          error_rate_percent: approval.recovered_error_rate_percent ?? 0,
          sample_count: activeProvenance?.recovery?.sample_count ?? undefined,
        }
      : null;

  return (
    <div className="page-shell command-center">
      <CommandCenterHeader
        labStatus={labStatus}
        telemetryMode={telemetryMode}
      />

      <main className="workspace command-workspace">
        {inWorkspace && service ? (
          <IncidentHeader
            phase={headerPhase}
            service={service}
            title={title}
            live={live?.streaming === true}
            telemetryMode={telemetryMode}
            eventCount={live?.eventCount}
            revision={
              live?.approval?.version ??
              activeProvenance?.service_revision ??
              null
            }
          />
        ) : null}

        {error ? (
          <div className="error-banner" role="alert">
            <p>{error}</p>
            {startGateDetail ? (
              <p className="status-copy">{startGateDetail}</p>
            ) : null}
            <div className="error-actions">
              {!(
                sessionStartBlocked ||
                startGateCode === "session_live_incident_limit"
              ) ? (
                <button type="button" className="button-secondary" onClick={retry}>
                  Retry
                </button>
              ) : null}
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
            Warming up live incident lab…
          </p>
        ) : null}

        {sandboxBanner && phase === "ready" ? (
          <div className="error-banner" role="status">
            <p>{sandboxBanner}</p>
          </div>
        ) : null}

        {phase === "ready" ? <LandingHero /> : null}

        {phase === "ready" && scenarios.length > 0 ? (
          <section aria-labelledby="scenario-heading">
            <h2 id="scenario-heading" className="section-heading">
              {telemetryMode === "live"
                ? "Live incident lab"
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
            <div className="lab-command">
              <div className="lab-command-row">
                {turnstileSiteKey ? (
                  <div className="turnstile-panel">
                    <TurnstileWidget
                      siteKey={turnstileSiteKey}
                      onToken={setTurnstileToken}
                      onStatus={setTurnstileStatus}
                      resetSignal={turnstileReset}
                    />
                    {turnstileStatus === "loading" ? (
                      <p className="turnstile-status">
                        Verifying you are human…
                      </p>
                    ) : null}
                    {turnstileStatus === "error" ? (
                      <p className="turnstile-status">
                        Cloudflare check failed. Retry the widget to continue.
                      </p>
                    ) : null}
                    {turnstileStatus === "expired" ? (
                      <p className="turnstile-status">
                        Cloudflare check expired. Complete it again to start.
                      </p>
                    ) : null}
                  </div>
                ) : null}
                <div className="lab-command-main">
                  <button
                    type="button"
                    className="button-primary button-primary-hero"
                    onClick={() => void handleStart()}
                    disabled={
                      busy ||
                      sessionStartBlocked ||
                      !selectedScenario ||
                      Boolean(turnstileSiteKey && !turnstileToken)
                    }
                  >
                    <span className="button-play-icon" aria-hidden="true">
                      <svg
                        viewBox="0 0 12 12"
                        width="12"
                        height="12"
                        fill="currentColor"
                      >
                        <path d="M3 1.5v9l8-4.5-8-4.5z" />
                      </svg>
                    </span>
                    Start live investigation
                  </button>
                  {selectedScenario ? (
                    <p className="start-hint">
                      Selected:{" "}
                      {humanizeServiceName(selectedScenario.affected_service)}
                    </p>
                  ) : null}
                  {sessionStartBlocked ? (
                    <p className="start-hint">
                      Live demo limit reached for this browser session.
                    </p>
                  ) : null}
                </div>
              </div>
              <SafetyCallout />
            </div>
            <div className="architecture-below">
              <ArchitectureSection />
            </div>
          </section>
        ) : null}

        {live && inWorkspace ? (
          <>
            <LifecycleRail
              steps={lifecycleSteps({
                phase,
                hasBaseline: Boolean(live.baseline),
                hasHypothesis: Boolean(live.hypothesis),
                hasApproval: Boolean(live.approval),
                resolved: phase === "resolved",
                failureStage: live.failureStage,
              })}
            />
            <div className="incident-ops-grid">
              <ServiceTopology affectedService={service} phase={topologyPhase} />
              <TelemetryBands
                mode={telemetryMode}
                baseline={
                  live.baseline
                    ? {
                        ...live.baseline,
                        sample_count: activeProvenance?.baseline?.sample_count,
                      }
                    : null
                }
                degraded={
                  live.degraded
                    ? {
                        ...live.degraded,
                        sample_count: activeProvenance?.degraded?.sample_count,
                      }
                    : null
                }
                recovery={recoveryWindow}
              />
            </div>

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
                <HypothesisPanel
                  hypothesis={live.hypothesis}
                  evidenceCount={live.evidence.length}
                />
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
              <RecoveryPanel
                original={originalMetrics}
                approval={approval}
                freshTelemetryVerified={
                  activeProvenance?.recovery?.all_samples_post_remediation ??
                  activeProvenance?.recovery?.verified ??
                  null
                }
              />
            ) : null}

            <LiveProvenancePanel
              provenance={activeProvenance}
              loading={provenanceLoading}
              phase={phase}
              approvalStatus={approval?.approval_status ?? null}
            />

            <InspectionSection
              symptomSummary={live.symptomSummary}
              evidence={live.evidence}
              incidentId={live.incidentId}
              lifecyclePhase={headerPhase}
            />

            {phase === "resolved" ||
            phase === "rejected" ||
            phase === "complete" ? (
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
