"""Human-demonstration recorder → .wps.json with semantic selectors.

Unlike the legacy pynput macro (absolute coords only), this captures, at each
click, the UI element under the cursor via perception fusion (text/role/coords/
source) — the same selector shape agent traces export — so replays re-locate by
text and survive layout/resolution changes.

Design:
- StepBuilder: PURE event→step aggregation (no pynput/fusion). Unit-testable.
- HumanRecorder: thin shell — pynput listeners push raw events onto a queue; a
  worker thread resolves selectors (fusion.observe) + feeds StepBuilder, so a
  slow observe never blocks/drops input events.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from ..config import SCRIPTS_DIR
from ..utils.log import BUS, logger

_DRAG_THRESHOLD = 8   # px: press→release beyond this = drag, else click
_MOVE_SAMPLE = 8      # px: min spacing between sampled drag path points
_MODIFIERS = {"ctrl", "alt", "shift", "cmd"}
# Special (non-printable) keys → emitted as hotkey steps when pressed alone.
_SPECIAL_KEYS = {
    "enter", "tab", "esc", "backspace", "delete", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "insert",
    *(f"f{i}" for i in range(1, 13)),
}


def _dist(ax: int, ay: int, bx: int, by: int) -> float:
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


class StepBuilder:
    """Aggregates normalized input events into .wps.json steps.

    Feed it mouse_press/move/release and key_down/up with NORMALIZED names
    (the pynput→name mapping lives in HumanRecorder). Printable keystrokes
    coalesce into one type_text; modifier combos and special keys become
    hotkey steps; press→release becomes click (or drag if moved far)."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._buf: list[str] = []
        self._held: set[str] = set()
        self._down: dict[str, Any] | None = None

    # ------------------------------------------------------------- keyboard
    def key_down(self, name: str) -> None:
        if name in _MODIFIERS:
            self._held.add(name)
            return
        combo_mods = self._held & {"ctrl", "alt", "cmd"}  # shift alone = uppercase
        if combo_mods:
            self.flush_text()
            keys = sorted(combo_mods) + [name]
            self.steps.append({"tool": "hotkey", "keys": keys})
            return
        if name == "space":
            self._buf.append(" ")
            return
        if len(name) == 1:  # printable character
            self._buf.append(name)
            return
        if name in _SPECIAL_KEYS:
            self.flush_text()
            self.steps.append({"tool": "hotkey", "keys": [name]})

    def key_up(self, name: str) -> None:
        self._held.discard(name)

    def flush_text(self) -> None:
        if self._buf:
            self.steps.append({"tool": "type_text", "text_input": "".join(self._buf)})
            self._buf = []

    # ---------------------------------------------------------------- mouse
    def mouse_press(self, x: int, y: int, button: str, selector: dict | None) -> None:
        self.flush_text()
        self._down = {"x": x, "y": y, "button": button, "selector": selector or {}, "points": [[x, y]]}

    def mouse_move(self, x: int, y: int) -> None:
        if not self._down:
            return
        pts = self._down["points"]
        last = pts[-1]
        if _dist(last[0], last[1], x, y) >= _MOVE_SAMPLE:
            pts.append([x, y])

    def mouse_release(self, x: int, y: int, button: str) -> None:
        down = self._down
        self._down = None
        if not down or down["button"] != button:
            return
        if _dist(down["x"], down["y"], x, y) < _DRAG_THRESHOLD:
            sel = down["selector"]
            step: dict[str, Any] = {"tool": "click", "coords": [down["x"], down["y"]], "button": button}
            if sel.get("text"):
                step["text"] = sel["text"]
            if sel.get("role"):
                step["role"] = sel["role"]
            if sel.get("source"):
                step["source"] = sel["source"]
            self.steps.append(step)
        else:
            mids = down["points"][1:]  # intermediate path points (exclude start)
            self.steps.append({
                "tool": "drag",
                "from": [down["x"], down["y"]],
                "to": [x, y],
                "points": mids,
                "duration": 0.4,
            })

    # ----------------------------------------------------------------- done
    def finish(self) -> list[dict[str, Any]]:
        self.flush_text()
        return self.steps


# pynput key object → normalized name
def _key_name(key: Any) -> str | None:
    """Map a pynput key (KeyCode or Key) to a normalized name, or None."""
    try:
        from pynput import keyboard as kb
    except Exception:
        return None
    if isinstance(key, kb.KeyCode):
        return key.char if key.char else None
    if isinstance(key, kb.Key):
        name = key.name  # e.g. 'enter', 'ctrl_l', 'shift', 'cmd', 'f5'
        if name.startswith("ctrl"):
            return "ctrl"
        if name.startswith("alt"):
            return "alt"
        if name.startswith("shift"):
            return "shift"
        if name in ("cmd", "cmd_l", "cmd_r"):
            return "cmd"
        if name == "page_up":
            return "pageup"
        if name == "page_down":
            return "pagedown"
        return name
    return None


class HumanRecorder:
    """Captures real user input into a .wps.json script (singleton-style)."""

    def __init__(self) -> None:
        self._builder: StepBuilder | None = None
        self._queue: Queue = Queue()
        self._worker: threading.Thread | None = None
        self._mouse_listener = None
        self._kbd_listener = None
        self._stop = threading.Event()
        self._name = ""
        self._lock = threading.Lock()

    def is_recording(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # selector resolution (perception) — overridable in tests
    def _resolve_selector(self, x: int, y: int) -> dict | None:
        try:
            import win32gui
            from ..perception import fusion
            hwnd = win32gui.WindowFromPoint((x, y))
            if hwnd:
                hwnd = win32gui.GetAncestor(hwnd, 2)  # GA_ROOT
            if not hwnd:
                hwnd = win32gui.GetForegroundWindow()
            snap = fusion.observe(hwnd, include_icons=False)
            hit = None
            best_area = None
            for el in snap.elements:
                left, top, right, bottom = el.bbox
                if left <= x <= right and top <= y <= bottom:
                    area = max(1, (right - left) * (bottom - top))
                    if best_area is None or area < best_area:
                        best_area, hit = area, el
            if hit:
                return {"text": hit.text, "role": hit.role, "coords": [x, y], "source": hit.source}
        except Exception as exc:
            logger.debug("human recorder 选择器解析失败: %s", exc)
        return None

    def start(self, name: str = "") -> None:
        with self._lock:
            if self.is_recording():
                return
            from pynput import keyboard as kb, mouse as ms
            self._builder = StepBuilder()
            self._queue = Queue()
            self._stop = threading.Event()
            self._name = name or time.strftime("演示_%Y%m%d-%H%M%S")

            self._mouse_listener = ms.Listener(
                on_click=lambda x, y, button, pressed: self._queue.put(
                    ("click", int(x), int(y), button.name, pressed)),
                on_move=lambda x, y: self._queue.put(("move", int(x), int(y))),
            )
            self._kbd_listener = kb.Listener(
                on_press=lambda k: self._queue.put(("kdown", _key_name(k))),
                on_release=lambda k: self._queue.put(("kup", _key_name(k))),
            )
            self._mouse_listener.start()
            self._kbd_listener.start()
            self._worker = threading.Thread(target=self._drain, daemon=True, name="human-rec")
            self._worker.start()
        BUS.publish("human", state="recording", name=self._name)

    def _drain(self) -> None:
        b = self._builder
        assert b is not None
        while not self._stop.is_set():
            try:
                ev = self._queue.get(timeout=0.2)
            except Empty:
                continue
            kind = ev[0]
            if kind == "click":
                _, x, y, button, pressed = ev
                if pressed:
                    b.mouse_press(x, y, button, self._resolve_selector(x, y))
                else:
                    b.mouse_release(x, y, button)
            elif kind == "move":
                b.mouse_move(ev[1], ev[2])
            elif kind == "kdown" and ev[1]:
                b.key_down(ev[1])
            elif kind == "kup" and ev[1]:
                b.key_up(ev[1])

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.is_recording():
                return {"ok": False, "error": "未在录制"}
            self._stop.set()
            if self._mouse_listener:
                self._mouse_listener.stop()
            if self._kbd_listener:
                self._kbd_listener.stop()
            if self._worker:
                self._worker.join(timeout=3.0)
            builder = self._builder
            name = self._name
            self._worker = None
        # drain any leftover queued events into the builder before finishing
        steps = builder.finish() if builder else []
        from ..recorder import trace as trace_mod
        steps = trace_mod._distill_steps(steps)
        script = {
            "format": "wps/2",
            "name": name,
            "source": "human",
            "created": int(time.time()),
            "steps": steps,
        }
        SCRIPTS_DIR.mkdir(exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "demo"
        path = SCRIPTS_DIR / f"{safe}.wps.json"
        import json
        path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        BUS.publish("human", state="stopped", file=path.name, steps=len(steps))
        return {"ok": True, "file": path.name, "steps": len(steps)}


RECORDER = HumanRecorder()
