"""Unit tests for header-bound secret hygiene validators (no live GCP)."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "deploy" / "cloud-run" / "preflight_header_secrets.py"
    spec = importlib.util.spec_from_file_location("header_hygiene", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_header_rejects_newline() -> None:
    mod = _load()
    with pytest.raises(RuntimeError, match="CR/LF"):
        mod.validate_raw_header_bytes(b"abc\n")


def test_raw_header_rejects_whitespace() -> None:
    mod = _load()
    with pytest.raises(RuntimeError, match="whitespace"):
        mod.validate_raw_header_bytes(b" abc")


def test_raw_header_accepts_hex_token() -> None:
    mod = _load()
    mod.validate_raw_header_bytes(b"a" * 64)


def test_basic_auth_shape() -> None:
    mod = _load()
    payload = base64.b64encode(b"1234567:token-value").decode("ascii")
    mod.validate_authorization_basic_bytes(f"Basic {payload}".encode("ascii"))
    with pytest.raises(RuntimeError, match="Basic"):
        mod.validate_authorization_basic_bytes(b"Bearer x")
