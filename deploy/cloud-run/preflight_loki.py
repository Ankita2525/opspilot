#!/usr/bin/env python3
"""Preflight: Grafana Cloud Loki authorization can authenticate to the data API.

Never prints credentials. Uses pinned OPSPILOT_LOKI_URL + Grafana Authorization
secret to GET /loki/api/v1/labels (not /status/buildinfo).
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import subprocess
import sys
from pathlib import Path

import httpx

CLOUD_RUN_DIR = Path(__file__).resolve().parent
DEFAULT_VARS = CLOUD_RUN_DIR / "render.vars"
DEFAULT_PROJECT = "opspilot-live-lab"


def _load_render_module():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _access_text(project: str, secret: str, version: str) -> str:
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
    return proc.stdout.decode("utf-8")


def _validate_basic_shape(authorization: str) -> None:
    if authorization != authorization.strip():
        raise RuntimeError("Loki Authorization has surrounding whitespace")
    if "\n" in authorization or "\r" in authorization:
        raise RuntimeError("Loki Authorization contains CR/LF")
    if not authorization.startswith("Basic "):
        raise RuntimeError("Loki Authorization must start with 'Basic '")
    decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
    if ":" not in decoded:
        raise RuntimeError("Loki Authorization Basic payload must be username:token")
    user, token = decoded.split(":", 1)
    if not user or not token:
        raise RuntimeError("Loki Authorization username/token empty")
    if not user.isdigit():
        raise RuntimeError("Loki username is not a numeric Grafana stack user id")


def run_loki_preflight(*, project: str, vars_file: Path) -> None:
    render = _load_render_module()
    values = render.load_vars_file(vars_file)
    loki_url = values["OPSPILOT_LOKI_URL"].strip().rstrip("/")
    version = values["OPSPILOT_GRAFANA_LOKI_SECRET_VERSION"].strip()
    authorization = _access_text(
        project, "opspilot-grafana-loki-authorization", version
    )
    _validate_basic_shape(authorization)

    labels_url = f"{loki_url}/loki/api/v1/labels"
    response = httpx.get(
        labels_url,
        headers={"Authorization": authorization},
        timeout=15.0,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError(
            "LOKI_TOKEN_INVALID: Grafana rejected Authorization for /loki/api/v1/labels. "
            "Create a new Cloud Access Policy token (logs:read + logs:write) for the "
            "existing opspilot-loki policy and provide it securely so version can be rotated."
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Loki labels check failed with HTTP {response.status_code} "
            "(endpoint/config may be invalid)"
        )
    payload = response.json()
    if not (
        payload.get("status") == "success" or isinstance(payload.get("data"), list)
    ):
        raise RuntimeError("Loki labels response shape unexpected")
    print(f"Loki auth/query preflight: PASS (secret pin {version}, /labels 200)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--vars-file", type=Path, default=DEFAULT_VARS)
    args = parser.parse_args(argv)
    try:
        run_loki_preflight(project=args.project, vars_file=args.vars_file)
    except Exception as exc:  # noqa: BLE001 — preflight surfaces one clear failure
        print("Loki auth/query preflight: FAIL")
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
