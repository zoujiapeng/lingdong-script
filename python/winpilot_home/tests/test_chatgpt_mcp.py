import asyncio
import base64
import json
from pathlib import Path
import subprocess
import sys

from winpilot.agent.tool_result import ToolResult
from winpilot.integrations.chatgpt_mcp import (
    ChatGPTBridgeHooks,
    ChatGPTDesktopBridge,
    build_chatgpt_mcp,
)
from winpilot.orchestrator.projection import RUN_PROJECTIONS


def test_runtime_python_cwd_does_not_shadow_official_mcp_sdk():
    python_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mcp; from mcp.server.fastmcp import FastMCP; print(mcp.__file__)",
        ],
        cwd=python_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert str(python_root / "mcp").lower() not in result.stdout.lower()


def _hooks(**overrides):
    values = {
        "status": lambda: {"ok": True, "runtime": {"running": False}},
        "start_graph": lambda graph, inputs: {"ok": True, "graph_id": graph.id, "inputs": inputs},
        "get_run": lambda run_id: {"ok": True, "run_id": run_id},
        "cancel_run": lambda run_id: {"ok": True, "run_id": run_id},
        "decide_gate": lambda run_id, node_id, approved, comment: {
            "ok": True, "run_id": run_id, "node_id": node_id, "approved": approved,
        },
        "busy": lambda: False,
        "enabled": lambda: True,
        "mutation_allowed": lambda: True,
    }
    values.update(overrides)
    return ChatGPTBridgeHooks(**values)


def _graph(node):
    return {
        "format": "arp/1",
        "id": "mcp-graph",
        "name": "MCP graph",
        "nodes": [
            {"id": "start", "kind": "start"},
            node,
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": node["id"]},
            {"id": "e2", "source": node["id"], "target": "end"},
        ],
    }


def test_mcp_catalog_exposes_controller_first_tools_and_annotations():
    mcp, _app, _bridge = build_chatgpt_mcp(_hooks())
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert {
        "get_gui_agent_state",
        "observe_desktop",
        "capture_visual_fallback",
        "execute_gui_subgoal",
        "execute_action_batch",
        "run_arp_graph",
    } <= set(by_name)
    assert by_name["observe_desktop"].annotations.readOnlyHint is True
    assert by_name["execute_gui_subgoal"].annotations.destructiveHint is True
    assert "subgoal" in by_name["execute_gui_subgoal"].inputSchema["required"]


def test_chatgpt_graph_rejects_nested_api_agent_but_accepts_controller_batch():
    bridge = ChatGPTDesktopBridge(_hooks())
    api_graph = _graph({"id": "goal", "kind": "agent_goal", "config": {"goal": "click it"}})
    batch_graph = _graph({
        "id": "batch",
        "kind": "action_batch",
        "config": {"actions": [{"action": "read_text"}]},
    })

    rejected = bridge.validate_graph(api_graph)
    accepted = bridge.validate_graph(batch_graph)

    assert rejected["valid"] is False
    assert "already the planner" in rejected["errors"][-1]
    assert accepted["valid"] is True


def test_mutation_guard_blocks_controller_calls_while_runtime_is_busy():
    bridge = ChatGPTDesktopBridge(_hooks(busy=lambda: True))

    result = bridge.dispatch(
        "run_action_batch",
        {"actions": [{"action": "click_element", "label": "Export"}]},
        mutating=True,
    )

    assert result["ok"] is False
    assert result["retryable"] is True


def test_visual_fallback_returns_raw_image_bytes_without_leaking_base64(monkeypatch):
    raw = b"fake-jpeg-bytes"
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.executor.zoom_region",
        lambda x, y, width, height, quality: {
            "ok": True,
            "base64": base64.b64encode(raw).decode("ascii"),
            "width": width,
            "height": height,
            "region": {"x": x, "y": y, "width": width, "height": height},
        },
    )
    bridge = ChatGPTDesktopBridge(_hooks())

    metadata, image = bridge.visual_capture(x=10, y=20, width=30, height=40, quality=70)

    assert metadata["ok"] is True
    assert "base64" not in metadata
    assert image == raw


def test_controller_result_is_compact_before_returning_to_chatgpt():
    class FakeSession:
        task = ""

        def dispatch(self, _name, _args):
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "controller_state": {"phase": "verified", "verified": True},
                "results": [{
                    "index": 1,
                    "action": "click_element",
                    "ok": True,
                    "data": {
                        "ok": True,
                        "verified": True,
                        "resolved_element": {"elem_id": 7, "text": "Export", "role": "button"},
                        "anchor_locator": {
                            "found": True,
                            "score": 1.2,
                            "candidates": [{"text": "noise"}] * 500,
                        },
                    },
                }],
            }))

    bridge = ChatGPTDesktopBridge(_hooks())
    fake_session = FakeSession()
    bridge._session = fake_session
    bridge._session_id = "mcp-test"

    result = bridge.dispatch("execute_subgoal", {"subgoal": "Export"}, mutating=True)

    assert result["ok"] is True
    assert result["controller_state"]["verified"] is True
    assert "candidates" not in result["results"][0]["detail"]["anchor_locator"]
    assert len(json.dumps(result)) < 2000
    assert fake_session.run_id is None
    projection = RUN_PROJECTIONS.get(result["run_id"])
    assert projection["status"] == "done"
    assert projection["finished_at"] is not None
