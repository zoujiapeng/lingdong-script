import json
import threading
import time

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import server

    monkeypatch.setattr(server, "SCRIPTS_DIR", tmp_path)
    with server.STATE.lock:
        server.STATE.runner = None
        server.STATE.thread = None
        server.STATE.replay_thread = None
        server.STATE.replay_jobs.clear()
        server.STATE.stop_event = threading.Event()
    return TestClient(server.app)


def _write_script(root, name="demo.wps.json"):
    path = root / name
    path.write_text(json.dumps({
        "format": "wps/2",
        "name": "demo",
        "steps": [{"tool": "click", "coords": [1, 2]}],
    }), encoding="utf-8")
    return path


def _wait_for(client, replay_id, statuses, timeout=2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/replay/{replay_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in statuses:
            return last
        time.sleep(0.02)
    raise AssertionError(f"replay job did not reach {statuses}: {last}")


def test_replay_returns_queryable_job(client, tmp_path, monkeypatch):
    from winpilot import server

    _write_script(tmp_path)

    def fake_replay(path, stop_event=None):
        return {
            "ok": True,
            "results": [{"index": "1", "tool": "click", "ok": True}],
            "preflight": {"ok": True},
        }

    monkeypatch.setattr(server.trace_mod, "replay", fake_replay)

    created = client.post("/api/replay", json={"script": "demo.wps.json"})
    assert created.status_code == 200
    replay_id = created.json()["replay_id"]
    assert replay_id.startswith("replay-")

    done = _wait_for(client, replay_id, {"done"})
    assert done["ok"] is True
    assert done["result"]["results"][0]["tool"] == "click"
    assert done["finished_at"] is not None


def test_replay_cancel_sets_job_stop_event(client, tmp_path, monkeypatch):
    from winpilot import server

    _write_script(tmp_path)
    started = threading.Event()

    def fake_replay(path, stop_event=None):
        started.set()
        deadline = time.time() + 1.0
        while time.time() < deadline and not stop_event.is_set():
            time.sleep(0.01)
        return {"ok": False, "results": [{"index": "1", "tool": "wait", "ok": False, "stopped": True}]}

    monkeypatch.setattr(server.trace_mod, "replay", fake_replay)

    created = client.post("/api/replay", json={"script": "demo.wps.json"})
    replay_id = created.json()["replay_id"]
    assert started.wait(1.0)

    canceled = client.post(f"/api/replay/{replay_id}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["job"]["status"] == "canceling"

    stopped = _wait_for(client, replay_id, {"stopped", "done"})
    assert stopped["ok"] is False


def test_get_script_as_automation_ir(client, tmp_path):
    _write_script(tmp_path)

    resp = client.get("/api/script/demo.wps.json/ir")

    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "automation-ir/1"
    assert data["source"]["kind"] == "wps"
    assert data["steps"] == [{"tool": "click", "coords": [1, 2]}]
