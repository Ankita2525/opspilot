# Public Ephemeral Live Incident Lab — Cloud Run Profile

This deployment profile is **separate** from the [full production architecture](../../docker-compose.prod.yml)
(VM + Caddy + segmented Docker networks). It exists for the public portfolio demo.

## Goals

- `min instances = 0`, `max instances = 1`
- Single public ingress (OpsPilot FastAPI on port 8000)
- Sandbox + observability sidecars share the instance network namespace
- Sidecars **listen on `0.0.0.0`** so Cloud Run startup probes can reach them
- Inter-container URLs stay `http://127.0.0.1:<port>` (loopback clients)
- Real HTTP faults, real telemetry, no simulator fallback in live mode
- Neon PostgreSQL for durable lease / incident / provenance state
- Grafana Cloud Loki for logs (OTEL collector sidecar)
- **Six** Secret Manager active versions (within free allowance)

## Container topology

| Container | Role | Listen | Client URL |
|-----------|------|--------|------------|
| `opspilot` | **Ingress** — FastAPI API | `0.0.0.0:8000` | public |
| `checkout-api` | Sidecar | `0.0.0.0:8081` | `http://127.0.0.1:8081` |
| `auth-service` | Sidecar | `0.0.0.0:8082` | `http://127.0.0.1:8082` |
| `payments-service` | Sidecar | `0.0.0.0:8083` | `http://127.0.0.1:8083` |
| `provider-service` | Sidecar | `0.0.0.0:8084` | `http://127.0.0.1:8084` |
| `prometheus` | Sidecar (ephemeral TSDB) | `0.0.0.0:9090` | `http://127.0.0.1:9090` |
| `otel-collector` | Sidecar | `0.0.0.0:4318` | `http://127.0.0.1:4318` |

Only `opspilot` declares `ports:` / public ingress. Sidecar `0.0.0.0` binds are for
Cloud Run probes + in-instance clients; they are not separate public services.
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

No real Cloud Run deploy may run unless **all** checks pass, in order.

Secret Manager versions are **pinned** in `render.vars` (never `latest`). Cloud Run
only creates a new revision when the service template changes; rotating a secret
value without bumping its pin does not create a fresh revision.

**A. Pinned Secret Manager versions exist + ENABLED; DB pin passes `SELECT 1`**

```bash
uv run python deploy/cloud-run/preflight_secret_pins.py \
  --project=opspilot-live-lab \
  --vars-file=deploy/cloud-run/render.vars
```

**B. Header-bound secret hygiene (sandbox token + Grafana Authorization)**

Rejects CR/LF, surrounding whitespace, empty values, and malformed `Basic` auth.

```bash
uv run python deploy/cloud-run/preflight_header_secrets.py \
  --project=opspilot-live-lab \
  --vars-file=deploy/cloud-run/render.vars
```

**C. Grafana Cloud Loki auth/query (`GET /loki/api/v1/labels`)**

```bash
uv run python deploy/cloud-run/preflight_loki.py \
  --project=opspilot-live-lab \
  --vars-file=deploy/cloud-run/render.vars
```

**D. Probe-replay (revision 00003 class): `/health` isolated, `/ready` degrades**

```bash
uv run python deploy/cloud-run/preflight_probe_replay.py
```

**E. Cloud Run render/profile + DB + secret-pin + probe-replay unit tests**

```bash
uv run pytest -q \
  tests/test_cloud_run_profile.py \
  tests/test_cloud_run_render.py \
  tests/test_cloud_run_db_preflight.py \
  tests/test_cloud_run_secret_pins.py \
  tests/test_cloud_run_probe_replay.py \
  tests/test_header_secret_hygiene.py
```

**F. Render with an immutable image tag and pinned secret versions**

```bash
python deploy/cloud-run/render_service.py \
  --project-id opspilot-live-lab \
  --region us-central1 \
  --image-tag <git-sha> \
  --vars-file deploy/cloud-run/render.vars
```

**G. Cloud Run server-side validation (dry-run only)**

```bash
gcloud run services replace \
  deploy/cloud-run/rendered/service.yaml \
  --project=opspilot-live-lab \
  --region=us-central1 \
  --dry-run
```

### Health contract

| Endpoint | Role | Cloud Run probe? |
|----------|------|------------------|
| `GET /health` | Process-local: FastAPI accepts HTTP. **No** DB/HTTP/Loki/Prometheus/lease I/O. | **Yes** — startup + liveness |
| `GET /healthz` | Internal alias only. **Do not** use publicly: Google Frontend intercepted exact `/healthz` with HTML 404 (rev `00004` evidence). | No |
| `GET /ready` | Deep diagnostic dependency health (async, cached, degraded-tolerant). Loki uses authenticated `/loki/api/v1/labels`. | **No** |

Revision annotation `run.googleapis.com/startup-cpu-boost: "true"` is enabled while
preserving `minScale=0`, `maxScale=1`, and `cpu-throttling=true`.

### Canary deploy plan (required while `00004-zws` is healthy)

`gcloud run services replace` has **no** `--no-traffic` flag. Pin traffic first.

```bash
# 1) Explicitly pin 100% traffic to the known-healthy revision
gcloud run services update-traffic opspilot-live-lab \
  --project=opspilot-live-lab --region=us-central1 \
  --to-revisions=opspilot-live-lab-00004-zws=100

# 2) Replace creates a new LATEST revision; traffic stays on 00004 while pinned
gcloud run services replace deploy/cloud-run/rendered/service.yaml \
  --project=opspilot-live-lab --region=us-central1

# 3) Wait until the new revision is Ready=True
gcloud run revisions list --service=opspilot-live-lab \
  --project=opspilot-live-lab --region=us-central1

# 4) Tag LATEST for direct canary URL (does not change percent traffic)
gcloud run services update-traffic opspilot-live-lab \
  --project=opspilot-live-lab --region=us-central1 \
  --update-tags=canary=LATEST

# 5) Smoke ONLY the tagged URL (/health, /ready, runtime, sandbox, live incident):
#    https://canary---opspilot-live-lab-<hash>-uc.a.run.app/health

# 6) Promote only after canary smoke passes
gcloud run services update-traffic opspilot-live-lab \
  --project=opspilot-live-lab --region=us-central1 \
  --to-latest --clear-tags

# 7) After promotion + cold-start verification, disable obsolete sandbox token v1:
#    gcloud secrets versions disable 1 \
#      --secret=opspilot-sandbox-control-token \
#      --project=opspilot-live-lab
```

When **no** healthy revision exists, step 1 cannot pin traffic. In that case
`services replace` is required to obtain the first Ready revision; smoke `/health`
+ `/ready` on the service URL immediately after Ready=True, then keep traffic on
that revision before further replaces.

Real replace (only after A–G pass, and only when explicitly requested):

```bash
gcloud run services replace \
  deploy/cloud-run/rendered/service.yaml \
  --project=opspilot-live-lab \
  --region=us-central1
```

The database secret must be **exactly one** canonical Neon URI, for example:

`postgresql://USER:PASSWORD@HOST/DB?sslmode=require&channel_binding=require`

Do not concatenate two URLs. Do not strip `channel_binding` or weaken `sslmode`.
Do not pin `opspilot-database-url` to disabled version `1`.
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
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/ready
python scripts/validate_cloud_run_local.py
```

## Files

| File | Purpose |
|------|---------|
| `service.yaml.tmpl` | Cloud Run multi-container template |
| `render_service.py` | Substitute project/region/tag + deploy-time vars + secret version pins |
| `preflight_database.py` | Parse `DATABASE_URL` with libpq and run `SELECT 1` (no secret output) |
| `preflight_secret_pins.py` | Confirm pinned versions are ENABLED; DB pin parse/connect/`SELECT 1` |
| `preflight_header_secrets.py` | Header-bound secret hygiene (no CR/LF/whitespace) |
| `preflight_loki.py` | Grafana Cloud Loki Authorization + `/labels` auth/query |
| `preflight_probe_replay.py` | `/health` isolation + `/ready` degradation + event-loop responsiveness |
| `render.vars.example` | Non-secret deploy-time values + explicit secret version pins |
| `prometheus.yml` | Localhost scrape targets (uploaded to Secret Manager) |
| `otel-collector.yaml` | Source for embedded OTEL config (not stored in Secret Manager) |
| `env.example` | Full environment reference |

Do **not** deploy from this repository without creating secrets and rendering the manifest.
