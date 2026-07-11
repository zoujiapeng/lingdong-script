from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))


def test_execution_store_encrypts_and_migrates_vault_values(tmp_path, monkeypatch):
    from engine import store as store_mod
    from engine.secure_store import PREFIX

    db_path = tmp_path / "store.db"
    monkeypatch.setenv("LOBSTER_DB", str(db_path))
    store_mod._DB_PATH = None
    store_mod._store = None
    store_mod.ExecutionStore._instance = None

    store = store_mod.ExecutionStore()
    store.vault_set("api", "secret-value")

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT value FROM vault WHERE key = 'api'").fetchone()[0]
        assert raw.startswith(PREFIX)
        assert "secret-value" not in raw
        conn.execute("INSERT INTO vault(key, value, created_at) VALUES ('legacy', 'plain', 0)")
        conn.commit()

    store._migrate_plaintext_vault(store._get_conn())
    assert store.vault_get("api") == "secret-value"
    assert store.vault_get("legacy") == "plain"

    with sqlite3.connect(db_path) as conn:
        migrated = conn.execute("SELECT value FROM vault WHERE key = 'legacy'").fetchone()[0]
        assert migrated.startswith(PREFIX)
        assert migrated != "plain"
