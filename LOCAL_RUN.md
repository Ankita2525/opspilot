# Local OpsPilot

## Native development

```bash
uv sync
export OPSPILOT_MODEL_PROVIDER=deterministic
uv run uvicorn backend.app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
export NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

For live Groq instead of deterministic demo mode:

```bash
export OPSPILOT_MODEL_PROVIDER=groq
export GROQ_API_KEY=your_key_here
```

## Docker

`OPSPILOT_MODEL_PROVIDER` is required for every Compose run. Compose will fail
fast if it is missing. Set it explicitly to `deterministic` or `groq`.

### Deterministic demo (no Groq key)

```bash
OPSPILOT_MODEL_PROVIDER=deterministic docker compose up --build
```

Open http://localhost:3000. The API is at http://localhost:8000.

## Live incident sandbox

The live sandbox runs real FastAPI microservices, generates on-demand HTTP
workload, scrapes Prometheus metrics, and queries Loki logs. Reference mode
continues to use deterministic simulator fixtures for CI and evaluation.

```bash
export OPSPILOT_MODEL_PROVIDER=deterministic
export OPSPILOT_TELEMETRY_MODE=live
export OPSPILOT_PROMETHEUS_URL=http://localhost:9090
export OPSPILOT_LOKI_URL=http://localhost:3100
export SANDBOX_CONTROL_TOKEN=sandbox-control-test-token

docker compose --profile live up --build
```

Validate measured before/after behavior locally:

```bash
uv run python scripts/validate_live_sandbox.py
```

### Groq

```bash
export GROQ_API_KEY=your_key_here
OPSPILOT_MODEL_PROVIDER=groq docker compose up --build
```

Copy `.env.example` to `.env` for a fuller local variable template. Never commit `.env`.
If you use a `.env` file, still set `OPSPILOT_MODEL_PROVIDER` there explicitly.
