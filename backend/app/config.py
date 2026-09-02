from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.deterministic_provider import DeterministicModelProvider
from backend.app.models.groq_provider import DEFAULT_GROQ_MODEL, GroqModelProvider
from backend.app.models.provider import ModelProvider
from backend.app.telemetry.models import TelemetryMode

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


class DeploymentProfile(str, Enum):
    VM_PRODUCTION = "vm_production"
    EPHEMERAL_LIVE_LAB = "ephemeral_live_lab"


class ModelProviderKind(str, Enum):
    GROQ = "groq"
    DETERMINISTIC = "deterministic"


class OpsPilotSettings(BaseModel):
    """Validated runtime configuration loaded from environment variables."""

    model_config = ConfigDict(frozen=True)

    environment: Environment = Environment.DEVELOPMENT
    deployment_profile: DeploymentProfile = DeploymentProfile.VM_PRODUCTION
    model_provider: ModelProviderKind
    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model: str = DEFAULT_GROQ_MODEL
    database_url: str | None = Field(default=None, repr=False)
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    telemetry_mode: TelemetryMode = TelemetryMode.REFERENCE
    prometheus_url: str | None = None
    loki_url: str | None = None
    sandbox_control_token: str | None = Field(default=None, repr=False)
    # Public sandbox hardening
    session_cookie_secure: bool = False
    lease_ttl_seconds: int = 600
    incident_ttl_seconds: int = 1800
    turnstile_required: bool = False
    turnstile_secret_key: str | None = Field(default=None, repr=False)
    turnstile_site_key: str | None = None
    rate_limit_burst_per_ip: int = 10
    rate_limit_window_seconds: float = 60.0
    quota_max_live_incidents_per_session: int = 3
    quota_max_model_calls_per_incident: int = 5
    quota_max_model_calls_per_session_per_day: int = 20
    quota_global_daily_model_call_cap: int = 500
    cleanup_interval_seconds: float = 30.0
    public_domain: str | None = None
    session_cookie_samesite: Literal["lax", "none", "strict"] = "lax"
    session_cookie_domain: str | None = None
    trust_proxy_headers: bool = False

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
            deployment_profile=_parse_deployment_profile(
                env.get("OPSPILOT_DEPLOYMENT_PROFILE", "vm_production")
            ),
            model_provider=model_provider,
            groq_api_key=_optional(env.get("GROQ_API_KEY")),
            groq_model=env.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip(),
            database_url=_optional(env.get("DATABASE_URL")),
            cors_origins=env.get("OPSPILOT_CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)),
            telemetry_mode=_parse_telemetry_mode(env.get("OPSPILOT_TELEMETRY_MODE", "reference")),
            prometheus_url=_optional(env.get("OPSPILOT_PROMETHEUS_URL")),
            loki_url=_optional(env.get("OPSPILOT_LOKI_URL")),
            sandbox_control_token=_optional(env.get("SANDBOX_CONTROL_TOKEN")),
            session_cookie_secure=_parse_bool(
                env.get("OPSPILOT_SESSION_COOKIE_SECURE", "false")
            ),
            lease_ttl_seconds=_parse_int(env.get("OPSPILOT_LEASE_TTL_SECONDS", "600")),
            incident_ttl_seconds=_parse_int(
                env.get("OPSPILOT_INCIDENT_TTL_SECONDS", "1800")
            ),
            turnstile_required=_parse_bool(env.get("OPSPILOT_TURNSTILE_REQUIRED", "false")),
            turnstile_secret_key=_optional(env.get("OPSPILOT_TURNSTILE_SECRET_KEY")),
            turnstile_site_key=_optional(env.get("OPSPILOT_TURNSTILE_SITE_KEY")),
            rate_limit_burst_per_ip=_parse_int(
                env.get("OPSPILOT_RATE_LIMIT_BURST_PER_IP", "10")
            ),
            rate_limit_window_seconds=_parse_float(
                env.get("OPSPILOT_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            quota_max_live_incidents_per_session=_parse_int(
                env.get("OPSPILOT_QUOTA_MAX_LIVE_INCIDENTS_PER_SESSION", "3")
            ),
            quota_max_model_calls_per_incident=_parse_int(
                env.get("OPSPILOT_QUOTA_MAX_MODEL_CALLS_PER_INCIDENT", "5")
            ),
            quota_max_model_calls_per_session_per_day=_parse_int(
                env.get("OPSPILOT_QUOTA_MAX_MODEL_CALLS_PER_SESSION_PER_DAY", "20")
            ),
            quota_global_daily_model_call_cap=_parse_int(
                env.get("OPSPILOT_QUOTA_GLOBAL_DAILY_MODEL_CALL_CAP", "500")
            ),
            cleanup_interval_seconds=_parse_float(
                env.get("OPSPILOT_CLEANUP_INTERVAL_SECONDS", "30")
            ),
            public_domain=_optional(env.get("OPSPILOT_PUBLIC_DOMAIN")),
            session_cookie_samesite=_parse_samesite(
                env.get("OPSPILOT_SESSION_COOKIE_SAMESITE", "lax")
            ),
            session_cookie_domain=_optional(env.get("OPSPILOT_SESSION_COOKIE_DOMAIN")),
            trust_proxy_headers=_parse_bool(
                env.get("OPSPILOT_TRUST_PROXY_HEADERS", "false")
            ),
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
        if self.telemetry_mode is TelemetryMode.LIVE:
            if not self.prometheus_url:
                raise ConfigurationError(
                    "OPSPILOT_PROMETHEUS_URL is required when OPSPILOT_TELEMETRY_MODE=live."
                )
            if not self.loki_url:
                raise ConfigurationError(
                    "OPSPILOT_LOKI_URL is required when OPSPILOT_TELEMETRY_MODE=live."
                )
            if not self.sandbox_control_token:
                raise ConfigurationError(
                    "SANDBOX_CONTROL_TOKEN is required when OPSPILOT_TELEMETRY_MODE=live."
                )

    @property
    def is_live_telemetry_mode(self) -> bool:
        return self.telemetry_mode is TelemetryMode.LIVE

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
        summary = {
            "environment": self.environment.value,
            "deployment_profile": self.deployment_profile.value,
            "model_provider": self.model_provider.value,
            "database": database_status,
            "telemetry_mode": self.telemetry_mode.value,
        }
        if self.turnstile_site_key:
            summary["turnstile_site_key"] = self.turnstile_site_key
        return summary


def _parse_telemetry_mode(value: str) -> TelemetryMode:
    try:
        return TelemetryMode(value.strip().lower())
    except ValueError as exc:
        raise ConfigurationError(
            "OPSPILOT_TELEMETRY_MODE must be 'reference' or 'live'."
        ) from exc


def _parse_deployment_profile(value: str) -> DeploymentProfile:
    try:
        return DeploymentProfile(value.strip().lower())
    except ValueError as exc:
        raise ConfigurationError(
            "OPSPILOT_DEPLOYMENT_PROFILE must be 'vm_production' or "
            "'ephemeral_live_lab'."
        ) from exc


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


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str) -> int:
    return int(value.strip())


def _parse_float(value: str) -> float:
    return float(value.strip())


def _parse_samesite(value: str) -> Literal["lax", "none", "strict"]:
    normalized = value.strip().lower()
    if normalized not in {"lax", "none", "strict"}:
        raise ConfigurationError(
            "OPSPILOT_SESSION_COOKIE_SAMESITE must be 'lax', 'none', or 'strict'."
        )
    return normalized  # type: ignore[return-value]
