import hashlib
import importlib
import json

import pytest


def _fresh_experience(tmp_path, monkeypatch):
    from winpilot import config

    monkeypatch.setattr(config, "MEMORY_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "experience_memory.json")
    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setitem(config.CONFIG._data["agent"], "experience_memory", True)
    from winpilot.memory import experience

    importlib.reload(experience)
    return experience


def _trusted_record(exp, *args, run_id="test-source-run", **kwargs):
    return exp.record(
        *args,
        **kwargs,
        verified=True,
        source_run_id=run_id,
        source_verification={"done_verified": True},
    )


def test_experience_api_stays_recipe_like(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    (tmp_path / "clear-cache.wps.json").write_text(
        '{"format":"wps/2","steps":[]}', encoding="utf-8"
    )

    _trusted_record(
        exp,
        "open browser settings and clear cache",
        "Browser",
        ["open settings", "clear cache"],
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
    script_path = tmp_path / "qq-message.wps.json"
    script_path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    candidate = exp.record(
        "send a message to my phone",
        "QQ",
        ["click Send"],
        verified=False,
        script="qq-message.wps.json",
        evidence=["message is visible in chat history"],
        verification=[{"predicate": "visible.text", "value": "message sent"}],
    )

    assert candidate is not None
    assert candidate["kind"] == "skill"
    assert candidate["provisional"] is True
    assert exp.reusable_script_for("send a message to my phone")[0] == ""

    assert exp.promote_skill(candidate["id"], "message appeared in chat history") is None
    promoted = exp.promote_skill_from_replay(
        candidate["id"],
        "verify-1",
        {
            "ok": True,
            "postflight": {
                "ok": True,
                "required": True,
                "checks": [{"ok": True, "check": {"text_appears": "message sent"}}],
            },
        },
    )

    assert promoted is not None
    assert promoted["provisional"] is False
    assert promoted["status"] == "active"
    assert promoted["successes"] == 1
    assert exp.reusable_script_for("send a message to my phone")[0] == "qq-message.wps.json"


def test_verified_boolean_without_source_proof_stays_provisional(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    path = tmp_path / "boolean-only.wps.json"
    path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")

    candidate = exp.record(
        "boolean only",
        "App",
        ["step"],
        verified=True,
        script=path.name,
    )

    assert candidate["provisional"] is True
    assert candidate["script_sha256"] == ""
    assert exp.reusable_script_for("boolean only", candidate["id"])[0] == ""


def test_explicit_id_still_requires_intent_app_and_unchanged_script(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    path = tmp_path / "settings.wps.json"
    path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    recipe = _trusted_record(
        exp,
        "open browser settings",
        "Browser",
        ["open settings"],
        script=path.name,
    )

    assert exp.reusable_script_for("open calculator", recipe["id"])[0] == ""
    assert exp.reusable_script_for(
        "open browser settings",
        recipe["id"],
        runtime_profile={"app": "Calculator"},
        require_runtime_context=True,
    )[0] == ""
    assert exp.reusable_script_for(
        "open browser settings",
        recipe["id"],
        runtime_profile={"app": "Browser - Settings"},
        require_runtime_context=True,
    )[0] == path.name
    assert exp.reusable_script_for(
        "open browser settings",
        recipe["id"],
        runtime_profile={"app": "Browser - Settings", "safety_scope": "shell"},
        require_runtime_context=True,
    )[0] == ""

    path.write_text('{"format":"wps/2","steps":[{"tool":"wait"}]}', encoding="utf-8")
    assert exp.reusable_script_for("open browser settings", recipe["id"])[0] == ""


def test_editing_verified_skill_revokes_trust_and_manual_activation_is_rejected(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    path = tmp_path / "verified.wps.json"
    path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    recipe = _trusted_record(exp, "open settings", "Settings", ["open"], script=path.name)

    edited = exp.update(recipe["id"], {"task": "open all settings"})

    assert edited["provisional"] is True
    assert edited["status"] == "provisional"
    assert edited["verification_state"] == "candidate_edited"
    assert exp.reusable_script_for("open all settings", recipe["id"])[0] == ""
    with pytest.raises(ValueError, match="verified replay"):
        exp.update(recipe["id"], {"provisional": False})


def test_hash_without_verification_provenance_is_not_trusted(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    path = tmp_path / "unproven.wps.json"
    path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    recipe = _trusted_record(exp, "open settings", "Settings", ["open"], script=path.name)
    exp.update(recipe["id"], {"verification_state": ""}, preserve_trust=True)

    assert exp.script_trust(path.name)["trust"] == "draft"
    assert exp.reusable_script_for("open settings", recipe["id"])[0] == ""


def test_retired_candidate_cannot_be_reactivated_by_late_replay(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    path = tmp_path / "late.wps.json"
    path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    candidate = exp.record(
        "late replay",
        "App",
        ["step"],
        verified=False,
        script=path.name,
        verification=[{"predicate": "visible.text", "value": "done"}],
    )
    exp._store().retire_node(candidate["id"])

    promoted = exp.promote_skill_from_replay(
        candidate["id"],
        "verify-late",
        {
            "ok": True,
            "postflight": {
                "ok": True,
                "required": True,
                "checks": [{"ok": True, "check": {"text_appears": "done"}}],
            },
        },
        expected_script=path.name,
    )

    assert promoted is None
    assert exp._store().get_node(candidate["id"])["status"] == "retired"


def test_new_provisional_version_keeps_prior_verified_skill(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    trusted_path = tmp_path / "trusted.wps.json"
    trusted_path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    trusted = _trusted_record(exp, "same workflow", "App", ["trusted"], script=trusted_path.name)
    draft = exp.record("same workflow", "App", ["draft"], verified=False, script=trusted_path.name)

    assert exp.get(trusted["id"])["status"] == "active"
    assert exp.get(draft["id"])["status"] == "provisional"
    related = exp._store().related(draft["id"], {"supersedes"}, direction="out")
    assert any(item["node"]["id"] == trusted["id"] for item in related)
    assert exp.script_trust(trusted_path.name)["trust"] == "verified"


def test_same_title_in_different_apps_does_not_supersede(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    browser_path = tmp_path / "browser.wps.json"
    system_path = tmp_path / "system.wps.json"
    browser_path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")
    system_path.write_text('{"format":"wps/2","steps":[]}', encoding="utf-8")

    browser = _trusted_record(exp, "open settings", "Browser", ["browser"], script=browser_path.name, run_id="browser-run")
    system = _trusted_record(exp, "open settings", "System Settings", ["system"], script=system_path.name, run_id="system-run")

    assert exp.get(browser["id"])["status"] == "active"
    assert exp.get(system["id"])["status"] == "active"
    related = exp._store().related(system["id"], {"supersedes"}, direction="out")
    assert not any(item["node"]["id"] == browser["id"] for item in related)


def test_legacy_json_migration_is_idempotent(tmp_path, monkeypatch):
    exp = _fresh_experience(tmp_path, monkeypatch)
    legacy = tmp_path / "experience_memory.json"
    script_bytes = b'{"format":"wps/2","steps":[]}'
    (tmp_path / "calc.wps.json").write_bytes(script_bytes)
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
                    "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
                    "verification_state": "source_run_verified",
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
    assert recipes[0]["provisional"] is True
    assert recipes[0]["script_sha256"] == ""
    assert recipes[0]["verification_state"] == "legacy_unverified"
    assert exp.script_trust("calc.wps.json")["trust"] == "draft"
    assert legacy.exists()
    assert exp.all_recipes()[0]["id"] == "abc123"
