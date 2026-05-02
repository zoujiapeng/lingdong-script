"""UI element positioning — semantic click core: combines OCR + template matching + positional rules."""
from typing import Optional, Union
import numpy as np
from core.screenshot import ScreenshotManager
from .ocr import OCRRecognizer
from .matcher import MultiScaleMatcher


class UIElementLocator:
    """Locate UI elements on screen by combining multiple strategies.

    Strategy pipeline:
    1. OCR text matching (find element by its visible text label)
    2. Template matching (find element by image template)
    3. Positional rules (find by spatial relationships)
    4. Score-based fusion: combine all strategies for best candidate

    This is the core engine for "semantic click" — clicking by meaning
    rather than by fixed coordinates.
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 ocr: Optional[OCRRecognizer] = None,
                 matcher: Optional[MultiScaleMatcher] = None):
        self.screenshot = screenshot_mgr
        self.ocr = ocr or OCRRecognizer(screenshot_mgr)
        self.matcher = matcher or MultiScaleMatcher(screenshot_mgr)

        # Historical positions for learning
        self._position_history: dict[str, list[tuple[int, int]]] = {}

    def locate_by_text(self, text: str,
                       region: Optional[tuple[int, int, int, int]] = None,
                       fuzzy: bool = True) -> list[dict]:
        """Locate elements by their visible text.

        Returns:
            List of {text, confidence, x, y, w, h, center_x, center_y} dicts.
        """
        matches = self.ocr.find_text(text, region, fuzzy=fuzzy)
        results = []
        for matched_text, confidence, box in matches:
            results.append({
                'text': matched_text,
                'confidence': confidence,
                'x': box[0],
                'y': box[1],
                'w': box[2],
                'h': box[3],
                'center_x': box[0] + box[2] // 2,
                'center_y': box[1] + box[3] // 2,
                'method': 'ocr',
            })
        return results

    def locate_by_template(self, template_path: Optional[str] = None,
                           template_b64: Optional[str] = None,
                           region: Optional[tuple[int, int, int, int]] = None,
                           similarity: Optional[float] = None) -> list[dict]:
        """Locate elements by template matching.

        Returns:
            List of {center_x, center_y, confidence, method} dicts.
        """
        found, positions, scores = self.matcher.find_best(
            template_path=template_path, template_b64=template_b64,
            region=region, similarity=similarity
        )
        if not found:
            return []

        results = []
        for (cx, cy), score in zip(positions, scores):
            results.append({
                'center_x': cx,
                'center_y': cy,
                'confidence': score,
                'method': 'template',
            })
        return results

    def locate_by_position_rule(self, reference: tuple[int, int],
                                direction: str,
                                offset: int = 50,
                                size_hint: Optional[tuple[int, int]] = None) -> list[dict]:
        """Locate an element relative to a reference position.

        Args:
            reference: (x, y) reference point.
            direction: 'above', 'below', 'left', 'right', 'top_left', etc.
            offset: Pixel offset from reference.
            size_hint: (w, h) expected size of the target element.

        Returns:
            List with one candidate dict.
        """
        rx, ry = reference
        dir_map = {
            'above': (0, -offset),
            'below': (0, offset),
            'left': (-offset, 0),
            'right': (offset, 0),
            'top_left': (-offset, -offset),
            'top_right': (offset, -offset),
            'bottom_left': (-offset, offset),
            'bottom_right': (offset, offset),
        }
        dx, dy = dir_map.get(direction, (0, 0))
        cx, cy = rx + dx, ry + dy

        result = {
            'center_x': cx,
            'center_y': cy,
            'confidence': 0.5,
            'method': 'position_rule',
            'reference': reference,
            'direction': direction,
        }

        if size_hint:
            result['w'] = size_hint[0]
            result['h'] = size_hint[1]

        return [result]

    def locate_combined(self, text: Optional[str] = None,
                        template_path: Optional[str] = None,
                        template_b64: Optional[str] = None,
                        region: Optional[tuple[int, int, int, int]] = None,
                        strategy: str = 'ocr_first',
                        text_fuzzy: bool = True,
                        similarity: Optional[float] = None) -> Optional[dict]:
        """Locate element using combined strategies with scoring.

        Args:
            text: Text label to search for (OCR).
            template_path/b64: Template image to match.
            region: Search region.
            strategy: 'ocr_first', 'template_first', 'fusion'.
            text_fuzzy: Fuzzy text matching.
            similarity: Min similarity for template matching.

        Returns:
            Best candidate dict, or None if no match found.
        """
        candidates = []

        # Strategy 1: OCR
        if text:
            ocr_results = self.locate_by_text(text, region, fuzzy=text_fuzzy)
            candidates.extend(ocr_results)

        # Strategy 2: Template matching
        if template_path or template_b64:
            tpl_results = self.locate_by_template(
                template_path, template_b64, region, similarity
            )
            candidates.extend(tpl_results)

        # Strategy 3: History-based (check if we've seen this element before)
        history_key = text or template_path or 'unknown'
        if history_key in self._position_history:
            avg_x = int(np.mean([p[0] for p in self._position_history[history_key]]))
            avg_y = int(np.mean([p[1] for p in self._position_history[history_key]]))
            candidates.append({
                'center_x': avg_x,
                'center_y': avg_y,
                'confidence': 0.4,
                'method': 'history',
            })

        if not candidates:
            return None

        # Strategy-based selection
        if strategy == 'ocr_first':
            ocr_candidates = [c for c in candidates if c.get('method') == 'ocr']
            if ocr_candidates:
                return max(ocr_candidates, key=lambda c: c.get('confidence', 0))
            tpl_candidates = [c for c in candidates if c.get('method') == 'template']
            if tpl_candidates:
                return max(tpl_candidates, key=lambda c: c.get('confidence', 0))

        elif strategy == 'template_first':
            tpl_candidates = [c for c in candidates if c.get('method') == 'template']
            if tpl_candidates:
                return max(tpl_candidates, key=lambda c: c.get('confidence', 0))
            ocr_candidates = [c for c in candidates if c.get('method') == 'ocr']
            if ocr_candidates:
                return max(ocr_candidates, key=lambda c: c.get('confidence', 0))

        # Fusion: weighted score across all methods
        best = max(candidates, key=lambda c: c.get('confidence', 0))

        # Save to history
        if best.get('center_x') and best.get('center_y'):
            self._position_history.setdefault(history_key, [])
            self._position_history[history_key].append(
                (best['center_x'], best['center_y'])
            )
            # Keep last 10
            if len(self._position_history[history_key]) > 10:
                self._position_history[history_key].pop(0)

        return best

    def locate_all_combined(self, text: Optional[str] = None,
                            template_path: Optional[str] = None,
                            region: Optional[tuple[int, int, int, int]] = None
                            ) -> list[dict]:
        """Find all instances of an element using combined strategies.

        Returns:
            List of candidate dicts sorted by confidence.
        """
        candidates = []
        seen_centers = set()

        if text:
            for result in self.locate_by_text(text, region):
                center = (result['center_x'], result['center_y'])
                if center not in seen_centers:
                    seen_centers.add(center)
                    candidates.append(result)

        if template_path:
            for result in self.locate_by_template(template_path, region):
                center = (result['center_x'], result['center_y'])
                if center not in seen_centers:
                    seen_centers.add(center)
                    candidates.append(result)

        candidates.sort(key=lambda c: c.get('confidence', 0), reverse=True)
        return candidates
