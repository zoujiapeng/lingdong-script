"""Small, domain-neutral GUI planning helpers."""
from __future__ import annotations

import re

from ..config import CONFIG
from ..perception.app_aliases import expand as expand_app_aliases
from .task_rules import (
    _is_communication_task,
    _is_gui_only_task,
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


# ``_is_gui_only_task`` intentionally answers a policy question: whether the
# task *must* stay in the visible UI.  Tool profiling needs a slightly broader
# answer.  A normal request such as "open Notepad and type hello" is plainly a
# desktop operation even when the user did not say "GUI-only".  Keep this
# classifier about generic interaction grammar and visible UI surfaces; do not
# encode benchmark subjects (for example a particular animal to draw).
_NATIVE_GUI_ACTION_RE = re.compile(
    r"\b(?:open|launch|start|focus|click|press|select|choose|type|enter|write|"
    r"scroll|drag|drop|draw|paint|close|maximi[sz]e|minimi[sz]e|switch|"
    r"navigate|browse|search|send|play|pause|calculate)\b|"
    r"\u6253\u5f00|\u542f\u52a8|\u8fd0\u884c|\u805a\u7126|\u70b9\u51fb|\u6309\u4e0b|"
    r"\u9009\u62e9|\u8f93\u5165|\u952e\u5165|\u586b\u5199|\u4e66\u5199|\u6eda\u52a8|"
    r"\u62d6\u62fd|\u62d6\u52a8|\u653e\u7f6e|\u7ed8\u5236|\u753b|\u5173\u95ed|"
    r"\u6700\u5927\u5316|\u6700\u5c0f\u5316|\u5207\u6362|\u6d4f\u89c8|\u641c\u7d22|"
    r"\u53d1\u9001|\u64ad\u653e|\u6682\u505c|\u8ba1\u7b97",
    re.IGNORECASE,
)
_NATIVE_GUI_TARGET_RE = re.compile(
    r"\b(?:desktop|window|app|application|client|browser|webpage|tab|toolbar|"
    r"menu|button|dialog|text\s*box|input\s*field|canvas|whiteboard|taskbar|"
    r"system\s*tray|icon|notepad|calculator|paint|chrome|edge|firefox|"
    r"file\s*explorer|settings|control\s*panel|word|excel|powerpoint|qq|wechat)\b|"
    r"\u684c\u9762|\u7a97\u53e3|\u5e94\u7528|\u8f6f\u4ef6|\u5ba2\u6237\u7aef|"
    r"\u6d4f\u89c8\u5668|\u7f51\u9875|\u6807\u7b7e\u9875|\u5de5\u5177\u680f|"
    r"\u83dc\u5355|\u6309\u94ae|\u5bf9\u8bdd\u6846|\u8f93\u5165\u6846|\u753b\u5e03|"
    r"\u753b\u677f|\u767d\u677f|\u4efb\u52a1\u680f|\u7cfb\u7edf\u6258\u76d8|"
    r"\u56fe\u6807|\u8bb0\u4e8b\u672c|\u8ba1\u7b97\u5668|\u753b\u56fe|"
    r"\u8d44\u6e90\u7ba1\u7406\u5668|\u8bbe\u7f6e|\u63a7\u5236\u9762\u677f|"
    r"\u5fae\u4fe1",
    re.IGNORECASE,
)
_DIRECT_NATIVE_GUI_GESTURE_RE = re.compile(
    r"\b(?:click|scroll|drag|drop|hotkey|double[- ]click|right[- ]click)\b|"
    r"\u70b9\u51fb|\u53cc\u51fb|\u53f3\u952e|\u6eda\u52a8|\u62d6\u62fd|\u62d6\u52a8",
    re.IGNORECASE,
)


def _looks_like_native_gui_operation_task(task: str) -> bool:
    """Return whether a native-vision run is clearly a visible GUI job.

    This is deliberately narrower than "not coding": explanations, arithmetic,
    repository work, and system inspection should keep the standard schema.
    Direct pointer gestures are self-identifying; other verbs need an explicit
    application or UI surface so phrases such as "search the documentation" do
    not lose research/file tools.
    """
    text = str(task or "").strip()
    if not text:
        return False
    if _DIRECT_NATIVE_GUI_GESTURE_RE.search(text):
        return True
    return bool(
        _NATIVE_GUI_ACTION_RE.search(text)
        and _NATIVE_GUI_TARGET_RE.search(text)
    )


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
    communication_source = task_intent if task_intent is not None else task
    if _is_gui_only_task(task) or _is_communication_task(communication_source):
        return "lean_gui"
    # The broader GUI classifier is useful for both controller modes.  A
    # native-vision model gets the ultra-small frame/action schema downstream;
    # a text-only model keeps the richer lean GUI controller plus VLM tools.
    # In either case unrelated research/system/file tools should not be sent
    # on every turn of an obvious desktop interaction task.
    if _looks_like_native_gui_operation_task(task):
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
