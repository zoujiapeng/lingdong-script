"""Trace recording, script export, and offline (0-token) replay.

- TraceRecorder: a BUS sink that writes every event of the active run to
  ``traces/<run_id>.jsonl`` and saves a screenshot after each action tool.
- export_script: distill a trace into a ``.wps.json`` replay script whose
  steps carry multi-level selectors (element text → recorded coords).
- replay: execute a script offline, re-locating elements on the live screen
  through the same perception fusion, with the same verify engine.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import cv2

from ..action import executor, verify
from ..config import SCRIPTS_DIR, TRACES_DIR
from ..perception import fusion, win32
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture
from . import replay_control

_SCREENSHOT_TOOLS = {
    "click",
    "type_text",
    "hotkey",
    "launch",
    "drag",
    "scroll",
    "draw_strokes",
}
_BATCH_SCREENSHOT_TOOLS = {
    "click",
    "click_element",
    "type_text",
    "hotkey",
    "launch",
    "drag",
    "scroll",
    "draw_strokes",
    "focus_window",
    "clipboard_paste",
}
_SCREENSHOT_MAX_W = 960

# Volatile tokens that make a text selector unstable across runs (unread
# counts, badges) — strip them so "计技25A葛瑞念 消息: 132" → "计技25A葛瑞念".
_VOLATILE_PATTERNS = [
    re.compile(r"\s*消息[:：]\s*\d+"),
    re.compile(r"\s*\d+\s*条(新)?消息"),
    re.compile(r"\s*\(\d+\)\s*$"),
    re.compile(r"\s*\[\d+\]\s*$"),
    re.compile(r"\s*未读\s*\d+"),
]


def clean_selector_text(text: str) -> str:
    """Strip volatile tokens from a text selector; collapse whitespace."""
    if not text:
        return text
    out = text
    for pat in _VOLATILE_PATTERNS:
        out = pat.sub("", out)
    return re.sub(r"\s+", " ", out).strip() or text


class TraceRecorder:
    """Singleton sink: call start(run_id) before a run, stop() after."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_id: str | None = None
        self._file = None
        self._step = 0
        BUS.add_sink(self._on_event)

    def start(self, run_id: str) -> None:
        with self._lock:
            self._close()
            TRACES_DIR.mkdir(exist_ok=True)
            (TRACES_DIR / run_id).mkdir(exist_ok=True)
            self._file = open(TRACES_DIR / f"{run_id}.jsonl", "a", encoding="utf-8")
            self._run_id = run_id
            self._step = 0
        BUS.publish("trace", state="recording", run_id=run_id)

    def stop(self) -> None:
        with self._lock:
            run_id = self._run_id
            self._close()
        if run_id:
            BUS.publish("trace", state="stopped", run_id=run_id)

    def _close(self) -> None:
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = None
        self._run_id = None

    def _on_event(self, event: dict[str, Any]) -> None:
        # Phase 1 (locked): claim a step number; never do I/O while locked.
        with self._lock:
            if self._file is None or event.get("type") == "trace":
                return
            run_id = self._run_id
            step = None
            phase = "after"
            if event.get("type") == "agent_tool_start" and event.get("tool") in _SCREENSHOT_TOOLS:
                self._step += 1
                step = self._step
                phase = "before"
            elif event.get("type") == "agent_tool" and event.get("tool") in _SCREENSHOT_TOOLS:
                step = self._step or 1
                phase = "after"
            elif (
                event.get("type") == "agent_batch_action_start"
                and event.get("tool") in _BATCH_SCREENSHOT_TOOLS
            ):
                self._step += 1
                step = self._step
                phase = "batch_before"
            elif (
                event.get("type") == "agent_batch_action"
                and event.get("tool") in _BATCH_SCREENSHOT_TOOLS
            ):
                step = self._step or 1
                phase = "batch_after"
        # Phase 2 (unlocked): screen grab + JPEG encode are slow.
        record = dict(event)
        if step is not None:
            shot = self._save_screenshot(run_id, step, phase)
            if shot:
                record["screenshot"] = shot
        # Phase 3 (locked): append, re-checking the file is still open.
        with self._lock:
            if self._file is None or self._run_id != run_id:
                return
            try:
                self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                self._file.flush()
            except OSError as exc:
                logger.warning("trace 写入失败: %s", exc)

    def _save_screenshot(self, run_id: str | None, step: int, phase: str = "after") -> str | None:
        if not run_id:
            return None
        try:
            image = capture()
            h, w = image.shape[:2]
            if w > _SCREENSHOT_MAX_W:
                scale = _SCREENSHOT_MAX_W / w
                image = cv2.resize(image, (_SCREENSHOT_MAX_W, int(h * scale)))
            rel = f"{run_id}/{phase}_{step:03d}.jpg"
            path = TRACES_DIR / rel
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                path.write_bytes(buf.tobytes())
                return rel
        except Exception as exc:
            logger.debug("trace 截图失败: %s", exc)
        return None


RECORDER = TraceRecorder()


# ------------------------------------------------------------------ listing
def list_traces() -> list[dict[str, Any]]:
    """Trace runs on disk, newest first, with task + status summary."""
    TRACES_DIR.mkdir(exist_ok=True)
    out: list[dict[str, Any]] = []
    for path in sorted(TRACES_DIR.glob("*.jsonl"), reverse=True):
        info: dict[str, Any] = {"run_id": path.stem, "task": "", "status": "",
                                "steps": 0, "size_kb": round(path.stat().st_size / 1024, 1)}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    event = json.loads(line)
                    if event.get("type") == "agent_start":
                        info["task"] = event.get("task", "")
                    elif event.get("type") == "agent_tool":
                        info["steps"] += 1
                    elif event.get("type") == "agent_done":
                        info["status"] = event.get("status", "")
        except (json.JSONDecodeError, OSError):
            pass
        out.append(info)
    return out


def read_trace(run_id: str, limit: int = 2000) -> list[dict[str, Any]]:
    path = TRACES_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break
    return events


# ------------------------------------------------------------------- export
def _distill_steps(steps: list[dict]) -> list[dict]:
    """Clean a raw replay-step list into a reusable procedure: clean volatile
    text selectors and drop consecutive duplicate steps (re-tries/floundering).
    Conservative — keeps every distinct action, only removes obvious noise."""
    distilled: list[dict] = []
    prev_key = None
    for raw in steps:
        step = dict(raw)
        step.pop("elapsed_ms", None)
        for field in ("text", "element_text"):
            if step.get(field):
                step[field] = clean_selector_text(step[field])
        key = _semantic_step_key(step)
        if key == prev_key:  # identical consecutive step → drop the repeat
            continue
        prev_key = key
        distilled.append(step)
    return distilled


def _semantic_step_key(step: dict) -> str:
    clean = {k: v for k, v in step.items() if k not in {"elapsed_ms"}}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)


def _extract_replay_steps(events: list[dict[str, Any]]) -> list[dict]:
    steps: list[dict] = []
    for event in events:
        replay = event.get("replay")
        if not replay:
            continue
        if event.get("type") == "agent_batch_action":
            steps.append(replay)
        elif event.get("type") == "agent_tool" and event.get("tool") != "run_action_batch":
            steps.append(replay)
    return steps


def export_script(run_id: str, name: str | None = None) -> Path:
    """Distill a trace into a replayable .wps.json script."""
    events = read_trace(run_id)
    if not events:
        raise FileNotFoundError(f"trace 不存在: {run_id}")
    task = next(
        (e.get("task", "") for e in events if e.get("type") == "agent_start"), "")
    steps = _extract_replay_steps(events)
    if not steps:
        raise ValueError("trace 中没有可重放的动作步骤")
    steps = _distill_steps(steps)
    script = {
        "format": "wps/2",
        "name": name or task[:60] or run_id,
        "task": task,
        "source": "agent",
        "source_run": run_id,
        "created": int(time.time()),
        "steps": steps,
    }
    err = validate_script(script)
    if err:
        raise ValueError(f"exported .wps.json is not replayable: {err}")
    SCRIPTS_DIR.mkdir(exist_ok=True)
    path = SCRIPTS_DIR / f"{name or run_id}.wps.json"
    path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    BUS.publish("trace", state="exported", run_id=run_id, script=str(path))
    return path


def list_scripts() -> list[dict[str, Any]]:
    SCRIPTS_DIR.mkdir(exist_ok=True)
    out = []
    for path in sorted(SCRIPTS_DIR.glob("*.wps.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({
                "file": path.name, "name": data.get("name", path.stem),
                "task": data.get("task", ""), "steps": len(data.get("steps", [])),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return out


# ---------------------------------------------------------------- validation
# Tools the offline Replayer understands: leaf actions + control flow + ext.*.
LEAF_TOOLS = frozenset({
    "click", "type_text", "hotkey", "launch", "focus_window",
    "scroll", "drag", "wait", "run_shell", "tray_click",
    "draw_strokes", "run_detached",
})
CTRL_TOOLS = frozenset({"ctrl.if", "ctrl.loop"})
EXT_TOOLS = frozenset({
    "ext.delay", "ext.var_set", "ext.clipboard_set",
    "ext.clipboard_get", "ext.run_shell", "ext.screenshot",
})
KNOWN_TOOLS = LEAF_TOOLS | CTRL_TOOLS | EXT_TOOLS


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _target_window_hint_from(value: Any, source: str) -> dict[str, Any] | None:
    """Normalize optional replay target-window metadata."""
    if isinstance(value, str):
        title = value.strip()
        return {"source": source, "title": title} if title else None
    if not isinstance(value, dict):
        return None

    hint: dict[str, Any] = {"source": source}
    title = (
        str(value.get("title") or value.get("window_title") or value.get("name") or "")
        .strip()
    )
    process = (
        str(value.get("process") or value.get("process_name") or value.get("app") or "")
        .strip()
    )
    hwnd = value.get("hwnd")
    try:
        hwnd_int = int(hwnd) if hwnd not in (None, "") else None
    except (TypeError, ValueError):
        hwnd_int = None

    if hwnd_int:
        hint["hwnd"] = hwnd_int
    if title:
        hint["title"] = title
    if process:
        hint["process"] = process
    return hint if len(hint) > 1 else None


def _target_window_hint(script: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("target_window", "target"):
        hint = _target_window_hint_from(script.get(key), key)
        if hint:
            return hint

    conditions = script.get("conditions")
    if isinstance(conditions, dict):
        for key in ("target_window", "window_title", "window_appears", "target"):
            hint = _target_window_hint_from(conditions.get(key), f"conditions.{key}")
            if hint:
                return hint
    return None


def _target_window_spec_present(script: dict[str, Any]) -> bool:
    if "target_window" in script:
        return True
    conditions = script.get("conditions")
    return isinstance(conditions, dict) and any(
        key in conditions for key in ("target_window", "window_title", "window_appears")
    )


def _has_click_target(step: dict) -> bool:
    return (
        _nonempty_str(step.get("text"))
        or _nonempty_str(step.get("element_text"))
        or _is_point(step.get("coords"))
    )


def _valid_strokes(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return any(
        isinstance(stroke, list)
        and sum(1 for point in stroke if _is_point(point)) >= 2
        for stroke in value
    )


def _validate_leaf_step(step: dict, loc: str) -> str | None:
    tool = step.get("tool")
    if tool == "click" and not _has_click_target(step):
        return f"{loc} click requires text/element_text or coords"
    if tool == "type_text" and "text_input" not in step:
        return f"{loc} type_text requires text_input"
    if tool == "hotkey" and not isinstance(step.get("keys"), list):
        return f"{loc} hotkey requires keys[]"
    if tool == "launch" and not _nonempty_str(step.get("command")):
        return f"{loc} launch requires command"
    if tool == "focus_window" and not _nonempty_str(step.get("title")):
        return f"{loc} focus_window requires title"
    if tool == "scroll" and not _is_point(step.get("coords")):
        return f"{loc} scroll requires coords"
    if tool == "drag" and (not _is_point(step.get("from")) or not _is_point(step.get("to"))):
        return f"{loc} drag requires from/to points"
    if tool == "draw_strokes" and not _valid_strokes(step.get("strokes")):
        return f"{loc} draw_strokes requires at least one stroke with two points"
    if tool == "wait" and not isinstance(step.get("condition"), dict):
        return f"{loc} wait requires condition object"
    if tool == "run_shell" and not _nonempty_str(step.get("command")):
        return f"{loc} run_shell requires command"
    if tool == "run_detached" and not _nonempty_str(step.get("script")):
        return f"{loc} run_detached requires script"
    if tool == "tray_click" and not _nonempty_str(step.get("name")):
        return f"{loc} tray_click requires name"
    if tool == "ext.delay" and not _is_number(step.get("seconds", 0)):
        return f"{loc} ext.delay requires numeric seconds"
    if tool == "ext.var_set" and not _nonempty_str(step.get("name")):
        return f"{loc} ext.var_set requires name"
    if tool == "ext.clipboard_set" and "text" not in step:
        return f"{loc} ext.clipboard_set requires text"
    if tool == "ext.clipboard_get" and step.get("into") is not None and not _nonempty_str(step.get("into")):
        return f"{loc} ext.clipboard_get into must be a non-empty string"
    if tool == "ext.run_shell" and not _nonempty_str(step.get("command")):
        return f"{loc} ext.run_shell requires command"
    return None


def _validate_steps(steps: Any, where: str) -> str | None:
    if not isinstance(steps, list):
        return f"{where} 必须是数组"
    for i, step in enumerate(steps, start=1):
        loc = f"{where}[{i}]"
        if not isinstance(step, dict):
            return f"{loc} 必须是对象"
        tool = step.get("tool")
        if tool not in KNOWN_TOOLS:
            return f"{loc} 未知步骤类型: {tool!r}"
        if tool == "ctrl.if":
            cond = step.get("cond")
            if not isinstance(cond, dict):
                return f"{loc} ctrl.if requires cond object"
            if not cond:
                return f"{loc} ctrl.if cond must be non-empty or {{'always': true}}"
            for branch in ("then", "else"):
                if step.get(branch) is not None:
                    err = _validate_steps(step[branch], f"{loc}.{branch}")
                    if err:
                        return err
        elif tool == "ctrl.loop":
            loop_type = step.get("loop_type", "count")
            if loop_type not in ("count", "while", "until", "infinite"):
                return f"{loc} invalid loop_type: {loop_type!r}"
            if loop_type == "count" and step.get("count") is not None and not _is_number(step.get("count")):
                return f"{loc} ctrl.loop count requires numeric count"
            if loop_type in ("while", "until") and not isinstance(step.get("cond"), dict):
                return f"{loc} ctrl.loop {loop_type} requires cond object"
            if step.get("max_iters") is not None and not _is_number(step.get("max_iters")):
                return f"{loc} ctrl.loop max_iters must be numeric"
            err = _validate_steps(step.get("body") or [], f"{loc}.body")
            if err:
                return err
        else:
            err = _validate_leaf_step(step, loc)
            if err:
                return err
    return None


def validate_script(data: Any) -> str | None:
    """Structural validation of a .wps.json document (any nesting depth).

    Returns an error message string, or None if valid. Used by the PUT
    endpoint to reject malformed/unknown-tool scripts before saving."""
    if not isinstance(data, dict):
        return "脚本必须是 JSON 对象"
    fmt = data.get("format")
    if fmt not in ("wps/1", "wps/2"):
        return f"未知 format: {fmt!r}（应为 wps/1 或 wps/2）"
    if data.get("vars") is not None and not isinstance(data.get("vars"), dict):
        return "vars 必须是对象"
    if _target_window_spec_present(data) and not _target_window_hint(data):
        return "target_window requires title/window_title/name, hwnd, or process"
    return _validate_steps(data.get("steps"), "steps")


# ------------------------------------------------------------------- replay
def _normalize_hotkeys(keys: Any) -> list[str]:
    out = []
    for key in keys or []:
        value = str(key).strip().lower()
        out.append("win" if value == "cmd" else value)
    return out


class Replayer:
    """Offline script execution: selector-based re-location + verify."""

    def __init__(self, stop_event: threading.Event | None = None) -> None:
        self.stop_event = stop_event or threading.Event()
        self.hwnd: int | None = None
        # wps/2: per-run variable store for ${var} interpolation.
        self.vars: dict[str, str] = {}
        # Inter-leaf pacing (overridable in tests for speed).
        self.step_delay: float = 0.25
        self._results: list[dict[str, Any]] = []

    def _relocate(self, step: dict) -> tuple[int, int] | None:
        """Find click target on the live screen: text match → recorded coords."""
        text = (step.get("text") or step.get("element_text") or "").strip()
        if text:
            hwnd = self._observe_hwnd()
            if not hwnd:
                coords = step.get("coords")
                return (int(coords[0]), int(coords[1])) if coords else None
            try:
                snapshot = fusion.observe(hwnd, include_icons=False)
                exact = [e for e in snapshot.elements if e.text.strip() == text]
                partial = [e for e in snapshot.elements if text in e.text]
                hit = (exact or partial or [None])[0]
                if hit:
                    return hit.cx, hit.cy
            except Exception as exc:
                logger.debug("replay 重定位失败: %s", exc)
        coords = step.get("coords")
        return (int(coords[0]), int(coords[1])) if coords else None

    def _observe_hwnd(self) -> int | None:
        """Return a window anchor for semantic replay relocation."""
        if self.hwnd:
            return self.hwnd
        # Never guess a replay target from the first visible window. Text-based
        # relocation needs a focused/recorded target; otherwise a replay can
        # silently click the wrong app. Coordinate-only steps can still run.
        return None

    def _target_hint_label(self, hint: dict[str, Any]) -> str:
        parts = []
        if hint.get("title"):
            parts.append(f'title="{hint["title"]}"')
        if hint.get("process"):
            parts.append(f'process="{hint["process"]}"')
        if hint.get("hwnd"):
            parts.append(f"hwnd={hint['hwnd']}")
        return ", ".join(parts) or "empty target_window"

    def _window_process_name(self, win: dict) -> str:
        pid = win.get("pid")
        proc_fn = getattr(win32, "_process_name", None)
        if not callable(proc_fn) or not pid:
            return ""
        try:
            return str(proc_fn(int(pid)) or "")
        except Exception:
            return ""

    def _window_matches_target_hint(self, win: dict, hint: dict[str, Any]) -> bool:
        title = str(hint.get("title") or "").strip()
        if title and not win32.title_contains(str(win.get("title") or ""), title):
            return False

        process = str(hint.get("process") or "").strip().casefold()
        if process:
            actual = self._window_process_name(win).casefold()
            if not actual:
                return False
            process_key = process.removesuffix(".exe")
            actual_key = actual.removesuffix(".exe")
            if process not in actual and process_key not in actual_key:
                return False
        return True

    def _find_target_window(self, hint: dict[str, Any]) -> dict | None:
        try:
            hwnd = hint.get("hwnd")
            if hwnd:
                for win in win32.list_windows():
                    if int(win.get("hwnd") or 0) == int(hwnd) and self._window_matches_target_hint(win, hint):
                        return win
                return None

            title = str(hint.get("title") or "").strip()
            process = str(hint.get("process") or "").strip()
            if title and not process:
                return win32.find_window(title)

            for win in win32.list_windows():
                if self._window_matches_target_hint(win, hint):
                    return win
        except Exception as exc:
            logger.warning("replay target-window preflight failed: %s", exc)
        return None

    def _preflight_target_window(self, script: dict[str, Any]) -> dict[str, Any]:
        hint = _target_window_hint(script)
        if not hint:
            return {"ok": True, "required": False}

        found = self._find_target_window(hint)
        if not found:
            error = f"target window not found: {self._target_hint_label(hint)}"
            result = {"ok": False, "error": error, "hint": hint}
            self._emit_step("preflight", "preflight.target_window", False, result)
            return {"ok": False, "required": True, "error": error, "hint": hint}

        self.hwnd = int(found["hwnd"])
        return {
            "ok": True,
            "required": True,
            "hwnd": self.hwnd,
            "title": found.get("title"),
            "hint": hint,
        }

    def _run_step(self, idx: int, step: dict) -> dict[str, Any]:
        tool = step.get("tool", "")
        expect = step.get("expect")

        if tool == "launch":
            return verify.run_with_verify(
                "launch", lambda: executor.launch(step["command"]),
                expect=expect, hwnd=self.hwnd, timeout_s=10.0,
                stop_event=self.stop_event)
        if tool == "focus_window":
            found = win32.find_window(step.get("title", ""))
            if not found:
                return {"ok": False, "error": f'窗口 "{step.get("title")}" 不存在'}
            self.hwnd = found["hwnd"]
            return executor.focus_window(self.hwnd)
        if tool == "click":
            point = self._relocate(step)
            if point is None:
                return {"ok": False, "error": "无法定位点击目标"}
            x, y = point
            return verify.run_with_verify(
                "click",
                lambda: executor.click(x, y, button=step.get("button", "left"),
                                       double=bool(step.get("double")), hwnd=self.hwnd),
                expect=expect, hwnd=self.hwnd, stop_event=self.stop_event)
        if tool == "type_text":
            point = self._relocate(step)

            def action() -> dict:
                if point:
                    executor.click(point[0], point[1], hwnd=self.hwnd)
                    time.sleep(0.15)
                return executor.type_text(step.get("text_input", ""))

            return verify.run_with_verify(
                "type_text", action, expect=expect, hwnd=self.hwnd, retryable=False,
                stop_event=self.stop_event)
        if tool == "hotkey":
            return verify.run_with_verify(
                "hotkey", lambda: executor.press_keys(_normalize_hotkeys(step.get("keys", []))),
                expect=expect, hwnd=self.hwnd, retryable=False,
                stop_event=self.stop_event)
        if tool == "scroll":
            coords = step.get("coords", [0, 0])
            return executor.scroll(coords[0], coords[1], int(step.get("amount", 0)))
        if tool == "drag":
            frm, to = step.get("from", [0, 0]), step.get("to", [0, 0])
            return executor.drag(frm[0], frm[1], to[0], to[1],
                                 duration_s=float(step.get("duration", 0.4)),
                                 points=step.get("points"), hwnd=self.hwnd)
        if tool == "draw_strokes":
            return verify.run_with_verify(
                "draw_strokes",
                lambda: executor.draw_strokes(
                    step.get("strokes", []),
                    duration_s=float(step.get("duration", 0.4)),
                    hwnd=self.hwnd,
                ),
                expect=expect, hwnd=self.hwnd, stop_event=self.stop_event)
        if tool == "run_shell":
            from ..action import shell
            return shell.run(step.get("command", ""),
                             shell=step.get("shell", "powershell"),
                             timeout_s=float(step.get("timeout_s", 30)),
                             stop_event=self.stop_event)
        if tool == "run_detached":
            from ..action import shell
            return shell.run_detached(str(step.get("script", "")),
                                      name=str(step.get("name", "task")))
        if tool == "tray_click":
            from ..perception import tray
            return tray.click_tray_icon(
                step.get("name", ""), double=bool(step.get("double")),
                button=step.get("button", "left"))
        if tool == "wait":
            outcome = verify.check(step.get("condition", {}), hwnd=self.hwnd,
                                   timeout_s=float(step.get("timeout_s", 10)),
                                   stop_event=self.stop_event)
            return {"ok": outcome.verified, **outcome.to_dict()}
        return {"ok": False, "error": f"未知步骤类型: {tool}"}

    def run(self, script_path: str | Path) -> dict[str, Any]:
        path = Path(script_path)
        if not path.is_absolute():
            path = SCRIPTS_DIR / path
        script = json.loads(path.read_text(encoding="utf-8"))
        steps = script.get("steps", [])
        # wps/2: seed variables (all string-valued). v1 scripts have none.
        raw_vars = script.get("vars") or {}
        self.vars = {str(k): str(v) for k, v in raw_vars.items()}
        self._results = []
        total = replay_control.count_leaves(steps)
        BUS.publish("replay", state="start", script=path.name, total=total)
        preflight = self._preflight_target_window(script)
        if not preflight.get("ok", False):
            BUS.publish("replay", state="done", ok=False)
            return {"ok": False, "results": self._results, "preflight": preflight}
        ok_all = self._exec_steps(steps)
        BUS.publish("replay", state="done", ok=ok_all)
        return {"ok": ok_all, "results": self._results, "preflight": preflight}

    # --------------------------------------------------------- step engine
    def _should_stop(self) -> bool:
        return self.stop_event.is_set()

    def _check_condition(self, cond: dict, timeout_s: float) -> bool:
        """Evaluate a verify-DSL condition offline (0-token). Used by ctrl.*."""
        try:
            return verify.check(cond, hwnd=self.hwnd, timeout_s=timeout_s).verified
        except Exception as exc:
            logger.warning("replay 条件求值失败: %s", exc)
            return False

    def _exec_steps(self, steps: list[dict], prefix: str = "") -> bool:
        """Execute a (possibly nested) step list. Returns False on first failure
        so the caller can locate where replay diverged."""
        for i, raw in enumerate(steps, start=1):
            if self._should_stop():
                BUS.publish("replay", state="stopped", at=f"{prefix}{i}")
                return False
            idx = f"{prefix}{i}"
            step = replay_control.interpolate_step(raw, self.vars)
            tool = step.get("tool", "")

            if tool == "ctrl.if":
                if not replay_control.exec_if(self, step, idx):
                    return False
                continue
            if tool == "ctrl.loop":
                if not replay_control.exec_loop(self, step, idx):
                    return False
                continue

            if tool.startswith("ext."):
                result = self._run_ext_step(step)
                ok = bool(result.get("ok", False))
            else:
                result = self._run_step(idx, step)
                ok = (bool(result.get("ok", False))
                      and result.get("verified", True) is not False)
            self._emit_step(idx, tool, ok, result)
            if not ok:
                return False
            if self.step_delay:
                time.sleep(self.step_delay)
        return True

    def _emit_step(self, idx: str, tool: str, ok: bool, result: dict) -> None:
        BUS.publish("replay", state="step", index=idx, tool=tool, ok=ok,
                    detail={k: v for k, v in result.items()
                            if k in ("error", "verify_detail", "attempts", "timeout", "stopped", "hint")})
        record = {"index": idx, "tool": tool, "ok": ok}
        for key in ("error", "verify_detail", "attempts", "timeout", "stopped", "hint"):
            if key in result:
                record[key] = result[key]
        self._results.append(record)

    def _emit_ctrl(self, idx: str, tool: str, ok: bool, detail: dict) -> None:
        BUS.publish("replay", state="ctrl", index=idx, tool=tool, ok=ok,
                    detail=detail)

    # ------------------------------------------------------- ext.* steps
    def _run_ext_step(self, step: dict) -> dict[str, Any]:
        """Human-convenience offline steps (never seen by the agent)."""
        tool = step.get("tool", "")
        if tool == "ext.delay":
            try:
                time.sleep(max(0.0, float(step.get("seconds", 0))))
            except (TypeError, ValueError):
                return {"ok": False, "error": "ext.delay seconds 非法"}
            return {"ok": True}

        if tool == "ext.var_set":
            name = str(step.get("name", "")).strip()
            if not name:
                return {"ok": False, "error": "ext.var_set 缺少 name"}
            if step.get("from") == "clipboard":
                value = _clipboard_get()
            else:
                value = str(step.get("value", ""))
            self.vars[name] = value
            return {"ok": True, "value": value}

        if tool == "ext.clipboard_set":
            return _clipboard_set(str(step.get("text", "")))

        if tool == "ext.clipboard_get":
            into = str(step.get("into", "")).strip()
            text = _clipboard_get()
            if into:
                self.vars[into] = text
            return {"ok": True, "text": text}

        if tool == "ext.run_shell":
            from ..action import shell
            result = shell.run(str(step.get("command", "")),
                               shell=str(step.get("shell", "powershell")),
                               timeout_s=float(step.get("timeout_s", 30)),
                               stop_event=self.stop_event)
            into = str(step.get("into", "")).strip()
            if into:
                self.vars[into] = result.get("stdout", "") or ""
            return result

        if tool == "ext.screenshot":
            return self._ext_screenshot(step)

        return {"ok": False, "error": f"未知 ext 步骤: {tool}"}

    def _ext_screenshot(self, step: dict) -> dict[str, Any]:
        rel = str(step.get("path") or "shots/replay.png")
        target = (SCRIPTS_DIR / rel)
        region_spec = step.get("region")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            region = None
            if isinstance(region_spec, dict):
                region = Region(int(region_spec["x"]), int(region_spec["y"]),
                                int(region_spec["w"]), int(region_spec["h"]))
            image = capture(region)
            ok = cv2.imwrite(str(target), image)
            return {"ok": bool(ok), "path": str(target)}
        except Exception as exc:
            logger.warning("ext.screenshot 失败: %s", exc)
            return {"ok": False, "error": str(exc)}


def replay(script_path: str | Path, stop_event: threading.Event | None = None) -> dict:
    return Replayer(stop_event).run(script_path)


# ------------------------------------------------------------- clipboard
def _clipboard_get() -> str:
    """Read Unicode text from the Windows clipboard (empty on failure)."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except (TypeError, OSError):
            data = ""
        finally:
            win32clipboard.CloseClipboard()
        return data or ""
    except Exception as exc:
        logger.warning("剪贴板读取失败: %s", exc)
        return ""


def _clipboard_set(text: str) -> dict[str, Any]:
    """Write Unicode text to the Windows clipboard."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return {"ok": True}
    except Exception as exc:
        logger.warning("剪贴板写入失败: %s", exc)
        return {"ok": False, "error": str(exc)}
