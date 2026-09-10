from __future__ import annotations

from tests.fakes import FakeModelProvider


def test_hosted_cli_prints_human_report_without_exposing_api_key(
    monkeypatch,
    capsys,
) -> None:
    from backend.app.evals import run_hosted

    captured: dict[str, object] = {}

    def fake_groq_provider(*, api_key: str, model: str):
        captured["api_key"] = api_key
        captured["model"] = model
        return FakeModelProvider()

    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key-for-test")
    monkeypatch.setattr(run_hosted, "GroqModelProvider", fake_groq_provider)

    exit_code = run_hosted.main(
        [
            "--trials",
            "1",
            "--model",
            "test-model",
            "--scenario",
            "checkout-db-pool-regression",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured == {
        "api_key": "fake-groq-key-for-test",
        "model": "test-model",
    }

    assert "OpsPilot Hosted-Model Evaluation" in output
    assert "Provider: groq" in output
    assert "Model: test-model" in output
    assert "Trials per scenario: 1" in output
    assert "checkout-db-pool-regression: 1/1 passed (100.0%)" in output

    assert "fake-groq-key-for-test" not in output
    assert "GROQ_API_KEY" not in output


def test_hosted_cli_can_emit_machine_readable_json(
    monkeypatch,
    capsys,
) -> None:
    import json

    from backend.app.evals import run_hosted

    def fake_groq_provider(*, api_key: str, model: str):
        return FakeModelProvider()

    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key-for-json-test")
    monkeypatch.setattr(run_hosted, "GroqModelProvider", fake_groq_provider)

    exit_code = run_hosted.main(
        [
            "--trials",
            "1",
            "--model",
            "json-test-model",
            "--scenario",
            "checkout-db-pool-regression",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""

    assert payload["provider"] == "groq"
    assert payload["model"] == "json-test-model"
    assert payload["trials_per_scenario"] == 1
    assert payload["total_evaluations"] == 1
    assert payload["successful_evaluations"] == 1
    assert payload["failed_evaluations"] == 0
    assert payload["provider_success_rate"] == 1.0
    assert payload["end_to_end_pass_rate"] == 1.0

    assert payload["scenario_results"][0]["scenario_id"] == (
        "checkout-db-pool-regression"
    )
    assert payload["scenario_results"][0]["pass_rate"] == 1.0
    assert payload["provider_failures"] == []

    serialized = captured.out
    assert "fake-groq-key-for-json-test" not in serialized
    assert "GROQ_API_KEY" not in serialized


def test_hosted_cli_fails_cleanly_when_api_key_is_missing(
    monkeypatch,
    capsys,
) -> None:
    from backend.app.evals import run_hosted

    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    exit_code = run_hosted.main(
        [
            "--trials",
            "1",
            "--scenario",
            "checkout-db-pool-regression",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "Hosted evaluation requires GROQ_API_KEY.\n"


def test_hosted_cli_rejects_non_positive_trial_count_before_provider_creation(
    monkeypatch,
    capsys,
) -> None:
    import pytest

    from backend.app.evals import run_hosted

    provider_created = False

    def fake_groq_provider(*, api_key: str, model: str):
        nonlocal provider_created
        provider_created = True
        return FakeModelProvider()

    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key-for-invalid-trials-test")
    monkeypatch.setattr(run_hosted, "GroqModelProvider", fake_groq_provider)

    with pytest.raises(SystemExit) as exc_info:
        run_hosted.main(["--trials", "0"])

    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert provider_created is False
    assert captured.out == ""
    assert "--trials" in captured.err
    assert "fake-groq-key-for-invalid-trials-test" not in captured.err
