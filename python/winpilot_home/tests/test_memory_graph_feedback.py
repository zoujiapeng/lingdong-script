def test_packed_but_unused_does_not_score_success(tmp_path):
    from winpilot.memory.runtime import prepare_run_memory
    from winpilot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    node = store.add_node(
        "skill",
        "open calculator",
        body="launch calculator",
        payload={"steps": ["launch calculator"], "script": "calc.wps.json", "uses": 0, "successes": 0},
        conditions=["same calculator workflow"],
        evidence=["calculator window visible"],
        anchors=["calculator"],
    )

    pack = prepare_run_memory("open calculator", store=store, run_id="run-1")
    assert node["id"] in pack.selected_ids
    unchanged = store.get_node(node["id"])
    assert unchanged["payload"].get("uses") == 0
    assert unchanged["payload"].get("successes") == 0


def test_replay_failed_done_does_not_add_success(tmp_path):
    from winpilot.memory.feedback import apply_run_feedback
    from winpilot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    node = store.add_node(
        "skill",
        "open calculator",
        body="launch calculator",
        payload={"steps": ["launch calculator"], "script": "calc.wps.json", "uses": 0, "successes": 0},
        conditions=["same calculator workflow"],
        evidence=["calculator window visible"],
        anchors=["calculator"],
    )
    store.record_attempt(node["id"], "used", run_id="run-1")
    store.record_attempt(node["id"], "replay_failed", run_id="run-1", success=False)

    apply_run_feedback("run-1", "done", store=store)

    updated = store.get_node(node["id"])
    assert updated["payload"]["uses"] == 1
    assert updated["payload"]["successes"] == 0

    for i in range(2, 4):
        store.record_attempt(node["id"], "replay_failed", run_id=f"run-{i}", success=False)
        apply_run_feedback(f"run-{i}", "fail", store=store)

    assert store.get_node(node["id"])["status"] == "retired"
