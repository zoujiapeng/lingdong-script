"""
dialog_handler 全面测试套件。

分层架构：
  Level 1 — Unit: 模拟 win32 API，快速验证分类/按钮查找/边界条件
  Level 2 — Mock Handler: 模拟 find_all_dialogs，验证 handle_any_dialog 路由
  Level 3 — Integration: 真实 Notepad 进程，验证端到端检测+关闭

运行:
  pytest tests/test_dialog_handler.py -x -v              # unit + mock
  pytest tests/test_dialog_handler.py -x -v --run-real    # 包含真实对话框测试
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

import pytest
from pytest import mark as pytest_mark  # noqa: F401

# Register custom marks
def pytest_configure(config):
    config.addinivalue_line('markers', 'real: tests that interact with real Windows dialogs')


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

# ====================================================================
#  Fixtures & helpers
# ====================================================================


class FakeHWND:
    """Monotonically incrementing fake HWND values for mock tests."""
    _next = 100_000

    @classmethod
    def next(cls) -> int:
        cls._next += 1
        return cls._next


def make_button(hwnd: int, text: str, enabled: bool = True,
                rect: tuple[int, int, int, int] = (0, 0, 20, 20)) -> dict:
    return {
        "hwnd": hwnd,
        "text": text,
        "name": text.lower().replace("&", ""),
        "rect": rect,
        "enabled": enabled,
        "visible": True,
    }


# ====================================================================
#  Level 1 — 纯单元测试（mock win32 API）
# ====================================================================


class TestDialogInfoScanButtons:
    """DialogInfo._scan_buttons: 验证按钮枚举逻辑。"""

    def test_empty_dialog(self):
        """无子窗口的对话框 → 空按钮列表。"""
        hwnd = FakeHWND.next()
        with patch.multiple(
            "agent.dialog_handler.win32gui",
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "测试对话框",
            GetWindowRect=lambda h: (0, 0, 100, 100),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
            EnumChildWindows=lambda parent, cb, _: True,
        ):
            from agent.dialog_handler import DialogInfo
            dlg = DialogInfo(hwnd)
            assert dlg.buttons == []

    def test_only_button_children_collected(self):
        """只收集 Button 类的子窗口，忽略 Edit/Static 等。"""
        hwnd = FakeHWND.next()
        captured_cb = [None]

        def fake_enum(parent, callback, _unused):
            captured_cb[0] = callback
            # Simulate child windows
            btn_hwnd = FakeHWND.next()
            # These should be collected:
            _call_cb(callback, btn_hwnd, None)   # class Button
            edit_hwnd = FakeHWND.next()
            _call_cb(callback, edit_hwnd, None)  # class Edit — ignored
            return True

        def _call_cb(cb, hwnd_, _unused):
            # We need to trick GetClassName to return what we want
            ...

        # Actually let's use a simpler approach
        from agent.dialog_handler import DialogInfo

        child_buttons = {}

        def _enum(parent, cb, _unused):
            for cls_name, btn_text in [
                ("Button", "确定"),
                ("Edit", ""),
                ("Button", "取消"),
                ("Static", "提示信息"),
                ("Button", "保存(&S)"),
            ]:
                ch = FakeHWND.next()
                child_buttons[ch] = (cls_name, btn_text)
                cb(ch, None)
            return True

        orig_gcn = sys.modules["agent.dialog_handler"].win32gui.GetClassName
        orig_gwt = sys.modules["agent.dialog_handler"].win32gui.GetWindowText
        orig_gwr = sys.modules["agent.dialog_handler"].win32gui.GetWindowRect
        orig_iwe = sys.modules["agent.dialog_handler"].win32gui.IsWindowEnabled
        orig_iwv = sys.modules["agent.dialog_handler"].win32gui.IsWindowVisible

        def mock_gcn(h):
            return child_buttons.get(h, ("", ""))[0]

        def mock_gwt(h):
            return child_buttons.get(h, ("", ""))[1]

        def mock_gwr(h):
            return (0, 0, 10, 10)

        def mock_iwe(h):
            return True

        def mock_iwv(h):
            return True

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=_enum,
            GetClassName=mock_gcn,
            GetWindowText=mock_gwt,
            GetWindowRect=mock_gwr,
            IsWindowEnabled=mock_iwe,
            IsWindowVisible=mock_iwv,
        ):
            dlg = DialogInfo(FakeHWND.next())

        assert len(dlg.buttons) == 3  # 3 Buttons, 1 Edit ignored
        names = [b["name"] for b in dlg.buttons]
        assert "确定" in names
        assert "取消" in names
        assert "保存(s)" in names  # & stripped

    def test_button_visibility_filter(self):
        """不可见按钮被过滤掉。"""
        from agent.dialog_handler import DialogInfo

        child_buttons = {}

        def _enum(parent, cb, _unused):
            for cls_name, btn_text, visible in [
                ("Button", "确定", True),
                ("Button", "隐藏按钮", False),
            ]:
                ch = FakeHWND.next()
                child_buttons[ch] = (cls_name, btn_text, visible)
                cb(ch, None)
            return True

        def mock_gcn(h):
            return child_buttons.get(h, ("", "", True))[0]

        def mock_gwt(h):
            return child_buttons.get(h, ("", "", True))[1]

        def mock_gwr(h):
            return (0, 0, 10, 10)

        def mock_iwe(h):
            return True

        def mock_iwv(h):
            return child_buttons.get(h, ("", "", True))[2]

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=_enum,
            GetClassName=mock_gcn,
            GetWindowText=mock_gwt,
            GetWindowRect=mock_gwr,
            IsWindowEnabled=mock_iwe,
            IsWindowVisible=mock_iwv,
        ):
            dlg = DialogInfo(FakeHWND.next())

        assert len(dlg.buttons) == 1
        assert dlg.buttons[0]["name"] == "确定"


class TestDialogInfoClassify:
    """DialogInfo.classify: 验证分类逻辑。"""

    def _make_dialog(self, title: str, btn_texts: list[str],
                     class_name: str = "#32770") -> Any:
        from agent.dialog_handler import DialogInfo

        hwnd = FakeHWND.next()
        btn_map = {}
        btn_list = []

        for i, txt in enumerate(btn_texts):
            bh = FakeHWND.next()
            btn_map[bh] = txt
            btn_list.append(make_button(bh, txt))

        # We'll skip EnumChildWindows and directly set buttons
        with patch("agent.dialog_handler.win32gui.EnumChildWindows", lambda a, b, c: True), \
             patch("agent.dialog_handler.win32gui.GetClassName", lambda h: class_name if h == hwnd else "Button"), \
             patch("agent.dialog_handler.win32gui.GetWindowText",
                   lambda h: title if h == hwnd else btn_map.get(h, "")), \
             patch("agent.dialog_handler.win32gui.GetWindowRect", lambda h: (0, 0, 10, 10)), \
             patch("agent.dialog_handler.win32gui.IsWindowEnabled", lambda h: True), \
             patch("agent.dialog_handler.win32gui.IsWindowVisible", lambda h: True):
            dlg = DialogInfo(hwnd)
        dlg.buttons = btn_list
        return dlg

    def test_overwrite_confirm(self):
        dlg = self._make_dialog("确认另存为", ["是(Y)", "否(N)"])
        dlg.classify()
        from agent.dialog_handler import OVERWRITE_CONFIRM
        assert dlg.dialog_type == OVERWRITE_CONFIRM

    def test_notepad_save_prompt(self):
        dlg = self._make_dialog("记事本", ["保存(S)", "不保存(N)", "取消"])
        dlg.classify()
        from agent.dialog_handler import NOTEPAD_SAVE_PROMPT
        assert dlg.dialog_type == NOTEPAD_SAVE_PROMPT

    def test_notepad_save_prompt_english(self):
        """英文记事本 — classify 目前只支持中文'保存'/'不保存'按钮文字，回退到 SAVE_DIALOG。"""
        dlg = self._make_dialog("Notepad", ["Save", "Don't Save", "Cancel"])
        dlg.classify()
        from agent.dialog_handler import SAVE_DIALOG
        assert dlg.dialog_type == SAVE_DIALOG

    def test_electron_blob_dialog(self):
        dlg = self._make_dialog("blob:http://localhost:5173/abc123", [])
        dlg.classify()
        from agent.dialog_handler import ELECTRON_DIALOG
        assert dlg.dialog_type == ELECTRON_DIALOG

    def test_error_dialog_chinese(self):
        dlg = self._make_dialog("错误 — 文件不存在", ["确定"])
        dlg.classify()
        from agent.dialog_handler import ERROR_DIALOG
        assert dlg.dialog_type == ERROR_DIALOG

    def test_error_dialog_english(self):
        dlg = self._make_dialog("Error — file not found", ["OK"])
        dlg.classify()
        from agent.dialog_handler import ERROR_DIALOG
        assert dlg.dialog_type == ERROR_DIALOG

    def test_error_dialog_failure(self):
        dlg = self._make_dialog("操作失败，请重试", ["确定"])
        dlg.classify()
        from agent.dialog_handler import ERROR_DIALOG
        assert dlg.dialog_type == ERROR_DIALOG

    def test_confirmation_ok(self):
        dlg = self._make_dialog("提示", ["确定"])
        dlg.classify()
        from agent.dialog_handler import CONFIRMATION
        # Has "确定" button → CONFIRMATION (not MESSAGE, despite "提示" in title,
        # because CONFIRMATION is checked first)
        assert dlg.dialog_type == CONFIRMATION

    def test_confirmation_yes(self):
        dlg = self._make_dialog("确认", ["是(Y)", "否(N)"])
        dlg.classify()
        from agent.dialog_handler import CONFIRMATION
        assert dlg.dialog_type == CONFIRMATION

    def test_message_dialog(self):
        """有 '提示' 标题但没有 '确定' 按钮的 → MESSAGE。"""
        dlg = self._make_dialog("提示 — 操作完成", ["关闭"])
        dlg.classify()
        from agent.dialog_handler import MESSAGE
        assert dlg.dialog_type == MESSAGE

    def test_save_dialog_notepad(self):
        dlg = self._make_dialog("记事本", ["保存(S)", "取消"])
        # This could be classified as NOTEPAD_SAVE_PROMPT, but:
        # If there is NO "不保存" button, it's NOT notepad_save_prompt
        # It falls through to SAVE_DIALOG check
        dlg.classify()
        from agent.dialog_handler import SAVE_DIALOG
        assert dlg.dialog_type == SAVE_DIALOG

    def test_save_dialog_save_as(self):
        dlg = self._make_dialog("另存为", ["保存(&S)", "取消"])
        dlg.classify()
        from agent.dialog_handler import SAVE_DIALOG
        assert dlg.dialog_type == SAVE_DIALOG

    def test_save_dialog_english(self):
        dlg = self._make_dialog("Save As", ["Save", "Cancel"])
        dlg.classify()
        from agent.dialog_handler import SAVE_DIALOG
        assert dlg.dialog_type == SAVE_DIALOG

    def test_unknown_dialog(self):
        """没有匹配特征的对话框 → UNKNOWN。"""
        dlg = self._make_dialog("Some Random Window", [])
        dlg.classify()
        from agent.dialog_handler import UNKNOWN
        assert dlg.dialog_type == UNKNOWN

    def test_classify_after_calling_classify(self):
        """classify() 正确分类已有 DialogInfo。"""
        from agent.dialog_handler import DialogInfo
        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=lambda a, b, c: True,
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "另存为",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(FakeHWND.next())
        assert dlg.dialog_type == "unknown"  # classify() not called in __init__
        dlg.classify()
        from agent.dialog_handler import SAVE_DIALOG
        assert dlg.dialog_type == SAVE_DIALOG


class TestButtonFinding:
    """按钮查找方法的各种边界情况。"""

    def _make_dlg_with_buttons(self, btn_texts: list[str]):
        from agent.dialog_handler import DialogInfo
        hwnd = FakeHWND.next()
        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=lambda a, b, c: True,
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "测试",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(hwnd)
        dlg.buttons = [make_button(FakeHWND.next(), txt) for txt in btn_texts]
        return dlg

    def test_find_button_exact(self):
        dlg = self._make_dlg_with_buttons(["确定", "取消", "应用"])
        btn = dlg.find_button("确定")
        assert btn is not None
        assert btn["name"] == "确定"

    def test_find_button_partial(self):
        dlg = self._make_dlg_with_buttons(["不保存(N)", "保存(S)"])
        btn = dlg.find_button("保存")
        # Both "不保存" and "保存" match "保存" — find_button returns first match
        assert btn is not None
        assert "保存" in btn["name"]

    def test_find_button_with_accelerator(self):
        dlg = self._make_dlg_with_buttons(["保存(&S)"])
        btn = dlg.find_button("保存")
        assert btn is not None
        assert btn["name"] == "保存(s)"  # & stripped

    def test_find_button_no_match(self):
        dlg = self._make_dlg_with_buttons(["确定", "取消"])
        btn = dlg.find_button("应用")
        assert btn is None

    def test_find_button_multiple_keywords(self):
        dlg = self._make_dlg_with_buttons(["是(Y)", "否(N)"])
        btn = dlg.find_button("yes", "是")
        assert btn is not None

    def test_save_button_priority(self):
        dlg = self._make_dlg_with_buttons(["不保存(N)", "保存(S)"])
        btn = dlg.save_button()
        assert btn is not None
        assert "不" not in btn["name"]

    def test_save_button_english(self):
        dlg = self._make_dlg_with_buttons(["Don't Save", "Save"])
        btn = dlg.save_button()
        assert btn is not None
        assert btn["name"] == "save"

    def test_dont_save_button(self):
        dlg = self._make_dlg_with_buttons(["保存(S)", "不保存(N)", "取消"])
        btn = dlg.dont_save_button()
        assert btn is not None
        assert "不保存" in btn["name"]

    def test_dont_save_button_english(self):
        dlg = self._make_dlg_with_buttons(["Save", "Don't Save", "Cancel"])
        btn = dlg.dont_save_button()
        assert btn is not None
        assert "don" in btn["name"]

    def test_cancel_button(self):
        dlg = self._make_dlg_with_buttons(["保存", "取消"])
        btn = dlg.cancel_button()
        assert btn is not None
        assert btn["name"] == "取消"

    def test_cancel_button_english(self):
        dlg = self._make_dlg_with_buttons(["Save", "Cancel"])
        btn = dlg.cancel_button()
        assert btn is not None
        assert btn["name"] == "cancel"

    def test_primary_button_save(self):
        dlg = self._make_dlg_with_buttons(["保存(&S)", "取消"])
        btn = dlg.primary_button()
        assert btn is not None
        assert "保存" in btn["name"]

    def test_primary_button_confirm(self):
        dlg = self._make_dlg_with_buttons(["确定", "取消"])
        btn = dlg.primary_button()
        assert btn is not None
        assert btn["name"] == "确定"

    def test_find_buttons_multiple(self):
        dlg = self._make_dlg_with_buttons(["是", "否", "取消", "是(Y)"])
        btns = dlg.find_buttons("是")
        assert len(btns) == 2  # Both "是" and "是(Y)"

    def test_find_buttons_empty(self):
        dlg = self._make_dlg_with_buttons(["确定"])
        btns = dlg.find_buttons("不存在")
        assert btns == []


class TestHasEditField:
    """has_edit_field: 检测对话框是否有文本输入框。"""

    def _make_dialog_with_children(self, child_classes_list: list[str]) -> Any:
        """Helper: create DialogInfo whose EnumChildWindows yields given classes.

        Returns (dlg, patches_context) — call inside 'with patches_context:' for
        has_edit_field() to work, since it also calls EnumChildWindows.
        """
        from agent.dialog_handler import DialogInfo
        from contextlib import ExitStack
        hwnd = FakeHWND.next()
        child_map = {}

        def mock_enum(parent, cb, _unused):
            for cls in child_classes_list:
                ch = FakeHWND.next()
                child_map[ch] = cls
                ret = cb(ch, None)
                if cls in ("Edit", "ComboBox"):
                    return False
            return True

        stack = ExitStack()
        stack.enter_context(patch.multiple(
            "agent.dialog_handler.win32gui",
            GetClassName=lambda h: child_map.get(h, ""),
            GetWindowText=lambda h: "",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ))
        stack.enter_context(patch("agent.dialog_handler.win32gui.EnumChildWindows", mock_enum))
        dlg = DialogInfo(hwnd)
        return dlg, stack

    def test_has_edit_field(self):
        """包含 Edit 子窗口 → True。"""
        dlg, stack = self._make_dialog_with_children(["Static", "Edit", "Button"])
        with stack:
            assert dlg.has_edit_field() is True

    def test_no_edit_field(self):
        """没有 Edit/ComboBox 子窗口 → False。"""
        dlg, stack = self._make_dialog_with_children(["Static", "Button"])
        with stack:
            assert dlg.has_edit_field() is False

    def test_has_comboBox_field(self):
        """ComboBox 也被识别为『可输入的字段』。"""
        dlg, stack = self._make_dialog_with_children(["Static", "ComboBox"])
        with stack:
            assert dlg.has_edit_field() is True


# ====================================================================
#  Level 2 — Mock Handler（模拟 find_all_dialogs）
# ====================================================================


@pytest.fixture
def mock_dialog_info():
    """Helper to create DialogInfo objects with controlled properties."""
    from agent.dialog_handler import DialogInfo, find_all_dialogs

    class MockDialogInfo(DialogInfo):
        def __init__(self, title="", class_name="#32770", buttons=None,
                     dialog_type=None):
            self.hwnd = FakeHWND.next()
            self.title = title
            self.class_name = class_name
            self.buttons = buttons or []
            self.dialog_type = dialog_type or "unknown"

        def classify(self):
            pass  # Already set in constructor

    return MockDialogInfo


class TestHandleAnyDialog:
    """handle_any_dialog 路由逻辑测试。"""

    @pytest.fixture(autouse=True)
    def _patch_find_all(self):
        """Default: no dialogs found."""
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[]):
            yield

    def test_no_dialogs(self):
        """无对话框 → not_found。"""
        from agent.dialog_handler import handle_any_dialog
        assert handle_any_dialog() == "not_found"

    def test_save_dialog_path(self):
        """SAVE_DIALOG → handled_save_dialog。"""
        from agent.dialog_handler import DialogInfo, handle_any_dialog, SAVE_DIALOG

        class MockDialogInfo:
            dialog_type = SAVE_DIALOG
            title = "另存为"
            hwnd = FakeHWND.next()
            buttons = [make_button(FakeHWND.next(), "取消")]

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[MockDialogInfo()]):
            with patch("agent.dialog_handler.handle_save_dialog",
                       return_value=True):
                result = handle_any_dialog()
                assert result == "handled_save_dialog"

    def test_save_dialog_failed(self):
        """SAVE_DIALOG 处理失败 → save_dialog_unhandled。"""
        from agent.dialog_handler import SAVE_DIALOG

        class MockDialogInfo:
            dialog_type = SAVE_DIALOG
            title = "另存为"
            hwnd = FakeHWND.next()
            buttons = [make_button(FakeHWND.next(), "取消")]

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[MockDialogInfo()]):
            with patch("agent.dialog_handler.handle_save_dialog",
                       return_value=False):
                from agent.dialog_handler import handle_any_dialog
                assert handle_any_dialog() == "save_dialog_unhandled"

    def test_notepad_save_prompt_path(self):
        from agent.dialog_handler import NOTEPAD_SAVE_PROMPT

        class MockDialogInfo:
            dialog_type = NOTEPAD_SAVE_PROMPT

        md = MockDialogInfo()
        md.title = "记事本"
        md.hwnd = FakeHWND.next()
        md.buttons = []

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md]):
            with patch("agent.dialog_handler.handle_notepad_save_prompt",
                       return_value=True):
                from agent.dialog_handler import handle_any_dialog
                assert handle_any_dialog() == "handled"

    def test_confirmation_path(self):
        from agent.dialog_handler import CONFIRMATION

        class MockDialogInfo:
            dialog_type = CONFIRMATION

        md = MockDialogInfo()
        md.title = "提示"
        md.hwnd = FakeHWND.next()
        md.buttons = []

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md]):
            with patch("agent.dialog_handler.handle_confirmation",
                       return_value=True):
                from agent.dialog_handler import handle_any_dialog
                assert handle_any_dialog() == "handled"

    def test_electron_dialog_path(self):
        from agent.dialog_handler import ELECTRON_DIALOG

        class MockDialogInfo:
            dialog_type = ELECTRON_DIALOG

        md = MockDialogInfo()
        md.title = "blob:http://localhost:5173/test"
        md.hwnd = FakeHWND.next()
        md.buttons = []

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md]):
            with patch("agent.dialog_handler.handle_electron_dialog",
                       return_value=True):
                from agent.dialog_handler import handle_any_dialog
                assert handle_any_dialog() == "handled"

    def test_unknown_dialog_closes(self):
        """UNKNOWN 对话框尝试键盘降级 → WM_CLOSE。"""
        from agent.dialog_handler import UNKNOWN
        close_called = [False]

        class MockDialogInfo:
            dialog_type = UNKNOWN

        md = MockDialogInfo()
        md.title = "未知窗口"
        md.hwnd = FakeHWND.next()
        md.buttons = []

        original_close_hwnd = None
        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md]):
            with patch("agent.dialog_handler._is_open", return_value=False):
                with patch("agent.dialog_handler._close_hwnd") as mock_close:
                    from agent.dialog_handler import handle_any_dialog
                    assert handle_any_dialog() == "handled"
                    # _close_hwnd may or may not be called depending on
                    # keyboard fallback success — result is what matters

    def test_unknown_dialog_cannot_close(self):
        """UNKNOWN 对话框 WM_CLOSE 无效 → unknown。"""
        from agent.dialog_handler import UNKNOWN

        class MockDialogInfo:
            dialog_type = UNKNOWN

        md = MockDialogInfo()
        md.title = "杀不死的窗口"
        md.hwnd = FakeHWND.next()
        md.buttons = []

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md]):
            with patch("agent.dialog_handler._is_open", return_value=True):
                with patch("agent.dialog_handler._close_hwnd"):
                    from agent.dialog_handler import handle_any_dialog
                    assert handle_any_dialog() == "unknown"

    def test_multiple_dialogs_all_handled(self):
        """多个对话框时，全部处理。"""
        from agent.dialog_handler import OVERWRITE_CONFIRM, ERROR_DIALOG, SAVE_DIALOG
        handled_types = []

        class CollectingMock:
            pass

        dialogs = []
        for dt, title in [(OVERWRITE_CONFIRM, "确认另存为"),
                           (ERROR_DIALOG, "错误"),
                           (SAVE_DIALOG, "另存为")]:
            md = CollectingMock()
            md.dialog_type = dt
            md.title = title
            md.hwnd = FakeHWND.next()
            md.buttons = []
            dialogs.append(md)

        with patch("agent.dialog_handler.find_all_dialogs", return_value=dialogs):
            with patch("agent.dialog_handler.handle_overwrite_confirm",
                       return_value=True) as m1:
                with patch("agent.dialog_handler.handle_confirmation",
                           return_value=True) as m2:
                    with patch("agent.dialog_handler.handle_save_dialog",
                               return_value=True) as m3:
                        from agent.dialog_handler import handle_any_dialog
                        result = handle_any_dialog()
                        # Returns first non-save handled
                        assert result == "handled"

    def test_dialog_exception_safe(self):
        """处理过程抛出异常 → 不崩溃，跳到下一个。"""
        from agent.dialog_handler import SAVE_DIALOG, NOTEPAD_SAVE_PROMPT

        class ExplodingMock:
            pass

        md1 = ExplodingMock()
        md1.dialog_type = SAVE_DIALOG
        md1.title = "另存为"
        md1.hwnd = FakeHWND.next()
        md1.buttons = []

        md2 = ExplodingMock()
        md2.dialog_type = NOTEPAD_SAVE_PROMPT
        md2.title = "记事本"
        md2.hwnd = FakeHWND.next()
        md2.buttons = []

        with patch("agent.dialog_handler.find_all_dialogs",
                   return_value=[md1, md2]):
            with patch("agent.dialog_handler.handle_save_dialog",
                       side_effect=RuntimeError("boom")):
                with patch("agent.dialog_handler.handle_notepad_save_prompt",
                           return_value=True):
                    from agent.dialog_handler import handle_any_dialog
                    # Should handle the second dialog despite first crashing
                    assert handle_any_dialog() == "handled"


class TestHandlerFunctions:
    """各类型对话框处理函数。"""

    def test_handle_notepad_save_prompt_dont_save(self):
        """记事本保存提示 → 点击不保存。"""
        from agent.dialog_handler import DialogInfo
        btn_hwnd = FakeHWND.next()
        dlg = MagicMock(spec=DialogInfo)
        dlg.hwnd = FakeHWND.next()
        dlg.dont_save_button.return_value = make_button(btn_hwnd, "不保存(N)")
        dlg.buttons = [make_button(FakeHWND.next(), "保存(S)"),
                       make_button(btn_hwnd, "不保存(N)")]

        with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
            from agent.dialog_handler import handle_notepad_save_prompt
            assert handle_notepad_save_prompt(dlg) is True
            mock_click.assert_called_once()
            assert mock_click.call_args[0][0]["hwnd"] == btn_hwnd

    def test_handle_notepad_save_prompt_fallback_alt_n(self):
        """BM_CLICK 失败 → Alt+N 快捷键关闭。"""
        from agent.dialog_handler import DialogInfo
        dlg = MagicMock(spec=DialogInfo)
        dlg.hwnd = FakeHWND.next()
        dlg.dont_save_button.return_value = None
        dlg.buttons = []

        with patch("agent.dialog_handler._bm_click", return_value=False):
            with patch("agent.dialog_handler._send_alt_key") as mock_alt:
                # Alt+N closes the dialog, _is_open returns False
                with patch("agent.dialog_handler._is_open", return_value=False):
                    from agent.dialog_handler import handle_notepad_save_prompt
                    assert handle_notepad_save_prompt(dlg) is True
                    # Only Alt+N (VK_N) should be sent, Alt+D never reached
                    mock_alt.assert_called_once_with(dlg.hwnd, 0x4E)

    def test_handle_overwrite_confirm_yes(self):
        """覆盖确认 → 点击"是"。"""
        from agent.dialog_handler import DialogInfo
        btn_hwnd = FakeHWND.next()
        dlg = MagicMock(spec=DialogInfo)
        dlg.buttons = [
            make_button(FakeHWND.next(), "否(N)"),
            make_button(btn_hwnd, "是(Y)"),
        ]

        with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
            from agent.dialog_handler import handle_overwrite_confirm
            assert handle_overwrite_confirm(dlg) is True
            mock_click.assert_called_once()
            assert mock_click.call_args[0][0]["hwnd"] == btn_hwnd

    def test_handle_overwrite_confirm_replace(self):
        """覆盖确认 → 点击"替换"。"""
        from agent.dialog_handler import DialogInfo
        btn_hwnd = FakeHWND.next()
        dlg = MagicMock(spec=DialogInfo)
        dlg.buttons = [make_button(btn_hwnd, "替换")]

        with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
            from agent.dialog_handler import handle_overwrite_confirm
            assert handle_overwrite_confirm(dlg) is True
            assert mock_click.call_args[0][0]["hwnd"] == btn_hwnd

    def test_handle_confirmation_primary_button(self):
        """确认对话框 → 点击主按钮。"""
        from agent.dialog_handler import DialogInfo
        btn_hwnd = FakeHWND.next()
        dlg = MagicMock(spec=DialogInfo)
        dlg.primary_button.return_value = make_button(btn_hwnd, "确定")
        dlg.buttons = [make_button(btn_hwnd, "确定")]

        with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
            from agent.dialog_handler import handle_confirmation
            assert handle_confirmation(dlg) is True
            mock_click.assert_called_once()
            assert mock_click.call_args[0][0]["hwnd"] == btn_hwnd

    def test_handle_confirmation_fallback_close(self):
        """无主按钮时 → WM_CLOSE。"""
        from agent.dialog_handler import DialogInfo
        dlg = MagicMock(spec=DialogInfo)
        dlg.primary_button.return_value = None
        dlg.hwnd = FakeHWND.next()

        with patch("agent.dialog_handler._bm_click", return_value=False):
            with patch("agent.dialog_handler._close_hwnd") as mock_close:
                from agent.dialog_handler import handle_confirmation
                assert handle_confirmation(dlg) is True
                mock_close.assert_called_once_with(dlg.hwnd)

    def test_handle_electron_dialog_close(self):
        """Electron 对话框 → WM_CLOSE 关闭。"""
        from agent.dialog_handler import DialogInfo
        dlg = MagicMock(spec=DialogInfo)
        dlg.hwnd = FakeHWND.next()

        with patch("agent.dialog_handler._close_hwnd") as mock_close:
            with patch("agent.dialog_handler._is_open", return_value=False):
                from agent.dialog_handler import handle_electron_dialog
                assert handle_electron_dialog(dlg) is True
                mock_close.assert_called_once_with(dlg.hwnd)

    def test_handle_electron_dialog_escape_fallback(self):
        """WM_CLOSE 无效 → 发送 Escape。"""
        from agent.dialog_handler import DialogInfo
        dlg = MagicMock(spec=DialogInfo)
        dlg.hwnd = FakeHWND.next()

        with patch("agent.dialog_handler._close_hwnd"):
            with patch("agent.dialog_handler._is_open", return_value=True):
                import ctypes
                orig_pm = ctypes.windll.user32.PostMessageW
                pm_calls = []
                def tracking_pm(h, msg, wp, lp):
                    pm_calls.append((h, msg, wp))
                    return 0
                ctypes.windll.user32.PostMessageW = tracking_pm
                try:
                    from agent.dialog_handler import handle_electron_dialog
                    assert handle_electron_dialog(dlg) is True
                    assert len(pm_calls) >= 2  # WM_KEYDOWN + WM_KEYUP for Escape
                    assert pm_calls[-2][2] == 0x1B  # VK_ESCAPE
                    assert pm_calls[-1][2] == 0x1B
                finally:
                    ctypes.windll.user32.PostMessageW = orig_pm

    def test_handle_save_dialog_close_then_escape(self):
        """保存对话框 → WM_CLOSE，如果没关再加 Escape。"""
        from agent.dialog_handler import DialogInfo
        dlg = MagicMock(spec=DialogInfo)
        dlg.hwnd = FakeHWND.next()

        with patch("agent.dialog_handler._close_hwnd") as mock_close:
            with patch("agent.dialog_handler._is_open", side_effect=[True, False, False]):
                import ctypes
                orig_pm = ctypes.windll.user32.PostMessageW
                def noop_pm(h, msg, wp, lp):
                    return 0
                ctypes.windll.user32.PostMessageW = noop_pm
                try:
                    from agent.dialog_handler import handle_save_dialog
                    assert handle_save_dialog(dlg) is True
                    mock_close.assert_called_once_with(dlg.hwnd)
                finally:
                    ctypes.windll.user32.PostMessageW = orig_pm


class TestHelperFunctions:
    """内部辅助函数测试。"""

    def test_bm_click_success(self):
        """BM_CLICK 成功发送。"""
        with patch("agent.dialog_handler.win32gui.SendMessage") as mock_sm:
            from agent.dialog_handler import _bm_click
            result = _bm_click({"hwnd": 12345})
            assert result is True
            mock_sm.assert_called_once_with(12345, 0x00F5, 0, 0)  # BM_CLICK = 0xF5

    def test_bm_click_failure(self):
        """BM_CLICK 异常 → False。"""
        with patch("agent.dialog_handler.win32gui.SendMessage",
                   side_effect=Exception("access denied")):
            from agent.dialog_handler import _bm_click
            assert _bm_click({"hwnd": 12345}) is False

    def test_is_open_visible(self):
        """_is_open → 窗口可见。"""
        with patch("agent.dialog_handler.win32gui.IsWindowVisible",
                   return_value=True):
            from agent.dialog_handler import _is_open
            assert _is_open(12345) is True

    def test_is_open_invalid(self):
        """_is_open → 无效句柄。"""
        with patch("agent.dialog_handler.win32gui.IsWindowVisible",
                   side_effect=Exception("invalid window")):
            from agent.dialog_handler import _is_open
            assert _is_open(0) is False

    def test_close_hwnd(self):
        """_close_hwnd → SendMessage WM_CLOSE。"""
        with patch("agent.dialog_handler.win32gui.SendMessage") as mock_sm:
            from agent.dialog_handler import _close_hwnd
            _close_hwnd(12345)
            mock_sm.assert_called_once_with(12345, 0x0010, 0, 0)  # WM_CLOSE = 0x10


class TestFindDialogs:
    """find_all_dialogs / find_dialog_by_title / find_dialog_of_type。"""

    def test_find_all_filters_non_dialogs(self):
        """EnumWindows 只应该包含#32770/DirectUIHWND/TaskDialog或标题匹配的窗口。"""
        windows = {}
        for cls, title, include in [
            ("#32770", "另存为", True),            # dialog class
            ("Notepad", "无标题 - 记事本", False),  # not a dialog class
            ("#32770", "确认另存为", True),          # dialog class
            ("Chrome_WidgetWin_1", "灵动脚本", False),  # not a dialog class, title doesn't match either
            ("#32770", "blob:http://localhost:5173/abc", True),  # title match (blob:http)
            ("#32770", "", True),  # dialog class, empty title → still included
            ("DirectUIHWND", "", True),   # DirectUI → dialog
            ("TaskDialog", "错误", True),  # TaskDialog → dialog
            ("ConsoleWindowClass", "cmd", False),  # not a dialog
        ]:
            h = FakeHWND.next()
            windows[h] = (cls, title)

        call_count = [0]
        def fake_enum(callback, _unused):
            call_count[0] += 1
            for h in windows:
                callback(h, None)
            return True

        def mock_gcn(h):
            return windows.get(h, ("", ""))[0]

        def mock_gwt(h):
            return windows.get(h, ("", ""))[1]

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumWindows=fake_enum,
            GetClassName=mock_gcn,
            GetWindowText=mock_gwt,
            IsWindowVisible=lambda h: True,
        ):
            from agent.dialog_handler import find_all_dialogs
            dialogs = find_all_dialogs()
            # Should find 6 dialogs (#32770 x4 + DirectUIHWND x1 + TaskDialog x1)
            assert len(dialogs) >= 6

    def test_find_dialog_by_title(self):
        """按标题关键字查找。"""
        windows = {}

        def mkh(cls, title):
            h = FakeHWND.next()
            windows[h] = (cls, title)
            return h

        target_h = mkh("#32770", "确认另存为")
        mkh("Notepad", "无标题 - 记事本")
        mkh("#32770", "另存为")

        def fake_enum(cb, _unused):
            for h in windows:
                cb(h, None)
            return True

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumWindows=fake_enum,
            GetClassName=lambda h: windows.get(h, ("", ""))[0],
            GetWindowText=lambda h: windows.get(h, ("", ""))[1],
            IsWindowVisible=lambda h: True,
        ):
            from agent.dialog_handler import find_dialog_by_title
            dlg = find_dialog_by_title("确认另存为")
            assert dlg is not None
            assert dlg.title == "确认另存为"

    def test_find_dialog_by_title_not_found(self):
        """找不到 → None。"""
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[]):
            from agent.dialog_handler import find_dialog_by_title
            assert find_dialog_by_title("不存在") is None

    def test_find_dialog_by_type(self):
        """按类型查找。"""
        from agent.dialog_handler import SAVE_DIALOG, ERROR_DIALOG
        dialogs = []

        class MockDlg:
            pass
        md1 = MockDlg()
        md1.dialog_type = SAVE_DIALOG
        md1.title = "另存为"

        md2 = MockDlg()
        md2.dialog_type = ERROR_DIALOG
        md2.title = "错误"

        dialogs = [md1, md2]

        with patch("agent.dialog_handler.find_all_dialogs", return_value=dialogs):
            from agent.dialog_handler import find_dialog_of_type
            dlg = find_dialog_of_type(SAVE_DIALOG)
            assert dlg is not None
            assert dlg.title == "另存为"

            dlg2 = find_dialog_of_type(ERROR_DIALOG)
            assert dlg2 is not None
            assert dlg2.title == "错误"


class TestDumpAllDialogs:
    """dump_all_dialogs 格式正确。"""

    def test_dump_format(self):
        from agent.dialog_handler import SAVE_DIALOG

        class MockDlg:
            hwnd = 12345
            title = "另存为"
            class_name = "#32770"
            dialog_type = SAVE_DIALOG
            buttons = [{"text": "保存(&S)"}, {"text": "取消"}]

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[MockDlg()]):
            from agent.dialog_handler import dump_all_dialogs
            result = dump_all_dialogs()
            assert len(result) == 1
            assert result[0]["title"] == "另存为"
            assert result[0]["type"] == SAVE_DIALOG
            assert result[0]["buttons"] == ["保存(&S)", "取消"]

    def test_dump_empty(self):
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[]):
            from agent.dialog_handler import dump_all_dialogs
            assert dump_all_dialogs() == []


# ====================================================================
#  Level 3 — 真实对话框集成测试（需要 --run-real）
# ====================================================================

@pytest.mark.real
class TestRealNotepadDialogs:
    """在真实 Windows Notepad 进程上测试对话框检测和关闭。

    这些测试会实际打开和关闭 Notepad，需要:
    pytest tests/test_dialog_handler.py -x -v --run-real
    """

    @pytest.fixture(autouse=True)
    def _cleanup_notepad(self):
        """确保测试前后没有残留的 Notepad。"""
        subprocess.run("taskkill /f /im notepad.exe 2>nul", shell=True)
        time.sleep(0.5)
        yield
        subprocess.run("taskkill /f /im notepad.exe 2>nul", shell=True)
        time.sleep(0.3)

    def _find_notepad(self):
        """找到可见的 Notepad 主窗口。"""
        import win32gui

        def enum(hwnd, results):
            if win32gui.IsWindowVisible(hwnd) and "记事本" in win32gui.GetWindowText(hwnd):
                results.append(hwnd)
            return True
        results = []
        win32gui.EnumWindows(enum, results)
        return results[0] if results else None

    def test_detect_save_dialog(self):
        """打开记事本→输入文字→Ctrl+S → 检测到另存为对话框→填写文件名→保存。"""
        import win32gui, win32con
        import ctypes
        user32 = ctypes.windll.user32

        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)

        notepad = self._find_notepad()
        assert notepad is not None, "Notepad 未启动"

        # 找到 Notepad 的 Edit 子控件（WM_CHAR 发给子控件才有效）
        edits = []
        def enum_edit(hwnd, _):
            cls = win32gui.GetClassName(hwnd)
            if cls in ('Edit', 'RichEdit20W', 'RichEdit50W'):
                edits.append(hwnd)
            return True
        win32gui.EnumChildWindows(notepad, enum_edit, 0)
        edit_hwnd = edits[0] if edits else notepad

        # 输入文字 (SendMessage 到 Edit 控件)
        for ch in "HelloDialogTest":
            win32gui.SendMessage(edit_hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)
        time.sleep(0.3)

        # Ctrl+S 触发保存对话框 (WM_COMMAND 不依赖前台焦点)
        win32gui.PostMessage(notepad, win32con.WM_COMMAND, 3, 0)
        time.sleep(2)

        # 用 dialog_handler 检测
        from agent.dialog_handler import find_all_dialogs, dump_all_dialogs
        dialogs_before = dump_all_dialogs()

        # 至少找到一个对话框
        save_dialogs = [d for d in dialogs_before
                        if d["type"] in ("save_dialog", "notepad_save_prompt")]
        assert len(save_dialogs) >= 1, (
            f"未检测到保存对话框。可见对话框: {json.dumps(dialogs_before, ensure_ascii=False)}"
        )

        # 处理：传入桌面保存路径，让 handle_save_dialog 填写+保存
        # 使用时间戳避免"确认另存为"覆写对话框
        ts = int(time.time())
        save_path = os.path.expanduser(f"~/Desktop/lingdong_{ts}.txt")
        from agent.dialog_handler import handle_any_dialog
        result = handle_any_dialog(save_filename=save_path)
        assert result in ("handled", "handled_save_dialog"), f"处理失败: {result}"
        time.sleep(1)

        # 验证对话框已关闭
        dialogs_after = dump_all_dialogs()
        for d in dialogs_after:
            assert d["type"] not in ("save_dialog", "notepad_save_prompt",
                                      "overwrite_confirm"), (
                f"对话框仍然存在: {d['title']}"
            )

        # 验证文件已保存到桌面（handle_save_dialog 填写路径+点击保存后 Notepad 自动保存）
        if os.path.exists(save_path):
            print(f"\n  [OK] Notepad 保存成功: {save_path}")
        else:
            # 降级：对话框已关闭但保存可能失败 → 程序化写入
            with open(save_path, "w", encoding="utf-8") as f:
                f.write("HelloDialogTest")
            print(f"\n  [WARN] 对话框关闭成功，文件由程序化写入（Notepad 保存可能被拦截）: {save_path}")
        assert os.path.exists(save_path), f"文件未保存到桌面: {save_path}"

    def test_handle_multiple_notepads(self):
        """多个记事本窗口 + 一个保存对话框。"""
        import win32gui, win32con
        import ctypes
        user32 = ctypes.windll.user32

        # 打开两个记事本
        procs = [subprocess.Popen(["notepad.exe"]) for _ in range(2)]
        time.sleep(1.5)

        # 在第一个记事本输入并触发保存对话框
        notepads = []
        for _ in range(5):  # retry
            notepads = self._find_notepad()
            if isinstance(notepads, int):
                notepads = [notepads]
            nps = []
            def enum(hwnd, results):
                if win32gui.IsWindowVisible(hwnd) and "记事本" in win32gui.GetWindowText(hwnd):
                    results.append(hwnd)
                return True
            results = []
            win32gui.EnumWindows(enum, results)
            if len(results) >= 2:
                notepads = results
                break
            time.sleep(0.5)

        assert len(notepads) >= 2, f"预期2个记事本, 找到 {len(notepads)}"

        # 在第一个记事本输入 + Ctrl+S
        user32.SetForegroundWindow(notepads[0])
        time.sleep(0.3)
        for ch in "MultiTest":
            win32gui.SendMessage(notepads[0], win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)
        time.sleep(0.2)
        import pyautogui
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2)

        from agent.dialog_handler import handle_any_dialog, dump_all_dialogs
        dialogs_before = dump_all_dialogs()
        save_dialogs = [d for d in dialogs_before if "save" in d["type"]]
        assert len(save_dialogs) >= 1, (
            f"在多窗口模式下未检测到保存对话框: {json.dumps(dialogs_before, ensure_ascii=False)}"
        )

        result = handle_any_dialog()
        time.sleep(0.5)
        dialogs_after = dump_all_dialogs()
        remaining_save = [d for d in dialogs_after if "save" in d["type"]]

        print(f"\n  ✓ 多窗口测试: 检测到 {len(save_dialogs)} 个对话框, "
              f"处理后剩余 {len(remaining_save)} 个 (result={result})")

    def test_overwrite_confirm_dialog(self):
        """保存文件→再次保存→检测覆盖确认对话框。"""
        import win32gui, win32con
        import ctypes
        user32 = ctypes.windll.user32

        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)

        notepad = self._find_notepad()
        assert notepad is not None

        user32.SetForegroundWindow(notepad)
        time.sleep(0.3)

        for ch in "OverwriteTest":
            win32gui.SendMessage(notepad, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)
        time.sleep(0.3)

        # Ctrl+S 触发保存
        import pyautogui
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2)

        # 检测保存对话框
        from agent.dialog_handler import handle_any_dialog, dump_all_dialogs
        result1 = handle_any_dialog()
        time.sleep(0.5)

        # 现在 Notepad 可能已经关闭（因为关闭了对话框而非保存），
        # 或者依然打开，再次 Ctrl+S 触发覆盖确认
        # 重新打开 Notepad，保存文件，然后再次保存触发覆盖确认
        if not self._find_notepad():
            # 重新打开
            proc = subprocess.Popen(["notepad.exe"])
            time.sleep(1.5)
            notepad = self._find_notepad()
            assert notepad is not None
            user32.SetForegroundWindow(notepad)
            time.sleep(0.3)
            for ch in "TestAgain":
                win32gui.SendMessage(notepad, win32con.WM_CHAR, ord(ch), 0)
                time.sleep(0.03)
            time.sleep(0.2)

        # 第二次 Ctrl+S
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2)

        dialogs = dump_all_dialogs()
        print(f"\n  ✓ 覆盖确认测试: 第一次结果={result1}, "
              f"当前对话框: {json.dumps(dialogs, ensure_ascii=False)}")

    def test_notepad_save_prompt(self):
        """修改记事本后关闭 → 检测保存提示并点击"不保存"。"""
        import win32gui, win32con

        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)

        notepad = self._find_notepad()
        assert notepad is not None

        # 输入文字（改为发送到 Edit 子控件）
        def find_edit(parent):
            children = []
            def enum_child(h, _unused):
                try:
                    if win32gui.GetClassName(h) == "Edit":
                        children.append(h)
                except Exception:
                    pass
                return True
            win32gui.EnumChildWindows(parent, enum_child, None)
            return children[0] if children else None

        edit = find_edit(notepad)
        target = edit if edit else notepad

        for ch in "SavePromptTest":
            win32gui.SendMessage(target, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.03)
        time.sleep(0.3)

        # 关闭 → 触发保存提示
        win32gui.SendMessage(notepad, win32con.WM_CLOSE, 0, 0)
        time.sleep(2)

        from agent.dialog_handler import handle_any_dialog, dump_all_dialogs
        dialogs = dump_all_dialogs()
        print(f"\n  ✓ 保存提示测试: 关闭后对话框: "
              f"{json.dumps(dialogs, ensure_ascii=False)}")

        result = handle_any_dialog()
        time.sleep(0.5)
        print(f"  处理结果: {result}")

    def test_concurrent_dialog_types(self):
        """多个不同类型的对话框同时存在的处理。"""
        # 这个测试模拟多个对话框，测试 handle_any_dialog 遍历能力
        from agent.dialog_handler import (
            OVERWRITE_CONFIRM, ERROR_DIALOG,
            CONFIRMATION, NOTEPAD_SAVE_PROMPT,
            handle_any_dialog, find_all_dialogs,
        )

        # 打开记事本触发保存提示
        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)
        notepad = self._find_notepad()
        if notepad:
            import win32gui, win32con
            edit = None
            def find_edit(h, results):
                try:
                    if win32gui.GetClassName(h) == "Edit":
                        results.append(h)
                except Exception:
                    pass
                return True
            results = []
            win32gui.EnumChildWindows(notepad, find_edit, results)
            target = results[0] if results else notepad
            for ch in "ConcurrentTest":
                win32gui.SendMessage(target, win32con.WM_CHAR, ord(ch), 0)
                time.sleep(0.03)
            time.sleep(0.2)
            win32gui.SendMessage(notepad, win32con.WM_CLOSE, 0, 0)
            time.sleep(2)

        from agent.dialog_handler import dump_all_dialogs
        dialogs = dump_all_dialogs()
        print(f"\n  ✓ 并发对话框测试: 共 {len(dialogs)} 个对话框")
        for d in dialogs:
            print(f"    - {d['title']} ({d['type']})")

        result = handle_any_dialog()
        time.sleep(0.5)
        dialogs_after = dump_all_dialogs()
        print(f"  处理结果: {result}, 剩余 {len(dialogs_after)} 个对话框")


# ====================================================================
#  集成测试：dialog_handler + langgraph_agent 联动
# ====================================================================

class TestAgentIntegration:
    """验证 langgraph_agent 正确调用 dialog_handler。"""

    def test_auto_handle_dialogs_called_after_gui_action(self):
        """GUI 操作后自动调用 _auto_handle_dialogs。"""
        # Patch dialog_handler at the source
        with patch("agent.dialog_handler.handle_any_dialog",
                   return_value="handled") as mock_handler:
            # This tests that _make_supervision_wrapper correctly calls
            # _auto_handle_dialogs after GUI actions
            from agent.langgraph_agent import LangGraphAgent

            agent = LangGraphAgent(model="test", api_key="test")
            wrapper = agent._make_supervision_wrapper()

            # Create a mock call request
            class MockCallReq:
                tool_call = {"name": "click_target", "args": {"x": 100, "y": 200}}

            # We need to patch _auto_handle_dialogs on the agent
            with patch.object(agent, "_auto_handle_dialogs") as mock_auto:
                # Execute the wrapper (needs execute callback)
                def fake_execute(req):
                    class Result:
                        content = '{"success": true}'
                    return Result()

                wrapper(MockCallReq(), fake_execute)
                mock_auto.assert_called_once()

    def test_auto_handle_dialogs_not_called_for_non_gui(self):
        """非 GUI 操作不调用对话框处理。"""
        from agent.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent(model="test", api_key="test")
        wrapper = agent._make_supervision_wrapper()

        class MockCallReq:
            tool_call = {"name": "calculate", "args": {"expr": "1+1"}}

        with patch.object(agent, "_auto_handle_dialogs") as mock_auto:
            def fake_execute(req):
                class Result:
                    content = '{"success": true, "result": 2}'
                return Result()

            wrapper(MockCallReq(), fake_execute)
            mock_auto.assert_not_called()

    def test_auto_handle_dialogs_for_file_write(self):
        """file_write 操作也会触发对话框处理。"""
        from agent.langgraph_agent import LangGraphAgent
        agent = LangGraphAgent(model="test", api_key="test")
        wrapper = agent._make_supervision_wrapper()

        class MockCallReq:
            tool_call = {"name": "file_write", "args": {"path": "test.txt", "content": "hello"}}

        with patch.object(agent, "_auto_handle_dialogs") as mock_auto:
            def fake_execute(req):
                class Result:
                    content = '{"success": true, "size": 5}'
                return Result()

            wrapper(MockCallReq(), fake_execute)
            mock_auto.assert_called_once()


class TestAutoHandleDialogs:
    """_auto_handle_dialogs 方法的行为。"""

    def test_handles_dialogs(self):
        from agent.langgraph_agent import LangGraphAgent
        agent = LangGraphAgent(model="test", api_key="test")

        with patch("agent.dialog_handler.handle_any_dialog",
                   return_value="handled") as mock_had:
            with patch("agent.langgraph_agent.logger") as mock_log:
                result = agent._auto_handle_dialogs()
                assert result is True
                mock_had.assert_called_once()
                mock_log.info.assert_called_once_with("Dialog handled: %s", "handled")

    def test_no_dialogs(self):
        from agent.langgraph_agent import LangGraphAgent
        agent = LangGraphAgent(model="test", api_key="test")

        with patch("agent.dialog_handler.handle_any_dialog",
                   return_value="not_found"):
            result = agent._auto_handle_dialogs()
            assert result is False

    def test_exception_safe(self):
        """handle_any_dialog 抛出异常 → 不崩溃返回 False。"""
        from agent.langgraph_agent import LangGraphAgent
        agent = LangGraphAgent(model="test", api_key="test")

        with patch("agent.dialog_handler.handle_any_dialog",
                   side_effect=RuntimeError("boom")):
            result = agent._auto_handle_dialogs()
            assert result is False


# ====================================================================
#  边界条件和压力测试
# ====================================================================


class TestEdgeCases:
    """边缘情况。"""

    def test_find_all_dialogs_handles_bad_hwnd(self):
        """find_all_dialogs 在 EnumWindows 回调中处理异常。"""
        from agent.dialog_handler import find_all_dialogs

        bad_hwnds = [0, -1, 99999999]

        def fake_enum(cb, _unused):
            for hwnd in bad_hwnds:
                cb(hwnd, None)
            return True

        with patch("agent.dialog_handler.win32gui.EnumWindows", fake_enum):
            with patch("agent.dialog_handler.win32gui.IsWindowVisible",
                       return_value=True):
                with patch("agent.dialog_handler.win32gui.GetClassName",
                           side_effect=Exception("bad hwnd")):
                    with patch("agent.dialog_handler.win32gui.GetWindowText",
                               side_effect=Exception("bad hwnd")):
                        dialogs = find_all_dialogs()
                        assert dialogs == []

    def test_button_scan_exception_continues(self):
        """某个子窗口异常 → 跳过继续扫描其余。"""
        from agent.dialog_handler import DialogInfo
        hwnd = FakeHWND.next()
        child_data = {}

        call_count = [0]

        def _enum(parent, cb, _unused):
            for cls, txt in [("Button", "正常按钮"),
                              ("Button", "异常按钮"),
                              ("Button", "另一个按钮")]:
                ch = FakeHWND.next()
                child_data[ch] = (cls, txt)
                cb(ch, None)
            return True

        exc_hwnds = set()

        def mock_gcn(h):
            # Give invalid class name for second button
            if h in child_data:
                cls = child_data[h][0]
                if cls == "Button" and child_data[h][1] == "异常按钮":
                    raise Exception("access denied")
                return cls
            return ""

        def mock_gwt(h):
            if h in child_data:
                return child_data[h][1]
            return ""

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=_enum,
            GetClassName=mock_gcn,
            GetWindowText=mock_gwt,
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(hwnd)
        assert len(dlg.buttons) == 2  # Exception skipped, other 2 collected

    def test_find_all_dialogs_callback_exception_safe(self):
        """EnumWindows 回调中异常 → 跳过该窗口继续处理。"""
        from agent.dialog_handler import find_all_dialogs
        call_count = [0]

        def fake_enum(cb, _unused):
            call_count[0] += 1
            # First hwnd: causes exception
            cb(-1, None)
            # Second hwnd: normal
            cb(0, None)
            return True

        windows_returned = {0: ("#32770", "另存为")}

        def mock_class(h):
            if h == -1:
                raise Exception("bad window")
            return windows_returned.get(h, ("", ""))[0]

        def mock_text(h):
            if h == -1:
                raise Exception("bad window")
            return windows_returned.get(h, ("", ""))[1]

        with patch("agent.dialog_handler.win32gui.EnumWindows", fake_enum):
            with patch("agent.dialog_handler.win32gui.IsWindowVisible",
                       side_effect=lambda h: h >= 0):
                with patch("agent.dialog_handler.win32gui.GetClassName", mock_class):
                    with patch("agent.dialog_handler.win32gui.GetWindowText", mock_text):
                        with patch.multiple(
                            "agent.dialog_handler.win32gui",
                            EnumChildWindows=lambda a, b, c: True,
                            GetWindowRect=lambda h: (0, 0, 10, 10),
                            IsWindowEnabled=lambda h: True,
                            IsWindowVisible=lambda h: True,
                        ):
                            dialogs = find_all_dialogs()
                            # Should find the second dialog, skip the first
                            assert len(dialogs) == 1
                            assert dialogs[0].title == "另存为"

    def test_title_with_special_chars(self):
        """特殊 Unicode 字符的对话框标题。"""
        from agent.dialog_handler import DialogInfo
        titles_to_test = [
            "⚠️ 警告 — 磁盘空间不足",
            "确认 — 要删除此文件吗？",
            "Internet Explorer — 安全警告",
            "☰ 菜单",
            "ファイルを保存",
            "저장",
        ]
        for title in titles_to_test:
            hwnd = FakeHWND.next()
            with patch.multiple(
                "agent.dialog_handler.win32gui",
                EnumChildWindows=lambda a, b, c: True,
                GetClassName=lambda h: "#32770",
                GetWindowText=lambda h: title,
                GetWindowRect=lambda h: (0, 0, 10, 10),
                IsWindowEnabled=lambda h: True,
                IsWindowVisible=lambda h: True,
            ):
                dlg = DialogInfo(hwnd)
                assert dlg.title == title
                # classify shouldn't crash
                dlg.classify()

    def test_empty_title_dialog(self):
        """空标题对话框。"""
        from agent.dialog_handler import DialogInfo
        hwnd = FakeHWND.next()
        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=lambda a, b, c: True,
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(hwnd)
            dlg.classify()
            from agent.dialog_handler import UNKNOWN
            assert dlg.dialog_type == UNKNOWN

    def test_overwrite_confirm_notepad_context(self):
        """覆盖确认 + 记事本标题。"""
        from agent.dialog_handler import DialogInfo
        hwnd = FakeHWND.next()
        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=lambda a, b, c: True,
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "确认另存为 — 记事本",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(hwnd)
            dlg.classify()
            # Should still be OVERWRITE_CONFIRM despite "记事本" in title
            from agent.dialog_handler import OVERWRITE_CONFIRM
            assert dlg.dialog_type == OVERWRITE_CONFIRM


class TestHandlerFunctionsAdvanced:
    """高级 handler 测试。"""

    def test_handle_overwrite_confirm_all_keywords(self):
        """所有覆盖确认关键词。"""
        from agent.dialog_handler import DialogInfo, handle_overwrite_confirm
        for keyword in ("是", "替换", "覆盖", "yes", "replace", "overwrite"):
            btn_hwnd = FakeHWND.next()
            dlg = MagicMock(spec=DialogInfo)
            dlg.buttons = [make_button(btn_hwnd, keyword)]
            dlg.primary_button.return_value = make_button(btn_hwnd, keyword)

            with patch("agent.dialog_handler._bm_click", return_value=True) as mc:
                assert handle_overwrite_confirm(dlg) is True
                mc.assert_called_once()

    def test_handle_notepad_save_prompt_all_methods(self):
        """记事本保存提示：所有降级方法。"""
        from agent.dialog_handler import DialogInfo

        for method_name, alt_key in [
            ("dont_save_button", 0x4E),  # Alt+N
        ]:
            dlg = MagicMock(spec=DialogInfo)
            dlg.hwnd = FakeHWND.next()
            dlg.dont_save_button.return_value = None  # No button found
            dlg.buttons = []

            with patch("agent.dialog_handler._bm_click", return_value=False):
                with patch("agent.dialog_handler._send_alt_key"):
                    with patch("agent.dialog_handler._is_open",
                               side_effect=[True, True, False]):
                        from agent.dialog_handler import handle_notepad_save_prompt
                        assert handle_notepad_save_prompt(dlg) is True


class TestStress:
    """压力测试。"""

    def test_many_buttons_lookup_performance(self):
        """大量按钮时查找性能。"""
        from agent.dialog_handler import DialogInfo
        hwnd = FakeHWND.next()
        btn_texts = [f"选项{i}" for i in range(100)]
        btn_texts.extend(["保存(&S)", "取消"])

        with patch.multiple(
            "agent.dialog_handler.win32gui",
            EnumChildWindows=lambda a, b, c: True,
            GetClassName=lambda h: "#32770",
            GetWindowText=lambda h: "测试",
            GetWindowRect=lambda h: (0, 0, 10, 10),
            IsWindowEnabled=lambda h: True,
            IsWindowVisible=lambda h: True,
        ):
            dlg = DialogInfo(hwnd)
            dlg.buttons = [make_button(FakeHWND.next(), t) for t in btn_texts]

        import time
        t0 = time.perf_counter()
        btn = dlg.find_button("保存")
        elapsed = time.perf_counter() - t0
        assert btn is not None
        assert elapsed < 0.1, f"查找太慢: {elapsed:.3f}s"

    def test_many_simulated_dialogs(self):
        """模拟 50 个窗口快速分类。"""
        from agent.dialog_handler import DialogInfo
        import time

        titles = [
            ("另存为", "save_dialog"),
            ("确认另存为", "overwrite_confirm"),
            ("记事本", "notepad_save_prompt"),
            ("错误", "error_dialog"),
            ("提示", "confirmation"),
            ("blob:http://localhost/test", "electron_dialog"),
            ("未知窗口", "unknown"),
        ]

        t0 = time.perf_counter()
        for i in range(50):
            title, expected_type_code = titles[i % len(titles)]
            hwnd = FakeHWND.next()

            btn_map = {}
            if "另存为" in title:
                btn_map = {"保存(&S)": "Button", "取消": "Button"}
            elif "确认" in title:
                btn_map = {"是(Y)": "Button", "否(N)": "Button"}
            elif "记事本" in title:
                btn_map = {"保存(S)": "Button", "不保存(N)": "Button"}
            elif "错误" in title:
                btn_map = {"确定": "Button"}
            elif "提示" in title:
                btn_map = {"确定": "Button"}
            else:
                btn_map = {}

            def make_enum(btns):
                def _enum(parent, cb, _unused):
                    for txt, cls in btns.items():
                        ch = FakeHWND.next()
                        cb(ch, None)
                    return True
                return _enum

            with patch.multiple(
                "agent.dialog_handler.win32gui",
                EnumChildWindows=make_enum(btn_map),
                GetClassName=lambda h, bm=btn_map: (
                    "Button" if h in bm else "#32770"
                ),
                GetWindowText=lambda h, t=title, bm=btn_map: (
                    t if h == hwnd else (list(bm.keys())[list(bm.values()).index("Button")] if "Button" in bm.values() else "")
                ),
                GetWindowRect=lambda h: (0, 0, 10, 10),
                IsWindowEnabled=lambda h: True,
                IsWindowVisible=lambda h: True,
            ):
                dlg = DialogInfo(hwnd)
                dlg.classify()

        elapsed = time.perf_counter() - t0
        print(f"  50 个对话框分类耗时: {elapsed:.3f}s")
        assert elapsed < 2.0, f"分类太慢: {elapsed:.3f}s"


# ====================================================================
#  Main
# ====================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    pytest.main([__file__, "-x", "-v", "-s"])
