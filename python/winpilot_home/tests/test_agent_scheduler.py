from datetime import datetime, timedelta, timezone


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
