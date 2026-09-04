"""Tests for Cloud Run ephemeral live lab deployment profile."""

from __future__ import annotations

import json
import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"
PROMETHEUS_YML = CLOUD_RUN_DIR / "prometheus.yml"
LOCAL_COMPOSE = ROOT / "docker-compose.cloud-run-local.yml"
RENDER_VARS_EXAMPLE = CLOUD_RUN_DIR / "render.vars.example"


def _load_render_module():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rendered_service_text() -> str:
    render = _load_render_module()
    return render.render_service(
        project_id="test-project",
        region="us-central1",
        image_tag="testtag01",
        extra_vars=render.load_vars_file(RENDER_VARS_EXAMPLE),
    )


def _service_spec() -> dict:
    return yaml.safe_load(_rendered_service_text())


def test_only_opspilot_has_public_port() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    ingress = [c for c in containers if c.get("ports")]
    assert len(ingress) == 1
    assert ingress[0]["name"] == "opspilot"
    assert ingress[0]["ports"][0]["containerPort"] == 8000


def test_sidecars_listen_all_interfaces_for_cloud_run_probes() -> None:
    """Cloud Run probes cannot reach 127.0.0.1-only listeners (rev 00002 evidence)."""
    text = _rendered_service_text()
    for port, name in (
        ("8081", "checkout-api"),
        ("8082", "auth-service"),
        ("8083", "payments-service"),
        ("8084", "provider-service"),
    ):
        assert f'"0.0.0.0", "--port", "{port}"' in text
        assert f'"127.0.0.1", "--port", "{port}"' not in text
        assert name in text


def test_min_and_max_instances() -> None:
    annotations = _service_spec()["spec"]["template"]["metadata"]["annotations"]
    assert annotations["autoscaling.knative.dev/minScale"] == "0"
    assert annotations["autoscaling.knative.dev/maxScale"] == "1"


_GCP_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_GCP_LABEL_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")


def test_gcp_labels_conform_to_naming_style() -> None:
    spec = _service_spec()
    service_labels = spec["metadata"].get("labels") or {}
    revision_labels = spec["spec"]["template"]["metadata"].get("labels") or {}
    assert "opspilot.deployment_profile" not in service_labels
    assert "opspilot.deployment_profile" not in revision_labels
    assert service_labels.get("opspilot_deployment_profile") == "ephemeral_live_lab"
    for labels in (service_labels, revision_labels):
        for key, value in labels.items():
            assert _GCP_LABEL_KEY.fullmatch(key), key
            assert _GCP_LABEL_VALUE.fullmatch(str(value)), f"{key}={value}"


def test_request_based_billing_cpu_throttling_enabled() -> None:
    annotations = _service_spec()["spec"]["template"]["metadata"]["annotations"]
    assert annotations["run.googleapis.com/cpu-throttling"] == "true"
    assert annotations["autoscaling.knative.dev/minScale"] == "0"
    assert annotations["autoscaling.knative.dev/maxScale"] == "1"


def test_startup_cpu_boost_with_scale_and_throttling() -> None:
    """Cold-start boost must coexist with min=0/max=1 request-based billing."""
    annotations = _service_spec()["spec"]["template"]["metadata"]["annotations"]
    assert annotations["run.googleapis.com/startup-cpu-boost"] == "true"
    assert annotations["run.googleapis.com/cpu-throttling"] == "true"
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
    assert spec["containerConcurrency"] == 10


def test_container_count_within_cloud_run_limit() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 7
    assert len(containers) <= 10


def test_ingress_binds_all_interfaces_not_localhost() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    cmd_line = [line for line in dockerfile.splitlines() if line.startswith("CMD")][-1]
    assert "0.0.0.0" in cmd_line
    assert "${PORT:-8000}" in cmd_line
    assert "127.0.0.1" not in cmd_line
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    assert opspilot["ports"][0]["containerPort"] == 8000
    env_names = [item["name"] for item in opspilot.get("env", [])]
    assert "PORT" not in env_names


def test_opspilot_lifecycle_probes_use_health() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    assert opspilot["startupProbe"]["httpGet"]["path"] == "/health"
    assert opspilot["startupProbe"]["httpGet"]["port"] == 8000
    assert opspilot["startupProbe"]["timeoutSeconds"] == 2
    assert opspilot["startupProbe"]["failureThreshold"] == 24
    assert opspilot["livenessProbe"]["httpGet"]["path"] == "/health"
    assert opspilot["livenessProbe"]["httpGet"]["port"] == 8000


def test_opspilot_startup_probe_does_not_use_ready_or_healthz() -> None:
    """Public /healthz is intercepted by Google Frontend; /ready is deep diagnostic."""
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    assert opspilot["startupProbe"]["httpGet"]["path"] not in {"/ready", "/healthz"}
    assert opspilot["livenessProbe"]["httpGet"]["path"] not in {"/ready", "/healthz"}
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "Google Frontend" in text or "healthz" in text.lower()


def test_sidecar_startup_probes_configured() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    for name in ("checkout-api", "auth-service", "payments-service", "provider-service", "prometheus"):
        container = next(c for c in containers if c["name"] == name)
        assert "startupProbe" in container


def test_http_probes_do_not_set_host() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    expected_startup_probes = {
        "opspilot": {"path": "/health", "port": 8000},
        "checkout-api": {"path": "/health", "port": 8081},
        "auth-service": {"path": "/health", "port": 8082},
        "payments-service": {"path": "/health", "port": 8083},
        "provider-service": {"path": "/health", "port": 8084},
        "prometheus": {"path": "/-/ready", "port": 9090},
    }
    for container in containers:
        for probe_name in ("startupProbe", "livenessProbe", "readinessProbe"):
            probe = container.get(probe_name)
            if probe is None:
                continue
            http_get = probe.get("httpGet", {})
            assert "host" not in http_get, (
                f"{container['name']} {probe_name} must not set httpGet.host"
            )
        startup = container.get("startupProbe")
        if startup is None:
            continue
        expected = expected_startup_probes.get(container["name"])
        if expected is None:
            continue
        http_get = startup["httpGet"]
        assert http_get["path"] == expected["path"]
        assert http_get["port"] == expected["port"]


def test_every_container_dependency_has_startup_probe() -> None:
    spec = _service_spec()
    containers = {c["name"]: c for c in spec["spec"]["template"]["spec"]["containers"]}
    raw = spec["spec"]["template"]["metadata"]["annotations"][
        "run.googleapis.com/container-dependencies"
    ]
    graph = json.loads(raw)
    targets = {name for deps in graph.values() for name in deps}
    assert targets
    expected_http_probes = {
        "checkout-api": ("/health", 8081),
        "auth-service": ("/health", 8082),
        "payments-service": ("/health", 8083),
        "provider-service": ("/health", 8084),
        "prometheus": ("/-/ready", 9090),
    }
    for target in targets:
        assert target in containers, f"dependency {target} has no matching container"
        container = containers[target]
        assert "startupProbe" in container, f"{target} is a dependency without startupProbe"
        probe = container["startupProbe"]
        assert "httpGet" in probe or "tcpSocket" in probe, (
            f"{target} startupProbe must use httpGet or tcpSocket"
        )
        if target == "otel-collector":
            assert probe["tcpSocket"]["port"] == 4318
        if target in expected_http_probes:
            path, port = expected_http_probes[target]
            assert probe["httpGet"]["path"] == path
            assert probe["httpGet"]["port"] == port


def test_container_dependencies_are_revision_scoped() -> None:
    spec = _service_spec()
    service_annotations = spec["metadata"].get("annotations", {})
    revision_annotations = spec["spec"]["template"]["metadata"]["annotations"]
    assert "run.googleapis.com/container-dependencies" not in service_annotations
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


def test_prometheus_listens_all_interfaces_for_probes() -> None:
    text = _rendered_service_text()
    assert "--web.listen-address=0.0.0.0:9090" in text
    assert "--web.listen-address=127.0.0.1:9090" not in text


def test_backend_env_localhost_service_urls() -> None:
    containers = _service_spec()["spec"]["template"]["spec"]["containers"]
    opspilot = next(c for c in containers if c["name"] == "opspilot")
    env = {item["name"]: item["value"] for item in opspilot.get("env", []) if "value" in item}
    assert env["CHECKOUT_API_URL"] == "http://127.0.0.1:8081"
    assert env["OPSPILOT_PROMETHEUS_URL"] == "http://127.0.0.1:9090"


def test_template_exists_and_is_not_committed_rendered_output() -> None:
    """Rendered manifests may exist locally (gitignored) but must never be tracked."""
    import subprocess

    assert TEMPLATE_PATH.is_file()
    rendered_dir = CLOUD_RUN_DIR / "rendered"
    gitignore = CLOUD_RUN_DIR / ".gitignore"
    assert gitignore.is_file()
    assert "rendered/" in gitignore.read_text(encoding="utf-8")
    tracked = subprocess.check_output(
        ["git", "ls-files", "--", "deploy/cloud-run/rendered"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert tracked == ""
    # Local render artifacts are allowed on disk; only commits are forbidden.
    _ = rendered_dir
