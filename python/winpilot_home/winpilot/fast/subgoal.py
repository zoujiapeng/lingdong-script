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
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import win32gui

from ..action.verify import _match_visible_text
from ..agent.task_rules import _required_literal_tokens, _required_visible_payloads, _visible_state_targets
from ..agent.tool_result import ToolResult
from ..perception import ocr, vision_cv, win32
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


def parse_subgoal_request(args: dict[str, Any], task: str = "") -> SubgoalRequest:
    input_spec = _as_dict(args.get("input") or args.get("type") or args.get("text_input"))
    subgoal_text = str(args.get("subgoal") or args.get("goal") or "").strip()
    raw_clicks = args.get("clicks") or args.get("buttons") or []
    click_items: list[dict[str, Any]] = []
    if isinstance(raw_clicks, list):
        click_items = [item for item in raw_clicks if isinstance(item, dict)]
    click_spec = _as_dict(args.get("click") or args.get("button") or args.get("press"))
    if not click_spec and not click_items:
        click_spec = _infer_click_spec_from_subgoal(subgoal_text)
    draw_spec = _as_dict(args.get("draw") or args.get("drawing") or {})
    if not draw_spec and args.get("shape"):
        draw_spec = {"shape": args.get("shape")}
    raw_draws = args.get("draws") or args.get("drawings") or []
    draw_specs: list[dict[str, Any]] = []
    if isinstance(raw_draws, list):
        draw_specs = [item for item in (_as_dict(item) for item in raw_draws) if item]
    if not input_spec and args.get("text"):
        input_spec = {"text": args.get("text"), "label": args.get("input_label") or args.get("label") or ""}
    if not click_spec and args.get("button_label"):
        click_spec = {"label": args.get("button_label"), "role": "button"}
    clicks: list[dict[str, Any]] = []
    if click_spec:
        clicks.append(click_spec)
    clicks.extend(click_items)
    verify_texts = _expand_verify_texts(
        _as_text_list(args.get("verify_texts") or args.get("verify") or []),
        task,
    )
    if not verify_texts and input_spec.get("text"):
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
            "verify_texts": verify_texts,
        },
        ensure_ascii=False,
        default=str,
    )
    for target in _task_visible_targets(task):
        if _scope_mentions(target, scope_text):
            verify_texts.append(target)
    return SubgoalRequest(
        subgoal=subgoal_text or str(task or "").strip(),
        target_window=_as_dict(args.get("target_window") or {}),
        input=input_spec,
        click=click_spec,
        clicks=clicks,
        draw=draw_spec,
        draws=draw_specs,
        verify_texts=_unique(verify_texts),
        constraints=_as_dict(args.get("constraints") or {}),
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
    return _unique(matched)


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
    if any(term in raw for term in ("line", "线", "直线")):
        return "line"
    if any(term in raw for term in ("check", "勾", "对号")):
        return "check"
    return "freeform"


def _requested_color(draw_spec: dict[str, Any], subgoal: str, task: str) -> str:
    color_terms = {
        "blue": ("blue", "蓝", "蓝色"),
        "red": ("red", "红", "红色"),
        "green": ("green", "绿", "绿色"),
        "yellow": ("yellow", "黄", "黄色"),
        "black": ("black", "黑", "黑色"),
        "purple": ("purple", "紫", "紫色"),
        "orange": ("orange", "橙", "橙色"),
    }
    explicit = " ".join([
        str(draw_spec.get("color") or ""),
        str(draw_spec.get("colour") or ""),
    ]).casefold()
    if explicit:
        for color, terms in color_terms.items():
            if any(term in explicit for term in terms):
                return color
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


def _shape_strokes(shape: str, canvas: Region) -> list[list[list[int]]]:
    left, top, right, bottom = _inset_region(canvas)
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
        p1 = [cx - half, cy - half]
        p2 = [cx + half, cy - half]
        p3 = [cx + half, cy + half]
        p4 = [cx - half, cy + half]
        return [[p1, p2, p3, p4, p1]]
    if shape == "circle":
        radius = half
        points: list[list[int]] = []
        for i in range(33):
            angle = (math.tau * i) / 32
            points.append([int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle))])
        return [points]
    if shape == "line":
        return [[[left, top], [right, bottom]]]
    if shape == "check":
        return [[[cx - half, cy], [cx - half // 4, cy + half], [cx + half, cy - half]]]
    return [
        [[left, top], [right, bottom]],
        [[right, top], [left, bottom]],
    ]


class SubgoalExecutor:
    """Execute one high-level GUI routine inside the fast controller."""

    def __init__(self, session, controller: FastGuiController) -> None:
        self.session = session
        self.controller = controller

    def _target_hwnd(self, request: SubgoalRequest, allow_foreground: bool = True) -> int | None:
        target = request.target_window or {}
        title = str(target.get("title") or "").strip()
        hwnd = target.get("hwnd")
        try:
            if hwnd:
                return int(hwnd)
        except (TypeError, ValueError):
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
        pure_draw = bool(_draw_specs(request) and not request.input and not request.clicks and not request.verify_texts)
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
            if snapshot is not None and snapshot_hwnd and snapshot_hwnd != int(hwnd):
                try:
                    self.session.snapshot = None
                except Exception:
                    pass
                prelude.append({
                    "action": "discard_stale_snapshot",
                    "ok": True,
                    "preview": f"discarded snapshot hwnd={snapshot_hwnd}; target hwnd={int(hwnd)}",
                })
        if getattr(self.session, "snapshot", None) is None:
            if _draw_specs(request) and not request.input and not request.clicks and not request.verify_texts:
                prelude.append({
                    "action": "draw_direct_canvas_capture",
                    "ok": True,
                    "preview": "pure draw routine skipped initial observe; controller will wait for a ready canvas and locate the likely drawing surface",
                })
                return None, prelude, skip_click_auto_observe
            targeted = self._targeted_initial_snapshot(request)
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
                observe = self.session.dispatch("observe", {})
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

    def _build_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        input_spec = request.input
        if input_spec:
            text = input_spec.get("text", input_spec.get("value", input_spec.get("text_input", "")))
            if text is not None and str(text) != "":
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
        last_color = ""
        for draw_spec in _draw_specs(request):
            draw_action = self._build_draw_action(request, draw_spec)
            if draw_action:
                color = draw_action.get("requested_color")
                if color and color != last_color:
                    actions.append({"action": "select_color", "color": color})
                    last_color = str(color)
                actions.append(draw_action)
        click_specs = request.clicks or ([request.click] if request.click else [])
        submit_file_dialog_with_enter = bool(
            input_spec and click_specs and _file_dialog_enter_submit(request, input_spec, click_specs)
        )
        if submit_file_dialog_with_enter:
            title = ""
            if isinstance(request.target_window, dict):
                title = str(request.target_window.get("title") or "").strip()
            action = {"action": "hotkey", "keys": ["enter"]}
            if title:
                action["expect"] = {"window_gone": title, "timeout_s": 5.0}
            actions.append(action)
            click_specs = []
        changed_query_text = ""
        if not input_spec and click_specs and len(request.verify_texts) == 1:
            changed_query_text = request.verify_texts[0]
        allow_click_interval_wait = not (
            isinstance(request.constraints, dict)
            and request.constraints.get("wait") is False
        )
        for index, click_spec in enumerate(click_specs):
            label = click_spec.get("label") or click_spec.get("text") or click_spec.get("name")
            if label or click_spec.get("anchor"):
                action = self._anchor_fields(click_spec, default_role="button")
                if label and not action.get("label"):
                    action["label"] = str(label)
                action.update({
                    "action": "click_element",
                    "button": click_spec.get("button", "left"),
                    "double": bool(click_spec.get("double", False)),
                })
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
            if changed_query_text:
                actions.append({
                    "action": "observe",
                    "changed_only": True,
                    "query": changed_query_text,
                    "max_elements": 80,
                })
            else:
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

    def _build_draw_action(self, request: SubgoalRequest, draw_spec: dict[str, Any]) -> dict[str, Any] | None:
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
        draw_region = canvas
        hint = str(draw_spec.get("expected_region") or draw_spec.get("region") or "").strip()
        hinted = _rough_region(canvas, hint) if hint else None
        if hinted is not None:
            draw_region = hinted
        shape = _draw_shape_name(draw_spec, request.subgoal, getattr(self.session, "task", ""))
        strokes = draw_spec.get("strokes") if isinstance(draw_spec.get("strokes"), list) else None
        if not strokes:
            strokes = _shape_strokes(shape, draw_region)
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
        color = _requested_color(draw_spec, request.subgoal, getattr(self.session, "task", ""))
        if color:
            action["requested_color"] = color
        return action

    def _draw_color_evidence(self, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        merged: dict[str, Any] = {}
        per_draw: list[dict[str, Any]] = []
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
        draw_specs = _draw_specs(request)
        pure_verify = bool(
            request.verify_texts
            and not request.input
            and not request.click
            and not request.clicks
            and not draw_specs
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
        snapshot, prelude, skip_click_auto_observe = self._ensure_observed(request)
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
        visible_text = _pick_text(batch)
        expect_matched = _verified_expect_texts(actions, batch)
        visible_colors = _visual_colors_from_batch(batch)
        draw_visible_colors, per_draw_color_evidence = self._draw_color_evidence(actions)
        visible_colors = {**visible_colors, **draw_visible_colors}
        requested_colors = _requested_draw_colors(request, getattr(self.session, "task", ""))
        matched_visual_colors = [color for color in requested_colors if color in visible_colors]
        missing_visual_colors = [color for color in requested_colors if color not in visible_colors]
        matched = [
            text for text in request.verify_texts
            if _match_visible_text(text, visible_text)
            or any(_match_visible_text(text, verified) for verified in expect_matched)
        ]
        missing = [text for text in request.verify_texts if text not in matched]
        verified = (
            bool(batch.get("ok"))
            and not missing
            and not missing_visual_colors
        ) if (request.verify_texts or requested_colors) else bool(batch.get("verified"))
        recovery_candidates = _visible_region_text_candidates(snapshot, request)
        evidence_packet = {
            "matched_texts": matched,
            "missing_texts": missing,
            "verified_expect_texts": expect_matched,
            "visible_colors": visible_colors,
            "per_draw_color_evidence": per_draw_color_evidence,
            "matched_visual_colors": matched_visual_colors,
            "missing_visual_colors": missing_visual_colors,
            "candidate_click_labels": recovery_candidates,
            "visible_text_preview": visible_text[:1200],
            "perception_reducer": reduction,
            "prelude": prelude,
        }
        payload = {
            "ok": bool(batch.get("ok")) and not missing and not missing_visual_colors,
            "verified": bool(verified),
            "subgoal": request.subgoal,
            "subgoal_route": "controller_first",
            "controller_state": {
                **(batch.get("controller_state") or {}),
                "needs_llm_reason": bool(missing or missing_visual_colors or not batch.get("ok")),
            },
            "actions_executed": actions,
            "results": _public_batch_results(batch.get("results") or []),
            "metrics": batch.get("metrics") or {},
            "evidence_packet": evidence_packet,
            "needs_llm_reason": bool(missing or missing_visual_colors or not batch.get("ok")),
        }
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
            if recovery_candidates:
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
            payload["error"] = (
                "subgoal routine drew visible pixels but required color evidence is missing: "
                + ", ".join(missing_visual_colors)
            )
            payload["blocked"] = True
            payload["fast_path_advice"] = (
                "The drawing changed pixels but auto_visual_verify did not see the requested color. "
                "Retry execute_subgoal with the same draw.color so the controller can select a palette "
                "swatch before drawing, or use a visible color control and then draw again."
            )
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={"tool": "execute_subgoal", "subgoal": request.subgoal, "actions": actions},
        )
