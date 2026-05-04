"""
感知层: 图像识别模块
- 模板匹配（多尺度）
- 多目标检测
- 区域变化检测（帧差/SSIM）
- 颜色检测
- 进度条识别
- 截图缓存
"""

from __future__ import annotations
import time
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import cv2
import numpy as np
from PIL import Image
import mss

try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


BBox = Tuple[int, int, int, int]   # x, y, w, h
Point = Tuple[int, int]            # x, y


class ScreenCapture:
    """截图工具，支持全屏和区域截图，带缓存"""

    _cache: Dict[str, tuple] = {}    # hash → (timestamp, img)
    _cache_ttl: float = 0.1          # 100ms 缓存

    @classmethod
    def capture(cls, region: Optional[BBox] = None) -> np.ndarray:
        """截图，返回 BGR numpy array"""
        with mss.mss() as sct:
            if region:
                x, y, w, h = region
                monitor = {"left": x, "top": y, "width": w, "height": h}
            else:
                monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            img = np.array(shot)
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    @classmethod
    def capture_cached(cls, region: Optional[BBox] = None) -> np.ndarray:
        key = str(region)
        now = time.time()
        if key in cls._cache:
            ts, img = cls._cache[key]
            if now - ts < cls._cache_ttl:
                return img
        img = cls.capture(region)
        cls._cache[key] = (now, img)
        return img

    @classmethod
    def clear_cache(cls):
        cls._cache.clear()


class TemplateMatcher:
    """
    模板匹配器
    - 多尺度匹配
    - 多目标检测
    - 相似度阈值
    """

    def __init__(self, template_dir: str = "templates"):
        self._template_dir = Path(template_dir)
        self._cache: Dict[str, np.ndarray] = {}

    def load_template(self, name: str) -> Optional[np.ndarray]:
        if name in self._cache:
            return self._cache[name]
        for ext in [".png", ".jpg", ".bmp"]:
            path = self._template_dir / (name + ext)
            if path.exists():
                tmpl = cv2.imread(str(path))
                self._cache[name] = tmpl
                return tmpl
        return None

    def preload_all(self):
        """预加载所有模板"""
        for f in self._template_dir.glob("*.*"):
            if f.suffix.lower() in {".png", ".jpg", ".bmp"}:
                name = f.stem
                if name not in self._cache:
                    self._cache[name] = cv2.imread(str(f))

    def find(
        self,
        template: np.ndarray | str,
        screen: Optional[np.ndarray] = None,
        threshold: float = 0.8,
        region: Optional[BBox] = None,
    ) -> Optional[Tuple[Point, float]]:
        """
        查找单个目标，返回 (center_point, confidence) 或 None
        """
        if isinstance(template, str):
            tmpl = self.load_template(template)
            if tmpl is None:
                return None
        else:
            tmpl = template

        if screen is None:
            screen = ScreenCapture.capture_cached(region)

        result = self._match_multiscale(screen, tmpl, threshold)
        return result

    def find_all(
        self,
        template: np.ndarray | str,
        screen: Optional[np.ndarray] = None,
        threshold: float = 0.8,
        region: Optional[BBox] = None,
        max_results: int = 10,
    ) -> List[Tuple[Point, float]]:
        """查找所有目标"""
        if isinstance(template, str):
            tmpl = self.load_template(template)
            if tmpl is None:
                return []
        else:
            tmpl = template

        if screen is None:
            screen = ScreenCapture.capture_cached(region)

        return self._match_all(screen, tmpl, threshold, max_results)

    def _match_multiscale(
        self, screen: np.ndarray, template: np.ndarray, threshold: float
    ) -> Optional[Tuple[Point, float]]:
        """多尺度模板匹配"""
        h, w = template.shape[:2]
        best_val = 0.0
        best_loc = None
        best_scale = 1.0

        for scale in np.linspace(0.5, 1.5, 13):
            tw = int(w * scale)
            th = int(h * scale)
            if tw < 10 or th < 10:
                continue
            resized = cv2.resize(template, (tw, th))
            if resized.shape[0] > screen.shape[0] or resized.shape[1] > screen.shape[1]:
                continue

            result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        if best_val < threshold or best_loc is None:
            return None

        tw = int(w * best_scale)
        th = int(h * best_scale)
        cx = best_loc[0] + tw // 2
        cy = best_loc[1] + th // 2
        return (cx, cy), best_val

    def _match_all(
        self,
        screen: np.ndarray,
        template: np.ndarray,
        threshold: float,
        max_results: int,
    ) -> List[Tuple[Point, float]]:
        """非极大值抑制查找所有目标"""
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        h, w = template.shape[:2]
        locations = np.where(result >= threshold)
        matches = []

        for pt in zip(*locations[::-1]):
            score = result[pt[1], pt[0]]
            cx = pt[0] + w // 2
            cy = pt[1] + h // 2
            matches.append(((cx, cy), float(score)))

        # NMS: 合并相近点
        return self._nms(matches, min_dist=w // 2)[:max_results]

    def _nms(self, matches: list, min_dist: int = 20) -> list:
        if not matches:
            return []
        matches.sort(key=lambda x: -x[1])
        kept = []
        for pt, score in matches:
            too_close = False
            for kpt, _ in kept:
                dist = ((pt[0] - kpt[0]) ** 2 + (pt[1] - kpt[1]) ** 2) ** 0.5
                if dist < min_dist:
                    too_close = True
                    break
            if not too_close:
                kept.append((pt, score))
        return kept


class ChangeDetector:
    """区域变化检测"""

    def __init__(self):
        self._prev_frame: Optional[np.ndarray] = None

    def has_changed(
        self,
        region: Optional[BBox] = None,
        method: str = "ssim",
        threshold: float = 0.95,
    ) -> bool:
        """检测区域是否发生变化"""
        curr = ScreenCapture.capture(region)
        if self._prev_frame is None:
            self._prev_frame = curr
            return False

        if method == "ssim" and HAS_SKIMAGE:
            gray_prev = cv2.cvtColor(self._prev_frame, cv2.COLOR_BGR2GRAY)
            gray_curr = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
            score, _ = ssim(gray_prev, gray_curr, full=True)
            changed = score < threshold
        else:
            # 帧差法
            diff = cv2.absdiff(self._prev_frame, curr)
            changed = np.mean(diff) > (1 - threshold) * 255

        self._prev_frame = curr
        return changed

    def is_stable(self, region: Optional[BBox] = None, duration: float = 1.0) -> bool:
        """等待区域稳定"""
        end = time.time() + duration
        while time.time() < end:
            if self.has_changed(region):
                end = time.time() + duration
            time.sleep(0.1)
        return True


class ColorDetector:
    """颜色检测 + 进度条识别"""

    # 遮挡检测状态
    _baseline_sat_mean: Optional[float] = None
    _occlusion_threshold: float = 0.4  # 当前均值 < 基线*0.4 视为遮挡
    _progress_template: Optional[np.ndarray] = None  # 进度条模板（用于重定位）

    @classmethod
    def init_progress_bar(cls, region: BBox) -> dict:
        """
        初始化进度条监控：捕获模板 + 建立饱和度基线。
        返回 {success, ratio, occluded, template_saved}
        """
        img = ScreenCapture.capture(region)
        if img is None or img.size == 0:
            return {"success": False, "error": "区域无图像"}
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        s = hsv[:, :, 1].astype(np.float32)
        cls._baseline_sat_mean = float(s.mean())
        cls._progress_template = img.copy()
        ratio = cls.detect_progress_bar(region)
        return {
            "success": True,
            "ratio": ratio,
            "occluded": False,
            "baseline_saturation": round(cls._baseline_sat_mean, 1),
            "template_saved": True,
        }

    @classmethod
    def relocate_progress_bar(cls, search_region: Optional[BBox] = None) -> Optional[BBox]:
        """
        用模板匹配重新定位进度条位置。
        返回新的 BBox (x,y,w,h) 或 None（未找到）。
        """
        if cls._progress_template is None:
            return None
        try:
            from perception.vision import TemplateMatcher
            matcher = TemplateMatcher()
            screen = ScreenCapture.capture(search_region)
            match = matcher.find(cls._progress_template, screen=screen, threshold=0.6)
            if match:
                (cx, cy), conf = match
                h, w = cls._progress_template.shape[:2]
                x = cx - w // 2
                y = cy - h // 2
                # 重设基线
                new_img = ScreenCapture.capture((x, y, w, h))
                if new_img is not None and new_img.size > 0:
                    hsv = cv2.cvtColor(new_img, cv2.COLOR_BGR2HSV)
                    cls._baseline_sat_mean = float(hsv[:, :, 1].mean())
                return (x, y, w, h)
            return None
        except Exception:
            return None

    @classmethod
    def clear_progress_state(cls):
        """清除进度条状态（换目标时调用）"""
        cls._baseline_sat_mean = None
        cls._progress_template = None

    @staticmethod
    def detect_color(
        color_hsv: Tuple[int, int, int],
        tolerance: Tuple[int, int, int] = (10, 50, 50),
        region: Optional[BBox] = None,
    ) -> float:
        """返回指定颜色在区域内的占比"""
        img = ScreenCapture.capture_cached(region)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h, s, v = color_hsv
        tol_h, tol_s, tol_v = tolerance
        lower = np.array([max(0, h - tol_h), max(0, s - tol_s), max(0, v - tol_v)])
        upper = np.array([min(179, h + tol_h), min(255, s + tol_s), min(255, v + tol_v)])
        mask = cv2.inRange(hsv, lower, upper)
        ratio = np.sum(mask > 0) / mask.size
        return float(ratio)

    @staticmethod
    def detect_progress_bar(
        region: BBox,
        fg_color_hsv: Optional[Tuple] = None,
        direction: str = "auto",
    ) -> float:
        """
        进度条识别，返回 0.0~1.0
        算法:
        1. 优先用饱和度通道（彩色进度条），自适应阈值
        2. 饱和度不足时降级为灰度强度（黑白进度条），自动判断明暗方向
        """
        img = ScreenCapture.capture(region)
        if img is None or img.size == 0:
            return 0.0
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        s = hsv[:, :, 1].astype(np.float32)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

        h, w = s.shape
        if direction == "auto":
            direction = "horizontal" if w > h else "vertical"

        if direction == "horizontal":
            ratios = []
            for r in range(h):
                row_s = s[r, :]
                max_sat = float(np.max(row_s))
                # 策略1: 彩色检测
                if max_sat >= 15:
                    thr = max_sat * 0.35
                    filled = np.where(row_s > thr)[0]
                    if len(filled) > 0:
                        ratios.append((filled[-1] + 1) / w)
                    continue
                # 策略2: 灰度降级
                row_gray = gray[r, :]
                row_range = float(np.ptp(row_gray))
                if row_range < 20:
                    continue
                left_gray = float(np.median(row_gray[:w // 4]))
                right_gray = float(np.median(row_gray[3 * w // 4:]))
                if abs(left_gray - right_gray) < 12:
                    continue
                thr = (left_gray + right_gray) / 2
                if left_gray > right_gray:
                    # 填充比背景亮（亮底暗条 → 反转）
                    filled = np.where(row_gray > thr)[0]
                else:
                    # 填充比背景暗（暗底亮条）
                    filled = np.where(row_gray < thr)[0]
                if len(filled) > 0:
                    ratios.append((filled[-1] + 1) / w)
            return float(np.median(ratios)) if ratios else 0.0
        else:
            ratios = []
            for c in range(w):
                col_s = s[:, c]
                max_sat = float(np.max(col_s))
                # 策略1: 彩色检测
                if max_sat >= 15:
                    thr = max_sat * 0.35
                    filled = np.where(col_s > thr)[0]
                    if len(filled) > 0:
                        ratios.append((filled[-1] + 1) / h)
                    continue
                # 策略2: 灰度降级
                col_gray = gray[:, c]
                col_range = float(np.ptp(col_gray))
                if col_range < 20:
                    continue
                top_gray = float(np.median(col_gray[:h // 4]))
                bottom_gray = float(np.median(col_gray[3 * h // 4:]))
                if abs(top_gray - bottom_gray) < 12:
                    continue
                thr = (top_gray + bottom_gray) / 2
                if top_gray > bottom_gray:
                    filled = np.where(col_gray > thr)[0]
                else:
                    filled = np.where(col_gray < thr)[0]
                if len(filled) > 0:
                    ratios.append((filled[-1] + 1) / h)
            return float(np.median(ratios)) if ratios else 0.0

    @classmethod
    def set_progress_region(cls, region: BBox) -> dict:
        """
        设置进度条区域并捕获模板/基线（供 PROGRESS_INIT DSL 使用）。
        返回 {success, ratio, occluded, baseline, template}
        """
        return cls.init_progress_bar(region)

    @classmethod
    def analyze_progress_bar(
        cls,
        region: BBox,
        direction: str = "auto",
    ) -> dict:
        """
        进度条检测 + 遮挡检测 + 置信度。
        返回 {ratio, occluded, confidence, saturation_mean, baseline}

        occlusion 判定：当前区域饱和度均值 < 基线 * 0.4 视为遮挡。
        遮挡时 ratio 仍然返回但不应该被信任。
        """
        img = ScreenCapture.capture(region)
        if img is None or img.size == 0:
            return {"ratio": 0.0, "occluded": True, "confidence": 0.0,
                    "saturation_mean": 0.0, "baseline": cls._baseline_sat_mean,
                    "error": "空图像"}

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        s = hsv[:, :, 1].astype(np.float32)
        sat_mean = float(s.mean())

        # 计算填充比例
        ratio = cls.detect_progress_bar(region, direction=direction)

        # 遮挡检测
        occluded = False
        h, w = s.shape
        if direction == "vertical":
            active = np.sum(s.max(axis=0) >= 15) / w
        else:
            active = np.sum(s.max(axis=1) >= 15) / h

        if cls._baseline_sat_mean is not None:
            # 当基线足够高（有颜色信号）时，用均值比较检测遮挡
            if cls._baseline_sat_mean > 5:
                if sat_mean < cls._baseline_sat_mean * cls._occlusion_threshold:
                    occluded = True
            # 当基线很低（颜色信号弱），用有效行比例判断
            else:
                if active < 0.15:
                    occluded = True

        # 置信度
        confidence = active if not occluded else max(0.0, active * 0.3)

        return {
            "ratio": ratio,
            "occluded": occluded,
            "confidence": round(confidence, 3),
            "saturation_mean": round(sat_mean, 1),
            "baseline": round(cls._baseline_sat_mean, 1) if cls._baseline_sat_mean else None,
        }


class SceneClassifier:
    """场景识别（基于特征哈希）"""

    def __init__(self):
        self._scenes: Dict[str, np.ndarray] = {}

    def register_scene(self, name: str, image: np.ndarray):
        self._scenes[name] = self._hash(image)

    def identify(self, screen: Optional[np.ndarray] = None) -> Optional[str]:
        if screen is None:
            screen = ScreenCapture.capture()
        h = self._hash(screen)
        best_name = None
        best_sim = 0.0
        for name, ref_hash in self._scenes.items():
            sim = self._similarity(h, ref_hash)
            if sim > best_sim:
                best_sim = sim
                best_name = name
        if best_sim > 0.85:
            return best_name
        return None

    def _hash(self, img: np.ndarray, size: int = 16) -> np.ndarray:
        """感知哈希"""
        small = cv2.resize(img, (size, size))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean = gray.mean()
        return (gray >= mean).flatten()

    def _similarity(self, h1: np.ndarray, h2: np.ndarray) -> float:
        return float(np.sum(h1 == h2)) / len(h1)
