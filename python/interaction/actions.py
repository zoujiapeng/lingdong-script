"""
ActionHandler: DSL 执行器的底层动作回调。
将 executor 派发的 action 转为实际系统操作（鼠标、键盘、截图等）。
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

# Lazy imports — fail gracefully per action rather than at import time
_pyautogui = None
_has_display = False


def _ensure_pyautogui():
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui as _m
            _m.FAILSAFE = True
            _m.PAUSE = 0.05
            _pyautogui = _m
        except ImportError:
            raise RuntimeError("pyautogui not installed: pip install pyautogui")
    return _pyautogui


class ActionHandler:
    """DSL executor 底层动作回调。
    接收 action 名称和参数，调用 pyautogui/OS API 执行实际操作。
    可通过 __call__ 直接调用，兼容 DSLExecutor._call_action 的签名。
    """

    def __call__(self, action: str, **kwargs) -> any:
        handler = getattr(self, f"_exec_{action}", None)
        if handler is None:
            # 降级到 feature registry
            return self._exec_via_registry(action, **kwargs)
        try:
            return handler(**kwargs)
        except Exception as e:
            logger.warning("ActionHandler.%s failed: %s", action, e)
            raise

    # ── 基础动作 ─────────────────────────────────────────────

    def _exec_click(self, target=None, x=None, y=None, button="left", **kw):
        p = _ensure_pyautogui()
        if target is not None:
            p.click(*target)
        elif x is not None and y is not None:
            p.click(x, y)
        else:
            p.click()
        return True

    def _exec_right_click(self, target=None, x=None, y=None, **kw):
        p = _ensure_pyautogui()
        if target is not None:
            p.rightClick(*target)
        elif x is not None and y is not None:
            p.rightClick(x, y)
        else:
            p.rightClick()
        return True

    def _exec_middle_click(self, target=None, x=None, y=None, **kw):
        p = _ensure_pyautogui()
        if target is not None:
            p.middleClick(*target)
        elif x is not None and y is not None:
            p.middleClick(x, y)
        else:
            p.middleClick()
        return True

    def _exec_triple_click(self, target=None, x=None, y=None, **kw):
        p = _ensure_pyautogui()
        if target is not None:
            p.tripleClick(*target)
        elif x is not None and y is not None:
            p.tripleClick(x, y)
        return True

    def _exec_type(self, text="", **kw):
        p = _ensure_pyautogui()
        p.typewrite(text)
        return True

    def _exec_hotkey(self, keys=None, **kw):
        p = _ensure_pyautogui()
        if isinstance(keys, list):
            p.hotkey(*keys)
        elif keys:
            p.hotkey(keys)
        return True

    def _exec_delay(self, duration=0.5, **kw):
        time.sleep(float(duration))
        return True

    def _exec_wait(self, condition=None, timeout=10, **kw):
        if condition is None or str(condition).strip().lower() == "true":
            time.sleep(timeout if timeout < 30 else 30)
            return True
        # 其他条件通过 feature registry 处理
        return self._exec_via_registry("wait", condition=condition, timeout=timeout)

    def _exec_launch(self, target="", **kw):
        import subprocess
        import shlex
        try:
            if not target or not isinstance(target, str):
                return False

            # Use program registry for name resolution (PATH + App Paths + cache)
            from features.program_registry import resolve_program
            resolved = resolve_program(target) or target

            args = shlex.split(resolved, posix=False)
            subprocess.Popen(args, shell=False)
        except FileNotFoundError:
            # Fallback: shell=True resolves via Windows App Paths registry
            try:
                subprocess.Popen(target, shell=True)
            except Exception as e2:
                logger.error("Launch fallback failed: target=%r error=%s", target, e2)
                return False
        except Exception as e:
            logger.error("Launch failed: target=%r error=%s", target, e)
            return False
        return True

    def _exec_screenshot(self, dest=None, **kw):
        p = _ensure_pyautogui()
        if dest:
            p.screenshot(dest)
        else:
            import tempfile, os
            path = os.path.join(tempfile.gettempdir(), "lobster_screenshot.png")
            p.screenshot(path)
        return True

    def _exec_scroll(self, clicks=1, **kw):
        p = _ensure_pyautogui()
        p.scroll(int(clicks))
        return True

    def _exec_swipe(self, start_x=0, start_y=0, end_x=0, end_y=0, duration=0.3, **kw):
        p = _ensure_pyautogui()
        p.moveTo(start_x, start_y, duration=0.1)
        p.drag(end_x - start_x, end_y - start_y, duration=duration, button='left')
        return True

    def _exec_focus_window(self, title="", **kw):
        try:
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle(title)
            if windows:
                windows[0].activate()
                return True
            # 常见应用多语言标题兜底
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
        p = _ensure_pyautogui()
        s = strategy if isinstance(strategy, list) else [strategy]
        for strat in s:
            if strat in ("auto", "esc"):
                p.press("esc"); time.sleep(0.3)
            if strat in ("auto", "close_btn"):
                _, sw = p.size()
                p.click(sw - 15, 15); time.sleep(0.3)
            if strat in ("auto", "click_away"):
                sw, sh = p.size()
                p.click(sw // 2, int(sh * 0.65)); time.sleep(0.3)
        return True

    def _exec_check_condition(self, condition="", **kw):
        """文本/图片/颜色/变量条件检查 — 委托给 feature registry"""
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
            return True  # 变量条件由 executor 自己处理
        return True

    # ── 通过 feature registry 执行 ──────────────────────────

    _registry = None

    def _exec_via_registry(self, action: str, **kwargs) -> any:
        """通过 @feature 注册表调用其他动作"""
        if self._registry is None:
            try:
                from features.registry import registry as r
                self.__class__._registry = r
            except ImportError:
                raise RuntimeError(f"ActionHandler: feature registry not available for '{action}'")

        spec = self._registry.get(action)
        if spec is None:
            raise ValueError(f"ActionHandler: unknown action '{action}'")

        # 尝试用 name 参数获取 feature 的原始函数
        func = getattr(spec, "func", None)  # FeatureSpec.func 指向原始 @feature 函数
        if func is not None:
            return func(**kwargs)

        # 兜底：通过 execute 方法，返回 handler 的实际结果（而非包装 dict）
        execute_result = self._registry.execute(action, **kwargs)
        if isinstance(execute_result, dict):
            return execute_result.get("result", execute_result)
        return execute_result
