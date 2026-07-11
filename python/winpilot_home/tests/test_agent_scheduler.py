from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace


def test_agent_scheduler_create_due_finish_and_cancel(tmp_path, monkeypatch):
    from winpilot import scheduler

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")

    entry = scheduler.create_task(
        "open calculator later",
        name="calc later",
        delay_seconds=0,
        interval_seconds=60,
        max_steps=7,
    )

    assert entry["id"].startswith("agent_sched_")
    assert scheduler.list_tasks()[0]["task"] == "open calculator later"
    assert scheduler.due_tasks(limit=1)[0]["id"] == entry["id"]

    scheduler.mark_started(entry["id"], "run-123")
    assert scheduler.due_tasks() == []

    scheduler.mark_finished(entry["id"], "run-123", "done")
    updated = scheduler.list_tasks()[0]
    assert updated["running"] is False
    assert updated["enabled"] is True
    assert updated["last_run_id"] == "run-123"
    assert updated["run_count"] == 1
    assert updated["next_run_at"]

    assert scheduler.cancel_task(entry["id"]) is True
    assert scheduler.list_tasks()[0]["enabled"] is False


def test_agent_scheduler_accepts_iso_run_at(tmp_path, monkeypatch):
    from winpilot import scheduler

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    scheduler.create_task("future task", run_at=future.isoformat())

    assert scheduler.due_tasks() == []


def test_agent_scheduler_rejects_invalid_run_at(tmp_path, monkeypatch):
    import pytest

    from winpilot import scheduler

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")

    with pytest.raises(ValueError, match="run_at must be an ISO-8601 datetime"):
        scheduler.create_task("bad future task", run_at="tomorrow morning maybe")

    assert scheduler.list_tasks() == []


def test_agent_scheduler_rejects_invalid_delay_seconds(tmp_path, monkeypatch):
    import pytest

    from winpilot import scheduler

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")

    with pytest.raises(ValueError, match="delay_seconds must be a number"):
        scheduler.create_task("bad delay task", delay_seconds="soon")

    assert scheduler.list_tasks() == []


def test_agent_schedule_rest_create_list_cancel(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import scheduler, server

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")

    with TestClient(server.app) as client:
        created = client.post(
            "/api/agent-schedules",
            json={"task": "say hello later", "delay_seconds": 3600, "name": "hello later"},
        )
        assert created.status_code == 200
        schedule_id = created.json()["schedule"]["id"]

        listed = client.get("/api/agent-schedules")
        assert listed.status_code == 200
        assert listed.json()["schedules"][0]["id"] == schedule_id

        cancelled = client.delete(f"/api/agent-schedules/{schedule_id}")
        assert cancelled.status_code == 200
        assert scheduler.list_tasks()[0]["enabled"] is False


def test_agent_schedule_rest_rejects_invalid_run_at(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import scheduler, server

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")

    with TestClient(server.app) as client:
        created = client.post(
            "/api/agent-schedules",
            json={"task": "say hello later", "run_at": "not a datetime"},
        )
        assert created.status_code == 400
        assert "run_at must be an ISO-8601 datetime" in created.text
        assert scheduler.list_tasks() == []


def test_agent_schedule_rest_updates_and_reenables_task(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import scheduler, server

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")
    with TestClient(server.app) as client:
        created = client.post(
            "/api/agent-schedules",
            json={"task": "old task", "delay_seconds": 3600},
        ).json()["schedule"]
        client.delete(f"/api/agent-schedules/{created['id']}")
        updated = client.patch(
            f"/api/agent-schedules/{created['id']}",
            json={"task": "new task", "enabled": True, "delay_seconds": 120, "max_steps": 8},
        )

    assert updated.status_code == 200
    schedule = updated.json()["schedule"]
    assert schedule["task"] == "new task"
    assert schedule["enabled"] is True
    assert schedule["max_steps"] == 8
    assert schedule["next_run_at"]


def test_scheduler_thread_executes_due_task_through_unified_runner(tmp_path, monkeypatch):
    from winpilot import scheduler, server

    monkeypatch.setattr(scheduler, "SCHEDULES_PATH", tmp_path / "agent_schedules.json")
    state = server._RunState()
    state.busy = lambda: False
    scheduler_stop = threading.Event()
    completed = threading.Event()
    calls = {}
    session_rows = []

    class FakeRunner:
        run_id = "scheduled-run-1"
        task = "emit scheduler marker"
        status = "running"
        result_text = ""

        def run(self):
            self.status = "reply"
            self.result_text = "SCHEDULER_THREAD_OK"
            completed.set()
            return {
                "run_id": self.run_id,
                "status": self.status,
                "result": self.result_text,
            }

    def build_runner(task, **kwargs):
        calls.update(task=task, kwargs=kwargs)
        return FakeRunner()

    fake_session = SimpleNamespace(
        recent=lambda: ["prior context"],
        append=lambda *args: session_rows.append(args),
        all=lambda: list(session_rows),
    )
    fake_recorder = SimpleNamespace(start=lambda _run_id: None, stop=lambda: None)

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_SCHEDULER_STOP", scheduler_stop)
    monkeypatch.setattr(server, "_SCHEDULER_THREAD", None)
    monkeypatch.setattr(server, "build_runner", build_runner)
    monkeypatch.setattr(server, "SESSION", fake_session)
    monkeypatch.setattr(server.trace_mod, "RECORDER", fake_recorder)
    monkeypatch.setattr(server.BUS, "publish", lambda *_args, **_kwargs: None)

    entry = scheduler.create_task(
        "emit scheduler marker",
        delay_seconds=0,
        max_steps=3,
    )
    server._start_scheduler_thread()
    try:
        assert completed.wait(timeout=2.5)
        job = state.job_for_run("scheduled-run-1")
        assert job is not None
        assert job.done.wait(timeout=1.0)
        assert job.status == "done"
    finally:
        server._stop_scheduler_thread()
        scheduler_thread = server._SCHEDULER_THREAD
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=1.5)

    updated = scheduler.list_tasks()[0]
    assert updated["id"] == entry["id"]
    assert updated["enabled"] is False
    assert updated["running"] is False
    assert updated["run_count"] == 1
    assert updated["last_run_id"] == "scheduled-run-1"
    assert updated["last_status"] == "reply"
    assert calls["task"] == "emit scheduler marker"
    assert calls["kwargs"]["max_steps_override"] == 3
    assert calls["kwargs"]["session_context"] == ["prior context"]
    assert session_rows[0][1:] == ("reply", "SCHEDULER_THREAD_OK", "scheduled-run-1")
