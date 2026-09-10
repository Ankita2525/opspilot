"""Run OpsPilot's controlled evaluation suite against a hosted Groq model."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from backend.app.evals.hosted import HostedEvaluationRunner
from backend.app.evals.hosted_report import render_hosted_evaluation_report
from backend.app.models.groq_provider import (
    DEFAULT_GROQ_MODEL,
    GroqModelProvider,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print(
            "Hosted evaluation requires GROQ_API_KEY.",
            file=sys.stderr,
        )
        return 2

    provider = GroqModelProvider(
        api_key=api_key,
        model=args.model,
    )

    result = HostedEvaluationRunner(
        provider=provider,
        provider_name="groq",
        model_name=args.model,
        scenario_ids=args.scenario,
        trials=args.trials,
    ).run()

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(render_hosted_evaluation_report(result), end="")

    return 0


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--trials must be a positive integer."
        ) from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError(
            "--trials must be a positive integer."
        )

    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a hosted Groq model against OpsPilot's controlled "
            "incident scenarios."
        )
    )
    parser.add_argument(
        "--trials",
        type=_positive_int,
        default=3,
        help="Number of trials per scenario (default: 3).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GROQ_MODEL,
        help=f"Groq model to evaluate (default: {DEFAULT_GROQ_MODEL}).",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        help=(
            "Scenario ID to evaluate. Repeat the flag for multiple scenarios. "
            "Defaults to all scenarios."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the evaluation result as machine-readable JSON.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
