"""
动作名称 → @feature 名称映射表

DSLExecutor 使用简短动作名（click/type/launch 等）调用 action_handler。
此映射表桥接动作名与 @feature 注册表中的完整功能名称。
同时提供 make_action_handler() 工厂函数，用 registry.execute 构建 handler。
"""
from __future__ import annotations

# DSLExecutor 动作名 → @feature 名称
# 不存在映射的将直接作为 feature name 使用
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
    # 以下名称与 feature 名称一致，无需映射
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
    # DSL 内部动作（没有对应 feature）
    # "delay" — time.sleep，由 handler 内联处理
    # "check_condition" — 条件求值，由 handler 内联处理
    # "template_capture" — 模板截图注册，无对应 feature
}

# Agent controller step_type → @feature 名称
# 与 ACTION_TO_FEATURE 重叠但覆盖更多类型（file_read/run_command 等）
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
    "wait": None,       # time.sleep
    "type": None,       # 剪贴板或键盘输入（有 fallback 逻辑）
    "text_click": None, # OCR 查找 → pyautogui.click
    "file_save": None,  # 保存文件对话框（有 fallback 逻辑）
    "condition": None,  # 条件分支（内部求值）
}


def resolve_feature(step_type: str) -> str | None:
    """将 step_type 解析为 feature 名称，None 表示需特殊处理。"""
    return STEP_TYPE_TO_FEATURE.get(step_type) or (
        step_type if step_type in STEP_TYPE_TO_FEATURE else None
    )


def make_action_handler():
    """用 registry.execute 构建 DSLExecutor 可用的 action_handler。

    返回: (action: str, **kwargs) → Any 的可调用对象
    """
    from features.registry import registry

    def handler(action: str, **kwargs) -> object:
        kwargs.pop("ctx", None)  # DSLExecutor 内部参数

        # ── 内联处理的 DSL 内部动作 ──
        if action == "delay":
            import time
            duration = kwargs.get("duration", 1)
            time.sleep(duration)
            return True

        if action == "check_condition":
            return _eval_condition(kwargs.get("condition", ""))

        if action == "template_capture":
            # 模板截图 — GUI 操作，模拟成功
            return True

        # ── 委派给 @feature registry ──
        feature_name = ACTION_TO_FEATURE.get(action, action)

        # 只传 feature 声明接受的参数，避免多余参数导致 TypeError
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

    # 前缀条件 — 通过 registry 求值
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

    # 未知条件 — 安全返回 False
    return False
