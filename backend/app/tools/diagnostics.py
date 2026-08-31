from simulator.environment import SimulatedEnvironment

from backend.app.observability.tracing import get_tracer
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse


class DiagnosticTools:
    """Read-only diagnostic access to a simulated production environment."""

    def __init__(self, environment: SimulatedEnvironment) -> None:
        self._environment = environment

    def query_metrics(self, service: str) -> MetricResponse:
        with get_tracer().start_as_current_span("opspilot.tool.query_metrics") as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "query_metrics")
            snapshot = self._environment.query_metrics(service)
            return MetricResponse.model_validate(snapshot, from_attributes=True)

    def get_service_logs(self, service: str) -> list[LogResponse]:
        with get_tracer().start_as_current_span("opspilot.tool.get_service_logs") as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "get_service_logs")
            events = self._environment.get_logs(service)
            return [
                LogResponse.model_validate(event, from_attributes=True)
                for event in events
            ]

    def get_recent_deployments(self, service: str) -> list[DeploymentResponse]:
        with get_tracer().start_as_current_span(
            "opspilot.tool.get_recent_deployments"
        ) as span:
            span.set_attribute("opspilot.service", service)
            span.set_attribute("opspilot.tool", "get_recent_deployments")
            events = self._environment.get_recent_deployments(service)
            return [
                DeploymentResponse.model_validate(event, from_attributes=True)
                for event in events
            ]
