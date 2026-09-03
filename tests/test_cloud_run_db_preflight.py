"""Tests for Cloud Run DATABASE_URL preflight (parse, connect, no secret leakage)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "deploy" / "cloud-run" / "preflight_database.py"
README_PATH = ROOT / "deploy" / "cloud-run" / "README.md"
TEST_DATABASE_ENV = "OPSPILOT_TEST_DATABASE_URL"

CANONICAL_URI = (
    "postgresql://opspilot:opspilot@localhost:5432/opspilot"
    "?sslmode=require&channel_binding=require"
)
LEAK_PASSWORD = "supersecret-leak-token-9f3a"
LEAK_HOST = "ep-secret-host.example.invalid"
LEAK_USER = "neon-leak-user"
MALFORMED_EXTRA_EQUALS = (
    f"postgresql://{LEAK_USER}:{LEAK_PASSWORD}@{LEAK_HOST}/neondb"
    "?sslmode=require&channel_binding=require=require"
)
CONCATENATED_URIS = CANONICAL_URI + CANONICAL_URI
INVALID_URI = "not-a-postgres-url"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("cloud_run_db_preflight", PREFLIGHT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in ("DATABASE_URL", "CHECKOUT_DATABASE_URL"):
        merged.pop(key, None)
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(PREFLIGHT_PATH)],
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def _assert_no_leakage(*blobs: str) -> None:
    combined = "\n".join(blobs)
    assert LEAK_PASSWORD not in combined
    assert LEAK_HOST not in combined
    assert LEAK_USER not in combined
    assert CANONICAL_URI not in combined
    assert MALFORMED_EXTRA_EQUALS not in combined
    assert CONCATENATED_URIS not in combined
    assert f"{LEAK_USER}:" not in combined


def test_parse_accepts_canonical_postgres_uri() -> None:
    preflight = _load_preflight()
    preflight.parse_database_url(CANONICAL_URI)


def test_parse_rejects_malformed_query_extra_equals() -> None:
    preflight = _load_preflight()
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_database_url(MALFORMED_EXTRA_EQUALS)
    assert excinfo.value.step == "parse"
    assert "channel_binding" in excinfo.value.message
    assert "2 '=' separators" in excinfo.value.message
    _assert_no_leakage(excinfo.value.message)


def test_parse_rejects_concatenated_urls() -> None:
    preflight = _load_preflight()
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_database_url(CONCATENATED_URIS)
    assert excinfo.value.step == "parse"
    assert "concatenated" in excinfo.value.message.lower()
    _assert_no_leakage(excinfo.value.message)


def test_parse_rejects_missing_and_invalid_uri() -> None:
    preflight = _load_preflight()
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_database_url(None)
    assert excinfo.value.step == "parse"
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_database_url("")
    assert excinfo.value.step == "parse"
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_database_url(INVALID_URI)
    assert excinfo.value.step == "parse"
    _assert_no_leakage(excinfo.value.message)


def test_cli_malformed_query_does_not_leak_secrets() -> None:
    result = _run_cli({"DATABASE_URL": MALFORMED_EXTRA_EQUALS})
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "DATABASE_URL parse: FAIL" in result.stdout
    assert "PostgreSQL connection: SKIP" in result.stdout
    assert "SELECT 1: SKIP" in result.stdout
    _assert_no_leakage(output)


def test_cli_missing_url() -> None:
    result = _run_cli({})
    assert result.returncode == 1
    assert "DATABASE_URL parse: FAIL" in result.stdout
    _assert_no_leakage(result.stdout, result.stderr)


def test_valid_uri_mocked_connection(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _load_preflight()
    monkeypatch.setenv("DATABASE_URL", CANONICAL_URI)
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (1,)
    with patch.object(preflight.psycopg, "connect", return_value=conn) as mock_connect:
        assert preflight.main() == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "DATABASE_URL parse: PASS",
        "PostgreSQL connection: PASS",
        "SELECT 1: PASS",
    ]
    _assert_no_leakage(out)
    mock_connect.assert_called_once()


def test_preflight_select_1_against_test_database() -> None:
    url = os.environ.get(TEST_DATABASE_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_ENV} is not set")
    preflight = _load_preflight()
    preflight.run_preflight(url)


def test_readme_documents_pre_deploy_gate() -> None:
    text = README_PATH.read_text()
    assert "preflight_database.py" in text
    assert "--dry-run" in text
    assert "pytest" in text
    assert "No real Cloud Run deploy" in text
