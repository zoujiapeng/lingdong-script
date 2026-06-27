"""Perception fusion: run enabled dimensions in priority order, merge,
dedupe, and produce both a structured snapshot and a compact text rendering
for the LLM.

Element IDs are stable within one observation; the agent refers to elements
as ``click(element_id=12)`` and the executor resolves them through the
snapshot registry.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import win32gui

from ..config import CONFIG
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, screen_size, window_region
from . import ocr, uia, vision_cv, vlm, win32
from .model import UIElement

_DEDUP_IOU = 0.5


def _rect_overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area = max(1, (ax2 - ax1) * (ay2 - ay1))
    return inter / area


def _foreground_modal_over_target(hwnd: int, region: Region) -> bool:
    """Detect owner-drawn/modal foreground overlays that can make UIA hang.

    When a XAML/custom modal is foreground and overlaps the requested target,
    querying UIA on the owner window can block. In that case Win32/OCR/CV and
    visual summaries are safer than walking the owner's UIA tree.
    """
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg or fg == hwnd:
            return False
        cls = (win32gui.GetClassName(fg) or "").casefold()
        title = (win32gui.GetWindowText(fg) or "").strip()
        rect = win32gui.GetWindowRect(fg)
    except Exception:
        return False
    target_rect = (region.left, region.top, region.right, region.bottom)
    overlap = _rect_overlap_ratio(rect, target_rect)
    modalish = (
        "modal" in cls
        or "popup" in cls
        or "tooltip" in cls
        or (not title and overlap > 0.25)
    )
    return modalish and overlap > 0.15


@dataclass
class Snapshot:
    """One observation of a window: fused elements + metadata."""

    hwnd: int
    title: str
    region: Region
    elements: list[UIElement] = field(default_factory=list)
    dim_stats: dict[str, dict] = field(default_factory=dict)  # per-dimension count+ms
    created_at: float = field(default_factory=time.time)

    def by_id(self, elem_id: int) -> UIElement | None:
        for element in self.elements:
            if element.elem_id == elem_id:
                return element
        return None

    def to_text(self, max_elements: int = 160) -> str:
        lines = [
            f"Window: \"{self.title}\" hwnd={self.hwnd} "
            f"rect=({self.region.left},{self.region.top},"
            f"{self.region.width}x{self.region.height})"
        ]
        stats = " ".join(
            f"{name}:{stat['count']} in {stat['ms']}ms"
            for name, stat in self.dim_stats.items()
        )
        if stats:
            lines.append(f"Perception: {stats}")

        readable = sum(1 for e in self.elements if (e.text or "").strip())
        big = self.region.width * self.region.height > 200_000
        if readable < 3 and big:
            lines.append(
                "WARNING: this large window has almost no readable structured "
                "elements. It may be canvas/video/game/custom drawing. Verify "
                "focus first; if no visual model is available, do not pretend "
                "to know pixel contents from coordinates alone."
            )
        shown = self.elements[:max_elements]
        lines.extend(element.to_line() for element in shown)
        if len(self.elements) > max_elements:
            lines.append(f"... {len(self.elements) - max_elements} more elements omitted")
        return "\n".join(lines)


def _merge(primary: list[UIElement], secondary: list[UIElement]) -> list[UIElement]:
    """Append secondary elements that don't duplicate a primary one.

    A secondary element is a duplicate if it overlaps (IoU) a primary element
    AND adds no new text. OCR text that a UIA element lacks is grafted onto
    the UIA element instead of being dropped.
    """
    merged = list(primary)
    for cand in secondary:
        duplicate = False
        for kept in merged:
            if cand.iou(kept) >= _DEDUP_IOU:
                if cand.text and not kept.text:
                    kept.text = cand.text  # graft OCR text onto unnamed element
                if cand.text and kept.text and cand.text.strip() in kept.text:
                    duplicate = True
                    break
                if not cand.text:
                    duplicate = True
                    break
                if cand.text == kept.text:
                    duplicate = True
                    break
        if not duplicate:
            merged.append(cand)
    return merged


def _has_readable(elements: list[UIElement]) -> bool:
    return any((e.text or "").strip() for e in elements)


# Lowered OCR confidence for rescue passes — the normal threshold already
# returned nothing, so trade precision for recall.
_ESCALATE_OCR_CONF = 0.4


def _escalate_empty(snapshot: Snapshot, region: Region,
                    fused: list[UIElement]) -> list[UIElement]:
    """Rescue path when perception came back blind (no readable element).

    The agent cannot act on an empty screen — before returning it, retry with
    progressively stronger fallbacks: window OCR at lowered confidence →
    full-screen OCR → VLM (when configured). Controlled by
    ``fusion.escalate_on_empty`` (turn off for dimension-isolation testing).
    """
    steps: list[tuple[str, object]] = [
        ("esc_ocr_window", lambda: ocr.collect(region, min_conf=_ESCALATE_OCR_CONF)),
        ("esc_ocr_screen", lambda: ocr.collect(
            Region(0, 0, *screen_size()), min_conf=_ESCALATE_OCR_CONF)),
    ]
    if vlm.is_configured():
        steps.append(("esc_vlm", lambda: vlm.collect(region)))

    for name, collect_fn in steps:
        started = time.monotonic()
        try:
            collected = collect_fn()
        except Exception as exc:
            logger.warning("空快照升级 %s 异常: %s", name, exc)
            collected = []
        elapsed_ms = int((time.monotonic() - started) * 1000)
        snapshot.dim_stats[name] = {"count": len(collected), "ms": elapsed_ms}
        fused = _merge(fused, collected) if fused else list(collected)
        if _has_readable(fused):
            logger.info("空快照升级生效: %s 补回 %d 个元素", name, len(collected))
            break
    return fused


def observe(hwnd: int, include_icons: bool = True,
            vision_priority: bool = False,
            perception_mode: str = "") -> Snapshot:
    """Observe a window with all enabled dimensions, fused by priority.

    perception_mode controls how aggressively visual perception is applied:
      "normal"  (default) — UIA-rich windows skip OCR/VLM for speed.
      "visual"  — chat/canvas/mirror tasks: OCR always runs; when VLM is
                  configured it also runs as an assist dimension (not just
                  as rescue), unless the window is extremely UIA-rich
                  (>= vlm_assist_threshold, default 60).
      "vlm_first" — future; reserved.

    For backward-compat, passing vision_priority=True is equivalent to
    perception_mode="visual".
    """
    # Normalise the mode from either argument.
    if not perception_mode:
        perception_mode = "visual" if vision_priority else "normal"

    region = window_region(hwnd)
    title = win32._window_text(hwnd)
    snapshot = Snapshot(hwnd=hwnd, title=title, region=region)

    order = CONFIG.enabled_dimensions()
    is_visual = perception_mode == "visual"

    # OCR: skip when UIA already returned a rich, reliable element set —
    # except for visual-mode tasks (chat/canvas) where the real content lives
    # in pixels invisible to UIA.
    skip_ocr_if_rich = (
        bool(CONFIG.get("fusion", "ocr_skip_when_uia_rich", default=True))
        and not is_visual
    )
    uia_rich_threshold = int(CONFIG.get("fusion", "uia_rich_threshold", default=20))

    # VLM assist: in visual mode, VLM runs as a regular dimension (not just
    # the rescue ladder) when configured. A higher threshold (vlm_assist_threshold,
    # default 60) is used so that extremely UIA-rich windows (e.g. a full IDE)
    # still skip VLM for latency, while chat-app chrome (~20-40 UIA elements)
    # gets VLM coverage for the pixel-rendered message bubbles.
    vlm_assist_threshold = int(CONFIG.get("fusion", "vlm_assist_threshold", default=60))
    # In normal mode, VLM on the hot path is still suppressed at uia_rich_threshold.
    skip_vlm_if_rich_normal = bool(CONFIG.get("fusion", "vlm_skip_when_uia_rich", default=True))

    uia_count = 0
    skip_uia_modal = _foreground_modal_over_target(hwnd, region)
    fused: list[UIElement] = []
    for dim in order:
        started = time.monotonic()
        collected: list[UIElement] = []
        try:
            if dim == "uia":
                if skip_uia_modal:
                    snapshot.dim_stats["uia"] = {
                        "count": 0,
                        "ms": 0,
                        "skipped": "foreground_modal_overlay",
                    }
                    continue
                collected = uia.collect(hwnd)
                uia_count = sum(1 for e in collected if (e.text or "").strip())
            elif dim == "win32":
                collected = win32.collect(hwnd)
                collected.extend(win32.explorer_items(hwnd))
            elif dim == "ocr":
                if skip_ocr_if_rich and uia_count >= uia_rich_threshold:
                    snapshot.dim_stats["ocr"] = {"count": 0, "ms": 0, "skipped": "uia_rich"}
                    continue
                collected = ocr.collect(region)
            elif dim == "cv":
                collected = vision_cv.detect_icon_candidates(region) if include_icons else []
            elif dim == "vlm":
                # Visual mode: VLM assists up to the higher threshold.
                # Normal mode: VLM is still skipped when UIA is rich.
                if is_visual:
                    skip_vlm = uia_count >= vlm_assist_threshold
                    skip_reason = "vlm_assist_threshold"
                else:
                    skip_vlm = skip_vlm_if_rich_normal and uia_count >= uia_rich_threshold
                    skip_reason = "uia_rich"
                if skip_vlm:
                    snapshot.dim_stats["vlm"] = {"count": 0, "ms": 0, "skipped": skip_reason}
                    continue
                collected = vlm.collect(region)
                if collected:
                    logger.info(
                        "VLM assist (%s mode): %d elements from %s",
                        perception_mode, len(collected), title[:40],
                    )
        except Exception as exc:
            logger.warning("感知维度 %s 异常: %s", dim, exc)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        snapshot.dim_stats[dim] = {"count": len(collected), "ms": elapsed_ms}
        fused = _merge(fused, collected) if fused else list(collected)

    # CV icon candidates that overlap any textual element are noise — drop them.
    fused = [
        element for element in fused
        if not (
            element.source == "cv"
            and element.role == "icon"
            and any(
                other.source != "cv" and element.iou(other) > 0.25 for other in fused
            )
        )
    ]

    # Phase 2-D: never hand the agent a blind (no readable element) snapshot
    # without trying the rescue ladder first.
    if (
        not _has_readable(fused)
        and CONFIG.get("fusion", "escalate_on_empty", default=True)
    ):
        fused = _escalate_empty(snapshot, region, fused)

    for idx, element in enumerate(fused, start=1):
        element.elem_id = idx
    snapshot.elements = fused

    BUS.publish(
        "perception",
        hwnd=hwnd,
        title=title,
        dims=snapshot.dim_stats,
        total=len(fused),
    )
    return snapshot


def observe_screen() -> list[dict]:
    """Top-level window list (independent of dimension toggles)."""
    return win32.list_windows()
