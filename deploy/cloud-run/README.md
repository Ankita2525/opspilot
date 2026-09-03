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
- **Six** Secret Manager active versions (within free allowance)

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

## Pre-deploy gate (required)

No real Cloud Run deploy may run unless **all three** checks pass, in order.

**A. Cloud Run render/profile tests**

```bash
uv run pytest -q tests/test_cloud_run_profile.py tests/test_cloud_run_render.py tests/test_cloud_run_db_preflight.py
```

**B. Production DB secret parse + real `SELECT 1`**

Use the same `psycopg`/libpq as the Cloud Run images (`uv run`, lockfile `psycopg==3.3.4`). Supply the Secret Manager value only as an environment variable; do not echo it.

```bash
DATABASE_URL="$(gcloud secrets versions access latest \
  --secret=opspilot-database-url \
  --project=opspilot-live-lab)" \
  uv run python deploy/cloud-run/preflight_database.py
```

Expected stdout:

```
DATABASE_URL parse: PASS
PostgreSQL connection: PASS
SELECT 1: PASS
```

The secret must be **exactly one** canonical Neon URI, for example:

`postgresql://USER:PASSWORD@HOST/DB?sslmode=require&channel_binding=require`

Do not concatenate two URLs. Do not strip `channel_binding` or weaken `sslmode`.

**C. Cloud Run server-side validation (dry-run only)**

```bash
gcloud run services replace \
  deploy/cloud-run/rendered/service.yaml \
  --project=opspilot-live-lab \
  --region=us-central1 \
  --dry-run
```

Real deploy (only after A–C pass, and only when explicitly requested):

```bash
gcloud run services replace \
  deploy/cloud-run/rendered/service.yaml \
  --project=opspilot-live-lab \
  --region=us-central1
```

## Public access

The rendered manifest sets `run.googleapis.com/invoker-iam-disabled: "true"` on the
service. This is Google's recommended mechanism for unauthenticated portfolio access
without granting `allUsers` the Cloud Run Invoker role.

The runtime service account is **not** granted Cloud Run Invoker. It is the application
identity only.

## Runtime service account IAM (external)

`opspilot-cloud-run@<PROJECT_ID>.iam.gserviceaccount.com` needs:

| Grant | Scope |
|-------|-------|
| `roles/secretmanager.secretAccessor` | **Only** the six secrets below (secret-level IAM, not project-wide) |
| `roles/artifactregistry.reader` | `opspilot` Artifact Registry repository only, if required for custom image pull by this identity |

Do **not** grant `roles/run.invoker` to the runtime service account.

## Deployer IAM (external)

`post2ankitak@gmail.com` (or CI deploy principal):

| Grant | Scope |
|-------|-------|
| `roles/iam.serviceAccountUser` | On `opspilot-cloud-run@...` only |
| Project owner / Cloud Run Admin | Existing deployment permissions are sufficient |

Do not add redundant broad Secret Manager accessor roles on the deployer if secret
creation and IAM binding are done explicitly.

## Secret Manager (exactly six active versions)

| Secret | Containers |
|--------|------------|
| `opspilot-database-url` | `opspilot` (`DATABASE_URL`), `checkout-api` (`CHECKOUT_DATABASE_URL`) |
| `opspilot-groq-api-key` | `opspilot` |
| `opspilot-sandbox-control-token` | `opspilot`, all sandbox sidecars |
| `opspilot-turnstile-secret` | `opspilot` |
| `opspilot-grafana-loki-authorization` | `opspilot` (`OPSPILOT_LOKI_AUTHORIZATION`), `otel-collector` |
| `opspilot-prometheus-config` | `prometheus` volume (`prometheus.yml` content) |

Create `opspilot-grafana-loki-authorization` with the **complete** HTTP Authorization
header value Grafana Cloud expects, e.g. `Basic <base64(user_id:api_key)>`.

Checkout safely reuses `opspilot-database-url`: production already maps
`CHECKOUT_DATABASE_URL=${DATABASE_URL}`; checkout only uses the `checkout_orders` table.

## Config delivery

| Component | Delivery |
|-----------|----------|
| **Prometheus** | Secret Manager volume mount (`opspilot-prometheus-config`) — Prometheus requires a file |
| **OTEL collector** | Non-secret YAML embedded in `OTEL_COLLECTOR_CONFIG` env var; loaded via `--config=env:OTEL_COLLECTOR_CONFIG`. Grafana auth substituted at runtime via `${OPSPILOT_LOKI_AUTHORIZATION}` |

Create the Prometheus config secret externally:

```bash
gcloud secrets create opspilot-prometheus-config --data-file=deploy/cloud-run/prometheus.yml
```

## Concurrency

`containerConcurrency: 10` (deliberate, not Cloud Run default 80):

- `max-instances: 1` caps the deployment to a single instance
- **Global Postgres lease** serializes live incident mutations (only one fault/rollback at a time)
- Concurrent **read-only** traffic (`/ready`, `/api/sandbox/status`, provenance GET) can proceed during SSE Request A

## Local validation (no GCP)

```bash
docker compose -f docker-compose.cloud-run-local.yml up --build
curl -fsS http://localhost:8000/ready
python scripts/validate_cloud_run_local.py
```

## Files

| File | Purpose |
|------|---------|
| `service.yaml.tmpl` | Cloud Run multi-container template |
| `render_service.py` | Substitute project/region/tag + deploy-time vars |
| `preflight_database.py` | Parse `DATABASE_URL` with libpq and run `SELECT 1` (no secret output) |
| `render.vars.example` | Non-secret deploy-time values |
| `prometheus.yml` | Localhost scrape targets (uploaded to Secret Manager) |
| `otel-collector.yaml` | Source for embedded OTEL config (not stored in Secret Manager) |
| `env.example` | Full environment reference |

Do **not** deploy from this repository without creating secrets and rendering the manifest.
