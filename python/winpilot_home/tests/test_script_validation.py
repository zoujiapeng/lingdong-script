"""validate_script (pure) + GET/PUT /api/script HTTP round-trip."""
import json

import pytest

from winpilot.recorder import trace as trace_mod


# ----------------------------------------------------------- validate_script
def test_valid_v1_script():
    assert trace_mod.validate_script(
        {"format": "wps/1", "steps": [{"tool": "click"}]}) is None


def test_valid_v2_nested_script():
    script = {
        "format": "wps/2",
        "vars": {"a": "1"},
        "steps": [
            {"tool": "ctrl.if", "then": [{"tool": "click"}],
             "else": [{"tool": "ext.delay", "seconds": 1}]},
            {"tool": "ctrl.loop", "body": [{"tool": "type_text"}]},
        ],
    }
    assert trace_mod.validate_script(script) is None


def test_reject_bad_format():
    assert "format" in trace_mod.validate_script({"format": "x", "steps": []})


def test_reject_non_dict():
    assert trace_mod.validate_script([1, 2]) is not None


def test_reject_steps_not_list():
    assert "steps" in trace_mod.validate_script({"format": "wps/2", "steps": {}})


def test_reject_unknown_tool():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "evil.exec"}]})
    assert "未知步骤" in err


def test_reject_unknown_tool_nested_in_branch():
    script = {"format": "wps/2", "steps": [
        {"tool": "ctrl.if", "then": [{"tool": "nope"}]},
    ]}
    err = trace_mod.validate_script(script)
    assert "nope" in err and "then" in err


def test_reject_bad_vars():
    assert "vars" in trace_mod.validate_script(
        {"format": "wps/2", "steps": [], "vars": "nope"})


# ------------------------------------------------------------- HTTP endpoints
@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import server
    monkeypatch.setattr(server, "SCRIPTS_DIR", tmp_path)
    return TestClient(server.app)


def test_put_then_get_roundtrip(client):
    script = {"format": "wps/2", "name": "t",
              "steps": [{"tool": "click", "coords": [1, 2]}]}
    put = client.put("/api/script/demo.wps.json", json={"script": script})
    assert put.status_code == 200 and put.json()["ok"] is True
    got = client.get("/api/script/demo.wps.json")
    assert got.status_code == 200
    assert got.json()["steps"][0]["tool"] == "click"


def test_put_rejects_invalid_script(client):
    bad = {"format": "wps/2", "steps": [{"tool": "rm -rf"}]}
    resp = client.put("/api/script/bad.wps.json", json={"script": bad})
    assert resp.status_code == 400


def test_get_missing_script_404(client):
    assert client.get("/api/script/none.wps.json").status_code == 404


def test_reject_non_wps_filename(client):
    assert client.get("/api/script/passwd.txt").status_code == 400


def test_path_traversal_blocked(client):
    # ..%2f style traversal must not escape SCRIPTS_DIR
    resp = client.get("/api/script/..%2f..%2fconfig.wps.json")
    assert resp.status_code in (400, 404)
