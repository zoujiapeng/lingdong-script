"""Perception and visual-inspection tool handlers for ToolSession."""
from __future__ import annotations

import json
import sys
import time

from ..config import CONFIG, TRACES_DIR
from ..perception.model import UIElement
from ..perception import fusion, intent, ocr, reducer, uia, vision_cv, vlm, win32
from ..perception.ocr_lines import merge_line_elements
from ..utils.image_capture import capture_window_jpeg_b64, select_visual_capture_scope
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from .task_rules import _communication_mode, _is_communication_task, _quoted_payloads
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


def _suppress_nonblocking_notice_lines(text: str, notice: dict) -> str:
    """Hide grounded non-modal status noise from the model-facing view."""
    if not isinstance(notice, dict) or notice.get("blocking") is not False:
        return text
    labels = [
        str(value or "").strip().casefold()
        for value in (notice.get("suppressed_texts") or [])
        if str(value or "").strip()
    ]
    if not labels:
        return text
    kept: list[str] = []
    hidden = 0
    for line in str(text or "").splitlines():
        folded = line.casefold()
        if any(label in folded for label in labels):
            hidden += 1
            continue
        kept.append(line)
    if hidden:
        kept.append(
            f"Perception note: suppressed {hidden} grounded non-blocking/stale status element(s); "
            "the complete raw elements remain in the perception trace."
        )
    return "\n".join(kept)


class PerceptionToolMixin:
    def _maybe_auto_execute_grounded_communication(
        self,
        reduction: dict,
    ) -> ToolResult | None:
        """Return a grounded proposal without mutating the desktop.

        ``observe`` used to send a message from inside this read path.  That
        violated the most important controller invariant: perception must not
        have side effects, and the visual model never got a chance to inspect
        the proposed composer/send target.  Keep the generic proposal for the
        next decision turn, but execution now requires an explicit tool call.
        """
        if not _is_communication_task(self.task_intent) or _communication_mode(self.task_intent) != "send":
            return None
        payloads = _quoted_payloads(self.task)
        if not payloads:
            return None
        chat_context = reduction.get("chat_context") if isinstance(reduction, dict) else None
        if not isinstance(chat_context, dict) or not chat_context.get("center"):
            return None
        payload = str(payloads[0] or "").strip()
        if not payload:
            return None
        routine_args = {
            "subgoal": "send the user's explicit message in the grounded conversation",
            "target_window": {"hwnd": int(getattr(self, "hwnd", 0) or 0)},
            "input": {
                "label": "message",
                "fallback_text": ["输入消息", "消息", "message"],
                "expected_region": "bottom-right",
                "text": payload,
                "clear": True,
                "expect_text": True,
            },
            "click": {
                "label": "发送",
                "fallback_text": ["Send", "send"],
                "expected_region": "bottom-right",
            },
            "verify_texts": [payload],
            "constraints": {"wait": True},
        }
        BUS.publish(
            "controller_proposal",
            run_id=getattr(self, "run_id", None),
            source="observe:grounded_communication_proposal",
            tool="execute_subgoal",
            args=routine_args,
        )
        return ToolResult(json.dumps({
            "ok": True,
            "executed": False,
            "proposal_only": True,
            "recommended_tool": "execute_subgoal",
            "arguments": routine_args,
            "reason": (
                "A possible conversation/composer geometry was inferred, but observe is "
                "read-only. Inspect the current real frame and explicitly approve or "
                "replace this proposal in the next visual transaction."
            ),
        }, ensure_ascii=False))

    def _perception_mode(self) -> str:
        """Return the perception strategy mode for the current task + window title."""
        title = self.snapshot.title if self.snapshot else ""
        return intent.perception_mode(self.task_intent, title)

    def _vision_priority(self) -> bool:
        """Legacy accessor - True when perception mode is 'visual'."""
        return self._perception_mode() != "normal"

    def _capture_window_b64(self, hwnd: int, args: dict | None = None) -> str:
        """Capture the configured visual scope when the main model has vision.

        ``observe(image_region=...)`` is a generic, model-selected zoom.  It
        maps coordinates from the current authoritative submitted image back
        to physical screen pixels, captures that crop atomically, and records
        the exact crop so AgentRunner can make it the next authoritative frame.
        No OCR/UIA result is used to choose the crop.
        """
        from ..agent.loop import _main_model_has_vision
        if not _main_model_has_vision():
            return ""
        decision = select_visual_capture_scope(
            hwnd,
            enabled=None,
            related_window=getattr(self, "_window_related_to_target", None),
        )
        region = decision.region
        args = args if isinstance(args, dict) else {}
        image_region = args.get("image_region")
        physical_region = args.get("region")
        if isinstance(image_region, dict):
            frame_region = getattr(self, "_visual_frame_region", None)
            submitted = getattr(self, "_visual_submitted_size", None)
            try:
                frame_left = int(frame_region.get("left"))
                frame_top = int(frame_region.get("top"))
                frame_width = int(frame_region.get("width"))
                frame_height = int(frame_region.get("height"))
                submitted_width = int(submitted[0])
                submitted_height = int(submitted[1])
                x = float(image_region.get("x"))
                y = float(image_region.get("y"))
                width = float(image_region.get("width"))
                height = float(image_region.get("height"))
                if (
                    frame_width > 0 and frame_height > 0
                    and submitted_width > 0 and submitted_height > 0
                    and width > 0 and height > 0
                ):
                    x1 = max(0.0, min(float(submitted_width), x))
                    y1 = max(0.0, min(float(submitted_height), y))
                    x2 = max(x1 + 1.0, min(float(submitted_width), x + width))
                    y2 = max(y1 + 1.0, min(float(submitted_height), y + height))
                    region = Region(
                        frame_left + int(round(x1 * frame_width / submitted_width)),
                        frame_top + int(round(y1 * frame_height / submitted_height)),
                        max(1, int(round((x2 - x1) * frame_width / submitted_width))),
                        max(1, int(round((y2 - y1) * frame_height / submitted_height))),
                    )
            except (TypeError, ValueError, AttributeError, IndexError, ZeroDivisionError):
                logger.debug("invalid observe image_region; using normal capture scope")
        elif isinstance(physical_region, dict):
            try:
                left = int(physical_region.get("x", physical_region.get("left")))
                top = int(physical_region.get("y", physical_region.get("top")))
                width = int(physical_region.get("width"))
                height = int(physical_region.get("height"))
                if width > 0 and height > 0:
                    region = Region(left, top, width, height)
            except (TypeError, ValueError):
                logger.debug("invalid observe physical region; using normal capture scope")
        image_b64 = capture_window_jpeg_b64(hwnd, region=region)
        if not image_b64:
            logger.debug("窗口截图捕获失败或返回空图像")
        elif region is not None:
            # The agent loop consumes this exact metadata when it attaches the
            # observe image.  Never pair an older JPEG with a later window
            # rectangle: window moves/resizes would corrupt normalized clicks.
            self._last_native_frame_capture = {
                "image_b64": image_b64,
                "hwnd": int(hwnd),
                "region": region,
                "scope": decision.scope,
                "scope_reason": (
                    "explicit_visual_roi"
                    if isinstance(image_region, dict) or isinstance(physical_region, dict)
                    else decision.reason
                ),
            }
        return image_b64

    def _visual_state_summary(self, hwnd: int) -> str:
        """Compatibility hook; visual state is carried by the real frame/fusion.

        Do not derive a second task-keyword-specific view of the screenshot.
        Native vision consumes the authoritative pixels directly, while
        text-only models receive the normal OCR/CV/VLM fusion result.
        """
        del hwnd
        return ""

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
                    default=250,
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

    def _native_visual_direct_enabled(self, args: dict) -> bool:
        """Return whether the main VLM can consume the real frame directly.

        A native multimodal model does not need the same screenshot decomposed
        into several lossy, sometimes contradictory UIA/OCR/VLM summaries.
        ``observe`` is therefore a visual refresh even when the caller supplies
        a question-like query.  Structured perception remains an explicit
        controller path rather than an automatic recovery from visual doubt.
        """
        if not bool(CONFIG.get("fusion", "native_vision_direct", default=True)):
            return False
        if bool(args.get("full") or args.get("force_full") or args.get("changed_only")):
            return False
        try:
            from .loop import _main_model_has_vision

            return bool(_main_model_has_vision())
        except Exception:
            return False

    def _fast_native_visual_snapshot(self, hwnd: int) -> fusion.Snapshot:
        """Build only window geometry; the paired JPEG is the observation."""
        started = time.monotonic()
        region = _tools_attr("window_region", window_region)(hwnd)
        title = _tools_attr("win32", win32)._window_text(hwnd)
        snapshot = fusion.Snapshot(hwnd=hwnd, title=title, region=region)
        for dim in CONFIG.enabled_dimensions():
            snapshot.dim_stats[dim] = {
                "count": 0,
                "ms": 0,
                "skipped": "native_vision_direct",
            }
        snapshot.dim_stats["native_frame"] = {
            "count": 1,
            "ms": int((time.monotonic() - started) * 1000),
            "authoritative": True,
        }
        BUS.publish(
            "perception",
            run_id=getattr(self, "run_id", None),
            hwnd=hwnd,
            title=title,
            dims=snapshot.dim_stats,
            total=0,
            mode="native_vision_direct",
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
            communication_context=getattr(
                self, "communication_context", None,
            ),
        )
        trace_rel = self._write_perception_trace(snapshot, reduction, observe_profile, query, args)
        full_requested = bool(args.get("full") or args.get("force_full"))
        full_downgraded = False
        full_count = 0
        full_limit = int(CONFIG.get(
            "fusion", "full_observe_repeat_limit", default=2,
        ) or 2)
        if full_requested and not bool(args.get("force_full")):
            counts = getattr(self, "_full_observe_counts", None)
            if not isinstance(counts, dict):
                counts = {}
                self._full_observe_counts = counts
            key = int(snapshot.hwnd)
            full_count = int(counts.get(key, 0) or 0) + 1
            counts[key] = full_count
            if full_count > max(1, full_limit):
                full_requested = False
                full_downgraded = True
        reducer_enabled = bool(CONFIG.get("fusion", "perception_reducer", default=True))
        raw_text = snapshot.to_text(max_elements=max_elements, query=query)
        notice = reduction.get("non_blocking_notice") if isinstance(reduction, dict) else {}
        raw_text = _suppress_nonblocking_notice_lines(raw_text, notice)
        if not reducer_enabled or full_requested:
            if isinstance(notice, dict) and notice.get("blocking") is False and notice.get("guidance"):
                raw_text = (
                    "Perception guardrail (grounded from the current editable surface): "
                    + str(notice["guidance"])
                    + "\n"
                    + raw_text
                )
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
            + _suppress_nonblocking_notice_lines(
                snapshot.to_text(max_elements=raw_sample_limit, query=query),
                notice,
            )
        )
        if full_downgraded:
            text = (
                "Repeated full observe was downgraded to the compact reducer "
                f"after {full_limit} full snapshot(s) for this window "
                f"(request #{full_count}). The complete raw snapshot remains in trace; "
                "use force_full=true only for a specific unresolved ambiguity.\n"
                + text
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
        native_visual = self._native_visual_direct_enabled(args)
        perception_mode = "native_visual" if native_visual else self._perception_mode()
        # Domain controllers sometimes need a cheap structural snapshot and
        # then perform obligation-scoped OCR themselves.  Keep this private to
        # deterministic controller calls so the model-facing observe contract
        # does not acquire another policy knob.
        controller_mode = str(args.get("_controller_perception_mode") or "").strip()
        if controller_mode == "structured_ui":
            perception_mode = controller_mode
        changed_regions = self._recent_changed_regions(hwnd) if bool(args.get("changed_only")) else []
        observe_profile = (
            "changed_roi"
            if changed_regions
            else "native_visual"
            if native_visual
            else "full"
        )
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
            elif native_visual:
                self.snapshot = self._fast_native_visual_snapshot(hwnd)
            else:
                self.snapshot = fusion_mod.observe(
                    hwnd,
                    include_icons=include_icons,
                    perception_mode=perception_mode,
                    stop_event=getattr(self, "stop_event", None),
                )
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
                    stop_event=getattr(self, "stop_event", None),
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
            not native_visual
            and query_norm
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
        text, reduction, _trace_rel = self._render_observe_text(
            self.snapshot,
            args=args,
            observe_profile=render_profile,
            query=query,
            max_elements=max_elements,
        )
        if native_visual:
            text = (
                "Native visual direct observation: the attached real GUI frame is "
                "authoritative. Structured UIA/OCR/VLM decomposition was intentionally "
                "skipped.\n" + text
            )
        action_proposal = (
            None
            if native_visual
            else self._maybe_auto_execute_grounded_communication(reduction)
        )
        if action_proposal is not None:
            try:
                proposal_data = json.loads(action_proposal.text or "{}")
            except (json.JSONDecodeError, TypeError):
                proposal_data = {"ok": False, "error": action_proposal.text or "invalid proposal"}
            text += (
                "\nNon-mutating visual action proposal (nothing was executed):\n"
                + json.dumps(proposal_data, ensure_ascii=False, default=str)[:1600]
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
        screen_summary = self._screen_bulletin_summary()
        if screen_summary:
            text = text + "\n" + screen_summary
        summary = (
            f'[已折叠] observe "{self.snapshot.title}" '
            f"{len(self.snapshot.elements)}个元素"
        )
        if isinstance(args, dict) and (
            isinstance(args.get("image_region"), dict)
            or isinstance(args.get("region"), dict)
        ):
            image_b64 = self._capture_window_b64(hwnd, args)
        else:
            # Keep the one-argument hook compatible with existing ToolSession
            # doubles and integrations that replace the capture helper.
            image_b64 = self._capture_window_b64(hwnd)
        return ToolResult(text, is_observe=True, summary=summary,
                          image_b64=image_b64 if image_b64 else "")

    def _t_read_text(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无目标窗口")
        query = str(args.get("query") or "").strip()
        require_non_input = bool(args.get("require_non_input"))
        if query and "uia" in CONFIG.enabled_dimensions():
            query_started = time.monotonic()
            try:
                direct = _tools_attr("uia", uia).find_text(int(hwnd), query)
                direct = self._query_grounded_elements(query, direct)
                if require_non_input:
                    direct = [
                        element for element in direct
                        if not any(
                            token in str(getattr(element, "role", "") or "").casefold()
                            for token in ("edit", "textbox", "input", "combobox", "searchbox")
                        )
                    ]
                texts = [
                    str(getattr(element, "text", "") or "").strip()
                    for element in direct
                    if str(getattr(element, "text", "") or "").strip()
                ]
                if texts:
                    elapsed_ms = int((time.monotonic() - query_started) * 1000)
                    self._remember_target(int(hwnd), "")
                    BUS.publish(
                        "verify",
                        run_id=getattr(self, "run_id", None),
                        action="read_text_query",
                        attempt=1,
                        verified=True,
                        detail=f'found visible text "{query}"',
                        method="uia-query",
                        elapsed_ms=elapsed_ms,
                        trace={
                            "query": query,
                            "match_count": len(texts),
                            "require_non_input": require_non_input,
                        },
                    )
                    body = "\n".join(dict.fromkeys(texts))
                    return ToolResult(
                        body,
                        summary=f'[folded] read_text query="{query}" {len(texts)} texts source=uia-query',
                        replay={
                            "tool": "read_text",
                            "query": query,
                            "source": "uia-query",
                            "elapsed_ms": elapsed_ms,
                        },
                    )
            except Exception as exc:
                logger.debug("read_text UIA query fast path failed: %s", exc)
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
        selected = snapshot.elements
        if query:
            selected = self._query_grounded_elements(query, selected)
            if require_non_input:
                selected = [
                    element for element in selected
                    if not any(
                        token in str(getattr(element, "role", "") or "").casefold()
                        for token in ("edit", "textbox", "input", "combobox", "searchbox")
                    )
                ]
        texts = [e.text for e in selected if e.text.strip()]
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

    def _vlm_capture_region(
        self,
        args: dict,
    ) -> tuple[Region, dict[str, object], int | None] | None:
        """Resolve the exact pixels a text-only + VLM tool may inspect.

        An explicit ROI is an intentional caller constraint and therefore is
        never widened or replaced. Otherwise VLM tools share the same safe
        selector as native visual frames: the setting may disable cropping,
        and ambiguous, background, cross-application, or multi-window cases
        fall back to the full virtual desktop.
        """
        if all(k in args for k in ("left", "top", "width", "height")):
            region = Region(
                int(args["left"]),
                int(args["top"]),
                int(args["width"]),
                int(args["height"]),
            )
            return region, {
                "capture_scope": "explicit_roi",
                "capture_scope_reason": "caller_explicit_region",
                "capture_region": [
                    region.left,
                    region.top,
                    region.width,
                    region.height,
                ],
                "target_verified": False,
            }, None

        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return None
        decision = select_visual_capture_scope(
            hwnd,
            task=self.task,
            related_window=getattr(self, "_window_related_to_target", None),
        )
        return decision.region, {
            "capture_scope": decision.scope,
            "capture_scope_reason": decision.reason,
            "capture_region": [
                decision.region.left,
                decision.region.top,
                decision.region.width,
                decision.region.height,
            ],
            "target_hwnd": decision.target_hwnd,
            "target_verified": decision.target_verified,
        }, int(hwnd)

    def _record_vlm_capture_scope(
        self,
        tool: str,
        metadata: dict[str, object],
        hwnd: int | None,
    ) -> None:
        BUS.publish(
            "perception",
            run_id=getattr(self, "run_id", None),
            mode=tool,
            hwnd=int(hwnd or 0) or None,
            capture_scope=metadata["capture_scope"],
            capture_scope_reason=metadata["capture_scope_reason"],
            capture_region=metadata["capture_region"],
            target_verified=bool(metadata.get("target_verified")),
        )

    def _t_vlm_describe(self, args: dict) -> ToolResult:
        if not vlm.is_configured():
            return ToolResult("VLM 未配置")
        capture_scope = self._vlm_capture_region(args)
        if capture_scope is None:
            return ToolResult("无目标窗口且未指定区域")
        region, metadata, hwnd = capture_scope
        self._record_vlm_capture_scope("vlm_describe", metadata, hwnd)
        answer = vlm.describe(region, str(args["question"]))
        scope_note = (
            f"[capture_scope={metadata['capture_scope']}; "
            f"reason={metadata['capture_scope_reason']}; "
            f"region={metadata['capture_region']}]"
        )
        return ToolResult(
            f"{scope_note}\n{answer}",
            replay={"tool": "vlm_describe", **metadata},
        )

    def _t_vlm_verify(self, args: dict) -> ToolResult:
        if not vlm.is_configured():
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "semantic_visual_verified": False,
                "verify_method": "semantic-vlm",
                "error": vlm.configuration_issue() or "VLM is not configured",
            }))
        capture_scope = self._vlm_capture_region(args)
        if capture_scope is None:
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "semantic_visual_verified": False,
                "verify_method": "semantic-vlm",
                "error": "No target window or explicit region",
            }))
        region, metadata, hwnd = capture_scope
        self._record_vlm_capture_scope("vlm_verify", metadata, hwnd)
        started = time.monotonic()
        requested_criteria = str(args.get("criteria") or "").strip()
        full_goal = str(self.task or "").strip()
        if requested_criteria and full_goal:
            criteria = (
                f"User's complete visible goal (mandatory): {full_goal}\n"
                f"Additional checklist: {requested_criteria}"
            )
        else:
            criteria = requested_criteria or full_goal
        verdict = vlm.verify(region, criteria)
        if isinstance(verdict, dict):
            verdict["criteria"] = requested_criteria or full_goal
            verdict["full_goal_included"] = bool(requested_criteria and full_goal)
            verdict.update(metadata)
        BUS.publish(
            "verify",
            run_id=getattr(self, "run_id", ""),
            method="semantic-vlm",
            verified=verdict.get("verified") is True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            capture_scope=metadata["capture_scope"],
            capture_scope_reason=metadata["capture_scope_reason"],
            capture_region=metadata["capture_region"],
        )
        return ToolResult(
            self._fmt(verdict),
            is_observe=True,
            replay={"tool": "vlm_verify", **metadata},
        )
