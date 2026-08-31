"""Run the deterministic evaluation suite with DeterministicModelProvider.

Requires no API key, PostgreSQL, or network access.
"""

from backend.app.evals.report import render_evaluation_report
from backend.app.evals.suite import run_deterministic_baseline_evaluation


def main() -> None:
    result = run_deterministic_baseline_evaluation()
    print(render_evaluation_report(result), end="")


if __name__ == "__main__":
    main()
