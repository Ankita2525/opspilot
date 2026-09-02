"""Tests for Cloud Run ephemeral live lab deployment profile."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
SERVICE_YAML = CLOUD_RUN_DIR / "service.yaml"
PROMETHEUS_YML = CLOUD_RUN_DIR / "prometheus.yml"
LOCAL_COMPOSE = ROOT / "docker-compose.cloud-run-local.yml"


def _service_spec() -> dict:
    import yaml

    return yaml.safe_load(SERVICE_YAML.read_text())


def test_only_opspilot_has_public_port() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    ingress = [c for c in containers if c.get("ports")]
    assert len(ingress) == 1
    assert ingress[0]["name"] == "opspilot"
    assert ingress[0]["ports"][0]["containerPort"] == 8000


def test_sidecars_use_localhost_command_hosts() -> None:
    text = SERVICE_YAML.read_text()
    for port, name in (
        ("8081", "checkout-api"),
        ("8082", "auth-service"),
        ("8083", "payments-service"),
        ("8084", "provider-service"),
    ):
        assert f'"127.0.0.1", "--port", "{port}"' in text
        assert name in text


def test_min_and_max_instances() -> None:
    annotations = _service_spec()["spec"]["template"]["metadata"]["annotations"]
    assert annotations["autoscaling.knative.dev/minScale"] == "0"
    assert annotations["autoscaling.knative.dev/maxScale"] == "1"


def test_prometheus_scrapes_localhost_sidecars() -> None:
    text = PROMETHEUS_YML.read_text()
    for port in ("8081", "8082", "8083", "8084"):
        assert f"127.0.0.1:{port}" in text
    assert "checkout-api:" not in text


def test_local_compose_uses_localhost_urls() -> None:
    text = LOCAL_COMPOSE.read_text()
    assert "CHECKOUT_API_URL: http://127.0.0.1:8081" in text
    assert "OPSPILOT_DEPLOYMENT_PROFILE: ephemeral_live_lab" in text
    assert "network_mode: service:backend" in text


def test_no_simulator_fallback_in_cloud_run_env() -> None:
    env_example = (CLOUD_RUN_DIR / "env.example").read_text()
    assert "OPSPILOT_TELEMETRY_MODE=live" in env_example
    assert "reference" not in env_example.lower().split("telemetry_mode")[1].split("\n")[0]


def test_container_concurrency_bounded() -> None:
    spec = _service_spec()["spec"]["template"]["spec"]
    assert spec["containerConcurrency"] == 1


def test_prometheus_listen_localhost_only() -> None:
    text = SERVICE_YAML.read_text()
    assert "--web.listen-address=127.0.0.1:9090" in text


def test_backend_env_localhost_service_urls() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    env = {item["name"]: item["value"] for item in opspilot.get("env", []) if "value" in item}
    assert env["CHECKOUT_API_URL"] == "http://127.0.0.1:8081"
    assert env["OPSPILOT_PROMETHEUS_URL"] == "http://127.0.0.1:9090"
