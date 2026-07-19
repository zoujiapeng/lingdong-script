"""Controller-first subgoal executor.

This is not a second agent. It is a deterministic routine layer under the
unified AgentRunner/ToolSession: the LLM supplies a compact subgoal and the
local GUI controller performs the low-level click/type/wait/verify steps.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import win32gui

from ..action.verify import _match_visible_text
from ..config import CONFIG
from ..agent.task_rules import (
    _required_visible_payloads,
    _visible_state_targets,
)
from ..agent.tool_result import ToolResult
from ..perception import ocr, vision_cv, vlm, win32
from ..perception import reducer
from ..perception.fusion import Snapshot
from ..perception.model import UIElement
from ..utils.screenshot import Region, window_region
from .controller import FastGuiController


@dataclass
class SubgoalRequest:
    subgoal: str
    target_window: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    click: dict[str, Any] = field(default_factory=dict)
    clicks: list[dict[str, Any]] = field(default_factory=list)
    draw: dict[str, Any] = field(default_factory=dict)
    draws: list[dict[str, Any]] = field(default_factory=list)
    formats: list[dict[str, Any]] = field(default_factory=list)
    verify_texts: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tool_result_data(result: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(getattr(result, "text", "") or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = re.sub(r"[\s_\-:：]+", "", value or "").casefold()
        if value and key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _compact_key(value: Any) -> str:
    return re.sub(r"[\s_\-:\uff1a]+", "", str(value or "").casefold())


def _looks_like_file_path(text: Any) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if "\\" in value or "/" in value:
        return True
    return bool("." in value and len(value) <= 260 and not any(ch.isspace() for ch in value))


def _strip_stale_status_lines(text: str) -> str:
    markers = (
        "\u6b63\u5728\u65b0\u5efa\u6587\u6863",
        "\u4e0a\u4e00\u6b21\u672a\u6b63\u5e38\u5173\u95ed",
        "\u5df2\u6062\u590d\u5230\u6700\u65b0\u7248\u672c",
        "\u7acb\u5373\u6062\u590d",
        "\u53d1\u9001\u62a5\u544a",
    )
    return "\n".join(
        line for line in str(text or "").splitlines()
        if not any(marker in line for marker in markers)
    )


def _file_dialog_enter_submit(request: SubgoalRequest, input_spec: dict[str, Any],
                              click_specs: list[dict[str, Any]]) -> bool:
    text = input_spec.get("text", input_spec.get("value", input_spec.get("text_input", "")))
    if not _looks_like_file_path(text):
        return False
    parts: list[Any] = [
        request.subgoal,
        request.target_window.get("title") if isinstance(request.target_window, dict) else "",
        input_spec.get("label"),
        input_spec.get("fallback_text"),
    ]
    for click in click_specs:
        parts.extend([click.get("label"), click.get("text"), click.get("name")])
    haystack = _compact_key(" ".join(str(part or "") for part in parts))
    markers = [
        "filename", "file name", "filepath", "file path", "path",
        "save", "open", "saveas", "文件名", "文件路径", "路径",
        "保存", "打开", "另存为", "另存",
    ]
    return any(_compact_key(marker) in haystack for marker in markers)


def _file_dialog_navigation_clicks(
    input_spec: dict[str, Any],
    click_specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return location/folder clicks that must run before filename entry."""
    input_markers = {
        _compact_key(input_spec.get("label")),
        "filename", "filepath", "文件名", "文件路径",
    }
    submit_markers = {
        "save", "saveas", "open", "ok", "confirm",
        "保存", "另存为", "打开", "确定", "确认",
    }
    navigation: list[dict[str, Any]] = []
    for spec in click_specs:
        label = str(spec.get("label") or spec.get("text") or spec.get("name") or "")
        key = _compact_key(label)
        if not key:
            continue
        if any(marker and (marker in key or key in marker) for marker in input_markers):
            continue
        if any(marker and (marker in key or key in marker) for marker in submit_markers):
            continue
        navigation.append(spec)
    return navigation


def _app_launch_target(request: SubgoalRequest) -> str:
    """Recognize a no-window subgoal that names an application to launch."""
    if request.input or _draw_specs(request) or request.target_window:
        return ""
    click_specs = request.clicks or ([request.click] if request.click else [])
    if len(click_specs) != 1:
        return ""
    spec = click_specs[0]
    label = str(spec.get("label") or spec.get("text") or spec.get("name") or "").strip()
    goal = str(request.subgoal or "").strip()
    if not label or not goal:
        return ""
    if not re.match(r"^(?:open|launch|start|run|打开|启动|运行)", goal, re.IGNORECASE):
        return ""
    if _compact_key(label) not in _compact_key(goal):
        goal_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]{3,}", goal)
        }
        label_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]{3,}", label)
        }
        if not (goal_tokens & label_tokens):
            return ""
    generic_controls = (
        "menu", "button", "dialog", "panel", "file", "folder",
        "blank", "new", "blank workbook", "blank document",
        "菜单", "按钮", "对话框", "面板", "文件", "文件夹",
        "空白", "新建", "空白工作簿", "空白文档",
    )
    if _compact_key(label) in {_compact_key(item) for item in generic_controls}:
        return _initial_task_app_target(goal)
    return label


def _initial_task_app_target(task: str) -> str:
    """Extract the first explicitly requested app for an unbound GUI run.

    The planner can legitimately start with an in-app action (for example,
    "click the document tab") even though the requested application has not
    been opened yet.  This parser provides a one-shot launch prelude without
    teaching the controller any application-specific workflow.
    """
    text = str(task or "").strip()
    if not text:
        return ""

    launch_re = re.compile(
        r"(?:open|launch|start|run|打开|启动|运行)\s*[\"'“‘]?",
        re.IGNORECASE,
    )
    action_boundary_re = re.compile(
        r"(?:"
        r"\s+\b(?:and|then|to|before|after|create|new|search|find|send|play|"
        r"calculate|type|enter|copy|paste|save)\b"
        r"|并且|并|然后|接着|随后|再|新建|创建|搜索|查找|给|发送|播放|"
        r"计算|输入|填写|复制|粘贴|保存|进入"
        r")",
        re.IGNORECASE,
    )
    punctuation_re = re.compile(r"[，,。；;！？!?\r\n]")
    generic_targets = {
        "app", "application", "software", "window", "page", "website",
        "file", "folder", "document", "menu", "dialog",
        "应用", "应用程序", "软件", "窗口", "页面", "网页", "网站",
        "文件", "文件夹", "文档", "菜单", "对话框",
    }

    for match in launch_re.finditer(text):
        prefix = text[max(0, match.start() - 4):match.start()]
        if re.search(r"(?:不要|禁止|无需|不必|别)\s*$", prefix, re.IGNORECASE):
            continue
        tail = text[match.end():]
        punctuation = punctuation_re.search(tail)
        if punctuation:
            tail = tail[:punctuation.start()]
        boundary = action_boundary_re.search(tail)
        if boundary:
            tail = tail[:boundary.start()]
        candidate = tail.strip(" \t\"'“”‘’：:")
        candidate = re.sub(
            r"^(?:the\s+|本地(?:的)?|电脑上(?:的)?|桌面上(?:的)?|系统(?:中|里|内)的?)",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        candidate = re.sub(r"\s+(?:app|application|应用|软件)$", "", candidate, flags=re.IGNORECASE).strip()
        if not candidate or len(candidate) > 64:
            continue
        if _compact_key(candidate) in {_compact_key(item) for item in generic_targets}:
            continue
        return candidate

    # Natural tasks often name an unknown application as a location/instrument
    # rather than an explicit launch command. Extract only that app phrase;
    # discovery/launch remains the generic app-catalog responsibility.
    implicit_patterns = (
        re.compile(
            r"(?:^|[，,。；;！？!?\s])在\s*([^，,。；;！？!?\r\n]{1,32}?)"
            r"(?:上|中|里|内)(?=\s*(?:画|写|输入|编辑|制作|创建|搜索|查找|播放|操作|完成))",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[，,。；;！？!?\s])用\s*([^，,。；;！？!?\r\n]{1,32}?)"
            r"(?=\s*(?:给|向|发送|画|写|输入|编辑|制作|创建|搜索|查找|播放|操作))",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[,.!?;\s])(?:in|inside|using)\s+([A-Za-z0-9_. -]{1,32}?)"
            r"(?=\s+(?:draw|write|type|edit|create|search|find|play|send|operate))",
            re.IGNORECASE,
        ),
    )
    for pattern in implicit_patterns:
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group(1).strip(" \t\"'“”‘’：:")
        candidate = re.sub(
            r"\s+(?:app|application|应用|软件)$",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        if candidate and _compact_key(candidate) not in {
            _compact_key(item) for item in generic_targets
        }:
            return candidate
    return ""


_PRINTABLE_SEQUENCE_TRIGGERS = (
    "input", "type", "enter", "key", "keyboard",
    "输入", "键入", "录入", "按键", "键盘",
)

_PRINTABLE_LABEL_ALIASES = {
    "space": " ",
    "空格": " ",
}


def _click_label_to_printable_key(label: Any) -> str | None:
    text = unicodedata.normalize("NFKC", str(label or "")).strip()
    if not text:
        return None
    compact = _compact_key(text)
    if compact in _PRINTABLE_LABEL_ALIASES:
        return _PRINTABLE_LABEL_ALIASES[compact]
    if len(text) == 1:
        if text.isprintable() and not text.isspace():
            return text
    return None


_SHORTCUT_KEY_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "escape": "esc",
    "esc": "esc",
    "space": "space",
    "home": "home",
    "end": "end",
    "delete": "delete",
    "del": "delete",
    "backspace": "backspace",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}


def _click_label_to_hotkey(label: Any) -> list[str]:
    """Translate a model-authored shortcut label into a physical key chord."""
    text = unicodedata.normalize("NFKC", str(label or "")).strip().casefold()
    if "+" not in text:
        return []
    parts = [part.strip() for part in text.split("+") if part.strip()]
    if len(parts) < 2 or any(part in {"win", "windows", "meta", "super"} for part in parts):
        return []
    keys: list[str] = []
    for part in parts:
        key = _SHORTCUT_KEY_ALIASES.get(part)
        if key is None and re.fullmatch(r"[a-z0-9]", part):
            key = part
        if key is None and re.fullmatch(r"f(?:[1-9]|1[0-2])", part):
            key = part
        if key is None:
            return []
        keys.append(key)
    return keys

def _successful_draw_scene_specs(
    draw_specs: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    batch: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return scene primitives whose corresponding draw action executed."""
    results = {
        int(item.get("index") or 0): item
        for item in (batch.get("results") or [])
        if isinstance(item, dict)
    }
    successful: list[dict[str, Any]] = []
    draw_index = 0
    for action_index, action in enumerate(actions, start=1):
        if action.get("action") != "draw_strokes":
            continue
        spec = draw_specs[draw_index] if draw_index < len(draw_specs) else None
        draw_index += 1
        result = results.get(action_index)
        data = result.get("data") if isinstance(result, dict) else None
        visual_change = data.get("visual_change_detected") if isinstance(data, dict) else None
        deferred_delivery = bool(
            action.get("defer_visual_verify")
            and isinstance(data, dict)
            and data.get("visual_verify_deferred") is True
        )
        if (
            spec is not None
            and isinstance(result, dict)
            and result.get("ok") is True
            and (visual_change is True or deferred_delivery)
        ):
            successful.append(dict(spec))
    return successful


def _successful_executed_draw_actions(
    actions: list[dict[str, Any]],
    batch_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Align planned actions to child results and retain delivered draws only."""
    planned_draws = [
        action for action in actions if action.get("action") == "draw_strokes"
    ]
    successful: list[dict[str, Any]] = []
    draw_index = 0
    for result in batch_results:
        if not isinstance(result, dict) or result.get("action") != "draw_strokes":
            continue
        action = planned_draws[draw_index] if draw_index < len(planned_draws) else None
        draw_index += 1
        if action is None:
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        deferred_delivery = bool(
            action.get("defer_visual_verify")
            and data.get("visual_verify_deferred") is True
        )
        result_ok = result.get("ok")
        if result_ok is None:
            result_ok = data.get("ok")
        result_verified = result.get("verified")
        if result_verified is None:
            result_verified = data.get("verified")
        if (
            result_ok is True
            and (result_verified is not False or deferred_delivery)
        ):
            successful.append(action)
    return successful


def _merge_draw_scene_specs(
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in [*existing, *additions]:
        if not isinstance(spec, dict):
            continue
        key = json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(spec))
    return merged[-80:]


def _click_sequence_as_keyboard_text(request: SubgoalRequest,
                                     click_specs: list[dict[str, Any]]) -> str:
    if request.input or len(click_specs) < 2:
        return ""
    scope_text = _compact_key(
        " ".join([
            request.subgoal,
            " ".join(request.verify_texts),
            json.dumps(request.constraints, ensure_ascii=False, default=str),
        ])
    )
    if not any(_compact_key(token) in scope_text for token in _PRINTABLE_SEQUENCE_TRIGGERS):
        return ""
    out: list[str] = []
    for spec in click_specs:
        if any(key in spec for key in ("x", "y", "element_id", "anchor", "double", "drag")):
            return ""
        count_source = spec.get("click_count") or spec.get("count") or spec.get("times")
        try:
            count = int(count_source or 1)
        except (TypeError, ValueError):
            return ""
        if count != 1:
            return ""
        label = spec.get("label") or spec.get("text") or spec.get("name")
        key = _click_label_to_printable_key(label)
        if key is None:
            return ""
        out.append(key)
    text = "".join(out)
    # Avoid converting generic two-button UI choices such as A/B unless the
    # sequence contains an obvious text/calculation shape.
    if not any(ch.isdigit() or ch in "+-*/=." for ch in text) and len(text) < 4:
        return ""
    return text


def _direct_keyboard_text(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not value or "\n" in value or "\r" in value or len(value) > 180:
        return ""
    if not value.isprintable():
        return ""
    return value


def _type_into_element_failed_to_focus(batch: dict[str, Any]) -> bool:
    results = batch.get("results") or []
    if not isinstance(results, list) or not results:
        return False
    first = results[0]
    if not isinstance(first, dict) or first.get("action") != "type_into_element":
        return False
    data = first.get("data") if isinstance(first.get("data"), dict) else {}
    if data.get("ok") is True:
        return False
    reason = " ".join(str(data.get(key) or "") for key in ("stage", "error", "via")).casefold()
    return (
        "focus" in reason
        or "could not focus" in reason
        or "could not locate" in reason
        or "locator" in reason
    )


def _merge_fallback_batch(primary: dict[str, Any], fallback: dict[str, Any],
                          fallback_actions: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(fallback)
    merged["fallback_from"] = "type_into_element"
    merged["fallback_actions"] = fallback_actions
    merged["primary_failed_results"] = primary.get("results") or []
    merged["results"] = list(primary.get("results") or []) + list(fallback.get("results") or [])
    metrics = dict(fallback.get("metrics") or {})
    primary_metrics = primary.get("metrics") if isinstance(primary.get("metrics"), dict) else {}
    try:
        metrics["elapsed_ms"] = int(primary_metrics.get("elapsed_ms") or 0) + int(metrics.get("elapsed_ms") or 0)
    except (TypeError, ValueError):
        pass
    try:
        metrics["action_count"] = int(primary_metrics.get("action_count") or 0) + int(metrics.get("action_count") or 0)
    except (TypeError, ValueError):
        pass
    metrics["fallback_from_type_into_element"] = True
    merged["metrics"] = metrics
    return merged


def _task_visible_targets(task: str) -> list[str]:
    return _unique([*_required_visible_payloads(task), *_visible_state_targets(task)])


def _looks_like_distinctive_state_token(text: str) -> bool:
    value = str(text or "")
    return bool(
        "_" in value
        or re.search(r"[A-Z]{2,}", value)
        or re.search(r"[A-Za-z]+\d|\d+[A-Za-z]", value)
    )


def _expand_verify_texts(verify_texts: list[str], task: str) -> list[str]:
    targets = _task_visible_targets(task)
    out: list[str] = []
    for text in verify_texts:
        key = _compact_key(text)
        if key.isdigit() and len(key) <= 2:
            expanded = [
                target for target in targets
                if _compact_key(target).endswith(key)
                and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in str(target))
            ]
            out.extend(expanded)
            continue
        out.append(text)
    out = _unique(out)
    if any(_looks_like_distinctive_state_token(text) for text in out):
        distinctive = [text for text in out if _looks_like_distinctive_state_token(text)]
        if distinctive:
            return _unique(distinctive)
    return out


def _scope_mentions(target: str, *parts: Any) -> bool:
    key = _compact_key(target)
    if not key:
        return False
    haystack = "\n".join(str(part or "") for part in parts)
    haystack_key = _compact_key(haystack)
    return key in haystack_key


_CN_CLICK_LABEL_RE = re.compile(
    r"(?:点击|点按|按下|按|选择|打开)\s*[\"“'‘]?([^，。；;,.!！?？\n\"”'’]{1,24}?)[\"”'’]?\s*(?:按钮|按键|菜单|链接)"
)
_EN_CLICK_LABEL_RE = re.compile(
    r"\b(?:click|press|tap|select|open)\s+(?:the\s+)?[\"']?(.{1,32}?)[\"']?\s+"
    r"(?:button|btn|menu|link)\b",
    re.IGNORECASE,
)


def _infer_click_spec_from_subgoal(subgoal: str) -> dict[str, Any]:
    text = str(subgoal or "").strip()
    if not text:
        return {}
    for pattern in (_CN_CLICK_LABEL_RE, _EN_CLICK_LABEL_RE):
        match = pattern.search(text)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n:：-—")
        if label:
            return {"label": label, "role": "button"}
    return {}


def _infer_semantic_region(text: str) -> str:
    compact = _compact_key(text)
    list_markers = (
        "会话列表", "联系人列表", "聊天列表", "左侧列表",
        "conversationlist", "contactlist", "chatlist", "sidebarlist", "listpane",
    )
    if any(_compact_key(marker) in compact for marker in list_markers):
        return "left-list"
    return ""


def _mentions_input_action(subgoal: str) -> bool:
    text = str(subgoal or "").casefold()
    return bool(re.search(
        r"\b(type|input|fill|write|enter|paste)\b|"
        r"(输入|填写|键入|写入|粘贴)",
        text,
        re.IGNORECASE,
    ))


def _natural_navigation_input(
    subgoal: str,
    click_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Recover a URL input routine omitted from otherwise explicit tool args."""
    if not click_spec or not _mentions_input_action(subgoal):
        return None
    match = re.search(r"https?://[^\s\"'<>]+", str(subgoal or ""), re.IGNORECASE)
    if not match:
        return None
    url = match.group(0).rstrip(".,;:!?)]}，。；：！？")
    if not url:
        return None
    anchor = {
        key: value
        for key, value in click_spec.items()
        if key in {"label", "expected_region", "visual_anchor", "fallback_text", "fallback"}
        and value not in (None, "", [])
    }
    anchor.update({
        "text": url,
        "clear": True,
        "role": "edit",
        "navigation": True,
    })
    return anchor, {"submit_with_enter": True, "wait": True}


def _input_requests_enter_submit(subgoal: str, input_spec: dict[str, Any]) -> bool:
    text = str(subgoal or "")
    if re.search(
        r"\b(?:press|hit)\s+(?:the\s+)?enter\b|\bthen\s+enter\b|"
        r"按(?:下)?\s*(?:Enter|回车)|回车(?:键)?|提交搜索",
        text,
        re.IGNORECASE,
    ):
        return True
    value = str(input_spec.get("text") or input_spec.get("value") or "").strip()
    return bool(
        re.match(r"^https?://", value, re.IGNORECASE)
        and re.search(r"\b(?:navigate|visit|open|access|go\s+to)\b|访问|进入|打开", text, re.IGNORECASE)
    )


def _same_input_anchor(input_spec: dict[str, Any], click_spec: dict[str, Any]) -> bool:
    input_label = _compact_key(input_spec.get("label") or input_spec.get("name") or "")
    click_label = _compact_key(click_spec.get("label") or click_spec.get("name") or "")
    if not input_label or not click_label:
        return False
    return input_label == click_label or input_label in click_label or click_label in input_label


def _is_browser_location_click(spec: dict[str, Any]) -> bool:
    values = [
        spec.get("label"),
        spec.get("text"),
        spec.get("name"),
        spec.get("role"),
        *(spec.get("fallback_text") or []),
    ]
    joined = " ".join(str(value or "") for value in values).casefold()
    return bool(re.search(
        r"\b(?:address(?:\s+and\s+search)?\s+bar|location\s+bar|url\s+bar|omnibox)\b|"
        r"地址(?:和搜索)?栏|网址栏",
        joined,
        re.IGNORECASE,
    ))


def parse_subgoal_request(args: dict[str, Any], task: str = "") -> SubgoalRequest:
    input_spec = _as_dict(args.get("input") or args.get("type") or args.get("text_input"))
    constraints = _as_dict(args.get("constraints") or {})
    subgoal_text = str(args.get("subgoal") or args.get("goal") or "").strip()
    raw_clicks = args.get("clicks") or args.get("buttons") or []
    click_items: list[dict[str, Any]] = []
    if isinstance(raw_clicks, list):
        click_items = [item for item in raw_clicks if isinstance(item, dict)]
    click_spec = _as_dict(args.get("click") or args.get("button") or args.get("press"))
    if not click_spec and not click_items and not (
        not input_spec and _mentions_input_action(subgoal_text)
    ):
        click_spec = _infer_click_spec_from_subgoal(subgoal_text)
    draw_spec = _as_dict(args.get("draw") or args.get("drawing") or {})
    if not draw_spec and args.get("shape"):
        draw_spec = {"shape": args.get("shape")}
    raw_draws = args.get("draws") or args.get("drawings") or []
    draw_specs: list[dict[str, Any]] = []
    if isinstance(raw_draws, list):
        draw_specs = [item for item in (_as_dict(item) for item in raw_draws) if item]
    raw_formats = args.get("formats") or []
    format_specs: list[dict[str, Any]] = []
    single_format = _as_dict(args.get("format") or args.get("text_format") or {})
    if single_format:
        format_specs.append(single_format)
    if isinstance(raw_formats, list):
        format_specs.extend(item for item in (_as_dict(item) for item in raw_formats) if item)
    if not input_spec and args.get("text"):
        input_spec = {"text": args.get("text"), "label": args.get("input_label") or args.get("label") or ""}
    if not input_spec:
        inferred_navigation = _natural_navigation_input(subgoal_text, click_spec)
        if inferred_navigation is not None:
            input_spec, inferred_constraints = inferred_navigation
            constraints = {**constraints, **inferred_constraints}
            click_spec = {}
            click_items = []
    if not click_spec and args.get("button_label"):
        click_spec = {"label": args.get("button_label"), "role": "button"}
    if input_spec and _input_requests_enter_submit(subgoal_text, input_spec):
        constraints = {**constraints, "submit_with_enter": True, "wait": True}
        input_spec = {**input_spec, "navigation": True}
        if _same_input_anchor(input_spec, click_spec):
            click_spec = {}
            click_items = []
    clicks: list[dict[str, Any]] = []
    if click_spec:
        clicks.append(click_spec)
    clicks.extend(click_items)
    verify_texts = _expand_verify_texts(
        _as_text_list(args.get("verify_texts") or args.get("verify") or []),
        task,
    )
    if (
        not verify_texts
        and input_spec.get("text")
        and not input_spec.get("navigation")
    ):
        verify_texts.append(str(input_spec.get("text") or ""))
    # Task-level evidence still belongs to the final done gate. A routine should
    # only inherit task literals that are actually part of this subgoal; otherwise
    # an input-only routine can be marked failed because a later button/state
    # requirement has not happened yet.
    scope_text = json.dumps(
        {
            "subgoal": subgoal_text,
            "input": input_spec,
            "click": click_spec,
            "clicks": clicks,
            "draw": draw_spec,
            "draws": draw_specs,
            "formats": format_specs,
            "verify_texts": verify_texts,
        },
        ensure_ascii=False,
        default=str,
    )
    for target in _task_visible_targets(task):
        if not _scope_mentions(target, scope_text):
            continue
        target_key = _compact_key(target)
        covered_by_explicit_target = bool(
            len(target_key) >= 3
            and any(
                target_key != _compact_key(existing)
                and target_key in _compact_key(existing)
                and (
                    _looks_like_distinctive_state_token(existing)
                    or _looks_like_file_path(existing)
                )
                for existing in verify_texts
            )
        )
        if not covered_by_explicit_target:
            verify_texts.append(target)
    return SubgoalRequest(
        subgoal=subgoal_text or str(task or "").strip(),
        target_window=_as_dict(args.get("target_window") or {}),
        input=input_spec,
        click=click_spec,
        clicks=clicks,
        draw=draw_spec,
        draws=draw_specs,
        formats=format_specs,
        verify_texts=_unique(verify_texts),
        constraints=constraints,
    )


def _pick_text(data: dict[str, Any]) -> str:
    def _looks_like_browser_chrome_text(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        return bool(re.search(
            r"^(?:https?://|file://|localhost\b|127\.0\.0\.1\b|\[::1\])|"
            r"(?:\.html?|:\d{2,5}/|\?.*=)",
            value,
            re.IGNORECASE,
        ))

    def _diagnostic_line(line: str) -> bool:
        value = str(line or "").strip()
        lower = value.casefold()
        if not value:
            return False
        diagnostic_markers = (
            "changed-only observe query fallback",
            "no elements matched query",
            "no changed-region text matched query",
            "query fallback",
        )
        if any(marker in lower for marker in diagnostic_markers):
            return True
        if lower.startswith((
            "perception reducer",
            "screen_stage=",
            "target_window=",
            "stats=",
            "top_candidates:",
            "uncertainties:",
            "recent_changes:",
            "recommended_next_controller_actions:",
        )):
            return True
        return False

    def _visible_from_observe_line(line: str) -> str:
        raw = str(line or "")
        stripped = raw.strip()
        if not stripped or _diagnostic_line(stripped):
            return ""
        if stripped.startswith("["):
            match = re.search(r'"([^"]{1,240})"', stripped)
            visible = match.group(1).strip() if match else stripped
            return "" if _looks_like_browser_chrome_text(visible) else visible
        # Reducer candidate rows can contain both visible text and explanation
        # that echoes the query. Keep only the visible text field.
        match = re.search(r'\btext="([^"]{1,240})"', stripped)
        if match:
            visible = match.group(1).strip()
            return "" if _looks_like_browser_chrome_text(visible) else visible
        if re.search(r"\bquery\s*[:=]", stripped, re.IGNORECASE):
            return ""
        if _looks_like_browser_chrome_text(stripped):
            return ""
        return raw

    def _strip_observe_diagnostics(text: str) -> str:
        lines: list[str] = []
        for raw_line in str(text or "").splitlines():
            visible = _visible_from_observe_line(raw_line)
            if not visible:
                continue
            lines.append(visible)
        return "\n".join(lines)

    chunks: list[str] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("action") in {"read_text", "observe"}:
            action = str(item.get("action") or "")
            full_text = item.get("full_text")
            if isinstance(full_text, str):
                chunks.append(_strip_observe_diagnostics(full_text) if action == "observe" else full_text)
            preview = str(item.get("preview") or "")
            chunks.append(_strip_observe_diagnostics(preview) if action == "observe" else preview)
        child_data = item.get("data")
        if isinstance(child_data, dict):
            for key in ("text", "body", "visible_text", "preview"):
                value = child_data.get(key)
                if isinstance(value, str):
                    chunks.append(_strip_observe_diagnostics(value))
    return "\n".join(chunks)


def _last_read_text(data: dict[str, Any]) -> str:
    results = data.get("results") or []
    for item in reversed(results if isinstance(results, list) else []):
        if not isinstance(item, dict) or item.get("action") != "read_text":
            continue
        full_text = item.get("full_text")
        if isinstance(full_text, str) and full_text.strip():
            return full_text
        child = item.get("data") if isinstance(item.get("data"), dict) else {}
        for value in (child.get("text"), child.get("body"), child.get("preview"), item.get("preview")):
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _navigation_evidence(request: SubgoalRequest, batch: dict[str, Any]) -> dict[str, Any]:
    requested = str(request.input.get("text") or request.input.get("value") or "").strip()
    if not request.input.get("navigation") or not re.match(r"^https?://", requested, re.IGNORECASE):
        return {"required": False, "ok": True}
    final_text = _last_read_text(batch)
    urls: list[str] = []
    for line in final_text.splitlines()[:40]:
        for match in re.finditer(
            r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?",
            line,
            re.IGNORECASE,
        ):
            candidate = match.group(0).rstrip(".,;:!?)]}")
            if candidate and candidate not in urls:
                urls.append(candidate)
    final_url = urls[0] if urls else ""
    requested_leaf = requested.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    requested_leaf = re.sub(r"\.(?:html?|aspx?|php|jsp)$", "", requested_leaf, flags=re.IGNORECASE)
    identity_matched = bool(
        final_url
        and (
            (len(requested_leaf) >= 4 and requested_leaf.casefold() in final_url.casefold())
            or requested.casefold().rstrip("/") in final_url.casefold().rstrip("/")
        )
    )
    page_changed = any(
        isinstance(item, dict)
        and item.get("action") == "wait_change"
        and isinstance(item.get("data"), dict)
        and (
            item["data"].get("changed") is True
            or (
                isinstance(item["data"].get("delta"), dict)
                and item["data"]["delta"].get("changed") is True
            )
        )
        for item in (batch.get("results") or [])
    )
    return {
        "required": True,
        "ok": bool(page_changed and identity_matched),
        "requested_url": requested,
        "final_url": final_url,
        "page_changed": page_changed,
        "identity_matched": identity_matched,
    }


def _public_batch_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    public: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        public.append({
            key: value
            for key, value in item.items()
            if key != "full_text"
        })
    return public


def _verified_expect_texts(actions: list[dict[str, Any]], data: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    results = data.get("results") or []
    if not isinstance(results, list):
        return matched
    for action, result in zip(actions, results):
        if not isinstance(result, dict):
            continue
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        verified = result.get("verified") is True or result_data.get("verified") is True
        expect = action.get("expect") if isinstance(action.get("expect"), dict) else {}
        text = expect.get("text_appears")
        if verified and isinstance(text, str) and text.strip():
            matched.append(text.strip())
        file_path = expect.get("file_exists")
        if verified and isinstance(file_path, str) and file_path.strip():
            matched.append(file_path.strip())
    return _unique(matched)


def _document_format_actions(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate a semantic rich-text range format into stable keyboard actions."""
    range_name = str(spec.get("range") or spec.get("target") or "").strip().casefold()
    range_name = range_name.replace("-", "_").replace(" ", "_")
    aliases = {
        "heading": "first_line",
        "title": "first_line",
        "body": "body_after_first_line",
        "rest": "body_after_first_line",
        "remaining_text": "body_after_first_line",
        "document": "all",
        "all_document": "all",
    }
    range_name = aliases.get(range_name, range_name)
    if range_name == "first_line":
        actions = [
            {"action": "hotkey", "keys": ["ctrl", "home"]},
            {"action": "hotkey", "keys": ["shift", "end"]},
        ]
    elif range_name == "body_after_first_line":
        actions = [
            {"action": "hotkey", "keys": ["ctrl", "home"]},
            # Move by paragraph rather than by rendered line. A centered or
            # wrapped heading can make plain Down land in an unexpected visual
            # row and include the heading in the body alignment selection.
            {"action": "hotkey", "keys": ["ctrl", "down"]},
            {"action": "hotkey", "keys": ["ctrl", "shift", "end"]},
        ]
    elif range_name == "current_line":
        actions = [
            {"action": "hotkey", "keys": ["home"]},
            {"action": "hotkey", "keys": ["shift", "end"]},
        ]
    elif range_name == "all":
        actions = [{"action": "hotkey", "keys": ["ctrl", "a"]}]
    else:
        return []

    if spec.get("bold") is True:
        actions.append({"action": "hotkey", "keys": ["ctrl", "b"]})
    if spec.get("italic") is True:
        actions.append({"action": "hotkey", "keys": ["ctrl", "i"]})
    alignment = str(spec.get("alignment") or spec.get("align") or "").strip().casefold()
    shortcut = {
        "left": "l",
        "center": "e",
        "centre": "e",
        "right": "r",
        "justify": "j",
    }.get(alignment)
    if shortcut:
        actions.append({"action": "hotkey", "keys": ["ctrl", shortcut]})
    return actions


def _document_format_prefix_ok(
    actions: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> bool:
    """Return true when formatting finished even if a later save step failed."""
    format_end = 0
    for index, action in enumerate(actions, start=1):
        keys = [str(key).casefold() for key in (action.get("keys") or [])]
        if action.get("action") == "hotkey" and keys == ["ctrl", "end"]:
            format_end = index
            break
    if not format_end or len(results) < format_end:
        return False
    return all(
        isinstance(item, dict) and item.get("ok") is True
        for item in results[:format_end]
    )


def _visual_colors_from_batch(data: dict[str, Any]) -> dict[str, Any]:
    colors: dict[str, Any] = {}
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        item_data = item.get("data") if isinstance(item.get("data"), dict) else {}
        summary = item_data.get("nonvisual_summary") if isinstance(item_data.get("nonvisual_summary"), dict) else {}
        visible = summary.get("visible_colors") if isinstance(summary.get("visible_colors"), dict) else {}
        for color, stats in visible.items():
            colors[str(color)] = stats
    return colors


def _matched_executed_draw_colors(
    requested_colors: list[str],
    per_draw_color_evidence: list[dict[str, Any]],
) -> list[str]:
    """Match colors only to successfully executed primitive evidence."""
    executed_color_matches = {
        str(item.get("requested_color") or "")
        for item in per_draw_color_evidence
        if isinstance(item, dict) and item.get("matched") is True
    }
    return [color for color in requested_colors if color in executed_color_matches]


def _requested_draw_colors(request: SubgoalRequest, task: str) -> list[str]:
    colors: list[str] = []
    for draw_spec in _draw_specs(request):
        color = _requested_color(draw_spec, request.subgoal, task)
        if color:
            colors.append(color)
    return _unique(colors)


def _required_user_draw_colors(task: str) -> list[str]:
    """Return only colors explicitly requested by the user-level task.

    Colors introduced by the planner are aesthetic preferences, not hard
    postconditions.  Keeping this distinction prevents an unavailable optional
    palette shade from aborting an otherwise valid drawing routine.
    """
    # Do not seed this from the legacy substring matcher: words such as
    # "ordered" contain "red" and can turn a Controller instruction into a
    # fabricated user color postcondition.  Only explicit bounded color terms
    # in the user-level task are strict.
    colors: list[str] = []
    text = str(task or "").casefold()
    for color, terms in _DRAW_COLOR_TERMS.items():
        if color in colors:
            continue
        if any(
            (
                term.isascii()
                and re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text)
            )
            or (
                not term.isascii()
                and len(term) >= 2
                and term.endswith("色")
                and term in text
            )
            for term in terms
        ):
            colors.append(color)
    return _unique(color for color in colors if color)


def _rough_region(window: Region, hint: str) -> Region | None:
    hint = (hint or "").strip().casefold().replace("_", "-").replace(" ", "-")
    hint = {
        "center-left": "left",
        "middle-left": "left",
        "left-center": "left",
        "center-right": "right",
        "middle-right": "right",
        "right-center": "right",
    }.get(hint, hint)
    if not hint:
        return None
    left, top, width, height = window.left, window.top, window.width, window.height
    if hint in {"top", "upper"}:
        return Region(left, top, width, max(80, int(height * 0.46))).clamp_to_screen()
    if hint in {"bottom", "lower"}:
        h = max(80, int(height * 0.46))
        return Region(left, top + height - h, width, h).clamp_to_screen()
    if hint == "left":
        trim = min(96, max(0, int(height * 0.08))) if height >= 420 else 0
        return Region(left, top + trim, max(100, int(width * 0.36)), max(80, height - trim)).clamp_to_screen()
    if hint == "right":
        trim = min(96, max(0, int(height * 0.08))) if height >= 420 else 0
        w = max(100, int(width * 0.36))
        return Region(left + width - w, top + trim, w, max(80, height - trim)).clamp_to_screen()
    if hint in {"top-right", "upper-right"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left + width - w, top, w, h).clamp_to_screen()
    if hint in {"top-left", "upper-left"}:
        return Region(left, top, max(100, int(width * 0.55)), max(80, int(height * 0.48))).clamp_to_screen()
    if hint in {"bottom-right", "lower-right"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left + width - w, top + height - h, w, h).clamp_to_screen()
    if hint in {"bottom-left", "lower-left"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left, top + height - h, w, h).clamp_to_screen()
    if hint in {"center", "middle"}:
        w = max(140, int(width * 0.82))
        h = max(100, int(height * 0.72))
        return Region(left + (width - w) // 2, top + (height - h) // 2, w, h).clamp_to_screen()
    return None


def _click_terms(request: SubgoalRequest) -> list[str]:
    terms: list[str] = []
    for spec in request.clicks or ([request.click] if request.click else []):
        for key in ("label", "text", "name"):
            value = str(spec.get(key) or "").strip()
            if value:
                terms.append(value)
        fallback = spec.get("fallback_text") or spec.get("fallback") or []
        if isinstance(fallback, str):
            fallback = [fallback]
        if isinstance(fallback, list):
            terms.extend(str(item or "").strip() for item in fallback if str(item or "").strip())
    return _unique(terms)


def _region_hint(request: SubgoalRequest) -> str:
    for spec in request.clicks or ([request.click] if request.click else []):
        value = str(spec.get("expected_region") or "").strip()
        if value:
            return value
    for draw_spec in _draw_specs(request):
        value = str(draw_spec.get("expected_region") or draw_spec.get("region") or "").strip()
        if value:
            return value
    return ""


def _draw_specs(request: SubgoalRequest) -> list[dict[str, Any]]:
    if request.draws:
        return request.draws
    return [request.draw] if request.draw else []


def _semantic_part(draw_spec: dict[str, Any]) -> str:
    value = str(
        draw_spec.get("part")
        or draw_spec.get("semantic_part")
        or draw_spec.get("expected_region")
        or ""
    ).strip()
    if value.casefold() in {"canvas", "center", "middle", "left", "right", "top", "bottom"}:
        return ""
    return value


def _effective_normalized_strokes(draw_spec: dict[str, Any]) -> Any:
    """Return only geometry explicitly supplied by the model.

    The controller validates and maps coordinates; it must not infer anatomy or
    silently turn generic boxes/short lines into an object-specific outline.
    Visual quality belongs to the post-action pixel judge.
    """
    return draw_spec.get("normalized_strokes")
def _custom_contour_metrics(draw_spec: dict[str, Any]) -> dict[str, Any]:
    """Summarise an expressive canvas-relative polyline without trusting shape.

    ``normalized_strokes`` overrides the primitive shape at execution time.  A
    non-empty field with two points is merely a straight line, though, so the
    polished-scene gate only counts paths with at least five distinct points.
    """
    raw_strokes = _effective_normalized_strokes(draw_spec)
    if not isinstance(raw_strokes, list):
        return {"valid": False, "point_count": 0, "area": 0.0}
    points: list[tuple[float, float]] = []
    for raw_stroke in raw_strokes:
        if not isinstance(raw_stroke, list):
            continue
        for raw_point in raw_stroke:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                continue
            try:
                x = float(raw_point[0])
                y = float(raw_point[1])
            except (TypeError, ValueError):
                continue
            if 0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0:
                points.append((x, y))
    distinct = list(dict.fromkeys(points))
    if len(distinct) < 5:
        return {
            "valid": False,
            "point_count": len(points),
            "distinct_point_count": len(distinct),
            "area": 0.0,
        }
    xs = [point[0] for point in distinct]
    ys = [point[1] for point in distinct]
    return {
        "valid": True,
        "point_count": len(points),
        "distinct_point_count": len(distinct),
        "area": max(0.0, max(xs) - min(xs)) * max(0.0, max(ys) - min(ys)),
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
    }


def _line_box_coordinates(
    raw: Any,
    mode: str = "",
    *,
    semantic_part: str = "",
) -> tuple[float, float, float, float, bool] | None:
    """Resolve a typed four-value line box without semantic guessing."""
    del semantic_part
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        first_x, first_y, third, fourth = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    normalized_mode = str(mode or "").strip().casefold().replace("-", "_")
    if normalized_mode in {"xywh", "offset", "width_height"}:
        use_offsets = True
    else:
        # The public schema defines endpoints as the default.  Silently
        # choosing the shorter interpretation made malformed model output look
        # successful while drawing somewhere else.
        use_offsets = False
    second_x = first_x + third if use_offsets else third
    second_y = first_y + fourth if use_offsets else fourth
    return first_x, first_y, second_x, second_y, use_offsets


def _planned_geometry_bbox(draw_spec: dict[str, Any]) -> list[float] | None:
    metrics = _custom_contour_metrics(draw_spec)
    if metrics.get("valid") and isinstance(metrics.get("bbox"), list):
        return [float(value) for value in metrics["bbox"]]
    raw_box = draw_spec.get("box")
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return None
    try:
        x, y, third, fourth = [float(value) for value in raw_box]
    except (TypeError, ValueError):
        return None
    mode = str(draw_spec.get("box_mode") or "").strip().casefold()
    shape = str(draw_spec.get("shape") or "").strip().casefold()
    if shape == "line":
        resolved = _line_box_coordinates(
            raw_box,
            mode,
            semantic_part=_semantic_part(draw_spec),
        )
        if resolved is None:
            return None
        x, y, third, fourth, _use_offsets = resolved
        left, right = sorted((x, third))
        top, bottom = sorted((y, fourth))
    elif mode in {"endpoints", "points", "xyxy"}:
        left, right = sorted((x, third))
        top, bottom = sorted((y, fourth))
    else:
        left, top = x, y
        right, bottom = x + max(0.0, third), y + max(0.0, fourth)
    return [
        max(0.0, min(1000.0, left)),
        max(0.0, min(1000.0, top)),
        max(0.0, min(1000.0, right)),
        max(0.0, min(1000.0, bottom)),
    ]


def _scene_structure_preflight(
    draw_specs: list[dict[str, Any]],
    task: str,
    *,
    enforce_primitive_ceiling: bool = True,
) -> dict[str, Any]:
    """Validate executable scene payloads without encoding an aesthetic recipe.

    Rich drawing requests are test cases for the same visual closed loop used
    elsewhere. The controller may reject technically unsupported payloads, but
    primitive counts, anatomy, composition and attractiveness are judged from
    the fresh rendered frame instead of from animal- or app-specific rules.
    The primitive-ceiling argument remains only for call compatibility.
    """
    del enforce_primitive_ceiling
    del task
    if not draw_specs:
        return {"required": False, "ok": True}

    colors = sorted({
        str(spec.get("color") or spec.get("colour") or "").strip().casefold()
        for spec in draw_specs
        if str(spec.get("color") or spec.get("colour") or "").strip()
    })
    parts = [_semantic_part(spec) for spec in draw_specs]
    labelled = [part for part in parts if part]
    distinct_parts = sorted({part.casefold() for part in labelled})
    contour_metrics = [_custom_contour_metrics(spec) for spec in draw_specs]
    expressive_contours = [
        {"part": _semantic_part(spec), **metrics}
        for spec, metrics in zip(draw_specs, contour_metrics)
        if metrics.get("valid")
    ]

    errors: list[str] = []
    filled_parts = [
        _semantic_part(spec) or str(index + 1)
        for index, spec in enumerate(draw_specs)
        if spec.get("filled") is True or spec.get("fill") is True
    ]
    if filled_parts:
        errors.append(
            "drawing primitives are outline-only; filled/fill=true is unsupported "
            f"({', '.join(filled_parts[:4])})"
        )

    warnings: list[str] = []
    geometry_groups: dict[tuple[float, ...], list[str]] = {}
    for spec in draw_specs:
        bbox = _planned_geometry_bbox(spec)
        if bbox is None:
            continue
        signature = tuple(round(value, 1) for value in bbox)
        geometry_groups.setdefault(signature, []).append(
            _semantic_part(spec) or "unlabelled"
        )
    collapsed = max(geometry_groups.values(), key=len, default=[])
    if len(collapsed) >= 3:
        warnings.append(
            "several primitives share the same geometry; verify the rendered "
            f"composition visually ({', '.join(collapsed[:6])})"
        )

    return {
        "required": True,
        "ok": not errors,
        "quality_deferred_to_visual_judge": True,
        "primitive_count": len(draw_specs),
        "requested_colors": colors,
        "semantic_part_count": len(labelled),
        "distinct_semantic_parts": distinct_parts,
        "expressive_contour_count": len(expressive_contours),
        "expressive_contour_point_count": sum(
            int(item.get("point_count") or 0) for item in expressive_contours
        ),
        "warnings": warnings,
        "errors": errors,
    }
def _elements_contain_terms(elements: list[UIElement], terms: list[str]) -> bool:
    if not terms:
        return bool(elements)
    return any(
        _scope_mentions(term, element.text)
        for term in terms
        for element in elements
    )


_CHROME_TEXT_RE = re.compile(r"^(?:win!?|chat\(?|https?://|127\.0\.0\.1|ch\b)", re.IGNORECASE)


def _point_in_region(element: UIElement, region: Region) -> bool:
    try:
        return region.left <= int(element.cx) <= region.right and region.top <= int(element.cy) <= region.bottom
    except (TypeError, ValueError):
        return False


def _visible_region_text_candidates(
    snapshot: Snapshot | None,
    request: SubgoalRequest,
    limit: int = 16,
) -> list[str]:
    if snapshot is None:
        return []
    hint = _region_hint(request)
    region = _rough_region(snapshot.region, hint) if hint else snapshot.region
    if region is None:
        region = snapshot.region
    # Skip browser/title chrome for candidate hints; the raw snapshot remains in
    # trace, this list is only a recovery nudge after an anchor miss.
    top_cutoff = snapshot.region.top + max(72, int(snapshot.region.height * 0.08))
    texts: list[str] = []
    for element in snapshot.elements:
        text = str(getattr(element, "text", "") or "").strip()
        if not text or len(text) > 42:
            continue
        if _CHROME_TEXT_RE.search(text):
            continue
        if not _point_in_region(element, region):
            continue
        if getattr(element, "cy", 0) < top_cutoff:
            continue
        width = int(getattr(element, "width", 0) or 0)
        height = int(getattr(element, "height", 0) or 0)
        if width > 280 or height > 56:
            continue
        texts.append(text)
    return _unique(texts)[:limit]


def _draw_shape_name(draw_spec: dict[str, Any], subgoal: str, task: str) -> str:
    """Return only an explicitly typed primitive name.

    Natural-language interpretation belongs to the model.  The controller
    must not turn task words into geometry behind the model's back.
    """
    del subgoal, task
    explicit = " ".join([
        str(draw_spec.get("shape") or ""),
        str(draw_spec.get("object") or ""),
    ]).casefold()
    if any(term in explicit for term in ("triangle", "三角", "三角形")):
        return "triangle"
    if any(term in explicit for term in ("rectangle", "square", "方形", "矩形", "正方形")):
        return "rectangle"
    if any(term in explicit for term in ("circle", "圆", "圆形")):
        return "circle"
    if any(term in explicit for term in ("ellipse", "oval", "椭圆")):
        return "ellipse"
    if any(term in explicit for term in ("line", "线", "直线")):
        return "line"
    if any(term in explicit for term in ("check", "勾", "对号")):
        return "check"
    return "freeform"


_DRAW_COLOR_TERMS = {
    "lime": ("lime", "lime green", "chartreuse", "\u4eae\u7eff", "\u9ec4\u7eff\u8272"),
    "cyan": ("cyan", "aqua", "\u9752\u8272", "\u84dd\u7eff\u8272"),
    "magenta": ("magenta", "fuchsia", "\u54c1\u7ea2", "\u6d0b\u7ea2"),
    "blue": ("blue", "蓝", "蓝色"),
    "red": ("red", "红", "红色"),
    "green": ("green", "绿", "绿色"),
    "yellow": ("yellow", "黄", "黄色"),
    "black": ("black", "黑", "黑色"),
    "purple": ("purple", "紫", "紫色"),
    "orange": ("orange", "橙", "橙色"),
    "brown": ("brown", "棕", "棕色", "褐", "褐色"),
    "pink": ("pink", "粉", "粉色"),
    "gray": ("gray", "grey", "灰", "灰色"),
    "white": ("white", "白", "白色"),
}

_DRAW_COLOR_RGB = {
    "lime": (128, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (220, 45, 45),
    "green": (50, 180, 80),
    "blue": (65, 105, 225),
    "yellow": (255, 215, 0),
    "purple": (147, 112, 219),
    "orange": (255, 107, 53),
    "brown": (150, 75, 0),
    "pink": (255, 105, 180),
    "gray": (128, 128, 128),
}

_DRAW_COLOR_HEX_ALIASES = {
    "#ffc0cb": "pink",
    "#ff1493": "pink",
    "#d2691e": "orange",
    "#8b4513": "brown",
}


def _canonical_color_name(value: Any) -> str:
    """Map model color names/hex values to the controller palette vocabulary."""
    explicit = str(value or "").strip().casefold()
    if not explicit:
        return ""
    for color, terms in _DRAW_COLOR_TERMS.items():
        if any(term in explicit for term in terms):
            return color
    hex_match = re.search(r"#([0-9a-f]{6})\b", explicit, re.IGNORECASE)
    if not hex_match:
        return ""
    alias = _DRAW_COLOR_HEX_ALIASES.get(f"#{hex_match.group(1).casefold()}")
    if alias:
        return alias
    value = hex_match.group(1)
    rgb = tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    return min(
        _DRAW_COLOR_RGB,
        key=lambda name: sum(
            (rgb[channel] - _DRAW_COLOR_RGB[name][channel]) ** 2
            for channel in range(3)
        ),
    )


def _requested_color(draw_spec: dict[str, Any], subgoal: str, task: str) -> str:
    """Return only a color carried by the typed draw payload."""
    del subgoal, task
    explicit = " ".join([
        str(draw_spec.get("color") or ""),
        str(draw_spec.get("colour") or ""),
    ])
    if explicit.strip():
        canonical = _canonical_color_name(explicit)
        if canonical:
            return canonical
    return ""


def _canvas_region_from_image(window: Region, image) -> Region | None:
    detection = vision_cv.credible_light_work_surface(image)
    if not isinstance(detection, dict) or detection.get("ok") is not True:
        return None
    bbox = detection.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [int(value) for value in bbox]
    return Region(
        window.left + x1,
        window.top + y1,
        max(1, x2 - x1),
        max(1, y2 - y1),
    ).clamp_to_screen()


def _fallback_canvas_region(window: Region) -> Region:
    left = window.left + max(24, int(window.width * 0.12))
    top = window.top + max(96, int(window.height * 0.24))
    width = max(120, int(window.width * 0.72))
    height = max(90, int(window.height * 0.48))
    return Region(left, top, width, height).clamp_to_screen()


_CANVAS_TRANSFORM_CACHE_KEY = "_canvas_transform_cache"


def _region_payload(region: Region) -> dict[str, int]:
    return {
        "x": int(region.left),
        "y": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


def _region_from_payload(raw: Any) -> Region | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Region(
            int(raw["x"]),
            int(raw["y"]),
            int(raw["width"]),
            int(raw["height"]),
        ).clamp_to_screen()
    except (KeyError, TypeError, ValueError):
        return None


def _drawing_canvas_generation_token(session: Any) -> str:
    """Return a stable document-generation token without using screen epochs.

    ``_screen_epoch`` changes after every delivered stroke, so it cannot key a
    coordinate transform. Hosts which know that a new document/canvas was
    created can expose one of the explicit generation counters below.
    """
    parts: list[str] = []
    for name in (
        "_canvas_generation",
        "_drawing_generation",
        "_document_generation",
    ):
        if not hasattr(session, name):
            continue
        value = getattr(session, name, None)
        parts.append(f"{name}={value!r}")
    return "|".join(parts) or "default"


def _same_window_geometry(cached: Region | None, current: Region | None) -> bool:
    if cached is None or current is None:
        return False
    position_tolerance = max(8, int(round(max(current.width, current.height) * 0.01)))
    size_tolerance = max(12, int(round(max(current.width, current.height) * 0.02)))
    return bool(
        abs(cached.left - current.left) <= position_tolerance
        and abs(cached.top - current.top) <= position_tolerance
        and abs(cached.width - current.width) <= size_tolerance
        and abs(cached.height - current.height) <= size_tolerance
    )


def _region_inside(container: Region, inner: Region, margin: int = 3) -> bool:
    return bool(
        container.left - margin <= inner.left
        and container.top - margin <= inner.top
        and inner.right <= container.right + margin
        and inner.bottom <= container.bottom + margin
    )


def _regions_overlap(first: Region, second: Region) -> bool:
    return bool(
        min(first.right, second.right) > max(first.left, second.left)
        and min(first.bottom, second.bottom) > max(first.top, second.top)
    )


def _cached_logical_scene_canvas(
    previous: dict[str, Any] | None,
    detected_canvas: Region,
    *,
    hwnd: int | None,
    window: Region | None,
    generation: str,
) -> Region | None:
    """Reuse the first scene transform while its real window generation lives.

    Ink can split a white canvas into multiple bright components.  Those
    components are useful evidence that a canvas still exists, but they must
    not become a new 0..1000 coordinate origin on every repair pass.
    """
    prior = _region_from_payload(previous)
    if prior is None or prior.width != prior.height:
        return None
    metadata = previous.get(_CANVAS_TRANSFORM_CACHE_KEY) if isinstance(previous, dict) else None
    if not isinstance(metadata, dict):
        # Backward compatibility for sessions created before transform metadata
        # existed: only reuse when the new detector still fully contains it.
        return prior if _region_inside(detected_canvas, prior, margin=0) else None
    try:
        cached_hwnd = int(metadata.get("hwnd") or 0)
        current_hwnd = int(hwnd or 0)
    except (TypeError, ValueError):
        return None
    cached_window = _region_from_payload(metadata.get("window"))
    original_canvas = _region_from_payload(metadata.get("detected_canvas"))
    if (
        not cached_hwnd
        or cached_hwnd != current_hwnd
        or str(metadata.get("generation") or "") != str(generation or "")
        or not _same_window_geometry(cached_window, window)
        or window is None
        or original_canvas is None
        or not _region_inside(window, prior)
        or not _region_inside(window, original_canvas)
        or not _region_inside(original_canvas, prior)
        or not _region_inside(window, detected_canvas)
        or not _regions_overlap(original_canvas, detected_canvas)
    ):
        return None
    return prior


def _canvas_transform_cache_payload(
    logical_canvas: Region,
    detected_canvas: Region,
    *,
    hwnd: int | None,
    window: Region,
    generation: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = _region_payload(logical_canvas)
    previous_region = _region_from_payload(previous)
    previous_metadata = (
        previous.get(_CANVAS_TRANSFORM_CACHE_KEY)
        if isinstance(previous, dict) else None
    )
    original_canvas = detected_canvas
    reusable_previous = (
        _cached_logical_scene_canvas(
            previous,
            detected_canvas,
            hwnd=hwnd,
            window=window,
            generation=generation,
        )
        if previous_region == logical_canvas and isinstance(previous_metadata, dict)
        else None
    )
    if reusable_previous == logical_canvas and isinstance(previous_metadata, dict):
        cached_original = _region_from_payload(previous_metadata.get("detected_canvas"))
        if cached_original is not None:
            original_canvas = cached_original
    payload[_CANVAS_TRANSFORM_CACHE_KEY] = {
        "version": 1,
        "hwnd": int(hwnd or 0),
        "window": _region_payload(window),
        "generation": str(generation or ""),
        "detected_canvas": _region_payload(original_canvas),
    }
    return payload


def _logical_scene_canvas(
    canvas: Region,
    draw_specs: list[dict[str, Any]],
    task: str,
    previous: dict[str, Any] | None = None,
    *,
    hwnd: int | None = None,
    window: Region | None = None,
    generation: str = "",
) -> Region:
    """Preserve the detected canvas exactly when mapping typed geometry."""
    del draw_specs, task, previous, hwnd, window, generation
    return canvas


def _looks_like_unready_window(image) -> bool:
    try:
        arr = np.asarray(image)
    except Exception:
        return False
    if arr is None or getattr(arr, "size", 0) == 0:
        return False
    if arr.ndim < 3:
        gray = arr.astype("float32")
    else:
        gray = arr[:, :, :3].astype("float32").mean(axis=2)
    bright_ratio = float((gray > 180).mean())
    mid_ratio = float((gray > 45).mean())
    return bright_ratio < 0.025 and mid_ratio < 0.42


def _inset_region(region: Region, margin_ratio: float = 0.16) -> tuple[int, int, int, int]:
    mx = max(12, int(region.width * margin_ratio))
    my = max(12, int(region.height * margin_ratio))
    left = region.left + mx
    top = region.top + my
    right = region.right - mx
    bottom = region.bottom - my
    if right <= left:
        left = region.left + 8
        right = region.right - 8
    if bottom <= top:
        top = region.top + 8
        bottom = region.bottom - 8
    return left, top, right, bottom


def _shape_strokes(
    shape: str,
    canvas: Region,
    *,
    margin_ratio: float = 0.16,
) -> list[list[list[int]]]:
    explicit_bounds = margin_ratio <= 0
    if explicit_bounds:
        left, top = canvas.left, canvas.top
        right = max(left + 1, canvas.right - 1)
        bottom = max(top + 1, canvas.bottom - 1)
    else:
        left, top, right, bottom = _inset_region(canvas, margin_ratio)
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    if explicit_bounds:
        width = max(2, int(canvas.width))
        height = max(2, int(canvas.height))
        size = max(4, min(width, height, 360))
    else:
        width = max(24, right - left)
        height = max(24, bottom - top)
        size = max(28, min(width, height, 360))
    half = size // 2
    if shape == "triangle":
        p1 = [cx, cy - half]
        p2 = [cx - half, cy + half]
        p3 = [cx + half, cy + half]
        return [[p1, p2, p3, p1]]
    if shape == "rectangle":
        p1 = [left, top]
        p2 = [right, top]
        p3 = [right, bottom]
        p4 = [left, bottom]
        return [[p1, p2, p3, p4, p1]]
    if shape == "circle":
        radius = max(2, min(width, height) // 2) if explicit_bounds else half
        points: list[list[int]] = []
        for i in range(33):
            angle = (math.tau * i) / 32
            points.append([int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle))])
        return [points]
    if shape == "ellipse":
        minimum_radius = 2 if explicit_bounds else 12
        rx = max(minimum_radius, width // 2)
        ry = max(minimum_radius, height // 2)
        points = []
        for i in range(41):
            angle = (math.tau * i) / 40
            points.append([int(cx + rx * math.cos(angle)), int(cy + ry * math.sin(angle))])
        return [points]
    if shape == "line":
        return [[[left, top], [right, bottom]]]
    if shape == "check":
        return [[[cx - half, cy], [cx - half // 4, cy + half], [cx + half, cy - half]]]
    return []


def _normalized_box_region(canvas: Region, raw: Any) -> Region | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, width, height = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    x = min(1000.0, max(0.0, x))
    y = min(1000.0, max(0.0, y))
    width = min(1000.0 - x, max(1.0, width))
    height = min(1000.0 - y, max(1.0, height))
    return Region(
        canvas.left + int(round(canvas.width * x / 1000.0)),
        canvas.top + int(round(canvas.height * y / 1000.0)),
        max(2, int(round(canvas.width * width / 1000.0))),
        max(2, int(round(canvas.height * height / 1000.0))),
    ).clamp_to_screen()


def _normalized_closed_shape_region(
    canvas: Region,
    raw: Any,
    *,
    mode: str = "",
    semantic_part: str = "",
) -> tuple[Region | None, dict[str, Any]]:
    """Map a typed closed-shape box exactly as declared by ``box_mode``."""
    del semantic_part
    metadata: dict[str, Any] = {}
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None, metadata
    try:
        x, y, third, fourth = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None, metadata

    normalized_mode = str(mode or "").strip().casefold().replace("-", "_")
    endpoint_width = third - x
    endpoint_height = fourth - y
    if normalized_mode in {"endpoints", "points", "xyxy"}:
        width, height = endpoint_width, endpoint_height
        interpretation = "xyxy"
    else:
        width, height = third, fourth
        interpretation = "xywh"

    metadata.update({
        "box_interpretation": interpretation,
        "normalized_box": [x, y, width, height],
    })
    return _normalized_box_region(canvas, [x, y, width, height]), metadata


def _normalized_line_geometry(
    canvas: Region,
    raw: Any,
    mode: str = "",
    *,
    semantic_part: str = "",
) -> tuple[list[list[list[int]]], Region] | None:
    """Resolve line endpoints; ``xywh`` is accepted only when explicitly set."""
    resolved = _line_box_coordinates(
        raw,
        mode,
        semantic_part=semantic_part,
    )
    if resolved is None:
        return None
    first_x, first_y, second_x, second_y, _use_offsets = resolved
    first_x = _safe_canvas_coordinate(first_x)
    first_y = _safe_canvas_coordinate(first_y)
    second_x = _safe_canvas_coordinate(second_x)
    second_y = _safe_canvas_coordinate(second_y)

    def screen_point(x: float, y: float) -> list[int]:
        return [
            canvas.left + int(round(canvas.width * x / 1000.0)),
            canvas.top + int(round(canvas.height * y / 1000.0)),
        ]

    first = screen_point(first_x, first_y)
    second = screen_point(second_x, second_y)
    left = min(first[0], second[0])
    top = min(first[1], second[1])
    region = Region(
        left,
        top,
        max(2, abs(second[0] - first[0]) + 1),
        max(2, abs(second[1] - first[1]) + 1),
    ).clamp_to_screen()
    return [[first, second]], region


def _safe_canvas_coordinate(value: float) -> float:
    """Keep normalized drawing points out of canvas borders and scrollbars."""
    bounded = min(1000.0, max(0.0, float(value)))
    if bounded <= 0.0:
        return 30.0
    if bounded >= 1000.0:
        return 970.0
    return bounded


def _normalized_point_geometry(
    canvas: Region,
    raw: Any,
    *,
    close: bool = False,
) -> tuple[list[list[list[int]]], Region] | None:
    """Map a normalized 3+ point path onto the physical canvas."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 6 or len(raw) % 2:
        return None
    try:
        values = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    points = [
        [
            canvas.left + int(round(canvas.width * _safe_canvas_coordinate(values[i]) / 1000.0)),
            canvas.top + int(round(canvas.height * _safe_canvas_coordinate(values[i + 1]) / 1000.0)),
        ]
        for i in range(0, len(values), 2)
    ]
    if close and points[-1] != points[0]:
        points.append(list(points[0]))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    region = Region(
        min(xs),
        min(ys),
        max(2, max(xs) - min(xs) + 1),
        max(2, max(ys) - min(ys) + 1),
    ).clamp_to_screen()
    return [points], region


def _smooth_polyline(
    points: list[list[int]],
    *,
    iterations: int = 2,
) -> list[list[int]]:
    """Round model waypoints with Chaikin subdivision, preserving endpoints."""
    if len(points) < 3:
        return points
    closed = points[0] == points[-1]
    working = [list(point) for point in (points[:-1] if closed else points)]
    for _ in range(max(0, iterations)):
        if len(working) < 3:
            break
        smoothed: list[list[int]] = [] if closed else [working[0]]
        pairs = list(zip(working, working[1:] + ([working[0]] if closed else [])))
        for first, second in pairs:
            smoothed.append([
                int(round(first[0] * 0.75 + second[0] * 0.25)),
                int(round(first[1] * 0.75 + second[1] * 0.25)),
            ])
            smoothed.append([
                int(round(first[0] * 0.25 + second[0] * 0.75)),
                int(round(first[1] * 0.25 + second[1] * 0.75)),
            ])
        if not closed:
            smoothed.append(working[-1])
        working = smoothed
    if closed and working:
        working.append(list(working[0]))
    return working


def _normalized_strokes_to_screen(
    canvas: Region,
    raw: Any,
    *,
    smooth: bool = False,
) -> list[list[list[int]]]:
    if not isinstance(raw, list):
        return []
    strokes: list[list[list[int]]] = []
    for raw_stroke in raw:
        if not isinstance(raw_stroke, list):
            continue
        stroke: list[list[int]] = []
        for raw_point in raw_stroke:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                continue
            try:
                x = _safe_canvas_coordinate(float(raw_point[0]))
                y = _safe_canvas_coordinate(float(raw_point[1]))
            except (TypeError, ValueError):
                continue
            stroke.append([
                canvas.left + int(round(canvas.width * x / 1000.0)),
                canvas.top + int(round(canvas.height * y / 1000.0)),
            ])
        if len(stroke) >= 2:
            if smooth:
                stroke = _smooth_polyline(stroke)
            strokes.append(stroke)
    return strokes


class SubgoalExecutor:
    """Execute one high-level GUI routine inside the fast controller."""

    def __init__(self, session, controller: FastGuiController) -> None:
        self.session = session
        self.controller = controller

    def _target_hwnd(self, request: SubgoalRequest, allow_foreground: bool = True) -> int | None:
        target = request.target_window or {}
        title = str(target.get("title") or "").strip()
        process = str(target.get("process") or target.get("exe") or "").strip()
        hwnd = target.get("hwnd")
        session_hwnd = getattr(self.session, "hwnd", None)
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            # A controller transaction is a mutation, not a target-discovery
            # operation.  In cooperative mode it may consume only the live
            # binding established before dispatch and must never adopt the
            # human's foreground window (including a same-process sibling).
            try:
                live_session_hwnd = bool(
                    session_hwnd
                    and win32gui.IsWindow(int(session_hwnd))
                    and win32gui.IsWindowVisible(int(session_hwnd))
                )
            except (TypeError, ValueError):
                live_session_hwnd = False
            if not live_session_hwnd:
                return None
            try:
                if hwnd and int(hwnd) != int(session_hwnd):
                    return None
            except (TypeError, ValueError):
                return None
            return int(session_hwnd)
        try:
            if hwnd:
                resolver = getattr(self.session, "_resolve_hwnd", None)
                if callable(resolver):
                    resolved = resolver({"hwnd": int(hwnd)})
                    if resolved:
                        return int(resolved)
                if win32gui.IsWindow(int(hwnd)) and win32gui.IsWindowVisible(int(hwnd)):
                    return int(hwnd)
        except (TypeError, ValueError):
            pass
        except Exception:
            pass
        if process:
            try:
                app = win32.find_app(process)
                for candidate in app.get("windows") or []:
                    candidate_hwnd = int(candidate.get("hwnd") or 0)
                    if candidate_hwnd and candidate.get("visible"):
                        setattr(self.session, "hwnd", candidate_hwnd)
                        return candidate_hwnd
            except Exception:
                pass
        if title and allow_foreground:
            try:
                foreground = int(win32gui.GetForegroundWindow())
                if foreground:
                    fg_title = win32._window_text(foreground)
                    if win32.title_contains(fg_title, title):
                        try:
                            setattr(self.session, "hwnd", foreground)
                        except Exception:
                            pass
                        return foreground
            except Exception:
                pass
        if title:
            try:
                found = win32.find_window(title)
                if found and found.get("hwnd"):
                    hwnd = int(found["hwnd"])
                    try:
                        setattr(self.session, "hwnd", hwnd)
                    except Exception:
                        pass
                    return hwnd
            except Exception:
                pass
        if session_hwnd:
            if title:
                try:
                    session_title = win32._window_text(int(session_hwnd))
                    if session_title and not win32.title_contains(session_title, title):
                        return None
                except Exception:
                    pass
            return session_hwnd
        if not allow_foreground:
            return None
        if title:
            return None
        try:
            foreground = int(win32gui.GetForegroundWindow())
            if foreground:
                try:
                    setattr(self.session, "hwnd", foreground)
                except Exception:
                    pass
                return foreground
        except Exception:
            pass
        return None

    def _ensure_observed(self, request: SubgoalRequest) -> tuple[Snapshot | None, list[dict[str, Any]], bool]:
        prelude: list[dict[str, Any]] = []
        skip_click_auto_observe = False
        entry_snapshot = getattr(self.session, "snapshot", None)
        draw_specs = _draw_specs(request)
        pure_draw = bool(draw_specs and not request.input and not request.clicks and not request.verify_texts)
        requires_color_selection = any(
            str(spec.get("color") or "").strip() for spec in draw_specs
        )
        hwnd = self._target_hwnd(request, allow_foreground=not pure_draw)
        cooperative = bool(CONFIG.get(
            "controller", "multi_input_cooperation", default=False,
        ))
        if hwnd:
            if cooperative:
                prelude.append({
                    "action": "preserve_cooperative_foreground",
                    "ok": True,
                    "target_hwnd": int(hwnd),
                    "preview": (
                        "skipped generic focus; scoped observation and each "
                        "concrete action will enforce its own input policy"
                    ),
                })
            else:
                try:
                    focus = self.session.dispatch("focus_window", {"hwnd": hwnd})
                    focus_data = _tool_result_data(focus)
                    focus_ok = bool(
                        focus_data.get("ok") is True
                        and focus_data.get("blocked") is not True
                    )
                    prelude.append({
                        "action": "focus_window",
                        "ok": focus_ok,
                        "blocked": bool(focus_data.get("blocked")),
                        "cooperative_input": bool(focus_data.get("cooperative_input")),
                        "preview": focus.text[:240],
                    })
                    if not focus_ok:
                        return None, prelude, skip_click_auto_observe
                except Exception as exc:
                    prelude.append({
                        "action": "focus_window",
                        "ok": False,
                        "blocked": True,
                        "preview": f"focus_window failed: {exc}"[:240],
                    })
                    return None, prelude, skip_click_auto_observe
            snapshot = getattr(self.session, "snapshot", None)
            try:
                snapshot_hwnd = int(getattr(snapshot, "hwnd", 0) or 0) if snapshot is not None else 0
            except Exception:
                snapshot_hwnd = 0
            real_session_same_hwnd_snapshot = bool(
                snapshot is not None
                and snapshot is entry_snapshot
                and hasattr(self.session, "_screen_epoch")
            )
            if snapshot is not None and (
                (snapshot_hwnd and snapshot_hwnd != int(hwnd))
                or real_session_same_hwnd_snapshot
            ):
                try:
                    self.session.snapshot = None
                except Exception:
                    pass
                prelude.append({
                    "action": "discard_stale_snapshot",
                    "ok": True,
                    "preview": (
                        f"discarded snapshot hwnd={snapshot_hwnd}; target hwnd={int(hwnd)}; "
                        "new controller transaction will refresh structured grounding"
                    ),
                })
        if getattr(self.session, "snapshot", None) is None:
            if pure_draw and not requires_color_selection:
                prelude.append({
                    "action": "draw_direct_canvas_capture",
                    "ok": True,
                    "preview": "colorless draw routine skipped initial observe; controller will wait for a ready canvas and locate the likely drawing surface",
                })
                return None, prelude, skip_click_auto_observe
            # A colored drawing routine needs both the canvas and live tool
            # state (palette, opacity, brush). A canvas-only ROI snapshot can
            # hide those prerequisites and lead to a visually wrong stroke.
            targeted = None if requires_color_selection else self._targeted_initial_snapshot(request)
            if targeted is not None:
                self.session.snapshot = targeted
                targeted_meta = targeted.dim_stats.get("targeted_initial", {})
                prelude.append({
                    "action": "targeted_observe",
                    "ok": True,
                    "preview": targeted.to_text(max_elements=12)[:320],
                    "label_matched": targeted_meta.get("label_matched"),
                    "dim_stats": targeted.dim_stats,
                })
            else:
                observe = self.session.dispatch(
                    "observe", {
                        "full": True,
                        "force_full": True,
                        **({"hwnd": int(hwnd)} if cooperative and hwnd else {}),
                    },
                )
                observe_data = _tool_result_data(observe)
                prelude.append({
                    "action": "observe",
                    "ok": bool(
                        observe_data.get("ok") is not False
                        and observe_data.get("blocked") is not True
                        and getattr(self.session, "snapshot", None) is not None
                    ),
                    "blocked": bool(observe_data.get("blocked")),
                    "preview": observe.text[:320],
                })
        snapshot = getattr(self.session, "snapshot", None)
        return snapshot, prelude, skip_click_auto_observe

    def _targeted_initial_snapshot(self, request: SubgoalRequest) -> Snapshot | None:
        hwnd = self._target_hwnd(request)
        hint = _region_hint(request)
        if not hwnd or not hint:
            return None
        try:
            full_region = window_region(hwnd)
        except Exception:
            return None
        roi = _rough_region(full_region, hint)
        if roi is None:
            return None
        terms = _click_terms(request)
        dim_stats: dict[str, dict[str, Any]] = {}
        elements: list[UIElement] = []
        started = time.monotonic()
        ocr_elements = ocr.collect(roi)
        dim_stats["ocr"] = {
            "count": len(ocr_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "targeted_initial_roi",
            "roi": [roi.left, roi.top, roi.width, roi.height],
        }
        elements.extend(ocr_elements)
        started = time.monotonic()
        cv_elements = vision_cv.detect_icon_candidates(roi)
        dim_stats["cv"] = {
            "count": len(cv_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "targeted_initial_roi",
            "roi": [roi.left, roi.top, roi.width, roi.height],
        }
        elements.extend(cv_elements)
        label_matched = _elements_contain_terms(elements, terms)
        if not label_matched and not elements:
            return None
        for idx, element in enumerate(elements, start=1):
            element.elem_id = idx
            if "targeted_initial_roi" not in element.states:
                element.states.append("targeted_initial_roi")
        dim_stats["targeted_initial"] = {
            "count": len(elements),
            "ms": 0,
            "scope": "targeted_initial_roi",
            "label_matched": label_matched,
            "term_count": len(terms),
        }
        return Snapshot(
            hwnd=int(hwnd),
            title=win32._window_text(int(hwnd)),
            region=full_region,
            elements=elements,
            dim_stats=dim_stats,
        )

    @staticmethod
    def _anchor_fields(spec: dict[str, Any], default_role: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("label", "expected_region", "visual_anchor", "fallback_text", "fallback", "role"):
            if key in spec and spec.get(key) not in (None, "", []):
                out[key] = spec[key]
        if default_role and not out.get("role"):
            out["role"] = default_role
        return out

    def _central_document_focus_action(self, request: SubgoalRequest) -> dict[str, Any] | None:
        hwnd = self._target_hwnd(request, allow_foreground=False)
        if not hwnd:
            return None
        snapshot = getattr(self.session, "snapshot", None)
        region = None
        if int(getattr(snapshot, "hwnd", 0) or 0) == int(hwnd):
            region = getattr(snapshot, "region", None)
        if region is None:
            try:
                region = window_region(hwnd)
            except Exception:
                region = None
        if region is None or region.width < 240 or region.height < 240:
            return None
        return {
            "action": "click",
            "x": int(region.left + region.width * 0.5),
            "y": int(region.top + region.height * 0.58),
            "button": "left",
            "physical": True,
            "skip_auto_observe": True,
            "context": "focus central editable document surface",
        }

    def _drawing_opacity_action(self, request: SubgoalRequest) -> dict[str, Any] | None:
        """Restore a solid drawing surface when an accessible opacity slider says otherwise.

        This is intentionally based on role + visible state, not an
        application name: an explicitly transparent request is left untouched,
        while a color drawing request gets a high-opacity click on a live
        slider before the local drawing routine starts.
        """
        requested = " ".join([
            request.subgoal,
            str(getattr(self.session, "task", "") or ""),
        ]).casefold()
        if any(term in requested for term in ("transparent", "translucent", "opacity", "半透明", "透明")):
            return None
        snapshot = getattr(self.session, "snapshot", None)
        for element in list(getattr(snapshot, "elements", []) or []):
            role = str(getattr(element, "role", "") or "").casefold()
            label = str(getattr(element, "text", "") or "").strip()
            if role != "slider" or "opacity" not in label.casefold():
                continue
            match = re.search(r"(\d{1,3})\s*%", label)
            if not match or int(match.group(1)) >= 85:
                continue
            left = int(getattr(element, "left", 0) or 0)
            top = int(getattr(element, "top", 0) or 0)
            width = max(1, int(getattr(element, "width", 0) or 0))
            height = max(1, int(getattr(element, "height", 0) or 0))
            if height > width * 1.4:
                x = left + width // 2
                y = top + max(2, int(height * 0.04))
            else:
                x = left + width - max(2, int(width * 0.04))
                y = top + height // 2
            return {
                "action": "click",
                "x": x,
                "y": y,
                "skip_auto_observe": True,
                "context": "raise visible drawing opacity for requested solid color",
            }
        return None

    def _build_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        input_spec = request.input
        if input_spec:
            text = input_spec.get("text", input_spec.get("value", input_spec.get("text_input", "")))
            if text is not None and str(text) != "":
                if input_spec.get("document_surface") is True:
                    focus_action = self._central_document_focus_action(request)
                    if focus_action:
                        actions.append(focus_action)
                    if input_spec.get("clear", input_spec.get("replace", True)):
                        actions.extend([
                            {"action": "hotkey", "keys": ["ctrl", "a"]},
                            {"action": "hotkey", "keys": ["backspace"]},
                        ])
                    actions.append({"action": "type_text", "text": str(text)})
                else:
                    action = self._anchor_fields(input_spec, default_role="edit")
                    action.update({
                        "action": "type_into_element",
                        "text": str(text),
                        "clear": input_spec.get("clear", input_spec.get("replace", True)),
                    })
                    expect_text = input_spec.get("expect_text")
                    if expect_text is True or (expect_text is None and str(text) in request.verify_texts):
                        action["expect"] = {"text_appears": str(text)}
                    elif isinstance(expect_text, str) and expect_text.strip():
                        action["expect"] = {"text_appears": expect_text.strip()}
                    actions.append(action)
        if input_spec and request.constraints.get("submit_with_enter") is True:
            actions.extend([
                {"action": "hotkey", "keys": ["enter"]},
                {
                    "action": "wait_change",
                    "timeout_s": 5.0,
                    "min_ratio": 0.0002,
                    "required": False,
                },
                {"action": "wait_stable", "stable_ms": 300, "timeout_s": 5.0},
                {"action": "read_text"},
            ])
        if request.formats:
            # Foreground window identity does not prove that the document
            # editor owns keyboard focus; Office-style apps commonly leave it
            # on a ribbon/search/AI control. Focus a conservative central work
            # surface point before issuing selection shortcuts.
            focus_action = self._central_document_focus_action(request)
            if focus_action:
                focus_action["context"] = "focus central editable document surface before formatting"
                actions.append(focus_action)
        for format_spec in request.formats:
            actions.extend(_document_format_actions(format_spec))
        if request.formats and actions:
            actions.append({"action": "hotkey", "keys": ["ctrl", "end"]})
        draw_specs = _draw_specs(request)
        draw_canvas_context: tuple[Region, list[dict[str, Any]]] | None = None
        if draw_specs:
            # Continuous surface mutation must never guess a work area.  The
            # public schema has no low-confidence opt-in, so make credible
            # current-pixel grounding the unconditional controller contract.
            hwnd = self._target_hwnd(request, allow_foreground=False)
            window = None
            if hwnd:
                try:
                    window = window_region(hwnd)
                except Exception:
                    snapshot = getattr(self.session, "snapshot", None)
                    window = snapshot.region if snapshot is not None else None
            if window is not None:
                detected_canvas, ready_attempts = self._ready_canvas_region(
                    window,
                    allow_fallback=False,
                )
                if detected_canvas is not None:
                    previous_canvas = getattr(self.session, "_last_canvas_region", None)
                    generation = _drawing_canvas_generation_token(self.session)
                    canvas = _logical_scene_canvas(
                        detected_canvas,
                        draw_specs,
                        getattr(self.session, "task", ""),
                        previous_canvas,
                        hwnd=hwnd,
                        window=window,
                        generation=generation,
                    )
                    draw_canvas_context = (canvas, ready_attempts)
        if draw_canvas_context is not None and any(
            _requested_color(spec, request.subgoal, getattr(self.session, "task", ""))
            for spec in draw_specs
        ):
            opacity_action = self._drawing_opacity_action(request)
            if opacity_action is not None:
                actions.append(opacity_action)
        last_color = ""
        first_draw_for_color = True
        user_required_colors = set(
            _required_user_draw_colors(getattr(self.session, "task", ""))
        )
        # Color changes are driven only by typed draw payloads.  User-required
        # colors are strict; model-selected decoration is best-effort, but it
        # is never enabled by aesthetic keywords hidden in the task text.
        for draw_spec in (draw_specs if draw_canvas_context is not None else []):
            draw_action = self._build_draw_action(
                request,
                draw_spec,
                canvas_context=draw_canvas_context,
            )
            if draw_action:
                color = draw_action.get("requested_color")
                required_by_user = bool(color and str(color) in user_required_colors)
                should_select = bool(color and str(color) != last_color)
                if should_select:
                    actions.append({
                        "action": "select_color",
                        "color": color,
                        "strict": required_by_user,
                        "required_by_user": required_by_user,
                    })
                    last_color = str(color)
                    first_draw_for_color = True
                draw_action["trace_checkpoint"] = bool(first_draw_for_color)
                actions.append(draw_action)
                first_draw_for_color = False
        draw_action_indices = [
            index
            for index, action in enumerate(actions)
            if action.get("action") == "draw_strokes"
        ]
        if len(draw_action_indices) >= 3:
            final_draw_index = draw_action_indices[-1]
            for index in draw_action_indices:
                actions[index]["defer_visual_verify"] = index != final_draw_index
            actions[final_draw_index]["trace_checkpoint"] = True
        click_specs = request.clicks or ([request.click] if request.click else [])
        shortcut_keys: list[str] = []
        if not input_spec and len(click_specs) == 1:
            if _is_browser_location_click(click_specs[0]):
                shortcut_keys = ["ctrl", "l"]
            shortcut_label = (
                click_specs[0].get("label")
                or click_specs[0].get("text")
                or click_specs[0].get("name")
            )
            if not shortcut_keys:
                shortcut_keys = _click_label_to_hotkey(shortcut_label)
        if shortcut_keys:
            actions.append({"action": "hotkey", "keys": shortcut_keys})
            if request.constraints.get("wait") is not False:
                actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0})
            if request.verify_texts:
                actions.append({"action": "read_text"})
            return actions
        submit_file_dialog_with_enter = bool(
            input_spec and click_specs and _file_dialog_enter_submit(request, input_spec, click_specs)
        )
        if submit_file_dialog_with_enter:
            navigation_prefix: list[dict[str, Any]] = []
            for navigation_spec in _file_dialog_navigation_clicks(input_spec, click_specs):
                label = (
                    navigation_spec.get("label")
                    or navigation_spec.get("text")
                    or navigation_spec.get("name")
                )
                navigation_action = self._anchor_fields(navigation_spec)
                if label and not navigation_action.get("label"):
                    navigation_action["label"] = str(label)
                navigation_action.update({
                    "action": "click_element",
                    "button": navigation_spec.get("button", "left"),
                    "double": bool(navigation_spec.get("double", False)),
                    "context": request.subgoal,
                })
                navigation_prefix.extend([
                    navigation_action,
                    {"action": "wait_stable", "stable_ms": 200, "timeout_s": 2.0},
                    {"action": "observe"},
                ])
            if navigation_prefix:
                actions[0:0] = navigation_prefix
            title = ""
            if isinstance(request.target_window, dict):
                title = str(request.target_window.get("title") or "").strip()
                if not title and request.target_window.get("hwnd"):
                    try:
                        title = str(
                            win32._window_text(int(request.target_window["hwnd"])) or ""
                        ).strip()
                    except (TypeError, ValueError, OSError):
                        title = ""
            action = {"action": "hotkey", "keys": ["enter"]}
            input_text = str(
                input_spec.get("text", input_spec.get("value", input_spec.get("text_input", "")))
                or ""
            ).strip()
            task_text = f"{getattr(self.session, 'task', '')} {request.subgoal}".casefold()
            is_save = any(marker in task_text for marker in (
                "save", "save as", "export", "\u4fdd\u5b58", "\u53e6\u5b58", "\u5bfc\u51fa",
            ))
            if is_save and _looks_like_file_path(input_text):
                action["expect"] = {"file_exists": input_text, "timeout_s": 8.0}
            elif title:
                action["expect"] = {"window_gone": title, "timeout_s": 5.0}
            actions.append(action)
            click_specs = []
        keyboard_text = _click_sequence_as_keyboard_text(
            request,
            click_specs,
        )
        if keyboard_text:
            action = {
                "action": "type_text",
                "text": keyboard_text,
                "keyboard_sequence_from_clicks": True,
            }
            if len(request.verify_texts) == 1:
                action["expect"] = {"text_appears": request.verify_texts[0]}
            actions.append(action)
            wait_spec = request.constraints.get("wait") if isinstance(request.constraints, dict) else None
            if wait_spec is not False:
                actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0})
            if request.verify_texts and len(request.verify_texts) != 1:
                actions.append({"action": "read_text"})
            return actions
        changed_query_text = ""
        targeted_result_text = ""
        if input_spec and click_specs and len(request.verify_texts) == 1:
            targeted_result_text = request.verify_texts[0]
        elif click_specs and len(request.verify_texts) == 1:
            changed_query_text = request.verify_texts[0]
        allow_click_interval_wait = not (
            isinstance(request.constraints, dict)
            and request.constraints.get("wait") is False
        )
        for index, click_spec in enumerate(click_specs):
            label = click_spec.get("label") or click_spec.get("text") or click_spec.get("name")
            if label or click_spec.get("anchor"):
                inferred_region = ""
                if not click_spec.get("expected_region"):
                    inferred_region = _infer_semantic_region(request.subgoal)
                default_role = "" if inferred_region == "left-list" else "button"
                action = self._anchor_fields(click_spec, default_role=default_role)
                if inferred_region:
                    action["expected_region"] = inferred_region
                if label and not action.get("label"):
                    action["label"] = str(label)
                action.update({
                    "action": "click_element",
                    "button": click_spec.get("button", "left"),
                    "double": bool(click_spec.get("double", False)),
                })
                if request.subgoal:
                    action["context"] = request.subgoal
                for option in (
                    "physical",
                    "force_physical",
                    "prefer_uia_invoke",
                    "visual_servo",
                    "servo_iterations",
                    "servo_tolerance_px",
                    "servo_settle_ms",
                ):
                    if option in click_spec:
                        action[option] = click_spec[option]
                count_source = (
                    click_spec.get("click_count")
                    or click_spec.get("count")
                    or click_spec.get("times")
                    or (
                        request.constraints.get("click_count")
                        if len(click_specs) == 1 and isinstance(request.constraints, dict)
                        else None
                    )
                )
                try:
                    click_count = int(count_source or 1)
                except (TypeError, ValueError):
                    click_count = 1
                click_count = max(1, min(click_count, 12))
                try:
                    click_interval_ms = int(request.constraints.get("click_interval_ms") or 140)
                except (TypeError, ValueError, AttributeError):
                    click_interval_ms = 140
                click_interval_ms = max(40, min(click_interval_ms, 600))
                for repeat_index in range(click_count):
                    repeated = dict(action)
                    if click_count > 1:
                        repeated["repeat_index"] = repeat_index + 1
                        repeated["repeat_count"] = click_count
                    actions.append(repeated)
                    last_click = index == len(click_specs) - 1 and repeat_index == click_count - 1
                    if allow_click_interval_wait and not last_click and (click_count > 1 or len(click_specs) > 1):
                        actions.append({"action": "wait", "duration_ms": click_interval_ms})
        wait_spec = request.constraints.get("wait") if isinstance(request.constraints, dict) else None
        if wait_spec is not False and actions:
            actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0})
        if request.verify_texts:
            if targeted_result_text:
                actions.append({
                    "action": "read_text",
                    "query": targeted_result_text,
                    "require_non_input": True,
                })
            elif changed_query_text:
                actions.append({
                    "action": "observe",
                    "changed_only": True,
                    "query": changed_query_text,
                    "max_elements": 80,
                })
            else:
                actions.append({"action": "read_text"})
        return actions

    def _keyboard_fallback_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        if not request.input or request.click or request.clicks or _draw_specs(request):
            return []
        if not self._target_hwnd(request, allow_foreground=False):
            return []
        text = _direct_keyboard_text(
            request.input.get("text", request.input.get("value", request.input.get("text_input", "")))
        )
        if not text:
            return []
        action = {
            "action": "type_text",
            "text": text,
            "keyboard_fallback_from_type_into_element": True,
        }
        if len(request.verify_texts) == 1:
            action["expect"] = {"text_appears": request.verify_texts[0]}
        actions = [action]
        wait_spec = request.constraints.get("wait") if isinstance(request.constraints, dict) else None
        if wait_spec is not False:
            actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0})
        if request.verify_texts and len(request.verify_texts) != 1:
            actions.append({"action": "read_text"})
        return actions

    def _ready_canvas_region(
        self,
        window: Region,
        timeout_s: float = 3.5,
        *,
        allow_fallback: bool = False,
    ) -> tuple[Region | None, list[dict[str, Any]]]:
        deadline = time.monotonic() + max(0.1, timeout_s)
        attempts: list[dict[str, Any]] = []
        last_image = None
        while True:
            try:
                image = self.controller.capture_fn(window)
            except Exception as exc:
                attempts.append({"ok": False, "error": str(exc)})
                image = None
            last_image = image
            canvas = _canvas_region_from_image(window, image) if image is not None else None
            unready = _looks_like_unready_window(image) if image is not None else False
            attempts.append({
                "ok": canvas is not None,
                "canvas": (
                    {"x": canvas.left, "y": canvas.top, "width": canvas.width, "height": canvas.height}
                    if canvas is not None else None
                ),
                "looks_unready": unready,
            })
            if canvas is not None:
                return canvas, attempts
            if not unready or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        if (
            allow_fallback
            and last_image is not None
            and not _looks_like_unready_window(last_image)
        ):
            fallback = _fallback_canvas_region(window)
            attempts.append({
                "ok": True,
                "canvas": {
                    "x": fallback.left,
                    "y": fallback.top,
                    "width": fallback.width,
                    "height": fallback.height,
                },
                "source": "window_inset_fallback",
                "confidence": "low",
                "requires_fresh_visual_verification": True,
            })
            return fallback, attempts
        return None, attempts

    def _build_draw_action(
        self,
        request: SubgoalRequest,
        draw_spec: dict[str, Any],
        *,
        canvas_context: tuple[Region, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any] | None:
        if canvas_context is None:
            hwnd = self._target_hwnd(request, allow_foreground=False)
            if not hwnd:
                return None
            try:
                window = window_region(hwnd)
            except Exception:
                snapshot = getattr(self.session, "snapshot", None)
                window = snapshot.region if snapshot is not None else None
            if window is None:
                return None
            detected_canvas, ready_attempts = self._ready_canvas_region(
                window,
                allow_fallback=False,
            )
            if detected_canvas is None:
                return None
            previous_canvas = getattr(self.session, "_last_canvas_region", None)
            generation = _drawing_canvas_generation_token(self.session)
            canvas = _logical_scene_canvas(
                detected_canvas,
                _draw_specs(request),
                getattr(self.session, "task", ""),
                previous_canvas,
                hwnd=hwnd,
                window=window,
                generation=generation,
            )
        else:
            canvas, ready_attempts = canvas_context
        task_text = str(getattr(self.session, "task", "") or "")
        shape = _draw_shape_name(draw_spec, request.subgoal, task_text)
        part = _semantic_part(draw_spec)
        raw_box = draw_spec.get("box")
        multi_point_line = bool(
            shape == "line"
            and isinstance(raw_box, (list, tuple))
            and len(raw_box) >= 6
            and len(raw_box) % 2 == 0
        )
        point_geometry = (
            _normalized_point_geometry(
                canvas,
                raw_box,
                close=shape == "triangle",
            )
            if shape == "triangle" or multi_point_line
            else None
        )
        line_geometry = (
            point_geometry or _normalized_line_geometry(
                canvas,
                raw_box,
                str(draw_spec.get("box_mode") or ""),
                semantic_part=part,
            )
            if shape == "line"
            else None
        )
        draw_region = (
            point_geometry[1]
            if point_geometry is not None
            else line_geometry[1] if line_geometry is not None else canvas
        )
        box_metadata: dict[str, Any] = {}
        if line_geometry is None and point_geometry is None:
            boxed, box_metadata = _normalized_closed_shape_region(
                canvas,
                draw_spec.get("box"),
                mode=str(draw_spec.get("box_mode") or ""),
                semantic_part=part,
            )
        else:
            boxed = None
        if boxed is not None:
            draw_region = boxed
        hint = str(draw_spec.get("expected_region") or draw_spec.get("region") or "").strip()
        hinted = (
            _rough_region(canvas, hint)
            if hint and boxed is None and line_geometry is None
            else None
        )
        if hinted is not None:
            draw_region = hinted
        strokes = draw_spec.get("strokes") if isinstance(draw_spec.get("strokes"), list) else None
        effective_normalized_strokes = _effective_normalized_strokes(draw_spec)
        normalized_strokes = _normalized_strokes_to_screen(
            canvas,
            effective_normalized_strokes,
            smooth=False,
        )
        if normalized_strokes:
            strokes = normalized_strokes
        elif not strokes and point_geometry is not None:
            strokes = point_geometry[0]
        elif not strokes and line_geometry is not None:
            strokes = line_geometry[0]
        if not strokes:
            strokes = _shape_strokes(
                shape,
                draw_region,
                margin_ratio=0.0 if boxed is not None else 0.16,
            )
        if not strokes:
            return None
        action = {
            "action": "draw_strokes",
            "strokes": strokes,
            "duration": float(draw_spec.get("duration", 0.28)),
            "canvas_region": {
                "x": canvas.left,
                "y": canvas.top,
                "width": canvas.width,
                "height": canvas.height,
            },
            "draw_region": {
                "x": draw_region.left,
                "y": draw_region.top,
                "width": draw_region.width,
                "height": draw_region.height,
            },
            "canvas_ready_attempts": ready_attempts[-4:],
            "shape": shape,
        }
        canvas_payload: dict[str, Any] = _region_payload(canvas)
        self.session._last_canvas_region = canvas_payload
        action.update(box_metadata)
        if part:
            action["semantic_part"] = part
        color = _requested_color(draw_spec, request.subgoal, getattr(self.session, "task", ""))
        if color:
            action["requested_color"] = color
        return action

    def _draw_color_evidence(
        self,
        actions: list[dict[str, Any]],
        batch_results: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        actions = _successful_executed_draw_actions(actions, batch_results)
        merged: dict[str, Any] = {}
        per_draw: list[dict[str, Any]] = []
        canvas_region: Region | None = None
        canvas_image = None
        for action in actions:
            raw_canvas = action.get("canvas_region")
            if action.get("action") != "draw_strokes" or not isinstance(raw_canvas, dict):
                continue
            try:
                canvas_region = Region(
                    int(raw_canvas["x"]),
                    int(raw_canvas["y"]),
                    int(raw_canvas["width"]),
                    int(raw_canvas["height"]),
                ).clamp_to_screen()
                candidate = self.controller.capture_fn(canvas_region)
                arr = np.asarray(candidate)
                if (
                    arr.ndim >= 2
                    and arr.shape[1] == int(canvas_region.width)
                    and arr.shape[0] == int(canvas_region.height)
                ):
                    canvas_image = arr
            except Exception:
                canvas_region = None
                canvas_image = None
            break
        for action in actions:
            if action.get("action") != "draw_strokes" or not action.get("requested_color"):
                continue
            region = FastGuiController._action_region("draw_strokes", action)
            if region is None:
                raw = action.get("draw_region") if isinstance(action.get("draw_region"), dict) else {}
                try:
                    region = Region(int(raw["x"]), int(raw["y"]), int(raw["width"]), int(raw["height"])).clamp_to_screen()
                except Exception:
                    region = None
            if region is None:
                per_draw.append({
                    "requested_color": action.get("requested_color"),
                    "matched": False,
                    "error": "no draw action region",
                })
                continue
            try:
                image = None
                if canvas_image is not None and canvas_region is not None:
                    left = max(region.left, canvas_region.left)
                    top = max(region.top, canvas_region.top)
                    right = min(region.right, canvas_region.right)
                    bottom = min(region.bottom, canvas_region.bottom)
                    if right > left and bottom > top:
                        image = canvas_image[
                            top - canvas_region.top:bottom - canvas_region.top,
                            left - canvas_region.left:right - canvas_region.left,
                        ]
                if image is None or getattr(image, "size", 0) == 0:
                    image = self.controller.capture_fn(region)
                summary = FastGuiController._compact_image_summary(image)
                visible = summary.get("visible_colors") if isinstance(summary.get("visible_colors"), dict) else {}
            except Exception as exc:
                per_draw.append({
                    "requested_color": action.get("requested_color"),
                    "matched": False,
                    "region": {"x": region.left, "y": region.top, "width": region.width, "height": region.height},
                    "error": str(exc),
                })
                continue
            for color, stats in visible.items():
                merged[str(color)] = stats
            requested = str(action.get("requested_color") or "")
            per_draw.append({
                "requested_color": requested,
                "matched": requested in visible,
                "region": {"x": region.left, "y": region.top, "width": region.width, "height": region.height},
                "visible_colors": visible,
            })
        return merged, per_draw

    def execute(self, args: dict[str, Any]) -> ToolResult:
        request = parse_subgoal_request(args, task=getattr(self.session, "task", ""))
        launch_prelude: list[dict[str, Any]] = []
        draw_specs = _draw_specs(request)
        prior_scene_specs = getattr(self.session, "_verified_draw_scene_specs", [])
        if not isinstance(prior_scene_specs, list):
            prior_scene_specs = []
        scene_preflight = _scene_structure_preflight(
            [*prior_scene_specs, *draw_specs],
            getattr(self.session, "task", ""),
            enforce_primitive_ceiling=not bool(prior_scene_specs),
        )
        if prior_scene_specs and draw_specs:
            scene_preflight["incremental"] = True
            scene_preflight["prior_primitive_count"] = len(prior_scene_specs)
            scene_preflight["new_primitive_count"] = len(draw_specs)
        pure_verify = bool(
            request.verify_texts
            and not request.input
            and not request.click
            and not request.clicks
            and not draw_specs
            and not request.formats
        )
        launch_target = _app_launch_target(request)
        if launch_target and not self._target_hwnd(request, allow_foreground=False):
            launched = self.session.dispatch("open_app", {"name": launch_target})
            try:
                launched_data = json.loads(launched.text or "{}")
            except (json.JSONDecodeError, TypeError):
                launched_data = {}
            payload = {
                **(launched_data if isinstance(launched_data, dict) else {}),
                "ok": bool(isinstance(launched_data, dict) and launched_data.get("ok")),
                "verified": bool(
                    isinstance(launched_data, dict)
                    and (launched_data.get("verified") or launched_data.get("target_hwnd"))
                ),
                "subgoal": request.subgoal,
                "subgoal_route": "open_app_capability",
                "controller_state": {
                    "phase": "verified" if isinstance(launched_data, dict) and launched_data.get("ok") else "blocked",
                    "verified": bool(isinstance(launched_data, dict) and launched_data.get("verified")),
                    "needs_llm_reason": not bool(isinstance(launched_data, dict) and launched_data.get("ok")),
                },
                "evidence_packet": {
                    "matched_texts": [launch_target] if isinstance(launched_data, dict) and launched_data.get("ok") else [],
                    "missing_texts": [] if isinstance(launched_data, dict) and launched_data.get("ok") else [launch_target],
                },
                "needs_llm_reason": not bool(isinstance(launched_data, dict) and launched_data.get("ok")),
                "fast_path_advice": (
                    "Application launch was completed by the same Agent's open_app capability; "
                    "continue with the next unfinished GUI subgoal."
                ),
            }
            return ToolResult(
                json.dumps(payload, ensure_ascii=False, default=str),
                terminal=launched.terminal,
                replay=launched.replay,
            )
        if pure_verify and not self._target_hwnd(request, allow_foreground=False):
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "needs_target_window": True,
                "subgoal": request.subgoal,
                "subgoal_route": "controller_first",
                "controller_state": {
                    "phase": "blocked",
                    "needs_llm_reason": True,
                    "needs_target_window": True,
                },
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": request.verify_texts,
                },
                "error": "execute_subgoal verify-only routine requires an existing target window/session hwnd.",
                "needs_llm_reason": True,
                "fast_path_advice": (
                    "Do not use verify-only execute_subgoal to open or discover an app. "
                    "First use launch/find_app/focus_window or perform the GUI action that "
                    "creates the target state; then verify against that target window."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        has_mutating_routine = bool(
            request.input or request.click or request.clicks or draw_specs or request.formats
        )
        if (
            has_mutating_routine
            and not self._target_hwnd(request, allow_foreground=False)
            and getattr(self.session, "snapshot", None) is None
            and not bool(getattr(self.session, "_initial_app_launch_attempted", False))
        ):
            initial_app = _initial_task_app_target(getattr(self.session, "task", ""))
            if initial_app:
                setattr(self.session, "_initial_app_launch_attempted", True)
                launched = self.session.dispatch("open_app", {"name": initial_app})
                try:
                    launched_data = json.loads(launched.text or "{}")
                except (json.JSONDecodeError, TypeError):
                    launched_data = {}
                launch_ok = bool(
                    isinstance(launched_data, dict)
                    and launched_data.get("ok")
                    and (launched_data.get("verified") or launched_data.get("target_hwnd"))
                    and self._target_hwnd(request, allow_foreground=False)
                )
                launch_prelude.append({
                    "action": "open_app",
                    "app": initial_app,
                    "ok": launch_ok,
                    "target_hwnd": (
                        launched_data.get("target_hwnd")
                        if isinstance(launched_data, dict) else None
                    ),
                    "preview": (launched.text or "")[:320],
                })
                if not launch_ok:
                    payload = {
                        "ok": False,
                        "verified": False,
                        "blocked": True,
                        "needs_target_window": True,
                        "subgoal": request.subgoal,
                        "subgoal_route": "initial_app_launch_prelude",
                        "controller_state": {
                            "phase": "blocked",
                            "verified": False,
                            "needs_llm_reason": True,
                            "needs_target_window": True,
                        },
                        "evidence_packet": {
                            "matched_texts": [],
                            "missing_texts": request.verify_texts,
                            "prelude": launch_prelude,
                        },
                        "error": (
                            f'Initial task application "{initial_app}" could not be opened '
                            "and bound to a visible target window."
                        ),
                        "needs_llm_reason": True,
                        "fast_path_advice": (
                            "Inspect the open_app result once, then use scan_windows or "
                            "focus_window to establish the intended target; do not mutate "
                            "the unrelated foreground window."
                        ),
                    }
                    return ToolResult(
                        json.dumps(payload, ensure_ascii=False, default=str),
                        terminal=launched.terminal,
                        replay=launched.replay,
                    )
        if has_mutating_routine and not self._target_hwnd(request, allow_foreground=False):
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "needs_target_window": True,
                "subgoal": request.subgoal,
                "subgoal_route": "controller_first",
                "controller_state": {
                    "phase": "blocked",
                    "verified": False,
                    "needs_llm_reason": True,
                    "needs_target_window": True,
                },
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": request.verify_texts,
                },
                "error": (
                    "execute_subgoal requires an established target hwnd before "
                    "desktop mutation; the current foreground window is not an "
                    "implicit target."
                ),
                "needs_llm_reason": True,
                "fast_path_advice": (
                    "Use launch/find_app/focus_window first (or list_windows to resolve "
                    "the hwnd), then retry "
                    "the controller routine against that established target window."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        if draw_specs and scene_preflight.get("required") and not scene_preflight.get("ok"):
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "subgoal": request.subgoal,
                "subgoal_route": "controller_scene_preflight",
                "controller_state": {
                    "phase": "blocked",
                    "verified": False,
                    "needs_llm_reason": True,
                },
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": [],
                    "scene_structure": scene_preflight,
                },
                "error": "detailed scene structure preflight failed",
                "needs_llm_reason": True,
                "fast_path_advice": (
                    "The payload uses a technically unsupported drawing feature. "
                    "Revise only the invalid fields reported in scene_structure.errors; "
                    "do not infer an object-specific recipe or clear/restart the canvas."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        snapshot, prelude, skip_click_auto_observe = self._ensure_observed(request)
        prelude = launch_prelude + prelude
        blocked_focus = next(
            (
                item for item in prelude
                if item.get("action") == "focus_window" and item.get("ok") is not True
            ),
            None,
        )
        if blocked_focus is not None:
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "cooperative_input": bool(blocked_focus.get("cooperative_input")),
                "subgoal": request.subgoal,
                "subgoal_route": "controller_focus_prelude",
                "prelude": prelude,
                "error": (
                    "execute_subgoal stopped because its established target could "
                    "not be focused without taking foreground ownership"
                ),
                "needs_observe": True,
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        recent_changes = {}
        try:
            recent_changes = self.controller.watcher.status()
        except Exception:
            recent_changes = {}
        reduction = reducer.reduce_snapshot(
            snapshot,
            task=getattr(self.session, "task", ""),
            query=request.subgoal,
            recent_changes=recent_changes,
            communication_context=getattr(
                self.session, "communication_context", None,
            ),
        ) if snapshot is not None else {}
        actions = self._build_actions(request)
        if skip_click_auto_observe:
            for action in actions:
                if action.get("action") == "click_element":
                    action["skip_auto_observe"] = True
        draw_surface_missing = bool(
            draw_specs
            and not any(action.get("action") == "draw_strokes" for action in actions)
        )
        if not actions or draw_surface_missing:
            needs_target_window = bool(draw_specs)
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "work_surface_fail_closed": draw_surface_missing,
                "needs_target_window": needs_target_window,
                "subgoal": request.subgoal,
                "subgoal_route": "controller_first",
                "controller_state": {
                    "phase": "blocked",
                    "needs_llm_reason": True,
                    "needs_target_window": needs_target_window,
                },
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": request.verify_texts,
                    "perception_reducer": reduction,
                },
                "error": (
                    "execute_subgoal could not uniquely ground one credible work surface "
                    "from the current target pixels; no requested action was executed."
                    if draw_specs else
                    "execute_subgoal needs an input.text routine, a click.label routine, a draw routine, or verify_texts."
                ),
                "needs_llm_reason": True,
            }
            if draw_specs:
                payload["fast_path_advice"] = (
                    "Inspect a fresh real frame and make the intended work surface uniquely visible. "
                    "Do not reuse a window-inset estimate or reinterpret the supplied coordinates."
                )
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        batch = self.controller.execute(
            actions,
            stop_on_error=True,
            sync=False,
            subgoal=request.subgoal,
        )
        fallback_actions: list[dict[str, Any]] = []
        if not batch.get("ok") and _type_into_element_failed_to_focus(batch):
            fallback_actions = self._keyboard_fallback_actions(request)
            if fallback_actions:
                primary_action_count = len(batch.get("results") or [])
                fallback_batch = self.controller.execute(
                    fallback_actions,
                    stop_on_error=True,
                    sync=False,
                    subgoal=f"{request.subgoal} (keyboard fallback)",
                )
                batch = _merge_fallback_batch(batch, fallback_batch, fallback_actions)
                actions = actions[:primary_action_count] + fallback_actions
        successful_scene_specs = _successful_draw_scene_specs(draw_specs, actions, batch)
        if successful_scene_specs:
            existing_specs = getattr(self.session, "_verified_draw_scene_specs", [])
            if not isinstance(existing_specs, list):
                existing_specs = []
            self.session._verified_draw_scene_specs = _merge_draw_scene_specs(
                existing_specs,
                successful_scene_specs,
            )
        visible_text = _strip_stale_status_lines(_pick_text(batch))
        navigation = _navigation_evidence(request, batch)
        verification_text = (
            _strip_stale_status_lines(_last_read_text(batch))
            if navigation.get("required")
            else visible_text
        )
        expect_matched = _verified_expect_texts(actions, batch)
        visible_colors = _visual_colors_from_batch(batch)
        batch_results = batch.get("results") or []
        input_applied = any(
            isinstance(result, dict)
            and (
                (
                    isinstance(result.get("data"), dict)
                    and result["data"].get("mutation_applied") is True
                )
                or (
                    result.get("action") in {"type_text", "type_into_element", "clipboard_paste"}
                    and result.get("ok") is True
                )
            )
            for result in batch_results
        )
        executed_actions = actions[:len(batch_results)]
        planned_draw_actions = [
            action for action in actions if action.get("action") == "draw_strokes"
        ]
        verified_draw_actions = _successful_executed_draw_actions(actions, batch_results)
        draw_visible_colors, per_draw_color_evidence = self._draw_color_evidence(
            actions,
            batch_results,
        )
        visible_colors = {**visible_colors, **draw_visible_colors}
        task_text = getattr(self.session, "task", "")
        requested_colors = _requested_draw_colors(request, task_text)
        required_visual_colors = _required_user_draw_colors(task_text)
        # Only evidence from an executed draw can satisfy a requested color.
        # A white canvas background, for example, must not prove that an
        # unexecuted white primitive was delivered.
        matched_visual_colors = _matched_executed_draw_colors(
            requested_colors,
            per_draw_color_evidence,
        )
        executed_color_matches = set(matched_visual_colors)
        missing_planned_visual_colors = [
            color for color in requested_colors if color not in executed_color_matches
        ]
        matched_required_visual_colors = [
            color for color in required_visual_colors if color in executed_color_matches
        ]
        missing_required_visual_colors = [
            color for color in required_visual_colors if color not in executed_color_matches
        ]
        optional_missing_visual_colors = [
            color
            for color in missing_planned_visual_colors
            if color not in set(required_visual_colors)
        ]
        # Backward-compatible repair fields represent hard user requirements
        # only. Planner-selected aesthetic colors are diagnostics, not a reason
        # to retry or block the delivered geometry.
        missing_visual_colors = list(missing_required_visual_colors)
        scene_structure = dict(scene_preflight)
        scene_structure.update({
            "executed_draw_count": len(verified_draw_actions),
            "matched_visual_color_count": len(matched_visual_colors),
            "structured_scene_verified": bool(
                scene_preflight.get("required")
                and scene_preflight.get("ok")
                and bool(batch.get("ok"))
                and len(verified_draw_actions) == len(planned_draw_actions)
                and not missing_required_visual_colors
            ),
        })
        partial_matched = [
            text for text in request.verify_texts
            if _match_visible_text(text, verification_text)
            or any(_match_visible_text(text, verified) for verified in expect_matched)
        ]
        matched = partial_matched if bool(batch.get("ok")) else []
        missing = [text for text in request.verify_texts if text not in matched]
        format_applied = bool(
            request.formats
            and _document_format_prefix_ok(executed_actions, batch_results)
        )
        verified = (
            bool(batch.get("ok"))
            and not missing
            and not missing_required_visual_colors
            and bool(navigation.get("ok", True))
        ) if (request.verify_texts or required_visual_colors) else bool(
            batch.get("verified") and navigation.get("ok", True)
        )
        if scene_structure.get("required"):
            verified = bool(verified and scene_structure.get("structured_scene_verified"))
        recovery_candidates = _visible_region_text_candidates(snapshot, request)
        evidence_packet = {
            "matched_texts": matched,
            "missing_texts": missing,
            "verified_expect_texts": expect_matched,
            "visible_colors": visible_colors,
            "per_draw_color_evidence": per_draw_color_evidence,
            "requested_visual_colors": requested_colors,
            "required_visual_colors": required_visual_colors,
            "matched_visual_colors": matched_visual_colors,
            "missing_visual_colors": missing_visual_colors,
            "optional_missing_visual_colors": optional_missing_visual_colors,
            "matched_required_visual_colors": matched_required_visual_colors,
            "missing_required_visual_colors": missing_required_visual_colors,
            "scene_structure": scene_structure,
            "candidate_click_labels": recovery_candidates,
            "visible_text_preview": visible_text[:1200],
            "navigation": navigation,
            "perception_reducer": reduction,
            "prelude": prelude,
            "input_applied": input_applied,
            "format_applied": format_applied,
            "format_specs": request.formats,
        }
        if partial_matched and not batch.get("ok"):
            evidence_packet["partial_matched_texts"] = partial_matched
        payload = {
            "ok": bool(batch.get("ok")) and not missing and not missing_required_visual_colors,
            "verified": bool(verified),
            "subgoal": request.subgoal,
            "subgoal_route": "controller_first",
            "controller_state": {
                **(batch.get("controller_state") or {}),
                "verified": bool(verified),
                "needs_llm_reason": bool(missing or missing_required_visual_colors or not batch.get("ok")),
            },
            "actions_executed": executed_actions,
            "results": _public_batch_results(batch.get("results") or []),
            "metrics": batch.get("metrics") or {},
            "evidence_packet": evidence_packet,
            "needs_llm_reason": bool(missing or missing_required_visual_colors or not batch.get("ok")),
            "input_applied": input_applied,
        }
        if format_applied:
            payload.update({
                "format_applied": True,
                "mutation_applied": True,
                "postcondition_pending": True,
                "needs_llm_reason": bool(missing),
            })
            payload["controller_state"]["needs_llm_reason"] = bool(missing)
            payload["fast_path_advice"] = (
                "The explicitly structured rich-text formats were executed. "
                "Use fresh visual evidence to decide the next action."
            )
        if scene_structure.get("structured_scene_verified"):
            payload["structured_scene_verified"] = True
        if verified and draw_specs:
            existing_specs = getattr(self.session, "_verified_draw_scene_specs", [])
            if not isinstance(existing_specs, list):
                existing_specs = []
            self.session._verified_draw_scene_specs = _merge_draw_scene_specs(
                existing_specs,
                draw_specs,
            )
        if verified and not missing:
            payload["fast_path_advice"] = (
                "The controller already executed and verified this GUI subgoal"
                " with matched_texts in evidence_packet. Complete the matching"
                " plan item or call done instead of repeating observe/read_text"
                " for the same evidence."
            )
        if missing:
            payload["error"] = "subgoal routine completed but required visible text evidence is missing"
            payload["blocked"] = True
            if input_applied:
                payload["fast_path_advice"] = (
                    "The input mutation was already delivered to the focused target. Do not type the "
                    "literal again. Verify the existing text with read_text/observe, or continue with "
                    "the next formatting/save action while preserving the current document contents."
                )
            elif recovery_candidates:
                payload["fast_path_advice"] = (
                    "The requested anchor label was not verified. For GUI tasks,"
                    " retry execute_subgoal with one of evidence_packet.candidate_click_labels"
                    " that best matches the intended visible state; do not inspect"
                    " source files or shell output just to find a GUI label."
                )
                payload["recovery_candidates"] = {
                    "candidate_click_labels": recovery_candidates,
                    "expected_visible_texts": request.verify_texts,
                }
        if missing_visual_colors:
            draw_attempted = bool(verified_draw_actions)
            payload["error"] = (
                (
                    "subgoal routine drew visible pixels but required color evidence is missing: "
                    if draw_attempted else
                    "subgoal routine stopped before a verified draw; required color was not selected: "
                )
                + ", ".join(missing_visual_colors)
            )
            payload["blocked"] = True
            payload["fast_path_advice"] = (
                (
                    "The drawing changed pixels but auto_visual_verify did not see the requested color. "
                    if draw_attempted else
                    "The controller stopped at color selection before drawing. "
                )
                + "Do not repeat the complete drawing: the existing scene and matched_visual_colors are "
                "already visible. Retry only primitives whose draw.color is listed in "
                "missing_visual_colors, so successful geometry is not displaced or duplicated."
            )
        elif optional_missing_visual_colors and draw_specs and batch.get("ok"):
            payload["warning"] = (
                "Optional planner-selected palette colors were unavailable: "
                + ", ".join(optional_missing_visual_colors)
            )
            payload["fast_path_advice"] = (
                "Drawing geometry was delivered using the current visible color after an optional "
                "palette miss. Keep the existing scene; do not redraw or clear it. Let the final "
                "semantic visual judge decide whether the completed picture satisfies the task."
            )
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={"tool": "execute_subgoal", "subgoal": request.subgoal, "actions": actions},
        )
