#!/usr/bin/env python3
"""Render deploy/cloud-run/service.yaml.tmpl for Cloud Run deployment."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CLOUD_RUN_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"
OTEL_COLLECTOR_CONFIG_PATH = CLOUD_RUN_DIR / "otel-collector.yaml"
DEFAULT_OUTPUT_DIR = CLOUD_RUN_DIR / "rendered"

REQUIRED_PLACEHOLDERS = (
    "PROJECT_ID",
    "REGION",
    "IMAGE_TAG",
    "OPSPILOT_CORS_ORIGINS",
    "OPSPILOT_TURNSTILE_SITE_KEY",
    "OPSPILOT_LOKI_URL",
    "GRAFANA_CLOUD_LOKI_ENDPOINT",
    "OPSPILOT_PUBLIC_DOMAIN",
    "OPSPILOT_DATABASE_SECRET_VERSION",
    "OPSPILOT_GROQ_SECRET_VERSION",
    "OPSPILOT_SANDBOX_TOKEN_SECRET_VERSION",
    "OPSPILOT_TURNSTILE_SECRET_VERSION",
    "OPSPILOT_GRAFANA_LOKI_SECRET_VERSION",
    "OPSPILOT_PROMETHEUS_CONFIG_SECRET_VERSION",
)

REQUIRED_SECRET_MANAGER_SECRETS = (
    "opspilot-database-url",
    "opspilot-groq-api-key",
    "opspilot-sandbox-control-token",
    "opspilot-turnstile-secret",
    "opspilot-grafana-loki-authorization",
    "opspilot-prometheus-config",
)

# Render-var -> Secret Manager secret id.
SECRET_VERSION_VARS: dict[str, str] = {
    "OPSPILOT_DATABASE_SECRET_VERSION": "opspilot-database-url",
    "OPSPILOT_GROQ_SECRET_VERSION": "opspilot-groq-api-key",
    "OPSPILOT_SANDBOX_TOKEN_SECRET_VERSION": "opspilot-sandbox-control-token",
    "OPSPILOT_TURNSTILE_SECRET_VERSION": "opspilot-turnstile-secret",
    "OPSPILOT_GRAFANA_LOKI_SECRET_VERSION": "opspilot-grafana-loki-authorization",
    "OPSPILOT_PROMETHEUS_CONFIG_SECRET_VERSION": "opspilot-prometheus-config",
}

NUMERIC_VERSION = re.compile(r"^[1-9][0-9]*$")
SECRET_KEY_LATEST = re.compile(r"key:\s*['\"]?latest['\"]?\b")
PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
# Cloud Run startup probes cannot reach loopback-only listeners (rev 00002).
LOOPBACK_ONLY_LISTEN_PATTERNS = (
    re.compile(r'"--host",\s*"127\.0\.0\.1"'),
    re.compile(r"--host=127\.0\.0\.1\b"),
    re.compile(r"--web\.listen-address=127\.0\.0\.1:"),
    re.compile(r"endpoint:\s*127\.0\.0\.1:\d+"),
)


def load_vars_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid vars line (expected KEY=VALUE): {raw_line!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def validate_secret_versions(values: dict[str, str]) -> None:
    for var_name, secret_name in SECRET_VERSION_VARS.items():
        version = values.get(var_name, "").strip()
        if not version:
            raise ValueError(f"Missing required secret version: {var_name}")
        if version.lower() == "latest":
            raise ValueError(
                f"{var_name} must be an explicit numeric Secret Manager version, "
                f"not 'latest' (secret={secret_name})."
            )
        if not NUMERIC_VERSION.fullmatch(version):
            raise ValueError(
                f"{var_name} must be a positive integer version id, got {version!r} "
                f"(secret={secret_name})."
            )


def build_otel_collector_config(grafana_loki_endpoint: str) -> str:
    text = OTEL_COLLECTOR_CONFIG_PATH.read_text()
    return text.replace("${GRAFANA_CLOUD_LOKI_ENDPOINT}", grafana_loki_endpoint)


def indent_yaml_block(text: str, spaces: int) -> str:
    pad = " " * spaces
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(f"{pad}{line}")
        else:
            lines.append("")
    return "\n".join(lines)


def render_service(
    *,
    project_id: str,
    region: str,
    image_tag: str,
    extra_vars: dict[str, str] | None = None,
    template_path: Path = TEMPLATE_PATH,
) -> str:
    if not image_tag or image_tag == "latest":
        raise ValueError("IMAGE_TAG must be set and must not be 'latest'.")
    if not project_id.strip():
        raise ValueError("PROJECT_ID is required.")
    if not region.strip():
        raise ValueError("REGION is required.")

    values: dict[str, str] = {
        "PROJECT_ID": project_id.strip(),
        "REGION": region.strip(),
        "IMAGE_TAG": image_tag.strip(),
    }
    if extra_vars:
        values.update(extra_vars)

    missing = [key for key in REQUIRED_PLACEHOLDERS if not values.get(key, "").strip()]
    if missing:
        raise ValueError(
            "Missing required render variables: "
            + ", ".join(missing)
            + ". Supply via --vars-file or CLI flags."
        )

    validate_secret_versions(values)

    otel_config = build_otel_collector_config(values["GRAFANA_CLOUD_LOKI_ENDPOINT"])
    values["OTEL_COLLECTOR_CONFIG_BLOCK"] = indent_yaml_block(otel_config, 16)

    rendered = template_path.read_text()
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)

    unresolved = sorted(set(PLACEHOLDER_PATTERN.findall(rendered)))
    if unresolved:
        raise ValueError(f"Unresolved template placeholders: {', '.join(unresolved)}")

    if ":latest" in rendered:
        raise ValueError("Rendered manifest must not contain ':latest' image tags.")
    if SECRET_KEY_LATEST.search(rendered):
        raise ValueError(
            "Rendered manifest must not use secretKeyRef/volume key 'latest'; "
            "pin explicit numeric Secret Manager versions."
        )
    for pattern in LOOPBACK_ONLY_LISTEN_PATTERNS:
        if pattern.search(rendered):
            raise ValueError(
                "Rendered manifest must not bind probed sidecars to 127.0.0.1 only; "
                "Cloud Run startup probes require 0.0.0.0 (clients may still use "
                "http://127.0.0.1)."
            )

    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument(
        "--vars-file",
        type=Path,
        help="KEY=VALUE file for deploy-time non-secret configuration.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write rendered YAML to this path (default: rendered/service.yaml).",
    )
    parser.add_argument(
        "--cors-origins",
        help="OPSPILOT_CORS_ORIGINS (overrides vars file).",
    )
    parser.add_argument(
        "--turnstile-site-key",
        help="OPSPILOT_TURNSTILE_SITE_KEY (overrides vars file).",
    )
    parser.add_argument(
        "--loki-url",
        help="OPSPILOT_LOKI_URL query base (overrides vars file).",
    )
    parser.add_argument(
        "--grafana-loki-endpoint",
        help="GRAFANA_CLOUD_LOKI_ENDPOINT push URL (overrides vars file).",
    )
    parser.add_argument(
        "--public-domain",
        help="OPSPILOT_PUBLIC_DOMAIN (overrides vars file).",
    )
    args = parser.parse_args(argv)

    extra_vars: dict[str, str] = {}
    if args.vars_file:
        extra_vars.update(load_vars_file(args.vars_file))
    if args.cors_origins:
        extra_vars["OPSPILOT_CORS_ORIGINS"] = args.cors_origins
    if args.turnstile_site_key:
        extra_vars["OPSPILOT_TURNSTILE_SITE_KEY"] = args.turnstile_site_key
    if args.loki_url:
        extra_vars["OPSPILOT_LOKI_URL"] = args.loki_url
    if args.grafana_loki_endpoint:
        extra_vars["GRAFANA_CLOUD_LOKI_ENDPOINT"] = args.grafana_loki_endpoint
    if args.public_domain:
        extra_vars["OPSPILOT_PUBLIC_DOMAIN"] = args.public_domain

    rendered = render_service(
        project_id=args.project_id,
        region=args.region,
        image_tag=args.image_tag,
        extra_vars=extra_vars,
    )

    output_path = args.output
    if output_path is None:
        output_path = DEFAULT_OUTPUT_DIR / "service.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
