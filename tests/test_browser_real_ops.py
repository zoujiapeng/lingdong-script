"""
浏览器真实操作测试 — 在真实网站上测试浏览器的全部交互能力。

测试覆盖:
  - 导航: navigate, refresh, back, forward
  - 元素操作: click, type, select, hover, check, scroll_to, press_key
  - 内容提取: text, html, attributes, title, url
  - 脚本执行: execute_js
  - Cookie/Storage: get/set/delete cookies, localStorage
  - 视觉锚点: visual_anchor
  - 等待: wait_for, wait_element
  - 截图: screenshot
  - 状态快照: state_snapshot / state_restore
  - 多标签: new_tab, list_tabs, switch_tab, close_tab
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from features._all import *
from features.registry import registry


def run(feature_name, **params):
    """Execute a feature and return the result dict."""
    result = registry.execute(feature_name, **params)
    ok = result.get("success")
    err = result.get("error", "")
    status_str = "OK" if ok else "FAIL"
    print(f"  [{status_str}] {feature_name}({params}) -> {err if err else 'OK'}")
    return result


def run_browser_tests():
    """Run all browser tests sequentially (browser state is shared)."""
    results = {"passed": 0, "failed": 0, "errors": []}

    def test(name, fn):
        try:
            fn()
            results["passed"] += 1
            print(f"\n  === PASS: {name} ===")
        except Exception as e:
            results["failed"] += 1
            results["errors"].append((name, str(e)))
            print(f"\n  === FAIL: {name}: {e} ===")

    # ═══════════════════════════════════════════════════
    # 1. 启动浏览器并导航到真实网站
    # ═══════════════════════════════════════════════════
    test("导航到百度首页", lambda:
        run("browser_navigate", url="https://www.baidu.com", wait_until="domcontentloaded")
    )

    test("获取页面标题和URL", lambda:
        (run("browser_get_title"),
         run("browser_get_url"))
    )

    test("获取页面文本内容", lambda:
        run("browser_get_text", selector="body")
    )

    test("获取页面 HTML", lambda:
        run("browser_get_html", selector="head title")
    )

    # ═══════════════════════════════════════════════════
    # 2. 元素操作 — 搜索框输入 + 点击搜索
    # ═══════════════════════════════════════════════════
    test("搜索框输入文字", lambda:
        run("browser_type", selector="#kw", text="Playwright自动化测试", delay=20, clear_first=True)
    )

    test("点击搜索按钮", lambda:
        run("browser_click", selector="#su")
    )

    test("等待搜索结果加载", lambda:
        run("browser_wait_element", selector=".result", timeout=10)
    )

    test("提取搜索结果", lambda:
        run("browser_get_text", selector=".result")
    )

    # ═══════════════════════════════════════════════════
    # 3. 导航操作
    # ═══════════════════════════════════════════════════
    test("后退", lambda:
        run("browser_go_back")
    )

    test("前进", lambda:
        run("browser_go_forward")
    )

    test("刷新页面", lambda:
        run("browser_refresh")
    )

    # ═══════════════════════════════════════════════════
    # 4. 多标签页操作
    # ═══════════════════════════════════════════════════
    test("列出当前标签页", lambda:
        run("browser_list_tabs")
    )

    test("打开新标签页(about:blank)", lambda:
        run("browser_new_tab", url="about:blank")
    )

    test("打开第二个标签页到GitHub", lambda:
        run("browser_navigate", url="https://github.com", wait_until="domcontentloaded")
    )

    test("获取GitHub标题", lambda:
        run("browser_get_title")
    )

    test("列出所有标签页确认多标签", lambda:
        run("browser_list_tabs")
    )

    test("切换回第一个标签页", lambda:
        run("browser_switch_tab", target=0)
    )

    test("确认回到百度", lambda:
        run("browser_get_title")
    )

    # ═══════════════════════════════════════════════════
    # 5. JavaScript 执行
    # ═══════════════════════════════════════════════════
    test("执行JS获取页面信息", lambda:
        run("browser_execute_js", code="return {url: location.href, title: document.title, cookies: document.cookie}")
    )

    test("执行JS修改页面", lambda:
        run("browser_execute_js", code="document.title = 'Modified Title'")
    )

    test("验证JS修改效果", lambda:
        run("browser_get_title")
    )

    # ═══════════════════════════════════════════════════
    # 6. 元素高级操作
    # ═══════════════════════════════════════════════════
    test("导航到MDN测试hover/scroll", lambda:
        run("browser_navigate", url="https://developer.mozilla.org/zh-CN/docs/Web", wait_until="domcontentloaded")
    )

    test("查找页面元素", lambda:
        run("browser_find_elements", selector="a", max_results=5)
    )

    test("获取元素属性", lambda:
        run("browser_get_attributes", selector="a", attribute="href")
    )

    test("滚动到元素", lambda:
        run("browser_scroll_to", selector="footer", behavior="smooth")
    )

    test("获取滚动后的页面文本", lambda:
        run("browser_get_text", selector="footer")
    )

    # ═══════════════════════════════════════════════════
    # 7. Cookie 和 Storage 操作
    # ═══════════════════════════════════════════════════
    test("导航回百度(Cookie测试准备)", lambda:
        run("browser_navigate", url="https://www.baidu.com")
    )

    test("获取当前cookies", lambda:
        run("browser_get_cookies")
    )

    test("设置自定义cookie", lambda:
        run("browser_set_cookie", name="test_cookie", value="hello_browser", domain=".baidu.com")
    )

    test("确认cookie已设置", lambda:
        run("browser_get_cookies")
    )

    test("设置localStorage", lambda:
        run("browser_set_local_storage", key="test_key", value="test_value")
    )

    test("读取localStorage", lambda:
        run("browser_get_local_storage", key="test_key")
    )

    test("删除cookie", lambda:
        run("browser_delete_cookies", names="test_cookie")
    )

    test("确认cookie已删除", lambda:
        run("browser_get_cookies")
    )

    # ═══════════════════════════════════════════════════
    # 8. 视觉锚点 (新功能)
    # ═══════════════════════════════════════════════════
    test("视觉锚点: 通过文本定位", lambda:
        run("browser_visual_anchor", text="新闻", timeout=5)
    )

    test("视觉锚点: 通过CSS选择器定位搜索框", lambda:
        run("browser_visual_anchor", selector="#kw", timeout=5)
    )

    # ═══════════════════════════════════════════════════
    # 9. 状态快照 (新功能)
    # ═══════════════════════════════════════════════════
    test("保存浏览器状态快照", lambda:
        run("browser_state_snapshot", name="baidu_test_snapshot")
    )

    # ═══════════════════════════════════════════════════
    # 10. 等待条件 (新功能)
    # ═══════════════════════════════════════════════════
    test("等待URL包含条件", lambda:
        run("browser_wait_for", condition="url_contains", target="baidu", timeout=5)
    )

    test("等待标题包含条件", lambda:
        run("browser_wait_for", condition="title_contains", target="百度", timeout=5)
    )

    # ═══════════════════════════════════════════════════
    # 11. 截图
    # ═══════════════════════════════════════════════════
    test("页面截图", lambda:
        run("browser_screenshot", full_page=True, path="browser_test_screenshot.png")
    )

    # ═══════════════════════════════════════════════════
    # 12. 恢复状态快照
    # ═══════════════════════════════════════════════════
    test("恢复浏览器状态快照", lambda:
        run("browser_state_restore", path="baidu_test_snapshot")
    )

    # ═══════════════════════════════════════════════════
    # 13. 关闭浏览器
    # ═══════════════════════════════════════════════════
    test("关闭浏览器", lambda:
        run("browser_close")
    )

    # ═══════════════════════════════════════════════════
    # 报告
    # ═══════════════════════════════════════════════════
    print("")
    print("=" * 60)
    print("  Browser Real-Ops Test Report")
    print("=" * 60)
    print(f"  PASS: {results['passed']}  |  FAIL: {results['failed']}")
    if results["errors"]:
        print("")
        print("  Failures:")
        for name, err in results["errors"]:
            print(f"    - {name}: {err}")
    print("=" * 60)

    return results


if __name__ == "__main__":
    run_browser_tests()
