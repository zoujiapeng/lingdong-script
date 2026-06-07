"""
Integration tests — chain AgentConfig → ReflectionEngine → MemoryStore.

These verify that components work together as they would in a real agent run:
  Config resolution → per-step reflection → post-task reflection →
  rule/preference/episode extraction → memory storage → memory injection

No real LLM calls or screen interaction — all LLM responses are mocked.
"""
import os
import sys
import json
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.config import AgentConfig
from agent.reflection import ReflectionEngine
from agent.memory import MemoryStore, Episode
import agent.memory as _mem


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def temp_memory_dir():
    """Use a temp directory for all memory files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("agent.memory.MEMORY_DIR", tmpdir):
            with patch("agent.memory.PREFERENCES_FILE", os.path.join(tmpdir, "preferences.json")):
                with patch("agent.memory.RULES_FILE", os.path.join(tmpdir, "rules.json")):
                    with patch("agent.memory.EPISODES_FILE", os.path.join(tmpdir, "episodes.json")):
                        yield


@pytest.fixture
def store():
    return MemoryStore()


# ═══════════════════════════════════════════════════════════════════
#  1. Config → resolve()  public API
# ═══════════════════════════════════════════════════════════════════

class TestConfigResolution:
    """AgentConfig.resolve() contract for all 4 execution modes."""

    def test_multimodal_produces_expected_values(self):
        cfg = AgentConfig(execution_mode="multimodal")
        r = cfg.resolve()
        assert r["llm_protocol"] == "text"
        assert r["vision_mode"] == "direct"
        assert r["planning"] == "per_step"
        assert r["screen_context"] == "minimal"
        assert r["on_error"] == "retry"
        assert r["multimodal"] is True

    def test_text_mode_produces_expected_values(self):
        cfg = AgentConfig(execution_mode="text")
        r = cfg.resolve()
        assert r["llm_protocol"] == "text"
        assert r["vision_mode"] == "som"
        assert r["planning"] == "per_step"
        assert r["screen_context"] == "full"
        assert r["on_error"] == "retry"
        assert r["multimodal"] is False

    def test_text_subconfig_affects_resolve(self):
        cfg = AgentConfig(execution_mode="text")
        cfg.text_vision_cfg.uia_enabled = True
        cfg.text_vision_cfg.visual_strategy = "ocr_llm"
        r = cfg.resolve()
        assert r["uia_enabled"] is True
        assert r["visual_strategy"] == "ocr_llm"

    def test_flow_chart_resolve(self):
        cfg = AgentConfig(execution_mode="flow_chart")
        r = cfg.resolve()
        assert r["flow_chart_mode"] is True
        assert r["multimodal"] is False


# ═══════════════════════════════════════════════════════════════════
#  2. Config serialization → deserialization → re-resolution
# ═══════════════════════════════════════════════════════════════════

class TestConfigSerializationChain:
    """Config survives IPC roundtrip and re-resolution."""

    def test_roundtrip_then_resolve(self):
        original = AgentConfig(
            execution_mode="text",
            on_error="replan",
        )
        original.text_vision_cfg.visual_strategy = "ocr_llm"
        serialized = original.to_dict()
        deserialized = AgentConfig.from_dict(serialized)
        r = deserialized.resolve()
        assert r["on_error"] == "replan"
        assert r["visual_strategy"] == "ocr_llm"

    def test_partial_config_re_resolve(self):
        """Partial dict → from_dict → resolve should still work."""
        cfg = AgentConfig.from_dict({"execution_mode": "multimodal"})
        r = cfg.resolve()
        assert r["multimodal"] is True
        assert r["vision_mode"] == "direct"


# ═══════════════════════════════════════════════════════════════════
#  3. Reflection → MemoryStore  full pipeline
# ═══════════════════════════════════════════════════════════════════

class TestReflectionToMemory:
    """Post-task reflection output → MemoryStore storage."""

    def test_reflect_task_rules_flow_into_memory(self, store):
        mock_llm = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "completed",
                "rules": [
                    {"rule": "verify before submit", "trigger": "form", "confidence": 0.8}
                ],
                "preferences": [
                    {"key": "writing_style", "value": "formal", "confidence": 0.7}
                ],
                "insights": ["takes time to render"],
                "mistakes": ["clicked too early"],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock_llm)

        steps = [
            {"step_num": 1, "tool": "click", "result": {"success": True}},
            {"step_num": 2, "tool": "type", "result": {"success": False, "error": "field slow"}},
            {"step_num": 3, "tool": "click", "result": {"success": True}},
        ]
        reflection = engine.reflect_task("fill form", steps, True, 5000)

        # Store reflection results (same logic as agent.py post-task)
        for r in reflection.get("rules", []):
            store.add_rule(
                r["rule"],
                trigger_context=r.get("trigger", "general"),
                confidence=r.get("confidence", 0.5),
            )
        for p in reflection.get("preferences", []):
            store.add_preference(
                p["key"], p["value"],
                confidence=p.get("confidence", 0.5),
            )
        store.add_episode(Episode(
            goal="fill form",
            success=True,
            total_steps=len(steps),
            insights=reflection.get("insights", []),
            mistakes=reflection.get("mistakes", []),
        ))

        # Verify stored data
        assert len(store.get_rules()) == 1
        assert store.get_rules()[0].rule_text == "verify before submit"

        assert len(store.get_preferences()) == 1
        assert store.get_preferences()[0].key == "writing_style"

        episodes = store.get_recent_episodes()
        assert len(episodes) == 1
        assert episodes[0].insights == ["takes time to render"]
        assert episodes[0].mistakes == ["clicked too early"]

    def test_reflection_with_multiple_rules_and_preferences(self, store):
        mock_llm = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "done",
                "rules": [
                    {"rule": "use UIA over OCR", "trigger": "window", "confidence": 0.9},
                    {"rule": "wait after click", "trigger": "form", "confidence": 0.7},
                ],
                "preferences": [
                    {"key": "style", "value": "concise", "confidence": 0.8},
                    {"key": "browser", "value": "chrome", "confidence": 0.6},
                ],
                "insights": [],
                "mistakes": [],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock_llm)

        reflection = engine.reflect_task(
            "automate", [{"step_num": 1, "result": {"success": True}}], True, 1000,
        )

        for r in reflection.get("rules", []):
            store.add_rule(r["rule"], trigger_context=r.get("trigger", "general"), confidence=r["confidence"])
        for p in reflection.get("preferences", []):
            store.add_preference(p["key"], p["value"], confidence=p["confidence"])

        assert len(store.get_rules()) == 2
        assert len(store.get_preferences()) == 2

    def test_empty_reflection_does_not_store(self, store):
        """LLM failure produces empty reflection — nothing stored."""
        mock_llm = MagicMock(side_effect=Exception("API fail"))
        engine = ReflectionEngine(call_llm_fn=mock_llm)

        reflection = engine.reflect_task(
            "fail", [{"step_num": 1, "result": {"success": True}}], False, 0,
        )
        assert reflection == {}

        # Nothing stored
        assert store.stats()["rules"] == 0
        assert store.stats()["preferences"] == 0


# ═══════════════════════════════════════════════════════════════════
#  4. Memory injection → context prompt  integration
# ═══════════════════════════════════════════════════════════════════

class TestMemoryInjection:
    """Stored memory produces usable context prompts for LLM."""

    def test_context_prompt_contains_stored_knowledge(self, store):
        store.add_rule("verify before submit", trigger_context="form", confidence=0.8)
        store.add_preference("style", "concise", confidence=0.9)
        store.add_episode(Episode(
            goal="fill login form",
            success=True,
            total_steps=3,
            summary="Filled and submitted login",
            insights=["wait for button to enable"],
        ))

        prompt = store.build_context_prompt("fill form")
        assert "verify before submit" in prompt
        assert "concise" in prompt

    def test_context_prompt_without_relevant_data_is_empty(self, store):
        """If no stored data matches the goal, prompt is empty."""
        store.add_rule("use chrome", trigger_context="browser", confidence=0.9)
        prompt = store.build_context_prompt("open calculator")
        assert prompt == ""

    def test_context_prompt_length_bounded(self, store):
        """Large memory should not produce unbounded prompt."""
        for i in range(50):
            store.add_rule(f"rule_{i}", trigger_context="general", confidence=0.5)
        for i in range(50):
            store.add_preference(f"pref_{i}", f"val_{i}", confidence=0.5)

        prompt = store.build_context_prompt("anything")
        assert len(prompt) < 3000


# ═══════════════════════════════════════════════════════════════════
#  5. End-to-end: Reflection → Memory → Injection  chain
# ═══════════════════════════════════════════════════════════════════

class TestEndToEnd:
    """Full chain: task reflection → store → context injection."""

    def test_run_reflect_store_inject(self, store):
        """Simulate a complete agent learning cycle."""
        # ── Step 1: Task reflection ──
        mock_llm = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "good",
                "rules": [
                    {"rule": "use keyboard shortcuts", "trigger": "navigate", "confidence": 0.8}
                ],
                "preferences": [],
                "insights": ["Ctrl+N is faster"],
                "mistakes": [],
            })}}]
        })
        engine = ReflectionEngine(call_llm_fn=mock_llm)
        reflection = engine.reflect_task(
            "navigate",
            [{"step_num": 1, "result": {"success": True}}],
            True, 2000,
        )

        # ── Step 2: Store ──
        for r in reflection.get("rules", []):
            store.add_rule(r["rule"], trigger_context=r.get("trigger", "general"), confidence=r["confidence"])
        store.add_episode(Episode(
            goal="navigate",
            success=True,
            total_steps=1,
            summary="Ctrl+N is faster — use keyboard shortcuts",
            insights=reflection.get("insights", []),
        ))

        # ── Step 3: Memory injection (for a related task) ──
        prompt = store.build_context_prompt("navigate to settings")
        assert "use keyboard shortcuts" in prompt
        assert "Ctrl+N is faster" in prompt

        # ── Step 4: Verify memory survives re-instantiation ──
        store2 = MemoryStore()
        assert len(store2.get_rules()) == 1
        assert store2.get_rules()[0].rule_text == "use keyboard shortcuts"

    def test_two_tasks_accumulate_knowledge(self, store):
        """Multiple tasks should accumulate rules and episodes."""
        # Task 1
        mock1 = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "ok",
                "rules": [{"rule": "click center", "trigger": "click", "confidence": 0.7}],
                "preferences": [{"key": "speed", "value": "fast", "confidence": 0.6}],
                "insights": [],
                "mistakes": [],
            })}}]
        })
        r1 = ReflectionEngine(call_llm_fn=mock1).reflect_task("click button", [{"step_num": 1, "result": {"success": True}}], True, 500)
        for rule in r1.get("rules", []):
            store.add_rule(rule["rule"], trigger_context=rule.get("trigger", "general"), confidence=rule["confidence"])
        for pref in r1.get("preferences", []):
            store.add_preference(pref["key"], pref["value"], confidence=pref["confidence"])
        store.add_episode(Episode(goal="click button", success=True, total_steps=1))

        # Task 2
        mock2 = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "done",
                "rules": [{"rule": "type slowly", "trigger": "type", "confidence": 0.8}],
                "preferences": [],
                "insights": [],
                "mistakes": ["pressed enter too soon"],
            })}}]
        })
        r2 = ReflectionEngine(call_llm_fn=mock2).reflect_task("type text", [{"step_num": 1, "result": {"success": True}}], True, 300)
        for rule in r2.get("rules", []):
            store.add_rule(rule["rule"], trigger_context=rule.get("trigger", "general"), confidence=rule["confidence"])
        store.add_episode(Episode(
            goal="type text", success=True, total_steps=1,
            mistakes=r2.get("mistakes", []),
        ))

        assert store.stats()["rules"] == 2
        assert store.stats()["preferences"] == 1
        assert store.stats()["episodes"] == 2

        # Prompt should contain both rules
        prompt = store.build_context_prompt("click and type")
        assert "click center" in prompt
        assert "type slowly" in prompt

    def test_persistence_across_restarts(self, store):
        """Memory survives store re-creation (simulates agent restart)."""
        mock_llm = MagicMock(return_value={
            "choices": [{"message": {"content": json.dumps({
                "overall_assessment": "done",
                "rules": [{"rule": "check network", "trigger": "connect", "confidence": 0.9}],
                "preferences": [{"key": "protocol", "value": "https", "confidence": 0.8}],
                "insights": ["always verify connection"],
                "mistakes": [],
            })}}]
        })
        reflection = ReflectionEngine(call_llm_fn=mock_llm).reflect_task(
            "connect", [{"step_num": 1, "result": {"success": True}}], True, 1000,
        )
        for r in reflection.get("rules", []):
            store.add_rule(r["rule"], trigger_context=r.get("trigger", "general"), confidence=r["confidence"])
        for p in reflection.get("preferences", []):
            store.add_preference(p["key"], p["value"], confidence=p["confidence"])
        store.add_episode(Episode(
            goal="connect", success=True, total_steps=1,
            insights=reflection.get("insights", []),
        ))

        # Simulate restart
        store2 = MemoryStore()
        assert len(store2.get_rules()) == 1
        assert len(store2.get_preferences()) == 1
        assert len(store2.get_recent_episodes()) == 1
        assert store2.get_recent_episodes()[0].insights == ["always verify connection"]

        # Memory injection still works after restart
        prompt = store2.build_context_prompt("connect to server")
        assert "check network" in prompt
