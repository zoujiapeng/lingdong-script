import threading
import time

import pytest

from winpilot.action import executor
from winpilot.action import desktop_queue as desktop_queue_module
from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    ClickCount,
    ClickMechanism,
    MouseButton,
    RawBackendResult,
)
from winpilot.utils.log import BUS


def test_desktop_action_queue_serializes_parallel_jobs():
    queue = DesktopActionQueue()
    active = 0
    max_active = 0
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def job(idx: int):
        nonlocal active, max_active
        barrier.wait()

        def body():
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return idx

        return queue.run(f"job-{idx}", body)

    threads = [threading.Thread(target=job, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert max_active == 1
    status = queue.status()
    assert status["submitted"] == 4
    assert status["completed"] == 4
    assert status["failed"] == 0


def test_desktop_action_queue_allows_reentrant_nested_jobs():
    queue = DesktopActionQueue()

    result = queue.run("outer", lambda: queue.run("inner", lambda: "ok"))

    assert result == "ok"
    status = queue.status()
    assert status["submitted"] == 2
    assert status["completed"] == 2
    assert status["reentrant"] == 1


def _run_competing_jobs(queue, jobs):
    barrier = threading.Barrier(len(jobs))
    active = 0
    max_active = 0
    active_names = set()
    overlaps = set()
    lock = threading.Lock()

    def run(name, scope, hwnd):
        nonlocal active, max_active
        barrier.wait()

        def body():
            nonlocal active, max_active
            with lock:
                overlaps.update({tuple(sorted((name, other))) for other in active_names})
                active_names.add(name)
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with lock:
                active_names.remove(name)
                active -= 1

        queue.run(name, body, scope=scope, target_hwnd=hwnd)

    threads = [
        threading.Thread(target=run, args=job, daemon=True)
        for job in jobs
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    return max_active, overlaps


def test_window_scoped_actions_parallelize_only_across_different_hwnds():
    queue = DesktopActionQueue()

    max_active, overlaps = _run_competing_jobs(
        queue,
        [
            ("window-101-a", "window", 101),
            ("window-101-b", "window", 101),
            ("window-202", "window", 202),
        ],
    )

    assert max_active == 2
    assert ("window-101-a", "window-101-b") not in overlaps
    assert any("window-202" in pair for pair in overlaps)
    assert queue.status()["parallel_peak"] == 2


def test_physical_actions_remain_globally_serialized_across_hwnds():
    queue = DesktopActionQueue()

    max_active, overlaps = _run_competing_jobs(
        queue,
        [
            ("physical-101", "physical", 101),
            ("physical-202", "physical", 202),
        ],
    )

    assert max_active == 1
    assert overlaps == set()


def test_physical_action_conflicts_with_same_window_but_not_other_window():
    queue = DesktopActionQueue()
    physical_started = threading.Event()
    window_101_started = threading.Event()
    window_202_started = threading.Event()
    release_physical = threading.Event()

    physical = threading.Thread(
        target=lambda: queue.run(
            "physical-101",
            lambda: (physical_started.set(), release_physical.wait(timeout=2)),
            target_hwnd=101,
        ),
        daemon=True,
    )
    physical.start()
    assert physical_started.wait(timeout=1)

    window_101 = threading.Thread(
        target=lambda: queue.run(
            "window-101",
            window_101_started.set,
            scope="window",
            target_hwnd=101,
        ),
        daemon=True,
    )
    window_202 = threading.Thread(
        target=lambda: queue.run(
            "window-202",
            window_202_started.set,
            scope="window",
            target_hwnd=202,
        ),
        daemon=True,
    )
    window_101.start()
    window_202.start()

    assert window_202_started.wait(timeout=1)
    assert not window_101_started.wait(timeout=0.1)
    release_physical.set()
    assert window_101_started.wait(timeout=1)
    for thread in (physical, window_101, window_202):
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_unknown_physical_target_blocks_every_window_resource():
    queue = DesktopActionQueue()
    physical_started = threading.Event()
    window_101_started = threading.Event()
    window_202_started = threading.Event()
    release_physical = threading.Event()

    physical = threading.Thread(
        target=lambda: queue.run(
            "physical-unknown",
            lambda: (physical_started.set(), release_physical.wait(timeout=2)),
        ),
        daemon=True,
    )
    physical.start()
    assert physical_started.wait(timeout=1)

    windows = [
        threading.Thread(
            target=lambda hwnd=hwnd, started=started: queue.run(
                f"window-{hwnd}",
                started.set,
                scope="window",
                target_hwnd=hwnd,
            ),
            daemon=True,
        )
        for hwnd, started in ((101, window_101_started), (202, window_202_started))
    ]
    for thread in windows:
        thread.start()

    assert not window_101_started.wait(timeout=0.1)
    assert not window_202_started.wait(timeout=0.1)
    release_physical.set()
    assert window_101_started.wait(timeout=1)
    assert window_202_started.wait(timeout=1)
    for thread in (physical, *windows):
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_clipboard_resource_is_independent_but_single_writer():
    queue = DesktopActionQueue()

    max_active, overlaps = _run_competing_jobs(
        queue,
        [
            ("clipboard-a", "clipboard", None),
            ("clipboard-b", "clipboard", None),
            ("window-101", "window", 101),
        ],
    )

    assert max_active == 2
    assert ("clipboard-a", "clipboard-b") not in overlaps
    assert any("window-101" in pair for pair in overlaps)


def test_nested_action_cannot_escalate_its_resource_lease():
    queue = DesktopActionQueue()

    with pytest.raises(RuntimeError, match="cannot escalate resources"):
        queue.run(
            "outer-window",
            lambda: queue.run("inner-physical", lambda: None, target_hwnd=101),
            scope="window",
            target_hwnd=101,
        )


def test_desktop_action_queue_marks_controller_lease_for_nested_coalescing():
    queue = DesktopActionQueue()

    observed = queue.run(
        "controller_batch",
        queue.coalescing_nested_actions,
        coalesce_nested=True,
    )

    assert observed is True
    assert queue.coalescing_nested_actions() is False


def test_desktop_action_queue_emits_bus_events_and_errors():
    queue = DesktopActionQueue()
    events = []
    BUS.add_sink(events.append)
    try:
        assert queue.run("ok-action", lambda: {"ok": True}) == {"ok": True}
        with pytest.raises(RuntimeError):
            queue.run("bad-action", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    finally:
        BUS.remove_sink(events.append)

    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [event["state"] for event in queue_events] == ["start", "done", "start", "error"]
    assert queue_events[0]["action"] == "ok-action"
    assert queue_events[2]["action"] == "bad-action"
    assert queue_events[3]["error"] == "boom"
    assert queue_events[3]["wait_ms"] >= 0
    assert queue_events[3]["elapsed_ms"] >= 0
    assert queue_events[3]["reentrant"] is False
    assert queue.status()["failed"] == 1


def test_public_click_uses_one_outer_queue_event(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: False)
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda x, y, button, count, hwnd: (
            calls.append((x, y, button, count, hwnd))
            or RawBackendResult(True, {})
        ),
    )

    BUS.add_sink(events.append)
    try:
        out = executor.click(10, 20, button="left", hwnd=123)
    finally:
        BUS.remove_sink(events.append)

    assert out["ok"] is True
    assert calls == [(10, 20, MouseButton.LEFT, ClickCount.SINGLE, 123)]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "click"),
        ("done", "click"),
    ]


def test_typed_click_uses_one_outer_queue_event(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda x, y, button, count, hwnd, prepositioned=False,
        allow_target_focus=True: (
            calls.append((
                x, y, button, count, hwnd, prepositioned, allow_target_focus,
            ))
            or RawBackendResult(True, {})
        ),
    )

    BUS.add_sink(events.append)
    try:
        out = executor.execute_click_input(
            10, 20, MouseButton.LEFT, ClickCount.SINGLE,
            ClickMechanism.PHYSICAL, hwnd=123,
        )
    finally:
        BUS.remove_sink(events.append)

    assert out.input_completed is True
    assert calls == [(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE, 123, False, False,
    )]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "click"),
        ("done", "click"),
    ]


def test_typed_click_unqueued_core_bypasses_queue_and_matches_public(monkeypatch):
    backend_calls = []
    queue_calls = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda *args, **kwargs: (
            backend_calls.append((args, kwargs))
            or RawBackendResult(True, {"via": "test"})
        ),
    )

    def fake_run(action, fn, **kwargs):
        queue_calls.append((action, kwargs))
        return fn()

    monkeypatch.setattr(
        desktop_queue_module.DESKTOP_ACTION_QUEUE,
        "run",
        fake_run,
    )
    arguments = (
        10,
        20,
        MouseButton.LEFT,
        ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
    )

    core_result = executor._execute_click_input_unqueued(*arguments, hwnd=123)
    assert queue_calls == []

    public_result = executor.execute_click_input(*arguments, hwnd=123)

    assert public_result.to_dict() == core_result.to_dict()
    assert queue_calls == [
        ("click", {"target_hwnd": 123, "scope": "physical"}),
    ]
    assert len(backend_calls) == 2
    assert backend_calls[0] == backend_calls[1]


def test_window_scoped_decorator_binds_positional_hwnd(monkeypatch):
    observed = {}

    def fake_run(action, fn, **kwargs):
        observed.update({"action": action, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(desktop_queue_module.DESKTOP_ACTION_QUEUE, "run", fake_run)

    assert executor.close_window(321) == {"ok": True}
    assert observed["action"] == "close_window"
    assert observed["scope"] == "window"
    assert observed["target_hwnd"] == 321


def test_prepositioned_click_keeps_physical_servo_path(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *_args, **_kwargs: (
            calls.append(("post",)) or RawBackendResult(True, {})
        ),
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda x, y, button, count, hwnd, prepositioned=False,
        allow_target_focus=True: (
            calls.append(("raw", x, y, button, count, hwnd, prepositioned))
            or RawBackendResult(True, {})
        ),
    )

    out = executor.click(30, 40, hwnd=456, prepositioned=True)

    assert out["ok"] is True
    assert out["prepositioned"] is True
    assert calls == [(
        "raw", 30, 40, MouseButton.LEFT, ClickCount.SINGLE, 456, True,
    )]


def test_force_physical_click_bypasses_background_window_message(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *_args, **_kwargs: (
            calls.append(("post",)) or RawBackendResult(True, {})
        ),
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda x, y, button, count, hwnd, prepositioned=False,
        allow_target_focus=True: (
            calls.append(("raw", x, y, button, count, hwnd, prepositioned))
            or RawBackendResult(True, {})
        ),
    )

    out = executor.click(30, 40, hwnd=456, force_physical=True)

    assert out["ok"] is True
    assert out["via"] == "physical_sendinput"
    assert calls == [(
        "raw", 30, 40, MouseButton.LEFT, ClickCount.SINGLE, 456, False,
    )]


def test_raw_prepositioned_click_does_not_refocus_or_repeat_slow_move(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(
        executor,
        "_raw_focus_window",
        lambda hwnd: calls.append(("focus", hwnd)),
    )
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (30, 40))
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda hwnd: True)
    monkeypatch.setattr(
        executor,
        "_raw_move_mouse",
        lambda x, y: calls.append(("move", x, y)),
    )
    monkeypatch.setattr(executor, "_mouse_input", lambda flag: flag)
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: calls.append(("send", tuple(inputs))),
    )
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", sleeps.append)

    result = executor._raw_click(
        30, 40, MouseButton.LEFT, ClickCount.SINGLE, 456,
        prepositioned=True,
    )

    assert result.input_completed is True
    assert all(call[0] not in {"focus", "move"} for call in calls)
    assert [call[0] for call in calls] == ["send", "send"]
    assert sleeps == [0.008]


def test_raw_focus_continues_when_set_foreground_raises(monkeypatch):
    state = {"foreground": 12}
    monkeypatch.setattr(executor.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(executor.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(
        executor.win32gui,
        "GetForegroundWindow",
        lambda: state["foreground"],
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda _inputs: None)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    def denied(_hwnd):
        raise RuntimeError("foreground lock denied")

    monkeypatch.setattr(executor.win32gui, "SetForegroundWindow", denied)
    monkeypatch.setattr(
        executor.win32gui,
        "BringWindowToTop",
        lambda hwnd: state.__setitem__("foreground", hwnd),
    )

    result = executor._raw_focus_window(91)

    assert result["ok"] is True
    assert result["route"] == "bring_to_top"
    assert any(
        item["route"] == "set_foreground"
        and item["called"] is False
        and "foreground lock denied" in item["error"]
        for item in result["attempts"]
    )


def test_raw_focus_dismisses_transient_shell_before_activation(monkeypatch):
    state = {"foreground": 12}
    sends = []
    monkeypatch.setattr(executor.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(executor.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(
        executor.win32gui,
        "GetForegroundWindow",
        lambda: state["foreground"],
    )
    monkeypatch.setattr(
        executor.win32gui,
        "GetClassName",
        lambda hwnd: "Windows.UI.Core.CoreWindow" if hwnd == 12 else "AppWindow",
    )

    def send(inputs):
        sends.append(inputs)
        if len(sends) == 1:
            state["foreground"] = 0

    monkeypatch.setattr(executor, "_send_inputs", send)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        executor.win32gui,
        "SetForegroundWindow",
        lambda hwnd: state.__setitem__("foreground", hwnd),
    )
    monkeypatch.setattr(executor.win32gui, "BringWindowToTop", lambda _hwnd: None)

    result = executor._raw_focus_window(91)

    assert result["ok"] is True
    assert result["route"] == "set_foreground"
    assert len(sends) == 2
    assert result["attempts"][0]["route"] == "dismiss_transient_shell"


def test_public_scroll_uses_one_outer_queue_event(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: False)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: calls.append(("move", x, y)))
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: calls.append(("send", len(inputs))))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    BUS.add_sink(events.append)
    try:
        out = executor.scroll(10, 20, -2)
    finally:
        BUS.remove_sink(events.append)

    assert out["ok"] is True
    assert calls == [("move", 10, 20), ("send", 1)]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "scroll"),
        ("done", "scroll"),
    ]


def test_public_drag_uses_one_outer_queue_event(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: False)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: calls.append(("focus", hwnd)))
    monkeypatch.setattr(
        executor,
        "_stroke_path",
        lambda waypoints, duration_s, **_kwargs: (
            calls.append(("stroke", waypoints, duration_s)) or True
        ),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    BUS.add_sink(events.append)
    try:
        out = executor.drag(1, 2, 5, 6, points=[[3, 4]], hwnd=123)
    finally:
        BUS.remove_sink(events.append)

    assert out["ok"] is True
    assert calls == [
        ("focus", 123),
        ("stroke", [(1, 2), (3, 4), (5, 6)], 0.4),
    ]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "drag"),
        ("done", "drag"),
    ]


def test_public_draw_strokes_uses_one_outer_queue_event(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: False)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: calls.append(("focus", hwnd)))
    monkeypatch.setattr(
        executor,
        "_stroke_path",
        lambda waypoints, duration_s, **_kwargs: (
            calls.append(("stroke", waypoints, duration_s)) or True
        ),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    BUS.add_sink(events.append)
    try:
        out = executor.draw_strokes([[[1, 2], [3, 4]], [[9, 9]]], hwnd=123)
    finally:
        BUS.remove_sink(events.append)

    assert out == {"ok": True, "strokes_drawn": 1}
    assert calls == [
        ("focus", 123),
        ("stroke", [(1, 2), (3, 4)], 0.4),
    ]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "draw_strokes"),
        ("done", "draw_strokes"),
    ]


def test_long_pointer_path_releases_button_when_target_window_is_lost(monkeypatch):
    target_checks = iter((True, True, False))
    sent = []

    monkeypatch.setattr(
        executor,
        "_target_window_still_foreground",
        lambda _hwnd: next(target_checks, False),
    )
    monkeypatch.setattr(
        executor,
        "_cooperative_action_still_owned",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(executor, "_abs_mouse_move", lambda *_args: None)
    monkeypatch.setattr(executor, "_emit_move", lambda *_args: None)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        executor,
        "_mouse_input",
        lambda flags, **_kwargs: int(flags),
    )
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: sent.extend(inputs),
    )

    completed = executor._stroke_path([(10, 10), (40, 40)], 0.1, hwnd=123)

    assert completed is False
    assert sent == [
        executor.win32con.MOUSEEVENTF_LEFTDOWN,
        executor.win32con.MOUSEEVENTF_LEFTUP,
    ]


def test_target_window_guard_accepts_same_root_and_rejects_other_app(monkeypatch):
    foreground = {"hwnd": 456}
    roots = {123: 10, 456: 10, 789: 20}
    monkeypatch.setattr(
        executor.win32gui,
        "GetForegroundWindow",
        lambda: foreground["hwnd"],
    )
    monkeypatch.setattr(
        executor.win32gui,
        "GetAncestor",
        lambda hwnd, _kind: roots.get(hwnd, hwnd),
    )

    assert executor._target_window_still_foreground(123) is True
    foreground["hwnd"] = 789
    assert executor._target_window_still_foreground(123) is False


def test_type_text_newline_uses_raw_enter_without_nested_queue(monkeypatch):
    sent_batches = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_should_type_via_vk", lambda _text: False)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent_batches.append(len(inputs)))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    BUS.add_sink(events.append)
    try:
        out = executor.type_text("a\nb", interval_s=0)
    finally:
        BUS.remove_sink(events.append)

    assert out == {"ok": True, "typed": 3}
    assert sent_batches == [2, 1, 1, 2]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "type_text"),
        ("done", "type_text"),
    ]
    action_events = [event for event in events if event.get("type") == "action"]
    assert [event["action"] for event in action_events] == ["type_text"]


def test_clipboard_paste_uses_raw_hotkey_without_nested_queue(monkeypatch):
    calls = []
    events = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_raw_press_keys", lambda keys: calls.append(tuple(keys)))

    BUS.add_sink(events.append)
    try:
        out = executor.clipboard_paste()
    finally:
        BUS.remove_sink(events.append)

    assert out == {"ok": True}
    assert calls == [("ctrl", "v")]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "clipboard_paste"),
        ("done", "clipboard_paste"),
    ]
    action_events = [event for event in events if event.get("type") == "action"]
    assert [event["action"] for event in action_events] == ["clipboard_paste"]


def test_desktop_action_queue_async_worker_serializes_jobs():
    queue = DesktopActionQueue()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def make_job(idx: int):
        def body():
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return idx

        return body

    job_ids = [queue.submit(f"async-{i}", make_job(i)) for i in range(4)]
    results = [queue.wait(job_id, timeout_s=2) for job_id in job_ids]

    assert [result["result"] for result in results] == [0, 1, 2, 3]
    assert all(result["ok"] for result in results)
    assert max_active == 1
    status = queue.status()
    assert status["async_submitted"] == 4
    assert status["async_completed"] == 4
    assert status["async_failed"] == 0
    assert status["async_status"]["done"] == 4


def test_desktop_action_queue_async_jobs_parallelize_by_window_resource():
    queue = DesktopActionQueue()
    started = {101: threading.Event(), 202: threading.Event()}
    release = threading.Event()

    def body(hwnd):
        started[hwnd].set()
        assert release.wait(timeout=2)
        return hwnd

    job_101 = queue.submit("window-101", lambda: body(101), scope="window", target_hwnd=101)
    job_202 = queue.submit("window-202", lambda: body(202), scope="window", target_hwnd=202)

    assert started[101].wait(timeout=1)
    assert started[202].wait(timeout=1)
    release.set()
    assert queue.wait(job_101, timeout_s=2)["result"] == 101
    assert queue.wait(job_202, timeout_s=2)["result"] == 202
    assert queue.status()["parallel_peak"] == 2


def test_desktop_action_queue_async_cancel_pending_job():
    queue = DesktopActionQueue()
    started = threading.Event()
    release = threading.Event()
    ran_second = False

    def first():
        started.set()
        assert release.wait(timeout=2)
        return "first"

    def second():
        nonlocal ran_second
        ran_second = True
        return "second"

    first_id = queue.submit("first", first)
    assert started.wait(timeout=1)
    second_id = queue.submit("second", second)

    assert queue.cancel(second_id)["ok"] is True
    release.set()

    assert queue.wait(first_id, timeout_s=2)["ok"] is True
    canceled = queue.wait(second_id, timeout_s=2)
    assert canceled["ok"] is False
    assert canceled["status"] == "canceled"
    assert ran_second is False
    assert queue.status()["async_canceled"] == 1


def test_desktop_action_queue_async_cancel_running_job_reports_not_interruptible():
    queue = DesktopActionQueue()
    started = threading.Event()
    release = threading.Event()

    def running():
        started.set()
        assert release.wait(timeout=2)
        return "done"

    job_id = queue.submit("running", running)
    assert started.wait(timeout=1)

    canceled = queue.cancel(job_id)
    release.set()

    assert canceled["ok"] is False
    assert canceled["status"] == "running"
    assert "cannot be interrupted" in canceled["error"]
    assert queue.wait(job_id, timeout_s=2)["ok"] is True


def test_desktop_action_queue_async_reports_errors_and_bus_events():
    queue = DesktopActionQueue()
    events = []

    def boom():
        raise RuntimeError("async boom")

    BUS.add_sink(events.append)
    try:
        job_id = queue.submit("bad-async", boom, metadata={"purpose": "test"})
        out = queue.wait(job_id, timeout_s=2)
    finally:
        BUS.remove_sink(events.append)

    assert out["ok"] is False
    assert out["status"] == "error"
    assert out["error"] == "async boom"
    job_events = [
        event for event in events
        if event.get("type") == "desktop_action_queue_job"
        and event.get("job_id") == job_id
    ]
    assert [event["state"] for event in job_events] == [
        "queued",
        "waiting_resources",
        "running",
        "error",
    ]
    assert job_events[0]["metadata"] == {"purpose": "test"}
    assert queue.status()["async_failed"] == 1
