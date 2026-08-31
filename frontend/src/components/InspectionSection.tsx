"use client";

import { useEffect, useState } from "react";

import { getBaselineEvaluation, getIncidentAudit } from "@/lib/api";
import {
  DETERMINISTIC_BASELINE_EXPLAIN,
  baselineModeLabel,
  evaluationScenarioRows,
  evidenceTypeLabel,
  formatAverageInvestigationSteps,
  formatAuditClock,
  formatAuditEventName,
  formatPassedScenarios,
  formatUnitInterval,
  formatUnsafeActionRate,
  preserveAuditOrder,
  usefulAuditMetadata,
} from "@/lib/inspection";
import { formatErrorRate, formatLatency } from "@/lib/labels";
import type { BoundedEvidence } from "@/lib/live-incident";
import type { AuditEvent, BaselineEvaluation } from "@/lib/types";

type InspectionTab = "evidence" | "audit" | "evaluation";

type LifecyclePhase =
  | "investigating"
  | "active"
  | "resolved"
  | "rejected"
  | "failed";

type InspectionSectionProps = {
  symptomSummary: string | null;
  evidence: BoundedEvidence[];
  incidentId: string | null;
  lifecyclePhase: LifecyclePhase;
};

type LoadState = "idle" | "loading" | "loaded" | "empty" | "error";

let baselineCache: BaselineEvaluation | null = null;
let baselinePromise: Promise<BaselineEvaluation> | null = null;

function loadBaselineEvaluation(): Promise<BaselineEvaluation> {
  if (baselineCache) {
    return Promise.resolve(baselineCache);
  }
  if (!baselinePromise) {
    baselinePromise = getBaselineEvaluation()
      .then((result) => {
        baselineCache = result;
        return result;
      })
      .catch((cause) => {
        baselinePromise = null;
        throw cause;
      });
  }
  return baselinePromise;
}

function auditRefreshKey(
  incidentId: string,
  lifecyclePhase: LifecyclePhase,
): string {
  if (
    lifecyclePhase === "active" ||
    lifecyclePhase === "resolved" ||
    lifecyclePhase === "rejected"
  ) {
    return `${incidentId}:${lifecyclePhase}`;
  }
  return `${incidentId}:inspect`;
}

export function InspectionSection({
  symptomSummary,
  evidence,
  incidentId,
  lifecyclePhase,
}: InspectionSectionProps) {
  const [tab, setTab] = useState<InspectionTab>("evidence");
  const prefetchAudit =
    lifecyclePhase === "active" ||
    lifecyclePhase === "resolved" ||
    lifecyclePhase === "rejected";
  const loadAudit = Boolean(incidentId) && (prefetchAudit || tab === "audit");

  return (
    <section className="inspection" aria-labelledby="inspection-heading">
      <h2 id="inspection-heading" className="section-heading">
        Inspection
      </h2>
      <div className="segmented" role="tablist" aria-label="Inspection views">
        <TabButton
          id="evidence"
          selected={tab === "evidence"}
          onSelect={setTab}
        >
          Evidence
        </TabButton>
        <TabButton id="audit" selected={tab === "audit"} onSelect={setTab}>
          Audit trail
        </TabButton>
        <TabButton
          id="evaluation"
          selected={tab === "evaluation"}
          onSelect={setTab}
        >
          Evaluation
        </TabButton>
      </div>

      {tab === "evidence" ? (
        <EvidencePanel
          symptomSummary={symptomSummary}
          evidence={evidence}
        />
      ) : null}
      {loadAudit && incidentId ? (
        <AuditLoader
          key={auditRefreshKey(incidentId, lifecyclePhase)}
          incidentId={incidentId}
          visible={tab === "audit"}
        />
      ) : tab === "audit" ? (
        <AuditPanel incidentId={incidentId} state="idle" events={[]} />
      ) : null}
      {tab === "evaluation" ? <EvaluationLoader /> : null}
    </section>
  );
}

function AuditLoader({
  incidentId,
  visible,
}: {
  incidentId: string;
  visible: boolean;
}) {
  const [state, setState] = useState<LoadState>("loading");
  const [events, setEvents] = useState<AuditEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    void getIncidentAudit(incidentId)
      .then((result) => {
        if (cancelled) {
          return;
        }
        const next = preserveAuditOrder(result.events);
        setEvents(next);
        setState(next.length === 0 ? "empty" : "loaded");
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [incidentId]);

  if (!visible) {
    return null;
  }
  return <AuditPanel incidentId={incidentId} state={state} events={events} />;
}

function EvaluationLoader() {
  const [state, setState] = useState<LoadState>(
    baselineCache ? "loaded" : "loading",
  );
  const [evaluation, setEvaluation] = useState<BaselineEvaluation | null>(
    baselineCache,
  );

  useEffect(() => {
    if (baselineCache) {
      return;
    }
    let cancelled = false;
    void loadBaselineEvaluation()
      .then((result) => {
        if (cancelled) {
          return;
        }
        setEvaluation(result);
        setState("loaded");
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <EvaluationPanel state={state} evaluation={evaluation} />;
}

function TabButton({
  id,
  selected,
  onSelect,
  children,
}: {
  id: InspectionTab;
  selected: boolean;
  onSelect: (tab: InspectionTab) => void;
  children: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      id={`inspection-tab-${id}`}
      aria-selected={selected}
      aria-controls={`inspection-panel-${id}`}
      tabIndex={selected ? 0 : -1}
      onClick={() => onSelect(id)}
    >
      {children}
    </button>
  );
}

function EvidencePanel({
  symptomSummary,
  evidence,
}: {
  symptomSummary: string | null;
  evidence: BoundedEvidence[];
}) {
  return (
    <div
      className="panel inspection-panel"
      role="tabpanel"
      id="inspection-panel-evidence"
      aria-labelledby="inspection-tab-evidence"
    >
      <p className="inspection-caption">
        Bounded evidence used for diagnosis — not a causal proof.
      </p>
      {symptomSummary ? (
        <div className="evidence-symptoms">
          <h3>Incident symptoms</h3>
          <p>{symptomSummary}</p>
        </div>
      ) : null}
      {evidence.length > 0 ? (
        <>
          <h3 className="evidence-heading">
            Evidence
            <span className="evidence-count">{evidence.length}</span>
          </h3>
          <ol className="bounded-evidence">
            {evidence.map((item, index) => (
              <li key={`${item.evidenceType}-${index}`}>
                <span className="evidence-type">
                  {evidenceTypeLabel(item.evidenceType)}
                </span>
                <p>{item.summary}</p>
              </li>
            ))}
          </ol>
        </>
      ) : (
        <p className="inspection-empty">
          {symptomSummary
            ? "No bounded evidence items in this context."
            : "Evidence will appear once incident context is built."}
        </p>
      )}
    </div>
  );
}

function AuditPanel({
  incidentId,
  state,
  events,
}: {
  incidentId: string | null;
  state: LoadState;
  events: AuditEvent[];
}) {
  return (
    <div
      className="panel inspection-panel"
      role="tabpanel"
      id="inspection-panel-audit"
      aria-labelledby="inspection-tab-audit"
    >
      {!incidentId ? (
        <p className="inspection-empty">
          Audit events appear after an incident execution starts.
        </p>
      ) : null}
      {incidentId && state === "loading" ? (
        <p className="inspection-empty">Loading audit trail…</p>
      ) : null}
      {incidentId && state === "error" ? (
        <p className="inspection-empty">Unable to load the audit trail.</p>
      ) : null}
      {incidentId && state === "empty" ? (
        <p className="inspection-empty">No durable lifecycle events yet.</p>
      ) : null}
      {incidentId && state === "loaded" ? (
        <ol className="audit-list">
          {events.map((item, index) => (
            <AuditRow key={`${item.event_type}-${item.timestamp}-${index}`} event={item} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  const meta = usefulAuditMetadata(event.metadata);
  return (
    <li className="audit-row">
      <time className="audit-time" dateTime={event.timestamp}>
        {formatAuditClock(event.timestamp)}
      </time>
      <div className="audit-body">
        <p className="audit-title">{formatAuditEventName(event.event_type)}</p>
        {meta.length > 0 ? (
          <dl className="audit-meta">
            {meta.map((row) => (
              <div key={row.key}>
                <dt>{row.label}</dt>
                <dd className={row.secondary ? "audit-secondary" : undefined}>
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </li>
  );
}

function EvaluationPanel({
  state,
  evaluation,
}: {
  state: LoadState;
  evaluation: BaselineEvaluation | null;
}) {
  return (
    <div
      className="panel inspection-panel"
      role="tabpanel"
      id="inspection-panel-evaluation"
      aria-labelledby="inspection-tab-evaluation"
    >
      {state === "loading" || state === "idle" ? (
        <p className="inspection-empty">
          Running deterministic baseline…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="inspection-empty">
          Unable to load the deterministic evaluation baseline.
        </p>
      ) : null}
      {state === "loaded" && evaluation ? (
        <EvaluationResults evaluation={evaluation} />
      ) : null}
    </div>
  );
}

function EvaluationResults({
  evaluation,
}: {
  evaluation: BaselineEvaluation;
}) {
  const rows = evaluationScenarioRows(evaluation.scenario_results);
  return (
    <>
      <p className="eval-mode">{baselineModeLabel(evaluation.evaluation_mode)}</p>
      <p className="inspection-caption">{DETERMINISTIC_BASELINE_EXPLAIN}</p>
      <dl className="eval-metrics">
        <div>
          <dt>Scenarios passed</dt>
          <dd>
            {formatPassedScenarios(
              evaluation.passed_scenarios,
              evaluation.total_scenarios,
            )}
          </dd>
        </div>
        <div>
          <dt>Root-cause accuracy</dt>
          <dd>{formatUnitInterval(evaluation.root_cause_accuracy)}</dd>
        </div>
        <div>
          <dt>Action accuracy</dt>
          <dd>{formatUnitInterval(evaluation.recommended_action_accuracy)}</dd>
        </div>
        <div>
          <dt>Approval compliance</dt>
          <dd>{formatUnitInterval(evaluation.approval_compliance_rate)}</dd>
        </div>
        <div className={evaluation.unsafe_action_rate === 0 ? "eval-metric-unsafe" : undefined}>
          <dt>Unsafe action rate</dt>
          <dd>{formatUnsafeActionRate(evaluation.unsafe_action_rate)}</dd>
        </div>
        <div>
          <dt>Resolution rate</dt>
          <dd>{formatUnitInterval(evaluation.resolution_rate)}</dd>
        </div>
        <div>
          <dt>Health recovery</dt>
          <dd>{formatUnitInterval(evaluation.health_recovery_rate)}</dd>
        </div>
        <div>
          <dt>Remediation execution</dt>
          <dd>{formatUnitInterval(evaluation.remediation_execution_rate)}</dd>
        </div>
        <div>
          <dt>Average investigation steps</dt>
          <dd>{formatAverageInvestigationSteps(evaluation.average_investigation_steps)}</dd>
        </div>
      </dl>
      <ul className="eval-scenarios">
        {rows.map((row) => (
          <li key={row.scenarioId}>
            <p className="eval-scenario-id">{row.scenarioId}</p>
            <p className="eval-scenario-result">
              <span className={row.passed ? "eval-pass" : "eval-fail"}>
                {row.passed ? "PASS" : "FAIL"}
              </span>
              <span>Root cause {row.rootCauseCorrect ? "✓" : "✗"}</span>
              <span>Action {row.actionCorrect ? "✓" : "✗"}</span>
              <span>Resolved {row.resolved ? "✓" : "✗"}</span>
            </p>
            <p className="eval-scenario-metrics">
              {formatLatency(row.finalP95LatencyMs)} p95 ·{" "}
              {formatErrorRate(row.finalErrorRatePercent)} errors
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
