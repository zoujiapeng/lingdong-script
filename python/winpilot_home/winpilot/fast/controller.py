"""Fast GUI controller: execute short grounded action batches."""
from __future__ import annotations

import json
import time
from typing import Any

from ..agent.tool_result import ToolResult
from ..perception import vision_cv
from ..utils.log import BUS
from ..utils.screenshot import Region, capture, window_region
from .element_cache import ElementCache, anchor_from_dict
from .types import BatchMetrics
from .watcher import ScreenWatcher

ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
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
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
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
            if query and callable(dispatch):
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
                        "snapshot and one targeted observe retry."
                    ),
                    "via": "click_element",
                    "anchor": _anchor_payload(anchor),
                    "anchor_locator": located.to_dict(),
                    "initial_anchor_locator": initial_locator.to_dict(),
                }
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
            max(0, metrics.action_count - 1) + metrics.auto_observe_count
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
