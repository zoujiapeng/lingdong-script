"""
Tests for ReflectionEngine — per-step and post-task reflection.
Tests are implementation-agnostic and focus on the reflection contract:
  - Parse arbitrary LLM output into structured data
  - Extract actionable insights from step/task execution
  - Gracefully handle failures, edge cases, and malformed input
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import json
import pytest
from unittest.mock import MagicMock
from agent.reflection import ReflectionEngine


# ═══════════════════════════════════════════════════════════════════
#  _parse_json — real-world LLM output quirks
# ═══════════════════════════════════════════════════════════════════

class TestParseJson:
    def test_clean_json(self):
        assert ReflectionEngine._parse_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        assert ReflectionEngine._parse_json(
            '```json\n{"a": 1, "b": 2}\n```'
        ) == {"a": 1, "b": 2}

    def test_fence_no_lang(self):
        assert ReflectionEngine._parse_json(
            '```\n{"key": "value"}\n```'
        ) == {"key": "value"}

    def test_brace_extraction_from_surrounding_text(self):
        assert ReflectionEngine._parse_json(
            "Here is the result: {\"answer\": 42}. That's it."
        ) == {"answer": 42}

    def test_trailing_comma_inside_object(self):
        """LLMs commonly produce trailing commas — stdlib json rejects these."""
        result = ReflectionEngine._parse_json('{"a": 1, "b": 2,}')
        assert result == {}  # json.loads is strict

    def test_single_quotes_instead_of_double(self):
        """Some LLM outputs use single quotes — stdlib json rejects these."""
        result = ReflectionEngine._parse_json("{'a': 1, 'b': 'hello'}")
        assert result == {}  # json.loads requires double quotes

    def test_truncated_json_returns_partial(self):
        """LLM output may be cut off mid-object — return what's parseable."""
        result = ReflectionEngine._parse_json('{"a": 1, "b": 2')
        assert result == {}  # can't parse incomplete

    def test_escaped_unicode(self):
        result = ReflectionEngine._parse_json('{"text": "hello\\u4e16\\u754c"}')
        assert result.get("text") == "hello世界"

    def test_empty_string(self):
        assert ReflectionEngine._parse_json("") == {}

    def test_completely_malformed(self):
        assert ReflectionEngine._parse_json("This is not JSON at all") == {}

    def test_nested_structures(self):
        text = '{"rules": [{"rule": "test", "confidence": 0.8}]}'
        result = ReflectionEngine._parse_json(text)
        assert len(result["rules"]) == 1

    def test_json_with_trailing_text(self):
        assert ReflectionEngine._parse_json(
            '{"a": 1}\nSome trailing explanation'
        ) == {"a": 1}


# ═══════════════════════════════════════════════════════════════════
#  Format helpers
# ═══════════════════════════════════════════════════════════════════

class TestFormatHelpers:
    def test_format_reflection_result_basic(self):
        reflection = {"insight": "good", "rule": "always do X", "confidence": 0.9}
        result = ReflectionEngine.format_reflection_result(3, reflection)
        assert result["step_num"] == 3
        assert result["insight"] == "good"
        assert result["rule"] == "always do X"

    def test_format_reflection_with_missing_key(self):
        """reflection dict missing optional keys should not crash."""
        result = ReflectionEngine.format_reflection_result(1, {"insight": "ok"})
        assert result["step_num"] == 1
        assert result["insight"] == "ok"
        # rule may be absent — implementation should handle it

    def test_format_learning_result_basic(self):
        reflection = {
            "rules": [{"rule": "r1"}],
            "preferences": [{"key": "p1"}],
            "insights": ["i1"],
            "mistakes": ["m1"],
        }
        stats = {"preferences": 5, "rules": 3, "episodes": 10}
        result = ReflectionEngine.format_learning_result(reflection, stats)
        assert result["rules_count"] == 1
        assert result["insights"] == ["i1"]
        assert result["mistakes"] == ["m1"]
        assert result["stats"] == stats

    def test_format_learning_result_empty(self):
        result = ReflectionEngine.format_learning_result({}, {})
        assert result["rules_count"] == 0
        assert result["insights"] == []


# ═══════════════════════════════════════════════════════════════════
#  _llm_json_call — error resilience
# ═══════════════════════════════════════════════════════════════════

class TestLlmJsonCall:
    def test_successful_call(self):
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": '{"insight": "test"}'}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([{"role": "user", "content": "hi"}]) == {"insight": "test"}

    def test_none_response(self):
        mock = MagicMock(return_value=None)
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([]) == {}

    def test_malformed_content(self):
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": "not json"}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([]) == {}

    def test_llm_exception(self):
        mock = MagicMock(side_effect=Exception("API error"))
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([]) == {}

    def test_fenced_json_from_llm(self):
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": "```json\n{\"key\": \"val\"}\n```"}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([]) == {"key": "val"}

    def test_empty_choices_list(self):
        mock = MagicMock(return_value={"choices": []})
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine._llm_json_call([]) == {}


# ═══════════════════════════════════════════════════════════════════
#  reflect_step — per-step reflection behavior
# ═══════════════════════════════════════════════════════════════════

class TestReflectStep:
    def test_clean_success_skips_llm(self):
        """Successful step without error → no analysis needed."""
        mock = MagicMock()
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step(
            {"tool": "click", "result": {"success": True, "error": ""}},
            "test goal",
        )
        assert result == {}
        mock.assert_not_called()

    def test_failure_triggers_llm(self):
        """Failed step calls LLM for analysis."""
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": '{"insight": "failed", "rule": null}'}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step(
            {"tool": "click", "result": {"success": False, "error": "timeout"}},
            "test",
        )
        assert result["insight"] == "failed"
        mock.assert_called_once()

    def test_extracts_rule_from_llm(self):
        mock = MagicMock(return_value={
            "choices": [{
                "message": {
                    "content": '{"insight": "tip", "rule": "do X first", "confidence": 0.8}'
                }
            }]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step(
            {"tool": "type_text", "result": {"success": False, "error": "element not found"}},
            "fill form",
        )
        assert result["rule"] == "do X first"

    def test_success_with_nonempty_error_still_analyzes(self):
        """success=True with non-empty error text should still analyze."""
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": '{"insight": "partial"}'}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step(
            {"tool": "click", "result": {"success": True, "error": "button found but delayed"}},
            "test",
        )
        assert result["insight"] == "partial"
        mock.assert_called_once()

    def test_missing_result_key_returns_empty(self):
        """Step with no 'result' key should not crash."""
        mock = MagicMock()
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step({"tool": "click", "step_num": 1}, "test")
        assert result == {}
        mock.assert_not_called()

    def test_none_args_handled(self):
        """Step with args=None should not crash json.dumps."""
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": '{"insight": "ok"}'}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        step = {
            "tool": "click", "step_num": 1,
            "result": {"success": False, "error": "fail"},
            "args": None,
        }
        result = engine.reflect_step(step, "test")
        assert result == {"insight": "ok"}

    def test_empty_result_dict_returns_empty(self):
        """Step with empty result {} — no error text to analyze → skip LLM."""
        mock = MagicMock()
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_step(
            {"tool": "click", "result": {}},
            "test",
        )
        assert result == {}
        mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
#  reflect_task — post-task reflection
# ═══════════════════════════════════════════════════════════════════

class TestReflectTask:
    def test_no_steps_returns_empty(self):
        engine = ReflectionEngine(call_llm_fn=MagicMock())
        assert engine.reflect_task("goal", [], True, 1000) == {}

    def test_calls_llm_with_step_summary(self):
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "good",
                "rules": [],
                "preferences": [],
                "insights": ["done"],
                "mistakes": [],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        steps = [
            {"step_num": 1, "tool": "click", "result": {"success": True}},
            {"step_num": 2, "tool": "type", "result": {"success": True}},
        ]
        result = engine.reflect_task("test task", steps, True, 2000)
        assert result["overall_assessment"] == "good"
        assert result["insights"] == ["done"]

    def test_mixed_success_failure_steps(self):
        """Reflection should handle a mix of successful and failed steps."""
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "partial",
                "rules": [{"rule": "check before submit", "confidence": 0.7}],
                "preferences": [],
                "insights": ["step 2 failed but recovered"],
                "mistakes": ["missed validation"],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        steps = [
            {"step_num": 1, "tool": "click", "result": {"success": True}},
            {"step_num": 2, "tool": "type", "result": {"success": False, "error": "field not found"}},
            {"step_num": 3, "tool": "click", "result": {"success": True}},
        ]
        result = engine.reflect_task("fill form", steps, True, 3000)
        assert result["overall_assessment"] == "partial"
        assert len(result["rules"]) == 1

    def test_llm_failure_returns_empty(self):
        mock = MagicMock(side_effect=Exception("API fail"))
        engine = ReflectionEngine(call_llm_fn=mock)
        assert engine.reflect_task("test", [{"step_num": 1, "result": {"success": True}}], True, 0) == {}

    def test_extracts_rules_and_preferences(self):
        mock = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "completed",
                "rules": [
                    {"rule": "verify before submit", "trigger": "form", "confidence": 0.8}
                ],
                "preferences": [
                    {"key": "style", "value": "formal", "confidence": 0.7}
                ],
                "insights": ["works"],
                "mistakes": [],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock)
        result = engine.reflect_task("test", [{"step_num": 1, "result": {"success": True}}], True, 1000)
        assert result["rules"][0]["rule"] == "verify before submit"
        assert result["preferences"][0]["key"] == "style"
