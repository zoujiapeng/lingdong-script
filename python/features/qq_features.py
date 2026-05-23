"""
QQ 适配器功能注册
通过融合感知定位 QQ 界面元素，完成联系人搜索、消息发送。
优先使用 UIA → OCR → 模板匹配，不依赖固定像素坐标。

继承自 AppAdapter 基类，仅覆盖应用特有逻辑。
"""
from __future__ import annotations

import os
import time
import logging
import subprocess

from features.registry import feature, P, TC, FeatureCategory as F
from perception.app_adapter import AppAdapter

logger = logging.getLogger(__name__)

QQ_NT_EXE = r"C:\Program Files\Tencent\QQNT\QQ.exe"

QQ_SEARCH_TIMEOUT = 10  # 融合定位超时（秒）


class QQAdapter(AppAdapter):
    app_name = "qq"
    display_name = "QQ"

    def _match_window(self, hwnd: int, title: str, cls: str) -> bool:
        return title == "QQ" and cls == "Chrome_WidgetWin_1"

    def _launch_command(self) -> list[str] | None:
        exe = QQ_NT_EXE
        if not os.path.exists(exe):
            try:
                from features.program_registry import resolve_program
                resolved = resolve_program("qq")
                if resolved and os.path.exists(resolved):
                    exe = resolved
            except Exception:
                pass
        return [exe] if os.path.exists(exe) else None


_adapter = QQAdapter()


# ── 底层工具函数（保留兼容性，内部委托给 adapter）───────────────────────

def _find_qq_hwnd():
    return _adapter.find_window()


def _bring_to_front(hwnd=None):
    return _adapter.bring_to_front(hwnd)


def _is_qq_hwnd(hwnd):
    if not hwnd:
        return False
    info = _adapter.get_window_info(hwnd)
    if info:
        return True
    # 同进程检查
    qq_hwnd = _adapter.find_window()
    return _adapter._same_process(hwnd, qq_hwnd)


def _is_qq_foreground(hwnd=None):
    return _adapter.is_foreground(hwnd)


def _ensure_qq_foreground(hwnd=None, timeout=2.0):
    return _adapter.ensure_foreground(hwnd, timeout=timeout)


def _get_qq_region():
    info = _adapter.get_window_info()
    if not info:
        return None
    return info.hwnd, (info.left, info.top, info.width, info.height)


def _click_abs(x, y):
    _adapter.click(x, y)


def _qq_hotkey(*keys):
    _adapter.hotkey(*keys)


def _qq_press(key):
    _adapter.press(key)


def _type_via_clipboard(text):
    _adapter.type_via_clipboard(text)


def _wait_screen_ready(hwnd=None, timeout=3.0):
    return _adapter._wait_screen_ready(hwnd, timeout=timeout)


def _is_valid_click_point(x, y):
    return _adapter._is_valid_click_point(x, y)


def _capture_has_content(region=None):
    return _adapter._capture_has_content(region)


def _is_qq_process_running():
    """检查 QQ 进程是否已在运行，防止二次启动。"""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            try:
                if p.info["name"] in ("QQ.exe", "QQEX.exe"):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _launch_qq_internal():
    """启动 QQ。如果进程已在运行则不重复启动，只尝试聚焦窗口。"""
    if _is_qq_process_running():
        logger.info("QQ 进程已在运行，跳过重复启动")
        hwnd = _adapter.find_window()
        if hwnd:
            _adapter.bring_to_front(hwnd)
        return
    cmd = _adapter._launch_command()
    if cmd:
        subprocess.Popen(cmd, shell=False)
        time.sleep(2)


def _wait_qq_window(timeout=20):
    return _adapter.wait_window(timeout=timeout)


# ── 融合定位元素 ─────────────────────────────────────────────────────

def _find_qq_element(query, region="", threshold=0.6, timeout=QQ_SEARCH_TIMEOUT):
    """在 QQ 窗口内融合定位目标。若 QQ 未运行则自动启动。"""
    hwnd = _adapter.find_window()
    if not hwnd:
        # 进程已在运行但窗口未找到 → 等待窗口出现，不重复启动
        if _is_qq_process_running():
            logger.info("QQ 进程已在，等待窗口出现")
            hwnd = _adapter.wait_window(timeout=10)
        else:
            _launch_qq_internal()
            hwnd = _adapter.wait_window(timeout=20)
        if not hwnd:
            from perception.fusion import FusionResult
            return FusionResult(found=False, reason="qq_not_running")
    result = _adapter.find_element(query, timeout=timeout, threshold=threshold)
    return result


def _find_ocr_matches(query, timeout=2, threshold=0.45):
    return _adapter.find_ocr(query, timeout=timeout, threshold=threshold)


# ── 聊天开启辅助 ─────────────────────────────────────────────────────

def _click_result_or_press_enter(query, steps, attempts=3):
    for i in range(attempts):
        _wait_screen_ready(_find_qq_hwnd(), timeout=1.5)

        matches = _find_ocr_matches(query, timeout=1.5, threshold=0.45)
        if matches:
            best = max(matches, key=lambda r: (r.bbox[1], -abs(r.bbox[0])))
            x, y, w, h = best.bbox
            cx = int(x + w // 2)
            cy = int(y + h // 2)
            if _is_valid_click_point(cx, cy):
                _click_abs(cx, cy)
                steps.append({"step": "open_chat", "status": "clicked_ocr_result", "attempt": i + 1, "x": cx, "y": cy, "text": best.text})
            else:
                _qq_press("enter")
                steps.append({"step": "open_chat", "status": "enter_invalid_point", "attempt": i + 1})
        else:
            result = _find_qq_element(query, timeout=2)
            if result.found and _is_valid_click_point(result.center_x, result.center_y):
                cx, cy = result.center_x, result.center_y
                _click_abs(cx, cy)
                steps.append({"step": "open_chat", "status": "clicked_result", "attempt": i + 1, "x": cx, "y": cy, "strategy": result.strategy})
            else:
                _qq_press("enter")
                steps.append({"step": "open_chat", "status": "enter_result", "attempt": i + 1})
        time.sleep(0.8)

        _wait_screen_ready(_find_qq_hwnd(), timeout=1.5)
        send_result = _find_qq_element("发送", timeout=2)
        if send_result.found:
            steps.append({"step": "confirm_chat", "status": "send_visible", "strategy": send_result.strategy})
            return True

        input_result = _find_qq_element("输入", timeout=1)
        if input_result.found:
            steps.append({"step": "confirm_chat", "status": "input_visible", "strategy": input_result.strategy})
            return True

    return False


# ── Features ─────────────────────────────────────────────────────────

@feature(
    name="qq_operation",
    display_name="操作 QQ",
    description="QQ 综合操作：启动/发送消息/查找联系人/查看状态",
    category=F.ACTION,
    params=[
        P("action", "str", "操作类型", example="send_message",
          choices={"launch": "启动QQ", "send_message": "发送消息", "find_contact": "查找联系人", "get_status": "查看状态"},
          default="launch", required=False),
        P("contact", "str", "联系人名称（发送消息/查找联系人时用）", required=False),
        P("message", "str", "消息内容（发送消息时用）", required=False),
        P("timeout", "number", "操作超时秒数", required=False, default=15),
    ],
    returns="dict{success, ...} - 操作结果",
    tags=["qq", "app"],
    hidden=False,
    dsl_keyword="",
)
def qq_operation(action: str = "launch", contact: str = "", message: str = "", timeout: float = 15) -> dict:
    """QQ 综合操作：根据 action 分发到具体的子功能。"""
    if action == "launch":
        return qq_launch(wait=True, timeout=timeout)
    elif action == "send_message":
        if not contact or not message:
            return {"success": False, "error": "发送消息需要填写 contact 和 message 参数"}
        return qq_send_message(contact=contact, message=message, max_wait=timeout)
    elif action == "find_contact":
        if not contact:
            return {"success": False, "error": "查找联系人需要填写 contact 参数"}
        return qq_find_contact(contact=contact, timeout=timeout)
    elif action == "get_status":
        return qq_get_status()
    return {"success": False, "error": f"未知操作: {action}"}


@feature(
    name="qq_launch",
    display_name="启动 QQ",
    description="【QQ·启动】启动 QQNT。如果 QQ 已经在运行，则聚焦到 QQ 窗口",
    category=F.SYSTEM,
    params=[
        P("wait", "boolean", "是否等待窗口出现", required=False, default=True),
        P("timeout", "number", "等待超时秒数", required=False, default=20),
    ],
    returns="dict{hwnd, width, height, left, top} - 窗口信息",
    tags=["qq", "launch", "app"],
    hidden=True,
)
def qq_launch(wait: bool = True, timeout: float = 20) -> dict:
    try:
        hwnd = _adapter.find_window()
        if hwnd:
            _adapter.bring_to_front(hwnd)
            info = _adapter.get_window_info(hwnd)
            return {
                "hwnd": hwnd,
                "left": info.left, "top": info.top,
                "width": info.width, "height": info.height,
                "status": "already_running",
            } if info else {"status": "already_running", "hwnd": hwnd}

        _launch_qq_internal()
        if not wait:
            return {"status": "launched", "hwnd": None}

        hwnd = _adapter.wait_window(timeout=timeout)
        if not hwnd:
            return {"success": False, "error": "QQ 启动超时"}

        info = _adapter.get_window_info(hwnd)
        return {
            "hwnd": hwnd,
            "left": info.left, "top": info.top,
            "width": info.width, "height": info.height,
            "status": "launched",
        } if info else {"hwnd": hwnd, "status": "launched"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="qq_send_message",
    display_name="QQ 发送消息",
    description="【QQ·消息】在 QQ 中搜索联系人并发送消息。使用融合感知定位搜索框和发送按钮，不依赖固定坐标",
    category=F.ACTION,
    params=[
        P("contact", "str", "联系人名称（支持模糊匹配）", example="手机"),
        P("message", "str", "要发送的消息内容", example="你好，这是自动发送的消息！"),
        P("max_wait", "number", "最大等待秒数", required=False, default=15),
    ],
    returns="dict{success, steps, error} - 执行结果",
    tags=["qq", "message", "automation"],
    examples=["qq_send_message contact=手机 message=测试消息"],
    hidden=True,
)
def qq_send_message(contact: str, message: str, max_wait: float = 15) -> dict:
    steps = []
    hwnd = _adapter.find_window()

    # Step 1: 确保 QQ 运行
    if not hwnd:
        steps.append({"step": "launch", "action": "启动 QQ"})
        result = qq_launch(wait=True, timeout=30)
        if not result.get("hwnd"):
            return {"success": False, "steps": steps, "error": "QQ 启动失败"}
        hwnd = result["hwnd"]
        steps.append({"step": "launch", "status": "ok", "hwnd": hwnd})
    else:
        _adapter.bring_to_front(hwnd)
        steps.append({"step": "focus", "status": "ok"})

    # Step 2: 融合定位搜索框
    steps.append({"step": "find_search", "query": "搜索"})
    result = _find_qq_element("搜索", timeout=max_wait)
    if result.found:
        cx, cy = result.center_x, result.center_y
        _click_abs(cx, cy)
        steps.append({"step": "find_search", "status": "found", "x": cx, "y": cy, "strategy": result.strategy})
    else:
        try:
            for _ in range(8):
                _qq_press("tab")
                time.sleep(0.15)
            steps.append({"step": "find_search", "status": "tab_fallback"})
        except Exception as e:
            steps.append({"step": "find_search", "status": "failed", "error": str(e)})
            return {"success": False, "steps": steps, "error": "无法定位搜索框"}

    time.sleep(0.5)

    # Step 3: 输入联系人名称
    steps.append({"step": "search_contact", "contact": contact})
    _type_via_clipboard(contact)
    time.sleep(1)

    # Step 4: 融合定位联系人项
    steps.append({"step": "click_contact", "query": contact})
    result = _find_qq_element(contact, timeout=5)
    if result.found:
        cx, cy = result.center_x, result.center_y
        _click_abs(cx, cy)
        steps.append({"step": "click_contact", "status": "ok", "x": cx, "y": cy, "strategy": result.strategy})
    else:
        try:
            _qq_press("enter")
            steps.append({"step": "click_contact", "status": "enter_fallback"})
        except Exception as e:
            steps.append({"step": "click_contact", "status": "failed", "error": str(e)})
            return {"success": False, "steps": steps, "error": "无法选择联系人"}

    time.sleep(0.8)

    # Step 5: 确认进入聊天界面
    steps.append({"step": "confirm_chat", "query": "发送"})
    if not _click_result_or_press_enter(contact, steps, attempts=3):
        steps.append({"step": "confirm_chat", "status": "failed"})
        return {"success": False, "steps": steps, "error": "未进入聊天界面"}

    # Step 6: 输入消息
    steps.append({"step": "type_message", "message": message})
    _type_via_clipboard(message)
    time.sleep(0.5)

    # Step 7: 融合定位发送按钮
    steps.append({"step": "find_send", "query": "发送"})
    result = _find_qq_element("发送", timeout=5)
    if result.found:
        cx, cy = result.center_x, result.center_y
        _click_abs(cx, cy)
        steps.append({"step": "find_send", "status": "ok", "x": cx, "y": cy, "strategy": result.strategy})
    else:
        try:
            _adapter.hotkey("ctrl", "enter")
            steps.append({"step": "find_send", "status": "ctrl_enter_fallback"})
        except Exception as e:
            steps.append({"step": "find_send", "status": "failed", "error": str(e)})
            return {"success": False, "steps": steps, "error": "无法发送消息"}

    time.sleep(1)
    steps.append({"step": "done", "status": "message_sent"})
    return {"success": True, "steps": steps, "contact": contact, "message": message}


@feature(
    name="qq_find_contact",
    display_name="QQ 查找联系人",
    description="【QQ·感知】在 QQ 中搜索联系人，返回其位置信息。使用融合感知定位",
    category=F.PERCEPTION,
    params=[
        P("contact", "str", "联系人名称（支持模糊匹配）", example="手机"),
        P("timeout", "number", "超时秒数", required=False, default=10),
    ],
    returns="dict{found, x, y, w, h, confidence, strategy}",
    tags=["qq", "perception", "find"],
    hidden=True,
)
def qq_find_contact(contact: str, timeout: float = 10) -> dict:
    hwnd = _adapter.find_window()
    if not hwnd:
        return {"found": False, "error": "QQ 未运行，请先调用 qq_launch"}

    result = _find_qq_element(contact, timeout=timeout)
    if result.found:
        return {
            "found": True,
            "x": result.x, "y": result.y,
            "w": result.w, "h": result.h,
            "center_x": result.center_x, "center_y": result.center_y,
            "confidence": result.confidence,
            "strategy": result.strategy,
            "strategies_tried": result.strategies_used,
        }
    return {
        "found": False,
        "strategies_tried": result.strategies_used,
        "reason": result.reason,
    }


@feature(
    name="qq_get_status",
    display_name="QQ 状态",
    description="【QQ·状态】获取 QQ 运行状态：进程是否存在、窗口是否可见、窗口大小和位置",
    category=F.SYSTEM,
    params=[],
    returns="dict{running, hwnd, left, top, width, height}",
    tags=["qq", "status", "window"],
    hidden=True,
)
def qq_get_status() -> dict:
    try:
        import psutil
        running = any(
            p.info["name"] in ("QQ.exe", "QQEX.exe")
            for p in psutil.process_iter(["name"])
        )
    except Exception:
        running = False

    info = _adapter.get_window_info()
    if info:
        return {
            "running": True,
            "hwnd": info.hwnd,
            "left": info.left, "top": info.top,
            "width": info.width, "height": info.height,
        }
    return {"running": running, "hwnd": None}
