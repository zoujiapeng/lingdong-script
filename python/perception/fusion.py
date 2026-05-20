"""
Local fusion resolver: UI Automation → OCR → template matching.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

BBox = Tuple[int, int, int, int]


@dataclass
class FusionResult:
    found: bool = False
    text: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    confidence: float = 0.0
    strategy: str = ""
    strategies_used: list[str] = field(default_factory=list)
    reason: str = "not_found"

    @property
    def center_x(self) -> int:
        return self.x + self.w // 2

    @property
    def center_y(self) -> int:
        return self.y + self.h // 2


class LocalFusionEngine:
    def find_element(self, query: str, region: Optional[BBox] = None, threshold: float = 0.6) -> FusionResult:
        query = str(query or "").strip()
        tried: list[str] = []
        if not query:
            return FusionResult(found=False, strategies_used=tried, reason="empty_query")

        result = self._find_uia(query, region, tried, threshold)
        if result.found:
            return result

        result = self._find_ocr(query, region, tried, threshold)
        if result.found:
            return result

        result = self._find_template(query, region, tried, threshold)
        if result.found:
            return result

        return FusionResult(found=False, strategies_used=tried, reason="not_found")

    def wait_element(
        self,
        query: str,
        region: Optional[BBox] = None,
        timeout: float = 10,
        interval: float = 0.5,
        threshold: float = 0.6,
    ) -> FusionResult:
        deadline = time.time() + max(0.0, float(timeout))
        last = FusionResult(found=False, reason="timeout")
        while time.time() <= deadline:
            last = self.find_element(query, region=region, threshold=threshold)
            if last.found:
                return last
            time.sleep(max(0.05, float(interval)))
        last.reason = "timeout"
        return last

    def resolve_learned_target(self, target_id: str, context: Optional[dict] = None) -> FusionResult:
        return self.find_element(target_id, threshold=(context or {}).get("threshold", 0.6))

    def _find_uia(self, query: str, region: Optional[BBox], tried: list[str], threshold: float) -> FusionResult:
        tried.append("uia")
        try:
            import uiautomation as auto
            root = auto.GetRootControl()
            if not root:
                return FusionResult(strategies_used=tried)
            controls = []

            def walk(ctrl):
                if len(controls) >= 20:
                    return
                try:
                    name = ctrl.Name or ""
                    if query.lower() in name.lower():
                        controls.append(ctrl)
                    for child in ctrl.GetChildren():
                        walk(child)
                except Exception:
                    pass

            walk(root)
            for ctrl in controls:
                try:
                    rect = ctrl.BoundingRectangle
                    if not rect:
                        continue
                    x, y = int(rect.left), int(rect.top)
                    w, h = int(rect.right - rect.left), int(rect.bottom - rect.top)
                    if w <= 0 or h <= 0:
                        continue
                    if region:
                        rx, ry, rw, rh = region
                        if x < rx or y < ry or x + w > rx + rw or y + h > ry + rh:
                            continue
                    return FusionResult(True, ctrl.Name or query, x, y, w, h, 0.95, "uia", tried.copy())
                except Exception:
                    continue
        except Exception:
            pass
        return FusionResult(strategies_used=tried)

    def _find_ocr(self, query: str, region: Optional[BBox], tried: list[str], threshold: float) -> FusionResult:
        tried.append("ocr")
        try:
            from perception.ocr import OCREngine
            ocr = OCREngine()
            hit = ocr.find_text(query, region=region, threshold=max(0.4, float(threshold)))
            if hit:
                x, y, w, h = hit.bbox
                return FusionResult(True, hit.text, int(x), int(y), int(w), int(h), float(hit.confidence), "ocr", tried.copy())
        except Exception:
            pass
        return FusionResult(strategies_used=tried)

    def _find_template(self, query: str, region: Optional[BBox], tried: list[str], threshold: float) -> FusionResult:
        tried.append("template")
        try:
            from perception.vision import TemplateMatcher
            matcher = TemplateMatcher()
            template_name = Path(query).stem
            tmpl = matcher.load_template(template_name)
            if tmpl is None:
                return FusionResult(strategies_used=tried)
            result = matcher.find(tmpl, threshold=max(0.4, float(threshold)), region=region)
            if result:
                (cx, cy), conf = result
                if region:
                    cx += int(region[0])
                    cy += int(region[1])
                h, w = tmpl.shape[:2]
                x = int(cx - w // 2)
                y = int(cy - h // 2)
                return FusionResult(True, template_name, x, y, int(w), int(h), float(conf), "template", tried.copy())
        except Exception:
            pass
        return FusionResult(strategies_used=tried)


_engine: Optional[LocalFusionEngine] = None


def get_fusion_engine() -> LocalFusionEngine:
    global _engine
    if _engine is None:
        _engine = LocalFusionEngine()
    return _engine
