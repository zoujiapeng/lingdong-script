"""Small, domain-neutral GUI planning helpers."""
from __future__ import annotations

import re

from ..config import CONFIG
from ..perception.app_aliases import expand as expand_app_aliases
from .task_rules import (
    _is_gui_only_task,
    _structured_task_intent,
)


_OPEN_PLAN_TERMS = (
    "open", "launch", "start", "focus", "bring up",
    "打开", "启动", "运行", "聚焦", "置前",
)
_OPEN_PLAN_MIXED_ACTION_TERMS = (
    "draw", "type", "input", "write", "search", "send", "click",
    "verify", "confirm", "screenshot", "save", "upload", "publish",
    "create", "new document", "new workbook", "blank document", "blank workbook",
    "绘制", "画一个", "画一", "输入", "搜索", "发送", "点击", "确认", "核验", "验证",
    "截图", "保存", "上传", "发布", "新建", "创建", "空白文档", "空白工作簿",
)


def _looks_like_native_gui_operation_task(task: str, *, task_intent=None) -> bool:
    """Return whether a native-vision run is clearly a visible GUI job.

    The Task Contract parser owns application and interaction recognition.  A
    second list of well-known programs here used to make the schema profile
    depend on whether a product happened to be named in source code.  Use the
    structured domain instead: named applications, current-window gestures,
    drawing surfaces, browser tabs, games, and future UI types all compile to a
    desktop/communication intent without another product catalogue.
    """
    source = task_intent if task_intent is not None else task
    try:
        intent = _structured_task_intent(source)
    except (TypeError, ValueError):
        return False
    domain = str(intent.get("domain") or "").casefold()
    return domain in {"desktop", "communication"}


def _schema_profile_for_task(
    task: str,
    *,
    is_coding: bool,
    native_vision: bool = False,
    task_intent=None,
    stage_type: str = "",
) -> str:
    """Choose the model-facing tool schema size for the current run.

    A typed stage has already been classified by the Task Contract and must
    not be sent through the legacy task-string classifiers again.  Direct
    AgentRunner callers retain the old text fallback for compatibility.
    """
    configured = str(CONFIG.get("agent", "tool_profile", default="auto") or "auto").strip().lower()
    if configured in {"standard", "full", "lean_gui", "drawing_gui"}:
        return configured
    if is_coding:
        return "standard"
    if str(stage_type or "").strip():
        return "lean_gui"
    if _is_gui_only_task(task):
        return "lean_gui"
    # The broader GUI classifier is useful for both controller modes.  A
    # native-vision model gets the ultra-small frame/action schema downstream;
    # a text-only model keeps the richer lean GUI controller plus VLM tools.
    # In either case unrelated research/system/file tools should not be sent
    # on every turn of an obvious desktop interaction task.
    if _looks_like_native_gui_operation_task(task, task_intent=task_intent):
        return "lean_gui"
    return "standard"

def _looks_like_pure_open_plan_goal(goal: str) -> bool:
    text = (goal or "").casefold()
    if not any(term.casefold() in text for term in _OPEN_PLAN_TERMS):
        return False
    return not any(term.casefold() in text for term in _OPEN_PLAN_MIXED_ACTION_TERMS)


def _pure_open_goal_matches_bound_target(goal: str, target_hint: str) -> bool:
    """Require a pure open step to name the already-bound app before reuse."""

    if not _looks_like_pure_open_plan_goal(goal):
        return False
    goal_text = str(goal or "").casefold()
    if any(term in goal_text for term in ("current app", "current window", "当前应用", "当前窗口")):
        return True
    compact_goal = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", goal_text)
    if not compact_goal:
        return False
    for alias in expand_app_aliases(str(target_hint or "")):
        compact_alias = re.sub(
            r"[^0-9a-z\u4e00-\u9fff]+", "", str(alias or "").casefold()
        )
        if len(compact_alias) >= 2 and compact_alias in compact_goal:
            return True
    return False


__all__ = (
    "_schema_profile_for_task",
    "_looks_like_native_gui_operation_task",
    "_looks_like_pure_open_plan_goal",
    "_pure_open_goal_matches_bound_target",
)
