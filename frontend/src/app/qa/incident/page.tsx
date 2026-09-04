"use client";

import type { ReactNode } from "react";

import { ApprovalPanel } from "@/components/ApprovalPanel";
import { CommandCenterHeader } from "@/components/CommandCenterHeader";
import { HypothesisPanel } from "@/components/HypothesisPanel";
import { IncidentHeader } from "@/components/IncidentHeader";
import { InvestigationTimeline } from "@/components/InvestigationTimeline";
import { LifecycleRail } from "@/components/LifecycleRail";
import { LiveProvenancePanel } from "@/components/LiveProvenancePanel";
import { RecoveryPanel } from "@/components/RecoveryPanel";
import { ServiceTopology } from "@/components/ServiceTopology";
import { TelemetryBands } from "@/components/TelemetryBands";
import { lifecycleSteps } from "@/lib/command-center";
import type { TimelineStep } from "@/lib/live-incident";
import type { ApprovalRequest, IncidentApprovalResponse, LiveProvenance, Metrics } from "@/lib/types";

const STEPS: TimelineStep[] = [
  { id: "inspect_metrics", label: "Inspect metrics", status: "completed" },
  { id: "inspect_deployments", label: "Inspect deployments", status: "completed" },
  { id: "inspect_logs", label: "Inspect logs", status: "completed" },
  { id: "build_context", label: "Build incident context", status: "completed" },
  { id: "load_skills", label: "Load diagnostic skills", status: "completed" },
  {
    id: "generate_hypothesis",
    label: "Generate root-cause hypothesis",
    status: "completed",
  },
];

const RUNNING_STEPS: TimelineStep[] = STEPS.map((step, index) =>
  index < 2
    ? step
    : index === 2
      ? { ...step, status: "running" }
      : { ...step, status: "pending" },
);

const HYPOTHESIS = {
  rootCause:
    "Deployment v1.18.3 introduced configuration changes that caused database connection pool exhaustion.",
  confidence: 0.9,
  recommendedAction: "rollback_deployment",
  recommendationSummary:
    "Rollback the checkout-api deployment to restore pool headroom.",
};

const APPROVAL: ApprovalRequest = {
  type: "approval_required",
  proposal_id: "prop_fixture",
  incident_id: "inc_fixture",
  action: "rollback_deployment",
  service: "checkout-api",
  version: "v1.18.3",
  risk_level: "high_risk",
  message: "Rollback checkout-api from v1.18.3 to the previous healthy revision.",
};

const ORIGINAL: Metrics = {
  service: "checkout-api",
  p95_latency_ms: 4850,
  error_rate_percent: 18.4,
  timestamp: "2026-09-04T00:00:00Z",
};

const APPROVAL_RESULT: IncidentApprovalResponse = {
  incident_id: "inc_fixture",
  status: "resolved",
  execution_success: true,
  recovered_p95_latency_ms: 479,
  recovered_error_rate_percent: 0.4,
  resolved: true,
  approval_status: "approved",
};

const PROVENANCE: LiveProvenance = {
  run_id: "run_fixture",
  incident_id: "inc_fixture",
  telemetry_mode: "live",
  environment: "ephemeral_live_lab",
  service: "checkout-api",
  service_revision: "v1.18.3",
  started_at: "2026-09-04T00:00:00Z",
  baseline: {
    sample_count: 42,
    p95_latency_ms: 210,
    error_rate: 0.2,
  },
  degraded: {
    sample_count: 38,
    p95_latency_ms: 4850,
    error_rate: 18.4,
  },
  diagnosis: {
    provider: "groq",
    model: "openai/gpt-oss-20b",
    evidence_count: 6,
    generated_at: "2026-09-04T00:01:00Z",
  },
  remediation: {
    typed_action: "rollback_deployment",
    approval_required: true,
    approved_at: "2026-09-04T00:02:00Z",
    executed_at: "2026-09-04T00:02:10Z",
  },
  recovery: {
    sample_count: 40,
    p95_latency_ms: 479,
    error_rate: 0.4,
    latest_metric_timestamp: "2026-09-04T00:03:00Z",
    latest_log_timestamp: "2026-09-04T00:03:00Z",
    all_samples_post_remediation: true,
    verified: true,
  },
  ground_truth_visible_to_agent: false,
  evidence_manifest_hash: "a1b2c3d4e5f67890",
};

const SKILLS = ["deployment-regression", "postgres-diagnostics"];

function FixtureShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="qa-fixture">
      <h2 className="section-heading">{title}</h2>
      <div className="qa-fixture-body">{children}</div>
    </section>
  );
}

export default function IncidentQaPage() {
  return (
    <div className="page-shell command-center">
      <CommandCenterHeader labStatus="degraded" telemetryMode="live" />
      <main className="workspace command-workspace">
        <p className="status-copy">
          Local visual fixtures only — no live incident traffic.
        </p>

        <FixtureShell title="A — Degraded / investigating">
          <IncidentHeader
            phase="investigating"
            service="checkout-api"
            title="Checkout API latency after deployment"
            telemetryMode="live"
            live
            eventCount={12}
            revision="v1.18.3"
          />
          <LifecycleRail
            steps={lifecycleSteps({
              phase: "investigating",
              hasBaseline: true,
              hasHypothesis: false,
              hasApproval: false,
              resolved: false,
            })}
          />
          <div className="incident-ops-grid">
            <ServiceTopology affectedService="checkout-api" phase="degraded" />
            <TelemetryBands
              mode="live"
              baseline={{
                p95_latency_ms: 210,
                error_rate_percent: 0.2,
                sample_count: 42,
              }}
              degraded={{
                p95_latency_ms: 4850,
                error_rate_percent: 18.4,
                sample_count: 38,
              }}
              recovery={null}
            />
          </div>
          <div className="workspace-grid workspace-grid-solo">
            <InvestigationTimeline
              steps={RUNNING_STEPS}
              skills={[]}
              symptomSummary="Checkout p95 and error rate rose after revision v1.18.3."
            />
          </div>
        </FixtureShell>

        <FixtureShell title="B — Awaiting approval">
          <IncidentHeader
            phase="active"
            service="checkout-api"
            title="Checkout API latency after deployment"
            telemetryMode="live"
            eventCount={28}
            revision="v1.18.3"
          />
          <LifecycleRail
            steps={lifecycleSteps({
              phase: "active",
              hasBaseline: true,
              hasHypothesis: true,
              hasApproval: true,
              resolved: false,
            })}
          />
          <div className="workspace-grid">
            <InvestigationTimeline steps={STEPS} skills={SKILLS} />
            <HypothesisPanel
              hypothesis={HYPOTHESIS}
              evidenceCount={6}
            />
          </div>
          <ApprovalPanel
            approvalRequest={APPROVAL}
            recommendedAction="rollback_deployment"
            proposedVersion="v1.18.3"
            busy={false}
            onApprove={() => undefined}
            onReject={() => undefined}
          />
        </FixtureShell>

        <FixtureShell title="C — Resolved / recovered">
          <IncidentHeader
            phase="resolved"
            service="checkout-api"
            title="Checkout API latency after deployment"
            telemetryMode="live"
            eventCount={41}
            revision="v1.18.3"
          />
          <LifecycleRail
            steps={lifecycleSteps({
              phase: "resolved",
              hasBaseline: true,
              hasHypothesis: true,
              hasApproval: true,
              resolved: true,
            })}
          />
          <div className="incident-ops-grid">
            <ServiceTopology affectedService="checkout-api" phase="rollback" />
            <TelemetryBands
              mode="live"
              baseline={{
                p95_latency_ms: 210,
                error_rate_percent: 0.2,
                sample_count: 42,
              }}
              degraded={{
                p95_latency_ms: 4850,
                error_rate_percent: 18.4,
                sample_count: 38,
              }}
              recovery={{
                p95_latency_ms: 479,
                error_rate_percent: 0.4,
                sample_count: 40,
              }}
            />
          </div>
          <RecoveryPanel
            original={ORIGINAL}
            approval={APPROVAL_RESULT}
            freshTelemetryVerified
          />
          <LiveProvenancePanel provenance={PROVENANCE} />
        </FixtureShell>
      </main>
    </div>
  );
}
