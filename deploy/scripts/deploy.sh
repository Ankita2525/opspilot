#!/usr/bin/env bash
# Safe deployment script for GitHub Actions SSH deploy (manual/disabled workflow).
# Does NOT run unless explicitly invoked on the target VM.
set -euo pipefail

REPO_DIR="${OPSPILOT_DEPLOY_DIR:-/opt/opspilot}"
COMPOSE_FILE="${OPSPILOT_COMPOSE_FILE:-docker-compose.prod.yml}"
READINESS_TIMEOUT="${OPSPILOT_READINESS_TIMEOUT:-180}"
READINESS_URL="${OPSPILOT_READINESS_URL:-http://127.0.0.1:8000/ready}"

cd "$REPO_DIR"

echo "==> Pulling latest images / rebuilding..."
docker compose -f "$COMPOSE_FILE" pull --ignore-buildable || true
docker compose -f "$COMPOSE_FILE" build --pull

echo "==> Starting stack..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "==> Waiting for readiness (timeout ${READINESS_TIMEOUT}s)..."
deadline=$((SECONDS + READINESS_TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
  if curl -fsS "$READINESS_URL" | grep -q '"status":"ready"'; then
    echo "==> Ready"
    break
  fi
  sleep 5
done

if ! curl -fsS "$READINESS_URL" | grep -q '"status":"ready"'; then
  echo "ERROR: readiness check failed" >&2
  docker compose -f "$COMPOSE_FILE" ps
  exit 1
fi

echo "==> Post-deploy smoke tests..."
curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'
curl -fsS http://127.0.0.1:8000/api/scenarios | grep -q checkout

echo "==> Deploy complete"
