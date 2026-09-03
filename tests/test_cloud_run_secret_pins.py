"""Regression tests for pinned Cloud Run Secret Manager versions."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLOUD_RUN_DIR = ROOT / "deploy" / "cloud-run"
RENDER_VARS_EXAMPLE = CLOUD_RUN_DIR / "render.vars.example"
README_PATH = CLOUD_RUN_DIR / "README.md"
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"

EXPECTED_PRODUCTION_PINS = {
    "OPSPILOT_DATABASE_SECRET_VERSION": "2",
    "OPSPILOT_GROQ_SECRET_VERSION": "1",
    "OPSPILOT_SANDBOX_TOKEN_SECRET_VERSION": "2",
    "OPSPILOT_TURNSTILE_SECRET_VERSION": "1",
    "OPSPILOT_GRAFANA_LOKI_SECRET_VERSION": "2",
    "OPSPILOT_PROMETHEUS_CONFIG_SECRET_VERSION": "1",
}

LEAK_MARKERS = (
    "supersecret",
    "password=",
)


def _load_render():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render_pins", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_pins():
    path = CLOUD_RUN_DIR / "preflight_secret_pins.py"
    spec = importlib.util.spec_from_file_location("cloud_run_secret_pins", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _example_vars() -> dict[str, str]:
    return _load_render().load_vars_file(RENDER_VARS_EXAMPLE)


def _rendered_spec() -> dict:
    render = _load_render()
    text = render.render_service(
        project_id="opspilot-live-lab",
        region="us-central1",
        image_tag="fa215f3",
        extra_vars=_example_vars(),
    )
    return yaml.safe_load(text)


def _all_secret_refs(spec: dict) -> list[tuple[str, str, str]]:
    """Return (container_or_volume, secret_name, version_key)."""
    refs: list[tuple[str, str, str]] = []
    containers = spec["spec"]["template"]["spec"]["containers"]
    for container in containers:
        for item in container.get("env") or []:
            ref = (item.get("valueFrom") or {}).get("secretKeyRef")
            if not ref:
                continue
            refs.append((container["name"], ref["name"], str(ref["key"])))
    for volume in spec["spec"]["template"]["spec"].get("volumes") or []:
        secret = volume.get("secret") or {}
        secret_name = secret.get("secretName")
        for entry in secret.get("items") or []:
            refs.append((volume["name"], secret_name, str(entry["key"])))
    return refs


def test_example_vars_pin_current_production_versions() -> None:
    values = _example_vars()
    for key, expected in EXPECTED_PRODUCTION_PINS.items():
        assert values[key] == expected


def test_template_has_no_literal_latest_secret_keys() -> None:
    text = TEMPLATE_PATH.read_text()
    assert not re.search(r"key:\s*latest\b", text)
    for var_name in EXPECTED_PRODUCTION_PINS:
        assert f"{{{{{var_name}}}}}" in text


def test_rendered_secret_refs_are_numeric_never_latest() -> None:
    refs = _all_secret_refs(_rendered_spec())
    assert refs
    for _where, secret_name, key in refs:
        assert key != "latest", f"{secret_name} still uses latest"
        assert re.fullmatch(r"[1-9][0-9]*", key), f"{secret_name} key={key!r}"


def test_rendered_database_pin_is_version_2() -> None:
    refs = _all_secret_refs(_rendered_spec())
    db_refs = [key for _where, name, key in refs if name == "opspilot-database-url"]
    assert db_refs
    assert set(db_refs) == {"2"}


def test_rendered_non_database_pins_match_example() -> None:
    refs = _all_secret_refs(_rendered_spec())
    expected = {
        "opspilot-groq-api-key": "1",
        "opspilot-sandbox-control-token": "2",
        "opspilot-turnstile-secret": "1",
        "opspilot-grafana-loki-authorization": "2",
        "opspilot-prometheus-config": "1",
    }
    for _where, name, key in refs:
        if name in expected:
            assert key == expected[name]


def test_disabled_database_v1_is_never_referenced() -> None:
    refs = _all_secret_refs(_rendered_spec())
    for _where, name, key in refs:
        if name == "opspilot-database-url":
            assert key != "1"


def test_six_secret_alias_mapping_unchanged() -> None:
    render = _load_render()
    assert render.REQUIRED_SECRET_MANAGER_SECRETS == (
        "opspilot-database-url",
        "opspilot-groq-api-key",
        "opspilot-sandbox-control-token",
        "opspilot-turnstile-secret",
        "opspilot-grafana-loki-authorization",
        "opspilot-prometheus-config",
    )
    assert set(render.SECRET_VERSION_VARS.values()) == set(
        render.REQUIRED_SECRET_MANAGER_SECRETS
    )


def test_render_rejects_latest_secret_version() -> None:
    render = _load_render()
    values = _example_vars()
    values["OPSPILOT_DATABASE_SECRET_VERSION"] = "latest"
    with pytest.raises(ValueError, match="latest"):
        render.render_service(
            project_id="opspilot-live-lab",
            region="us-central1",
            image_tag="fa215f3",
            extra_vars=values,
        )


def test_render_rejects_non_numeric_secret_version() -> None:
    render = _load_render()
    values = _example_vars()
    values["OPSPILOT_GROQ_SECRET_VERSION"] = "1a"
    with pytest.raises(ValueError, match="positive integer"):
        render.render_service(
            project_id="opspilot-live-lab",
            region="us-central1",
            image_tag="fa215f3",
            extra_vars=values,
        )


def test_pin_preflight_rejects_database_v1(tmp_path: Path) -> None:
    pins = _load_pins()
    render = _load_render()
    values = _example_vars()
    values["OPSPILOT_DATABASE_SECRET_VERSION"] = "1"
    vars_path = tmp_path / "render.vars"
    vars_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    )
    with pytest.raises(RuntimeError, match="malformed version 1"):
        pins.run_pin_preflight(project="opspilot-live-lab", vars_file=vars_path)
    # ensure validate still sees numeric-but-forbidden path after render validation
    render.validate_secret_versions(values)


def test_pin_preflight_checks_enabled_and_db(tmp_path: Path) -> None:
    pins = _load_pins()
    values = _example_vars()
    vars_path = tmp_path / "render.vars"
    vars_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    )

    states = {
        ("opspilot-database-url", "2"): "ENABLED",
        ("opspilot-groq-api-key", "1"): "ENABLED",
        ("opspilot-sandbox-control-token", "2"): "ENABLED",
        ("opspilot-turnstile-secret", "1"): "ENABLED",
        ("opspilot-grafana-loki-authorization", "2"): "ENABLED",
        ("opspilot-prometheus-config", "1"): "ENABLED",
    }

    def fake_state(_project: str, secret: str, version: str) -> str:
        return states[(secret, version)]

    with (
        patch.object(pins, "_version_state", side_effect=fake_state),
        patch.object(
            pins,
            "_access_version",
            return_value=(
                "postgresql://opspilot:opspilot@localhost:5432/opspilot"
                "?sslmode=require&channel_binding=require"
            ),
        ),
        patch.object(pins, "_load_db_preflight") as load_db,
    ):
        db_mod = load_db.return_value
        db_mod.PreflightError = type("PreflightError", (Exception,), {})
        db_mod.run_preflight.return_value = None
        pins.run_pin_preflight(project="opspilot-live-lab", vars_file=vars_path)
        db_mod.run_preflight.assert_called_once()


def test_readme_documents_pin_gate() -> None:
    text = README_PATH.read_text()
    assert "preflight_secret_pins.py" in text
    assert "pinned" in text.lower()
    assert "never `latest`" in text
    for marker in LEAK_MARKERS:
        assert marker not in text


def test_no_secret_values_in_pin_module_source() -> None:
    source = (CLOUD_RUN_DIR / "preflight_secret_pins.py").read_text()
    for marker in ("postgresql://", "password=", "supersecret"):
        assert marker not in source
