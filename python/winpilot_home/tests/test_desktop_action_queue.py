import threading
import time

import pytest

from winpilot.action import executor
from winpilot.action.desktop_queue import DesktopActionQueue
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
        lambda x, y, button, double, hwnd: calls.append((x, y, button, double, hwnd)),
    )

    BUS.add_sink(events.append)
    try:
        out = executor.click(10, 20, button="left", hwnd=123)
    finally:
        BUS.remove_sink(events.append)

    assert out["ok"] is True
    assert calls == [(10, 20, "left", False, 123)]
    queue_events = [event for event in events if event.get("type") == "desktop_action_queue"]
    assert [(event["state"], event["action"]) for event in queue_events] == [
        ("start", "click"),
        ("done", "click"),
    ]


def test_prepositioned_click_keeps_physical_servo_path(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *_args, **_kwargs: calls.append(("post",)) or True,
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda x, y, button, double, hwnd, prepositioned=False: calls.append((
            "raw", x, y, button, double, hwnd, prepositioned,
        )),
    )

    out = executor.click(30, 40, hwnd=456, prepositioned=True)

    assert out["ok"] is True
    assert out["prepositioned"] is True
    assert calls == [("raw", 30, 40, "left", False, 456, True)]


def test_raw_prepositioned_click_does_not_refocus_or_repeat_slow_move(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(
        executor,
        "_raw_focus_window",
        lambda hwnd: calls.append(("focus", hwnd)),
    )
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (30, 40))
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

    executor._raw_click(30, 40, "left", False, 456, prepositioned=True)

    assert all(call[0] not in {"focus", "move"} for call in calls)
    assert [call[0] for call in calls] == ["send", "send"]
    assert sleeps == [0.008]


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
        lambda waypoints, duration_s: calls.append(("stroke", waypoints, duration_s)),
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
        lambda waypoints, duration_s: calls.append(("stroke", waypoints, duration_s)),
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
    assert [event["state"] for event in job_events] == ["queued", "running", "error"]
    assert job_events[0]["metadata"] == {"purpose": "test"}
    assert queue.status()["async_failed"] == 1
