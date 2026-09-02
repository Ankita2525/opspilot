"""Regression tests for Cloud Run service manifest rendering."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"
RENDER_VARS_EXAMPLE = CLOUD_RUN_DIR / "render.vars.example"

SENSITIVE_ENV_NAMES = frozenset(
    {
        "DATABASE_URL",
        "CHECKOUT_DATABASE_URL",
        "GROQ_API_KEY",
        "SANDBOX_CONTROL_TOKEN",
        "OPSPILOT_TURNSTILE_SECRET_KEY",
        "OPSPILOT_LOKI_USERNAME",
        "OPSPILOT_LOKI_API_KEY",
        "GRAFANA_CLOUD_LOKI_USERNAME",
        "GRAFANA_CLOUD_LOKI_API_KEY",
    }
)

PLAINTEXT_SECRET_PATTERNS = (
    re.compile(r"postgresql://"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"replace-with", re.IGNORECASE),
)


def _load_render_module():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_render_vars() -> dict[str, str]:
    return _load_render_module().load_vars_file(RENDER_VARS_EXAMPLE)


def _rendered_text(image_tag: str = "fa215f3") -> str:
    render = _load_render_module()
    return render.render_service(
        project_id="opspilot-live-lab",
        region="us-central1",
        image_tag=image_tag,
        extra_vars=_test_render_vars(),
    )


def _rendered_spec(image_tag: str = "fa215f3") -> dict:
    return yaml.safe_load(_rendered_text(image_tag=image_tag))


def _container_env(container: dict) -> dict[str, object]:
    env: dict[str, object] = {}
    for item in container.get("env", []):
        name = item["name"]
        if "value" in item:
            env[name] = item["value"]
        elif "valueFrom" in item:
            env[name] = item["valueFrom"]
    return env


def test_render_rejects_latest_tag() -> None:
    render = _load_render_module()
    with pytest.raises(ValueError, match="latest"):
        render.render_service(
            project_id="opspilot-live-lab",
            region="us-central1",
            image_tag="latest",
            extra_vars=_test_render_vars(),
        )


def test_rendered_manifest_has_no_latest_images() -> None:
    text = _rendered_text()
    assert ":latest" not in text
    assert "backend:fa215f3" in text
    assert "sandbox:fa215f3" in text


def test_rendered_manifest_has_no_plaintext_secrets() -> None:
    text = _rendered_text()
    for pattern in PLAINTEXT_SECRET_PATTERNS:
        assert not pattern.search(text)


def test_sensitive_env_uses_secret_key_ref() -> None:
    spec = _rendered_spec()
    containers = spec["spec"]["template"]["spec"]["containers"]
    for container in containers:
        env = _container_env(container)
        for name in SENSITIVE_ENV_NAMES:
            if name not in env:
                continue
            ref = env[name]
            assert isinstance(ref, dict), f"{container['name']} {name} must use secretKeyRef"
            assert "secretKeyRef" in ref


def test_backend_has_required_configuration() -> None:
    containers = _rendered_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    env = _container_env(opspilot)
    required_plain = {
        "OPSPILOT_ENV": "production",
        "OPSPILOT_DEPLOYMENT_PROFILE": "ephemeral_live_lab",
        "OPSPILOT_MODEL_PROVIDER": "groq",
        "OPSPILOT_TELEMETRY_MODE": "live",
        "GROQ_MODEL": "openai/gpt-oss-20b",
        "OPSPILOT_PROMETHEUS_URL": "http://127.0.0.1:9090",
        "OPSPILOT_SESSION_COOKIE_SECURE": "true",
        "OPSPILOT_SESSION_COOKIE_SAMESITE": "none",
        "OPSPILOT_TRUST_PROXY_HEADERS": "true",
        "OPSPILOT_TURNSTILE_REQUIRED": "true",
    }
    for key, value in required_plain.items():
        assert env[key] == value
    for secret_name in (
        "DATABASE_URL",
        "GROQ_API_KEY",
        "SANDBOX_CONTROL_TOKEN",
        "OPSPILOT_TURNSTILE_SECRET_KEY",
        "OPSPILOT_LOKI_USERNAME",
        "OPSPILOT_LOKI_API_KEY",
    ):
        assert secret_name in env


def test_sandbox_services_receive_control_token() -> None:
    containers = _rendered_spec()["spec"]["template"]["spec"]["containers"]
    for name in ("checkout-api", "auth-service", "payments-service", "provider-service"):
        container = next(c for c in containers if c["name"] == name)
        env = _container_env(container)
        ref = env["SANDBOX_CONTROL_TOKEN"]
        assert ref["secretKeyRef"]["name"] == "opspilot-sandbox-control-token"


def test_checkout_receives_database_configuration() -> None:
    containers = _rendered_spec()["spec"]["template"]["spec"]["containers"]
    checkout = next(c for c in containers if c["name"] == "checkout-api")
    env = _container_env(checkout)
    ref = env["CHECKOUT_DATABASE_URL"]
    assert ref["secretKeyRef"]["name"] == "opspilot-database-url"


def test_cloud_run_service_account_configured() -> None:
    sa = _rendered_spec()["spec"]["template"]["spec"]["serviceAccountName"]
    assert sa == "opspilot-cloud-run@opspilot-live-lab.iam.gserviceaccount.com"


def test_secret_annotations_list_all_secrets() -> None:
    annotations = _rendered_spec()["spec"]["template"]["metadata"]["annotations"]
    secrets = annotations["run.googleapis.com/secrets"]
    for name in (
        "opspilot-database-url",
        "opspilot-groq-api-key",
        "opspilot-sandbox-control-token",
        "opspilot-turnstile-secret",
        "opspilot-grafana-loki-username",
        "opspilot-grafana-loki-api-key",
        "opspilot-prometheus-config",
        "opspilot-otel-collector-config",
    ):
        assert name in secrets
        assert "projects/opspilot-live-lab/secrets/" in secrets


def test_otel_collector_grafana_env_wired() -> None:
    containers = _rendered_spec()["spec"]["template"]["spec"]["containers"]
    otel = next(c for c in containers if c["name"] == "otel-collector")
    env = _container_env(otel)
    assert "GRAFANA_CLOUD_LOKI_ENDPOINT" in env
    for name in ("GRAFANA_CLOUD_LOKI_USERNAME", "GRAFANA_CLOUD_LOKI_API_KEY"):
        assert "secretKeyRef" in env[name]


def test_prometheus_and_otel_config_volume_mounts() -> None:
    spec = _rendered_spec()["spec"]["template"]["spec"]
    volumes = {item["name"]: item for item in spec["volumes"]}
    assert volumes["prometheus-config"]["secret"]["secretName"] == "opspilot-prometheus-config"
    assert volumes["otel-config"]["secret"]["secretName"] == "opspilot-otel-collector-config"
    prometheus = next(c for c in spec["containers"] if c["name"] == "prometheus")
    otel = next(c for c in spec["containers"] if c["name"] == "otel-collector")
    assert prometheus["volumeMounts"][0]["mountPath"] == "/etc/prometheus"
    assert otel["volumeMounts"][0]["subPath"] == "otel-collector-config.yaml"


def test_loki_config_from_environ_supports_grafana_auth() -> None:
    from backend.app.telemetry.clients import loki_config_from_environ

    config = loki_config_from_environ(
        {
            "OPSPILOT_LOKI_URL": "https://logs.example.net",
            "OPSPILOT_LOKI_USERNAME": "12345",
            "OPSPILOT_LOKI_API_KEY": "glc_test_key",
        }
    )
    assert config._http_auth == ("12345", "glc_test_key")
