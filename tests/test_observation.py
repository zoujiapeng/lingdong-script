"""
Observation collector tests — with mocked @feature registry.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from unittest.mock import MagicMock, patch

import pytest
from core.interfaces import ToolResult, Observation
from agent.observation import ObservationCollector


@pytest.fixture
def mock_registry():
    """Create a mock feature registry with controllable execute()."""
    reg = MagicMock()
    return reg


@pytest.fixture
def collector(mock_registry):
    """ObservationCollector with mocked registry."""
    col = ObservationCollector()
    col._registry = mock_registry
    return col


class TestObservationCollector:
    """ObservationCollector with mocked @feature registry."""

    def test_collect_basic(self, collector, mock_registry):
        """Basic collect returns an Observation with timestamp."""
        mock_registry.execute.side_effect = [
            # get_active_window
            {"success": True, "result": {"process": "notepad.exe", "title": "Untitled - Notepad"}},
            # ocr_extract_all
            {"success": True, "result": [{"text": "Hello"}, {"text": "World"}]},
        ]

        obs = collector.collect()

        assert obs.timestamp > 0
        assert obs.currentApp == "notepad.exe"
        assert obs.windowTitle == "Untitled - Notepad"
        assert obs.ocrText == ["Hello", "World"]
        assert obs.lastActionResult is None
        assert obs.screenshotPath == ""

    def test_collect_with_last_result(self, collector, mock_registry):
        """Attaches the last tool result to the observation."""
        result = ToolResult.ok(data="click done")
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "explorer.exe", "title": "Test"}},
            {"success": True, "result": []},
        ]

        obs = collector.collect(last_result=result)

        assert obs.lastActionResult is not None
        assert obs.lastActionResult.data == "click done"

    def test_collect_with_screenshot(self, collector, mock_registry):
        """When screenshot=True, calls screenshot feature."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "test", "title": "Test"}},
            {"success": True, "result": []},
            {"success": True, "result": {"path": "C:\\ss.png"}},
        ]

        obs = collector.collect(screenshot=True)

        assert obs.screenshotPath == "C:\\ss.png"
        # Verify screenshot was called with format=jpeg
        screenshot_call = mock_registry.execute.call_args_list[2]
        assert screenshot_call[0][0] == "screenshot"

    def test_collect_with_screenshot_data_field(self, collector, mock_registry):
        """Screenshot result might use 'data' key instead of 'path'."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "t", "title": ""}},
            {"success": True, "result": []},
            {"success": True, "result": {"data": "base64data"}},
        ]

        obs = collector.collect(screenshot=True)
        assert obs.screenshotPath == "base64data"

    def test_collect_skips_screenshot_by_default(self, collector, mock_registry):
        """Default collect() does NOT capture a screenshot."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "test", "title": ""}},
            {"success": True, "result": []},
        ]

        obs = collector.collect()
        assert obs.screenshotPath == ""

    def test_collect_after_action(self, collector, mock_registry):
        """Shorthand that attaches result and skips screenshot."""
        result = ToolResult.ok()
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "calc", "title": "Calculator"}},
            {"success": True, "result": []},
        ]

        obs = collector.collect_after_action(result)

        assert obs.lastActionResult is not None
        assert obs.windowTitle == "Calculator"
        assert obs.screenshotPath == ""  # no screenshot

    def test_collect_with_screenshot_shorthand(self, collector, mock_registry):
        """collect_with_screenshot shorthand."""
        result = ToolResult.ok()
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "test", "title": ""}},
            {"success": True, "result": []},
            {"success": True, "result": {"path": "ss.png"}},
        ]

        obs = collector.collect_with_screenshot(result)
        assert obs.screenshotPath == "ss.png"
        assert obs.lastActionResult is not None

    def test_graceful_degradation_all_fail(self, collector, mock_registry):
        """When all registry calls fail, returns empty observation."""
        mock_registry.execute.side_effect = Exception("all broken")

        obs = collector.collect()

        assert obs.currentApp == ""
        assert obs.windowTitle == ""
        assert obs.ocrText == []

    def test_graceful_degradation_window_fails_ocr_works(self, collector, mock_registry):
        """get_active_window fails, but OCR still works."""
        calls = 0

        def side_effect(name, **kw):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise Exception("window error")
            return {"success": True, "result": [{"text": "OCR text"}]}

        mock_registry.execute.side_effect = side_effect
        obs = collector.collect()

        assert obs.currentApp == ""
        assert "OCR text" in obs.ocrText

    def test_ocr_returns_raw_strings(self, collector, mock_registry):
        """OCR result might include raw strings instead of dicts."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "t", "title": ""}},
            {"success": True, "result": ["Line1", "Line2"]},
        ]

        obs = collector.collect()
        assert "Line1" in obs.ocrText
        assert "Line2" in obs.ocrText

    def test_ocr_fails_gracefully(self, collector, mock_registry):
        """OCR feature fails, observation still has window info."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "win", "title": "Test"}},
            Exception("OCR crashed"),
        ]

        obs = collector.collect()
        assert obs.windowTitle == "Test"
        assert obs.ocrText == []

    def test_ocr_unsuccessful(self, collector, mock_registry):
        """OCR returns success=False."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "t", "title": ""}},
            {"success": False, "error": "ocr busy"},
        ]

        obs = collector.collect()
        assert obs.ocrText == []

    def test_window_non_dict_result(self, collector, mock_registry):
        """get_active_window returns unexpected format."""
        mock_registry.execute.side_effect = [
            {"success": True, "result": "just a string"},
            {"success": True, "result": []},
        ]

        obs = collector.collect()
        assert obs.currentApp == ""
        assert obs.windowTitle == ""

    def test_ocr_limits_to_50(self, collector, mock_registry):
        """OCR text list is capped at 50 entries."""
        many_texts = [{"text": f"line{i}"} for i in range(100)]
        mock_registry.execute.side_effect = [
            {"success": True, "result": {"process": "t", "title": ""}},
            {"success": True, "result": many_texts},
        ]

        obs = collector.collect()
        assert len(obs.ocrText) == 50


class TestDetectChanges:
    """ObservationCollector.detect_changes between two observations."""

    def test_window_change(self, collector):
        before = Observation(windowTitle="A", timestamp=time.time())
        after = Observation(windowTitle="B", timestamp=time.time())
        changes = collector.detect_changes(before, after)
        assert "window_changed" in changes
        assert changes["window_changed"]["from"] == "A"
        assert changes["window_changed"]["to"] == "B"

    def test_no_window_change(self, collector):
        before = Observation(windowTitle="Same", timestamp=time.time())
        after = Observation(windowTitle="Same", timestamp=time.time())
        changes = collector.detect_changes(before, after)
        assert "window_changed" not in changes

    def test_new_text(self, collector):
        before = Observation(ocrText=["a", "b"], timestamp=time.time())
        after = Observation(ocrText=["a", "b", "c"], timestamp=time.time())
        changes = collector.detect_changes(before, after)
        assert "new_text" in changes
        assert "c" in changes["new_text"]

    def test_screenshot_change(self, collector):
        before = Observation(screenshotPath="old.png", timestamp=time.time())
        after = Observation(screenshotPath="new.png", timestamp=time.time())
        changes = collector.detect_changes(before, after)
        assert changes["screenshot_changed"] is True

    def test_no_changes(self, collector):
        before = Observation(windowTitle="W", ocrText=["x"], screenshotPath="s.png", timestamp=time.time())
        after = Observation(windowTitle="W", ocrText=["x"], screenshotPath="s.png", timestamp=time.time())
        changes = collector.detect_changes(before, after)
        assert changes == {}


class TestSummarize:
    """ObservationCollector.summarize() compact text representation."""

    def test_summarize_full(self, collector):
        obs = Observation(
            windowTitle="Notepad",
            currentApp="notepad.exe",
            ocrText=["File", "Edit", "View"],
            screenshotPath="C:\\ss.png",
            timestamp=time.time(),
        )
        summary = collector.summarize(obs)
        assert "Window: Notepad" in summary
        assert "App: notepad.exe" in summary
        assert "Screen text:" in summary
        assert "Screenshot: C:\\ss.png" in summary

    def test_summarize_empty(self, collector):
        obs = Observation.empty()
        summary = collector.summarize(obs)
        assert summary == "(no observation data)"

    def test_summarize_ocr_limited_to_5(self, collector):
        many_texts = [f"line{i}" for i in range(20)]
        obs = Observation(ocrText=many_texts, timestamp=time.time())
        summary = collector.summarize(obs)
        # Only first 5 lines in summary
        assert "line0" in summary
        assert "line5" not in summary  # past the limit


class TestCleanText:
    """ObservationCollector._clean_text helper."""

    def test_clean_dict(self, collector):
        assert collector._clean_text({"text": "hello"}) == "hello"

    def test_clean_dict_no_text_key(self, collector):
        assert collector._clean_text({"value": 42}) == "{'value': 42}"

    def test_clean_string(self, collector):
        assert collector._clean_text("  hello world  ") == "hello world"

    def test_clean_truncates_long(self, collector):
        long_str = "x" * 300
        assert len(collector._clean_text(long_str)) == 200

    def test_clean_int(self, collector):
        assert collector._clean_text(123) == "123"
