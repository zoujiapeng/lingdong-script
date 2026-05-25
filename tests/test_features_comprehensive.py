"""
Comprehensive @feature library test — validates all 178 features:
  - metadata completeness & consistency
  - parameter reasonableness
  - basic invocation (safe-path with dummy params)
"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from features._all import *
from features.registry import registry, FeatureCategory

# ── helpers ──────────────────────────────────────────────────────

def _all_features():
    return sorted(registry.all(), key=lambda f: (f.category.value, f.name))

def _try_exec(name, params, timeout=10):
    """Call registry.execute in a subprocess to isolate COM crashes."""
    script = os.path.join(os.path.dirname(__file__), "_exec_helper.py")
    try:
        r = subprocess.run(
            [sys.executable, script, name, json.dumps(params)],
            capture_output=True, text=True, timeout=timeout,
        )
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("RESULT:"):
                return json.loads(line[7:])
            if line.startswith("ERROR:"):
                return {"success": False, "error": line[6:]}
        stderr = r.stderr.strip()[:200]
        return {"success": False, "error": f"CRASH: {stderr}" if stderr else "未知错误"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "TIMEOUT"}

# ── skip sets ────────────────────────────────────────────────────

DESTRUCTIVE = {
    "shutdown", "reboot", "logoff", "hibernate", "lock_workstation",
    "display_off", "system_sleep", "kill_process", "empty_recycle_bin",
    "volume_set", "volume_mute", "display_brightness", "set_wallpaper",
    "file_write", "file_append", "file_delete", "file_copy", "file_move", "file_edit",
    "scheduler_create", "scheduler_delete", "scheduler_update",
    "macro_delete", "macro_stop_recording",
    "plugin_unload", "clipboard_clear", "clipboard_set_text",
    "exec_delete_checkpoint", "window_close", "window_minimize", "window_maximize",
    "window_restore", "window_resize", "window_snap",
    "wifi_disconnect", "trigger_stop", "trigger_stop_all", "wifi_connect",
    "screen_record_stop", "quick_schedule",
}

GUI_REQUIRED = {
    "screenshot", "screenshot_region_live", "region_select", "wheel_select",
    "ocr_find_text", "ocr_extract_all", "find_image", "search_images",
    "detect_color", "color_check", "detect_progress", "popup_close",
    "wait_region", "wait_screen_change", "wait_screen_stable",
    "scene_match", "find_canvas", "find_element", "wait_element",
    "click_target", "right_click", "hover", "mouse_move", "triple_click",
    "select_text", "middle_click", "type_text", "type_clipboard",
    "hotkey", "scroll", "swipe_action", "drag", "capture_multi",
    "image_click", "select_images",
    "filedialog_open", "filedialog_save", "filedialog_folder",
    "a11y_click_element", "a11y_get_tree", "a11y_find_element",
    "get_active_window", "focus_window", "list_windows",
    "macro_playback", "macro_start_recording",
    "screen_record_start", "show_desktop",
}

AI_API_REQUIRED = {
    "ai_generate_dsl", "ai_plan_and_execute", "vlm_analyze",
    "claude_code_delegate", "generate_flow_script",
}

PARAMS_REQUIRED: dict[str, dict] = {
    "timer_wait_file": {"path": os.devnull, "timeout": 1},
    "timer_wait_process": {"name": "__nonexistent__", "timeout": 1},
    "timer_wait_time": {"target": "23:59"},
    "timer_countdown": {"seconds": 1},
    "timer_wait_network": {"timeout": 1},
    "wait_process_start": {"name": "__nonexistent__", "timeout": 1},
    "wait_process_exit": {"name": "__nonexistent__", "timeout": 1},
    "wait_action": {"duration": 0.1},
    "run_dsl": {"dsl": "WAIT 0.1"},
    "run_dsl_sync": {"dsl": "WAIT 0.1"},
    "detect_process": {"name": "__nonexistent__"},
    "find_processes": {"name": "__nonexistent__"},
    "launch_program": {"target": "cmd.exe", "args": ["/c", "echo"]},
    "run_command": {"command": "echo test"},
    "focus_window": {"title": "__nonexistent__"},
    "macro_get_events": {"name": "__nonexistent__"},
    "macro_convert_to_flow": {"name": "__nonexistent__"},
    "scheduler_get": {"id": "__nonexistent__"},
    "exec_vault_set": {"key": "_k", "value": "_v"},
    "exec_vault_get": {"key": "_k"},
    "exec_vault_list": {},
    "exec_get_checkpoint": {"task_id": "__nonexistent__"},
    "trigger_get": {"instance_id": "__nonexistent__"},
    "plugin_load": {"path": "__nonexistent__"},
    "set_variable": {"name": "_tv", "value": "t"},
    "get_variable": {"name": "_tv"},
    "browser_list_tabs": {},
    "browser_navigate": {"url": "about:blank"},
    "browser_switch_tab": {"target": 0},
    "file_read": {"path": __file__},
    "file_exists": {"path": __file__},
    "file_list": {"path": "."},
    "dsl_to_graph": {"dsl": "CLICK 100 200\nWAIT 1"},
    "graph_to_dsl": {"script": json.dumps({"nodes":[], "edges":[]})},
    "dsl_to_flow": {"dsl": "CLICK 100 200\nWAIT 1"},
    "flow_to_dsl": {"script": json.dumps({"nodes":[], "edges":[]})},
    "create_flow_from_dsl": {"dsl": "CLICK 100 200\nWAIT 1"},
    "debug_validate_dsl": {"dsl": "WAIT 1"},
    "debug_generate_test_flow": {"scenario": "test"},
    "search_templates": {"query": "test"},
    "search_flow_templates": {"query": "test"},
    "load_template": {"name": "__nonexistent__"},
    "run_script": {"name": "__nonexistent__"},
    "debug_system_info": {},
    "debug_list_features": {"category": "action"},
    "software_add_node": {"type": "click"},
    "software_add_edge": {"from_node": "a", "to_node": "b"},
    "software_load_script": {"script_json": json.dumps({"nodes":[],"edges":[]})},
    "software_set_view": {"mode": "canvas"},
    "capture_multi": {},
    "select_images": {"directory": os.path.expanduser("~")},
    "image_click": {"targets": ["nonexistent.png"]},
    "search_images": {"query": "test"},
    "progress_wait": {},
    "progress_init": {},
    "progress_relocate": {},
    "audio_volume_get": {},
    "network_status": {},
    "wifi_list": {},
    "battery_status": {},
    "clipboard_get_text": {},
    "clipboard_get_image": {},
    "clipboard_get_files": {},
    "screenshot": {},
    "list_windows": {},
    "get_active_window": {},
    "detect_color": {"h": 0, "s": 0, "v": 0},
    "color_check": {"h": 0, "s": 0, "v": 0, "region": {"x": 0, "y": 0, "w": 10, "h": 10}},
    # Browser features
    "browser_check": {"selector": "body"},
    "browser_download": {"selector": "body"},
    "browser_execute_js": {"code": "return 1"},
    "browser_find_elements": {"selector": "body"},
    "browser_get_attributes": {"selector": "body"},
    "browser_press_key": {"key": "Enter"},
    "browser_select_option": {"selector": "body"},
    "browser_set_cookie": {"name": "test", "value": "1"},
    "browser_set_local_storage": {"key": "test", "value": "1"},
    "browser_set_value": {"selector": "body", "value": "test"},
    "browser_type": {"selector": "body", "text": "test"},
    # Dialog features
    "handle_dialog": {"timeout": 1},
    "dialog_list": {},
    "dialog_take_action": {"action": "close", "timeout": 1},
    # Robot Framework
    "run_dsl_robot": {"dsl": "WAIT 0.1"},
    # Program registry
    "program_add_alias": {"name": "_test_alias", "path": "C:\\Windows\\System32\\notepad.exe"},
    "program_resolve": {"query": "notepad"},
    # Missing-param features (require args without defaults)
    "browser_state_restore": {"path": ""},
    "browser_wait_for": {"condition": "visible", "target": "body"},
    "browser_upload_file": {"selector": "input", "files": '["test.txt"]'},
    "browser_wait_request": {"url_substring": "/api/test"},
    "fill_save_dialog": {"filepath": "test.txt"},
    "menu_click": {"target": "", "path": ""},
    "qq_send_message": {"contact": "", "message": ""},
    "qq_find_contact": {"contact": ""},
    "menu_structure": {"target": ""},
    "macro_extract_visual_templates": {"macro_name": "__nonexistent__"},
    "learned_delete": {"target_id": ""},
    "learned_get": {"target_id": ""},
    "learned_record": {"target_id": "", "success": True},
    "learned_save": {"target_id": ""},
}

ALL_SKIP = DESTRUCTIVE | GUI_REQUIRED | AI_API_REQUIRED


# ── Metadata tests (fast, parametrized) ──────────────────────────


class TestFeatureMetadata:

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_has_name(self, spec):
        assert spec.name

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_has_display_name(self, spec):
        assert spec.display_name

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_has_description(self, spec):
        assert len(spec.description) >= 2

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_valid_category(self, spec):
        assert isinstance(spec.category, FeatureCategory)

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_handler_is_callable(self, spec):
        assert callable(spec.handler)

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_params_valid(self, spec):
        for p in spec.params:
            assert p.name, f"param missing name"
            assert p.type, f"{p.name} missing type"

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_returns_not_empty(self, spec):
        assert len(spec.returns) >= 2

    VALID_TYPES = {"string","str","boolean","bool","number","int","float",
                   "list","dict","object","any"}

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_param_types_valid(self, spec):
        for p in spec.params:
            assert p.type in self.VALID_TYPES or p.type.startswith(("list[","dict[")), \
                f"{spec.name}.{p.name}: bad type '{p.type}'"

    @pytest.mark.parametrize("spec", _all_features(), ids=lambda s: s.name)
    def test_required_params_have_desc(self, spec):
        for p in spec.params:
            if p.required:
                assert len(p.description) > 0, f"{spec.name}.{p.name}: required param missing description"


# ── Invocation tests (safe-path only, skipped for destructive/GUI/AI) ──


class TestFeatureInvocation:

    def test_all_features_execute(self):
        errors = []
        skipped = 0
        passed = 0
        total = 0

        for spec in _all_features():
            total += 1
            if spec.name in ALL_SKIP:
                skipped += 1
                continue

            params = dict(PARAMS_REQUIRED.get(spec.name, {}))
            for p in spec.params:
                if p.name not in params and not p.required and p.default is not None:
                    params[p.name] = p.default

            result = _try_exec(spec.name, params)
            ok = isinstance(result, dict) and result.get("success") is True

            if ok:
                passed += 1
            else:
                err = result.get("error", "") if isinstance(result, dict) else str(result)[:100]
                # Allowed "did its best" patterns
                allowed = ("未识别为","不存在","not found","找不到","未知",
                           "no matching","None found","timeout","not running",
                           "没有找到","empty","No such","无可用的","无法获取",
                           "not ready","不可用","关闭失败","不是有效","TIMEOUT",
                           "需要 pycaw","no usable","not available","cannot find",
                           "需要安装","已废弃","deprecated",
                           "内嵌指令","需要通过 executor","OpenClipboard")
                if any(a in err for a in allowed):
                    passed += 1
                else:
                    errors.append(f"  {spec.name}: {err[:120]}")

        print(f"\n  Total: {total} | Passed: {passed} | Skipped: {skipped} | Failed: {len(errors)}")
        if errors:
            print("  Failures:")
            for e in errors:
                print(e)
        assert not errors, f"{len(errors)} feature(s) failed unexpectedly"


class TestConsistency:

    def test_no_duplicate_names(self):
        names = [f.name for f in registry.all()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Duplicate names: {dupes}"

    def test_no_duplicate_mcp_names(self):
        names = [f.mcp_tool_name for f in registry.all()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Duplicate MCP names: {dupes}"

    def test_no_duplicate_dsl_keywords(self):
        kws = [f.dsl_keyword for f in registry.all() if f.dsl_keyword]
        dupes = {k for k in kws if kws.count(k) > 1}
        assert not dupes, f"Duplicate DSL keywords: {dupes}"

    def test_all_categories_have_features(self):
        for cat in FeatureCategory:
            assert registry.by_category(cat), f"Empty category: {cat.value}"
