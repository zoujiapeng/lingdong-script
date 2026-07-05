import threading
import time

import pytest

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
