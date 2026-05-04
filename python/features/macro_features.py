"""
宏录制播放功能
录制 / 播放 / 管理宏 — 独立进程 (macro_recorder.py) + @feature 接口
"""
import json, os, time, threading
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from features.registry import feature, P, TC, FeatureCategory as F

_MACRO_DIR = Path(__file__).resolve().parent.parent / "macros"
_MACRO_DIR.mkdir(exist_ok=True)

_recording = {"active": False, "events": [], "start": 0, "thread": None}

# ─── 新独立录制器进程管理 ───
_recorder_proc: subprocess.Popen | None = None
_recorder_buf = ""
_recorder_result: dict | None = None
_recorder_lock = threading.Lock()


def _macro_path(name: str) -> Path:
    return _MACRO_DIR / f"{name}.json"


@feature(
    name="macro_record",
    display_name="录制宏（旧版）",
    description="【宏】录制鼠标和键盘操作序列（旧版阻塞式）。建议改用 macro_start_recording（支持 F6 热键启停、非阻塞）",
    category=F.MACRO,
    deprecated=True,
    params=[
        P("name", "str", "宏名称", example="登录流程"),
        P("include_mouse", "boolean", "是否录制鼠标移动", required=False, default=True),
        P("include_keys", "boolean", "是否录制键盘输入", required=False, default=True),
        P("duration", "number", "最大录制秒数", required=False, default=30),
    ],
    returns="dict{name, events, duration} - 录制结果",
    tags=["macro", "recording"],
)
def macro_record(name: str, include_mouse: bool = True, include_keys: bool = True, duration: float = 30) -> dict:
    global _recording
    if _recording["active"]:
        return {"success": False, "error": "已有录制在进行中"}

    try:
        from pynput import mouse, keyboard
    except ImportError:
        return {"success": False, "error": "需要 pynput: pip install pynput"}

    _recording["active"] = True
    _recording["events"] = []
    _recording["start"] = time.time()

    def on_click(x, y, button, pressed):
        if not _recording["active"]:
            return False
        if include_mouse:
            _recording["events"].append({
                "type": "click", "time": time.time() - _recording["start"],
                "x": x, "y": y, "button": str(button), "pressed": pressed,
            })

    def on_move(x, y):
        if not _recording["active"]:
            return False
        if include_mouse:
            if not _recording["events"] or _recording["events"][-1].get("type") != "move":
                _recording["events"].append({
                    "type": "move", "time": time.time() - _recording["start"],
                    "x": x, "y": y,
                })

    def on_press(key):
        if not _recording["active"]:
            return False
        if include_keys:
            try:
                k = key.char
            except AttributeError:
                k = str(key)
            _recording["events"].append({
                "type": "key", "time": time.time() - _recording["start"],
                "key": k, "pressed": True,
            })

    def on_release(key):
        if not _recording["active"]:
            return False
        if include_keys:
            try:
                k = key.char
            except AttributeError:
                k = str(key)
            _recording["events"].append({
                "type": "key", "time": time.time() - _recording["start"],
                "key": k, "pressed": False,
            })

    m_listener = mouse.Listener(on_click=on_click, on_move=on_move)
    k_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    m_listener.start()
    k_listener.start()

    def _stop_after():
        time.sleep(duration)
        _recording["active"] = False
        m_listener.stop()
        k_listener.stop()

    threading.Thread(target=_stop_after, daemon=True).start()
    _recording["active"] = True

    # 等待录制完成
    while _recording["active"]:
        time.sleep(0.1)
    m_listener.join(timeout=2)
    k_listener.join(timeout=2)

    actual_duration = time.time() - _recording["start"]
    events = _recording["events"]
    _recording["active"] = False

    # 保存到文件
    data = {
        "name": name, "events": events,
        "duration": round(actual_duration, 1),
        "created": datetime.now().isoformat(),
        "include_mouse": include_mouse, "include_keys": include_keys,
    }
    with open(_macro_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "result": {"name": name, "events": len(events), "duration": round(actual_duration, 1)},
    }


@feature(
    name="macro_playback",
    display_name="播放宏",
    description="【宏】播放已录制的宏操作序列。用于自动重现录制的操作流程",
    category=F.MACRO,
    params=[
        P("name", "str", "宏名称", example="登录流程"),
        P("speed", "number", "播放速度倍率", required=False, default=1.0),
        P("loop", "int", "循环次数（0=不循环）", required=False, default=0),
    ],
    returns="bool - 是否播放成功",
    tags=["macro", "playback"],
)
def macro_playback(name: str, speed: float = 1.0, loop: int = 0) -> dict:
    path = _macro_path(name)
    if not path.exists():
        return {"success": False, "error": f"宏 '{name}' 不存在"}

    try:
        import pyautogui
    except ImportError:
        return {"success": False, "error": "需要 pyautogui: pip install pyautogui"}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", [])
    if not events:
        return {"success": True, "result": True}

    iterations = loop if loop > 0 else 1
    for _ in range(iterations):
        prev_time = 0
        for ev in events:
            delay = (ev["time"] - prev_time) / speed
            if delay > 0:
                time.sleep(delay)
            prev_time = ev["time"]

            if ev["type"] == "click" and ev["pressed"]:
                button = "left" if "left" in ev.get("button", "") else \
                         "right" if "right" in ev.get("button", "") else "middle"
                pyautogui.click(ev["x"], ev["y"], button=button)
            elif ev["type"] == "move":
                pyautogui.moveTo(ev["x"], ev["y"], duration=0.05)
            elif ev["type"] == "key" and ev["pressed"]:
                key = ev["key"]
                if key.startswith("Key."):
                    key = key.replace("Key.", "")
                    special_keys = {"enter": "enter", "tab": "tab", "space": "space",
                                    "backspace": "backspace", "esc": "escape",
                                    "shift": "shift", "ctrl": "ctrl", "alt": "alt"}
                    key = special_keys.get(key.lower(), key)
                try:
                    pyautogui.write(key, interval=0.01)
                except Exception:
                    pyautogui.press(key)

    return {"success": True, "result": True}


@feature(
    name="macro_list",
    display_name="列出宏",
    description="【宏】列出所有已录制的宏。用于查看和管理已有的宏",
    category=F.MACRO,
    params=[],
    returns="list[dict{name, events, duration, created}] - 宏列表",
    tags=["macro", "management"],
)
def macro_list() -> dict:
    macros = []
    for f in sorted(_MACRO_DIR.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            macros.append({
                "name": data.get("name", f.stem),
                "events": len(data.get("events", [])),
                "duration": data.get("duration", 0),
                "created": data.get("created", ""),
            })
        except Exception:
            macros.append({"name": f.stem, "error": "读取失败"})
    return {"success": True, "result": macros}


# ─── 新独立录制器 @feature 接口 ───

_RECORDER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "macro_recorder.py"


def _spawn_recorder() -> subprocess.Popen | None:
    """启动独立录制器进程，等待 ready 信号"""
    global _recorder_proc, _recorder_buf, _recorder_result
    if _recorder_proc and _recorder_proc.poll() is None:
        return _recorder_proc
    if not _RECORDER_SCRIPT.exists():
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(_RECORDER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        _recorder_proc = proc
        _recorder_buf = ""
        _recorder_result = None
        # Wait for ready signal (timeout 10s)
        deadline = time.time() + 10
        while time.time() < deadline:
            line = proc.stdout.readline()  # type: ignore
            if not line:
                break
            _recorder_buf += line
            try:
                msg = json.loads(line.strip())
                if msg.get("type") == "ready":
                    return proc
            except json.JSONDecodeError:
                continue
        # Timeout — kill and return None
        try:
            proc.kill()
        except Exception:
            pass
        _recorder_proc = None
        return None
    except Exception:
        return None


def _send_to_recorder(cmd: dict, timeout: float = 30) -> dict:
    """向录制器进程发送命令并等待 {type: 'result'} 响应"""
    global _recorder_proc, _recorder_buf, _recorder_result
    proc = _spawn_recorder()
    if not proc or not proc.stdin:
        return {"success": False, "error": "录制器进程不可用"}
    try:
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()
    except Exception as e:
        return {"success": False, "error": f"写入失败: {e}"}
    # 读取 stdout 直到收到 type=result
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = proc.stdout.readline()  # type: ignore
        except Exception:
            break
        if not line:
            break
        _recorder_buf += line
        try:
            msg = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "result":
            return msg
        if msg.get("type") == "error":
            return {"success": False, "error": msg.get("message", "录制器错误")}
        # status / pong — ignore
    return {"success": False, "error": "录制器无响应（超时）"}


@feature(
    name="macro_start_recording",
    display_name="开始录制宏",
    description="【宏】启动独立录制进程，支持 F6 热键启停、鼠标智能过滤。非阻塞，立即返回",
    category=F.MACRO,
    params=[
        P("name", "str", "宏名称", required=False, default=None),
        P("include_mouse", "boolean", "是否录制鼠标", required=False, default=True),
        P("include_keys", "boolean", "是否录制键盘", required=False, default=True),
    ],
    returns="dict{success, recording} - 录制状态",
    tags=["macro", "recording"],
)
def macro_start_recording(name: str | None = None, include_mouse: bool = True, include_keys: bool = True) -> dict:
    if not name:
        name = f"recording_{int(time.time())}"
    result = _send_to_recorder({
        "type": "start",
        "name": name,
        "include_mouse": include_mouse,
        "include_keys": include_keys,
    })
    if result.get("success"):
        return {"success": True, "result": {"recording": True, "name": name}}
    return result


@feature(
    name="macro_stop_recording",
    display_name="停止录制宏",
    description="【宏】停止当前录制，保存宏文件并返回事件统计",
    category=F.MACRO,
    params=[],
    returns="dict{success, name, file, events, duration} - 录制结果",
    tags=["macro", "recording"],
)
def macro_stop_recording() -> dict:
    result = _send_to_recorder({"type": "stop"})
    return result


@feature(
    name="macro_delete",
    display_name="删除宏",
    description="【宏】删除已录制的宏文件",
    category=F.MACRO,
    params=[
        P("name", "str", "宏名称（不含 .json）", example="登录流程"),
    ],
    returns="bool - 是否删除成功",
    tags=["macro", "management"],
)
def macro_delete(name: str) -> dict:
    path = _MACRO_DIR / f"{name.strip()}.json"
    if not path.exists():
        # Try without .json
        path = _MACRO_DIR / name.strip()
    if not path.exists():
        return {"success": False, "error": f"宏 '{name}' 不存在"}
    try:
        path.unlink()
        return {"success": True, "result": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="macro_get_events",
    display_name="获取宏事件",
    description="【宏】读取已录制宏的原始事件数据。用于查看或二次处理",
    category=F.MACRO,
    params=[
        P("name", "str", "宏名称", example="登录流程"),
    ],
    returns="dict{name, events, duration} - 宏事件数据",
    tags=["macro", "management"],
)
def macro_get_events(name: str) -> dict:
    path = _MACRO_DIR / f"{name.strip()}.json"
    if not path.exists():
        return {"success": False, "error": f"宏 '{name}' 不存在"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"success": True, "result": {
            "name": data.get("name", name),
            "events": data.get("events", []),
            "duration": data.get("duration", 0),
            "created": data.get("created", ""),
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="macro_convert_to_flow",
    display_name="宏转流程图",
    description="【宏】将宏事件转换为流程图 FlowScript（CLICK/INPUT_TEXT/KEYBOARD/WAIT 节点）。用于可视化编辑和回放",
    category=F.MACRO,
    params=[
        P("name", "str", "宏名称", example="登录流程"),
    ],
    returns="dict{success, flow} - FlowScript 对象",
    tags=["macro", "flow", "conversion"],
)
def macro_convert_to_flow(name: str) -> dict:
    path = _MACRO_DIR / f"{name.strip()}.json"
    if not path.exists():
        return {"success": False, "error": f"宏 '{name}' 不存在"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"读取失败: {e}"}

    events = data.get("events", [])
    if not events:
        return {"success": False, "error": "宏没有事件数据"}

    nodes = []
    edges = []
    prev_time = 0
    prev_id = "start"

    # Start node
    nodes.append({
        "id": "start", "type": "start",
        "position": {"x": 100, "y": 100},
        "params": {},
    })

    for i, ev in enumerate(events):
        nid = f"n{i}"
        nt = ev.get("type", "")
        t = ev.get("time", 0)
        gap = t - prev_time

        # Add WAIT node if gap >= 300ms
        if gap >= 0.3:
            wid = f"w{i}"
            nodes.append({
                "id": wid, "type": "wait",
                "position": {"x": 100 + len(nodes) * 200, "y": 100},
                "params": {"time": round(gap, 1)},
            })
            edges.append({"id": f"e{prev_id}-{wid}", "from_node": prev_id, "to_node": wid})
            prev_id = wid

        if nt == "click" and ev.get("pressed", False):
            nodes.append({
                "id": nid, "type": "click",
                "position": {"x": 100 + len(nodes) * 200, "y": 100},
                "params": {"x": ev.get("x", 0), "y": ev.get("y", 0)},
            })
            edges.append({"id": f"e{prev_id}-{nid}", "from_node": prev_id, "to_node": nid})
            prev_id = nid

        elif nt == "key" and ev.get("pressed", False):
            k = ev.get("key", "")
            if k.startswith("Key."):
                # Special key → KEYBOARD node
                special = k.replace("Key.", "")
                nodes.append({
                    "id": nid, "type": "keyboard",
                    "position": {"x": 100 + len(nodes) * 200, "y": 100},
                    "params": {"keys": [special]},
                })
            elif len(k) == 1:
                # Single char → INPUT_TEXT
                nodes.append({
                    "id": nid, "type": "input_text",
                    "position": {"x": 100 + len(nodes) * 200, "y": 100},
                    "params": {"text": k},
                })
            else:
                # Multi-char → INPUT_TEXT
                nodes.append({
                    "id": nid, "type": "input_text",
                    "position": {"x": 100 + len(nodes) * 200, "y": 100},
                    "params": {"text": k},
                })
            edges.append({"id": f"e{prev_id}-{nid}", "from_node": prev_id, "to_node": nid})
            prev_id = nid

        prev_time = t

    # End node
    end_id = "end"
    nodes.append({
        "id": end_id, "type": "end",
        "position": {"x": 100 + len(nodes) * 200, "y": 100},
        "params": {},
    })
    edges.append({"id": f"e{prev_id}-{end_id}", "from_node": prev_id, "to_node": end_id})

    flow = {"nodes": nodes, "edges": edges}
    return {"success": True, "result": {"flow": flow, "node_count": len(nodes), "event_count": len(events)}}
