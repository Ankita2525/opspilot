"""Regression tests for Cloud Run service manifest rendering."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
RENDER_VARS_EXAMPLE = CLOUD_RUN_DIR / "render.vars.example"
OTEL_IMAGE = "otel/opentelemetry-collector-contrib:0.120.0"
OTEL_IMAGE_PULL_TIMEOUT_SECONDS = 300
OTEL_COLLECTOR_STARTUP_TIMEOUT_SECONDS = 15

SENSITIVE_ENV_NAMES = frozenset(
    {
        "DATABASE_URL",
        "CHECKOUT_DATABASE_URL",
        "GROQ_API_KEY",
        "SANDBOX_CONTROL_TOKEN",
        "OPSPILOT_TURNSTILE_SECRET_KEY",
        "OPSPILOT_LOKI_AUTHORIZATION",
    }
)

REMOVED_SECRET_NAMES = (
    "opspilot-grafana-loki-username",
    "opspilot-grafana-loki-api-key",
    "opspilot-otel-collector-config",
)

PLAINTEXT_SECRET_PATTERNS = (
    re.compile(r"postgresql://"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"replace-with", re.IGNORECASE),
    re.compile(r"Authorization:\s*Basic\s+[A-Za-z0-9+/=]{8,}"),
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


def test_exactly_six_secret_manager_versions_required() -> None:
    render = _load_render_module()
    assert len(render.REQUIRED_SECRET_MANAGER_SECRETS) == 6
    annotations = _rendered_spec()["spec"]["template"]["metadata"]["annotations"]
    secrets_annotation = annotations["run.googleapis.com/secrets"]
    for secret_name in render.REQUIRED_SECRET_MANAGER_SECRETS:
        assert secret_name in secrets_annotation
    for removed in REMOVED_SECRET_NAMES:
        assert removed not in secrets_annotation


def test_secrets_annotation_matches_cloud_run_grammar() -> None:
    render = _load_render_module()
    annotations = _rendered_spec()["spec"]["template"]["metadata"]["annotations"]
    secrets_annotation = annotations["run.googleapis.com/secrets"]

    assert ", " not in secrets_annotation
    assert re.fullmatch(
        r"([a-z0-9-]+:projects/[a-z0-9-]+/secrets/[a-z0-9-]+"
        r")(,[a-z0-9-]+:projects/[a-z0-9-]+/secrets/[a-z0-9-]+)*",
        secrets_annotation,
    ), secrets_annotation

    entries = secrets_annotation.split(",")
    assert len(entries) == 6
    for entry in entries:
        alias, _, mapping = entry.partition(":")
        assert mapping == f"projects/opspilot-live-lab/secrets/{alias}"
        assert alias in render.REQUIRED_SECRET_MANAGER_SECRETS


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


def test_no_separate_grafana_username_or_api_key_env() -> None:
    spec = _rendered_spec()
    containers = spec["spec"]["template"]["spec"]["containers"]
    forbidden = {
        "OPSPILOT_LOKI_USERNAME",
        "OPSPILOT_LOKI_API_KEY",
        "GRAFANA_CLOUD_LOKI_USERNAME",
        "GRAFANA_CLOUD_LOKI_API_KEY",
    }
    for container in containers:
        env = _container_env(container)
        assert forbidden.isdisjoint(env.keys())


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
        "OPSPILOT_LOKI_AUTHORIZATION",
    ):
        assert secret_name in env
        assert env[secret_name]["secretKeyRef"]["name"] == {
            "DATABASE_URL": "opspilot-database-url",
            "GROQ_API_KEY": "opspilot-groq-api-key",
            "SANDBOX_CONTROL_TOKEN": "opspilot-sandbox-control-token",
            "OPSPILOT_TURNSTILE_SECRET_KEY": "opspilot-turnstile-secret",
            "OPSPILOT_LOKI_AUTHORIZATION": "opspilot-grafana-loki-authorization",
        }[secret_name]


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


def test_public_access_invoker_iam_disabled() -> None:
    annotations = _rendered_spec()["metadata"]["annotations"]
    assert annotations["run.googleapis.com/invoker-iam-disabled"] == "true"


def test_container_dependencies_are_revision_scoped() -> None:
    spec = _rendered_spec()
    service_annotations = spec["metadata"].get("annotations", {})
    revision_annotations = spec["spec"]["template"]["metadata"]["annotations"]
    assert "run.googleapis.com/container-dependencies" not in service_annotations
    assert service_annotations.get("run.googleapis.com/invoker-iam-disabled") == "true"
    raw = revision_annotations["run.googleapis.com/container-dependencies"]
    assert json.loads(raw) == {
        "opspilot": [
            "checkout-api",
            "auth-service",
            "payments-service",
            "provider-service",
            "prometheus",
            "otel-collector",
        ]
    }


def test_otel_collector_uses_env_config_provider() -> None:
    containers = _rendered_spec()["spec"]["template"]["spec"]["containers"]
    otel = next(c for c in containers if c["name"] == "otel-collector")
    assert otel["args"] == ["--config=env:OTEL_COLLECTOR_CONFIG"]
    env = _container_env(otel)
    assert "OTEL_COLLECTOR_CONFIG" in env
    config_text = str(env["OTEL_COLLECTOR_CONFIG"])
    assert "receivers:" in config_text
    assert "Authorization: ${OPSPILOT_LOKI_AUTHORIZATION}" in config_text
    assert "opspilot-grafana-loki-authorization" in yaml.dump(env["OPSPILOT_LOKI_AUTHORIZATION"])


def test_otel_collector_has_no_secret_manager_config_volume() -> None:
    spec = _rendered_spec()["spec"]["template"]["spec"]
    volumes = spec.get("volumes", [])
    assert len(volumes) == 1
    assert volumes[0]["name"] == "prometheus-config"
    otel = next(c for c in spec["containers"] if c["name"] == "otel-collector")
    assert "volumeMounts" not in otel


def test_prometheus_config_volume_mount() -> None:
    spec = _rendered_spec()["spec"]["template"]["spec"]
    volumes = {item["name"]: item for item in spec["volumes"]}
    assert volumes["prometheus-config"]["secret"]["secretName"] == "opspilot-prometheus-config"
    prometheus = next(c for c in spec["containers"] if c["name"] == "prometheus")
    assert prometheus["volumeMounts"][0]["mountPath"] == "/etc/prometheus"


def test_loki_config_from_environ_supports_authorization_header() -> None:
    from backend.app.telemetry.clients import loki_config_from_environ

    config = loki_config_from_environ(
        {
            "OPSPILOT_LOKI_URL": "https://logs.example.net",
            "OPSPILOT_LOKI_AUTHORIZATION": "Basic ZmFrZTpwYXNz",
        }
    )
    assert config.request_headers() == {"Authorization": "Basic ZmFrZTpwYXNz"}


def test_loki_client_sends_authorization_header() -> None:
    from backend.app.telemetry.clients import LokiClient, LokiConfig

    config = LokiConfig(
        base_url="https://logs.example.net",
        authorization="Basic ZmFrZTpwYXNz",
    )
    client = LokiClient(config)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"version": "2.9.0"}

    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        captured["headers"] = kwargs.get("headers")
        return mock_response

    with patch("backend.app.telemetry.clients.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get = fake_get
        mock_client_cls.return_value = mock_client
        assert client.is_api_ready()

    assert captured["headers"] == {"Authorization": "Basic ZmFrZTpwYXNz"}


def _docker_pull_otel_image() -> None:
    result = subprocess.run(
        ["docker", "pull", OTEL_IMAGE],
        capture_output=True,
        text=True,
        timeout=OTEL_IMAGE_PULL_TIMEOUT_SECONDS,
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}".strip()
    assert result.returncode == 0, f"failed to pull {OTEL_IMAGE}:\n{combined}"


def _assert_otel_collector_started(combined: str) -> None:
    lowered = combined.lower()
    assert "cannot unmarshal" not in lowered, combined
    assert "invalid configuration" not in lowered, combined
    assert "error reading configuration" not in lowered, combined
    assert "Everything is ready" in combined, (
        "collector did not report readiness:\n" + combined
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker required")
def test_otel_collector_validates_env_config_locally() -> None:
    render = _load_render_module()
    config = render.build_otel_collector_config(
        "https://logs.example.net/loki/api/v1/push"
    )

    _docker_pull_otel_image()

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                f"OTEL_COLLECTOR_CONFIG={config}",
                "-e",
                "OPSPILOT_LOKI_AUTHORIZATION=Basic ZmFrZTpwYXNz",
                OTEL_IMAGE,
                "--config=env:OTEL_COLLECTOR_CONFIG",
            ],
            capture_output=True,
            text=True,
            timeout=OTEL_COLLECTOR_STARTUP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        combined = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        _assert_otel_collector_started(combined)
        return

    combined = f"{result.stdout}\n{result.stderr}".strip()
    assert result.returncode == 0, (
        f"collector exited with status {result.returncode}:\n{combined}"
    )
    _assert_otel_collector_started(combined)
