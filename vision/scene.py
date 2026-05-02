"""Scene recognition — classify screen content using lightweight models.

Supports:
- CLIP-based scene classification (requires torch + transformers)
- Lightweight color histogram-based classifier (no extra deps)
- Template-based scene matching
"""
from typing import Optional, Union
import numpy as np
from core.screenshot import ScreenshotManager
from utils.image import ImageUtils


class SceneClassifier:
    """Recognize scenes/states on screen.

    Modes (in order of preference):
    1. CLIP-based (requires torch + transformers + clip)
    2. Color histogram classifier (built-in, no extra deps)
    3. Template matching fallback
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 use_clip: bool = False):
        self.screenshot = screenshot_mgr
        self._clip_model = None
        self._clip_processor = None
        self._labels: list[str] = []

        if use_clip:
            self._init_clip()

    def _init_clip(self):
        """Attempt to initialize CLIP model. Silently fails if deps missing."""
        try:
            import torch
            import clip
            self._clip_model, self._clip_processor = clip.load('ViT-B/32')
            self._use_clip = True
        except ImportError:
            self._use_clip = False

    @property
    def use_clip(self) -> bool:
        return self._clip_model is not None

    def classify(self, labels: list[str],
                 x: Optional[int] = None, y: Optional[int] = None,
                 w: Optional[int] = None, h: Optional[int] = None) -> tuple[str, float]:
        """Classify the screen region into one of the given labels.

        Args:
            labels: List of scene labels (e.g., ["main_menu", "battle", "inventory"]).
            x, y, w, h: Region. None = full screen.

        Returns:
            (best_label, confidence) tuple.
        """
        if x is not None and w is not None:
            img = self.screenshot.capture(region=(x, y, w, h))
        else:
            img = self.screenshot.capture()

        if self.use_clip:
            return self._classify_clip(img, labels)
        else:
            return self._classify_histogram(img, labels)

    def _classify_clip(self, img: np.ndarray, labels: list[str]) -> tuple[str, float]:
        """Classify using CLIP model."""
        try:
            import torch
            pil_img = self.screenshot.capture_pil()
            inputs = self._clip_processor(pil_img, return_tensors="pt")
            text_tokens = self._clip_processor(labels, return_tensors="pt", padding=True)

            with torch.no_grad():
                img_features = self._clip_model.get_image_features(**inputs)
                text_features = self._clip_model.get_text_features(**text_tokens)

                img_features = img_features / img_features.norm(dim=-1, keepdim=True)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)

                similarity = (img_features @ text_features.T).squeeze(0)
                probs = torch.softmax(similarity, dim=0)

            best_idx = int(torch.argmax(probs))
            return labels[best_idx], float(probs[best_idx])
        except Exception:
            return self._classify_histogram(img, labels)

    def _classify_histogram(self, img: np.ndarray, labels: list[str]) -> tuple[str, float]:
        """Fallback: classify using color histogram matching.

        This requires pre-registered label histograms via register_label().
        If no histograms are registered, returns the first label with low confidence.
        """
        if not hasattr(self, '_histograms') or not self._histograms:
            return (labels[0] if labels else "unknown", 0.5)

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        query_hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(query_hist, query_hist, 0, 1, cv2.NORM_MINMAX)

        best_label = labels[0] if labels else "unknown"
        best_score = -1.0

        for label, ref_hist in self._histograms.items():
            if label not in labels:
                continue
            score = cv2.compareHist(query_hist, ref_hist, cv2.HISTCMP_CORREL)
            if score > best_score:
                best_score = score
                best_label = label

        return best_label, max(0.0, min(1.0, best_score))

    def register_label(self, label: str, x: int, y: int, w: int, h: int):
        """Register a reference histogram for a label from current screen region."""
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

        if not hasattr(self, '_histograms'):
            self._histograms = {}
        self._histograms[label] = hist

    def match_template_scene(self, scene_templates: dict[str, list[str]],
                             region: Optional[tuple[int, int, int, int]] = None,
                             threshold: float = 0.8) -> tuple[Optional[str], float]:
        """Match scene by template matching against pre-loaded templates.

        Args:
            scene_templates: {scene_name: [list_of_template_paths]}.
            region: Screen region to search in.
            threshold: Minimum similarity to consider a match.

        Returns:
            (scene_name, max_confidence) or (None, 0) if no match.
        """
        from recognition.matcher import MultiScaleMatcher
        matcher = MultiScaleMatcher(self.screenshot)

        best_scene = None
        best_score = 0.0

        for scene_name, template_paths in scene_templates.items():
            for tpl_path in template_paths:
                found, positions, scores = matcher.find_best(
                    tpl_path, region=region
                )
                if found and scores[0] > best_score:
                    best_score = scores[0]
                    best_scene = scene_name

        if best_score >= threshold:
            return best_scene, best_score
        return None, 0.0


import cv2  # noqa: E402
