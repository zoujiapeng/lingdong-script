"""
动作名称 → @feature 名称映射表

DSLExecutor 使用简短动作名（click/type/launch 等）调用 action_handler。
此映射表桥接动作名与 @feature 注册表中的完整功能名称。
同时提供 make_action_handler() 工厂函数，用 registry.execute 构建 handler。

优先级链（2026-05-17 重构）:
  RPA.Windows (desktop_actions) - 控件级别定位，最可靠
  → uiautomation (yinkaisheng) - 按文本找控件
  → win32api - 像素坐标操作
  → pyautogui - 截图降级
"""
from __future__ import annotations

import logging

logger = logging.getLogger("action_map")

# DSLExecutor 动作名 → @feature 名称
ACTION_TO_FEATURE: dict[str, str] = {
    "click": "click_target",
    "type": "type_text",
    "launch": "launch_program",
    "wait": "wait_action",
    "swipe": "swipe_action",
    "run_macro": "macro_playback",
    "ocr_find": "ocr_find_text",
    "ocr_extract": "ocr_extract_all",
    "image_find": "find_image",
    "region_wait": "wait_region",
    "drag": "drag",
    "scroll": "scroll",
    "screenshot": "screenshot",
    "wait_screen_change": "wait_screen_change",
    "wait_screen_stable": "wait_screen_stable",
    "hotkey": "hotkey",
    "focus_window": "focus_window",
    "region_select": "region_select",
    "popup_close": "popup_close",
    "color_check": "color_check",
    "find_canvas": "find_canvas",
}

# RPA.Windows desktop_actions 方法映射（优先使用）
_RPA_ACTION_MAP: dict[str, str] = {
    "click": "click_coordinates",
    "type": "type_text",
    "launch": "launch_program",
    "hotkey": "send_keys",
    "scroll": "scroll",
    "drag": "drag_and_drop",
    "focus_window": "focus_window",
    "screenshot": "screenshot",
    "wait": None,  # time.sleep, no RPA equivalent
}

# Agent controller step_type → @feature 名称
STEP_TYPE_TO_FEATURE: dict[str, str | None] = {
    "click": "click_target",
    "image_click": "image_click",
    "scroll": "scroll",
    "run_command": "run_command",
    "launch": "launch_program",
    "focus": "focus_window",
    "screenshot": "screenshot",
    "drag": "drag",
    "file_read": "file_read",
    "file_write": "file_write",
    "mouse_move": "mouse_move",
    "hotkey": "hotkey",
    "wait": None,
    "type": None,
    "text_click": None,
    "file_save": None,
    "condition": None,
}


def _rpa_handler(action: str, **kwargs) -> object | None:
    """Try to handle action via RPA.Windows (desktop_actions).
    Returns None if RPA is not available or action not supported.
    """
    method_name = _RPA_ACTION_MAP.get(action)
    if method_name is None:
        return None

    try:
        from rpa_keywords.desktop_actions import desktop_actions
        rpa = desktop_actions()
        method = getattr(rpa, method_name, None)
        if method is None:
            return None

        # Map common kwargs to desktop_actions method parameters
        if action == "click":
            x = kwargs.get("x")
            y = kwargs.get("y")
            target = kwargs.get("target")
            button = kwargs.get("button", "left")

            # If x,y provided as kwargs, use directly
            if x is not None and y is not None:
                return rpa.click_coordinates(int(x), int(y), button)

            # If target is text coordinates like "100,200"
            if target and isinstance(target, str):
                try:
                    parts = target.replace(",", " ").split()
                    if len(parts) == 2:
                        return rpa.click_coordinates(int(parts[0]), int(parts[1]), button)
                except (ValueError, TypeError):
                    pass
                # Target is text → use click_element with name locator
                return rpa.click_element(f"name:{target}")

            return None

        elif action == "type":
            text = kwargs.get("text", "")
            if text:
                return method(text)
            return None

        elif action == "hotkey":
            keys = kwargs.get("keys", "")
            if isinstance(keys, list):
                keys = "+".join(keys)
            if keys:
                return rpa.send_keys(keys)
            return None

        elif action == "scroll":
            clicks = kwargs.get("clicks", 3)
            direction = "down" if clicks >= 0 else "up"
            return method(direction, abs(clicks))

        elif action == "launch":
            target = kwargs.get("target", "")
            if target:
                return method(target)
            return None

        elif action == "focus_window":
            title = kwargs.get("title", "")
            if title:
                return method(title)
            return None

        elif action == "screenshot":
            dest = kwargs.get("dest")
            return method(dest or "")

        elif action == "drag":
            source = kwargs.get("from_target", "")
            target = kwargs.get("to_target", "")
            if source and target:
                return method(source, target)
            return None

        return None
    except ImportError as e:
        logger.debug("RPA.Windows not available, fallback to old path: %s", e)
        return None
    except Exception as e:
        logger.debug("RPA.Windows action failed, fallback to old path: %s", e)
        return None


def resolve_feature(step_type: str) -> str | None:
    """将 step_type 解析为 feature 名称，None 表示需特殊处理。"""
    return STEP_TYPE_TO_FEATURE.get(step_type) or (
        step_type if step_type in STEP_TYPE_TO_FEATURE else None
    )


def make_action_handler():
    """用 registry.execute 构建 DSLExecutor 可用的 action_handler。

    优先级: RPA.Windows → registry.execute (@feature → ActionHandler)

    返回: (action: str, **kwargs) → Any 的可调用对象
    """
    from features.registry import registry

    def handler(action: str, **kwargs) -> object:
        kwargs.pop("ctx", None)

        # ── 内联处理的 DSL 内部动作 ──
        if action == "delay":
            import time
            duration = kwargs.get("duration", 1)
            time.sleep(duration)
            return True

        if action == "check_condition":
            return _eval_condition(kwargs.get("condition", ""))

        if action == "template_capture":
            return True

        # ── Priority 1: RPA.Windows ──
        try:
            rpa_result = _rpa_handler(action, **kwargs)
            if rpa_result is not None:
                return rpa_result
        except Exception as e:
            logger.debug("RPA.Windows handler failed, falling back: %s", e)

        # ── Priority 2: @feature registry (uiautomation/win32) ──
        feature_name = ACTION_TO_FEATURE.get(action, action)

        spec = registry.get(feature_name)
        if spec is None:
            raise RuntimeError(f"Action '{action}': feature '{feature_name}' not found")
        known = {p.name for p in spec.params}
        filtered = {k: v for k, v in kwargs.items() if k in known}

        result = registry.execute(feature_name, **filtered)

        if not result.get("success", False):
            raise RuntimeError(result.get("error", f"Action '{action}' failed"))

        return result.get("result", True)

    return handler


def _eval_condition(condition: str) -> bool:
    """评估 DSL 条件表达式。"""
    c = condition.strip()
    cl = c.lower()

    if cl in ("true", "1", "yes"):
        return True
    if cl in ("false", "0", "no"):
        return False

    if c.startswith("text:"):
        query = c[5:].strip().strip('"').strip("'")
        from features.registry import registry
        r = registry.execute("ocr_find_text", query=query)
        return bool(r.get("result")) if r.get("success") else False

    if c.startswith("img:"):
        target = c[4:].strip().strip('"').strip("'")
        from features.registry import registry
        r = registry.execute("find_image", target=target)
        return bool(r.get("result")) if r.get("success") else False

    if c.startswith("color:"):
        parts = c[6:].strip().strip('"').split(",")
        if len(parts) >= 3:
            from features.registry import registry
            r = registry.execute("detect_color", h=int(parts[0]), s=int(parts[1]), v=int(parts[2]))
            return bool(r.get("result")) if r.get("success") else False

    return False
