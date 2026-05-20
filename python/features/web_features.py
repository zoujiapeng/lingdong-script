"""
Web 浏览器自动化功能注册
通过 Playwright 操控网页，与桌面自动化混排。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger(__name__)

_playwright_browser = None
_playwright_page = None


def _ensure_browser(headless: bool = False):
    global _playwright_browser, _playwright_page
    if _playwright_page is not None:
        return _playwright_page
    try:
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        _playwright_browser = p.chromium.launch(headless=headless)
        _playwright_page = _playwright_browser.new_page()
        logger.info("🌐 Playwright 浏览器已启动 (headless=%s)", headless)
        return _playwright_page
    except ImportError:
        raise RuntimeError("请安装 Playwright: pip install playwright && playwright install chromium")
    except Exception as e:
        raise RuntimeError(f"启动浏览器失败: {e}")


def _close_browser():
    global _playwright_browser, _playwright_page
    try:
        if _playwright_page:
            _playwright_page.close()
        if _playwright_browser:
            _playwright_browser.close()
    except Exception:
        pass
    _playwright_page = None
    _playwright_browser = None


@feature(
    name="web_browser_execute",
    display_name="浏览器执行操作",
    description="【Web】通过 Playwright 执行浏览器操作。支持导航/点击/输入/截图/等待/提取内容/滚动。与桌面自动化操作混排。",
    category=F.ACTION,
    params=[
        P("action", "str", "操作类型: navigate|click|input_text|screenshot|wait|extract|scroll"),
        P("url", "str", "导航 URL（action=navigate 时需要）", required=False, default=""),
        P("selector", "str", "CSS 选择器（action=click/input_text/extract 时需要）", required=False, default=""),
        P("text", "str", "输入文本（action=input_text 时需要）", required=False, default=""),
        P("wait_seconds", "number", "等待秒数（action=wait 时使用）", required=False, default=1),
        P("extract_attr", "str", "提取属性: textContent|href|src|innerHTML", required=False, default="textContent"),
        P("scroll_direction", "str", "滚动方向: down|up", required=False, default="down"),
        P("timeout", "number", "操作超时秒数", required=False, default=30),
        P("headless", "boolean", "无头模式", required=False, default=False),
    ],
    returns="dict{success, result, error}",
    tags=["web", "browser", "playwright"],
    examples=["web_browser_execute action=navigate url=https://example.com"],
)
def web_browser_execute(
    action: str = "navigate",
    url: str = "",
    selector: str = "",
    text: str = "",
    wait_seconds: float = 1,
    extract_attr: str = "textContent",
    scroll_direction: str = "down",
    timeout: float = 30,
    headless: bool = False,
) -> dict:
    try:
        page = _ensure_browser(headless=headless)
        timeout_ms = int(max(1, float(timeout)) * 1000)

        if action == "navigate":
            if not url:
                return {"success": False, "error": "缺少 URL"}
            page.goto(url, timeout=timeout_ms)
            return {"success": True, "result": {"url": page.url, "title": page.title()}}

        elif action == "click":
            if not selector:
                return {"success": False, "error": "缺少 CSS 选择器"}
            page.click(selector, timeout=timeout_ms)
            return {"success": True, "result": {"clicked": selector}}

        elif action == "input_text":
            if not selector:
                return {"success": False, "error": "缺少 CSS 选择器"}
            page.fill(selector, text or "")
            return {"success": True, "result": {"filled": selector, "text": text}}

        elif action == "screenshot":
            path = f"web_screenshot_{int(time.time())}.png"
            page.screenshot(path=path, full_page=True)
            return {"success": True, "result": {"path": path}}

        elif action == "wait":
            time.sleep(max(0.1, float(wait_seconds)))
            return {"success": True, "result": {"waited": wait_seconds}}

        elif action == "extract":
            if not selector:
                return {"success": False, "error": "缺少 CSS 选择器"}
            attr = extract_attr or "textContent"
            elements = page.query_selector_all(selector)
            if not elements:
                return {"success": False, "error": f"未找到元素: {selector}"}
            if attr in ("textContent", "innerText"):
                values = [el.inner_text() for el in elements if el]
            else:
                values = [(el.get_attribute(attr) or "") for el in elements if el]
            return {"success": True, "result": {"extracted": values, "count": len(values)}}

        elif action == "scroll":
            direction = scroll_direction if scroll_direction in ("down", "up") else "down"
            delta_y = 500 if direction == "down" else -500
            page.evaluate(f"window.scrollBy(0, {delta_y})")
            time.sleep(0.3)
            return {"success": True, "result": {"scrolled": direction}}

        else:
            return {"success": False, "error": f"未知操作: {action}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
