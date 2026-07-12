"""Generic input-field locator for UIA-off and custom-rendered surfaces.

The locator deliberately avoids app-specific rules. It combines UIA roles,
OCR labels, CV/icon context, rough geometry, focused-state hints, and fallback
synthetic click boxes near labels/placeholders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..perception.fusion import Snapshot
from ..perception.model import UIElement
from ..utils.screenshot import Region

_INPUT_ROLE_TERMS = (
    "edit", "combobox", "textbox", "text box", "search", "input", "textarea",
    "编辑", "组合框", "输入", "搜索框",
)
_BUTTON_ROLE_TERMS = ("button", "link", "menu", "按钮", "链接", "菜单")
_PLACEHOLDER_TERMS = (
    "search", "find", "input", "type", "enter", "message", "comment", "query",
    "\u641c\u7d22", "\u67e5\u627e", "\u8f93\u5165", "\u8bf7\u8f93\u5165",
    "\u6d88\u606f", "\u8bc4\u8bba",
    "搜索", "查找", "输入", "请输入", "消息", "评论", "搜索框",
)
_CARET_TEXTS = {"i", "l", "|", "丨", "︱"}
_SEARCH_ICON_TEXTS = {"q", "o", "0", "🔍"}
_SEARCH_SEMANTIC_TERMS = (
    "search", "find", "query",
    "搜索",
    "搜素",  # common OCR substitution/transposition
    "检索",
    "查找",
)
_CHAT_TASK_RE = re.compile(
    r"\b(?:chat|message|reply|send)\b|"
    r"\u804a\u5929|\u53d1\u6d88\u606f|\u53d1\u4fe1\u606f|\u56de\u590d|"
    r"\u79c1\u4fe1|\u6211\u7684\u624b\u673a|\bqq\b|\bwechat\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InputCandidate:
    element: UIElement | None
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    score: float
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "elem_id": self.element.elem_id if self.element is not None else None,
            "text": self.element.text if self.element is not None else "",
            "role": self.element.role if self.element is not None else "edit",
            "source": self.source,
            "bbox": [int(v) for v in self.bbox],
            "center": [int(v) for v in self.center],
            "score": round(float(self.score), 3),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class InputLocateResult:
    found: bool
    candidate: InputCandidate | None = None
    score: float = 0.0
    reason: str = ""
    candidates: tuple[InputCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "score": round(float(self.score), 3),
            "reason": self.reason,
            "candidate": self.candidate.to_dict() if self.candidate is not None else None,
            "candidates": [item.to_dict() for item in self.candidates[:5]],
        }


def _norm(text: object) -> str:
    return re.sub(r"[\s_\-:：]+", "", str(text or "")).strip().casefold()


def _role_has(element: UIElement, terms: Iterable[str]) -> bool:
    role = str(element.role or "").casefold()
    return any(term in role for term in terms)


def _is_input_role(element: UIElement) -> bool:
    return _role_has(element, _INPUT_ROLE_TERMS)


def _is_button_role(element: UIElement) -> bool:
    return _role_has(element, _BUTTON_ROLE_TERMS)


def _is_compact_property_editor(element: UIElement, window: Region) -> bool:
    """Detect inline list/detail editors that are not free-form inputs.

    Windows file views expose item metadata such as ``Name=folder`` as UIA
    Edit controls. Treating the first such control as a text box can rename
    user data. A compact key/value editor is therefore unsafe without an exact
    label or explicit focused state.
    """
    text = str(element.text or "").strip()
    if not text or not re.match(r"^[^=\r\n]{1,32}=.+", text):
        return False
    states = {_norm(state) for state in element.states}
    if "focused" in states:
        return False
    return int(element.width or 0) < int(window.width * 0.45)


def _is_caret_like(element: UIElement) -> bool:
    text = _norm(element.text)
    return (
        element.source == "ocr"
        and text in _CARET_TEXTS
        and int(element.height or 0) >= max(12, int(element.width or 1) * 2)
    )


def _looks_like_field_label(text: object) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    label_terms = {
        "inputbox",
        "textbox",
        "searchbox",
        "editbox",
        "\u8f93\u5165\u6846",
        "\u641c\u7d22\u6846",
        "\u6587\u672c\u6846",
        "\u76ee\u6807\u8f93\u5165\u6846",
    }
    return any(term in normalized for term in label_terms)


def _looks_search_semantic(text: object) -> bool:
    normalized = _norm(text)
    if not normalized:
        return False
    # Magnifiers in owner-drawn fields are commonly OCR'd as Q/O/0 and merged
    # into the placeholder token (for example "Q 搜素").
    if normalized[:1] in _SEARCH_ICON_TEXTS and len(normalized) > 1:
        normalized = normalized[1:]
    return any(_norm(term) in normalized for term in _SEARCH_SEMANTIC_TERMS)


def _wants_search(terms: Iterable[str]) -> bool:
    return any(_looks_search_semantic(term) for term in terms)


def _wants_browser_location(terms: Iterable[str]) -> bool:
    markers = (
        "address bar", "address and search bar", "location bar", "omnibox",
        "url bar", "地址栏", "地址和搜索栏", "网址栏",
    )
    normalized = [_norm(term) for term in terms]
    return any(
        value and any(_norm(marker) in value or value in _norm(marker) for marker in markers)
        for value in normalized
    )


def _wants_filename(terms: Iterable[str]) -> bool:
    concrete = {
        "filename", "file name", "filepath", "file path", "path",
        "文件名", "文件名称", "文件路径", "路径",
    }
    normalized = [_norm(term) for term in terms]
    return any(
        value and any(_norm(item) in value or value in _norm(item) for item in concrete)
        for value in normalized
    )


def _looks_like_filename_value(text: object) -> bool:
    """Recognize field values, while excluding file-type descriptions."""
    value = str(text or "").strip()
    if not value or len(value) > 260 or "\n" in value:
        return False
    if re.fullmatch(r"\*\.[A-Za-z0-9*?_-]{1,16}", value):
        return True
    if any(char in value for char in "()（）") or any(char.isspace() for char in value):
        return False
    leaf = value.replace("/", "\\").rsplit("\\", 1)[-1]
    return bool(re.fullmatch(r"[^<>:\"/\\|?*]{1,220}\.[A-Za-z0-9_-]{1,16}", leaf))


def _terms(label: str = "", fallback_text: Iterable[str] = ()) -> list[str]:
    out: list[str] = []
    for raw in (label, *fallback_text):
        value = str(raw or "").strip()
        if value:
            out.append(value)
    seen: set[str] = set()
    unique: list[str] = []
    for value in out:
        key = _norm(value)
        if key and key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _requires_concrete_label_match(terms: list[str]) -> bool:
    """Some labels are concrete field identities, not soft semantic hints."""
    if not terms:
        return False
    concrete = {
        "filename", "file name", "filepath", "file path", "path",
        "文件名", "文件名称", "文件路径", "路径",
        "保存位置", "另存为", "saveas", "save as",
    }
    for term in terms:
        if _looks_like_field_label(term):
            return True
        key = _norm(term)
        if any(_norm(item) in key or key in _norm(item) for item in concrete):
            return True
    return False


def _looks_like_browser_location_field(element: UIElement, window: Region) -> bool:
    text = str(element.text or "").casefold()
    normalized = _norm(text)
    markers = (
        "addressandsearchbar",
        "addressbar",
        "地址和搜索栏",
        "地址栏",
    )
    urlish = "http://" in text or "https://" in text or "www." in text
    top_wide = (
        int(element.cy or 0) <= window.top + max(90, int(window.height * 0.14))
        and int(element.width or 0) >= int(window.width * 0.45)
    )
    return top_wide and (urlish or any(_norm(marker) in normalized for marker in markers))


def _text_score(text: str, terms: list[str]) -> tuple[float, str]:
    text_norm = _norm(text)
    if not text_norm or not terms:
        return 0.0, ""
    if _looks_search_semantic(text) and _wants_search(terms):
        return 0.28, "search semantic OCR match"
    for term in terms:
        term_norm = _norm(term)
        if text_norm == term_norm:
            return 0.34, f'exact label/text match "{term}"'
        if term_norm in text_norm:
            return 0.25, f'contains label "{term}"'
        if text_norm in term_norm and len(text_norm) >= 2:
            return 0.16, f'text inside requested label "{term}"'
    return 0.0, ""


def _region_match(element: UIElement | InputCandidate, window: Region, expected: str) -> float:
    expected = (expected or "").casefold().replace("_", "-").replace(" ", "-")
    if not expected:
        return 0.0
    if isinstance(element, InputCandidate):
        x, y = element.center
    else:
        x, y = element.cx, element.cy
    rel_x = (x - window.left) / max(1, window.width)
    rel_y = (y - window.top) / max(1, window.height)
    ok = True
    if expected in {"top", "upper"}:
        ok = rel_y <= 0.38
    elif expected in {"bottom", "lower"}:
        ok = rel_y >= 0.62
    elif expected == "left":
        ok = rel_x <= 0.42
    elif expected == "right":
        ok = rel_x >= 0.58
    elif expected in {"top-right", "upper-right"}:
        ok = rel_x >= 0.50 and rel_y <= 0.45
    elif expected in {"top-left", "upper-left"}:
        ok = rel_x <= 0.50 and rel_y <= 0.45
    elif expected in {"bottom-right", "lower-right"}:
        ok = rel_x >= 0.50 and rel_y >= 0.55
    elif expected in {"bottom-left", "lower-left"}:
        ok = rel_x <= 0.50 and rel_y >= 0.55
    elif expected in {"center", "middle"}:
        ok = 0.20 <= rel_x <= 0.80 and 0.20 <= rel_y <= 0.80
    return 0.08 if ok else -0.22


def _candidate_from_element(element: UIElement, score: float, reason: list[str]) -> InputCandidate:
    return InputCandidate(
        element=element,
        bbox=(int(element.left), int(element.top), int(element.width), int(element.height)),
        center=(int(element.cx), int(element.cy)),
        score=score,
        source=element.source or "element",
        reason="; ".join(dict.fromkeys(reason)),
    )


def _rect(element: UIElement | InputCandidate) -> tuple[int, int, int, int]:
    if isinstance(element, InputCandidate):
        left, top, width, height = element.bbox
        return int(left), int(top), int(left + width), int(top + height)
    return int(element.left), int(element.top), int(element.left + element.width), int(element.top + element.height)


def _vertical_overlap(a: UIElement, b: UIElement) -> float:
    _ax1, ay1, _ax2, ay2 = _rect(a)
    _bx1, by1, _bx2, by2 = _rect(b)
    overlap = max(0, min(ay2, by2) - max(ay1, by1))
    return overlap / max(1, min(int(a.height or 0), int(b.height or 0)))


def _label_elements(snapshot: Snapshot, label_terms: list[str]) -> list[UIElement]:
    if not label_terms:
        return []
    labels: list[UIElement] = []
    for element in snapshot.elements:
        text = str(element.text or "").strip()
        text_norm = _norm(text)
        if not text_norm or _is_caret_like(element):
            continue
        for term in label_terms:
            term_norm = _norm(term)
            if term_norm and (term_norm in text_norm or text_norm in term_norm):
                labels.append(element)
                break
    return labels


def _input_candidates_near_labels(snapshot: Snapshot, label_terms: list[str]) -> list[InputCandidate]:
    """Promote real input controls that sit on the same row as a matched label."""
    labels = _label_elements(snapshot, label_terms)
    if not labels:
        return []
    candidates: list[InputCandidate] = []
    for label in labels:
        label_right = int(label.left + label.width)
        label_cy = int(label.cy)
        for element in snapshot.elements:
            if element is label or _is_caret_like(element) or not _is_input_role(element):
                continue
            if int(element.width or 0) <= 0 or int(element.height or 0) <= 0:
                continue
            same_row = _vertical_overlap(label, element) >= 0.35 or abs(int(element.cy) - label_cy) <= max(18, int(label.height or 0), int(element.height or 0))
            to_right = int(element.left) >= label_right - 12
            spans_label_row = int(element.left) <= label_right + 80 and int(element.left + element.width) > label_right + 80
            if not same_row or not (to_right or spans_label_row):
                continue
            gap = max(0, int(element.left) - label_right)
            score = 0.96
            if element.source in {"win32", "uia"}:
                score += 0.10
            if gap <= 80:
                score += 0.08
            if int(element.width or 0) >= max(120, int(label.width or 0)):
                score += 0.04
            candidates.append(_candidate_from_element(
                element,
                score,
                [f'input-like control near concrete label "{label.text}"'],
            ))
    return candidates


def _nearby_synthetic_candidates(snapshot: Snapshot, label_terms: list[str]) -> list[InputCandidate]:
    if not label_terms:
        return []
    candidates: list[InputCandidate] = []
    window = snapshot.region
    for element in snapshot.elements:
        text = str(element.text or "").strip()
        text_norm = _norm(text)
        if not text_norm or _is_caret_like(element):
            continue
        matched = False
        score = 0.0
        reason = ""
        for term in label_terms:
            term_norm = _norm(term)
            if term_norm and (term_norm in text_norm or text_norm in term_norm):
                matched = True
                score = 0.58 if term_norm in text_norm else 0.48
                reason = f'ocr/uia label near requested input "{term}"'
                break
        # Search icons are often OCR'd as Q/O. They are weak but useful when the
        # requested label itself says search/query.
        if not matched:
            wants_search = _wants_search(label_terms)
            if wants_search and (
                text_norm in _SEARCH_ICON_TEXTS
                or _looks_search_semantic(text)
            ):
                matched = True
                score = 0.52
                reason = "possible search icon/placeholder beside input"
        if not matched:
            continue
        h = max(26, min(54, int(element.height * 1.8) if element.height else 32))
        w = max(140, min(760, int(window.width * 0.52)))
        gap = max(8, min(20, int(element.width * 0.18) if element.width else 10))
        if _looks_like_field_label(text):
            # Field labels such as "input box" usually sit above the real
            # editable rectangle; clicking to the right often hits empty chrome.
            left = min(max(window.left + 8, int(element.left)), window.right - 80)
            top = min(max(window.top, int(element.top + element.height + 8)), window.bottom - h)
            width = min(w, max(80, window.right - left - 8))
            reason += "; field-label below"
        else:
            # Prefer a box to the right of the label/icon, clipped inside window.
            left = min(max(window.left, int(element.left + element.width + gap)), window.right - 40)
            top = min(max(window.top, int(element.cy - h / 2)), window.bottom - h)
            width = min(w, max(40, window.right - left - 8))
        if width < 80:
            # Fallback below the label for mobile/web stacked forms.
            left = min(max(window.left + 8, int(element.left)), window.right - 80)
            top = min(max(window.top, int(element.top + element.height + 8)), window.bottom - h)
            width = min(w, max(80, window.right - left - 8))
            reason += "; below-label fallback"
        center = (left + width // 2, top + h // 2)
        candidates.append(InputCandidate(
            element=None,
            bbox=(left, top, width, h),
            center=center,
            score=score,
            source="synthetic_near_label",
            reason=reason,
        ))
    return candidates


def _chat_composer_candidate(
    snapshot: Snapshot,
    *,
    task: str,
    label_terms: list[str],
    expected_region: str,
) -> InputCandidate | None:
    """Infer a custom-rendered chat composer from split-pane geometry.

    This deliberately uses no application names, fixed pixels, or UIA class
    identifiers. A target label repeated in a left list row and a right-side
    header is strong evidence that a conversation is already open. Chat apps
    commonly render the composer as an unlabeled custom surface below history.
    """
    context = " ".join([task or "", *label_terms])
    expected = (expected_region or "").casefold().replace("_", "-")
    if not _CHAT_TASK_RE.search(context) and expected not in {
        "bottom", "lower", "bottom-right", "lower-right",
    }:
        return None
    window = snapshot.region
    task_norm = _norm(task)
    by_text: dict[str, list[UIElement]] = {}
    for element in snapshot.elements:
        text = _norm(element.text)
        if len(text) < 2 or int(element.width or 0) <= 0:
            continue
        by_text.setdefault(text, []).append(element)

    matched_label = ""
    for text, elements in by_text.items():
        left_row = any(
            int(element.cx) <= window.left + int(window.width * 0.17)
            and int(element.cy) >= window.top + int(window.height * 0.10)
            for element in elements
        )
        right_header = any(
            int(element.cx) >= window.left + int(window.width * 0.15)
            and int(element.cy) <= window.top + int(window.height * 0.16)
            for element in elements
        )
        requested = text in task_norm or any(text in _norm(term) for term in label_terms)
        if left_row and right_header and requested:
            matched_label = text
            break
    if not matched_label:
        return None

    left = window.left + max(12, int(window.width * 0.18))
    top = window.top + max(80, int(window.height * 0.82))
    right_margin = max(12, int(window.width * 0.02))
    bottom_margin = max(12, int(window.height * 0.025))
    width = max(160, window.right - right_margin - left)
    height = max(64, window.bottom - bottom_margin - top)
    center = (left + width // 2, top + height // 2)
    score = 0.92 + _region_match(
        InputCandidate(None, (left, top, width, height), center, 0.0, "", ""),
        window,
        expected_region or "bottom-right",
    )
    return InputCandidate(
        element=None,
        bbox=(left, top, width, height),
        center=center,
        score=score,
        source="synthetic_chat_composer",
        reason=(
            "target conversation header and list row are both visible; "
            "synthetic message input in lower conversation pane"
        ),
    )


def locate_input(
    snapshot: Snapshot,
    label: str = "",
    expected_region: str = "",
    fallback_text: Iterable[str] = (),
    min_score: float = 0.45,
    task: str = "",
) -> InputLocateResult:
    terms = _terms(label, fallback_text)
    strict_label = _requires_concrete_label_match(terms)
    wants_filename = _wants_filename(terms)
    wants_location = _wants_browser_location(terms)
    scored: list[InputCandidate] = []
    for element in snapshot.elements:
        if _is_caret_like(element):
            continue
        reason: list[str] = []
        score = 0.0
        if _is_input_role(element):
            score += 0.70
            reason.append("input-like role")
        elif str(element.text or "").strip() and (
            any(_norm(p) in _norm(element.text) for p in _PLACEHOLDER_TERMS)
            or _looks_search_semantic(element.text)
        ):
            if _looks_like_field_label(element.text):
                score += 0.04
                reason.append("field-label text; prefer nearby input box")
            else:
                score += 0.48
                reason.append("placeholder/search-like text")
        elif "focused" in {_norm(s) for s in element.states} and not _is_button_role(element):
            role = str(element.role or "").strip().casefold()
            area_ratio = (
                int(element.width or 0) * int(element.height or 0)
                / max(1, int(snapshot.region.width) * int(snapshot.region.height))
            )
            if role in {"window", "pane", "document", "group"} or area_ratio >= 0.30:
                continue
            score += 0.42
            reason.append("focused non-button element")
        else:
            continue
        text_bonus, text_reason = _text_score(element.text, terms)
        if text_bonus:
            score += text_bonus
            reason.append(text_reason)
        region_bonus = _region_match(element, snapshot.region, expected_region)
        if region_bonus:
            score += region_bonus
            reason.append("matches expected region" if region_bonus > 0 else "outside expected region")
        if element.source == "uia":
            score += 0.05
            reason.append("uia")
        width_ratio = int(element.width or 0) / max(1, int(snapshot.region.width))
        height_ratio = int(element.height or 0) / max(1, int(snapshot.region.height))
        if width_ratio >= 0.40 and height_ratio <= 0.12:
            score += 0.32
            reason.append("wide single-line input geometry")
        if wants_filename and _looks_like_filename_value(element.text):
            score += 0.48
            reason.append("filename value pattern matches requested field")
        if "focused" in {_norm(state) for state in element.states}:
            score += 0.20
            reason.append("focused")
        if _is_compact_property_editor(element, snapshot.region):
            score -= 0.85
            reason.append("compact key/value property editor is unsafe")
        if (
            int(element.width or 0) * int(element.height or 0)
            > int(snapshot.region.width * snapshot.region.height * 0.30)
            and "\n" in str(element.text or "")
        ):
            score -= 0.40
            reason.append("large document surface, not a field")
        is_location = _looks_like_browser_location_field(element, snapshot.region)
        if is_location and wants_location:
            score += 1.10
            reason.append("explicit browser location intent")
        elif is_location and not _wants_search(terms):
            score -= 0.85
            reason.append("browser location field conflicts with requested app input")
        elif wants_location:
            score -= 0.90
            reason.append("not a browser location field")
        scored.append(_candidate_from_element(element, score, reason))
    scored.extend(_input_candidates_near_labels(snapshot, terms))
    scored.extend(_nearby_synthetic_candidates(snapshot, terms))
    chat_composer = _chat_composer_candidate(
        snapshot,
        task=task,
        label_terms=terms,
        expected_region=expected_region,
    )
    if chat_composer is not None:
        scored.append(chat_composer)
    if wants_location:
        location_candidates = [
            candidate for candidate in scored
            if candidate.element is not None
            and _looks_like_browser_location_field(candidate.element, snapshot.region)
        ]
        if location_candidates:
            scored = location_candidates
    if strict_label:
        label_linked = [
            cand for cand in scored
            if cand.source == "synthetic_chat_composer"
            or "label" in cand.reason.casefold()
            or "filename value pattern" in cand.reason.casefold()
            or any(_norm(term) and _norm(term) in _norm(cand.element.text if cand.element else "") for term in terms)
        ]
        if not label_linked:
            best = max(scored, key=lambda cand: cand.score, default=None)
            return InputLocateResult(
                False,
                candidate=None,
                score=best.score if best else 0.0,
                reason="concrete_label_not_found",
                candidates=tuple(scored[:5]),
            )
        scored = label_linked
    if expected_region:
        adjusted: list[InputCandidate] = []
        for cand in scored:
            if (
                "matches expected region" in cand.reason
                or "outside expected region" in cand.reason
            ):
                adjusted.append(cand)
                continue
            region_bonus = _region_match(cand, snapshot.region, expected_region)
            if region_bonus:
                adjusted.append(InputCandidate(
                    element=cand.element,
                    bbox=cand.bbox,
                    center=cand.center,
                    score=cand.score + region_bonus,
                    source=cand.source,
                    reason=cand.reason + ("; matches expected region" if region_bonus > 0 else "; outside expected region"),
                ))
            else:
                adjusted.append(cand)
        scored = adjusted
    scored.sort(key=lambda cand: cand.score, reverse=True)
    if not scored:
        return InputLocateResult(False, score=0.0, reason="no input-like candidates")
    best = scored[0]
    if best.score < min_score:
        return InputLocateResult(
            False,
            candidate=None,
            score=best.score,
            reason="best_input_score_below_threshold",
            candidates=tuple(scored[:5]),
        )
    return InputLocateResult(
        True,
        candidate=best,
        score=best.score,
        reason=best.reason or "located_input",
        candidates=tuple(scored[:5]),
    )
