"""Coding tasks: excluded from action-replay reuse, but keep factual notes.

Covers the option-B design:
- auto-recorded action recipes are NOT injected into coding tasks
- manual 'remember' notes ARE injected (notes_only)
- manual notes are never evicted by score-based feedback
- _is_coding_task detection
"""
import importlib

import pytest


@pytest.fixture
def fresh_memory(tmp_path, monkeypatch):
    """Point experience storage at a temp file and reload the module."""
    from winpilot import config
    mem = tmp_path / "experience_memory.json"
    monkeypatch.setattr(config, "MEMORY_PATH", mem)
    monkeypatch.setitem(config.CONFIG._data["agent"], "experience_memory", True)
    from winpilot.memory import experience
    importlib.reload(experience)
    # reload re-binds MEMORY_PATH from config at import time
    monkeypatch.setattr(experience, "MEMORY_PATH", mem)
    monkeypatch.setitem(experience.CONFIG._data["agent"], "experience_memory", True)
    return experience


def test_notes_only_excludes_action_recipes(fresh_memory):
    exp = fresh_memory
    # an auto-recorded action recipe (manual defaults False via record())
    exp.record("在某网站登录并下单", "chrome",
               ['click "登录"', 'type_text "user"'], verified=True)
    # a manual factual note (remember)
    exp.add("项目acme用pnpm不用npm", app="acme", steps=["pnpm install"], manual=True)

    notes = exp.note_hints("acme 项目怎么装依赖")
    assert "pnpm" in notes               # factual note surfaces
    assert "登录" not in notes           # action recipe excluded

    # full hints (GUI path) still include the action recipe
    full, _ids = exp.hints_with_ids("登录并下单")
    assert "登录" in full


def test_manual_notes_survive_failure_feedback(fresh_memory):
    exp = fresh_memory
    note = exp.add("acme 测试命令是 pytest -q", app="acme", steps=["pytest -q"], manual=True)
    nid = note["id"]
    # three failed runs that "injected" this note must NOT evict it (it's a fact)
    for _ in range(3):
        exp.feedback([nid], success=False)
    assert exp.get(nid) is not None, "manual factual note must never be auto-evicted"


def test_auto_recipe_still_evicted_on_repeated_failure(fresh_memory):
    exp = fresh_memory
    exp.record("脆弱的GUI流程", "app", ['click "X"'], verified=True)
    rid = exp.all_recipes()[0]["id"]
    for _ in range(3):
        exp.feedback([rid], success=False)
    assert exp.get(rid) is None, "a never-successful auto recipe should be evicted"


def test_volatile_window_state_tasks_do_not_reuse_or_record(fresh_memory):
    exp = fresh_memory
    task = "只检查当前已经打开的QQ窗口，找出哪个已经登录、哪个是登录页"

    assert exp.is_volatile_state_task(task)
    exp.record(task, "QQ", ['focus_window "QQ"', 'read_text'], verified=True,
               script="stale.wps.json")
    assert exp.all_recipes() == []

    exp.add(task, app="QQ", steps=['focus_window "QQ"'], manual=False)
    full, ids = exp.hints_with_ids(task)
    assert full == ""
    assert ids == []
    script, recipe = exp.reusable_script_for(task)
    assert script == ""
    assert recipe is None


def test_is_coding_task_detection():
    from winpilot.agent.loop import _is_coding_task
    # strong coding signals
    assert _is_coding_task("调试这个 python 项目的报错")
    assert _is_coding_task("重构仓库代码")
    assert _is_coding_task("clone 一个开源项目跑起来")
    # "build a <software>" intent → coding (the snake-game case that regressed)
    assert _is_coding_task("做一个贪吃蛇小游戏")
    assert _is_coding_task("帮我写一个登录页面")
    assert _is_coding_task("开发一个待办事项网页")
    # open/launch/play intents → GUI, even for games/apps
    assert not _is_coding_task("打开游戏让我玩一下")
    assert not _is_coding_task("打开记事本输入你好")
    assert not _is_coding_task("玩贪吃蛇游戏")
    assert not _is_coding_task("网易云音乐搜索周杰伦播放")
