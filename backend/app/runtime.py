from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver

from backend.app.config import Environment, OpsPilotSettings
from backend.app.models.provider import ModelProvider
from backend.app.persistence.memory import InMemoryOpsPilotRepository
from backend.app.persistence.postgres import PostgresOpsPilotRepository
from backend.app.persistence.repository import OpsPilotRepository
from backend.app.persistence.schema import initialize_schema

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


class ProductionCheckpointerError(RuntimeError):
    """Raised when production runtime cannot use the required Postgres checkpointer."""


@dataclass
class RuntimeResources:
    provider: ModelProvider
    repository: OpsPilotRepository
    checkpointer: BaseCheckpointSaver | None
    settings: OpsPilotSettings | None
    _checkpointer_cm: AbstractContextManager[PostgresSaver] | None = None

    @property
    def database_status(self) -> str:
        if self.settings is None or not self.settings.uses_postgres:
            return "in_memory"
        if self.ping_database():
            return "ready"
        return "unavailable"

    @property
    def model_provider_name(self) -> str:
        if self.settings is None:
            return "injected"
        return self.settings.model_provider.value

    @property
    def requires_postgres_checkpointer(self) -> bool:
        if self.settings is None:
            return False
        return (
            self.settings.environment is Environment.PRODUCTION
            and self.settings.uses_postgres
        )

    @property
    def allow_in_memory_checkpointer(self) -> bool:
        return not self.requires_postgres_checkpointer

    def ensure_production_checkpointer_configured(self) -> None:
        if self.requires_postgres_checkpointer and self.checkpointer is None:
            raise ProductionCheckpointerError(
                "Production PostgreSQL runtime requires a Postgres checkpointer."
            )

    def startup(self) -> None:
        if self.settings is None or not self.settings.uses_postgres:
            return
        database_url = self.settings.database_url
        if database_url is None:
            return
        initialize_schema(database_url)
        self._checkpointer_cm = PostgresSaver.from_conn_string(database_url)
        checkpointer = self._checkpointer_cm.__enter__()
        checkpointer.setup()
        self.checkpointer = checkpointer
        self.ensure_production_checkpointer_configured()

    def shutdown(self) -> None:
        if self._checkpointer_cm is not None:
            self._checkpointer_cm.__exit__(None, None, None)
            self._checkpointer_cm = None

    def ping_database(self) -> bool:
        if self.settings is None or not self.settings.uses_postgres:
            return True
        database_url = self.settings.database_url
        if database_url is None:
            return True
        try:
            with psycopg.connect(database_url) as connection:
                connection.execute("SELECT 1")
            return True
        except Exception:
            return False


def build_runtime_from_settings(settings: OpsPilotSettings) -> RuntimeResources:
    provider = settings.create_provider()
    if settings.uses_postgres:
        database_url = settings.database_url
        if database_url is None:
            raise ValueError("database_url must be set for PostgreSQL runtime")
        repository: OpsPilotRepository = PostgresOpsPilotRepository(database_url)
        return RuntimeResources(
            provider=provider,
            repository=repository,
            checkpointer=None,
            settings=settings,
        )
    return RuntimeResources(
        provider=provider,
        repository=InMemoryOpsPilotRepository(),
        checkpointer=None,
        settings=settings,
    )
