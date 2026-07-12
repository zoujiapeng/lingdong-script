"""Fast-controller tool handlers for ToolSession."""
from __future__ import annotations

import json
import re
from typing import Any

from ..fast.controller import BATCH_MUTATING_ACTIONS, FastGuiController
from ..fast.perception_daemon import PERCEPTION_DAEMON
from ..fast.subgoal import SubgoalExecutor
from ..perception import win32
from ..perception.reducer import _non_blocking_notice_context
from .tool_result import ToolResult


def _has_execute_subgoal_routine(args: dict[str, Any]) -> bool:
    if not isinstance(args, dict):
        return False
    input_spec = args.get("input") or args.get("type") or args.get("text_input")
    click_spec = args.get("click") or args.get("button") or args.get("press")
    clicks = args.get("clicks") or args.get("buttons")
    draw_spec = args.get("draw") or args.get("drawing")
    draws = args.get("draws") or args.get("drawings")
    format_spec = args.get("format") or args.get("text_format")
    formats = args.get("formats")
    verify = args.get("verify_texts") or args.get("verify")
    if isinstance(input_spec, dict) and input_spec:
        return True
    if isinstance(click_spec, dict) and click_spec:
        return True
    if isinstance(clicks, list) and any(isinstance(item, dict) and item for item in clicks):
        return True
    if isinstance(draw_spec, dict) and draw_spec:
        return True
    if isinstance(draws, list) and any(isinstance(item, dict) and item for item in draws):
        return True
    if isinstance(format_spec, dict) and format_spec:
        return True
    if isinstance(formats, list) and any(isinstance(item, dict) and item for item in formats):
        return True
    if isinstance(verify, str) and verify.strip():
        return True
    if isinstance(verify, list) and any(str(item or "").strip() for item in verify):
        return True
    return bool(args.get("text") or args.get("button_label") or args.get("shape"))


def _needs_resolved_target_window(session: Any, args: dict[str, Any]) -> bool:
    target = args.get("target_window")
    if not isinstance(target, dict):
        return False
    if target.get("hwnd"):
        return False
    if not (str(target.get("process") or "").strip() or str(target.get("title") or "").strip()):
        return False
    try:
        if int(getattr(session, "hwnd", 0) or 0):
            return False
    except Exception:
        pass
    # The controller can safely act on a visible process/title hint once it
    # has been resolved locally. Do that here too so this outer guard does not
    # create a needless LLM recovery turn before the controller is reached.
    process = str(target.get("process") or target.get("exe") or "").strip()
    title = str(target.get("title") or "").strip()
    try:
        if process:
            for candidate in win32.find_app(process).get("windows") or []:
                hwnd = int(candidate.get("hwnd") or 0)
                if hwnd and candidate.get("visible"):
                    setattr(session, "hwnd", hwnd)
                    target["hwnd"] = hwnd
                    return False
        if title:
            found = win32.find_window(title)
            hwnd = int((found or {}).get("hwnd") or 0)
            if hwnd:
                setattr(session, "hwnd", hwnd)
                target["hwnd"] = hwnd
                return False
    except Exception:
        pass
    return True


def _blocked_unresolved_target_payload(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "needs_target_window": True,
        "subgoal": str(args.get("subgoal") or args.get("goal") or ""),
        "subgoal_route": "controller_first",
        "controller_state": {
            "phase": "blocked",
            "needs_llm_reason": True,
            "needs_target_window": True,
        },
        "evidence_packet": {"matched_texts": [], "missing_texts": []},
        "error": (
            "execute_subgoal target_window has only a process/title hint. "
            "Resolve it to a real hwnd before running a controller routine."
        ),
        "needs_llm_reason": True,
        "fast_path_advice": (
            "Use launch/find_app/list_windows/scan_windows/focus_window to establish "
            "the target app/window first. Do not click the current foreground window "
            "as a substitute for an unresolved target_window."
        ),
    }


def _blocked_nonblocking_notice_click(session: Any, args: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = getattr(session, "snapshot", None)
    if snapshot is None:
        return None
    click_specs = []
    for value in (args.get("click"), args.get("button"), args.get("press")):
        if isinstance(value, dict):
            click_specs.append(value)
    for value in (args.get("clicks"), args.get("buttons")):
        if isinstance(value, list):
            click_specs.extend(item for item in value if isinstance(item, dict))
    if not click_specs:
        return None
    try:
        elements = list(snapshot.scoped_elements())
        notice = _non_blocking_notice_context(snapshot, elements)
    except Exception:
        return None
    if not notice or notice.get("blocking") is not False:
        return None
    suppressed = [
        re.sub(r"\s+", "", str(text or "")).casefold()
        for text in notice.get("suppressed_texts") or []
        if str(text or "").strip()
    ]
    requested_labels = []
    for spec in click_specs:
        requested_labels.extend([
            spec.get("label"),
            spec.get("text"),
            spec.get("name"),
            *(spec.get("fallback_text") or []),
        ])
    matched = next((
        str(label)
        for label in requested_labels
        if label
        and any(
            key and (
                key in re.sub(r"\s+", "", str(label)).casefold()
                or re.sub(r"\s+", "", str(label)).casefold() in key
            )
            for key in suppressed
        )
    ), "")
    if not matched:
        return None
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "non_blocking_notice_click_blocked": True,
        "requested_label": matched,
        "notice": notice,
        "error": (
            "The requested control belongs to a grounded non-blocking notice. "
            "Clicking it would distract from the active work surface."
        ),
        "needs_llm_reason": False,
        "fast_path_advice": str(notice.get("guidance") or "Continue the pending task on the active work surface."),
    }


def _batch_open_app_target(session: Any, args: dict[str, Any]) -> str:
    """Recover hallucinated pre-observe click ids for an app-launch subgoal."""
    if getattr(session, "snapshot", None) is not None:
        return ""
    try:
        if int(getattr(session, "hwnd", 0) or 0):
            return ""
    except Exception:
        return ""
    goal = str(args.get("subgoal") or args.get("goal") or "").strip()
    match = re.match(r"^(?:open|launch|start|run|打开|启动|运行)\s*(.+)$", goal, re.IGNORECASE)
    if not match:
        return ""
    name = match.group(1).strip().strip("：:，,。.!！")
    name = re.sub(
        r"(?:应用程序|应用|软件|application|app)$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    if not name or len(name) > 64:
        return ""
    if name.casefold() not in str(getattr(session, "task", "") or "").casefold():
        return ""
    generic = {
        "menu", "button", "dialog", "panel", "file", "folder",
        "菜单", "按钮", "对话框", "面板", "文件", "文件夹",
    }
    if name.casefold() in {item.casefold() for item in generic}:
        return ""
    return name


class FastToolMixin:
    def _fast_controller(self) -> FastGuiController:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            lease = None
            try:
                hwnd = int(getattr(self, "hwnd", 0) or 0)
                if hwnd:
                    lease = PERCEPTION_DAEMON.acquire(
                        hwnd,
                        run_id=str(getattr(self, "run_id", "") or ""),
                    )
            except Exception:
                lease = None
            controller = FastGuiController(
                self,
                cache=lease.cache if lease else None,
                watcher=lease.watcher if lease else None,
            )
            self._perception_lease = lease
            self._fast_gui_controller = controller
        return controller

    def _fast_screen_status(self) -> dict:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            return {}
        try:
            return controller.watcher.status()
        except Exception:
            return {}

    @staticmethod
    def _batch_has_mutation(actions: list) -> bool:
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = str(action.get("action") or action.get("tool") or "")
            if not name and len(action) == 1:
                name = str(next(iter(action.keys())))
            if name in BATCH_MUTATING_ACTIONS:
                return True
        return False

    def _t_execute_subgoal(self, args: dict) -> ToolResult:
        if not isinstance(args, dict):
            args = {}
        notice_block = _blocked_nonblocking_notice_click(self, args)
        if notice_block is not None:
            return ToolResult(json.dumps(notice_block, ensure_ascii=False, default=str))
        if not _has_execute_subgoal_routine(args):
            natural_subgoal = str(args.get("subgoal") or args.get("goal") or "").strip()
            launch_target = _batch_open_app_target(self, args)
            if launch_target:
                return SubgoalExecutor(self, self._fast_controller()).execute({
                    "subgoal": natural_subgoal or f"Open {launch_target}",
                    "click": {"label": launch_target},
                })
            if re.search(
                r"^(?:observe|inspect|look at|check (?:the )?(?:current )?(?:screen|window|state)|"
                r"\u89c2\u5bdf|\u67e5\u770b|\u68c0\u67e5\u5f53\u524d)",
                natural_subgoal,
                re.IGNORECASE,
            ):
                target = args.get("target_window") if isinstance(args.get("target_window"), dict) else {}
                observe_args = {
                    "hwnd": int(target.get("hwnd") or getattr(self, "hwnd", 0) or 0),
                    "query": natural_subgoal[:240],
                }
                return self._t_observe(observe_args)
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "needs_target_window": False,
                "subgoal": str(args.get("subgoal") or args.get("goal") or ""),
                "subgoal_route": "controller_first",
                "controller_state": {
                    "phase": "blocked",
                    "needs_llm_reason": True,
                    "needs_target_window": False,
                },
                "evidence_packet": {"matched_texts": [], "missing_texts": []},
                "error": (
                    "execute_subgoal requires an executable routine: input.text, "
                    "click.label/clicks, format/formats, draw/draws, or verify_texts. The subgoal "
                    "string is only a label and cannot launch/discover an app."
                ),
                "needs_llm_reason": True,
                "fast_path_advice": (
                    "Do not use execute_subgoal with only a natural-language subgoal. "
                    "First use launch/find_app/focus_window/list_windows to establish "
                    "the target app/window, or provide input/click/format/draw/verify_texts "
                    "for a concrete controller routine."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        if _needs_resolved_target_window(self, args):
            return ToolResult(json.dumps(_blocked_unresolved_target_payload(args), ensure_ascii=False, default=str))
        return SubgoalExecutor(self, self._fast_controller()).execute(args)

    def _t_run_action_batch(self, args: dict) -> ToolResult:
        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            return ToolResult("run_action_batch requires a non-empty actions array")
        formats = args.get("formats")
        if args.get("semantic_override") == "clipboard_replace":
            request = {
                "subgoal": str(args.get("subgoal") or "Replace placeholder with clipboard content"),
            }
            if isinstance(args.get("target_window"), dict):
                request["target_window"] = dict(args["target_window"])
            return SubgoalExecutor(self, self._fast_controller()).execute(request)
        if args.get("semantic_override") == "document_format" and isinstance(formats, list) and formats:
            request = {
                "subgoal": str(args.get("subgoal") or "Apply requested document formatting"),
                "formats": formats,
                "constraints": dict(args.get("constraints") or {}),
            }
            if isinstance(args.get("target_window"), dict):
                request["target_window"] = dict(args["target_window"])
            return SubgoalExecutor(self, self._fast_controller()).execute(request)
        if args.get("semantic_override") == "spreadsheet_table":
            request = {
                "subgoal": (
                    "Enter the complete table data into the requested cell range in one batch. "
                    + str(args.get("subgoal") or "")
                )[:360],
                "click": {
                    "label": "A1",
                    "expected_region": "spreadsheet grid",
                },
            }
            if isinstance(args.get("target_window"), dict):
                request["target_window"] = dict(args["target_window"])
            return SubgoalExecutor(self, self._fast_controller()).execute(request)
        if args.get("semantic_override") == "spreadsheet_row_format":
            request = {
                "subgoal": str(args.get("subgoal") or "Apply requested spreadsheet row formatting"),
            }
            if isinstance(args.get("target_window"), dict):
                request["target_window"] = dict(args["target_window"])
            return SubgoalExecutor(self, self._fast_controller()).execute(request)
        if args.get("semantic_override") == "exact_save_as":
            request = {
                "subgoal": str(args.get("subgoal") or "Save As exact path"),
                "input": dict(args.get("input") or {}),
                "click": dict(args.get("click") or {}),
                "verify_texts": list(args.get("verify_texts") or []),
                "constraints": dict(args.get("constraints") or {}),
            }
            if isinstance(args.get("target_window"), dict):
                request["target_window"] = dict(args["target_window"])
            return SubgoalExecutor(self, self._fast_controller()).execute(request)
        launch_target = _batch_open_app_target(self, args)
        if launch_target:
            return SubgoalExecutor(self, self._fast_controller()).execute({
                "subgoal": str(args.get("subgoal") or f"打开{launch_target}"),
                "click": {"label": launch_target},
            })
        stop_on_error = bool(args.get("stop_on_error", True))
        if "sync" in args:
            sync = args.get("sync") or None
        elif self._batch_has_mutation(actions):
            sync = {"mode": "stable", "stable_ms": 250, "timeout_s": 2.0}
        else:
            sync = None
        result = self._fast_controller().execute(
            actions,
            stop_on_error=stop_on_error,
            sync=sync,
            subgoal=str(args.get("subgoal") or ""),
        )
        return ToolResult(self._fmt(result))

    def _t_screen_watch(self, args: dict) -> ToolResult:
        action = str(args.get("action") or "status").casefold()
        watcher = self._fast_controller().watcher
        if action == "start":
            watcher.start()
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        if action == "stop":
            watcher.stop()
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        if action == "sample":
            delta = watcher.sample_once()
            return ToolResult(json.dumps({
                "ok": True,
                "delta": delta.to_dict(),
                "screen_watch": watcher.status(),
            }, ensure_ascii=False))
        if action == "status":
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        return ToolResult(f"screen_watch unknown action: {action}")
