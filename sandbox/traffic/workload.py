from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from sandbox.common.lease import SandboxLeaseManager
from sandbox.scenarios import LiveScenarioMapping, get_live_scenario_mapping


@dataclass
class WorkloadSample:
    timestamp: datetime
    latency_ms: float
    success: bool
    status_code: int | None


@dataclass
class WorkloadSession:
    scenario_id: str
    incident_id: str
    service: str
    base_url: str
    started_at: datetime
    stopped: bool = False
    samples: list[WorkloadSample] = field(default_factory=list)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def stop(self) -> None:
        self.stopped = True
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


class WorkloadDriver:
    """On-demand HTTP workload for live sandbox incidents."""

    def __init__(
        self,
        *,
        lease_manager: SandboxLeaseManager | None = None,
        concurrency: int = 4,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self._lease_manager = lease_manager or SandboxLeaseManager()
        self._concurrency = concurrency
        self._request_timeout_seconds = request_timeout_seconds
        self._sessions: dict[str, WorkloadSession] = {}
        self._lock = threading.Lock()

    def service_base_url(self, mapping: LiveScenarioMapping) -> str:
        return os.environ.get(
            mapping.service_base_url_env,
            mapping.default_base_url,
        ).rstrip("/")

    def warm_service(self, mapping: LiveScenarioMapping, incident_id: str) -> None:
        base_url = self.service_base_url(mapping)
        with httpx.Client(timeout=self._request_timeout_seconds) as client:
            for _ in range(3):
                client.get(f"{base_url}/health", headers=self._headers(incident_id))

    def collect_baseline(
        self,
        mapping: LiveScenarioMapping,
        incident_id: str,
        *,
        duration_seconds: float = 3.0,
    ) -> list[WorkloadSample]:
        return self._run_traffic(
            mapping=mapping,
            incident_id=incident_id,
            duration_seconds=duration_seconds,
        )

    def start_continuous(
        self,
        mapping: LiveScenarioMapping,
        incident_id: str,
    ) -> WorkloadSession:
        with self._lock:
            existing = self._sessions.get(incident_id)
            if existing is not None and not existing.stopped:
                return existing
        base_url = self.service_base_url(mapping)
        session = WorkloadSession(
            scenario_id=mapping.scenario_id,
            incident_id=incident_id,
            service=mapping.affected_service,
            base_url=base_url,
            started_at=datetime.now(UTC),
        )

        def _worker() -> None:
            while not session._stop_event.is_set():
                batch = self._run_traffic(
                    mapping=mapping,
                    incident_id=incident_id,
                    duration_seconds=1.0,
                    base_url=base_url,
                )
                session.samples.extend(batch)
                session._stop_event.wait(0.25)

        thread = threading.Thread(target=_worker, daemon=True)
        session._thread = thread
        with self._lock:
            self._sessions[incident_id] = session
        thread.start()
        return session

    def stop(self, incident_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(incident_id, None)
        if session is not None:
            session.stop()

    def acquire_lease(self, service: str, incident_id: str) -> bool:
        return self._lease_manager.try_acquire(service, incident_id) is not None

    def release_lease(self, service: str, incident_id: str) -> None:
        self._lease_manager.release(service, incident_id)

    def is_sandbox_busy(self, service: str, incident_id: str) -> bool:
        return self._lease_manager.is_busy(service, incident_id)

    def _headers(self, incident_id: str) -> dict[str, str]:
        return {"X-Correlation-Id": incident_id}

    def _run_traffic(
        self,
        *,
        mapping: LiveScenarioMapping,
        incident_id: str,
        duration_seconds: float,
        base_url: str | None = None,
    ) -> list[WorkloadSample]:
        resolved_base = (base_url or self.service_base_url(mapping)).rstrip("/")
        deadline = time.monotonic() + duration_seconds
        samples: list[WorkloadSample] = []

        def _one_request() -> WorkloadSample:
            start = time.perf_counter()
            status_code: int | None = None
            success = False
            try:
                with httpx.Client(timeout=self._request_timeout_seconds) as client:
                    if mapping.affected_service == "checkout-api":
                        response = client.post(
                            f"{resolved_base}/api/v1/checkout",
                            headers=self._headers(incident_id),
                        )
                    elif mapping.affected_service == "auth-service":
                        token_response = client.post(
                            f"{resolved_base}/oauth/token",
                            json={"client_id": "checkout-web"},
                            headers=self._headers(incident_id),
                        )
                        token = token_response.json().get("access_token", "")
                        response = client.post(
                            f"{resolved_base}/oauth/validate",
                            headers={
                                **self._headers(incident_id),
                                "Authorization": f"Bearer {token}",
                            },
                        )
                    else:
                        response = client.post(
                            f"{resolved_base}/v1/charges",
                            json={"amount_cents": 1299, "currency": "USD"},
                            headers=self._headers(incident_id),
                        )
                    status_code = response.status_code
                    success = response.is_success
            except Exception:
                success = False
            latency_ms = (time.perf_counter() - start) * 1000
            return WorkloadSample(
                timestamp=datetime.now(UTC),
                latency_ms=latency_ms,
                success=success,
                status_code=status_code,
            )

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            futures = []
            while time.monotonic() < deadline:
                futures.append(executor.submit(_one_request))
                time.sleep(0.05)
            for future in as_completed(futures):
                samples.append(future.result())
        return samples

    def summarize_samples(self, samples: list[WorkloadSample]) -> dict[str, Any]:
        if not samples:
            return {
                "request_count": 0,
                "p95_latency_ms": 0,
                "error_rate_percent": 0.0,
            }
        latencies = sorted(sample.latency_ms for sample in samples)
        p95_index = max(0, int(len(latencies) * 0.95) - 1)
        failures = sum(1 for sample in samples if not sample.success)
        return {
            "request_count": len(samples),
            "p95_latency_ms": int(latencies[p95_index]),
            "error_rate_percent": round((failures / len(samples)) * 100, 2),
        }
