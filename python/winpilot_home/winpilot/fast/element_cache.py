"""Visual anchor cache and locator for fast repeated GUI actions."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable

from ..perception.fusion import Snapshot
from ..perception.model import UIElement
from ..utils.screenshot import Region
from .types import VisualAnchor


@dataclass
class ElementCacheEntry:
    key: str
    anchor: VisualAnchor
    element: UIElement
    window_title: str
    region: Region
    created_at: float
    hits: int = 0


@dataclass
class LocateResult:
    """Element lookup result with enough evidence for traces and the LLM."""

    element: UIElement | None
    score: float = 0.0
    cache_hit: bool = False
    reason: str = ""
    candidates: list[dict] | None = None

    def to_dict(self) -> dict:
        return {
            "found": self.element is not None,
            "score": round(float(self.score), 3),
            "cache_hit": bool(self.cache_hit),
            "reason": self.reason,
            "element": self.element.to_dict() if self.element is not None else None,
            "candidates": list(self.candidates or []),
        }


def _region_match(element: UIElement, window: Region, expected: str) -> bool:
    expected = (expected or "").casefold().replace("_", "-").replace(" ", "-")
    if not expected:
        return True
    rel_x = (element.cx - window.left) / max(1, window.width)
    rel_y = (element.cy - window.top) / max(1, window.height)
    if expected in {"top", "upper"}:
        return rel_y <= 0.35
    if expected in {"bottom", "lower"}:
        return rel_y >= 0.65
    if expected in {"left"}:
        return rel_x <= 0.35
    if expected in {"right"}:
        return rel_x >= 0.65
    if expected in {"top-right", "upper-right"}:
        return rel_x >= 0.55 and rel_y <= 0.45
    if expected in {"top-left", "upper-left"}:
        return rel_x <= 0.45 and rel_y <= 0.45
    if expected in {"bottom-right", "lower-right"}:
        return rel_x >= 0.55 and rel_y >= 0.55
    if expected in {"bottom-left", "lower-left"}:
        return rel_x <= 0.45 and rel_y >= 0.55
    if expected in {"center", "middle"}:
        return 0.25 <= rel_x <= 0.75 and 0.25 <= rel_y <= 0.75
    return True


def _terms(anchor: VisualAnchor) -> list[str]:
    out = [anchor.label, *anchor.fallback_text]
    return [term.casefold() for term in out if term]


_URL_OR_ADDRESS_RE = re.compile(
    r"^(?:https?://|file://|localhost\b|127\.0\.0\.1\b|\[::1\])|"
    r"(?:\.html?|:\d{2,5}/|\?.*=)",
    re.IGNORECASE,
)


def _looks_like_browser_address_text(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if len(value) > 120:
        return True
    return bool(_URL_OR_ADDRESS_RE.search(value))


def _role_family(role: str) -> str:
    value = (role or "").strip().casefold()
    if not value:
        return ""
    if any(term in value for term in ("button", "btn", "pushbutton", "splitbutton")):
        return "button"
    if any(term in value for term in ("edit", "textbox", "input", "combobox", "searchbox")):
        return "input"
    if any(term in value for term in ("menuitem", "menu item", "link", "hyperlink", "tab")):
        return "command"
    if "text" in value or "document" in value or "pane" in value:
        return "text"
    return value


def _role_penalty(element: UIElement, anchor: VisualAnchor) -> float:
    requested = _role_family(anchor.role)
    if not requested:
        return 0.0
    current = _role_family(element.role)
    if requested == current:
        return 0.12
    # OCR-only custom controls often surface as text even when they are really
    # buttons. Keep them viable, but do not let native browser chrome or normal
    # text fields win just because a fallback term matched.
    if requested == "button" and _looks_like_browser_address_text(element.text):
        return -1.2
    if requested == "button" and current == "text" and (element.source or "").casefold() == "ocr":
        return -0.05
    if requested in {"button", "input"} and current:
        return -0.42
    return 0.0


def _score(element: UIElement, anchor: VisualAnchor, window: Region) -> float:
    text = (element.text or "").casefold()
    terms = _terms(anchor)
    score = 0.0
    if terms:
        for term in terms:
            if text == term:
                score = max(score, 1.0)
            elif term in text:
                score = max(score, 0.82)
            elif text and text in term:
                score = max(score, 0.55)
        # Accessibility trees can expose hidden tabs, logs, and JSON blobs.
        # A substring inside a large body of text is not a click target.
        longest_term = max((len(term) for term in terms), default=0)
        if score and text not in terms and len(text) > max(96, longest_term * 6):
            score = min(score, 0.22)
        element_area = max(0, int(element.width)) * max(0, int(element.height))
        window_area = max(1, int(window.width)) * max(1, int(window.height))
        if score and element_area / window_area > 0.18:
            score -= 0.25
    if anchor.role:
        score += _role_penalty(element, anchor)
    if anchor.expected_region and _region_match(element, window, anchor.expected_region):
        score += 0.08
    elif anchor.expected_region:
        score -= 0.25
    return score


def _rel_center(element: UIElement, window: Region) -> tuple[float, float]:
    return (
        (element.cx - window.left) / max(1, window.width),
        (element.cy - window.top) / max(1, window.height),
    )


def _rel_distance(a: UIElement, a_window: Region, b: UIElement, b_window: Region) -> float:
    ax, ay = _rel_center(a, a_window)
    bx, by = _rel_center(b, b_window)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _text_compatible(previous: str, current: str) -> bool:
    previous = (previous or "").strip().casefold()
    current = (current or "").strip().casefold()
    if not previous or not current:
        return True
    return previous == current or previous in current or current in previous


def _role_compatible(previous: str, current: str) -> bool:
    previous = (previous or "").strip().casefold()
    current = (current or "").strip().casefold()
    if not previous or not current:
        return True
    return previous == current or previous in current or current in previous


def _candidate_trace(element: UIElement, score: float) -> dict:
    data = element.to_dict()
    data["score"] = round(float(score), 3)
    return data


class ElementCache:
    """Cache stable semantic anchors for a single run."""

    def __init__(self, ttl_s: float = 180.0) -> None:
        self.ttl_s = float(ttl_s)
        self._items: dict[str, ElementCacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._stale = 0

    @staticmethod
    def key(snapshot: Snapshot, anchor: VisualAnchor) -> str:
        title = (snapshot.title or "").casefold()
        label = (anchor.label or "|".join(anchor.fallback_text) or anchor.visual_anchor).casefold()
        region = (anchor.expected_region or "").casefold()
        role = (anchor.role or "").casefold()
        return f"{title}|{label}|{region}|{role}"

    def remember(self, snapshot: Snapshot, anchor: VisualAnchor, element: UIElement) -> None:
        key = self.key(snapshot, anchor)
        self._items[key] = ElementCacheEntry(
            key=key,
            anchor=anchor,
            element=element,
            window_title=snapshot.title,
            region=snapshot.region,
            created_at=time.monotonic(),
        )

    def get(self, snapshot: Snapshot, anchor: VisualAnchor) -> UIElement | None:
        result = self.get_with_trace(snapshot, anchor)
        return result.element

    def get_with_trace(self, snapshot: Snapshot, anchor: VisualAnchor) -> LocateResult:
        entry = self._items.get(self.key(snapshot, anchor))
        if not entry:
            self._misses += 1
            return LocateResult(None, reason="cache_miss")
        if time.monotonic() - entry.created_at > self.ttl_s:
            self._items.pop(entry.key, None)
            self._stale += 1
            return LocateResult(None, reason="cache_expired")

        # Element ids are valid only within one observe. Revalidate the cached
        # anchor against the current snapshot by semantics and relative geometry.
        scored: list[tuple[UIElement, float]] = []
        for element in snapshot.elements:
            anchor_score = _score(element, anchor, snapshot.region)
            distance = _rel_distance(element, snapshot.region, entry.element, entry.region)
            proximity = 0.35 if distance <= 0.04 else 0.22 if distance <= 0.09 else 0.08 if distance <= 0.16 else -0.30
            compatibility = 0.0
            if not _text_compatible(entry.element.text, element.text):
                compatibility -= 0.45
            if not _role_compatible(entry.element.role, element.role):
                compatibility -= 0.18
            scored.append((element, anchor_score + proximity + compatibility))
        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            self._misses += 1
            return LocateResult(None, reason="cache_target_not_visible")
        current, score = scored[0]
        if score < 0.72:
            self._misses += 1
            return LocateResult(
                None,
                score=score,
                reason="cache_revalidation_failed",
                candidates=[_candidate_trace(e, s) for e, s in scored[:3]],
            )
        entry.hits += 1
        self._hits += 1
        return LocateResult(
            current,
            score=score,
            cache_hit=True,
            reason="cache_revalidated",
            candidates=[_candidate_trace(e, s) for e, s in scored[:3]],
        )

    def locate(self, snapshot: Snapshot, anchor: VisualAnchor, min_score: float = 0.55) -> UIElement | None:
        return self.locate_with_trace(snapshot, anchor, min_score=min_score).element

    def locate_with_trace(
        self,
        snapshot: Snapshot,
        anchor: VisualAnchor,
        min_score: float = 0.55,
    ) -> LocateResult:
        cached = self.get_with_trace(snapshot, anchor)
        if cached.element is not None:
            return cached
        candidates = sorted(
            ((element, _score(element, anchor, snapshot.region)) for element in snapshot.elements),
            key=lambda item: item[1],
            reverse=True,
        )
        if not candidates or candidates[0][1] < min_score:
            return LocateResult(
                None,
                score=candidates[0][1] if candidates else 0.0,
                reason="best_score_below_threshold",
                candidates=[_candidate_trace(e, s) for e, s in candidates[:5]],
            )
        element = candidates[0][0]
        self.remember(snapshot, anchor, element)
        return LocateResult(
            element,
            score=candidates[0][1],
            cache_hit=False,
            reason="located_from_snapshot",
            candidates=[_candidate_trace(e, s) for e, s in candidates[:5]],
        )

    def stats(self) -> dict:
        return {
            "entries": len(self._items),
            "hits": self._hits,
            "misses": self._misses,
            "stale": self._stale,
        }


def anchor_from_dict(data: dict) -> VisualAnchor:
    fallback = data.get("fallback_text") or data.get("fallback") or []
    if isinstance(fallback, str):
        fallback = [fallback]
    visual_anchor = str(data.get("visual_anchor") or "")
    role = str(data.get("role") or "")
    if not role and any(token in visual_anchor.casefold() for token in ("button", "按钮", "btn")):
        role = "button"
    return VisualAnchor(
        label=str(data.get("label") or ""),
        expected_region=str(data.get("expected_region") or ""),
        visual_anchor=visual_anchor,
        fallback_text=tuple(str(item) for item in fallback if item),
        role=role,
    )


def best_text_match(snapshot: Snapshot, labels: Iterable[str]) -> UIElement | None:
    anchor = VisualAnchor(fallback_text=tuple(labels))
    return ElementCache(ttl_s=0).locate(snapshot, anchor)
