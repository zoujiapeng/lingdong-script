"""
ActionHandler: DSL 执行器的底层动作回调。

Priority chain:
  uiautomation (control finding by name/text)
  → win32api (mouse/keyboard/window/screenshot)
  → pyautogui (screenshot fallback only)

Files originally based on pywinauto removed per 2026-05-16 refactoring:
  pywinauto is dead (6 years no feature updates, 538 open issues).
  Replaced by uiautomation (yinkaisheng, active) + win32api (native).
"""
import logging
import os
import time
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)

# Lazy imports — fail gracefully per action
_win32 = None
_uia = None
_pyautogui = None


def _ensure_win32():
    global _win32
    if _win32 is None:
        try:
            import win32api as _a
            import win32con as _c
            import win32gui as _g
            import win32ui as _u
            _win32 = dict(api=_a, con=_c, gui=_g, ui=_u)
        except ImportError:
            raise RuntimeError("pywin32 not installed")
    return _win32


def _ensure_uia():
    global _uia
    if _uia is None:
        try:
            import uiautomation as auto
            _uia = auto
        except ImportError:
            raise RuntimeError("uiautomation not installed")
    return _uia


def _ensure_pyautogui():
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui as m
            m.FAILSAFE = True
            m.PAUSE = 0.05
            _pyautogui = m
        except ImportError:
            raise RuntimeError("pyautogui not installed")
    return _pyautogui


# ── win32api Mouse Helpers ──────────────────────────────────────

def _win32_set_cursor(x, y):
    w32 = _ensure_win32()
    w32['api'].SetCursorPos((int(x), int(y)))


def _win32_click(x, y, button="left"):
    w32 = _ensure_win32()
    _win32_set_cursor(x, y)
    if button == "left":
        down, up = w32['con'].MOUSEEVENTF_LEFTDOWN, w32['con'].MOUSEEVENTF_LEFTUP
    elif button == "right":
        down, up = w32['con'].MOUSEEVENTF_RIGHTDOWN, w32['con'].MOUSEEVENTF_RIGHTUP
    elif button == "middle":
        down, up = w32['con'].MOUSEEVENTF_MIDDLEDOWN, w32['con'].MOUSEEVENTF_MIDDLEUP
    else:
        down, up = w32['con'].MOUSEEVENTF_LEFTDOWN, w32['con'].MOUSEEVENTF_LEFTUP
    w32['api'].mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.01)
    w32['api'].mouse_event(up, 0, 0, 0, 0)
    return True


def _win32_scroll(clicks):
    w32 = _ensure_win32()
    w32['api'].mouse_event(w32['con'].MOUSEEVENTF_WHEEL, 0, 0, int(clicks * 120), 0)
    return True


def _win32_screenshot(dest):
    """Capture screen via win32 → PIL → PNG. Only function that may keep pyautogui fallback."""
    w32 = _ensure_win32()
    hwnd_dc = w32['gui'].GetDC(0)
    mfc_dc = w32['ui'].CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    width = w32['api'].GetSystemMetrics(w32['con'].SM_CXSCREEN)
    height = w32['api'].GetSystemMetrics(w32['con'].SM_CYSCREEN)
    bitmap = w32['ui'].CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bitmap)
    save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), w32['con'].SRCCOPY)

    from PIL import Image
    bmp_info = bitmap.GetInfo()
    bmp_bits = bitmap.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                           bmp_bits, "raw", "BGRX", 0, 1)
    img.save(dest, "PNG")

    w32['gui'].DeleteObject(bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    w32['gui'].ReleaseDC(0, hwnd_dc)
    return True


# ── win32api Hotkey Helpers ─────────────────────────────────────

_VK_MAP = {
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "menu": 0x12,
    "shift": 0x10,
    "win": 0x5B, "windows": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "back": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "space": 0x20,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B, "f13": 0x7C, "f14": 0x7D, "f15": 0x7E,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "pause": 0x13, "break": 0x13,
}


def _win32_hotkey(*keys):
    """Send hotkey via win32api keybd_event (sequenced press, reversed release)."""
    w32 = _ensure_win32()
    vks = []
    for k in keys:
        lower = k.strip().lower()
        if lower in _VK_MAP:
            vks.append(_VK_MAP[lower])
        elif len(k) == 1:
            vks.append(ord(k.upper()))
        else:
            logger.warning("Unknown key in hotkey: %s", k)
            return False

    for vk in vks:
        w32['api'].keybd_event(vk, 0, 0, 0)
        time.sleep(0.01)
    for vk in reversed(vks):
        w32['api'].keybd_event(vk, 0, w32['con'].KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)
    return True


# ── uiautomation Helpers ────────────────────────────────────────

def _uia_find_control(name, ctrl_type=None, timeout=3):
    """Find a UI control by name. Returns control or None."""
    uia = _ensure_uia()
    root = uia.GetRootControl()
    if not root:
        return None
    condition = lambda c, d: c.Name and name in c.Name
    deadline = time.time() + timeout
    while time.time() < deadline:
        ctrl = uia.FindControl(root, condition)
        if ctrl:
            return ctrl
        time.sleep(0.2)
    return None


def _uia_click_by_name(name, timeout=3):
    """Find control by name and click it via uiautomation."""
    ctrl = _uia_find_control(name, timeout=timeout)
    if ctrl:
        try:
            ctrl.Click()
            return True
        except Exception:
            pass
    return False


def _uia_send_keys(text):
    """Type text via uiautomation SendKeys (handles Unicode well)."""
    uia = _ensure_uia()
    uia.SendKeys(text, wait_time=0.01)
    return True


# ════════════════════════════════════════════════════════════════
#  ActionHandler
# ════════════════════════════════════════════════════════════════

class ActionHandler:
    """DSL executor 底层动作回调。

    优先级: uiautomation (按文本找控件) → win32api (坐标操作)
           → pyautogui (截图降级)
    """

    def __call__(self, action: str, **kwargs) -> any:
        handler = getattr(self, f"_exec_{action}", None)
        if handler is None:
            return self._exec_via_registry(action, **kwargs)
        try:
            return handler(**kwargs)
        except Exception as e:
            logger.warning("ActionHandler.%s failed: %s", action, e)
            raise

    # ── Click / Right / Middle / Triple ─────────────────────────

    def _exec_click(self, target=None, x=None, y=None, button="left", **kw):
        learn_target_id = kw.get("learn_target_id") or kw.get("target_id")
        learn_label = kw.get("learn_label") or (target if isinstance(target, str) else "")
        # Priority 1: uiautomation click by target name
        if target and isinstance(target, str) and not target.startswith("("):
            if _uia_click_by_name(target):
                self._learn_click_target(learn_target_id, learn_label or target, action="click", uia_name=target, button=button)
                return True
        # Priority 2: win32api coordinate click
        cx, cy = None, None
        if x is not None and y is not None:
            cx, cy = int(x), int(y)
        elif target is not None and isinstance(target, (list, tuple)) and len(target) >= 2:
            cx, cy = int(target[0]), int(target[1])
        if cx is not None:
            ok = _win32_click(cx, cy, button)
            if ok:
                self._learn_click_target(learn_target_id, learn_label, x=cx, y=cy, action="click", button=button)
            return ok
        # Fallback: current position
        cx, cy = _get_cursor_pos()
        ok = _win32_click(cx, cy, button)
        if ok:
            self._learn_click_target(learn_target_id, learn_label, x=cx, y=cy, action="click", button=button)
        return ok

    def _exec_right_click(self, target=None, x=None, y=None, **kw):
        return self._exec_click(target=target, x=x, y=y, button="right", **kw)

    def _exec_middle_click(self, target=None, x=None, y=None, **kw):
        return self._exec_click(target=target, x=x, y=y, button="middle", **kw)

    def _exec_triple_click(self, target=None, x=None, y=None, **kw):
        cx, cy = None, None
        if x is not None and y is not None:
            cx, cy = int(x), int(y)
        elif target is not None and isinstance(target, (list, tuple)) and len(target) >= 2:
            cx, cy = int(target[0]), int(target[1])
        if cx is None:
            cx, cy = _get_cursor_pos()
        for _ in range(3):
            _win32_click(cx, cy)
            time.sleep(0.05)
        return True

    # ── Type / Hotkey ───────────────────────────────────────────

    def _exec_type(self, text="", **kw):
        if not text:
            return True
        # uiautomation SendKeys handles Unicode
        try:
            return _uia_send_keys(text)
        except Exception:
            pass
        # Fallback: clipboard paste
        return self._clipboard_type(text)

    def _exec_hotkey(self, keys=None, **kw):
        if not keys:
            return False
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.replace("+", " ").split()]
        return _win32_hotkey(*keys)

    # ── Clipboard typing ────────────────────────────────────────

    def _clipboard_type(self, text):
        """Type text via clipboard copy-paste (best for Chinese)."""
        try:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text)
            time.sleep(0.05)
            _win32_hotkey("ctrl", "v")
            time.sleep(0.1)
            if old is not None:
                try:
                    pyperclip.copy(old)
                except Exception:
                    pass
            return True
        except ImportError:
            return _uia_send_keys(text)

    # ── Wait / Delay ────────────────────────────────────────────

    def _exec_delay(self, duration=0.5, **kw):
        time.sleep(float(duration))
        return True

    def _exec_wait(self, condition=None, timeout=10, **kw):
        if condition is None or str(condition).strip().lower() == "true":
            time.sleep(timeout if timeout < 30 else 30)
            return True
        return self._exec_via_registry("wait", condition=condition, timeout=timeout)

    # ── Launch / Focus / Popup ──────────────────────────────────

    def _exec_launch(self, target="", **kw):
        import subprocess
        import shlex
        try:
            if not target or not isinstance(target, str):
                return False
            from features.program_registry import resolve_program
            resolved = resolve_program(target) or target
            args = shlex.split(resolved, posix=False)
            subprocess.Popen(args, shell=False)
        except FileNotFoundError:
            try:
                subprocess.Popen(target, shell=True)
            except Exception as e2:
                logger.error("Launch fallback failed: target=%r error=%s", target, e2)
                return False
        except Exception as e:
            logger.error("Launch failed: target=%r error=%s", target, e)
            return False
        return True

    def _exec_focus_window(self, title="", **kw):
        # Priority 1: uiautomation find and set focus
        try:
            uia = _ensure_uia()
            root = uia.GetRootControl()
            if root:
                condition = lambda c, d: c.Name and title in c.Name
                ctrl = uia.FindControl(root, condition)
                if ctrl:
                    ctrl.SetFocus()
                    return True
        except Exception:
            pass
        # Priority 2: win32gui EnumWindows
        try:
            w32 = _ensure_win32()
            target_hwnd = [0]

            def enum_cb(hwnd, _unused):
                if w32['gui'].IsWindowVisible(hwnd):
                    t = w32['gui'].GetWindowText(hwnd)
                    if title in t:
                        target_hwnd[0] = hwnd
                        return False  # stop
                return True

            w32['gui'].EnumWindows(enum_cb, None)
            if target_hwnd[0]:
                w32['gui'].SetForegroundWindow(target_hwnd[0])
                return True
        except Exception:
            pass
        # Fallback: pygetwindow
        try:
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle(title)
            if windows:
                windows[0].activate()
                return True
            fallbacks = {
                "画图": "Paint", "Paint": "画图",
                "记事本": "Notepad", "Notepad": "记事本",
                "计算器": "Calculator", "Calculator": "计算器",
            }
            alt = fallbacks.get(title)
            if alt:
                windows = gw.getWindowsWithTitle(alt)
                if windows:
                    windows[0].activate()
                    return True
        except ImportError:
            pass
        return False

    def _exec_popup_close(self, strategy="auto", **kw):
        strategies = strategy if isinstance(strategy, list) else [strategy]
        for s in strategies:
            if s in ("auto", "esc"):
                _win32_hotkey("esc")
                time.sleep(0.3)
            if s in ("auto", "close_btn"):
                w32 = _ensure_win32()
                sw = w32['api'].GetSystemMetrics(w32['con'].SM_CXSCREEN)
                _win32_click(sw - 15, 15)
                time.sleep(0.3)
            if s in ("auto", "click_away"):
                w32 = _ensure_win32()
                sw = w32['api'].GetSystemMetrics(w32['con'].SM_CXSCREEN)
                sh = w32['api'].GetSystemMetrics(w32['con'].SM_CYSCREEN)
                _win32_click(sw // 2, int(sh * 0.65))
                time.sleep(0.3)
        return True

    # ── Screenshot / Scroll / Swipe ─────────────────────────────

    def _exec_screenshot(self, dest=None, **kw):
        try:
            if dest:
                return _win32_screenshot(dest)
            import tempfile
            path = os.path.join(tempfile.gettempdir(), "lobster_screenshot.png")
            return _win32_screenshot(path)
        except Exception:
            p = _ensure_pyautogui()
            if dest:
                p.screenshot(dest)
            else:
                import tempfile
                path = os.path.join(tempfile.gettempdir(), "lobster_screenshot.png")
                p.screenshot(path)
            return True

    def _exec_scroll(self, clicks=1, **kw):
        return _win32_scroll(int(clicks))

    def _exec_swipe(self, start_x=0, start_y=0, end_x=0, end_y=0, duration=0.3, **kw):
        _win32_set_cursor(int(start_x), int(start_y))
        w32 = _ensure_win32()
        w32['api'].mouse_event(w32['con'].MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        steps = max(int(duration / 0.01), 5)
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(start_x + (end_x - start_x) * t)
            cy = int(start_y + (end_y - start_y) * t)
            _win32_set_cursor(cx, cy)
            time.sleep(duration / steps)
        w32['api'].mouse_event(w32['con'].MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True

    def _handle_image_click(
        self,
        targets="",
        similarity=0.9,
        method="single",
        timeout=5,
        region="",
        offset_x=0,
        offset_y=0,
        match_all=False,
        **kw,
    ):
        from features._utils import parse_region
        from perception.vision import TemplateMatcher

        target_names = [Path(t.strip()).stem for t in str(targets or "").split(",") if t.strip()]
        if not target_names:
            return {"success": True, "found": False, "error": "缺少模板名"}

        reg = parse_region(region) if isinstance(region, str) else region
        matcher = TemplateMatcher()
        deadline = time.time() + max(0.0, float(timeout))
        clicked = []
        last_error = "未找到匹配图像"

        while time.time() <= deadline:
            for name in target_names:
                if match_all:
                    matches = matcher.find_all(name, threshold=float(similarity), region=reg)
                    if not matches:
                        continue
                    for (x, y), conf in matches:
                        cx, cy = self._adjust_match_point(x, y, reg, offset_x, offset_y)
                        self._click_by_method(cx, cy, method)
                        clicked.append({"target": name, "x": cx, "y": cy, "confidence": conf})
                    return {"success": True, "found": True, "clicked": clicked, "count": len(clicked)}

                result = matcher.find(name, threshold=float(similarity), region=reg)
                if not result:
                    continue
                (x, y), conf = result
                cx, cy = self._adjust_match_point(x, y, reg, offset_x, offset_y)
                self._click_by_method(cx, cy, method)
                learn_target_id = kw.get("learn_target_id") or kw.get("target_id") or name
                self._learn_click_target(
                    learn_target_id,
                    name,
                    x=cx,
                    y=cy,
                    action="image_click",
                    template_name=name,
                    region=reg,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    confidence=conf,
                    button=method,
                )
                return {"success": True, "found": True, "target": name, "x": cx, "y": cy, "confidence": conf}
            if timeout <= 0:
                break
            time.sleep(0.2)

        return {"success": False, "found": False, "error": last_error, "targets": target_names}

    def _adjust_match_point(self, x, y, region, offset_x, offset_y):
        if region:
            x += int(region[0])
            y += int(region[1])
        return int(x + int(offset_x)), int(y + int(offset_y))

    def _click_by_method(self, x, y, method):
        method = str(method or "single").lower()
        button = "right" if method == "right" else "left"
        _win32_click(x, y, button)
        if method == "double":
            time.sleep(0.05)
            _win32_click(x, y, button)
        return True

    def _learn_click_target(
        self,
        target_id=None,
        label="",
        x=None,
        y=None,
        action="click",
        uia_name="",
        template_name="",
        region=None,
        offset_x=0,
        offset_y=0,
        confidence=0.0,
        button="left",
    ):
        target_id = str(target_id or "").strip()
        if not target_id:
            return
        try:
            from engine.store import get_store
            app = self._active_app_context()
            template_path = ""
            if template_name:
                template_path = str(Path("templates") / f"{Path(template_name).stem}.png")
            elif x is not None and y is not None:
                template_path = self._capture_click_template(target_id, int(x), int(y))
            target = {
                "target_id": target_id,
                "app": app,
                "action": action,
                "template_path": template_path,
                "ocr": {"text": label} if label else {},
                "uia": {"name": uia_name} if uia_name else {},
                "offset": {"x": int(offset_x or 0), "y": int(offset_y or 0)},
                "context": {"region": list(region) if region else None, "button": button, "confidence": float(confidence or 0)},
                "success_count": 1,
                "failure_count": 0,
                "last_used_at": time.time(),
            }
            get_store().save_learned_target(target)
        except Exception as e:
            logger.debug("learn target failed: %s", e)

    def _capture_click_template(self, target_id, x, y, size=64):
        try:
            import cv2
            from perception.vision import ScreenCapture
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in target_id)[:80]
            template_dir = Path(__file__).resolve().parents[2] / "templates"
            template_dir.mkdir(exist_ok=True)
            half = size // 2
            region = (max(0, x - half), max(0, y - half), size, size)
            img = ScreenCapture.capture(region)
            if img is None or img.size == 0:
                return ""
            path = template_dir / f"learned_{safe}.png"
            cv2.imwrite(str(path), img)
            return str(path)
        except Exception:
            return ""

    def _active_app_context(self):
        try:
            w32 = _ensure_win32()
            hwnd = w32['gui'].GetForegroundWindow()
            title = w32['gui'].GetWindowText(hwnd) if hwnd else ""
            return {"hwnd": hwnd, "title": title}
        except Exception:
            return {}

    # ── Condition check ─────────────────────────────────────────

    def _exec_check_condition(self, condition="", **kw):
        condition = str(condition).strip()
        if condition.lower() == "true":
            return True
        if condition.lower().startswith("text:"):
            r = self._exec_via_registry("ocr_find", query=condition[5:].strip())
            return r.get("success", False) if isinstance(r, dict) else bool(r)
        if condition.lower().startswith("img:"):
            r = self._exec_via_registry("image_find", args=condition[4:].strip())
            return r.get("success", False) if isinstance(r, dict) else bool(r)
        if condition.lower().startswith("color:"):
            r = self._exec_via_registry("check_condition", condition=condition, **kw)
            return r.get("success", False) if isinstance(r, dict) else bool(r)
        if condition.lower().startswith("var:"):
            return True
        return True

    # ── Registry fallback ───────────────────────────────────────

    _registry = None

    def _exec_via_registry(self, action: str, **kwargs) -> any:
        if self._registry is None:
            try:
                from features.registry import registry as r
                self.__class__._registry = r
            except ImportError:
                raise RuntimeError(f"ActionHandler: registry not available for '{action}'")

        spec = self._registry.get(action)
        if spec is None:
            raise ValueError(f"ActionHandler: unknown action '{action}'")

        func = getattr(spec, "func", None)
        if func is not None:
            return func(**kwargs)

        execute_result = self._registry.execute(action, **kwargs)
        if isinstance(execute_result, dict):
            return execute_result.get("result", execute_result)
        return execute_result


# ── Module-level helper ─────────────────────────────────────────

def _get_cursor_pos():
    w32 = _ensure_win32()
    pos = w32['api'].GetCursorPos()
    return pos
