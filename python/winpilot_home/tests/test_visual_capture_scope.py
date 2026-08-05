from __future__ import annotations


def test_credible_work_surface_bounds_use_authoritative_frame_coordinates():
    import base64

    import cv2
    import numpy as np

    from winpilot.perception.vision_cv import credible_work_surface_frame_bounds

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

    from winpilot.perception.vision_cv import credible_work_surface_frame_bounds

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
    from winpilot.perception.vision_cv import credible_work_surface_frame_bounds

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


def test_capture_scope_uses_runtime_window_state_not_global_task_words(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    target = Region(100, 80, 900, 700)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)

    decision = image_capture.select_visual_capture_scope(
        71,
        enabled=True,
        task="\u628a\u684c\u9762\u56fe\u6807\u62d6\u5230\u53e6\u4e00\u4e2a\u7a97\u53e3",
    )

    assert decision.scope == "target_window"
    assert decision.region == target
    assert decision.reason == "verified_foreground_target"


def test_capture_scope_does_not_classify_cross_application_word_orders(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    target = Region(100, 80, 900, 700)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)
    tasks = [
        "paste this into the design tool from the notes app",
        "from one unknown app copy the text to another app",
        "\u4ece\u4e00\u4e2a\u672a\u77e5\u5e94\u7528\u590d\u5236\u6587\u672c\u5230\u53e6\u4e00\u4e2a\u5e94\u7528",
        "\u628a\u6587\u4ef6\u4ece\u5de6\u8fb9\u7a97\u53e3\u62d6\u62fd\u5230\u53f3\u8fb9\u7a97\u53e3",
    ]

    for task in tasks:
        decision = image_capture.select_visual_capture_scope(71, enabled=True, task=task)
        assert decision.scope == "target_window", task
        assert decision.region == target, task
        assert decision.reason == "verified_foreground_target", task


def test_capture_scope_does_not_classify_comparison_or_side_by_side_words(monkeypatch):
    from winpilot.utils import image_capture
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    monkeypatch.setattr(image_capture.screenshot, "virtual_screen_region", lambda: desktop)
    target = Region(100, 80, 900, 700)
    monkeypatch.setattr(image_capture.screenshot, "window_region", lambda _hwnd: target)
    monkeypatch.setattr(image_capture.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(image_capture.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(image_capture.win32gui, "GetForegroundWindow", lambda: 71)
    tasks = [
        "compare two unknown applications side by side",
        "look at both applications and report the differences",
        "\u540c\u65f6\u67e5\u770b\u4e24\u4e2a\u5e94\u7528",
        "\u5bf9\u6bd4\u5de6\u4fa7\u7a97\u53e3\u4e0e\u53f3\u4fa7\u7a97\u53e3\u7684\u5185\u5bb9",
        "\u67e5\u770b\u5de6\u8fb9\u7a97\u53e3\u548c\u53f3\u8fb9\u7a97\u53e3",
    ]

    for task in tasks:
        decision = image_capture.select_visual_capture_scope(71, enabled=True, task=task)
        assert decision.scope == "target_window", task
        assert decision.region == target, task
        assert decision.reason == "verified_foreground_target", task


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
        "\u628a\u8fd9\u6bb5\u6587\u5b57\u7c98\u8d34\u5230\u5f53\u524d\u8f93\u5165\u6846",
        "\u5728\u5f53\u524d\u5de5\u4f5c\u533a\u4e2d\u62d6\u52a8\u5bf9\u8c61\u5230\u53f3\u8fb9",
        "\u628a\u56fe\u7247\u79fb\u52a8\u5230\u5f53\u524d\u9875\u9762\u4e2d\u5fc3",
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
    assert "Frame scope=target_window" in text
    assert "Exact authoritative full-image size=900x700 pixels" in text
    assert "All native spatial tool coordinates use image_* pixel values" in text
    assert "x=0..899, y=0..699" in text
    assert "frame_*" not in text
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
    state = runner._frame_authority.current
    assert state is not None
    assert runner.session._frame_authority is runner._frame_authority
    assert state.frame_id == runner.session._visual_frame_id
    assert state.region == (-1600, 40, 900, 700)
    assert state.submitted_size == (900, 700)
    assert state.image_b64 == "jpeg"


def test_normalized_detail_crop_magnifies_same_frame_pixels_without_new_coordinates():
    import base64

    import cv2
    import numpy as np

    from winpilot.utils.image_capture import normalized_detail_crop_jpeg_b64

    image = np.zeros((300, 600, 3), dtype=np.uint8)
    image[20:80, 240:360] = (255, 255, 255)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok is True

    crop_b64, bounds = normalized_detail_crop_jpeg_b64(
        base64.b64encode(encoded.tobytes()).decode("ascii"),
        [400, 60, 600, 280],
        padding=40,
        min_long_side=560,
    )

    decoded = cv2.imdecode(
        np.frombuffer(base64.b64decode(crop_b64), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert bounds == [360, 20, 640, 320]
    assert decoded is not None
    assert max(decoded.shape[:2]) >= 560
