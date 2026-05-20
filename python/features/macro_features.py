"""
宏录制播放功能
录制 / 播放 / 管理宏 — AHK 优先，pynput 回退
"""
import json, os, time, threading, sys
from datetime import datetime
from pathlib import Path

from features.registry import feature, P, TC, FeatureCategory as F

_MACRO_DIR = Path(__file__).resolve().parent.parent / "macros"
_MACRO_DIR.mkdir(exist_ok=True)

# AHK 后端
_HAS_AHK = False
try:
    from tools.ahk_manager import start_recorder, stop_recorder, recorder_status, is_ahk_available
    _HAS_AHK = is_ahk_available()
except ImportError:
    pass


def _macro_path(name: str) -> Path:
    return _MACRO_DIR / f"{name}.json"


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

    from interaction.actions import ActionHandler
    ah = ActionHandler()

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
                ah("click", x=ev["x"], y=ev["y"], button=button)
            elif ev["type"] == "move":
                from interaction.actions import _ensure_win32
                w32 = _ensure_win32()
                w32['api'].SetCursorPos((int(ev["x"]), int(ev["y"])))
            elif ev["type"] == "key" and ev["pressed"]:
                key = ev["key"]
                if key.startswith("Key."):
                    key = key.replace("Key.", "")
                    special_keys = {"enter": "enter", "tab": "tab", "space": "space",
                                    "backspace": "backspace", "esc": "escape",
                                    "shift": "shift", "ctrl": "ctrl", "alt": "alt"}
                    key = special_keys.get(key.lower(), key)
                ah("hotkey", keys=[key])

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


# ─── 录制器后端（AHK 优先 → pynput 回退） ───

_HAS_PYNPUT = False
try:
    import pynput  # noqa
    _HAS_PYNPUT = True
except ImportError:
    pass

_pynput_recording = {"active": False, "events": [], "start": 0, "thread": None}


def _record_pynput(name: str, include_mouse: bool, include_keys: bool) -> dict:
    """pynput 回退版录制"""
    global _pynput_recording
    if _pynput_recording["active"]:
        return {"success": False, "error": "已有录制在进行中"}
    if not _HAS_PYNPUT:
        return {"success": False, "error": "需要安装 pynput: pip install pynput"}

    from pynput import mouse, keyboard

    _pynput_recording["active"] = True
    _pynput_recording["events"] = []
    _pynput_recording["start"] = time.time()

    def on_click(x, y, button, pressed):
        if not _pynput_recording["active"]:
            return False
        if include_mouse:
            _pynput_recording["events"].append({
                "type": "click", "time": time.time() - _pynput_recording["start"],
                "x": x, "y": y, "button": str(button), "pressed": pressed,
            })

    def on_move(x, y):
        if not _pynput_recording["active"]:
            return False
        if include_mouse:
            if not _pynput_recording["events"] or _pynput_recording["events"][-1].get("type") != "move":
                _pynput_recording["events"].append({
                    "type": "move", "time": time.time() - _pynput_recording["start"],
                    "x": x, "y": y,
                })

    def on_press(key):
        if not _pynput_recording["active"]:
            return False
        if include_keys:
            try:
                k = key.char
            except AttributeError:
                k = str(key)
            _pynput_recording["events"].append({
                "type": "key", "time": time.time() - _pynput_recording["start"],
                "key": k, "pressed": True,
            })

    m_listener = mouse.Listener(on_click=on_click, on_move=on_move)
    k_listener = keyboard.Listener(on_press=on_press)
    m_listener.start()
    k_listener.start()

    # 非阻塞等待: 直到 active=False
    def _wait():
        while _pynput_recording["active"]:
            time.sleep(0.1)
        m_listener.stop()
        k_listener.stop()
        m_listener.join(timeout=2)
        k_listener.join(timeout=2)
    threading.Thread(target=_wait, daemon=True).start()

    return {"success": True, "result": {"recording": True, "name": name}}


def _stop_pynput() -> dict:
    """停止 pynput 录制并保存"""
    global _pynput_recording
    if not _pynput_recording["active"]:
        return {"success": False, "error": "录制器未运行"}
    _pynput_recording["active"] = False
    time.sleep(0.3)  # 等线程退出

    actual_duration = round(time.time() - _pynput_recording["start"], 1)
    events = _pynput_recording["events"]

    name = f"recording_{int(time.time())}"
    data = {
        "name": name, "events": events,
        "duration": actual_duration,
        "created": datetime.now().isoformat(),
        "include_mouse": True, "include_keys": True,
    }
    with open(_macro_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"success": True, "result": {"name": name, "events": len(events), "duration": actual_duration}}


@feature(
    name="macro_start_recording",
    display_name="开始录制宏",
    description="【宏】启动录制（AHK 优先 → pynput 回退），支持 F6 热键启停、鼠标智能过滤。非阻塞，立即返回",
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
    # AHK 优先
    if _HAS_AHK:
        result = start_recorder(name, include_mouse, include_keys)
        if result.get("success"):
            return result
        # AHK 失败时静默回退
    # pynput 回退
    return _record_pynput(name, include_mouse, include_keys)


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
    # AHK 优先
    if _HAS_AHK:
        result = stop_recorder()
        if result.get("success"):
            return result
    # pynput 回退
    return _stop_pynput()


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
