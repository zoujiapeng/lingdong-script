"""
独立宏录制进程
通过 stdin JSON 控制: {type: "start", name: "...", include_mouse: true, include_keys: true}
                    {type: "stop"}
                    {type: "ping"}

通过 stdout JSON 输出事件:
  {type: "status", recording: true/false, events: N, elapsed: 1.5}
  {type: "result", success: true, name: "...", events: N, file: "..."}
  {type: "error", message: "..."}

F6 热键全局切换录制启停
"""

import json
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

_MACRO_DIR = Path(__file__).resolve().parent.parent / "macros"

# --- Internal state ---
_recording = False
_events = []
_start_time = 0
_last_x = None
_last_y = None
_include_mouse = True
_include_keys = True
_macro_name = "unnamed"
_listeners = []
_f6_pressed = False
_stdin_thread = None


def _ensure_dir():
    _MACRO_DIR.mkdir(exist_ok=True)


def _emit(msg: dict):
    """Write a JSON line to stdout."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _on_click(x, y, button, pressed):
    global _recording, _last_x, _last_y
    if not _recording:
        return True
    if not _include_mouse:
        return True
    t = time.time() - _start_time
    _events.append({
        "type": "click",
        "time": round(t, 3),
        "x": x,
        "y": y,
        "button": str(button),
        "pressed": pressed,
    })
    _last_x, _last_y = x, y
    return True


def _on_move(x, y):
    global _recording, _last_x, _last_y
    if not _recording:
        return True
    if not _include_mouse:
        return True
    # Smart filter: skip if moved < threshold pixels
    if _last_x is not None and _last_y is not None:
        dx = abs(x - _last_x)
        dy = abs(y - _last_y)
        if dx < 10 and dy < 10:
            return True
    _last_x, _last_y = x, y
    t = time.time() - _start_time
    _events.append({
        "type": "move",
        "time": round(t, 3),
        "x": x,
        "y": y,
    })
    return True


def _on_press(key):
    global _recording, _f6_pressed
    if not _recording:
        # Check for F6 to start even when not recording
        try:
            if hasattr(key, 'name') and key.name == 'f6' and not _f6_pressed:
                _f6_pressed = True
                return False  # signal to main thread
        except Exception:
            pass
        return True
    if not _include_keys:
        return True
    try:
        k = key.char
    except AttributeError:
        k = str(key)
    t = time.time() - _start_time
    _events.append({
        "type": "key",
        "time": round(t, 3),
        "key": k,
        "pressed": True,
    })
    return True


def _on_release(key):
    global _recording, _f6_pressed
    if not _recording:
        # Check for F6 — toggle recording
        try:
            if hasattr(key, 'name') and key.name == 'f6' and not _f6_pressed:
                _f6_pressed = True
                return False
        except Exception:
            pass
        return True
    if not _include_keys:
        return True
    try:
        k = key.char
    except AttributeError:
        k = str(key)
    t = time.time() - _start_time
    _events.append({
        "type": "key",
        "time": round(t, 3),
        "key": k,
        "pressed": False,
    })
    return True


def _start_recording(name: str, include_mouse: bool = True, include_keys: bool = True):
    global _recording, _events, _start_time, _last_x, _last_y, _include_mouse, _include_keys, _macro_name, _listeners
    if _recording:
        return

    _events = []
    _start_time = time.time()
    _last_x = None
    _last_y = None
    _include_mouse = include_mouse
    _include_keys = include_keys
    _macro_name = name

    from pynput import mouse, keyboard

    m_listener = mouse.Listener(on_click=_on_click, on_move=_on_move)
    k_listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    _listeners = [m_listener, k_listener]
    m_listener.start()
    k_listener.start()

    _recording = True
    _emit({"type": "status", "recording": True, "events": 0, "elapsed": 0})
    _emit({"type": "result", "success": True, "recording": True, "name": name})

    # Periodic status updates
    def _status_loop():
        while _recording:
            time.sleep(0.5)
            if _recording:
                _emit({
                    "type": "status",
                    "recording": True,
                    "events": len(_events),
                    "elapsed": round(time.time() - _start_time, 1),
                })
    threading.Thread(target=_status_loop, daemon=True).start()


def _stop_recording():
    global _recording, _listeners, _events, _f6_pressed
    if not _recording:
        return

    _recording = False
    _f6_pressed = False

    for lst in _listeners:
        try:
            lst.stop()
        except Exception:
            pass
    _listeners = []

    actual_duration = round(time.time() - _start_time, 1)
    events = _events

    # Save to file
    _ensure_dir()
    data = {
        "name": _macro_name,
        "events": events,
        "duration": actual_duration,
        "created": datetime.now().isoformat(),
        "include_mouse": _include_mouse,
        "include_keys": _include_keys,
    }

    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in _macro_name).strip()
    if not safe_name:
        safe_name = f"recording_{int(time.time())}"

    file_path = _MACRO_DIR / f"{safe_name}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _emit({
        "type": "result",
        "success": True,
        "name": _macro_name,
        "file": str(file_path),
        "events": len(events),
        "duration": actual_duration,
    })


def _handle_stdin():
    """Background thread: read JSON commands from stdin."""
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = cmd.get("type", "")
            if t == "start":
                _start_recording(
                    name=cmd.get("name", f"recording_{int(time.time())}"),
                    include_mouse=cmd.get("include_mouse", True),
                    include_keys=cmd.get("include_keys", True),
                )
            elif t == "stop":
                _stop_recording()
            elif t == "ping":
                _emit({"type": "pong", "recording": _recording})
    except (EOFError, SystemExit):
        pass


def main():
    _ensure_dir()

    # Start stdin listener thread
    global _stdin_thread
    _stdin_thread = threading.Thread(target=_handle_stdin, daemon=True)
    _stdin_thread.start()

    _emit({"type": "ready", "pid": os.getpid()})

    # Main thread: wait for F6 hotkey to toggle
    try:
        from pynput import keyboard

        def _on_f6(key):
            global _recording, _f6_pressed
            try:
                if hasattr(key, 'name') and key.name == 'f6' and not _f6_pressed:
                    _f6_pressed = True
                    if _recording:
                        _stop_recording()
                        # Re-create f6 listener after stop (pynput stops on return False)
                        return False
                    else:
                        _start_recording(
                            name=f"recording_{int(time.time())}",
                            include_mouse=True,
                            include_keys=True,
                        )
                    _f6_pressed = False
            except Exception:
                pass
            return True

        # Keep re-creating the F6 listener after it stops
        def _f6_loop():
            while True:
                try:
                    with keyboard.Listener(on_press=_on_f6) as listener:
                        listener.join()
                except Exception:
                    pass
                time.sleep(0.1)

        threading.Thread(target=_f6_loop, daemon=True).start()

        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _stop_recording()


if __name__ == "__main__":
    main()
