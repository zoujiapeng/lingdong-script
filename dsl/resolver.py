"""Condition resolver — dispatch text:/img:/color: prefixes to detection functions."""
from typing import Any, Optional, Union
from core.screenshot import ScreenshotManager
from recognition.ocr import OCRRecognizer
from recognition.matcher import MultiScaleMatcher
from vision.color import ColorDetector
from logic.variables import VariableSystem


class ConditionResolver:
    """Resolve condition expressions by their prefix.

    Prefix system:
    - text:/text_contains: — OCR text detection
    - img:/image: — template image existence
    - color: — color detection in region
    - var:/variable: — variable system query
    - proc:/process: — process running check
    - win:/window: — window existence check
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 ocr: Optional[OCRRecognizer] = None,
                 matcher: Optional[MultiScaleMatcher] = None,
                 color_detector: Optional[ColorDetector] = None,
                 variables: Optional[VariableSystem] = None):
        self.screenshot = screenshot_mgr
        self.ocr = ocr or OCRRecognizer(screenshot_mgr)
        self.matcher = matcher or MultiScaleMatcher(screenshot_mgr)
        self.color = color_detector or ColorDetector(screenshot_mgr)
        self.variables = variables or VariableSystem()

    def resolve(self, condition: str) -> bool:
        """Resolve a condition string to a boolean.

        Args:
            condition: Condition string with prefix.
                       Examples:
                       - "text:领取" — does "领取" appear on screen?
                       - "img:path/to/button.png" — does the template match?
                       - "color:(100,100,50x50)#(120,50,50)" — region color check?
                       - "var:score>100" — variable comparison
                       - "proc:chrome.exe" — is process running?
                       - "win:设置" — does window exist?
                       - "true" / "false" — literal booleans

        Returns:
            Boolean result.
        """
        condition = condition.strip()

        if condition.lower() == 'true':
            return True
        if condition.lower() == 'false':
            return False

        if condition.startswith('text:'):
            return self._resolve_text(condition[5:].strip())
        elif condition.startswith('text_contains:'):
            return self._resolve_text_contains(condition[14:].strip())
        elif condition.startswith('img:') or condition.startswith('image:'):
            prefix_len = 4 if condition.startswith('img:') else 6
            return self._resolve_image(condition[prefix_len:].strip())
        elif condition.startswith('color:'):
            return self._resolve_color(condition[6:].strip())
        elif condition.startswith('var:') or condition.startswith('variable:'):
            prefix_len = 4 if condition.startswith('var:') else 9
            return self._resolve_variable(condition[prefix_len:].strip())
        elif condition.startswith('proc:'):
            return self._resolve_process(condition[5:].strip())
        elif condition.startswith('win:') or condition.startswith('window:'):
            prefix_len = 4 if condition.startswith('win:') else 7
            return self._resolve_window(condition[prefix_len:].strip())
        elif condition.startswith('not:'):
            return not self.resolve(condition[4:].strip())
        elif condition.startswith('and:'):
            parts = condition[4:].strip().split(',')
            return all(self.resolve(p.strip()) for p in parts)
        elif condition.startswith('or:'):
            parts = condition[3:].strip().split(',')
            return any(self.resolve(p.strip()) for p in parts)
        else:
            # Treat as text search by default
            return self._resolve_text(condition)

    def _resolve_text(self, text: str) -> bool:
        """Check if text appears on screen with sufficient confidence."""
        matches = self.ocr.find_text(text, fuzzy=True)
        if matches:
            _, conf, _ = matches[0]
            return conf > 0.3
        return False

    def _resolve_text_contains(self, text: str) -> bool:
        """Check if any text on screen contains the substring."""
        all_texts = self.ocr.recognize()
        for detected, conf, box in all_texts:
            if conf > 0.3 and text.lower() in detected.lower():
                return True
        return False

    def _resolve_image(self, expr: str) -> bool:
        """Check if a template image appears on screen.

        Format: "path/to/template.png" or "path.png,0.85" (with threshold)
        """
        parts = expr.rsplit(',', 1)
        path = parts[0].strip()
        similarity = float(parts[1]) if len(parts) > 1 else 0.85

        found, _, _ = self.matcher.find_best(
            template_path=path, similarity=similarity
        )
        return found

    def _resolve_color(self, expr: str) -> bool:
        """Check color condition.

        Format: "x,y,w,h,lower_h,lower_s,lower_v,upper_h,upper_s,upper_v,threshold"
        Or: "(x,y,w,h)#(h,s,v),(tolerance)"
        """
        try:
            if '#' in expr:
                # Format: (x,y,w,h)#(h,s,v),tolerance
                region_part, rest = expr.split('#', 1)
                color_part = rest.split(',')[0] if ',' in rest else rest
                tolerance_str = rest.split(',')[1] if ',' in rest else '30'

                region_part = region_part.strip('() ')
                color_part = color_part.strip('() ')

                rx, ry, rw, rh = map(int, region_part.split(','))
                th, ts, tv = map(int, color_part.split(','))
                tolerance = float(tolerance_str)

                return self.color.check_color(
                    rx, ry, rw, rh,
                    (th, ts, tv), tolerance=tolerance
                )
            return False
        except Exception:
            return False

    def _resolve_variable(self, expr: str) -> bool:
        """Resolve a variable condition.

        Format: "name==value", "name>100", "name exists", "name"
        """
        operators = ['>=', '<=', '!=', '==', '>', '<']
        for op in operators:
            if op in expr:
                parts = expr.split(op, 1)
                return self.variables.evaluate_condition(
                    parts[0].strip(), op, parts[1].strip()
                )

        # Simple existence check
        return self.variables.has(expr.strip())

    def _resolve_process(self, process_name: str) -> bool:
        """Check if a process is running."""
        from system.process import ProcessDetector
        detector = ProcessDetector()
        return detector.is_running(process_name)

    def _resolve_window(self, title: str) -> bool:
        """Check if a window exists."""
        from system.window import WindowManager
        wm = WindowManager()
        return wm.window_exists(title)
