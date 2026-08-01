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
from typing import Any

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


def _normalize_key_char(char: str) -> str:
    """Turn pynput's Ctrl+A..Z control characters back into key names."""
    if len(char) == 1 and "\x01" <= char <= "\x1a":
        return chr(ord("a") + ord(char) - 1)
    return char


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
        self._buf_target: dict[str, str] | None = None
        self._held: set[str] = set()
        self._down: dict[str, Any] | None = None

    @staticmethod
    def _targeted(step: dict[str, Any], target: dict[str, str] | None) -> dict[str, Any]:
        return {**step, **({"target_window": dict(target)} if target else {})}

    # ------------------------------------------------------------- keyboard
    def key_down(
        self,
        name: str,
        target_window: dict[str, str] | None = None,
        *,
        pasted_text: str | None = None,
    ) -> None:
        if name in _MODIFIERS:
            self._held.add(name)
            return
        if self._buf and target_window != self._buf_target:
            self.flush_text()
        combo_mods = self._held & {"ctrl", "alt", "cmd"}  # shift alone = uppercase
        if combo_mods:
            self.flush_text()
            if (
                name.casefold() == "v"
                and "ctrl" in combo_mods
                and not combo_mods.intersection({"alt", "cmd"})
                and pasted_text is not None
            ):
                if pasted_text:
                    self.steps.append(self._targeted(
                        {"tool": "type_text", "text_input": pasted_text},
                        target_window,
                    ))
                return
            keys = sorted(combo_mods) + [name]
            self.steps.append(self._targeted({"tool": "hotkey", "keys": keys}, target_window))
            return
        if name == "space":
            self._buf_target = target_window
            self._buf.append(" ")
            return
        if len(name) == 1:  # printable character
            self._buf_target = target_window
            self._buf.append(name)
            return
        if name in _SPECIAL_KEYS:
            self.flush_text()
            self.steps.append(self._targeted({"tool": "hotkey", "keys": [name]}, target_window))

    def key_up(self, name: str) -> None:
        self._held.discard(name)

    def flush_text(self) -> None:
        if self._buf:
            self.steps.append(self._targeted(
                {"tool": "type_text", "text_input": "".join(self._buf)}, self._buf_target))
            self._buf = []
            self._buf_target = None

    # ---------------------------------------------------------------- mouse
    def mouse_press(self, x: int, y: int, button: str, selector: dict | None,
                    target_window: dict[str, str] | None = None) -> None:
        self.flush_text()
        self._down = {
            "x": x, "y": y, "button": button, "selector": selector or {},
            "points": [[x, y]], "target_window": target_window}

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
            step: dict[str, Any] = {
                "tool": "click",
                "coords": [down["x"], down["y"]],
                "button": button,
                # A demonstrated click may only establish focus. The replay's
                # final evidence, rather than a per-click pixel delta, proves
                # whether the demonstrated task completed.
                "allow_no_change": True,
            }
            if sel.get("text"):
                step["text"] = sel["text"]
            if sel.get("role"):
                step["role"] = sel["role"]
            if sel.get("source"):
                step["source"] = sel["source"]
            self.steps.append(self._targeted(step, down.get("target_window")))
        else:
            mids = down["points"][1:]  # intermediate path points (exclude start)
            self.steps.append(self._targeted({
                "tool": "drag",
                "from": [down["x"], down["y"]],
                "to": [x, y],
                "points": mids,
                "duration": 0.4,
            }, down.get("target_window")))

    def mouse_cancel(self) -> None:
        self._down = None

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
        return _normalize_key_char(key.char) if key.char else None
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
        self._control_window: dict[str, Any] | None = None
        self._target_window: dict[str, str] | None = None
        self._action_epoch_hwnd: int | None = None
        self._action_epoch_target: dict[str, str] | None = None
        self._accepted_mouse_button: str | None = None
        self._accepted_mouse_target: dict[str, str] | None = None
        self._accepted_keys: set[str] = set()
        self._listener_modifiers: set[str] = set()

    def is_recording(self) -> bool:
        return self._builder is not None

    def _window_context(self, hwnd: int | None) -> dict[str, Any] | None:
        """Resolve transient identity plus replay-stable title/process metadata."""
        try:
            import win32gui
            import win32process
            from ..perception import win32 as win32_perception

            if not hwnd:
                return None
            root = int(win32gui.GetAncestor(int(hwnd), 2) or hwnd)  # GA_ROOT
            title = str(win32gui.GetWindowText(root) or "").strip()
            _tid, pid = win32process.GetWindowThreadProcessId(root)
            process = str(win32_perception._process_name(int(pid)) or "").strip()
            return {
                "hwnd": root,
                "pid": int(pid),
                "title": title,
                "process": process,
            }
        except Exception as exc:
            logger.debug("human recorder window identity failed: %s", exc)
            return None

    def _window_at_point(self, x: int, y: int) -> dict[str, Any] | None:
        try:
            import win32gui

            return self._window_context(win32gui.WindowFromPoint((x, y)))
        except Exception as exc:
            logger.debug("human recorder point window failed: %s", exc)
            return None

    def _foreground_window(self) -> dict[str, Any] | None:
        try:
            import win32gui

            return self._window_context(win32gui.GetForegroundWindow())
        except Exception as exc:
            logger.debug("human recorder foreground window failed: %s", exc)
            return None

    def _is_control_window(self, context: dict[str, Any] | None) -> bool:
        control = self._control_window
        if not context or not control:
            return False
        if context.get("hwnd") and context.get("hwnd") == control.get("hwnd"):
            return True
        return bool(
            context.get("pid")
            and control.get("pid")
            and context.get("pid") == control.get("pid")
        )

    def _accept_action_window(self, context: dict[str, Any] | None) -> dict[str, str] | None:
        if not context:
            return None
        hwnd = int(context.get("hwnd") or 0)
        if not hwnd:
            return None
        if hwnd != self._action_epoch_hwnd:
            # An input callback can observe a title mutation caused by that
            # same input.  Keep replay metadata stable for one HWND activation
            # epoch and refresh it only after another HWND has been active.
            self._action_epoch_hwnd = hwnd
            self._action_epoch_target = None
            if not self._is_control_window(context):
                title = str(context.get("title") or "").strip()
                process = str(context.get("process") or "").strip()
                if title and process:
                    self._action_epoch_target = {
                        "title": title,
                        "process": process,
                    }
        target = self._action_epoch_target
        if target:
            self._target_window = self._target_window or target
            return dict(target)
        return None

    def _queue_mouse_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        self._queue.put((
            "click",
            int(x),
            int(y),
            button.name,
            bool(pressed),
            self._window_at_point(int(x), int(y)),
        ))

    def _queue_key(self, kind: str, key: Any) -> None:
        name = _key_name(key)
        if not name:
            return
        context = self._foreground_window()
        pasted_text = None
        if kind == "kdown":
            if name in _MODIFIERS:
                self._listener_modifiers.add(name)
            elif (
                name.casefold() == "v"
                and "ctrl" in self._listener_modifiers
                and not self._listener_modifiers.intersection({"alt", "cmd"})
                and not self._is_control_window(context)
            ):
                pasted_text = self._read_clipboard_text()
        self._queue.put((kind, name, context, pasted_text))
        if kind == "kup" and name in _MODIFIERS:
            self._listener_modifiers.discard(name)

    def _read_clipboard_text(self) -> str | None:
        """Snapshot Unicode clipboard text; None preserves non-text paste."""
        import win32clipboard

        last_error: Exception | None = None
        for attempt in range(4):
            opened = False
            try:
                win32clipboard.OpenClipboard()
                opened = True
                if not win32clipboard.IsClipboardFormatAvailable(
                    win32clipboard.CF_UNICODETEXT,
                ):
                    return None
                value = win32clipboard.GetClipboardData(
                    win32clipboard.CF_UNICODETEXT,
                )
                return str(value or "")
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.01)
            finally:
                if opened:
                    win32clipboard.CloseClipboard()
        logger.debug("human recorder clipboard snapshot failed: %s", last_error)
        return None

    # selector resolution (perception) — overridable in tests
    def _resolve_selector(self, x: int, y: int, hwnd: int | None = None) -> dict | None:
        try:
            import win32gui
            from ..perception import fusion
            if hwnd:
                hwnd = win32gui.GetAncestor(int(hwnd), 2) or int(hwnd)  # GA_ROOT
            else:
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
            self._control_window = self._foreground_window()
            self._target_window = None
            self._action_epoch_hwnd = None
            self._action_epoch_target = None
            self._accepted_mouse_button = None
            self._accepted_mouse_target = None
            self._accepted_keys = set()
            self._listener_modifiers = set()

            self._mouse_listener = ms.Listener(
                on_click=self._queue_mouse_click,
                on_move=lambda x, y: self._queue.put(("move", int(x), int(y))),
            )
            self._kbd_listener = kb.Listener(
                on_press=lambda key: self._queue_key("kdown", key),
                on_release=lambda key: self._queue_key("kup", key),
            )
            self._mouse_listener.start()
            self._kbd_listener.start()
            self._worker = threading.Thread(target=self._drain, daemon=True, name="human-rec")
            self._worker.start()
        BUS.publish("human", state="recording", name=self._name)

    def _drain(self) -> None:
        b = self._builder
        assert b is not None
        while not self._stop.is_set() or not self._queue.empty():
            try:
                ev = self._queue.get(timeout=0.2)
            except Empty:
                continue
            kind = ev[0]
            if kind == "click":
                _, x, y, button, pressed, context = ev
                if pressed:
                    target = self._accept_action_window(context)
                    if target:
                        self._accepted_mouse_button = button
                        self._accepted_mouse_target = target
                        hwnd = int(context.get("hwnd") or 0)
                        b.mouse_press(x, y, button, self._resolve_selector(x, y, hwnd), target)
                    else:
                        self._accepted_mouse_button = None
                        self._accepted_mouse_target = None
                        b.mouse_cancel()
                elif self._accepted_mouse_button == button:
                    release_target = self._accept_action_window(context)
                    if release_target == self._accepted_mouse_target:
                        b.mouse_release(x, y, button)
                    else:
                        b.mouse_cancel()
                    self._accepted_mouse_button = None
                    self._accepted_mouse_target = None
            elif kind == "move":
                b.mouse_move(ev[1], ev[2])
            elif kind == "kdown":
                _, name, context, pasted_text = ev
                target = self._accept_action_window(context)
                if target:
                    b.key_down(name, target, pasted_text=pasted_text)
                    self._accepted_keys.add(name)
            elif kind == "kup":
                _, name, _context, _pasted_text = ev
                if name in self._accepted_keys:
                    b.key_up(name)
                    self._accepted_keys.discard(name)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.is_recording():
                return {"ok": False, "error": "未在录制"}
            if not self._stop.is_set():
                if self._mouse_listener:
                    self._mouse_listener.stop()
                if self._kbd_listener:
                    self._kbd_listener.stop()
                self._stop.set()
            if self._worker:
                self._worker.join(timeout=3.0)
                if self._worker.is_alive():
                    return {"ok": False, "error": "recorded input is still draining; try again"}
            builder = self._builder
            name = self._name
            target_window = self._target_window
            self._worker = None
            self._builder = None
            self._mouse_listener = None
            self._kbd_listener = None
            self._control_window = None
            self._target_window = None
            self._action_epoch_hwnd = None
            self._action_epoch_target = None
            self._accepted_mouse_button = None
            self._accepted_mouse_target = None
            self._accepted_keys = set()
            self._listener_modifiers = set()
        steps = builder.finish() if builder else []
        from ..recorder import trace as trace_mod
        steps = trace_mod._distill_steps(steps)
        if not steps:
            error = "recording contains no executable actions"
            BUS.publish("human", state="stopped", ok=False, error=error, steps=0)
            return {"ok": False, "error": error, "steps": 0}
        if not target_window:
            error = "recording has no stable target window"
            BUS.publish("human", state="stopped", ok=False, error=error, steps=len(steps))
            return {"ok": False, "error": error, "steps": len(steps)}
        script = {
            "format": "wps/2",
            "name": name,
            "source": "human",
            "created": int(time.time()),
            "target_window": target_window,
            "steps": steps,
        }
        error = trace_mod.validate_script(script)
        if error:
            BUS.publish("human", state="stopped", ok=False, error=error, steps=len(steps))
            return {"ok": False, "error": error, "steps": len(steps)}
        SCRIPTS_DIR.mkdir(exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "demo"
        path = SCRIPTS_DIR / f"{safe}.wps.json"
        import json
        path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        BUS.publish("human", state="stopped", file=path.name, steps=len(steps))
        return {"ok": True, "file": path.name, "steps": len(steps)}


RECORDER = HumanRecorder()
