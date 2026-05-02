"""Multi-scale template matching — find templates in screenshots at multiple scales."""
from typing import Optional, Union
import cv2
import numpy as np
from core.screenshot import ScreenshotManager
from core.template_cache import TemplateCache
from core.parallel import ThreadPoolManager


class MultiScaleMatcher:
    """Multi-scale template matching with configurable parameters.

    Features:
    - Multi-scale search: runs matching across a range of scales
    - Region limitation: restrict search to a sub-region
    - Multiple object detection: find all instances above threshold
    - Configurable similarity threshold
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 template_cache: Optional[TemplateCache] = None,
                 default_scales: Optional[list[float]] = None,
                 default_similarity: float = 0.85):
        self.screenshot = screenshot_mgr
        self.template_cache = template_cache or TemplateCache()
        self.default_scales = default_scales or [0.7, 0.85, 1.0, 1.15, 1.3]
        self.default_similarity = default_similarity

    def find_best(self, template_path: Optional[str] = None,
                  template_b64: Optional[str] = None,
                  template_img: Optional[np.ndarray] = None,
                  region: Optional[tuple[int, int, int, int]] = None,
                  scales: Optional[list[float]] = None,
                  similarity: Optional[float] = None,
                  grayscale: bool = True) -> tuple[bool, list[tuple[int, int]], list[float]]:
        """Find the best match for a template on screen.

        Args:
            template_path: Path to template image.
            template_b64: Base64-encoded template image.
            template_img: Numpy array of template image.
            region: (x, y, w, h) region to search in. None = full screen.
            scales: List of scale factors to try.
            similarity: Minimum similarity (0-1).
            grayscale: Convert to grayscale for faster matching.

        Returns:
            (found, [(cx, cy), ...], [score, ...]) for the best match.
        """
        if template_img is None:
            tmpl = self.template_cache.get(template_path, template_b64,
                                           grayscale=grayscale)
        else:
            tmpl = cv2.cvtColor(template_img, cv2.COLOR_BGR2GRAY) if \
                grayscale and len(template_img.shape) == 3 else template_img

        if tmpl is None:
            return False, [], []

        eff_similarity = similarity if similarity is not None else self.default_similarity
        eff_scales = scales if scales is not None else self.default_scales

        screen = self.screenshot.capture_gray(region=region)
        best_val = -1.0
        best_pos = (0, 0)
        best_scale = 1.0

        for scale in eff_scales:
            if scale != 1.0:
                w = int(tmpl.shape[1] * scale)
                h = int(tmpl.shape[0] * scale)
                if w < 5 or h < 5:
                    continue
                scaled_tmpl = cv2.resize(tmpl, (w, h), interpolation=cv2.INTER_AREA)
            else:
                scaled_tmpl = tmpl

            if scaled_tmpl.shape[0] > screen.shape[0] or scaled_tmpl.shape[1] > screen.shape[1]:
                continue

            result = cv2.matchTemplate(screen, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_pos = max_loc
                best_scale = scale

        if best_val >= eff_similarity:
            # Calculate center position
            tmpl_w = int(tmpl.shape[1] * best_scale)
            tmpl_h = int(tmpl.shape[0] * best_scale)
            cx = best_pos[0] + tmpl_w // 2
            cy = best_pos[1] + tmpl_h // 2

            # Offset by region
            if region:
                cx += region[0]
                cy += region[1]

            return True, [(cx, cy)], [float(best_val)]

        return False, [], []

    def find_all(self, template_path: Optional[str] = None,
                 template_b64: Optional[str] = None,
                 template_img: Optional[np.ndarray] = None,
                 region: Optional[tuple[int, int, int, int]] = None,
                 scales: Optional[list[float]] = None,
                 similarity: Optional[float] = None,
                 max_results: int = 20) -> list[tuple[tuple[int, int], float, float]]:
        """Find all instances of a template on screen.

        Returns:
            List of ((cx, cy), score, scale) sorted by score descending.
        """
        if template_img is None:
            tmpl = self.template_cache.get(template_path, template_b64, grayscale=True)
        else:
            tmpl = cv2.cvtColor(template_img, cv2.COLOR_BGR2GRAY) if \
                len(template_img.shape) == 3 else template_img

        if tmpl is None:
            return []

        eff_similarity = similarity if similarity is not None else self.default_similarity
        eff_scales = scales if scales is not None else self.default_scales

        screen = self.screenshot.capture_gray(region=region)
        all_matches = []

        for scale in eff_scales:
            if scale != 1.0:
                w = int(tmpl.shape[1] * scale)
                h = int(tmpl.shape[0] * scale)
                if w < 5 or h < 5:
                    continue
                scaled_tmpl = cv2.resize(tmpl, (w, h), interpolation=cv2.INTER_AREA)
            else:
                scaled_tmpl = tmpl

            if scaled_tmpl.shape[0] > screen.shape[0] or scaled_tmpl.shape[1] > screen.shape[1]:
                continue

            result = cv2.matchTemplate(screen, scaled_tmpl, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= eff_similarity)

            for pt in zip(*locations[::-1]):
                cx = pt[0] + scaled_tmpl.shape[1] // 2
                cy = pt[1] + scaled_tmpl.shape[0] // 2
                if region:
                    cx += region[0]
                    cy += region[1]
                score = float(result[pt[1], pt[0]])
                all_matches.append(((cx, cy), score, scale))

        # NMS: remove overlapping duplicates
        filtered = self._nms(all_matches)

        # Sort by score descending
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered[:max_results]

    def _nms(self, matches: list, overlap_threshold: float = 0.5) -> list:
        """Simple non-maximum suppression to remove overlapping matches."""
        if not matches:
            return []

        # Sort by score descending
        sorted_matches = sorted(matches, key=lambda x: x[1], reverse=True)
        kept = []

        for match in sorted_matches:
            (cx, cy), score, scale = match
            # Check overlap with kept matches (within 20px)
            is_duplicate = False
            for (kcx, kcy), _, _ in kept:
                if abs(cx - kcx) < 20 and abs(cy - kcy) < 20:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(match)

        return kept

    def match_in_parallel(self, templates: list[dict],
                          region: Optional[tuple[int, int, int, int]] = None,
                          similarity: Optional[float] = None
                          ) -> list[tuple[str, bool, list, list]]:
        """Match multiple templates in parallel.

        Args:
            templates: List of {"name": str, "path": str, "b64": str} dicts.
            region: Search region.
            similarity: Min similarity threshold.

        Returns:
            List of (name, found, positions, scores).
        """
        pool = ThreadPoolManager.get_instance()
        tasks = []

        for tpl in templates:
            tasks.append((
                self.find_best,
                [],
                {
                    'template_path': tpl.get('path'),
                    'template_b64': tpl.get('b64'),
                    'region': region,
                    'similarity': similarity,
                }
            ))

        results = pool.run_all(tasks)
        output = []
        for tpl, (found, positions, scores) in zip(templates, results):
            output.append((tpl.get('name', ''), found, positions, scores))

        return output
