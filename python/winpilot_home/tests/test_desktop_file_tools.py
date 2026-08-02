import json
import sys

import numpy as np
from PIL import Image
import pytest


def test_screenshot_saves_target_window_without_shell(monkeypatch, tmp_path):
    from winpilot.agent import desktop_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    image_path = tmp_path / "calculator.png"
    frame = np.full((24, 40, 3), (10, 20, 30), dtype=np.uint8)
    monkeypatch.setattr(desktop_tools, "capture", lambda region: frame)
    monkeypatch.setattr(desktop_tools, "window_region", lambda hwnd: Region(8, 9, 40, 24))

    session = ToolSession(task="save calculator screenshot")
    session.hwnd = 123
    result = session._t_screenshot({"path": str(image_path), "window": True})
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["scope"] == "window"
    assert payload["region"] == {"left": 8, "top": 9, "width": 40, "height": 24}
    assert image_path.is_file()
    assert Image.open(image_path).size == (40, 24)


def test_flow_managed_screenshot_uses_run_artifact_store(monkeypatch, tmp_path):
    from winpilot.agent.tools import ToolSession
    from winpilot.tasking import artifacts
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    frame = np.full((18, 32, 3), (7, 8, 9), dtype=np.uint8)
    monkeypatch.setattr(artifacts, "capture", lambda _region: frame)
    store = ArtifactStore("flow-shot", tmp_path / "artifacts")
    registry = EvidenceRegistry("flow-shot")
    session = ToolSession(task="capture flow screenshot")
    session.run_id = "flow-shot"
    session.managed_artifact_store = store
    session.evidence_registry = registry
    try:
        result = session._t_screenshot({
            "managed_artifact": True,
            "scope": "screen",
            "purpose": "user_output",
            "sendable": True,
            "quality": 71,
        })
    finally:
        session.close()
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["artifact_id"].startswith("art-")
    assert payload["path"] == payload["managed_path"]
    assert payload["width"] == 32
    assert payload["height"] == 18
    assert len(payload["sha256"]) == 64
    assert store.snapshot()["artifacts"][0]["artifact_id"] == payload["artifact_id"]
    assert registry.snapshot()["records"][0]["artifact_id"] == payload["artifact_id"]


def test_flow_managed_region_screenshot_preserves_physical_coordinates(monkeypatch, tmp_path):
    from winpilot.agent.tools import ToolSession
    from winpilot.tasking import artifacts
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    requested = Region(-1500, 120, 320, 180)
    captured = []
    monkeypatch.setattr(
        artifacts,
        "monitor_regions",
        lambda: (Region(-1920, 0, 1920, 1080), Region(0, 0, 1920, 1080)),
    )
    monkeypatch.setattr(
        artifacts,
        "capture",
        lambda region: captured.append(region) or np.full((180, 320, 3), (17, 34, 51), dtype=np.uint8),
    )
    store = ArtifactStore("flow-region-shot", tmp_path / "artifacts")
    registry = EvidenceRegistry("flow-region-shot")
    session = ToolSession(task="capture selected screen region")
    session.run_id = "flow-region-shot"
    session.managed_artifact_store = store
    session.evidence_registry = registry
    try:
        result = session._t_screenshot({
            "managed_artifact": True,
            "scope": "region",
            "region": {"x": requested.left, "y": requested.top, "width": requested.width, "height": requested.height},
            "purpose": "user_output",
            "sendable": True,
        })
    finally:
        session.close()
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert captured == [requested]
    assert payload["scope"] == "region"
    assert payload["region"] == {"left": -1500, "top": 120, "width": 320, "height": 180}
    assert (payload["width"], payload["height"]) == (320, 180)
    assert store.snapshot()["artifacts"][0]["metadata"]["region"] == payload["region"]


def test_explicit_artifact_regions_fail_closed_before_capture(monkeypatch, tmp_path):
    from winpilot.tasking import artifacts
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        artifacts,
        "monitor_regions",
        lambda: (Region(-1920, 0, 1920, 1080), Region(100, 0, 1820, 1080)),
    )
    monkeypatch.setattr(
        artifacts,
        "capture",
        lambda _region: (_ for _ in ()).throw(AssertionError("invalid region reached physical capture")),
    )
    store = ArtifactStore("invalid-region-shot", tmp_path / "artifacts")
    invalid = [
        {"x": -10, "y": 10, "width": 120, "height": 80},  # display gap/crossing
        {"x": -1930, "y": 10, "width": 20, "height": 20},
        {"x": 100, "y": 10, "width": 0, "height": 20},
        {"x": 100, "y": 10, "width": True, "height": 20},
        {"x": 100.5, "y": 10, "width": 20, "height": 20},
        {"x": 100, "y": 10, "width": 20},
    ]
    for region in invalid:
        with pytest.raises(ValueError):
            store.capture_artifact(scope="region", region=region)

    with pytest.raises(ValueError):
        store.capture_artifact(scope="screen", region={"x": 100, "y": 10, "width": 20, "height": 20})
    with pytest.raises(ValueError):
        store.capture_artifact(scope="region")
    with pytest.raises(ValueError):
        store.capture_artifact(scope="unknown")


def test_managed_artifact_bridges_preserve_invalid_region_for_owner_validation(monkeypatch, tmp_path):
    from winpilot.agent.tools import ToolSession
    from winpilot.tasking import artifacts
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    monkeypatch.setattr(
        artifacts,
        "capture",
        lambda _region: (_ for _ in ()).throw(AssertionError("invalid region reached physical capture")),
    )
    session = ToolSession(task="reject malformed managed artifact regions")
    session.run_id = "invalid-region-bridge"
    session.managed_artifact_store = ArtifactStore("invalid-region-bridge", tmp_path / "artifacts")
    session.evidence_registry = EvidenceRegistry("invalid-region-bridge")
    try:
        screenshot = json.loads(session._t_screenshot({
            "managed_artifact": True,
            "scope": "screen",
            "region": [1, 2, 3, 4],
        }).text)
        capture_artifact = json.loads(session._t_capture_artifact({
            "scope": "screen",
            "region": "1,2,3,4",
        }).text)
    finally:
        session.close()

    assert screenshot["ok"] is False
    assert capture_artifact["ok"] is False
    assert "region" in screenshot["error"]
    assert "region" in capture_artifact["error"]


def test_artifact_capture_rejects_frame_dimension_drift(monkeypatch, tmp_path):
    from winpilot.tasking import artifacts
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(artifacts, "monitor_regions", lambda: (Region(0, 0, 1920, 1080),))
    monkeypatch.setattr(artifacts, "capture", lambda _region: np.zeros((179, 320, 3), dtype=np.uint8))
    store = ArtifactStore("dimension-drift", tmp_path / "artifacts")

    with pytest.raises(ValueError, match="dimensions do not match"):
        store.capture_artifact(
            scope="region",
            region={"x": 10, "y": 20, "width": 320, "height": 180},
        )


def test_model_screenshot_without_managed_mode_keeps_base64_path(monkeypatch):
    from winpilot.agent import desktop_tools
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        desktop_tools.executor,
        "screenshot_base64",
        lambda quality: {"ok": True, "quality": quality, "base64": ""},
    )
    session = ToolSession(task="observe screen")
    monkeypatch.setattr(
        session,
        "_managed_artifacts",
        lambda: (_ for _ in ()).throw(AssertionError("default screenshot must not create an artifact")),
    )
    try:
        result = session._t_screenshot({"quality": 63})
    finally:
        session.close()

    assert json.loads(result.text) == {"ok": True, "quality": 63, "base64": ""}


def test_paste_image_file_uses_clipboard_dib_and_focused_target(monkeypatch, tmp_path):
    from winpilot.action import executor

    image_path = tmp_path / "result.png"
    Image.new("RGB", (18, 11), color=(12, 34, 56)).save(image_path)
    calls = []

    class Clipboard:
        @staticmethod
        def OpenClipboard():
            calls.append("open")

        @staticmethod
        def EmptyClipboard():
            calls.append("empty")

        @staticmethod
        def SetClipboardData(fmt, payload):
            calls.append(("set", fmt, payload[:4]))

        @staticmethod
        def CloseClipboard():
            calls.append("close")

    monkeypatch.setitem(sys.modules, "win32clipboard", Clipboard)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: {"ok": hwnd == 88})
    monkeypatch.setattr(executor, "_raw_press_keys", lambda keys: calls.append(("keys", keys)))

    result = executor.paste_image_file(str(image_path), hwnd=88)

    assert result["ok"] is True
    assert result["via"] == "clipboard_dib"
    assert ("set", executor.win32con.CF_DIB, b"(\x00\x00\x00") in calls
    assert ("keys", ["ctrl", "v"]) in calls


def test_paste_image_file_rejects_missing_image():
    from winpilot.action import executor

    result = executor.paste_image_file(r"C:\\missing\\calculator.png")

    assert result["ok"] is False
    assert "not found" in result["error"]
