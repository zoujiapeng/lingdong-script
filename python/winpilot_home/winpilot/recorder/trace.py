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
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

import cv2

from ..action import executor, verify
from ..automation_ir import automation_ir_from_wps, validate_automation_ir, wps_from_automation_ir
from ..config import SCRIPTS_DIR, TRACES_DIR
from ..fast.visual_servo import ServoConfig, track_visual_target
from ..memory import replay_anchors
from ..perception import fusion, groundtruth, ocr, uia, vision_cv, win32
from ..perception.model import UIElement
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from ..utils.trace_payload import sanitize_trace_payload
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
_POST_ACTION_CAPTURE_SETTLE_S = 0.035

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
        self._evidence_step = 0
        self._accepting = False
        self._action_steps: dict[str, int] = {}
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1024)
        self._dropped_events = 0
        self._worker = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="WinPilotTraceWriter",
        )
        self._worker.start()
        BUS.add_sink(self._on_event)

    def start(self, run_id: str) -> None:
        with self._lock:
            active = self._file is not None
        if active:
            self.stop()
        with self._lock:
            TRACES_DIR.mkdir(exist_ok=True)
            (TRACES_DIR / run_id).mkdir(exist_ok=True)
            self._file = open(TRACES_DIR / f"{run_id}.jsonl", "a", encoding="utf-8")
            self._run_id = run_id
            self._step = 0
            self._evidence_step = 0
            self._action_steps = {}
            self._accepting = True
        BUS.publish("trace", state="recording", run_id=run_id)

    def stop(self) -> None:
        with self._lock:
            run_id = self._run_id
            self._accepting = False
        self._queue.join()
        with self._lock:
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
        self._accepting = False

    def _on_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            if not self._accepting or self._file is None or event.get("type") == "trace":
                return
            run_id = self._run_id
            step: int | None = None
            phase = "after"
            event_type = str(event.get("type") or "")
            action_key = self._action_key(event)
            if event_type == "agent_tool_start" and event.get("tool") in _SCREENSHOT_TOOLS:
                self._step += 1
                step = self._step
                phase = "before"
                self._action_steps[action_key] = step
            elif event_type == "agent_tool" and event.get("tool") in _SCREENSHOT_TOOLS:
                step = self._action_steps.pop(action_key, None)
                if step is None:
                    self._step += 1
                    step = self._step
                phase = "after"
            elif (
                event_type == "agent_batch_action_start"
                and event.get("tool") in _BATCH_SCREENSHOT_TOOLS
                and event.get("trace_screenshot", True) is not False
            ):
                self._step += 1
                step = self._step
                phase = "batch_before"
                self._action_steps[action_key] = step
            elif (
                event_type == "agent_batch_action"
                and event.get("tool") in _BATCH_SCREENSHOT_TOOLS
                and event.get("trace_screenshot", True) is not False
            ):
                step = self._action_steps.pop(action_key, None)
                if step is None:
                    self._step += 1
                    step = self._step
                phase = "batch_after"

        frame = None
        if step is not None and phase in {"before", "batch_before"}:
            try:
                from ..fast.perception_daemon import PERCEPTION_DAEMON

                latest = PERCEPTION_DAEMON.latest_frame(run_id=str(run_id or ""), max_age_ms=500)
                if latest is not None:
                    frame, _region = latest
            except Exception:
                frame = None
        self._enqueue({
            "kind": "event",
            "run_id": run_id,
            "record": dict(event),
            "step": step,
            "phase": phase,
            "frame": frame,
        })

    @staticmethod
    def _action_key(event: dict[str, Any]) -> str:
        action_id = str(event.get("action_id") or "").strip()
        if action_id:
            return f"action:{action_id}"
        batch_id = str(event.get("batch_id") or "").strip()
        batch_index = str(event.get("batch_index") or "").strip()
        if batch_id or batch_index:
            return f"batch:{batch_id}:{batch_index}"
        return f"legacy:{event.get('tool') or ''}"

    def _enqueue(self, item: dict[str, Any]) -> bool:
        try:
            self._queue.put(item, timeout=0.05)
            return True
        except queue.Full:
            with self._lock:
                self._dropped_events += 1
            logger.warning("trace queue full; dropped %s", item.get("kind"))
            return False

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item.get("kind") == "event":
                    self._write_event(item)
                elif item.get("kind") == "evidence":
                    self._write_image(item.get("frame"), item.get("path"), quality=82)
            except Exception as exc:
                logger.warning("trace worker failed: %s", exc)
            finally:
                self._queue.task_done()

    def _write_event(self, item: dict[str, Any]) -> None:
        run_id = item.get("run_id")
        raw_record = dict(item.get("record") or {})
        record = sanitize_trace_payload(
            raw_record,
            frame_id=raw_record.get("frame_id") or raw_record.get("visual_frame_id"),
        )
        step = item.get("step")
        if step is not None:
            phase = str(item.get("phase") or "after")
            if phase in {"after", "batch_after"} and item.get("frame") is None:
                # The Level-0 daemon's latest frame at action completion can
                # still be the pre-action sample.  Capture asynchronously after
                # a short paint interval so trace evidence represents the
                # resulting frame without slowing the controller hot path.
                time.sleep(_POST_ACTION_CAPTURE_SETTLE_S)
            shot = self._save_screenshot(
                run_id,
                int(step),
                phase,
                image=item.get("frame"),
            )
            if shot:
                record["screenshot"] = shot
                # Screenshot capture is asynchronous, so the original BUS
                # event has already reached live projections without this
                # path. Publish a small follow-up event after the file exists
                # so the frontend can render current-run evidence immediately.
                BUS.publish(
                    "trace_screenshot",
                    run_id=str(record.get("run_id") or run_id or ""),
                    trace_run_id=str(run_id or ""),
                    screenshot=shot,
                    screenshot_kind=str(item.get("phase") or "after"),
                    tool=record.get("tool"),
                    action_id=record.get("action_id"),
                    batch_id=record.get("batch_id"),
                    batch_index=record.get("batch_index"),
                )
        with self._lock:
            if self._file is None or self._run_id != run_id:
                return
            try:
                self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                self._file.flush()
            except OSError as exc:
                logger.warning("trace 写入失败: %s", exc)

    def _save_screenshot(
        self,
        run_id: str | None,
        step: int,
        phase: str = "after",
        *,
        image: Any = None,
    ) -> str | None:
        if not run_id:
            return None
        try:
            image = capture() if image is None else image
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

    def save_evidence_image(self, image: Any, kind: str = "evidence") -> str | None:
        """Persist an already-captured evidence frame in the active trace.

        Action verification can observe states that disappear before the normal
        post-action screenshot. Saving that exact frame keeps the trace and UI
        grounded in what the controller actually evaluated.
        """
        with self._lock:
            run_id = self._run_id
            if not run_id or self._file is None or not self._accepting:
                return None
            self._evidence_step += 1
            step = self._evidence_step
        try:
            frame = image.copy()
            safe_kind = re.sub(r"[^a-z0-9_-]+", "_", str(kind).casefold()).strip("_") or "evidence"
            rel = f"{run_id}/evidence_{step:03d}_{safe_kind}.jpg"
            if self._enqueue({"kind": "evidence", "run_id": run_id, "frame": frame, "path": rel}):
                return rel
        except Exception as exc:
            logger.debug("trace evidence image save failed: %s", exc)
        return None

    @staticmethod
    def _write_image(frame: Any, path: str | None, quality: int) -> bool:
        if frame is None or not path:
            return False
        image = frame
        h, w = image.shape[:2]
        if w > _SCREENSHOT_MAX_W:
            scale = _SCREENSHOT_MAX_W / w
            image = cv2.resize(image, (_SCREENSHOT_MAX_W, max(1, int(h * scale))))
        target = TRACES_DIR / str(path)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if ok:
            target.write_bytes(buf.tobytes())
        return bool(ok)


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


def _step_semantic_text(step: dict[str, Any]) -> str:
    """Return the best replay-stable text selector carried by an action."""
    for field in ("text", "element_text"):
        value = str(step.get(field) or "").strip()
        if value:
            return value

    anchor = step.get("anchor")
    if isinstance(anchor, dict):
        for field in ("label", "text_query"):
            value = str(anchor.get(field) or "").strip()
            if value:
                return value
        fallback = anchor.get("fallback_text")
        if isinstance(fallback, str):
            fallback = [fallback]
        if isinstance(fallback, list):
            for item in fallback:
                value = str(item or "").strip()
                if value:
                    return value

    resolved = step.get("resolved_element")
    if isinstance(resolved, dict):
        return str(resolved.get("text") or "").strip()
    return ""


def _extract_replay_steps(events: list[dict[str, Any]]) -> list[dict]:
    steps: list[dict] = []
    for event in events:
        replay = event.get("replay")
        if not replay:
            continue
        tool = replay.get("tool") if isinstance(replay, dict) else None
        if tool not in KNOWN_TOOLS:
            continue
        if event.get("type") == "agent_batch_action":
            steps.append(replay)
        elif event.get("type") == "agent_tool" and event.get("tool") != "run_action_batch":
            steps.append(replay)
    return steps


def _stable_target_window_hint(hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only replay-stable window identity; never persist old hwnd."""
    if not hint:
        return None
    title = str(hint.get("title") or "").strip()
    process = str(hint.get("process") or "").strip()
    out: dict[str, Any] = {}
    if title:
        out["title"] = title
    if process:
        out["process"] = process
    return out or None


def _target_window_from_result_meta(meta: Any) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    for key in ("target_window", "window"):
        hint = _stable_target_window_hint(
            _target_window_hint_from(meta.get(key), f"result_meta.{key}")
        )
        if hint:
            return hint
    reducer = meta.get("perception_reducer")
    if isinstance(reducer, dict):
        hint = _stable_target_window_hint(
            _target_window_hint_from(reducer.get("target_window"), "result_meta.perception_reducer.target_window")
        )
        if hint:
            return hint
    evidence = meta.get("evidence_packet")
    if isinstance(evidence, dict):
        reducer = evidence.get("perception_reducer")
        if isinstance(reducer, dict):
            hint = _stable_target_window_hint(
                _target_window_hint_from(
                    reducer.get("target_window"),
                    "result_meta.evidence_packet.perception_reducer.target_window",
                )
            )
            if hint:
                return hint
    return None


def _target_window_from_args(args: Any) -> dict[str, Any] | None:
    if not isinstance(args, dict):
        return None
    for key in ("target_window", "target"):
        hint = _stable_target_window_hint(
            _target_window_hint_from(args.get(key), f"args.{key}")
        )
        if hint:
            return hint
    expect = args.get("expect")
    if isinstance(expect, dict):
        for key in ("window_title", "window_appears", "target_window"):
            hint = _stable_target_window_hint(
                _target_window_hint_from(expect.get(key), f"args.expect.{key}")
            )
            if hint:
                return hint
    return None


def _extract_target_window(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        hint = _target_window_from_args(event.get("args"))
        if hint:
            return hint
        hint = _target_window_from_result_meta(event.get("result_meta"))
        if hint:
            return hint
    return None


def build_script(run_id: str, name: str | None = None) -> dict[str, Any]:
    """Distill a trace into a replayable .wps.json document."""
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
    target_window = _extract_target_window(events)
    if target_window:
        script["target_window"] = target_window
    err = validate_script(script)
    if err:
        raise ValueError(f"exported .wps.json is not replayable: {err}")
    return script


def export_script(run_id: str, name: str | None = None) -> Path:
    """Write the legacy WPS adapter and a canonical Automation IR sibling."""
    script = build_script(run_id, name=name)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    path = SCRIPTS_DIR / f"{name or run_id}.wps.json"
    path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    write_automation_ir(script, name=name, basename=name or run_id)
    BUS.publish("trace", state="exported", run_id=run_id, script=str(path))
    return path


def write_automation_ir(
    script: dict[str, Any],
    *,
    name: str | None,
    basename: str,
    directory: Path | None = None,
) -> Path:
    ir = automation_ir_from_wps(script, name=name)
    err = validate_automation_ir(ir)
    if err:
        raise ValueError(f"exported Automation IR is invalid: {err}")
    root = directory or SCRIPTS_DIR
    root.mkdir(exist_ok=True)
    path = root / f"{basename}.automation.json"
    path.write_text(json.dumps(ir, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def export_automation_ir(run_id: str, name: str | None = None) -> Path:
    """Distill a trace into Automation IR v1 for skill/Flow/replay handoff."""
    script = build_script(run_id, name=name)
    path = write_automation_ir(script, name=name, basename=name or run_id)
    BUS.publish("trace", state="exported_ir", run_id=run_id, ir=str(path))
    return path


def list_scripts() -> list[dict[str, Any]]:
    SCRIPTS_DIR.mkdir(exist_ok=True)
    out = []
    canonical_bases: set[str] = set()
    for path in sorted(SCRIPTS_DIR.glob("*.automation.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if validate_automation_ir(data):
                continue
            canonical_bases.add(path.name.removesuffix(".automation.json"))
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            out.append({
                "file": path.name,
                "name": data.get("name", path.stem),
                "task": source.get("task", ""),
                "steps": len(data.get("steps", [])),
                "format": "automation-ir/1",
            })
        except (json.JSONDecodeError, OSError):
            continue
    for path in sorted(SCRIPTS_DIR.glob("*.wps.json"), reverse=True):
        if path.name.removesuffix(".wps.json") in canonical_bases:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({
                "file": path.name, "name": data.get("name", path.stem),
                "task": data.get("task", ""), "steps": len(data.get("steps", [])),
                "format": data.get("format", "wps/2"),
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
            if loop_type in ("while", "until"):
                cond = step.get("cond")
                if not isinstance(cond, dict):
                    return f"{loc} ctrl.loop {loop_type} requires cond object"
                if not cond:
                    return f"{loc} ctrl.loop {loop_type} cond must be non-empty"
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

    def __init__(
        self,
        stop_event: threading.Event | None = None,
        anchor_profile: str = "",
    ) -> None:
        self.stop_event = stop_event or threading.Event()
        self.hwnd: int | None = None
        self._target_signature = ""
        # wps/2: per-run variable store for ${var} interpolation.
        self.vars: dict[str, str] = {}
        # Inter-leaf pacing (overridable in tests for speed).
        self.step_delay: float = 0.25
        self._results: list[dict[str, Any]] = []
        self._last_relocation: dict[str, Any] = {}
        self.anchor_profile = ""
        self._anchors: dict[str, dict[str, Any]] = {}
        self._anchor_dirty = False
        self._anchor_metrics: dict[str, Any] = {}
        self._reset_anchor_metrics()
        if anchor_profile:
            self.configure_anchor_cache(anchor_profile)

    def _reset_anchor_metrics(self) -> None:
        self._anchor_metrics = {
            "lookup_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "fallback_count": 0,
            "learned_count": 0,
            "lookup_latency_ms": [],
        }

    def configure_anchor_cache(self, profile: str) -> None:
        profile = str(profile or "").strip()
        if profile == self.anchor_profile:
            return
        self._flush_anchor_cache()
        self.anchor_profile = profile
        self._anchors = replay_anchors.load_profile(profile) if profile else {}
        self._anchor_dirty = False

    def _flush_anchor_cache(self) -> None:
        if not self.anchor_profile or not self._anchor_dirty:
            return
        replay_anchors.save_profile(self.anchor_profile, self._anchors)
        self._anchor_dirty = False

    def _anchor_summary(self) -> dict[str, Any]:
        metrics = self._anchor_metrics
        latencies = [int(value) for value in metrics.get("lookup_latency_ms", [])]
        lookups = int(metrics.get("lookup_count") or 0)
        hits = int(metrics.get("hit_count") or 0)
        return {
            "enabled": bool(self.anchor_profile),
            "profile": self.anchor_profile,
            "stored_count": len(self._anchors),
            "lookup_count": lookups,
            "hit_count": hits,
            "miss_count": int(metrics.get("miss_count") or 0),
            "fallback_count": int(metrics.get("fallback_count") or 0),
            "learned_count": int(metrics.get("learned_count") or 0),
            "hit_rate": round(hits / lookups, 4) if lookups else 0.0,
            "lookup_latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "lookup_latency_max_ms": max(latencies) if latencies else 0,
        }

    def _focus_for_anchor_lookup(self) -> bool:
        if not self.hwnd:
            return False
        try:
            foreground = int(win32.win32gui.GetForegroundWindow())
        except Exception:
            foreground = 0
        if foreground == int(self.hwnd):
            return True
        try:
            result = executor.focus_window(int(self.hwnd))
            return bool(result.get("ok", False))
        except Exception:
            return False

    def _finish_anchor_lookup(
        self,
        key: str,
        anchor: dict[str, Any],
        started: float,
        *,
        hit: bool,
        reason: str,
        confidence: float = 0.0,
    ) -> int:
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        self._anchor_metrics["lookup_count"] += 1
        self._anchor_metrics["lookup_latency_ms"].append(elapsed_ms)
        now = int(time.time())
        anchor["updated_at"] = now
        if hit:
            self._anchor_metrics["hit_count"] += 1
            anchor["hits"] = int(anchor.get("hits") or 0) + 1
            anchor["last_used_at"] = now
            anchor["confidence"] = max(float(anchor.get("confidence") or 0.0), confidence)
        else:
            self._anchor_metrics["miss_count"] += 1
            self._anchor_metrics["fallback_count"] += 1
            anchor["misses"] = int(anchor.get("misses") or 0) + 1
        self._anchors[key] = anchor
        self._anchor_dirty = True
        return elapsed_ms

    def _relocate_from_anchor(
        self,
        step: dict[str, Any],
        anchor_key: str,
    ) -> tuple[tuple[int, int] | None, dict[str, Any]]:
        key = str(anchor_key or "").strip()
        anchor = self._anchors.get(key)
        if not self.anchor_profile or not key or not isinstance(anchor, dict) or not self.hwnd:
            return None, {}
        started = time.monotonic()
        text = _step_semantic_text(step)
        learned_text = str(anchor.get("text") or "").strip()
        if text and learned_text and text.casefold() != learned_text.casefold():
            elapsed = self._finish_anchor_lookup(
                key, anchor, started, hit=False, reason="selector_changed",
            )
            return None, {
                "anchor_cache": {
                    "key": key, "hit": False, "reason": "selector_changed",
                    "lookup_ms": elapsed,
                },
            }
        if not self._focus_for_anchor_lookup():
            elapsed = self._finish_anchor_lookup(
                key, anchor, started, hit=False, reason="focus_failed",
            )
            return None, {
                "anchor_cache": {
                    "key": key, "hit": False, "reason": "focus_failed",
                    "lookup_ms": elapsed,
                },
            }
        try:
            window = window_region(int(self.hwnd))
            rel = anchor["relative_bbox"]
            predicted = Region(
                window.left + int(round(float(rel[0]) * window.width)),
                window.top + int(round(float(rel[1]) * window.height)),
                max(8, int(round(float(rel[2]) * window.width))),
                max(8, int(round(float(rel[3]) * window.height))),
            )
            pad_x = max(24, int(predicted.width * 0.75))
            pad_y = max(20, int(predicted.height * 1.25))
            left = max(window.left, predicted.left - pad_x)
            top = max(window.top, predicted.top - pad_y)
            right = min(window.right, predicted.right + pad_x)
            bottom = min(window.bottom, predicted.bottom + pad_y)
            search = Region(left, top, max(9, right - left), max(9, bottom - top))
            matches = vision_cv.match_template(
                str(anchor["template"]),
                region=search,
                threshold=0.83,
                max_matches=4,
            )
            if matches:
                predicted_center = predicted.center
                hit = min(
                    matches,
                    key=lambda item: (
                        (int(item.cx) - predicted_center[0]) ** 2
                        + (int(item.cy) - predicted_center[1]) ** 2
                    ),
                )
                offset = anchor["click_offset"]
                x = int(round(int(hit.left) + float(offset[0]) * int(hit.width)))
                y = int(round(int(hit.top) + float(offset[1]) * int(hit.height)))
                confidence = float(getattr(hit, "confidence", 0.0) or 0.0)
                elapsed = self._finish_anchor_lookup(
                    key, anchor, started, hit=True, reason="template_match",
                    confidence=confidence,
                )
                return (x, y), {
                    "source": "anchor_cache",
                    "text": text or learned_text,
                    "point": [x, y],
                    "bbox": [int(hit.left), int(hit.top), int(hit.width), int(hit.height)],
                    "perception_ms": elapsed,
                    "perception_method": "anchor-template-roi",
                    "perception_dims": {
                        "template": {
                            "count": len(matches),
                            "ms": elapsed,
                            "roi": [search.left, search.top, search.width, search.height],
                        },
                    },
                    "anchor_cache": {
                        "key": key,
                        "hit": True,
                        "reason": "template_match",
                        "confidence": round(confidence, 4),
                        "lookup_ms": elapsed,
                        "predicted_bbox": [
                            predicted.left, predicted.top, predicted.width, predicted.height,
                        ],
                    },
                }
            reason = "template_not_found"
        except Exception as exc:
            logger.debug("replay anchor lookup failed: %s", exc)
            reason = "lookup_error"
        elapsed = self._finish_anchor_lookup(
            key, anchor, started, hit=False, reason=reason,
        )
        return None, {
            "anchor_cache": {
                "key": key, "hit": False, "reason": reason, "lookup_ms": elapsed,
            },
        }

    def _learn_anchor(
        self,
        anchor_key: str,
        step: dict[str, Any],
        before: Any,
        surface: Region | None,
    ) -> bool:
        key = str(anchor_key or "").strip()
        text = _step_semantic_text(step)
        resolved = self._last_relocation
        point = resolved.get("point") if isinstance(resolved, dict) else None
        if (
            not self.anchor_profile or not key or not text or before is None
            or not isinstance(point, list) or len(point) != 2 or not self.hwnd
        ):
            return False
        try:
            surface = surface or window_region(int(self.hwnd))
            raw_bbox = resolved.get("bbox")
            if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
                left, top, width, height = (int(value) for value in raw_bbox)
            else:
                width, height = 120, 52
                left = int(point[0]) - width // 2
                top = int(point[1]) - height // 2
            left -= 3
            top -= 3
            width += 6
            height += 6
            width = min(320, max(12, width))
            height = min(140, max(12, height))
            left = max(surface.left, min(left, surface.right - 12))
            top = max(surface.top, min(top, surface.bottom - 12))
            right = min(surface.right, left + width)
            bottom = min(surface.bottom, top + height)
            left = max(surface.left, right - width)
            top = max(surface.top, bottom - height)
            width = right - left
            height = bottom - top
            x1, y1 = left - surface.left, top - surface.top
            x2, y2 = x1 + width, y1 + height
            crop = before[y1:y2, x1:x2]
            if crop.shape[0] < 8 or crop.shape[1] < 8:
                return False
            name = replay_anchors.template_name(self.anchor_profile, key)
            path = vision_cv.save_template_image(crop, name)
            previous = self._anchors.get(key) or {}
            now = int(time.time())
            source = str(resolved.get("source") or "")
            confidence = 0.9 if source == "uia" else 0.78 if source == "ocr" else 0.7
            self._anchors[key] = {
                "key": key,
                "text": text,
                "role": str(step.get("role") or step.get("element_role") or "")[:80],
                "template": path.name,
                "relative_bbox": [
                    (left - surface.left) / max(1, surface.width),
                    (top - surface.top) / max(1, surface.height),
                    width / max(1, surface.width),
                    height / max(1, surface.height),
                ],
                "click_offset": [
                    max(0.0, min(1.0, (int(point[0]) - left) / max(1, width))),
                    max(0.0, min(1.0, (int(point[1]) - top) / max(1, height))),
                ],
                "confidence": confidence,
                "hits": int(previous.get("hits") or 0),
                "misses": int(previous.get("misses") or 0),
                "learned_at": int(previous.get("learned_at") or now),
                "updated_at": now,
                "last_used_at": int(previous.get("last_used_at") or 0),
            }
            self._anchor_metrics["learned_count"] += 1
            self._anchor_dirty = True
            return True
        except Exception as exc:
            logger.debug("replay anchor learning failed: %s", exc)
            return False

    @staticmethod
    def _relocation_ocr_region(elements: list[Any], fallback: Any) -> Region:
        """Bound OCR to the union of duplicate semantic candidates."""
        boxes: list[tuple[int, int, int, int]] = []
        for element in elements:
            width = int(getattr(element, "width", 0) or 0)
            height = int(getattr(element, "height", 0) or 0)
            if width <= 0 or height <= 0:
                continue
            left = int(getattr(element, "left", 0) or 0)
            top = int(getattr(element, "top", 0) or 0)
            pad_x = max(12, width // 3)
            pad_y = max(12, height // 2)
            boxes.append((
                left - pad_x,
                top - pad_y,
                left + width + pad_x,
                top + height + pad_y,
            ))
        if not boxes:
            return Region(
                int(fallback.left), int(fallback.top),
                int(fallback.width), int(fallback.height),
            )
        left = max(int(fallback.left), min(box[0] for box in boxes))
        top = max(int(fallback.top), min(box[1] for box in boxes))
        right = min(int(fallback.left + fallback.width), max(box[2] for box in boxes))
        bottom = min(int(fallback.top + fallback.height), max(box[3] for box in boxes))
        candidate = Region(left, top, max(1, right - left), max(1, bottom - top))
        fallback_area = max(1, int(fallback.width) * int(fallback.height))
        if candidate.width * candidate.height > fallback_area * 0.4:
            return Region(
                int(fallback.left), int(fallback.top),
                int(fallback.width), int(fallback.height),
            )
        return candidate

    @classmethod
    def _choose_visually_supported(
        cls,
        elements: list[Any],
        fallback: Any,
    ) -> tuple[Any | None, dict[str, Any]]:
        """Pick the duplicate UIA box that is visibly rendered on screen."""
        unique: dict[tuple[int, int, int, int], Any] = {}
        for element in elements:
            box = (
                int(getattr(element, "left", 0) or 0),
                int(getattr(element, "top", 0) or 0),
                int(getattr(element, "width", 0) or 0),
                int(getattr(element, "height", 0) or 0),
            )
            if box[2] > 0 and box[3] > 0:
                unique.setdefault(box, element)
        if len(unique) == 1 and len(elements) == 1:
            return next(iter(unique.values())), {"method": "single-candidate"}
        if len(unique) == 1:
            return None, {
                "method": "duplicate-same-bbox",
                "duplicate_count": len(elements),
                "confident": False,
            }
        if len(unique) < 2:
            return None, {}

        started = time.monotonic()
        roi = cls._relocation_ocr_region(list(unique.values()), fallback)
        frame = capture(roi)
        scored: list[tuple[float, Any, dict[str, Any]]] = []
        for (left, top, width, height), element in unique.items():
            x1 = max(0, left - roi.left - 4)
            y1 = max(0, top - roi.top - 4)
            x2 = min(frame.shape[1], left + width - roi.left + 4)
            y2 = min(frame.shape[0], top + height - roi.top + 4)
            patch = frame[y1:y2, x1:x2]
            if patch.size == 0:
                continue
            gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            contrast = float(gray.std())
            edge_ratio = float((cv2.Canny(gray, 50, 150) > 0).mean())
            score = contrast / 32.0 + edge_ratio * 8.0
            scored.append((score, element, {
                "bbox": [left, top, width, height],
                "score": round(score, 4),
                "contrast": round(contrast, 2),
                "edge_ratio": round(edge_ratio, 4),
            }))
        if not scored:
            return None, {}
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        # Contrast proves that a candidate is rendered, not that it carries
        # the requested text. Skip OCR only when every alternative is
        # effectively blank; two visibly real controls require text evidence.
        confident = best[0] >= 0.3 and runner_up < 0.3
        metadata = {
            "method": "visual-support",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "roi": [roi.left, roi.top, roi.width, roi.height],
            "candidates": [item[2] for item in scored],
            "confident": confident,
        }
        return (best[1] if confident else None), metadata

    @staticmethod
    def _expanded_relocation_region(elements: list[Any], fallback: Any) -> Region:
        boxes = [
            (
                int(getattr(element, "left", 0) or 0),
                int(getattr(element, "top", 0) or 0),
                int(getattr(element, "width", 0) or 0),
                int(getattr(element, "height", 0) or 0),
            )
            for element in elements
            if int(getattr(element, "width", 0) or 0) > 0
            and int(getattr(element, "height", 0) or 0) > 0
        ]
        if not boxes:
            return Region(
                int(fallback.left), int(fallback.top),
                int(fallback.width), int(fallback.height),
            )
        max_width = max(box[2] for box in boxes)
        max_height = max(box[3] for box in boxes)
        pad_x = max(64, max_width // 2)
        pad_y = max(140, max_height * 3)
        left = max(int(fallback.left), min(box[0] for box in boxes) - pad_x)
        top = max(int(fallback.top), min(box[1] for box in boxes) - pad_y)
        right = min(
            int(fallback.left + fallback.width),
            max(box[0] + box[2] for box in boxes) + pad_x,
        )
        bottom = min(
            int(fallback.top + fallback.height),
            max(box[1] + box[3] for box in boxes) + pad_y,
        )
        return Region(left, top, max(1, right - left), max(1, bottom - top))

    def _relocate(self, step: dict, anchor_key: str = "") -> tuple[int, int] | None:
        """Find click target on the live screen: text match → recorded coords."""
        cached_point, cache_meta = self._relocate_from_anchor(step, anchor_key)
        self._last_relocation = dict(cache_meta)
        if cached_point is not None:
            return cached_point
        text = _step_semantic_text(step)
        if text:
            hwnd = self._observe_hwnd()
            if not hwnd:
                coords = step.get("coords")
                if coords:
                    self._last_relocation.update({
                        "source": "recorded_coords",
                        "point": [int(coords[0]), int(coords[1])],
                    })
                    return int(coords[0]), int(coords[1])
                return None
            try:
                observe_started = time.monotonic()
                semantic_hits = uia.find_by_text(hwnd, text)
                query_attempts = 1
                unique_boxes = {
                    (
                        int(getattr(item, "left", 0) or 0),
                        int(getattr(item, "top", 0) or 0),
                        int(getattr(item, "width", 0) or 0),
                        int(getattr(item, "height", 0) or 0),
                    )
                    for item in semantic_hits
                }
                if len(semantic_hits) > 1 and len(unique_boxes) == 1:
                    if not self.stop_event.wait(0.04):
                        refreshed = uia.find_by_text(hwnd, text)
                        query_attempts += 1
                        if refreshed:
                            semantic_hits = refreshed
                if semantic_hits:
                    perception_ms = int((time.monotonic() - observe_started) * 1000)
                    perception_region = window_region(hwnd)
                    self._last_relocation = {
                        **self._last_relocation,
                        "perception_ms": perception_ms,
                        "perception_method": "uia-name-query",
                        "perception_dims": {
                            "uia_query": {
                                "count": len(semantic_hits),
                                "ms": perception_ms,
                                "attempts": query_attempts,
                            },
                        },
                    }
                else:
                    snapshot = fusion.observe(hwnd, include_icons=False)
                    perception_region = snapshot.region
                    self._last_relocation = {
                        **self._last_relocation,
                        "perception_ms": int((time.monotonic() - observe_started) * 1000),
                        "perception_method": "fusion",
                        "perception_dims": dict(getattr(snapshot, "dim_stats", {}) or {}),
                    }
                    exact = [e for e in snapshot.elements if e.text.strip() == text]
                    partial = [e for e in snapshot.elements if text in e.text]
                    semantic_hits = exact or partial
                hit = (semantic_hits or [None])[0]
                if hit:
                    visual_hits = [
                        item for item in semantic_hits
                        if getattr(item, "source", "") == "ocr"
                    ]
                    # Chromium can expose duplicate UIA trees for background
                    # tabs. Resolve duplicate semantic matches against the
                    # physical screenshot before sending input.
                    if not visual_hits and len(semantic_hits) > 1:
                        try:
                            visual_hit, visual_meta = self._choose_visually_supported(
                                semantic_hits,
                                perception_region,
                            )
                            self._last_relocation["visual_disambiguation"] = visual_meta
                            if visual_hit is not None:
                                visual_hits = [visual_hit]
                            else:
                                roi = self._relocation_ocr_region(semantic_hits, perception_region)
                                started = time.monotonic()
                                visual_hits = ocr.find_text(roi, text, min_conf=0.3)
                                self._last_relocation = {
                                    **self._last_relocation,
                                    "disambiguation_ms": int((time.monotonic() - started) * 1000),
                                    "ocr_roi": [roi.left, roi.top, roi.width, roi.height],
                                }
                                if not visual_hits:
                                    expanded = self._expanded_relocation_region(
                                        semantic_hits,
                                        perception_region,
                                    )
                                    expanded_started = time.monotonic()
                                    visual_hits = ocr.find_text(expanded, text, min_conf=0.3)
                                    self._last_relocation.update({
                                        "expanded_disambiguation_ms": int(
                                            (time.monotonic() - expanded_started) * 1000
                                        ),
                                        "expanded_ocr_roi": [
                                            expanded.left, expanded.top,
                                            expanded.width, expanded.height,
                                        ],
                                    })
                        except Exception as exc:
                            logger.debug("replay OCR coordinate cross-check failed: %s", exc)
                    if visual_hits:
                        hit = visual_hits[0]
                    elif len(semantic_hits) > 1:
                        self._last_relocation.update({
                            "selector_rejected": True,
                            "reason": "duplicate semantic candidates lacked matching pixel evidence",
                        })
                        hit = None
                    if hit is None:
                        return None
                    resolved = {
                        **self._last_relocation,
                        "source": getattr(hit, "source", "semantic"),
                        "text": getattr(hit, "text", text),
                        "point": [int(hit.cx), int(hit.cy)],
                    }
                    hit_width = int(getattr(hit, "width", 0) or 0)
                    hit_height = int(getattr(hit, "height", 0) or 0)
                    if hit_width > 0 and hit_height > 0:
                        resolved["bbox"] = [
                            int(getattr(hit, "left", 0) or 0),
                            int(getattr(hit, "top", 0) or 0),
                            hit_width,
                            hit_height,
                        ]
                    self._last_relocation = resolved
                    return hit.cx, hit.cy
            except Exception as exc:
                logger.debug("replay 重定位失败: %s", exc)
        coords = step.get("coords")
        if coords:
            self._last_relocation = {
                **self._last_relocation,
                "source": "recorded_coords",
                "point": [int(coords[0]), int(coords[1])],
            }
            return int(coords[0]), int(coords[1])
        return None

    def _capture_action_surface(self):
        try:
            return capture(window_region(self.hwnd)) if self.hwnd else capture()
        except Exception:
            return None

    @staticmethod
    def _step_requests_visual_servo(step: dict[str, Any]) -> bool:
        if step.get("visual_servo") is not None:
            return bool(step.get("visual_servo"))
        if step.get("moving_target") or step.get("track_target"):
            return True
        resolved = step.get("resolved_element")
        source = str(resolved.get("source") or "") if isinstance(resolved, dict) else ""
        return source.casefold().startswith("servo-")

    def _visual_servo_replay_target(
        self,
        step: dict[str, Any],
        point: tuple[int, int],
    ) -> tuple[tuple[int, int] | None, dict[str, Any]]:
        resolved = dict(self._last_relocation)
        recorded = step.get("resolved_element")
        if not isinstance(recorded, dict):
            recorded = {}
        raw_bbox = resolved.get("bbox") or recorded.get("bbox")
        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
            left, top, width, height = (int(value) for value in raw_bbox)
        else:
            width, height = 120, 40
            left = int(point[0]) - width // 2
            top = int(point[1]) - height // 2
        anchor = step.get("anchor")
        anchor_role = anchor.get("role") if isinstance(anchor, dict) else ""
        text = _step_semantic_text(step)
        initial = UIElement(
            elem_id=int(recorded.get("elem_id") or -1),
            source=str(resolved.get("source") or recorded.get("source") or "replay"),
            role=str(recorded.get("role") or anchor_role or "button"),
            text=text,
            cx=int(point[0]),
            cy=int(point[1]),
            left=left,
            top=top,
            width=max(12, width),
            height=max(12, height),
            confidence=float(recorded.get("confidence") or 0.0),
        )
        labels = [text]
        if isinstance(anchor, dict):
            fallback = anchor.get("fallback_text")
            if isinstance(fallback, str):
                fallback = [fallback]
            if isinstance(fallback, list):
                labels.extend(str(item).strip() for item in fallback if str(item).strip())
        try:
            surface = window_region(int(self.hwnd)) if self.hwnd else None
        except Exception:
            surface = None
        if surface is not None:
            margin = 12
            corners = [
                (surface.left + margin, surface.top + margin),
                (surface.right - margin, surface.top + margin),
                (surface.left + margin, surface.bottom - margin),
                (surface.right - margin, surface.bottom - margin),
            ]
            arm_point = max(
                corners,
                key=lambda item: (item[0] - point[0]) ** 2 + (item[1] - point[1]) ** 2,
            )
        else:
            arm_point = (max(0, int(point[0]) - 240), max(0, int(point[1]) - 180))
        executor.move_mouse(int(arm_point[0]), int(arm_point[1]))
        time.sleep(0.03)
        tracked, servo_trace = track_visual_target(
            initial,
            [label for label in labels if label],
            capture_fn=capture,
            move_fn=executor.move_mouse,
            ocr_fn=ocr.recognize,
            config=ServoConfig.from_action({
                **step,
                "servo_strict": True,
                "servo_always_ocr": True,
            }),
        )
        if tracked is None:
            servo_trace["arm_point"] = {"x": int(arm_point[0]), "y": int(arm_point[1])}
            return None, servo_trace
        servo_trace["arm_point"] = {"x": int(arm_point[0]), "y": int(arm_point[1])}
        self._last_relocation.update({
            "source": tracked.source,
            "text": tracked.text or text,
            "point": [int(tracked.cx), int(tracked.cy)],
            "bbox": [
                int(tracked.left), int(tracked.top),
                int(tracked.width), int(tracked.height),
            ],
            "visual_servo": servo_trace,
        })
        return (int(tracked.cx), int(tracked.cy)), servo_trace

    def _wait_for_url_launch_settle(
        self,
        before: Any,
        timeout_s: float = 1.2,
    ) -> dict[str, Any]:
        """Wait for a newly opened browser tab to replace an existing surface."""
        if before is None or not self.hwnd:
            return {"observed_change": False, "waited_ms": 0}
        started = time.monotonic()
        deadline = started + max(0.2, timeout_s)
        previous = before
        observed_change = False
        stable_samples = 0
        peak_ratio = 0.0
        while time.monotonic() < deadline:
            if self.stop_event.wait(0.04):
                break
            current = self._capture_action_surface()
            if current is None:
                break
            from_start = self._screen_change_ratio(before, current)
            between_frames = self._screen_change_ratio(previous, current)
            peak_ratio = max(peak_ratio, from_start)
            if from_start >= 0.00005:
                observed_change = True
            if observed_change and between_frames < 0.00005:
                stable_samples += 1
                if stable_samples >= 2 and time.monotonic() - started >= 0.35:
                    break
            else:
                stable_samples = 0
            # If Windows reused the current tab without any visible transition,
            # retain a short grace period but do not spend the full timeout.
            if not observed_change and time.monotonic() - started >= 0.45:
                break
            previous = current
        return {
            "observed_change": observed_change,
            "waited_ms": int((time.monotonic() - started) * 1000),
            "peak_change_ratio": peak_ratio,
            "stable_samples": stable_samples,
        }

    @staticmethod
    def _screen_change_ratio(before: Any, after: Any) -> float:
        if before is None or after is None or getattr(before, "shape", None) != getattr(after, "shape", None):
            return 0.0
        delta = cv2.absdiff(before, after)
        if len(delta.shape) == 3:
            delta = delta.max(axis=2)
        return float((delta > 12).sum()) / max(1, int(delta.size))

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
        self._target_signature = json.dumps(
            {key: hint[key] for key in ("title", "process", "hwnd") if key in hint},
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "ok": True,
            "required": True,
            "hwnd": self.hwnd,
            "title": found.get("title"),
            "hint": hint,
        }

    @staticmethod
    def _condition_checks(script: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        conditions = script.get("conditions")
        if not isinstance(conditions, dict):
            return [], []
        checks: list[dict[str, Any]] = []
        ignored: list[str] = []
        target_keys = {"target_window", "target", "window_title", "window_appears"}
        direct = {
            key: value for key, value in conditions.items()
            if key in verify.SUPPORTED_CONDITIONS and key not in target_keys
        }
        if direct:
            checks.append(direct)
        anchors = conditions.get("visible_anchors")
        if isinstance(anchors, str):
            anchors = [anchors]
        if isinstance(anchors, list):
            checks.extend(
                {"text_appears": str(anchor)}
                for anchor in anchors
                if str(anchor).strip()
            )
        raw_checks = conditions.get("checks")
        if isinstance(raw_checks, dict):
            raw_checks = [raw_checks]
        if isinstance(raw_checks, list):
            for raw in raw_checks:
                if not isinstance(raw, dict):
                    ignored.append(str(raw))
                    continue
                check = {key: value for key, value in raw.items() if key in verify.SUPPORTED_CONDITIONS}
                unknown = [key for key in raw if key not in verify.SUPPORTED_CONDITIONS and key != "timeout_s"]
                if unknown:
                    ignored.extend(unknown)
                if check:
                    if "timeout_s" in raw:
                        check["timeout_s"] = raw["timeout_s"]
                    checks.append(check)
        return checks, ignored

    def _preflight(self, script: dict[str, Any]) -> dict[str, Any]:
        target = self._preflight_target_window(script)
        if not target.get("ok", False):
            return target
        safety = script.get("safety") if isinstance(script.get("safety"), dict) else {}
        hint = _target_window_hint(script)
        if (
            safety.get("requires_preflight")
            and safety.get("mutates_desktop")
            and not hint
        ):
            error = "desktop Automation IR requires an explicit target_window preflight"
            result = {"ok": False, "error": error, "safety": safety}
            self._emit_step("preflight", "preflight.target_window", False, result)
            return {"ok": False, "required": True, "error": error, "safety": safety}

        checks, ignored = self._condition_checks(script)
        results: list[dict[str, Any]] = []
        for index, check in enumerate(checks, start=1):
            visual = any(
                key not in {"timeout_s", *groundtruth.GROUNDTRUTH_KEYS}
                for key in check
            )
            if visual and not self.hwnd:
                error = "visual replay preflight checks require target_window"
                result = {"ok": False, "error": error, "check": check}
                self._emit_step(f"preflight.{index}", "preflight.condition", False, result)
                return {
                    "ok": False,
                    "required": True,
                    "error": error,
                    "target": target,
                    "checks": results + [result],
                    "ignored": ignored,
                }
            timeout_s = max(0.2, min(float(check.get("timeout_s") or 2.0), 10.0))
            outcome = verify.check(
                check,
                hwnd=self.hwnd,
                timeout_s=timeout_s,
                stop_event=self.stop_event,
            )
            record = {"ok": outcome.verified, "check": check, **outcome.to_dict()}
            results.append(record)
            if not outcome.verified:
                self._emit_step(f"preflight.{index}", "preflight.condition", False, record)
                return {
                    "ok": False,
                    "required": True,
                    "error": f"replay preflight condition failed: {outcome.detail}",
                    "target": target,
                    "checks": results,
                    "ignored": ignored,
                }
        return {
            "ok": True,
            "required": bool(target.get("required") or checks or safety.get("requires_preflight")),
            "target": target,
            "checks": results,
            "ignored": ignored,
            **({"hwnd": self.hwnd} if self.hwnd else {}),
        }

    @staticmethod
    def _evidence_expectation(requirement: Any) -> dict[str, Any] | None:
        if not isinstance(requirement, dict):
            return None
        direct = {
            key: value for key, value in requirement.items()
            if key in verify.SUPPORTED_CONDITIONS
        }
        if direct:
            return direct
        predicate = str(requirement.get("predicate") or "").strip().casefold()
        value = requirement.get("value")
        mapping = {
            "visible.text": "text_appears",
            "window.visible": "window_appears",
            "file.exists": "file_exists",
            "process.running": "process_running",
        }
        key = mapping.get(predicate)
        return {key: value} if key and value not in (None, "") else None

    def _postflight_evidence(self, script: dict[str, Any]) -> dict[str, Any]:
        evidence = script.get("evidence")
        required = evidence.get("required", []) if isinstance(evidence, dict) else evidence
        if not isinstance(required, list):
            return {"ok": True, "required": False, "checks": []}
        checks: list[dict[str, Any]] = []
        advisory: list[str] = []
        unsupported: list[Any] = []
        for requirement in required:
            expectation = self._evidence_expectation(requirement)
            if expectation:
                checks.append(expectation)
            elif isinstance(requirement, str):
                advisory.append(requirement)
            else:
                unsupported.append(requirement)
        if unsupported:
            error = "unsupported structured evidence requirement"
            result = {"ok": False, "error": error, "unsupported": unsupported}
            self._emit_step("postflight", "postflight.evidence", False, result)
            return {**result, "required": True, "checks": [], "advisory": advisory}
        results = []
        for index, check in enumerate(checks, start=1):
            outcome = verify.check(
                check,
                hwnd=self.hwnd,
                timeout_s=max(0.2, min(float(check.get("timeout_s") or 2.0), 10.0)),
                stop_event=self.stop_event,
            )
            record = {"ok": outcome.verified, "check": check, **outcome.to_dict()}
            results.append(record)
            if not outcome.verified:
                self._emit_step(f"postflight.{index}", "postflight.evidence", False, record)
                return {
                    "ok": False,
                    "required": True,
                    "error": f"replay evidence requirement failed: {outcome.detail}",
                    "checks": results,
                    "advisory": advisory,
                }
        return {"ok": True, "required": bool(checks), "checks": results, "advisory": advisory}

    def _run_step(self, idx: int, step: dict) -> dict[str, Any]:
        tool = step.get("tool", "")
        expect = step.get("expect")

        if tool == "launch":
            command = str(step.get("command") or "")
            is_url_launch = bool(re.match(r"^https?://", command, flags=re.IGNORECASE))
            launch_before = self._capture_action_surface() if self.hwnd and is_url_launch else None
            if is_url_launch and launch_before is None and isinstance(expect, dict):
                existing_title = str(
                    expect.get("window_title") or expect.get("window_appears") or ""
                ).strip()
                existing = win32.find_window(existing_title) if existing_title else None
                if existing and existing.get("hwnd"):
                    try:
                        launch_before = capture(window_region(int(existing["hwnd"])))
                    except Exception:
                        launch_before = None
            verify_expect = expect
            if isinstance(expect, dict) and expect.get("window_title"):
                # A launch target need not win foreground focus on Windows.
                # Search all windows immediately instead of spending three
                # foreground-title retry rounds before the deterministic
                # title fallback can run.
                verify_expect = dict(expect)
                verify_expect.setdefault("window_appears", verify_expect.pop("window_title"))
            result = verify.run_with_verify(
                "launch", lambda: executor.launch(step["command"]),
                expect=verify_expect, hwnd=self.hwnd, timeout_s=10.0, retryable=False,
                stop_event=self.stop_event)
            if result.get("ok"):
                expected = expect if isinstance(expect, dict) else {}
                title = str(
                    expected.get("window_title")
                    or expected.get("window_appears")
                    or ""
                ).strip()
                found = win32.find_window(title) if title else None
                if found and found.get("hwnd"):
                    self.hwnd = int(found["hwnd"])
                    result["target_hwnd"] = self.hwnd
                    focus_result = executor.focus_window(self.hwnd)
                    result["focused"] = bool(focus_result.get("ok", False))
                    # Launch verification may have sampled the old foreground
                    # window while Windows was still applying focus-stealing
                    # rules. Finding the declared title is deterministic proof
                    # that the requested target opened.
                    if result.get("verified") is False:
                        result["verified"] = True
                        result["verify_detail"] = f'target window found: "{found.get("title", "")}"'
                        result["verify_method"] = "win32-title"
                elif (result.get("post_state") or {}).get("foreground_hwnd"):
                    self.hwnd = int(result["post_state"]["foreground_hwnd"])
                    result["target_hwnd"] = self.hwnd
                if launch_before is not None and self.hwnd:
                    result["launch_settle"] = self._wait_for_url_launch_settle(launch_before)
            return result
        if tool == "focus_window":
            found = win32.find_window(step.get("title", ""))
            if not found:
                return {"ok": False, "error": f'窗口 "{step.get("title")}" 不存在'}
            self.hwnd = found["hwnd"]
            return executor.focus_window(self.hwnd)
        if tool == "click":
            anchor_key = str(idx)
            point = self._relocate(step, anchor_key)
            if point is None:
                return {"ok": False, "error": "无法定位点击目标"}
            servo_trace = None
            if self._step_requests_visual_servo(step):
                point, servo_trace = self._visual_servo_replay_target(step, point)
                if point is None:
                    return {
                        "ok": False,
                        "verified": False,
                        "blocked": True,
                        "error": "visual servo lost the replay target before mouse-down",
                        "resolved": dict(self._last_relocation),
                        "visual_servo": servo_trace,
                    }
            x, y = point
            implicit_verify = not expect and not bool(step.get("allow_no_change"))
            needs_learning = bool(
                self.anchor_profile
                and self._last_relocation.get("source") != "anchor_cache"
            )
            surface = None
            if needs_learning and self.hwnd:
                try:
                    surface = window_region(int(self.hwnd))
                except Exception:
                    surface = None
            before = self._capture_action_surface() if implicit_verify or needs_learning else None
            press_frames: list[Any] = []

            def capture_pressed_state() -> None:
                frame = self._capture_action_surface()
                if frame is not None:
                    press_frames.append(frame)

            previous_probe = None
            probe_installed = False
            if before is not None and implicit_verify:
                install_probe = getattr(executor, "install_press_probe", None)
                if callable(install_probe):
                    previous_probe = install_probe(capture_pressed_state)
                    probe_installed = True
            try:
                result = verify.run_with_verify(
                    "click",
                    lambda: executor.click(x, y, button=step.get("button", "left"),
                                           double=bool(step.get("double")), hwnd=self.hwnd,
                                           prepositioned=bool(servo_trace)),
                    expect=expect, hwnd=self.hwnd, stop_event=self.stop_event)
            finally:
                if probe_installed:
                    restore_probe = getattr(executor, "restore_press_probe", None)
                    if callable(restore_probe):
                        restore_probe(previous_probe)

            result["resolved"] = dict(self._last_relocation)
            if servo_trace is not None:
                result["visual_servo"] = servo_trace
            if implicit_verify and result.get("ok"):
                after = self._capture_action_surface()
                post_ratio = self._screen_change_ratio(before, after)
                candidates = [("after", post_ratio)]
                candidates.extend(
                    ("press", self._screen_change_ratio(before, frame))
                    for frame in press_frames
                )
                delta_source, ratio = max(candidates, key=lambda item: item[1])
                changed = ratio >= 0.00005
                result.update({
                    "verified": changed,
                    "verify_method": "screen-delta",
                    "verify_detail": (
                        f"visible change observed ({delta_source}, ratio={ratio:.6f})"
                        if changed
                        else "click produced no visible change; set allow_no_change=true only when intentional"
                    ),
                    "screen_change_ratio": ratio,
                    "post_change_ratio": post_ratio,
                    "screen_delta_source": delta_source,
                    "press_sample_count": len(press_frames),
                    "transient_observed": bool(changed and delta_source == "press"),
                })
            if (
                result.get("ok")
                and result.get("verified", True) is not False
                and needs_learning
                and self._learn_anchor(anchor_key, step, before, surface)
            ):
                self._last_relocation["anchor_learned"] = True
                result["resolved"] = dict(self._last_relocation)
            return result
        if tool == "type_text":
            anchor_key = str(idx)
            point = self._relocate(step, anchor_key)
            needs_learning = bool(
                point
                and self.anchor_profile
                and self._last_relocation.get("source") != "anchor_cache"
            )
            surface = None
            if needs_learning and self.hwnd:
                try:
                    surface = window_region(int(self.hwnd))
                except Exception:
                    surface = None
            before = self._capture_action_surface() if needs_learning else None

            def action() -> dict:
                if point:
                    executor.click(point[0], point[1], hwnd=self.hwnd)
                    time.sleep(0.15)
                return executor.type_text(step.get("text_input", ""))

            result = verify.run_with_verify(
                "type_text", action, expect=expect, hwnd=self.hwnd, retryable=False,
                stop_event=self.stop_event)
            result["resolved"] = dict(self._last_relocation)
            if (
                result.get("ok")
                and result.get("verified", True) is not False
                and needs_learning
                and self._learn_anchor(anchor_key, step, before, surface)
            ):
                self._last_relocation["anchor_learned"] = True
                result["resolved"] = dict(self._last_relocation)
            return result
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
        if script.get("format") == "automation-ir/1":
            script = wps_from_automation_ir(script)
        return self.run_script(script, script_name=path.name)

    def run_script(self, script: dict[str, Any], *, script_name: str = "in-memory") -> dict[str, Any]:
        error = validate_script(script)
        if error:
            return {
                "ok": False,
                "error": error,
                "results": [],
                "preflight": {"ok": False, "required": False, "error": error},
                "anchor_cache": self._anchor_summary(),
            }
        steps = script.get("steps", [])
        # wps/2: seed variables (all string-valued). v1 scripts have none.
        raw_vars = script.get("vars") or {}
        self.vars = {str(k): str(v) for k, v in raw_vars.items()}
        self.hwnd = None
        self._target_signature = ""
        self._results = []
        self._reset_anchor_metrics()
        total = replay_control.count_leaves(steps)
        BUS.publish("replay", state="start", script=script_name, total=total)
        preflight = self._preflight(script)
        if not preflight.get("ok", False):
            BUS.publish("replay", state="done", ok=False)
            self._flush_anchor_cache()
            return {
                "ok": False,
                "results": self._results,
                "preflight": preflight,
                "anchor_cache": self._anchor_summary(),
            }
        ok_all = self._exec_steps(steps)
        postflight = (
            self._postflight_evidence(script)
            if ok_all
            else {"ok": False, "required": False, "checks": [], "skipped": True}
        )
        ok_all = bool(ok_all and postflight.get("ok", False))
        BUS.publish("replay", state="done", ok=ok_all)
        self._flush_anchor_cache()
        return {
            "ok": ok_all,
            "results": self._results,
            "preflight": preflight,
            "postflight": postflight,
            "anchor_cache": self._anchor_summary(),
        }

    def run_steps(
        self,
        steps: list[dict[str, Any]],
        *,
        target_window: Any = None,
        variables: dict[str, Any] | None = None,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Execute an in-memory WPS segment without resetting replay state.

        ARP uses this entry point for editable offline replay nodes. Window
        anchors, variables, and prior state survive across adjacent nodes while
        each call returns only the records produced by that segment.
        """
        script: dict[str, Any] = {"format": "wps/2", "steps": steps}
        if target_window not in (None, "", {}):
            script["target_window"] = target_window
        error = validate_script(script)
        if error:
            return {
                "ok": False,
                "error": error,
                "results": [],
                "preflight": {"ok": False, "required": False, "error": error},
            }

        self._reset_anchor_metrics()
        if variables:
            self.vars.update({str(key): str(value) for key, value in variables.items()})
        before = len(self._results)
        preflight: dict[str, Any] = {"ok": True, "required": False}
        target_hint = _target_window_hint(script)
        target_signature = json.dumps(
            {key: target_hint[key] for key in ("title", "process", "hwnd") if key in target_hint},
            ensure_ascii=False,
            sort_keys=True,
        ) if target_hint else ""
        if target_hint and (not self.hwnd or target_signature != self._target_signature):
            preflight = self._preflight_target_window(script)
            if not preflight.get("ok", False):
                return {
                    "ok": False,
                    "error": str(preflight.get("error") or "target window preflight failed"),
                    "results": self._results[before:],
                    "preflight": preflight,
                    "anchor_cache": self._anchor_summary(),
                }

        BUS.publish("replay", state="segment_start", total=replay_control.count_leaves(steps))
        ok_all = self._exec_steps(steps, prefix=prefix)
        results = self._results[before:]
        BUS.publish("replay", state="segment_done", ok=ok_all, count=len(results))
        self._flush_anchor_cache()
        response = {
            "ok": ok_all,
            "results": results,
            "preflight": preflight,
            "hwnd": self.hwnd,
            "variables": dict(self.vars),
            "anchor_cache": self._anchor_summary(),
        }
        if not ok_all and results:
            failed = next((item for item in reversed(results) if not item.get("ok")), results[-1])
            response["error"] = str(
                failed.get("error")
                or failed.get("verify_detail")
                or f"{failed.get('tool') or 'replay step'} failed"
            )
        return response

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
            if self.step_delay and self.stop_event.wait(self.step_delay):
                BUS.publish("replay", state="stopped", at=idx, during="step_delay")
                return False
        return True

    def _emit_step(self, idx: str, tool: str, ok: bool, result: dict) -> None:
        detail_keys = (
            "error", "verify_detail", "verify_method", "verified", "attempts",
            "timeout", "stopped", "hint", "resolved", "screen_change_ratio",
            "post_change_ratio", "screen_delta_source", "press_sample_count",
            "transient_observed", "launch_settle",
        )
        BUS.publish("replay", state="step", index=idx, tool=tool, ok=ok,
                    detail={k: v for k, v in result.items()
                            if k in detail_keys})
        record = {"index": idx, "tool": tool, "ok": ok}
        for key in detail_keys:
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
                delay = max(0.0, float(step.get("seconds", 0)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "ext.delay seconds 非法"}
            if self.stop_event.wait(delay):
                return {
                    "ok": False,
                    "stopped": True,
                    "error": "replay canceled during ext.delay",
                }
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


def replay(
    script_path: str | Path,
    stop_event: threading.Event | None = None,
    anchor_profile: str = "",
) -> dict:
    path = Path(script_path)
    if not path.is_absolute():
        path = SCRIPTS_DIR / path
    profile = anchor_profile or replay_anchors.script_profile(path)
    return Replayer(stop_event, anchor_profile=profile).run(path)


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
