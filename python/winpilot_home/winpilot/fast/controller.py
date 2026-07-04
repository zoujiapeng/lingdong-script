"""Fast GUI controller: execute short grounded action batches."""
from __future__ import annotations

import json
import time
from typing import Any

import cv2
import numpy as np

from ..agent.tool_result import ToolResult
from ..perception import ocr, vision_cv
from ..utils.log import BUS
from ..utils.screenshot import Region, capture, window_region
from .element_cache import ElementCache, anchor_from_dict
from .input_locator import locate_input
from .types import BatchMetrics
from .watcher import ScreenWatcher

ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "wait",
    "wait_change",
    "wait_stable",
    "read_text",
    "zoom",
    "clipboard_write",
    "clipboard_paste",
}
BATCH_MUTATING_ACTIONS = {
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "clipboard_paste",
}
BATCH_OBSERVE_ACTIONS = {
    "observe",
    "read_text",
    "vlm_describe",
    "zoom",
    "auto_visual_verify",
}
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


def _json_dict(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_json_object(text: str) -> bool:
    return (text or "").lstrip().startswith("{")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _norm_text(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _color_mask(image: np.ndarray, color: str) -> np.ndarray | None:
    if image is None or getattr(image, "size", 0) == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    chroma = (sat > 70) & (val > 50)
    color = str(color or "").casefold()
    masks = {
        "red": chroma & ((hue <= 10) | (hue >= 170)),
        "orange": chroma & (hue > 10) & (hue <= 24),
        "yellow": chroma & (hue > 24) & (hue <= 38),
        "green": chroma & (hue > 38) & (hue <= 88),
        "blue": chroma & (hue >= 95) & (hue <= 135),
        "purple": chroma & (hue > 135) & (hue < 170),
        "black": gray < 80,
    }
    return masks.get(color)


def _small_color_components(image: np.ndarray) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    if image is None or getattr(image, "size", 0) == 0:
        return components
    total = int(image.shape[0] * image.shape[1])
    min_area = max(18, int(total * 0.000015))
    max_area = max(420, int(total * 0.004))
    for color in ("red", "orange", "yellow", "green", "blue", "purple", "black"):
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
    if "verified" in data and data.get("verified") is False and data.get("expect"):
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
        "controller_state",
        "subgoal",
        "element_cache",
        "controller_auto_observe",
        "controller_internal_action_count",
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
        return name, {k: v for k, v in action.items() if k not in {"action", "tool"}}
    if len(action) == 1:
        only_name, only_args = next(iter(action.items()))
        name = str(only_name)
        if isinstance(only_args, dict):
            return name, dict(only_args)
        if name == "hotkey":
            keys = list(only_args) if isinstance(only_args, list) else [only_args]
            return name, {"keys": [str(key) for key in keys]}
        if name in {"type_text", "type_into_element"}:
            return name, {"text": str(only_args)}
        return name, {"value": only_args}
    return "", {}


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


def _with_batch_expect_defaults(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in BATCH_OBSERVE_ACTIONS:
        return args
    expect = args.get("expect")
    if not isinstance(expect, dict) or "timeout_s" in expect:
        return args
    updated = dict(args)
    updated["expect"] = {**expect, "timeout_s": 2.0}
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
        self.watcher = watcher or ScreenWatcher(region_provider=self._target_region)
        self.capture_fn = capture_fn
        self._color_palette_button_cache: dict[tuple[int, int, int, int, int], tuple[int, int]] = {}

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
        # Palette swatches live in a dropdown/panel below the titlebar and main
        # toolbar. Colorful app/tool icons above this line are not selectable
        # drawing colors.
        title_cutoff = max(96, int(arr.shape[0] * 0.24))
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
            cluster_count = sum(
                1 for other in compact_components
                if abs(int((other.get("center") or [0, 0])[0]) - int(cx)) <= 260
                and abs(int((other.get("center") or [0, 0])[1]) - int(cy)) <= 72
            )
            if cluster_count >= 6:
                enriched = dict(item)
                enriched["cluster_count"] = cluster_count
                wanted.append(enriched)
        if not wanted:
            return None
        wanted.sort(
            key=lambda item: (
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

    def _palette_cache_key(self, region: Region) -> tuple[int, int, int, int, int]:
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        return (hwnd, int(region.left), int(region.top), int(region.width), int(region.height))

    def _open_color_palette(
        self,
        region: Region,
        *,
        cache_key: tuple[int, int, int, int, int] | None = None,
        prefer_cache: bool = True,
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
        label_terms = {"colors", "colour", "color", "颜色", "色彩", "顏色"}
        candidates = []
        for element in elements:
            text = _norm_text(getattr(element, "text", ""))
            if not text:
                continue
            if text in label_terms or any(term in text for term in label_terms):
                candidates.append(element)
        if not candidates:
            return ToolResult(json.dumps({
                "ok": False,
                "error": "no visible Colors/颜色 toolbar label found",
                "elapsed_ms": max(0, _now_ms() - started),
                "ocr_ms": ocr_ms,
            }, ensure_ascii=False))
        candidates.sort(key=lambda element: int(getattr(element, "cy", 0) or 0))
        label = candidates[0]
        x = int(getattr(label, "cx", 0) or 0)
        y = int(getattr(label, "cy", 0) or 0)
        offset = max(34, min(82, int(region.height * 0.10)))
        click_y = max(region.top + 8, y - offset)
        if cache_key is not None:
            self._color_palette_button_cache[cache_key] = (x, click_y)
        click = self._dispatch("click", {"x": x, "y": click_y, "skip_screen_delta": True})
        return ToolResult(json.dumps({
            "ok": not _failed(click),
            "cached": False,
            "elapsed_ms": max(0, _now_ms() - started),
            "ocr_ms": ocr_ms,
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

        def capture_palette_point() -> tuple[tuple[int, int] | None, dict[str, Any], int]:
            started = _now_ms()
            image = self.capture_fn(region)
            point = self._palette_swatch_point(image, region, color)
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
                    point, summary, elapsed_ms = capture_palette_point()
                    attempts.append({
                        "action": action,
                        "ok": point is not None,
                        "summary": summary,
                        "elapsed_ms": elapsed_ms,
                    })
                except Exception as exc:
                    attempts.append({"action": "capture_after_open", "ok": False, "error": str(exc)})
                    point = None

        try:
            point, summary, elapsed_ms = capture_palette_point()
            attempts.append({
                "action": "scan_color_palette",
                "ok": point is not None,
                "summary": summary,
                "elapsed_ms": elapsed_ms,
            })
        except Exception as exc:
            attempts.append({"action": "capture_palette", "ok": False, "error": str(exc)})
            point = None
        if point is None:
            opened = self._open_color_palette(region, cache_key=cache_key)
            opened_data = _json_dict(opened.text)
            attempts.append({
                "action": "open_color_palette",
                "ok": not _failed(opened),
                "data": opened_data,
                "preview": opened.text[:240],
            })
            if _failed(opened):
                return ToolResult(json.dumps({
                    "ok": False,
                    "verified": False,
                    "color": color,
                    "attempts": attempts,
                    "error": "could not open a visible color palette",
                }, ensure_ascii=False, default=str))
            scan_after_open("scan_after_open")
            if point is None and opened_data.get("cached"):
                opened = self._open_color_palette(region, cache_key=cache_key, prefer_cache=False)
                opened_data = _json_dict(opened.text)
                attempts.append({
                    "action": "open_color_palette_uncached",
                    "ok": not _failed(opened),
                    "data": opened_data,
                    "preview": opened.text[:240],
                })
                if not _failed(opened):
                    scan_after_open("scan_after_uncached_open")
        if point is None:
            return ToolResult(json.dumps({
                "ok": False,
                "verified": False,
                "color": color,
                "attempts": attempts,
                "error": f"no compact {color} swatch found in visible palette",
            }, ensure_ascii=False, default=str))
        click = self._dispatch("click", {"x": point[0], "y": point[1], "skip_screen_delta": True})
        attempts.append({
            "action": "click_color_swatch",
            "ok": not _failed(click),
            "point": {"x": point[0], "y": point[1]},
            "preview": click.text[:240],
        })
        ok = not _failed(click)
        return ToolResult(json.dumps({
            "ok": ok,
            "verified": ok,
            "color": color,
            "point": {"x": point[0], "y": point[1]},
            "verify_method": "cv-color-swatch",
            "attempts": attempts,
        }, ensure_ascii=False, default=str))

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        started = _now_ms()
        result = self.session.dispatch(name, args)
        elapsed = max(0, _now_ms() - started)
        result.replay = result.replay or {}
        result.replay.setdefault("elapsed_ms", elapsed)
        return result

    def _publish_child_start(
        self,
        batch_id: str,
        index: int,
        name: str,
        args: dict[str, Any],
    ) -> None:
        BUS.publish(
            "agent_batch_action_start",
            run_id=getattr(self.session, "run_id", None),
            parent_tool="run_action_batch",
            batch_id=batch_id,
            batch_index=index,
            tool=name,
            args=args,
        )

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
    ) -> None:
        BUS.publish(
            "agent_batch_action",
            run_id=getattr(self.session, "run_id", None),
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
        )

    def _click_element(self, action: dict[str, Any]) -> ToolResult:
        if self.session.snapshot is None:
            observe = self._dispatch("observe", {})
            if _failed(observe):
                return observe
        snapshot = self.session.snapshot
        if snapshot is None:
            return ToolResult("No snapshot available for click_element")
        anchor_data = action.get("anchor") if isinstance(action.get("anchor"), dict) else action
        anchor = anchor_from_dict(anchor_data)
        located = self.cache.locate_with_trace(snapshot, anchor)
        element = located.element
        auto_observe: dict[str, Any] | None = None
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
                observe_args = {"query": query, "max_elements": max_elements}
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
                return ToolResult(json.dumps(payload, ensure_ascii=False))
        args = {
            "element_id": element.elem_id,
            "button": action.get("button", "left"),
            "double": bool(action.get("double", False)),
        }
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
            if auto_observe is not None:
                auto_observe["found_after_retry"] = True
                data["controller_auto_observe"] = auto_observe
            result.text = json.dumps(data, ensure_ascii=False, default=str)
            result.replay = result.replay or {}
            result.replay.setdefault("anchor", _anchor_payload(anchor))
            result.replay.setdefault("resolved_element", element.to_dict())
        return result

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
            }
        }
        internal: list[dict[str, Any]] = []

        # UIA-off/custom UI input fallback: before relying on the generic
        # text-anchor ElementCache, try an input-specific locator that can use
        # edit roles, focused state, OCR labels, and synthetic nearby boxes.
        if self.session.snapshot is None:
            observe = self._dispatch("observe", {})
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
        if snapshot is not None:
            anchor_data = action.get("anchor") if isinstance(action.get("anchor"), dict) else click_args
            fallback = anchor_data.get("fallback_text") or anchor_data.get("fallback") or []
            if isinstance(fallback, str):
                fallback = [fallback]
            located_input = locate_input(
                snapshot,
                label=str(anchor_data.get("label") or ""),
                expected_region=str(anchor_data.get("expected_region") or ""),
                fallback_text=tuple(str(item) for item in fallback if item),
            )
            input_locator_data = located_input.to_dict()
            candidate = located_input.candidate if located_input.found else None
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

        if _truthy(action.get("clear") or action.get("replace") or action.get("clear_existing")):
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

        type_args = {"text": text}
        if isinstance(action.get("expect"), dict):
            type_args["expect"] = action["expect"]
        type_result = self._dispatch("type_text", type_args)
        type_data = _json_dict(type_result.text)
        type_failed = _failed(type_result) or (
            isinstance(action.get("expect"), dict)
            and type_data.get("verified") is False
        )
        internal.append({
            "action": "type_text",
            "ok": not type_failed,
            "data": type_data if type_data else {"preview": type_result.text[:240]},
        })
        payload = {
            "ok": not type_failed,
            "via": "type_into_element",
            "text_length": len(text),
            "subresults": internal,
            "controller_internal_action_count": len(internal),
            "type_result": type_data,
        }
        if isinstance(action.get("expect"), dict):
            payload["expect"] = action["expect"]
        if "verified" in type_data:
            payload["verified"] = bool(type_data.get("verified"))
        if type_failed:
            payload["blocked"] = True
            payload["stage"] = "type_text"
            payload["error"] = "type_into_element text entry did not verify"
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
        delta = self.watcher.wait_for_change(
            timeout_s=float(action.get("timeout_s", 3.0)),
            min_ratio=float(action.get("min_ratio", 0.0005)),
        )
        return ToolResult(json.dumps({
            "ok": bool(delta.changed),
            "changed": delta.changed,
            "delta": delta.to_dict(),
            "screen_watch": self.watcher.status(),
        }, ensure_ascii=False))

    def _wait_stable(self, action: dict[str, Any]) -> ToolResult:
        result = self.watcher.wait_for_stable(
            stable_ms=int(action.get("stable_ms", 500)),
            timeout_s=float(action.get("timeout_s", 5.0)),
            max_ratio=float(action.get("max_ratio", 0.0008)),
        )
        return ToolResult(json.dumps({
            "ok": bool(result.get("stable")),
            "stable": bool(result.get("stable")),
            "delta": result.get("delta", {}),
            "screen_watch": self.watcher.status(),
        }, ensure_ascii=False))

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
    def _action_region(name: str, args: dict[str, Any]) -> Region | None:
        coords: list[tuple[int, int]] = []
        try:
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
                payload["verify_method"] = "auto-region-summary"
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
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": "action must be an object"})
                break
            name, normalised_args = _normalise_action(action)
            name, normalised_args, upgraded_from = _upgrade_semantic_action(name, normalised_args)
            normalised_args = _with_batch_expect_defaults(name, normalised_args)
            last_action = name
            if name in BATCH_MUTATING_ACTIONS:
                mutating_seen = True
            if name not in ALLOWED_BATCH_ACTIONS:
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": f"batch action not allowed: {name}"})
                if stop_on_error:
                    break
                continue
            started = _now_ms()
            self._publish_child_start(batch_id, index, name, normalised_args)
            if name == "click_element":
                result = self._click_element(normalised_args)
            elif name == "type_into_element":
                result = self._type_into_element(normalised_args)
            elif name == "select_color":
                result = self._select_color(normalised_args)
            elif name == "wait" and not isinstance(normalised_args.get("condition"), dict):
                result = self._pause(normalised_args)
            elif name == "wait_change":
                result = self._wait_change(normalised_args)
            elif name == "wait_stable":
                result = self._wait_stable(normalised_args)
            else:
                result = self._dispatch(name, normalised_args)
            elapsed = max(0, _now_ms() - started)
            metrics.tool_latencies_ms.append(elapsed)
            metrics.action_count += 1
            if name in BATCH_OBSERVE_ACTIONS:
                metrics.observe_count += 1
            if isinstance(normalised_args.get("expect"), dict) or name == "wait":
                metrics.verify_count += 1
            if name in {"wait_change", "wait_stable"}:
                metrics.screen_wait_count += 1
            item = {
                "index": index,
                "action": name,
                "elapsed_ms": elapsed,
                "terminal": result.terminal,
                "preview": result.text[:800],
            }
            if upgraded_from:
                item["upgraded_from"] = upgraded_from
            data = _json_dict(result.text)
            has_expect = isinstance(normalised_args.get("expect"), dict)
            if data.get("verified") is True:
                verified_any = True
                current_state_verified = True
                if has_expect:
                    verified_expect_any = True
            elif data.get("verified") is False:
                if name in BATCH_MUTATING_ACTIONS:
                    current_state_verified = False
                if has_expect:
                    verified_expect_false_any = True
            elif name in BATCH_MUTATING_ACTIONS:
                current_state_verified = False
            plain_error = (
                not data
                and not _is_json_object(result.text)
                and name not in BATCH_OBSERVE_ACTIONS
            )
            failed = _failed(result) or plain_error or (
                has_expect
                and data.get("verified") is False
            )
            item["ok"] = not failed
            if "verified" in data:
                item["verified"] = bool(data.get("verified"))
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
            results.append(item)
            if name in BATCH_OBSERVE_ACTIONS:
                last_visual_region = None
                last_visual_action = ""
                last_visual_data = {}
                semantic_observe_after_visual = True
            elif name in BATCH_MUTATING_ACTIONS:
                region = self._action_region(name, normalised_args)
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
            self._publish_child_start(batch_id, index, name, args)
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
            self._publish_child_done(batch_id, index, name, args, result, elapsed, ok, data)
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
