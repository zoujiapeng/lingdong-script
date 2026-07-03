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


def _stage(snapshot: Snapshot) -> str:
    title = _norm(snapshot.title)
    roles = " ".join(_norm(e.role) for e in snapshot.elements[:200])
    readable = sum(1 for e in snapshot.elements if _norm(e.text))
    if not title or "program manager" in title or "desktop" in title or "workerw" in title:
        return "desktop_or_shell"
    if any(term in roles for term in ("dialog", "window", "pane", "对话")) and any(
        _role_contains(e, _BUTTON_ROLES) for e in snapshot.elements
    ):
        return "dialog_or_window"
    if any(_role_contains(e, _INPUT_ROLES) for e in snapshot.elements) and any(
        _role_contains(e, _BUTTON_ROLES) for e in snapshot.elements
    ):
        return "form_or_search_surface"
    if readable < 3 and snapshot.region.width * snapshot.region.height > 200_000:
        return "custom_canvas_or_uia_off_surface"
    return "standard_window"


def _score_element(element: UIElement, terms: list[str], changed_regions: list[dict[str, Any]]) -> CandidateScore:
    score = 0.0
    why: list[str] = []
    text = _norm(element.text)
    role = _norm(element.role)
    source = _norm(element.source)
    if _role_contains(element, _INPUT_ROLES):
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
    scored = [_score_element(element, terms, changed_regions) for element in snapshot.elements]
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
    readable = sum(1 for e in snapshot.elements if _norm(e.text))
    input_count = sum(1 for e in snapshot.elements if _role_contains(e, _INPUT_ROLES))
    button_count = sum(1 for e in snapshot.elements if _role_contains(e, _BUTTON_ROLES))
    uncertainties: list[str] = []
    if not readable:
        uncertainties.append("no readable text from UIA/OCR; likely custom-rendered or visually sparse")
    if len(snapshot.elements) > max_candidates:
        uncertainties.append(f"{len(snapshot.elements) - len(candidates)} lower-ranked elements hidden from default view")
    if query and not any(_norm(query) in _norm(e.text) for e in snapshot.elements):
        uncertainties.append(f'query "{query}" not found in visible text')
    if changed_regions:
        uncertainties.append("summary is biased toward the most recent changed ROI")
    recommendations: list[str] = []
    task_norm = _norm(task)
    if input_count and any(word in task_norm for word in ("type", "input", "fill", "search", "输入", "填写", "搜索")):
        recommendations.append("Use execute_subgoal with an input.text payload or run_action_batch/type_into_element for the input routine.")
    elif input_count:
        recommendations.append("Input-like candidate exists; prefer type_into_element over separate click/type/observe loops.")
    if button_count:
        recommendations.append("Actionable candidate exists; prefer click_element with label/role rather than raw coordinates when text is stable.")
    if changed_regions:
        recommendations.append("Use observe(changed_only=true) or verify_texts against the changed ROI before a full-screen re-observe.")
    return {
        "screen_stage": _stage(snapshot),
        "target_window": {
            "title": snapshot.title,
            "hwnd": int(snapshot.hwnd),
            "rect": _region_dict(snapshot.region),
        },
        "stats": {
            "total_elements": len(snapshot.elements),
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
            lines.append(
                f"- id={cand['elem_id']} role={cand['role']} text=\"{label}\" "
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
