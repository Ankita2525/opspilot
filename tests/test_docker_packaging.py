from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DOCKERFILE = REPO_ROOT / "Dockerfile"
SANDBOX_DOCKERFILE = REPO_ROOT / "sandbox" / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

SANDBOX_SERVICES = (
    "checkout-api",
    "auth-service",
    "payments-service",
    "provider-service",
)


def test_uvicorn_is_a_runtime_dependency() -> None:
    contents = PYPROJECT.read_text(encoding="utf-8")
    assert "uvicorn" in contents


def test_backend_dockerfile_uses_uv_venv_path() -> None:
    contents = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    assert 'PATH="/app/.venv/bin:$PATH"' in contents
    assert "uv sync --frozen --no-dev" in contents


def test_backend_dockerfile_includes_sandbox_for_live_mode() -> None:
    contents = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY sandbox ./sandbox" in contents


def test_sandbox_dockerfile_matches_uv_venv_convention() -> None:
    contents = SANDBOX_DOCKERFILE.read_text(encoding="utf-8")
    assert 'PATH="/app/.venv/bin:$PATH"' in contents
    assert "uv sync --frozen --no-dev" in contents
    assert "pip install" not in contents or "pip install --no-cache-dir uv" in contents


def test_compose_sandbox_services_invoke_uvicorn() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    for service in SANDBOX_SERVICES:
        marker = f"\n  {service}:\n"
        assert marker in compose, f"missing service block for {service}"
        section = compose.split(marker, 1)[1].split("\n\n", 1)[0]
        assert "sandbox/Dockerfile" in section
        assert '"uvicorn"' in section
