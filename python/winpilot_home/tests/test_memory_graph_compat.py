import importlib
import json


def _fresh_experience(tmp_path, monkeypatch):
    from winpilot import config

    monkeypatch.setattr(config, "MEMORY_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "experience_memory.json")
    monkeypatch.setitem(config.CONFIG._data["agent"], "experience_memory", True)
    from winpilot.memory import experience

    importlib.reload(experience)
    return experience


def test_experience_api_stays_recipe_like(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)

    exp.record(
        "open browser settings and clear cache",
        "Browser",
        ["open settings", "clear cache"],
        verified=True,
        script="clear-cache.wps.json",
        conditions=["settings page visible"],
        evidence=["cache cleared text visible"],
    )
    recipe = exp.all_recipes()[0]

    assert set(
        "id task app steps uses successes provisional manual script conditions evidence".split()
    ).issubset(recipe)
    assert recipe["script"] == "clear-cache.wps.json"
    assert recipe["conditions"] == ["settings page visible"]

    script, matched = exp.reusable_script_for("open browser settings and clear cache")
    assert script == "clear-cache.wps.json"
    assert matched["id"] == recipe["id"]

    note = exp.add("acme uses pnpm", app="acme", steps=["pnpm install"], manual=True)
    assert "pnpm" in exp.note_hints("acme dependencies")
    assert exp.update(note["id"], {"task": "acme uses pnpm only"})["task"] == "acme uses pnpm only"
    assert exp.delete(note["id"])


def test_apprentice_skill_is_not_reusable_until_verified(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    candidate = exp.record(
        "send a message to my phone",
        "QQ",
        ["click Send"],
        verified=False,
        script="qq-message.wps.json",
        evidence=["message is visible in chat history"],
    )

    assert candidate is not None
    assert candidate["kind"] == "skill"
    assert candidate["provisional"] is True
    assert exp.reusable_script_for("send a message to my phone")[0] == ""

    promoted = exp.promote_skill(candidate["id"], "message appeared in chat history")

    assert promoted is not None
    assert promoted["provisional"] is False
    assert promoted["status"] == "active"
    assert promoted["successes"] == 1
    assert exp.reusable_script_for("send a message to my phone")[0] == "qq-message.wps.json"


def test_legacy_json_migration_is_idempotent(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    legacy = tmp_path / "experience_memory.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "task": "open calculator",
                    "app": "Calculator",
                    "steps": ["launch calculator"],
                    "script": "calc.wps.json",
                    "manual": False,
                    "provisional": False,
                    "conditions": ["calculator task"],
                    "evidence": ["calculator visible"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recipes = exp.all_recipes()
    assert [r["id"] for r in recipes] == ["abc123"]
    assert legacy.exists()
    assert exp.all_recipes()[0]["id"] == "abc123"
