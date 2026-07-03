import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def fresh_memory(tmp_path, monkeypatch):
    from winpilot import config

    mem = tmp_path / "experience_memory.json"
    monkeypatch.setattr(config, "MEMORY_PATH", mem)
    monkeypatch.setitem(config.CONFIG._data["agent"], "experience_memory", True)
    from winpilot.memory import experience

    importlib.reload(experience)
    monkeypatch.setattr(experience, "MEMORY_PATH", mem)
    monkeypatch.setitem(experience.CONFIG._data["agent"], "experience_memory", True)
    return experience


def test_memory_hints_render_conditions_evidence_and_truncate_steps(fresh_memory):
    exp = fresh_memory
    exp.record(
        "open browser settings and enable sync",
        "Browser",
        [f"step {i}" for i in range(1, 13)],
        verified=True,
        script="enable-sync.wps.json",
        conditions=["current browser settings page is visible"],
        evidence=["observe/read_text confirms sync is enabled before done"],
    )

    hints, ids = exp.hints_with_ids("open browser settings and enable sync")

    assert ids
    assert "Conditions:" in hints
    assert "current browser settings page is visible" in hints
    assert "Evidence required:" in hints
    assert "observe/read_text confirms sync is enabled before done" in hints
    assert "step 8" in hints
    assert "step 9" not in hints
    assert "... 4 more step(s) kept in memory" in hints


def test_reusable_script_requires_strong_implicit_match(fresh_memory):
    exp = fresh_memory
    exp.record(
        "open browser settings and clear cache",
        "Browser",
        ["click Settings", "click Clear cache"],
        verified=True,
        script="clear-cache.wps.json",
    )

    script, recipe = exp.reusable_script_for("open calculator")

    assert script == ""
    assert recipe is None

    script, recipe = exp.reusable_script_for("open browser settings and clear cache")

    assert script == "clear-cache.wps.json"
    assert recipe["task"] == "open browser settings and clear cache"


def test_session_context_only_for_referential_tasks():
    from winpilot.agent import session

    assert session.should_include_context("open calculator") is False
    assert session.should_include_context("do it again") is True
    assert session.should_include_context("continue the previous one") is True
    assert session.should_include_context("继续刚才那个") is True


def test_fold_checkpoint_preserves_goal_screen_and_recent_evidence():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("open settings and enable sync")
    runner.session.snapshot = SimpleNamespace(
        title="Settings",
        hwnd=42,
        elements=[object(), object()],
    )
    runner._recent_results = [
        ("read_text", "Sync is still off; enable button visible"),
        ("run_shell", "deterministic check pending"),
    ]

    for i in range(3):
        runner._append_tool_result(
            f"call-{i}",
            "x" * 12000,
            summary=f"[folded] bulky observation {i}",
        )

    folded = runner.messages[0]["content"]

    assert "[folded context checkpoint]" in folded
    assert "goal: open settings and enable sync" in folded
    assert 'screen: title="Settings"' in folded
    assert "recent_tool_evidence:" in folded
    assert "Sync is still off" in folded
    assert "folded_item: [folded] bulky observation 0" in folded


def test_legacy_playbook_gets_conditions_and_evidence(tmp_path, monkeypatch):
    import json

    from winpilot.agent import playbooks

    playbook_file = tmp_path / "generic-dialog.json"
    playbook_file.write_text(
        json.dumps(
            {
                "name": "generic-dialog",
                "description": "dialog tactic",
                "keywords": ["dialog"],
                "window_triggers": ["Dialog"],
                "content": "Use the dialog carefully.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    monkeypatch.setattr(playbooks, "_cache", None)

    [book] = playbooks.load_all()
    rendered = playbooks.render_section([book])

    assert book.conditions
    assert book.evidence
    assert "# Playbook: generic-dialog" in rendered
    assert "Conditions:" in rendered
    assert "Task keyword anchors: dialog." in rendered
    assert "Evidence required:" in rendered
    assert "verify the requested result" in rendered


def test_done_does_not_score_unused_injected_memory(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    calls = []
    runner = AgentRunner("same keyword task")
    runner.status = "done"
    runner._hint_ids = ["hinted"]

    monkeypatch.setattr(
        "winpilot.agent.loop.experience.feedback",
        lambda ids, success: calls.append((list(ids), success)),
    )

    runner._score_experience_feedback()

    assert calls == []


def test_reuse_skill_records_actual_recipe_use(tmp_path, monkeypatch):
    from winpilot import config
    from winpilot.agent.tools import ToolSession
    from winpilot.agent import memory_tools
    from winpilot.recorder import trace as trace_mod

    script = tmp_path / "demo.wps.json"
    script.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(
        memory_tools.experience,
        "reusable_script_for",
        lambda task, recipe_id="": ("demo.wps.json", {"id": "rid-1", "task": task}),
    )
    monkeypatch.setattr(trace_mod, "replay", lambda *a, **k: {"ok": True, "results": []})

    session = ToolSession(task="demo task")
    result = session._t_reuse_skill({"task": "demo task"})

    assert "observe" in result.text
    assert session.used_memory_recipe_ids == ["rid-1"]


def test_experience_feedback_scores_used_recipe_not_all_hints(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    calls = []
    runner = AgentRunner("task")
    runner.status = "done"
    runner._hint_ids = ["hinted"]
    runner.session.used_memory_recipe_ids = ["used", "used"]

    monkeypatch.setattr(
        "winpilot.agent.loop.experience.feedback",
        lambda ids, success: calls.append((list(ids), success)),
    )

    runner._score_experience_feedback()

    assert calls == [(["used"], True)]


def test_failed_run_without_reuse_still_penalizes_injected_hints(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    calls = []
    runner = AgentRunner("task")
    runner.status = "fail"
    runner._hint_ids = ["hinted"]

    monkeypatch.setattr(
        "winpilot.agent.loop.experience.feedback",
        lambda ids, success: calls.append((list(ids), success)),
    )

    runner._score_experience_feedback()

    assert calls == [(["hinted"], False)]
