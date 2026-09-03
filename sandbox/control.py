from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from sandbox.scenarios import LiveScenarioMapping, parse_deployment_timestamp


@dataclass(frozen=True)
class SandboxServiceEndpoints:
    service: str
    base_url: str
    control_token: str


class SandboxControlClient:
    """OpsPilot-controlled sandbox mutation client (never exposed to the LLM)."""

    def __init__(
        self,
        endpoints: SandboxServiceEndpoints,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._endpoints = endpoints
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_mapping(
        cls,
        mapping: LiveScenarioMapping,
        *,
        control_token: str | None = None,
        base_url: str | None = None,
    ) -> SandboxControlClient:
        resolved_url = base_url or os.environ.get(
            mapping.service_base_url_env,
            mapping.default_base_url,
        )
        token = control_token or os.environ.get(
            "SANDBOX_CONTROL_TOKEN",
            "sandbox-control-test-token",
        )
        # Secret Manager / shell piping can introduce trailing newlines; HTTP headers forbid them.
        token = token.strip()
        return cls(
            SandboxServiceEndpoints(
                service=mapping.affected_service,
                base_url=resolved_url.rstrip("/"),
                control_token=token,
            )
        )

    def _headers(self) -> dict[str, str]:
        return {"X-Sandbox-Control-Token": self._endpoints.control_token.strip()}

    def get_revision(self) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.get(f"{self._endpoints.base_url}/internal/revision")
            response.raise_for_status()
            return response.json()

    def activate_fault(self) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(
                f"{self._endpoints.base_url}/internal/control/activate-fault",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    def rollback(self, version: str) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(
                f"{self._endpoints.base_url}/internal/control/rollback",
                headers=self._headers(),
                json={"version": version},
            )
            response.raise_for_status()
            return response.json()

    def get_deployments(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.get(f"{self._endpoints.base_url}/internal/deployments")
            response.raise_for_status()
            return response.json()

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.get(f"{self._endpoints.base_url}/health")
            response.raise_for_status()
            return response.json()

    def deployment_events(self) -> list[tuple[str, datetime]]:
        events = []
        for item in self.get_deployments():
            events.append(
                (
                    str(item["version"]),
                    parse_deployment_timestamp(str(item["timestamp"])),
                )
            )
        return events
