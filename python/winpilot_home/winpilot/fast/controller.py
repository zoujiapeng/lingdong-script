"""Fast GUI controller: execute short grounded action batches."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any

import cv2
import numpy as np
import win32gui
import win32process

from ..agent.tool_result import ToolResult
from ..action import executor as action_executor
from ..action.desktop_queue import DESKTOP_ACTION_QUEUE
from ..action.raw_device import RawDeviceExecutor
from ..config import CONFIG
from ..perception import ocr, uia, vision_cv, vlm
from ..utils.log import BUS
from ..utils.screenshot import Region, capture, window_region
from .action_contract import (
    ALLOWED_BATCH_ACTIONS,
    BATCH_CLIPBOARD_ACTIONS,
    BATCH_MUTATING_ACTIONS,
    BATCH_OBSERVE_ACTIONS,
    build_action_envelope,
    validate_batch_actions,
)
from .element_cache import ElementCache, LocateResult, anchor_from_dict
from .input_locator import locate_input
from .types import BatchMetrics
from .visual_servo import ServoConfig, track_visual_target
from .watcher import ScreenWatcher
_ANCHOR_HINT_KEYS = {
    "anchor",
    "label",
    "expected_region",
    "visual_anchor",
    "fallback_text",
    "role",
    "text_query",
}


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _same_window_process(first_hwnd: int, second_hwnd: int) -> bool:
    try:
        _first_thread, first_pid = win32process.GetWindowThreadProcessId(int(first_hwnd))
        _second_thread, second_pid = win32process.GetWindowThreadProcessId(int(second_hwnd))
        return bool(first_pid and first_pid == second_pid)
    except Exception:
        return False


def _json_dict(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_json_object(text: str) -> bool:
    return (text or "").lstrip().startswith("{")


def _grounded_query_matches(snapshot: Any, query: str) -> list[Any]:
    needle = _norm_text(query)
    if not needle or snapshot is None:
        return []
    matches = []
    for element in getattr(snapshot, "elements", []) or []:
        text = str(getattr(element, "text", "") or "").strip()
        compact = _norm_text(text)
        if not compact or needle not in compact:
            continue
        if len(compact) > max(96, len(needle) * 6):
            continue
        matches.append(element)
    return matches


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _norm_text(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _short_control_terms(anchor: Any) -> list[str]:
    terms: list[str] = []
    for value in (anchor.label, *anchor.fallback_text):
        text = re.sub(r"\s+", "", str(value or "").strip())
        if 1 <= len(text) <= 3 and text not in terms:
            terms.append(text)
    return terms


_DIALOG_CONTEXT_MARKERS = (
    "dialog", "popup", "modal", "prompt", "flyout",
    "对话框", "弹窗", "弹出", "提示框", "浮层",
)


def _dialog_context_candidate(snapshot: Any, anchor: Any, action: dict[str, Any]) -> Any | None:
    context = str(action.get("context") or action.get("subgoal") or "").casefold()
    if not any(marker in context for marker in _DIALOG_CONTEXT_MARKERS):
        return None
    terms = [
        _norm_text(value)
        for value in (anchor.label, *anchor.fallback_text)
        if _norm_text(value)
    ]
    if not terms:
        return None
    region = snapshot.region
    expected = (
        str(anchor.expected_region or "").casefold()
        .replace("_", "-")
        .replace(" ", "-")
        .replace("/", "-")
    )
    titlebar_bottom = region.top + max(52, int(region.height * 0.06))

    def expected_match(element: Any) -> bool:
        rel_x = (int(element.cx) - region.left) / max(1, region.width)
        rel_y = (int(element.cy) - region.top) / max(1, region.height)
        if expected in {"top-right", "upper-right"}:
            return rel_x >= 0.55 and rel_y <= 0.45
        if expected in {"top-left", "upper-left"}:
            return rel_x <= 0.45 and rel_y <= 0.45
        if expected in {"bottom-right", "lower-right"}:
            return rel_x >= 0.55 and rel_y >= 0.55
        if expected in {"bottom-left", "lower-left"}:
            return rel_x <= 0.45 and rel_y >= 0.55
        return True

    candidates: list[tuple[int, Any]] = []
    for element in getattr(snapshot, "elements", []) or []:
        text = _norm_text(getattr(element, "text", ""))
        role = str(getattr(element, "role", "") or "").casefold()
        if "button" not in role or not text:
            continue
        exact = text in terms
        if not exact and not any(term in text or text in term for term in terms):
            continue
        if not (region.left <= int(element.cx) < region.right and region.top <= int(element.cy) < region.bottom):
            continue
        if int(element.cy) <= titlebar_bottom or not expected_match(element):
            continue
        candidates.append((2 if exact else 1, element))
    if not candidates:
        return None
    best_quality = max(quality for quality, _element in candidates)
    elements = [element for quality, element in candidates if quality == best_quality]
    if expected.startswith("bottom") or expected.startswith("lower"):
        return max(elements, key=lambda element: int(element.cy))
    return min(elements, key=lambda element: int(element.cy))


def _needs_replace_guard(text: str, action: dict[str, Any]) -> bool:
    """Return true for inputs where append-vs-replace mistakes are costly."""
    raw_parts = [
        text,
        action.get("label"),
        action.get("text_query"),
        action.get("role"),
        action.get("expected_region"),
    ]
    for item in action.get("fallback_text") or []:
        raw_parts.append(item)
    haystack = " ".join(str(part or "") for part in raw_parts).casefold()
    if any(token in haystack for token in (
        "filename", "file name", "文件名", "path", "路径", "save as", "另存", "打开",
    )):
        return True
    stripped = text.strip()
    if "\\" in stripped or "/" in stripped:
        return True
    if "." in stripped and len(stripped) <= 260 and not any(ch.isspace() for ch in stripped):
        return True
    return False


def _is_input_element(element: Any, region: Region | None = None) -> bool:
    role = str(getattr(element, "role", "") or "").casefold()
    if not any(term in role for term in (
        "edit", "combobox", "textbox", "text box", "searchbox", "input", "textarea",
    )):
        return False
    if region is None:
        return True
    text = str(getattr(element, "text", "") or "").strip()
    states = {
        _norm_text(state)
        for state in (getattr(element, "states", None) or [])
    }
    compact_property = bool(re.match(r"^[^=\r\n]{1,32}=.+", text))
    narrow = int(getattr(element, "width", 0) or 0) < int(region.width * 0.45)
    return not (compact_property and narrow and "focused" not in states)


def _detect_prefixed_glob_text(visible_text: str, target_text: str) -> str:
    """Detect common file-dialog failures like '*.txtReport.txt' after clear."""
    target = str(target_text or "").strip()
    if not target:
        return ""
    compact_visible = "".join(str(visible_text or "").split()).casefold()
    compact_target = "".join(target.split()).casefold()
    if not compact_target or compact_target not in compact_visible:
        return ""
    prefixes = ("*.", "*")
    for prefix in prefixes:
        needle = f"{prefix}{compact_target}"
        if needle in compact_visible:
            return f"input was appended to existing wildcard prefix ({prefix})"
    # Windows file dialogs often start with a filter-like default such as
    # *.txt; if it remains immediately before the intended filename, the save
    # target is invalid even though the requested literal appears on screen.
    for idx in range(len(compact_visible)):
        if not compact_visible.startswith(compact_target, idx):
            continue
        before = compact_visible[max(0, idx - 12):idx]
        if "*." in before and before.endswith((".txt", ".log", ".csv", ".json", ".md", ".png", ".jpg", ".jpeg")):
            return "input was appended after an existing file-glob filter"
    return ""


def _input_presence_error(visible_text: str, target_text: str) -> str:
    """Return an error when a high-risk input is not visible after typing."""
    target = str(target_text or "").strip()
    if not target:
        return ""
    visible_norm = _norm_text(visible_text)
    if not visible_norm:
        return "typed text was not visible after input"
    candidates = [_norm_text(target)]
    if "\\" in target or "/" in target:
        basename = target.replace("/", "\\").rsplit("\\", 1)[-1]
        if basename and basename != target:
            candidates.append(_norm_text(basename))
    for candidate in candidates:
        if candidate and candidate in visible_norm:
            return ""
    return "typed text was not visible after input"


def _should_move_to_end_before_typing(text: str, action: dict[str, Any]) -> bool:
    """For append-style entry, place the caret at the document/input end.

    Clicking a text editor before typing can leave the caret in the middle or
    preserve a selection. When the requested text starts with a newline, the
    caller is almost certainly appending another line rather than replacing the
    current contents.
    """
    if _truthy(action.get("append") or action.get("move_to_end")):
        return True
    return str(text or "").startswith(("\n", "\r"))


def _color_mask(image: np.ndarray, color: str) -> np.ndarray | None:
    if image is None or getattr(image, "size", 0) == 0:
        return None
    return vision_cv.coarse_color_masks(image).get(str(color or "").casefold())


def _small_color_components(image: np.ndarray) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    if image is None or getattr(image, "size", 0) == 0:
        return components
    total = int(image.shape[0] * image.shape[1])
    min_area = max(18, int(total * 0.000015))
    max_area = max(420, int(total * 0.004))
    for color in (
        "red", "orange", "brown", "yellow", "lime", "green", "cyan", "blue",
        "purple", "magenta", "pink",
        "black", "gray", "white",
    ):
        mask = _color_mask(image, color)
        if mask is None:
            continue
        num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask.astype("uint8"), connectivity=8
        )
        for label in range(1, num_labels):
            x, y, w, h, area = [int(v) for v in stats[label].tolist()]
            if area < min_area or area > max_area:
                continue
            if w < 6 or h < 6 or w > 64 or h > 64:
                continue
            aspect = w / max(1, h)
            if aspect < 0.45 or aspect > 2.2:
                continue
            components.append({
                "color": color,
                "bbox": [x, y, x + w, y + h],
                "area": area,
                "center": [x + w // 2, y + h // 2],
            })
    return components


def _failed(result: ToolResult) -> bool:
    if result.terminal == "fail":
        return True
    data = _json_dict(result.text)
    if data.get("blocked") is True:
        return True
    if data.get("ok") is False:
        return True
    if (
        "verified" in data
        and data.get("verified") is False
        and data.get("expect")
        and not (
            data.get("mutation_applied") is True
            and data.get("postcondition_pending") is True
        )
    ):
        return True
    return False


def _result_meta(result: ToolResult, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is None:
        data = _json_dict(result.text)
    meta: dict[str, Any] = {}
    for key in (
        "ok",
        "verified",
        "via",
        "visual_change_detected",
        "visual_change_ratio",
        "screen_delta",
        "screen_watch",
        "action_target",
        "needs_observe",
        "verify_method",
        "verify_trace",
        "controller_state",
        "subgoal",
        "element_cache",
        "controller_auto_observe",
        "controller_internal_action_count",
        "cooperative_input",
        "blocked",
        "error",
    ):
        if key in data:
            meta[key] = data[key]
    locator = data.get("anchor_locator")
    if isinstance(locator, dict):
        meta["anchor_locator"] = {
            "found": bool(locator.get("found")),
            "cache_hit": bool(locator.get("cache_hit")),
            "score": locator.get("score"),
            "reason": locator.get("reason"),
        }
    resolved = data.get("resolved_element")
    if isinstance(resolved, dict):
        meta["resolved_element"] = {
            "elem_id": resolved.get("elem_id"),
            "text": resolved.get("text"),
            "role": resolved.get("role"),
            "source": resolved.get("source"),
            "bbox": resolved.get("bbox"),
        }
    servo = data.get("visual_servo")
    if isinstance(servo, dict):
        meta["visual_servo"] = {
            "enabled": bool(servo.get("enabled")),
            "confirmed": bool(servo.get("confirmed")),
            "corrected": bool(servo.get("corrected")),
            "correction_count": int(servo.get("correction_count") or 0),
            "elapsed_ms": int(servo.get("elapsed_ms") or 0),
            "activation": servo.get("activation"),
            "lost_reason": servo.get("lost_reason"),
            "source_counts": servo.get("source_counts") or {},
        }
    return meta


def _normalise_action(action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Accept both explicit and natural shorthand action formats.

    Supported:
      {"action":"click", "element_id": 1}
      {"tool":"click", "element_id": 1}
      {"click": {"element_id": 1}}
    """
    name = str(action.get("action") or action.get("tool") or "")
    if name:
        args = {k: v for k, v in action.items() if k not in {"action", "tool", "params"}}
        params = action.get("params")
        if isinstance(params, dict):
            args = {**params, **args}
        return _normalise_wait_condition(name, args)
    if len(action) == 1:
        only_name, only_args = next(iter(action.items()))
        name = str(only_name)
        if isinstance(only_args, dict):
            return _normalise_wait_condition(name, dict(only_args))
        if name == "hotkey":
            keys = list(only_args) if isinstance(only_args, list) else [only_args]
            return name, {"keys": [str(key) for key in keys]}
        if name in {"type_text", "type_into_element"}:
            return name, {"text": str(only_args)}
        return name, {"value": only_args}
    return "", {}


def _normalise_wait_condition(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Route the model's common stable-condition shorthand to ScreenWatcher."""
    condition = args.get("condition")
    if name != "wait" or not isinstance(condition, dict):
        return name, args
    condition_type = str(condition.get("type") or "").strip().casefold().replace("-", "_")
    if condition_type not in {"stable", "screen_stable", "visual_stable"}:
        return name, args
    try:
        stable_ms = int(
            condition.get("duration_ms")
            or condition.get("stable_ms")
            or args.get("stable_ms")
            or 300
        )
    except (TypeError, ValueError):
        stable_ms = 300
    stable_ms = max(50, min(1500, stable_ms))
    try:
        timeout_s = float(condition.get("timeout_s") or args.get("timeout_s") or 2.0)
    except (TypeError, ValueError):
        timeout_s = 2.0
    normalised = {
        key: value
        for key, value in args.items()
        if key not in {"condition", "duration_ms"}
    }
    normalised["stable_ms"] = stable_ms
    normalised["timeout_s"] = max(stable_ms / 1000.0, min(2.0, timeout_s))
    for key in ("required", "max_ratio"):
        if key in condition and key not in normalised:
            normalised[key] = condition[key]
    return "wait_stable", normalised


_HOTKEY_MODIFIERS = {
    "alt", "alt_l", "alt_r", "ctrl", "control", "control_l", "control_r",
    "shift", "shift_l", "shift_r", "meta", "super",
}


def _stable_batch_actions(actions: list[dict[str, Any]], subgoal: str) -> list[dict[str, Any]]:
    """Normalize common model-generated key sequences before execution.

    This remains application agnostic: it repairs invalid multi-key chords and
    gives first-line formatting a deterministic document-start anchor.
    """
    expanded: list[dict[str, Any]] = []
    for original in actions:
        if not isinstance(original, dict):
            expanded.append(original)
            continue
        name, args = _normalise_action(original)
        keys = args.get("keys") if name == "hotkey" else None
        if not isinstance(keys, list):
            expanded.append(original)
            continue
        modifiers = [str(key) for key in keys if str(key).casefold() in _HOTKEY_MODIFIERS]
        ordinary = [str(key) for key in keys if str(key).casefold() not in _HOTKEY_MODIFIERS]
        if len(ordinary) <= 1:
            if modifiers and ordinary:
                normalized_keys = [*modifiers, *ordinary]
                if normalized_keys != [str(key) for key in keys]:
                    expanded.append({"action": "hotkey", "keys": normalized_keys})
                else:
                    expanded.append(original)
            else:
                expanded.append(original)
            continue
        # Models sometimes emit ["Down", "Home"] to mean two sequential
        # keys.  Sending it as one chord is undefined in desktop editors.
        for key in ordinary:
            expanded.append({"action": "hotkey", "keys": [*modifiers, key]})

    normalized_goal = _norm_text(subgoal)
    first_line_format = (
        any(term in normalized_goal for term in ("firstline", "firstparagraph", "heading", "title", "首行", "第一行", "标题"))
        and any(term in normalized_goal for term in ("bold", "加粗"))
        and any(term in normalized_goal for term in ("center", "centre", "居中"))
    )
    if not first_line_format:
        return expanded

    key_sets: list[set[str]] = []
    for item in expanded:
        name, args = _normalise_action(item) if isinstance(item, dict) else ("", {})
        keys = args.get("keys") if name == "hotkey" else []
        key_sets.append({str(key).casefold() for key in keys} if isinstance(keys, list) else set())
    has_select_line = any(keys == {"shift", "end"} for keys in key_sets)
    has_bold = any(keys in ({"ctrl", "b"}, {"control", "b"}) for keys in key_sets)
    has_center = any(keys in ({"ctrl", "e"}, {"control", "e"}) for keys in key_sets)
    if not (has_select_line and has_bold and has_center):
        return expanded
    for index, keys in enumerate(key_sets):
        if keys == {"home"}:
            expanded[index] = {"action": "hotkey", "keys": ["Ctrl", "Home"]}
            break
    return expanded


def _anchor_payload(anchor) -> dict[str, Any]:
    return {
        "label": anchor.label,
        "expected_region": anchor.expected_region,
        "visual_anchor": anchor.visual_anchor,
        "fallback_text": list(anchor.fallback_text),
        "role": anchor.role,
    }


def _anchor_query(anchor) -> str:
    seen: set[str] = set()
    terms: list[str] = []
    for raw in (anchor.label, *anchor.fallback_text):
        term = str(raw or "").strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms[0] if terms else ""


def _text_width_units(value: str) -> float:
    """Approximate OCR text width without requiring per-character boxes."""
    total = 0.0
    for char in value:
        total += 2.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 1.0
    return max(1.0, total)


def _non_native_text_click_point(element: Any, anchor: Any) -> tuple[int, int, dict[str, Any] | None]:
    """Map a label inside a merged OCR text run to physical screen pixels."""
    source = str(getattr(element, "source", "") or "").casefold()
    cx = int(getattr(element, "cx", 0) or 0)
    cy = int(getattr(element, "cy", 0) or 0)
    if not source.startswith("ocr"):
        return cx, cy, None

    text = str(getattr(element, "text", "") or "")
    folded = text.casefold()
    match: tuple[int, str] | None = None
    for raw in (getattr(anchor, "label", ""), *getattr(anchor, "fallback_text", ())):
        term = str(raw or "").strip()
        if not term:
            continue
        index = folded.find(term.casefold())
        if index >= 0 and len(term) < len(text):
            match = (index, text[index:index + len(term)])
            break
    if match is None:
        return cx, cy, None

    index, matched = match
    left = int(getattr(element, "left", 0) or 0)
    top = int(getattr(element, "top", 0) or 0)
    width = max(1, int(getattr(element, "width", 0) or 0))
    height = max(1, int(getattr(element, "height", 0) or 0))
    total_units = _text_width_units(text)
    start_units = _text_width_units(text[:index]) if index else 0.0
    matched_units = _text_width_units(matched)
    ratio = min(1.0, max(0.0, (start_units + matched_units / 2.0) / total_units))
    if width >= height * 1.4:
        cx = left + int(round(width * ratio))
    elif height >= width * 1.4:
        cy = top + int(round(height * ratio))
    return cx, cy, {
        "source_text": text,
        "matched_text": matched,
        "ratio": round(ratio, 4),
        "point": [cx, cy],
    }


def _with_batch_expect_defaults(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in BATCH_OBSERVE_ACTIONS:
        return args
    expect = args.get("expect")
    if not isinstance(expect, dict) or "timeout_s" in expect:
        return args
    updated = dict(args)
    normalized_expect = dict(expect)
    file_path = normalized_expect.get("file_exists")
    if isinstance(file_path, str) and "file_format" not in normalized_expect:
        suffix = file_path.rsplit(".", 1)[-1].casefold() if "." in file_path else ""
        if suffix in {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}:
            normalized_expect["file_format"] = {
                "path": file_path,
                "format": suffix,
            }
    eventual_groundtruth = {
        "file_exists", "file_format", "file_contains", "process_running", "process_gone",
    }
    timeout_s = 6.0 if any(key in normalized_expect for key in eventual_groundtruth) else 2.0
    updated["expect"] = {**normalized_expect, "timeout_s": timeout_s}
    return updated


def _has_anchor_hint(args: dict[str, Any]) -> bool:
    if any(key in args and args.get(key) not in (None, "", []) for key in _ANCHOR_HINT_KEYS):
        return True
    anchor = args.get("anchor")
    return isinstance(anchor, dict) and any(
        key in anchor and anchor.get(key) not in (None, "", [])
        for key in _ANCHOR_HINT_KEYS
    )


def _has_direct_click_target(args: dict[str, Any]) -> bool:
    if args.get("element_id") is not None:
        return True
    return args.get("x") is not None and args.get("y") is not None


def _upgrade_semantic_action(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Route semantic click/type hints through the visual-anchor controller.

    Models naturally emit things like click(label="Export"). Raw click cannot
    execute that safely; click_element can locate, cache, retry observe, and
    return resolved evidence.
    """
    if name == "click" and not _has_direct_click_target(args) and _has_anchor_hint(args):
        return "click_element", args, name
    if name == "type_text" and _has_anchor_hint(args):
        return "type_into_element", args, name
    return name, args, ""


class FastGuiController:
    """Small inner-loop executor owned by a ToolSession."""

    def __init__(
        self,
        session,
        cache: ElementCache | None = None,
        watcher: ScreenWatcher | None = None,
        capture_fn=capture,
    ) -> None:
        self.session = session
        self.cache = cache or ElementCache()
        self.watcher = watcher or ScreenWatcher(
            region_provider=self._target_region,
            event_context_provider=lambda: {"run_id": getattr(self.session, "run_id", None)},
        )
        self.capture_fn = capture_fn
        self.raw_device = RawDeviceExecutor(session)
        self._color_palette_button_cache: dict[tuple[int, int, int, int, int], tuple[int, int]] = {}
        self._color_swatch_cache: dict[
            tuple[tuple[int, int, int, int, int], str], tuple[int, int]
        ] = {}
        self._persistent_palette_keys: set[tuple[int, int, int, int, int]] = set()
        self._active_color = ""

    def _target_region(self):
        snapshot = getattr(self.session, "snapshot", None)
        if snapshot is not None:
            return snapshot.region
        hwnd = getattr(self.session, "hwnd", None)
        if hwnd:
            try:
                return window_region(hwnd)
            except Exception:
                return None
        return None

    @staticmethod
    def _palette_swatch_point(image, region: Region, color: str) -> tuple[int, int] | None:
        try:
            arr = np.asarray(image)
        except Exception:
            return None
        if arr is None or getattr(arr, "size", 0) == 0:
            return None
        components = _small_color_components(arr)
        # A real color palette normally exposes many compact color choices. This
        # prevents single colored icons, scrollbars, or accent lines from being
        # treated as selectable swatches.
        # Palette swatches may live in a dropdown or directly in a ribbon.
        # Exclude only the actual titlebar; local color diversity below guards
        # against treating a colorful app/tool icon as a selectable palette.
        title_cutoff = max(48, int(arr.shape[0] * 0.055))
        compact_components = []
        for item in components:
            bbox = item.get("bbox") or [0, 0, 0, 0]
            center = item.get("center") or [0, 0]
            if int(bbox[3]) >= int(arr.shape[0] * 0.75):
                continue
            if int(center[1]) < title_cutoff:
                continue
            compact_components.append(item)
        if len(compact_components) < 8:
            return None
        wanted = []
        for item in compact_components:
            if str(item.get("color") or "").casefold() != str(color or "").casefold():
                continue
            cx, cy = item.get("center") or [0, 0]
            bbox = item.get("bbox") or [0, 0, 0, 0]
            x1, y1, x2, y2 = [int(value) for value in bbox]
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            pad = max(4, int(max(width, height) * 0.35))
            patch = arr[
                max(0, y1 - pad):min(arr.shape[0], y2 + pad),
                max(0, x1 - pad):min(arr.shape[1], x2 + pad),
            ]
            target_mask = _color_mask(patch, color)
            color_purity = 0.0
            if target_mask is not None and getattr(target_mask, "size", 0):
                normalized_color = str(color or "").casefold()
                hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                sat, val = hsv[:, :, 1], hsv[:, :, 2]
                if normalized_color == "black":
                    denominator = max(1, int((val < 110).sum()))
                elif normalized_color == "gray":
                    denominator = max(
                        1, int(((sat < 55) & (val >= 45) & (val < 235)).sum())
                    )
                elif normalized_color == "white":
                    denominator = max(1, int(((sat < 55) & (val >= 205)).sum()))
                else:
                    denominator = max(1, int(((sat > 70) & (val > 50)).sum()))
                color_purity = float(target_mask.sum()) / denominator
            # A solid swatch remains mostly one hue when its bbox is expanded.
            # Multihue "edit colors"/rainbow icons contain a valid target-hue
            # component too, but their expanded neighborhood has low purity.
            if (
                str(color or "").casefold() not in {"black", "gray", "white"}
                and color_purity < 0.58
            ):
                continue
            core_min_area = max(36, int(arr.shape[0] * arr.shape[1] * 0.00025))
            row_core = [
                other
                for other in compact_components
                if int(other.get("area") or 0) >= core_min_area
                and abs(int((other.get("center") or [0, 0])[1]) - int(cy)) <= 13
            ]
            # Collapse multiple masks around one anti-aliased swatch, then
            # find the contiguous horizontal chain containing this candidate.
            x_groups: list[list[dict[str, Any]]] = []
            for other in sorted(
                row_core,
                key=lambda entry: int((entry.get("center") or [0, 0])[0]),
            ):
                ox = int((other.get("center") or [0, 0])[0])
                if x_groups and abs(
                    ox - int((x_groups[-1][-1].get("center") or [0, 0])[0])
                ) <= 8:
                    x_groups[-1].append(other)
                else:
                    x_groups.append([other])
            segments: list[list[list[dict[str, Any]]]] = []
            for group in x_groups:
                gx = int((group[0].get("center") or [0, 0])[0])
                if segments:
                    previous_x = int(
                        (segments[-1][-1][0].get("center") or [0, 0])[0]
                    )
                else:
                    previous_x = gx
                if not segments or gx - previous_x > 66:
                    segments.append([group])
                else:
                    segments[-1].append(group)
            candidate_segment = next(
                (
                    segment
                    for segment in segments
                    if any(
                        abs(
                            int((member.get("center") or [0, 0])[0]) - int(cx)
                        ) <= 8
                        for group in segment
                        for member in group
                    )
                ),
                [],
            )
            if len(candidate_segment) < 6:
                continue
            column_support = any(
                abs(int((other.get("center") or [0, 0])[0]) - int(cx)) <= 8
                and 22
                <= abs(int((other.get("center") or [0, 0])[1]) - int(cy))
                <= 42
                for other in compact_components
            )
            group_centers = [
                int(round(sum(
                    int((member.get("center") or [0, 0])[0])
                    for member in group
                ) / max(1, len(group))))
                for group in candidate_segment
            ]
            candidate_index = min(
                range(len(group_centers)),
                key=lambda index: abs(group_centers[index] - int(cx)),
            )
            gaps = [
                group_centers[index + 1] - group_centers[index]
                for index in range(len(group_centers) - 1)
            ]
            median_gap = float(np.median(gaps)) if gaps else 0.0
            adjacent_gaps = []
            if candidate_index > 0:
                adjacent_gaps.append(group_centers[candidate_index] - group_centers[candidate_index - 1])
            if candidate_index + 1 < len(group_centers):
                adjacent_gaps.append(group_centers[candidate_index + 1] - group_centers[candidate_index])
            regular_single_row = (
                18 <= median_gap <= 45
                and any(
                    18 <= gap <= 45 and abs(float(gap) - median_gap) <= 10
                    for gap in adjacent_gaps
                )
            )
            if not column_support and not regular_single_row:
                continue
            segment_items = [member for group in candidate_segment for member in group]
            neutral_colors = {"black", "gray", "white"}
            chromatic_colors = {
                str(other.get("color") or "")
                for other in segment_items
                if str(other.get("color") or "") not in neutral_colors
            }
            neutral_ratio = (
                sum(
                    1
                    for other in segment_items
                    if str(other.get("color") or "") in neutral_colors
                )
                / max(1, len(segment_items))
            )
            if len(chromatic_colors) >= 4:
                enriched = dict(item)
                enriched["cluster_count"] = len(candidate_segment)
                enriched["color_diversity"] = len(chromatic_colors)
                enriched["neutral_ratio"] = neutral_ratio
                enriched["color_purity"] = round(color_purity, 4)
                wanted.append(enriched)
        if not wanted:
            return None
        wanted.sort(
            key=lambda item: (
                float(item.get("color_purity") or 0.0),
                int(item.get("color_diversity") or 0),
                -float(item.get("neutral_ratio") or 0.0),
                int(item.get("cluster_count") or 0),
                int(item.get("area") or 0),
                -int((item.get("center") or [0, 0])[1]),
            ),
            reverse=True,
        )
        cx, cy = wanted[0]["center"]
        return region.left + int(cx), region.top + int(cy)

    @staticmethod
    def _palette_component_summary(image) -> dict[str, Any]:
        try:
            components = _small_color_components(np.asarray(image))
        except Exception as exc:
            return {"error": str(exc)}
        counts: dict[str, int] = {}
        for item in components:
            color = str(item.get("color") or "")
            counts[color] = counts.get(color, 0) + 1
        return {
            "component_count": len(components),
            "color_counts": counts,
        }

    def _visible_color_label_point(
        self,
        region: Region,
        color: str,
    ) -> tuple[tuple[int, int] | None, dict[str, Any]]:
        """Locate a labeled color control after a CV swatch miss.

        Owner-drawn palettes often expose no accessible text.  A local visual
        grounding pass is a robust fallback because it returns a real screen
        bbox rather than a palette-specific hard-coded coordinate.
        """
        wanted = _norm_text(color)
        aliases = {wanted}
        aliases.update({
            "red": "红色", "blue": "蓝色", "green": "绿色",
            "yellow": "黄色", "orange": "橙色", "purple": "紫色",
            "black": "黑色", "white": "白色", "pink": "粉色",
        }.get(wanted, "").split())
        aliases.discard("")
        toolbar = Region(
            region.left,
            region.top,
            region.width,
            max(120, min(region.height, int(region.height * 0.48))),
        ).clamp_to_screen()
        candidates = list(getattr(getattr(self.session, "snapshot", None), "elements", []) or [])
        source = "snapshot"
        # A palette/dropdown can create fresh accessible children after the
        # parent control was clicked.  The session snapshot intentionally
        # stays stable during a batch, so refresh this narrow UIA surface
        # before falling back to a costly visual grounding pass.
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        if hwnd and "uia" in CONFIG.enabled_dimensions():
            try:
                candidates.extend(uia.collect(hwnd))
                source = "snapshot+uia"
            except Exception:
                pass
        has_exact_structured_match = any(
            _norm_text(getattr(element, "text", "")) == wanted
            and str(getattr(element, "source", "") or "").casefold() in {"uia", "win32"}
            for element in candidates
        )
        if not has_exact_structured_match and vlm.is_configured():
            try:
                candidates.extend(vlm.collect(toolbar))
                source = f"{source}+vlm"
            except Exception as exc:
                return None, {"source": source, "error": str(exc)}
        matches: list[tuple[bool, Any]] = []
        for element in candidates:
            raw_label = str(getattr(element, "text", "") or "").strip()
            label = _norm_text(raw_label)
            if not label:
                continue
            exact = label in aliases
            token_match = any(
                (alias in label if not alias.isascii() else bool(re.search(
                    rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                    raw_label.casefold(),
                )))
                for alias in aliases
            )
            if not exact and not token_match:
                continue
            x, y = int(getattr(element, "cx", 0) or 0), int(getattr(element, "cy", 0) or 0)
            if toolbar.left <= x <= toolbar.right and toolbar.top <= y <= toolbar.bottom:
                matches.append((exact, element))
        if not matches:
            return None, {"source": source, "match_count": 0}
        def match_priority(item: tuple[bool, Any]) -> tuple[int, int, int, float]:
            exact, element = item
            source = str(getattr(element, "source", "") or "").casefold()
            role = str(getattr(element, "role", "") or "").casefold()
            source_rank = {"uia": 0, "win32": 1, "ocr": 2, "vlm": 3}.get(source, 4)
            role_rank = 0 if role in {"button", "menuitem", "radio", "listitem", "checkbox"} else 1
            return (0 if exact else 1, source_rank, role_rank, -float(getattr(element, "confidence", 0.0) or 0.0))

        matches.sort(key=match_priority)
        chosen = matches[0][1]
        return (
            (int(getattr(chosen, "cx", 0)), int(getattr(chosen, "cy", 0))),
            {
                "source": source,
                "element_source": str(getattr(chosen, "source", "")),
                "label": str(getattr(chosen, "text", "")),
                "match_count": len(matches),
            },
        )

    def _palette_cache_key(self, region: Region) -> tuple[int, int, int, int, int]:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        return (hwnd, int(region.left), int(region.top), int(region.width), int(region.height))

    def _open_color_palette(
        self,
        region: Region,
        *,
        cache_key: tuple[int, int, int, int, int] | None = None,
        prefer_cache: bool = True,
        allow_vlm: bool = True,
    ) -> ToolResult:
        if prefer_cache and cache_key is not None:
            cached = self._color_palette_button_cache.get(cache_key)
            if cached is not None:
                x, y = cached
                started = _now_ms()
                click = self._dispatch("click", {"x": x, "y": y, "skip_screen_delta": True})
                if not _failed(click):
                    return ToolResult(json.dumps({
                        "ok": True,
                        "cached": True,
                        "elapsed_ms": max(0, _now_ms() - started),
                        "point": {"x": x, "y": y},
                        "click": _json_dict(click.text),
                        "preview": click.text[:240],
                    }, ensure_ascii=False, default=str))
        toolbar = Region(
            region.left,
            region.top,
            region.width,
            max(96, min(region.height, int(region.height * 0.36))),
        ).clamp_to_screen()
        started = _now_ms()
        label_terms = {"colors", "colour", "color", "颜色", "色彩", "顏色"}
        def is_color_label(element: Any) -> bool:
            text = _norm_text(getattr(element, "text", ""))
            return bool(text) and (
                text in label_terms or any(term in text for term in label_terms)
            )

        # Prefer the live fused snapshot when it contains an accessible
        # control.  Its centre is an actual physical click target, unlike an
        # OCR/VLM label baseline which may need a conservative offset.
        candidates = [
            element
            for element in list(getattr(getattr(self.session, "snapshot", None), "elements", []) or [])
            if is_color_label(element)
            and toolbar.left <= int(getattr(element, "cx", 0) or 0) <= toolbar.right
            and toolbar.top <= int(getattr(element, "cy", 0) or 0) <= toolbar.bottom
        ]
        locator_source = "snapshot" if candidates else ""
        ocr_ms = 0
        if not candidates:
            try:
                ocr_started = _now_ms()
                elements = ocr.collect(toolbar)
                ocr_ms = max(0, _now_ms() - ocr_started)
            except Exception as exc:
                return ToolResult(json.dumps({
                    "ok": False,
                    "error": f"color palette OCR failed: {exc}",
                    "elapsed_ms": max(0, _now_ms() - started),
                }, ensure_ascii=False))
            for element in elements:
                if is_color_label(element):
                    candidates.append(element)
            if candidates:
                locator_source = "ocr"
        vlm_ms = 0
        if not candidates and allow_vlm and vlm.is_configured():
            # Owner-drawn ribbons commonly expose only a color icon to OCR.
            # Escalate this one local control lookup to VLM rather than making
            # the planner repeatedly re-observe the whole application.
            try:
                vlm_started = _now_ms()
                visual_elements = vlm.collect(toolbar)
                vlm_ms = max(0, _now_ms() - vlm_started)
                for element in visual_elements:
                    text = _norm_text(getattr(element, "text", ""))
                    if text and (text in label_terms or any(term in text for term in label_terms)):
                        candidates.append(element)
                if candidates:
                    locator_source = "vlm"
            except Exception as exc:
                logger.debug("color palette VLM locator failed: %s", exc)
        if not candidates:
            return ToolResult(json.dumps({
                "ok": False,
                "error": "no visible Colors/颜色 toolbar label found",
                "elapsed_ms": max(0, _now_ms() - started),
                "ocr_ms": ocr_ms,
                "vlm_ms": vlm_ms,
            }, ensure_ascii=False))
        def candidate_priority(element: Any) -> tuple[int, int, int]:
            source = str(getattr(element, "source", "") or "").casefold()
            role = str(getattr(element, "role", "") or "").casefold()
            source_rank = {"uia": 0, "win32": 1, "ocr": 2, "vlm": 3}.get(source, 4)
            role_rank = 0 if role in {"button", "menuitem", "radio", "checkbox", "combobox"} else 1
            return source_rank, role_rank, int(getattr(element, "cy", 0) or 0)

        candidates.sort(key=candidate_priority)
        label = candidates[0]
        x = int(getattr(label, "cx", 0) or 0)
        y = int(getattr(label, "cy", 0) or 0)
        source = str(getattr(label, "source", "") or "").casefold()
        role = str(getattr(label, "role", "") or "").casefold()
        structured_control = source in {"uia", "win32"} and role in {
            "button", "menuitem", "radio", "checkbox", "combobox",
        }
        # Only unstructured label detections need an upward shift to their
        # owner-drawn button; structured controls are clicked at their centre.
        offset = 0 if structured_control else max(34, min(82, int(region.height * 0.10)))
        click_y = max(region.top + 8, y - offset)
        if cache_key is not None:
            self._color_palette_button_cache[cache_key] = (x, click_y)
        click = self._dispatch("click", {"x": x, "y": click_y, "skip_screen_delta": True})
        return ToolResult(json.dumps({
            "ok": not _failed(click),
            "cached": False,
            "elapsed_ms": max(0, _now_ms() - started),
            "ocr_ms": ocr_ms,
            "vlm_ms": vlm_ms,
            "locator_source": locator_source,
            "point": {"x": x, "y": click_y},
            "click": _json_dict(click.text),
            "preview": click.text[:240],
        }, ensure_ascii=False, default=str))

    def _select_color(self, action: dict[str, Any]) -> ToolResult:
        color = str(action.get("color") or action.get("name") or "").strip().casefold()
        if not color:
            return ToolResult(json.dumps({
                "ok": False,
                "error": "select_color requires color",
            }, ensure_ascii=False))
        hwnd = getattr(self.session, "hwnd", None)
        region = None
        if hwnd:
            try:
                region = window_region(hwnd)
            except Exception:
                region = None
        if region is None:
            region = self._target_region()
        if region is None:
            return ToolResult(json.dumps({
                "ok": False,
                "error": "select_color has no target window/region",
            }, ensure_ascii=False))
        cache_key = self._palette_cache_key(region)
        attempts: list[dict[str, Any]] = []
        raw_strict = action.get("strict")
        strict = True if raw_strict is None else _truthy(raw_strict)
        strict = bool(strict or _truthy(action.get("required_by_user")))
        palette_opened = False

        def optional_fallback(error: str) -> ToolResult:
            if palette_opened:
                close = self._dispatch(
                    "hotkey",
                    {"keys": ["esc"], "skip_screen_delta": True},
                )
                attempts.append({
                    "action": "close_optional_palette",
                    "ok": not _failed(close),
                    "preview": close.text[:240],
                })
            return ToolResult(json.dumps({
                "ok": True,
                "verified": False,
                "mutation_applied": False,
                "postcondition_pending": False,
                "needs_observe": False,
                "color": color,
                "strict": False,
                "color_selection_skipped": True,
                "fallback_to_current_color": True,
                "active_color": self._active_color or None,
                "attempts": attempts,
                "warning": error,
            }, ensure_ascii=False, default=str))

        swatch_cache_key = (cache_key, color)
        cached_swatch = self._color_swatch_cache.get(swatch_cache_key)
        if cached_swatch is not None and cache_key in self._persistent_palette_keys:
            click = self._dispatch(
                "click",
                {
                    "x": cached_swatch[0],
                    "y": cached_swatch[1],
                    "skip_screen_delta": True,
                },
            )
            if not _failed(click):
                self._active_color = color
                return ToolResult(json.dumps({
                    "ok": True,
                    "verified": False,
                    "mutation_applied": True,
                    "postcondition_pending": True,
                    "needs_observe": True,
                    "color": color,
                    "point": {"x": cached_swatch[0], "y": cached_swatch[1]},
                    "verify_method": "cached-cv-color-swatch",
                    "attempts": [{"action": "click_cached_color_swatch", "ok": True}],
                }, ensure_ascii=False, default=str))
            self._color_swatch_cache.pop(swatch_cache_key, None)

        def capture_palette_point(
            *,
            expanded: bool = False,
        ) -> tuple[tuple[int, int] | None, dict[str, Any], int]:
            started = _now_ms()
            toolbar_height = min(
                region.height,
                max(320, int(region.height * 0.62))
                if expanded else
                max(180, int(region.height * 0.28)),
            )
            scan_region = Region(
                region.left,
                region.top,
                region.width,
                toolbar_height,
            ).clamp_to_screen()
            image = self.capture_fn(scan_region)
            image_arr = np.asarray(image)
            mapped_region = scan_region
            if (
                image_arr.ndim < 2
                or image_arr.shape[1] != int(scan_region.width)
                or image_arr.shape[0] != int(scan_region.height)
            ):
                # Custom/test capture providers may return the full target even
                # when asked for an ROI. Preserve their coordinate contract.
                mapped_region = region
            point = self._palette_swatch_point(image, mapped_region, color)
            summary = self._palette_component_summary(image)
            return point, summary, max(0, _now_ms() - started)

        def scan_after_open(action: str) -> None:
            nonlocal point, summary
            deadline = time.monotonic() + 1.6
            first_scan = True
            while time.monotonic() < deadline and point is None:
                if first_scan:
                    first_scan = False
                else:
                    time.sleep(0.12)
                try:
                    point, summary, elapsed_ms = capture_palette_point(expanded=True)
                    attempts.append({
                        "action": action,
                        "ok": point is not None,
                        "summary": summary,
                        "elapsed_ms": elapsed_ms,
                    })
                except Exception as exc:
                    attempts.append({"action": "capture_after_open", "ok": False, "error": str(exc)})
                    point = None
                if not strict:
                    break

        try:
            point, summary, elapsed_ms = capture_palette_point()
            palette_was_visible = point is not None
            attempts.append({
                "action": "scan_color_palette",
                "ok": point is not None,
                "summary": summary,
                "elapsed_ms": elapsed_ms,
            })
        except Exception as exc:
            attempts.append({"action": "capture_palette", "ok": False, "error": str(exc)})
            point = None
            palette_was_visible = False
        if point is None and not strict and self._active_color:
            return optional_fallback(
                f"optional {color} swatch was not already visible; kept active {self._active_color}"
            )
        if point is None:
            opened = self._open_color_palette(
                region,
                cache_key=cache_key,
                allow_vlm=strict,
            )
            opened_data = _json_dict(opened.text)
            attempts.append({
                "action": "open_color_palette",
                "ok": not _failed(opened),
                "data": opened_data,
                "preview": opened.text[:240],
            })
            if _failed(opened):
                if not strict:
                    return optional_fallback("optional palette could not be opened; kept current visible color")
                return ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "color": color,
                    "attempts": attempts,
                    "error": "could not open a visible color palette",
                }, ensure_ascii=False, default=str))
            palette_opened = True
            scan_after_open("scan_after_open")
            if point is None and opened_data.get("cached") and strict:
                opened = self._open_color_palette(
                    region,
                    cache_key=cache_key,
                    prefer_cache=False,
                    allow_vlm=True,
                )
                opened_data = _json_dict(opened.text)
                attempts.append({
                    "action": "open_color_palette_uncached",
                    "ok": not _failed(opened),
                    "data": opened_data,
                    "preview": opened.text[:240],
                })
                if not _failed(opened):
                    scan_after_open("scan_after_uncached_open")
        if point is None and strict:
            point, label_meta = self._visible_color_label_point(region, color)
            attempts.append({
                "action": "locate_labeled_color_control",
                "ok": point is not None,
                "point": {"x": point[0], "y": point[1]} if point else None,
                **label_meta,
            })
        if point is None:
            if not strict:
                return optional_fallback(
                    f"optional {color} swatch was unavailable; kept current visible color"
                )
            return ToolResult(json.dumps({
                "ok": False,
                "verified": False,
                "color": color,
                "attempts": attempts,
                "error": f"no compact {color} swatch found in visible palette",
            }, ensure_ascii=False, default=str))
        if palette_was_visible:
            self._persistent_palette_keys.add(cache_key)
            self._color_swatch_cache[swatch_cache_key] = point
        click = self._dispatch("click", {"x": point[0], "y": point[1], "skip_screen_delta": True})
        attempts.append({
            "action": "click_color_swatch",
            "ok": not _failed(click),
            "point": {"x": point[0], "y": point[1]},
            "preview": click.text[:240],
        })
        ok = not _failed(click)
        if ok:
            self._active_color = color
        return ToolResult(json.dumps({
            "ok": ok,
            "verified": False,
            "mutation_applied": bool(ok),
            "postcondition_pending": bool(ok),
            "needs_observe": bool(ok),
            "color": color,
            "point": {"x": point[0], "y": point[1]},
            "verify_method": "candidate-color-click",
            "attempts": attempts,
        }, ensure_ascii=False, default=str))

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        started = _now_ms()
        scope_recovery = None
        if self.raw_device.supports(name):
            # The batch owns one outer ToolSession call, but foreground scope
            # can change between child actions (launches, dialogs, user input,
            # multi-window apps). Re-check every raw mutation before bypassing
            # the full ToolSession dispatch path.
            if name in BATCH_MUTATING_ACTIONS:
                from ..agent import scope_guard

                blocked = scope_guard.ensure_scope(self.session, name, args)
                scope_recovery = scope_guard.take_scope_recovery(self.session)
                if blocked is not None:
                    result = blocked
                else:
                    if scope_recovery is not None:
                        self._invalidate_after_scope_recovery()
                    result = self.raw_device.execute(name, args)
            else:
                result = self.raw_device.execute(name, args)
        else:
            result = self.session.dispatch(name, args)
        if scope_recovery is not None:
            data = _json_dict(result.text)
            if data:
                data.setdefault("scope_recovery", scope_recovery)
                result.text = json.dumps(data, ensure_ascii=False, default=str)
        elapsed = max(0, _now_ms() - started)
        result.replay = result.replay or {}
        result.replay.setdefault("elapsed_ms", elapsed)
        return result

    def _action_event_envelope(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = getattr(self.session, "snapshot", None)
        region = (
            args.get("region")
            or getattr(self.session, "_visual_frame_region", None)
            or getattr(snapshot, "region", None)
        )
        frame_id = (
            args.get("visual_frame_id")
            or args.get("frame_id")
            or getattr(self.session, "_visual_frame_id", None)
        )
        envelope = build_action_envelope(
            name,
            args,
            action_id=f"{batch_id}:{index}",
            hwnd=getattr(self.session, "hwnd", None),
            frame_id=frame_id,
            epoch=(
                getattr(self.session, "_visual_frame_epoch", None)
                if getattr(self.session, "_visual_frame_epoch", None) is not None
                else getattr(self.session, "_screen_epoch", None)
            ),
            region=region,
            retry_budget=0,
        )
        return envelope.to_dict()

    def _publish_child_start(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        envelope = self._action_event_envelope(
            batch_id, index, name, args,
        )
        BUS.publish(
            "agent_batch_action_start",
            run_id=getattr(self.session, "run_id", None),
            event_name="action.started",
            action_id=envelope["action_id"],
            action_envelope=envelope,
            parent_tool="run_action_batch",
            batch_id=batch_id,
            batch_index=index,
            tool=name,
            args=args,
            trace_screenshot=bool(
                not args.get("defer_visual_verify") or args.get("trace_checkpoint")
            ),
        )
        return envelope

    def _publish_child_done(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
        elapsed_ms: int,
        ok: bool,
        data: dict[str, Any],
        action_envelope: dict[str, Any] | None = None,
    ) -> None:
        # Start and finish must describe the same immutable target snapshot.
        # Rebuilding after execution can accidentally bind the finished event
        # to a newer screenshot/epoch.
        envelope = action_envelope or self._action_event_envelope(
            batch_id, index, name, args,
        )
        BUS.publish(
            "agent_batch_action",
            run_id=getattr(self.session, "run_id", None),
            event_name="action.finished",
            action_id=envelope["action_id"],
            action_envelope=envelope,
            parent_tool="run_action_batch",
            batch_id=batch_id,
            batch_index=index,
            tool=name,
            args=args,
            ok=ok,
            result_preview=result.text[:300],
            result_meta=_result_meta(result, data),
            elapsed_ms=elapsed_ms,
            terminal=result.terminal,
            replay=result.replay,
            trace_screenshot=bool(
                not args.get("defer_visual_verify") or args.get("trace_checkpoint")
            ),
        )

    def _click_element(self, action: dict[str, Any]) -> ToolResult:
        if self.session.snapshot is None:
            observe = self._dispatch("observe", {"full": True, "force_full": True})
            if _failed(observe):
                return observe
        snapshot = self.session.snapshot
        if snapshot is None:
            return ToolResult("No snapshot available for click_element")
        anchor_data = action.get("anchor") if isinstance(action.get("anchor"), dict) else action
        anchor = anchor_from_dict(anchor_data)
        located = self.cache.locate_with_trace(snapshot, anchor)
        element = located.element
        contextual = _dialog_context_candidate(snapshot, anchor, action)
        if contextual is not None and (element is None or contextual.elem_id != element.elem_id):
            element = contextual
            self.cache.remember(snapshot, anchor, contextual)
            located = LocateResult(
                contextual,
                score=max(0.8, float(located.score)),
                cache_hit=False,
                reason="dialog_context_disambiguated",
                candidates=located.candidates,
            )
        auto_observe: dict[str, Any] | None = None
        targeted_control_ocr: dict[str, Any] | None = None
        if element is None:
            initial_locator = located
            query = _anchor_query(anchor)
            dispatch = getattr(self.session, "dispatch", None)
            skip_auto_observe = bool(action.get("skip_auto_observe"))
            if query and callable(dispatch) and not skip_auto_observe:
                try:
                    max_elements = max(1, int(action.get("max_elements") or 160))
                except (TypeError, ValueError):
                    max_elements = 160
                observe_args = {
                    "query": query,
                    "max_elements": max_elements,
                    "actionable_query": True,
                }
                observe = self._dispatch("observe", observe_args)
                auto_observe = {
                    "used": True,
                    "query": query,
                    "ok": not _failed(observe),
                    "elapsed_ms": int((observe.replay or {}).get("elapsed_ms") or 0),
                    "preview": observe.text[:240],
                }
                if not _failed(observe):
                    snapshot = self.session.snapshot
                    if snapshot is not None:
                        located = self.cache.locate_with_trace(snapshot, anchor)
                        element = located.element
                        auto_observe["retry_anchor_locator"] = located.to_dict()
            if element is None:
                terms = _short_control_terms(anchor)
                if terms and snapshot is not None:
                    try:
                        matches = ocr.locate_control_text(snapshot.region, terms)
                    except Exception as exc:
                        matches = []
                        targeted_control_ocr = {
                            "used": True,
                            "terms": terms,
                            "match_count": 0,
                            "error": str(exc),
                        }
                    if targeted_control_ocr is None:
                        targeted_control_ocr = {
                            "used": True,
                            "terms": terms,
                            "match_count": len(matches),
                            "matches": [item.to_dict() for item in matches[:8]],
                        }
                    if matches:
                        next_id = max(
                            [int(item.elem_id) for item in snapshot.elements] or [0]
                        ) + 1
                        for match in matches:
                            match.elem_id = next_id
                            next_id += 1
                            snapshot.elements.append(match)
                        retry = self.cache.locate_with_trace(snapshot, anchor)
                        element = retry.element
                        if element is not None:
                            located = LocateResult(
                                element,
                                score=retry.score,
                                cache_hit=False,
                                reason="targeted_control_ocr",
                                candidates=retry.candidates,
                            )
            if element is None:
                payload = {
                    "ok": False,
                    "blocked": True,
                    "error": (
                        "click_element could not locate anchor after the available "
                        "snapshot"
                        + (
                            " without an extra observe retry."
                            if skip_auto_observe
                            else " and one targeted observe retry."
                        )
                    ),
                    "via": "click_element",
                    "anchor": _anchor_payload(anchor),
                    "anchor_locator": located.to_dict(),
                    "initial_anchor_locator": initial_locator.to_dict(),
                }
                if skip_auto_observe:
                    payload["skipped_auto_observe"] = True
                if auto_observe is not None:
                    payload["controller_auto_observe"] = auto_observe
                if targeted_control_ocr is not None:
                    payload["targeted_control_ocr"] = targeted_control_ocr
                return ToolResult(json.dumps(payload, ensure_ascii=False))
        servo_trace = None
        text_subregion_click = None
        servo_enabled, servo_reason = self._should_visual_servo(element, action)
        if servo_enabled:
            tracked, servo_trace = self._visual_servo_target(element, anchor, action)
            servo_trace["activation"] = servo_reason
            if tracked is None:
                return ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "error": "visual servo lost the target before mouse-down; click was not sent",
                    "via": "click_element",
                    "anchor": _anchor_payload(anchor),
                    "anchor_locator": located.to_dict(),
                    "resolved_element": element.to_dict(),
                    "visual_servo": servo_trace,
                }, ensure_ascii=False, default=str))
            element = tracked
            args = {
                "x": element.cx,
                "y": element.cy,
                "button": action.get("button", "left"),
                "double": bool(action.get("double", False)),
                "physical": True,
                "_prepositioned": True,
            }
        else:
            # Only genuine UIA elements have an invokable/stable element id.
            # OCR/CV/synthetic ids are local to a fused snapshot and may be -1;
            # resolving them again can click a different physical point.
            source = str(getattr(element, "source", "") or "").casefold()
            non_native_source = bool(source) and source not in {"uia", "win32"}
            if int(getattr(element, "elem_id", -1) or -1) < 0 or non_native_source:
                click_x, click_y, text_subregion_click = _non_native_text_click_point(
                    element,
                    anchor,
                )
                args = {
                    "x": click_x,
                    "y": click_y,
                    "button": action.get("button", "left"),
                    "double": bool(action.get("double", False)),
                    "physical": True,
                }
            else:
                args = {
                    "element_id": element.elem_id,
                    "button": action.get("button", "left"),
                    "double": bool(action.get("double", False)),
                }
            explicit_physical = action.get("physical")
            prefer_uia = bool(action.get("prefer_uia_invoke")) or explicit_physical is False
            default_physical = bool(
                getattr(self.session, "physical_anchor_clicks", False)
            )
            if element.source == "ocr-control":
                args["physical"] = True
            elif not prefer_uia and (
                explicit_physical is True
                or action.get("force_physical")
                or default_physical
            ):
                args["physical"] = True
        if isinstance(action.get("expect"), dict):
            args["expect"] = action["expect"]
        result = self._dispatch("click", args)
        data = _json_dict(result.text)
        if data:
            data.setdefault("ok", True)
            data["via"] = "click_element"
            data["anchor"] = _anchor_payload(anchor)
            data["resolved_element"] = element.to_dict()
            data["anchor_locator"] = located.to_dict()
            if servo_trace is not None:
                data["visual_servo"] = servo_trace
            if auto_observe is not None:
                auto_observe["found_after_retry"] = True
                data["controller_auto_observe"] = auto_observe
            if targeted_control_ocr is not None:
                data["targeted_control_ocr"] = targeted_control_ocr
            if text_subregion_click is not None:
                data["text_subregion_click"] = text_subregion_click
            result.text = json.dumps(data, ensure_ascii=False, default=str)
            result.replay = result.replay or {}
            result.replay.setdefault("anchor", _anchor_payload(anchor))
            result.replay.setdefault("resolved_element", element.to_dict())
        return result

    def _should_visual_servo(
        self,
        element,
        action: dict[str, Any],
    ) -> tuple[bool, str]:
        if bool(CONFIG.get(
            "controller", "multi_input_cooperation", default=False,
        )):
            # Servo owns the one physical cursor between move and mouse-down.
            # Cooperative mode instead prefers UIA/window-message clicks and
            # must not silently turn into a second cursor that Windows lacks.
            return False, "cooperative_input"
        if "visual_servo" in action:
            return _truthy(action.get("visual_servo")), "explicit"
        if _truthy(action.get("moving_target") or action.get("track_target")):
            return True, "moving_target_hint"
        mode = str(
            CONFIG.get("controller", "visual_servo_mode", default="adaptive")
            or "adaptive"
        ).casefold()
        if mode in {"off", "false", "disabled", "none"}:
            return False, "disabled"
        if mode in {"on", "true", "always"}:
            return True, "configured_always"
        try:
            status = self.watcher.status()
        except Exception:
            return False, "watcher_unavailable"
        try:
            max_age = max(50, int(CONFIG.get(
                "controller", "visual_servo_recent_change_ms", default=900,
            )))
        except (TypeError, ValueError):
            max_age = 900
        def change_age(key: str) -> int:
            raw = status.get(key)
            if raw is None:
                return 10**9
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 10**9

        age = min(
            change_age("last_change_age_ms"),
            change_age("last_action_change_age_ms"),
        )
        if age > max_age:
            return False, "target_region_stable"
        width = max(16, int(getattr(element, "width", 0) or 0))
        height = max(12, int(getattr(element, "height", 0) or 0))
        target = Region(
            int(getattr(element, "left", 0) or (int(element.cx) - width // 2)),
            int(getattr(element, "top", 0) or (int(element.cy) - height // 2)),
            width,
            height,
        )
        for raw in [
            *(status.get("action_changed_regions") or []),
            *(status.get("recent_changed_regions") or []),
        ]:
            if not isinstance(raw, dict):
                continue
            try:
                changed = Region(
                    int(raw["left"]), int(raw["top"]),
                    int(raw["width"]), int(raw["height"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            intersects = (
                target.left < changed.right
                and target.right > changed.left
                and target.top < changed.bottom
                and target.bottom > changed.top
            )
            if intersects:
                return True, "recent_target_roi_change"
        return False, "change_outside_target"

    def _visual_servo_target(self, element, anchor, action: dict[str, Any]):
        """Track and confirm a target locally immediately before mouse-down."""
        labels = [anchor.label, *anchor.fallback_text]
        labels = [str(label).strip() for label in labels if str(label).strip()]

        def move_target(x: int, y: int) -> None:
            result = self._dispatch("move_mouse", {
                "x": int(x),
                "y": int(y),
                "skip_screen_delta": True,
            })
            if _failed(result):
                raise RuntimeError(result.text[:240])

        servo_action = dict(action)
        servo_action.setdefault(
            "servo_strict",
            _truthy(CONFIG.get("controller", "visual_servo_strict", default=True)),
        )
        return track_visual_target(
            element,
            labels,
            capture_fn=self.capture_fn,
            move_fn=move_target,
            ocr_fn=ocr.recognize,
            config=ServoConfig.from_action(servo_action),
        )

    def _targeted_input_read(
        self,
        input_locator_data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Read only the located input bbox after typing, avoiding full-window truncation."""
        hwnd = getattr(self.session, "hwnd", None)
        candidate = input_locator_data.get("candidate")
        raw_bbox = candidate.get("bbox") if isinstance(candidate, dict) else None
        if not hwnd or not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            return "", {"ok": False, "reason": "missing target hwnd/input bbox"}
        left, top, width, height = (int(value) for value in raw_bbox)
        target = Region(left, top, max(1, width), max(1, height)).clamp_to_screen()
        started = _now_ms()
        texts: list[str] = []
        methods: list[str] = []

        if "uia" in CONFIG.enabled_dimensions():
            try:
                for element in uia.collect(int(hwnd)):
                    role = str(getattr(element, "role", "") or "").casefold()
                    if not any(term in role for term in ("edit", "combobox", "textbox", "input")):
                        continue
                    box = Region(
                        int(getattr(element, "left", 0) or 0),
                        int(getattr(element, "top", 0) or 0),
                        max(1, int(getattr(element, "width", 0) or 0)),
                        max(1, int(getattr(element, "height", 0) or 0)),
                    )
                    intersects = (
                        target.left < box.right and target.right > box.left
                        and target.top < box.bottom and target.bottom > box.top
                    )
                    value = str(getattr(element, "text", "") or "").strip()
                    if intersects and value:
                        texts.append(value)
                if texts:
                    methods.append("uia-input-bbox")
            except Exception:
                pass

        if not texts:
            padding_x = max(12, int(target.width * 0.04))
            padding_y = max(8, int(target.height * 0.5))
            roi = Region(
                target.left - padding_x,
                target.top - padding_y,
                target.width + padding_x * 2,
                target.height + padding_y * 2,
            ).clamp_to_screen()
            try:
                texts.extend(
                    str(element.text).strip()
                    for element in ocr.collect(roi, min_conf=0.25)
                    if str(getattr(element, "text", "") or "").strip()
                )
                if texts:
                    methods.append("ocr-input-roi")
            except Exception:
                pass

        return "\n".join(dict.fromkeys(texts)), {
            "ok": bool(texts),
            "methods": methods,
            "bbox": [target.left, target.top, target.width, target.height],
            "elapsed_ms": max(0, _now_ms() - started),
            "text_count": len(texts),
        }

    def _type_into_element(self, action: dict[str, Any]) -> ToolResult:
        text_marker = object()
        raw_text = action.get("text", action.get("text_input", action.get("value", text_marker)))
        if raw_text is text_marker:
            return ToolResult(json.dumps({
                "ok": False,
                "blocked": True,
                "error": "type_into_element requires text/text_input/value",
                "via": "type_into_element",
            }, ensure_ascii=False))
        text = str(raw_text)
        click_args = {
            k: v for k, v in action.items()
            if k not in {
                "text", "text_input", "value", "clear", "replace",
                "clear_existing", "clear_method", "expect",
                "append", "move_to_end", "verify_after_submit",
            }
        }
        internal: list[dict[str, Any]] = []

        # UIA-off/custom UI input fallback: before relying on the generic
        # text-anchor ElementCache, try an input-specific locator that can use
        # edit roles, focused state, OCR labels, and synthetic nearby boxes.
        if self.session.snapshot is None:
            observe = self._dispatch("observe", {"full": True, "force_full": True})
            internal.append({
                "action": "observe",
                "ok": not _failed(observe),
                "data": _json_dict(observe.text) or {"preview": observe.text[:240]},
            })
            if _failed(observe):
                return observe

        snapshot = self.session.snapshot
        input_locator_data: dict[str, Any] = {}
        click_data: dict[str, Any] = {}
        click_result: ToolResult | None = None
        explicit_id = action.get("element_id")
        if snapshot is not None and explicit_id is not None:
            try:
                wanted_id = int(explicit_id)
            except (TypeError, ValueError):
                wanted_id = -1
            explicit_element = next(
                (
                    element for element in snapshot.elements
                    if int(getattr(element, "elem_id", -1) or -1) == wanted_id
                ),
                None,
            )
            if explicit_element is None:
                return ToolResult(json.dumps({
                    "ok": False,
                    "blocked": True,
                    "stage": "focus",
                    "error": "type_into_element element_id is absent from the current snapshot",
                    "element_id": wanted_id,
                    "via": "type_into_element",
                }, ensure_ascii=False))
            if not _is_input_element(explicit_element, snapshot.region):
                return ToolResult(json.dumps({
                    "ok": False,
                    "blocked": True,
                    "stage": "focus",
                    "error": (
                        "type_into_element refused an element_id that is not a safe "
                        "input field; re-observe and use the actual focused/wide input"
                    ),
                    "element_id": wanted_id,
                    "resolved_element": explicit_element.to_dict(),
                    "via": "type_into_element",
                }, ensure_ascii=False))
            input_locator_data = {
                "found": True,
                "score": 1.0,
                "reason": "explicit_current_snapshot_element_id",
                "candidate": {
                    **explicit_element.to_dict(),
                    "center": [int(explicit_element.cx), int(explicit_element.cy)],
                },
                "candidates": [],
            }
            click_result = self._dispatch("click", {
                "element_id": wanted_id,
                "button": action.get("button", "left"),
                "double": bool(action.get("double", False)),
            })
            click_data = _json_dict(click_result.text)
            if click_data:
                click_data.setdefault("via", "input_locator")
                click_data["input_locator"] = input_locator_data
                click_data["resolved_element"] = explicit_element.to_dict()
                click_data["anchor_locator"] = {
                    "found": True,
                    "score": 1.0,
                    "cache_hit": False,
                    "reason": "explicit_current_snapshot_element_id",
                }
                click_result.text = json.dumps(click_data, ensure_ascii=False, default=str)
            internal.append({
                "action": "click_element",
                "via": "explicit_element_id",
                "ok": not _failed(click_result),
                "data": click_data if click_data else {"preview": click_result.text[:240]},
            })

        if snapshot is not None and click_result is None:
            anchor_data = action.get("anchor") if isinstance(action.get("anchor"), dict) else click_args
            fallback = anchor_data.get("fallback_text") or anchor_data.get("fallback") or []
            if isinstance(fallback, str):
                fallback = [fallback]
            located_input = locate_input(
                snapshot,
                label=str(anchor_data.get("label") or ""),
                expected_region=str(anchor_data.get("expected_region") or ""),
                fallback_text=tuple(str(item) for item in fallback if item),
                task=str(getattr(self.session, "task", "") or ""),
                communication_context=getattr(
                    self.session, "communication_context", None,
                ),
            )
            input_locator_data = located_input.to_dict()
            candidate = located_input.candidate if located_input.found else None
            if (
                not located_input.found
                and str(input_locator_data.get("reason") or "") == "concrete_label_not_found"
            ):
                # Native file dialogs often expose the shell search field before
                # the filename combobox. Refresh once locally after the dialog
                # settles instead of paying an LLM round trip for the same input.
                self._wait_stable({"stable_ms": 250, "timeout_s": 2.0})
                observe = self._dispatch("observe", {"full": True, "force_full": True})
                internal.append({
                    "action": "observe_retry",
                    "ok": not _failed(observe),
                    "data": _json_dict(observe.text) or {"preview": observe.text[:240]},
                })
                snapshot = self.session.snapshot
                if snapshot is not None and not _failed(observe):
                    located_input = locate_input(
                        snapshot,
                        label=str(anchor_data.get("label") or ""),
                        expected_region=str(anchor_data.get("expected_region") or ""),
                        fallback_text=tuple(str(item) for item in fallback if item),
                        task=str(getattr(self.session, "task", "") or ""),
                        communication_context=getattr(
                            self.session, "communication_context", None,
                        ),
                    )
                    input_locator_data = located_input.to_dict()
                    candidate = located_input.candidate if located_input.found else None
                if candidate is None:
                    return ToolResult(json.dumps({
                        "ok": False,
                        "blocked": True,
                        "stage": "focus",
                        "error": "type_into_element concrete input label was not visible after local retry",
                        "via": "type_into_element",
                        "input_locator": input_locator_data,
                        "subresults": internal,
                        "controller_internal_action_count": len(internal),
                    }, ensure_ascii=False, default=str))
            if candidate is not None:
                if candidate.element is not None and candidate.element.elem_id is not None and candidate.element.elem_id >= 0:
                    click_payload = {
                        "element_id": int(candidate.element.elem_id),
                        "button": action.get("button", "left"),
                        "double": bool(action.get("double", False)),
                    }
                else:
                    click_payload = {
                        "x": int(candidate.center[0]),
                        "y": int(candidate.center[1]),
                        "button": action.get("button", "left"),
                        "double": bool(action.get("double", False)),
                    }
                click_result = self._dispatch("click", click_payload)
                click_data = _json_dict(click_result.text)
                if click_data:
                    click_data.setdefault("via", "input_locator")
                    click_data["input_locator"] = input_locator_data
                    if candidate.element is not None:
                        click_data["resolved_element"] = candidate.element.to_dict()
                        click_data["anchor_locator"] = {
                            "found": True,
                            "score": input_locator_data.get("score"),
                            "cache_hit": False,
                            "reason": input_locator_data.get("reason"),
                        }
                    else:
                        click_data["resolved_element"] = candidate.to_dict()
                    click_result.text = json.dumps(click_data, ensure_ascii=False, default=str)
                internal.append({
                    "action": "click_element",
                    "via": "input_locator",
                    "ok": not _failed(click_result),
                    "data": click_data if click_data else {"preview": click_result.text[:240]},
                })

        if click_result is None or _failed(click_result):
            # Fallback to the existing visual-anchor cache path for buttons,
            # labels that are themselves editable controls, and legacy tests.
            click_result = self._click_element(click_args)
            click_data = _json_dict(click_result.text)
            if input_locator_data and click_data:
                click_data.setdefault("input_locator", input_locator_data)
                click_result.text = json.dumps(click_data, ensure_ascii=False, default=str)
            internal.append({
                "action": "click_element",
                "ok": not _failed(click_result),
                "data": click_data if click_data else {"preview": click_result.text[:240]},
            })
        if _failed(click_result):
            payload = {
                "ok": False,
                "blocked": True,
                "stage": "focus",
                "error": "type_into_element could not focus the target element",
                "via": "type_into_element",
                "subresults": internal,
                "controller_internal_action_count": len(internal),
            }
            if input_locator_data:
                payload["input_locator"] = input_locator_data
            for key in (
                "anchor", "anchor_locator", "initial_anchor_locator",
                "controller_auto_observe", "resolved_element",
            ):
                if key in click_data:
                    payload[key] = click_data[key]
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

        clear_requested = _truthy(
            action.get("clear") or action.get("replace") or action.get("clear_existing")
        ) or _needs_replace_guard(text, action)
        if clear_requested:
            clear_result = self._dispatch("hotkey", {"keys": ["ctrl", "a"]})
            clear_data = _json_dict(clear_result.text)
            internal.append({
                "action": "hotkey",
                "ok": not _failed(clear_result),
                "data": clear_data if clear_data else {"preview": clear_result.text[:240]},
            })
            if _failed(clear_result):
                payload = {
                    "ok": False,
                    "blocked": True,
                    "stage": "clear",
                    "error": "type_into_element could not select existing text",
                    "via": "type_into_element",
                    "subresults": internal,
                    "controller_internal_action_count": len(internal),
                }
                return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
            delete_result = self._dispatch("hotkey", {"keys": ["backspace"]})
            delete_data = _json_dict(delete_result.text)
            internal.append({
                "action": "hotkey",
                "ok": not _failed(delete_result),
                "data": delete_data if delete_data else {"preview": delete_result.text[:240]},
            })
            if _failed(delete_result):
                payload = {
                    "ok": False,
                    "blocked": True,
                    "stage": "clear",
                    "error": "type_into_element could not delete selected text",
                    "via": "type_into_element",
                    "subresults": internal,
                    "controller_internal_action_count": len(internal),
                }
                return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        elif _should_move_to_end_before_typing(text, action):
            move_result = self._dispatch("hotkey", {"keys": ["ctrl", "end"]})
            move_data = _json_dict(move_result.text)
            internal.append({
                "action": "hotkey",
                "purpose": "move_to_end_before_append",
                "ok": not _failed(move_result),
                "data": move_data if move_data else {"preview": move_result.text[:240]},
            })
            if _failed(move_result):
                payload = {
                    "ok": False,
                    "blocked": True,
                    "stage": "append_position",
                    "error": "type_into_element could not move caret to end before append",
                    "via": "type_into_element",
                    "subresults": internal,
                    "controller_internal_action_count": len(internal),
                }
                return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

        type_args = {"text": text}
        if isinstance(action.get("expect"), dict):
            type_args["expect"] = action["expect"]
        type_result = self._dispatch("type_text", type_args)
        type_data = _json_dict(type_result.text)
        delivery_failed = bool(
            type_result.terminal == "fail"
            or type_data.get("blocked") is True
            or type_data.get("ok") is False
        )
        postcondition_pending = bool(
            isinstance(action.get("expect"), dict)
            and type_data.get("verified") is False
        )
        type_failed = delivery_failed
        internal.append({
            "action": "type_text",
            "ok": not delivery_failed,
            "verified": bool(type_data.get("verified")) if "verified" in type_data else None,
            "data": type_data if type_data else {"preview": type_result.text[:240]},
        })
        replace_guard_error = ""
        verify_after_submit = _truthy(action.get("verify_after_submit"))
        if (
            not type_failed
            and clear_requested
            and _needs_replace_guard(text, action)
            and not verify_after_submit
        ):
            read_result = self._dispatch("read_text", {})
            read_data = _json_dict(read_result.text)
            read_preview = read_result.text[:240]
            internal.append({
                "action": "read_text",
                "ok": not _failed(read_result),
                "data": read_data if read_data else {"preview": read_preview},
            })
            verification_text = "" if _failed(read_result) else read_result.text
            replace_guard_error = _input_presence_error(verification_text, text)
            if replace_guard_error and getattr(self.session, "hwnd", None):
                targeted_text, targeted_meta = self._targeted_input_read(input_locator_data)
                internal.append({
                    "action": "targeted_input_read",
                    "ok": bool(targeted_meta.get("ok")),
                    "data": {
                        **targeted_meta,
                        "preview": targeted_text[:500],
                    },
                })
                if targeted_text:
                    verification_text = targeted_text
                    replace_guard_error = _input_presence_error(verification_text, text)
            if not replace_guard_error:
                replace_guard_error = _detect_prefixed_glob_text(verification_text, text)
            if replace_guard_error:
                type_failed = True
            elif postcondition_pending:
                postcondition_pending = False
        payload = {
            "ok": not type_failed,
            "via": "type_into_element",
            "text_length": len(text),
            "mutation_applied": not delivery_failed,
            "verify_after_submit": verify_after_submit,
            "postcondition_pending": bool(postcondition_pending and not type_failed),
            "subresults": internal,
            "controller_internal_action_count": len(internal),
            "type_result": type_data,
        }
        if isinstance(action.get("expect"), dict):
            payload["expect"] = action["expect"]
        if "verified" in type_data:
            payload["verified"] = bool(type_data.get("verified")) or bool(
                not postcondition_pending and not type_failed
            )
        if type_failed:
            payload["blocked"] = True
            payload["stage"] = "replace_guard" if replace_guard_error else "type_text"
            payload["error"] = replace_guard_error or "type_into_element text entry did not verify"
        elif postcondition_pending:
            payload["needs_observe"] = True
            payload["verify_detail"] = (
                "Text input was delivered, but the immediate postcondition was not visible yet. "
                "Continue the same controller batch with wait/read_text or observe; do not type it again."
            )
            payload["fast_path_advice"] = (
                "The literal was already delivered to the focused target. Do not repeat type_text; "
                "verify the current UI with the following read_text/observe action."
            )
        if input_locator_data:
            payload["input_locator"] = input_locator_data
        for key in (
            "anchor", "anchor_locator", "initial_anchor_locator",
            "controller_auto_observe", "resolved_element",
        ):
            if key in click_data:
                payload[key] = click_data[key]
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    def _wait_change(self, action: dict[str, Any]) -> ToolResult:
        region = self._region_from_value(action.get("region") or action.get("effect_region"))
        stop_event = getattr(self.session, "stop_event", None)
        require_persistent = _truthy(action.get("require_persistent", False))
        if region is not None:
            kwargs = {
                "timeout_s": float(action.get("timeout_s", 3.0)),
                "min_ratio": float(action.get("min_ratio", 0.0005)),
            }
            if require_persistent or stop_event is not None:
                kwargs.update({
                    "stop_event": stop_event,
                    "require_persistent": require_persistent,
                    "stable_ms": int(action.get("stable_ms", 350)),
                    "stable_max_ratio": float(action.get("stable_max_ratio", 0.0008)),
                })
            try:
                delta = self.watcher.wait_for_region_change(region, **kwargs)
            except TypeError as exc:
                # Preserve compatibility with injected legacy watcher doubles.
                # Production ScreenWatcher supports the cancellation protocol;
                # never hide an internal TypeError from that implementation.
                if isinstance(self.watcher, ScreenWatcher):
                    raise
                delta = self.watcher.wait_for_region_change(
                    region,
                    timeout_s=kwargs["timeout_s"],
                    min_ratio=kwargs["min_ratio"],
                )
        else:
            kwargs = {
                "timeout_s": float(action.get("timeout_s", 3.0)),
                "min_ratio": float(action.get("min_ratio", 0.0005)),
            }
            if stop_event is not None:
                kwargs["stop_event"] = stop_event
            try:
                delta = self.watcher.wait_for_change(**kwargs)
            except TypeError:
                if isinstance(self.watcher, ScreenWatcher):
                    raise
                delta = self.watcher.wait_for_change(
                    timeout_s=kwargs["timeout_s"],
                    min_ratio=kwargs["min_ratio"],
                )
        required = _truthy(action.get("required", True))
        canceled = bool(
            stop_event is not None
            and getattr(stop_event, "is_set", lambda: False)()
        )
        payload = {
            "ok": not canceled and (bool(delta.changed) or not required),
            "changed": delta.changed,
            "required": required,
            "needs_observe": not delta.changed,
            "delta": delta.to_dict(),
            "screen_watch": self.watcher.status(),
            "change_scope": "explicit_or_previous_action_roi" if region is not None else "bound_target_window",
            "require_persistent": require_persistent,
            "stable_after_change": bool(require_persistent and delta.changed),
            "canceled": canceled,
        }
        if canceled:
            payload["error"] = "visual change wait canceled"
        if region is not None:
            payload["region"] = self._region_payload(region)
        return ToolResult(json.dumps(payload, ensure_ascii=False))

    def _wait_for_visual_change(self, action: dict[str, Any]) -> ToolResult:
        result = self._wait_change(action)
        payload = _json_dict(result.text)
        payload["wait_kind"] = "visual_change"
        payload["semantic_completion_evidence"] = False
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    def _wait_stable(self, action: dict[str, Any]) -> ToolResult:
        region = self._region_from_value(action.get("region") or action.get("effect_region"))
        if region is not None:
            result = self.watcher.wait_for_region_stable(
                region,
                stable_ms=int(action.get("stable_ms", 500)),
                timeout_s=float(action.get("timeout_s", 5.0)),
                max_ratio=float(action.get("max_ratio", 0.0008)),
            )
        else:
            result = self.watcher.wait_for_stable(
                stable_ms=int(action.get("stable_ms", 500)),
                timeout_s=float(action.get("timeout_s", 5.0)),
                max_ratio=float(action.get("max_ratio", 0.0008)),
            )
        stable = bool(result.get("stable"))
        required = _truthy(action.get("required"))
        payload = {
            "ok": stable or not required,
            "stable": stable,
            "required": required,
            "needs_observe": not stable,
            "delta": result.get("delta", {}),
            "screen_watch": self.watcher.status(),
        }
        if region is not None:
            payload["region"] = self._region_payload(region)
        return ToolResult(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _document_editor_ready(title: str, visible_text: str) -> bool:
        compact_title = re.sub(r"\s+", "", str(title or "").casefold())
        if compact_title in {
            "文档-wpsoffice",
            "新建文档-wpsoffice",
            "新标签页-wpsoffice",
            "wpsoffice",
            "wps文字-wpsoffice",
        }:
            return False
        text = str(visible_text or "")
        chrome_hits = sum(bool(re.search(pattern, text, re.IGNORECASE)) for pattern in (
            r"(?:^|\n)(?:开始|home)(?:\n|$)",
            r"(?:^|\n)(?:插入|insert)(?:\n|$)",
            r"页面\s*[:：]?\s*\d|page\s*[:：]?\s*\d",
            r"字数\s*[:：]?\s*\d|word\s*count\s*[:：]?\s*\d",
        ))
        return chrome_hits >= 2

    def _ensure_document_surface(self, action: dict[str, Any]) -> ToolResult:
        """Keep creation/navigation local until a real editor surface is visible."""
        max_attempts = max(1, min(4, int(action.get("max_attempts") or 3)))
        evidence: list[dict[str, Any]] = []
        for attempt in range(1, max_attempts + 1):
            hwnd = int(getattr(self.session, "hwnd", 0) or 0)
            try:
                title = str(win32gui.GetWindowText(hwnd) or "") if hwnd else ""
            except Exception:
                title = ""
            read_result = self._dispatch("read_text", {})
            visible_text = str(read_result.text or "")
            ready = self._document_editor_ready(title, visible_text)
            evidence.append({
                "attempt": attempt,
                "title": title,
                "ready": ready,
                "text_preview": visible_text[:500],
            })
            if ready:
                self.session._document_surface_ready = True
                self.session._new_blank_applied = True
                return ToolResult(json.dumps({
                    "ok": True,
                    "verified": True,
                    "document_surface_ready": True,
                    "attempts": attempt,
                    "evidence": evidence,
                }, ensure_ascii=False, default=str))
            compact_title = re.sub(r"\s+", "", title.casefold())
            at_application_home = compact_title in {
                "文档-wpsoffice",
                "wpsoffice",
                "wps文字-wpsoffice",
            }
            create_result = None
            create_via = "ctrl+n"
            snapshot = getattr(self.session, "snapshot", None)
            region = getattr(snapshot, "region", None)
            creation_hub = all(
                re.search(rf"(?:^|\n){term}(?:\n|$)", visible_text, re.IGNORECASE)
                for term in ("文字", "演示", "表格")
            )
            if creation_hub:
                text_document_candidates = [
                    element
                    for element in (getattr(snapshot, "elements", []) or [])
                    if re.sub(r"\s+", "", str(getattr(element, "text", "") or "")) == "文字"
                ] if snapshot is not None else []
                if text_document_candidates:
                    element = max(
                        text_document_candidates,
                        key=lambda item: int(getattr(item, "width", 0) or 0) * int(getattr(item, "height", 0) or 0),
                    )
                    create_via = "grounded-document-type"
                    create_result = self._dispatch("click", {
                        "x": int(getattr(element, "cx", 0) or 0),
                        "y": int(getattr(element, "cy", 0) or 0),
                        "button": "left",
                        "physical": True,
                    })
                else:
                    create_via = "semantic-document-type"
                    create_result = self._click_element({
                        "label": "文字",
                        "fallback_text": ["Document", "Writer", "Office文档"],
                        "expected_region": "top-left",
                    })
            elif at_application_home and re.search(r"(?:^|\n)(?:\+|＋|十)?\s*(?:新建|new)(?:\n|$)", visible_text, re.IGNORECASE):
                region = getattr(snapshot, "region", None)
                wide_left_new = []
                if snapshot is not None and region is not None:
                    for element in getattr(snapshot, "elements", []) or []:
                        label = re.sub(r"\s+", "", str(getattr(element, "text", "") or "").casefold())
                        if not re.search(r"(?:新建|new)", label, re.IGNORECASE):
                            continue
                        if int(getattr(element, "cx", 0) or 0) > region.left + int(region.width * 0.40):
                            continue
                        if int(getattr(element, "cy", 0) or 0) < region.top + int(region.height * 0.07):
                            continue
                        width = int(getattr(element, "width", 0) or 0)
                        if width >= 80:
                            wide_left_new.append((width, element))
                if wide_left_new:
                    _width, element = max(wide_left_new, key=lambda item: item[0])
                    create_via = "grounded-wide-left-new"
                    create_result = self._dispatch("click", {
                        "x": int(getattr(element, "cx", 0) or 0),
                        "y": int(getattr(element, "cy", 0) or 0),
                        "button": "left",
                        "physical": True,
                    })
                else:
                    create_via = "semantic-new-button"
                    create_result = self._click_element({
                        "label": "新建",
                        "fallback_text": ["+ 新建", "＋ 新建", "New"],
                        "role": "button",
                        "expected_region": "left-center",
                    })
            if create_result is None or _failed(create_result):
                create_via = "ctrl+n"
                create_result = self._dispatch("hotkey", {"keys": ["ctrl", "n"]})
            evidence[-1]["create_via"] = create_via
            if _failed(create_result):
                break
            self._wait_change({
                "timeout_s": 5.0,
                "min_ratio": 0.0002,
                "required": False,
            })
            self._wait_stable({"stable_ms": 300, "timeout_s": 5.0})
        self.session._document_surface_ready = False
        return ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "document_surface_ready": False,
            "error": "No editable document surface appeared after local creation retries.",
            "evidence": evidence,
        }, ensure_ascii=False, default=str))

    @staticmethod
    def _pause(action: dict[str, Any]) -> ToolResult:
        duration_s: float | None = None
        if isinstance(action.get("duration_s"), (int, float)):
            duration_s = float(action["duration_s"])
        elif isinstance(action.get("seconds"), (int, float)):
            duration_s = float(action["seconds"])
        elif isinstance(action.get("duration_ms"), (int, float)):
            duration_s = float(action["duration_ms"]) / 1000.0
        elif isinstance(action.get("ms"), (int, float)):
            duration_s = float(action["ms"]) / 1000.0
        elif isinstance(action.get("value"), (int, float)):
            # Shorthand {"wait": 500} means 500ms inside action batches.
            duration_s = float(action["value"]) / 1000.0
        elif isinstance(action.get("time"), (int, float)):
            # Common model shorthand: {"action":"wait", "time":1.0}.
            duration_s = float(action["time"])
        if duration_s is None:
            return ToolResult(json.dumps({
                "ok": False,
                "error": "wait in run_action_batch requires condition or duration_ms/ms/value",
            }, ensure_ascii=False))
        duration_s = max(0.0, min(duration_s, 5.0))
        time.sleep(duration_s)
        return ToolResult(json.dumps({
            "ok": True,
            "waited_ms": int(duration_s * 1000),
        }, ensure_ascii=False))

    def _sync_after_batch(self, sync: dict[str, Any] | bool) -> tuple[str, ToolResult]:
        if isinstance(sync, dict):
            mode = str(sync.get("mode") or "stable").casefold()
            args = dict(sync)
        else:
            mode = "stable"
            args = {}
        if mode == "change":
            return "sync_change", self._wait_change(args)
        return "sync_stable", self._wait_stable({
            "stable_ms": args.get("stable_ms", 250),
            "timeout_s": args.get("timeout_s", 2.0),
            "max_ratio": args.get("max_ratio", 0.0008),
        })

    @staticmethod
    def _coords_region(coords: list[tuple[int, int]], pad: int = 40) -> Region | None:
        if not coords:
            return None
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        return Region(
            min(xs) - pad,
            min(ys) - pad,
            max(1, max(xs) - min(xs) + 2 * pad),
            max(1, max(ys) - min(ys) + 2 * pad),
        ).clamp_to_screen()

    @staticmethod
    def _region_payload(region: Region) -> dict[str, int]:
        return {
            "x": int(region.left), "y": int(region.top),
            "width": int(region.width), "height": int(region.height),
        }

    @staticmethod
    def _region_from_value(value: Any) -> Region | None:
        if not isinstance(value, dict):
            return None
        try:
            left = int(value.get("x", value.get("left")))
            top = int(value.get("y", value.get("top")))
            width = int(value.get("width"))
            height = int(value.get("height"))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return Region(left, top, width, height).clamp_to_screen()

    def _map_canvas_relative_draw(self, args: dict[str, Any]) -> dict[str, Any]:
        # Resolve at the concrete input boundary so direct tools and controller
        # batches share the same strict contract. In particular, never trust a
        # model-provided canvas_region or the controller's historical
        # _last_canvas_region: the interaction resolver captures current target
        # pixels before the first path and binds its cache to hwnd + geometry.
        resolver = getattr(self.session, "_prepare_draw_strokes_args", None)
        if not callable(resolver):
            return args
        return resolver(dict(args), capture_fn=self.capture_fn)

    def _invalidate_after_scope_recovery(self) -> None:
        """Discard state that may describe the temporarily foreground app.

        The next normalized draw mapping will consequently capture current
        target pixels and bind the work surface again before the uncommitted
        primitive is dispatched.  Completed primitives are not replayed.
        """

        for attr in ("snapshot", "_observe_cache", "_draw_surface_cache"):
            try:
                setattr(self.session, attr, None)
            except Exception:
                pass

    @staticmethod
    def _action_region(name: str, args: dict[str, Any]) -> Region | None:
        explicit = FastGuiController._region_from_value(
            args.get("effect_region") or args.get("region")
        )
        if explicit is not None:
            return explicit
        coords: list[tuple[int, int]] = []
        try:
            if name in {"click", "scroll", "move_mouse"}:
                return FastGuiController._coords_region(
                    [(int(args["x"]), int(args["y"]))], pad=48,
                )
            if name == "draw_strokes":
                for stroke in args.get("strokes") or []:
                    for point in stroke or []:
                        coords.append((int(point[0]), int(point[1])))
                return FastGuiController._coords_region(coords, pad=48)
            if name == "drag":
                coords.append((int(args["x1"]), int(args["y1"])))
                coords.append((int(args["x2"]), int(args["y2"])))
                for point in args.get("points") or []:
                    coords.append((int(point[0]), int(point[1])))
                return FastGuiController._coords_region(coords, pad=56)
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        return None

    @staticmethod
    def _action_region_from_result(data: dict[str, Any]) -> Region | None:
        point = data.get("point")
        if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
            try:
                return FastGuiController._coords_region(
                    [(int(point["x"]), int(point["y"]))], pad=48,
                )
            except (TypeError, ValueError):
                pass
        resolved = data.get("resolved_element")
        bbox = resolved.get("bbox") if isinstance(resolved, dict) else None
        if isinstance(bbox, dict):
            return FastGuiController._region_from_value(bbox)
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                left, top, third, fourth = [int(value) for value in bbox]
                # Perception bboxes are commonly l/t/r/b.  If the final pair
                # cannot be edges, treat them as width/height.
                width = third - left if third > left else third
                height = fourth - top if fourth > top else fourth
                if width > 0 and height > 0:
                    return Region(left, top, width, height).clamp_to_screen()
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _compact_image_summary(image) -> dict[str, Any]:
        ink = vision_cv.image_ink_stats_from_array(image)
        colors = vision_cv.color_presence(image).get("colors", {})
        visible_colors = {
            name: {
                key: value
                for key, value in stats.items()
                if key in {"pixels", "ratio", "bbox", "largest_bbox", "largest_area"}
            }
            for name, stats in colors.items()
            if isinstance(stats, dict) and stats.get("visible")
        }
        return {
            "width": ink.get("width"),
            "height": ink.get("height"),
            "blankish": ink.get("blankish"),
            "ink_ratio": ink.get("ink_ratio"),
            "ink_bbox": ink.get("ink_bbox"),
            "component_count": ink.get("component_count"),
            "visible_colors": visible_colors,
        }

    def _auto_visual_verify(
        self,
        region: Region,
        source_action: str,
        source_data: dict[str, Any],
    ) -> ToolResult:
        """Summarise local pixel effects; never infer semantic completion.

        The historical action name is kept in traces for compatibility, but an
        after-frame ink/color summary is not a goal-conditioned verifier.  It
        may confirm that an already verified source action had an effect; it
        cannot turn arbitrary pixel change into a satisfied postcondition.
        """
        started = _now_ms()
        payload: dict[str, Any] = {
            "ok": True,
            "source_action": source_action,
            "region": {
                "x": region.left,
                "y": region.top,
                "width": region.width,
                "height": region.height,
            },
        }
        try:
            image = self.capture_fn(region)
            payload["nonvisual_summary"] = self._compact_image_summary(image)
            if "visual_change_detected" in source_data:
                payload["visual_change_detected"] = bool(source_data.get("visual_change_detected"))
            if "visual_change_ratio" in source_data:
                payload["visual_change_ratio"] = source_data.get("visual_change_ratio")
            if source_data.get("verified") is True:
                payload["verified"] = True
                payload["verify_method"] = "source-verification+pixel-effect-summary"
            else:
                payload["verified"] = False
            payload["effect_observed"] = bool(
                source_data.get("visual_change_detected") is True
            )
            payload["postcondition_satisfied"] = bool(
                source_data.get("verified") is True
            )
            payload["semantic_verified"] = False
            payload["completion_evidence"] = False
            payload["summary_kind"] = "pixel_effect_only"
        except Exception as exc:
            payload = {
                "ok": False,
                "source_action": source_action,
                "error": f"auto visual verify capture failed: {exc}",
            }
        payload["elapsed_ms"] = max(0, _now_ms() - started)
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    def execute(
        self,
        actions: list[dict[str, Any]],
        stop_on_error: bool = True,
        sync: dict[str, Any] | bool | None = None,
        subgoal: str = "",
    ) -> dict[str, Any]:
        actions = _stable_batch_actions(actions, subgoal)
        stale_buttons = action_executor.release_held_mouse_buttons()
        if stale_buttons:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "failure_type": "STALE_CONTINUOUS_INPUT_RECOVERED",
                "input_cleanup": {
                    "released_mouse_buttons": stale_buttons,
                    "reason": "button hold cannot cross controller transactions",
                },
                "error": (
                    "A mouse button from an earlier transaction was still held. "
                    "It was released safely; re-observe before retrying."
                ),
            }
        validation_errors = validate_batch_actions(actions)
        if validation_errors:
            return {
                "ok": False,
                "verified": False,
                "blocked": True,
                "failure_type": "INVALID_ACTION_TRANSACTION",
                "metrics": {
                    "failed_at": 1,
                    "action_count": len(actions),
                    "validation_failed": True,
                },
                "results": [{
                    "index": 1,
                    "ok": False,
                    "preview": validation_errors[0],
                    "error": validation_errors[0],
                }],
                "validation_errors": validation_errors,
                "error": "; ".join(validation_errors),
            }
        has_mutation = False
        has_clipboard = False
        for action in actions:
            if not isinstance(action, dict):
                continue
            name, args = _normalise_action(action)
            name, _args, _upgraded_from = _upgrade_semantic_action(name, args)
            if name in BATCH_MUTATING_ACTIONS:
                has_mutation = True
            if name in BATCH_CLIPBOARD_ACTIONS:
                has_clipboard = True
        def execute() -> dict[str, Any]:
            payload: dict[str, Any] | None = None
            released: list[str] = []
            try:
                payload = self._execute_in_lease(
                    actions,
                    stop_on_error=stop_on_error,
                    sync=sync,
                    subgoal=subgoal,
                )
                return payload
            finally:
                released = action_executor.release_held_mouse_buttons()
                if released:
                    BUS.publish(
                        "input_cleanup",
                        run_id=getattr(self.session, "run_id", None),
                        released_mouse_buttons=released,
                        reason="controller_transaction_exit",
                    )
                    if payload is not None:
                        payload["ok"] = False
                        payload["verified"] = False
                        payload["failure_type"] = "CONTINUOUS_INPUT_ABORTED"
                        payload["input_cleanup"] = {
                            "released_mouse_buttons": released,
                            "reason": "controller transaction exited while input was held",
                        }
                        payload["error"] = (
                            "The continuous pointer transaction ended before its "
                            "matching mouse_up; the Controller released it safely."
                        )
                        state = payload.get("controller_state")
                        if isinstance(state, dict):
                            state.update({
                                "phase": "blocked",
                                "verified": False,
                                "needs_observe": True,
                            })
        if not has_mutation and not has_clipboard:
            return execute()
        target = getattr(self.session, "hwnd", None)
        scope = (
            "physical_clipboard"
            if has_mutation and has_clipboard
            else "physical"
            if has_mutation
            else "clipboard"
        )
        return DESKTOP_ACTION_QUEUE.run(
            "controller_batch",
            execute,
            target_hwnd=int(target) if target else None,
            scope=scope,
            metadata={"subgoal": str(subgoal or ""), "action_count": len(actions)},
            coalesce_nested=True,
        )

    def _execute_in_lease(
        self,
        actions: list[dict[str, Any]],
        stop_on_error: bool = True,
        sync: dict[str, Any] | bool | None = None,
        subgoal: str = "",
    ) -> dict[str, Any]:
        metrics = BatchMetrics(started_ms=_now_ms())
        batch_id = f"batch-{metrics.started_ms}"
        results: list[dict[str, Any]] = []
        verified_any = False
        current_state_verified = False
        verified_expect_any = False
        verified_expect_false_any = False
        auto_verified_visual = False
        last_action = ""
        mutating_seen = False
        last_visual_region: Region | None = None
        last_visual_action = ""
        last_visual_data: dict[str, Any] = {}
        semantic_observe_after_visual = False
        typed_literals: list[str] = []
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": "action must be an object"})
                break
            name, normalised_args = _normalise_action(action)
            name, normalised_args, upgraded_from = _upgrade_semantic_action(name, normalised_args)
            normalised_args = _with_batch_expect_defaults(name, normalised_args)
            if (
                name in {"wait_change", "wait_for_visual_change", "wait_stable"}
                and normalised_args.get("region") is None
                and normalised_args.get("effect_region") is None
                and last_visual_region is not None
            ):
                # Bind an immediately following wait to the action that caused
                # it, rather than unrelated animation elsewhere in the target
                # window. Callers can supply a broader interaction ROI when
                # waiting for a human/opponent response.
                normalised_args["region"] = self._region_payload(last_visual_region)
                normalised_args["region_source"] = "previous_action_roi"
            optional_action = bool(normalised_args.pop("optional", False))
            prior_mutation_seen = mutating_seen
            last_action = name
            if name in BATCH_MUTATING_ACTIONS:
                mutating_seen = True
            if name not in ALLOWED_BATCH_ACTIONS:
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": f"batch action not allowed: {name}"})
                if stop_on_error:
                    break
                continue
            # Scope guard every mutation before any controller-local work.
            # Some actions such as select_color perform capture/recognition
            # before their eventual raw click; guarding only RawDeviceExecutor
            # let those actions inspect an occluding foreground window.
            scope_blocked = None
            scope_recovery = None
            if name in BATCH_MUTATING_ACTIONS:
                from ..agent import scope_guard

                scope_blocked = scope_guard.ensure_scope(self.session, name, normalised_args)
                scope_recovery = scope_guard.take_scope_recovery(self.session)
                if scope_recovery is not None:
                    metrics.scope_recovery_count += 1
                    self._invalidate_after_scope_recovery()
            if name == "draw_strokes" and scope_blocked is None:
                # Map only after foreground ownership has been proved.  A
                # successful refocus invalidates the surface cache above, so
                # this binds the uncommitted primitive from fresh target pixels.
                normalised_args = self._map_canvas_relative_draw(normalised_args)
            force_semantic_rebind = bool(normalised_args.pop("_rebind_after_mutation", False))
            if (
                prior_mutation_seen
                and force_semantic_rebind
                and scope_blocked is None
                and name in {"click_element", "type_into_element"}
            ):
                # A semantic child after a mutation is safe only if it is
                # rebound from current target pixels/UIA.  Discard the prior
                # snapshot locally; _click_element/_type_into_element will
                # perform a deterministic observe without an LLM round trip.
                try:
                    self.session.snapshot = None
                    self.session._observe_cache = None
                except Exception:
                    pass
            epoch_before = int(getattr(self.session, "_screen_epoch", 0) or 0)
            started = _now_ms()
            action_envelope = self._publish_child_start(
                batch_id, index, name, normalised_args,
            )
            condition = normalised_args.get("condition")
            text_appears = (
                str(condition.get("text_appears") or "").strip()
                if name == "wait" and isinstance(condition, dict)
                else ""
            )
            self_fulfilling_wait = bool(
                text_appears
                and any(text_appears in literal for literal in typed_literals)
            )
            if scope_blocked is not None:
                result = scope_blocked
            elif self_fulfilling_wait:
                result = ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "error": (
                        "wait text_appears is self-fulfilling because the same marker "
                        "was typed earlier in this batch. Verify a distinct result, "
                        "state change, file/process condition, or a scoped output region."
                    ),
                    "text_appears": text_appears,
                }, ensure_ascii=False))
            elif name == "click_element":
                result = self._click_element(normalised_args)
            elif name == "type_into_element":
                result = self._type_into_element(normalised_args)
            elif name == "select_color":
                result = self._select_color(normalised_args)
            elif name == "wait" and not isinstance(normalised_args.get("condition"), dict):
                result = self._pause(normalised_args)
            elif name == "wait_change":
                result = self._wait_change(normalised_args)
            elif name == "wait_for_visual_change":
                result = self._wait_for_visual_change(normalised_args)
            elif name == "wait_stable":
                result = self._wait_stable(normalised_args)
            elif name == "ensure_document_surface":
                result = self._ensure_document_surface(normalised_args)
            else:
                result = self._dispatch(name, normalised_args)
            if name in {"type_text", "type_into_element"} and not _failed(result):
                typed = str(normalised_args.get("text") or "")
                if typed:
                    typed_literals.append(typed)
            elapsed = max(0, _now_ms() - started)
            metrics.tool_latencies_ms.append(elapsed)
            metrics.action_count += 1
            if name in BATCH_OBSERVE_ACTIONS:
                metrics.observe_count += 1
            if isinstance(normalised_args.get("expect"), dict) or name == "wait":
                metrics.verify_count += 1
            if name in {"wait_change", "wait_for_visual_change", "wait_stable"}:
                metrics.screen_wait_count += 1
            item = {
                "index": index,
                "action": name,
                "elapsed_ms": elapsed,
                "terminal": result.terminal,
                "preview": result.text[:800],
            }
            if scope_recovery is not None:
                item["scope_recovery"] = scope_recovery
            if name in BATCH_OBSERVE_ACTIONS and len(result.text or "") > 800:
                item["full_text"] = result.text
            if upgraded_from:
                item["upgraded_from"] = upgraded_from
            data = _json_dict(result.text)
            nested_scope_recovery = data.get("scope_recovery")
            if scope_recovery is None and isinstance(nested_scope_recovery, dict):
                scope_recovery = dict(nested_scope_recovery)
                metrics.scope_recovery_count += 1
                item["scope_recovery"] = scope_recovery
            post_state = data.get("post_state") if isinstance(data.get("post_state"), dict) else {}
            try:
                foreground_hwnd = int(post_state.get("foreground_hwnd") or 0)
                prior_hwnd = int(getattr(self.session, "hwnd", 0) or 0)
            except (TypeError, ValueError):
                foreground_hwnd = 0
                prior_hwnd = 0
            if foreground_hwnd and foreground_hwnd != prior_hwnd:
                related = False
                relation_check = getattr(self.session, "_window_related_to_target", None)
                if callable(relation_check) and prior_hwnd:
                    try:
                        related = bool(relation_check(foreground_hwnd, prior_hwnd))
                    except Exception:
                        related = False
                if not related and prior_hwnd:
                    try:
                        related = win32gui.GetWindow(foreground_hwnd, 4) == prior_hwnd
                    except Exception:
                        related = False
                if (
                    not related
                    and prior_hwnd
                    and name == "hotkey"
                    and [str(key).casefold() for key in normalised_args.get("keys") or []] == ["ctrl", "n"]
                    and not bool(CONFIG.get(
                        "controller", "multi_input_cooperation", default=False,
                    ))
                ):
                    # New-document shortcuts in shared-host Office apps create
                    # a sibling top-level window rather than an owned modal.
                    # Scope this exception to Ctrl+N so unrelated same-process
                    # chat/app instances remain isolated.
                    related = _same_window_process(foreground_hwnd, prior_hwnd)
                if related:
                    remember_target = getattr(self.session, "_remember_target", None)
                    if callable(remember_target):
                        try:
                            remember_target(foreground_hwnd, str(post_state.get("foreground_title") or ""))
                        except Exception:
                            setattr(self.session, "hwnd", foreground_hwnd)
                    else:
                        setattr(self.session, "hwnd", foreground_hwnd)
                    try:
                        self.session.snapshot = None
                    except Exception:
                        pass
                    data["controller_adopted_foreground_hwnd"] = foreground_hwnd
                    data["controller_previous_hwnd"] = prior_hwnd
                    result.text = json.dumps(data, ensure_ascii=False, default=str)
            has_expect = isinstance(normalised_args.get("expect"), dict)
            query_required = bool(normalised_args.get("require_match"))
            query_matches = []
            if query_required and name in BATCH_OBSERVE_ACTIONS:
                query = str(normalised_args.get("query") or normalised_args.get("text") or "")
                query_matches = _grounded_query_matches(getattr(self.session, "snapshot", None), query)
                item["query"] = query
                item["query_match"] = bool(query_matches)
                item["query_match_count"] = len(query_matches)
            screen_delta = data.get("screen_delta") if isinstance(data.get("screen_delta"), dict) else {}
            transient_required = bool(normalised_args.get("require_transient"))
            screen_change_required = bool(normalised_args.get("require_screen_change")) or transient_required
            evidence_requirement_failed = (
                (query_required and not query_matches)
                or (screen_change_required and not bool(screen_delta.get("changed")))
                or (transient_required and not bool(screen_delta.get("transient_observed")))
            )
            evidence_requirement_verified = not evidence_requirement_failed and (
                (query_required and query_matches)
                or (screen_change_required and bool(screen_delta.get("changed")))
            )
            if evidence_requirement_verified:
                current_state_verified = True
                verified_any = True
                item["verified"] = True
            if data.get("verified") is True:
                verified_any = True
                current_state_verified = True
                if has_expect:
                    verified_expect_any = True
            elif data.get("verified") is False:
                if name in BATCH_MUTATING_ACTIONS and (has_expect or not evidence_requirement_verified):
                    current_state_verified = False
                if has_expect and not (
                    data.get("mutation_applied") is True
                    and data.get("postcondition_pending") is True
                ):
                    verified_expect_false_any = True
            elif name in BATCH_MUTATING_ACTIONS:
                current_state_verified = False
            plain_error = (
                not data
                and not _is_json_object(result.text)
                and name not in BATCH_OBSERVE_ACTIONS
            )
            deferred_postcondition = bool(
                has_expect
                and data.get("verified") is False
                and data.get("mutation_applied") is True
                and data.get("postcondition_pending") is True
            )
            visual_no_progress = bool(
                name in {"draw_strokes", "drag"}
                and data.get("visual_change_detected") is False
                and not data.get("visual_verify_deferred")
            )
            failed = _failed(result) or plain_error or (
                has_expect
                and data.get("verified") is False
                and not deferred_postcondition
            ) or evidence_requirement_failed or visual_no_progress
            if visual_no_progress and not data.get("error"):
                data["error"] = "visual action produced no pixel change in its intended region"
                if name == "draw_strokes":
                    data["failure_type"] = "DRAW_NO_VISIBLE_CHANGE"
                    data["failure_subtype"] = "effect_not_observed"
                else:
                    # A drag is not necessarily drawing.  Classifying every
                    # no-effect drag as a canvas/tool failure sent recovery
                    # down an application-specific path and repeated the same
                    # bad geometry.  Preserve the drawing code for the
                    # dedicated drawing primitive, but keep generic spatial
                    # interaction failures application-neutral.
                    data["failure_type"] = "VISUAL_ACTION_NO_EFFECT"
                    data["failure_subtype"] = "source_or_target_not_confirmed"
            if failed and optional_action:
                item["optional_failed"] = True
                failed = False
            item["ok"] = not failed
            if name in BATCH_MUTATING_ACTIONS and item["ok"]:
                # Raw-device child actions intentionally bypass the outer
                # ToolSession dispatch.  Invalidate its perception cache here
                # so a later read/observe in the same batch cannot reuse the
                # pre-action UIA/OCR snapshot.  Preserve session.snapshot for
                # stable anchor coordinates between adjacent fast clicks.
                current_epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
                if current_epoch <= epoch_before:
                    setattr(self.session, "_screen_epoch", epoch_before + 1)
                setattr(self.session, "_observe_cache", None)
            if evidence_requirement_failed:
                item["error"] = "required controller evidence was not observed"
            elif visual_no_progress:
                item["error"] = data["error"]
            if "verified" in data:
                item["verified"] = evidence_requirement_verified or bool(data.get("verified"))
            if data:
                item["data"] = data
                auto_observe = data.get("controller_auto_observe")
                if isinstance(auto_observe, dict) and auto_observe.get("used"):
                    metrics.observe_count += 1
                    metrics.auto_observe_count += 1
                internal_count = data.get("controller_internal_action_count")
                if isinstance(internal_count, int) and internal_count > 0:
                    metrics.internal_action_count += internal_count
                    metrics.compound_round_trips_saved += max(0, internal_count - 1)
                servo = data.get("visual_servo")
                if isinstance(servo, dict) and servo.get("enabled"):
                    metrics.visual_servo_count += 1
                    metrics.visual_servo_correction_count += max(
                        0, int(servo.get("correction_count") or 0),
                    )
                    if not servo.get("confirmed"):
                        metrics.visual_servo_failure_count += 1
                    metrics.visual_servo_latency_ms.append(
                        max(0, int(servo.get("elapsed_ms") or 0)),
                    )
            results.append(item)
            if name in BATCH_OBSERVE_ACTIONS:
                last_visual_region = None
                last_visual_action = ""
                last_visual_data = {}
                semantic_observe_after_visual = True
            elif name in BATCH_MUTATING_ACTIONS:
                region = (
                    self._action_region(name, normalised_args)
                    or self._action_region_from_result(data)
                )
                if region is not None:
                    last_visual_region = region
                    last_visual_action = name
                    last_visual_data = data
                    semantic_observe_after_visual = False
                else:
                    last_visual_region = None
                    last_visual_action = ""
                    last_visual_data = {}
                    semantic_observe_after_visual = False
            self._publish_child_done(
                batch_id,
                index,
                name,
                normalised_args,
                result,
                elapsed,
                item["ok"],
                data,
                action_envelope,
            )
            if failed and stop_on_error:
                metrics.failed_at = index
                break
        if (
            metrics.failed_at is None
            and last_visual_region is not None
            and not semantic_observe_after_visual
        ):
            index = len(results) + 1
            started = _now_ms()
            name = "auto_visual_verify"
            args = {
                "source_action": last_visual_action,
                "region": {
                    "x": last_visual_region.left,
                    "y": last_visual_region.top,
                    "width": last_visual_region.width,
                    "height": last_visual_region.height,
                },
            }
            action_envelope = self._publish_child_start(batch_id, index, name, args)
            result = self._auto_visual_verify(last_visual_region, last_visual_action, last_visual_data)
            elapsed = max(0, _now_ms() - started)
            data = _json_dict(result.text)
            ok = not _failed(result)
            metrics.tool_latencies_ms.append(elapsed)
            metrics.observe_count += 1
            metrics.verify_count += 1
            results.append({
                "index": index,
                "action": name,
                "elapsed_ms": elapsed,
                "terminal": result.terminal,
                "preview": result.text[:800],
                "ok": ok,
                "verified": bool(data.get("verified")) if "verified" in data else False,
                "data": data,
            })
            self._publish_child_done(
                batch_id, index, name, args, result, elapsed, ok, data,
                action_envelope,
            )
            if data.get("verified") is True:
                verified_any = True
                current_state_verified = True
                auto_verified_visual = True
        sync_result: dict[str, Any] = {}
        if metrics.failed_at is None and sync and mutating_seen:
            sync_required = bool(sync.get("required")) if isinstance(sync, dict) else False
            started = _now_ms()
            sync_name, result = self._sync_after_batch(sync)
            elapsed = max(0, _now_ms() - started)
            ok = not _failed(result)
            metrics.tool_latencies_ms.append(elapsed)
            metrics.verify_count += 1
            metrics.screen_wait_count += 1
            sync_result = {
                "action": sync_name,
                "ok": ok,
                "required": sync_required,
                "elapsed_ms": elapsed,
            }
            results.append({
                "index": len(results) + 1,
                "action": sync_name,
                "elapsed_ms": elapsed,
                "terminal": result.terminal,
                "preview": result.text[:800],
                "ok": ok or not sync_required,
            })
            if sync_required and not ok:
                metrics.failed_at = len(results)
        metrics.elapsed_ms = max(0, _now_ms() - metrics.started_ms)
        metrics.controller_round_trips_saved = (
            max(0, metrics.action_count - 1)
            + metrics.auto_observe_count
            + metrics.compound_round_trips_saved
        )
        needs_observe = (
            metrics.failed_at is None
            and metrics.action_count > 0
            and not current_state_verified
            and last_action not in BATCH_OBSERVE_ACTIONS
        )
        if metrics.failed_at is not None:
            phase = "blocked"
        elif current_state_verified:
            phase = "verified"
        elif needs_observe:
            phase = "needs_observe"
        else:
            phase = "executed"
        payload = {
            "ok": metrics.failed_at is None,
            "subgoal": str(subgoal or ""),
            "controller_state": {
                "phase": phase,
                "verified": bool(current_state_verified and metrics.failed_at is None),
                "needs_observe": bool(needs_observe),
                "failed_at": metrics.failed_at,
                "last_action": last_action,
            },
            "results": results,
            "metrics": metrics.to_dict(),
            "element_cache": self.cache.stats(),
            "last_action": last_action,
            "sync": sync_result,
            "needs_observe": needs_observe,
            "transaction": {
                "planned_action_count": len(actions),
                "attempted_action_count": min(len(actions), len(results)),
                "committed_action_count": sum(
                    1
                    for item in results[:len(actions)]
                    if isinstance(item, dict) and item.get("ok") is True
                ),
                "next_uncommitted_action_index": metrics.failed_at,
                "remaining_action_count": (
                    max(0, len(actions) - int(metrics.failed_at) + 1)
                    if metrics.failed_at is not None else 0
                ),
                "scope_recovery_count": metrics.scope_recovery_count,
            },
        }
        if current_state_verified and metrics.failed_at is None:
            payload["verified"] = True
        if auto_verified_visual and metrics.failed_at is None:
            payload["fast_path_advice"] = (
                "The controller already verified the visible local change for "
                "the last draw/drag action. For a simple visible manipulation "
                "task, call done instead of adding another zoom/observe round "
                "unless the user explicitly needs semantic inspection."
            )
        if verified_expect_any or verified_expect_false_any:
            payload["verified"] = bool(
                verified_expect_any
                and not verified_expect_false_any
                and metrics.failed_at is None
            )
        return payload
