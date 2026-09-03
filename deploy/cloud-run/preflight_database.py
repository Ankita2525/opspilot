#!/usr/bin/env python3
"""Pre-deploy check that DATABASE_URL is a single valid libpq URI and accepts SELECT 1.

Reads DATABASE_URL only from the environment. Never prints the connection
string, username, password, hostname, or other secret contents.
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import conninfo

CONNECT_TIMEOUT_SECONDS = 8
SAFE_QUERY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


class PreflightError(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


def _safe_query_name(name: str) -> str:
    if SAFE_QUERY_NAME.fullmatch(name):
        return name
    return "<redacted>"


def safe_error_text(exc: BaseException, url: str | None) -> str:
    """Return an exception summary with credential material stripped."""
    text = f"{type(exc).__name__}: {exc}"
    if not url:
        return text
    text = text.replace(url, "<redacted>")
    try:
        parts = urlsplit(url.strip().strip("\"'"))
    except Exception:
        return text
    for piece in (
        parts.password,
        parts.username,
        parts.hostname,
        parts.netloc,
        parts.path,
        parts.query,
        parts.fragment,
    ):
        if piece:
            text = text.replace(str(piece), "<redacted>")
    return text


def parse_database_url(url: str | None) -> None:
    """Reject empty/malformed URIs using structural checks and libpq."""
    if url is None or not str(url).strip():
        raise PreflightError("parse", "DATABASE_URL is missing or empty")

    raw = str(url)
    scheme_hits = raw.count("postgresql://") + raw.count("postgres://")
    if scheme_hits > 1:
        raise PreflightError(
            "parse",
            "DATABASE_URL contains more than one URI (concatenated URLs)",
        )

    parts = urlsplit(raw)
    if parts.scheme not in {"postgresql", "postgres"}:
        raise PreflightError("parse", "DATABASE_URL must use postgresql:// or postgres://")
    if not parts.hostname:
        raise PreflightError("parse", "DATABASE_URL is missing a hostname")
    if not parts.path or parts.path in {"", "/"}:
        raise PreflightError("parse", "DATABASE_URL is missing a database name")

    if parts.query:
        for segment in parts.query.split("&"):
            equals = segment.count("=")
            if equals != 1:
                name = unquote(segment.split("=", 1)[0]) if segment else "<empty>"
                raise PreflightError(
                    "parse",
                    f"query parameter '{_safe_query_name(name)}' has "
                    f"{equals} '=' separators (expected 1)",
                )

    try:
        conninfo.conninfo_to_dict(raw)
    except Exception as exc:
        raise PreflightError("parse", safe_error_text(exc, raw)) from None


def ping_database(url: str) -> None:
    try:
        conn = psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except Exception as exc:
        raise PreflightError("connection", safe_error_text(exc, url)) from None
    try:
        try:
            row = conn.execute("SELECT 1").fetchone()
        except Exception as exc:
            raise PreflightError("select", safe_error_text(exc, url)) from None
        if row is None or row[0] != 1:
            raise PreflightError("select", "SELECT 1 did not return 1")
    finally:
        conn.close()


def run_preflight(url: str | None) -> None:
    parse_database_url(url)
    assert url is not None
    ping_database(url)


def _print_status(*, parse: str, connection: str, select: str) -> None:
    print(f"DATABASE_URL parse: {parse}")
    print(f"PostgreSQL connection: {connection}")
    print(f"SELECT 1: {select}")


def main(argv: list[str] | None = None) -> int:
    del argv
    url = os.environ.get("DATABASE_URL")
    try:
        run_preflight(url)
    except PreflightError as exc:
        if exc.step == "parse":
            _print_status(parse=FAIL, connection=SKIP, select=SKIP)
        elif exc.step == "connection":
            _print_status(parse=PASS, connection=FAIL, select=SKIP)
        else:
            _print_status(parse=PASS, connection=PASS, select=FAIL)
        print(exc.message)
        return 1
    _print_status(parse=PASS, connection=PASS, select=PASS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
