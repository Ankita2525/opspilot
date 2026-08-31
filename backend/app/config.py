from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.deterministic_provider import DeterministicModelProvider
from backend.app.models.groq_provider import DEFAULT_GROQ_MODEL, GroqModelProvider
from backend.app.models.provider import ModelProvider

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


class ConfigurationError(ValueError):
    """Raised when runtime configuration is invalid."""


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TEST = "test"


class ModelProviderKind(str, Enum):
    GROQ = "groq"
    DETERMINISTIC = "deterministic"


class OpsPilotSettings(BaseModel):
    """Validated runtime configuration loaded from environment variables."""

    model_config = ConfigDict(frozen=True)

    environment: Environment = Environment.DEVELOPMENT
    model_provider: ModelProviderKind
    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model: str = DEFAULT_GROQ_MODEL
    database_url: str | None = Field(default=None, repr=False)
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return DEFAULT_CORS_ORIGINS
        if isinstance(value, str):
            origins = tuple(
                origin.strip()
                for origin in value.split(",")
                if origin.strip()
            )
            if not origins:
                raise ConfigurationError(
                    "OPSPILOT_CORS_ORIGINS must include at least one origin."
                )
            return origins
        if isinstance(value, (list, tuple)):
            origins = tuple(str(origin).strip() for origin in value if str(origin).strip())
            if not origins:
                raise ConfigurationError(
                    "OPSPILOT_CORS_ORIGINS must include at least one origin."
                )
            return origins
        raise ConfigurationError("OPSPILOT_CORS_ORIGINS must be a comma-separated list.")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = environ if environ is not None else os.environ
        provider_raw = _required(env, "OPSPILOT_MODEL_PROVIDER")
        try:
            model_provider = ModelProviderKind(provider_raw.strip().lower())
        except ValueError as exc:
            raise ConfigurationError(
                "OPSPILOT_MODEL_PROVIDER must be 'groq' or 'deterministic'."
            ) from exc

        environment_raw = env.get("OPSPILOT_ENV", Environment.DEVELOPMENT.value)
        try:
            environment = Environment(environment_raw.strip().lower())
        except ValueError as exc:
            raise ConfigurationError(
                "OPSPILOT_ENV must be 'development', 'production', or 'test'."
            ) from exc

        settings = cls(
            environment=environment,
            model_provider=model_provider,
            groq_api_key=_optional(env.get("GROQ_API_KEY")),
            groq_model=env.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip(),
            database_url=_optional(env.get("DATABASE_URL")),
            cors_origins=env.get("OPSPILOT_CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)),
        )
        settings.validate_runtime()
        return settings

    def validate_runtime(self) -> None:
        if self.model_provider is ModelProviderKind.GROQ and not self.groq_api_key:
            raise ConfigurationError(
                "GROQ_API_KEY is required when OPSPILOT_MODEL_PROVIDER=groq."
            )
        if self.environment is Environment.PRODUCTION and not self.database_url:
            raise ConfigurationError(
                "DATABASE_URL is required when OPSPILOT_ENV=production."
            )

    @property
    def uses_postgres(self) -> bool:
        return self.database_url is not None

    @property
    def is_reference_model_mode(self) -> bool:
        return self.model_provider is ModelProviderKind.DETERMINISTIC

    def create_provider(self) -> ModelProvider:
        if self.model_provider is ModelProviderKind.GROQ:
            return GroqModelProvider(
                api_key=self.groq_api_key,
                model=self.groq_model,
            )
        return DeterministicModelProvider()

    def safe_summary(self) -> dict[str, str]:
        database_status = "configured" if self.database_url else "not_configured"
        return {
            "environment": self.environment.value,
            "model_provider": self.model_provider.value,
            "database": database_status,
        }


def _required(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key)
    if value is None or not value.strip():
        raise ConfigurationError(f"{key} is required.")
    return value.strip()


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
