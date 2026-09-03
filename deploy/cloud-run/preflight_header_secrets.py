#!/usr/bin/env python3
"""Preflight: header-bound Secret Manager values must be HTTP-header safe.

Audits secrets that are placed directly into HTTP Authorization / custom headers.
Never prints secret contents.

Fails on CR, LF, surrounding whitespace, or empty values.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

CLOUD_RUN_DIR = Path(__file__).resolve().parent
DEFAULT_VARS = CLOUD_RUN_DIR / "render.vars"
DEFAULT_PROJECT = "opspilot-live-lab"

# (render.vars key, secret name, kind)
HEADER_SECRETS: tuple[tuple[str, str, str], ...] = (
    ("OPSPILOT_SANDBOX_TOKEN_SECRET_VERSION", "opspilot-sandbox-control-token", "raw_header"),
    (
        "OPSPILOT_GRAFANA_LOKI_SECRET_VERSION",
        "opspilot-grafana-loki-authorization",
        "authorization_basic",
    ),
)


def _load_render_module():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _access_bytes(project: str, secret: str, version: str) -> bytes:
    proc = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            version,
            f"--secret={secret}",
            f"--project={project}",
        ],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"failed to access {secret}:{version} (exit {proc.returncode})"
        )
    return proc.stdout


def validate_raw_header_bytes(raw: bytes) -> None:
    if not raw:
        raise RuntimeError("header secret is empty")
    if b"\n" in raw or b"\r" in raw:
        raise RuntimeError("header secret contains CR/LF")
    if raw != raw.strip():
        raise RuntimeError("header secret has leading/trailing whitespace")
    # HTTP header values must be printable ASCII without CTL chars.
    if any(b < 0x20 or b > 0x7E for b in raw):
        raise RuntimeError("header secret contains non-printable bytes")


def validate_authorization_basic_bytes(raw: bytes) -> None:
    validate_raw_header_bytes(raw)
    text = raw.decode("utf-8")
    if not text.startswith("Basic "):
        raise RuntimeError("Authorization secret must start with 'Basic '")
    payload = text[6:]
    try:
        decoded = base64.b64decode(payload, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Authorization Basic payload is not valid base64") from exc
    try:
        pair = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Authorization Basic payload is not UTF-8") from exc
    if ":" not in pair:
        raise RuntimeError("Authorization Basic payload must be username:token")
    user, token = pair.split(":", 1)
    if not user or not token:
        raise RuntimeError("Authorization Basic username/token must be non-empty")


def run_header_hygiene_preflight(*, project: str, vars_file: Path) -> None:
    render = _load_render_module()
    values = render.load_vars_file(vars_file)
    print("Header-bound secret hygiene:")
    for var_name, secret_name, kind in HEADER_SECRETS:
        version = values[var_name].strip()
        raw = _access_bytes(project, secret_name, version)
        if kind == "raw_header":
            validate_raw_header_bytes(raw)
        elif kind == "authorization_basic":
            validate_authorization_basic_bytes(raw)
        else:
            raise RuntimeError(f"unknown header secret kind {kind}")
        print(f"  {secret_name}:{version} -> PASS ({kind})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--vars-file", type=Path, default=DEFAULT_VARS)
    args = parser.parse_args(argv)
    try:
        run_header_hygiene_preflight(project=args.project, vars_file=args.vars_file)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        print("Header secret hygiene preflight: FAIL")
        print(str(exc))
        return 1
    print("Header secret hygiene preflight: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
