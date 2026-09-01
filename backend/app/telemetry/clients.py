from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class PrometheusConfig:
    base_url: str
    timeout_seconds: float = 5.0


class PrometheusClient:
    def __init__(self, config: PrometheusConfig) -> None:
        self._config = config

    def query_scalar(self, promql: str) -> float | None:
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
            return float(value[1])
        except (TypeError, ValueError):
            return None

    def query_p95_latency_ms(self, service: str, window: str = "2m") -> int | None:
        promql = (
            f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket"
            f'{{service="{service}"}}[{window}])) by (le))'
        )
        value = self.query_scalar(promql)
        if value is None:
            return None
        return int(value * 1000)

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


@dataclass(frozen=True)
class LokiConfig:
    base_url: str
    timeout_seconds: float = 5.0


class LokiClient:
    def __init__(self, config: LokiConfig) -> None:
        self._config = config

    def query_logs(
        self,
        service: str,
        *,
        limit: int = 50,
        lookback_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        query = f'{{service="{service}"}}'
        end = datetime.now(UTC)
        start = end.timestamp() - (lookback_minutes * 60)
        params = {
            "query": query,
            "limit": str(limit),
            "start": str(int(start * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "direction": "backward",
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.get(
                f"{self._config.base_url.rstrip('/')}/loki/api/v1/query_range",
                params=params,
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
                entries.append(
                    {
                        "timestamp": datetime.fromtimestamp(
                            int(ts_ns) / 1_000_000_000,
                            tz=UTC,
                        ),
                        "service": labels.get("service", service),
                        "level": labels.get("severity", "INFO"),
                        "message": line,
                    }
                )
        entries.sort(key=lambda item: item["timestamp"], reverse=True)
        return entries[:limit]
