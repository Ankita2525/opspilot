# Public Ephemeral Live Incident Lab — Cloud Run Profile

This deployment profile is **separate** from the [full production architecture](../../docker-compose.prod.yml)
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

## Render before deploy

`service.yaml.tmpl` is the checked-in template. **Do not edit image tags manually.**

```bash
cp deploy/cloud-run/render.vars.example deploy/cloud-run/render.vars
# Edit render.vars with your Vercel origin, Turnstile site key, Grafana URLs.

python deploy/cloud-run/render_service.py \
  --project-id opspilot-live-lab \
  --region us-central1 \
  --image-tag fa215f3 \
  --vars-file deploy/cloud-run/render.vars
```

Output: `deploy/cloud-run/rendered/service.yaml` (gitignored).

Deploy externally:

```bash
gcloud run services replace deploy/cloud-run/rendered/service.yaml --region=us-central1
```

## Runtime service account

The rendered manifest sets:

`opspilot-cloud-run@<PROJECT_ID>.iam.gserviceaccount.com`

Grant IAM roles to this account externally (Secret Manager accessor, Artifact Registry reader, etc.).

## Secret Manager (minimum set)

| Secret | Containers |
|--------|------------|
| `opspilot-database-url` | `opspilot` (`DATABASE_URL`), `checkout-api` (`CHECKOUT_DATABASE_URL`) |
| `opspilot-groq-api-key` | `opspilot` |
| `opspilot-sandbox-control-token` | `opspilot`, all sandbox sidecars |
| `opspilot-turnstile-secret` | `opspilot` |
| `opspilot-grafana-loki-username` | `opspilot` (`OPSPILOT_LOKI_USERNAME`), `otel-collector` |
| `opspilot-grafana-loki-api-key` | `opspilot` (`OPSPILOT_LOKI_API_KEY`), `otel-collector` |
| `opspilot-prometheus-config` | `prometheus` volume (file content from `prometheus.yml`) |
| `opspilot-otel-collector-config` | `otel-collector` volume (file content from `otel-collector.yaml`) |

Checkout safely reuses `opspilot-database-url`: production already maps `CHECKOUT_DATABASE_URL=${DATABASE_URL}`; checkout only uses the `checkout_orders` table and does not collide with OpsPilot schema migrations.

Grafana Loki credentials are shared between the OTEL collector (push) and OpsPilot Loki query client (read) via the same username/API-key secrets with different env var names.

## Config file delivery (Prometheus + OTEL)

Cloud Run multi-container services do not support bind mounts or ConfigMaps. The only file-volume mechanism is **Secret Manager secret mounts**. `prometheus.yml` and `otel-collector.yaml` are non-secret configuration payloads stored as secrets for operational delivery only.

Create secrets externally:

```bash
gcloud secrets create opspilot-prometheus-config --data-file=deploy/cloud-run/prometheus.yml
gcloud secrets create opspilot-otel-collector-config --data-file=deploy/cloud-run/otel-collector.yaml
```

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
| `service.yaml.tmpl` | Cloud Run multi-container template |
| `render_service.py` | Substitute project/region/tag + deploy-time vars |
| `render.vars.example` | Non-secret deploy-time values |
| `prometheus.yml` | Localhost scrape targets |
| `otel-collector.yaml` | OTLP → Grafana Cloud Loki |
| `env.example` | Full environment reference |

Do **not** deploy from this repository without creating secrets and rendering the manifest.
