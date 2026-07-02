"""Visual anchor cache and locator for fast repeated GUI actions."""
from __future__ import annotations

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


def _score(element: UIElement, anchor: VisualAnchor, window: Region) -> float:
    text = (element.text or "").casefold()
    role = (element.role or "").casefold()
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
    if anchor.role and anchor.role.casefold() in role:
        score += 0.12
    if anchor.expected_region and _region_match(element, window, anchor.expected_region):
        score += 0.08
    elif anchor.expected_region:
        score -= 0.25
    return score


class ElementCache:
    """Cache stable semantic anchors for a single run."""

    def __init__(self, ttl_s: float = 180.0) -> None:
        self.ttl_s = float(ttl_s)
        self._items: dict[str, ElementCacheEntry] = {}

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
        entry = self._items.get(self.key(snapshot, anchor))
        if not entry:
            return None
        if time.monotonic() - entry.created_at > self.ttl_s:
            self._items.pop(entry.key, None)
            return None
        # Only reuse if it is still visible in the latest snapshot.
        current = snapshot.by_id(entry.element.elem_id)
        if current is None:
            return None
        entry.hits += 1
        return current

    def locate(self, snapshot: Snapshot, anchor: VisualAnchor, min_score: float = 0.55) -> UIElement | None:
        cached = self.get(snapshot, anchor)
        if cached is not None:
            return cached
        candidates = sorted(
            ((element, _score(element, anchor, snapshot.region)) for element in snapshot.elements),
            key=lambda item: item[1],
            reverse=True,
        )
        if not candidates or candidates[0][1] < min_score:
            return None
        element = candidates[0][0]
        self.remember(snapshot, anchor, element)
        return element

    def stats(self) -> dict:
        return {
            "entries": len(self._items),
            "hits": sum(item.hits for item in self._items.values()),
        }


def anchor_from_dict(data: dict) -> VisualAnchor:
    fallback = data.get("fallback_text") or data.get("fallback") or []
    if isinstance(fallback, str):
        fallback = [fallback]
    return VisualAnchor(
        label=str(data.get("label") or ""),
        expected_region=str(data.get("expected_region") or ""),
        visual_anchor=str(data.get("visual_anchor") or ""),
        fallback_text=tuple(str(item) for item in fallback if item),
        role=str(data.get("role") or ""),
    )


def best_text_match(snapshot: Snapshot, labels: Iterable[str]) -> UIElement | None:
    anchor = VisualAnchor(fallback_text=tuple(labels))
    return ElementCache(ttl_s=0).locate(snapshot, anchor)
