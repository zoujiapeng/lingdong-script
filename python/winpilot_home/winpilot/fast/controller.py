"""Fast GUI controller: execute short grounded action batches."""
from __future__ import annotations

import json
import time
from typing import Any

from ..agent.tool_result import ToolResult
from ..utils.log import BUS
from ..utils.screenshot import window_region
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
        "visual_change_detected",
        "visual_change_ratio",
        "needs_observe",
        "verify_method",
    ):
        if key in data:
            meta[key] = data[key]
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
    ) -> None:
        self.session = session
        self.cache = cache or ElementCache()
        self.watcher = watcher or ScreenWatcher(region_provider=self._target_region)

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
        element = self.cache.locate(snapshot, anchor)
        if element is None:
            return ToolResult(
                "click_element could not locate anchor; observe with a more specific query "
                "or escalate perception before retrying."
            )
        args = {
            "element_id": element.elem_id,
            "button": action.get("button", "left"),
            "double": bool(action.get("double", False)),
        }
        if isinstance(action.get("expect"), dict):
            args["expect"] = action["expect"]
        return self._dispatch("click", args)

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

    def execute(
        self,
        actions: list[dict[str, Any]],
        stop_on_error: bool = True,
        sync: dict[str, Any] | bool | None = None,
    ) -> dict[str, Any]:
        metrics = BatchMetrics(started_ms=_now_ms())
        batch_id = f"batch-{metrics.started_ms}"
        results: list[dict[str, Any]] = []
        verified_any = False
        current_state_verified = False
        verified_expect_any = False
        verified_expect_false_any = False
        last_action = ""
        mutating_seen = False
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
            results.append(item)
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
        metrics.controller_round_trips_saved = max(0, metrics.action_count - 1)
        payload = {
            "ok": metrics.failed_at is None,
            "results": results,
            "metrics": metrics.to_dict(),
            "element_cache": self.cache.stats(),
            "last_action": last_action,
            "sync": sync_result,
            "needs_observe": (
                metrics.failed_at is None
                and metrics.action_count > 0
                and not current_state_verified
                and last_action not in BATCH_OBSERVE_ACTIONS
            ),
        }
        if current_state_verified and metrics.failed_at is None:
            payload["verified"] = True
        if verified_expect_any or verified_expect_false_any:
            payload["verified"] = bool(
                verified_expect_any
                and not verified_expect_false_any
                and metrics.failed_at is None
            )
        return payload
