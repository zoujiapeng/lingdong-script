from dataclasses import FrozenInstanceError

import pytest

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.utils.image_capture import VisualCaptureScope
from winpilot.utils.screenshot import Region


def _observer(
    authority: FrameAuthority,
    *,
    capture=lambda _hwnd, **_kwargs: "captured",
    select_scope=lambda _hwnd, **_kwargs: VisualCaptureScope(
        Region(10, 20, 800, 600),
        "target_window",
        "selected",
    ),
    settled=None,
    desktop=lambda: Region(0, 0, 1920, 1080),
):
    from winpilot.visual_runtime.frame_observer import FrameObserver

    return FrameObserver(
        authority,
        capture=capture,
        select_scope=select_scope,
        settled_initial_capture=settled,
        virtual_desktop=desktop,
        captured_at=lambda: 123.0,
    )


def test_provided_region_and_image_are_committed_as_one_immutable_observation():
    authority = FrameAuthority()
    desktop = Region(-1120, 0, 2240, 1120)
    observer = _observer(
        authority,
        capture=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provided pixels must not be recaptured")
        ),
        select_scope=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provided geometry must bypass scope selection")
        ),
        desktop=lambda: desktop,
    )

    captured = observer.capture(
        epoch=4,
        hwnd=71,
        phase="preflight",
        action="visual_preflight_changed",
        provided_image_b64="provided-jpeg",
        provided_region=desktop,
    )

    assert captured is not None
    assert authority.current is None
    assert captured.region is desktop
    assert captured.scope == "full_desktop"
    assert captured.reason == "preflight_exact_capture_region"
    assert captured.submitted_size == (1120, 560)

    state = observer.commit(captured)
    assert authority.current is state
    assert state.region == (-1120, 0, 2240, 1120)
    assert state.submitted_size == (1120, 560)
    assert state.image_b64 == "provided-jpeg"
    with pytest.raises(FrozenInstanceError):
        captured.region = Region(0, 0, 1, 1)


def test_matching_cached_capture_reuses_exact_image_region_pair():
    authority = FrameAuthority()
    cached_region = Region(30, 40, 900, 700)
    observer = _observer(
        authority,
        capture=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching cached pixels must not be recaptured")
        ),
        select_scope=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching cached geometry must bypass scope selection")
        ),
    )

    captured = observer.capture(
        epoch=2,
        hwnd=22,
        phase="observe",
        provided_image_b64="cached-jpeg",
        cached_capture={
            "image_b64": "cached-jpeg",
            "hwnd": 22,
            "region": cached_region,
            "scope": "target_window",
            "scope_reason": "observe_exact_capture_region",
        },
    )

    assert captured is not None
    assert captured.region is cached_region
    assert captured.scope == "target_window"
    assert captured.reason == "observe_exact_capture_region"
    assert captured.image_b64 == "cached-jpeg"


def test_scope_selection_and_capture_are_injected_without_task_semantics():
    authority = FrameAuthority()
    region = Region(50, 60, 1000, 700)
    related = lambda _foreground, _target: True
    seen = []

    def select(hwnd, **kwargs):
        seen.append(("scope", hwnd, kwargs))
        return VisualCaptureScope(region, "target_window", "verified_foreground_target")

    def capture(hwnd, **kwargs):
        seen.append(("capture", hwnd, kwargs))
        return "selected-jpeg"

    observer = _observer(authority, capture=capture, select_scope=select)
    captured = observer.capture(
        epoch=3,
        hwnd=91,
        phase="post_action",
        action="click",
        related_window=related,
    )

    assert captured is not None
    assert seen == [
        ("scope", 91, {"enabled": None, "related_window": related}),
        ("capture", 91, {"region": region}),
    ]
    assert captured.region is region
    assert captured.image_b64 == "selected-jpeg"
    assert captured.submitted_size == (1000, 700)


def test_initial_prebind_uses_injected_settled_capture():
    authority = FrameAuthority()
    region = Region(10, 20, 800, 600)
    seen = []
    observer = _observer(
        authority,
        capture=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prebind must use settled capture")
        ),
        settled=lambda hwnd, selected: seen.append((hwnd, selected)) or "settled-jpeg",
    )

    captured = observer.capture(
        epoch=1,
        hwnd=42,
        phase="initial",
        action="target_prebind",
    )

    assert captured is not None
    assert captured.image_b64 == "settled-jpeg"
    assert seen == [(42, region)]


@pytest.mark.parametrize(
    ("region", "capture_result", "capture_calls"),
    [
        (Region(0, 0, 15, 600), "unreachable", 0),
        (Region(0, 0, 800, 600), "", 1),
    ],
)
def test_tiny_region_and_empty_capture_fail_without_committing(
    region: Region,
    capture_result: str,
    capture_calls: int,
):
    authority = FrameAuthority()
    seen = []
    observer = _observer(
        authority,
        select_scope=lambda *_args, **_kwargs: VisualCaptureScope(
            region, "target_window", "selected"
        ),
        capture=lambda *_args, **_kwargs: seen.append(True) or capture_result,
    )

    assert observer.capture(epoch=0, hwnd=7, phase="initial") is None
    assert len(seen) == capture_calls
    assert authority.current is None


def test_commit_rejects_authority_change_after_capture():
    authority = FrameAuthority()
    observer = _observer(authority)
    captured = observer.capture(
        epoch=1,
        hwnd=7,
        phase="preflight",
        provided_image_b64="candidate",
        provided_region=Region(10, 20, 800, 600),
    )
    assert captured is not None
    authority.commit(
        epoch=1,
        hwnd=7,
        region=(10, 20, 800, 600),
        scope="target_window",
        reason="concurrent",
        submitted_size=(800, 600),
        image_b64="newer",
        captured_at=124.0,
        expected_previous_frame_id=0,
    )

    with pytest.raises(RuntimeError, match="changed before commit"):
        observer.commit(captured)
    assert authority.current is not None
    assert authority.current.image_b64 == "newer"


def test_legacy_visual_frame_delegates_capture_and_commit_to_shared_owner(monkeypatch):
    from winpilot.agent import loop
    from winpilot.agent.loop import AgentRunner
    from winpilot.visual_runtime.frame_observer import FrameObserver

    calls = []

    class SpyFrameObserver(FrameObserver):
        def capture(self, **kwargs):
            calls.append(("capture", kwargs))
            return super().capture(**kwargs)

        def commit(self, captured):
            calls.append(("commit", captured))
            return super().commit(captured)

    region = Region(10, 20, 800, 600)
    monkeypatch.setattr(loop, "FrameObserver", SpyFrameObserver)
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        loop,
        "select_visual_capture_scope",
        lambda *_args, **_kwargs: VisualCaptureScope(
            region, "target_window", "verified_foreground_target"
        ),
    )
    monkeypatch.setattr(loop, "capture_window_jpeg_b64", lambda *_args, **_kwargs: "jpeg")
    monkeypatch.setattr(loop.BUS, "publish", lambda *_args, **_kwargs: None)

    runner = AgentRunner("operate from the current pixels")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 71
    monkeypatch.setattr(runner, "_refresh_visual_target_binding", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_is_native_execute_desktop_task", lambda: True)

    assert runner._inject_visual_frame(phase="initial") is True
    assert [name for name, _payload in calls] == ["capture", "commit"]
    assert runner._frame_authority.current is not None
    assert runner._frame_authority.current.image_b64 == "jpeg"
