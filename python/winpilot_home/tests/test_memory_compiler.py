from types import SimpleNamespace


def test_automatic_run_ingestion_still_respects_disabled_switch(monkeypatch):
    from winpilot.memory import compiler

    monkeypatch.setattr(compiler, "_enabled", lambda: False)

    assert compiler.ingest_run(
        SimpleNamespace(task="do not persist", run_id="disabled-run")
    ) == {}


def test_run_ingestion_is_idempotent_and_binds_script_hash(tmp_path, monkeypatch):
    from winpilot import config
    from winpilot.memory import compiler
    from winpilot.memory.store import MemoryStore
    from winpilot.recorder import trace

    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "experience_memory.json")
    # Explicit compilation/recovery must not depend on a process configured to
    # disable automatic experience writes for cold-start/no-memory validation.
    monkeypatch.setattr(compiler, "_enabled", lambda: False)
    path = tmp_path / "run-1.automation.json"
    path.write_text('{"format":"automation-ir/1","steps":[]}', encoding="utf-8")
    monkeypatch.setattr(trace, "export_automation_ir", lambda run_id: path)
    runner = SimpleNamespace(
        task="open settings",
        run_id="run-1",
        status="done",
        result_text="done",
        _steps_log=["open settings"],
        _is_coding=False,
        _done_verified=True,
        _done_unverified=False,
        _had_verified_action=False,
        session=SimpleNamespace(snapshot=SimpleNamespace(title="Settings")),
        usage_total={},
        runtime_profile=SimpleNamespace(to_dict=lambda: {"stage_type": ""}),
    )
    store = MemoryStore(tmp_path / "memory.db")

    first = compiler.ingest_run(runner, store=store)
    second = compiler.ingest_run(runner, store=store)

    assert first["recipe"]["id"] == second["recipe"]["id"]
    nodes = [node for node in store.list_nodes(include_inactive=True) if node.get("source") == "run-1"]
    assert len([node for node in nodes if node["kind"] == "episode"]) == 1
    assert len([node for node in nodes if node["kind"] == "skill"]) == 1
    payload = second["recipe"]["payload"]
    assert payload["verification_state"] == "source_run_verified"
    assert len(payload["script_sha256"]) == 64


def test_run_ingestion_recovers_when_only_episode_was_written(tmp_path, monkeypatch):
    from winpilot import config
    from winpilot.memory import compiler
    from winpilot.memory.store import MemoryStore
    from winpilot.recorder import trace

    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "experience_memory.json")
    path = tmp_path / "partial.automation.json"
    path.write_text('{"format":"automation-ir/1","steps":[]}', encoding="utf-8")
    monkeypatch.setattr(trace, "export_automation_ir", lambda run_id: path)
    store = MemoryStore(tmp_path / "memory.db")
    store.add_node("episode", "partial run", source="run-partial")
    runner = SimpleNamespace(
        task="finish partial run",
        run_id="run-partial",
        status="done",
        result_text="done",
        _steps_log=["finish"],
        _is_coding=False,
        _done_verified=True,
        _done_unverified=False,
        _had_verified_action=False,
        session=SimpleNamespace(snapshot=SimpleNamespace(title="App")),
        usage_total={},
        runtime_profile={},
    )

    created = compiler.ingest_run(runner, store=store)

    assert created["recipe"]["source"] == "run-partial"
    nodes = [node for node in store.list_nodes(include_inactive=True) if node.get("source") == "run-partial"]
    assert len([node for node in nodes if node["kind"] == "episode"]) == 1
    assert len([node for node in nodes if node["kind"] == "skill"]) == 1
