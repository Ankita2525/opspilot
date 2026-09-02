# Public Ephemeral Live Incident Lab — Cloud Run Profile

This deployment profile is **separate** from the [full production architecture](../docker-compose.prod.yml)
(VM + Caddy + segmented Docker networks). It exists for the public portfolio demo.

## Goals

- `min instances = 0`, `max instances = 1`
- Single public ingress (OpsPilot FastAPI on port 8000)
- Sandbox + observability sidecars on `127.0.0.1` inside the instance
- Real HTTP faults, real telemetry, no simulator fallback in live mode
- Neon PostgreSQL for durable lease / incident / provenance state
- Grafana Cloud Loki for logs (OTEL collector sidecar)

## Container topology

| Container | Role | Address |
|-----------|------|---------|
| `opspilot` | **Ingress** — FastAPI API | `0.0.0.0:8000` |
| `checkout-api` | Sidecar | `127.0.0.1:8081` |
| `auth-service` | Sidecar | `127.0.0.1:8082` |
| `payments-service` | Sidecar | `127.0.0.1:8083` |
| `provider-service` | Sidecar | `127.0.0.1:8084` |
| `prometheus` | Sidecar (ephemeral TSDB) | `127.0.0.1:9090` |
| `otel-collector` | Sidecar | `127.0.0.1:4318` |

## Concurrency

`containerConcurrency: 10` (deliberate, not Cloud Run default 80):

- `max-instances: 1` caps the deployment to a single instance
- **Global Postgres lease** serializes live incident mutations (only one fault/rollback at a time)
- Concurrent **read-only** traffic (`/ready`, `/api/sandbox/status`, provenance GET) can proceed during SSE Request A
- Request A (SSE) ends at `approval_required` before Request B (approval) begins — no overlap on the remediation path

## Outbound networking

All containers in a Cloud Run multi-container instance share the **same network namespace**. Outbound Internet from any container reaches external dependencies:

| Dependency | Used by |
|------------|---------|
| Neon PostgreSQL | OpsPilot (incidents, lease, provenance, checkpoints), checkout-api (connection pool) |
| Groq API | OpsPilot (hypothesis generation in live mode) |
| Cloudflare Turnstile | OpsPilot (public abuse guard when enabled) |
| Grafana Cloud Loki | OTEL collector (log export); OpsPilot queries Loki HTTP API |

Sandbox sidecars (auth, payments, provider) do not require direct Neon access except checkout-api.

## Container startup

**Request A** (investigation stream):

1. Acquire global Postgres lease
2. Warm sandbox, baseline traffic, activate fault, degraded traffic
3. Collect Prometheus/Loki evidence
4. LLM diagnosis → `approval_required`
5. Persist bounded provenance manifest
6. SSE ends (instance may scale to zero)

**Request B** (approval):

1. Reconcile durable incident + provenance from Postgres
2. Rebuild live session; confirm faulty revision active
3. Resume LangGraph checkpoint; execute rollback
4. Generate **fresh** post-remediation traffic + verification
5. Update provenance recovery window; release lease

## Local validation (no GCP)

```bash
docker compose -f docker-compose.cloud-run-local.yml up --build
curl -fsS http://localhost:8000/ready
python scripts/validate_cloud_run_local.py
```

## Honest limitations

- Three controlled incident classes (checkout, auth, payments)
- One remediation action (`rollback_deployment`)
- Shared public sandbox (global lease)
- Scale-to-zero cold start (~30–90s)
- Ephemeral Prometheus history between instances (recovery re-collects fresh telemetry)
- Reference evaluation mode remains separate (`OPSPILOT_TELEMETRY_MODE=reference`)

## Files

| File | Purpose |
|------|---------|
| `service.yaml` | Cloud Run multi-container template |
| `prometheus.yml` | Localhost scrape targets |
| `otel-collector.yaml` | OTLP → Grafana Cloud Loki |
| `env.example` | Environment placeholders |

Do **not** deploy from this repository without substituting secrets and building images.
