from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest


def test_runtime_jobs_serialize_shared_resource_but_allow_independent_work():
    from winpilot.runtime_jobs import RuntimeJobManager

    manager = RuntimeJobManager()
    desktop_active = 0
    desktop_max = 0
    independent_ran = threading.Event()
    lock = threading.Lock()

    def desktop_job(stop_event):
        nonlocal desktop_active, desktop_max
        with lock:
            desktop_active += 1
            desktop_max = max(desktop_max, desktop_active)
        time.sleep(0.05)
        with lock:
            desktop_active -= 1
        return {"ok": True}

    first = manager.submit("agent", desktop_job, resources=("desktop",))
    second = manager.submit("replay", desktop_job, resources=("desktop",))
    independent = manager.submit(
        "trace-conversion",
        lambda stop: independent_ran.set() or {"ok": True},
        resources=(),
    )

    assert independent_ran.wait(0.5)
    assert first.done.wait(1.0)
    assert second.done.wait(1.0)
    assert independent.done.wait(1.0)
    assert desktop_max == 1
    assert {first.status, second.status, independent.status} == {"done"}


def test_runtime_job_cancel_propagates_stop_event():
    from winpilot.runtime_jobs import RuntimeJobManager

    manager = RuntimeJobManager()
    started = threading.Event()

    def runner(stop_event):
        started.set()
        stop_event.wait(1.0)
        return {"ok": False, "error": "stopped"}

    job = manager.submit("replay", runner, resources=("desktop",))
    assert started.wait(0.5)
    assert manager.cancel(job.id) is job
    assert job.done.wait(1.0)
    assert job.status == "canceled"


def test_runtime_job_reports_active_jobs_and_runs_finish_callback():
    from winpilot.runtime_jobs import RuntimeJobManager

    manager = RuntimeJobManager()
    release = threading.Event()
    finished = []
    job = manager.submit(
        "agent",
        lambda stop: release.wait(1.0) or {"ok": True},
        resources=("interactive_runtime",),
        metadata={"run_id": "run-1"},
        on_finish=lambda completed: finished.append((completed.id, completed.status)),
    )

    deadline = time.time() + 0.5
    while job.status != "running" and time.time() < deadline:
        time.sleep(0.01)
    assert manager.active(resource="interactive_runtime") == [job]
    release.set()
    assert job.done.wait(1.0)
    assert finished == [(job.id, "done")]


def test_runtime_job_submit_rolls_back_when_thread_start_fails(monkeypatch):
    from winpilot.runtime_jobs import RuntimeJobManager

    manager = RuntimeJobManager()
    finished = []

    def fail_start(_thread):
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(threading.Thread, "start", fail_start)

    with pytest.raises(RuntimeError, match="thread start failed"):
        manager.submit(
            "arp",
            lambda _stop: {"ok": True},
            resources=("interactive_runtime",),
            job_id="job-start-failure",
            on_finish=lambda job: finished.append(job),
        )

    assert manager.get("job-start-failure") is None
    assert manager.busy(resource="interactive_runtime") is False
    assert len(finished) == 1
    assert finished[0].status == "failed"
    assert finished[0].done.is_set()


def test_queued_runner_registration_does_not_replace_current_runner():
    from winpilot.server import _RunState

    state = _RunState()
    running = SimpleNamespace(run_id="running")
    queued = SimpleNamespace(run_id="queued")
    state.bind_runner(running, "job-running")
    state.register_runner(queued, "job-queued")

    assert state.runner is running
    assert state.runners["queued"] is queued
    assert state.run_jobs["queued"] == "job-queued"
