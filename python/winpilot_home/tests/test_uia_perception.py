def test_chromium_legacy_root_is_skipped_only_when_modern_tree_exists():
    from winpilot.perception.uia import _is_legacy_chromium_root_child

    assert _is_legacy_chromium_root_child(
        "Chrome_RenderWidgetHostHWND",
        has_modern_browser_tree=True,
    ) is True
    assert _is_legacy_chromium_root_child(
        "Chrome_RenderWidgetHostHWND",
        has_modern_browser_tree=False,
    ) is False
    assert _is_legacy_chromium_root_child(
        "BrowserRootView",
        has_modern_browser_tree=True,
    ) is False


def test_exact_uia_query_rejects_direct_legacy_renderer_match():
    from winpilot.perception.uia import _is_legacy_chromium_match

    class Control:
        def __init__(self, class_name="", control_type="PaneControl", parent=None):
            self.ClassName = class_name
            self.ControlTypeName = control_type
            self.parent = parent

        def GetParentControl(self):
            return self.parent

    root = Control("Chrome_WidgetWin_1", "WindowControl")
    direct_renderer = Control("Chrome_RenderWidgetHostHWND", parent=root)
    stale_button = Control(parent=direct_renderer)
    browser_view = Control("Chrome_WidgetWin_1", "PaneControl", root)
    modern_renderer = Control("Chrome_RenderWidgetHostHWND", parent=browser_view)
    live_button = Control(parent=modern_renderer)

    assert _is_legacy_chromium_match(
        stale_button,
        has_modern_browser_tree=True,
    ) is True
    assert _is_legacy_chromium_match(
        live_button,
        has_modern_browser_tree=True,
    ) is False
    assert _is_legacy_chromium_match(
        stale_button,
        has_modern_browser_tree=False,
    ) is False
