"""
dialog_features @feature 注册表测试。

测试三层架构：
  Level 1 — 注册表元数据: @feature 装饰器正确注册 feature 名称/参数/分类
  Level 2 — 调用路由: handle_dialog / dialog_list / dialog_take_action 正确派发
  Level 3 — 集成: 各 action 选项按预期工作

运行:
  pytest tests/test_dialog_features.py -x -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))


# ====================================================================
#  Level 1 — 注册表元数据
# ====================================================================


class TestRegistryMetadata:
    """@feature 装饰器正确注册 dialog 相关功能。"""

    @pytest.fixture(autouse=True)
    def _ensure_features_loaded(self):
        import features._all  # noqa: F401 — triggers lazy load
        from features.registry import registry
        self.registry = registry

    def test_handle_dialog_registered(self):
        """handle_dialog feature 已注册。"""
        spec = self.registry.get("handle_dialog")
        assert spec is not None, "handle_dialog feature not found in registry"
        assert spec.display_name == "处理对话框"

    def test_dialog_list_registered(self):
        """dialog_list feature 已注册。"""
        spec = self.registry.get("dialog_list")
        assert spec is not None
        assert spec.display_name == "列出对话框"

    def test_dialog_take_action_registered(self):
        """dialog_take_action feature 已注册。"""
        spec = self.registry.get("dialog_take_action")
        assert spec is not None
        assert spec.display_name == "对话框操作"

    def test_handle_dialog_params(self):
        """handle_dialog 有 timeout 参数。"""
        spec = self.registry.get("handle_dialog")
        param_names = {p.name for p in spec.params}
        assert "timeout" in param_names

    def test_dialog_take_action_params(self):
        """dialog_take_action 有 action 和 timeout 参数。"""
        spec = self.registry.get("dialog_take_action")
        param_names = {p.name for p in spec.params}
        assert "action" in param_names
        assert "timeout" in param_names

    def test_dialog_list_no_params(self):
        """dialog_list 没有入参。"""
        spec = self.registry.get("dialog_list")
        assert len(spec.params) == 0

    def test_all_three_executable(self):
        """三个 feature 都可以通过 registry.execute 调用（不抛出 import 异常）。"""
        for name in ("handle_dialog", "dialog_list", "dialog_take_action"):
            spec = self.registry.get(name)
            assert spec is not None, f"{name} not registered"
            assert spec.handler is not None, f"{name} has no handler"


# ====================================================================
#  Level 2 — 调用路由
# ====================================================================


class TestHandleDialogRouting:
    """handle_dialog 调用正确路由到 agent.dialog_handler."""

    def test_no_dialogs_returns_not_found(self):
        """无对话框 → 'not_found'。"""
        with patch("agent.dialog_handler.handle_any_dialog", return_value="not_found"):
            from features.dialog_features import handle_dialog
            assert handle_dialog() == "not_found"

    def test_dialog_handled(self):
        """对话框被处理 → 'handled'。"""
        with patch("agent.dialog_handler.handle_any_dialog", return_value="handled"):
            from features.dialog_features import handle_dialog
            assert handle_dialog() == "handled"

    def test_timeout_polls_until_dialog_appears(self):
        """timeout>0 时轮询直到对话框出现或超时。"""
        call_count = [0]

        def side_effect():
            call_count[0] += 1
            if call_count[0] >= 3:
                return "handled"
            return "not_found"

        with patch("agent.dialog_handler.handle_any_dialog", side_effect=side_effect):
            with patch("time.sleep"):
                from features.dialog_features import handle_dialog
                assert handle_dialog(timeout=3) == "handled"
                assert call_count[0] >= 3

    def test_timeout_expires(self):
        """timeout 到期仍未发现对话框 → 'not_found'。"""
        with patch("agent.dialog_handler.handle_any_dialog", return_value="not_found"):
            with patch("time.sleep"):
                with patch("time.time", side_effect=[0, 10, 20]):
                    from features.dialog_features import handle_dialog
                    assert handle_dialog(timeout=3) == "not_found"

    def test_exception_handled(self):
        """dialog_handler.handle_any_dialog 抛异常 → 返回错误字符串。"""
        with patch("agent.dialog_handler.handle_any_dialog",
                   side_effect=RuntimeError("boom")):
            from features.dialog_features import handle_dialog
            result = handle_dialog()
            assert "error" in result

    def test_registry_execute_returns_dict(self):
        """通过 registry.execute 调用返回 {'success': True, 'result': ...}。"""
        from features.registry import registry
        import features._all  # noqa: F401
        with patch("agent.dialog_handler.handle_any_dialog", return_value="handled"):
            result = registry.execute("handle_dialog")
            assert result.get("success") is True
            assert result.get("result") == "handled"


class TestDialogListRouting:
    """dialog_list 正确返回对话框列表。"""

    def test_empty_list(self):
        """无对话框 → 空列表。"""
        with patch("agent.dialog_handler.dump_all_dialogs", return_value=[]):
            from features.dialog_features import dialog_list
            assert dialog_list() == []

    def test_returns_dialog_data(self):
        """返回正确的对话框数据格式。"""
        mock_dialogs = [
            {"hwnd": 12345, "title": "保存", "type": "save_dialog",
             "buttons": ["保存(&S)", "取消"]},
        ]
        with patch("agent.dialog_handler.dump_all_dialogs", return_value=mock_dialogs):
            from features.dialog_features import dialog_list
            result = dialog_list()
            assert len(result) == 1
            assert result[0]["title"] == "保存"
            assert result[0]["type"] == "save_dialog"

class TestDialogTakeActionRouting:
    """dialog_take_action 各种 action 选项。"""

    def test_no_dialogs_returns_false(self):
        """无对话框 → False。"""
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[]):
            from features.dialog_features import dialog_take_action
            assert dialog_take_action("close") is False

    def test_action_close(self):
        """action=close → _close_hwnd 被调用。"""
        mock_dlg = _make_mock_dlg(hwnd=555)
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler._close_hwnd") as mock_close:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("close") is True
                mock_close.assert_called_once_with(555)

    def test_action_click_found(self):
        """action=click:保存 → 查找按钮并 BM_CLICK。"""
        btn = {"hwnd": 999, "text": "保存(&S)"}
        mock_dlg = _make_mock_dlg()
        mock_dlg.find_button.return_value = btn

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("click:保存") is True
                mock_dlg.find_button.assert_called_once_with("保存")
                mock_click.assert_called_once_with(btn)

    def test_action_click_not_found(self):
        """action=click:不存在的按钮 → False。"""
        mock_dlg = _make_mock_dlg()
        mock_dlg.find_button.return_value = None

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            from features.dialog_features import dialog_take_action
            assert dialog_take_action("click:不存在") is False

    def test_action_save(self):
        """action=save → save_button() + BM_CLICK。"""
        btn = {"hwnd": 100, "text": "保存(&S)"}
        mock_dlg = _make_mock_dlg()
        mock_dlg.save_button.return_value = btn

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("save") is True
                mock_click.assert_called_once_with(btn)

    def test_action_save_no_button(self):
        """action=save 但找不到保存按钮 → False。"""
        mock_dlg = _make_mock_dlg()
        mock_dlg.save_button.return_value = None

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            from features.dialog_features import dialog_take_action
            assert dialog_take_action("save") is False

    def test_action_dont_save(self):
        """action=dont_save → handle_notepad_save_prompt。"""
        mock_dlg = _make_mock_dlg()
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler.handle_notepad_save_prompt",
                       return_value=True) as mock_handler:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("dont_save") is True
                mock_handler.assert_called_once_with(mock_dlg)

    def test_action_cancel(self):
        """action=cancel → cancel_button() + BM_CLICK。"""
        btn = {"hwnd": 200, "text": "取消"}
        mock_dlg = _make_mock_dlg()
        mock_dlg.cancel_button.return_value = btn

        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler._bm_click", return_value=True) as mock_click:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("cancel") is True
                mock_click.assert_called_once_with(btn)

    def test_action_overwrite_yes(self):
        """action=overwrite_yes → handle_overwrite_confirm。"""
        mock_dlg = _make_mock_dlg()
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler.handle_overwrite_confirm",
                       return_value=True) as mock_handler:
                from features.dialog_features import dialog_take_action
                assert dialog_take_action("overwrite_yes") is True
                mock_handler.assert_called_once_with(mock_dlg)

    def test_unknown_action(self):
        """未知 action → False。"""
        mock_dlg = _make_mock_dlg()
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            from features.dialog_features import dialog_take_action
            assert dialog_take_action("nonexistent") is False

    def test_timeout_waits_for_dialog(self):
        """timeout>0 时等待对话框出现。"""
        call_count = [0]
        mock_dlg = _make_mock_dlg()

        def find_side_effect():
            call_count[0] += 1
            if call_count[0] >= 2:
                return [mock_dlg]
            return []

        with patch("agent.dialog_handler.find_all_dialogs",
                   side_effect=find_side_effect):
            with patch("agent.dialog_handler._close_hwnd"):
                with patch("time.sleep"):
                    from features.dialog_features import dialog_take_action
                    assert dialog_take_action("close", timeout=3) is True
                    assert call_count[0] >= 2

    def test_exception_safe(self):
        """处理过程抛异常 → False 不崩溃。"""
        with patch("agent.dialog_handler.find_all_dialogs",
                   side_effect=RuntimeError("boom")):
            from features.dialog_features import dialog_take_action
            assert dialog_take_action("close") is False


# ====================================================================
#  Level 3 — 集成
# ====================================================================


class TestRegistryIntegration:
    """通过 registry.execute 完整集成测试。"""

    @pytest.fixture(autouse=True)
    def _load_features(self):
        import features._all  # noqa: F401

    def test_registry_execute_handle_dialog(self):
        """registry.execute('handle_dialog') → {'success': True, 'result': ...}。"""
        from features.registry import registry
        with patch("agent.dialog_handler.handle_any_dialog", return_value="handled"):
            result = registry.execute("handle_dialog")
            assert result["success"] is True
            assert result["result"] == "handled"

    def test_registry_execute_handle_dialog_with_timeout(self):
        """registry.execute('handle_dialog', timeout=2) 传递参数。"""
        from features.registry import registry
        with patch("agent.dialog_handler.handle_any_dialog", return_value="handled"):
            result = registry.execute("handle_dialog", timeout=2)
            assert result["success"] is True

    def test_registry_execute_dialog_list(self):
        """registry.execute('dialog_list') → 返回列表。"""
        from features.registry import registry
        with patch("agent.dialog_handler.dump_all_dialogs", return_value=[]):
            result = registry.execute("dialog_list")
            assert result["success"] is True
            assert result["result"] == []

    def test_registry_execute_dialog_take_action(self):
        """registry.execute('dialog_take_action', action='close')。"""
        from features.registry import registry
        mock_dlg = _make_mock_dlg(hwnd=123)
        with patch("agent.dialog_handler.find_all_dialogs", return_value=[mock_dlg]):
            with patch("agent.dialog_handler._close_hwnd"):
                result = registry.execute("dialog_take_action", action="close")
                assert result["success"] is True


class TestExecutorIntegration:
    """DSLExecutor 中 dialog 相关逻辑。"""

    def test_executor_has_dialog_daemon(self):
        """DSLExecutor 初始化自带 dialog daemon。"""
        from engine.executor import DSLExecutor
        executor = DSLExecutor()
        assert hasattr(executor, "_dialog_auto")
        assert executor._dialog_auto is True
        assert hasattr(executor, "_daemon_thread")
        executor.stop()

    def test_set_dialog_auto_toggle(self):
        """set_dialog_auto 开/关。"""
        from engine.executor import DSLExecutor
        executor = DSLExecutor()
        assert executor._dialog_auto is True
        executor.set_dialog_auto(False)
        assert executor._dialog_auto is False
        executor.set_dialog_auto(True)
        assert executor._dialog_auto is True
        executor.stop()


# ====================================================================
#  Helpers
# ====================================================================


def _make_mock_dlg(hwnd: int = 99999):
    """创建一个可用的 mock dialog 对象。"""
    from unittest.mock import MagicMock
    from agent.dialog_handler import DialogInfo
    dlg = MagicMock(spec=DialogInfo)
    dlg.hwnd = hwnd
    dlg.buttons = []
    dlg.find_button.return_value = None
    dlg.save_button.return_value = None
    dlg.cancel_button.return_value = None
    dlg.dont_save_button.return_value = None
    dlg.primary_button.return_value = None
    return dlg
