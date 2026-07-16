import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

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
        "begin_gui_task",
        "observe_desktop",
        "capture_visual_fallback",
        "execute_gui_subgoal",
        "execute_action_batch",
        "execute_visual_transaction",
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


def test_unbound_task_change_atomically_rebuilds_all_semantic_session_state():
    bridge = ChatGPTDesktopBridge(_hooks())
    first = bridge._get_session("Click Continue")
    first_contract = bridge._task_contract
    first_session_id = bridge._session_id
    first.hwnd = 91
    first.target_hint = "Current target"
    closed = []
    first.close = lambda: closed.append(True)
    bridge._frame_sequence = 4
    bridge._authoritative_frame = {"frame_id": 4}
    bridge._authoritative_frame_raw = b"old-frame"
    bridge._last_post_frame_raw = b"old-post-frame"

    task = "Send a message via QQ"
    second = bridge._get_session(task)
    contract = bridge._task_contract

    assert second is bridge._session
    assert second is not first
    assert closed == [True]
    assert bridge._bound_task == ""
    assert bridge._session_id != first_session_id
    assert contract is not None and contract is not first_contract
    assert contract.task == task
    assert second.task == task
    assert second._mcp_task_contract is contract
    assert second.task_intent == contract.intent.model_dump(mode="json")
    assert second.communication_context == {
        "active": True,
        "recipient": "",
        "channel": "QQ",
        "send_mode": "send",
    }
    assert second.runtime_profile.task_contract_id == contract.task_id
    assert second.runtime_profile.stage_type == contract.stages[0].type.value
    # Preserve only live navigation compatibility.  Task-dependent perception
    # and all old frame-coordinate authority are invalidated.
    assert second.hwnd == 91
    assert second.target_hint == "Current target"
    assert second.snapshot is None
    assert bridge._frame_sequence == 0
    assert bridge._authoritative_frame is None
    assert bridge._authoritative_frame_raw is None
    assert bridge._last_post_frame_raw is None


def test_failed_unbound_session_preparation_leaves_previous_snapshot_atomic(
    monkeypatch,
):
    bridge = ChatGPTDesktopBridge(_hooks())
    session = bridge._get_session("Click Continue")
    contract = bridge._task_contract
    session_id = bridge._session_id
    frame = {"frame_id": 3}
    bridge._frame_sequence = 3
    bridge._authoritative_frame = frame
    bridge._authoritative_frame_raw = b"frame"
    bridge._last_post_frame_raw = b"post"

    def fail_prepare(_task):
        raise RuntimeError("synthetic preparation failure")

    monkeypatch.setattr(bridge, "_prepare_session", fail_prepare)
    with pytest.raises(RuntimeError, match="synthetic preparation failure"):
        bridge._get_session("Send a message via QQ")

    assert bridge._session is session
    assert bridge._task_contract is contract
    assert bridge._session_id == session_id
    assert bridge._bound_task == ""
    assert bridge._frame_sequence == 3
    assert bridge._authoritative_frame is frame
    assert bridge._authoritative_frame_raw == b"frame"
    assert bridge._last_post_frame_raw == b"post"


def test_bound_task_remains_contract_authority_for_later_subgoal_labels():
    bridge = ChatGPTDesktopBridge(_hooks())
    task = "Send a message via QQ"
    bound = bridge.bind_task(task)
    session = bridge._session
    contract = bridge._task_contract

    returned = bridge._get_session("Click the visible Send button")

    assert bound["ok"] is True
    assert returned is session
    assert bridge._bound_task == task
    assert returned.task == task
    assert returned._mcp_task_contract is contract
    assert returned.task_intent == contract.intent.model_dump(mode="json")


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
    binding = metadata["authoritative_frame"]
    assert binding["frame_id"] == 1
    assert binding["region"] == {"x": 10, "y": 20, "width": 30, "height": 40}
    assert binding["sha256"]


def test_mcp_frame_coordinates_are_bound_and_mapped_from_exact_capture(monkeypatch):
    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    raw = _jpeg()
    authority = _authority()
    _bind_frame(bridge, raw, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.executor.zoom_region",
        lambda *_args, **_kwargs: {
            "ok": True,
            "base64": base64.b64encode(raw).decode("ascii"),
        },
    )
    args = {
        "source_frame_id": 4,
        "actions": [
            {"action": "drag", "frame_x1": 0, "frame_y1": 0,
             "frame_x2": 1000, "frame_y2": 1000},
            {"action": "wait_for_visual_change",
             "frame_region": {"x": 250, "y": 250, "width": 500, "height": 500}},
        ],
    }

    blocked = bridge._prepare_frame_bound_batch(FakeSession(), args)

    assert blocked is None
    action = args["actions"][0]
    assert (action["x1"], action["y1"], action["x2"], action["y2"]) == (100, 200, 500, 400)
    assert action["source_frame_coordinates"]["frame_id"] == 4
    assert args["actions"][1]["region"] == {"x": 200, "y": 250, "width": 200, "height": 100}


def test_mcp_balanced_pointer_path_is_one_frame_bound_local_gesture(monkeypatch):
    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    raw = _jpeg()
    authority = _authority()
    _bind_frame(bridge, raw, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (raw, None))
    args = {
        "source_frame_id": 4,
        "actions": [
            {"action": "mouse_down", "button": "left", "frame_x": 100, "frame_y": 200},
            {"action": "move_mouse", "frame_x": 500, "frame_y": 500},
            {"action": "mouse_up", "button": "left", "frame_x": 900, "frame_y": 800},
        ],
    }

    blocked = bridge._prepare_frame_bound_batch(FakeSession(), args)

    assert blocked is None
    assert [(item["x"], item["y"]) for item in args["actions"]] == [
        (140, 240), (300, 300), (460, 360),
    ]
    assert all("source_frame_coordinates" in item for item in args["actions"])


def _jpeg(color=(30, 60, 90)):
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _jpeg_with_block(*, block_x: int) -> bytes:
    image = Image.new("RGB", (1000, 500), (245, 245, 245))
    for x in range(block_x, block_x + 18):
        for y in range(241, 259):
            image.putpixel((x, y), (10, 10, 10))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _authority(hwnd=91, *, foreground=91, left=100, top=200):
    return {
        "hwnd": hwnd,
        "foreground_hwnd": foreground,
        "desktop_region": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "window_region": (
            {"x": left, "y": top, "width": 401, "height": 201} if hwnd else None
        ),
        "client_region": (
            {"x": left + 8, "y": top + 30, "width": 385, "height": 163}
            if hwnd else None
        ),
        "valid": True,
    }


def _bind_frame(bridge, raw, *, authority=None):
    authority = authority or _authority()
    bridge._session_id = "mcp-frame-test"
    bridge._authoritative_frame_raw = raw
    bridge._authoritative_frame = {
        "frame_id": 4,
        "bridge_session_id": bridge._session_id,
        "screen_epoch": 7,
        "hwnd": 91,
        "session_target_hwnd": 91,
        "foreground_hwnd": 91,
        "region": {"x": 100, "y": 200, "width": 401, "height": 201},
        "capture_scope": "target_window",
        "authority_kind": "target_window",
        "authority_region": authority["window_region"],
        "authority_snapshot": authority,
        "capture_quality": 70,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def test_mcp_rejects_stale_or_unidentified_frame_coordinates(monkeypatch):
    class FakeSession:
        hwnd = 91
        _screen_epoch = 8

    bridge = ChatGPTDesktopBridge(_hooks())
    monkeypatch.setattr(
        bridge,
        "_replacement_frame_rejection",
        lambda _session, _frame, **kwargs: {
            "ok": False,
            "stale_authoritative_frame": True,
            **kwargs,
        },
    )
    bridge._authoritative_frame = {
        "frame_id": 4,
        "screen_epoch": 7,
        "hwnd": 91,
        "region": {"x": 100, "y": 200, "width": 401, "height": 201},
    }
    missing_id = bridge._prepare_frame_bound_batch(FakeSession(), {
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    })
    stale = bridge._prepare_frame_bound_batch(FakeSession(), {
        "source_frame_id": 4,
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    })

    assert missing_id["needs_visual_capture"] is True
    assert stale["stale_authoritative_frame"] is True


def test_explicit_roi_must_stay_inside_bound_window_authority(monkeypatch):
    class FakeSession:
        hwnd = 91
        task = "interact with the current target"
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    authority = _authority()
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    captures = []
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.executor.zoom_region",
        lambda *args, **kwargs: captures.append((args, kwargs)) or {"ok": False},
    )

    metadata, raw = bridge._capture_authoritative_locked(
        FakeSession(), x=20, y=20, width=30, height=30, quality=70,
    )

    assert raw is None
    assert metadata["blocked"] is True
    assert "outside" in metadata["error"].lower()
    assert metadata["target_hwnd"] == 91
    assert captures == []


def test_explicit_roi_inside_client_binds_real_target_authority(monkeypatch):
    class FakeSession:
        hwnd = 91
        task = "interact with the current target"
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    bridge._session_id = "mcp-frame-test"
    authority = _authority()
    raw = _jpeg()
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.executor.zoom_region",
        lambda x, y, width, height, quality: {
            "ok": True,
            "base64": base64.b64encode(raw).decode("ascii"),
            "region": {"x": x, "y": y, "width": width, "height": height},
        },
    )

    metadata, image = bridge._capture_authoritative_locked(
        FakeSession(), x=120, y=240, width=40, height=30, quality=70,
    )

    assert image == raw
    binding = metadata["authoritative_frame"]
    assert binding["hwnd"] == 91
    assert binding["session_target_hwnd"] == 91
    assert binding["authority_kind"] == "window_client"
    assert binding["capture_scope"] == "explicit_target_client_roi"
    assert binding["authority_region"] == authority["client_region"]


def test_full_desktop_fallback_never_carries_unverified_session_hwnd(monkeypatch):
    class FakeSession:
        hwnd = 91
        task = "inspect multiple windows"
        _screen_epoch = 7
        _window_related_to_target = None

    bridge = ChatGPTDesktopBridge(_hooks())
    bridge._session_id = "mcp-frame-test"
    raw = _jpeg()
    desktop = _authority(hwnd=0, foreground=333)
    region = SimpleNamespace(left=0, top=0, width=1920, height=1080)
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.select_visual_capture_scope",
        lambda *_args, **_kwargs: SimpleNamespace(
            region=region,
            scope="full_desktop",
            target_hwnd=91,
            target_verified=False,
        ),
    )
    monkeypatch.setattr(bridge, "_live_authority_state", lambda hwnd: desktop if hwnd == 0 else _authority())
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.executor.zoom_region",
        lambda x, y, width, height, quality: {
            "ok": True,
            "base64": base64.b64encode(raw).decode("ascii"),
            "region": {"x": x, "y": y, "width": width, "height": height},
        },
    )

    metadata, image = bridge._capture_authoritative_locked(
        FakeSession(), x=None, y=None, width=None, height=None, quality=70,
    )

    assert image == raw
    binding = metadata["authoritative_frame"]
    assert binding["capture_scope"] == "full_desktop"
    assert binding["hwnd"] == 0
    assert binding["session_target_hwnd"] == 91
    assert binding["authority_kind"] == "virtual_desktop"


def test_full_desktop_recovery_frame_cannot_ground_bound_target_gesture(monkeypatch):
    """Scope recovery must not refocus after desktop coordinates were mapped."""

    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    bridge._session_id = "mcp-desktop-recovery-test"
    raw = _jpeg()
    authority = _authority(hwnd=0, foreground=333)
    frame = {
        "frame_id": 4,
        "bridge_session_id": bridge._session_id,
        "screen_epoch": 7,
        "hwnd": 0,
        "session_target_hwnd": 91,
        "foreground_hwnd": 333,
        "region": dict(authority["desktop_region"]),
        "capture_scope": "full_desktop",
        "authority_kind": "virtual_desktop",
        "authority_region": dict(authority["desktop_region"]),
        "authority_snapshot": authority,
        "capture_quality": 70,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    bridge._authoritative_frame = frame
    bridge._authoritative_frame_raw = raw
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (raw, None))
    args = {
        "source_frame_id": 4,
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    }

    result = bridge._prepare_frame_bound_batch(FakeSession(), args)

    assert result["blocked"] is True
    assert result["needs_target_frame"] is True
    assert result["fresh_target_frame_required"] is True
    assert result["session_target_hwnd"] == 91
    assert "frame_x" in args["actions"][0]
    assert "x" not in args["actions"][0]


def test_raw_frame_gesture_rejects_every_visual_invalidating_prefix(monkeypatch):
    from winpilot.fast.action_contract import BATCH_MUTATING_ACTIONS

    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    raw = _jpeg()
    authority = _authority()
    _bind_frame(bridge, raw, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (raw, None))
    invalidating = set(BATCH_MUTATING_ACTIONS) | {
        "wait", "wait_change", "wait_for_visual_change", "wait_stable",
    }

    for prefix in sorted(invalidating):
        args = {
            "source_frame_id": 4,
            "actions": [
                {"action": prefix},
                {"action": "click", "frame_x": 500, "frame_y": 500},
            ],
        }
        result = bridge._prepare_frame_bound_batch(FakeSession(), args)
        assert result["blocked"] is True, prefix
        assert result["fresh_target_frame_required"] is True, prefix
        assert result["invalidating_prefix_actions"] == [
            {"index": 0, "action": prefix},
        ]
        assert "frame_x" in args["actions"][1]
        assert "x" not in args["actions"][1]


def test_read_only_prefix_may_share_frame_with_later_raw_gesture(monkeypatch):
    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    raw = _jpeg()
    authority = _authority()
    _bind_frame(bridge, raw, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (raw, None))
    args = {
        "source_frame_id": 4,
        "actions": [
            {"action": "read_text"},
            {"action": "click", "frame_x": 500, "frame_y": 500},
        ],
    }

    result = bridge._prepare_frame_bound_batch(FakeSession(), args)

    assert result is None
    assert args["actions"][1]["x"] == 300
    assert args["actions"][1]["y"] == 300


@pytest.mark.parametrize(
    ("live_authority", "expected_reason"),
    [
        (_authority(left=140), "window_geometry_changed"),
        (_authority(foreground=777), "foreground_changed"),
    ],
)
def test_window_move_or_foreground_change_rejects_and_returns_replacement_frame(
    monkeypatch, live_authority, expected_reason,
):
    class FakeSession:
        hwnd = 91
        task = "interact with target"
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    raw = _jpeg()
    _bind_frame(bridge, raw)
    replacement = {"frame_id": 5, "hwnd": 0, "capture_scope": "full_desktop"}
    replacement_raw = _jpeg((90, 20, 10))
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: live_authority)
    monkeypatch.setattr(
        bridge,
        "_capture_authoritative_locked",
        lambda *_args, **_kwargs: (
            {"ok": True, "authoritative_frame": replacement}, replacement_raw,
        ),
    )

    result = bridge._prepare_frame_bound_batch(FakeSession(), {
        "source_frame_id": 4,
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    })

    assert result["blocked"] is True
    assert result["stale_reason"] == expected_reason
    assert result["replacement_frame"] == replacement
    assert result["replacement_frame_available"] is True
    assert bridge.take_last_post_frame() == replacement_raw


def test_frame_preflight_uses_action_roi_and_not_exact_jpeg_sha(monkeypatch):
    class FakeSession:
        hwnd = 91
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    captured = _jpeg((10, 20, 30))
    recaptured = _jpeg((200, 210, 220))
    authority = _authority()
    _bind_frame(bridge, captured, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (recaptured, None))
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.image_capture.jpeg_diff_ratio",
        lambda *_args, **_kwargs: 0.25,
    )
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.image_capture.jpeg_diff_ratios_in_normalized_rois",
        lambda _before, _after, rois, **_kwargs: [0.0] * len(rois),
    )
    args = {
        "source_frame_id": 4,
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    }

    result = bridge._prepare_frame_bound_batch(FakeSession(), args)

    assert result is None
    assert args["actions"][0]["x"] == 300
    assert args["actions"][0]["y"] == 300


def test_long_drag_preflight_does_not_dilute_small_endpoint_motion(monkeypatch):
    """A tiny moved source blocks a long stale drag despite low global delta."""
    class FakeSession:
        hwnd = 91
        task = "manipulate the visible target"
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    captured = _jpeg_with_block(block_x=41)
    recaptured = _jpeg_with_block(block_x=941)
    authority = _authority()
    _bind_frame(bridge, captured, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (recaptured, None))
    replacement = {"frame_id": 5, "hwnd": 91, "capture_scope": "target_window"}
    monkeypatch.setattr(
        bridge,
        "_capture_authoritative_locked",
        lambda *_args, **_kwargs: (
            {"ok": True, "authoritative_frame": replacement}, recaptured,
        ),
    )

    result = bridge._prepare_frame_bound_batch(FakeSession(), {
        "source_frame_id": 4,
        "actions": [{
            "action": "drag",
            "frame_x1": 50,
            "frame_y1": 500,
            "frame_x2": 950,
            "frame_y2": 500,
        }],
    })

    assert result["stale_reason"] == "visual_preflight_changed"
    assert result["global_change_ratio"] < 0.0025
    assert result["target_change_ratio"] > 0.01
    assert len(result["target_rois"]) == 2
    assert result["replacement_frame"] == replacement


def test_frame_preflight_blocks_action_target_change(monkeypatch):
    class FakeSession:
        hwnd = 91
        task = "interact with target"
        _screen_epoch = 7

    bridge = ChatGPTDesktopBridge(_hooks())
    captured = _jpeg((10, 20, 30))
    recaptured = _jpeg((200, 210, 220))
    authority = _authority()
    _bind_frame(bridge, captured, authority=authority)
    monkeypatch.setattr(bridge, "_live_authority_state", lambda _hwnd: authority)
    monkeypatch.setattr(bridge, "_capture_frame_region", lambda _frame: (recaptured, None))
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.image_capture.jpeg_diff_ratio",
        lambda *_args, **_kwargs: 0.001,
    )
    monkeypatch.setattr(
        "winpilot.integrations.chatgpt_mcp.image_capture.jpeg_diff_ratios_in_normalized_rois",
        lambda _before, _after, rois, **_kwargs: [0.2] * len(rois),
    )
    replacement = {"frame_id": 5, "hwnd": 91, "capture_scope": "target_window"}
    monkeypatch.setattr(
        bridge,
        "_capture_authoritative_locked",
        lambda *_args, **_kwargs: (
            {"ok": True, "authoritative_frame": replacement}, recaptured,
        ),
    )

    result = bridge._prepare_frame_bound_batch(FakeSession(), {
        "source_frame_id": 4,
        "actions": [{"action": "click", "frame_x": 500, "frame_y": 500}],
    })

    assert result["stale_reason"] == "visual_preflight_changed"
    assert result["decision_scope"] == "action_target_roi"
    assert result["target_change_ratio"] == 0.2
    assert result["replacement_frame"] == replacement


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
