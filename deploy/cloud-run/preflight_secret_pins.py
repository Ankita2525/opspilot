#!/usr/bin/env python3
"""Verify pinned Secret Manager versions are ENABLED and DB pin passes SELECT 1.

Never prints secret values. Reads pin numbers from render.vars (or --vars-file).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

CLOUD_RUN_DIR = Path(__file__).resolve().parent
DEFAULT_VARS = CLOUD_RUN_DIR / "render.vars"
DEFAULT_PROJECT = "opspilot-live-lab"

# Malformed concatenated URL version; must never be pinned.
FORBIDDEN_DATABASE_VERSIONS = frozenset({"1"})


def _load_render_module():
    path = CLOUD_RUN_DIR / "render_service.py"
    spec = importlib.util.spec_from_file_location("cloud_run_render", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_db_preflight():
    path = CLOUD_RUN_DIR / "preflight_database.py"
    spec = importlib.util.spec_from_file_location("cloud_run_db_preflight", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gcloud_json(args: list[str]) -> object:
    proc = subprocess.run(
        ["gcloud", *args, "--format=json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gcloud {' '.join(args)} failed (exit {proc.returncode}); "
            "stderr omitted to avoid accidental secret leakage"
        )
    return json.loads(proc.stdout)


def _version_state(project: str, secret: str, version: str) -> str:
    data = _gcloud_json(
        [
            "secrets",
            "versions",
            "describe",
            version,
            f"--secret={secret}",
            f"--project={project}",
        ]
    )
    assert isinstance(data, dict)
    state = str(data.get("state", "")).upper()
    if not state:
        raise RuntimeError(f"{secret}:{version} missing state")
    return state


def _access_version(project: str, secret: str, version: str) -> str:
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


def run_pin_preflight(*, project: str, vars_file: Path) -> None:
    render = _load_render_module()
    values = render.load_vars_file(vars_file)
    render.validate_secret_versions(values)

    print("Secret pin states:")
    for var_name, secret_name in render.SECRET_VERSION_VARS.items():
        version = values[var_name].strip()
        if secret_name == "opspilot-database-url" and version in FORBIDDEN_DATABASE_VERSIONS:
            raise RuntimeError(
                f"refusing to pin {secret_name} to disabled/malformed version {version}"
            )
        state = _version_state(project, secret_name, version)
        print(f"  {secret_name}:{version} -> {state}")
        if state != "ENABLED":
            raise RuntimeError(
                f"{secret_name} version {version} is {state}, expected ENABLED"
            )

    db_version = values["OPSPILOT_DATABASE_SECRET_VERSION"].strip()
    db_preflight = _load_db_preflight()
    payload = _access_version(project, "opspilot-database-url", db_version)
    try:
        db_preflight.run_preflight(payload)
    except db_preflight.PreflightError as exc:
        raise RuntimeError(f"database pin preflight failed: {exc.message}") from None
    print(f"DATABASE_URL pin {db_version}: PASS (parse + connection + SELECT 1)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--vars-file", type=Path, default=DEFAULT_VARS)
    args = parser.parse_args(argv)
    try:
        run_pin_preflight(project=args.project, vars_file=args.vars_file)
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print("Secret pin preflight: FAIL")
        print(str(exc))
        return 1
    print("Secret pin preflight: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
