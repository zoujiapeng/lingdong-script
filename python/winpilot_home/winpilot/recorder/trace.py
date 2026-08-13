"""Trace recording, script export, and offline (0-token) replay.

- TraceRecorder: a BUS sink that writes every event of the active run to
  ``traces/<run_id>.jsonl`` and saves a screenshot after each action tool.
- export_script: distill a trace into a ``.wps.json`` replay script whose
  steps carry multi-level selectors (element text → recorded coords).
- replay: execute a script offline, re-locating elements on the live screen
  through the same perception fusion, with the same verify engine.
"""
from __future__ import annotations

import base64
import hashlib
import json
import inspect
import os
import queue
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..action import executor, verify
from ..automation_ir import automation_ir_from_wps, validate_automation_ir, wps_from_automation_ir
from ..config import CONFIG, SCRIPTS_DIR, TRACES_DIR
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

# These colors are intentionally stable: a saved pointer-debug image can be
# understood without knowing which application happened to be underneath it.
_POINTER_DEBUG_COLORS = {
    "model_pixel": "#FFFFFF",
    "cursor_before": "#00E5FF",
    "move": "#00D9FF",
    "click": "#FF3158",
    "double_click": "#FF8A00",
    "drag_start": "#21D375",
    "drag_path": "#FFD400",
    "drag_end": "#BD4BFF",
    "scroll": "#2F80FF",
    "mouse_down": "#20E070",
    "mouse_up": "#FF45A0",
    "relative_move": "#00FFD5",
    "horizontal_scroll": "#9B51E0",
}

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


def _pointer_color(name: str) -> tuple[int, int, int]:
    value = _POINTER_DEBUG_COLORS[name].lstrip("#")
    return tuple(
        int(value[index:index + 2], 16)
        for index in (4, 2, 0)
    )


def _pointer_image_point(
    point: tuple[int, int],
    *,
    frame_region: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[int, int]:
    left, top, frame_width, frame_height = frame_region
    image_width, image_height = image_size
    x = round((point[0] - left) * max(0, image_width - 1) / max(1, frame_width - 1))
    y = round((point[1] - top) * max(0, image_height - 1) / max(1, frame_height - 1))
    return (
        max(0, min(image_width - 1, x)),
        max(0, min(image_height - 1, y)),
    )


def _pointer_label(
    image: Any,
    point: tuple[int, int],
    text: str,
    color: tuple[int, int, int],
) -> None:
    height, width = image.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.42
    thickness = 1
    (text_width, text_height), baseline = cv2.getTextSize(
        text, font, scale, thickness,
    )
    x = max(2, min(width - text_width - 5, point[0] + 8))
    y = max(text_height + 4, min(height - baseline - 3, point[1] - 8))
    cv2.rectangle(
        image,
        (x - 3, y - text_height - 3),
        (x + text_width + 3, y + baseline + 2),
        (12, 12, 12),
        -1,
    )
    cv2.putText(image, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def _pointer_marker(
    image: Any,
    point: tuple[int, int],
    color_name: str,
    text: str,
    *,
    double: bool = False,
) -> None:
    color = _pointer_color(color_name)
    cv2.circle(image, point, 9, color, 2, cv2.LINE_AA)
    if double:
        cv2.circle(image, point, 15, color, 2, cv2.LINE_AA)
    cv2.line(image, (point[0] - 12, point[1]), (point[0] + 12, point[1]), color, 1)
    cv2.line(image, (point[0], point[1] - 12), (point[0], point[1] + 12), color, 1)
    cv2.circle(image, point, 2, _pointer_color("model_pixel"), -1, cv2.LINE_AA)
    _pointer_label(image, point, text, color)


def _pointer_arrow(
    image: Any,
    start: tuple[int, int],
    end: tuple[int, int],
    color_name: str,
    text: str,
) -> None:
    color = _pointer_color(color_name)
    cv2.arrowedLine(image, start, end, color, 3, cv2.LINE_AA, tipLength=0.12)
    _pointer_label(image, end, text, color)


def _render_pointer_debug(image: Any, item: Mapping[str, Any]) -> Any:
    """Draw every canonical pointer event over the exact submitted pixels."""

    output = image.copy()
    height, width = output.shape[:2]
    raw_region = tuple(item.get("frame_region") or ())
    if (
        len(raw_region) != 4
        or any(type(value) is not int for value in raw_region)
        or raw_region[2] <= 0
        or raw_region[3] <= 0
    ):
        raise ValueError("pointer debug frame region is invalid")
    frame_region = raw_region

    def mapped(x: Any, y: Any) -> tuple[int, int]:
        if type(x) is not int or type(y) is not int:
            raise ValueError("pointer debug physical point is invalid")
        return _pointer_image_point(
            (x, y),
            frame_region=frame_region,
            image_size=(width, height),
        )

    physical = dict(item.get("physical_arguments") or {})
    action = str(item.get("action") or "")
    cursor_raw = item.get("cursor_before")
    cursor_screen = (
        tuple(cursor_raw)
        if isinstance(cursor_raw, (tuple, list))
        and len(cursor_raw) == 2
        and all(type(value) is int for value in cursor_raw)
        else None
    )
    cursor = mapped(*cursor_screen) if cursor_screen is not None else None
    if cursor is not None:
        _pointer_marker(
            output,
            cursor,
            "cursor_before",
            f"CURSOR BEFORE screen{cursor_screen}",
        )

    if action in {"click", "double_click", "move_mouse", "scroll"}:
        target_screen = (int(physical["x"]), int(physical["y"]))
        target = mapped(*target_screen)
        source = dict(item.get("image_coordinates") or {})
        coordinate_text = (
            f"img({source.get('image_x')},{source.get('image_y')}) "
            f"screen{target_screen}"
        )
        if action == "move_mouse":
            if cursor is not None and cursor != target:
                _pointer_arrow(output, cursor, target, "move", "MOVE")
            _pointer_marker(
                output, target, "move", f"MOVE TARGET {coordinate_text}",
            )
        elif action == "click":
            _pointer_marker(output, target, "click", f"CLICK {coordinate_text}")
        elif action == "double_click":
            _pointer_marker(
                output,
                target,
                "double_click",
                f"DOUBLE CLICK {coordinate_text}",
                double=True,
            )
        else:
            amount = int(physical.get("signed_wheel_amount") or 0)
            length = min(70, max(25, abs(amount) // 4))
            end_y = target[1] - length if amount > 0 else target[1] + length
            end = (target[0], max(2, min(height - 3, end_y)))
            _pointer_arrow(
                output, target, end, "scroll", f"SCROLL {amount:+d}",
            )
            _pointer_marker(
                output, target, "scroll", f"SCROLL AT {coordinate_text}",
            )
    elif action == "drag":
        screen_path = [
            (int(physical["x1"]), int(physical["y1"])),
            *[
                (int(point[0]), int(point[1]))
                for point in physical.get("points") or []
            ],
            (int(physical["x2"]), int(physical["y2"])),
        ]
        path: list[tuple[int, int]] = []
        for point in screen_path:
            image_point = mapped(*point)
            if not path or image_point != path[-1]:
                path.append(image_point)
        if len(path) > 1:
            for start, end in zip(path, path[1:]):
                _pointer_arrow(output, start, end, "drag_path", "DRAG PATH")
        source_path = list(dict(item.get("image_coordinates") or {}).get("image_path") or [])
        start_source = source_path[0] if source_path else "?"
        end_source = source_path[-1] if source_path else "?"
        _pointer_marker(
            output,
            path[0],
            "drag_start",
            f"DRAG START img{start_source} screen{screen_path[0]}",
        )
        _pointer_marker(
            output,
            path[-1],
            "drag_end",
            f"DRAG END img{end_source} screen{screen_path[-1]}",
        )
    elif action == "input_sequence":
        current_screen = cursor_screen
        current = cursor
        for event in physical.get("pointer_events") or []:
            index = int(event.get("index", 0))
            kind = str(event.get("kind") or "")
            if kind == "move":
                target_screen = (int(event["x"]), int(event["y"]))
                target = mapped(*target_screen)
                if current is not None:
                    _pointer_arrow(output, current, target, "move", f"{index}: MOVE")
                _pointer_marker(
                    output,
                    target,
                    "move",
                    f"{index}: MOVE screen{target_screen}",
                )
                current_screen, current = target_screen, target
            elif kind == "move_relative" and current_screen is not None:
                target_screen = (
                    current_screen[0] + int(event["x"]),
                    current_screen[1] + int(event["y"]),
                )
                target = mapped(*target_screen)
                assert current is not None
                _pointer_arrow(
                    output,
                    current,
                    target,
                    "relative_move",
                    f"{index}: REL {int(event['x']):+d},{int(event['y']):+d}",
                )
                current_screen, current = target_screen, target
            elif kind in {"mouse_down", "mouse_up"} and current is not None:
                color = "mouse_down" if kind == "mouse_down" else "mouse_up"
                button = str(event.get("button") or "")
                _pointer_marker(
                    output, current, color, f"{index}: {kind.upper()} {button}",
                )
            elif kind in {"wheel", "hwheel"} and current is not None:
                amount = int(event.get("amount") or 0)
                if kind == "wheel":
                    end = (
                        current[0],
                        max(2, min(height - 3, current[1] + (-35 if amount > 0 else 35))),
                    )
                    color = "scroll"
                else:
                    end = (
                        max(2, min(width - 3, current[0] + (35 if amount > 0 else -35))),
                        current[1],
                    )
                    color = "horizontal_scroll"
                _pointer_arrow(
                    output, current, end, color, f"{index}: {kind.upper()} {amount:+d}",
                )

    submitted = tuple(item.get("submitted_image_size") or ())
    banner = (
        f"POINTER DEBUG | {action.upper()} | frame {item.get('frame_id')} | "
        f"image {width}x{height} submitted {submitted} | "
        f"screen region {frame_region}"
    )
    cv2.rectangle(output, (0, 0), (width - 1, min(height - 1, 25)), (8, 8, 8), -1)
    cv2.putText(
        output,
        banner,
        (6, min(18, height - 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return output


class TraceRecorder:
    """Singleton sink: call start(run_id) before a run, stop() after."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callbacks_idle = threading.Condition(self._lock)
        self._callbacks_in_flight = 0
        self._run_id: str | None = None
        self._file = None
        self._step = 0
        self._evidence_step = 0
        self._pointer_debug_step = 0
        self._accepting = False
        self._action_steps: dict[str, int] = {}
        self._action_regions: dict[str, Region] = {}
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1024)
        self._dropped_events = 0
        self._accepted_events = 0
        self._write_failures = 0
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
            self._pointer_debug_step = 0
            self._action_steps = {}
            self._action_regions = {}
            self._dropped_events = 0
            self._accepted_events = 0
            self._write_failures = 0
            self._accepting = True
        BUS.publish("trace", state="recording", run_id=run_id)

    def owns(self, run_id: str) -> bool:
        """Return whether this recorder is accepting the exact run."""

        with self._lock:
            return bool(
                self._accepting
                and self._file is not None
                and self._run_id == run_id
            )

    def stop(self) -> None:
        with self._callbacks_idle:
            run_id = self._run_id
            self._accepting = False
            self._callbacks_idle.wait_for(lambda: self._callbacks_in_flight == 0)
        self._queue.join()
        with self._lock:
            if run_id and self._file is not None and self._run_id == run_id:
                self._write_integrity_locked(run_id)
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
        with self._callbacks_idle:
            if not self._accepting or self._file is None or event.get("type") == "trace":
                return
            self._callbacks_in_flight += 1
        try:
            self._record_accepted_event(event)
        finally:
            with self._callbacks_idle:
                self._callbacks_in_flight -= 1
                if self._callbacks_in_flight == 0:
                    self._callbacks_idle.notify_all()

    def _record_accepted_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            run_id = self._run_id
            step: int | None = None
            phase = "after"
            event_type = str(event.get("type") or "")
            action_key = self._action_key(event)
            frame_region: Region | None = None
            if event_type == "agent_tool_start" and event.get("tool") in _SCREENSHOT_TOOLS:
                self._step += 1
                step = self._step
                phase = "before"
                self._action_steps[action_key] = step
            elif event_type == "agent_tool" and event.get("tool") in _SCREENSHOT_TOOLS:
                step = self._action_steps.pop(action_key, None)
                frame_region = self._action_regions.pop(action_key, None)
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
                frame_region = self._action_regions.pop(action_key, None)
                if step is None:
                    self._step += 1
                    step = self._step
                phase = "batch_after"

        frame = None
        baseline_capture_source = ""
        post_capture_source = ""
        strict_trace = (
            os.environ.get("WINPILOT_STRICT_TRACE_BASELINE", "1")
            .strip()
            .casefold()
            not in {"0", "false", "no", "off"}
        )
        if step is not None and phase in {"before", "batch_before"}:
            expected_region = self._region_from_event(event)
            # A causal action record is useful only when its before-frame was
            # captured before input dispatch.  Make that invariant the normal
            # runtime contract; an explicit opt-out remains available for
            # latency experiments, but evaluation and ordinary production
            # traces must not silently substitute a possibly stale daemon
            # sample and then call the action verified.
            if strict_trace and expected_region is not None:
                try:
                    # Evaluation-only strict mode pays this small synchronous
                    # cost so the baseline is guaranteed to precede input.
                    frame = capture(expected_region)
                    frame_region = expected_region
                    baseline_capture_source = "sync_pre_action"
                except Exception:
                    frame = None
            elif not strict_trace:
                try:
                    from ..fast.perception_daemon import PERCEPTION_DAEMON

                    latest = PERCEPTION_DAEMON.latest_frame(
                        run_id=str(run_id or ""), max_age_ms=500,
                    )
                    if latest is not None:
                        candidate_frame, candidate_region = latest
                        if expected_region is not None and candidate_region == expected_region:
                            frame, frame_region = candidate_frame, candidate_region
                            baseline_capture_source = "perception_daemon"
                except Exception:
                    frame = None
        elif step is not None and phase in {"after", "batch_after"} and strict_trace:
            expected_region = frame_region or self._region_from_event(event)
            if expected_region is not None:
                try:
                    # Seal the post-action pixels before this event callback
                    # returns. Otherwise the next action can start while the
                    # trace worker is settling, and its pixels can be falsely
                    # attributed to the previous action.
                    time.sleep(_POST_ACTION_CAPTURE_SETTLE_S)
                    frame = capture(expected_region)
                    frame_region = expected_region
                    post_capture_source = "sync_post_action"
                except Exception:
                    frame = None
        if step is not None and phase in {"before", "batch_before"}:
            if frame_region is None:
                frame_region = self._region_from_event(event)
            if frame_region is not None:
                with self._lock:
                    if self._accepting and self._run_id == run_id:
                        self._action_regions[action_key] = frame_region
        queued = self._enqueue({
            "kind": "event",
            "run_id": run_id,
            "record": dict(event),
            "step": step,
            "phase": phase,
            "frame": frame,
            "region": frame_region,
            "require_supplied_frame": phase in {"before", "batch_before"},
            "baseline_capture_source": baseline_capture_source,
            "post_capture_source": post_capture_source,
        })
        if queued:
            with self._lock:
                if self._run_id == run_id:
                    self._accepted_events += 1

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

    @staticmethod
    def _region_from_event(event: dict[str, Any]) -> Region | None:
        envelope = event.get("action_envelope")
        target = envelope.get("target") if isinstance(envelope, dict) else None
        raw = target.get("region") if isinstance(target, dict) else None
        if not isinstance(raw, dict):
            return None
        try:
            region = Region(
                int(raw.get("left")),
                int(raw.get("top")),
                int(raw.get("width")),
                int(raw.get("height")),
            )
        except (TypeError, ValueError):
            return None
        return region if region.width > 0 and region.height > 0 else None

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
                elif item.get("kind") == "pointer_debug":
                    if not self._write_pointer_debug(item):
                        raise OSError("pointer debug screenshot was not written")
            except Exception as exc:
                with self._lock:
                    if self._run_id == item.get("run_id"):
                        self._write_failures += 1
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
            screenshot_region = item.get("region")
            if phase in {"after", "batch_after"} and item.get("frame") is None:
                # The Level-0 daemon's latest frame at action completion can
                # still be the pre-action sample.  Capture asynchronously after
                # a short paint interval so trace evidence represents the
                # resulting frame without slowing the controller hot path.
                time.sleep(_POST_ACTION_CAPTURE_SETTLE_S)
            supplied_frame = item.get("frame")
            shot = None
            if supplied_frame is not None or not bool(item.get("require_supplied_frame")):
                shot = self._save_screenshot(
                    run_id,
                    int(step),
                    phase,
                    image=supplied_frame,
                    region=screenshot_region if isinstance(screenshot_region, Region) else None,
                )
            if shot:
                record["screenshot"] = shot
                if item.get("baseline_capture_source"):
                    record["baseline_capture_source"] = str(
                        item.get("baseline_capture_source")
                    )
                if item.get("post_capture_source"):
                    record["post_capture_source"] = str(
                        item.get("post_capture_source")
                    )
                if isinstance(screenshot_region, Region):
                    record["screenshot_region"] = {
                        "left": int(screenshot_region.left),
                        "top": int(screenshot_region.top),
                        "width": int(screenshot_region.width),
                        "height": int(screenshot_region.height),
                    }
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
                self._write_failures += 1
                logger.warning("trace 写入失败: %s", exc)

    def _write_integrity_locked(self, run_id: str) -> None:
        """Append a final local completeness record after the queue drains."""

        assert self._file is not None
        record = {
            "type": "trace_integrity",
            "ts": time.time(),
            "run_id": run_id,
            "accepted_events": self._accepted_events,
            "dropped_events": self._dropped_events,
            "write_failures": self._write_failures,
            "complete": self._dropped_events == 0 and self._write_failures == 0,
        }
        try:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._file.flush()
        except OSError as exc:
            self._write_failures += 1
            logger.warning("trace 完整性记录写入失败: %s", exc)

    def _save_screenshot(
        self,
        run_id: str | None,
        step: int,
        phase: str = "after",
        *,
        image: Any = None,
        region: Region | None = None,
    ) -> str | None:
        if not run_id:
            return None
        try:
            image = (
                capture(region) if region is not None else capture()
            ) if image is None else image
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

    def record_pointer_debug(
        self,
        *,
        run_id: str,
        decision_id: str,
        action: str,
        frame_image_b64: str,
        frame_id: int,
        frame_region: tuple[int, int, int, int],
        submitted_image_size: tuple[int, int],
        image_coordinates: Mapping[str, Any],
        physical_arguments: Mapping[str, Any],
        cursor_before: tuple[int, int] | None,
    ) -> Mapping[str, Any] | None:
        """Queue a colored overlay of the exact model-authoritative frame.

        This is deliberately a side channel.  Rendering failure cannot delay,
        reject, or replay the physical input that is about to run.
        """

        with self._lock:
            if (
                not self._accepting
                or self._file is None
                or self._run_id != run_id
            ):
                return None
            self._pointer_debug_step += 1
            step = self._pointer_debug_step
        safe_action = (
            re.sub(r"[^a-z0-9_-]+", "_", str(action).casefold()).strip("_")
            or "pointer"
        )
        rel = f"{run_id}/pointer_{step:03d}_{safe_action}.jpg"
        item = {
            "kind": "pointer_debug",
            "run_id": run_id,
            "decision_id": str(decision_id),
            "action": str(action),
            "frame_image_b64": str(frame_image_b64),
            "frame_id": int(frame_id),
            "frame_region": tuple(frame_region),
            "submitted_image_size": tuple(submitted_image_size),
            "image_coordinates": dict(image_coordinates),
            "physical_arguments": dict(physical_arguments),
            "cursor_before": (
                tuple(cursor_before) if cursor_before is not None else None
            ),
            "path": rel,
        }
        if not self._enqueue(item):
            return None
        return {
            "screenshot": rel,
            "coordinate_space": "submitted_image_pixels",
            "cursor_before": (
                list(cursor_before) if cursor_before is not None else None
            ),
            "colors": dict(_POINTER_DEBUG_COLORS),
        }

    @staticmethod
    def _write_pointer_debug(item: Mapping[str, Any]) -> bool:
        raw = base64.b64decode(
            str(item.get("frame_image_b64") or ""),
            validate=True,
        )
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return False
        rendered = _render_pointer_debug(image, item)
        relative = str(item.get("path") or "")
        if not relative:
            return False
        target = TRACES_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(
            ".jpg",
            rendered,
            [cv2.IMWRITE_JPEG_QUALITY, 92],
        )
        if not ok:
            return False
        target.write_bytes(encoded.tobytes())
        BUS.publish(
            "trace_screenshot",
            run_id=str(item.get("run_id") or ""),
            trace_run_id=str(item.get("run_id") or ""),
            screenshot=relative,
            screenshot_kind="pointer_debug",
            action=str(item.get("action") or ""),
            decision_id=str(item.get("decision_id") or ""),
        )
        return True

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


def list_traces() -> list[dict[str, Any]]:
    """Trace runs on disk, newest first, with task + status summary.

    Consumed by the Electron Platform workbench trace browser
    (``/api/traces``); part of the Studio/compat surface.
    """
    TRACES_DIR.mkdir(exist_ok=True)
    out: list[dict[str, Any]] = []
    for path in sorted(TRACES_DIR.glob("*.jsonl"), reverse=True):
        info: dict[str, Any] = {"run_id": path.stem, "task": "", "status": "",
                                "steps": 0, "size_kb": round(path.stat().st_size / 1024, 1)}
        legacy_action_ids: set[str] = set()
        legacy_anonymous = 0
        vnext_action_ids: set[str] = set()
        vnext_anonymous = 0
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    event = json.loads(line)
                    if event.get("type") == "agent_start":
                        info["task"] = event.get("task", "")
                    elif event.get("type") in {"agent_tool", "agent_batch_action"}:
                        replay = event.get("replay")
                        meta = event.get("result_meta")
                        wrapper_parent = bool(
                            event.get("type") == "agent_tool"
                            and isinstance(meta, dict)
                            and meta.get("wrapped_by_fast_controller") is True
                        )
                        if (
                            isinstance(replay, dict)
                            and replay.get("tool") in KNOWN_TOOLS
                            and event.get("tool") != "run_action_batch"
                            and not wrapper_parent
                        ):
                            action_id = str(event.get("action_id") or "")
                            if action_id:
                                legacy_action_ids.add(action_id)
                            else:
                                legacy_anonymous += 1
                    elif (
                        event.get("type") == "vnext_raw_receipt"
                        and event.get("input_completed") is True
                    ):
                        decision_id = str(event.get("decision_id") or "")
                        if decision_id:
                            vnext_action_ids.add(decision_id)
                        else:
                            vnext_anonymous += 1
                    elif event.get("type") == "agent_done":
                        info["status"] = event.get("status", "")
                    elif event.get("type") == "arp_run_done":
                        info["status"] = event.get("status", "")
                        snapshot = event.get("runtime_snapshot")
                        if isinstance(snapshot, dict):
                            info["thread_id"] = str(snapshot.get("thread_id") or "")
                            info["turn_id"] = str(snapshot.get("turn_id") or "")
                    elif event.get("type") == "trace_integrity":
                        info["complete"] = event.get("complete") is True
                        info["dropped_events"] = int(event.get("dropped_events") or 0)
        except (json.JSONDecodeError, OSError):
            pass
        info["steps"] = (
            len(vnext_action_ids) + vnext_anonymous
            if vnext_action_ids or vnext_anonymous
            else len(legacy_action_ids) + legacy_anonymous
        )
        out.append(info)
    return out


def _read_trace_records(
    run_id: str,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = TRACES_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return [], {
            "total_lines": 0,
            "returned_events": 0,
            "malformed_lines": 0,
            "truncated": False,
        }
    checked_limit = None if limit is None else max(0, int(limit))
    events: list[dict[str, Any]] = []
    total_lines = 0
    malformed_lines = 0
    valid_events = 0
    try:
        with path.open("rb") as fh:
            for line in fh:
                total_lines += 1
                try:
                    event = json.loads(line)
                except (
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    RecursionError,
                ):
                    malformed_lines += 1
                    continue
                if not isinstance(event, dict):
                    malformed_lines += 1
                    continue
                valid_events += 1
                if checked_limit is None or len(events) < checked_limit:
                    events.append(event)
    except OSError:
        malformed_lines += 1
    return events, {
        "total_lines": total_lines,
        "returned_events": len(events),
        "malformed_lines": malformed_lines,
        "truncated": checked_limit is not None and valid_events > len(events),
    }


def read_trace(
    run_id: str,
    limit: int | None = None,
    *,
    include_integrity: bool = False,
) -> list[dict[str, Any]]:
    """Read a trace; callers choose full export reads or bounded public reads."""

    events, integrity = _read_trace_records(run_id, limit=limit)
    if include_integrity or integrity["malformed_lines"] or integrity["truncated"]:
        events.append({
            "type": "trace_read_integrity",
            "run_id": run_id,
            "complete": not integrity["malformed_lines"] and not integrity["truncated"],
            **integrity,
        })
    return events


# ------------------------------------------------------------------- export
def _distill_steps(steps: list[dict]) -> list[dict]:
    """Clean volatile metadata without deleting executed actions."""
    distilled: list[dict] = []
    for raw in steps:
        step = dict(raw)
        step.pop("elapsed_ms", None)
        for field in ("text", "element_text"):
            if step.get(field):
                step[field] = clean_selector_text(step[field])
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


def _event_target_window(event: dict[str, Any]) -> dict[str, Any] | None:
    hint = _stable_target_window_hint(
        _target_window_hint_from(event.get("target_window"), "event.target_window")
    )
    if hint:
        return hint
    hint = _target_window_from_args(event.get("args"))
    if hint:
        return hint
    hint = _target_window_from_result_meta(event.get("result_meta"))
    if hint:
        return hint
    snapshot = event.get("runtime_snapshot")
    if isinstance(snapshot, dict):
        context = snapshot.get("execution_context")
        if isinstance(context, dict):
            return _stable_target_window_hint(
                _target_window_hint_from(
                    context.get("target_window"),
                    "runtime_snapshot.execution_context.target_window",
                )
            )
    return None


def _extract_replay_steps(
    events: list[dict[str, Any]],
) -> tuple[list[dict], list[str]]:
    steps: list[dict] = []
    omissions: list[str] = []
    pending_batch_keys: set[str] = set()
    for event in events:
        event_type = str(event.get("type") or "")
        event_tool = str(event.get("tool") or "")
        if event_type not in {"agent_tool", "agent_batch_action"}:
            continue
        if event_type == "agent_tool" and event_tool in {
            "run_action_batch", "execute_subgoal",
        } and not (
            isinstance(event.get("replay"), dict)
            and event["replay"].get("tool") in KNOWN_TOOLS
        ):
            # These are transport/controller wrappers. Their physical children
            # are recorded separately and must be the replay source of truth.
            # A historical execute_subgoal may instead carry one already-
            # flattened leaf replay; keep that explicit executable record.
            pending_batch_keys.clear()
            continue
        replay = event.get("replay")
        if not replay:
            if _legacy_event_requires_replay(event):
                identity = str(
                    event.get("action_id")
                    or event.get("batch_id")
                    or "<anonymous>"
                )
                omissions.append(
                    f"missing_replay_for_action:{event_tool}:{identity}"
                )
            continue
        tool = replay.get("tool") if isinstance(replay, dict) else None
        if tool not in KNOWN_TOOLS:
            if _legacy_event_requires_replay(event):
                omissions.append(f"unsupported_replay_tool:{tool or '<missing>'}")
            continue
        step = dict(replay)
        hint = _event_target_window(event)
        if hint and "target_window" not in step:
            step["target_window"] = hint
        if event.get("type") == "agent_batch_action":
            steps.append(step)
            pending_batch_keys.add(_semantic_step_key(step))
        elif event.get("type") == "agent_tool":
            if event.get("tool") == "run_action_batch":
                pending_batch_keys.clear()
                continue
            result_meta = event.get("result_meta")
            wrapped = bool(
                isinstance(result_meta, dict)
                and result_meta.get("wrapped_by_fast_controller") is True
            )
            if wrapped and _semantic_step_key(step) in pending_batch_keys:
                # The batch child and outer wrapper describe one physical
                # action. This is identity-aware wrapper removal, not semantic
                # deduplication of two independently executed equal actions.
                pending_batch_keys.clear()
                continue
            steps.append(step)
            pending_batch_keys.clear()
    return steps, omissions


_LEGACY_REPLAY_REQUIRED_ACTIONS = frozenset({
    "open_app", "launch", "focus_window",
    "click", "click_element", "double_click",
    "type_text", "type_into_element", "hotkey",
    "scroll", "drag", "draw_strokes", "tray_click",


    "mouse_down", "mouse_up", "move_mouse", "hold_key", "select_color",
    "coordinate_click_test", "run_detached", "write_file", "apply_patch",
})


def _legacy_event_requires_replay(event: dict[str, Any]) -> bool:
    """Identify completed legacy leaf mutations, never reads or wrappers."""

    if event.get("type") not in {"agent_tool", "agent_batch_action"}:
        return False
    tool = str(event.get("tool") or "")
    if tool in {"run_action_batch", "execute_subgoal"}:
        return False
    if event.get("error"):
        return False
    meta = event.get("result_meta")
    if isinstance(meta, dict) and (
        meta.get("mutation_skipped") is True
        or meta.get("action_executed") is False
        or meta.get("native_action_executed") is False
    ):
        return False
    return tool in _LEGACY_REPLAY_REQUIRED_ACTIONS or bool(
        isinstance(meta, dict) and meta.get("mutation_applied") is True
    )


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
        hint = _event_target_window(event)
        if hint:
            return hint
    return None


_JOURNAL_EVENT_FIELDS = (
    "format",
    "schema_version",
    "sequence",
    "previous_event_sha256",
    "event_kind",
    "mission_id",
    "run_id",
    "decision_id",
    "action_id",
    "payload",
    "event_sha256",
)
_JOURNAL_EVENT_KINDS = frozenset({
    "actor_decision",
    "admission",
    "physical_attempt",
    "raw_receipt",
    "controller_transition",
    "frame_artifact",
})
_JOURNAL_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_JOURNAL_SHA256 = re.compile(r"[0-9a-f]{64}")
_JOURNAL_GENESIS = "0" * 64


def _strict_journal_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_journal_constant(_value: str) -> None:
    raise ValueError("non_finite_number")


def _canonical_journal_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
        raise ValueError("non_canonical_json") from None


def _read_vnext_journal(
    run_id: str,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Authenticate the frozen journal wire format without importing vNext."""

    path = TRACES_DIR / "vnext_runtime_evidence" / run_id / "evidence.jsonl"
    try:
        data = path.read_bytes()
    except OSError:
        return [], _JOURNAL_GENESIS, "read_failed"
    if not data:
        return [], _JOURNAL_GENESIS, None
    if not data.endswith(b"\n"):
        return [], _JOURNAL_GENESIS, "missing_trailing_newline"
    lines = data[:-1].split(b"\n")
    if any(not line for line in lines):
        return [], _JOURNAL_GENESIS, "empty_event_line"

    records: list[dict[str, Any]] = []
    previous = _JOURNAL_GENESIS
    mission_id = ""
    try:
        for sequence, line in enumerate(lines):
            try:
                text = line.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise ValueError("invalid_utf8") from None
            try:
                event = json.loads(
                    text,
                    object_pairs_hook=_strict_journal_object,
                    parse_constant=_reject_journal_constant,
                )
            except (ValueError, RecursionError) as exc:
                code = str(exc)
                if code not in {"duplicate_json_key", "non_finite_number"}:
                    code = "malformed_json"
                raise ValueError(code) from None
            if type(event) is not dict:
                raise ValueError("event_not_object")
            if set(event) != set(_JOURNAL_EVENT_FIELDS):
                raise ValueError("event_fields")
            if _canonical_journal_bytes(event) != line:
                raise ValueError("non_canonical_event_bytes")
            if event.get("format") != "runtime-evidence-journal-event/1":
                raise ValueError("event_format")
            if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
                raise ValueError("schema_version")
            if type(event.get("sequence")) is not int or event["sequence"] != sequence:
                raise ValueError("event_sequence")
            if event.get("previous_event_sha256") != previous:
                raise ValueError("hash_chain")
            if (
                type(event.get("event_kind")) is not str
                or event["event_kind"] not in _JOURNAL_EVENT_KINDS
            ):
                raise ValueError("event_kind")
            current_mission = event.get("mission_id")
            current_run = event.get("run_id")
            if (
                type(current_mission) is not str
                or _JOURNAL_IDENTITY.fullmatch(current_mission) is None
                or type(current_run) is not str
                or _JOURNAL_IDENTITY.fullmatch(current_run) is None
            ):
                raise ValueError("event_scope")
            if current_run != run_id:
                raise ValueError("run_scope")
            if mission_id and current_mission != mission_id:
                raise ValueError("mission_scope")
            mission_id = current_mission
            for field in ("decision_id", "action_id"):
                identity = event.get(field)
                if identity is not None and (
                    type(identity) is not str
                    or _JOURNAL_IDENTITY.fullmatch(identity) is None
                ):
                    raise ValueError(f"{field}_identity")
            if type(event.get("payload")) is not dict:
                raise ValueError("payload_not_object")
            digest = event.get("event_sha256")
            if (
                type(digest) is not str
                or _JOURNAL_SHA256.fullmatch(digest) is None
            ):
                raise ValueError("event_digest")
            material = {
                field: event[field]
                for field in _JOURNAL_EVENT_FIELDS[:-1]
            }
            if hashlib.sha256(_canonical_journal_bytes(material)).hexdigest() != digest:
                raise ValueError("event_hash")
            records.append(event)
            previous = digest
    except (ValueError, TypeError, RecursionError) as exc:
        return [], _JOURNAL_GENESIS, str(exc)
    return records, previous, None


def _physical_attempt_to_wps(
    attempt: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one authenticated physical attempt onto ordinary WPS actions."""

    action = str(attempt.get("action") or "")
    raw_arguments = attempt.get("physical_arguments")
    if not isinstance(raw_arguments, Mapping):
        return None
    arguments = dict(raw_arguments)
    if action in {"click", "double_click"}:
        if arguments.get("kind") != "pointer_click":
            return None
        step: dict[str, Any] = {
            "tool": "click",
            "coords": [arguments.get("x"), arguments.get("y")],
        }
        if action == "double_click":
            step["double"] = True
        return step
    if action == "scroll" and arguments.get("kind") == "wheel":
        return {
            "tool": "scroll",
            "coords": [arguments.get("x"), arguments.get("y")],
            "amount": arguments.get("signed_wheel_amount"),
        }
    if action == "drag" and arguments.get("kind") == "pointer_drag":
        if (
            arguments.get("started_held") is True
            or arguments.get("keep_holding") is True
        ):
            return None
        step = {
            "tool": "drag",
            "from": [arguments.get("x1"), arguments.get("y1")],
            "to": [arguments.get("x2"), arguments.get("y2")],
        }
        if isinstance(arguments.get("points"), (list, tuple)):
            step["points"] = [list(point) for point in arguments["points"]]
        return step
    if action == "type_text" and arguments.get("kind") == "type_text":
        return {"tool": "type_text", "text_input": arguments.get("text")}
    if action == "hotkey" and arguments.get("kind") == "hotkey":
        modifiers = arguments.get("modifiers")
        if not isinstance(modifiers, (list, tuple)):
            return None
        return {"tool": "hotkey", "keys": [*modifiers, arguments.get("key")]}
    return None


def _vnext_journal_steps(
    run_id: str,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    trace_receipts: dict[str, list[dict[str, Any]]] = {}
    reasons: list[str] = []
    explicit_vnext = any(
        event.get("runtime_version") == "vnext"
        and event.get("type") in {
            "arp_run_start",
            "arp_run_done",
            "runtime_evidence_sealed",
            "vnext_model_call",
        }
        for event in events
    )
    has_evidence_seal = any(
        event.get("type") == "runtime_evidence_sealed"
        and event.get("runtime_version") == "vnext"
        for event in events
    )
    for event in events:
        if event.get("type") != "vnext_raw_receipt":
            continue
        decision_id = str(event.get("decision_id") or "").strip()
        if not decision_id:
            reasons.append("raw_receipt_decision_id_missing")
            continue
        receipt_run_id = event.get("run_id")
        if receipt_run_id != run_id:
            reasons.append(f"raw_receipt_run_scope_mismatch:{decision_id}")
            continue
        trace_receipts.setdefault(decision_id, []).append(event)

    journal_path = (
        TRACES_DIR / "vnext_runtime_evidence" / run_id / "evidence.jsonl"
    )
    if not trace_receipts and not reasons and not journal_path.is_file():
        if not explicit_vnext:
            return [], [], False
        if has_evidence_seal:
            return [], ["runtime_evidence_missing"], True
        legacy_steps, legacy_reasons = _extract_replay_steps(events)
        if legacy_steps or legacy_reasons:
            reasons.append("vnext_trace_contains_legacy_replay")
        return [], reasons, True

    if not journal_path.is_file():
        if has_evidence_seal or any(
            receipt.get("input_completed") is not False
            for receipt_events in trace_receipts.values()
            for receipt in receipt_events
        ):
            reasons.append("runtime_evidence_missing")
        return [], list(dict.fromkeys(reasons)), True

    journal, journal_head, journal_error = _read_vnext_journal(run_id)
    if journal_error:
        reasons.append(f"runtime_evidence_invalid:{journal_error}")
        return [], list(dict.fromkeys(reasons)), True

    journal_mission_id = str(journal[0].get("mission_id") or "") if journal else ""
    if journal_mission_id:
        scoped_receipts: dict[str, list[dict[str, Any]]] = {}
        for decision_id, receipt_events in trace_receipts.items():
            for receipt in receipt_events:
                receipt_mission_id = receipt.get("mission_id")
                if receipt_mission_id != journal_mission_id:
                    reasons.append(
                        f"raw_receipt_mission_scope_mismatch:{decision_id}"
                    )
                    continue
                scoped_receipts.setdefault(decision_id, []).append(receipt)
        trace_receipts = scoped_receipts

    seal = next((
        event
        for event in reversed(events)
        if event.get("type") == "runtime_evidence_sealed"
        and str(event.get("run_id") or "") == run_id
    ), None)
    if seal is None:
        reasons.append("runtime_evidence_seal_missing")
    elif seal.get("mission_id") != journal_mission_id:
        reasons.append("runtime_evidence_seal_mission_scope_mismatch")
    else:
        if seal.get("journal_head_sha256") != journal_head:
            reasons.append("runtime_evidence_seal_head_mismatch")
        if (
            type(seal.get("journal_event_count")) is not int
            or seal["journal_event_count"] != len(journal)
        ):
            reasons.append("runtime_evidence_seal_count_mismatch")
        if (
            seal.get("report_status") != "complete"
            or type(seal.get("missing_evidence_count")) is not int
            or seal["missing_evidence_count"] != 0
            or type(seal.get("verification_failure_count")) is not int
            or seal["verification_failure_count"] != 0
        ):
            reasons.append("runtime_evidence_report_incomplete")

    attempts: dict[str, list[dict[str, Any]]] = {}
    journal_receipts: dict[str, list[dict[str, Any]]] = {}
    for event in journal:
        decision_id = str(event.get("decision_id") or "")
        if event.get("event_kind") == "physical_attempt":
            attempts.setdefault(decision_id, []).append(event)
        elif event.get("event_kind") == "raw_receipt":
            journal_receipts.setdefault(decision_id, []).append(event)

    ordered_steps: list[tuple[int, int, dict[str, Any]]] = []
    all_decisions = list(dict.fromkeys([
        *trace_receipts,
        *attempts,
        *journal_receipts,
    ]))
    for order, decision_id in enumerate(all_decisions):
        traced = trace_receipts.get(decision_id, [])
        recorded_attempts = attempts.get(decision_id, [])
        recorded_receipts = journal_receipts.get(decision_id, [])
        if len(traced) > 1:
            reasons.append(f"duplicate_trace_raw_receipt:{decision_id}:{len(traced)}")
        if len(recorded_attempts) > 1:
            reasons.append(f"duplicate_physical_attempt:{decision_id}:{len(recorded_attempts)}")
        if len(recorded_receipts) > 1:
            reasons.append(f"duplicate_journal_raw_receipt:{decision_id}:{len(recorded_receipts)}")
        trace_receipt = traced[-1] if traced else None
        attempt_event = recorded_attempts[-1] if recorded_attempts else None
        journal_receipt_event = recorded_receipts[-1] if recorded_receipts else None
        trace_receipt_missing = trace_receipt is None
        if trace_receipt is None:
            if attempt_event is not None and journal_receipt_event is not None:
                reasons.append(f"trace_raw_receipt_missing:{decision_id}")
                trace_receipt = {}
            elif attempt_event is not None:
                reasons.append(f"physical_attempt_without_trace_receipt:{decision_id}")
                continue
            elif journal_receipt_event is not None:
                reasons.append(f"journal_receipt_without_trace_receipt:{decision_id}")
                continue
            else:
                continue

        trace_completed = trace_receipt.get("input_completed")
        if trace_completed is not None and type(trace_completed) is not bool:
            reasons.append(f"trace_raw_receipt_invalid:{decision_id}")
            continue
        if journal_receipt_event is None:
            if trace_completed is not False or attempt_event is not None:
                reasons.append(f"trace_receipt_without_journal_receipt:{decision_id}")
        else:
            receipt_payload = journal_receipt_event.get("payload")
            journal_completed = (
                receipt_payload.get("input_completed")
                if isinstance(receipt_payload, dict)
                else "invalid"
            )
            if journal_completed is not None and type(journal_completed) is not bool:
                reasons.append(f"journal_raw_receipt_invalid:{decision_id}")
                continue
            if trace_receipt_missing:
                trace_completed = journal_completed
            elif journal_completed is not trace_completed:
                reasons.append(f"raw_receipt_completion_mismatch:{decision_id}")
                continue

        if trace_completed is None:
            reasons.append(f"physical_attempt_outcome_uncertain:{decision_id}")
            continue
        if trace_completed is False:
            if attempt_event is not None:
                reasons.append(f"physical_attempt_not_completed:{decision_id}")
            continue
        if attempt_event is None:
            reasons.append(f"completed_receipt_without_physical_attempt:{decision_id}")
            continue

        attempt = attempt_event.get("payload")
        if not isinstance(attempt, dict) or (
            attempt.get("format") != "physical-attempt-record/1"
            or (
                journal_mission_id
                and attempt.get("mission_id") != journal_mission_id
            )
            or attempt.get("run_id") != run_id
            or attempt.get("decision_id") != decision_id
            or attempt.get("action_id") != decision_id
        ):
            reasons.append(f"physical_attempt_invalid:{decision_id}")
            continue
        attempt_action = str(attempt.get("action") or "")
        controller_action = str(trace_receipt.get("controller_action") or "")
        if controller_action and controller_action != attempt_action:
            reasons.append(f"controller_action_mismatch:{decision_id}")
            continue
        expected_raw_action = (
            "click" if attempt_action == "double_click" else attempt_action
        )
        receipt_payload = (
            journal_receipt_event.get("payload")
            if journal_receipt_event is not None
            else None
        )
        journal_action = str(
            receipt_payload.get("action") or ""
            if isinstance(receipt_payload, dict)
            else ""
        )
        trace_raw_action = str(trace_receipt.get("raw_action") or "")
        if (
            journal_action
            and journal_action != expected_raw_action
        ) or (
            trace_raw_action
            and journal_action
            and trace_raw_action != journal_action
        ):
            reasons.append(f"raw_receipt_action_mismatch:{decision_id}")
            continue
        replay = _physical_attempt_to_wps(attempt)
        if replay is None:
            reasons.append(
                f"physical_action_not_replayable:"
                f"{attempt.get('action') or '<missing>'}:{decision_id}"
            )
            continue
        step = dict(replay)
        trace_target = _event_target_window(trace_receipt) or {}
        identity = attempt.get("target_identity")
        executable = str(
            identity.get("executable_identity") or ""
            if isinstance(identity, dict)
            else ""
        ).strip()
        process = executable.replace("\\", "/").rsplit("/", 1)[-1]
        # The authenticated process identity remains the replay authority.
        # Retain the trace title only when the trace names that same process;
        # this keeps a useful same-process window hint without allowing a
        # mismatched trace target to replace the sealed executable identity.
        target = {"process": process} if process else {}
        trace_process = str(trace_target.get("process") or "").strip()
        trace_title = str(trace_target.get("title") or "").strip()
        if (
            process
            and trace_process
            and process.casefold() != trace_process.casefold()
        ):
            reasons.append(f"physical_action_target_mismatch:{decision_id}")
        elif process and trace_process and trace_title:
            target["title"] = trace_title
        if target:
            step["target_window"] = target
        else:
            reasons.append(f"physical_action_target_missing:{decision_id}")
        timestamp = attempt.get("timestamp_ns")
        ordered_steps.append((
            timestamp if type(timestamp) is int else 0,
            order,
            step,
        ))
    ordered_steps.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ordered_steps], list(dict.fromkeys(reasons)), True


def _trace_export_manifest(
    run_id: str,
    events: list[dict[str, Any]],
    read_integrity: dict[str, Any],
    extra_reasons: list[str],
) -> dict[str, Any]:
    reasons = list(extra_reasons)
    integrity_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "trace_integrity"
    ]
    if not integrity_indexes:
        reasons.append("trace_integrity_missing")
        dropped_events = None
        write_failures = None
    else:
        dropped_events = 0
        write_failures = 0
        prior_integrity_index = -1
        for integrity_index in integrity_indexes:
            integrity = events[integrity_index]
            segment_event_count = sum(
                event.get("type") != "trace_integrity"
                for event in events[prior_integrity_index + 1:integrity_index]
            )
            accepted_events = integrity.get("accepted_events")
            if (
                not isinstance(accepted_events, int)
                or isinstance(accepted_events, bool)
                or accepted_events < 0
            ):
                reasons.append(
                    "trace_integrity_accepted_events_missing_or_invalid"
                )
            elif accepted_events != segment_event_count:
                reasons.append(
                    "trace_integrity_event_count_mismatch:"
                    f"{accepted_events}:{segment_event_count}"
                )
            if integrity.get("complete") is not True:
                reasons.append("trace_integrity_failed")
            for field in ("dropped_events", "write_failures"):
                value = integrity.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    reasons.append(f"trace_integrity_{field}_missing_or_invalid")
                    value = 0
                if field == "dropped_events":
                    dropped_events += value
                else:
                    write_failures += value
            prior_integrity_index = integrity_index
        if dropped_events:
            reasons.append(f"trace_queue_dropped:{dropped_events}")
        if write_failures:
            reasons.append(f"trace_write_failures:{write_failures}")
        trailing_event_count = sum(
            event.get("type") != "trace_integrity"
            for event in events[integrity_indexes[-1] + 1:]
        )
        if trailing_event_count:
            reasons.append(f"trace_events_after_integrity:{trailing_event_count}")
    malformed = int(read_integrity.get("malformed_lines") or 0)
    if malformed:
        reasons.append(f"trace_malformed_lines:{malformed}")
    if read_integrity.get("truncated") is True:
        reasons.append("trace_read_truncated")
    reasons = list(dict.fromkeys(reasons))
    return {
        "format": "agent-replay-export/1",
        "scope": "run",
        "coverage": "ordinary_arp_actions",
        "complete": not reasons,
        "partial": bool(reasons),
        "reasons": reasons,
        "source_runs": [run_id],
        "trace_event_count": len(events),
        "dropped_events": dropped_events,
        "write_failures": write_failures,
    }


def _trace_task(events: list[dict[str, Any]], run_id: str) -> str:
    for event in events:
        if event.get("type") == "agent_start" and event.get("task"):
            return str(event["task"])
    for event in events:
        if event.get("type") != "arp_run_start":
            continue
        admission = event.get("goal_admission")
        if isinstance(admission, Mapping) and admission.get("goal"):
            return str(admission["goal"])
    for event in reversed(events):
        snapshot = event.get("runtime_snapshot")
        if not isinstance(snapshot, dict):
            continue
        admission = snapshot.get("goal_admission")
        if isinstance(admission, dict) and admission.get("goal"):
            return str(admission["goal"])
        if snapshot.get("graph_name"):
            return str(snapshot["graph_name"])
    return run_id


def _build_run_segment(
    run_id: str,
    *,
    records: tuple[list[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    events, read_integrity = (
        records if records is not None else _read_trace_records(run_id, limit=None)
    )
    if not events:
        raise FileNotFoundError(f"trace 不存在: {run_id}")
    legacy_steps, omissions = _extract_replay_steps(events)
    vnext_steps, vnext_reasons, has_vnext_trace = _vnext_journal_steps(run_id, events)
    steps = vnext_steps if has_vnext_trace else legacy_steps
    reasons = [*omissions, *vnext_reasons]
    default_target = (
        next((
            _stable_target_window_hint(step.get("target_window"))
            for step in steps
            if isinstance(step.get("target_window"), dict)
            and _stable_target_window_hint(step.get("target_window"))
        ), None)
        if has_vnext_trace
        else _extract_target_window(events)
    )
    if default_target and not has_vnext_trace:
        for step in steps:
            step.setdefault("target_window", default_target)
    manifest = _trace_export_manifest(run_id, events, read_integrity, reasons)
    return _trace_task(events, run_id), _distill_steps(steps), manifest, default_target


def build_script(run_id: str, name: str | None = None) -> dict[str, Any]:
    """Distill a trace into a replayable .wps.json document."""
    task, steps, manifest, target_window = _build_run_segment(run_id)
    if not steps:
        raise ValueError("trace 中没有可重放的动作步骤")
    script = {
        "format": "wps/2",
        "name": name or task[:60] or run_id,
        "task": task,
        "source": "agent",
        "source_run": run_id,
        "created": int(time.time()),
        "steps": steps,
        "export_manifest": manifest,
    }
    if target_window:
        script["target_window"] = target_window
    err = validate_script(script)
    if err:
        raise ValueError(f"exported .wps.json is not replayable: {err}")
    return script


def _trace_thread_ids(
    events: list[dict[str, Any]],
) -> tuple[str, str, tuple[str, ...]]:
    """Return one trace owner, or every conflicting owner without guessing."""

    identities: list[str] = []
    turns: dict[str, str] = {}
    for event in events:
        candidates: list[Mapping[str, Any]] = []
        snapshot = event.get("runtime_snapshot")
        if isinstance(snapshot, Mapping):
            candidates.append(snapshot)
        candidates.append(event)
        for candidate in candidates:
            thread_id = str(candidate.get("thread_id") or "").strip()
            if not thread_id:
                continue
            if thread_id not in identities:
                identities.append(thread_id)
            turn_id = str(candidate.get("turn_id") or "").strip()
            if turn_id:
                turns[thread_id] = turn_id
    if len(identities) > 1:
        return "", "", tuple(identities)
    if not identities:
        return "", "", ()
    thread_id = identities[0]
    return thread_id, turns.get(thread_id, ""), ()


def build_thread_script(
    thread_id: str,
    name: str | None = None,
    *,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Concatenate every replayable run in one Agent conversation."""

    checked_thread = str(thread_id or "").strip()
    if not checked_thread:
        raise ValueError("thread_id is required")
    segments: list[tuple[float, str, str, list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]] = []
    explicit_runs = run_ids is not None
    requested_runs = list(dict.fromkeys(run_ids or []))
    requested_set = set(requested_runs)
    existing_runs = {
        path.stem: path
        for path in TRACES_DIR.glob("*.jsonl")
    }
    # The renderer intentionally keeps only bounded conversation state for
    # display/model context. Trace files are the durable action ledger, so a
    # thread export must discover older matching runs as well. Explicit run
    # ids remain completeness assertions; they are not an exclusion list.
    run_paths = list(existing_runs.items())
    reasons: list[str] = []
    if explicit_runs:
        reasons.extend(
            f"trace_missing:{run_id}"
            for run_id in requested_runs
            if run_id not in existing_runs
        )
    for requested_run_id, path in run_paths:
        events, _read_integrity = _read_trace_records(path.stem, limit=None)
        found_thread, _turn_id, mixed_threads = _trace_thread_ids(events)
        if mixed_threads:
            if (
                (explicit_runs and requested_run_id in requested_set)
                or checked_thread in mixed_threads
            ):
                reasons.append(
                    "trace_thread_id_mixed:"
                    f"{requested_run_id}:{'|'.join(mixed_threads)}"
                )
            continue
        if not found_thread:
            if explicit_runs and requested_run_id in requested_set:
                reasons.append(f"trace_thread_id_missing:{requested_run_id}")
            else:
                unowned_manifest = _trace_export_manifest(
                    requested_run_id,
                    events,
                    _read_integrity,
                    [],
                )
                uncertain_reasons = [
                    reason
                    for reason in unowned_manifest["reasons"]
                    if reason != "trace_integrity_missing"
                ]
                if uncertain_reasons:
                    reasons.append(
                        f"trace_thread_identity_unverifiable:{requested_run_id}"
                    )
            continue
        if found_thread != checked_thread:
            if explicit_runs and requested_run_id in requested_set:
                reasons.append(
                    "trace_thread_id_mismatch:"
                    f"{requested_run_id}:{found_thread}"
                )
            continue
        timestamp = next(
            (
                float(event.get("ts"))
                for event in events
                if isinstance(event.get("ts"), (int, float))
            ),
            path.stat().st_mtime,
        )
        task, steps, manifest, target = _build_run_segment(
            path.stem,
            records=(events, _read_integrity),
        )
        segments.append((timestamp, path.stem, task, steps, manifest, target))
    if not segments:
        raise FileNotFoundError(f"thread 没有可导出的 trace: {checked_thread}")
    segments.sort(key=lambda item: (item[0], item[1]))
    steps = [step for segment in segments for step in segment[3]]
    if not steps:
        raise ValueError("thread 中没有可重放的普通 ARP 动作")
    reasons.extend(
        f"{run_id}:{reason}"
        for _ts, run_id, _task, _steps, manifest, _target in segments
        for reason in manifest.get("reasons", [])
    )
    manifest = {
        "format": "agent-replay-export/1",
        "scope": "thread",
        "coverage": "ordinary_arp_actions",
        "thread_id": checked_thread,
        "complete": not reasons,
        "partial": bool(reasons),
        "reasons": reasons,
        "source_runs": [segment[1] for segment in segments],
        "run_count": len(segments),
    }
    task = " / ".join(segment[2] for segment in segments if segment[2])
    script: dict[str, Any] = {
        "format": "wps/2",
        "name": name or task[:60] or checked_thread,
        "task": task,
        "source": "agent-thread",
        "source_run": checked_thread,
        "source_runs": manifest["source_runs"],
        "created": int(time.time()),
        "steps": _distill_steps(steps),
        "export_manifest": manifest,
    }
    first_target = next((segment[5] for segment in segments if segment[5]), None)
    if first_target:
        script["target_window"] = first_target
    err = validate_script(script)
    if err:
        raise ValueError(f"exported thread .wps.json is not replayable: {err}")
    return script


_WINDOWS_RESERVED_LEAF_STEMS = {
    "con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    "com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³",
}


def windows_safe_leaf(value: str, *, what: str = "export name") -> str:
    """Validate one Windows filename component without rewriting user text."""

    if (
        type(value) is not str
        or not 1 <= len(value) <= 160
        or value in {".", ".."}
        or value[-1] in {".", " "}
        or any(
            character in '/\\:\0*?"<>|'
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        )
        or (
            value.split(".", 1)[0].rstrip(" .").casefold()
            in _WINDOWS_RESERVED_LEAF_STEMS
        )
    ):
        raise ValueError(
            f"{what} must be one Windows-safe filename component: no paths, "
            "reserved device names, invalid characters, or trailing dots/spaces"
        )
    return value


def ensure_script_name_available(
    value: str,
    *,
    directory: Path,
    what: str = "script name",
) -> str:
    """Reject a Windows-safe name that would replace an existing script."""

    checked = windows_safe_leaf(value, what=what)
    if not directory.is_dir():
        return checked
    existing = {entry.name.casefold(): entry.name for entry in directory.iterdir()}
    conflict = next((
        existing.get(f"{checked}{suffix}".casefold())
        for suffix in (".wps.json", ".automation.json")
        if f"{checked}{suffix}".casefold() in existing
    ), None)
    if conflict:
        raise ValueError(
            f'{what} "{checked}" conflicts with existing script "{conflict}"; '
            "choose a different name to keep the existing recording"
        )
    return checked


def _export_names(name: str | None, fallback: str) -> tuple[str | None, str]:
    checked_name = windows_safe_leaf(name, what="export name") if name is not None else None
    return checked_name, checked_name or windows_safe_leaf(fallback, what="export basename")


def _unique_export_basename(value: str, *, directory: Path) -> str:
    """Choose one basename free for both WPS and canonical IR siblings."""

    checked = windows_safe_leaf(value, what="export basename")
    existing = (
        {entry.name.casefold() for entry in directory.iterdir()}
        if directory.is_dir()
        else set()
    )
    candidate = checked
    sequence = 2
    while any(
        f"{candidate}{suffix}".casefold() in existing
        for suffix in (".wps.json", ".automation.json")
    ):
        suffix = f"-{sequence}"
        if len(checked) + len(suffix) > 160:
            raise ValueError(
                "export basename conflicts with an existing script and is too "
                "long to add a unique suffix"
            )
        candidate = f"{checked}{suffix}"
        sequence += 1
    return candidate


def _write_export_pair(
    script: dict[str, Any],
    *,
    name: str | None,
    basename: str,
) -> Path:
    """Exclusively create one WPS/IR pair without replacing either sibling."""

    SCRIPTS_DIR.mkdir(exist_ok=True)
    unique_basename = _unique_export_basename(basename, directory=SCRIPTS_DIR)
    path = SCRIPTS_DIR / f"{unique_basename}.wps.json"
    payload = json.dumps(script, ensure_ascii=False, indent=1)
    with path.open("x", encoding="utf-8") as fh:
        fh.write(payload)
    try:
        write_automation_ir(
            script,
            name=name,
            basename=unique_basename,
        )
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def export_script(run_id: str, name: str | None = None) -> Path:
    """Write the legacy WPS adapter and a canonical Automation IR sibling."""
    checked_name, basename = _export_names(name, run_id)
    script = build_script(run_id, name=checked_name)
    path = _write_export_pair(script, name=checked_name, basename=basename)
    BUS.publish("trace", state="exported", run_id=run_id, script=str(path))
    return path


def export_thread_script(
    thread_id: str,
    name: str | None = None,
    *,
    run_ids: list[str] | None = None,
) -> Path:
    """Write one editable WPS/Automation IR pair for a whole Agent thread."""

    checked_name, basename = _export_names(name, f"thread-{thread_id}")
    script = build_thread_script(thread_id, name=checked_name, run_ids=run_ids)
    path = _write_export_pair(script, name=checked_name, basename=basename)
    BUS.publish(
        "trace",
        state="thread_exported",
        thread_id=thread_id,
        script=str(path),
    )
    return path


def write_automation_ir(
    script: dict[str, Any],
    *,
    name: str | None,
    basename: str,
    directory: Path | None = None,
) -> Path:
    checked_name = windows_safe_leaf(name, what="export name") if name is not None else None
    checked_basename = windows_safe_leaf(basename, what="export basename")
    ir = automation_ir_from_wps(script, name=checked_name)
    err = validate_automation_ir(ir)
    if err:
        raise ValueError(f"exported Automation IR is invalid: {err}")
    root = directory or SCRIPTS_DIR
    root.mkdir(exist_ok=True)
    path = root / f"{checked_basename}.automation.json"
    payload = json.dumps(ir, ensure_ascii=False, indent=1)
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(payload)
    except FileExistsError as exc:
        raise FileExistsError(
            f'script "{path.name}" already exists; choose a different macro name '
            "to keep the existing recording"
        ) from exc
    return path


def export_automation_ir(run_id: str, name: str | None = None) -> Path:
    """Distill a trace into Automation IR v1 for skill/Flow/replay handoff."""
    checked_name, basename = _export_names(name, run_id)
    script = build_script(run_id, name=checked_name)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    basename = _unique_export_basename(basename, directory=SCRIPTS_DIR)
    path = write_automation_ir(
        script, name=checked_name, basename=basename,
    )
    BUS.publish("trace", state="exported_ir", run_id=run_id, ir=str(path))
    return path


def export_thread_automation_ir(
    thread_id: str,
    name: str | None = None,
    *,
    run_ids: list[str] | None = None,
) -> Path:
    checked_name, basename = _export_names(name, f"thread-{thread_id}")
    script = build_thread_script(thread_id, name=checked_name, run_ids=run_ids)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    basename = _unique_export_basename(basename, directory=SCRIPTS_DIR)
    path = write_automation_ir(
        script, name=checked_name, basename=basename,
    )
    BUS.publish(
        "trace",
        state="thread_exported_ir",
        thread_id=thread_id,
        ir=str(path),
    )
    return path


def list_scripts() -> list[dict[str, Any]]:
    SCRIPTS_DIR.mkdir(exist_ok=True)
    out = []
    canonical_bases: set[str] = set()

    def trust(file: str) -> dict[str, Any]:
        try:
            from ..memory import experience

            return experience.script_trust(file)
        except Exception:
            return {
                "trust": "draft",
                "memory_id": None,
                "memory_status": None,
                "verification_state": "",
                "verification_count": 0,
                "script_sha256": "",
            }

    for path in sorted(SCRIPTS_DIR.glob("*.automation.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            error = validate_automation_ir(data)
            if not error:
                canonical_bases.add(path.name.removesuffix(".automation.json"))
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            out.append({
                "file": path.name,
                "name": data.get("name", path.stem),
                "task": source.get("task", ""),
                "steps": len(data.get("steps", [])),
                "format": "automation-ir/1",
                "valid": not bool(error),
                "validation_error": error or "",
                **trust(path.name),
            })
        except (json.JSONDecodeError, OSError) as exc:
            out.append({
                "file": path.name,
                "name": path.stem,
                "task": "",
                "steps": 0,
                "format": "automation-ir/1",
                "valid": False,
                "validation_error": str(exc),
                **trust(path.name),
            })
    for path in sorted(SCRIPTS_DIR.glob("*.wps.json"), reverse=True):
        if path.name.removesuffix(".wps.json") in canonical_bases:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            error = validate_script(data)
            out.append({
                "file": path.name, "name": data.get("name", path.stem),
                "task": data.get("task", ""), "steps": len(data.get("steps", [])),
                "format": data.get("format", "wps/2"),
                "valid": not bool(error),
                "validation_error": error or "",
                **trust(path.name),
            })
        except (json.JSONDecodeError, OSError) as exc:
            out.append({
                "file": path.name,
                "name": path.stem,
                "task": "",
                "steps": 0,
                "format": "wps/2",
                "valid": False,
                "validation_error": str(exc),
                **trust(path.name),
            })
    return out


def evidence_expectation(requirement: Any) -> dict[str, Any] | None:
    """Translate one persisted evidence predicate into a verifier condition."""
    if not isinstance(requirement, dict):
        return None
    direct = {
        key: value for key, value in requirement.items()
        if key in verify.SUPPORTED_CONDITIONS
    }
    if direct:
        if "timeout_s" in requirement:
            direct["timeout_s"] = requirement["timeout_s"]
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


def validate_evidence_requirements(requirements: Any) -> str | None:
    if not isinstance(requirements, list) or not requirements:
        return "at least one structured evidence requirement is required"
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            return f"evidence requirement {index + 1} must be an object"
        predicate = str(requirement.get("predicate") or "").strip()
        if predicate:
            unknown = set(requirement) - {"predicate", "value"}
            if unknown:
                return f"evidence requirement {index + 1} has unsupported fields"
        else:
            unknown = set(requirement) - set(verify.SUPPORTED_CONDITIONS) - {"timeout_s"}
            if unknown:
                return f"evidence requirement {index + 1} has unsupported fields"
            condition_values = [
                value for key, value in requirement.items()
                if key in verify.SUPPORTED_CONDITIONS
            ]
            if not condition_values or any(value in (None, "") for value in condition_values):
                return f"evidence requirement {index + 1} has no checkable value"
        if "timeout_s" in requirement:
            try:
                timeout_s = float(requirement["timeout_s"])
            except (TypeError, ValueError):
                return f"evidence requirement {index + 1} has invalid timeout_s"
            if timeout_s <= 0:
                return f"evidence requirement {index + 1} has invalid timeout_s"
        if evidence_expectation(requirement) is None:
            return f"evidence requirement {index + 1} is unsupported"
    return None


# ---------------------------------------------------------------- validation
# The .wps.json structural validator lives in the pure ``wps_validate``
# module so the vNext product line can validate REPLAY_STEP graphs without
# importing this module (and its legacy perception/verify stack).  Re-export
# for existing consumers (server.py, automation_ir.py, adapters.py).
from .wps_validate import (
    CTRL_TOOLS,
    EXT_TOOLS,
    KNOWN_TOOLS,
    LEAF_TOOLS,
    validate_script,
    _has_click_target,
    _is_number,
    _is_point,
    _nonempty_str,
    _target_window_hint,
    _target_window_hint_from,
    _target_window_spec_present,
    _valid_strokes,
    _validate_leaf_step,
    _validate_steps,
)


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
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            return False
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
        # Lazy import: the visual-servo tracker belongs to the legacy fast
        # package and must not load at service startup.
        from ..fast.visual_servo import ServoConfig, track_visual_target

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
        def guarded_move(x: int, y: int) -> None:
            move_fn = executor.move_mouse
            try:
                parameters = inspect.signature(move_fn).parameters.values()
                accepts_hwnd = any(
                    parameter.name == "hwnd"
                    or parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                accepts_hwnd = True
            move_result = (
                move_fn(int(x), int(y), hwnd=self.hwnd)
                if accepts_hwnd
                else move_fn(int(x), int(y))
            )
            if isinstance(move_result, dict) and move_result.get("ok") is not True:
                detail = move_result
                raise RuntimeError(json.dumps(detail, ensure_ascii=False, default=str))

        try:
            guarded_move(int(arm_point[0]), int(arm_point[1]))
            time.sleep(0.03)
            tracked, servo_trace = track_visual_target(
                initial,
                [label for label in labels if label],
                capture_fn=capture,
                move_fn=guarded_move,
                ocr_fn=ocr.recognize,
                config=ServoConfig.from_action({
                    **step,
                    "servo_strict": True,
                    "servo_always_ocr": True,
                }),
            )
        except RuntimeError as exc:
            return None, {
                "confirmed": False,
                "blocked": True,
                "cooperative_input": bool(CONFIG.get(
                    "controller", "multi_input_cooperation", default=False,
                )),
                "lost_reason": "cursor_move_blocked",
                "error": str(exc),
                "arm_point": {"x": int(arm_point[0]), "y": int(arm_point[1])},
            }
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

    @staticmethod
    def _target_hint_signature(hint: dict[str, Any]) -> str:
        return json.dumps(
            {key: hint[key] for key in ("title", "process", "hwnd") if key in hint},
            ensure_ascii=False,
            sort_keys=True,
        )

    def _window_process_name(self, win: dict) -> str:
        pid = win.get("pid")
        proc_fn = getattr(win32, "_process_name", None)
        if not callable(proc_fn) or not pid:
            return ""
        try:
            return str(proc_fn(int(pid)) or "")
        except Exception:
            return ""

    def _window_matches_target_process(self, win: dict, hint: dict[str, Any]) -> bool:
        process = str(hint.get("process") or "").strip().casefold()
        if not process:
            return False
        actual = self._window_process_name(win).casefold()
        if not actual:
            return False
        process_key = process.removesuffix(".exe")
        actual_key = actual.removesuffix(".exe")
        return process in actual or process_key in actual_key

    def _window_matches_target_hint(self, win: dict, hint: dict[str, Any]) -> bool:
        title = str(hint.get("title") or "").strip()
        if title and not win32.title_contains(str(win.get("title") or ""), title):
            return False

        process = str(hint.get("process") or "").strip()
        if process and not self._window_matches_target_process(win, hint):
            return False
        return True

    def _find_target_window(self, hint: dict[str, Any]) -> dict | None:
        try:
            signature = self._target_hint_signature(hint)
            same_bound_target = bool(
                self.hwnd is not None and self._target_signature == signature
            )
            title = str(hint.get("title") or "").strip()
            process = str(hint.get("process") or "").strip()
            hwnd = hint.get("hwnd")
            if title and not process and not hwnd and not same_bound_target:
                return win32.find_window(title)

            windows = win32.list_windows()
            if hwnd:
                for win in windows:
                    if int(win.get("hwnd") or 0) != int(hwnd):
                        continue
                    if self._window_matches_target_hint(win, hint):
                        return win
                    if (
                        same_bound_target
                        and int(self.hwnd or 0) == int(hwnd)
                        and process
                        and self._window_matches_target_process(win, hint)
                    ):
                        return win
                    break
                return None

            if same_bound_target:
                for win in windows:
                    if int(win.get("hwnd") or 0) != int(self.hwnd or 0):
                        continue
                    if self._window_matches_target_hint(win, hint):
                        return win
                    # Document titles may change after an action; keep the live
                    # bound HWND only when its recorded process still matches.
                    if process and self._window_matches_target_process(win, hint):
                        return win
                    break

            if title and not process:
                return win32.find_window(title)

            for win in windows:
                if self._window_matches_target_hint(win, hint):
                    return win
        except Exception as exc:
            logger.warning("replay target-window preflight failed: %s", exc)
        return None

    def _bind_target_window(self, hint: dict[str, Any] | None, *, focus: bool = False) -> dict[str, Any]:
        if not hint:
            return {"ok": False, "required": True, "error": "invalid target_window"}
        signature = self._target_hint_signature(hint)
        previous_hwnd = self.hwnd
        previous_signature = self._target_signature
        found = self._find_target_window(hint)
        if not found:
            error = f"target window not found: {self._target_hint_label(hint)}"
            return {"ok": False, "required": True, "error": error, "hint": hint}
        self.hwnd = int(found["hwnd"])
        self._target_signature = signature
        if focus:
            try:
                focused = executor.focus_window(int(self.hwnd))
            except Exception as exc:
                focused = {"ok": False, "error": str(exc)}
            if not isinstance(focused, dict) or focused.get("ok") is not True:
                detail = focused if isinstance(focused, dict) else {}
                error = str(detail.get("error") or "target window focus failed")
                return {"ok": False, "required": True, "error": error, "hint": hint}
        return {
            "ok": True,
            "required": bool(
                previous_hwnd != self.hwnd or previous_signature != signature
            ),
            "hwnd": self.hwnd,
            "title": found.get("title"),
            "hint": hint,
        }

    def _preflight_target_window(self, script: dict[str, Any], *, focus: bool = False) -> dict[str, Any]:
        hint = _target_window_hint(script)
        if not hint:
            return {"ok": True, "required": False}
        result = self._bind_target_window(hint, focus=focus)
        if not result.get("ok", False):
            self._emit_step("preflight", "preflight.target_window", False, result)
        return result

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

    def _preflight(
        self, script: dict[str, Any], *, focus_target: bool = False,
    ) -> dict[str, Any]:
        target = self._preflight_target_window(script, focus=focus_target)
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
        return evidence_expectation(requirement)

    def _postflight_evidence(self, script: dict[str, Any]) -> dict[str, Any]:
        if "evidence" not in script:
            return {"ok": True, "required": False, "checks": []}
        evidence = script.get("evidence")
        required = evidence.get("required", []) if isinstance(evidence, dict) else evidence
        if not isinstance(required, list):
            error = "postflight evidence required must be an array"
            result = {"ok": False, "required": True, "error": error, "checks": []}
            self._emit_step("postflight", "postflight.evidence", False, result)
            return result
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

    def verify_postflight(self, evidence: Any) -> dict[str, Any]:
        """Run the canonical WPS postflight contract for an ARP replay segment."""
        return self._postflight_evidence({"evidence": evidence})

    def verify_preflight(
        self,
        *,
        target_window: Any = None,
        conditions: Any = None,
        safety: Any = None,
    ) -> dict[str, Any]:
        """Run the canonical WPS preflight contract for an ARP replay segment."""
        script: dict[str, Any] = {"format": "wps/2", "steps": []}
        if target_window not in (None, "", {}):
            script["target_window"] = target_window
        if conditions is not None:
            script["conditions"] = conditions
        if safety is not None:
            script["safety"] = safety
        return self._preflight(script)

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
                    if CONFIG.get(
                        "controller", "multi_input_cooperation", default=False,
                    ):
                        result.update({
                            "ok": False,
                            "verified": False,
                            "blocked": True,
                            "cooperative_input": True,
                            "needs_target_window": True,
                            "error": (
                                "replay launch did not produce title/window evidence; "
                                "the foreground window was not adopted in cooperative mode"
                            ),
                        })
                        result.pop("target_hwnd", None)
                    else:
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
                    click_result = executor.click(point[0], point[1], hwnd=self.hwnd)
                    if (
                        not isinstance(click_result, dict)
                        or click_result.get("ok") is not True
                        or click_result.get("blocked") is True
                    ):
                        return (
                            click_result
                            if isinstance(click_result, dict)
                            else {"ok": False, "blocked": True, "error": "pre-click failed"}
                        )
                    time.sleep(0.15)
                return executor.type_text(
                    step.get("text_input", ""), hwnd=self.hwnd,
                )

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
                "hotkey",
                lambda: executor.press_keys(
                    _normalize_hotkeys(step.get("keys", [])),
                    hwnd=self.hwnd,
                ),
                expect=expect, hwnd=self.hwnd, retryable=False,
                stop_event=self.stop_event)
        if tool == "scroll":
            coords = step.get("coords", [0, 0])
            return executor.scroll(
                coords[0], coords[1], int(step.get("amount", 0)),
                hwnd=self.hwnd,
            )
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
        preflight = self._preflight(script, focus_target=True)
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
        if target_hint:
            preflight = self._preflight_target_window(script, focus=True)
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

            if "target_window" in step:
                hint = _target_window_hint_from(step.get("target_window"), f"{idx}.target_window")
                binding = self._bind_target_window(hint, focus=True)
                if not binding.get("ok", False):
                    self._emit_step(idx, tool, False, binding)
                    return False

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
                read = _clipboard_get()
                if not read.get("ok"):
                    return read
                value = str(read.get("text") or "")
            else:
                value = str(step.get("value", ""))
            self.vars[name] = value
            return {"ok": True, "value": value}

        if tool == "ext.clipboard_set":
            return _clipboard_set(str(step.get("text", "")))

        if tool == "ext.clipboard_get":
            into = str(step.get("into", "")).strip()
            read = _clipboard_get()
            if not read.get("ok"):
                return read
            text = str(read.get("text") or "")
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
def _clipboard_get(
    *,
    attempts: int = 3,
    retry_delay_s: float = 0.02,
) -> dict[str, Any]:
    """Read Unicode text, distinguishing an empty clipboard from I/O failure."""

    try:
        import win32clipboard
    except Exception as exc:
        logger.warning("剪贴板读取不可用: %s", exc)
        return {"ok": False, "error": f"clipboard unavailable: {exc}"}

    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        opened = False
        succeeded = False
        text = ""
        close_error: Exception | None = None
        try:
            win32clipboard.OpenClipboard()
            opened = True
            if win32clipboard.IsClipboardFormatAvailable(
                win32clipboard.CF_UNICODETEXT
            ):
                data = win32clipboard.GetClipboardData(
                    win32clipboard.CF_UNICODETEXT
                )
                if data is not None and not isinstance(data, str):
                    raise TypeError("Unicode clipboard data is not text")
                text = data or ""
            succeeded = True
        except Exception as exc:
            last_error = exc
        finally:
            if opened:
                try:
                    win32clipboard.CloseClipboard()
                except Exception as exc:
                    close_error = exc
        if succeeded and close_error is None:
            return {"ok": True, "text": text}
        if close_error is not None:
            last_error = close_error
        if attempt + 1 < max(1, int(attempts)):
            time.sleep(max(0.0, float(retry_delay_s)))
    logger.warning("剪贴板读取失败: %s", last_error)
    return {"ok": False, "error": f"clipboard read failed: {last_error}"}


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
