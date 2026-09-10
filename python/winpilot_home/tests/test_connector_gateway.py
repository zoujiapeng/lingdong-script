from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from winpilot.integrations.connector_gateway import (
    MAX_ARGUMENT_BYTES,
    MAX_RESULT_BYTES,
    ConnectorGateway,
)


def _tool(
    name: str = "get_record",
    *,
    description: str = "Read one record.",
    annotations: dict | None = None,
) -> dict:
    return {
        "server": "records",
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "annotations": dict(annotations or {}),
    }


@pytest.fixture(autouse=True)
def _reset_mcp_sessions():
    from winpilot.integrations import mcp_client

    mcp_client.close_sessions()
    mcp_client._tool_cache.clear()
    yield
    mcp_client.close_sessions()
    mcp_client._tool_cache.clear()


def test_gateway_stays_available_for_hot_configuration(monkeypatch) -> None:
    from winpilot.integrations import connector_gateway

    monkeypatch.setattr(
        connector_gateway.mcp_client,
        "discover",
        lambda **_kwargs: {
            "ok": True,
            "configured_servers": [],
            "servers": [],
            "tools": [],
            "failures": [],
        },
    )
    gateway = ConnectorGateway()
    assert gateway.available is True
    assert gateway.search()["configured_servers"] == []


def test_mcp_config_values_resolve_environment_references(monkeypatch) -> None:
    from winpilot.integrations import mcp_client

    monkeypatch.setenv("CONNECTOR_TEST_TOKEN", "secret-value")
    assert mcp_client._config_value("${CONNECTOR_TEST_TOKEN}") == "secret-value"
    assert mcp_client._config_value("${env:CONNECTOR_TEST_TOKEN}") == "secret-value"
    assert mcp_client._config_value(
        "Bearer ${env:CONNECTOR_TEST_TOKEN}"
    ) == "Bearer secret-value"
    with pytest.raises(ValueError, match="MISSING_CONNECTOR_TOKEN"):
        mcp_client._config_value("${env:MISSING_CONNECTOR_TOKEN}")


def test_explicit_mcp_config_has_highest_server_name_precedence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from winpilot.integrations import mcp_client

    workspace = tmp_path / "workspace"
    user = tmp_path / "user"
    workspace.mkdir()
    user.mkdir()
    explicit = tmp_path / "explicit.json"
    (workspace / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"same": {"url": "https://workspace.invalid"}}}),
        encoding="utf-8",
    )
    (user / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"same": {"url": "https://user.invalid"}}}),
        encoding="utf-8",
    )
    explicit.write_text(
        json.dumps({"mcpServers": {"same": {"url": "https://explicit.invalid"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("WINPILOT_MCP_CONFIG", str(explicit))
    monkeypatch.setattr(mcp_client.CONFIG, "get", lambda *_args, default=None: default)
    assert mcp_client.configured_servers()["same"]["url"] == "https://explicit.invalid"


def test_search_uses_or_matching_and_ranks_exact_tool_name(monkeypatch) -> None:
    from winpilot.integrations import mcp_client

    tools = [
        _tool("chart_add", description="Create a chart"),
        _tool("write_cells", description="Write spreadsheet values"),
        _tool("formula_fill", description="Fill Excel formulas"),
    ]
    monkeypatch.setattr(
        mcp_client,
        "configured_servers",
        lambda: {"records": {"url": "https://records.invalid"}},
    )

    async def listed(_name: str, _server: dict):
        return tools, "Use write_cells before formula_fill."

    monkeypatch.setattr(mcp_client, "_list_server_tools", listed)
    found = mcp_client.discover("write missing-token")
    assert [item["name"] for item in found["tools"]] == ["write_cells"]
    exact = mcp_client.discover("formula_fill")
    assert exact["tools"][0]["name"] == "formula_fill"
    assert exact["servers"][0]["instructions"].startswith("Use write_cells")


def test_mcp_tool_cache_is_scoped_to_exact_server_configuration(monkeypatch) -> None:
    from winpilot.integrations import mcp_client

    server = {"url": "https://first.invalid", "_config_path": "first.json"}
    calls: list[str] = []

    async def list_tools(name: str, configured: dict):
        calls.append(str(configured["url"]))
        return [_tool("read", description=str(configured["url"]))], ""

    monkeypatch.setattr(mcp_client, "configured_servers", lambda: {"same": dict(server)})
    monkeypatch.setattr(mcp_client, "_list_server_tools", list_tools)
    assert mcp_client.discover(server_name="same")["tools"][0]["description"] == "https://first.invalid"
    assert mcp_client.discover(server_name="same")["tools"][0]["description"] == "https://first.invalid"
    server.update(url="https://second.invalid", _config_path="second.json")
    assert mcp_client.discover(server_name="same")["tools"][0]["description"] == "https://second.invalid"
    assert calls == ["https://first.invalid", "https://second.invalid"]


@pytest.mark.parametrize("cancel_first", [False, True])
def test_mcp_coroutine_obeys_timeout_or_cancel(cancel_first: bool) -> None:
    from winpilot.integrations import mcp_client

    stop = threading.Event()
    if cancel_first:
        stop.set()

    async def pending() -> None:
        await asyncio.sleep(5)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        mcp_client._run(pending(), timeout_s=0.02, stop_event=stop)
    assert time.monotonic() - started < 0.5


def test_direct_gateway_call_does_not_rediscover(monkeypatch) -> None:
    from winpilot.integrations import connector_gateway

    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        connector_gateway.mcp_client,
        "discover",
        lambda **_kwargs: {
            "ok": True,
            "configured_servers": ["records"],
            "servers": [{"name": "records", "tool_count": 1}],
            "tools": [_tool(annotations={"destructiveHint": True})],
            "failures": [],
        },
    )
    monkeypatch.setattr(
        connector_gateway.mcp_client,
        "call",
        lambda server, tool, arguments, **_kwargs: (
            calls.append((server, tool, arguments))
            or {"ok": True, "content": [{"type": "text", "text": "value"}]}
        ),
    )
    gateway = ConnectorGateway()
    catalog = gateway.search("record")
    assert catalog["tools"][0]["connector"] == "records"
    assert "ref" not in catalog["tools"][0]
    result = gateway.call("records", "get_record", '{"id":"7"}')
    assert result["ok"] is True
    assert calls == [("records", "get_record", {"id": "7"})]


@pytest.mark.parametrize(
    "arguments_json",
    [
        "not json",
        "[]",
        '{"id":"first","id":"second"}',
        '{"value":NaN}',
        '{"value":"' + "x" * MAX_ARGUMENT_BYTES + '"}',
    ],
    ids=["not-json", "not-object", "duplicate-key", "nan", "too-large"],
)
def test_invalid_arguments_do_not_reach_mcp(monkeypatch, arguments_json: str) -> None:
    from winpilot.integrations import connector_gateway

    monkeypatch.setattr(
        connector_gateway.mcp_client,
        "call",
        lambda *_args, **_kwargs: pytest.fail("invalid arguments reached MCP"),
    )
    result = ConnectorGateway().call("records", "get_record", arguments_json)
    assert result["ok"] is False


def test_large_remote_result_is_bounded(monkeypatch) -> None:
    from winpilot.integrations import connector_gateway

    monkeypatch.setattr(
        connector_gateway.mcp_client,
        "call",
        lambda *_args, **_kwargs: {
            "ok": True,
            "content": "x" * (MAX_RESULT_BYTES * 2),
        },
    )
    result = ConnectorGateway().call("records", "get_record", '{"id":"7"}')
    assert result["truncated"] is True
    assert result["original_bytes"] > MAX_RESULT_BYTES
    assert len(result["preview"].encode("utf-8")) <= 8 * 1024


def test_stdio_session_is_reused_and_tool_pages_are_followed(monkeypatch) -> None:
    import mcp
    from winpilot.integrations import mcp_client

    entered = 0
    initialized = 0
    list_cursors: list[str | None] = []
    calls: list[str] = []

    class Tool:
        def __init__(self, name: str) -> None:
            self.name = name

        def model_dump(self, **_kwargs) -> dict:
            return {
                "name": self.name,
                "description": self.name,
                "inputSchema": {"type": "object"},
            }

    @asynccontextmanager
    async def connection(_server):
        nonlocal entered
        entered += 1
        yield object(), object()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            nonlocal initialized
            initialized += 1
            return SimpleNamespace(instructions="Office workflow")

        async def list_tools(self, cursor=None):
            list_cursors.append(cursor)
            if cursor is None:
                return SimpleNamespace(tools=[Tool("write_cells")], nextCursor="p2")
            return SimpleNamespace(tools=[Tool("create_chart")], nextCursor=None)

        async def call_tool(self, name, *, arguments):
            calls.append(name)
            return SimpleNamespace(
                content=[],
                isError=False,
                structuredContent={"arguments": arguments},
            )

    monkeypatch.setattr(mcp_client, "_connection", connection)
    monkeypatch.setattr(mcp, "ClientSession", lambda *_args: Session())
    monkeypatch.setattr(
        mcp_client,
        "configured_servers",
        lambda: {"office": {"command": "fake"}},
    )
    discovered = mcp_client.discover()
    assert [tool["name"] for tool in discovered["tools"]] == [
        "create_chart",
        "write_cells",
    ]
    assert discovered["servers"][0]["instructions"] == "Office workflow"
    assert mcp_client.call("office", "write_cells", {"value": 1})["ok"] is True
    assert mcp_client.call("office", "create_chart", {})["ok"] is True
    assert entered == initialized == 1
    assert list_cursors == [None, "p2"]
    assert calls == ["write_cells", "create_chart"]
