"""
Tests for MemoryStore — long-term memory for general-purpose agent.

Tests are language-agnostic (no natural language assertions), focus on
the memory contract: store → retrieve → persist → survive restart.
"""
import os
import sys
import json
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.memory import (
    MemoryStore, Preference, BehavioralRule, Episode, PatternAnalyzer,
)
import agent.memory as _mem


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
#  PatternAnalyzer — signal extraction from user feedback
# ═══════════════════════════════════════════════════════════════════

class TestPatternAnalyzer:
    def test_explicit_remember(self):
        r = PatternAnalyzer.analyze("remember: always check the output")
        assert r["type"] == "explicit"
        assert r["confidence"] >= 0.9

    def test_guardrail_dont_unless(self):
        r = PatternAnalyzer.analyze("don't add comments unless I ask")
        assert r["type"] == "guardrail"
        assert r["confidence"] >= 0.85

    def test_correction_no_prefix(self):
        r = PatternAnalyzer.analyze("No, you should use the other method")
        assert r["type"] == "correction"

    def test_correction_use_not(self):
        r = PatternAnalyzer.analyze("use pytest not unittest")
        assert r["type"] == "correction"

    def test_positive_feedback(self):
        r = PatternAnalyzer.analyze("perfect! that's exactly what I wanted")
        assert r["type"] == "positive"
        assert r["sentiment"] == "positive"

    def test_question_is_filtered(self):
        r = PatternAnalyzer.analyze("can you help me fix this bug?")
        assert r["type"] is None  # filtered out as false positive

    def test_short_non_actionable(self):
        r = PatternAnalyzer.analyze("yes")
        assert r["type"] is None

    def test_empty_text(self):
        r = PatternAnalyzer.analyze("")
        assert r["type"] is None

    def test_explicit_takes_priority(self):
        """'remember:' prefix bypasses all filters."""
        r = PatternAnalyzer.analyze("remember: no, don't do that")
        assert r["type"] == "explicit"

    def test_guardrail_takes_priority_over_false_positive(self):
        r = PatternAnalyzer.analyze("don't add comments unless I ask, can you help me?")
        assert r["type"] == "guardrail"

    def test_special_chars_only(self):
        r = PatternAnalyzer.analyze("!!! *** &&&")
        assert r["type"] is None

    def test_mixed_language_correction(self):
        """Correction patterns should work regardless of surrounding language."""
        r = PatternAnalyzer.analyze("use the API not the SDK please")
        assert r["type"] == "correction"

    def test_very_long_text_reduces_confidence(self):
        r = PatternAnalyzer.analyze("no, " + "x" * 300)
        assert r["type"] == "correction"
        assert r["confidence"] <= 0.70

    def test_multiple_patterns_boost_confidence(self):
        r = PatternAnalyzer.analyze("no, don't do that. I told you to use the API not the SDK")
        assert r["type"] == "correction"
        assert r["confidence"] >= 0.75


# ═══════════════════════════════════════════════════════════════════
#  Preferences — CRUD + confidence merging
# ═══════════════════════════════════════════════════════════════════

class TestPreferences:
    def test_add_and_get(self, store):
        store.add_preference("writing_style", "concise", confidence=0.8, domain="general")
        prefs = store.get_preferences()
        assert len(prefs) == 1
        assert prefs[0].key == "writing_style"
        assert prefs[0].value == "concise"
        assert prefs[0].confidence == 0.8

    def test_add_multiple(self, store):
        store.add_preference("style", "formal", confidence=0.7)
        store.add_preference("browser", "chrome", confidence=0.9)
        assert len(store.get_preferences()) == 2

    def test_update_with_higher_confidence(self, store):
        store.add_preference("style", "formal", confidence=0.7)
        store.add_preference("style", "concise", confidence=0.9)
        prefs = store.get_preferences()
        assert len(prefs) == 1
        assert prefs[0].value == "concise"
        assert prefs[0].confidence == 0.9

    def test_update_with_lower_confidence(self, store):
        store.add_preference("style", "formal", confidence=0.9)
        store.add_preference("style", "concise", confidence=0.5)
        prefs = store.get_preferences()
        assert len(prefs) == 1
        assert prefs[0].value == "concise"  # still updated (new value)
        assert prefs[0].confidence == 0.9    # kept higher

    def test_domain_filter(self, store):
        store.add_preference("style", "formal", domain="writing")
        store.add_preference("browser", "chrome", domain="tools")
        assert len(store.get_preferences(domain="writing")) == 1
        assert len(store.get_preferences(domain="tools")) == 1
        assert len(store.get_preferences()) == 2

    def test_confidence_filter(self, store):
        store.add_preference("a", "1", confidence=0.9)
        store.add_preference("b", "2", confidence=0.2)
        assert len(store.get_preferences(min_confidence=0.5)) == 1

    def test_get_specific_key(self, store):
        store.add_preference("style", "formal")
        assert store.get_preference("style") is not None
        assert store.get_preference("nonexistent") is None

    def test_empty_key_accepted(self, store):
        """Empty key should not crash."""
        store.add_preference("", "value", confidence=0.5)
        assert store.get_preference("") is not None


# ═══════════════════════════════════════════════════════════════════
#  Rules — CRUD + confidence + evidence tracking
# ═══════════════════════════════════════════════════════════════════

class TestRules:
    def test_add_and_get(self, store):
        store.add_rule("Verify before submit", trigger_context="form", confidence=0.85)
        rules = store.get_rules()
        assert len(rules) == 1
        assert rules[0].rule_text == "Verify before submit"
        assert rules[0].confidence == 0.85

    def test_trigger_context_filter(self, store):
        store.add_rule("Rule A", trigger_context="form")
        store.add_rule("Rule B", trigger_context="search")
        assert len(store.get_rules(trigger_context="form")) == 1

    def test_merge_duplicate_text(self, store):
        store.add_rule("Same rule", confidence=0.5)
        store.add_rule("Same rule", confidence=0.9)
        rules = store.get_rules()
        assert len(rules) == 1
        assert rules[0].confidence == 0.9
        assert rules[0].success_count == 2

    def test_record_failure_lowers_confidence(self, store):
        store.add_rule("Test rule", confidence=0.8)
        store.record_rule_failure("Test rule")
        rule = store.get_rules()[0]
        assert rule.failure_count == 1
        assert rule.confidence < 0.8

    def test_record_success_raises_confidence(self, store):
        store.add_rule("Test rule", confidence=0.5)
        store.record_rule_success("Test rule", evidence="worked great")
        rule = store.get_rules()[0]
        assert rule.confidence > 0.5
        assert rule.confirmed_count == 1

    def test_failure_with_evidence(self, store):
        store.add_rule("Test rule", confidence=0.8)
        store.record_rule_failure("Test rule", evidence="button not found")
        rule = store.get_rules()[0]
        assert rule.contradicted_count == 1
        assert "button not found" in rule.evidence

    def test_guardrail_flag(self, store):
        store.add_rule("Don't X unless Y", is_guardrail=True)
        assert store.get_rules()[0].is_guardrail is True

    def test_empty_rule_text(self, store):
        store.add_rule("", confidence=0.5)
        assert len(store.get_rules()) == 1

    def test_very_long_rule_text(self, store):
        long_text = "x" * 10000
        store.add_rule(long_text, confidence=0.5)
        assert store.get_rules()[0].rule_text == long_text

    def test_relevant_rules_by_keyword(self, store):
        store.add_rule("Use UIA over OCR", trigger_context="window automation")
        store.add_rule("Check network first", trigger_context="browser")
        relevant = store.get_relevant_rules("automate window resizing")
        texts = [r.rule_text for r in relevant]
        assert "Use UIA over OCR" in texts


# ═══════════════════════════════════════════════════════════════════
#  Episodes — task experience storage + similarity search
# ═══════════════════════════════════════════════════════════════════

class TestEpisodes:
    def test_add_and_get_recent(self, store):
        ep = Episode(
            run_id="run_1", goal="Open calculator",
            success=True, total_steps=3,
            summary="Opened calculator successfully",
            insights=["Use Win+R for quick launch"],
            tools_used=["press_keys", "click"],
        )
        store.add_episode(ep)
        recent = store.get_recent_episodes()
        assert len(recent) == 1
        assert recent[0].goal == "Open calculator"

    def test_find_similar_by_keywords(self, store):
        ep1 = Episode(goal="Open calculator and calculate", success=True, total_steps=5)
        ep2 = Episode(goal="Search for files in folder", success=True, total_steps=3)
        store.add_episode(ep1)
        store.add_episode(ep2)
        similar = store.find_similar_episodes("open calculator")
        assert len(similar) >= 1
        assert similar[0].goal == "Open calculator and calculate"

    def test_find_similar_no_match_returns_empty(self, store):
        """No keyword overlap should yield empty list."""
        store.add_episode(Episode(goal="Open notepad", success=True))
        similar = store.find_similar_episodes("something completely different")
        assert similar == []

    def test_find_similar_empty_query_returns_empty(self, store):
        store.add_episode(Episode(goal="open calculator", success=True))
        assert store.find_similar_episodes("") == []

    def test_find_similar_stopwords_only(self, store):
        store.add_episode(Episode(goal="open calculator", success=True))
        assert store.find_similar_episodes("the a an") == []

    def test_episode_preserves_insights_and_mistakes(self, store):
        ep = Episode(
            goal="complex task", success=False, total_steps=5,
            insights=["insight1", "insight2"],
            mistakes=["mistake1"],
            tools_used=["click", "type"],
            elapsed_ms=1500,
        )
        store.add_episode(ep)
        recent = store.get_recent_episodes(limit=1)
        assert recent[0].insights == ["insight1", "insight2"]
        assert recent[0].mistakes == ["mistake1"]
        assert recent[0].tools_used == ["click", "type"]
        assert recent[0].elapsed_ms == 1500

    def test_episode_empty_summary_does_not_crash(self, store):
        store.add_episode(Episode(goal="test task", success=True, total_steps=2))
        prompt = store.build_context_prompt("test task")
        assert len(prompt) > 0


# ═══════════════════════════════════════════════════════════════════
#  Context prompt assembly
# ═══════════════════════════════════════════════════════════════════

class TestContextAssembly:
    def test_empty_store_returns_empty(self, store):
        assert store.build_context_prompt("test goal") == ""

    def test_with_preferences(self, store):
        store.add_preference("style", "concise", confidence=0.9)
        prompt = store.build_context_prompt("write something")
        assert len(prompt) > 0
        assert "concise" in prompt

    def test_with_rules(self, store):
        store.add_rule("Verify before submit", confidence=0.85)
        prompt = store.build_context_prompt("submit form")
        assert "Verify before submit" in prompt

    def test_with_episodes(self, store):
        store.add_episode(Episode(goal="test task", success=True, total_steps=2, summary="done"))
        prompt = store.build_context_prompt("test task")
        assert len(prompt) > 0

    def test_length_limited(self, store):
        for i in range(20):
            store.add_preference(f"pref_{i}", f"val_{i}", confidence=0.9)
        for i in range(20):
            store.add_rule(f"rule {i}", trigger_context="general", confidence=0.9)
        for i in range(10):
            store.add_episode(Episode(goal=f"task {i}", success=True, total_steps=i))
        prompt = store.build_context_prompt("task")
        assert len(prompt) < 3000

    def test_relevant_rules_empty_goal_matches_general(self, store):
        store.add_rule("Test rule", trigger_context="general", confidence=0.8)
        assert len(store.get_relevant_rules("")) == 1


# ═══════════════════════════════════════════════════════════════════
#  Tension detection — contradictory rules
# ═══════════════════════════════════════════════════════════════════

class TestTensionDetection:
    def test_no_tensions_empty_store(self, store):
        assert store.detect_tensions() == []

    def test_guardrail_vs_action(self, store):
        store.add_rule("Always add comments", trigger_context="coding", is_guardrail=False)
        store.add_rule("Don't add comments", trigger_context="coding", is_guardrail=True)
        tensions = store.detect_tensions()
        types = [t["type"] for t in tensions]
        assert "guardrail_vs_action" in types

    def test_no_tension_without_context_overlap(self, store):
        store.add_rule("Rule A", trigger_context="form")
        store.add_rule("Rule B", trigger_context="search")
        assert store.detect_tensions() == []

    def test_direct_contradiction(self, store):
        store.add_rule("don't add extra features", trigger_context="coding", confidence=0.8)
        store.add_rule("add extra features for completeness", trigger_context="coding", confidence=0.8)
        tensions = store.detect_tensions()
        types = [t["type"] for t in tensions]
        assert "direct_contradiction" in types

    def test_no_tensions_single_rule(self, store):
        store.add_rule("Only rule", trigger_context="test", confidence=0.8)
        assert store.detect_tensions() == []

    def test_tension_min_confidence_filter(self, store):
        store.add_rule("Rule A low", trigger_context="general", confidence=0.2)
        store.add_rule("Rule B low", trigger_context="general", confidence=0.15)
        assert store.detect_tensions(min_confidence=0.5) == []


# ═══════════════════════════════════════════════════════════════════
#  Thread safety
# ═══════════════════════════════════════════════════════════════════

class TestThreadSafety:
    def test_concurrent_writes(self, store):
        errors = []
        def writer(k):
            try:
                for _ in range(20):
                    store.add_preference(k, "val", confidence=0.5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"key_{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(store.get_preferences()) == 5

    def test_concurrent_read_write_mixed(self, store):
        store.add_preference("test", "val")
        errors = []
        def read_write():
            try:
                for _ in range(50):
                    store.get_preferences()
                    store.build_context_prompt("test")
                    store.add_preference("dyn", "x", confidence=0.5)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_write) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_concurrent_rule_and_episode_writes(self, store):
        errors = []
        def write_rules():
            try:
                for i in range(20):
                    store.add_rule(f"rule_{i}", confidence=0.5)
            except Exception as e:
                errors.append(e)

        def write_episodes():
            try:
                for i in range(20):
                    store.add_episode(Episode(goal=f"task_{i}", success=True, total_steps=1))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=write_rules)
        t2 = threading.Thread(target=write_episodes)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors


# ═══════════════════════════════════════════════════════════════════
#  Persistence — data survives store re-instantiation
# ═══════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_data_survives_reload(self, store):
        store.add_preference("style", "formal", confidence=0.8)
        store.add_rule("Be careful", confidence=0.9)
        store2 = MemoryStore()
        assert len(store2.get_preferences()) == 1
        assert len(store2.get_rules()) == 1

    def test_episodes_survive_reload(self, store):
        store.add_episode(Episode(goal="test", success=True, total_steps=2))
        store2 = MemoryStore()
        assert len(store2.get_recent_episodes()) == 1

    def test_clear_removes_all(self, store):
        store.add_preference("a", "1")
        store.add_rule("r1", confidence=0.5)
        store.add_episode(Episode(goal="test", success=True))
        store.clear()
        assert len(store.get_preferences()) == 0
        assert len(store.get_rules()) == 0
        assert len(store.get_recent_episodes()) == 0

    def test_corrupted_json_file(self, store):
        """Corrupted JSON should not crash — storage should reset."""
        store.add_preference("test", "value")
        pref_path = os.path.join(_mem.MEMORY_DIR, "preferences.json")
        with open(pref_path, "w", encoding="utf-8") as f:
            f.write("{corrupted json")
        store2 = MemoryStore()
        assert len(store2.get_preferences()) == 0

    def test_truncated_json_file(self, store):
        """Partially written JSON should not crash."""
        store.add_preference("test", "value")
        pref_path = os.path.join(_mem.MEMORY_DIR, "preferences.json")
        with open(pref_path, "wb") as f:
            f.write(b'{"key": "trunc')  # truncated mid-value
        store2 = MemoryStore()
        assert len(store2.get_preferences()) == 0


# ═══════════════════════════════════════════════════════════════════
#  Stats
# ═══════════════════════════════════════════════════════════════════

class TestStats:
    def test_empty_stats(self, store):
        assert store.stats() == {"preferences": 0, "rules": 0, "episodes": 0}

    def test_stats_after_adding(self, store):
        store.add_preference("a", "1")
        store.add_rule("r1", confidence=0.5)
        stats = store.stats()
        assert stats["preferences"] == 1
        assert stats["rules"] == 1
        assert stats["episodes"] == 0


# ═══════════════════════════════════════════════════════════════════
#  Static method passthrough
# ═══════════════════════════════════════════════════════════════════

class TestQuickAnalyze:
    def test_quick_analyze_delegates_to_pattern_analyzer(self, store):
        r = store.quick_analyze("remember: always check first")
        assert r["type"] == "explicit"
        assert r["confidence"] >= 0.9
