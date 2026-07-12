"""Compact perception summaries for the LLM hot path.

The raw Snapshot remains the source of truth for traces and element lookup. This
module only ranks a small set of likely useful candidates and advisory facts so
text-only models do not have to read hundreds of OCR/UIA/CV rows every turn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..utils.screenshot import Region
from .fusion import Snapshot
from .model import UIElement

_ACTION_WORDS = {
    "click", "press", "tap", "open", "choose", "select", "apply", "submit", "send",
    "search", "find", "type", "input", "enter", "fill", "write", "verify", "visible",
    "点击", "按", "打开", "选择", "应用", "提交", "发送", "搜索", "查找", "输入", "填写", "写入", "验证", "可见", "显示",
}
_IMPORTANT_ROLES = (
    "button", "edit", "combobox", "textbox", "search", "input", "link",
    "menu", "menuitem", "tab", "listitem", "checkbox", "radio",
    "按钮", "编辑", "组合框", "菜单", "选项卡", "复选", "单选",
)
_INPUT_ROLES = ("edit", "combobox", "textbox", "search", "input", "编辑", "组合框")
_BUTTON_ROLES = ("button", "submit", "link", "menu", "按钮", "链接", "菜单")
_LITERAL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-]{3,80}\b|\b\d{4,18}\b")
_CHAT_TASK_RE = re.compile(
    r"\b(?:chat|message|reply|send)\b|"
    r"\u804a\u5929|\u53d1\u6d88\u606f|\u53d1\u4fe1\u606f|\u56de\u590d|"
    r"\u79c1\u4fe1|\u6211\u7684\u624b\u673a|\bqq\b|\bwechat\b",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r'"([^"]{1,120})"|“([^”]{1,120})”|「([^」]{1,120})」')


@dataclass(frozen=True)
class CandidateScore:
    element: UIElement
    score: float
    why: list[str]


def _norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _role_contains(element: UIElement, terms: Iterable[str]) -> bool:
    role = _norm(element.role)
    return any(term in role for term in terms)


def _region_dict(region: Region | None) -> dict[str, int] | None:
    if region is None:
        return None
    return {
        "left": int(region.left),
        "top": int(region.top),
        "width": int(region.width),
        "height": int(region.height),
    }


def _extract_terms(task: str = "", query: str = "") -> list[str]:
    terms: list[str] = []
    for source in (query, task):
        for groups in _QUOTED_RE.findall(source or ""):
            value = next((group for group in groups if group), "").strip()
            if value:
                terms.append(value)
        for token in _LITERAL_RE.findall(source or ""):
            if token.casefold() not in {"http", "https", "shell", "gui", "windows"}:
                terms.append(token)
        for word in re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\-\u4e00-\u9fff]{1,24}", source or ""):
            low = word.casefold()
            if low in _ACTION_WORDS:
                continue
            if len(word) >= 2:
                terms.append(word)
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = _norm(term)
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique[:16]


def _stage(snapshot: Snapshot, elements: list[UIElement] | None = None) -> str:
    title = _norm(snapshot.title)
    scoped = elements if elements is not None else snapshot.scoped_elements()
    roles = " ".join(_norm(e.role) for e in scoped[:200])
    readable = sum(1 for e in scoped if _norm(e.text))
    if not title or "program manager" in title or "desktop" in title or "workerw" in title:
        return "desktop_or_shell"
    if any(term in roles for term in ("dialog", "window", "pane", "对话")) and any(
        _role_contains(e, _BUTTON_ROLES) for e in scoped
    ):
        return "dialog_or_window"
    if any(_role_contains(e, _INPUT_ROLES) for e in scoped) and any(
        _role_contains(e, _BUTTON_ROLES) for e in scoped
    ):
        return "form_or_search_surface"
    if readable < 3 and snapshot.region.width * snapshot.region.height > 200_000:
        return "custom_canvas_or_uia_off_surface"
    return "standard_window"


def _chat_context(
    snapshot: Snapshot,
    task: str,
    elements: list[UIElement] | None = None,
) -> dict[str, Any] | None:
    """Detect an open split-pane conversation using generic geometry."""
    if not _CHAT_TASK_RE.search(task or ""):
        return None
    window = snapshot.region
    task_norm = re.sub(r"\s+", "", _norm(task))
    by_text: dict[str, list[UIElement]] = {}
    for element in (elements if elements is not None else snapshot.scoped_elements()):
        text = re.sub(r"\s+", "", _norm(element.text))
        if len(text) < 2 or int(element.width or 0) <= 0:
            continue
        by_text.setdefault(text, []).append(element)
    target = ""
    for text, elements in by_text.items():
        if text not in task_norm:
            continue
        has_list_row = any(
            int(element.cx) <= window.left + int(window.width * 0.17)
            and int(element.cy) >= window.top + int(window.height * 0.10)
            for element in elements
        )
        has_header = any(
            int(element.cx) >= window.left + int(window.width * 0.15)
            and int(element.cy) <= window.top + int(window.height * 0.16)
            for element in elements
        )
        if has_list_row and has_header:
            target = text
            break
    if not target:
        return None
    left = window.left + max(12, int(window.width * 0.18))
    top = window.top + max(80, int(window.height * 0.82))
    right_margin = max(12, int(window.width * 0.02))
    bottom_margin = max(12, int(window.height * 0.025))
    width = max(160, window.right - right_margin - left)
    height = max(64, window.bottom - bottom_margin - top)
    return {
        "target": target,
        "bbox": [left, top, width, height],
        "center": [left + width // 2, top + height // 2],
    }


def _non_blocking_notice_context(
    snapshot: Snapshot,
    elements: list[UIElement],
) -> dict[str, Any] | None:
    """Recognize a notice banner above an already editable work surface."""
    title = _norm(snapshot.title)
    text = "\n".join(str(element.text or "") for element in elements if element.text)
    spreadsheet_surface = bool(
        re.search(r"workbook|spreadsheet|worksheet|工作簿|工作表|表格", title, re.IGNORECASE)
        and any(
            re.fullmatch(r"[A-Z]{1,3}\d+", str(element.text or "").strip(), re.IGNORECASE)
            for element in elements
        )
    )
    document_surface = bool(
        re.search(
            r"\b(?:document|writer|word)\b|文字文稿|文档|\.docx?\b",
            title,
            re.IGNORECASE,
        )
        and (
            any(
                str(getattr(element, "role", "") or "").casefold() in {"window", "pane"}
                for element in elements
            )
            or (
                bool(re.search(r"(?:^|\n)(?:开始|home)(?:\n|$)", text, re.IGNORECASE))
                and bool(re.search(r"(?:^|\n)(?:插入|insert)(?:\n|$)", text, re.IGNORECASE))
                and bool(re.search(r"页面\s*[:：]?\s*\d|字数\s*[:：]?\s*\d|page\s*[:：]?\s*\d", text, re.IGNORECASE))
            )
        )
    )
    notice_patterns = (
        r"previously did not close normally|recovered to the latest|recovery report|"
        "\u4e0a\u4e00\u6b21\u672a\u6b63\u5e38\u5173\u95ed|"
        "\u5df2\u6062\u590d\u5230\u6700\u65b0\u7248\u672c|"
        "\u53d1\u9001\u62a5\u544a|\u6570\u636e\u6062\u590d|"
        "\u6b63\u5728\u65b0\u5efa\u6587\u6863|creating document"
    )
    notice = bool(re.search(notice_patterns, text, re.IGNORECASE))
    if not ((spreadsheet_surface or document_surface) and notice):
        return None
    if document_surface:
        surface = "document_editor"
        guidance = (
            "The Writer document editor is already available. The creating-document/recovery text is "
            "a stale non-modal UIA notice; do not wait for it to disappear, click it, press Escape, or "
            "re-plan around it. If the task requires a brand-new blank document, invoke New/Ctrl+N now; "
            "otherwise continue the requested document input, formatting, or Save As operation."
        )
    else:
        surface = "spreadsheet_grid"
        guidance = (
            "The spreadsheet grid and A1/name box are already editable. The recovery/notification "
            "banner is non-modal; do not click, dismiss, wait for, or re-plan around it. Continue the "
            "requested cell input, formatting, or Save As operation."
        )
    return {
        "kind": "non_blocking_notice",
        "surface": surface,
        "blocking": False,
        "suppressed_texts": [
            "\u6b63\u5728\u65b0\u5efa\u6587\u6863",
            "\u4e0a\u4e00\u6b21\u672a\u6b63\u5e38\u5173\u95ed",
            "\u5df2\u6062\u590d\u5230\u6700\u65b0\u7248\u672c",
            "\u7acb\u5373\u6062\u590d",
            "\u53d1\u9001\u62a5\u544a",
            "creating document",
            "recovery report",
        ],
        "guidance": guidance,
    }


def _score_element(element: UIElement, terms: list[str], changed_regions: list[dict[str, Any]]) -> CandidateScore:
    score = 0.0
    why: list[str] = []
    text = _norm(element.text)
    role = _norm(element.role)
    source = _norm(element.source)
    file_item_name = bool(
        _role_contains(element, ("edit", "textbox"))
        and source == "uia"
        and text.startswith(("name=", "名称="))
        and int(element.width or 0) < 420
    )
    if _role_contains(element, _INPUT_ROLES) and not file_item_name:
        score += 0.72
        why.append("input-like role")
    elif _role_contains(element, _BUTTON_ROLES):
        score += 0.58
        why.append("actionable role")
    elif text:
        score += 0.28
        why.append("readable text")
    elif source == "cv":
        score += 0.18
        why.append("visual candidate")
    if "focused" in {_norm(s) for s in element.states}:
        score += 0.18
        why.append("focused")
    if source == "uia":
        score += 0.06
        why.append("structured")
    if source == "ocr":
        score += 0.04
        why.append("ocr")
    if file_item_name:
        score -= 0.34
        why.append("file-grid item label, not an input field")
    if (
        _role_contains(element, ("combobox", "edit", "textbox"))
        and int(element.width or 0) >= 700
        and ("*." in text or text.endswith((".txt", ".docx", ".xlsx", ".pptx")))
    ):
        score += 0.42
        why.append("wide file-dialog input")
    for term in terms:
        term_norm = _norm(term)
        if not term_norm:
            continue
        if text == term_norm:
            score += 0.50
            why.append(f'exact text match "{term}"')
            break
        if term_norm in text or text in term_norm and text:
            score += 0.34
            why.append(f'text related to "{term}"')
            break
    for region in changed_regions[:3]:
        try:
            left = int(region.get("left", 0))
            top = int(region.get("top", 0))
            width = int(region.get("width", 0))
            height = int(region.get("height", 0))
        except (TypeError, ValueError):
            continue
        if left <= element.cx <= left + width and top <= element.cy <= top + height:
            score += 0.16
            why.append("inside recent changed ROI")
            break
    # Very small OCR fragments like a caret rendered as I are useful for trace,
    # but should not dominate the default LLM view.
    if element.source == "ocr" and text in {"i", "|", "l"} and element.height > element.width * 2:
        score -= 0.30
        why.append("caret-like OCR fragment")
    return CandidateScore(element=element, score=score, why=why)


def reduce_snapshot(
    snapshot: Snapshot,
    task: str = "",
    query: str = "",
    recent_changes: dict[str, Any] | None = None,
    max_candidates: int = 12,
) -> dict[str, Any]:
    """Return a small, source-attributed summary of a full Snapshot."""
    terms = _extract_terms(task=task, query=query)
    changed_regions = []
    if isinstance(recent_changes, dict):
        changed_regions = list(
            recent_changes.get("action_changed_regions")
            or recent_changes.get("changed_regions")
            or recent_changes.get("recent_changed_regions")
            or []
        )
    scoped_elements = snapshot.scoped_elements()
    filtered_count = max(0, len(snapshot.elements) - len(scoped_elements))
    chat_context = _chat_context(snapshot, task, scoped_elements)
    non_blocking_notice = _non_blocking_notice_context(snapshot, scoped_elements)
    scored = [_score_element(element, terms, changed_regions) for element in scoped_elements]
    scored.sort(key=lambda item: item.score, reverse=True)
    candidates: list[dict[str, Any]] = []
    seen_boxes: set[tuple[int, int, int, int, str]] = set()
    for item in scored:
        element = item.element
        if item.score <= 0.0:
            continue
        key = (int(element.left), int(element.top), int(element.width), int(element.height), _norm(element.text))
        if key in seen_boxes:
            continue
        seen_boxes.add(key)
        candidates.append({
            "elem_id": int(element.elem_id),
            "role": element.role or "elem",
            "label": element.text or "",
            "bbox": [int(element.left), int(element.top), int(element.width), int(element.height)],
            "center": [int(element.cx), int(element.cy)],
            "confidence": round(float(item.score), 3),
            "source": element.source or "unknown",
            "why_relevant": "; ".join(dict.fromkeys(item.why))[:180],
        })
        if len(candidates) >= max(1, int(max_candidates)):
            break
    if chat_context is not None:
        candidates.insert(0, {
            "elem_id": None,
            "role": "synthetic_chat_composer",
            "label": "",
            "bbox": chat_context["bbox"],
            "center": chat_context["center"],
            "confidence": 0.92,
            "source": "geometry",
            "why_relevant": (
                "target conversation header is open; lower-right custom composer inferred"
            ),
        })
        candidates = candidates[:max(1, int(max_candidates))]
    readable = sum(1 for e in scoped_elements if _norm(e.text))
    input_count = sum(1 for e in scoped_elements if _role_contains(e, _INPUT_ROLES))
    if chat_context is not None:
        input_count += 1
    button_count = sum(1 for e in scoped_elements if _role_contains(e, _BUTTON_ROLES))
    uncertainties: list[str] = []
    if not readable:
        uncertainties.append("no readable text from UIA/OCR; likely custom-rendered or visually sparse")
    if filtered_count:
        uncertainties.append(f"{filtered_count} off-window/stale elements excluded from the model view")
    if len(scoped_elements) > max_candidates:
        uncertainties.append(f"{len(scoped_elements) - len(candidates)} lower-ranked in-window elements hidden from default view")
    if query and not any(_norm(query) in _norm(e.text) for e in scoped_elements):
        uncertainties.append(f'query "{query}" not found in visible text')
    if changed_regions:
        uncertainties.append("summary is biased toward the most recent changed ROI")
    recommendations: list[str] = []
    task_norm = _norm(task)
    if chat_context is not None:
        recommendations.append(
            "Target conversation is already open; do not click the contact row again. "
            "Use execute_subgoal/type_into_element with expected_region=bottom-right, "
            "send with Enter, then verify a new message bubble."
        )
    if non_blocking_notice is not None:
        recommendations.insert(0, str(non_blocking_notice["guidance"]))
    if input_count and any(word in task_norm for word in ("type", "input", "fill", "search", "输入", "填写", "搜索")):
        recommendations.append("Use execute_subgoal with an input.text payload or run_action_batch/type_into_element for the input routine.")
    elif input_count:
        recommendations.append("Input-like candidate exists; prefer type_into_element over separate click/type/observe loops.")
    if button_count:
        recommendations.append("Actionable candidate exists; prefer click_element with label/role rather than raw coordinates when text is stable.")
    if changed_regions:
        recommendations.append("Use observe(changed_only=true) or verify_texts against the changed ROI before a full-screen re-observe.")
    return {
        "screen_stage": (
            "chat_conversation"
            if chat_context is not None
            else (
                (
                    "document_editor_with_nonblocking_notice"
                    if non_blocking_notice is not None
                    and non_blocking_notice.get("surface") == "document_editor"
                    else "spreadsheet_grid_with_nonblocking_notice"
                )
                if non_blocking_notice is not None
                else _stage(snapshot, scoped_elements)
            )
        ),
        "target_window": {
            "title": snapshot.title,
            "hwnd": int(snapshot.hwnd),
            "rect": _region_dict(snapshot.region),
        },
        "stats": {
            "total_elements": len(scoped_elements),
            "raw_total_elements": len(snapshot.elements),
            "off_window_filtered": filtered_count,
            "readable_elements": readable,
            "input_like": input_count,
            "actionable": button_count,
            "dimensions": snapshot.dim_stats,
        },
        "top_candidates": candidates,
        "uncertainties": uncertainties[:6],
        "recent_changes": {
            "running": bool((recent_changes or {}).get("running")),
            "last_changed": bool((recent_changes or {}).get("last_changed")),
            "changed_regions": changed_regions[:3],
            "last_change_age_ms": (recent_changes or {}).get("last_change_age_ms"),
            "last_action_change_age_ms": (recent_changes or {}).get("last_action_change_age_ms"),
        },
        "recommended_next_controller_actions": recommendations[:4],
        "chat_context": chat_context or {},
        "non_blocking_notice": non_blocking_notice or {},
    }


def render_reduced_snapshot(
    snapshot: Snapshot,
    task: str = "",
    query: str = "",
    recent_changes: dict[str, Any] | None = None,
    max_candidates: int = 12,
) -> str:
    reduced = reduce_snapshot(
        snapshot,
        task=task,
        query=query,
        recent_changes=recent_changes,
        max_candidates=max_candidates,
    )
    lines = [
        "Perception reducer:",
        f"screen_stage={reduced['screen_stage']}",
        (
            "target_window="
            f'"{reduced["target_window"]["title"]}" '
            f"hwnd={reduced['target_window']['hwnd']} "
            f"rect={reduced['target_window']['rect']}"
        ),
        (
            "stats="
            f"total:{reduced['stats']['total_elements']} "
            f"readable:{reduced['stats']['readable_elements']} "
            f"input_like:{reduced['stats']['input_like']} "
            f"actionable:{reduced['stats']['actionable']}"
        ),
    ]
    if reduced["top_candidates"]:
        lines.append("top_candidates:")
        for cand in reduced["top_candidates"]:
            label = cand["label"] if len(cand["label"]) <= 70 else cand["label"][:67] + "..."
            elem_id = cand["elem_id"] if cand["elem_id"] is not None else "synthetic"
            lines.append(
                f"- id={elem_id} role={cand['role']} text=\"{label}\" "
                f"center=({cand['center'][0]},{cand['center'][1]}) "
                f"bbox=({cand['bbox'][0]},{cand['bbox'][1]},{cand['bbox'][2]},{cand['bbox'][3]}) "
                f"score={cand['confidence']} src={cand['source']} why={cand['why_relevant']}"
            )
    if reduced["uncertainties"]:
        lines.append("uncertainties: " + " | ".join(reduced["uncertainties"]))
    recent = reduced["recent_changes"]
    if recent.get("changed_regions"):
        first = recent["changed_regions"][0]
        lines.append(
            "recent_changes="
            f"changed={recent.get('last_changed')} "
            f"roi=({first.get('left')},{first.get('top')},{first.get('width')}x{first.get('height')}) "
            f"age_ms={recent.get('last_change_age_ms')} "
            f"action_age_ms={recent.get('last_action_change_age_ms')}"
        )
    if reduced["recommended_next_controller_actions"]:
        lines.append("recommended_next_controller_actions:")
        lines.extend(f"- {item}" for item in reduced["recommended_next_controller_actions"])
    return "\n".join(lines)
