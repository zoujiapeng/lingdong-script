"""
Tests for Supervisor on_error strategy — skip/stop/replan behavior.
"""
import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.supervisor import Supervisor


@pytest.fixture
def supervisor():
    stop = threading.Event()
    captured = []
    sup = Supervisor(
        stop_event=stop,
        emit_event=lambda e, d: captured.append((e, d)),
    )
    sup.capture_b64 = lambda *a, **kw: ""
    sup.capture_a11y_summary = lambda *a, **kw: ""
    sup.wait_screen_stable = lambda *a, **kw: None
    sup.auto_handle_dialogs = lambda *a, **kw: False
    sup.ensure_safe_window = lambda *a, **kw: {"success": True}
    sup.execute_with_type_conv = lambda name, args: {"success": False, "error": "mock_failure"}
    return sup, captured


class TestSupervisorOnError:
    def test_on_error_empty_default_retry(self, supervisor):
        """Default (empty on_error) should behave as retry — fail through on failure."""
        sup, _ = supervisor
        sup.on_error = ""
        result = sup.execute("type_text", {"target": "test"})
        assert result.get("success") is False
        assert "error" in result

    def test_on_error_skip(self, supervisor):
        """on_error=skip should return success=True with skipped flag."""
        sup, captured = supervisor
        sup.on_error = "skip"
        result = sup.execute("type_text", {"target": "test"})
        assert result.get("success") is True
        assert result.get("skipped") is True
        assert result.get("original_error") == "mock_failure"
        assert result.get("tool") == "type_text"

    def test_on_error_stop(self, supervisor):
        """on_error=stop should set stop_event and return failure."""
        sup, captured = supervisor
        sup.on_error = "stop"
        assert sup._stop_event.is_set() is False
        result = sup.execute("type_text", {"target": "test"})
        assert result.get("success") is False
        assert result.get("error") == "stopped_by_error_strategy"
        assert sup._stop_event.is_set() is True

    def test_on_error_replan(self, supervisor):
        """on_error=replan should signal replan_needed."""
        sup, captured = supervisor
        sup.on_error = "replan"
        result = sup.execute("type_text", {"target": "test"})
        assert result.get("success") is False
        assert result.get("error") == "replan_needed"
        assert result.get("original_error") == "mock_failure"
        assert result.get("tool") == "type_text"

    def test_skip_logs_warning(self, supervisor):
        """skip strategy should emit a log event."""
        sup, captured = supervisor
        sup.on_error = "skip"
        sup.execute("type_text", {"target": "test"})
        log_events = [d for e, d in captured if e == ("log",)]
        assert any("on_error=skip" in d.get("message", "") for _, d in captured)

    def test_stop_logs_error(self, supervisor):
        """stop strategy should emit an error-level log."""
        sup, captured = supervisor
        sup.on_error = "stop"
        sup.execute("type_text", {"target": "test"})
        assert any("on_error=stop" in d.get("message", "") for _, d in captured)

    def test_successful_execution_not_affected(self, supervisor):
        """No on_error strategy should affect successful executions."""
        sup, captured = supervisor
        sup.execute_with_type_conv = lambda name, args: {"success": True, "result": "ok"}
        for strategy in ["", "skip", "stop", "replan"]:
            sup.on_error = strategy
            result = sup.execute("type_text", {"target": "test"})
            assert result.get("success") is True
            assert result.get("skipped") is not True
