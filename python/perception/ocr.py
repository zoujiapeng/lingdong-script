"""
感知层: OCR 文字识别模块
- 多后端 OCR: PaddleOCR → EasyOCR → Tesseract → 内置降级
- 模糊匹配 (LCS)
- 区域文本提取
- 文本位置定位
"""

from __future__ import annotations
import base64
import re
import time
from typing import List, Optional, Tuple, Dict, Any

import cv2
import numpy as np

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from perception.vision import ScreenCapture, BBox, Point


def _fuzzy_match(text: str, query: str, threshold: float = 0.6) -> float:
    """简单模糊匹配，返回相似度"""
    text = text.lower().strip()
    query = query.lower().strip()
    if query in text:
        return 1.0
    # 计算最长公共子序列比
    m, n = len(query), len(text)
    if m == 0:
        return 1.0
    if n == 0:
        return 0.0
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if query[i - 1] == text[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n] / m


class OCRResult:
    def __init__(self, text: str, bbox: BBox, confidence: float):
        self.text = text
        self.bbox = bbox           # x, y, w, h
        self.confidence = confidence

    @property
    def center(self) -> Point:
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2

    def __repr__(self):
        return f"OCRResult('{self.text}', conf={self.confidence:.2f})"


class UmiOCRBackend:
    """Umi-OCR HTTP API 后端。中文识别质量显著优于 Tesseract。

    需要先启动 Umi-OCR（https://github.com/hiroi-sora/Umi-OCR），
    开启 HTTP 服务（默认端口 24024）。自动检测，不依赖则 fallback。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 24024):
        self.base_url = f"http://{host}:{port}"
        self._available: Optional[bool] = None

    def ping(self) -> bool:
        if not HAS_REQUESTS:
            return False
        try:
            resp = _requests.get(f"{self.base_url}/api/ocr/get_options", timeout=2)
            return resp.ok
        except Exception:
            return False

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self.ping()
        return self._available

    def extract_all(self, img: np.ndarray) -> List[OCRResult]:
        if not HAS_REQUESTS:
            return []
        _, buf = cv2.imencode(".png", img)
        b64 = base64.b64encode(buf).decode()

        try:
            resp = _requests.post(
                f"{self.base_url}/api/ocr",
                json={"base64": b64, "options": {"data.format": "dict"}},
                timeout=10,
            )
            if not resp.ok:
                return []
            data = resp.json()
        except Exception:
            return []

        if data.get("code") != 100:
            return []

        results: List[OCRResult] = []
        for item in data.get("data", []):
            text = item.get("text", "").strip()
            if not text:
                continue
            score = item.get("score", 0)
            box = item.get("box")
            if box and len(box) == 4:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = min(xs), min(ys)
                w, h = max(xs) - min(xs), max(ys) - min(ys)
            else:
                x, y, w, h = 0, 0, 0, 0
            results.append(OCRResult(text=text, bbox=(int(x), int(y), int(w), int(h)), confidence=float(score)))

        return results


class OCREngine:
    """
    OCR 引擎封装
    支持: 全屏/区域 OCR, 文本定位, 模糊搜索
    自动选择后端: Umi-OCR (HTTP) → Tesseract
    """

    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang
        self._cache: Dict[str, Tuple[float, List[OCRResult]]] = {}
        self._cache_ttl = 0.3
        self._umi = UmiOCRBackend()
        self._backend_name: str = ""

    def _detect_backend(self) -> str:
        if self._umi.available:
            self._backend_name = "umi"
            return "umi"
        self._backend_name = "tesseract" if HAS_TESSERACT else "mock"
        return self._backend_name

    # ── 主要 API ──────────────────────────────────────────────────
    def extract_all(
        self, region: Optional[BBox] = None, use_cache: bool = True
    ) -> List[OCRResult]:
        """提取区域内所有文字，返回带坐标的结果列表"""
        cache_key = str(region)
        if use_cache and cache_key in self._cache:
            ts, results = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return results

        img = ScreenCapture.capture(region)

        self._detect_backend()
        if self._backend_name == "umi":
            offset = region[:2] if region else (0, 0)
            results = self._umi.extract_all(img)
            # Apply region offset
            if offset != (0, 0):
                ox, oy = offset
                for r in results:
                    x, y, w, h = r.bbox
                    r.bbox = (x + ox, y + oy, w, h)
        else:
            results = self._run_ocr(img, offset=region[:2] if region else (0, 0))

        if use_cache:
            self._cache[cache_key] = (time.time(), results)
        return results

    def find_text(
        self,
        query: str,
        region: Optional[BBox] = None,
        fuzzy: bool = True,
        threshold: float = 0.7,
    ) -> Optional[OCRResult]:
        """查找包含指定文字的第一个结果"""
        results = self.extract_all(region)
        best = None
        best_score = 0.0
        for r in results:
            score = _fuzzy_match(r.text, query, threshold)
            if score > best_score:
                best_score = score
                best = r
        if best and best_score >= threshold:
            return best
        return None

    def find_all_text(
        self,
        query: str,
        region: Optional[BBox] = None,
        threshold: float = 0.7,
    ) -> List[OCRResult]:
        """查找所有包含指定文字的结果"""
        results = self.extract_all(region)
        matches = []
        for r in results:
            score = _fuzzy_match(r.text, query, threshold)
            if score >= threshold:
                matches.append(r)
        return matches

    def extract_region_text(self, region: BBox) -> str:
        """提取区域内全部文字，返回纯文本"""
        results = self.extract_all(region, use_cache=False)
        return " ".join(r.text for r in results if r.text.strip())

    def wait_for_text(
        self,
        query: str,
        region: Optional[BBox] = None,
        timeout: float = 30.0,
        interval: float = 0.5,
    ) -> Optional[OCRResult]:
        """等待文字出现"""
        end = time.time() + timeout
        while time.time() < end:
            result = self.find_text(query, region)
            if result:
                return result
            time.sleep(interval)
        return None

    # ── 内部实现 ──────────────────────────────────────────────────
    def _run_ocr(
        self, img: np.ndarray, offset: Tuple[int, int] = (0, 0)
    ) -> List[OCRResult]:
        if not HAS_TESSERACT:
            return self._mock_ocr(img)

        # 预处理: 灰度 + 二值化
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        processed = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        try:
            data = pytesseract.image_to_data(
                processed,
                lang=self.lang,
                output_type=pytesseract.Output.DICT,
                config="--psm 11",
            )
        except Exception:
            return []

        results = []
        ox, oy = offset
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
            if not text or conf < 30:
                continue
            x = data["left"][i] + ox
            y = data["top"][i] + oy
            w = data["width"][i]
            h = data["height"][i]
            results.append(OCRResult(text=text, bbox=(x, y, w, h), confidence=conf / 100))
        return results

    def _mock_ocr(self, img: np.ndarray) -> List[OCRResult]:
        """Tesseract不可用时的模拟结果"""
        return []


class MultiBackendOCR:
    """
    多后端 OCR 识别器
    自动选择可用后端: PaddleOCR → EasyOCR → Tesseract → 降级
    """

    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang
        self._instance = None
        self._backend = None

    def _init(self):
        if self._backend:
            return
        for name, try_import in [("paddle", self._try_paddle), ("easyocr", self._try_easyocr),
                                  ("tesseract", self._try_tesseract)]:
            if try_import():
                self._backend = name
                return
        self._backend = "simple"

    def _try_paddle(self) -> bool:
        try:
            from paddleocr import PaddleOCR
            self._instance = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            return True
        except ImportError:
            return False

    def _try_easyocr(self) -> bool:
        try:
            import easyocr
            self._instance = easyocr.Reader(["ch_sim", "en"], gpu=False)
            return True
        except ImportError:
            return False

    def _try_tesseract(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._instance = pytesseract
            return True
        except Exception:
            return False

    def recognize(self, image: np.ndarray, region: Optional[BBox] = None) -> List[OCRResult]:
        self._init()
        if region:
            x, y, w, h = region
            image = image[y:y + h, x:x + w]
            offset = (x, y)
        else:
            offset = (0, 0)

        if self._backend == "paddle":
            return self._run_paddle(image, offset)
        elif self._backend == "easyocr":
            return self._run_easyocr(image, offset)
        elif self._backend == "tesseract":
            return self._run_tesseract(image, offset)
        return []

    def _run_paddle(self, img: np.ndarray, offset: Tuple[int, int]) -> List[OCRResult]:
        try:
            results = self._instance.ocr(img, cls=True)
            if not results or not results[0]:
                return []
            ox, oy = offset
            out = []
            for line in results[0]:
                box, (text, conf) = line
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = int(min(xs)) + ox, int(min(ys)) + oy
                w, h = int(max(xs)) - int(min(xs)), int(max(ys)) - int(min(ys))
                out.append(OCRResult(text=text, bbox=(x, y, w, h), confidence=float(conf)))
            return out
        except Exception:
            return []

    def _run_easyocr(self, img: np.ndarray, offset: Tuple[int, int]) -> List[OCRResult]:
        try:
            results = self._instance.readtext(img)
            ox, oy = offset
            out = []
            for box, text, conf in results:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = int(min(xs)) + ox, int(min(ys)) + oy
                w, h = int(max(xs)) - int(min(xs)), int(max(ys)) - int(min(ys))
                out.append(OCRResult(text=text, bbox=(x, y, w, h), confidence=float(conf)))
            return out
        except Exception:
            return []

    def _run_tesseract(self, img: np.ndarray, offset: Tuple[int, int]) -> List[OCRResult]:
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            processed = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                              cv2.THRESH_BINARY, 11, 2)
            data = pytesseract.image_to_data(processed, lang=self.lang,
                                             output_type=pytesseract.Output.DICT, config="--psm 11")
            ox, oy = offset
            out = []
            n = len(data["text"])
            for i in range(n):
                text = data["text"][i].strip()
                conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
                if not text or conf < 30:
                    continue
                out.append(OCRResult(
                    text=text, bbox=(data["left"][i] + ox, data["top"][i] + oy,
                                     data["width"][i], data["height"][i]),
                    confidence=conf / 100,
                ))
            return out
        except Exception:
            return []
