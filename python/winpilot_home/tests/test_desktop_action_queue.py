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
