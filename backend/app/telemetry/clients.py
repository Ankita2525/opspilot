from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.app.telemetry.health import with_bounded_retry

PROMETHEUS_SCRAPE_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class PrometheusConfig:
    base_url: str
    timeout_seconds: float = 5.0
    scrape_interval_seconds: float = PROMETHEUS_SCRAPE_INTERVAL_SECONDS


class PrometheusClient:
    def __init__(self, config: PrometheusConfig) -> None:
        self._config = config

    def _query_instant(self, promql: str) -> tuple[float, datetime] | None:
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.get(
                f"{self._config.base_url.rstrip('/')}/api/v1/query",
                params={"query": promql},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success":
            return None
        result = payload.get("data", {}).get("result", [])
        if not result:
            return None
        value = result[0].get("value")
        if not value or len(value) < 2:
            return None
        try:
            timestamp = datetime.fromtimestamp(float(value[0]), tz=UTC)
            return float(value[1]), timestamp
        except (TypeError, ValueError):
            return None

    def query_scalar(self, promql: str) -> float | None:
        observation = self._query_instant(promql)
        if observation is None:
            return None
        return observation[0]

    def query_p95_latency_ms(self, service: str, window: str = "2m") -> int | None:
        promql = (
            f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket"
            f'{{service="{service}"}}[{window}])) by (le))'
        )
        value = self.query_scalar(promql)
        if value is None:
            return None
        return int(value * 1000)

    def query_p95_latency_ms_with_timestamp(
        self, service: str, window: str = "2m"
    ) -> tuple[int, datetime] | None:
        promql = (
            f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket"
            f'{{service="{service}"}}[{window}])) by (le))'
        )
        observation = self._query_instant(promql)
        if observation is None:
            return None
        return int(observation[0] * 1000), observation[1]

    def query_error_rate_percent(self, service: str, window: str = "2m") -> float | None:
        errors = self.query_scalar(
            f'sum(rate(http_errors_total{{service="{service}"}}[{window}]))'
        )
        total = self.query_scalar(
            f'sum(rate(http_requests_total{{service="{service}"}}[{window}]))'
        )
        if errors is None or total is None or total == 0:
            return None
        return round((errors / total) * 100, 2)

    def query_error_rate_percent_with_timestamp(
        self, service: str, window: str = "2m"
    ) -> tuple[float, datetime] | None:
        errors_obs = self._query_instant(
            f'sum(rate(http_errors_total{{service="{service}"}}[{window}]))'
        )
        total_obs = self._query_instant(
            f'sum(rate(http_requests_total{{service="{service}"}}[{window}]))'
        )
        if total_obs is None or total_obs[0] == 0:
            return None
        if errors_obs is None:
            return 0.0, total_obs[1]
        observed_at = max(errors_obs[1], total_obs[1])
        return round((errors_obs[0] / total_obs[0]) * 100, 2), observed_at

    def query_request_rate_with_timestamp(
        self, service: str, window: str = "2m"
    ) -> tuple[float, datetime] | None:
        return self._query_instant(
            f'sum(rate(http_requests_total{{service="{service}"}}[{window}]))'
        )

    def is_ready(self) -> bool:
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.get(f"{self._config.base_url.rstrip('/')}/-/ready")
            response.raise_for_status()
            return True


@dataclass(frozen=True)
class LokiConfig:
    base_url: str
    timeout_seconds: float = 5.0
    username: str | None = None
    api_key: str | None = None

    @property
    def _http_auth(self) -> tuple[str, str] | None:
        if self.username and self.api_key:
            return (self.username, self.api_key)
        return None


def loki_config_from_environ(environ: Mapping[str, str] | None = None) -> LokiConfig:
    env = environ if environ is not None else os.environ
    username = _optional_env(env.get("OPSPILOT_LOKI_USERNAME"))
    api_key = _optional_env(env.get("OPSPILOT_LOKI_API_KEY"))
    return LokiConfig(
        base_url=env.get("OPSPILOT_LOKI_URL", "http://localhost:3100"),
        username=username,
        api_key=api_key,
    )


def _optional_env(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class LokiClient:
    def __init__(self, config: LokiConfig) -> None:
        self._config = config

    def is_api_ready(self) -> bool:
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.get(
                f"{self._config.base_url.rstrip('/')}/loki/api/v1/status/buildinfo",
                auth=self._config._http_auth,
            )
            response.raise_for_status()
            payload = response.json()
        return bool(payload.get("version"))

    def wait_until_ready(
        self,
        *,
        max_attempts: int = 12,
        base_delay_seconds: float = 2.0,
    ) -> bool:
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                if self.is_api_ready():
                    return True
            except Exception as exc:
                last_error = exc
            if attempt + 1 < max_attempts:
                time.sleep(min(base_delay_seconds * (2**attempt), 15.0))
        return False

    def query_logs(
        self,
        service: str,
        *,
        limit: int = 50,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        end = datetime.now(UTC)
        start = end - __import__("datetime").timedelta(minutes=lookback_minutes)
        return self.query_logs_since(
            service,
            since=start,
            until=end,
            limit=limit,
        )

    def query_logs_since(
        self,
        service: str,
        *,
        since: datetime,
        until: datetime | None = None,
        limit: int = 50,
        search_text: str | None = None,
    ) -> list[dict[str, Any]]:
        query = f'{{service_name="{service}"}}'
        if search_text:
            query += f' |= "{search_text}"'
        end = until or datetime.now(UTC)
        params = {
            "query": query,
            "limit": str(limit),
            "start": str(int(since.timestamp() * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "direction": "backward",
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.get(
                f"{self._config.base_url.rstrip('/')}/loki/api/v1/query_range",
                params=params,
                auth=self._config._http_auth,
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success":
            return []
        entries: list[dict[str, Any]] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for value in stream.get("values", []):
                if len(value) < 2:
                    continue
                ts_ns, line = value
                entries.append(_parse_loki_entry(line, labels, int(ts_ns)))
        entries.sort(key=lambda item: item["timestamp"], reverse=True)
        return entries[:limit]

    def contains_log_since(
        self,
        service: str,
        *,
        since: datetime,
        search_text: str,
    ) -> bool:
        return bool(
            self.query_logs_since(service, since=since, search_text=search_text, limit=5)
        )


def _parse_loki_entry(line: str, labels: dict[str, Any], ts_ns: int) -> dict[str, Any]:
    observed_at = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
    service = str(labels.get("service_name", labels.get("service", "")))
    level = str(labels.get("severity_text", labels.get("severity", labels.get("level", "INFO"))))
    message = line
    revision = labels.get("revision")
    try:
        payload = json.loads(line)
        if isinstance(payload, dict):
            service = str(payload.get("service", service))
            level = str(payload.get("severity", level))
            message = str(payload.get("message", line))
            revision = payload.get("revision", revision)
            timestamp_raw = payload.get("timestamp")
            if isinstance(timestamp_raw, str):
                observed_at = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    except json.JSONDecodeError:
        pass
    return {
        "timestamp": observed_at,
        "service": service,
        "level": level,
        "message": message,
        "revision": revision,
    }
