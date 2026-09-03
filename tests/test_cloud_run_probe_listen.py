"""Generic regression: Cloud Run probes cannot reach 127.0.0.1-only listeners."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
RENDER_VARS_EXAMPLE = CLOUD_RUN_DIR / "render.vars.example"
OTEL_PATH = CLOUD_RUN_DIR / "otel-collector.yaml"
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"

LOOPBACK_HOST_FLAGS = (
    re.compile(r'"--host",\s*"127\.0\.0\.1"'),
    re.compile(r"--web\.listen-address=127\.0\.0\.1:"),
    re.compile(r"endpoint:\s*127\.0\.0\.1:\d+"),
)


def _load_render():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render_listen", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rendered_text() -> str:
    render = _load_render()
    return render.render_service(
        project_id="opspilot-live-lab",
        region="us-central1",
        image_tag="fa215f3",
        extra_vars=render.load_vars_file(RENDER_VARS_EXAMPLE),
    )


def _probed_containers(spec: dict) -> list[dict]:
    return [
        c
        for c in spec["spec"]["template"]["spec"]["containers"]
        if c.get("startupProbe")
    ]


def test_no_loopback_only_listen_in_template_or_otel() -> None:
    for path in (TEMPLATE_PATH, OTEL_PATH):
        text = path.read_text()
        for pattern in LOOPBACK_HOST_FLAGS:
            assert pattern.search(text) is None, f"{path}: {pattern.pattern}"


def test_every_startup_probe_target_listens_on_all_interfaces() -> None:
    text = _rendered_text()
    spec = yaml.safe_load(text)
    probed = _probed_containers(spec)
    assert len(probed) == 7
    for container in probed:
        name = container["name"]
        blob = yaml.safe_dump(container)
        if name == "opspilot":
            # Ingress CMD is in the image; ports prove public listen contract.
            assert container["ports"][0]["containerPort"] == 8000
            continue
        if name == "otel-collector":
            assert re.search(r"endpoint:\s*0\.0\.0\.0:4318", blob)
            assert not re.search(r"endpoint:\s*127\.0\.0\.1:4318", blob)
            continue
        if name == "prometheus":
            assert "--web.listen-address=0.0.0.0:9090" in blob
            assert "--web.listen-address=127.0.0.1:9090" not in blob
            continue
        assert '"--host", "0.0.0.0"' in blob or "- 0.0.0.0" in blob
        assert '"--host", "127.0.0.1"' not in blob


def test_clients_still_use_loopback_urls() -> None:
    text = _rendered_text()
    for url in (
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
        "http://127.0.0.1:8083",
        "http://127.0.0.1:8084",
        "http://127.0.0.1:9090",
        "http://127.0.0.1:4318",
    ):
        assert url in text


def test_render_rejects_loopback_only_sidecar_listen() -> None:
    render = _load_render()
    values = render.load_vars_file(RENDER_VARS_EXAMPLE)
    broken = TEMPLATE_PATH.read_text().replace(
        '"--host", "0.0.0.0", "--port", "8081"',
        '"--host", "127.0.0.1", "--port", "8081"',
        1,
    )
    tmp = CLOUD_RUN_DIR / "service.yaml.tmpl.broken-listen"
    try:
        tmp.write_text(broken)
        with pytest.raises(ValueError, match="0.0.0.0"):
            render.render_service(
                project_id="opspilot-live-lab",
                region="us-central1",
                image_tag="fa215f3",
                extra_vars=values,
                template_path=tmp,
            )
    finally:
        if tmp.exists():
            tmp.unlink()
