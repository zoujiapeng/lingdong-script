"""Perception and visual-inspection tool handlers for ToolSession."""
from __future__ import annotations

import sys

from ..config import CONFIG
from ..perception import fusion, intent, ocr, vision_cv, vlm
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
        popup_terms = (
            "PopupHost", "Line", "Fill", "Color 1:", "No fill", "Brush",
            "Edit colors", "OK", "Cancel", "RGB",
        )
        popup_open = False
        if self.snapshot is not None:
            popup_open = any(
                any(term in (element.text or "") for term in popup_terms)
                or "popup" in (element.role or "").casefold()
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

    def _t_observe(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无法确定目标窗口，请先 list_windows + focus_window")
        include_icons = bool(args.get("include_icons", True))
        fusion_mod = _tools_attr("fusion", fusion)
        perception_mode = self._perception_mode()
        cache_key = (int(hwnd), bool(include_icons), perception_mode, self._screen_epoch)
        cache_ttl = float(CONFIG.get("fusion", "observe_cache_ttl_s", default=2.0))
        cached = self.cached_observe(cache_key, cache_ttl) if cache_ttl > 0 else None
        if cached is not None:
            self.snapshot = cached
            self.snapshot.dim_stats["cache"] = {"count": len(self.snapshot.elements), "ms": 0}
        else:
            self.snapshot = fusion_mod.observe(hwnd, include_icons=include_icons,
                                               perception_mode=perception_mode)
            self.store_observe_cache(cache_key, self.snapshot)
        self._remember_target(hwnd, self.snapshot.title)
        query = str(args.get("query", "") or "")
        max_elements = int(args.get("max_elements", 160) or 160)
        text = self.snapshot.to_text(max_elements=max_elements, query=query)
        visual_summary = self._visual_state_summary(hwnd)
        if visual_summary:
            text = text + "\n" + visual_summary
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
        ocr_mod = _tools_attr("ocr", ocr)
        snapshot = fusion_mod.observe(hwnd, include_icons=False,
                                      perception_mode=self._perception_mode())
        texts = [e.text for e in snapshot.elements if e.text.strip()]
        ocr_elements = ocr_mod.collect(snapshot.region, min_conf=0.45)
        texts.extend(e.text for e in ocr_elements if e.text.strip())
        body = "\n".join(dict.fromkeys(texts))
        return ToolResult(body or "（未识别到文字）",
                          summary=f"[已折叠] read_text {len(texts)}条文字")

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
