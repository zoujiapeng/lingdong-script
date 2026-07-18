from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def semantic_grounding_enabled(monkeypatch):
    """Opt into the legacy structured anchor assist for its focused tests."""
    from winpilot.agent import loop as loop_mod

    original_get = loop_mod.CONFIG.get

    def configured(*path, default=None):
        if path == ("controller", "semantic_grounding_assist"):
            return True
        return original_get(*path, default=default)

    monkeypatch.setattr(loop_mod.CONFIG, "get", configured)


@pytest.fixture
def authoritative_native_vision(monkeypatch):
    """Provide the native-vision premises exercised by synthetic-frame tests.

    These tests construct authoritative frames with coordinates that can be
    larger than the interactive desktop exposed by a headless Windows CI
    runner.  Letting ``Region.clamp_to_screen`` consult that host desktop makes
    an otherwise coherent synthetic frame stale.  The fixture also makes the
    image-capable model premise explicit instead of inheriting it from the
    ignored local ``config.json``.
    """
    from winpilot.agent import done_verifier
    from winpilot.utils import screenshot as screenshot_mod
    from winpilot.utils.screenshot import Region

    synthetic_desktop = Region(-4096, -2160, 8192, 4320)
    monkeypatch.setattr(
        "winpilot.agent.loop._main_model_has_vision",
        lambda: True,
    )
    monkeypatch.setattr(
        done_verifier,
        "model_supports_vision",
        lambda _model, _provider="": True,
    )
    monkeypatch.setattr(
        screenshot_mod,
        "virtual_screen_region",
        lambda: synthetic_desktop,
    )


def _tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def test_native_point_scope_guard_blocks_taskbar_hit(monkeypatch) -> None:
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the visible control")
    runner.session.hwnd = 100
    runner._last_visual_feedback_b64 = "x" * 128
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.WindowFromPoint", lambda _point: 200,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetAncestor", lambda hwnd, _kind: hwnd,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetClassName", lambda _hwnd: "Shell_TrayWnd",
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetWindowText", lambda _hwnd: "",
    )

    blocked = runner._native_point_scope_guard(
        "click", {"x": 1048, "y": 1021},
    )

    assert blocked is not None
    assert blocked["native_point_scope_guard"] is True
    assert blocked["scope_reason"] == "system_taskbar_surface"
    assert blocked["action_executed"] is False


def test_native_point_scope_guard_follows_rebound_target_on_full_desktop(
    monkeypatch,
) -> None:
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the visible control in the verified application")
    runner.session.hwnd = 100
    runner._visual_frame_scope = "full_desktop"
    runner._visual_frame_hwnd = 100
    runner._last_visual_feedback_b64 = "x" * 128

    def refresh_target():
        runner.session.hwnd = 200
        runner.session._last_scope_recovery = {
            "reason": "foreground_application_successor_rebound",
        }
        return 200

    monkeypatch.setattr(
        runner.session, "_refresh_bound_target_window", refresh_target,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.WindowFromPoint", lambda _point: 200,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetAncestor", lambda hwnd, _flag: hwnd,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetClassName", lambda _hwnd: "AppWindow",
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.win32gui.GetWindowText", lambda _hwnd: "Verified App",
    )
    monkeypatch.setattr(
        runner.session,
        "_window_related_to_target",
        lambda hwnd, target: hwnd == target,
    )
    monkeypatch.setattr(runner.session, "_same_window_process", lambda *_args: False)

    assert runner._native_point_scope_guard(
        "click", {"x": 320, "y": 240},
    ) is None
    assert runner.session.hwnd == 200


def test_native_point_scope_guard_requires_fresh_crop_after_target_rebound(
    monkeypatch,
) -> None:
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the visible control in the verified application")
    runner.session.hwnd = 100
    runner._visual_frame_scope = "target_window"
    runner._visual_frame_hwnd = 100
    runner._last_visual_feedback_b64 = "x" * 128

    def refresh_target():
        runner.session.hwnd = 200
        return 200

    monkeypatch.setattr(
        runner.session, "_refresh_bound_target_window", refresh_target,
    )

    blocked = runner._native_point_scope_guard(
        "click", {"x": 320, "y": 240},
    )

    assert blocked is not None
    assert blocked["scope_reason"] == "target_rebound_requires_fresh_frame"
    assert blocked["target_hwnd"] == 200
    assert blocked["previous_frame_hwnd"] == 100
    assert blocked["action_executed"] is False


def test_native_visual_observe_skips_lossy_structured_stack(monkeypatch):
    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.image_capture import VisualCaptureScope
    from winpilot.utils.screenshot import Region

    # This task is intentionally not tagged as chat/canvas/visual. Native
    # vision should still receive the whole frame directly for an unqueried
    # observe instead of paying the generic structured stack first.
    session = ToolSession(task="open the settings page and click calibration")
    session.hwnd = 7
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(perception_tools, "window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        perception_tools,
        "select_visual_capture_scope",
        lambda *_args, **_kwargs: VisualCaptureScope(
            Region(10, 20, 800, 600),
            "target_window",
            "verified_foreground_target",
            target_hwnd=7,
            target_verified=True,
        ),
    )
    monkeypatch.setattr("winpilot.agent.tools.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda _hwnd: "QQ")
    monkeypatch.setattr(
        perception_tools,
        "capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "frame",
    )
    monkeypatch.setattr(
        perception_tools.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native visual direct path must not call fusion.observe")
        ),
    )

    result = session._t_observe({"include_icons": True})

    assert result.image_b64 == "frame"
    assert "Native visual direct observation" in result.text
    assert session.snapshot is not None
    assert session.snapshot.dim_stats["native_frame"]["authoritative"] is True
    assert session.snapshot.dim_stats["uia"]["skipped"] == "native_vision_direct"
    assert session._last_native_frame_capture["image_b64"] == "frame"
    assert session._last_native_frame_capture["region"] == Region(10, 20, 800, 600)


def test_visual_frame_marks_current_screen_epoch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("draw on the canvas")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(50, 70, 900, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "jpeg",
    )

    assert runner._inject_visual_frame(phase="post_action", action="click") is True
    assert runner._has_fresh_visual_grounding() is True
    assert runner._visual_frame_epoch == 4
    assert runner._visual_frame_hwnd == 99
    assert runner._last_visual_feedback_action == "click"
    frame = runner.messages[-1]["content"]
    assert any(part.get("type") == "image_url" for part in frame)
    assert "authoritative current state" in frame[0]["text"]


def test_visual_frame_rebinds_verified_application_successor_before_binding(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("operate the verified application")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 3
    events = []

    def refresh_target():
        runner.session.hwnd = 22
        runner.session._last_scope_recovery = {
            "reason": "foreground_application_successor_rebound",
        }
        return 22

    monkeypatch.setattr(
        runner.session, "_refresh_bound_target_window", refresh_target,
    )
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    assert runner._inject_visual_frame(
        phase="preflight",
        action="visual_preflight_changed",
        image_b64="successor-frame-pixels",
        frame_region=Region(0, 0, 800, 600),
    ) is True

    assert runner._visual_frame_hwnd == 22
    rebound = [payload for name, payload in events if name == "visual_target_rebound"]
    assert rebound[-1]["previous_target_hwnd"] == 11
    assert rebound[-1]["target_hwnd"] == 22
    assert rebound[-1]["reason"] == "preflight_frame_capture"


def test_preflight_rebound_target_crop_returns_fresh_authoritative_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.image_capture import VisualCaptureScope
    from winpilot.utils.screenshot import Region

    old_region = Region(10, 20, 800, 600)
    new_region = Region(30, 40, 1000, 700)
    runner = AgentRunner("operate the verified application")
    runner.session.hwnd = 11
    runner._visual_frame_scope = "target_window"
    runner._visual_frame_hwnd = 11
    runner._visual_frame_region = (
        old_region.left, old_region.top, old_region.width, old_region.height,
    )
    runner._last_visual_feedback_b64 = "old-frame" * 64

    def refresh_target():
        runner.session.hwnd = 22
        return 22

    monkeypatch.setattr(
        runner.session, "_refresh_bound_target_window", refresh_target,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.select_visual_capture_scope",
        lambda *_args, **_kwargs: VisualCaptureScope(
            new_region,
            "target_window",
            "verified_foreground_target",
            target_hwnd=22,
            target_verified=True,
        ),
    )
    captures = []
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda hwnd, **kwargs: captures.append((hwnd, kwargs["region"]))
        or "new-frame" * 64,
    )

    frame, ratio, error, region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (frame, ratio, error, region) == (
        "new-frame" * 64, 1.0, "", new_region,
    )
    assert captures == [(22, new_region)]


def test_full_desktop_frame_stays_grounded_when_crop_becomes_available(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    desktop = Region(0, 0, 1920, 1080)
    runner = AgentRunner("operate the visible application")
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    runner._visual_frame_scope = "full_desktop"
    runner._visual_frame_region = (0, 0, 1920, 1080)
    runner._visual_frame_epoch = 4
    runner._visual_frame_hwnd = 99
    runner._visual_frame_id = 7
    runner._last_visual_feedback_b64 = "same-frame" * 64
    captures = []
    monkeypatch.setattr("winpilot.agent.loop.virtual_screen_region", lambda: desktop)
    monkeypatch.setattr(
        "winpilot.agent.loop.select_visual_capture_scope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a new crop decision must not remap an in-flight full frame")
        ),
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **kwargs: captures.append(kwargs["region"])
        or runner._last_visual_feedback_b64,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.0)

    assert runner._has_fresh_visual_grounding() is True
    frame, ratio, error, region = runner._preflight_visual_frame(
        "click", {"frame_x": 400, "frame_y": 200},
    )

    assert (frame, ratio, error) == ("", 0.0, "")
    assert region == desktop
    assert captures == [desktop]


def test_initial_prebind_frame_settle_requires_a_bounded_quiet_period(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("open the target app and use its visible interface")
    captures = iter([
        "startup-a", "startup-b", "settling-c", "settling-d", "settled-e",
    ])
    sleeps = []
    events = []
    ratios = iter([0.04, 0.001, 0.0008, 0.0004])
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: next(captures),
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio",
        lambda _before, _after: next(ratios),
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda event, **payload: events.append((event, payload)),
    )

    frame = runner._capture_settled_initial_frame(
        42, Region(10, 20, 800, 600),
    )

    assert frame == "settled-e"
    assert len(sleeps) == 4
    assert events[-1][0] == "initial_visual_settle"
    assert events[-1][1]["captures"] == 5
    assert events[-1][1]["stable"] is True
    assert events[-1][1]["stable_pairs"] == 3
    assert events[-1][1]["last_change_ratio"] == 0.0004


def test_visual_frame_reports_unique_work_surface_in_frame_coordinates(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create continuous paths on the visible work surface")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(runner, "_continuous_pointer_output_requested", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.credible_work_surface_frame_bounds",
        lambda _b64, **_kwargs: {
            "work_surface_frame_bounds": [125, 359, 984, 932],
            "source": "authoritative_frame_pixels",
        },
    )

    assert runner._inject_visual_frame(
        phase="initial",
        image_b64="exact-authoritative-frame",
        frame_region=Region(480, 154, 1008, 720),
    ) is True

    text = runner.messages[-1]["content"][0]["text"]
    assert "work_surface_frame_bounds=[125, 359, 984, 932]" in text
    assert "same frame 0..1000 coordinate system" in text
    assert "every authored point must remain inside" in text
    assert runner._visual_work_surface_frame_bounds == (125, 359, 984, 932)
    assert runner._visual_work_surface_frame_id == runner._visual_frame_id
    assert runner.session._visual_work_surface_frame_bounds == [125, 359, 984, 932]


def test_visual_frame_skips_light_surface_detection_for_non_continuous_task(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the visible primary action")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)

    def unexpected_surface_detection(*_args, **_kwargs):
        raise AssertionError("non-continuous tasks must not run light-surface detection")

    monkeypatch.setattr(
        "winpilot.agent.loop.credible_work_surface_frame_bounds",
        unexpected_surface_detection,
    )

    assert runner._inject_visual_frame(
        phase="initial",
        image_b64="exact-authoritative-frame",
        frame_region=Region(480, 154, 1008, 720),
    ) is True

    text = runner.messages[-1]["content"][0]["text"]
    assert "light work surface" not in text
    assert "work_surface_frame_bounds" not in text
    assert runner._visual_work_surface_frame_bounds is None
    assert runner._visual_work_surface_blocked_reason == ""
    assert runner.session._visual_work_surface_frame_bounds is None


def test_visual_work_surface_binding_survives_content_component_shrink():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("edit content on the visible document surface")
    original = (125, 359, 984, 932)
    frame_region = (480, 154, 1008, 720)

    first, first_policy = runner._stable_visual_work_surface(
        original,
        hwnd=99,
        frame_region=frame_region,
        initial=True,
    )
    shrunk, shrink_policy = runner._stable_visual_work_surface(
        (502, 359, 984, 932),
        hwnd=99,
        frame_region=frame_region,
    )
    missing, missing_policy = runner._stable_visual_work_surface(
        None,
        hwnd=99,
        frame_region=frame_region,
    )

    assert first == original
    assert first_policy == "fresh_target_binding"
    assert shrunk == original
    assert shrink_policy == "stable_binding_retained_after_component_shrink"
    assert missing is None
    assert missing_policy == "current_detection_missing_fail_closed"


def test_visual_work_surface_binding_rejects_central_subset_as_possible_overlay():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("edit content without acting through a visible overlay")
    frame_region = (480, 154, 1008, 720)
    runner._stable_visual_work_surface(
        (125, 359, 984, 932),
        hwnd=99,
        frame_region=frame_region,
        capture_scope="target_window",
        initial=True,
    )

    current, policy = runner._stable_visual_work_surface(
        (350, 450, 700, 750),
        hwnd=99,
        frame_region=frame_region,
        capture_scope="target_window",
    )

    assert current is None
    assert policy == "possible_nested_surface_fail_closed"


def test_visual_work_surface_displacement_requires_two_matching_frames():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("continue after a genuine visible layout change")
    frame_region = (480, 154, 1008, 720)
    replacement = (60, 280, 900, 880)
    runner._stable_visual_work_surface(
        (125, 359, 984, 932),
        hwnd=99,
        frame_region=frame_region,
        initial=True,
    )

    pending, pending_policy = runner._stable_visual_work_surface(
        replacement,
        hwnd=99,
        frame_region=frame_region,
    )
    rebound, rebound_policy = runner._stable_visual_work_surface(
        replacement,
        hwnd=99,
        frame_region=frame_region,
    )

    assert pending is None
    assert pending_policy == "layout_displacement_pending_confirmation"
    assert rebound == replacement
    assert rebound_policy == "rebound_after_confirmed_layout_displacement"


def test_visual_work_surface_binding_rebinds_for_new_capture_target():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate whichever visible document is current")
    original = (125, 359, 984, 932)
    replacement = (80, 210, 920, 900)
    runner._stable_visual_work_surface(
        original,
        hwnd=99,
        frame_region=(480, 154, 1008, 720),
        initial=True,
    )

    rebound, policy = runner._stable_visual_work_surface(
        replacement,
        hwnd=100,
        frame_region=(0, 0, 1920, 1080),
    )

    assert rebound == replacement
    assert policy == "fresh_target_binding"


@pytest.mark.usefixtures("authoritative_native_vision")
def test_ambiguous_current_surface_blocks_continuous_input(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("edit only the currently visible safe work surface")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    detections = iter([
        {
            "ok": True,
            "work_surface_frame_bounds": [125, 359, 984, 932],
            "source": "authoritative_frame_pixels",
        },
        {
            "ok": False,
            "reason": "a large nested light panel or modal obscured the work surface",
            "failure_kind": "ambiguous",
            "ambiguous": True,
            "source": "authoritative_frame_pixels",
        },
    ])
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(runner, "_continuous_pointer_output_requested", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.credible_work_surface_frame_bounds",
        lambda _b64, **_kwargs: next(detections),
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )

    assert runner._inject_visual_frame(
        phase="initial",
        image_b64="first-frame",
        frame_region=Region(480, 154, 1008, 720),
    ) is True
    assert runner._inject_visual_frame(
        phase="observe",
        image_b64="modal-frame",
        frame_region=Region(480, 154, 1008, 720),
    ) is True
    assert runner._visual_work_surface_frame_bounds is None
    # Keep this unit test independent of the real Win32 foreground-window
    # selector; the injected frame/epoch/region are otherwise current.
    runner._visual_frame_scope = ""

    with pytest.raises(NativeFrameCoordinateError) as blocked:
        runner._map_normalized_frame_coordinates(
            "draw_strokes",
            {"frame_strokes": [[[200, 400], [400, 500], [800, 800]]]},
        )

    assert blocked.value.metadata["work_surface_fail_closed"] is True
    assert blocked.value.metadata["work_surface_detection_failed"] is True
    assert "nested light panel or modal" in (
        blocked.value.metadata["work_surface_blocked_reason"]
    )


def test_new_visual_frame_folds_old_image_before_next_model_request(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("inspect and operate from the current frame only")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 1
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(10, 20, 800, 600),
    )

    assert runner._inject_visual_frame(
        phase="initial",
        image_b64="first-frame-pixels",
    ) is True
    first_index = len(runner.messages) - 1
    runner.session._screen_epoch = 2
    assert runner._inject_visual_frame(
        phase="post_action",
        action="click",
        image_b64="second-frame-pixels",
    ) is True

    assert isinstance(runner.messages[first_index]["content"], str)
    assert "folded" in runner.messages[first_index]["content"]
    live_images = [
        part["image_url"]["url"]
        for message in runner.messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert live_images == ["data:image/jpeg;base64,second-frame-pixels"]

    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=[]),
                finish_reason="stop",
            )])

    runner._trace_debug = lambda *_args, **_kwargs: None
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    assert runner._chat(client, []) is not None
    sent_images = [
        part["image_url"]["url"]
        for message in captured["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert sent_images == ["data:image/jpeg;base64,second-frame-pixels"]


def test_mimo_thinking_is_adaptive_until_continuous_pixels_really_change(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    values = {
        ("api",): {
            "provider": "mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5",
            "thinking": False,
            "temperature": 0.3,
            "max_tokens": 4096,
        },
        ("controller", "native_visual_thinking"): True,
    }
    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: values.get(tuple(path), default),
    )
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)

    runner = AgentRunner("draw a detailed freehand path in the visible editor")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._trace_debug = lambda *_args, **_kwargs: None
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content="ok",
                tool_calls=[],
                reasoning_content="spatial reasoning",
            ),
            finish_reason="stop",
        )])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    assert runner._chat(client, []) is not None
    assert requests[-1]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert requests[-1]["max_completion_tokens"] == 4096
    assert "max_tokens" not in requests[-1]
    assert "temperature" not in requests[-1]

    # Rejected/unexecuted proposals leave this False, so they keep thinking.
    assert runner._continuous_pointer_gesture_progress is False
    assert runner._chat(client, []) is not None
    assert requests[-1]["extra_body"] == {"thinking": {"type": "enabled"}}

    # This flag is set only after a comparable post-action pixel change.
    runner._continuous_pointer_gesture_progress = True
    assert runner._chat(client, []) is not None
    assert requests[-1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert requests[-1]["max_tokens"] == 4096
    assert requests[-1]["temperature"] == 0.3
    assert "max_completion_tokens" not in requests[-1]


def test_mimo_thinking_routing_is_not_a_paint_or_cat_special_case(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    values = {
        ("api",): {
            "provider": "mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5",
            "thinking": False,
        },
        ("controller", "native_visual_thinking"): True,
    }
    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: values.get(tuple(path), default),
    )
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)

    handwriting = AgentRunner("handwrite my signature on the visible form")
    handwriting._visual_closed_loop_active = True
    assert handwriting._native_visual_thinking_mode_for_decision("mimo-v2.5") == "enabled"

    ordinary = AgentRunner("open Notepad and type today's date")
    ordinary._visual_closed_loop_active = True
    assert ordinary._native_visual_thinking_mode_for_decision("mimo-v2.5") is None

    values[("api",)] = {**values[("api",)], "model": "mimo-v2.5-pro"}
    assert handwriting._native_visual_thinking_mode_for_decision("mimo-v2.5-pro") is None

    values[("api",)] = {**values[("api",)], "model": "mimo-v2.5"}
    values[("controller", "native_visual_thinking")] = False
    assert handwriting._native_visual_thinking_mode_for_decision("mimo-v2.5") is None


def test_native_draw_summary_is_scoped_to_latest_uninterrupted_surface_sequence():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("create paths on each requested work surface")
    committed = json.dumps({
        "ok": True,
        "native_action_executed": True,
        "visual_change_detected": True,
    })
    runner._tool_history = [
        ("draw_strokes", {"frame_strokes": [[[200, 400], [300, 500]]]}, committed),
    ]
    assert "1 pixel-changing draw transaction" in runner._native_transaction_summary()

    # A later visual mutation can create/switch/clear a surface even under the
    # same HWND, so an older composition must stop constraining current pixels.
    runner._tool_history.append(("hotkey", {"keys": ["ctrl", "n"]}, '{"ok":true}'))
    assert "pixel-changing draw transaction" not in runner._native_transaction_summary()

    runner._tool_history.append(
        ("draw_strokes", {"frame_strokes": [[[400, 500], [500, 600]]]}, committed)
    )
    summary = runner._native_transaction_summary()
    assert "1 pixel-changing draw transaction" in summary
    assert "at most one" not in summary


def test_model_turn_executes_only_one_visual_transaction(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the first button, then inspect the result")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 1000, 500)
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: SimpleNamespace(
            left=10, top=20, width=1000, height=500,
            clamp_to_screen=lambda: SimpleNamespace(left=10, top=20, width=1000, height=500),
        ),
    )
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult(json.dumps({"ok": True, "verified": False}))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
        _tool_call("two", "hotkey", {"keys": ["Enter"]}),
    ])

    assert [name for name, _args in dispatched] == ["click"]
    tool_messages = [message for message in runner.messages if message.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[1]["content"])["visual_turn_barrier"] is True
    assert feedback and feedback[0]["action"] == "click"


def test_observe_image_is_injected_after_all_tool_results(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("inspect the screen, then click only after seeing it")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult("observed", is_observe=True, image_b64="fresh-frame")

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_observe_screenshot",
        lambda image_b64="": injected.append((image_b64, list(runner.messages))) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "observe", {}),
        _tool_call("two", "click", {"x": 100, "y": 120}),
    ])

    assert [name for name, _args in dispatched] == ["observe"]
    assert injected and injected[0][0] == "fresh-frame"
    roles_at_injection = [message.get("role") for message in injected[0][1]]
    assert roles_at_injection[-2:] == ["tool", "tool"]
    second_payload = json.loads(injected[0][1][-1]["content"])
    assert second_payload["visual_turn_barrier"] is True


def test_terminal_visual_action_injects_post_frame_before_return(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the requested control")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 1000, 500)
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: SimpleNamespace(
            left=10, top=20, width=1000, height=500,
            clamp_to_screen=lambda: SimpleNamespace(left=10, top=20, width=1000, height=500),
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult('{"ok": true}', terminal="done"),
    )
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    terminal = runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
    ])

    assert terminal == "done"
    assert feedback and feedback[0]["phase"] == "post_action"
    tool_payload = json.loads(next(
        message["content"] for message in runner.messages
        if message.get("role") == "tool"
    ))
    assert tool_payload["needs_observe"] is False
    assert tool_payload["native_post_action_frame_pending"] is True
    assert tool_payload["native_action_executed"] is True
    assert "Do not call observe" in tool_payload["fast_path_advice"]
    recent_tool, recent_evidence = runner._recent_results[-1]
    assert recent_tool == "click"
    assert json.loads(recent_evidence)["native_action_executed"] is True


def test_hidden_local_recovery_epoch_change_returns_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the target in the current browser")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    dispatched = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *args: dispatched.append(args)
        or ToolResult('{"ok": false, "blocked": true}'),
    )

    def hidden_recovery(**_kwargs):
        runner.session._screen_epoch += 1
        return None

    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        hidden_recovery,
    )
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "execute_subgoal", {"subgoal": "click target"}),
    ])

    assert dispatched
    assert feedback
    assert feedback[0]["phase"] == "post_action"
    assert feedback[0]["action"] == "execute_subgoal:local_recovery"


def test_low_level_action_requires_fresh_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the visible button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 3
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ungrounded action executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"x": 100, "y": 120})])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_grounding_required"] is True


@pytest.mark.usefixtures("authoritative_native_vision")
def test_done_judge_receives_fresh_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content='{"done": true, "reason": "visible result"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    runner = AgentRunner("open the requested settings page")
    runner.result_text = "done"
    runner.messages = []
    runner._fresh_done_image_b64 = "fresh-frame"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setitem(runner.session.__dict__, "hwnd", 3)

    assert runner._call_done_judge(client, "window", "click", "evidence") is True
    user_content = captured["messages"][1]["content"]
    assert isinstance(user_content, list)
    image = next(part for part in user_content if part.get("type") == "image_url")
    assert image["image_url"]["url"].endswith("fresh-frame")


def test_native_done_judge_excludes_self_claim_and_action_geometry(monkeypatch):
    from winpilot.agent import done_verifier as done_mod
    from winpilot.agent.loop import AgentRunner

    monkeypatch.setattr(done_mod, "model_supports_vision", lambda _model: True)
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=(
                '{"done": false, "reason": "latest pixels are blank and ambiguous"}'
            ))
        )])

    task = "Draw a polished cat in the visible canvas and schedule a reminder for later."
    runner = AgentRunner(task)
    runner.result_text = "CONFIDENT_FALSE_CAT_CLAIM: the cat is beautiful and complete"
    runner._fresh_done_image_b64 = "blank-degraded-frame"
    runner._steps_log = [
        'draw_strokes({"frame_strokes": [[[101, 202], [303, 404]]], '
        '"physical_x": 1234, "physical_y": 567})'
    ]
    runner._recent_results = [
        (
            "draw_strokes",
            '{"ok": true, "frame_strokes": [[[101, 202], [303, 404]]], '
            '"semantic_hint": "CONFIDENT_FALSE_CAT_CLAIM"}',
        ),
        ("run_shell", "CONFIDENT_FALSE_CAT_CLAIM from an irrelevant echo command"),
        (
            "schedule_task",
            '{"ok": true, "enabled": true, "task": "SCHEDULE_CREATED_OK_7", '
            '"frame_strokes": [[[1, 2], [3, 4]]], "x": 999}',
        ),
    ]
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    assert runner._call_done_judge(
        client,
        "STRUCTURED_SCREEN_FALSE_CAT_HINT",
        "physical click x=1234 y=567 then draw_strokes",
        (
            '- run_action_batch: {"ok": true, "frame_strokes": [[[1,2],[3,4]]]}\n'
            "- deterministic_saved_file: exact requested artifact exists"
        ),
    ) is False

    content = captured["messages"][1]["content"]
    prompt = next(part["text"] for part in content if part.get("type") == "text")
    assert task in prompt
    assert "SCHEDULE_CREATED_OK_7" in prompt
    assert "deterministic_saved_file" in prompt
    assert "CONFIDENT_FALSE_CAT_CLAIM" not in prompt
    assert "STRUCTURED_SCREEN_FALSE_CAT_HINT" not in prompt
    assert "frame_strokes" not in prompt.casefold()
    assert "1234" not in prompt
    assert "999" not in prompt
    assert "截图若为空白、明显劣化、残缺" in captured["messages"][0]["content"]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_done_judge_receives_bounded_cross_window_post_action_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"done": true, "reason": "both windows verified"}')
        )])

    runner = AgentRunner("type the date in Notepad, then draw a cat in Paint")
    runner.result_text = "done"
    runner.session.hwnd = 22
    runner._fresh_done_image_b64 = "paint-current"
    runner._historical_visual_checkpoints = [{
        "image_b64": "notepad-after-type",
        "hwnd": 11,
        "title": "Untitled - Notepad",
        "action": "type_text",
        "frame_id": 2,
    }]
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    assert runner._call_done_judge(client, "", "", "") is True
    content = captured["messages"][1]["content"]
    urls = [
        part["image_url"]["url"]
        for part in content
        if part.get("type") == "image_url"
    ]
    assert urls == [
        "data:image/jpeg;base64,paint-current",
        "data:image/jpeg;base64,notepad-after-type",
    ]
    labels = "\n".join(
        part.get("text", "") for part in content if part.get("type") == "text"
    )
    assert "Untitled - Notepad" in labels
    assert "after_action" not in labels
    assert "hwnd=" not in labels
    assert "frame_id=" not in labels


def test_window_switch_checkpoints_only_fresh_post_action_frame():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("type in one app and continue in another")
    runner.session.hwnd = 11
    runner.session._screen_epoch = 4
    runner.session.snapshot = SimpleNamespace(title="Untitled - Notepad")
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 4
    runner._visual_frame_id = 3
    runner._last_visual_feedback_b64 = "notepad-frame"
    runner._last_visual_feedback_action = "type_text"

    runner._checkpoint_visual_frame_before_window_switch("open_app")

    assert runner._historical_visual_checkpoints == [{
        "image_b64": "notepad-frame",
        "hwnd": 11,
        "title": "Untitled - Notepad",
        "action": "type_text",
        "frame_id": 3,
        "screen_epoch": 4,
    }]

def test_done_rejections_rejudge_each_new_frame_without_a_fixed_repair_cap(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent import done_verifier as done_mod

    monkeypatch.setattr(done_mod, "model_supports_vision", lambda _model: True)
    verdicts = iter([
        '{"done": false, "reason": "first frame is incomplete"}',
        '{"done": false, "reason": "second frame still misses the goal"}',
        '{"done": true, "reason": "third frame now satisfies the original task"}',
    ])
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=next(verdicts)))
        ])

    runner = AgentRunner("create a polished illustration in the visible canvas")
    runner.result_text = "done"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    outcomes = []
    for frame in ("fresh-frame-one", "fresh-frame-two", "fresh-frame-three"):
        runner._fresh_done_image_b64 = frame
        outcomes.append(runner._call_done_judge(
            client, "", "draw_strokes", "fresh action evidence"
        ))

    assert outcomes == [False, False, True]
    assert runner._done_rejects == 2
    attached_urls = [
        next(
            part["image_url"]["url"]
            for part in request["messages"][1]["content"]
            if part.get("type") == "image_url"
        )
        for request in requests
    ]
    assert attached_urls[0].endswith("fresh-frame-one")
    assert attached_urls[1].endswith("fresh-frame-two")
    assert attached_urls[2].endswith("fresh-frame-three")
    guidance = "\n".join(
        str(message.get("content") or "") for message in runner.messages
    ).casefold()
    assert "single safe additive repair" not in guidance
    assert "one incremental execute_subgoal repair" not in guidance


def test_normalized_frame_coordinates_map_inside_exact_window_bounds(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click and drag using the current real frame")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (-100, 50, 1001, 501)
    runner._visual_submitted_size = (1001, 501)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(-100, 50, 1001, 501)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)

    top_left = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": 0, "frame_y": 0},
    )
    bottom_right = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": 1000, "frame_y": 1000},
    )
    repaired_numeric_string = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": "535", "frame_y": "203]"},
    )
    drag = runner._map_normalized_frame_coordinates("drag", {
        "frame_x1": 0,
        "frame_y1": 0,
        "frame_x2": 1000,
        "frame_y2": 1000,
        "frame_points": [[500, 500]],
    })
    constrained_drag = runner._map_normalized_frame_coordinates("drag", {
        "frame_x1": 250,
        "frame_y1": 250,
        "frame_x2": 750,
        "frame_y2": 750,
        "constraint_frame_region": [200, 200, 800, 800],
    })
    image_click = runner._map_normalized_frame_coordinates(
        "click", {"image_x": 500, "image_y": 250},
    )
    image_drag = runner._map_normalized_frame_coordinates("drag", {
        "image_x1": 0,
        "image_y1": 0,
        "image_x2": 1000,
        "image_y2": 500,
        "image_points": [[500, 250]],
    })

    assert (top_left["x"], top_left["y"]) == (-100, 50)
    assert (bottom_right["x"], bottom_right["y"]) == (900, 550)
    assert repaired_numeric_string["source_frame_coordinates"] == {
        "frame_x": "535",
        "frame_y": 203.0,
    }
    assert (drag["x1"], drag["y1"], drag["x2"], drag["y2"]) == (-100, 50, 900, 550)
    assert drag["points"] == [[400, 300]]
    assert drag["coordinate_space"] == "current_frame_normalized_0_1000"
    assert drag["visual_frame_id"] == 9
    assert constrained_drag["_constraint_region"] == {
        "x": 100,
        "y": 150,
        "width": 600,
        "height": 300,
    }
    assert constrained_drag["source_frame_coordinates"][
        "constraint_frame_region"
    ] == {"x": 200.0, "y": 200.0, "width": 600.0, "height": 600.0}
    assert (image_click["x"], image_click["y"]) == (400, 300)
    assert image_click["source_image_coordinates"] == {
        "image_x": 500.0,
        "image_y": 250.0,
        "submitted_image_size": [1001, 501],
    }
    assert (image_drag["x1"], image_drag["y1"], image_drag["x2"], image_drag["y2"]) == (
        -100, 50, 900, 550,
    )
    assert image_drag["points"] == [[400, 300]]
    assert image_drag["source_image_coordinates"]["image_points"] == [[500.0, 250.0]]
    with pytest.raises(ValueError, match="exact submitted image bounds"):
        runner._map_normalized_frame_coordinates(
            "click", {"image_x": 500, "image_y": 501},
        )
    with pytest.raises(NativeFrameCoordinateError) as outside:
        runner._map_normalized_frame_coordinates("click", {
            "frame_x": 900,
            "frame_y": 500,
            "constraint_frame_region": [100, 100, 600, 900],
        })
    assert outside.value.metadata["action_constraint_failed"] is True


def test_held_pointer_inherits_one_normalized_hard_region_across_frames(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("finish the visible interaction within its declared area")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner.session._pointer_hold_button = "left"
    runner.session._pointer_hold_constraint_frame_region = {
        "x": 200.0, "y": 100.0, "width": 600.0, "height": 800.0,
    }
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(100, 200, 1000, 600)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    monkeypatch.setattr(
        "winpilot.agent.desktop_tools.executor.agent_holds_mouse_button",
        lambda _button="left": True,
    )

    move = runner._map_normalized_frame_coordinates(
        "move_mouse", {"frame_x": 500, "frame_y": 500},
    )
    release = runner._map_normalized_frame_coordinates("mouse_up", {})

    assert move["source_frame_coordinates"]["constraint_frame_region"] == {
        "x": 200.0, "y": 100.0, "width": 600.0, "height": 800.0,
    }
    assert "_constraint_region" in release
    assert "x" not in release and "y" not in release
    with pytest.raises(NativeFrameCoordinateError, match="leaves the declared"):
        runner._map_normalized_frame_coordinates(
            "move_mouse", {"frame_x": 900, "frame_y": 500},
        )


def test_visual_semantic_grounding_assist_is_disabled_by_default(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception import fusion
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        fusion,
        "observe",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled semantic grounding must not invoke UIA/OCR fusion"
        ),
    )
    runner = AgentRunner("activate the visible target control")

    line, grounded, meta = runner._visual_semantic_grounding_assist(
        hwnd=77,
        frame_region=Region(0, 0, 1000, 600),
        force_refresh=True,
    )

    assert line == ""
    assert grounded is None
    assert meta == {"enabled": False}


def test_visual_semantic_grounding_assist_exposes_fresh_generic_anchor(
    monkeypatch,
    semantic_grounding_enabled,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception import fusion
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    snapshot = fusion.Snapshot(
        hwnd=77,
        title="Unknown custom application",
        region=Region(0, 0, 1000, 600),
        elements=[
            UIElement(
                elem_id=4,
                source="ocr",
                role="text",
                text="Target control",
                cx=450,
                cy=180,
                left=400,
                top=160,
                width=100,
                height=40,
                confidence=0.95,
            ),
        ],
    )
    monkeypatch.setattr(fusion, "observe", lambda *_args, **_kwargs: snapshot)
    runner = AgentRunner("activate the visible target control")
    runner.session._fast_screen_status = lambda: {}

    line, grounded, meta = runner._visual_semantic_grounding_assist(
        hwnd=77,
        frame_region=Region(0, 0, 1000, 600),
        force_refresh=True,
    )

    assert grounded is snapshot
    assert runner.session.snapshot is snapshot
    assert meta["source"] == "fresh_pixel_text_fusion"
    assert meta["candidate_count"] == 1
    assert '"label":"Target control"' in line
    assert '"center_frame":[450,301]' in line


def test_pixel_graphical_grounding_never_invents_icon_semantics() -> None:
    import base64

    import cv2
    import numpy as np

    from winpilot.agent.loop import AgentRunner

    image = np.full((500, 800, 3), 248, dtype=np.uint8)
    for y in (150, 300, 430):
        cv2.circle(image, (620, y), 10, (80, 80, 80), 2)
        cv2.line(image, (615, y), (625, y), (80, 80, 80), 2)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    runner = AgentRunner("operate every matching visible control")

    line, candidates = runner._visual_graphical_grounding_assist(
        base64.b64encode(encoded.tobytes()).decode(),
    )

    assert candidates
    assert "unlabeled_graphical_candidate" in line
    assert "no inferred application meaning" in line
    assert all("label" not in candidate for candidate in candidates)


def test_semantic_grounding_recovery_keeps_unlabeled_affordance_near_failed_roi(
    monkeypatch,
    semantic_grounding_enabled,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception import fusion, reducer
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    elements = [
        UIElement(
            elem_id=index,
            source="uia",
            role="link",
            text=(f"ranked control {index}" if index < 14 else "javascript:;"),
            cx=(50 + index * 20 if index < 14 else 420),
            cy=(60 if index < 14 else 310),
            left=(40 + index * 20 if index < 14 else 400),
            top=(50 if index < 14 else 300),
            width=40,
            height=20,
            confidence=1.0,
        )
        for index in range(1, 15)
    ]
    snapshot = fusion.Snapshot(
        hwnd=88,
        title="Arbitrary application",
        region=Region(0, 0, 1000, 600),
        elements=elements,
    )
    ranked = [
        {
            "elem_id": element.elem_id,
            "role": element.role,
            "label": element.text,
            "bbox": [element.left, element.top, element.width, element.height],
            "center": [element.cx, element.cy],
            "source": element.source,
        }
        for element in elements
    ]
    monkeypatch.setattr(fusion, "observe", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(
        reducer,
        "reduce_snapshot",
        lambda *_args, **_kwargs: {"top_candidates": ranked},
    )
    runner = AgentRunner("operate the visible control")
    runner.session._fast_screen_status = lambda: {}

    line, grounded, meta = runner._visual_semantic_grounding_assist(
        hwnd=88,
        frame_region=Region(0, 0, 1000, 600),
        force_refresh=True,
        focus_frame_roi=(380, 470, 480, 570),
    )

    assert grounded is snapshot
    assert meta["focus_candidate_count"] == 1
    assert '"element_id":14' in line
    assert '"near_failed_action":true' in line
    assert "jittering the old coordinates" in line


def test_direct_click_routes_semantic_anchor_through_controller():
    from winpilot.agent.tools import ToolSession

    captured = {}

    class Controller:
        def execute(self, actions, **kwargs):
            captured["actions"] = actions
            captured["kwargs"] = kwargs
            return {"ok": True, "verified": False, "resolved": "semantic"}

    session = ToolSession(task="activate an arbitrary visible control")
    session._fast_controller = lambda: Controller()
    try:
        payload = json.loads(session._t_click({
            "label": "Target control",
            "expected_region": "top",
            "visual_servo": True,
        }).text)
    finally:
        session.close()

    assert payload["ok"] is True
    assert captured["actions"] == [{
        "action": "click",
        "label": "Target control",
        "expected_region": "top",
        "visual_servo": True,
    }]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_first_native_frame_hides_only_redundant_observe_tool(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 600),
    )
    runner = AgentRunner("operate the visible application from its current pixels")
    runner._visual_closed_loop_active = True
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    tools = [
        {"type": "function", "function": {"name": "observe"}},
        {"type": "function", "function": {"name": "click"}},
        {"type": "function", "function": {"name": "done"}},
    ]

    assert [
        item["function"]["name"]
        for item in runner._model_tools_for_step(tools, 1)
    ] == ["click", "done"]
    assert runner._model_tools_for_step(tools, 2) is tools


def test_held_pointer_exposes_only_visual_continuation_or_release(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("steer the held object from each newly visible frame")
    monkeypatch.setattr(
        runner.session,
        "_pointer_hold_status",
        lambda: {"active": True, "button": "left"},
    )
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "observe", "click", "drag", "move_mouse", "mouse_up", "wait",
            "done", "blocked", "fail",
        )
    ]

    selected = [
        item["function"]["name"]
        for item in runner._model_tools_for_step(tools, 4)
    ]

    assert selected == ["mouse_up", "move_mouse", "wait", "blocked", "fail"]
    summary = runner._native_transaction_summary()
    assert "still holding the left mouse button" in summary
    assert "Decide release first" in summary
    assert "0 continuation move(s)" in summary


def test_held_pointer_summary_counts_only_current_gesture_moves(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("adapt a held pointer from fresh visual frames")
    monkeypatch.setattr(
        runner.session,
        "_pointer_hold_status",
        lambda: {"active": True, "button": "left"},
    )
    runner._tool_history = [
        ("move_mouse", {"x": 1, "y": 1}, "{}"),
        ("run_action_batch", {"actions": [
            {"action": "mouse_down", "x": 10, "y": 10},
            {"action": "move_mouse", "x": 20, "y": 20},
        ]}, "{}"),
        ("execute_subgoal", {"actions": [
            {"action": "move_mouse", "x": 30, "y": 30},
        ]}, "{}"),
    ]

    summary = runner._native_transaction_summary()

    assert "2 continuation move(s)" in summary
    assert "call mouse_up" in summary


def test_blocked_destructive_retry_temporarily_exposes_forward_tools_only(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("create continuous pointer output")
    monkeypatch.setattr(runner, "_continuous_pointer_output_requested", lambda: True)
    tools = [
        {"type": "function", "function": {"name": "observe"}},
        {"type": "function", "function": {"name": "hotkey"}},
        {"type": "function", "function": {"name": "draw_strokes"}},
        {"type": "function", "function": {"name": "done"}},
    ]
    runner._continuous_pointer_forward_only_correction = True

    assert [
        item["function"]["name"]
        for item in runner._model_tools_for_step(tools, 3)
    ] == ["observe", "draw_strokes", "done"]
    assert "Do not request another undo/reset/clear" in runner._native_transaction_summary()

    runner._continuous_pointer_done_repair_required = True
    assert [
        item["function"]["name"]
        for item in runner._model_tools_for_step(tools, 4)
    ] == ["observe", "draw_strokes"]
    assert "Completion repair required" in runner._native_transaction_summary()

    runner._record_continuous_pointer_progress(
        "draw_strokes",
        0.02,
        comparable=True,
    )
    assert runner._continuous_pointer_forward_only_correction is False
    assert runner._continuous_pointer_done_repair_required is False
    assert runner._model_tools_for_step(tools, 5) is tools


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_unwrapped_single_frame_polyline_is_normalized_without_retry(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create one continuous path in the visible editor")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 600),
    )
    source = [[100, 200], [300, 400], [500, 600]]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": source},
    )

    assert mapped["strokes"] == [[[200, 320], [400, 440], [600, 559]]]
    assert mapped["source_frame_coordinates"]["frame_strokes"] == source
    assert mapped["frame_stroke_preparation"]["unwrapped_single_polyline"] is True


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_flat_numeric_frame_paths_are_paired_without_model_retry(monkeypatch):
    from winpilot.agent.loop import (
        AgentRunner,
        _normalize_frame_stroke_list_structure,
        _normalize_stringified_frame_strokes,
    )
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create several continuous paths in the visible editor")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 600),
    )
    # Some providers emit each polyline as x1,y1,x2,y2,... even though the
    # schema requests [[x,y], ...]. Adjacent numeric pairs are deterministic,
    # so normalize them instead of discarding a valid GUI action.
    source = [
        [100, 400, 300, 500, 500, 600],
        [550, 450, 650, 550],
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": source},
    )

    assert [len(stroke) for stroke in mapped["strokes"]] == [3, 2]
    assert mapped["source_frame_coordinates"]["frame_strokes"] == source
    assert mapped["frame_stroke_preparation"]["flat_numeric_paths_normalized"] == 2

    normalized, evidence = _normalize_frame_stroke_list_structure(
        [100, 400, 300, 500],
    )
    assert normalized == [[[100, 400], [300, 500]]]
    assert evidence["flat_numeric_paths_normalized"] == 1

    stringified, stringified_evidence = _normalize_stringified_frame_strokes(
        "[[100,400,300,500],[500,600,700,700]]",
    )
    assert stringified == [
        [[100, 400], [300, 500]],
        [[500, 600], [700, 700]],
    ]
    assert stringified_evidence["flat_numeric_paths_normalized"] == 2

    with pytest.raises(ValueError, match="ambiguous nesting"):
        _normalize_frame_stroke_list_structure([[100, 400, 300]])


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_stringified_frame_strokes_recover_only_complete_numeric_paths(monkeypatch):
    from winpilot.agent.loop import AgentRunner, _normalize_stringified_frame_strokes
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create several continuous paths in the visible editor")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 600),
    )
    # The second path has one redundant opening bracket and the outer closing
    # bracket is absent, matching a real function-calling provider response.
    encoded = "[[[100,400],[300,500]], [[[500,600],[700,700]]]"

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": encoded},
    )

    assert len(mapped["strokes"]) == 2
    assert mapped["source_frame_coordinates"]["frame_strokes"] == [
        [[100, 400], [300, 500]],
        [[500, 600], [700, 700]],
    ]
    preparation = mapped["frame_stroke_preparation"]
    assert preparation["stringified_array_normalized"] is True
    assert preparation["missing_closing_brackets_repaired"] == 1
    assert preparation["stroke_count_recovered"] == 2

    trailing_extra = "[[[100,400],[300,500]],[[500,600],[700,700]]]]"
    repaired, trailing_evidence = _normalize_stringified_frame_strokes(trailing_extra)
    assert repaired == [
        [[100, 400], [300, 500]],
        [[500, 600], [700, 700]],
    ]
    assert trailing_evidence["extra_closing_brackets_removed"] == 1

    with pytest.raises(ValueError, match="non-numeric"):
        _normalize_stringified_frame_strokes('[[[1,2],[3,"click"]]]')


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_redundant_array_nesting_is_flattened_without_model_retry(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("add two continuous details in the visible editor")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (100, 200, 1000, 600)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 600),
    )
    nested = [
        [[100, 400], [300, 500]],
        [[[500, 600], [700, 700]]],
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": nested},
    )

    assert len(mapped["strokes"]) == 2
    assert mapped["source_frame_coordinates"]["frame_strokes"] == nested
    assert mapped["frame_stroke_preparation"]["redundant_list_nesting_normalized"] is True


def test_native_frame_stroke_budget_accepts_rich_composition_and_reports_real_overflow():
    from winpilot.agent.loop import _normalize_frame_stroke_list_structure

    rich = [
        [[200 + index, 400], [210 + index, 410]]
        for index in range(17)
    ]

    normalized, evidence = _normalize_frame_stroke_list_structure(rich)

    assert normalized == rich
    assert evidence["unwrapped_single_polyline"] is False

    too_many = [
        [[200 + index, 400], [210 + index, 410]]
        for index in range(25)
    ]
    with pytest.raises(ValueError, match=r"too many independent paths: count=25, limit=24"):
        _normalize_frame_stroke_list_structure(too_many)


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_frame_strokes_must_stay_in_grounded_work_surface(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create continuous paths on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )

    mapped = runner._map_normalized_frame_coordinates("draw_strokes", {
        "frame_strokes": [[[200, 400], [400, 500], [800, 800], [980, 900]]],
    })
    assert mapped["strokes"][0][0] == [681, 442]
    assert mapped["source_frame_coordinates"]["frame_strokes"][0][0] == [200, 400]

    with pytest.raises(NativeFrameCoordinateError) as start_error:
        runner._map_normalized_frame_coordinates("draw_strokes", {
            "frame_strokes": [[[450, 350], [500, 500], [600, 600]]],
        })
    assert start_error.value.metadata["start_inside"] is False
    assert start_error.value.metadata["work_surface_frame_bounds"] == [125, 359, 984, 932]

    with pytest.raises(NativeFrameCoordinateError) as ratio_error:
        runner._map_normalized_frame_coordinates("draw_strokes", {
            "frame_strokes": [[
                [200, 400], [300, 500], [400, 600], [500, 700],
                [10, 10], [20, 20],
            ]],
        })
    assert ratio_error.value.metadata["start_inside"] is True
    assert ratio_error.value.metadata["inside_point_count"] == 4
    assert ratio_error.value.metadata["inside_point_ratio"] == pytest.approx(4 / 6)


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_long_path_accepts_only_near_detector_edge_drift(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create a continuous path on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    path = [
        [200 + index * 25, 400 + (index % 4) * 70]
        for index in range(18)
    ] + [
        [650, 352], [700, 353], [750, 354], [800, 355],
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": [path]},
    )

    evidence = mapped["frame_stroke_preparation"]
    assert evidence["soft_edge_tolerance_applied"] is True
    assert evidence["soft_edge_tolerance_strokes"] == [0]
    assert evidence["soft_edge_margin"] == 8.0
    assert evidence["input_point_count"] == 22


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_surface_safe_inset_rejects_scrollbar_edge_strokes(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create continuous paths without dragging window chrome")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (149, 383, 960, 908)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )

    with pytest.raises(NativeFrameCoordinateError) as edge:
        runner._map_normalized_frame_coordinates(
            "draw_strokes",
            {
                "frame_strokes": [[
                    [490, 930], [450, 930], [410, 930], [370, 930],
                    [330, 930], [290, 930], [250, 930], [210, 930],
                    [170, 930], [130, 930],
                ]],
            },
        )

    assert edge.value.metadata["start_inside"] is False
    assert edge.value.metadata["work_surface_frame_bounds"] == [149, 383, 960, 908]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_multi_path_composition_rigidly_translates_tiny_safe_edge_miss(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create a multi-path composition on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (149, 383, 960, 908)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    source = [
        [[350, 500], [380, 420], [480, 420], [550, 500]],
        [[365, 420], [350, 370], [400, 410], [365, 420]],
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": source},
    )

    evidence = mapped["frame_stroke_preparation"]
    assert evidence["policy"] == "whole_composition_rigid_translation"
    assert evidence["applied"] is True
    assert evidence["maximum_edge_overflow"] == 13.0
    assert evidence["uniform_scale"] == 1.0
    assert evidence["translation"] == [0.0, 13.0]
    assert evidence["mapped_bbox"][1] == 383.0
    assert mapped["source_frame_coordinates"]["frame_strokes"] == source


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_multi_path_composition_uniformly_fits_unique_work_surface(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create several continuous paths on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    source = [
        [[450, 420], [250, 200], [0, 0]],
        [[450, 420], [700, 200], [1000, 0]],
        [[450, 420], [450, 700], [450, 1000]],
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": source},
    )

    evidence = mapped["frame_stroke_preparation"]
    assert evidence["policy"] == "whole_composition_uniform_affine_fit"
    assert evidence["applied"] is True
    assert evidence["shape_preserving"] is True
    assert evidence["point_clipping_used"] is False
    assert evidence["outside_point_ratio"] >= 0.20
    assert evidence["work_surface_frame_bounds"] == [125, 359, 984, 932]
    assert evidence["uniform_scale"] < 1.0
    assert mapped["source_frame_coordinates"]["frame_strokes"] == source
    assert evidence["execution_point_count"] == 9
    mapped_left, mapped_top, mapped_right, mapped_bottom = evidence["mapped_bbox"]
    assert 125 <= mapped_left <= mapped_right <= 984
    assert 359 <= mapped_top <= mapped_bottom <= 932


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_composition_fit_translates_small_overflow_but_rejects_unsafe_sparse_paths(monkeypatch):
    from winpilot.agent.loop import AgentRunner, NativeFrameCoordinateError
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create precise paths on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )

    with pytest.raises(NativeFrameCoordinateError) as single_path:
        runner._map_normalized_frame_coordinates("draw_strokes", {
            "frame_strokes": [[[450, 420], [250, 200], [0, 0], [450, 700]]],
        })
    single_fit = single_path.value.metadata["composition_affine_fit"]
    assert single_fit["applied"] is False
    assert single_fit["eligibility"]["multi_path_composition"] is False

    source = [
        [[200, 400], [300, 500], [400, 600], [500, 700]],
        [[600, 700], [700, 800], [800, 900], [990, 930]],
    ]
    translated = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": source},
    )
    minor_fit = translated["frame_stroke_preparation"]
    assert minor_fit["applied"] is True
    assert minor_fit["policy"] == "whole_composition_rigid_translation"
    assert minor_fit["uniform_scale"] == 1.0
    assert minor_fit["outside_point_count"] == 1
    assert minor_fit["eligibility"]["substantial_outside_points"] is False
    assert minor_fit["eligibility"]["small_rigid_translation"] is True
    assert translated["source_frame_coordinates"]["frame_strokes"] == source

    # A coherent composition can have only a small fraction of points outside
    # while two authored parts (for example two lower contours) exceed the same
    # bounded edge. Preserve the whole shape with one uniform fit instead of
    # rejecting it just because the outside-point ratio is below 20%.
    bounded_source = [
        [[500, 380], [480, 600], [470, 750], [500, 850], [530, 940], [550, 1000]],
        [[600, 380], [620, 600], [630, 750], [600, 850], [570, 940], [550, 1000]],
        *[
            [[300 + index * 40, 450], [320 + index * 40, 500], [340 + index * 40, 550]]
            for index in range(8)
        ],
    ]
    bounded = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": bounded_source},
    )
    bounded_fit = bounded["frame_stroke_preparation"]
    assert bounded_fit["applied"] is True
    assert bounded_fit["policy"] == "whole_composition_uniform_affine_fit"
    assert bounded_fit["outside_point_ratio"] < 0.20
    assert bounded_fit["outside_stroke_count"] == 2
    assert bounded_fit["eligibility"]["bounded_multi_stroke_overflow"] is True
    assert bounded_fit["uniform_scale"] < 1.0
    mapped_left, mapped_top, mapped_right, mapped_bottom = bounded_fit["mapped_bbox"]
    assert 125 <= mapped_left <= mapped_right <= 984
    assert 359 <= mapped_top <= mapped_bottom <= 932

    with pytest.raises(NativeFrameCoordinateError) as larger_overflow:
        runner._map_normalized_frame_coordinates("draw_strokes", {
            "frame_strokes": [
                [[200, 400], [300, 500], [400, 600], [500, 700]],
                [[600, 700], [700, 800], [800, 900], [900, 950]],
            ],
        })
    larger_fit = larger_overflow.value.metadata["composition_affine_fit"]
    assert larger_fit["applied"] is False
    assert larger_fit["maximum_edge_overflow"] == 18.0
    assert larger_fit["eligibility"]["repairable_overflow"] is False


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_dense_collinear_frame_path_is_simplified_before_execution(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create a continuous path on the visible work surface")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 9
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    dense = [
        [200 + index * 20, 400 + index * 10]
        for index in range(30)
    ]

    mapped = runner._map_normalized_frame_coordinates(
        "draw_strokes",
        {"frame_strokes": [dense]},
    )

    assert len(mapped["strokes"][0]) == 2
    assert mapped["source_frame_coordinates"]["frame_strokes"] == [dense]
    evidence = mapped["frame_stroke_preparation"]
    assert evidence["simplification"] == "near_collinear_rdp"
    assert evidence["input_point_count"] == 30
    assert evidence["execution_point_count"] == 2
    assert evidence["points_simplified"] == 28


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_surface_containment_failure_returns_bounds_without_dispatch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create continuous paths on the visible work surface")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 4
    runner._last_visual_feedback_b64 = "synthetic-frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("surface-invalid coordinates reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("surface-invalid coordinates were executed")
        ),
    )
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unexecuted coordinate rejection entered failure recovery")
        ),
    )

    runner._handle_tool_calls([_tool_call("bad-surface", "draw_strokes", {
        "frame_strokes": [[[450, 350], [5, 5], [500, 500], [995, 995]]],
    })])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert payload["work_surface_containment_failed"] is True
    assert payload["work_surface_frame_bounds"] == [125, 359, 984, 932]
    assert payload["start_inside"] is False
    assert "reinterpret" in payload["fast_path_advice"]
    assert "failure_recovery" not in payload


@pytest.mark.usefixtures("authoritative_native_vision")
def test_repeated_unexecuted_coordinate_constraint_safely_stops_on_third_category_hit(
    monkeypatch,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("operate the visible work surface")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (480, 154, 1008, 720)
    runner._visual_frame_id = 4
    runner._last_visual_feedback_b64 = "synthetic-frame"
    runner._visual_work_surface_frame_bounds = (125, 359, 984, 932)
    runner._visual_work_surface_frame_id = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(480, 154, 1008, 720),
    )
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid coordinate contract reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid coordinate contract was executed")
        ),
    )
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexecuted constraint entered generic recovery")
        ),
    )

    attempts = [
        {"frame_strokes": [[[450, 350], [5, 5], [500, 500], [995, 995]]]},
        {"frame_strokes": []},
        {"frame_strokes": [[[1001, 500], [900, 500]]]},
    ]
    terminals = []
    payloads = []
    for index, args in enumerate(attempts, start=1):
        terminals.append(runner._handle_tool_calls([
            _tool_call(f"bad-{index}", "draw_strokes", args),
        ]))
        payloads.append(json.loads(runner._tool_history[-1][2]))

    assert terminals == [None, None, "reply"]
    assert [item["constraint_category"] for item in payloads] == [
        "frame_coordinate_invalid",
        "frame_coordinate_invalid",
        "frame_coordinate_invalid",
    ]
    assert [item["constraint_rejection_streak"] for item in payloads] == [1, 2, 3]
    assert payloads[0]["strategy_change_required"] is False
    assert payloads[1]["strategy_change_required"] is True
    assert payloads[2]["safe_stop"] is True
    assert "continuing confidently" in payloads[2]["error"]


def test_unexecuted_constraint_debt_resets_only_on_real_progress_or_new_user_input():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("operate the visible application")
    rejection = json.dumps({
        "ok": False,
        "verified": False,
        "blocked": True,
        "frame_coordinate_invalid": True,
        "error": "invalid frame coordinates",
    })

    runner._apply_constraint_rejection_guard(
        "click", ToolResult(rejection), dispatched=False,
    )
    runner._apply_constraint_rejection_guard(
        "draw_strokes", ToolResult(rejection), dispatched=False,
    )
    assert runner._constraint_rejection_streak == 2

    # An unrelated failure is not progress and cannot launder the debt.
    runner._apply_constraint_rejection_guard(
        "click", ToolResult('{"ok": false, "verified": false}'), dispatched=True,
    )
    assert runner._constraint_rejection_streak == 2

    runner._apply_constraint_rejection_guard(
        "click", ToolResult('{"ok": true, "verified": false}'), dispatched=True,
    )
    assert runner._constraint_rejection_streak == 0

    runner._apply_constraint_rejection_guard(
        "click", ToolResult(rejection), dispatched=False,
    )
    runner._apply_constraint_rejection_guard(
        "observe", ToolResult("fresh pixels", is_observe=True), dispatched=True,
    )
    assert runner._constraint_rejection_streak == 0

    runner._apply_constraint_rejection_guard(
        "click", ToolResult(rejection), dispatched=False,
    )
    runner.interject("use the newly visible target instead")
    runner._drain_interjections()
    assert runner._constraint_rejection_streak == 0


def test_constraint_category_ignores_fail_closed_result_after_a_child_executed():
    from winpilot.agent.loop import _unexecuted_constraint_category

    payload = json.dumps({
        "ok": False,
        "blocked": True,
        "work_surface_fail_closed": True,
        "results": [{"ok": True, "verified": True, "action": "click"}],
    })

    assert _unexecuted_constraint_category(payload) == ""


def test_controller_internal_fail_closed_never_claims_action_might_have_succeeded(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("create continuous paths on the visible work surface")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "work_surface_fail_closed": True,
            "error": "no requested action was executed",
        })),
    )
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("controller-internal fail-closed result entered failure recovery")
        ),
    )

    runner._handle_tool_calls([_tool_call("surface", "execute_subgoal", {
        "subgoal": "trace the supplied continuous path",
        "draw": {"shape": "line", "box": [200, 300, 800, 700]},
    })])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["work_surface_fail_closed"] is True
    assert "failure_recovery" not in payload


@pytest.mark.parametrize(
    "guard_data",
    [
        {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "error": "user owns another foreground window",
            "scope_guard": {
                "recovery_attempted": False,
                "focus_stolen": False,
            },
        },
        {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
            "action": "focus_window",
            "target_hwnd": 11,
            "foreground_hwnd": 22,
            "error": "cooperative input blocked physical focus",
        },
    ],
)
def test_cooperative_preexecution_guard_never_enters_failure_recovery(
    monkeypatch, guard_data,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("focus the remembered target without stealing user input")
    runner._visual_closed_loop_active = False
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    controller_payload = {
        "ok": False,
        "wrapped_by_fast_controller": True,
        "source_action_ok": False,
        "results": [{
            "action": "focus_window",
            "ok": False,
            "data": guard_data,
        }],
    }
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult(json.dumps(controller_payload)),
    )
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cooperative pre-execution guard entered failure recovery")
        ),
    )

    runner._handle_tool_calls([
        _tool_call("cooperative-focus", "focus_window", {"hwnd": 11}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["ok"] is False
    assert payload["constraint_rejection_guard"] is True
    assert payload["constraint_category"] == "cooperative_input_scope"
    assert payload["constraint_rejection_streak"] == 1
    assert "failure_recovery" not in payload


def test_real_focus_failure_still_enters_failure_recovery(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("focus the remembered target")
    runner._visual_closed_loop_active = False
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "action": "focus_window",
            "error": "SetForegroundWindow failed after a real focus attempt",
        })),
    )
    recovery_calls = []
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **kwargs: recovery_calls.append(kwargs) or None,
    )

    runner._handle_tool_calls([
        _tool_call("real-focus", "focus_window", {"hwnd": 11}),
    ])

    assert len(recovery_calls) == 1
    assert recovery_calls[0]["tool_name"] == "focus_window"


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("click", {"frame_x": 10}),
        ("click", {"frame_x": -1, "frame_y": 20}),
        ("click", {"frame_x": float("nan"), "frame_y": 20}),
        ("click", {"frame_x": 10, "frame_y": 20, "x": 10, "y": 20}),
        ("scroll", {"amount": -120}),
        ("drag", {"frame_x1": 1, "frame_y1": 2}),
        ("draw_strokes", {"frame_strokes": [[[1, 2]]]}),
        ("draw_strokes", {"normalized_strokes": [[[10, 20], [30, 40]]]}),
    ],
)
def test_invalid_frame_coordinates_fail_atomically(monkeypatch, tool_name, args):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("use the current real frame")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "frame"
    region = Region(10, 20, 800, 600)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    original = json.loads(json.dumps(args)) if tool_name != "click" or not any(
        isinstance(value, float) for value in args.values()
    ) else dict(args)

    with pytest.raises(ValueError):
        runner._map_normalized_frame_coordinates(tool_name, args)

    if not any(isinstance(value, float) and value != value for value in args.values()):
        assert args == original


def test_native_physical_coordinates_are_blocked_without_dispatch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the visible button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid physical coordinates reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("physical model coordinates executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"x": 100, "y": 120})])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert "rejected model-supplied physical" in payload["error"]


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("click", {"x": 100, "y": 120, "label": "Continue"}),
        (
            "run_action_batch",
            {"actions": [{"action": "click", "x": 100, "y": 120, "label": "Continue"}]},
        ),
    ],
)
def test_native_semantic_hint_cannot_bypass_physical_coordinate_rejection(
    monkeypatch, tool_name, tool_args,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the visible Continue button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("semantic physical-coordinate bypass reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("physical coordinates with a semantic hint executed")
        ),
    )

    runner._handle_tool_calls([_tool_call("bad", tool_name, tool_args)])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert "rejected model-supplied physical" in payload["error"]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_batch_coordinates_cannot_bypass_frame_mapping(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click once from the current grounded frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(100, 200, 1000, 500))
    calls = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: calls.append((name, args)) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(runner, "_inject_visual_frame", lambda **_kwargs: True)

    runner._handle_tool_calls([_tool_call("bad", "run_action_batch", {
        "actions": [{"action": "click", "x": 20, "y": 30}],
    })])
    bad = json.loads(runner._tool_history[-1][2])
    assert bad["frame_coordinate_invalid"] is True
    assert calls == []

    runner._handle_tool_calls([_tool_call("good", "run_action_batch", {
        "actions": [{"action": "click", "frame_x": 250, "frame_y": 400}],
    })])
    assert calls[0][0] == "run_action_batch"
    mapped = calls[0][1]["actions"][0]
    assert (mapped["x"], mapped["y"]) == (350, 400)
    assert mapped["coordinate_space"] == "current_frame_normalized_0_1000"


@pytest.mark.parametrize(
    "actions",
    [
        [
            {"action": "click", "frame_x": 100, "frame_y": 120},
            {
                "action": "drag",
                "frame_x1": 200,
                "frame_y1": 220,
                "frame_x2": 700,
                "frame_y2": 720,
            },
        ],
        [
            {"action": "click", "element_id": 7},
            {
                "tool": "click",
                "params": {"element_id": 8},
            },
        ],
    ],
    ids=["frame-coordinates", "two-element-ids"],
)
def test_native_batch_rejects_two_spatial_mutations_before_preflight_or_dispatch(
    monkeypatch, actions,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("act only from each new real frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("multi-spatial native batch reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("multi-spatial native batch executed any child action")
        ),
    )

    runner._handle_tool_calls([
        _tool_call("multi", "run_action_batch", {"actions": actions}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert payload["native_batch_spatial_limit"] is True
    assert "at most one spatial UI mutation" in payload["error"]
    assert "No batch action was executed" in payload["error"]
    assert "next real post-action frame" in payload["fast_path_advice"]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_batch_allows_one_spatial_mutation_plus_keyboard_input(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the field and type the exact text")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    region = Region(100, 200, 1000, 500)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    preflights = []
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *args: preflights.append(args) or ("", 0.0, "", region),
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: calls.append((name, args)) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(runner, "_inject_visual_frame", lambda **_kwargs: True)

    runner._handle_tool_calls([_tool_call("one", "run_action_batch", {
        "actions": [
            {
                "action": "click",
                "frame_x": 250,
                "frame_y": 400,
                "label": "Search field",
            },
            {"action": "type_text", "text": "exact query"},
            {"action": "hotkey", "keys": ["enter"]},
        ],
    })])

    assert len(preflights) == 1
    assert len(calls) == 1
    name, mapped_args = calls[0]
    assert name == "run_action_batch"
    click, typed, hotkey = mapped_args["actions"]
    assert (click["x"], click["y"]) == (350, 400)
    assert click["label"] == "Search field"
    assert click["coordinate_space"] == "current_frame_normalized_0_1000"
    assert typed == {"action": "type_text", "text": "exact query"}
    assert hotkey == {"action": "hotkey", "keys": ["enter"]}


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_batch_maps_balanced_pointer_transaction_from_one_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("perform one continuous pointer transaction")
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(100, 200, 1000, 500),
    )

    mapped = runner._map_normalized_frame_coordinates("run_action_batch", {
        "actions": [
            {"action": "mouse_down", "button": "left", "frame_x": 100, "frame_y": 200},
            {"action": "move_mouse", "frame_x": 500, "frame_y": 500},
            {"action": "mouse_up", "button": "left", "frame_x": 900, "frame_y": 800},
        ],
    })

    assert [(item["x"], item["y"]) for item in mapped["actions"]] == [
        (200, 300), (600, 450), (999, 599),
    ]


@pytest.mark.usefixtures("authoritative_native_vision")
def test_native_batch_allows_controller_rebound_semantic_setup_and_one_raw_gesture(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("use a visible tool, then drag the grounded object")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    region = Region(100, 200, 1000, 500)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    monkeypatch.setattr(runner, "_preflight_visual_frame", lambda *_args: ("", 0.0, "", region))
    calls = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: calls.append((name, args)) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(runner, "_inject_visual_frame", lambda **_kwargs: True)

    runner._handle_tool_calls([_tool_call("semantic-transaction", "run_action_batch", {
        "actions": [
            {"action": "select_color", "color": "red"},
            {"action": "click_element", "label": "Start", "role": "button"},
            {
                "action": "drag",
                "frame_x1": 200, "frame_y1": 220,
                "frame_x2": 700, "frame_y2": 720,
            },
            {
                "action": "wait_for_visual_change",
                "frame_region": {"x": 250, "y": 250, "width": 300, "height": 180},
                "required": False,
            },
        ],
    })])

    assert len(calls) == 1
    mapped = calls[0][1]["actions"]
    assert mapped[0] == {"action": "select_color", "color": "red"}
    assert mapped[1]["label"] == "Start"
    assert (mapped[2]["x1"], mapped[2]["y1"], mapped[2]["x2"], mapped[2]["y2"]) == (
        300, 310, 799, 559,
    )
    assert mapped[3]["action"] == "wait_for_visual_change"
    assert mapped[3]["region"] == {"x": 350, "y": 325, "width": 299, "height": 90}


def test_preflight_external_change_cancels_action_and_returns_new_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the still-visible target")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    capture_region = Region(10, 20, 800, 600)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("new-frame", 0.12, "", capture_region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("changed-frame action executed")),
    )
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: injected.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_frame_changed"] is True
    assert injected == [{
        "phase": "preflight",
        "action": "visual_preflight_changed",
        "image_b64": "new-frame",
        "frame_region": capture_region,
    }]


def test_grounding_that_becomes_stale_inside_preflight_blocks_keyboard_dispatch(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("type only while the visible target is still authoritative")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "real-frame" * 128
    freshness_checks = []

    def freshness() -> bool:
        freshness_checks.append(len(freshness_checks) + 1)
        # Initial admission and the coordinate-contract dry run both see the
        # frame as current; it becomes stale in the final preflight window.
        return len(freshness_checks) < 3

    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", freshness)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("keyboard action with stale visual grounding was dispatched")
        ),
    )

    runner._handle_tool_calls([
        _tool_call("stale-keyboard", "hotkey", {"keys": ["ctrl", "s"]}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_preflight_failed"] is True
    assert payload["action_executed"] is False
    assert "became stale before preflight" in payload["error"]
    assert freshness_checks == [1, 2, 3]


def test_preflight_external_change_atomically_cancels_normalized_action_batch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click one visible target and type in one grounded batch")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    capture_region = Region(10, 20, 800, 600)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("new-frame", 0.12, "", capture_region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("changed-frame batch executed partially")
        ),
    )
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: injected.append(kwargs) or True,
    )

    runner._handle_tool_calls([_tool_call("one", "run_action_batch", {
        "actions": [
            {"action": "click", "frame_x": 100, "frame_y": 120},
            {"action": "type_text", "text": "exact input"},
        ],
    })])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_frame_changed"] is True
    assert injected == [{
        "phase": "preflight",
        "action": "visual_preflight_changed",
        "image_b64": "new-frame",
        "frame_region": capture_region,
    }]


def test_preflight_replacement_frame_invalidates_old_element_and_observe_cache(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target only if it belongs to the current frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "old-frame" * 32
    region = Region(10, 20, 800, 600)
    stale = Snapshot(
        hwnd=11,
        title="Old UI",
        region=region,
        elements=[UIElement(
            elem_id=7,
            source="uia",
            role="button",
            text="Old target",
            left=100,
            top=120,
            width=80,
            height=30,
            cx=140,
            cy=135,
        )],
    )
    cache_key = (11, True, "normal", "full", (), 2)
    runner.session.snapshot = stale
    runner.session.store_observe_cache(cache_key, stale)
    runner.session._last_native_frame_capture = {
        "image_b64": runner._last_visual_feedback_b64,
        "hwnd": 11,
        "region": region,
    }
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("replacement-frame" * 16, 0.25, "", region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("stale element action executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"element_id": 7})])

    assert runner.session.snapshot is None
    assert runner.session._observe_cache is None
    assert runner.session._last_native_frame_capture is None
    assert runner.session.cached_observe(cache_key, 2.0) is None
    stale_click = runner.session._t_click({"element_id": 7})
    assert "observe" in stale_click.text


def test_preflight_ignores_unrelated_animation_when_grounded_target_roi_is_stable(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the stable target")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.18)
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio_in_normalized_roi",
        lambda *_args: 0.0,
    )
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == ("", 0.0, "")
    assert capture_region == Region(0, 0, 800, 600)
    event = events[-1][1]
    assert event["decision_scope"] == "action_target_roi"
    assert event["global_change_ratio"] == 0.18
    assert event["target_change_ratio"] == 0.0
    assert event["target_roi"][0] < 500 < event["target_roi"][2]
    assert event["target_roi"][1] < 500 < event["target_roi"][3]


@pytest.mark.parametrize(
    "frame_strokes",
    [
        [[100, 200, 300, 400], [500, 600, 700, 800]],
        "[[100,200,300,400],[500,600,700,800]]",
    ],
)
def test_preflight_draw_roi_normalizes_provider_flat_paths(frame_strokes):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw several visible paths")

    roi = runner._visual_preflight_target_roi(
        "draw_strokes", {"frame_strokes": frame_strokes},
    )

    assert roi == (40.0, 140.0, 760.0, 860.0)

def test_preflight_blocks_semantic_draw_when_whole_fresh_frame_changed(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("create a polished illustration in the visible canvas")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr(runner, "_visual_preflight_target_roi", lambda *_args: None)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.54)
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    frame, ratio, error, region = runner._preflight_visual_frame(
        "execute_subgoal",
        {
            "subgoal": "add a visible subject",
            "draws": [{"shape": "ellipse", "box": [200, 200, 300, 220]}],
        },
    )

    assert (frame, ratio, error) == ("new" * 128, 0.54, "")
    assert region == Region(0, 0, 800, 600)
    event = events[-1][1]
    assert event["changed"] is True
    assert event["decision_scope"] == "whole_frame"
    assert event["global_change_ratio"] == 0.54
    assert event["target_change_ratio"] is None


def test_preflight_cancels_small_global_change_when_grounded_target_roi_changed(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target only if its pixels are stable")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.0001)
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio_in_normalized_roi",
        lambda *_args: 0.20,
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == ("new" * 128, 0.20, "")
    assert capture_region == Region(0, 0, 800, 600)


def test_preflight_cancels_same_pixel_window_move_before_coordinate_mapping(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click only while the target window geometry is unchanged")
    runner.session.hwnd = 11
    runner._visual_frame_region = (0, 0, 800, 600)
    runner._last_visual_feedback_b64 = "same-pixels" * 32
    moved = Region(120, 80, 800, 600)
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: moved)
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: runner._last_visual_feedback_b64,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("geometry changes must cancel before pixel equality can pass")
        ),
    )
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == (runner._last_visual_feedback_b64, 1.0, "")
    assert capture_region == moved
    assert events[-1][1]["geometry_changed"] is True
    assert events[-1][1]["decision_scope"] == "window_geometry"


def test_preflight_injection_binds_image_to_exact_capture_region(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("inspect the replacement frame")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    captured = Region(120, 80, 800, 600)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("injection re-queried geometry instead of using capture metadata")
        ),
    )

    injected = runner._inject_visual_frame(
        phase="preflight",
        action="visual_preflight_changed",
        image_b64="replacement-frame",
        frame_region=captured,
    )

    assert injected is True
    assert runner._visual_frame_region == (120, 80, 800, 600)
    assert runner._last_visual_feedback_b64 == "replacement-frame"


def test_normalized_roi_diff_ignores_pixels_outside_target():
    import base64

    import cv2
    import numpy as np

    from winpilot.utils.image_capture import jpeg_diff_ratio_in_normalized_roi

    before = np.zeros((100, 100, 3), dtype=np.uint8)
    after = before.copy()
    after[:20, :20] = 255

    def encode(image) -> str:
        ok, payload = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        assert ok
        return base64.b64encode(payload.tobytes()).decode()

    before_b64, after_b64 = encode(before), encode(after)
    changed = jpeg_diff_ratio_in_normalized_roi(
        before_b64, after_b64, (0, 0, 250, 250),
    )
    stable = jpeg_diff_ratio_in_normalized_roi(
        before_b64, after_b64, (750, 750, 1000, 1000),
    )

    assert changed is not None and changed > 0.5
    assert stable == 0.0


def test_preflight_threshold_is_strict_and_reported(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target if the grounded frame is still current")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, region=None: "y" * 256,
    )
    ratios = iter([0.0025, 0.002501])
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: next(ratios))
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    unchanged_frame, unchanged_ratio, unchanged_error, unchanged_region = runner._preflight_visual_frame()
    changed_frame, changed_ratio, changed_error, changed_region = runner._preflight_visual_frame()

    assert (unchanged_frame, unchanged_ratio, unchanged_error) == ("", 0.0025, "")
    assert (changed_frame, changed_ratio, changed_error) == ("y" * 256, 0.002501, "")
    assert unchanged_region == Region(0, 0, 800, 600)
    assert changed_region == Region(0, 0, 800, 600)
    assert events[0][0] == "visual_preflight"
    assert events[0][1]["threshold"] == 0.0025
    assert events[0][1]["changed"] is False
    assert events[1][1]["changed"] is True


@pytest.mark.parametrize("style", ["full", "compact"])
def test_native_prompt_uses_runtime_frame_coordinates(style):
    from winpilot.agent.prompts import system_prompt

    prompt = system_prompt(style=style, main_model_has_vision=True)

    assert "frame_*" in prompt
    assert "0..1000" in prompt
    assert "never" in prompt.casefold()
    assert "one direct typed action per visual turn" in prompt
    assert "inspect the next frame" in prompt
    assert "run_action_batch" not in prompt
    assert "execute_subgoal" not in prompt


def test_native_visual_context_is_bounded_and_removes_complete_tool_history(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the visible application until the requested state is clear")
    runner._visual_closed_loop_active = True
    runner.messages = [
        {"role": "system", "content": "stable system policy"},
        {"role": "user", "content": runner.task},
        {"role": "user", "content": "[static guidance] stay in the bound window"},
    ]
    runner._freeze_native_visual_static_context()
    runner.session.plan = SimpleNamespace(
        render=lambda: "1. inspect current state [done] 2. reach requested state [pending]"
    )
    runner.interject("use the newly visible target instead")
    runner._drain_interjections()
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)

    sizes = []
    for index in range(40):
        call_id = f"call-{index}"
        runner.messages.extend([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "click",
                        "arguments": json.dumps({"frame_x": 500, "frame_y": 500}),
                    },
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": "x" * 5000},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"authoritative frame {index}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,frame-{index}"},
                    },
                ],
            },
        ])
        runner._tool_history.append((
            "click",
            {"frame_x": 500, "frame_y": 500},
            json.dumps({"ok": True, "verified": False, "diagnostic": "z" * 5000}),
        ))
        runner._roll_native_visual_context()
        sizes.append(len(json.dumps(runner.messages, ensure_ascii=False)))

        assert all(message.get("role") not in {"assistant", "tool"} for message in runner.messages)
        assert all("tool_calls" not in message for message in runner.messages)
        images = [
            part["image_url"]["url"]
            for message in runner.messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        ]
        assert images == [f"data:image/jpeg;base64,frame-{index}"]

    rendered = json.dumps(runner.messages, ensure_ascii=False)
    assert runner.task in rendered
    assert "stable system policy" in rendered
    assert "static guidance" in rendered
    assert "use the newly visible target instead" in rendered
    assert "Current mutable plan" in rendered
    assert max(sizes[-10:]) - min(sizes[-10:]) < 100
    assert max(sizes) < 9000


def test_post_action_frame_reports_same_region_pixel_no_progress(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("operate from the visible pixels")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 91
    region = Region(10, 20, 800, 600)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.0)
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    assert runner._inject_visual_frame(
        phase="initial", image_b64="first", frame_region=region,
    )
    assert runner._inject_visual_frame(
        phase="post_action",
        action="click",
        image_b64="second",
        frame_region=region,
        action_signature="click:stable-target",
    )

    text = runner.messages[-1]["content"][0]["text"]
    assert "pixel change ratio" in text
    assert "0.000000" in text
    assert "NO VISUAL PROGRESS DETECTED" in text
    visual_event = next(
        payload for name, payload in reversed(events) if name == "visual_feedback"
    )
    assert visual_event["compared_to_frame_id"] == 1
    assert visual_event["pixel_change_comparable"] is True
    assert visual_event["pixel_change_ratio"] == 0.0
    assert visual_event["no_visual_progress"] is True
    assert visual_event["no_progress_streak"] == 1


def test_repeated_low_change_spatial_action_is_blocked_then_safely_stops(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("reach the requested visible state")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "authoritative-frame"
    args = {"frame_x": 500, "frame_y": 500}
    signature = runner._native_spatial_action_signature(
        "click", args, frame_region=runner._visual_frame_region,
    )
    runner._native_no_progress_signature = signature
    runner._native_no_progress_streak = 2
    runner._last_visual_change_ratio = 0.0
    runner._last_visual_change_comparable = True
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("repeat no-progress action reached dispatch")
        ),
    )

    first_terminal = runner._handle_tool_calls([_tool_call("repeat-1", "click", args)])
    first = json.loads(runner._tool_history[-1][2])
    assert first_terminal is None
    assert first["blocked"] is True
    assert first["native_no_progress_guard"] is True
    assert first["strategy_change_required"] is True
    assert first["repeated_guard_blocks"] == 1

    for attempt in (2, 3):
        terminal = runner._handle_tool_calls([
            _tool_call(f"repeat-{attempt}", "click", args),
        ])
        blocked = json.loads(runner._tool_history[-1][2])
        assert terminal is None
        assert blocked["blocked"] is True
        assert blocked["repeated_guard_blocks"] == attempt
        assert blocked.get("safe_stop") is not True

    fourth_terminal = runner._handle_tool_calls([
        _tool_call("repeat-4", "click", args),
    ])
    fourth = json.loads(runner._tool_history[-1][2])
    assert fourth_terminal == "reply"
    assert fourth["blocked"] is True
    assert fourth["safe_stop"] is True
    assert "stopping" in fourth["error"]
    assert any(
        "mandatory spatial replan" in str(message.get("content") or "")
        for message in runner.messages
    )


def test_first_no_progress_block_can_use_fresh_local_semantic_recovery(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("activate the current visible target")
    runner._visual_frame_id = 8
    runner._visual_frame_hwnd = 91
    runner.session.hwnd = 91
    runner.session.snapshot = Snapshot(
        hwnd=91,
        title="Arbitrary application",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=17,
            role="link",
            text="Current action",
            cx=120,
            cy=110,
            left=100,
            top=100,
            width=40,
            height=20,
            source="uia",
        )],
    )
    runner.session._semantic_grounding_frame_id = 8
    monkeypatch.setattr(
        runner,
        "_map_normalized_frame_coordinates",
        lambda _tool, _args: {"x": 96, "y": 110},
    )
    first_block = {
        "repeated_guard_blocks": 1,
        "repeat_signature": "click:missed-visual-point",
    }

    recovered = runner._native_no_progress_semantic_recovery(
        "click", {"image_x": 100, "image_y": 100}, first_block,
    )

    assert recovered == {"element_id": 17}
    assert runner._native_no_progress_semantic_recovery(
        "click",
        {"image_x": 100, "image_y": 100},
        {**first_block, "repeated_guard_blocks": 2},
    ) is None
    runner.session._semantic_grounding_frame_id = 7
    assert runner._native_no_progress_semantic_recovery(
        "click", {"image_x": 100, "image_y": 100}, first_block,
    ) is None


def test_meaningful_pixel_change_resets_native_repeat_guard():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the visible application")
    runner._native_no_progress_signature = "click:old"
    runner._native_no_progress_streak = 3
    runner._native_no_progress_block_count = 1

    runner._record_native_visual_progress(
        "click:old", 0.02, comparable=True,
    )

    assert runner._native_no_progress_signature == ""
    assert runner._native_no_progress_streak == 0
    assert runner._native_no_progress_block_count == 0


def test_alternating_ineffective_spatial_actions_keep_per_signature_stagnation_debt():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the changing visible interface")
    first = "click:first-stagnant-target"
    second = "scroll:second-stagnant-target"

    runner._record_native_visual_progress(first, 0.0, comparable=True)
    runner._record_native_visual_progress(second, 0.0, comparable=True)
    runner._record_native_visual_progress(first, 0.0, comparable=True)
    runner._record_native_visual_progress(second, 0.0, comparable=True)

    assert runner._native_no_progress_streaks[first] == 2
    assert runner._native_no_progress_streaks[second] == 2
    assert runner._native_no_progress_guard(
        "scroll",
        {"frame_x": 500, "frame_y": 500, "amount": 3},
    ) is None

    # The real guard keys by the normalized action signature. Seed a matching
    # signature to prove that alternating actions no longer erase its debt.
    runner._native_no_progress_streaks.clear()
    args = {"frame_x": 500, "frame_y": 500, "amount": 3}
    signature = runner._native_spatial_action_signature(
        "scroll", args, frame_region=(0, 0, 1920, 1080),
    )
    runner._visual_frame_region = (0, 0, 1920, 1080)
    runner._native_no_progress_streaks[signature] = 2
    blocked = runner._native_no_progress_guard("scroll", args)

    assert blocked is not None
    assert blocked["no_progress_streak"] == 2


def test_no_progress_signature_unifies_image_and_frame_coordinate_sources():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the visible interface")
    runner._visual_frame_region = (0, 0, 1920, 1080)
    runner._visual_submitted_size = (1120, 630)
    image_args = {"image_x": 612, "image_y": 569}
    frame_args = {
        "x": 1050,
        "y": 976,
        "source_frame_coordinates": {
            "frame_x": 612 * 1000.0 / 1119,
            "frame_y": 569 * 1000.0 / 629,
        },
        "source_image_coordinates": {
            "image_x": 612,
            "image_y": 569,
            "submitted_image_size": [1120, 630],
        },
    }

    image_signature = runner._native_spatial_action_signature(
        "click",
        image_args,
        frame_region=runner._visual_frame_region,
        submitted_size=runner._visual_submitted_size,
    )
    frame_signature = runner._native_spatial_action_signature(
        "click",
        frame_args,
        frame_region=runner._visual_frame_region,
        submitted_size=runner._visual_submitted_size,
    )

    assert image_signature == frame_signature
    runner._native_no_progress_streaks[frame_signature] = 2
    blocked = runner._native_no_progress_guard("click", image_args)
    assert blocked is not None
    assert blocked["repeat_signature"] == frame_signature


@pytest.mark.parametrize(
    "task",
    [
        "draw a polished landscape on the visible canvas",
        "sketch the visible outline",
        "write my name by hand on this page",
        "sign this document",
        "trace the line shown in the canvas",
        "用画板画一只狗,要好看一点不要简陋的",
        "在这里手写姓名",
        "给合同签名",
    ],
)
def test_continuous_pointer_intent_requires_explicit_surface_output(task):
    from winpilot.agent.loop import _requests_continuous_pointer_output

    assert _requests_continuous_pointer_output(task) is True


@pytest.mark.parametrize(
    "task",
    [
        "click the Continue button",
        "click the Draw button",
        "click Sign in to continue",
        "type a report in Notepad",
        "open Microsoft Paint and click the Brushes button",
        "打开画板并点击画笔按钮",
        "不要画，点击取消",
    ],
)
def test_continuous_pointer_intent_does_not_capture_ordinary_button_tasks(task):
    from winpilot.agent.loop import _requests_continuous_pointer_output

    assert _requests_continuous_pointer_output(task) is False


def test_typed_desktop_intent_outranks_antecedent_words_for_pointer_mode():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile

    runner = AgentRunner(
        "把刚刚画的画截图并交给另一个应用",
        runtime_profile=RuntimeProfile(stage_type="ExecuteDesktopTask"),
        task_intent={
            "operation": ["capture", "send"],
            "interaction": {"active": False},
        },
    )

    assert runner._continuous_pointer_output_requested() is False
    runner._native_visual_user_directives.append("现在在当前表面手写签名")
    assert runner._continuous_pointer_output_requested() is True


def test_managed_artifact_capture_advances_only_the_matching_plan_item():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("capture the current work and hand it to another app")
    runner.session.plan = Plan([
        "截图当前可见作品",
        "在目标应用中交付该作品",
    ])
    result = runner._maybe_auto_complete_artifact_capture_plan_item(
        "capture_artifact",
        {"scope": "window"},
        ToolResult(json.dumps({
            "ok": True,
            "artifact": {
                "artifact_id": "artifact-current-run",
                "sha256": "abc123",
                "managed_path": "C:/managed/artifact.png",
            },
        }, ensure_ascii=False)),
    )

    payload = json.loads(result.text)
    assert [step["done"] for step in runner.session.plan.steps] == [True, False]
    assert payload["auto_completed_plan_items"][0]["source"] == (
        "managed_artifact_capture"
    )
    assert payload.get("plan_all_done") is not True
    assert result.terminal is None


def test_managed_artifact_capture_advances_contiguous_evidence_prerequisites():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("capture the current work and hand it to another app")
    runner.session.plan = Plan([
        "确认当前应用中是否有未保存的画作",
        "截取当前可见画作",
        "将截图保存到本地",
        "发现并核实本机可用通信路线",
        "发送截图",
    ])
    result = runner._maybe_auto_complete_artifact_capture_plan_item(
        "capture_artifact",
        {"scope": "window"},
        ToolResult(json.dumps({
            "ok": True,
            "artifact": {
                "artifact_id": "artifact-current-run",
                "sha256": "abc123",
                "managed_path": "C:/managed/artifact.png",
            },
        }, ensure_ascii=False)),
    )

    payload = json.loads(result.text)
    assert [step["done"] for step in runner.session.plan.steps] == [
        True, True, True, False, False,
    ]
    assert [item["source"] for item in payload["auto_completed_plan_items"]] == [
        "managed_artifact_source_evidence",
        "managed_artifact_capture",
        "managed_artifact_path",
    ]
    assert "do not capture or save it again" in payload["fast_path_advice"]


def test_continuous_pointer_guard_accumulates_nearby_low_change_clicks():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("sketch a curved line on the visible surface")
    runner._record_native_visual_progress("click:near-a", 0.0, comparable=True)
    runner._record_native_visual_progress("click:near-b", 0.0, comparable=True)

    blocked = runner._continuous_pointer_click_guard(
        "click", {"frame_x": 535, "frame_y": 185},
    )

    assert blocked is not None
    assert blocked["continuous_pointer_guard"] is True
    assert blocked["continuous_gesture_required"] is True
    assert blocked["setup_clicks_observed"] == 2
    assert blocked["low_change_click_streak"] == 2
    assert "drag or draw_strokes" in blocked["error"]


def test_toolbar_pixel_changes_do_not_extend_continuous_setup_clicks():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("write the requested name by hand on the visible page")
    for index in range(3):
        runner._record_native_visual_progress(
            f"click:toolbar-{index}", 0.03, comparable=True,
        )

    blocked = runner._continuous_pointer_click_guard(
        "click", {"frame_x": 650, "frame_y": 90},
    )

    assert blocked is not None
    assert blocked["setup_clicks_observed"] == 3
    assert blocked["low_change_click_streak"] == 0
    assert "toolbar/menu pixel changes are not subject progress" in blocked["error"]


def test_visible_continuous_gesture_releases_tool_choice_guard_not_done_gate():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("trace the visible outline")
    runner._record_native_visual_progress("click:tool-a", 0.0, comparable=True)
    runner._record_native_visual_progress("click:tool-b", 0.0, comparable=True)
    assert runner._continuous_pointer_click_guard("click", {"frame_x": 1, "frame_y": 1})

    runner._record_native_visual_progress(
        "draw_strokes:path-a", 0.02, comparable=True,
    )

    assert runner._continuous_pointer_gesture_progress is True
    assert runner._continuous_pointer_click_guard(
        "click", {"frame_x": 1, "frame_y": 1},
    ) is None
    assert runner._had_verified_action is False
    assert runner._done_verified is False


def test_continuous_pointer_guard_blocks_before_dispatch_then_safely_stops(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a freehand shape on the current canvas")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "authoritative-frame"
    runner._record_native_visual_progress("click:one", 0.0, comparable=True)
    runner._record_native_visual_progress("click:two", 0.0, comparable=True)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("continuous-output click reached dispatch")
        ),
    )

    first_terminal = runner._handle_tool_calls([
        _tool_call("blocked-click-1", "click", {"frame_x": 200, "frame_y": 100}),
    ])
    first = json.loads(runner._tool_history[-1][2])
    assert first_terminal is None
    assert first["blocked"] is True
    assert first["continuous_gesture_required"] is True
    assert first["repeated_guard_blocks"] == 1

    second_terminal = runner._handle_tool_calls([
        _tool_call("blocked-click-2", "click", {"frame_x": 800, "frame_y": 120}),
    ])
    second = json.loads(runner._tool_history[-1][2])
    assert second_terminal == "reply"
    assert second["safe_stop"] is True
    assert "stopping" in second["error"]


def test_continuous_pointer_prompt_maps_output_to_gesture_tools():
    from winpilot.agent.prompts import system_prompt

    native = system_prompt(
        main_model_has_vision=True,
        continuous_pointer_output=True,
    )
    text_only = system_prompt(
        main_model_has_vision=False,
        continuous_pointer_output=True,
    )
    ordinary = system_prompt(main_model_has_vision=True)

    assert "clicks are setup only and never output evidence" in native.casefold()
    assert "use drag/draw_strokes for the output itself" in native
    assert "at most 12 meaningful anchor points per path" in native
    assert "if one rollback produces no visible change, never repeat it" in native
    assert "draw_strokes with normalized_strokes in 0..1000" in text_only
    assert "submit all currently known independent paths together" in text_only
    assert "multi-path mutation" not in ordinary
    assert "guide/scaffold strokes" not in ordinary


def test_gui_truncated_tool_retry_keeps_room_for_one_complete_call(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the visible application")
    runner.messages = [{"role": "assistant", "content": "<tool_call>" + "1" * 5000}]
    runner._trace_debug = lambda *_args, **_kwargs: None
    assert runner._recover_truncated_gui_tool_call() is True

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="use the tool", tool_calls=[]),
            finish_reason="stop",
        )])

    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: {
            "model": "mimo-v2.5-pro",
            "temperature": 0.3,
            "max_tokens": 4096,
        } if path == ("api",) else default,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    assert runner._chat(client, []) is not None
    assert captured["max_tokens"] == 4096
    assert runner._gui_tool_call_retry_pending is False
    correction = next(
        message["content"] for message in runner.messages
        if message.get("role") == "user"
    )
    assert "exactly one real tool call" in correction
    assert "at most 12 points per path" in correction
    assert "regularly spaced samples" in correction


def test_gui_truncated_tool_output_retries_once_then_fails_closed(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )
    runner = AgentRunner("operate the visible application")
    runner.messages = [{"role": "assistant", "content": "first pseudo tool body"}]

    assert runner._recover_truncated_gui_tool_call() is True
    runner.messages.append({"role": "assistant", "content": "second pseudo tool body"})
    assert runner._recover_truncated_gui_tool_call() is False

    rendered = json.dumps(runner.messages)
    assert "first pseudo tool body" not in rendered
    assert "second pseudo tool body" not in rendered
    assert sum(
        "exactly one real tool call" in str(message.get("content") or "")
        for message in runner.messages
    ) == 1
    assert runner.status == "fail"
    assert "Safety stop" in runner.result_text
    recoveries = [payload for name, payload in events if name == "agent_recovery"]
    assert [payload["attempt"] for payload in recoveries] == [1, 2]
    assert recoveries[-1]["safe_stop"] is True


def test_normal_complex_real_tool_call_keeps_configured_output_budget(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a detailed path in the visible editor")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._trace_debug = lambda *_args, **_kwargs: None
    captured = {}
    complex_call = _tool_call(
        "complex",
        "draw_strokes",
        {"frame_strokes": [[[index, index] for index in range(500)]]},
    )

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[complex_call]),
            finish_reason="length",
        )])

    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: {
            "model": "mimo-v2.5-pro",
            "temperature": 0.3,
            "max_tokens": 4096,
        } if path == ("api",) else default,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    response = runner._chat(client, [])

    assert captured["max_tokens"] == 4096
    assert response.choices[0].message.tool_calls == [complex_call]
    assert runner._gui_truncated_tool_retries == 0


def test_text_continuous_setup_budget_blocks_before_dispatch_and_safely_stops(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("write my name by hand on the visible page")
    runner.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    dispatched = []

    def dispatch(name, args):
        dispatched.append(name)
        if name == "click":
            return ToolResult('{"ok": true, "verified": false}')
        if name == "execute_subgoal":
            return ToolResult(json.dumps({
                "ok": True,
                "verified": False,
                "actions_executed": [{"action": "select_color"}],
            }))
        return ToolResult('{"ok": true}')

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)

    assert runner._handle_tool_calls([
        _tool_call("setup-click", "click", {"x": 100, "y": 100}),
    ]) is None
    assert runner._continuous_pointer_text_setup_mutations == 1
    assert runner._handle_tool_calls([
        _tool_call("observe", "observe", {"full": True}),
    ]) is None
    assert runner._continuous_pointer_text_setup_mutations == 1
    assert runner._handle_tool_calls([
        _tool_call("setup-subgoal", "execute_subgoal", {
            "subgoal": "select a visible pen option",
            "click": {"label": "Pen"},
        }),
    ]) is None
    assert runner._continuous_pointer_text_setup_mutations == 2

    assert runner._handle_tool_calls([
        _tool_call("blocked-click", "click", {"x": 200, "y": 100}),
    ]) is None
    first = json.loads(runner._tool_history[-1][2])
    assert first["text_only_continuous_setup_guard"] is True
    assert first["setup_mutations_observed"] == 2
    assert "normalized_strokes in 0..1000" in first["error"]
    assert dispatched == ["click", "observe", "execute_subgoal"]

    terminal = runner._handle_tool_calls([
        _tool_call("blocked-subgoal", "execute_subgoal", {
            "subgoal": "select another brush",
            "click": {"label": "Brush"},
        }),
    ])
    second = json.loads(runner._tool_history[-1][2])
    assert terminal == "reply"
    assert second["safe_stop"] is True
    assert dispatched == ["click", "observe", "execute_subgoal"]


def test_text_continuous_outcomes_ignore_failures_verification_and_observation(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("sign this form by hand")
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)

    runner._record_text_continuous_pointer_outcome(
        "click", {"x": 1, "y": 1}, ToolResult('{"ok": false, "verified": false}'),
    )
    runner._record_text_continuous_pointer_outcome(
        "click", {"x": 1, "y": 1}, ToolResult('{"ok": true, "blocked": true}'),
    )
    runner._record_text_continuous_pointer_outcome(
        "click", {"x": 1, "y": 1}, ToolResult('{"ok": true, "verified": true}'),
    )
    runner._record_text_continuous_pointer_outcome(
        "observe", {"full": True}, ToolResult('{"ok": true}'),
    )
    runner._record_text_continuous_pointer_outcome(
        "revise_plan", {"steps": ["sign"]}, ToolResult('{"ok": true}'),
    )
    runner._record_text_continuous_pointer_outcome(
        "draw_strokes",
        {"normalized_strokes": [[[1, 1], [2, 2]]]},
        ToolResult('{"ok": false, "blocked": true}'),
    )

    assert runner._continuous_pointer_text_setup_mutations == 0
    assert runner._continuous_pointer_gesture_progress is False


def test_successful_text_draw_releases_only_routing_guard(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tools import ToolResult

    runner = AgentRunner("trace the visible line")
    runner.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    runner._continuous_pointer_text_setup_mutations = 2
    dispatched = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: (
            dispatched.append((name, args)),
            ToolResult('{"ok": true, "verified": false}'),
        )[1],
    )

    terminal = runner._handle_tool_calls([_tool_call(
        "normalized-draw",
        "draw_strokes",
        {"normalized_strokes": [[[100, 100], [900, 900]]]},
    )])

    assert terminal is None
    assert dispatched == [(
        "draw_strokes",
        {"normalized_strokes": [[[100, 100], [900, 900]]]},
    )]
    assert runner._continuous_pointer_gesture_progress is True
    assert runner._continuous_pointer_text_setup_mutations == 0
    assert runner._text_continuous_pointer_preparation_guard(
        "click", {"x": 10, "y": 10},
    ) is None
    assert runner._had_verified_action is False
    assert runner._done_verified is False


def test_text_continuous_setup_guard_does_not_affect_ordinary_tasks(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the Continue button")
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    runner._continuous_pointer_text_setup_mutations = 99

    assert runner._text_continuous_pointer_preparation_guard(
        "click", {"x": 10, "y": 10},
    ) is None
