from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import Client
from mcp.server import MCPServer
from mcp_types import CallToolResult

from backend.app.mcp_server.server import create_mcp_server
from backend.app.tools.diagnostics import DiagnosticTools
from simulator.environment import SimulatedEnvironment

SCENARIO_ID = "checkout-db-pool-regression"
SERVICE = "checkout-api"
BAD_VERSION = "v1.18.3"
EXPECTED_TOOLS = [
    "query_metrics",
    "get_service_logs",
    "get_recent_deployments",
]
WRITE_TOOLS = [
    "rollback_deployment",
    "restart_service",
    "modify_configuration",
]


def _loaded_tools() -> tuple[SimulatedEnvironment, DiagnosticTools]:
    environment = SimulatedEnvironment()
    environment.load_scenario(SCENARIO_ID)
    return environment, DiagnosticTools(environment)


def _run(
    callback: Callable[[Client, SimulatedEnvironment], Awaitable[Any]],
) -> Any:
    async def _inner() -> Any:
        environment, tools = _loaded_tools()
        server = create_mcp_server(tools)
        async with Client(server) as client:
            return await callback(client, environment)

    return asyncio.run(_inner())


def _structured(result: CallToolResult) -> Any:
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content


def _list_payload(result: CallToolResult) -> list[dict[str, Any]]:
    payload = _structured(result)
    items = payload["result"] if isinstance(payload, dict) and "result" in payload else payload
    assert isinstance(items, list)
    return items


def test_mcp_server_can_be_created() -> None:
    _, tools = _loaded_tools()

    server = create_mcp_server(tools)

    assert isinstance(server, MCPServer)
    assert server.name == "OpsPilot"


def test_in_memory_client_connects() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        listed = await client.list_tools()
        assert listed.tools

    _run(_check)


def test_list_tools_exposes_exactly_the_read_only_tools() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        assert names == EXPECTED_TOOLS

    _run(_check)


def test_no_remediation_or_write_tools_are_exposed() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        for write_tool in WRITE_TOOLS:
            assert write_tool not in names

    _run(_check)


def test_query_metrics_returns_checkout_incident_metrics() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("query_metrics", {"service": SERVICE})
        metrics = _structured(result)
        assert metrics["service"] == SERVICE
        assert metrics["p95_latency_ms"] == 1940
        assert metrics["error_rate_percent"] == 8.2

    _run(_check)


def test_get_service_logs_returns_log_data() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("get_service_logs", {"service": SERVICE})
        logs = _list_payload(result)
        assert logs
        assert all(item["service"] == SERVICE for item in logs)

    _run(_check)


def test_logs_contain_database_connection_pool_timeout() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("get_service_logs", {"service": SERVICE})
        logs = _list_payload(result)
        combined = " ".join(item["message"].lower() for item in logs)
        assert "database connection pool timeout" in combined

    _run(_check)


def test_get_recent_deployments_returns_deployment_data() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("get_recent_deployments", {"service": SERVICE})
        deployments = _list_payload(result)
        assert deployments
        assert all(item["service"] == SERVICE for item in deployments)

    _run(_check)


def test_deployments_contain_v1_18_3() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("get_recent_deployments", {"service": SERVICE})
        versions = [item["version"] for item in _list_payload(result)]
        assert BAD_VERSION in versions

    _run(_check)


def test_mcp_calls_do_not_resolve_incident() -> None:
    async def _check(client: Client, environment: SimulatedEnvironment) -> None:
        await client.call_tool("query_metrics", {"service": SERVICE})
        await client.call_tool("get_service_logs", {"service": SERVICE})
        await client.call_tool("get_recent_deployments", {"service": SERVICE})
        assert environment.is_resolved is False

    _run(_check)


def test_environment_unchanged_after_mcp_reads() -> None:
    async def _check(client: Client, environment: SimulatedEnvironment) -> None:
        before_metrics = environment.query_metrics(SERVICE)
        before_logs = environment.get_logs(SERVICE)
        before_deployments = environment.get_recent_deployments(SERVICE)

        await client.call_tool("query_metrics", {"service": SERVICE})
        await client.call_tool("get_service_logs", {"service": SERVICE})
        await client.call_tool("get_recent_deployments", {"service": SERVICE})

        assert environment.is_resolved is False
        assert environment.get_audit_events() == []
        assert environment.query_metrics(SERVICE) == before_metrics
        assert environment.get_logs(SERVICE) == before_logs
        assert environment.get_recent_deployments(SERVICE) == before_deployments
        assert before_metrics.p95_latency_ms == 1940
        assert before_metrics.error_rate_percent == 8.2

    _run(_check)


def test_simulator_ground_truth_is_not_exposed() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        metrics = await client.call_tool("query_metrics", {"service": SERVICE})
        logs = await client.call_tool("get_service_logs", {"service": SERVICE})
        deployments = await client.call_tool("get_recent_deployments", {"service": SERVICE})
        listed = await client.list_tools()
        payload = json.dumps(
            {
                "tools": [tool.model_dump(mode="json") for tool in listed.tools],
                "metrics": _structured(metrics),
                "logs": _structured(logs),
                "deployments": _structured(deployments),
            }
        )
        assert "known_root_cause" not in payload
        assert "expected_remediation" not in payload
        assert "db_connection_pool_regression" not in payload
        assert "rollback_deployment" not in payload

    _run(_check)


def test_unknown_service_failure_is_surfaced() -> None:
    async def _check(client: Client, _environment: SimulatedEnvironment) -> None:
        result = await client.call_tool("query_metrics", {"service": "inventory-api"})
        assert result.is_error is True
        messages = " ".join(
            block.text for block in result.content if getattr(block, "text", None)
        )
        assert "Unknown service" in messages

    _run(_check)
