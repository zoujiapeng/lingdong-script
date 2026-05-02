"""Template image cache — preload and reuse template images to avoid repeated I/O."""
import os
import base64
import time
from typing import Optional
from functools import lru_cache

import cv2
import numpy as np


class TemplateCache:
    """Preloads and caches template images for template matching operations.

    Supports:
    - Loading from file paths
    - Loading from base64 strings
    - Automatic scale generation (multi-scale variants)
    - LRU eviction (via @lru_cache internally)
    - Lifetime management
    """

    def __init__(self, max_size: int = 64, preload_dir: Optional[str] = None,
                 scales: Optional[list[float]] = None):
        self.max_size = max_size
        self.scales = scales or [1.0]
        self._load_times: dict[str, float] = {}
        self._preloaded: dict[str, np.ndarray] = {}

        if preload_dir:
            self._preload_directory(preload_dir)

    def _preload_directory(self, directory: str):
        """Load all image files from a directory into cache."""
        valid_exts = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}
        for fname in os.listdir(directory):
            ext = os.path.splitext(fname)[1].lower()
            if ext in valid_exts:
                fpath = os.path.join(directory, fname)
                img = cv2.imread(fpath)
                if img is not None:
                    cache_key = f"path:{fpath}"
                    self._preloaded[cache_key] = img
                    self._load_times[cache_key] = time.time()

    def get(self, path: Optional[str] = None, base64_str: Optional[str] = None,
            grayscale: bool = False) -> Optional[np.ndarray]:
        """Get a template image from cache or load it.

        Args:
            path: File path to the image.
            base64_str: Base64-encoded image string.
            grayscale: If True, return grayscale version.

        Returns:
            Image as numpy array, or None on failure.
        """
        if path:
            cache_key = f"path:{path}"
            # Check preloaded cache first
            if cache_key in self._preloaded:
                img = self._preloaded[cache_key]
            else:
                img = cv2.imread(path)
                if img is None:
                    return None
                self._preloaded[cache_key] = img
                self._load_times[cache_key] = time.time()
                self._enforce_max_size()

            if grayscale and len(img.shape) == 3:
                return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            return img

        if base64_str:
            cache_key = f"b64:{hash(base64_str)}"
            if cache_key in self._preloaded:
                img = self._preloaded[cache_key]
            else:
                try:
                    b64_data = base64_str.split(',')[1] if ',' in base64_str else base64_str
                    img_data = base64.b64decode(b64_data)
                    nparr = np.frombuffer(img_data, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is None:
                        return None
                    self._preloaded[cache_key] = img
                    self._load_times[cache_key] = time.time()
                    self._enforce_max_size()
                except Exception:
                    return None

            if grayscale and len(img.shape) == 3:
                return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            return img

        return None

    def get_scales(self, path: Optional[str] = None, base64_str: Optional[str] = None,
                   scales: Optional[list[float]] = None) -> list[tuple[float, np.ndarray]]:
        """Get a template at multiple scales.

        Returns:
            List of (scale_factor, scaled_image) tuples.
        """
        img = self.get(path, base64_str)
        if img is None:
            return []

        scales_to_use = scales or self.scales
        h, w = img.shape[:2]
        results = []

        for scale in scales_to_use:
            if scale == 1.0:
                results.append((1.0, img.copy()))
            else:
                sw = max(1, int(w * scale))
                sh = max(1, int(h * scale))
                scaled = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
                results.append((scale, scaled))

        return results

    def preload(self, path: str) -> bool:
        """Explicitly preload an image."""
        img = cv2.imread(path)
        if img is not None:
            cache_key = f"path:{path}"
            self._preloaded[cache_key] = img
            self._load_times[cache_key] = time.time()
            self._enforce_max_size()
            return True
        return False

    def preload_base64(self, b64_str: str) -> bool:
        """Explicitly preload a base64-encoded image."""
        try:
            b64_data = b64_str.split(',')[1] if ',' in b64_str else b64_str
            img_data = base64.b64decode(b64_data)
            nparr = np.frombuffer(img_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                cache_key = f"b64:{hash(b64_str)}"
                self._preloaded[cache_key] = img
                self._load_times[cache_key] = time.time()
                self._enforce_max_size()
                return True
        except Exception:
            pass
        return False

    def remove(self, path: Optional[str] = None, base64_str: Optional[str] = None):
        """Remove an image from cache."""
        if path:
            self._preloaded.pop(f"path:{path}", None)
            self._load_times.pop(f"path:{path}", None)
        if base64_str:
            key = f"b64:{hash(base64_str)}"
            self._preloaded.pop(key, None)
            self._load_times.pop(key, None)

    def clear(self):
        self._preloaded.clear()
        self._load_times.clear()

    def _enforce_max_size(self):
        if len(self._preloaded) <= self.max_size:
            return
        # Evict oldest by load time
        sorted_keys = sorted(self._load_times, key=self._load_times.get)
        to_remove = len(self._preloaded) - self.max_size
        for key in sorted_keys[:to_remove]:
            self._preloaded.pop(key, None)
            self._load_times.pop(key, None)
