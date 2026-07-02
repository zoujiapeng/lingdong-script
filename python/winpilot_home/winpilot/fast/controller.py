"""Fast GUI controller: execute short grounded action batches."""
from __future__ import annotations

import json
import time
from typing import Any

from ..agent.tool_result import ToolResult
from .element_cache import ElementCache, anchor_from_dict
from .types import BatchMetrics

ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_text",
    "hotkey",
    "wait",
    "read_text",
    "clipboard_write",
    "clipboard_paste",
}


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _json_dict(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


class FastGuiController:
    """Small inner-loop executor owned by a ToolSession."""

    def __init__(self, session, cache: ElementCache | None = None) -> None:
        self.session = session
        self.cache = cache or ElementCache()

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        started = _now_ms()
        result = self.session.dispatch(name, args)
        elapsed = max(0, _now_ms() - started)
        result.replay = result.replay or {}
        result.replay.setdefault("elapsed_ms", elapsed)
        return result

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

    def execute(self, actions: list[dict[str, Any]], stop_on_error: bool = True) -> dict[str, Any]:
        metrics = BatchMetrics(started_ms=_now_ms())
        results: list[dict[str, Any]] = []
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": "action must be an object"})
                break
            name, normalised_args = _normalise_action(action)
            if name not in ALLOWED_BATCH_ACTIONS:
                metrics.failed_at = index
                results.append({"index": index, "ok": False, "error": f"batch action not allowed: {name}"})
                if stop_on_error:
                    break
                continue
            started = _now_ms()
            if name == "click_element":
                result = self._click_element(normalised_args)
            else:
                result = self._dispatch(name, normalised_args)
            elapsed = max(0, _now_ms() - started)
            metrics.tool_latencies_ms.append(elapsed)
            metrics.action_count += 1
            if name == "observe":
                metrics.observe_count += 1
            if isinstance(normalised_args.get("expect"), dict) or name == "wait":
                metrics.verify_count += 1
            item = {
                "index": index,
                "action": name,
                "elapsed_ms": elapsed,
                "terminal": result.terminal,
                "preview": result.text[:800],
            }
            data = _json_dict(result.text)
            failed = _failed(result) or (
                isinstance(normalised_args.get("expect"), dict)
                and data.get("verified") is False
            )
            item["ok"] = not failed
            results.append(item)
            if failed and stop_on_error:
                metrics.failed_at = index
                break
        metrics.elapsed_ms = max(0, _now_ms() - metrics.started_ms)
        metrics.controller_round_trips_saved = max(0, metrics.action_count - 1)
        return {
            "ok": metrics.failed_at is None,
            "results": results,
            "metrics": metrics.to_dict(),
            "element_cache": self.cache.stats(),
        }
