#!/usr/bin/env python3
"""Render deploy/cloud-run/service.yaml.tmpl for Cloud Run deployment."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CLOUD_RUN_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = CLOUD_RUN_DIR / "service.yaml.tmpl"
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
)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


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

    rendered = template_path.read_text()
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)

    unresolved = sorted(set(PLACEHOLDER_PATTERN.findall(rendered)))
    if unresolved:
        raise ValueError(f"Unresolved template placeholders: {', '.join(unresolved)}")

    if ":latest" in rendered:
        raise ValueError("Rendered manifest must not contain ':latest' image tags.")

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
