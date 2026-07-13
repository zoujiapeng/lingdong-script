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
from pathlib import Path
from typing import Any

import numpy as np
import win32gui

from ..action.verify import _match_visible_text
from ..agent.task_rules import (
    _document_format_specs,
    _exact_document_text_block,
    _required_literal_tokens,
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
    suite_text = f"{label} {goal}".casefold()
    if "wps" in suite_text:
        if re.search(r"\b(?:spreadsheet|workbook|worksheet)\b|表格|工作簿|工作表", suite_text, re.IGNORECASE):
            return "WPS Spreadsheets"
        if re.search(r"\b(?:presentation|slides?|slideshow)\b|演示|幻灯片", suite_text, re.IGNORECASE):
            return "WPS Presentation"
        if re.search(r"\b(?:writer|document|word processing)\b|文字|文档", suite_text, re.IGNORECASE):
            return "WPS Writer"
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

    # Natural tasks often name the application as a location/instrument rather
    # than an explicit launch command: "在画板上画...", "用 QQ 给...", or
    # "in Paint draw...". Extract only the app phrase; discovery/launch remains
    # the generic app-catalog responsibility.
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
    "input", "type", "enter", "key", "keyboard", "calculate", "compute", "result",
    "输入", "键入", "录入", "按键", "键盘", "计算", "算出", "结果",
)

_PRINTABLE_LABEL_ALIASES = {
    "add": "+",
    "plus": "+",
    "加": "+",
    "subtract": "-",
    "minus": "-",
    "减": "-",
    "multiply": "*",
    "multiplyby": "*",
    "times": "*",
    "乘": "*",
    "乘以": "*",
    "divide": "/",
    "divideby": "/",
    "dividedby": "/",
    "除": "/",
    "除以": "/",
    "equals": "=",
    "equal": "=",
    "等于": "=",
    "decimal": ".",
    "decimalpoint": ".",
    "point": ".",
    "dot": ".",
    "小数点": ".",
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
        if text in {"×", "✕"}:
            return "*"
        if text == "÷":
            return "/"
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


def _new_blank_item_hotkey(request: SubgoalRequest, click_spec: dict[str, Any]) -> list[str]:
    """Compile a generic New/Blank document control into the standard shortcut."""
    if request.input or request.formats or _draw_specs(request):
        return []
    label = str(
        click_spec.get("label")
        or click_spec.get("text")
        or click_spec.get("name")
        or ""
    ).strip()
    label_key = _compact_key(label)
    new_labels = {
        _compact_key(value)
        for value in (
            "New", "Create New", "Blank", "Blank Document", "Blank Workbook",
            "New Document", "New Workbook", "新建", "创建", "空白文档", "空白工作簿", "空白表格",
        )
    }
    label_mentions_new_blank = bool(
        re.search(
            r"\b(?:new|create|blank)\b.{0,40}\b(?:document|workbook|spreadsheet|presentation|file|tab)\b|"
            r"新建|创建|空白(?:文档|工作簿|表格|演示)",
            label,
            re.IGNORECASE,
        )
    )
    if label_key not in new_labels and not label_mentions_new_blank:
        return []
    if not re.search(
        r"\b(?:new|create|blank)\s+(?:document|workbook|spreadsheet|presentation|file)\b|"
        r"\bcreate\s+(?:a\s+)?new\b|新建|创建|空白(?:文档|工作簿|表格|演示)",
        request.subgoal,
        re.IGNORECASE,
    ):
        return []
    return ["ctrl", "n"]


_EXPLICIT_ARITHMETIC_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?(?:\s*(?:[+*/xX\u00d7\u00f7-])\s*"
    r"\d+(?:\.\d+)?){1,10})(?![\d.])"
)
_ARITHMETIC_INTENT_RE = re.compile(
    r"\b(?:calculate|compute|arithmetic|equation|evaluate|sum|product|"
    r"multiply|multiplied|divide|divided|plus|minus|result)\b|"
    r"\u8ba1\u7b97|\u7b97\u51fa|\u7b97\u5f0f|\u7ed3\u679c|\u76f8\u4e58|\u4e58\u4ee5|\u76f8\u52a0|\u9664\u4ee5",
    re.IGNORECASE,
)


def _explicit_arithmetic_keyboard_text(text: Any) -> str:
    """Return a normalized expression only when the user supplied one."""
    candidates = _EXPLICIT_ARITHMETIC_RE.findall(str(text or ""))
    if not candidates:
        return ""
    expression = next(
        (value for value in candidates if re.search(r"[+*/xX\u00d7\u00f7]", value)),
        candidates[0],
    )
    return (
        re.sub(r"\s+", "", expression)
        .replace("\u00d7", "*")
        .replace("\u00f7", "/")
        .replace("X", "*")
        .replace("x", "*")
    )


def _request_arithmetic_keyboard_text(request: SubgoalRequest, task: str = "") -> str:
    intent_scope = " ".join([
        str(task or ""),
        request.subgoal,
        " ".join(request.verify_texts),
    ])
    if not _ARITHMETIC_INTENT_RE.search(intent_scope):
        return ""
    direct = _explicit_arithmetic_keyboard_text(request.subgoal)
    if direct:
        return direct
    request_scope = _compact_key(request.subgoal)
    if any(
        _compact_key(token) in request_scope
        for token in ("calculate", "compute", "result", "计算", "算出", "结果")
    ):
        return _explicit_arithmetic_keyboard_text(task)
    return ""


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
        if (
            spec is not None
            and isinstance(result, dict)
            and result.get("ok") is True
            and visual_change is True
        ):
            successful.append(dict(spec))
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
                                     click_specs: list[dict[str, Any]],
                                     task: str = "") -> str:
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
    request_expression = _request_arithmetic_keyboard_text(request, task)
    arithmetic_scope = any(
        _compact_key(token) in scope_text
        for token in ("calculate", "compute", "result", "计算", "算出", "结果")
    ) or bool(request_expression)
    if arithmetic_scope:
        if request_expression:
            return request_expression + "="
    has_digit_label = any(
        any(ch.isdigit() for ch in str(spec.get("label") or spec.get("text") or spec.get("name") or ""))
        for spec in click_specs
    )
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
        if key in {"x", "X"} and arithmetic_scope and has_digit_label:
            key = "*"
        out.append(key)
    text = "".join(out)
    # Avoid converting generic two-button UI choices such as A/B unless the
    # sequence contains an obvious text/calculation shape.
    if not any(ch.isdigit() or ch in "+-*/=." for ch in text) and len(text) < 4:
        return ""
    return text


_NUMERIC_KEYPAD_LABELS = {
    "*": ("Multiply by", ["\u00d7", "*", "Multiply", "\u4e58", "\u4e58以"], "right"),
    "/": ("Divide by", ["\u00f7", "/", "Divide", "\u9664", "\u9664以"], "right"),
    "+": ("Plus", ["+", "Add", "\u52a0"], "right"),
    "-": ("Minus", ["-", "Subtract", "\u51cf"], "right"),
    ".": ("Decimal separator", [".", "Decimal", "\u5c0f数点"], "bottom"),
    "=": ("Equals", ["=", "Equal", "\u7b49于"], "bottom-right"),
}


def _numeric_keypad_click_actions(
    expression: str,
    *,
    context: str,
    click_specs: list[dict[str, Any]] | None = None,
    include_clear: bool = True,
) -> list[dict[str, Any]]:
    """Compile an expression into semantic on-screen keypad actions."""
    normalized = _explicit_arithmetic_keyboard_text(expression)
    if not normalized:
        return []
    specs = [spec for spec in (click_specs or []) if isinstance(spec, dict)]
    by_key: dict[str, dict[str, Any]] = {}
    clear_spec: dict[str, Any] | None = None
    for spec in specs:
        label = spec.get("label") or spec.get("text") or spec.get("name")
        compact = _compact_key(label)
        if clear_spec is None and compact in {
            "clear", "clearentry", "reset", "c", "ce", "\u6e05除", "\u91cd置",
        }:
            clear_spec = spec
        key = _click_label_to_printable_key(label)
        if key is not None and key not in by_key:
            by_key[key] = spec

    actions: list[dict[str, Any]] = []
    if include_clear:
        clear = {
            "action": "click_element",
            "label": "Clear",
            "fallback_text": ["C", "CE", "Clear entry", "Reset", "\u6e05除", "\u91cd置"],
            "role": "button",
            "expected_region": "center",
            "context": context,
            "numeric_keypad_sequence": True,
        }
        if clear_spec:
            for key in ("label", "text", "name", "fallback_text", "expected_region", "role"):
                if clear_spec.get(key) not in (None, "", []):
                    clear[key if key != "text" and key != "name" else "label"] = clear_spec[key]
        actions.append(clear)
        actions.append({"action": "wait", "duration_ms": 100})

    keys = [*normalized, "="]
    for index, key in enumerate(keys):
        original = by_key.get(key, {})
        if key.isdigit():
            label, fallbacks, region = key, [], "bottom"
        else:
            label, fallbacks, region = _NUMERIC_KEYPAD_LABELS[key]
        action = {
            "action": "click_element",
            "label": str(original.get("label") or original.get("text") or original.get("name") or label),
            "fallback_text": list(original.get("fallback_text") or fallbacks),
            "role": str(original.get("role") or "button"),
            "expected_region": str(original.get("expected_region") or region),
            "context": context,
            "numeric_keypad_sequence": True,
        }
        actions.append(action)
        if index < len(keys) - 1:
            actions.append({"action": "wait", "duration_ms": 100})
    actions.extend([
        {"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0},
        {"action": "read_text"},
    ])
    return actions


def _session_has_numeric_keypad(session: Any) -> bool:
    snapshot = getattr(session, "snapshot", None)
    elements = getattr(snapshot, "elements", None) or []
    keys = {
        key
        for element in elements
        if (key := _click_label_to_printable_key(getattr(element, "text", ""))) is not None
    }
    digit_count = len({key for key in keys if key.isdigit()})
    return digit_count >= 4 and "=" in keys and bool(keys.intersection({"+", "-", "*", "/"}))


def _direct_keyboard_text(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not value or "\n" in value or "\r" in value or len(value) > 180:
        return ""
    if not value.isprintable():
        return ""
    return value


def _looks_like_executable_keyboard_sequence(text: Any) -> bool:
    value = _direct_keyboard_text(text)
    if not value:
        return False
    return (
        value.endswith("=")
        and any(ch.isdigit() for ch in value)
        and any(op in value for op in "+-*/×÷")
    )


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
    return _unique([*_required_visible_payloads(task), *_required_literal_tokens(task), *_visible_state_targets(task)])


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
        and not _looks_like_executable_keyboard_sequence(input_spec.get("text"))
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
    arithmetic_expression = _explicit_arithmetic_keyboard_text(
        " ".join((str(task or ""), subgoal_text))
    )
    arithmetic_operands = set(re.findall(r"\d+(?:\.\d+)?", arithmetic_expression))
    for target in _task_visible_targets(task):
        if not _scope_mentions(target, scope_text):
            continue
        target_key = _compact_key(target)
        # Operands describe what to enter, not what must be visible after the
        # calculation.  Do not turn them into self-fulfilling output checks.
        if arithmetic_expression and target_key in arithmetic_operands:
            continue
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


_SPREADSHEET_RANGE_BLOCK_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,3})(\d+)\s*:\s*([A-Z]{1,3})(\d+)\s*[:：]\s*(?:\r?\n)",
    re.IGNORECASE,
)
_SPREADSHEET_BLOCK_INTENT_RE = re.compile(
    r"\b(?:table|spreadsheet|workbook|cells?|range|grid|paste|enter|input|fill)\b|"
    r"表格|工作簿|单元格|区域|整块|批量|粘贴|录入|输入|填写",
    re.IGNORECASE,
)


def _spreadsheet_column_number(label: str) -> int:
    value = 0
    for char in str(label or "").upper():
        if not "A" <= char <= "Z":
            return 0
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _spreadsheet_table_block(task: str, subgoal: str) -> dict[str, Any] | None:
    """Extract a literal tabular block that immediately follows a cell range.

    The user-provided tab/newline structure is the source of truth.  This is a
    generic spreadsheet primitive, not an application workflow: once parsed,
    the same clipboard block can be delivered to any focused spreadsheet grid.
    """
    task_text = str(task or "")
    goal_text = str(subgoal or "")
    if not _SPREADSHEET_BLOCK_INTENT_RE.search(goal_text):
        return None
    for match in _SPREADSHEET_RANGE_BLOCK_RE.finditer(task_text):
        start_col, start_row_raw, end_col, end_row_raw = match.groups()
        start_row = int(start_row_raw)
        end_row = int(end_row_raw)
        start_col_number = _spreadsheet_column_number(start_col)
        end_col_number = _spreadsheet_column_number(end_col)
        if start_row < 1 or end_row < start_row or start_col_number < 1 or end_col_number < start_col_number:
            continue
        row_count = end_row - start_row + 1
        column_count = end_col_number - start_col_number + 1
        if row_count > 500 or column_count > 100:
            continue
        range_text = f"{start_col.upper()}{start_row}:{end_col.upper()}{end_row}"
        start_ref = f"{start_col.upper()}{start_row}"
        compact_goal = _compact_key(goal_text)
        if (
            _compact_key(range_text) not in compact_goal
            and not re.search(r"\b(?:entire|whole|all|full)\s+table\b|整个表格|全部数据|所有数据", goal_text, re.IGNORECASE)
            and not re.search(r"\btable\s+(?:data|content)\b|表格(?:数据|内容)", goal_text, re.IGNORECASE)
            and not (
                _compact_key(start_ref) in compact_goal
                and re.search(
                    r"\b(?:start|begin|commence)\s+(?:the\s+)?(?:data\s+)?(?:entry|input)\b|"
                    r"\bdata\s+entry\b|"
                    r"\b(?:type|enter|input|fill)\b.{0,40}\bheader\s+row\b|"
                    r"\b(?:focus|select|click)\b.{0,40}\b(?:spreadsheet\s+grid|cell\s+grid|grid)\b|"
                    r"开始(?:数据)?(?:录入|输入)|数据录入起点|(?:输入|填写).{0,20}表头|聚焦.{0,20}(?:表格|单元格|网格)",
                    goal_text,
                    re.IGNORECASE,
                )
            )
        ):
            continue
        raw_lines = task_text[match.end():].replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(raw_lines) < row_count:
            continue
        rows: list[list[str]] = []
        valid = True
        for raw_line in raw_lines[:row_count]:
            cells = raw_line.split("\t")
            if len(cells) > column_count:
                valid = False
                break
            cells.extend([""] * (column_count - len(cells)))
            rows.append(cells)
        if not valid or len(rows) != row_count:
            continue
        clipboard_text = "\r\n".join("\t".join(row) for row in rows)
        flattened = [cell.strip() for row in rows for cell in row if cell.strip()]
        verification_tokens: list[str] = []
        if flattened:
            verification_tokens.append(flattened[0])
            distinctive = next(
                (value for value in reversed(flattened) if _looks_like_distinctive_state_token(value)),
                flattened[-1],
            )
            verification_tokens.append(distinctive)
        return {
            "range": range_text,
            "start_col": start_col.upper(),
            "start_col_number": start_col_number,
            "start_row": start_row,
            "end_col": end_col.upper(),
            "end_col_number": end_col_number,
            "end_row": end_row,
            "row_count": row_count,
            "column_count": column_count,
            "rows": rows,
            "clipboard_text": clipboard_text,
            "verification_tokens": _unique(verification_tokens),
        }
    return None


def _spreadsheet_bold_rows(subgoal: str) -> list[int]:
    text = str(subgoal or "")
    if not re.search(r"\bbold\b|加粗", text, re.IGNORECASE):
        return []
    rows: list[int] = []
    for pattern in (
        r"\brow\s*(\d{1,5})\b",
        r"第\s*(\d{1,5})\s*行",
        r"(?<!\d)(\d{1,5})\s*行",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = int(match.group(1))
            if 1 <= value <= 1_048_576 and value not in rows:
                rows.append(value)
    return rows


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


def _requested_draw_colors(request: SubgoalRequest, task: str) -> list[str]:
    colors: list[str] = []
    for draw_spec in _draw_specs(request):
        color = _requested_color(draw_spec, request.subgoal, task)
        if color:
            colors.append(color)
    return _unique(colors)


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


_DETAILED_SCENE_RE = re.compile(r"\b(?:detailed|intricate|fine[- ]grained)\b|细节|详细|精细", re.IGNORECASE)
_CREATURE_SCENE_RE = re.compile(
    r"\b(?:cat|dog|animal|creature|person|human|bird|rabbit|horse)\b|猫|狗|动物|人物|人像|鸟|兔|马",
    re.IGNORECASE,
)


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


def _scene_structure_preflight(draw_specs: list[dict[str, Any]], task: str) -> dict[str, Any]:
    detailed = bool(_DETAILED_SCENE_RE.search(str(task or "")))
    if not detailed:
        return {"required": False, "ok": True}
    colors = sorted({
        str(spec.get("color") or spec.get("colour") or "").strip().casefold()
        for spec in draw_specs
        if str(spec.get("color") or spec.get("colour") or "").strip()
    })
    parts = [_semantic_part(spec) for spec in draw_specs]
    labelled = [part for part in parts if part]
    distinct_parts = sorted({part.casefold() for part in labelled})
    errors: list[str] = []
    if len(draw_specs) < 8:
        errors.append("detailed scene needs at least 8 primitives")
    if len(colors) < 3:
        errors.append("detailed colorful scene needs at least 3 requested colors")
    required_label_count = min(len(draw_specs), max(5, math.ceil(len(draw_specs) * 0.65)))
    if len(labelled) < required_label_count:
        errors.append(
            f"semantic part labels are missing ({len(labelled)}/{required_label_count} required)"
        )
    if len(distinct_parts) < 5:
        errors.append("detailed scene needs at least 5 distinct semantic parts")

    creature = bool(_CREATURE_SCENE_RE.search(str(task or "")))
    normalized_parts = " ".join(distinct_parts)
    if creature:
        part_requirements = {
            "head": ("head", "face", "头", "脸"),
            "body": ("body", "torso", "trunk", "身体", "躯干"),
            "eye": ("eye", "pupil", "眼", "瞳"),
        }
        for label, terms in part_requirements.items():
            if not any(term in normalized_parts for term in terms):
                errors.append(f"creature scene is missing a labelled {label} part")
        large_closed_parts = 0
        for spec in draw_specs:
            if str(spec.get("shape") or "").casefold() not in {
                "circle", "ellipse", "rectangle", "square",
            }:
                continue
            raw_box = spec.get("box")
            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
                continue
            try:
                width, height = float(raw_box[2]), float(raw_box[3])
            except (TypeError, ValueError):
                continue
            if max(0.0, width) * max(0.0, height) >= 15000:
                large_closed_parts += 1
        if large_closed_parts < 2:
            errors.append("creature scene needs separate large closed head and body primitives")
    return {
        "required": True,
        "ok": not errors,
        "creature_scene": creature,
        "primitive_count": len(draw_specs),
        "requested_colors": colors,
        "semantic_part_count": len(labelled),
        "distinct_semantic_parts": distinct_parts,
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
    explicit = " ".join([
        str(draw_spec.get("shape") or ""),
        str(draw_spec.get("object") or ""),
    ]).casefold()
    raw = explicit or " ".join([subgoal or "", task or ""]).casefold()
    if any(term in raw for term in ("triangle", "三角", "三角形")):
        return "triangle"
    if any(term in raw for term in ("rectangle", "square", "方形", "矩形", "正方形")):
        return "rectangle"
    if any(term in raw for term in ("circle", "sun", "圆", "圆形")):
        return "circle"
    if any(term in raw for term in ("ellipse", "oval", "椭圆")):
        return "ellipse"
    if any(term in raw for term in ("line", "线", "直线")):
        return "line"
    if any(term in raw for term in ("check", "勾", "对号")):
        return "check"
    return "freeform"


def _requested_color(draw_spec: dict[str, Any], subgoal: str, task: str) -> str:
    color_terms = {
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
    explicit = " ".join([
        str(draw_spec.get("color") or ""),
        str(draw_spec.get("colour") or ""),
    ]).casefold()
    if explicit:
        for color, terms in color_terms.items():
            if any(term in explicit for term in terms):
                return color
        hex_match = re.search(r"#([0-9a-f]{6})\b", explicit, re.IGNORECASE)
        if hex_match:
            value = hex_match.group(1)
            rgb = tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
            palette = {
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
            return min(
                palette,
                key=lambda name: sum(
                    (rgb[channel] - palette[name][channel]) ** 2
                    for channel in range(3)
                ),
            )
    raw = " ".join([subgoal or "", task or ""]).casefold()
    for color, terms in color_terms.items():
        if any(term in raw for term in terms):
            return color
    return ""


def _canvas_region_from_image(window: Region, image) -> Region | None:
    bbox = vision_cv.likely_canvas_bbox(image)
    if not bbox:
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
    if margin_ratio <= 0:
        left, top = canvas.left, canvas.top
        right = max(left + 1, canvas.right - 1)
        bottom = max(top + 1, canvas.bottom - 1)
    else:
        left, top, right, bottom = _inset_region(canvas, margin_ratio)
    cx = (left + right) // 2
    cy = (top + bottom) // 2
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
        radius = half
        points: list[list[int]] = []
        for i in range(33):
            angle = (math.tau * i) / 32
            points.append([int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle))])
        return [points]
    if shape == "ellipse":
        rx = max(12, width // 2)
        ry = max(12, height // 2)
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


_CORE_SCENE_PART_RE = re.compile(
    r"\b(?:body|torso|trunk|head|face|background|frame|canvas|main)\b",
    re.IGNORECASE,
)


def _normalized_closed_shape_region(
    canvas: Region,
    raw: Any,
    *,
    mode: str = "",
    semantic_part: str = "",
) -> tuple[Region | None, dict[str, Any]]:
    """Map a closed-shape box while correcting an obvious xyxy/xywh mix-up.

    Text models sometimes label ``[x1, y1, x2, y2]`` as ``xywh``. Blindly
    trusting that typo turns a small detail into a canvas-sized primitive. We
    preserve explicit modes unless the offset interpretation is at least four
    times larger than a valid, small endpoint interpretation and the primitive
    is a labelled non-core detail. This is scene-generic and does not depend on
    a particular application or object type.
    """
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
    offset_area = max(0.0, third) * max(0.0, fourth)
    endpoint_area = max(0.0, endpoint_width) * max(0.0, endpoint_height)
    labelled_detail = bool(
        str(semantic_part or "").strip()
        and not _CORE_SCENE_PART_RE.search(str(semantic_part or ""))
    )
    obvious_mislabeled_endpoints = bool(
        labelled_detail
        and endpoint_width > 0
        and endpoint_height > 0
        and endpoint_area <= 60_000
        and offset_area >= 60_000
        and offset_area >= endpoint_area * 4.0
    )

    if normalized_mode in {"endpoints", "points", "xyxy"}:
        width, height = endpoint_width, endpoint_height
        interpretation = "xyxy"
    elif normalized_mode in {"xywh", "offset", "width_height"} and obvious_mislabeled_endpoints:
        width, height = endpoint_width, endpoint_height
        interpretation = "xyxy_corrected_from_xywh"
        metadata["box_interpretation_corrected"] = True
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
) -> tuple[list[list[list[int]]], Region] | None:
    """Resolve a line box without assuming endpoint or width/height syntax.

    New calls should use endpoint coordinates. Older text models frequently
    emitted [x, y, width, height], so auto mode chooses the interpretation that
    yields the shorter segment. Explicit box_mode always wins.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        first_x, first_y, third, fourth = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    normalized_mode = str(mode or "").strip().casefold().replace("-", "_")
    endpoint_length = math.hypot(third - first_x, fourth - first_y)
    offset_length = math.hypot(third, fourth)
    if normalized_mode in {"xywh", "offset", "width_height"}:
        use_offsets = True
    elif normalized_mode in {"endpoints", "points", "xyxy"}:
        use_offsets = False
    else:
        use_offsets = offset_length + 1e-6 < endpoint_length
    second_x = first_x + third if use_offsets else third
    second_y = first_y + fourth if use_offsets else fourth
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


def _normalized_strokes_to_screen(canvas: Region, raw: Any) -> list[list[list[int]]]:
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
        session_hwnd = getattr(self.session, "hwnd", None)
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
        if hwnd:
            try:
                focus = self.session.dispatch("focus_window", {"hwnd": hwnd})
                prelude.append({"action": "focus_window", "ok": not bool(focus.terminal), "preview": focus.text[:240]})
            except Exception:
                pass
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
                    "observe", {"full": True, "force_full": True},
                )
                prelude.append({"action": "observe", "ok": not bool(observe.terminal), "preview": observe.text[:320]})
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
        try:
            region = window_region(hwnd)
        except Exception:
            region = getattr(getattr(self.session, "snapshot", None), "region", None)
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

    def _spreadsheet_grid_geometry(self) -> dict[str, Any] | None:
        snapshot = getattr(self.session, "snapshot", None)
        hwnd = getattr(self.session, "hwnd", None)
        region = getattr(snapshot, "region", None)
        if region is None and hwnd:
            try:
                region = window_region(int(hwnd))
            except Exception:
                region = None
        if region is None or region.width < 320 or region.height < 260:
            return None

        elements = list(getattr(snapshot, "elements", []) or [])
        column_a_candidates = [
            element for element in elements
            if str(getattr(element, "text", "") or "").strip().upper() == "A"
            and region.left + int(region.width * 0.025) <= int(getattr(element, "cx", 0) or 0) <= region.left + int(region.width * 0.35)
            and region.top + int(region.height * 0.18) <= int(getattr(element, "cy", 0) or 0) <= region.top + int(region.height * 0.50)
        ]
        column_a = max(column_a_candidates, key=lambda item: int(getattr(item, "cy", 0) or 0), default=None)

        row_points: dict[int, tuple[int, int]] = {}
        for element in elements:
            label = str(getattr(element, "text", "") or "").strip()
            if not re.fullmatch(r"\d{1,5}", label):
                continue
            cx = int(getattr(element, "cx", 0) or 0)
            cy = int(getattr(element, "cy", 0) or 0)
            if cx > region.left + int(region.width * 0.09):
                continue
            if not region.top + int(region.height * 0.22) <= cy <= region.top + int(region.height * 0.92):
                continue
            number = int(label)
            if number not in row_points or cx < row_points[number][0]:
                row_points[number] = (cx, cy)

        if 1 in row_points and 2 in row_points:
            row_height = max(12, row_points[2][1] - row_points[1][1])
        else:
            ordered = sorted(row_points.items())
            deltas = [
                (second_y - first_y) / max(1, second_number - first_number)
                for (first_number, (_first_x, first_y)), (second_number, (_second_x, second_y))
                in zip(ordered, ordered[1:])
                if second_number > first_number and second_y > first_y
            ]
            if deltas:
                ranked_deltas = sorted(deltas)
                row_height = int(round(ranked_deltas[len(ranked_deltas) // 2]))
            else:
                row_height = max(16, int(region.height * 0.024))

        if column_a is not None:
            first_cell_x = int(getattr(column_a, "cx", 0) or 0)
        else:
            first_cell_x = region.left + int(region.width * 0.035)
        if 1 in row_points:
            first_cell_y = row_points[1][1]
        elif row_points:
            # OCR commonly misses the single-digit row labels while reading
            # 10, 11, ... reliably. Infer row 1 from those grounded labels
            # instead of using a fixed window percentage.
            inferred = sorted(
                int(y - (number - 1) * row_height)
                for number, (_x, y) in row_points.items()
            )
            first_cell_y = inferred[len(inferred) // 2]
            first_cell_y = max(
                region.top + int(region.height * 0.12),
                min(first_cell_y, region.top + int(region.height * 0.42)),
            )
        else:
            first_cell_y = region.top + int(region.height * 0.27)
        row_header_x = (
            min(point[0] for point in row_points.values())
            if row_points
            else region.left + int(region.width * 0.012)
        )
        return {
            "region": region,
            "first_cell": (int(first_cell_x), int(first_cell_y)),
            "row_header_x": int(row_header_x),
            "row_points": row_points,
            "row_height": int(row_height),
        }

    def _spreadsheet_table_actions(self, request: SubgoalRequest) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        spec = _spreadsheet_table_block(getattr(self.session, "task", ""), request.subgoal)
        if spec is None:
            return [], None
        applied = getattr(self.session, "_spreadsheet_blocks_applied", set())
        if isinstance(applied, set) and str(spec.get("range") or "") in applied:
            return [], None
        geometry = self._spreadsheet_grid_geometry()
        if geometry is None:
            return [], spec
        x, y = geometry["first_cell"]
        actions: list[dict[str, Any]] = [
            {"action": "hotkey", "keys": ["esc"]},
            {
                "action": "click",
                "x": x,
                "y": y,
                "button": "left",
                "physical": True,
                "skip_auto_observe": True,
                "context": "focus spreadsheet grid",
            },
            {"action": "hotkey", "keys": ["ctrl", "home"]},
        ]
        for _ in range(max(0, int(spec["start_row"]) - 1)):
            actions.append({"action": "hotkey", "keys": ["down"]})
        for _ in range(max(0, int(spec["start_col_number"]) - 1)):
            actions.append({"action": "hotkey", "keys": ["right"]})
        actions.extend([
            {"action": "clipboard_write", "text": str(spec["clipboard_text"])},
            {"action": "clipboard_paste"},
            {"action": "wait_stable", "stable_ms": 400, "timeout_s": 4.0},
            {"action": "read_text"},
        ])
        return actions, spec

    def _spreadsheet_row_format_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        rows = self._spreadsheet_requested_bold_rows(request)
        if not rows:
            return []
        geometry = self._spreadsheet_grid_geometry()
        if geometry is None:
            return []
        row_points = geometry["row_points"]
        row_header_x = int(geometry["row_header_x"])
        row_height = int(geometry["row_height"])
        row_one_y = int(row_points.get(1, (row_header_x, geometry["first_cell"][1]))[1])
        actions: list[dict[str, Any]] = [{"action": "hotkey", "keys": ["esc"]}]
        for row in rows:
            y = int(row_points.get(row, (row_header_x, row_one_y + (row - 1) * row_height))[1])
            actions.extend([
                {
                    "action": "click",
                    "x": row_header_x,
                    "y": y,
                    "button": "left",
                    "physical": True,
                    "skip_auto_observe": True,
                    "context": f"select spreadsheet row {row}",
                },
                {"action": "hotkey", "keys": ["ctrl", "b"]},
            ])
        actions.extend([
            {"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0},
            {"action": "read_text"},
        ])
        return actions

    def _exact_save_as_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        if not bool(request.constraints.get("open_save_as")):
            return []
        save_path = str(
            request.constraints.get("save_path")
            or request.input.get("text")
            or request.input.get("value")
            or ""
        ).strip()
        if not _looks_like_file_path(save_path):
            return []
        snapshot = getattr(self.session, "snapshot", None)
        dialog_ready = bool(
            snapshot is not None
            and any(
                (
                    str(getattr(element, "role", "") or "").casefold() in {"edit", "combobox", "textbox"}
                    and (
                        "文件名称" in str(getattr(element, "text", "") or "")
                        or "文件名" in str(getattr(element, "text", "") or "")
                        or int(getattr(element, "width", 0) or 0) >= 500
                    )
                )
                for element in getattr(snapshot, "elements", []) or []
            )
            and any(
                "保存" in str(getattr(element, "text", "") or "")
                or str(getattr(element, "text", "") or "").strip().casefold() == "save"
                for element in getattr(snapshot, "elements", []) or []
            )
        )
        prefix: list[dict[str, Any]] = []
        if not dialog_ready:
            task_text = str(getattr(self.session, "task", "") or "")
            office_like = bool(re.search(
                r"\b(?:office|word|excel|powerpoint|spreadsheet|workbook|document|presentation|wps)\b|"
                r"办公|文字|表格|工作簿|演示|文档",
                task_text,
                re.IGNORECASE,
            ))
            prefix.extend([
                {"action": "hotkey", "keys": ["f12"] if office_like else ["ctrl", "shift", "s"]},
                {"action": "wait", "duration_ms": 1200},
                {"action": "observe", "full": True, "force_full": True},
            ])
        return [
            *prefix,
            {
                "action": "type_into_element",
                "label": "File name",
                "fallback_text": ["文件名称(N)", "文件名", "文件名称", "File name"],
                "role": "combobox",
                "expected_region": "bottom",
                "text": save_path,
                "clear": True,
                "verify_after_submit": True,
                "context": "Save As filename field",
            },
            {
                "action": "hotkey",
                "keys": ["enter"],
                "expect": {"file_exists": save_path, "timeout_s": 10.0},
            },
            {"action": "wait_stable", "stable_ms": 300, "timeout_s": 5.0},
            {"action": "read_text"},
        ]

    def _spreadsheet_requested_bold_rows(self, request: SubgoalRequest) -> list[int]:
        rows = _spreadsheet_bold_rows(request.subgoal)
        if not rows:
            return []
        task_rows = _spreadsheet_bold_rows(getattr(self.session, "task", ""))
        if task_rows:
            rows = task_rows
        applied = getattr(self.session, "_spreadsheet_bold_rows_applied", set())
        if not isinstance(applied, set):
            applied = set()
        return [row for row in rows if row not in applied]

    def _pending_exact_save_path(self) -> str:
        from ..agent.done_verifier import _explicit_saved_paths

        paths = _explicit_saved_paths(str(getattr(self.session, "task", "") or ""))
        saved_paths = getattr(self.session, "_exact_save_paths_applied", set())
        if not isinstance(saved_paths, set):
            saved_paths = set()
        if not paths or str(paths[0]).casefold() in saved_paths:
            return ""
        return str(paths[0])

    def _advance_to_pending_exact_save(self, request: SubgoalRequest) -> bool:
        save_path = self._pending_exact_save_path()
        if not save_path:
            return False
        request.subgoal = f"Save As the exact requested path: {save_path}"
        request.input = {"label": "File name", "text": save_path, "clear": True}
        request.click = {"label": "Save", "fallback_text": ["Save"]}
        request.clicks = []
        request.verify_texts = [Path(save_path).name]
        request.constraints = {
            "open_save_as": True,
            "save_path": save_path,
            "wait": True,
        }
        return True

    def _spreadsheet_task_actions(
        self,
        request: SubgoalRequest,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Compile explicit table, row-format, and Save As work into one batch."""
        actions, spec = self._spreadsheet_table_actions(request)
        if not actions:
            return [], spec
        task_rows = _spreadsheet_bold_rows(str(getattr(self.session, "task", "") or ""))
        if task_rows:
            format_request = SubgoalRequest(
                subgoal="Make " + " and ".join(f"row {row} bold" for row in task_rows),
            )
            actions.extend(self._spreadsheet_row_format_actions(format_request))
        save_path = self._pending_exact_save_path()
        if save_path:
            request.constraints = {
                **request.constraints,
                "save_after": True,
                "save_path": save_path,
            }
            save_request = SubgoalRequest(
                subgoal=f"Save As the exact requested path: {save_path}",
                input={"label": "File name", "text": save_path, "clear": True},
                click={"label": "Save", "fallback_text": ["Save"]},
                verify_texts=[Path(save_path).name],
                constraints={
                    "open_save_as": True,
                    "save_path": save_path,
                    "wait": True,
                },
            )
            actions.extend(self._exact_save_as_actions(save_request))
        return actions, spec

    def _document_task_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        """Compile an explicit document body, formatting, and Save As routine."""
        task = str(getattr(self.session, "task", "") or "")
        body = _exact_document_text_block(task)
        if not body:
            return []
        formats = [dict(spec) for spec in _document_format_specs(task)]
        lines = [line for line in body.splitlines() if line.strip()]
        request.input = {
            "text": body,
            "document_surface": True,
            "clear": True,
        }
        request.formats = formats
        request.verify_texts = _unique([
            *lines[:1],
            *[
                line for line in lines
                if re.search(r"[A-Za-z]+[_-][A-Za-z0-9_-]+|\d{3,}", line)
            ],
            *lines[-1:],
        ])[:4]
        current_title = ""
        current_hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        if current_hwnd:
            try:
                current_title = str(win32._window_text(current_hwnd) or "").strip()
            except (OSError, TypeError, ValueError):
                current_title = ""
        if not current_title:
            current_title = str(getattr(getattr(self.session, "snapshot", None), "title", "") or "")
        compact_title = re.sub(r"\s+", "", current_title.casefold())
        if compact_title in {
            "文档-wpsoffice",
            "新建文档-wpsoffice",
            "wpsoffice",
            "wps文字-wpsoffice",
        }:
            self.session._document_surface_ready = False
        elif re.search(
            r"(?:(?:文字文稿|文档|document)[-_]?\d+|(?:untitled|未命名)[-_]?\d*)"
            r".*(?:wps|writer|office)",
            compact_title,
            re.IGNORECASE,
        ):
            self.session._document_surface_ready = True

        actions: list[dict[str, Any]] = [
            {"action": "ensure_document_surface", "max_attempts": 3},
        ]
        focus_action = self._central_document_focus_action(request)
        if focus_action:
            actions.append(focus_action)
        actions.extend([
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "hotkey", "keys": ["backspace"]},
        ])
        raw_lines = body.splitlines()
        first_format = next(
            (spec for spec in formats if str(spec.get("range") or "").casefold() == "first_line"),
            {},
        )
        body_format = next(
            (
                spec for spec in formats
                if str(spec.get("range") or "").casefold() == "body_after_first_line"
            ),
            {},
        )
        stream_format = bool(
            len(raw_lines) >= 2
            and all(line.strip() for line in raw_lines)
            and first_format.get("bold") is True
            and str(first_format.get("alignment") or "").casefold() in {"center", "centre"}
            and str(body_format.get("alignment") or "").casefold() == "left"
        )
        if stream_format:
            # A fresh document has a deterministic insertion state. Apply the
            # heading style before typing, then switch it off for the body. This
            # avoids a second coordinate click and fragile backward selection.
            actions.extend([
                {"action": "hotkey", "keys": ["ctrl", "b"]},
                {"action": "hotkey", "keys": ["ctrl", "e"]},
                {"action": "type_text", "text": raw_lines[0]},
                {"action": "hotkey", "keys": ["enter"]},
                {"action": "hotkey", "keys": ["ctrl", "b"]},
                {"action": "hotkey", "keys": ["ctrl", "l"]},
                {"action": "type_text", "text": "\n".join(raw_lines[1:])},
                {"action": "hotkey", "keys": ["ctrl", "end"]},
            ])
        else:
            actions.append({"action": "type_text", "text": body})
            for format_spec in formats:
                actions.extend(_document_format_actions(format_spec))
            if formats:
                actions.append({"action": "hotkey", "keys": ["ctrl", "end"]})
        save_path = self._pending_exact_save_path()
        if save_path:
            request.constraints = {
                **request.constraints,
                "save_after": True,
                "save_path": save_path,
            }
            save_request = SubgoalRequest(
                subgoal=f"Save As the exact requested path: {save_path}",
                input={"label": "File name", "text": save_path, "clear": True},
                click={"label": "Save", "fallback_text": ["Save"]},
                verify_texts=[Path(save_path).name],
                constraints={
                    "open_save_as": True,
                    "save_path": save_path,
                    "wait": True,
                },
            )
            actions.extend(self._exact_save_as_actions(save_request))
        return actions

    def _clipboard_replace_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        """Compile a GUI find/replace operation that consumes the clipboard."""
        scope = str(request.subgoal or "")
        token_match = re.search(r"\[\[[^\]\r\n]{1,100}\]\]", scope)
        if not token_match:
            return []
        token = token_match.group(0)
        task_scope = str(getattr(self.session, "task", "") or "")
        task_binds_token = token in task_scope
        combined_scope = f"{scope}\n{task_scope if task_binds_token else ''}"
        if not re.search(r"\b(?:find\s+and\s+replace|replac(?:e|ing))\b|查找和替换|替换", combined_scope, re.IGNORECASE):
            return []
        if not re.search(r"\bclipboard\b|剪贴板|\bcopy\b|复制", combined_scope, re.IGNORECASE):
            return []
        request.constraints = {
            **request.constraints,
            "clipboard_replace_token": token,
        }
        actions: list[dict[str, Any]] = []
        focus_action = self._central_document_focus_action(request)
        if focus_action:
            actions.append(focus_action)
        actions.extend([
            {"action": "hotkey", "keys": ["ctrl", "h"]},
            {"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0},
            {"action": "observe", "full": True, "force_full": True},
            {
                "action": "type_into_element",
                "label": "查找内容",
                "fallback_text": ["查找内容(N):", "Find what", "Find"],
                "role": "combobox",
                "text": token,
                "clear": True,
            },
            {
                "action": "click_element",
                "label": "替换为",
                "fallback_text": ["替换为(I):", "Replace with"],
                "role": "combobox",
            },
            {"action": "hotkey", "keys": ["ctrl", "v"]},
            {
                "action": "click_element",
                "label": "全部替换",
                "fallback_text": ["全部替换(A)", "Replace All"],
                "role": "button",
            },
            {"action": "wait_change", "timeout_s": 4.0, "min_ratio": 0.0002, "required": False},
            {"action": "wait_stable", "stable_ms": 200, "timeout_s": 3.0},
            {"action": "observe", "full": True, "force_full": True},
            {
                "action": "click_element",
                "label": "确定",
                "fallback_text": ["OK", "确认"],
                "role": "button",
                "optional": True,
            },
            {"action": "wait_stable", "stable_ms": 150, "timeout_s": 2.0},
            {"action": "hotkey", "keys": ["escape"]},
            {"action": "wait_stable", "stable_ms": 150, "timeout_s": 2.0},
            {"action": "read_text"},
        ])
        return actions

    def _build_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        clipboard_replace_actions = self._clipboard_replace_actions(request)
        if clipboard_replace_actions:
            return clipboard_replace_actions
        save_as_actions = self._exact_save_as_actions(request)
        if save_as_actions:
            return save_as_actions
        spreadsheet_actions, _spreadsheet_spec = self._spreadsheet_task_actions(request)
        if spreadsheet_actions:
            return spreadsheet_actions
        spreadsheet_format_actions = self._spreadsheet_row_format_actions(request)
        if spreadsheet_format_actions:
            save_path = str(request.constraints.get("save_path") or "").strip()
            if save_path:
                save_request = SubgoalRequest(
                    subgoal=f"Save As the exact requested path: {save_path}",
                    input={"label": "File name", "text": save_path, "clear": True},
                    click={"label": "Save", "fallback_text": ["Save"]},
                    verify_texts=[Path(save_path).name],
                    constraints={
                        "open_save_as": True,
                        "save_path": save_path,
                        "wait": True,
                    },
                )
                spreadsheet_format_actions.extend(self._exact_save_as_actions(save_request))
            return spreadsheet_format_actions
        if (
            isinstance(request.input, dict)
            and request.input.get("document_surface") is True
            and _exact_document_text_block(str(getattr(self.session, "task", "") or ""))
        ):
            document_actions = self._document_task_actions(request)
            if document_actions:
                return document_actions
        actions: list[dict[str, Any]] = []
        read_after_input_sequence = False
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
                if not request.verify_texts and _looks_like_executable_keyboard_sequence(text):
                    read_after_input_sequence = True
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
            save_path = str(request.constraints.get("save_path") or "").strip()
            if save_path:
                # A named output is a single controller routine, not a new LLM
                # episode. Refresh perception after opening the native dialog
                # so the filename locator works for both UIA and OCR-only UIs.
                actions.extend([
                    {"action": "hotkey", "keys": ["ctrl", "s"]},
                    {"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0},
                    {"action": "observe"},
                    {
                        "action": "type_into_element",
                        "label": "File name",
                        "fallback_text": ["文件名称(N)", "文件名", "文件名称", "File name"],
                        "role": "combobox",
                        "expected_region": "bottom",
                        "text": save_path,
                        "clear": True,
                        "verify_after_submit": True,
                        "context": "Save As filename field",
                    },
                    {
                        "action": "hotkey",
                        "keys": ["enter"],
                        "expect": {"file_exists": save_path, "timeout_s": 8.0},
                    },
                    {"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0},
                    {"action": "read_text"},
                ])
            elif bool(request.constraints.get("save_after")):
                actions.append({"action": "hotkey", "keys": ["ctrl", "s"]})
        draw_specs = _draw_specs(request)
        draw_canvas_context: tuple[Region, list[dict[str, Any]]] | None = None
        if draw_specs:
            hwnd = self._target_hwnd(request, allow_foreground=False)
            window = None
            if hwnd:
                try:
                    window = window_region(hwnd)
                except Exception:
                    snapshot = getattr(self.session, "snapshot", None)
                    window = snapshot.region if snapshot is not None else None
            if window is not None:
                canvas, ready_attempts = self._ready_canvas_region(window)
                if canvas is not None:
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
        for draw_spec in (draw_specs if draw_canvas_context is not None else []):
            draw_action = self._build_draw_action(
                request,
                draw_spec,
                canvas_context=draw_canvas_context,
            )
            if draw_action:
                color = draw_action.get("requested_color")
                if color and color != last_color:
                    actions.append({"action": "select_color", "color": color})
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
        new_blank_shortcut = False
        if not input_spec and len(click_specs) == 1:
            if _is_browser_location_click(click_specs[0]):
                shortcut_keys = ["ctrl", "l"]
            else:
                shortcut_keys = _new_blank_item_hotkey(request, click_specs[0])
            new_blank_shortcut = shortcut_keys == ["ctrl", "n"]
            shortcut_label = (
                click_specs[0].get("label")
                or click_specs[0].get("text")
                or click_specs[0].get("name")
            )
            if not shortcut_keys:
                shortcut_keys = _click_label_to_hotkey(shortcut_label)
            if not shortcut_keys and re.search(
                r"\b(?:press(?:ing|ed)?|hotkey|shortcut)\b|\u6309\u4e0b|\u5feb\u6377\u952e",
                request.subgoal,
                re.IGNORECASE,
            ):
                shortcut_match = re.search(
                    r"(?:ctrl|control|alt|shift)(?:\s*\+\s*(?:ctrl|control|alt|shift|"
                    r"enter|return|tab|escape|esc|space|home|end|delete|del|backspace|"
                    r"up|down|left|right|f(?:[1-9]|1[0-2])|[a-z0-9]))+",
                    request.subgoal,
                    re.IGNORECASE,
                )
                if shortcut_match:
                    shortcut_keys = _click_label_to_hotkey(shortcut_match.group(0))
            if shortcut_keys == ["ctrl", "n"] and re.search(
                r"\b(?:new|create|blank)\b|新建|创建|空白",
                request.subgoal,
                re.IGNORECASE,
            ):
                new_blank_shortcut = True
        if shortcut_keys:
            actions.append({"action": "hotkey", "keys": shortcut_keys})
            if request.constraints.get("wait") is not False:
                if new_blank_shortcut:
                    actions.extend([
                        {
                            "action": "wait_change",
                            "timeout_s": 6.0,
                            "min_ratio": 0.0002,
                            "required": False,
                        },
                        {"action": "wait_stable", "stable_ms": 300, "timeout_s": 5.0, "required": True},
                    ])
                else:
                    actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 3.0})
            shortcut_context = request.subgoal.casefold()
            state_read_required = bool(
                request.verify_texts
                or re.search(
                    r"\b(?:verify|confirm|check|new|create|open|blank)\b|"
                    r"\u9a8c\u8bc1|\u786e\u8ba4|\u68c0\u67e5|\u65b0\u5efa|\u521b\u5efa|\u6253\u5f00|\u7a7a\u767d",
                    shortcut_context,
                )
            )
            if state_read_required:
                actions.append({"action": "read_text"})
            if new_blank_shortcut:
                follow_request = SubgoalRequest(
                    subgoal="Enter the complete table data into the requested cell range in one batch",
                    target_window=dict(request.target_window),
                )
                follow_actions, _follow_spec = self._spreadsheet_task_actions(follow_request)
                if follow_actions:
                    request.constraints = {
                        **request.constraints,
                        **follow_request.constraints,
                    }
                    actions.extend(follow_actions)
                else:
                    document_actions = self._document_task_actions(request)
                    if document_actions:
                        actions.extend(document_actions)
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
        explicit_arithmetic_text = _request_arithmetic_keyboard_text(
            request,
            str(getattr(self.session, "task", "") or ""),
        )
        numeric_keypad_actions = _numeric_keypad_click_actions(
            explicit_arithmetic_text,
            context=request.subgoal,
            click_specs=click_specs,
        )
        if numeric_keypad_actions:
            actions.extend(numeric_keypad_actions)
            return actions
        keyboard_text = _click_sequence_as_keyboard_text(
            request,
            click_specs,
            task=str(getattr(self.session, "task", "") or ""),
        )
        if keyboard_text:
            exact_arithmetic_sequence = bool(
                explicit_arithmetic_text
                and keyboard_text == explicit_arithmetic_text + "="
            )
            action = {
                "action": "type_text",
                "text": keyboard_text,
                "keyboard_sequence_from_clicks": True,
            }
            if exact_arithmetic_sequence:
                action["keyboard_mode"] = "numeric_keypad"
            if len(request.verify_texts) == 1 and not exact_arithmetic_sequence:
                action["expect"] = {"text_appears": request.verify_texts[0]}
            actions.append(action)
            wait_spec = request.constraints.get("wait") if isinstance(request.constraints, dict) else None
            if wait_spec is not False:
                actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0})
            if (
                exact_arithmetic_sequence
                or
                (request.verify_texts and len(request.verify_texts) != 1)
                or (not request.verify_texts and _looks_like_executable_keyboard_sequence(keyboard_text))
            ):
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
        elif read_after_input_sequence:
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
        arithmetic_text = _explicit_arithmetic_keyboard_text(text)
        if arithmetic_text and not _session_has_numeric_keypad(self.session):
            # A failed input-locator attempt can leave a query-directed/ROI
            # snapshot in the session.  Refresh the complete target window
            # once before deciding that an owner-drawn numeric keypad is not
            # available.  This is a recovery-only perception call and keeps
            # normal input paths on the low-latency snapshot.
            try:
                self.controller._dispatch("observe", {
                    "full": True,
                    "force_full": True,
                    "include_icons": True,
                    "max_elements": 240,
                })
            except Exception:
                pass
        if arithmetic_text and _session_has_numeric_keypad(self.session):
            numeric_keypad_actions = _numeric_keypad_click_actions(
                arithmetic_text,
                context=f"{request.subgoal} (on-screen numeric keypad fallback)",
            )
            if numeric_keypad_actions:
                return numeric_keypad_actions
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
        elif not request.verify_texts and _looks_like_executable_keyboard_sequence(text):
            actions.append({"action": "read_text"})
        return actions

    def _ready_canvas_region(self, window: Region, timeout_s: float = 3.5) -> tuple[Region | None, list[dict[str, Any]]]:
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
        if last_image is not None and not _looks_like_unready_window(last_image):
            return _fallback_canvas_region(window), attempts
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
            canvas, ready_attempts = self._ready_canvas_region(window)
            if canvas is None:
                return None
        else:
            canvas, ready_attempts = canvas_context
        shape = _draw_shape_name(draw_spec, request.subgoal, getattr(self.session, "task", ""))
        point_geometry = (
            _normalized_point_geometry(
                canvas,
                draw_spec.get("box"),
                close=shape == "triangle",
            )
            if shape in {"line", "triangle"}
            else None
        )
        line_geometry = (
            point_geometry or _normalized_line_geometry(
                canvas,
                draw_spec.get("box"),
                str(draw_spec.get("box_mode") or ""),
            )
            if shape == "line"
            else None
        )
        draw_region = (
            point_geometry[1]
            if point_geometry is not None
            else line_geometry[1] if line_geometry is not None else canvas
        )
        part = _semantic_part(draw_spec)
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
        normalized_strokes = _normalized_strokes_to_screen(
            canvas,
            draw_spec.get("normalized_strokes"),
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
        self.session._last_canvas_region = {
            "x": canvas.left,
            "y": canvas.top,
            "width": canvas.width,
            "height": canvas.height,
        }
        action.update(box_metadata)
        if part:
            action["semantic_part"] = part
        color = _requested_color(draw_spec, request.subgoal, getattr(self.session, "task", ""))
        if color:
            action["requested_color"] = color
        return action

    def _draw_color_evidence(self, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        click_specs = request.clicks or ([request.click] if request.click else [])
        new_blank_requested = bool(
            not request.input
            and len(click_specs) == 1
            and _new_blank_item_hotkey(request, click_specs[0]) == ["ctrl", "n"]
        )
        if new_blank_requested and getattr(self.session, "_new_blank_applied", False):
            task_text = str(getattr(self.session, "task", "") or "")
            table_spec = _spreadsheet_table_block(
                task_text,
                "Enter the complete table data into the requested cell range in one batch",
            )
            applied_blocks = getattr(self.session, "_spreadsheet_blocks_applied", set())
            if (
                table_spec is not None
                and (
                    not isinstance(applied_blocks, set)
                    or str(table_spec.get("range") or "") not in applied_blocks
                )
            ):
                request.subgoal = "Enter the complete table data into the requested cell range in one batch"
                request.click = {"label": "A1", "expected_region": "spreadsheet grid"}
                request.clicks = []
                new_blank_requested = False
            else:
                return ToolResult(json.dumps({
                    "ok": True,
                    "verified": False,
                    "mutation_skipped": True,
                    "new_blank_applied": True,
                    "postcondition_pending": True,
                    "needs_llm_reason": False,
                    "subgoal": request.subgoal,
                    "subgoal_route": "controller_first",
                    "controller_state": {
                        "phase": "executed",
                        "verified": False,
                        "needs_llm_reason": False,
                    },
                    "evidence_packet": {"matched_texts": [], "missing_texts": []},
                    "fast_path_advice": (
                        "A new blank document/workbook was already created in this run. The duplicate "
                        "Ctrl+N mutation was skipped; continue with the next content, formatting, or save phase."
                    ),
                }, ensure_ascii=False, default=str))
        task_text = str(getattr(self.session, "task", "") or "")
        applied_blocks = getattr(self.session, "_spreadsheet_blocks_applied", set())
        pure_verify_request = bool(
            request.verify_texts
            and not request.input
            and not request.click
            and not request.clicks
            and not request.formats
            and not request.draw
            and not request.draws
        )
        if pure_verify_request and isinstance(applied_blocks, set) and applied_blocks:
            task_rows = _spreadsheet_bold_rows(task_text)
            applied_rows = getattr(self.session, "_spreadsheet_bold_rows_applied", set())
            if not isinstance(applied_rows, set):
                applied_rows = set()
            pending_rows = [row for row in task_rows if row not in applied_rows]
            if pending_rows:
                request.subgoal = "Make " + " and ".join(f"row {row} bold" for row in pending_rows)
                request.verify_texts = []
            else:
                if not self._advance_to_pending_exact_save(request):
                    return ToolResult(json.dumps({
                        "ok": True,
                        "verified": False,
                        "mutation_skipped": True,
                        "postcondition_pending": True,
                        "needs_llm_reason": False,
                        "subgoal": request.subgoal,
                        "subgoal_route": "controller_first",
                        "controller_state": {
                            "phase": "executed",
                            "verified": False,
                            "needs_llm_reason": False,
                        },
                        "evidence_packet": {
                            "matched_texts": [],
                            "missing_texts": [],
                            "spreadsheet_ranges_applied": sorted(applied_blocks),
                        },
                        "fast_path_advice": (
                            "The spreadsheet mutation stages are already complete. Do not repeat visual "
                            "cell verification; use the saved workbook artifact verifier or call done."
                        ),
                    }, ensure_ascii=False, default=str))
        if _spreadsheet_bold_rows(request.subgoal) and not request.constraints.get("open_save_as"):
            pending_save_path = self._pending_exact_save_path()
            if pending_save_path:
                request.constraints = {
                    **request.constraints,
                    "save_after": True,
                    "save_path": pending_save_path,
                }
        exact_save_path = str(request.constraints.get("save_path") or "").strip()
        if request.constraints.get("open_save_as") and exact_save_path:
            request.verify_texts = [Path(exact_save_path).name]
        requested_bold_rows = _spreadsheet_bold_rows(request.subgoal)
        task_bold_rows = _spreadsheet_bold_rows(getattr(self.session, "task", ""))
        all_requested_bold_rows = (task_bold_rows or requested_bold_rows) if requested_bold_rows else []
        applied_bold_rows = getattr(self.session, "_spreadsheet_bold_rows_applied", set())
        if not isinstance(applied_bold_rows, set):
            applied_bold_rows = set()
        if all_requested_bold_rows and all(row in applied_bold_rows for row in all_requested_bold_rows):
            if self._advance_to_pending_exact_save(request):
                exact_save_path = str(request.constraints.get("save_path") or "").strip()
                all_requested_bold_rows = []
            else:
                payload = {
                    "ok": True,
                    "verified": False,
                    "mutation_skipped": True,
                    "spreadsheet_row_format_applied": sorted(applied_bold_rows),
                    "postcondition_pending": True,
                    "subgoal": request.subgoal,
                    "subgoal_route": "controller_first",
                    "controller_state": {
                        "phase": "executed",
                        "verified": False,
                        "needs_llm_reason": False,
                    },
                    "evidence_packet": {
                        "matched_texts": [],
                        "missing_texts": [],
                        "spreadsheet_bold_rows": sorted(applied_bold_rows),
                    },
                    "needs_llm_reason": False,
                    "fast_path_advice": (
                        "The requested spreadsheet rows already received the bold toggle in this run. "
                        "Do not press Ctrl+B again because it would undo the formatting. Continue to Save As; "
                        "the XLSX artifact verifier will confirm the final styles."
                    ),
                }
                return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        spreadsheet_spec = _spreadsheet_table_block(
            getattr(self.session, "task", ""),
            request.subgoal,
        )
        applied_spreadsheet_blocks = getattr(self.session, "_spreadsheet_blocks_applied", set())
        if (
            spreadsheet_spec is not None
            and isinstance(applied_spreadsheet_blocks, set)
            and str(spreadsheet_spec.get("range") or "") in applied_spreadsheet_blocks
        ):
            spreadsheet_spec = None
        if spreadsheet_spec is not None:
            start_ref = f"{spreadsheet_spec.get('start_col')}{spreadsheet_spec.get('start_row')}"
            anchor_only_keys = {
                _compact_key(start_ref),
                _compact_key(f"cell {start_ref}"),
                _compact_key(f"{start_ref} cell"),
                _compact_key(f"单元格{start_ref}"),
            }
            request.verify_texts = _unique([
                *[
                    text for text in request.verify_texts
                    if _compact_key(text) not in anchor_only_keys
                ],
                *list(spreadsheet_spec.get("verification_tokens") or []),
            ])
            task_text = str(getattr(self.session, "task", "") or "")
            explicit_draw_task = bool(re.search(
                r"\b(?:draw|paint|sketch|illustrate|diagram)\b|\u753b|\u7ed8\u5236|\u6d82\u9e26|\u63d2\u753b",
                task_text,
                re.IGNORECASE,
            ))
            if not explicit_draw_task:
                # Some models misuse draw.box as a target-region hint when
                # composing a spreadsheet request. The semantic spreadsheet
                # routine does not execute that draw, so it must not create a
                # phantom color postcondition after the workbook was saved.
                request.draw = {}
                request.draws = []
        launch_prelude: list[dict[str, Any]] = []
        draw_specs = _draw_specs(request)
        prior_scene_specs = getattr(self.session, "_verified_draw_scene_specs", [])
        if not isinstance(prior_scene_specs, list):
            prior_scene_specs = []
        scene_preflight = _scene_structure_preflight(
            [*prior_scene_specs, *draw_specs],
            getattr(self.session, "task", ""),
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
                    "Revise the same draws[] call before touching the canvas. Give every primitive a "
                    "semantic part label. For a person or creature, use separate large head and body "
                    "primitives; keep eyes inside the head and attach limbs/tail to the body. Preserve "
                    "one subject frame and avoid detached decorative components."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        snapshot, prelude, skip_click_auto_observe = self._ensure_observed(request)
        prelude = launch_prelude + prelude
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
        ) if snapshot is not None else {}
        actions = self._build_actions(request)
        if spreadsheet_spec is None and any(
            action.get("action") == "clipboard_paste" for action in actions
        ):
            spreadsheet_spec = _spreadsheet_table_block(
                getattr(self.session, "task", ""),
                "Enter the complete table data into the requested cell range in one batch",
            )
            if spreadsheet_spec is not None:
                request.verify_texts = _unique([
                    *request.verify_texts,
                    *list(spreadsheet_spec.get("verification_tokens") or []),
                ])
        if skip_click_auto_observe:
            for action in actions:
                if action.get("action") == "click_element":
                    action["skip_auto_observe"] = True
        if not actions:
            needs_target_window = bool(draw_specs)
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
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
                    "execute_subgoal could not find a ready canvas for the draw routine."
                    if draw_specs else
                    "execute_subgoal needs an input.text routine, a click.label routine, a draw routine, or verify_texts."
                ),
                "needs_llm_reason": True,
            }
            if draw_specs:
                payload["fast_path_advice"] = (
                    "A draw routine requires a focused target app/window with a ready canvas. "
                    "Do not retry execute_subgoal(draw) yet; first use launch/find_app/focus_window "
                    "for the requested app, then call execute_subgoal(draw or draws) after the window "
                    "is foreground and ready."
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
        new_shortcut_results = [
            item
            for action, item in zip(executed_actions, batch_results)
            if isinstance(action, dict)
            and action.get("action") == "hotkey"
            and [str(key).casefold() for key in action.get("keys") or []] == ["ctrl", "n"]
            and isinstance(item, dict)
            and item.get("ok") is True
        ]
        new_wait_changed = any(
            isinstance(item, dict)
            and item.get("action") == "wait_change"
            and item.get("ok") is True
            and (
                (isinstance(item.get("data"), dict) and item["data"].get("changed") is True)
                or (isinstance(item.get("data"), dict)
                    and isinstance(item["data"].get("delta"), dict)
                    and item["data"]["delta"].get("changed") is True)
            )
            for item in batch_results
        )
        prior_title = str(getattr(snapshot, "title", "") or "").strip()
        new_window_adopted = any(
            isinstance(item.get("data"), dict)
            and (
                bool(item["data"].get("controller_adopted_foreground_hwnd"))
                or (
                    prior_title
                    and isinstance(item["data"].get("post_state"), dict)
                    and str(item["data"]["post_state"].get("foreground_title") or "").strip()
                    and str(item["data"]["post_state"].get("foreground_title") or "").strip() != prior_title
                )
            )
            for item in new_shortcut_results
        )
        new_blank_applied = bool(
            new_blank_requested
            and new_shortcut_results
            and (new_wait_changed or new_window_adopted)
        )
        planned_draw_actions = [
            action for action in actions if action.get("action") == "draw_strokes"
        ]
        verified_draw_actions = []
        for result in batch_results:
            if (
                result.get("action") != "draw_strokes"
                or not bool(result.get("ok", result.get("verified")))
                or result.get("verified", True) is False
            ):
                continue
            if len(verified_draw_actions) < len(planned_draw_actions):
                verified_draw_actions.append(planned_draw_actions[len(verified_draw_actions)])
        draw_visible_colors, per_draw_color_evidence = self._draw_color_evidence(
            verified_draw_actions
        )
        visible_colors = {**visible_colors, **draw_visible_colors}
        requested_colors = _requested_draw_colors(request, getattr(self.session, "task", ""))
        matched_visual_colors = [color for color in requested_colors if color in visible_colors]
        missing_visual_colors = [color for color in requested_colors if color not in visible_colors]
        scene_structure = dict(scene_preflight)
        scene_structure.update({
            "executed_draw_count": len(verified_draw_actions),
            "matched_visual_color_count": len(matched_visual_colors),
            "structured_scene_verified": bool(
                scene_preflight.get("required")
                and scene_preflight.get("ok")
                and bool(batch.get("ok"))
                and len(verified_draw_actions) == len(planned_draw_actions)
                and len(matched_visual_colors) >= 3
                and not missing_visual_colors
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
        save_dispatched = False
        exact_save_path = str(request.constraints.get("save_path") or "").strip()
        if request.formats and bool(request.constraints.get("save_after")):
            for action, item in zip(executed_actions, batch_results):
                keys = [str(key).casefold() for key in (action.get("keys") or [])]
                final_exact_save = bool(
                    exact_save_path
                    and keys == ["enter"]
                    and isinstance(action.get("expect"), dict)
                    and action["expect"].get("file_exists") == exact_save_path
                )
                ordinary_save = bool(not exact_save_path and keys == ["ctrl", "s"])
                if (
                    action.get("action") == "hotkey"
                    and (ordinary_save or final_exact_save)
                    and isinstance(item, dict)
                    and item.get("ok") is True
                ):
                    save_dispatched = True
                    break
        verified = (
            bool(batch.get("ok"))
            and not missing
            and not missing_visual_colors
            and bool(navigation.get("ok", True))
        ) if (request.verify_texts or requested_colors) else bool(
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
            "matched_visual_colors": matched_visual_colors,
            "missing_visual_colors": missing_visual_colors,
            "scene_structure": scene_structure,
            "candidate_click_labels": recovery_candidates,
            "visible_text_preview": visible_text[:1200],
            "navigation": navigation,
            "perception_reducer": reduction,
            "prelude": prelude,
            "input_applied": input_applied,
            "save_dispatched": save_dispatched,
            "format_applied": format_applied,
            "format_specs": request.formats,
        }
        spreadsheet_table_applied = bool(
            spreadsheet_spec is not None
            and any(
                isinstance(item, dict)
                and item.get("action") == "clipboard_paste"
                and item.get("ok") is True
                for item in batch_results
            )
        )
        if spreadsheet_table_applied and spreadsheet_spec is not None:
            applied = getattr(self.session, "_spreadsheet_blocks_applied", None)
            if not isinstance(applied, set):
                applied = set()
                setattr(self.session, "_spreadsheet_blocks_applied", applied)
            applied.add(str(spreadsheet_spec.get("range") or ""))
        spreadsheet_rows_applied: list[int] = []
        requested_rows_for_action = sorted({
            int(match.group(1))
            for action in executed_actions
            for match in [re.search(
                r"select spreadsheet row\s+(\d+)",
                str(action.get("context") or ""),
                re.IGNORECASE,
            )]
            if match
        })
        if not requested_rows_for_action:
            requested_rows_for_action = self._spreadsheet_requested_bold_rows(request)
        if requested_rows_for_action:
            bold_hotkeys = sum(
                1
                for action, item in zip(executed_actions, batch_results)
                if action.get("action") == "hotkey"
                and [str(key).casefold() for key in action.get("keys") or []] == ["ctrl", "b"]
                and isinstance(item, dict)
                and item.get("ok") is True
            )
            if bold_hotkeys >= len(requested_rows_for_action):
                applied = getattr(self.session, "_spreadsheet_bold_rows_applied", None)
                if not isinstance(applied, set):
                    applied = set()
                    setattr(self.session, "_spreadsheet_bold_rows_applied", applied)
                applied.update(requested_rows_for_action)
                spreadsheet_rows_applied = sorted(requested_rows_for_action)
        exact_save_as_applied = bool(
            exact_save_path
            and any(
                isinstance(action, dict)
                and action.get("action") == "hotkey"
                and [str(key).casefold() for key in action.get("keys") or []] == ["enter"]
                and isinstance(action.get("expect"), dict)
                and action["expect"].get("file_exists") == exact_save_path
                and isinstance(item, dict)
                and item.get("ok") is True
                for action, item in zip(executed_actions, batch_results)
            )
        )
        if spreadsheet_spec is not None:
            evidence_packet["spreadsheet_table"] = {
                "range": spreadsheet_spec.get("range"),
                "row_count": spreadsheet_spec.get("row_count"),
                "column_count": spreadsheet_spec.get("column_count"),
                "clipboard_batch": True,
                "applied": spreadsheet_table_applied,
            }
        if partial_matched and not batch.get("ok"):
            evidence_packet["partial_matched_texts"] = partial_matched
        payload = {
            "ok": bool(batch.get("ok")) and not missing and not missing_visual_colors,
            "verified": bool(verified),
            "subgoal": request.subgoal,
            "subgoal_route": "controller_first",
            "controller_state": {
                **(batch.get("controller_state") or {}),
                "verified": bool(verified),
                "needs_llm_reason": bool(missing or missing_visual_colors or not batch.get("ok")),
            },
            "actions_executed": executed_actions,
            "results": _public_batch_results(batch.get("results") or []),
            "metrics": batch.get("metrics") or {},
            "evidence_packet": evidence_packet,
            "needs_llm_reason": bool(missing or missing_visual_colors or not batch.get("ok")),
            "input_applied": input_applied,
            "spreadsheet_table_applied": spreadsheet_table_applied,
            "spreadsheet_row_format_applied": spreadsheet_rows_applied,
            "exact_save_as_applied": exact_save_as_applied,
            "new_blank_applied": new_blank_applied,
        }
        clipboard_replace_token = str(
            request.constraints.get("clipboard_replace_token") or ""
        ).strip()
        if clipboard_replace_token and bool(batch.get("ok")):
            replacement_evidence = json.dumps(
                batch.get("results") or [],
                ensure_ascii=False,
                default=str,
            )
            replacement_counts = [
                int(value)
                for value in re.findall(
                    r"(?:完成\s*|replaced\s*)(\d+)\s*(?:处替换|replacement)",
                    replacement_evidence,
                    re.IGNORECASE,
                )
            ]
            replacement_count = max(replacement_counts, default=-1)
            replacement_verified = replacement_count > 0
            payload.update({
                "ok": True,
                "verified": replacement_verified,
                "clipboard_replace_applied": True,
                "clipboard_replace_token": clipboard_replace_token,
                "mutation_applied": True,
                "postcondition_pending": not replacement_verified,
                "needs_llm_reason": False,
                "replacement_count": replacement_count if replacement_count >= 0 else None,
            })
            payload["controller_state"].update({
                "phase": "verified" if replacement_verified else "executed",
                "verified": replacement_verified,
                "needs_llm_reason": False,
            })
            payload["evidence_packet"]["clipboard_replace_token"] = clipboard_replace_token
            payload["fast_path_advice"] = (
                f"The controller completed the GUI Find/Replace routine for {clipboard_replace_token}. "
                + (
                    f"The application reported {replacement_count} replacement(s). "
                    if replacement_verified
                    else "Verify that this token is absent in the document. "
                )
                + "Continue the next source or save; "
                "do not repeat the replacement."
            )
        if new_blank_applied:
            self.session._new_blank_applied = True
            self.session._document_surface_ready = True
            payload.update({
                "ok": True,
                "mutation_applied": True,
                "postcondition_pending": True,
                "needs_llm_reason": False,
            })
            payload["controller_state"].update({
                "phase": "executed",
                "needs_llm_reason": False,
            })
            payload["fast_path_advice"] = (
                "The new blank document/workbook was created successfully. Do not invoke New or Ctrl+N "
                "again; continue directly with the requested content routine."
            )
        if exact_save_as_applied:
            saved = getattr(self.session, "_exact_save_paths_applied", None)
            if not isinstance(saved, set):
                saved = set()
                setattr(self.session, "_exact_save_paths_applied", saved)
            saved.add(exact_save_path.casefold())
            payload.update({
                "verified": True,
                "mutation_applied": True,
                "postcondition_pending": True,
                "needs_llm_reason": False,
                "saved_paths": [exact_save_path],
            })
            payload["controller_state"].update({
                "phase": "verified",
                "verified": True,
            })
            payload["controller_state"]["needs_llm_reason"] = False
            payload["evidence_packet"]["saved_paths"] = [exact_save_path]
        if spreadsheet_rows_applied:
            payload.update({
                "mutation_applied": True,
                "postcondition_pending": True,
                "needs_llm_reason": False,
            })
            payload["controller_state"]["needs_llm_reason"] = False
            payload["evidence_packet"]["spreadsheet_bold_rows"] = spreadsheet_rows_applied
        if format_applied:
            save_failed = bool(request.constraints.get("save_path")) and not save_dispatched
            payload.update({
                "format_applied": True,
                "mutation_applied": True,
                "postcondition_pending": True,
                "save_dispatched": save_dispatched,
                "save_failed": save_failed,
                "needs_llm_reason": save_failed,
            })
            payload["controller_state"]["needs_llm_reason"] = save_failed
            payload["fast_path_advice"] = (
                "The requested rich-text ranges were selected and formatted in one local controller "
                "batch. "
                + (
                    "Save was dispatched in the same batch; verify the saved artifact and do not repeat "
                    "the selection or formatting hotkeys."
                    if save_dispatched else
                    "The later save phase did not finish. Resume only the Save/Save As workflow and "
                    "verify the artifact; do not repeat the selection or formatting hotkeys."
                )
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
        mutation_stage_applied = bool(
            spreadsheet_table_applied
            or spreadsheet_rows_applied
            or format_applied
            or exact_save_as_applied
        )
        if missing and not mutation_stage_applied:
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
        elif missing and mutation_stage_applied:
            # The successful mutation grounds this stage. OCR can miss bold
            # text or split identifiers at cell boundaries, so unresolved
            # literals are deferred to the saved artifact verifier instead of
            # triggering a duplicate non-idempotent action.
            evidence_packet["pending_artifact_texts"] = list(missing)
            evidence_packet["missing_texts"] = []
            payload.update({
                "ok": True,
                "verified": bool(exact_save_as_applied),
                "mutation_applied": True,
                "postcondition_pending": True,
                "needs_llm_reason": False,
            })
            payload["controller_state"].update({
                "phase": "verified" if exact_save_as_applied else "executed",
                "verified": bool(exact_save_as_applied),
                "needs_llm_reason": False,
            })
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
        elif spreadsheet_table_applied:
            payload["fast_path_advice"] = (
                "The complete literal spreadsheet block was pasted into the requested range in one "
                "controller batch. Do not continue cell-by-cell or paste the block again. Continue "
                "with formatting/save, and use the saved workbook artifact for exact cell/formula verification."
            )
        elif spreadsheet_rows_applied:
            payload["fast_path_advice"] = (
                "All explicitly requested spreadsheet rows received the bold toggle in one controller "
                "batch. Do not press Ctrl+B again because it is not idempotent. Continue directly to "
                "Save As; the saved XLSX style verifier will provide the final proof."
            )
        elif exact_save_as_applied:
            payload["fast_path_advice"] = (
                "Save As completed and the exact output path exists from this controller batch. "
                "Do not open the File menu or save again. Continue to final artifact verification/done."
            )
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={"tool": "execute_subgoal", "subgoal": request.subgoal, "actions": actions},
        )
