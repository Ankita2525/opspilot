from collections.abc import Callable

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backend.app.tools.diagnostics import DiagnosticTools
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse


def create_mcp_server(tools: DiagnosticTools) -> MCPServer:
    """Build a read-only OpsPilot MCP server over DiagnosticTools."""
    server = MCPServer("OpsPilot")

    @server.tool(
        description=(
            "Retrieve current service health metrics including p95 latency and error rate."
        )
    )
    def query_metrics(service: str) -> MetricResponse:
        return _call_diagnostic(tools.query_metrics, service)

    @server.tool(description="Retrieve recent application logs for a service.")
    def get_service_logs(service: str) -> list[LogResponse]:
        return _call_diagnostic(tools.get_service_logs, service)

    @server.tool(description="Retrieve recent deployment history for a service.")
    def get_recent_deployments(service: str) -> list[DeploymentResponse]:
        return _call_diagnostic(tools.get_recent_deployments, service)

    return server


def _call_diagnostic[T](method: Callable[[str], T], service: str) -> T:
    try:
        return method(service)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
