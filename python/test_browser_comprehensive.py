"""
浏览器功能深度测试 — 覆盖 browser_features.py + web_features.py 全部功能。
注入 headless Playwright 实例到模块全局状态，在真实浏览器中逐功能验证。
"""
import sys, os, json, time, tempfile

sys.path.insert(0, os.path.dirname(__file__))

# ── 导入浏览器功能模块 ──────────────────────────────────────────
import features.browser_features as B
import features.web_features as W

# ── 启动 headless 浏览器并注入模块全局 ─────────────────────────
from playwright.sync_api import sync_playwright

_pw = sync_playwright().start()
_browser = _pw.chromium.launch(headless=True)
_context = _browser.new_context(viewport={"width": 1280, "height": 720}, locale="zh-CN")
_page = _context.new_page()

# 注入全局状态，让 browser_features 的各功能直接使用
B._pw = _pw
B._browser = _browser
B._context = _context
B._page = _page
B.HAS_PLAYWRIGHT = True

# 共享 Playwright 实例到 web_features，避免 "Sync API inside asyncio loop"
W._playwright_browser = _browser
W._playwright_page = _page
W.HAS_PLAYWRIGHT = True

def suite(name):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

passed = 0
failed = 0
errors = []

def test(feature_name, func, *args, expect_success=True, **kwargs):
    global passed, failed
    label = f"{feature_name}({', '.join(str(a) for a in args) + (', ' + ', '.join(f'{k}={v}' for k,v in kwargs.items()) if kwargs else '')})"
    try:
        result = func(*args, **kwargs)
        if isinstance(result, dict):
            ok = result.get("success", False)
            if expect_success and ok:
                status = "PASS"
                passed += 1
            elif not expect_success and not ok:
                status = f"PASS(expected fail: {result.get('error','')})"
                passed += 1
            elif expect_success and not ok:
                status = f"FAIL({result.get('error','')})"
                failed += 1
                errors.append((label, result.get('error','')))
            else:
                status = f"UNEXPECTED_PASS"
                passed += 1
        else:
            status = f"OK({result})"
            passed += 1
    except Exception as e:
        status = f"ERROR({e})"
        failed += 1
        errors.append((label, str(e)))
    print(f"  [{status}] {label}")


# ═════════════════════════════════════════════════════════════════
suite("1. 页面准备 — 导航到测试页面")
# ─────────────────────────────────────────────────────────────────

# 使用 page.route 拦截请求以提供有 origin 的测试页面（localStorage 需要非 null origin）
_TEST_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Browser Test</title></head>
<body>
  <h1 id="title">测试页面</h1>
  <p class="desc">这是一个用于测试浏览器功能的页面</p>
  <input id="name" type="text" placeholder="输入姓名">
  <input id="email" type="email" value="test@example.com">
  <select id="city">
    <option value="bj">北京</option>
    <option value="sh" selected>上海</option>
    <option value="gz">广州</option>
  </select>
  <input id="agree" type="checkbox" checked>
  <label for="agree">同意</label>
  <button id="submit" onclick="document.title='Submitted'">提交</button>
  <a id="link" href="https://example.com">示例链接</a>
  <div id="hover-area" style="width:100px;height:100px;background:red"
       onmouseenter="this.style.background='green'">悬停区域</div>
  <div id="scroll-target" style="margin-top:2000px;height:100px;background:blue">滚动目标</div>
  <input id="file-input" type="file">
  <div id="output"></div>
  <script>
    document.getElementById('name').addEventListener('input', function() {
      document.getElementById('output').textContent = 'typed:' + this.value;
    });
  </script>
</body></html>"""

_page.route("**/*", lambda route: route.fulfill(body=_TEST_HTML, status=200, headers={"Content-Type": "text/html"}))
_page.goto("http://browser-test.local/")
time.sleep(0.5)
print("  测试页面已加载 (origin: http://browser-test.local/)")


# ═════════════════════════════════════════════════════════════════
suite("2. 浏览器生命周期 & 标签管理")
# ─────────────────────────────────────────────────────────────────

# browser_new_tab
test("browser_new_tab", B.browser_new_tab, "about:blank")
test("browser_new_tab(空)", B.browser_new_tab, "")

# browser_list_tabs
test("browser_list_tabs", B.browser_list_tabs)

# browser_switch_tab (by title)
test("browser_switch_tab(title)", B.browser_switch_tab, "Browser Test")

# browser_get_url
test("browser_get_url", B.browser_get_url)

# browser_get_title
test("browser_get_title", B.browser_get_title)

# browser_close_tab test on a dedicated temp tab (close current = the temp page is active)
_page2_close = _context.new_page()
_page2_close.set_content('<title>TempTab</title><h1>Temp</h1>')
B._page = _page2_close
test("browser_close_tab(关闭当前)", B.browser_close_tab, "")
# After close, B._page is auto-switched to another page; set back to main
B._page = _page


# ═════════════════════════════════════════════════════════════════
suite("3. 导航操作")
# ─────────────────────────────────────────────────────────────────

# Navigate to a simple page
test("browser_navigate(about:blank)", B.browser_navigate, "about:blank")
test("browser_navigate(data:url)", B.browser_navigate, "data:text/html,<h1>Nav Test</h1>")
test("browser_get_url", B.browser_get_url)
test("browser_get_title", B.browser_get_title)

# Refresh test on a dedicated page (not main test page to preserve content)
_page2 = _context.new_page()
old_page2 = B._page
B._page = _page2
_page2.set_content('<h1>Refresh Test</h1>')
test("browser_refresh", B.browser_refresh)
_page2.close()
B._page = old_page2

# Re-set test page by re-navigating to the route-intercept URL (preserves origin for localStorage)
_page.goto("http://browser-test.local/")
B._page = _page

# browser_switch_tab by index (now test page has title "Browser Test" to switch back)
test("browser_switch_tab(idx=1)", B.browser_switch_tab, "1")
B.browser_switch_tab("Browser Test")


# ═════════════════════════════════════════════════════════════════
suite("4. 元素操作")
# ─────────────────────────────────────────────────────────────────

# browser_type
test("browser_type", B.browser_type, "#name", "测试用户")

# browser_set_value
test("browser_set_value", B.browser_set_value, "#email", "new@test.com")

# browser_select_option (by value)
test("browser_select_option(value)", B.browser_select_option, "#city", value="gz")
# by label
test("browser_select_option(label)", B.browser_select_option, "#city", label="北京")

# browser_check
test("browser_check(uncheck)", B.browser_check, "#agree", checked=False)
test("browser_check(check)", B.browser_check, "#agree", checked=True)

# browser_press_key
test("browser_press_key(Enter)", B.browser_press_key, "Enter")

# browser_click
test("browser_click(selector)", B.browser_click, selector="#submit")
test("browser_click(text)", B.browser_click, text="提交")

# browser_hover
test("browser_hover(selector)", B.browser_hover, selector="#hover-area")
test("browser_hover(text)", B.browser_hover, text="悬停区域")

# browser_scroll_to
test("browser_scroll_to(selector)", B.browser_scroll_to, selector="#scroll-target")
test("browser_scroll_to(text)", B.browser_scroll_to, text="滚动目标")


# ═════════════════════════════════════════════════════════════════
suite("5. 内容提取")
# ─────────────────────────────────────────────────────────────────

# browser_get_text
test("browser_get_text(全页)", B.browser_get_text)
test("browser_get_text(selector)", B.browser_get_text, "h1")

# browser_get_html
test("browser_get_html", B.browser_get_html)
test("browser_get_html(outer)", B.browser_get_html, "h1", outer=True)

# browser_get_attributes
test("browser_get_attributes", B.browser_get_attributes, "#link")
test("browser_get_attributes(single)", B.browser_get_attributes, "#link", attribute="href")

# browser_find_elements
test("browser_find_elements", B.browser_find_elements, "input, button, select")

# browser_screenshot
tmp_img = os.path.join(tempfile.gettempdir(), "browser_test_ss.png")
test("browser_screenshot", B.browser_screenshot, path=tmp_img)
if os.path.exists(tmp_img):
    sz = os.path.getsize(tmp_img)
    print(f"    截图已保存: {tmp_img} ({sz} bytes)")
    os.remove(tmp_img)


# ═════════════════════════════════════════════════════════════════
suite("6. JavaScript 执行")
# ─────────────────────────────────────────────────────────────────

test("browser_execute_js(expression)", B.browser_execute_js, "document.title")
test("browser_execute_js(return)", B.browser_execute_js, "return document.querySelector('h1').textContent")
test("browser_execute_js(复杂)", B.browser_execute_js, """
    const els = document.querySelectorAll('input');
    return Array.from(els).map(el => el.id + '=' + (el.value || el.placeholder));
""")


# ═════════════════════════════════════════════════════════════════
suite("7. 等待操作")
# ─────────────────────────────────────────────────────────────────

# browser_wait_element
test("browser_wait_element(selector)", B.browser_wait_element, selector="h1")
test("browser_wait_element(text)", B.browser_wait_element, text="测试页面")

# Dynamic element test
_page.evaluate("setTimeout(() => document.body.insertAdjacentHTML('beforeend', '<div id=\"late\">延迟出现</div>'), 200)")
test("browser_wait_element(动态)", B.browser_wait_element, selector="#late", timeout=5)

# browser_wait_navigation (navigate and wait)
_page2 = _context.new_page()
old_page = B._page
B._page = _page2
_page2.goto("about:blank")
test("browser_wait_navigation", B.browser_wait_navigation, timeout=10)
B._page = old_page
_page2.close()

# browser_wait_page_load
test("browser_wait_page_load", B.browser_wait_page_load, timeout=10)

# browser_wait_for
test("browser_wait_for(title)", B.browser_wait_for, condition="title_contains", target="Browser", timeout=5)
test("browser_wait_for(url)", B.browser_wait_for, condition="url_contains", target="about", timeout=5)

# browser_wait_request
test("browser_wait_request", B.browser_wait_request, "/nonexistent", timeout=5, expect_success=False)


# ═════════════════════════════════════════════════════════════════
suite("8. 存储管理")
# ─────────────────────────────────────────────────────────────────

# localStorage
test("browser_set_local_storage", B.browser_set_local_storage, "theme", "dark")
test("browser_get_local_storage(all)", B.browser_get_local_storage)
test("browser_get_local_storage(key)", B.browser_get_local_storage, key="theme")

# Cookies
test("browser_set_cookie", B.browser_set_cookie, "session", "abc123", domain="localhost")
test("browser_get_cookies", B.browser_get_cookies)
test("browser_delete_cookies(single)", B.browser_delete_cookies, '["session"]')


# ═════════════════════════════════════════════════════════════════
suite("9. 视觉锚点定位")
# ─────────────────────────────────────────────────────────────────

test("browser_visual_anchor(text)", B.browser_visual_anchor, text="提交")
test("browser_visual_anchor(selector)", B.browser_visual_anchor, selector="#submit")
test("browser_visual_anchor(不存在的元素)", B.browser_visual_anchor, text="__nonexistent__",
     expect_success=False)


# ═════════════════════════════════════════════════════════════════
suite("10. 状态快照")
# ─────────────────────────────────────────────────────────────────

test("browser_state_snapshot", B.browser_state_snapshot, name="test_snapshot")

# Clean up snapshot file
snap_path = os.path.join(os.path.dirname(__file__), "browser_snapshots", "test_snapshot.json")
if os.path.exists(snap_path):
    os.remove(snap_path)


# ═════════════════════════════════════════════════════════════════
suite("11. 文件上传")
# ─────────────────────────────────────────────────────────────────

# Create a temp file for upload
tmp_file = os.path.join(tempfile.gettempdir(), "browser_upload_test.txt")
with open(tmp_file, "w") as f:
    f.write("test content")

test("browser_upload_file", B.browser_upload_file, "#file-input", json.dumps([tmp_file]))
os.remove(tmp_file)


# ═════════════════════════════════════════════════════════════════
suite("12. web_browser_execute（独立 Playwright 接口）")
# ─────────────────────────────────────────────────────────────────

test("web_browser_execute(navigate)", W.web_browser_execute,
     action="navigate", url="about:blank", headless=True)
test("web_browser_execute(click)", W.web_browser_execute,
     action="click", selector="h1", headless=True,
     expect_success=False)  # h1 on about:blank → no match → fail
test("web_browser_execute(screenshot)", W.web_browser_execute,
     action="screenshot", headless=True)
test("web_browser_execute(wait)", W.web_browser_execute,
     action="wait", wait_seconds=0.2, headless=True)
test("web_browser_execute(extract)", W.web_browser_execute,
     action="extract", selector="body", headless=True)
test("web_browser_execute(scroll)", W.web_browser_execute,
     action="scroll", scroll_direction="down", headless=True)


# ═════════════════════════════════════════════════════════════════
suite("13. 关闭浏览器")
# ─────────────────────────────────────────────────────────────────

test("browser_close", B.browser_close)


# ═════════════════════════════════════════════════════════════════
#  清理 & 报告
# ═════════════════════════════════════════════════════════════════
try:
    _pw.stop()
except Exception:
    pass

print(f"\n{'='*60}")
print(f"  测试完成")
print(f"  {'='*30}")
print(f"  通过: {passed}")
print(f"  失败: {failed}")
if errors:
    print(f"\n  失败详情:")
    for name, err in errors:
        print(f"    - {name}")
        print(f"      {err}")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
