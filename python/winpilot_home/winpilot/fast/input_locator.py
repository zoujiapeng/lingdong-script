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
    "搜索", "查找", "输入", "请输入", "消息", "评论", "搜索框",
)
_CARET_TEXTS = {"i", "l", "|", "丨", "︱"}
_SEARCH_ICON_TEXTS = {"q", "o", "0", "🔍"}


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


def _is_caret_like(element: UIElement) -> bool:
    text = _norm(element.text)
    return (
        element.source == "ocr"
        and text in _CARET_TEXTS
        and int(element.height or 0) >= max(12, int(element.width or 1) * 2)
    )


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


def _text_score(text: str, terms: list[str]) -> tuple[float, str]:
    text_norm = _norm(text)
    if not text_norm or not terms:
        return 0.0, ""
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
            wants_search = any(_norm(t) in {"search", "搜索", "查找", "query"} for t in label_terms)
            if wants_search and text_norm in _SEARCH_ICON_TEXTS:
                matched = True
                score = 0.42
                reason = "possible search icon beside input"
        if not matched:
            continue
        h = max(26, min(54, int(element.height * 1.8) if element.height else 32))
        w = max(140, min(420, int(window.width * 0.36)))
        gap = max(8, min(20, int(element.width * 0.18) if element.width else 10))
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


def locate_input(
    snapshot: Snapshot,
    label: str = "",
    expected_region: str = "",
    fallback_text: Iterable[str] = (),
    min_score: float = 0.45,
) -> InputLocateResult:
    terms = _terms(label, fallback_text)
    scored: list[InputCandidate] = []
    for element in snapshot.elements:
        if _is_caret_like(element):
            continue
        reason: list[str] = []
        score = 0.0
        if _is_input_role(element):
            score += 0.70
            reason.append("input-like role")
        elif str(element.text or "").strip() and any(_norm(p) in _norm(element.text) for p in _PLACEHOLDER_TERMS):
            score += 0.48
            reason.append("placeholder/search-like text")
        elif "focused" in {_norm(s) for s in element.states} and not _is_button_role(element):
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
        scored.append(_candidate_from_element(element, score, reason))
    scored.extend(_nearby_synthetic_candidates(snapshot, terms))
    if expected_region:
        adjusted: list[InputCandidate] = []
        for cand in scored:
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
