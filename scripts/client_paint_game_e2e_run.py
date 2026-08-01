#!/usr/bin/env python3
"""Run one native Microsoft Paint tic-tac-toe turn through the visible client.

The board is a harness-owned precondition in an already-visible Paint window.
Only the unchanged user goal and the GUI-required selector reach the Agent.
Board geometry and the expected legal cell stay private to this independent
pixel oracle.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
WINPILOT_HOME = WORKSPACE / "python" / "winpilot_home"
for import_root in (WINPILOT_HOME, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from winpilot.utils.dpi import ensure_process_dpi_awareness  # noqa: E402

# UI Automation otherwise establishes only Per-Monitor-v1 awareness while it
# imports. Set PerMonitorV2 first so Win32 window rectangles and ImageGrab use
# the same physical-pixel screen space.
ensure_process_dpi_awareness()

import cv2
import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab


import client_e2e_run as client_harness  # noqa: E402


TASK = "跟我玩这个游戏，你打开画板就知道了，现在到你了，你用红色我用黑色，轮流来"
DEFAULT_OUTPUT = (
    WORKSPACE / "output" / "playwright" / "client-paint-game-e2e"
)
ORACLE_SOURCE = "independent_visible_paint_pixels"
_ROOT_ANCESTOR = 2  # GA_ROOT


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _rgb_array(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, Image.Image):
        value = np.asarray(image.convert("RGB"), dtype=np.uint8)
    else:
        value = np.asarray(image, dtype=np.uint8)
    if value.ndim != 3 or value.shape[2] != 3:
        raise ValueError("board image must be an RGB array")
    return np.ascontiguousarray(value)


def _red_mask(image: np.ndarray) -> np.ndarray:
    rgb = image.astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    mask = (
        (red >= 125)
        & (red - green >= 42)
        & (red - blue >= 32)
    ).astype(np.uint8) * 255
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    return cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )


def _neutral_dark_mask(image: np.ndarray) -> np.ndarray:
    rgb = image.astype(np.int16)
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    return ((maximum <= 175) & (maximum - minimum <= 38)).astype(np.uint8)


def cell_bounds(
    image_shape: tuple[int, ...],
    cell_index: int,
) -> tuple[int, int, int, int]:
    """Return one row-major tic-tac-toe cell in board-image pixels."""

    if (
        type(cell_index) is not int
        or not 0 <= cell_index <= 8
        or len(image_shape) < 2
    ):
        raise ValueError("expected cell must be a row-major integer in 0..8")
    height, width = int(image_shape[0]), int(image_shape[1])
    if width < 90 or height < 90:
        raise ValueError("board crop is too small for a reliable 3x3 oracle")
    row, column = divmod(cell_index, 3)
    return (
        round(column * width / 3),
        round(row * height / 3),
        round((column + 1) * width / 3),
        round((row + 1) * height / 3),
    )


def _inset(
    bounds: tuple[int, int, int, int],
    ratio: float,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    dx = max(1, round((right - left) * ratio))
    dy = max(1, round((bottom - top) * ratio))
    return left + dx, top + dy, right - dx, bottom - dy


def baseline_contract(
    baseline: Image.Image | np.ndarray,
    expected_cell: int,
) -> dict[str, Any]:
    """Fail closed unless the supplied legal cell is visibly empty."""

    image = _rgb_array(baseline)
    red = _red_mask(image)
    dark = _neutral_dark_mask(image)
    left, top, right, bottom = _inset(
        cell_bounds(image.shape, expected_cell),
        0.14,
    )
    interior = dark[top:bottom, left:right]
    area = int(image.shape[0] * image.shape[1])
    red_count = int(cv2.countNonZero(red))
    dark_count = int(dark.sum())
    interior_dark_ratio = float(interior.mean()) if interior.size else 1.0
    errors: list[str] = []
    if red_count > max(18, round(area * 0.0008)):
        errors.append("baseline board already contains red ink")
    if dark_count < max(90, round(area * 0.0015)):
        errors.append("baseline board has insufficient persistent dark structure")
    if interior_dark_ratio > 0.035:
        errors.append("expected legal cell is not visibly empty")
    return {
        "passed": not errors,
        "errors": errors,
        "expected_cell": expected_cell,
        "expected_cell_bounds": list(cell_bounds(image.shape, expected_cell)),
        "baseline_red_pixels": red_count,
        "baseline_dark_pixels": dark_count,
        "expected_cell_dark_ratio": round(interior_dark_ratio, 6),
        "image_size": [int(image.shape[1]), int(image.shape[0])],
    }


def evaluate_board_frame(
    baseline: Image.Image | np.ndarray,
    current: Image.Image | np.ndarray,
    expected_cell: int,
) -> tuple[dict[str, Any], np.ndarray]:
    """Evaluate a red X independently from runtime/agent completion claims."""

    before = _rgb_array(baseline)
    after = _rgb_array(current)
    if before.shape != after.shape:
        raise ValueError("baseline and current board crops differ in size")

    base_check = baseline_contract(before, expected_cell)
    red = _red_mask(after)
    base_dark = _neutral_dark_mask(before)
    current_dark = _neutral_dark_mask(after)
    retained = int(np.logical_and(base_dark, current_dark).sum())
    base_dark_count = int(base_dark.sum())
    retention = retained / max(1, base_dark_count)

    cell = cell_bounds(after.shape, expected_cell)
    left, top, right, bottom = cell
    cell_width, cell_height = right - left, bottom - top
    tolerance_x = max(2, round(cell_width * 0.045))
    tolerance_y = max(2, round(cell_height * 0.045))
    allowed = np.zeros(red.shape, dtype=np.uint8)
    allowed[
        max(0, top - tolerance_y) : min(red.shape[0], bottom + tolerance_y),
        max(0, left - tolerance_x) : min(red.shape[1], right + tolerance_x),
    ] = 255
    inside_red = cv2.bitwise_and(red, allowed)
    outside_red = cv2.bitwise_and(red, cv2.bitwise_not(allowed))
    inside_count = int(cv2.countNonZero(inside_red))
    outside_count = int(cv2.countNonZero(outside_red))
    wrong_red = outside_count > max(28, round(max(1, inside_count) * 0.08))

    cell_red = red[top:bottom, left:right]
    points = cv2.findNonZero(cell_red)
    bbox = [0, 0, 0, 0]
    width_ratio = 0.0
    height_ratio = 0.0
    if points is not None:
        x, y, width, height = cv2.boundingRect(points)
        bbox = [x + left, y + top, x + left + width, y + top + height]
        width_ratio = width / max(1, cell_width)
        height_ratio = height / max(1, cell_height)

    minimum_line = max(18, round(min(cell_width, cell_height) * 0.34))
    raw_lines = cv2.HoughLinesP(
        cell_red,
        1,
        np.pi / 180,
        threshold=max(10, round(minimum_line * 0.35)),
        minLineLength=minimum_line,
        maxLineGap=max(5, round(minimum_line * 0.28)),
    )
    positive: list[list[int]] = []
    negative: list[list[int]] = []
    for raw in [] if raw_lines is None else np.asarray(raw_lines).reshape(-1, 4):
        x1, y1, x2, y2 = (int(value) for value in raw)
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) < minimum_line * 0.55 or abs(dy) < minimum_line * 0.55:
            continue
        slope_ratio = abs(dy / dx)
        if not 0.38 <= slope_ratio <= 2.65:
            continue
        line = [x1 + left, y1 + top, x2 + left, y2 + top]
        (positive if dx * dy > 0 else negative).append(line)

    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    radius = max(4, round(min(cell_width, cell_height) * 0.16))
    yy, xx = np.ogrid[: red.shape[0], : red.shape[1]]
    center_mask = (
        (xx - center_x) ** 2 + (yy - center_y) ** 2 <= radius**2
    ).astype(np.uint8) * 255
    center_red_count = int(
        cv2.countNonZero(cv2.bitwise_and(red, center_mask))
    )

    shape_passed = bool(
        inside_count >= max(65, round(cell_width * cell_height * 0.006))
        and width_ratio >= 0.38
        and height_ratio >= 0.38
        and positive
        and negative
        and center_red_count >= 4
    )
    errors = list(base_check["errors"])
    if retention < 0.82:
        errors.append("baseline black board structure was not retained")
    if wrong_red:
        errors.append("red ink appeared outside the expected legal cell")
    if not shape_passed:
        errors.append("expected cell does not contain a complete red X")
    verdict = {
        "candidate_passed": bool(
            base_check["passed"]
            and retention >= 0.82
            and not wrong_red
            and shape_passed
        ),
        "errors": errors,
        "expected_cell": expected_cell,
        "expected_cell_bounds": list(cell),
        "red_pixels_inside_expected_cell": inside_count,
        "red_pixels_outside_expected_cell": outside_count,
        "wrong_red": wrong_red,
        "red_bbox": bbox,
        "red_bbox_width_ratio": round(width_ratio, 6),
        "red_bbox_height_ratio": round(height_ratio, 6),
        "positive_diagonal_count": len(positive),
        "negative_diagonal_count": len(negative),
        "positive_diagonals": positive[:8],
        "negative_diagonals": negative[:8],
        "center_red_pixels": center_red_count,
        "baseline_dark_retention": round(retention, 6),
        "baseline": base_check,
    }
    return verdict, red


class BoardOracle:
    """Stateful stability/sticky-failure layer over the pure pixel evaluator."""

    def __init__(
        self,
        baseline: Image.Image | np.ndarray,
        expected_cell: int,
        *,
        stable_frames: int = 3,
    ) -> None:
        if type(stable_frames) is not int or not 1 <= stable_frames <= 12:
            raise ValueError("stable_frames must be an integer in 1..12")
        self.baseline = _rgb_array(baseline)
        self.expected_cell = expected_cell
        self.stable_frames = stable_frames
        self.baseline_evidence = baseline_contract(
            self.baseline,
            expected_cell,
        )
        if self.baseline_evidence["passed"] is not True:
            raise ValueError(
                "invalid Paint board baseline: "
                + "; ".join(self.baseline_evidence["errors"])
            )
        self.valid_streak = 0
        self.sample_count = 0
        self.wrong_red_seen = False
        self.last_verdict: dict[str, Any] = {}

    def observe(
        self,
        current: Image.Image | np.ndarray,
    ) -> tuple[dict[str, Any], np.ndarray]:
        evaluation, mask = evaluate_board_frame(
            self.baseline,
            current,
            self.expected_cell,
        )
        self.sample_count += 1
        self.wrong_red_seen = bool(
            self.wrong_red_seen or evaluation["wrong_red"]
        )
        candidate = bool(
            evaluation["candidate_passed"] and not self.wrong_red_seen
        )
        self.valid_streak = self.valid_streak + 1 if candidate else 0
        passed = self.valid_streak >= self.stable_frames
        verdict = {
            "source": ORACLE_SOURCE,
            "passed": passed,
            "status": "passed" if passed else "observing",
            "sample_count": self.sample_count,
            "stable_frame_count": self.valid_streak,
            "required_stable_frames": self.stable_frames,
            "sticky_wrong_red_seen": self.wrong_red_seen,
            **evaluation,
        }
        # The stateful gate owns the final verdict, not the one-frame candidate.
        verdict["passed"] = passed
        self.last_verdict = verdict
        return verdict, mask


def _root_window(hwnd: int) -> int:
    return int(win32gui.GetAncestor(int(hwnd), _ROOT_ANCESTOR) or hwnd)


def _window_dpi(hwnd: int) -> int:
    """Return the target window's DPI without changing coordinate spaces."""

    try:
        get_dpi = ctypes.windll.user32.GetDpiForWindow
        get_dpi.argtypes = [ctypes.wintypes.HWND]
        get_dpi.restype = ctypes.c_uint
        dpi = int(get_dpi(ctypes.wintypes.HWND(int(hwnd))))
    except (AttributeError, OSError, ValueError):
        dpi = 0
    if dpi <= 0:
        try:
            get_system_dpi = ctypes.windll.user32.GetDpiForSystem
            get_system_dpi.argtypes = []
            get_system_dpi.restype = ctypes.c_uint
            dpi = int(get_system_dpi())
        except (AttributeError, OSError, ValueError):
            dpi = 96
    if not 48 <= dpi <= 768:
        raise RuntimeError(f"invalid DPI reported for target window: {dpi}")
    return dpi


def _scale_logical_pixel(value: int, dpi: int) -> int:
    """Match Win32 MulDiv-style rounding for one logical pixel offset."""

    if type(value) is not int or type(dpi) is not int or dpi <= 0:
        raise ValueError("logical pixel offsets and DPI must be integers")
    numerator = value * dpi
    if numerator >= 0:
        return (numerator + 48) // 96
    return -((-numerator + 48) // 96)


def logical_relative_bbox_to_physical(
    bbox: tuple[int, int, int, int],
    dpi: int,
) -> tuple[int, int, int, int]:
    """Scale an outer-window-relative logical bbox into physical pixels."""

    if len(bbox) != 4:
        raise ValueError("relative bbox must contain four coordinates")
    left, top, right, bottom = bbox
    if not all(type(value) is int for value in bbox):
        raise ValueError("relative bbox coordinates must be integers")
    if not 0 <= left < right or not 0 <= top < bottom:
        raise ValueError("relative bbox must have positive area")
    return tuple(
        _scale_logical_pixel(value, dpi)
        for value in (left, top, right, bottom)
    )


def _physical_length_to_logical(value: int, dpi: int) -> int:
    if value < 0:
        raise ValueError("physical length cannot be negative")
    return (int(value) * 96 + dpi // 2) // dpi


def _window_identity(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("Paint target window does not exist")
    root = _root_window(hwnd)
    _thread_id, process_id = win32process.GetWindowThreadProcessId(root)
    try:
        executable = psutil.Process(process_id).name()
    except psutil.Error:
        executable = ""
    rect = tuple(map(int, win32gui.GetWindowRect(root)))
    dpi = _window_dpi(root)
    physical_width = rect[2] - rect[0]
    physical_height = rect[3] - rect[1]
    return {
        "hwnd": root,
        "process_id": int(process_id),
        "executable": executable,
        "title": win32gui.GetWindowText(root),
        # Backwards-compatible key; PerMonitorV2 makes this physical.
        "window_rect": list(rect),
        "window_rect_physical": list(rect),
        "window_rect_coordinate_space": "physical_screen_pixels",
        "outer_window_size_physical": [physical_width, physical_height],
        "outer_window_size_logical": [
            _physical_length_to_logical(physical_width, dpi),
            _physical_length_to_logical(physical_height, dpi),
        ],
        "dpi": dpi,
        "scale_from_96dpi": round(dpi / 96.0, 6),
    }


def _focus_window(hwnd: int) -> dict[str, Any]:
    identity = _window_identity(hwnd)
    root = int(identity["hwnd"])
    original_rect = tuple(identity["window_rect"])
    if win32gui.IsIconic(root):
        win32gui.ShowWindow(root, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(root)
    errors: list[str] = []
    for _attempt in range(5):
        try:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                0,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
            )
            win32gui.SetForegroundWindow(root)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                win32con.KEYEVENTF_KEYUP,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
            )
        time.sleep(0.18)
        if _root_window(int(win32gui.GetForegroundWindow() or 0)) == root:
            break
    else:
        raise RuntimeError(
            "could not focus the supplied Paint target: "
            + "; ".join(errors[-3:])
        )
    if tuple(map(int, win32gui.GetWindowRect(root))) != original_rect:
        raise RuntimeError("focusing Paint changed its bound geometry")
    return {
        **identity,
        "foreground_root_hwnd": _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        ),
        "focused": True,
    }


class PaintWindowOracle:
    """Capture one immutable visible Paint board and evaluate later frames."""

    def __init__(
        self,
        hwnd: int,
        board_bbox: tuple[int, int, int, int],
        expected_cell: int,
        output_dir: Path,
        *,
        stable_frames: int = 3,
        sample_interval_s: float = 0.18,
        capture: Callable[..., Image.Image] = ImageGrab.grab,
    ) -> None:
        self.identity = _window_identity(hwnd)
        self.hwnd = int(self.identity["hwnd"])
        self.window_rect = tuple(self.identity["window_rect_physical"])
        self.process_id = int(self.identity["process_id"])
        self.dpi = int(self.identity["dpi"])
        logical_bbox = tuple(map(int, board_bbox))
        physical_relative_bbox = logical_relative_bbox_to_physical(
            logical_bbox,
            self.dpi,
        )
        physical_left, physical_top, physical_right, physical_bottom = (
            physical_relative_bbox
        )
        window_width = self.window_rect[2] - self.window_rect[0]
        window_height = self.window_rect[3] - self.window_rect[1]
        if not (
            0 <= physical_left < physical_right <= window_width
            and 0 <= physical_top < physical_bottom <= window_height
        ):
            raise ValueError(
                "board_bbox must be inside the supplied Paint window "
                "in outer-window-relative logical pixels"
            )
        self.relative_bbox = logical_bbox
        self.relative_bbox_physical = physical_relative_bbox
        self.screen_bbox = (
            self.window_rect[0] + physical_left,
            self.window_rect[1] + physical_top,
            self.window_rect[0] + physical_right,
            self.window_rect[1] + physical_bottom,
        )
        self.expected_cell = expected_cell
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stable_frames = stable_frames
        self.sample_interval_s = max(0.02, min(1.0, float(sample_interval_s)))
        self._capture_fn = capture
        self._board: BoardOracle | None = None
        self._last_image: np.ndarray | None = None
        self._last_mask: np.ndarray | None = None
        self._last_verdict: dict[str, Any] = {
            "source": ORACLE_SOURCE,
            "passed": False,
            "status": "not_started",
        }
        self._last_probe_error = ""

    def _validate_target(self) -> None:
        current = _window_identity(self.hwnd)
        if (
            int(current["hwnd"]) != self.hwnd
            or int(current["process_id"]) != self.process_id
            or tuple(current["window_rect_physical"]) != self.window_rect
            or int(current["dpi"]) != self.dpi
        ):
            raise RuntimeError(
                "bound Paint window identity, geometry, or DPI changed"
            )
        foreground = _root_window(
            int(win32gui.GetForegroundWindow() or 0)
        )
        if foreground != self.hwnd:
            raise RuntimeError("bound Paint window is not the visible foreground")

    def _bbox_evidence(self) -> dict[str, Any]:
        return {
            # Keep the original field for report consumers; its documented
            # meaning is now made explicit by the adjacent fields.
            "board_bbox_window_relative": list(self.relative_bbox),
            "board_bbox_window_relative_logical": list(self.relative_bbox),
            "board_bbox_window_relative_physical": list(
                self.relative_bbox_physical
            ),
            "board_bbox_screen": list(self.screen_bbox),
            "board_bbox_screen_physical": list(self.screen_bbox),
            "dpi": self.dpi,
            "scale_from_96dpi": round(self.dpi / 96.0, 6),
        }

    def _capture(self) -> np.ndarray:
        self._validate_target()
        image = self._capture_fn(
            bbox=self.screen_bbox,
            all_screens=True,
        )
        value = _rgb_array(image)
        expected_size = (
            self.screen_bbox[3] - self.screen_bbox[1],
            self.screen_bbox[2] - self.screen_bbox[0],
        )
        if value.shape[:2] != expected_size:
            raise RuntimeError("visible Paint board capture size changed")
        return value

    def capture_baseline(self) -> dict[str, Any]:
        baseline = self._capture()
        Image.fromarray(baseline, "RGB").save(
            self.output_dir / "baseline.png"
        )
        self._board = BoardOracle(
            baseline,
            self.expected_cell,
            stable_frames=self.stable_frames,
        )
        evidence = {
            "source": ORACLE_SOURCE,
            "passed": False,
            "status": "baseline_ready",
            "target": self.identity,
            **self._bbox_evidence(),
            "baseline_sha256": hashlib.sha256(
                baseline.tobytes()
            ).hexdigest(),
            "baseline": self._board.baseline_evidence,
        }
        self._last_verdict = evidence
        _write_json(self.output_dir / "oracle.json", evidence)
        return evidence

    def _save_observation(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        verdict: dict[str, Any],
    ) -> None:
        Image.fromarray(image, "RGB").save(self.output_dir / "final.png")
        Image.fromarray(mask, "L").save(self.output_dir / "mask.png")
        _write_json(self.output_dir / "oracle.json", verdict)
        self._last_image = image
        self._last_mask = mask
        self._last_verdict = verdict

    def probe(self, context: dict[str, Any]) -> dict[str, Any]:
        if self._board is None:
            raise RuntimeError("Paint oracle baseline was not captured")
        try:
            image = self._capture()
            verdict, mask = self._board.observe(image)
            verdict.update(
                {
                    "run_id": str(context.get("run_id") or ""),
                    "target": self.identity,
                    **self._bbox_evidence(),
                    "captured_at": datetime.now().astimezone().isoformat(),
                    "frame_sha256": hashlib.sha256(
                        image.tobytes()
                    ).hexdigest(),
                }
            )
            self._save_observation(image, mask, verdict)
            self._last_probe_error = ""
            # Once a complete X appears, collect the remaining stability frames
            # while Paint is still visibly foreground. This also catches a
            # runtime that reaches terminal immediately after its drawing input.
            while (
                verdict.get("candidate_passed") is True
                and verdict.get("passed") is not True
                and not verdict.get("sticky_wrong_red_seen")
            ):
                time.sleep(self.sample_interval_s)
                image = self._capture()
                verdict, mask = self._board.observe(image)
                verdict.update(
                    {
                        "run_id": str(context.get("run_id") or ""),
                        "target": self.identity,
                        **self._bbox_evidence(),
                        "captured_at": datetime.now().astimezone().isoformat(),
                        "frame_sha256": hashlib.sha256(
                            image.tobytes()
                        ).hexdigest(),
                    }
                )
                self._save_observation(image, mask, verdict)
            return dict(verdict)
        except Exception as error:
            self._last_probe_error = f"{type(error).__name__}: {error}"
            verdict = {
                "source": ORACLE_SOURCE,
                "passed": False,
                "status": "capture_error",
                "run_id": str(context.get("run_id") or ""),
                "error": self._last_probe_error,
                "target": self.identity,
                **self._bbox_evidence(),
            }
            return verdict

    def final_verdict(self) -> dict[str, Any]:
        verdict = dict(self._last_verdict)
        verdict.setdefault("source", ORACLE_SOURCE)
        if self._last_probe_error:
            verdict["last_probe_error"] = self._last_probe_error
            if verdict.get("status") in {"not_started", "baseline_ready"}:
                verdict["status"] = "capture_error"
        verdict["passed"] = verdict.get("passed") is True
        return verdict


def _copy_bound_traces(
    result: dict[str, Any],
    destination: Path,
) -> list[str]:
    copied: list[str] = []
    for trace_id in result.get("bound_trace_ids") or []:
        source = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not source.is_file():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def client_case_spec(*, timeout: int, max_steps: int) -> dict[str, Any]:
    """Return only values submitted through the visible primary Agent entry."""

    return {
        "task": TASK,
        "max_steps": int(max_steps),
        "timeout": int(timeout),
        "runtime_version": "vnext",
        "expected": "independent_success",
    }


def run_paint_game_case(
    *,
    paint_hwnd: int,
    board_bbox: tuple[int, int, int, int],
    expected_cell: int,
    output_root: Path,
    timeout: int = 480,
    max_steps: int = 30,
    stable_frames: int = 3,
    sample_interval_s: float = 0.18,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-paint-tic-tac-toe"
    case_dir.mkdir(parents=True, exist_ok=True)
    oracle_dir = case_dir / "oracle"
    oracle = PaintWindowOracle(
        paint_hwnd,
        board_bbox,
        expected_cell,
        oracle_dir,
        stable_frames=stable_frames,
        sample_interval_s=sample_interval_s,
    )
    initial_focus = _focus_window(paint_hwnd)
    baseline = oracle.capture_baseline()

    def handoff(
        _driver: client_harness.ElectronClientDriver,
        _client_result: dict[str, Any],
    ) -> dict[str, Any]:
        return _focus_window(paint_hwnd)

    result = client_harness.run_case(
        "paint-tic-tac-toe",
        client_case_spec(timeout=timeout, max_steps=max_steps),
        output_root=case_dir / "client",
        reset_session=True,
        after_submit=handoff,
        independent_completion_probe=oracle.probe,
        independent_verifier=lambda _result: oracle.final_verdict(),
    )
    copied_traces = _copy_bound_traces(result, case_dir / "traces")
    report = {
        "kind": "native_paint_tic_tac_toe_e2e",
        "entrypoint": "electron_client_windows_uia",
        "task": TASK,
        "runtime_version": "vnext",
        "expected_cell": expected_cell,
        "initial_focus": initial_focus,
        "baseline": baseline,
        "oracle": oracle.final_verdict(),
        "client_result": result,
        "copied_traces": copied_traces,
        "passed": result.get("passed") is True,
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    report_path = case_dir / "paint-report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the unchanged tic-tac-toe goal against a pre-arranged "
            "visible Microsoft Paint board."
        )
    )
    parser.add_argument("--paint-hwnd", type=int, required=True)
    parser.add_argument(
        "--board-bbox",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        required=True,
        help=(
            "board bounds in 96-DPI logical pixels relative to the outer "
            "Paint window; capture scales them using the target window DPI"
        ),
    )
    parser.add_argument(
        "--expected-cell",
        type=int,
        choices=range(9),
        required=True,
        help="single legal destination in row-major order, 0..8",
    )
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--stable-frames", type=int, default=3)
    parser.add_argument("--sample-interval", type=float, default=0.18)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_paint_game_case(
        paint_hwnd=args.paint_hwnd,
        board_bbox=tuple(args.board_bbox),
        expected_cell=args.expected_cell,
        output_root=args.output_root,
        timeout=args.timeout,
        max_steps=args.max_steps,
        stable_frames=args.stable_frames,
        sample_interval_s=args.sample_interval,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
