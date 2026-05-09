"""
Core interface tests — ToolResult, Observation, StepRecord, TaskState, EventBus, StructuredLogger.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from core.interfaces import ToolResult, Observation, StepRecord, TaskState
from core.events import EventBus, Event, global_bus
from core.logging import StructuredLogger


class TestToolResult:
    """ToolResult creation, factory methods, and bool coercion."""

    def test_ok(self):
        r = ToolResult.ok(data="hello")
        assert r.success is True
        assert r.data == "hello"
        assert r.confidence == 1.0
        assert r.error is None

    def test_fail(self):
        r = ToolResult.fail("something went wrong")
        assert r.success is False
        assert r.error == "something went wrong"
        assert r.confidence == 1.0

    def test_ok_with_confidence(self):
        r = ToolResult.ok(data=42, confidence=0.75)
        assert r.success is True
        assert r.data == 42
        assert r.confidence == 0.75

    def test_fail_with_extra(self):
        r = ToolResult.fail("error", screenshot="/tmp/ss.png")
        assert r.success is False
        assert r.screenshot == "/tmp/ss.png"

    def test_bool_coercion(self):
        assert bool(ToolResult.ok()) is True
        assert bool(ToolResult.fail("nope")) is False

    def test_default_confidence(self):
        r = ToolResult(success=True)
        assert r.confidence == 1.0
        assert r.data is None
        assert r.error is None
        assert r.screenshot is None


class TestObservation:
    """Observation dataclass creation and defaults."""

    def test_empty_observation(self):
        obs = Observation.empty()
        assert obs.timestamp > 0
        assert obs.currentApp == ""
        assert obs.windowTitle == ""
        assert obs.ocrText == []
        assert obs.detectedImages == []
        assert obs.lastActionResult is None
        assert obs.screenshotPath == ""

    def test_observation_with_result(self):
        r = ToolResult.ok(data="done")
        obs = Observation(lastActionResult=r, windowTitle="Test")
        assert obs.windowTitle == "Test"
        assert obs.lastActionResult is not None
        assert obs.lastActionResult.data == "done"

    def test_timestamp_auto_set(self):
        before = time.time()
        obs = Observation()
        after = time.time()
        assert before <= obs.timestamp <= after

    def test_ocr_text_list(self):
        obs = Observation(ocrText=["hello", "world"])
        assert len(obs.ocrText) == 2
        assert "hello" in obs.ocrText


class TestStepRecord:
    """StepRecord creation and defaults."""

    def test_step_record_basic(self):
        r = ToolResult.ok()
        record = StepRecord(step=1, tool="click", params={"x": 100}, result=r)
        assert record.step == 1
        assert record.tool == "click"
        assert record.params == {"x": 100}
        assert record.result.success is True
        assert record.timestamp > 0

    def test_step_record_with_observation(self):
        obs = Observation(windowTitle="Notepad")
        r = ToolResult.ok()
        record = StepRecord(step=2, tool="type", params={"text": "hi"}, result=r, observation=obs)
        assert record.observation.windowTitle == "Notepad"


class TestTaskState:
    """TaskState lifecycle and helpers."""

    def test_default_state(self):
        t = TaskState(goal="test")
        assert t.goal == "test"
        assert t.status == "idle"
        assert t.currentStep == 0
        assert t.retryCount == 0
        assert t.history == []
        assert t.total_steps == 0
        assert t.last_result is None

    def test_add_step(self):
        t = TaskState(goal="test", status="running")
        r = ToolResult.ok()
        record = StepRecord(step=1, tool="click", params={}, result=r)
        t.add_step(record)
        assert t.total_steps == 1
        assert t.currentStep == 1
        assert t.last_result is not None

    def test_last_result_none_when_empty(self):
        t = TaskState(goal="test")
        assert t.last_result is None

    def test_multiple_steps(self):
        t = TaskState(goal="test", status="running")
        for i in range(3):
            r = ToolResult.ok(data=i)
            t.add_step(StepRecord(step=i + 1, tool="click", params={}, result=r))
        assert t.total_steps == 3
        assert t.last_result.data == 2


class TestEventBus:
    """EventBus pub/sub mechanics."""

    def setup_method(self):
        self.bus = EventBus()
        self.received: list[Event] = []

    def _handler(self, event: Event):
        self.received.append(event)

    def test_subscribe_and_emit(self):
        self.bus.subscribe("test:event", self._handler)
        self.bus.emit("test:event", {"key": "value"})
        assert len(self.received) == 1
        assert self.received[0].type == "test:event"
        assert self.received[0].data["key"] == "value"

    def test_unsubscribe(self):
        self.bus.subscribe("test:event", self._handler)
        self.bus.unsubscribe("test:event", self._handler)
        self.bus.emit("test:event", {})
        assert len(self.received) == 0

    def test_no_subscribers_no_error(self):
        self.bus.emit("nonexistent", {})  # should not raise

    def test_multiple_subscribers(self):
        results: list[str] = []

        def a(e):
            results.append("a")

        def b(e):
            results.append("b")

        self.bus.subscribe("test:multi", a)
        self.bus.subscribe("test:multi", b)
        self.bus.emit("test:multi", {})
        assert results == ["a", "b"]

    def test_handler_exception_does_not_bubble(self):
        def broken(e):
            raise RuntimeError("oops")

        self.bus.subscribe("test:broken", broken)
        self.bus.subscribe("test:broken", self._handler)
        self.bus.emit("test:broken", {"ok": True})
        assert len(self.received) == 1  # second handler still runs

    def test_event_type_after_unsubscribe(self):
        """Unsubscribing one handler shouldn't affect other handlers for same type."""
        results: list[str] = []
        def a(e): results.append("a")
        def b(e): results.append("b")
        self.bus.subscribe("test:type", a)
        self.bus.subscribe("test:type", b)
        self.bus.unsubscribe("test:type", a)
        self.bus.emit("test:type", {})
        assert results == ["b"]

    def test_clear_all(self):
        self.bus.subscribe("a", self._handler)
        self.bus.subscribe("b", self._handler)
        self.bus.clear()
        assert self.bus.subscriber_count == 0

    def test_emit_event_object(self):
        self.bus.subscribe("direct", self._handler)
        ev = Event(type="direct", data={"from": "event"})
        self.bus.emit_event(ev)
        assert len(self.received) == 1
        assert self.received[0].data["from"] == "event"

    def test_global_bus_is_singleton(self):
        assert global_bus is not None
        assert isinstance(global_bus, EventBus)


class TestEvent:
    """Event dataclass."""

    def test_event_defaults(self):
        e = Event(type="test")
        assert e.type == "test"
        assert e.data == {}
        assert e.timestamp > 0

    def test_event_with_data(self):
        e = Event(type="test", data={"n": 1})
        assert e.data["n"] == 1

    def test_timestamp_auto(self):
        before = time.time()
        e = Event("auto")
        after = time.time()
        assert before <= e.timestamp <= after


class TestStructuredLogger:
    """StructuredLogger recording, query, export, and replay."""

    def setup_method(self):
        self.logger = StructuredLogger(max_history=10)

    def test_log_step(self):
        r = ToolResult.ok()
        record = StepRecord(step=1, tool="click", params={"x": 1}, result=r)
        self.logger.log_step(record)
        history = self.logger.get_history()
        assert len(history) == 1
        assert history[0].step == 1

    def test_log_event(self):
        event = Event(type="test:log", data={"msg": "hello"})
        self.logger.log_event(event)
        events = self.logger.get_events()
        assert len(events) == 1
        assert events[0].type == "test:log"

    def test_filter_events_by_type(self):
        self.logger.log_event(Event("a", {"n": 1}))
        self.logger.log_event(Event("b", {"n": 2}))
        self.logger.log_event(Event("a", {"n": 3}))
        a_events = self.logger.get_events("a")
        assert len(a_events) == 2
        b_events = self.logger.get_events("b")
        assert len(b_events) == 1

    def test_get_summary(self):
        self.logger.log_step(StepRecord(1, "click", {}, ToolResult.ok()))
        self.logger.log_step(StepRecord(2, "type", {}, ToolResult.fail("err")))
        summary = self.logger.get_summary()
        assert summary["total_steps"] == 2
        assert summary["successful"] == 1
        assert summary["failed"] == 1
        assert summary["last_step"] == 2

    def test_empty_summary(self):
        summary = self.logger.get_summary()
        assert summary["total_steps"] == 0
        assert summary["last_step"] is None

    def test_max_history_eviction(self):
        logger = StructuredLogger(max_history=3)
        for i in range(5):
            logger.log_step(StepRecord(i, "t", {}, ToolResult.ok()))
        assert len(logger.get_history()) == 3
        assert [s.step for s in logger.get_history()] == [2, 3, 4]

    def test_clear(self):
        self.logger.log_step(StepRecord(1, "t", {}, ToolResult.ok()))
        self.logger.log_event(Event("e"))
        self.logger.clear()
        assert len(self.logger.get_history()) == 0
        assert len(self.logger.get_events()) == 0

    def test_export_replay(self):
        self.logger.log_step(StepRecord(1, "click", {"x": 1}, ToolResult.ok(data="done")))
        data = self.logger.export_replay()
        assert data["version"] == 1
        assert len(data["steps"]) == 1
        assert data["steps"][0]["tool"] == "click"
        assert data["steps"][0]["result"]["success"] is True
        assert "exported_at" in data

    def test_export_json(self):
        self.logger.log_step(StepRecord(1, "click", {}, ToolResult.ok()))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            self.logger.export_json(path)
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["version"] == 1
            assert len(loaded["steps"]) == 1
        finally:
            os.unlink(path)

    def test_replay_yields_records(self):
        self.logger.log_step(StepRecord(1, "click", {}, ToolResult.ok()))
        self.logger.log_step(StepRecord(2, "type", {}, ToolResult.ok()))
        data = self.logger.export_replay()
        records = list(StructuredLogger.replay(data, speed=999))  # fast replay
        assert len(records) == 2
        assert records[0].step == 1
        assert records[1].step == 2

    def test_replay_empty(self):
        records = list(StructuredLogger.replay({"steps": [], "events": []}))
        assert records == []

    def test_thread_safety(self):
        """Rough check that concurrent logging doesn't crash."""
        import threading
        errors = []

        def log_some(n):
            try:
                for i in range(100):
                    self.logger.log_step(StepRecord(i, "t", {}, ToolResult.ok()))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=log_some, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(self.logger.get_history()) == 10  # capped by max_history
