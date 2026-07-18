from __future__ import annotations


def test_credible_work_surface_bounds_use_authoritative_frame_coordinates():
    import base64

    import cv2
    import numpy as np

    from winpilot.utils.image_capture import credible_work_surface_frame_bounds

    image = np.zeros((720, 1008, 3), dtype=np.uint8)
    image[258:671, 126:992] = 255
    ok, encoded = cv2.imencode(".png", image)
    assert ok is True

    result = credible_work_surface_frame_bounds(
        base64.b64encode(encoded.tobytes()).decode("ascii")
    )

    assert result is not None
    assert result["source"] == "authoritative_frame_pixels"
    assert result["detected_work_surface_frame_bounds"] == [125, 359, 984, 932]
    assert result["work_surface_frame_bounds"] == [149, 383, 960, 908]
    assert result["continuous_input_edge_inset"] == 24
    assert result["interaction_region"]["coordinate_space"] == "frame_0_1000"


def test_work_surface_is_clipped_to_window_client_authority():
    import base64

    import cv2
    import numpy as np

    from winpilot.utils.image_capture import credible_work_surface_frame_bounds

    image = np.zeros((500, 800, 3), dtype=np.uint8)
    image[50:480, 20:780] = 255
    ok, encoded = cv2.imencode(".png", image)
    assert ok is True

    result = credible_work_surface_frame_bounds(
        base64.b64encode(encoded.tobytes()).decode("ascii"),
        client_frame_bounds=[80, 140, 920, 940],
    )

    assert result is not None
    assert result["ok"] is True
    assert result["detected_work_surface_frame_bounds"] == [80, 140, 920, 940]
    assert result["work_surface_frame_bounds"] == [104, 164, 896, 916]


def test_region_frame_bounds_maps_physical_client_into_window_frame():
    from winpilot.utils.image_capture import region_frame_bounds
    from winpilot.utils.screenshot import Region

    frame = Region(400, 200, 1001, 701)
    client = Region(450, 270, 901, 601)

    assert region_frame_bounds(client, frame) == [50, 100, 950, 957]


def test_credible_work_surface_bounds_preserve_ambiguous_failure(monkeypatch):
    import base64

    import cv2
    import numpy as np

    from winpilot.perception import vision_cv
    from winpilot.utils.image_capture import credible_work_surface_frame_bounds

    image = np.zeros((120, 160, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok is True
    monkeypatch.setattr(
        vision_cv,
        "credible_light_work_surface",
        lambda _image: {
            "ok": False,
            "reason": "multiple similarly plausible light work surfaces were visible",
            "candidate_count": 2,
        },
    )

    result = credible_work_surface_frame_bounds(
        base64.b64encode(encoded.tobytes()).decode("ascii")
    )

    assert result is not None
    assert result["ok"] is False
    assert result["ambiguous"] is True
    assert result["failure_kind"] == "ambiguous"
    assert result["candidate_count"] == 2


def test_capture_scope_toggle_off_forces_full_desktop(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(-1920, 0, 3840, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.win32gui,
        "IsWindow",
        lambda _hwnd: (_ for _ in ()).throw(AssertionError("disabled mode must not probe hwnd")),
    )

    decision = image_capture.select_visual_capture_scope(71, enabled=False)

    assert decision.scope == "full_desktop"
    assert decision.region == desktop
    assert decision.reason == "optimization_disabled"
    assert decision.metadata()["normalized_coordinate_space"] == "frame_0_1000"


def test_capture_scope_auto_crops_only_verified_foreground_target(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    target = Region(140, 90, 1100, 760)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda hwnd: hwnd == 71)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda hwnd: hwnd == 71)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)

    decision = image_capture.select_visual_capture_scope(71, enabled=True, task="edit this document")

    assert decision.scope == "target_window"
    assert decision.region == target
    assert decision.target_verified is True
    assert decision.reason == "verified_foreground_target"
    assert decision.metadata()["screen_region"] == {
        "left": 140,
        "top": 90,
        "width": 1100,
        "height": 760,
    }


def test_capture_scope_auto_falls_back_when_foreground_is_ambiguous(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.screenshot,
        "window_region",
        lambda _hwnd: Region(100, 100, 900, 700),
    )
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 88)

    decision = image_capture.select_visual_capture_scope(71, enabled=True, task="edit this document")

    assert decision.scope == "full_desktop"
    assert decision.region == desktop
    assert decision.reason == "foreground_target_mismatch"


def test_capture_scope_auto_keeps_desktop_context_for_global_task(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.win32gui,
        "IsWindow",
        lambda _hwnd: (_ for _ in ()).throw(AssertionError("global task must fall back first")),
    )

    decision = image_capture.select_visual_capture_scope(
        71,
        enabled=True,
        task="把桌面图标拖到另一个窗口",
    )

    assert decision.scope == "full_desktop"
    assert decision.reason == "task_requires_desktop_context"


def test_capture_scope_detects_common_cross_application_word_orders(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.win32gui,
        "IsWindow",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("cross-app task must choose desktop before probing hwnd")
        ),
    )
    tasks = [
        "paste this into Word from Notepad",
        "from Notepad copy the text to Word",
        "把记事本的文字复制到Word",
        "从记事本复制文本到Word",
        "复制记事本文字到Word",
    ]

    for task in tasks:
        decision = image_capture.select_visual_capture_scope(71, enabled=True, task=task)
        assert decision.scope == "full_desktop", task
        assert decision.reason == "task_requires_desktop_context", task


def test_capture_scope_keeps_both_windows_for_comparison_and_side_by_side_tasks(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.win32gui,
        "IsWindow",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("multi-view task must choose desktop before probing hwnd")
        ),
    )
    tasks = [
        "compare Notepad and Word side by side",
        "look at both applications and report the differences",
        "同时查看两个应用",
        "对比记事本和Word的内容",
        "查看左边窗口和右边窗口",
    ]

    for task in tasks:
        decision = image_capture.select_visual_capture_scope(71, enabled=True, task=task)
        assert decision.scope == "full_desktop", task
        assert decision.reason == "task_requires_desktop_context", task


def test_capture_scope_does_not_expand_single_window_move_or_paste_tasks(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    target = Region(100, 80, 900, 700)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)
    tasks = [
        "把这段文字粘贴到当前输入框",
        "在画板中拖动圆形到右边",
        "把图片移动到当前幻灯片中心",
    ]

    for task in tasks:
        decision = image_capture.select_visual_capture_scope(71, enabled=True, task=task)
        assert decision.scope == "target_window", task
        assert decision.region == target, task


def test_vlm_describe_toggle_off_uses_full_desktop_and_returns_scope(monkeypatch):
    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(-1920, 0, 3840, 1080)
    captured = []
    events = []
    monkeypatch.setitem(
        perception_tools.CONFIG._data["controller"],
        "target_window_capture_enabled",
        False,
    )
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.win32gui,
        "IsWindow",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("disabled target-window capture must not probe the hwnd")
        ),
    )
    monkeypatch.setattr(perception_tools.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        perception_tools.vlm,
        "describe",
        lambda region, question: captured.append((region, question)) or "desktop description",
    )
    monkeypatch.setattr(
        perception_tools.BUS,
        "publish",
        lambda name, **payload: events.append((name, payload)),
    )
    session = ToolSession(task="inspect the current window")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda _args: 71)

    result = session._t_vlm_describe({"question": "what is visible?"})

    assert captured == [(desktop, "what is visible?")]
    assert result.text.startswith(
        "[capture_scope=full_desktop; reason=optimization_disabled; "
        "region=[-1920, 0, 3840, 1080]]"
    )
    assert result.replay["capture_scope"] == "full_desktop"
    assert result.replay["capture_scope_reason"] == "optimization_disabled"
    assert events[-1][0] == "perception"
    assert events[-1][1]["capture_region"] == [-1920, 0, 3840, 1080]


def test_vlm_verify_enabled_uses_only_verified_foreground_target(monkeypatch):
    import json

    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    target = Region(120, 80, 960, 720)
    captured = []
    monkeypatch.setitem(
        perception_tools.CONFIG._data["controller"],
        "target_window_capture_enabled",
        True,
    )
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda hwnd: hwnd == 71)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda hwnd: hwnd == 71)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)
    monkeypatch.setattr(perception_tools.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        perception_tools.vlm,
        "verify",
        lambda region, criteria: captured.append((region, criteria)) or {
            "ok": True,
            "verified": False,
            "semantic_visual_verified": False,
            "verify_method": "semantic-vlm",
        },
    )
    session = ToolSession(task="inspect this document")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda _args: 71)

    result = session._t_vlm_verify({"criteria": "the document is complete"})
    payload = json.loads(result.text)

    assert captured[0][0] == target
    assert payload["capture_scope"] == "target_window"
    assert payload["capture_scope_reason"] == "verified_foreground_target"
    assert payload["capture_region"] == [120, 80, 960, 720]
    assert result.replay["capture_scope"] == "target_window"


def test_vlm_verify_ambiguous_foreground_falls_back_to_full_desktop(monkeypatch):
    import json

    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 2560, 1440)
    captured = []
    monkeypatch.setitem(
        perception_tools.CONFIG._data["controller"],
        "target_window_capture_enabled",
        True,
    )
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        image_capture.screenshot,
        "window_region",
        lambda _hwnd: Region(100, 100, 1000, 700),
    )
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 88)
    monkeypatch.setattr(perception_tools.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        perception_tools.vlm,
        "verify",
        lambda region, _criteria: captured.append(region) or {
            "ok": True,
            "verified": False,
            "semantic_visual_verified": False,
            "verify_method": "semantic-vlm",
        },
    )
    session = ToolSession(task="inspect this document")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda _args: 71)
    monkeypatch.setattr(session, "_window_related_to_target", lambda *_args: False)

    payload = json.loads(session._t_vlm_verify({"criteria": "ready"}).text)

    assert captured == [desktop]
    assert payload["capture_scope"] == "full_desktop"
    assert payload["capture_scope_reason"] == "foreground_target_mismatch"


def test_vlm_explicit_roi_preserves_caller_region_and_bypasses_selector(monkeypatch):
    import json

    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    explicit = Region(-120, 35, 640, 360)
    described = []
    verified = []
    monkeypatch.setattr(perception_tools.vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        perception_tools,
        "select_visual_capture_scope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an explicit ROI must bypass automatic scope selection")
        ),
    )
    monkeypatch.setattr(
        perception_tools.vlm,
        "describe",
        lambda region, _question: described.append(region) or "roi description",
    )
    monkeypatch.setattr(
        perception_tools.vlm,
        "verify",
        lambda region, _criteria: verified.append(region) or {
            "ok": True,
            "verified": False,
            "semantic_visual_verified": False,
            "verify_method": "semantic-vlm",
        },
    )
    session = ToolSession(task="inspect only the requested area")
    args = {"left": -120, "top": 35, "width": 640, "height": 360}

    describe_result = session._t_vlm_describe({**args, "question": "what is here?"})
    verify_result = session._t_vlm_verify({**args, "criteria": "the ROI is ready"})
    verify_payload = json.loads(verify_result.text)

    assert described == [explicit]
    assert verified == [explicit]
    assert describe_result.replay["capture_scope"] == "explicit_roi"
    assert verify_payload["capture_scope"] == "explicit_roi"
    assert verify_payload["capture_scope_reason"] == "caller_explicit_region"
    assert verify_payload["capture_region"] == [-120, 35, 640, 360]


def test_main_model_frame_declares_scope_region_and_coordinate_space(monkeypatch):
    from winpilot.agent import loop
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.image_capture import VisualCaptureScope
    from winpilot.utils.screenshot import Region

    decision = VisualCaptureScope(
        Region(-1600, 40, 900, 700),
        "target_window",
        "verified_foreground_target",
        target_hwnd=71,
        target_verified=True,
    )
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    monkeypatch.setattr(loop, "select_visual_capture_scope", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(loop, "capture_window_jpeg_b64", lambda *_args, **_kwargs: "jpeg")
    events = []
    monkeypatch.setattr(loop.BUS, "publish", lambda name, **payload: events.append((name, payload)))

    runner = AgentRunner("edit this document")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 71

    assert runner._inject_visual_frame(phase="initial") is True
    text = runner.messages[-1]["content"][0]["text"]
    assert "Frame scope=target_window" in text
    assert "left=-1600, top=40, width=900, height=700" in text
    assert "image_* tool fields copied directly from this displayed image" in text
    assert "frame_* 0..1000 fields are an alternative" in text
    event = events[-1][1]
    assert event["capture_scope"] == "target_window"
    assert event["capture_region"] == [-1600, 40, 900, 700]
    assert event["coordinate_space"] == "frame_normalized_0_1000"
    assert runner.session._visual_frame_id == 1
    assert runner.session._visual_frame_epoch == 0
    assert runner.session._visual_frame_scope == "target_window"
    assert runner.session._visual_frame_region == {
        "left": -1600,
        "top": 40,
        "width": 900,
        "height": 700,
    }
