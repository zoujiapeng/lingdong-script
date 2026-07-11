"""Perception and visual-inspection tool handlers for ToolSession."""
from __future__ import annotations

import json
import sys
import time

from ..config import CONFIG, TRACES_DIR
from ..perception.model import UIElement
from ..perception import fusion, intent, ocr, reducer, vision_cv, vlm, win32
from ..perception.ocr_lines import merge_line_elements
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


class PerceptionToolMixin:
    def _perception_mode(self) -> str:
        """Return the perception strategy mode for the current task + window title."""
        title = self.snapshot.title if self.snapshot else ""
        return intent.perception_mode(self.task, title)

    def _vision_priority(self) -> bool:
        """Legacy accessor - True when perception mode is 'visual'."""
        return self._perception_mode() != "normal"

    def _needs_visual_state_summary(self) -> bool:
        text = f"{self.task} {self.snapshot.title if self.snapshot else ''}".casefold()
        needles = (
            "draw", "drawing", "paint", "sketch", "canvas", "whiteboard",
            "\u753b", "\u753b\u56fe", "\u7ed8\u56fe", "\u6d82\u9e26",
            "\u753b\u677f", "\u767d\u677f", "\u989c\u8272", "color",
        )
        return any(needle in text for needle in needles)

    def _capture_window_b64(self, hwnd: int) -> str:
        """Capture the target window as a JPEG when the main model has vision."""
        from ..agent.loop import _main_model_has_vision
        if not _main_model_has_vision():
            return ""
        image_b64 = capture_window_jpeg_b64(hwnd)
        if not image_b64:
            logger.debug("窗口截图捕获失败或返回空图像")
        return image_b64

    def _visual_state_summary(self, hwnd: int) -> str:
        if not self._needs_visual_state_summary():
            return ""
        try:
            region = _tools_attr("window_region", window_region)(hwnd)
            image = _tools_attr("capture", capture)(region)
            canvas_bbox = vision_cv.likely_canvas_bbox(image)
            offset_x = 0
            offset_y = 0
            scope = "current window"
            if canvas_bbox:
                x1, y1, x2, y2 = canvas_bbox
                image = image[y1:y2, x1:x2]
                offset_x = x1
                offset_y = y1
                scope = f"likely_canvas@({region.left + x1},{region.top + y1},{region.left + x2},{region.top + y2})"
            stats = vision_cv.color_presence(image)
        except Exception as exc:
            logger.debug("visual state summary failed: %s", exc)
            return ""
        colors = stats.get("colors", {}) if isinstance(stats, dict) else {}
        visible_parts: list[str] = []
        for name in ("red", "orange", "yellow", "green", "blue", "purple", "black"):
            item = colors.get(name, {})
            if not item.get("visible"):
                continue
            bbox = item.get("largest_bbox") or item.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                sx1, sy1, sx2, sy2 = (
                    int(bbox[0]) + region.left + offset_x,
                    int(bbox[1]) + region.top + offset_y,
                    int(bbox[2]) + region.left + offset_x,
                    int(bbox[3]) + region.top + offset_y,
                )
                visible_parts.append(f"{name}@({sx1},{sy1},{sx2},{sy2})")
            else:
                visible_parts.append(name)
        modal_terms = (
            "PopupHost", "Edit colors", "OK", "Cancel", "RGB",
            "\u786e\u5b9a", "\u53d6\u6d88",
        )
        popup_roles = ("popup", "dialog", "menu", "popover", "\u5bf9\u8bdd", "\u83dc\u5355")
        popup_open = False
        if self.snapshot is not None:
            popup_open = any(
                any(term in (element.text or "") for term in modal_terms)
                or any(role in (element.role or "").casefold() for role in popup_roles)
                for element in self.snapshot.elements[:80]
            )
        colors_text = ", ".join(visible_parts) if visible_parts else "none"
        popup_text = ""
        advisory = ""
        if popup_open and scope == "current window":
            popup_text = (
                "; popup/tool panel appears open; these colors may belong to "
                "the tool panel, not the canvas"
            )
            advisory = (
                "A tool/color modal is foreground. Its visible colors are controls, "
                "not proof of canvas content. Finish with OK/Cancel/Escape or close "
                "the modal, then observe the canvas before drawing or claiming color evidence."
            )
        elif popup_open:
            popup_text = "; popup/tool panel appears open"
            advisory = (
                "A popup/tool panel is open over the canvas. If tool/color selection is done, "
                "close it with OK/Escape or click back on the canvas before drawing. Do not keep "
                "probing the same panel."
            )
        if advisory and advisory != self._last_visual_advisory:
            self._pending_visual_advisory = advisory
            self._last_visual_advisory = advisory
        return (
            f"Visual state summary ({scope}): visible coarse colors: "
            f"{colors_text}{popup_text}. If this contradicts the plan, correct "
            "the next action before continuing."
        )

    def take_visual_advisory(self) -> str:
        note = self._pending_visual_advisory
        self._pending_visual_advisory = ""
        return note

    def _screen_bulletin_summary(self) -> str:
        get_status = getattr(self, "_fast_screen_status", None)
        status = get_status() if callable(get_status) else {}
        if not status:
            return ""
        regions = status.get("changed_regions") or []
        roi = "none"
        if regions:
            first = regions[0]
            roi = (
                f"{int(first.get('left', 0))},{int(first.get('top', 0))},"
                f"{int(first.get('width', 0))}x{int(first.get('height', 0))}"
            )
        recent_regions = status.get("recent_changed_regions") or []
        recent_roi = "none"
        if recent_regions:
            first = recent_regions[0]
            recent_roi = (
                f"{int(first.get('left', 0))},{int(first.get('top', 0))},"
                f"{int(first.get('width', 0))}x{int(first.get('height', 0))}"
            )
        action_regions = status.get("action_changed_regions") or []
        action_roi = "none"
        if action_regions:
            first = action_regions[0]
            action_roi = (
                f"{int(first.get('left', 0))},{int(first.get('top', 0))},"
                f"{int(first.get('width', 0))}x{int(first.get('height', 0))}"
            )
        return (
            "Screen watch bulletin: "
            f"running={bool(status.get('running'))}, "
            f"changed={bool(status.get('last_changed'))}, "
            f"ratio={float(status.get('last_ratio') or 0.0):.4f}, "
            f"roi={roi}, recent_roi={recent_roi}, action_roi={action_roi}, "
            f"recent_age_ms={int(status.get('last_change_age_ms') or 0)}, "
            f"action_age_ms={int(status.get('last_action_change_age_ms') or 0)}, "
            f"samples={int(status.get('samples') or 0)}, "
            f"capture_ms={int(status.get('capture_latency_ms') or 0)}, "
            f"diff_ms={int(status.get('diff_latency_ms') or 0)}."
        )

    @staticmethod
    def _regions_intersect(a: Region, b: Region) -> bool:
        return a.left < b.right and a.right > b.left and a.top < b.bottom and a.bottom > b.top

    @staticmethod
    def _intersect_region(a: Region, b: Region) -> Region | None:
        left = max(a.left, b.left)
        top = max(a.top, b.top)
        right = min(a.right, b.right)
        bottom = min(a.bottom, b.bottom)
        if right <= left or bottom <= top:
            return None
        return Region(left, top, right - left, bottom - top)

    @staticmethod
    def _union_region(regions: list[Region]) -> Region | None:
        if not regions:
            return None
        left = min(region.left for region in regions)
        top = min(region.top for region in regions)
        right = max(region.right for region in regions)
        bottom = max(region.bottom for region in regions)
        return Region(left, top, right - left, bottom - top)

    @staticmethod
    def _element_region(element: UIElement) -> Region:
        return Region(int(element.left), int(element.top),
                      max(1, int(element.width)), max(1, int(element.height)))

    def _recent_changed_regions(self, hwnd: int) -> list[Region]:
        status = self._fast_screen_status() if hasattr(self, "_fast_screen_status") else {}
        if not status:
            return []
        max_age_ms = int(CONFIG.get("fusion", "changed_region_recent_ms", default=30000))
        action_age = int(status.get("last_action_change_age_ms") or 0)
        if (
            status.get("action_changed_regions")
            and (not action_age or action_age <= max_age_ms)
        ):
            raw_regions = status.get("action_changed_regions") or []
        elif status.get("last_changed"):
            raw_regions = status.get("changed_regions") or []
        elif int(status.get("last_change_age_ms") or 0) <= max_age_ms:
            raw_regions = status.get("recent_changed_regions") or []
        else:
            raw_regions = []
        if not raw_regions:
            return []
        try:
            target = _tools_attr("window_region", window_region)(hwnd)
        except Exception:
            return []
        pad = int(CONFIG.get("fusion", "changed_region_pad_px", default=24))
        max_regions = int(CONFIG.get("fusion", "changed_region_max_regions", default=3))
        regions: list[Region] = []
        for item in raw_regions[:max(1, max_regions)]:
            if not isinstance(item, dict):
                continue
            try:
                region = Region(
                    int(item["left"]) - pad,
                    int(item["top"]) - pad,
                    int(item["width"]) + pad * 2,
                    int(item["height"]) + pad * 2,
                )
            except (KeyError, TypeError, ValueError):
                continue
            clipped = self._intersect_region(region, target)
            if clipped is not None:
                regions.append(clipped)
        return regions

    @staticmethod
    def _dedupe_elements(elements: list[UIElement]) -> list[UIElement]:
        kept: list[UIElement] = []
        for cand in elements:
            duplicate = False
            for existing in kept:
                if cand.iou(existing) >= 0.5 and (
                    not cand.text or cand.text == existing.text
                    or (cand.text and cand.text in existing.text)
                ):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(cand)
        return kept

    @staticmethod
    def _merge_ocr_line_elements(elements: list[UIElement]) -> list[UIElement]:
        return merge_line_elements(elements)

    @staticmethod
    def _text_matches_query(query: str, text: str) -> bool:
        matcher = getattr(ocr, "_match_text", None)
        if callable(matcher):
            return bool(matcher(query, text))
        return query.casefold() in text.casefold()

    @classmethod
    def _query_element_is_grounded(
        cls,
        query: str,
        element: UIElement,
        *,
        actionable: bool = False,
    ) -> bool:
        text = str(element.text or "").strip()
        if not text or not cls._text_matches_query(query, text):
            return False
        compact_query = "".join(ch for ch in query.casefold() if ch.isalnum())
        compact_text = "".join(ch for ch in text.casefold() if ch.isalnum())
        if len(compact_text) > max(96, len(compact_query) * 6):
            return False
        if not actionable:
            return True
        role = str(element.role or "").casefold()
        if any(token in role for token in ("button", "link", "menuitem", "tab", "check", "radio")):
            return True
        # OCR labels are allowed because custom-drawn controls often have no
        # native accessibility role; click_element still applies geometry.
        return str(element.source or "").casefold() == "ocr"

    @classmethod
    def _query_grounded_elements(
        cls,
        query: str,
        elements: list[UIElement],
        *,
        actionable: bool = False,
    ) -> list[UIElement]:
        return [
            element for element in elements
            if cls._query_element_is_grounded(query, element, actionable=actionable)
        ]

    def _predictive_ocr_for_regions(
        self,
        hwnd: int,
        regions: list[Region],
    ) -> tuple[list[UIElement], dict]:
        lease = getattr(self, "_perception_lease", None)
        if lease is not None and int(getattr(lease, "hwnd", 0) or 0) == int(hwnd):
            try:
                from ..fast.perception_daemon import PERCEPTION_DAEMON

                wait_ms = int(CONFIG.get(
                    "fusion",
                    "incremental_perception_wait_ms",
                    default=1200,
                ))
                daemon_elements, daemon_meta = PERCEPTION_DAEMON.incremental_elements(
                    int(hwnd),
                    regions=regions,
                    max_age_ms=int(CONFIG.get(
                        "fusion",
                        "changed_region_recent_ms",
                        default=30000,
                    )),
                    wait_ms=wait_ms,
                )
                if daemon_elements:
                    return daemon_elements, daemon_meta
            except Exception:
                logger.debug("incremental perception cache lookup failed", exc_info=True)
                daemon_meta = {}
        else:
            daemon_meta = {}
        lock = getattr(self, "_predictive_ocr_lock", None)
        if lock is None:
            return [], daemon_meta
        with lock:
            cache = dict(getattr(self, "_predictive_ocr_cache", None) or {})
        if not cache:
            return [], daemon_meta
        created_ms = int(cache.get("created_ms") or 0)
        max_age_ms = int(CONFIG.get("fusion", "changed_region_recent_ms", default=30000))
        if created_ms and int(time.monotonic() * 1000) - created_ms > max_age_ms:
            return [], {}
        cached_elements = cache.get("elements") if isinstance(cache.get("elements"), list) else []
        matched = [
            element for element in cached_elements
            if isinstance(element, UIElement)
            and any(self._regions_intersect(self._element_region(element), region) for region in regions)
        ]
        return matched, {
            "hit": bool(matched),
            "source": "session_predictive_ocr",
            "source_elapsed_ms": int(cache.get("elapsed_ms") or 0),
            "transient": bool(cache.get("transient")),
        }

    def _changed_region_snapshot(
        self,
        hwnd: int,
        include_icons: bool,
        regions: list[Region],
    ) -> fusion.Snapshot:
        title = _tools_attr("win32", win32)._window_text(hwnd)
        snapshot_region = self._union_region(regions) or regions[0]
        snapshot = fusion.Snapshot(hwnd=hwnd, title=title, region=snapshot_region)
        elements: list[UIElement] = []

        started = time.monotonic()
        win32_elements: list[UIElement] = []
        try:
            win32_mod = _tools_attr("win32", win32)
            for element in win32_mod.collect(hwnd):
                if any(self._regions_intersect(self._element_region(element), region)
                       for region in regions):
                    if "overlaps_changed_roi" not in element.states:
                        element.states.append("overlaps_changed_roi")
                    win32_elements.append(element)
        except Exception as exc:
            logger.warning("changed-only win32 observe failed: %s", exc)
        snapshot.dim_stats["win32"] = {
            "count": len(win32_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "changed_roi",
        }
        elements.extend(win32_elements)

        started = time.monotonic()
        ocr_elements: list[UIElement] = []
        transient_ocr_count = 0
        predictive_meta: dict = {}
        try:
            ocr_mod = _tools_attr("ocr", ocr)
            controller = getattr(self, "_fast_gui_controller", None)
            watcher = getattr(controller, "watcher", None)
            predictive_elements, predictive_meta = self._predictive_ocr_for_regions(
                hwnd,
                regions,
            )
            if predictive_elements:
                ocr_elements.extend(predictive_elements)
                transient_ocr_count += sum(
                    1 for element in predictive_elements
                    if "transient_action_evidence" in element.states
                    and "ocr_line_merge" not in element.states
                )
            evidence_reader = getattr(watcher, "action_evidence", None)
            if not predictive_elements and callable(evidence_reader):
                evidence_crops, is_transient = evidence_reader(
                    int(CONFIG.get("fusion", "changed_region_recent_ms", default=30000))
                )
                for image, evidence_region in evidence_crops:
                    if not any(self._regions_intersect(evidence_region, changed) for changed in regions):
                        continue
                    recognized = ocr_mod.recognize(
                        image,
                        origin=(evidence_region.left, evidence_region.top),
                    )
                    recognized_with_lines = [
                        *recognized,
                        *self._merge_ocr_line_elements(recognized),
                    ]
                    if is_transient:
                        for element in recognized_with_lines:
                            if "transient_action_evidence" not in element.states:
                                element.states.append("transient_action_evidence")
                    transient_ocr_count += len(recognized)
                    ocr_elements.extend(recognized_with_lines)
            prefer_cache = bool(CONFIG.get("fusion", "predictive_ocr_prefer_cache", default=True))
            if not predictive_elements or not prefer_cache:
                for region in regions:
                    current_ocr = ocr_mod.collect(region)
                    ocr_elements.extend(current_ocr)
                    ocr_elements.extend(self._merge_ocr_line_elements(current_ocr))
        except Exception as exc:
            logger.warning("changed-only OCR observe failed: %s", exc)
        snapshot.dim_stats["ocr"] = {
            "count": len(ocr_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "changed_roi",
            "transient_evidence_count": transient_ocr_count,
            "predictive_cache_hit": bool(predictive_meta.get("hit")),
            "predictive_source": str(predictive_meta.get("source") or ""),
            "predictive_source_ms": int(predictive_meta.get("source_elapsed_ms") or 0),
            "predictive_inflight": bool(predictive_meta.get("inflight")),
            "predictive_record_count": int(predictive_meta.get("record_count") or 0),
        }
        elements.extend(ocr_elements)

        started = time.monotonic()
        cv_elements: list[UIElement] = []
        if include_icons:
            try:
                for region in regions:
                    cv_elements.extend(vision_cv.detect_icon_candidates(region))
            except Exception as exc:
                logger.warning("changed-only CV observe failed: %s", exc)
        snapshot.dim_stats["cv"] = {
            "count": len(cv_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "changed_roi",
        }
        elements.extend(cv_elements)

        elements = self._dedupe_elements(elements)
        for idx, element in enumerate(elements, start=1):
            element.elem_id = idx
        snapshot.elements = elements
        BUS.publish(
            "perception",
            run_id=getattr(self, "run_id", None),
            hwnd=hwnd,
            title=title,
            dims=snapshot.dim_stats,
            total=len(elements),
        )
        return snapshot

    def _canvas_fast_observe_enabled(self, args: dict, perception_mode: str) -> bool:
        """Use a light visual snapshot for UIA-off canvas/drawing tasks.

        Canvas tasks usually need the work-area bbox and coarse visual state,
        not a full-window OCR pass over every toolbar label. Query/full observe
        still forces the complete path when text grounding is actually needed.
        """
        if perception_mode != "visual":
            return False
        if not self._needs_visual_state_summary():
            return False
        if str(args.get("query", "") or "").strip():
            return False
        if bool(args.get("full") or args.get("force_full")):
            return False
        if not bool(CONFIG.get("fusion", "canvas_fast_observe", default=True)):
            return False
        # Keep UIA-enabled behaviour conservative; the fast path is primarily
        # for UIA-off testing/custom surfaces where OCR is the dominant cost.
        return "uia" not in set(CONFIG.enabled_dimensions())

    def _fast_canvas_snapshot(self, hwnd: int, include_icons: bool) -> fusion.Snapshot:
        region = _tools_attr("window_region", window_region)(hwnd)
        title = _tools_attr("win32", win32)._window_text(hwnd)
        snapshot = fusion.Snapshot(hwnd=hwnd, title=title, region=region)
        order = CONFIG.enabled_dimensions()
        fused = []
        for dim in order:
            if dim not in {"win32", "cv"}:
                snapshot.dim_stats[dim] = {
                    "count": 0,
                    "ms": 0,
                    "skipped": "canvas_fast_observe",
                }
                continue
            started = time.monotonic()
            collected = []
            try:
                if dim == "win32":
                    win32_mod = _tools_attr("win32", win32)
                    collected = win32_mod.collect(hwnd)
                    collected.extend(win32_mod.explorer_items(hwnd))
                elif dim == "cv":
                    collected = vision_cv.detect_icon_candidates(region) if include_icons else []
            except Exception as exc:
                logger.warning("fast canvas observe %s failed: %s", dim, exc)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            snapshot.dim_stats[dim] = {"count": len(collected), "ms": elapsed_ms}
            fused.extend(collected)
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

    def _snapshot_debug_payload(
        self,
        snapshot: fusion.Snapshot,
        reduction: dict,
        observe_profile: str,
        query: str,
        args: dict,
    ) -> dict:
        return {
            "hwnd": int(snapshot.hwnd),
            "title": snapshot.title,
            "region": self._region_dict(snapshot.region),
            "observe_profile": observe_profile,
            "query": query,
            "args": {k: v for k, v in args.items() if k != "image_b64"},
            "dim_stats": snapshot.dim_stats,
            "reduction": reduction,
            "elements": [element.to_dict() for element in snapshot.elements],
        }

    def _write_perception_trace(
        self,
        snapshot: fusion.Snapshot,
        reduction: dict,
        observe_profile: str,
        query: str,
        args: dict,
    ) -> str:
        """Persist full observe data outside the LLM hot path."""
        if not CONFIG.get("agent", "llm_trace", default=True):
            return ""
        run_id = str(getattr(self, "run_id", "") or "")
        if not run_id:
            return ""
        try:
            index = int(getattr(self, "_perception_trace_index", 0)) + 1
            self._perception_trace_index = index
            run_dir = TRACES_DIR / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            path = run_dir / f"perception_{index:03d}_{observe_profile}.json"
            path.write_text(
                json.dumps(
                    self._snapshot_debug_payload(snapshot, reduction, observe_profile, query, args),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            return str(path.relative_to(TRACES_DIR))
        except Exception:
            logger.debug("perception trace write failed", exc_info=True)
            return ""

    def _render_observe_text(
        self,
        snapshot: fusion.Snapshot,
        args: dict,
        observe_profile: str,
        query: str,
        max_elements: int,
    ) -> tuple[str, dict, str]:
        recent_changes = self._fast_screen_status() if hasattr(self, "_fast_screen_status") else {}
        max_candidates = int(CONFIG.get("fusion", "perception_reducer_max_candidates", default=12) or 12)
        reduction = reducer.reduce_snapshot(
            snapshot,
            task=self.task,
            query=query,
            recent_changes=recent_changes,
            max_candidates=max_candidates,
        )
        trace_rel = self._write_perception_trace(snapshot, reduction, observe_profile, query, args)
        full_requested = bool(args.get("full") or args.get("force_full"))
        reducer_enabled = bool(CONFIG.get("fusion", "perception_reducer", default=True))
        raw_text = snapshot.to_text(max_elements=max_elements, query=query)
        if not reducer_enabled or full_requested:
            if trace_rel:
                raw_text += f"\nPerception trace: {trace_rel}"
            return raw_text, reduction, trace_rel
        raw_sample_limit = int(CONFIG.get("fusion", "perception_reducer_raw_sample", default=40) or 40)
        raw_sample_limit = max(1, min(max_elements, raw_sample_limit))
        reduced_text = reducer.render_reduced_snapshot(
            snapshot,
            task=self.task,
            query=query,
            recent_changes=recent_changes,
            max_candidates=max_candidates,
        )
        text = (
            reduced_text
            + "\nRaw element sample (debug; pass full=true for all raw elements):\n"
            + snapshot.to_text(max_elements=raw_sample_limit, query=query)
        )
        if trace_rel:
            text += f"\nPerception trace: {trace_rel}"
        return text, reduction, trace_rel

    def _t_observe(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无法确定目标窗口，请先 list_windows + focus_window")
        include_icons = bool(args.get("include_icons", True))
        fusion_mod = _tools_attr("fusion", fusion)
        perception_mode = self._perception_mode()
        changed_regions = self._recent_changed_regions(hwnd) if bool(args.get("changed_only")) else []
        fast_canvas = self._canvas_fast_observe_enabled(args, perception_mode)
        observe_profile = "changed_roi" if changed_regions else ("canvas_fast" if fast_canvas else "full")
        roi_signature = tuple(
            (region.left, region.top, region.width, region.height)
            for region in changed_regions
        )
        cache_key = (
            int(hwnd),
            bool(include_icons),
            perception_mode,
            observe_profile,
            roi_signature,
            self._screen_epoch,
        )
        cache_ttl = float(CONFIG.get("fusion", "observe_cache_ttl_s", default=2.0))
        cached = self.cached_observe(cache_key, cache_ttl) if cache_ttl > 0 else None
        if cached is not None:
            self.snapshot = cached
            self.snapshot.dim_stats["cache"] = {"count": len(self.snapshot.elements), "ms": 0}
        else:
            if changed_regions:
                self.snapshot = self._changed_region_snapshot(
                    hwnd,
                    include_icons=include_icons,
                    regions=changed_regions,
                )
            elif fast_canvas:
                self.snapshot = self._fast_canvas_snapshot(hwnd, include_icons=include_icons)
            else:
                self.snapshot = fusion_mod.observe(hwnd, include_icons=include_icons,
                                                   perception_mode=perception_mode)
            self.store_observe_cache(cache_key, self.snapshot)
        self._remember_target(hwnd, self.snapshot.title)
        query = str(args.get("query", "") or "")
        max_elements = int(args.get("max_elements", 160) or 160)
        query_norm = query.strip().casefold()
        actionable_query = bool(args.get("actionable_query"))
        changed_query_fallback = False
        if changed_regions and query_norm and not self._query_grounded_elements(
            query_norm,
            self.snapshot.elements,
            actionable=actionable_query,
        ):
            full_cache_key = (
                int(hwnd),
                bool(include_icons),
                perception_mode,
                "full_query_fallback",
                (),
                self._screen_epoch,
            )
            cached = self.cached_observe(full_cache_key, cache_ttl) if cache_ttl > 0 else None
            if cached is not None:
                self.snapshot = cached
                self.snapshot.dim_stats["cache"] = {"count": len(self.snapshot.elements), "ms": 0}
            else:
                self.snapshot = fusion_mod.observe(
                    hwnd,
                    include_icons=include_icons,
                    perception_mode=perception_mode,
                )
                self.store_observe_cache(full_cache_key, self.snapshot)
            self._remember_target(hwnd, self.snapshot.title)
            changed_query_fallback = True
        ocr_stats = self.snapshot.dim_stats.get("ocr") or {}
        full_ocr_already_ran = bool(
            ocr_stats
            and not ocr_stats.get("skipped")
            and not ocr_stats.get("error")
            and ocr_stats.get("scope") != "changed_roi"
        )
        if (
            query_norm
            and not full_ocr_already_ran
            and not self._query_grounded_elements(
                query_norm,
                self.snapshot.elements,
                actionable=actionable_query,
            )
        ):
            query_ocr_started = time.monotonic()
            query_hits: list[UIElement] = []
            try:
                target_region = _tools_attr("window_region", window_region)(hwnd)
                ocr_mod = _tools_attr("ocr", ocr)
                full_ocr = ocr_mod.collect(target_region)
                full_ocr.extend(self._merge_ocr_line_elements(full_ocr))
                query_hits = [
                    element for element in full_ocr
                    if self._query_element_is_grounded(
                        query_norm,
                        element,
                        actionable=actionable_query,
                    )
                ]
                for element in query_hits:
                    if "query_directed_ocr" not in element.states:
                        element.states.append("query_directed_ocr")
                if query_hits:
                    self.snapshot.elements = self._dedupe_elements([
                        *self.snapshot.elements,
                        *query_hits,
                    ])
                    for index, element in enumerate(self.snapshot.elements, start=1):
                        element.elem_id = index
            except Exception as exc:
                logger.warning("query-directed OCR fallback failed: %s", exc)
            self.snapshot.dim_stats["ocr_query"] = {
                "count": len(query_hits),
                "ms": int((time.monotonic() - query_ocr_started) * 1000),
                "scope": "full_query_fallback",
            }
        render_profile = "full_query_fallback" if changed_query_fallback else observe_profile
        text, _reduction, _trace_rel = self._render_observe_text(
            self.snapshot,
            args=args,
            observe_profile=render_profile,
            query=query,
            max_elements=max_elements,
        )
        if args.get("changed_only"):
            if changed_query_fallback:
                rois = "; ".join(
                    f"{region.left},{region.top},{region.width}x{region.height}"
                    for region in changed_regions
                )
                text = (
                    "Changed-only observe query fallback: recent screen_watch "
                    f"ROI(s) did not contain query \"{query_norm}\" ({rois}); "
                    "returned a full observe snapshot.\n" + text
                )
            elif changed_regions:
                rois = "; ".join(
                    f"{region.left},{region.top},{region.width}x{region.height}"
                    for region in changed_regions
                )
                text = (
                    "Changed-only observe: OCR/CV/Win32 filtered to recent "
                    f"screen_watch ROI(s): {rois}.\n" + text
                )
            else:
                text = (
                    "Changed-only observe requested but no recent changed ROI was "
                    "available; returned the normal observe snapshot.\n" + text
                )
        visual_summary = self._visual_state_summary(hwnd)
        if visual_summary:
            text = text + "\n" + visual_summary
        screen_summary = self._screen_bulletin_summary()
        if screen_summary:
            text = text + "\n" + screen_summary
        summary = (
            f'[已折叠] observe "{self.snapshot.title}" '
            f"{len(self.snapshot.elements)}个元素"
        )
        image_b64 = self._capture_window_b64(hwnd)
        return ToolResult(text, is_observe=True, summary=summary,
                          image_b64=image_b64 if image_b64 else "")

    def _t_read_text(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无目标窗口")
        fusion_mod = _tools_attr("fusion", fusion)
        perception_mode = self._perception_mode()
        cache_keys = [
            (int(hwnd), include_icons, perception_mode, "full", (), self._screen_epoch)
            for include_icons in (False, True)
        ]
        cache_ttl = float(CONFIG.get("fusion", "observe_cache_ttl_s", default=2.0))
        snapshot = None
        if cache_ttl > 0:
            for cache_key in cache_keys:
                snapshot = self.cached_observe(cache_key, cache_ttl)
                if snapshot is not None:
                    break
        cache_hit = snapshot is not None
        if snapshot is None:
            snapshot = fusion_mod.observe(
                hwnd,
                include_icons=False,
                perception_mode=perception_mode,
            )
            self.store_observe_cache(cache_keys[0], snapshot)
        else:
            dim_stats = getattr(snapshot, "dim_stats", None)
            if isinstance(dim_stats, dict):
                dim_stats["cache"] = {
                    "count": len(snapshot.elements),
                    "ms": 0,
                    "profile": "read_text",
                }
        dim_stats = getattr(snapshot, "dim_stats", None)
        ocr_stats = dim_stats.get("ocr") if isinstance(dim_stats, dict) else None
        if not isinstance(ocr_stats, dict) or ocr_stats.get("skipped") or ocr_stats.get("error"):
            try:
                region = getattr(snapshot, "region", None) or _tools_attr(
                    "window_region", window_region
                )(hwnd)
                supplemental = _tools_attr("ocr", ocr).collect(region)
                snapshot.elements = [*snapshot.elements, *supplemental]
            except Exception as exc:
                logger.warning("read_text supplemental OCR failed: %s", exc)
        self.snapshot = snapshot
        title = str(getattr(snapshot, "title", "") or "")
        self._remember_target(hwnd, title)
        texts = [e.text for e in snapshot.elements if e.text.strip()]
        body = "\n".join(dict.fromkeys(texts))
        source = "cache" if cache_hit else "fusion"
        return ToolResult(body or "（未识别到文字）",
                          summary=f"[已折叠] read_text {len(texts)}条文字 source={source}")

    def _t_find_image(self, args: dict) -> ToolResult:
        matches = vision_cv.match_template(str(args["template"]))
        if not matches:
            available = vision_cv.list_templates()
            listing = ("可用模板: " + ", ".join(available)) if available else (
                "模板库为空——可用 capture_template 先截存一个图标区域作为模板")
            return ToolResult(f"未找到匹配（检查模板名/阈值）。{listing}")
        lines = [
            f'匹配 "{m.text}" 中心({m.cx},{m.cy}) 置信度{m.confidence:.2f}'
            for m in matches
        ]
        return ToolResult("\n".join(lines))

    def _t_capture_template(self, args: dict) -> ToolResult:
        region = Region(int(args["left"]), int(args["top"]),
                        int(args["width"]), int(args["height"]))
        if region.width < 8 or region.height < 8 or region.width > 600 or region.height > 600:
            return ToolResult("模板区域不合理（宽高需在 8~600 像素之间，应只框住目标图标/按钮）")
        path = vision_cv.save_template(region, str(args["name"]))
        BUS.publish("action", action="capture_template", name=path.stem,
                    size=f"{region.width}x{region.height}")
        return ToolResult(f"已保存模板 \"{path.stem}\"，之后可用 find_image(\"{path.stem}\") 查找")

    def _t_color_probe(self, args: dict) -> ToolResult:
        probe = vision_cv.color_probe(int(args["x"]), int(args["y"]))
        return ToolResult(self._fmt(probe))

    def _t_vlm_describe(self, args: dict) -> ToolResult:
        if not vlm.is_configured():
            return ToolResult("VLM 未配置")
        if all(k in args for k in ("left", "top", "width", "height")):
            region = Region(int(args["left"]), int(args["top"]),
                            int(args["width"]), int(args["height"]))
        else:
            hwnd = self._resolve_hwnd(args)
            if not hwnd:
                return ToolResult("无目标窗口且未指定区域")
            region = window_region(hwnd)
        answer = vlm.describe(region, str(args["question"]))
        return ToolResult(answer)
