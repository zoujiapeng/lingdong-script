import sqlite3


def test_memory_store_schema_crud_edges_and_fallback(tmp_path):
    from winpilot.memory.store import MemoryStore

    db = tmp_path / "memory.db"
    store = MemoryStore(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
    assert "memory_nodes" in tables
    assert "memory_edges" in tables
    assert "memory_attempts" in tables
    assert "memory_packs" in tables

    node = store.add_node(
        "fact",
        "Use pnpm for acme",
        summary="Package manager note",
        body="pnpm install",
        conditions=["task is about acme"],
        evidence=["check package.json"],
        anchors=["acme", "pnpm"],
        manual=True,
    )

    loaded = store.get_node(node["id"])
    assert loaded["conditions"] == ["task is about acme"]
    assert loaded["evidence"] == ["check package.json"]
    assert "pnpm" in loaded["anchors"]

    updated = store.update_node(node["id"], {"summary": "Updated", "confidence": 0.8})
    assert updated["summary"] == "Updated"
    assert updated["confidence"] == 0.8

    other = store.add_node("warning", "Do not trust stale login state")
    edge = store.add_edge(other["id"], node["id"], "warning_for")
    assert edge["kind"] == "warning_for"
    related = store.related(node["id"], {"warning_for"}, direction="both")
    assert related[0]["node"]["id"] == other["id"]

    downgraded = store.add_node("skill", "Skill without guards")
    assert downgraded["kind"] == "procedure"
    assert downgraded["status"] == "provisional"

    store.fts_enabled = False
    hits = store.search("pnpm")
    assert hits
    assert hits[0][0]["id"] == node["id"]

    assert store.retire_node(node["id"])
    assert store.get_node(node["id"])["status"] == "retired"
    assert store.delete_node(node["id"])
    assert store.get_node(node["id"]) is None


def test_memory_store_redacts_secret_like_values(tmp_path):
    from winpilot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    node = store.add_node(
        "fact",
        "api_key=sk-secretsecretsecret should not persist",
        body="Bearer abcdefghijklmnopqrstuvwxyz",
        payload={"api_key": "sk-secretsecretsecret", "nested": {"token": "abc123456789"}},
        conditions=["password=hunter2 should be redacted"],
        evidence=["authorization: Bearer abcdefghijklmnopqrstuvwxyz"],
    )
    store.record_attempt(
        node["id"],
        "used",
        detail={"secret": "abc123456789", "note": "token=abc123456789"},
    )

    loaded = store.get_node(node["id"])
    attempts = store.attempts_for_node(node["id"])
    combined = str(loaded) + str(attempts)

    assert "sk-secretsecretsecret" not in combined
    assert "abcdefghijklmnopqrstuvwxyz" not in combined
    assert "abc123456789" not in combined
    assert "[REDACTED" in combined
