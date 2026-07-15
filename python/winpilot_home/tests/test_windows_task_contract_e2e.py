"""Real Windows Paint -> managed artifact -> QQ delivery tests.

These tests are intentionally opt-in and make a real external send.  They use
the same HTTP entry as the frontend and do not mock the LLM, Paint, QQ, tools,
or completion verifier.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.request import Request, urlopen

import pytest


pytestmark = [pytest.mark.real, pytest.mark.slow]

TASKS = (
    "用画板画一头猪然后截图打开QQ发送给我的手机",
    "用画板画一只猫然后截图用QQ发给我的手机",
    "把刚画的猫截图发送到QQ的‘我的手机’",
    "在画图里画猫，截取画图窗口，通过QQ发给我的手机",
)


def _post(url: str, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("WINPILOT_E2E_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str) -> dict:
    headers = {}
    token = os.environ.get("WINPILOT_E2E_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_graph(base: str, graph: dict, timeout_s: float) -> dict:
    started = _post(f"{base}/api/v2/arp/runs", {"graph": graph, "inputs": {}})
    run_id = started["run_id"]
    deadline = time.monotonic() + timeout_s
    snapshot = started["run"]
    while time.monotonic() < deadline and snapshot["status"] in {"queued", "running", "paused"}:
        time.sleep(1.0)
        snapshot = _get(f"{base}/api/v2/arp/runs/{run_id}")["run"]
    return snapshot


@pytest.mark.parametrize("task", TASKS, ids=("cold-pig-draw-capture-send", "draw-capture-send", "capture-existing-send", "draw-window-capture-send"))
def test_real_frontend_path_paint_capture_qq_delivery(task: str):
    if os.environ.get("WINPILOT_REAL_QQ_E2E") != "1":
        pytest.skip("set WINPILOT_REAL_QQ_E2E=1 to authorize a real QQ send")
    base = os.environ.get("WINPILOT_E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    compiled = _post(f"{base}/api/v2/task-contracts/compile", {
        "task": task,
        "max_steps": 80,
        "cold_start": True,
    })
    assert compiled["contract"]["satisfiable"] is True
    assert compiled["contract"]["experience_policy"] == "disabled"
    assert "task_stage" in [node["kind"] for node in compiled["graph"]["nodes"]]
    assert "skill_replay" not in [node["kind"] for node in compiled["graph"]["nodes"]]
    draws_in_main_task = any(stage["type"] == "DrawArtifact" for stage in compiled["contract"]["stages"])
    if draws_in_main_task:
        # A new-drawing case must not pass from a prior case's canvas.
        subprocess.run(
            ["taskkill", "/IM", "mspaint.exe", "/F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        time.sleep(0.5)
    else:
        # Referential tasks such as "刚画的" require a real current Paint
        # canvas. Establish that precondition through the same typed compiler
        # and runtime instead of depending on pytest parameter order.
        subprocess.run(
            ["taskkill", "/IM", "mspaint.exe", "/F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        setup = _post(f"{base}/api/v2/task-contracts/compile", {
            "task": "在画图里画一只猫",
            "max_steps": 40,
            "cold_start": True,
        })
        assert setup["contract"]["experience_policy"] == "disabled"
        assert "skill_replay" not in [node["kind"] for node in setup["graph"]["nodes"]]
        setup_run = _run_graph(
            base,
            setup["graph"],
            float(os.environ.get("WINPILOT_REAL_QQ_TIMEOUT_S", "600")),
        )
        assert setup_run["status"] == "done", setup_run
    snapshot = _run_graph(
        base,
        compiled["graph"],
        float(os.environ.get("WINPILOT_REAL_QQ_TIMEOUT_S", "600")),
    )
    assert snapshot["status"] == "done", snapshot
    assert snapshot["artifacts"]["count"] >= 1
    assert any(item.get("predicate") == "delivery.visible_outbound" and item.get("verified") is True for item in snapshot["contract_evidence"]["records"])
    assert all(item.get("kind") != "agent_goal" for item in snapshot["history"])
