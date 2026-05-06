"""
条件评估引擎 — 为调度守卫条件和 DSL IF/WHILE 提供统一评估。

两种使用方式:
  1. evaluate(rules, operator) — 评估条件规则列表
  2. evaluate_one(type, params) — 评估单个条件

设计原则:
  - 内置条件直接实现（窗口、进程、时间等）
  - 视觉条件（图像、OCR、颜色）通过 features.registry 桥接
  - 每个条件独立 try/except，失败时返回 True（宽松默认）
"""
from __future__ import annotations
import datetime
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("lobster.conditions")

# ── 条件注册表 ─────────────────────────────────────────────

_CONDITION_REGISTRY: dict[str, dict] = {}

def register_condition(
    type_name: str,
    description: str,
    params: list[tuple[str, str, str]],
    handler: Callable,
):
    """注册一个条件类型"""
    _CONDITION_REGISTRY[type_name] = {
        "type": type_name,
        "description": description,
        "params": [{"name": p[0], "type": p[1], "description": p[2]} for p in params],
        "handler": handler,
    }

def condition(type_name: str, description: str, params: list = None):
    """装饰器：注册条件类型"""
    def decorator(fn):
        register_condition(type_name, description, params or [], fn)
        return fn
    return decorator

def list_condition_types() -> list[dict]:
    """列出所有注册的条件类型（供 MCP 工具 / AI 上下文使用）"""
    return [{
        "type": v["type"],
        "description": v["description"],
        "params": v["params"],
    } for v in _CONDITION_REGISTRY.values()]


# ── 评估入口 ──────────────────────────────────────────────

def evaluate(rules: list[dict], operator: str = "AND", context: dict = None) -> bool:
    """评估一组条件规则，返回 True/False

    Args:
        rules: [{type: str, params: dict}, ...]
        operator: "AND" | "OR"
        context: 可选上下文（当前未使用，为未来扩展预留）
    """
    if not rules:
        return True
    results = [_eval_single(r) for r in rules]
    if operator == "AND":
        return all(results)
    elif operator == "OR":
        return any(results)
    return all(results)  # default AND

def evaluate_one(rule_type: str, params: dict = None) -> bool:
    """评估单个条件"""
    return _eval_single({"type": rule_type, "params": params or {}})


def _eval_single(rule: dict) -> bool:
    entry = _CONDITION_REGISTRY.get(rule.get("type", ""))
    if not entry:
        logger.warning(f"未知条件类型: {rule.get('type')}，返回 True")
        return True
    try:
        return bool(entry["handler"](**(rule.get("params") or {})))
    except Exception as e:
        logger.error(f"条件评估失败 [{rule.get('type')}]: {e}")
        return True  # 宽松默认：失败时视为通过，不阻塞调度


# ═══════════════════════════════════════════════════════════════
# 内置条件实现
# ═══════════════════════════════════════════════════════════════

@condition("always_true", "始终为真（无条件通过）")
def _always_true() -> bool:
    return True


@condition("window_exists", "窗口是否存在（按标题模糊匹配）", [
    ("title", "string", "窗口标题（模糊匹配，含此字符串即可）"),
])
def _window_exists(title: str = "") -> bool:
    if not title:
        return False
    try:
        import win32gui
        found = []
        def cb(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                wtext = win32gui.GetWindowText(hwnd) or ""
                if title.lower() in wtext.lower():
                    ctx.append(True)
        win32gui.EnumWindows(cb, found)
        return len(found) > 0
    except ImportError:
        logger.warning("win32gui 不可用，window_exists 返回 True")
        return True


@condition("process_running", "进程是否正在运行", [
    ("name", "string", "进程名称（如 chrome.exe，模糊匹配）"),
])
def _process_running(name: str = "") -> bool:
    if not name:
        return False
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and name.lower() in proc.info["name"].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False
    except ImportError:
        logger.warning("psutil 不可用，process_running 返回 True")
        return True


@condition("is_weekday", "是否为工作日（周一至周五）")
def _is_weekday() -> bool:
    return datetime.datetime.now().weekday() < 5


@condition("is_weekend", "是否为周末")
def _is_weekend() -> bool:
    return datetime.datetime.now().weekday() >= 5


@condition("day_of_week", "匹配星期几", [
    ("days", "list[int]", "匹配的星期列表（0=周一, 6=周日），如 [0,1,2,3,4] 表示工作日"),
])
def _day_of_week(days: list = None) -> bool:
    if not days:
        return False
    return datetime.datetime.now().weekday() in [int(d) for d in days]


@condition("time_between", "当前时间是否在指定时段内", [
    ("start_hour", "int", "起始小时（0-23）"),
    ("end_hour", "int", "结束小时（0-23）"),
    ("start_minute", "int", "起始分钟（0-59，可选）",),
    ("end_minute", "int", "结束分钟（0-59，可选）"),
])
def _time_between(start_hour: int = 0, end_hour: int = 0,
                  start_minute: int = 0, end_minute: int = 0) -> bool:
    now = datetime.datetime.now()
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if start <= end:
        return start <= now <= end
    else:  # 跨天（如 22:00 ~ 06:00）
        return now >= start or now <= end


# ═══════════════════════════════════════════════════════════════
# 视觉条件（通过 feature registry 桥接）
# ═══════════════════════════════════════════════════════════════

@condition("image_exists", "图片是否在屏幕上出现", [
    ("path", "string", "模板图片路径"),
    ("similarity", "number", "相似度阈值 0-100，默认 90"),
    ("timeout", "number", "超时秒数，默认 5"),
])
def _image_exists(path: str = "", similarity: int = 90, timeout: int = 5) -> bool:
    if not path:
        return False
    try:
        from features.registry import registry
        result = registry.execute("image_find", path=path, similarity=similarity, timeout=timeout)
        return bool(result and result.get("success"))
    except Exception as e:
        logger.warning(f"image_exists 评估失败: {e}")
        return True


@condition("text_exists", "文字是否在屏幕上出现（OCR）", [
    ("text", "string", "要查找的文字"),
    ("similarity", "number", "相似度阈值 0-100，默认 80"),
])
def _text_exists(text: str = "", similarity: int = 80) -> bool:
    if not text:
        return False
    try:
        from features.registry import registry
        result = registry.execute("ocr_find", text=text, similarity=similarity)
        return bool(result and result.get("success"))
    except Exception as e:
        logger.warning(f"text_exists 评估失败: {e}")
        return True


@condition("color_match", "区域颜色是否匹配", [
    ("x", "int", "区域左上角 X"),
    ("y", "int", "区域左上角 Y"),
    ("w", "int", "区域宽度"),
    ("h", "int", "区域高度"),
    ("expected_color", "string", "期望颜色（十六进制 #RRGGBB）"),
    ("tolerance", "int", "容差 0-255，默认 30"),
])
def _color_match(x: int = 0, y: int = 0, w: int = 0, h: int = 0,
                 expected_color: str = "", tolerance: int = 30) -> bool:
    if not expected_color:
        return False
    try:
        from features.registry import registry
        result = registry.execute("color_check",
            x=x, y=y, w=w, h=h,
            expected_color=expected_color, tolerance=tolerance)
        return bool(result and result.get("success"))
    except Exception as e:
        logger.warning(f"color_match 评估失败: {e}")
        return True


@condition("scene_match", "屏幕场景是否匹配", [
    ("scene_name", "string", "场景名称（需预先注册）"),
    ("similarity", "number", "相似度阈值 0-100，默认 85"),
])
def _scene_match(scene_name: str = "", similarity: int = 85) -> bool:
    if not scene_name:
        return False
    try:
        from features.registry import registry
        result = registry.execute("scene_condition",
            scene_name=scene_name, similarity=similarity)
        return bool(result and result.get("success"))
    except Exception as e:
        logger.warning(f"scene_match 评估失败: {e}")
        return True
