from backend.app.observability.tracing import get_tracer
from backend.app.telemetry.backend import TelemetryBackend
from backend.app.tools.schemas import (
    DeploymentResponse,
    LogResponse,
    MetricResponse,
    ServiceHealthResponse,
)
from simulator.environment import SimulatedEnvironment


class DiagnosticTools:
    """Read-only diagnostic access to a telemetry backend."""

    def __init__(self, backend: TelemetryBackend | SimulatedEnvironment) -> None:
        if isinstance(backend, SimulatedEnvironment):
            from backend.app.telemetry.simulator import SimulatorTelemetryBackend

            backend = SimulatorTelemetryBackend(backend)
        self._backend = backend

    @property
    def telemetry_mode(self) -> str:
        return self._backend.mode

    def source_health(self):
        return self._backend.source_health()

    def query_metrics(self, service: str) -> MetricResponse:
        with get_tracer().start_as_current_span("opspilot.tool.query_metrics") as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "query_metrics")
            span.set_attribute("opspilot.telemetry_mode", self.telemetry_mode)
            return self._backend.query_metrics(service)

    def get_service_logs(self, service: str) -> list[LogResponse]:
        with get_tracer().start_as_current_span("opspilot.tool.get_service_logs") as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "get_service_logs")
            span.set_attribute("opspilot.telemetry_mode", self.telemetry_mode)
            return self._backend.get_service_logs(service)

    def get_recent_deployments(self, service: str) -> list[DeploymentResponse]:
        with get_tracer().start_as_current_span(
            "opspilot.tool.get_recent_deployments"
        ) as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "get_recent_deployments")
            span.set_attribute("opspilot.telemetry_mode", self.telemetry_mode)
            return self._backend.get_recent_deployments(service)

    def get_service_health(self, service: str) -> ServiceHealthResponse:
        with get_tracer().start_as_current_span(
            "opspilot.tool.get_service_health"
        ) as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "get_service_health")
            span.set_attribute("opspilot.telemetry_mode", self.telemetry_mode)
            health = self._backend.get_service_health(service)
            span.set_attribute("opspilot.healthy", health.healthy)
            return health
