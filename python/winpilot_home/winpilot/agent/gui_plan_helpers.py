"""Small GUI-planning helpers used by the unified AgentRunner.

These functions are intentionally pure and model-free. They keep drawing/schema
heuristics out of ``agent.loop`` so the main loop stays focused on orchestration.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import CONFIG
from .task_rules import _is_drawing_task, _is_gui_only_task, _required_image_paths

_DRAW_COLOR_TERMS = {
    "blue": ("blue", "蓝色", "蓝"),
    "red": ("red", "红色", "红"),
    "green": ("green", "绿色", "绿"),
    "yellow": ("yellow", "黄色", "黄"),
    "black": ("black", "黑色", "黑"),
    "purple": ("purple", "紫色", "紫"),
    "orange": ("orange", "橙色", "橙"),
}
_DRAW_SHAPE_TERMS = {
    "triangle": ("triangle", "三角形", "三角"),
    "rectangle": ("rectangle", "square", "矩形", "正方形", "方形"),
    "circle": ("circle", "圆形", "圆"),
    "line": ("line", "直线", "线"),
}
_DRAW_REGION_TERMS = {
    "left": ("left", "左侧", "左边", "左"),
    "right": ("right", "右侧", "右边", "右"),
    "center": ("center", "middle", "中间", "中央", "中部"),
    "top": ("top", "上方", "上面", "顶部"),
    "bottom": ("bottom", "下方", "下面", "底部"),
}
_DRAW_NON_ACTION_TERMS = (
    "confirm", "verify", "verification", "check", "visible", "exist", "exists",
    "screenshot", "screen shot", "capture", "save",
    "确认", "核验", "验证", "检查", "可见", "存在", "截图", "截屏", "保存",
)
_DRAW_VISUAL_CONFIRM_TERMS = (
    "confirm", "verify", "verification", "check", "visible", "exist", "exists",
    "show", "shown", "on screen", "canvas",
    "确认", "核验", "验证", "检查", "可见", "存在", "显示", "画布", "画面",
)
_DRAW_VISUAL_OBJECT_TERMS = (
    "shape", "shapes", "drawing", "canvas", "color", "colors",
    "triangle", "rectangle", "square", "circle", "line",
    "图形", "画布", "画面", "颜色", "三角形", "矩形", "方形", "圆形", "线",
)
_DRAW_FILE_OUTPUT_TERMS = (
    "save", "save as", "export", "write file",
    "保存", "另存", "另存为", "导出", "写入文件",
)
_DRAW_ACTION_RE = re.compile(r"(draw|paint|sketch|绘制|画)")

_OPEN_PLAN_TERMS = (
    "open", "launch", "start", "focus", "bring up",
    "打开", "启动", "运行", "聚焦", "置前",
)
_OPEN_PLAN_MIXED_ACTION_TERMS = (
    "draw", "type", "input", "write", "search", "send", "click",
    "verify", "confirm", "screenshot", "save", "upload", "publish",
    "绘制", "画一个", "画一", "输入", "搜索", "发送", "点击", "确认", "核验", "验证",
    "截图", "保存", "上传", "发布",
)


def _schema_profile_for_task(task: str, *, is_coding: bool) -> str:
    """Choose the model-facing tool schema size for the current run."""
    configured = str(CONFIG.get("agent", "tool_profile", default="auto") or "auto").strip().lower()
    if configured in {"standard", "full", "lean_gui", "drawing_gui"}:
        return configured
    if is_coding:
        return "standard"
    if _is_drawing_task(task):
        return "drawing_gui"
    if _is_gui_only_task(task):
        return "lean_gui"
    return "standard"


def _find_term(text: str, term_map: dict[str, tuple[str, ...]]) -> str:
    haystack = (text or "").casefold()
    for value, terms in term_map.items():
        if any(term.casefold() in haystack for term in terms):
            return value
    return ""


def _find_terms(text: str, term_map: dict[str, tuple[str, ...]]) -> list[str]:
    haystack = (text or "").casefold()
    return [
        value
        for value, terms in term_map.items()
        if any(term.casefold() in haystack for term in terms)
    ]


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    haystack = (text or "").casefold()
    return any(term.casefold() in haystack for term in terms)


def _drawing_task_requests_file_output(task: str) -> bool:
    return bool(_required_image_paths(task)) or _has_any_term(task, _DRAW_FILE_OUTPUT_TERMS)


def _drawing_visual_confirmation_goal(goal: str) -> bool:
    return (
        _has_any_term(goal, _DRAW_VISUAL_CONFIRM_TERMS)
        and _has_any_term(goal, _DRAW_VISUAL_OBJECT_TERMS)
    )


def _looks_like_pure_open_plan_goal(goal: str) -> bool:
    text = (goal or "").casefold()
    if not any(term.casefold() in text for term in _OPEN_PLAN_TERMS):
        return False
    return not any(term.casefold() in text for term in _OPEN_PLAN_MIXED_ACTION_TERMS)


def _drawing_plan_check_allows_visual_completion(
    check: Any,
    task: str,
) -> bool:
    if not isinstance(check, dict) or not check:
        return True
    keys = {str(key) for key in check if str(key) != "timeout_s"}
    if not keys:
        return True
    visual_keys = {"text_appears", "text_contains", "window_title", "window_appears"}
    file_keys = {"file_exists", "file_contains"}
    if keys <= visual_keys:
        return True
    if keys <= (visual_keys | file_keys) and not _drawing_task_requests_file_output(task):
        return True
    return False


def _drawing_visual_confirmation_colors(goal: str, check: Any, task: str) -> list[str]:
    text = goal or ""
    if isinstance(check, dict):
        for value in check.values():
            if isinstance(value, str):
                text += " " + value
            elif isinstance(value, dict):
                text += " " + " ".join(str(v) for v in value.values())
    colors = _find_terms(text, _DRAW_COLOR_TERMS)
    if not colors:
        colors = _find_terms(task, _DRAW_COLOR_TERMS)
    return colors


def _parse_plan_draw_spec(text: str) -> dict[str, str]:
    lowered = (text or "").casefold()
    if any(term.casefold() in lowered for term in _DRAW_NON_ACTION_TERMS):
        return {}
    action_matches = list(_DRAW_ACTION_RE.finditer(text or ""))
    target_text = (text or "")[action_matches[-1].end():] if action_matches else (text or "")
    shape = _find_term(target_text, _DRAW_SHAPE_TERMS) or _find_term(text, _DRAW_SHAPE_TERMS)
    color = _find_term(target_text, _DRAW_COLOR_TERMS) or _find_term(text, _DRAW_COLOR_TERMS)
    if not (shape and color):
        return {}
    spec = {"shape": shape, "color": color}
    region = _find_term(text, _DRAW_REGION_TERMS)
    if region:
        spec["expected_region"] = region
    return spec


def _term_occurrences(text: str, term_map: dict[str, tuple[str, ...]]) -> list[tuple[int, int, str]]:
    haystack = (text or "").casefold()
    out: list[tuple[int, int, str]] = []
    for value, terms in term_map.items():
        for term in sorted(terms, key=len, reverse=True):
            needle = term.casefold()
            if not needle:
                continue
            start = 0
            while True:
                pos = haystack.find(needle, start)
                if pos < 0:
                    break
                out.append((pos, pos + len(needle), value))
                start = pos + max(1, len(needle))
    return sorted(out, key=lambda item: (item[0], item[1]))


def _extract_draw_specs_from_task_text(text: str) -> list[dict[str, str]]:
    """Extract simple color+shape drawing requests from the original task."""
    shapes = _term_occurrences(text, _DRAW_SHAPE_TERMS)
    colors = _term_occurrences(text, _DRAW_COLOR_TERMS)
    regions = _term_occurrences(text, _DRAW_REGION_TERMS)
    specs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for shape_start, shape_end, shape in shapes:
        best_color: tuple[int, int, str] | None = None
        best_score = -10_000
        for color_start, color_end, color in colors:
            distance = min(abs(shape_start - color_end), abs(color_start - shape_end))
            if distance > 36:
                continue
            before_bonus = 12 if color_start <= shape_start else 0
            score = before_bonus - distance
            if score > best_score:
                best_color = (color_start, color_end, color)
                best_score = score
        if best_color is None:
            continue
        color_start, color_end, color = best_color
        window_start = max(0, min(color_start, shape_start) - 36)
        window_end = min(len(text or ""), max(color_end, shape_end) + 36)
        region = ""
        best_region_score = 10_000
        for region_start, region_end, region_value in regions:
            if window_start <= region_start and region_end <= window_end:
                distance = min(
                    abs(region_end - min(color_start, shape_start)),
                    abs(region_start - max(color_end, shape_end)),
                )
                if region_start < shape_end:
                    distance += 48
                if distance < best_region_score:
                    best_region_score = distance
                    region = region_value
        spec = {"shape": shape, "color": color}
        if region:
            spec["expected_region"] = region
        key = _draw_spec_key(spec)
        if key not in seen:
            seen.add(key)
            specs.append(spec)
    return specs


def _draw_spec_signature(spec: dict[str, Any]) -> tuple[str, str]:
    return (
        str(spec.get("shape") or "").casefold(),
        str(spec.get("color") or spec.get("colour") or "").casefold(),
    )


def _draw_spec_region(spec: dict[str, Any]) -> str:
    return str(spec.get("expected_region") or spec.get("region") or "").casefold()


def _draw_spec_key(spec: dict[str, Any]) -> tuple[str, str, str]:
    shape, color = _draw_spec_signature(spec)
    return shape, color, _draw_spec_region(spec)
