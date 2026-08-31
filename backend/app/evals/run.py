"""Run the deterministic evaluation suite with FakeModelProvider.

Requires no API key, PostgreSQL, or network access.
"""

from backend.app.evals.report import render_evaluation_report
from backend.app.evals.suite import EvaluationSuiteRunner


def main() -> None:
    from tests.fakes import FakeModelProvider

    result = EvaluationSuiteRunner(provider=FakeModelProvider()).run()
    print(render_evaluation_report(result), end="")


if __name__ == "__main__":
    main()
