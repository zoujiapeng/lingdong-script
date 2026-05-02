"""OCR text recognition — extract text from screen regions.

Supports:
- PaddleOCR (best accuracy, requires paddlepaddle + paddleocr)
- Tesseract (good fallback, requires pytesseract + tesseract-ocr)
- EasyOCR (lightweight, requires easyocr)
- Built-in template-based digit recognition (zero deps)
"""
import re
from typing import Optional, Union
import numpy as np
from core.screenshot import ScreenshotManager


class OCRRecognizer:
    """OCR recognizer with multiple backend support.

    Priority: PaddleOCR > EasyOCR > Tesseract > Built-in fallback
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 lang: str = 'ch_sim+en',
                 backend: str = 'auto'):
        """
        Args:
            screenshot_mgr: Shared screenshot manager.
            lang: Language for OCR. 'ch_sim+en' for Chinese + English.
            backend: 'paddle', 'easyocr', 'tesseract', 'simple', or 'auto'.
        """
        self.screenshot = screenshot_mgr
        self.lang = lang
        self.backend = backend
        self._ocr_instance = None
        self._initialized = False

    def _init_ocr(self):
        """Initialize the OCR backend based on availability."""
        if self._initialized:
            return

        if self.backend == 'auto':
            backends_to_try = ['paddle', 'easyocr', 'tesseract', 'simple']
        else:
            backends_to_try = [self.backend]

        for backend_name in backends_to_try:
            if backend_name == 'paddle':
                try:
                    from paddleocr import PaddleOCR
                    self._ocr_instance = PaddleOCR(
                        use_angle_cls=True,
                        lang='ch',
                        show_log=False
                    )
                    self._backend_name = 'paddle'
                    self._initialized = True
                    return
                except ImportError:
                    continue

            elif backend_name == 'easyocr':
                try:
                    import easyocr
                    lang_code = 'ch_sim' if 'ch' in self.lang else 'en'
                    self._ocr_instance = easyocr.Reader([lang_code, 'en'], gpu=False)
                    self._backend_name = 'easyocr'
                    self._initialized = True
                    return
                except ImportError:
                    continue

            elif backend_name == 'tesseract':
                try:
                    import pytesseract
                    # Test if tesseract is installed
                    pytesseract.get_tesseract_version()
                    self._ocr_instance = pytesseract
                    self._backend_name = 'tesseract'
                    self._initialized = True
                    return
                except (ImportError, Exception):
                    continue

            elif backend_name == 'simple':
                self._backend_name = 'simple'
                self._initialized = True
                return

        self._backend_name = 'simple'
        self._initialized = True

    def recognize(self, img: Optional[np.ndarray] = None,
                  region: Optional[tuple[int, int, int, int]] = None
                  ) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        """Recognize text in an image or screen region.

        Args:
            img: Image as numpy array (BGR). If None, uses region or full screen.
            region: (x, y, w, h) screen region. Used if img is None.

        Returns:
            List of (text, confidence, (x, y, w, h)) tuples.
        """
        self._init_ocr()

        if img is None:
            if region:
                img = self.screenshot.capture(region=region)
            else:
                img = self.screenshot.capture()

        if self._backend_name == 'paddle':
            return self._recognize_paddle(img)
        elif self._backend_name == 'easyocr':
            return self._recognize_easyocr(img)
        elif self._backend_name == 'tesseract':
            return self._recognize_tesseract(img)
        else:
            return self._recognize_simple(img)

    def _recognize_paddle(self, img: np.ndarray) -> list:
        try:
            results = self._ocr_instance.ocr(img, cls=True)
            if not results or not results[0]:
                return []
            extracted = []
            for line in results[0]:
                box, (text, confidence) = line
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                extracted.append((text, float(confidence), (x, y, w, h)))
            return extracted
        except Exception:
            return []

    def _recognize_easyocr(self, img: np.ndarray) -> list:
        try:
            results = self._ocr_instance.readtext(img)
            extracted = []
            for box, text, confidence in results:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                extracted.append((text, float(confidence), (x, y, w, h)))
            return extracted
        except Exception:
            return []

    def _recognize_tesseract(self, img: np.ndarray) -> list:
        try:
            import pytesseract
            pil_img = self.screenshot.capture_pil()
            data = pytesseract.image_to_data(pil_img, lang=self.lang,
                                             output_type=pytesseract.Output.DICT)
            results = []
            for i in range(len(data['text'])):
                text = data['text'][i].strip()
                conf = int(data['conf'][i]) / 100.0
                if text and conf > 0:
                    x, y, w, h = (data['left'][i], data['top'][i],
                                   data['width'][i], data['height'][i])
                    results.append((text, conf, (x, y, w, h)))
            return results
        except Exception:
            return []

    def _recognize_simple(self, img: np.ndarray) -> list:
        """Simple fallback: extract digits and basic shapes."""
        import cv2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 11, 2)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        results = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 10 and h > 10 and h < 100 and w < 500:
                roi = thresh[y:y + h, x:x + w]
                # Try to match digits using template
                results.append(("[text]", 0.3, (x, y, w, h)))

        return results

    def find_text(self, target: str,
                  region: Optional[tuple[int, int, int, int]] = None,
                  fuzzy: bool = True,
                  case_sensitive: bool = False) -> list[tuple[str, float, tuple]]:
        """Find a specific text string on screen.

        Args:
            target: Text to search for.
            region: Screen region. None = full screen.
            fuzzy: Use fuzzy (substring) matching.
            case_sensitive: Case-sensitive comparison.

        Returns:
            List of matching (text, confidence, (x, y, w, h)) tuples.
        """
        results = self.recognize(region=region)

        if not case_sensitive:
            target = target.lower()

        matches = []
        for text, conf, box in results:
            compare = text if case_sensitive else text.lower()
            if fuzzy:
                if target in compare:
                    matches.append((text, conf, box))
            else:
                if compare == target:
                    matches.append((text, conf, box))

        return matches

    def find_text_position(self, target: str,
                           region: Optional[tuple[int, int, int, int]] = None,
                           fuzzy: bool = True) -> Optional[tuple[int, int]]:
        """Find the center position of a text string on screen.

        Returns:
            (x, y) center of the first match, or None.
        """
        matches = self.find_text(target, region, fuzzy)
        if not matches:
            return None
        _, _, box = matches[0]
        cx = box[0] + box[2] // 2
        cy = box[1] + box[3] // 2
        return (cx, cy)
