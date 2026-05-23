"""
浏览器交互功能 — 通过 Playwright 实现全面的 Web 控制。

功能覆盖:
  - 导航: navigate, refresh, back, forward, new_page, close, list, switch
  - 元素操作: click, type, select, hover, check, scroll_to, press_key
  - 内容提取: text, html, attributes, screenshot, url, title
  - 脚本执行: execute_js, set_value (通过 JS)
  - 存储管理: cookies (get/set/delete), localStorage, sessionStorage
  - 等待: wait_element, wait_navigation, wait_page_load
  - 下载: download

安装: pip install playwright && playwright install chromium

连接已有浏览器（调试模式）:
  1. 启动 Chrome: chrome.exe --remote-debugging-port=9222
  2. 调用 browser_connect(port=9222) 连接
"""
import json
import os
import threading
import time
from typing import Optional

from features.registry import feature, P, TC, FeatureCategory as F

# ── Playwright 懒加载 ──────────────────────────────────────────

HAS_PLAYWRIGHT = False
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    PWTimeout = Exception  # type: ignore

# ── 全局状态 ───────────────────────────────────────────────────

_lock = threading.Lock()
_browser = None
_context = None
_page = None
_pw = None  # playwright instance
_connected = False  # True if connecting to external browser


def _ensure_playwright():
    """确保 Playwright 已安装。"""
    if not HAS_PLAYWRIGHT:
        raise RuntimeError(
            "Playwright 未安装。运行: pip install playwright && playwright install chromium"
        )


def _get_page():
    """获取当前活跃页面，懒启动浏览器。"""
    global _browser, _context, _page, _pw, _connected
    if _page is not None:
        try:
            _page.title()  # quick health check
            return _page
        except Exception:
            pass  # page is dead

    _ensure_playwright()
    with _lock:
        if _page is not None:
            try:
                _page.title()
                return _page
            except Exception:
                pass

        # Try to reuse existing context
        if _context is not None:
            try:
                pages = _context.pages
                if pages:
                    _page = pages[0]
                    return _page
            except Exception:
                pass
            # Context exists but no pages — create one
            try:
                _page = _context.new_page()
                return _page
            except Exception:
                pass

        # Try to reuse existing browser (but not via connect_over_cdp)
        if _browser is not None:
            try:
                _context = _browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    locale="zh-CN",
                )
                _page = _context.new_page()
                return _page
            except Exception:
                pass

        # Fresh launch — _pw stays alive, skip sync_playwright().start()
        _cleanup()
        time.sleep(0.3)
        try:
            if _pw is None:
                _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(
                headless=False,
                args=["--start-maximized"],
            )
            _context = _browser.new_context(
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            _page = _context.new_page()
            _connected = False
            return _page
        except Exception as e:
            _full_cleanup()
            raise RuntimeError(f"无法启动浏览器: {e}")


def _connect_browser(port: int = 9222):
    """连接到已有 Chrome 实例（调试模式）。"""
    global _browser, _context, _page, _pw, _connected
    _ensure_playwright()
    with _lock:
        try:
            _pw = sync_playwright().start()
            _browser = _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            _context = _browser.contexts[0] if _browser.contexts else _browser.new_context()
            pages = _context.pages
            _page = pages[0] if pages else _context.new_page()
            _connected = True
            return True
        except Exception as e:
            _full_cleanup()
            raise RuntimeError(f"无法连接到 Chrome (端口 {port}): {e}")


def _cleanup():
    """清理浏览器资源（保留 Playwright 驱动）。

    不停止 _pw 以避免 "Sync API inside asyncio loop" 错误。
    _pw 在进程生命周期内保持活跃，仅在 _full_cleanup() 中真正释放。
    """
    global _browser, _context, _page, _connected
    try:
        if _page is not None:
            _page.close()
    except Exception:
        pass
    try:
        if _context is not None:
            _context.close()
    except Exception:
        pass
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None
    _context = None
    _page = None
    _connected = False


def _full_cleanup():
    """完整清理，包括 Playwright 驱动。用于进程退出/浏览器完全关闭。"""
    global _pw
    _cleanup()
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:
        pass
    _pw = None


def _not_installed_msg() -> dict:
    return {
        "success": False,
        "error": "Playwright 未安装。运行: pip install playwright && playwright install chromium",
    }


def _pick_another_page():
    """当当前页被关闭后，选择另一个可用标签页。"""
    global _context, _page
    # retry up to 3 times with delay (page close may not be instant)
    for _i in range(3):
        try:
            if _context:
                pages = _context.pages
                if pages:
                    return pages[0]
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _pwrap(fn, default=None):
    """Execute Playwright call with error handling."""
    try:
        return fn()
    except PWTimeout as e:
        return {"success": False, "error": f"操作超时: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  浏览器连接 / 生命周期
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_connect",
    display_name="连接已有浏览器",
    description="【浏览器】连接到已通过调试模式启动的 Chrome 浏览器（chrome.exe --remote-debugging-port=9222）。用于控制用户已有的浏览器而不是启动新实例",
    category=F.ACTION,
    params=[
        P("port", "int", "调试端口", required=False, default=9222),
    ],
    returns="bool - 是否连接成功",
    tags=["browser", "playwright"],
)
def browser_connect(port: int = 9222) -> dict:
    try:
        _connect_browser(port)
        return {"success": True, "result": True}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="browser_list_tabs",
    display_name="列出浏览器标签页",
    description="【浏览器】列出当前浏览器所有打开的标签页（标题+URL）。用于了解当前浏览器状态",
    category=F.ACTION,
    params=[],
    returns="list[dict] - 标签页列表",
    tags=["browser", "playwright"],
)
def browser_list_tabs() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    try:
        page = _get_page()
        context = page.context
        tabs = [{"title": p.title(), "url": p.url} for p in context.pages]
        return {"success": True, "result": tabs}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="browser_switch_tab",
    display_name="切换标签页",
    description="【浏览器】按标题或序号切换到指定标签页。序号从 1 开始",
    category=F.ACTION,
    params=[
        P("target", "str", "标签页标题或序号（如 '1', '微信'）", example="1"),
    ],
    returns="bool - 是否切换成功",
    tags=["browser", "playwright"],
)
def browser_switch_tab(target: str) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    try:
        page = _get_page()
        context = page.context
        pages = context.pages
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(pages):
                pages[idx].bring_to_front()
                return {"success": True, "result": True}
            return {"success": False, "error": f"标签页序号超出范围: 共 {len(pages)} 个"}
        for p in pages:
            if target.lower() in p.title().lower():
                p.bring_to_front()
                return {"success": True, "result": True}
        return {"success": False, "error": f"未找到标题包含 '{target}' 的标签页"}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="browser_new_tab",
    display_name="新建标签页",
    description="【浏览器】创建新标签页并导航到指定 URL。留空则打开空白页",
    category=F.ACTION,
    params=[
        P("url", "str", "要打开的 URL", required=False, default="about:blank"),
    ],
    returns="dict - 新标签页信息",
    tags=["browser", "playwright", "tab"],
)
def browser_new_tab(url: str = "about:blank") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    try:
        page = _get_page()
        context = page.context
        new_page = context.new_page()
        if url:
            _KNOWN_NON_HTTP = ("about:", "data:", "file:", "chrome:", "javascript:", "blob:", "view-source:")
            if not url.startswith(("http://", "https://")) and "://" not in url and not url.startswith(_KNOWN_NON_HTTP):
                url = "https://" + url
            new_page.goto(url, wait_until="domcontentloaded")
        return {"success": True, "result": {"title": new_page.title(), "url": new_page.url}}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="browser_close_tab",
    display_name="关闭标签页",
    description="【浏览器】关闭当前或指定标签页。留空 target 关闭当前页",
    category=F.ACTION,
    params=[
        P("target", "str", "标签页标题（留空关闭当前）", required=False, default=""),
    ],
    returns="bool - 是否关闭成功",
    tags=["browser", "playwright"],
)
def browser_close_tab(target: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    global _page
    try:
        page = _get_page()
        context = page.context
        pages = context.pages
        if not target:
            page.close()
            # switch to another page or create blank
            others = [p for p in pages if p != page]
            if others:
                _page = others[0]
            else:
                _page = context.new_page()
            return {"success": True, "result": True}
        for p in pages:
            if target.lower() in p.title().lower():
                p.close()
                if p == _page:
                    others = [q for q in context.pages if q != p]
                    if others:
                        _page = others[0]
                    else:
                        _page = context.new_page()
                return {"success": True, "result": True}
        return {"success": False, "error": f"未找到标题包含 '{target}' 的标签页"}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  导航
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_navigate",
    display_name="导航到 URL",
    description="【浏览器】打开指定 URL。支持 http/https 协议。用于导航到目标网页",
    category=F.ACTION,
    params=[
        P("url", "str", "完整 URL（自动补 https://）", example="example.com"),
        P("wait_until", "str", "等待策略: domcontentloaded | load | networkidle", required=False, default="domcontentloaded"),
    ],
    returns="dict{title, url} - 页面信息",
    tags=["browser", "playwright"],
)
def browser_navigate(url: str, wait_until: str = "domcontentloaded") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_navigate(url, wait_until))


def _do_navigate(url: str, wait_until: str = "domcontentloaded"):
    page = _get_page()
    # Only prepend https:// for bare domains, not special URLs like about:data:file:chrome:
    _KNOWN_NON_HTTP_SCHEMES = ("about:", "data:", "file:", "chrome:", "javascript:", "blob:", "view-source:")
    if not url.startswith(("http://", "https://")) and "://" not in url and not url.startswith(_KNOWN_NON_HTTP_SCHEMES):
        url = "https://" + url
    page.goto(url, wait_until=wait_until)
    return {"success": True, "result": {"title": page.title(), "url": page.url}}


@feature(
    name="browser_refresh",
    display_name="刷新页面",
    description="【浏览器】刷新当前页面。可选强制刷新（忽略缓存）",
    category=F.ACTION,
    params=[
        P("hard_reload", "boolean", "是否强制刷新", required=False, default=False),
    ],
    returns="dict - 页面信息",
    tags=["browser", "playwright"],
)
def browser_refresh(hard_reload: bool = False) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_refresh(hard_reload))


def _do_refresh(hard_reload: bool = False):
    page = _get_page()
    page.reload(wait_until="domcontentloaded")
    return {"success": True, "result": {"title": page.title(), "url": page.url}}


@feature(
    name="browser_go_back",
    display_name="后退",
    description="【浏览器】返回上一页",
    category=F.ACTION,
    params=[],
    returns="dict - 页面信息",
    tags=["browser", "playwright"],
)
def browser_go_back() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_go_back())


def _do_go_back():
    page = _get_page()
    page.go_back(wait_until="domcontentloaded")
    return {"success": True, "result": {"title": page.title(), "url": page.url}}


@feature(
    name="browser_go_forward",
    display_name="前进",
    description="【浏览器】前进到下一页",
    category=F.ACTION,
    params=[],
    returns="dict - 页面信息",
    tags=["browser", "playwright"],
)
def browser_go_forward() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_go_forward())


def _do_go_forward():
    page = _get_page()
    page.go_forward(wait_until="domcontentloaded")
    return {"success": True, "result": {"title": page.title(), "url": page.url}}


@feature(
    name="browser_get_url",
    display_name="获取当前 URL",
    description="【浏览器】获取当前标签页的 URL",
    category=F.ACTION,
    params=[],
    returns="str - 当前 URL",
    tags=["browser", "playwright"],
)
def browser_get_url() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: {"success": True, "result": _get_page().url})


@feature(
    name="browser_get_title",
    display_name="获取页面标题",
    description="【浏览器】获取当前标签页的标题",
    category=F.ACTION,
    params=[],
    returns="str - 页面标题",
    tags=["browser", "playwright"],
)
def browser_get_title() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: {"success": True, "result": _get_page().title()})


# ════════════════════════════════════════════════════════════════
#  元素操作
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_click",
    display_name="点击元素",
    description="【浏览器·交互】点击页面中的元素。支持 CSS 选择器和文本匹配两种方式找元素。适用于点击按钮、链接、复选框等任何可交互元素",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器，如 '#submit'、'.btn-primary'、'button'", required=False, default=""),
        P("text", "str", "元素文本内容匹配（与 selector 二选一）", required=False, default=""),
        P("timeout", "number", "等待超时秒数", required=False, default=10),
    ],
    returns="bool - 是否点击成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_click(selector: str = "", text: str = "", timeout: float = 10) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_click(selector, text, timeout))


def _do_click(selector: str, text: str, timeout: float):
    page = _get_page()
    ms = int(timeout * 1000)
    if text:
        page.get_by_text(text, exact=False).first.click(timeout=ms)
    else:
        page.click(selector, timeout=ms)
    return {"success": True, "result": True}


@feature(
    name="browser_type",
    display_name="输入文本",
    description="【浏览器·交互】在输入框中输入文本。先 clear 清空再输入。适用于表单填写、搜索框输入",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", example="#username"),
        P("text", "str", "要输入的文本", example="用户名"),
        P("delay", "number", "按键间隔秒数（模拟人类输入）", required=False, default=0),
        P("clear_first", "boolean", "输入前先清空", required=False, default=True),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_type(selector: str, text: str, delay: float = 0, clear_first: bool = True) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_type(selector, text, delay, clear_first))


def _do_type(selector: str, text: str, delay: float, clear_first: bool):
    page = _get_page()
    el = page.locator(selector)
    if clear_first:
        el.fill("")
    if delay > 0:
        el.type(text, delay=int(delay * 1000))
    else:
        el.fill(text)
    return {"success": True, "result": True}


@feature(
    name="browser_set_value",
    display_name="设置输入值",
    description="【浏览器·交互】通过 JavaScript 直接设置输入框的值（绕过输入事件）。适用于 React/Vue 等框架下的复杂输入框",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", example="#email"),
        P("value", "str", "要设置的值", example="user@example.com"),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_set_value(selector: str, value: str) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_set_value(selector, value))


def _do_set_value(selector: str, value: str):
    page = _get_page()
    page.eval_on_selector(selector, f"el => el.value = '{value.replace(chr(39), '\\' + chr(39))}'")
    return {"success": True, "result": True}


@feature(
    name="browser_select_option",
    display_name="选择下拉选项",
    description="【浏览器·交互】在 <select> 下拉框中选择指定值或标签。支持按 value、label、index 选择",
    category=F.ACTION,
    params=[
        P("selector", "str", "select 元素选择器", example="#city"),
        P("value", "str", "要选择的值（value 属性）", required=False, default=""),
        P("label", "str", "要选择的标签文本（与 value 二选一）", required=False, default=""),
        P("index", "int", "选项序号，从 0 开始（与 value/label 三选一）", required=False, default=-1),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_select_option(selector: str, value: str = "", label: str = "", index: int = -1) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_select(selector, value, label, index))


def _do_select(selector: str, value: str, label: str, index: int):
    page = _get_page()
    el = page.locator(selector)
    if value:
        el.select_option(value=value)
    elif label:
        el.select_option(label=label)
    elif index >= 0:
        el.select_option(index=index)
    else:
        el.select_option(index=0)
    return {"success": True, "result": True}


@feature(
    name="browser_check",
    display_name="勾选/取消复选框",
    description="【浏览器·交互】勾选或取消勾选复选框或单选框。适用于表单中的 checkbox/radio",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", example="#agree"),
        P("checked", "boolean", "是否勾选", required=False, default=True),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_check(selector: str, checked: bool = True) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_check(selector, checked))


def _do_check(selector: str, checked: bool):
    page = _get_page()
    if checked:
        page.check(selector)
    else:
        page.uncheck(selector)
    return {"success": True, "result": True}


@feature(
    name="browser_hover",
    display_name="悬停元素",
    description="【浏览器·交互】将鼠标悬停在元素上。用于触发 tooltip、下拉菜单等悬停效果",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", required=False, default=""),
        P("text", "str", "元素文本匹配（与 selector 二选一）", required=False, default=""),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "interaction"],
)
def browser_hover(selector: str = "", text: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_hover(selector, text))


def _do_hover(selector: str, text: str):
    page = _get_page()
    if text:
        page.get_by_text(text, exact=False).first.hover()
    else:
        page.hover(selector)
    return {"success": True, "result": True}


@feature(
    name="browser_press_key",
    display_name="按键",
    description="【浏览器·交互】在当前焦点元素或页面上按下键盘按键。适用于 Enter/Esc/Tab 等特殊键",
    category=F.ACTION,
    params=[
        P("key", "str", "按键名（Enter/Escape/Tab/ArrowDown/Control+a 等）", example="Enter"),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "keyboard"],
)
def browser_press_key(key: str) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_press_key(key))


def _do_press_key(key: str):
    page = _get_page()
    page.keyboard.press(key)
    return {"success": True, "result": True}


@feature(
    name="browser_scroll_to",
    display_name="滚动到元素",
    description="【浏览器·交互】将页面滚动到指定元素可见位置。适用于长页面中定位到特定内容",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", required=False, default=""),
        P("text", "str", "元素文本匹配（与 selector 二选一）", required=False, default=""),
        P("behavior", "str", "滚动行为: auto | smooth", required=False, default="smooth",
          choices={"auto": "自动", "smooth": "平滑"}),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "scroll"],
)
def browser_scroll_to(selector: str = "", text: str = "", behavior: str = "smooth") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_scroll_to(selector, text, behavior))


def _do_scroll_to(selector: str, text: str, behavior: str):
    page = _get_page()
    if text:
        el = page.get_by_text(text, exact=False).first
    else:
        el = page.locator(selector)
    el.scroll_into_view_if_needed()
    return {"success": True, "result": True}


@feature(
    name="browser_upload_file",
    display_name="上传文件",
    description="【浏览器·交互】通过文件输入元素上传文件。适用于 <input type=file>。支持多个文件同时上传",
    category=F.ACTION,
    params=[
        P("selector", "str", "文件输入元素选择器", example='input[type="file"]'),
        P("files", "str", "文件路径列表（JSON 数组）", example='["C:/doc/report.pdf"]'),
        P("timeout", "number", "等待超时秒数", required=False, default=30),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "upload", "file"],
)
def browser_upload_file(selector: str, files: str, timeout: float = 30) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_upload_file(selector, files, timeout))


def _do_upload_file(selector: str, files: str, timeout: float):
    page = _get_page()
    import json as _json
    try:
        file_list = _json.loads(files) if isinstance(files, str) else list(files)
    except Exception:
        file_list = [files]
    page.set_input_files(selector, file_list, timeout=int(timeout * 1000))
    return {"success": True, "result": True}


# ════════════════════════════════════════════════════════════════
#  内容提取
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_get_text",
    display_name="获取元素文本",
    description="【浏览器·提取】获取页面或指定元素的可见文本内容。留空 selector 获取全页文本",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器（留空获取全页文本）", required=False, default="body"),
    ],
    returns="str - 文本内容",
    tags=["browser", "playwright", "extract"],
)
def browser_get_text(selector: str = "body") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: {"success": True, "result": _get_page().locator(selector).inner_text()})


@feature(
    name="browser_get_html",
    display_name="获取元素 HTML",
    description="【浏览器·提取】获取页面或指定元素的 innerHTML/outerHTML。用于分析页面结构",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器（留空获取 <html>）", required=False, default="html"),
        P("outer", "boolean", "是否获取 outerHTML（否则 innerHTML）", required=False, default=False),
    ],
    returns="str - HTML 内容",
    tags=["browser", "playwright", "extract"],
)
def browser_get_html(selector: str = "html", outer: bool = False) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_get_html(selector, outer))


def _do_get_html(selector: str, outer: bool):
    page = _get_page()
    method = "outerHTML" if outer else "innerHTML"
    html = page.locator(selector).evaluate(f"el => el.{method}")
    return {"success": True, "result": html}


@feature(
    name="browser_get_attributes",
    display_name="获取元素属性",
    description="【浏览器·提取】获取指定元素的属性值。适用于读取 href、src、data-* 等属性",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", example="a.download-link"),
        P("attribute", "str", "属性名（留空返回所有属性对象）", required=False, default=""),
    ],
    returns="dict|str - 属性值",
    tags=["browser", "playwright", "extract"],
)
def browser_get_attributes(selector: str, attribute: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_get_attrs(selector, attribute))


def _do_get_attrs(selector: str, attribute: str):
    page = _get_page()
    el = page.locator(selector)
    if attribute:
        val = el.get_attribute(attribute)
        return {"success": True, "result": val}
    props = el.evaluate("""el => {
        const attrs = {};
        for (const a of el.attributes) attrs[a.name] = a.value;
        return attrs;
    }""")
    return {"success": True, "result": props}


@feature(
    name="browser_find_elements",
    display_name="查找页面元素",
    description="【浏览器·提取】查找页面中匹配选择器的所有元素，返回其标签、文本、可见性等信息。用于了解页面结构和元素状态",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", example="button, a"),
        P("max_results", "int", "最大返回数", required=False, default=20),
    ],
    returns="list[dict] - 元素列表",
    tags=["browser", "playwright", "extract"],
)
def browser_find_elements(selector: str, max_results: int = 20) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_find_elements(selector, max_results))


def _do_find_elements(selector: str, max_results: int):
    page = _get_page()
    elements = page.locator(selector).all()
    result = []
    for el in elements[:max_results]:
        try:
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            text = el.inner_text()[:100]
            visible = el.is_visible()
            attrs = el.evaluate("""el => {
                const a = {}; for (const attr of el.attributes) a[attr.name] = attr.value;
                return a;
            }""")
            result.append({
                "tag": tag,
                "text": text,
                "visible": visible,
                "attributes": {k: v for k, v in attrs.items() if k in ("id", "class", "type", "name", "href", "src", "placeholder", "aria-label", "role")},
            })
        except Exception:
            pass
    return {"success": True, "result": result}


@feature(
    name="browser_screenshot",
    display_name="截图页面",
    description="【浏览器·截图】截取当前页面或指定元素的截图。返回图片路径。适用于保存页面状态、验证UI",
    category=F.ACTION,
    params=[
        P("selector", "str", "元素选择器（留空截取整个可视区域）", required=False, default=""),
        P("full_page", "boolean", "是否截取整个页面（含不可见部分）", required=False, default=False),
        P("path", "str", "保存路径（留空自动生成）", required=False, default=""),
    ],
    returns="dict{path, width, height} - 截图信息",
    tags=["browser", "playwright", "screenshot"],
)
def browser_screenshot(selector: str = "", full_page: bool = False, path: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_screenshot(selector, full_page, path))


def _do_screenshot(selector: str, full_page: bool, path: str):
    page = _get_page()
    if not path:
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"browser_ss_{int(time.time())}.png")

    if selector:
        el = page.locator(selector)
        el.screenshot(path=path)
    else:
        page.screenshot(path=path, full_page=full_page)

    from PIL import Image
    img = Image.open(path)
    w, h = img.width, img.height
    img.close()
    return {"success": True, "result": {"path": path, "width": w, "height": h}}


# ════════════════════════════════════════════════════════════════
#  脚本执行
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_execute_js",
    display_name="执行 JavaScript",
    description="【浏览器·脚本】在页面中执行任意 JavaScript 代码。用于读取/修改页面数据、调用 API、操作 DOM 等高级场景",
    category=F.ACTION,
    params=[
        P("code", "str", "JavaScript 代码", example="return document.title"),
        P("selector", "str", "将元素作为参数传入（可选）", required=False, default=""),
    ],
    returns="any - 执行结果（JSON 序列化）",
    tags=["browser", "playwright", "script"],
)
def browser_execute_js(code: str, selector: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_execute_js(code, selector))


def _do_execute_js(code: str, selector: str):
    page = _get_page()
    if selector:
        result = page.locator(selector).evaluate(code)
    else:
        # Playwright page.evaluate() evaluates strings as JS expressions.
        # `return` is a statement, not an expression — it causes SyntaxError.
        # For statement-style code (containing `return`), wrap in an IIFE.
        if "return " in code or code.strip().startswith("return"):
            result = page.evaluate(f"(() => {{ {code} }})()")
        else:
            result = page.evaluate(code)
    return {"success": True, "result": result}


# ════════════════════════════════════════════════════════════════
#  等待
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_wait_element",
    display_name="等待元素出现",
    description="【浏览器·等待】等待页面中出现匹配选择器或文本的元素。适用于等待动态加载的内容",
    category=F.ACTION,
    params=[
        P("selector", "str", "CSS 选择器", required=False, default=""),
        P("text", "str", "等待包含此文本的元素出现（与 selector 二选一）", required=False, default=""),
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("state", "str", "元素状态: visible | attached | hidden", required=False, default="visible",
          choices={"visible": "可见", "attached": "已附加", "hidden": "隐藏"}),
    ],
    returns="bool - 是否找到",
    tags=["browser", "playwright", "wait"],
)
def browser_wait_element(selector: str = "", text: str = "", timeout: float = 30, state: str = "visible") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_wait_element(selector, text, timeout, state))


def _do_wait_element(selector: str, text: str, timeout: float, state: str):
    page = _get_page()
    ms = int(timeout * 1000)
    if text:
        page.get_by_text(text, exact=False).first.wait_for(state=state, timeout=ms)
    else:
        page.wait_for_selector(selector, state=state, timeout=ms)
    return {"success": True, "result": True}


@feature(
    name="browser_wait_navigation",
    display_name="等待页面导航",
    description="【浏览器·等待】等待页面发生导航（URL 变化或刷新）。适用于点击链接/提交表单后等待页面跳转",
    category=F.ACTION,
    params=[
        P("timeout", "number", "超时秒数", required=False, default=30),
    ],
    returns="dict{url} - 新页面 URL",
    tags=["browser", "playwright", "wait"],
)
def browser_wait_navigation(timeout: float = 30) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_wait_navigation(timeout))


def _do_wait_navigation(timeout: float):
    page = _get_page()
    page.wait_for_load_state("networkidle", timeout=int(timeout * 1000))
    return {"success": True, "result": {"url": page.url, "title": page.title()}}


@feature(
    name="browser_wait_page_load",
    display_name="等待页面加载完成",
    description="【浏览器·等待】等待页面加载完成（networkidle）。适用于 SPA 或动态加载页面",
    category=F.ACTION,
    params=[
        P("timeout", "number", "超时秒数", required=False, default=30),
    ],
    returns="dict{url, title} - 页面信息",
    tags=["browser", "playwright", "wait"],
)
def browser_wait_page_load(timeout: float = 30) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_wait_navigation(timeout))


@feature(
    name="browser_wait_request",
    display_name="等待网络请求",
    description="【浏览器·等待】等待发往指定 URL 的网络请求完成。支持按 URL 片段和 HTTP 方法过滤。适用于等待 API 响应或表单提交结果",
    category=F.ACTION,
    params=[
        P("url_substring", "str", "URL 包含的字符串（模糊匹配）", example="/api/submit"),
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("method", "str", "HTTP 方法过滤（GET/POST/PUT/DELETE，留空不限制）", required=False, default=""),
    ],
    returns="dict{url, status, method} - 请求信息",
    tags=["browser", "playwright", "wait", "network"],
)
def browser_wait_request(url_substring: str, timeout: float = 30, method: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_wait_request(url_substring, timeout, method))


def _do_wait_request(url_substring: str, timeout: float, method: str):
    page = _get_page()
    ms = int(timeout * 1000)

    def predicate(req):
        if url_substring.lower() not in req.url.lower():
            return False
        if method and req.method.upper() != method.upper():
            return False
        return True

    with page.expect_response(predicate, timeout=ms) as resp_info:
        resp = resp_info.value
    return {
        "success": True,
        "result": {
            "url": resp.url,
            "status": resp.status,
            "method": resp.request.method,
        },
    }


# ════════════════════════════════════════════════════════════════
#  存储管理
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_get_cookies",
    display_name="获取 Cookies",
    description="【浏览器·存储】获取当前页面的所有 Cookie。用于检查登录状态、会话信息",
    category=F.ACTION,
    params=[],
    returns="list[dict{name, value, domain}] - Cookie 列表",
    tags=["browser", "playwright", "storage"],
)
def browser_get_cookies() -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_get_cookies())


def _do_get_cookies():
    page = _get_page()
    cookies = page.context.cookies()
    return {"success": True, "result": [{"name": c["name"], "value": c["value"], "domain": c["domain"]} for c in cookies]}


@feature(
    name="browser_set_cookie",
    display_name="设置 Cookie",
    description="【浏览器·存储】设置一个 Cookie。适用于注入登录态或会话信息",
    category=F.ACTION,
    params=[
        P("name", "str", "Cookie 名称", example="session_id"),
        P("value", "str", "Cookie 值", example="abc123"),
        P("domain", "str", "Cookie 域名", required=False, default=""),
        P("url", "str", "Cookie URL（替代 domain）", required=False, default=""),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "storage"],
)
def browser_set_cookie(name: str, value: str, domain: str = "", url: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_set_cookie(name, value, domain, url))


def _do_set_cookie(name: str, value: str, domain: str, url: str):
    page = _get_page()
    cookie = {"name": name, "value": value}
    if url:
        cookie["url"] = url
    else:
        cookie["path"] = "/"
        if domain:
            cookie["domain"] = domain
    page.context.add_cookies([cookie])
    return {"success": True, "result": True}


@feature(
    name="browser_delete_cookies",
    display_name="删除 Cookies",
    description="【浏览器·存储】删除所有或指定名称的 Cookie。用于清除登录状态或重置会话",
    category=F.ACTION,
    params=[
        P("names", "str", "要删除的 Cookie 名称列表（JSON 数组，留空删除全部）", required=False, default=""),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "storage"],
)
def browser_delete_cookies(names: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_delete_cookies(names))


def _do_delete_cookies(names: str):
    page = _get_page()
    context = page.context

    if not names:
        # Delete ALL cookies
        for c in context.cookies():
            context.add_cookies([{
                "name": c["name"],
                "value": "",
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": 1,
            }])
        return {"success": True, "result": True}

    # names may arrive as JSON string ("[...]") or already-parsed list — handle both
    if isinstance(names, (list, tuple)):
        name_list = list(names)
    else:
        import json as _json
        try:
            name_list = _json.loads(names)
        except Exception:
            name_list = [names]

    for c in context.cookies():
        if c["name"] in name_list:
            context.add_cookies([{
                "name": c["name"],
                "value": "",
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": 1,
            }])
    return {"success": True, "result": True}


@feature(
    name="browser_get_local_storage",
    display_name="获取 localStorage",
    description="【浏览器·存储】获取页面 localStorage 中的指定键值或全部数据。用于读取 Web 应用本地存储的状态",
    category=F.ACTION,
    params=[
        P("key", "str", "要读取的键名（留空返回全部）", required=False, default=""),
    ],
    returns="any - 存储值",
    tags=["browser", "playwright", "storage"],
)
def browser_get_local_storage(key: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_get_storage("localStorage", key))


def _do_get_storage(storage: str, key: str):
    page = _get_page()
    if key:
        val = page.evaluate(f"{storage}.getItem('{key.replace(chr(39), '\\' + chr(39))}')")
    else:
        val = page.evaluate(f"Object.entries({storage})")
    return {"success": True, "result": val}


@feature(
    name="browser_set_local_storage",
    display_name="设置 localStorage",
    description="【浏览器·存储】在页面 localStorage 中设置指定键值。用于注入应用状态或配置",
    category=F.ACTION,
    params=[
        P("key", "str", "键名", example="theme"),
        P("value", "str", "要设置的键值", example="dark"),
    ],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "storage"],
)
def browser_set_local_storage(key: str, value: str) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_set_storage("localStorage", key, value))


def _do_set_storage(storage: str, key: str, value: str):
    page = _get_page()
    safe_val = json.dumps(value)
    page.evaluate(f"{storage}.setItem('{key.replace(chr(39), '\\' + chr(39))}', {safe_val})")
    return {"success": True, "result": True}


# ════════════════════════════════════════════════════════════════
#  下载
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_download",
    display_name="下载文件",
    description="【浏览器·下载】点击元素触发下载并等待文件下载完成。返回保存路径。适用于下载按钮/链接触发后的文件获取",
    category=F.ACTION,
    params=[
        P("selector", "str", "触发下载的元素选择器", example="a.download-link"),
        P("save_path", "str", "保存目录（留空使用下载目录）", required=False, default=""),
    ],
    returns="dict{path, suggested_name, mime_type} - 下载文件信息",
    tags=["browser", "playwright", "download"],
)
def browser_download(selector: str, save_path: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_download(selector, save_path))


def _do_download(selector: str, save_path: str):
    page = _get_page()
    if not save_path:
        import tempfile
        save_path = tempfile.gettempdir()
    os.makedirs(save_path, exist_ok=True)

    with page.expect_download() as download_info:
        page.click(selector)
    download = download_info.value
    dest = os.path.join(save_path, download.suggested_filename)
    download.save_as(dest)
    return {
        "success": True,
        "result": {
            "path": dest,
            "suggested_name": download.suggested_filename,
            "mime_type": download.mime_type or "",
        },
    }


# ════════════════════════════════════════════════════════════════
#  关闭
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_close",
    display_name="关闭浏览器",
    description="【浏览器·生命周期】关闭当前浏览器实例并释放所有资源。下次调用浏览器功能时会自动重新启动",
    category=F.ACTION,
    params=[],
    returns="bool - 是否成功",
    tags=["browser", "playwright", "lifecycle"],
)
def browser_close() -> dict:
    global _browser, _context, _page, _pw
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    try:
        _full_cleanup()
        return {"success": True, "result": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  视觉锚点 (Visual Anchor) — 截图 + 元素定位 → 坐标
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_visual_anchor",
    display_name="视觉锚点定位",
    description="【浏览器·视觉】在页面中定位元素并返回其屏幕坐标和尺寸。支持按文本匹配、CSS 选择器或图片查找。返回元素的 bounding box {x, y, w, h, center_x, center_y}，可用于后续点击/悬停等操作。适用于 canvas、SVG、iframe 或 CSS 选择器难以定位的元素",
    category=F.ACTION,
    params=[
        P("text", "str", "元素文本内容匹配（与 selector/description 三选一）", required=False, default=""),
        P("selector", "str", "CSS 选择器（与 text/description 三选一）", required=False, default=""),
        P("description", "str", "元素自然语言描述（实验性，使用 OCR 匹配附近文本）", required=False, default=""),
        P("timeout", "number", "等待超时秒数", required=False, default=10),
    ],
    returns="dict{x, y, w, h, center_x, center_y} - 元素坐标和尺寸",
    tags=["browser", "visual", "anchor", "coordinate"],
)
def browser_visual_anchor(text: str = "", selector: str = "", description: str = "", timeout: float = 10) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_visual_anchor(text, selector, description, timeout))


def _do_visual_anchor(text: str, selector: str, description: str, timeout: float):
    """Locate element and return its bounding box coordinates."""
    page = _get_page()
    ms = int(timeout * 1000)

    el = None
    if selector:
        el = page.locator(selector).first
    elif text:
        el = page.get_by_text(text, exact=False).first
    elif description:
        # Fallback: try text match on description keywords
        keywords = [w for w in description.replace("，", ",").replace(" ", ",").split(",") if len(w) > 1]
        for kw in keywords:
            try:
                el = page.get_by_text(kw, exact=False).first
                if el.is_visible():
                    break
                el = None
            except Exception:
                el = None

    if el is None:
        return {"success": False, "error": "无法定位元素，请尝试使用更精确的选择器或文本"}

    try:
        el.wait_for(state="visible", timeout=ms)
        box = el.bounding_box()
        if box is None:
            return {"success": False, "error": "元素存在但无法获取坐标（可能不可见）"}
        return {
            "success": True,
            "result": {
                "x": box["x"],
                "y": box["y"],
                "w": box["width"],
                "h": box["height"],
                "center_x": box["x"] + box["width"] / 2,
                "center_y": box["y"] + box["height"] / 2,
            },
        }
    except PWTimeout:
        return {"success": False, "error": f"等待元素超时 ({timeout}s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  浏览器状态快照 (State Snapshot) — 保存/恢复 cookies + storage
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_state_snapshot",
    display_name="保存浏览器状态快照",
    description="【浏览器·状态】保存当前浏览器的 cookies、localStorage 和 sessionStorage 到 JSON 文件。可用于断点续传、保持登录态、恢复会话等场景。返回快照文件路径",
    category=F.ACTION,
    params=[
        P("name", "str", "快照名称（用于后续恢复，如 'login-state'）", required=False, default=""),
        P("path", "str", "保存路径（留空自动生成）", required=False, default=""),
    ],
    returns="dict{path, name, cookie_count, storage_keys} - 快照信息",
    tags=["browser", "state", "snapshot", "persist"],
)
def browser_state_snapshot(name: str = "", path: str = "") -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_state_snapshot(name, path))


def _do_state_snapshot(name: str, path: str):
    page = _get_page()
    cookies = page.context.cookies()
    local_storage = page.evaluate("JSON.stringify(Object.entries(localStorage))")
    session_storage = page.evaluate("JSON.stringify(Object.entries(sessionStorage))")

    snapshot = {
        "name": name or f"snapshot_{int(time.time())}",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": page.url,
        "title": page.title(),
        "cookies": cookies,
        "localStorage": json.loads(local_storage),
        "sessionStorage": json.loads(session_storage),
    }

    if not path:
        save_dir = os.path.join(os.path.dirname(__file__), "..", "browser_snapshots")
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{snapshot['name']}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "result": {
            "path": path,
            "name": snapshot["name"],
            "cookie_count": len(cookies),
            "storage_keys": [k for k, _ in json.loads(local_storage)],
        },
    }


@feature(
    name="browser_state_restore",
    display_name="恢复浏览器状态快照",
    description="【浏览器·状态】从之前保存的快照 JSON 文件恢复 cookies、localStorage 和 sessionStorage。用于恢复登录态或会话",
    category=F.ACTION,
    params=[
        P("path", "str", "快照文件路径（或快照名称）", example="login-state"),
        P("navigate", "boolean", "恢复后是否导航到快照时的 URL", required=False, default=False),
    ],
    returns="dict{cookie_count, storage_keys, url} - 恢复结果",
    tags=["browser", "state", "snapshot", "restore"],
)
def browser_state_restore(path: str, navigate: bool = False) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_state_restore(path, navigate))


def _do_state_restore(path: str, navigate: bool):
    import glob as _glob

    page = _get_page()

    # If path doesn't exist, try snapshots directory
    if not os.path.exists(path):
        snap_dir = os.path.join(os.path.dirname(__file__), "..", "browser_snapshots")
        candidates = _glob.glob(os.path.join(snap_dir, f"{path}*"))
        if not candidates:
            return {"success": False, "error": f"快照文件不存在: {path}"}
        path = candidates[0]

    with open(path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    # Navigate to the domain first for cookie injection
    target_url = snapshot.get("url", "")
    if target_url:
        try:
            page.goto(target_url, wait_until="domcontentloaded")
        except Exception:
            pass

    # Set cookies
    for c in snapshot.get("cookies", []):
        try:
            page.context.add_cookies([{
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
            }])
        except Exception:
            pass

    # Restore localStorage
    for k, v in snapshot.get("localStorage", []):
        try:
            safe_val = json.dumps(v)
            page.evaluate(f"localStorage.setItem('{k.replace(chr(39), '\\' + chr(39))}', {safe_val})")
        except Exception:
            pass

    # Restore sessionStorage
    for k, v in snapshot.get("sessionStorage", []):
        try:
            safe_val = json.dumps(v)
            page.evaluate(f"sessionStorage.setItem('{k.replace(chr(39), '\\' + chr(39))}', {safe_val})")
        except Exception:
            pass

    if navigate and target_url:
        try:
            page.goto(target_url, wait_until="domcontentloaded")
        except Exception:
            pass

    return {
        "success": True,
        "result": {
            "cookie_count": len(snapshot.get("cookies", [])),
            "storage_keys": [k for k, _ in snapshot.get("localStorage", [])],
            "url": page.url,
        },
    }


# ════════════════════════════════════════════════════════════════
#  后台持续监控 (Background Monitor) — 轮询页面状态
# ════════════════════════════════════════════════════════════════


@feature(
    name="browser_wait_for",
    display_name="等待浏览器条件满足",
    description="【浏览器·监控】在后台轮询浏览器页面，等待指定条件满足（文本出现/消失、元素可见/隐藏、URL 匹配、标题匹配等）。适合在自动化流程中等待异步操作完成",
    category=F.ACTION,
    params=[
        P("condition", "str", "等待条件类型: text_visible | text_hidden | element_visible | element_hidden | url_contains | title_contains | page_stable", example="text_visible",
          choices={"text_visible": "文本出现", "text_hidden": "文本消失", "element_visible": "元素可见", "element_hidden": "元素隐藏", "url_contains": "URL 包含", "title_contains": "标题包含", "page_stable": "页面稳定"}),
        P("target", "str", "条件目标值（如文本内容、URL 片段、选择器）", example="登录成功"),
        P("timeout", "number", "超时秒数", required=False, default=60),
        P("interval", "number", "轮询间隔秒数", required=False, default=1),
    ],
    returns="dict{matched, url, title, elapsed} - 匹配结果",
    tags=["browser", "monitor", "wait", "poll"],
)
def browser_wait_for(condition: str, target: str, timeout: float = 60, interval: float = 1) -> dict:
    if not HAS_PLAYWRIGHT:
        return _not_installed_msg()
    return _pwrap(lambda: _do_wait_for(condition, target, timeout, interval))


def _do_wait_for(condition: str, target: str, timeout: float, interval: float):
    page = _get_page()
    start = time.time()
    deadline = start + timeout

    valid_conditions = {"text_visible", "text_hidden", "element_visible", "element_hidden", "url_contains", "title_contains", "page_stable"}
    if condition not in valid_conditions:
        return {"success": False, "error": f"不支持的条件类型: {condition}。支持: {', '.join(sorted(valid_conditions))}"}

    while time.time() < deadline:
        try:
            current_url = page.url
            current_title = page.title()
            elapsed = time.time() - start
            matched = False

            if condition == "text_visible":
                matched = page.get_by_text(target, exact=False).first.is_visible()
            elif condition == "text_hidden":
                try:
                    matched = not page.get_by_text(target, exact=False).first.is_visible()
                except Exception:
                    matched = True  # element doesn't exist = hidden
            elif condition == "element_visible":
                matched = page.locator(target).first.is_visible()
            elif condition == "element_hidden":
                try:
                    matched = not page.locator(target).first.is_visible()
                except Exception:
                    matched = True
            elif condition == "url_contains":
                matched = target.lower() in current_url.lower()
            elif condition == "title_contains":
                matched = target.lower() in current_title.lower()
            elif condition == "page_stable":
                html1 = page.content()
                time.sleep(0.5)
                html2 = page.content()
                matched = html1 == html2

            if matched:
                return {
                    "success": True,
                    "result": {
                        "matched": True,
                        "url": current_url,
                        "title": current_title,
                        "elapsed": round(elapsed, 2),
                    },
                }
        except Exception:
            pass

        time.sleep(interval)

    return {
        "success": True,
        "result": {
            "matched": False,
            "url": page.url,
            "title": page.title(),
            "elapsed": round(timeout, 2),
            "error": f"等待条件 '{condition}={target}' 超时 ({timeout}s)",
        },
    }
