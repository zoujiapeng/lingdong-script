"""Perception fusion: run enabled dimensions in priority order, merge,
dedupe, and produce both a structured snapshot and a compact text rendering
for the LLM.

Element IDs are stable within one observation; the agent refers to elements
as ``click(element_id=12)`` and the executor resolves them through the
snapshot registry.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import win32gui

from ..config import CONFIG
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, virtual_screen_region, window_region
from . import ocr, uia, vision_cv, vlm, win32
from .model import UIElement

_DEDUP_IOU = 0.5
_UIA_SUPPRESSION_LOCK = threading.Lock()
_UIA_SUPPRESSED_UNTIL: dict[int, float] = {}


def _uia_content_count(elements: list[UIElement], region: Region) -> int:
    """Count useful structured elements below window/tool chrome.

    A browser can expose dozens of tabs and toolbar buttons while its document
    tree is still unavailable.  Raw element count alone must not suppress OCR
    in that state.
    """
    content_top = region.top + max(72, min(160, int(region.height * 0.12)))
    window_area = max(1, region.width * region.height)
    count = 0
    for element in elements:
        if not str(element.text or "").strip():
            continue
        width = max(0, int(element.width or 0))
        height = max(0, int(element.height or 0))
        if width * height >= window_area * 0.65:
            continue
        if int(element.cy or 0) < content_top:
            continue
        if int(element.cy or 0) > region.bottom:
            continue
        count += 1
    return count


def _uia_is_rich(elements: list[UIElement], region: Region, threshold: int) -> bool:
    readable = sum(1 for element in elements if str(element.text or "").strip())
    required_content = max(3, min(12, max(1, int(threshold)) // 4))
    return readable >= int(threshold) and _uia_content_count(elements, region) >= required_content


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

    def scoped_elements(self, margin: int = 64) -> list[UIElement]:
        """Return elements that plausibly belong to this captured window.

        Shared-host applications sometimes expose stale UIA children from
        off-screen tabs with large negative coordinates. They remain in the
        raw trace for diagnosis, but must not occupy the model's normal view or
        become clickable candidates for the current window.
        """
        pad = max(0, int(margin))
        left = self.region.left - pad
        top = self.region.top - pad
        right = self.region.right + pad
        bottom = self.region.bottom + pad
        scoped: list[UIElement] = []
        for element in self.elements:
            elem_left = int(element.left or 0)
            elem_top = int(element.top or 0)
            elem_width = max(0, int(element.width or 0))
            elem_height = max(0, int(element.height or 0))
            if elem_width > 0 and elem_height > 0:
                elem_right = elem_left + elem_width
                elem_bottom = elem_top + elem_height
                if elem_right < left or elem_left > right or elem_bottom < top or elem_top > bottom:
                    continue
            else:
                cx = int(element.cx or 0)
                cy = int(element.cy or 0)
                if not (left <= cx <= right and top <= cy <= bottom):
                    continue
            scoped.append(element)
        return scoped

    def to_text(self, max_elements: int = 160, query: str = "") -> str:
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

        elements = self.scoped_elements()
        filtered_count = max(0, len(self.elements) - len(elements))
        if filtered_count:
            lines.append(
                f"Perception scope: filtered {filtered_count} off-window/stale element(s); "
                "the complete raw set remains available in the perception trace."
            )
        readable = sum(1 for e in elements if (e.text or "").strip())
        big = self.region.width * self.region.height > 200_000
        if readable < 3 and big:
            lines.append(
                "WARNING: this large window has almost no readable structured "
                "elements. It may be canvas/video/game/custom drawing. Verify "
                "focus first; if no visual model is available, do not pretend "
                "to know pixel contents from coordinates alone."
            )
        query = (query or "").strip().casefold()
        if query:
            query_terms = [
                part.strip()
                for part in re.split(r"[,，;；|\n]+", query)
                if part.strip()
            ] or [query]
            total_elements = len(elements)
            ranked_matches: list[tuple[int, UIElement]] = []
            for element in elements:
                element_text = (element.text or "").casefold()
                score = sum(1 for term in query_terms if term in element_text)
                if score:
                    ranked_matches.append((score, element))
            ranked_matches.sort(
                key=lambda item: (-item[0], len(item[1].text or "")),
            )
            matches = [element for _score, element in ranked_matches]
            if matches:
                elements = matches
                if len(query_terms) == 1:
                    lines.append(
                        f'Filtered elements matching query "{query}" '
                        f"({len(matches)}/{total_elements} in-window elements)."
                    )
                else:
                    lines.append(
                        f'Filtered elements matching any query term {query_terms} '
                        f"({len(matches)}/{total_elements} in-window elements)."
                    )
            else:
                lines.append(
                    f'No elements matched query "{query}" among '
                    f"{len(elements)} in-window elements; showing first {max_elements}."
                )
        shown = elements[:max_elements]
        lines.extend(element.to_line() for element in shown)
        if len(elements) > max_elements:
            lines.append(f"... {len(elements) - max_elements} more elements omitted")
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
            virtual_screen_region(), min_conf=_ESCALATE_OCR_CONF)),
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


def _run_uia(hwnd: int) -> tuple[list[UIElement], int]:
    """Run UIA collection and return (elements, elapsed_ms). Never raises."""
    started = time.monotonic()
    try:
        collected = uia.collect(hwnd)
    except Exception as exc:
        logger.warning("UIA 并行采集异常: %s", exc)
        collected = []
    return collected, int((time.monotonic() - started) * 1000)


def _run_ocr(region: Region) -> tuple[list[UIElement], int]:
    """Run OCR collection and return (elements, elapsed_ms). Never raises."""
    started = time.monotonic()
    try:
        collected = ocr.collect(region)
    except Exception as exc:
        logger.warning("OCR 并行采集异常: %s", exc)
        collected = []
    return collected, int((time.monotonic() - started) * 1000)


def _bounded_parallel_jobs(
    jobs: dict[str, object],
    *,
    timeout_s: float,
    stop_event=None,
) -> dict:
    """Run independent perception jobs without letting one block the caller.

    ``ThreadPoolExecutor`` waits for every worker when its context exits, so a
    stuck UIA provider can defeat both an ``as_completed`` timeout and Agent
    cancellation.  These workers are daemon threads: completed results are
    consumed, while a stuck provider is abandoned and reported explicitly.
    """
    started = time.monotonic()
    deadline = started + max(0.05, float(timeout_s))
    results: dict[str, object] = {}
    errors: dict[str, str] = {}
    done = {name: threading.Event() for name in jobs}
    lock = threading.Lock()

    def run(name: str, fn) -> None:
        try:
            value = fn()
            with lock:
                results[name] = value
        except Exception as exc:
            with lock:
                errors[name] = str(exc)
        finally:
            done[name].set()

    for name, fn in jobs.items():
        threading.Thread(
            target=run,
            args=(name, fn),
            name=f"winpilot-perception-{name}",
            daemon=True,
        ).start()

    canceled = False
    while not all(event.is_set() for event in done.values()):
        if stop_event is not None and stop_event.is_set():
            canceled = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Wake frequently so /api/stop can interrupt a blocked provider.
        next(iter(done.values())).wait(min(0.05, remaining))

    timed_out = [name for name, event in done.items() if not event.is_set()]
    return {
        "results": results,
        "errors": errors,
        "timed_out": timed_out,
        "canceled": canceled,
        "wall_ms": int((time.monotonic() - started) * 1000),
    }


def _uia_suppressed(hwnd: int) -> bool:
    now = time.monotonic()
    with _UIA_SUPPRESSION_LOCK:
        until = float(_UIA_SUPPRESSED_UNTIL.get(int(hwnd), 0.0) or 0.0)
        if until <= now:
            _UIA_SUPPRESSED_UNTIL.pop(int(hwnd), None)
            return False
        return True


def _suppress_uia(hwnd: int) -> None:
    cooldown = float(CONFIG.get(
        "fusion", "uia_timeout_cooldown_s", default=30.0,
    ) or 30.0)
    with _UIA_SUPPRESSION_LOCK:
        _UIA_SUPPRESSED_UNTIL[int(hwnd)] = time.monotonic() + max(1.0, cooldown)


def observe(hwnd: int, include_icons: bool = True,
            vision_priority: bool = False,
            perception_mode: str = "", stop_event=None) -> Snapshot:
    """Observe a window with all enabled dimensions, fused by priority.

    perception_mode controls how aggressively visual perception is applied:
      "normal"  (default) — UIA-rich windows skip OCR/VLM for speed.
      "pixel_text" — office/document surfaces: UIA and OCR run in parallel;
                   VLM runs only if OCR returns no readable content.
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
    is_pixel_text = perception_mode == "pixel_text"
    parallel_ocr = is_visual or is_pixel_text
    if is_visual and vlm.is_configured() and "vlm" not in order:
        order.append("vlm")

    # OCR: skip when UIA already returned a rich, reliable element set —
    # except for visual-mode tasks (chat/canvas) where the real content lives
    # in pixels invisible to UIA.
    skip_ocr_if_rich = (
        bool(CONFIG.get("fusion", "ocr_skip_when_uia_rich", default=True))
        and not parallel_ocr
    )
    uia_rich_threshold = int(CONFIG.get("fusion", "uia_rich_threshold", default=20))

    # VLM assist: in visual mode, VLM runs as a regular dimension (not just
    # the rescue ladder) when configured. A higher threshold (vlm_assist_threshold,
    # default 60) is used so that extremely UIA-rich windows (e.g. a full IDE)
    # still skip VLM for latency, while chat-app chrome (~20-40 UIA elements)
    # gets VLM coverage for the pixel-rendered message bubbles.
    vlm_assist_threshold = int(CONFIG.get("fusion", "vlm_assist_threshold", default=60))
    vlm_ocr_rich_threshold = int(CONFIG.get(
        "fusion", "vlm_ocr_rich_threshold", default=12,
    ))
    # In normal mode, VLM on the hot path is still suppressed at uia_rich_threshold.
    skip_vlm_if_rich_normal = bool(CONFIG.get("fusion", "vlm_skip_when_uia_rich", default=True))

    uia_count = 0
    uia_elements: list[UIElement] = []
    uia_rich = False
    skip_uia_modal = _foreground_modal_over_target(hwnd, region)
    fused: list[UIElement] = []

    if parallel_ocr and not skip_uia_modal and "uia" in order and "ocr" in order:
        # Visual/pixel-text modes: UIA and OCR are likely both needed but
        # historically ran serially (1800ms UIA + 1600ms OCR = 3.4s per call).
        # Run them in parallel so total latency ≈ max(UIA, OCR) ≈ 1.8s.
        logger.info("[fusion] PARALLEL PATH: visual=%s skip_modal=%s", is_visual, skip_uia_modal)
        uia_result: list[UIElement] = []
        ocr_result: list[UIElement] = []
        uia_ms = 0
        ocr_ms = 0
        uia_was_suppressed = _uia_suppressed(hwnd)
        jobs: dict[str, object] = {"ocr": lambda: _run_ocr(region)}
        if not uia_was_suppressed:
            jobs["uia"] = lambda: _run_uia(hwnd)
        dimension_timeout_s = float(CONFIG.get(
            "fusion", "visual_dimension_timeout_s", default=6.0,
        ) or 6.0)
        parallel = _bounded_parallel_jobs(
            jobs,
            timeout_s=dimension_timeout_s,
            stop_event=stop_event,
        )
        uia_payload = parallel["results"].get("uia")
        ocr_payload = parallel["results"].get("ocr")
        if isinstance(uia_payload, tuple) and len(uia_payload) == 2:
            uia_result, uia_ms = uia_payload
        if isinstance(ocr_payload, tuple) and len(ocr_payload) == 2:
            ocr_result, ocr_ms = ocr_payload
        if "uia" in parallel["timed_out"]:
            _suppress_uia(hwnd)
            uia_ms = parallel["wall_ms"]
        if "ocr" in parallel["timed_out"]:
            ocr_ms = parallel["wall_ms"]
        uia_count = sum(1 for e in uia_result if (e.text or "").strip())
        uia_elements = list(uia_result)
        uia_rich = _uia_is_rich(uia_elements, region, uia_rich_threshold)
        logger.info(
            "[fusion] PARALLEL DONE: uia=%dms ocr=%dms wall=%dms timeouts=%s canceled=%s",
            uia_ms,
            ocr_ms,
            parallel["wall_ms"],
            parallel["timed_out"],
            parallel["canceled"],
        )
        snapshot.dim_stats["uia"] = {
            "count": len(uia_result),
            "ms": uia_ms,
            "content_count": _uia_content_count(uia_result, region),
            "rich": uia_rich,
        }
        snapshot.dim_stats["ocr"] = {"count": len(ocr_result), "ms": ocr_ms}
        if uia_was_suppressed:
            snapshot.dim_stats["uia"]["skipped"] = "recent_provider_timeout"
        if "uia" in parallel["timed_out"]:
            snapshot.dim_stats["uia"]["timed_out"] = True
        if "ocr" in parallel["timed_out"]:
            snapshot.dim_stats["ocr"]["timed_out"] = True
        if parallel["canceled"]:
            snapshot.dim_stats["parallel"] = {
                "count": 0,
                "ms": parallel["wall_ms"],
                "canceled": True,
            }
        # Merge: UIA primary, OCR fills gaps (text grafting handled in _merge).
        fused = _merge(uia_result, ocr_result) if uia_result else list(ocr_result)
        # Win32 + CV still run serially (they're fast: <50ms each).
        for dim in order:
            if dim in ("uia", "ocr"):
                continue
            if stop_event is not None and stop_event.is_set():
                break
            started = time.monotonic()
            collected: list[UIElement] = []
            try:
                if dim == "win32":
                    collected = win32.collect(hwnd)
                    collected.extend(win32.explorer_items(hwnd))
                elif dim == "cv":
                    collected = vision_cv.detect_icon_candidates(region) if include_icons else []
                elif dim == "vlm":
                    if is_pixel_text and _has_readable(ocr_result):
                        snapshot.dim_stats["vlm"] = {
                            "count": 0,
                            "ms": 0,
                            "skipped": "pixel_text_ocr_grounded",
                        }
                        continue
                    ocr_readable_count = sum(
                        1 for element in ocr_result if (element.text or "").strip()
                    )
                    skip_vlm = (
                        False
                        if is_pixel_text
                        else (
                            _uia_is_rich(uia_elements, region, vlm_assist_threshold)
                            or ocr_readable_count >= vlm_ocr_rich_threshold
                        )
                    )
                    if skip_vlm:
                        reason = (
                            "ocr_rich"
                            if ocr_readable_count >= vlm_ocr_rich_threshold
                            else "vlm_assist_threshold"
                        )
                        snapshot.dim_stats["vlm"] = {
                            "count": 0, "ms": 0, "skipped": reason,
                        }
                        continue
                    collected = vlm.collect(region)
                    if collected:
                        logger.info("VLM assist (%s mode): %d elements from %s",
                                    perception_mode, len(collected), title[:40])
            except Exception as exc:
                logger.warning("感知维度 %s 异常: %s", dim, exc)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            snapshot.dim_stats[dim] = {"count": len(collected), "ms": elapsed_ms}
            fused = _merge(fused, collected) if fused else list(collected)
    else:
        # Normal mode: serial execution (unchanged behaviour).
        for dim in order:
            started = time.monotonic()
            collected = []
            try:
                if dim == "uia":
                    if skip_uia_modal:
                        snapshot.dim_stats["uia"] = {
                            "count": 0, "ms": 0, "skipped": "foreground_modal_overlay",
                        }
                        continue
                    collected = uia.collect(hwnd)
                    uia_count = sum(1 for e in collected if (e.text or "").strip())
                    uia_elements = list(collected)
                    uia_rich = _uia_is_rich(uia_elements, region, uia_rich_threshold)
                elif dim == "win32":
                    collected = win32.collect(hwnd)
                    collected.extend(win32.explorer_items(hwnd))
                elif dim == "ocr":
                    if skip_ocr_if_rich and uia_rich:
                        snapshot.dim_stats["ocr"] = {"count": 0, "ms": 0, "skipped": "uia_rich"}
                        continue
                    collected = ocr.collect(region)
                elif dim == "cv":
                    collected = vision_cv.detect_icon_candidates(region) if include_icons else []
                elif dim == "vlm":
                    if is_visual:
                        skip_vlm = _uia_is_rich(uia_elements, region, vlm_assist_threshold)
                        skip_reason = "vlm_assist_threshold"
                    else:
                        skip_vlm = skip_vlm_if_rich_normal and uia_rich
                        skip_reason = "uia_rich"
                    if skip_vlm:
                        snapshot.dim_stats["vlm"] = {"count": 0, "ms": 0, "skipped": skip_reason}
                        continue
                    collected = vlm.collect(region)
                    if collected:
                        logger.info("VLM assist (%s mode): %d elements from %s",
                                    perception_mode, len(collected), title[:40])
            except Exception as exc:
                logger.warning("感知维度 %s 异常: %s", dim, exc)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            snapshot.dim_stats[dim] = {"count": len(collected), "ms": elapsed_ms}
            if dim == "uia":
                snapshot.dim_stats[dim].update({
                    "content_count": _uia_content_count(collected, region),
                    "rich": uia_rich,
                })
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
        and not (stop_event is not None and stop_event.is_set())
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
