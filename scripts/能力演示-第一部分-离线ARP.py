"""能力演示 · 第一部分：canonical ARP 离线流程（0 模型调用）。

把一条 ARP 图提交给本机 WinPilot 服务执行，覆盖全部可离线执行的 ARP 节点：
start/end、action_batch（单击/输入/键盘/悬停/拖拽/滚轮/截图）、variable_set、
condition、delay、evidence_gate、replay_step（运行命令/写剪贴板）、skill_replay。
执行后逐项对照独立 oracle（靶场写出的 JSON）核验，并生成 Studio 可打开的 .flow。

用法：
    先启动服务（见“能力演示-一键运行.py”会自动处理）
    python scripts/能力演示-第一部分-离线ARP.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "output" / "能力演示"
ORACLE = OUT_DIR / "靶场状态.json"
REPORT = OUT_DIR / "第一部分-离线ARP报告.json"
FLOW = REPO / "python" / "winpilot_home" / "eval_targets" / "能力演示.flow"

PORT = os.environ.get("WINPILOT_SERVE_PORT", "8765")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = os.environ.get("WINPILOT_CONTROL_TOKEN", "ecc-demo-token")


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _center(box: dict) -> tuple[int, int]:
    return int(box["x"] + box["w"] / 2), int(box["y"] + box["h"] / 2)


def _clipboard_get():
    """读取当前剪贴板文本，用于演示后恢复，避免清掉用户内容。"""
    try:
        import win32clipboard as cb
        cb.OpenClipboard()
        try:
            return cb.GetClipboardData(cb.CF_UNICODETEXT)
        finally:
            cb.CloseClipboard()
    except Exception:
        return None


def _clipboard_set(text: str) -> None:
    try:
        import win32clipboard as cb
        cb.OpenClipboard()
        cb.EmptyClipboard()
        cb.SetClipboardText(text, cb.CF_UNICODETEXT)
        cb.CloseClipboard()
    except Exception:
        pass


def _trace_summary(run_id: str) -> tuple[int, dict | None]:
    """从运行 trace 里数“模型相关事件数”，并取 trace 完整性。"""
    path = REPO / "python" / "winpilot_home" / "traces" / f"{run_id}.jsonl"
    model, integrity = 0, None
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if "model" in str(event.get("type", "")):
                model += 1
            if event.get("type") == "trace_integrity":
                integrity = event
    return model, integrity


def build_graph(o: dict, nonce: str, window_title: str) -> dict:
    w = o["widgets"]
    click = _center(w["click_btn"])
    entry = _center(w["entry"])
    submit = _center(w["submit_btn"])
    hover = _center(w["hover_label"])
    progress = _center(w["progress_btn"])
    scroll = _center(w["scroll_area"])
    image = _center(o["image_bbox"])
    src, dst = (o["drag_src"]["x"], o["drag_src"]["y"]), (o["drag_dst"]["x"], o["drag_dst"]["y"])
    text = "演示文本-7391"
    marker = f"DEMO-CMD-{int(time.time())}"  # 文件名/内容用 ASCII，避免 PowerShell 编码问题
    nonce_file = str(OUT_DIR / "运行命令产物.txt")

    prep = {"actions": [
        {"action": "focus_window", "title": window_title},
        {"action": "clipboard_write", "text": nonce},
        {"action": "wait", "duration_ms": 200},
    ]}
    act = {"actions": [
        {"action": "click", "x": click[0], "y": click[1], "coordinate_space": "screen_physical_px"},
        {"action": "click", "x": entry[0], "y": entry[1], "coordinate_space": "screen_physical_px"},
        {"action": "type_text", "text": text},
        {"action": "click", "x": submit[0], "y": submit[1], "coordinate_space": "screen_physical_px"},
        {"action": "move_mouse", "x": hover[0], "y": hover[1], "coordinate_space": "screen_physical_px"},
        {"action": "move_mouse", "x": 20, "y": 20, "coordinate_space": "screen_physical_px"},
        {"action": "drag", "x1": src[0], "y1": src[1], "x2": dst[0], "y2": dst[1]},
        {"action": "scroll", "x": scroll[0], "y": scroll[1], "amount": -360},
        {"action": "click", "x": progress[0], "y": progress[1], "coordinate_space": "screen_physical_px"},
        {"action": "click", "x": image[0], "y": image[1], "coordinate_space": "screen_physical_px"},
        {"action": "screenshot", "managed_artifact": True},
    ]}
    replay1 = {"session": "演示", "step": {"tool": "ext.run_shell", "command":
        f"powershell -NoProfile -Command \"Set-Content -LiteralPath '{nonce_file}' -Value '{marker}' -Encoding Ascii\""}}
    replay2 = {"session": "演示", "step": {"tool": "ext.clipboard_set", "text": nonce}}
    nodes = [
        {"id": "start", "kind": "start", "name": "开始", "config": {}},
        {"id": "prep", "kind": "action_batch", "name": "聚焦+写剪贴板+等待", "config": prep},
        {"id": "setvar", "kind": "set_variable", "name": "设置阶段变量", "config": {"name": "stage", "value": "armed"}},
        {"id": "cond", "kind": "condition", "name": "阶段是否为 armed", "config": {"source": "variables.stage", "operator": "eq", "value": "armed"}},
        {"id": "replay1", "kind": "replay_step", "name": "离线重放·运行命令", "config": replay1},
        {"id": "replay2", "kind": "replay_step", "name": "离线重放·写剪贴板", "config": replay2},
        {"id": "skill", "kind": "skill_replay", "name": "技能重放（未命中）", "config": {"task": "__无此技能_演示__"}},
        {"id": "act", "kind": "action_batch", "name": "全部界面动作", "config": act},
        {"id": "delay", "kind": "delay", "name": "等待进度完成", "config": {"duration_ms": 4000}},
        {"id": "gate", "kind": "evidence_gate", "name": "截图证据门", "config": {"required": [{"predicate": "artifact.screenshot"}]}},
        {"id": "end", "kind": "end", "name": "结束", "config": {}},
        {"id": "noend", "kind": "end", "name": "否分支", "config": {}},
        {"id": "failend", "kind": "end", "name": "门未过", "config": {}},
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "prep"},
        {"id": "e2", "source": "prep", "target": "setvar"},
        {"id": "e3", "source": "setvar", "target": "cond"},
        {"id": "e4", "source": "cond", "target": "replay1", "branch": "yes"},
        {"id": "e5", "source": "cond", "target": "noend", "branch": "no"},
        {"id": "e6", "source": "replay1", "target": "replay2"},
        {"id": "e7", "source": "replay2", "target": "skill"},
        {"id": "e8", "source": "skill", "target": "act", "branch": "miss"},
        {"id": "e9", "source": "skill", "target": "act", "branch": "next"},
        {"id": "e10", "source": "act", "target": "delay"},
        {"id": "e11", "source": "delay", "target": "gate"},
        {"id": "e12", "source": "gate", "target": "end", "branch": "pass"},
        {"id": "e13", "source": "gate", "target": "failend", "branch": "fail"},
    ]
    return {"format": "arp/1", "id": "能力演示", "name": "WinPilot 能力演示（canonical ARP）",
            "nodes": nodes, "edges": edges, "metadata": {"演示": True, "预期模型调用": 0, "nonce": nonce, "标记": marker, "文本": text}}


_KIND_TO_TYPE = {"start": "start", "end": "end", "action_batch": "action_batch",
                 "set_variable": "variable_set", "condition": "condition", "delay": "wait",
                 "evidence_gate": "evidence_gate", "replay_step": "replay_step", "skill_replay": "skill_replay"}


def _flow_params(node: dict) -> dict:
    """把 ARP 节点配置翻译成 Studio(前端) 节点参数。

    前端 `flowToArpGraph` 对 condition/wait 有既定参数约定，不能直接复用 ARP 配置，
    否则会编译成 legacy_flow 而被 fail-closed 拒绝。
    """
    kind = node["kind"]
    if kind == "condition":
        return {"condition_type": "variable_equals", "variable_name": "stage", "variable_value": "armed"}
    if kind == "delay":
        return {"wait_type": "time", "duration": 4}
    return node.get("config", {})


def to_flow(graph: dict) -> dict:
    nodes, y = [], 80
    for n in graph["nodes"]:
        nodes.append({"id": n["id"], "type": _KIND_TO_TYPE.get(n["kind"], n["kind"]),
                      "x": 80 + (0 if n["kind"] in {"start", "end"} else 260), "y": y,
                      "label": n.get("name", n["id"]), "params": _flow_params(n)})
        y += 150
    edges = [{"id": e["id"], "from_node": e["source"], "from_port": e.get("branch", "next"),
              "to_node": e["target"], "to_port": "input"} for e in graph["edges"]]
    return {"meta": {"id": "能力演示", "name": "WinPilot 能力演示", "version": "1.0.0",
                     "created_at": "2026-10-14T00:00:00.000Z", "updated_at": "2026-10-14T00:00:00.000Z"},
            "nodes": nodes, "edges": edges, "variables": {},
            "settings": {"default_delay": 0, "error_handling": "stop", "max_retries": 0, "retry_delay": 0, "log_level": "info"}}


def _cmd_ok(nonce: str) -> bool:
    f = OUT_DIR / "运行命令产物.txt"
    return f.exists() and f.read_text(encoding="utf-8").lstrip("\ufeff").strip() == nonce


def main() -> int:
    o = json.loads(ORACLE.read_text(encoding="utf-8"))
    nonce = f"演示-剪贴板-{int(time.time())}"
    graph = build_graph(o, nonce, o["window_title"])
    (OUT_DIR / "ARP图.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    FLOW.write_text(json.dumps(to_flow(graph), ensure_ascii=False, indent=2), encoding="utf-8")

    saved_clip = _clipboard_get()
    resp = _req("POST", "/api/v2/arp/runs", {"graph": graph, "inputs": {}, "runtime_version": "legacy"})
    run_id = resp["run_id"]
    print(f"ARP 运行: {run_id}")
    status = "queued"
    for _ in range(90):
        time.sleep(2)
        run = _req("GET", f"/api/v2/arp/runs/{run_id}")["run"]
        status = run["status"]
        if status in {"done", "failed", "canceled"}:
            break
    run = _req("GET", f"/api/v2/arp/runs/{run_id}")["run"]
    after = json.loads(ORACLE.read_text(encoding="utf-8"))
    model_calls, integrity = _trace_summary(run_id)
    if saved_clip is not None:
        _clipboard_set(saved_clip)  # 演示结束，恢复用户原本的剪贴板

    bad_nodes = [h["node_id"] for h in run["history"] if not h.get("ok")]
    checks = [
        ("运行完成", status == "done", status),
        ("全部节点成功", not bad_nodes, "全部通过" if not bad_nodes else bad_nodes),
        ("模型调用次数=0", model_calls == 0, model_calls),
        ("trace 完整", bool(integrity and integrity.get("complete")),
         f"接收 {integrity.get('accepted_events')}/丢弃 {integrity.get('dropped_events')}/写失败 {integrity.get('write_failures')}" if integrity else "无"),
        ("单击", after["click_count"] >= 1, after["click_count"]),
        ("输入并提交", after["submitted_text"] == graph["metadata"]["文本"], after["submitted_text"]),
        ("键盘事件", len(after["key_events"]) > 0, len(after["key_events"])),
        ("悬停", after["hover_enter_count"] >= 1, after["hover_enter_count"]),
        ("拖拽", after["drag_count"] >= 1, after["drag_count"]),
        ("滚轮", after["scroll_events"] >= 1, after["scroll_events"]),
        ("剪贴板", after["clipboard_seen"] == nonce, after["clipboard_seen"]),
        ("颜色块", after["color_rgb"] == [18, 52, 86], after["color_rgb"]),
        ("进度条", after["progress_done"] is True, after["progress_value"]),
        ("图像点击（坐标）", after["image_click_count"] >= 1, after["image_click_count"]),
        ("截图工件", len(run.get("artifacts", {}).get("artifacts", [])) >= 1, run.get("artifacts", {}).get("count")),
        ("运行命令生成文件", _cmd_ok(graph["metadata"]["标记"]), "已生成" if _cmd_ok(graph["metadata"]["标记"]) else "缺失"),
    ]
    print("\n=== 第一部分：canonical ARP（离线、0 token） ===")
    failed = 0
    for name, ok, detail in checks:
        print(f"  [{'通过' if ok else '未过'}] {name}: {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} 通过（模型调用 {model_calls}）")
    REPORT.write_text(json.dumps({
        "运行ID": run_id, "状态": status, "模型调用次数": model_calls,
        "trace完整性": integrity,
        "检查": [{"项": c[0], "通过": bool(c[1]), "详情": str(c[2])[:120]} for c in checks],
        "靶场原始快照": after,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {REPORT}\n流程文件: {FLOW}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

