# OpsPilot

**Autonomous Production Engineering Agent**

Production demo: https://opspilot-chi.vercel.app

OpsPilot is an AI-powered production incident-response system that investigates real sandbox services, gathers live telemetry and logs, forms evidence-grounded root-cause hypotheses, proposes remediation, requires human approval for high-risk actions, executes approved rollbacks, and verifies recovery using fresh post-action telemetry.

The public demo operates controlled ephemeral infrastructure rather than displaying simulated incident results.

## Highlights

- Live incident investigation across metrics, logs, deployments, and runtime state
- Evidence-grounded root-cause hypotheses
- Human-in-the-loop approval for high-risk remediation
- Controlled rollback execution
- Fresh post-action recovery verification
- Prometheus metrics and Grafana Cloud Loki logs
- Durable incident, approval, lease, and provenance state in PostgreSQL
- Structured LLM output with bounded model fallback
- Deterministic evaluation separated from live LLM execution
- Shared-sandbox safety controls and rate limits
- Production deployment on Vercel and Google Cloud Run

## Incident Workflow

1. Select an incident scenario
2. Activate a controlled sandbox fault
3. Collect baseline and degraded telemetry
4. Inspect metrics, logs, deployment history, and runtime state
5. Build bounded incident context
6. Select diagnostic skills
7. Generate an evidence-based root-cause hypothesis
8. Evaluate the proposed remediation
9. Require human approval when the action is high risk
10. Execute approved remediation
11. Collect fresh post-action telemetry
12. Verify whether recovery actually occurred
13. Persist provenance and audit events

Approval and recovery are deliberately separate states. An approved remediation is never reported as resolved unless fresh telemetry confirms recovery.

## Built-in Live Incidents

### Checkout API

A deployment introduces PostgreSQL connection-pool pressure and increased latency.

### Authentication Service

A deployment introduces JWT signature-verification failures.

### Payments Service

A deployment introduces upstream provider timeouts and elevated request failures.

## Production Architecture

Browser -> Vercel Next.js -> same-origin API proxy -> Google Cloud Run

The Cloud Run live lab contains:

- OpsPilot FastAPI service
- checkout-api
- auth-service
- payments-service
- provider-service
- Prometheus
- OpenTelemetry Collector

External production services include:

- Neon PostgreSQL
- Groq
- Grafana Cloud Loki
- Google Secret Manager
- Cloudflare Turnstile

The public Cloud Run environment scales to zero when idle.

The repository also includes a Docker Compose production architecture with isolated services and observability boundaries.

## AI Diagnosis

The live diagnosis path uses Groq-hosted OpenAI-compatible models with structured output.

Production behavior includes:

- `openai/gpt-oss-20b` primary diagnosis model
- bounded fallback for eligible structured-output failures
- typed model responses
- explicit provider-error handling
- bounded retry behavior
- per-incident and daily quota accounting

The agent does not receive hidden incident ground truth during live diagnosis.

## Evidence and Observability

OpsPilot investigates using bounded evidence from:

- Prometheus
- OpenTelemetry
- Grafana Cloud Loki
- deployment history
- runtime service state
- diagnostic skills

The UI exposes:

- incident lifecycle
- live telemetry
- service topology
- evidence
- diagnosis
- proposed action
- approval state
- recovery evidence
- provenance
- audit trail

## Safety

The public live lab includes:

- human approval for risky remediation
- a global sandbox lease
- per-session incident limits
- Cloudflare Turnstile
- self-reverting fault TTLs
- explicit fault cleanup
- durable approval records
- post-action recovery verification
- quarantine behavior when cleanup cannot be proven
- sanitized public errors
- no hidden chain-of-thought exposure

## Deterministic Evaluation

The Evaluation view uses a deterministic reference provider rather than the live LLM.

It validates orchestration and safety behavior such as:

- root-cause accuracy
- action accuracy
- approval compliance
- unsafe-action rate
- remediation execution
- health recovery
- resolution rate

These reference results are not presented as live-model accuracy.

## Tech Stack

**Backend:** Python 3.12, FastAPI, Pydantic, LangGraph, PostgreSQL

**AI:** Groq, OpenAI-compatible structured output

**Frontend:** Next.js, React, TypeScript, Tailwind CSS

**Observability:** Prometheus, OpenTelemetry, Grafana Cloud Loki

**Infrastructure:** Google Cloud Run, Docker, Docker Compose, Vercel, GitHub Actions, Google Secret Manager, Cloudflare Turnstile

## Repository

- `backend/` - API and incident orchestration
- `frontend/` - production command center
- `sandbox/` - controlled services and fault injection
- `simulator/` - deterministic reference environment
- `observability/` - Prometheus and OTEL configuration
- `deploy/cloud-run/` - public Cloud Run profile
- `tests/` - backend, safety, integration, and evaluation tests
- `scripts/` - validation utilities

## Local Development

See `LOCAL_RUN.md`.

Cloud Run deployment details are in `deploy/cloud-run/README.md`.

## Design Principle

OpsPilot treats diagnosis, approval, execution, and recovery as separate verifiable stages.

The goal is to make autonomous production-engineering behavior observable, bounded, reversible, and auditable.
