"""
执行状态持久化存储: SQLite 后端
支持执行 checkpoint / resume / telemetry / vault / learned targets
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .secure_store import is_protected, protect_text, unprotect_text


_DB_PATH = None


def _get_db_path() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = os.environ.get(
            "LOBSTER_DB",
            str(Path.home() / ".lobster" / "store.db"),
        )
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    return _DB_PATH


@dataclass
class Checkpoint:
    task_id: str
    dsl: str
    state: str                  # running | paused | finished | error
    completed_indices: list     # 已完成的节点索引路径
    variables: dict             # 变量快照
    scope_count: int            # 作用域层数
    node_info: str              # 当前节点描述
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class TelemetryEntry:
    feature_name: str
    duration_ms: float
    success: bool
    error: str = ""
    strategy: str = ""
    timestamp: float = 0.0


@dataclass
class LearnedTarget:
    target_id: str
    app: dict = field(default_factory=dict)
    action: str = "click"
    template_path: str = ""
    ocr: dict = field(default_factory=dict)
    uia: dict = field(default_factory=dict)
    offset: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float = 0.0


class ExecutionStore:
    """
    SQLite 持久化存储

    表结构:
      checkpoints  — 执行断点
      telemetry    — 功能调用统计
      vault        — 加密敏感信息
      learned_targets — 已学习的本地目标
    """

    _instance: Optional["ExecutionStore"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._local = threading.local()
        return cls._instance

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(_get_db_path())
            self._local.conn.row_factory = sqlite3.Row
            self._init_db()
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                task_id TEXT PRIMARY KEY,
                dsl TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'running',
                completed_indices TEXT DEFAULT '[]',
                variables TEXT DEFAULT '{}',
                scope_count INTEGER DEFAULT 1,
                node_info TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_name TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                success INTEGER NOT NULL,
                error TEXT DEFAULT '',
                strategy TEXT DEFAULT '',
                timestamp REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vault (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learned_targets (
                target_id TEXT PRIMARY KEY,
                app TEXT DEFAULT '{}',
                action TEXT DEFAULT 'click',
                template_path TEXT DEFAULT '',
                ocr TEXT DEFAULT '{}',
                uia TEXT DEFAULT '{}',
                offset TEXT DEFAULT '{}',
                context TEXT DEFAULT '{}',
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_used_at REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_telemetry_feature ON telemetry(feature_name);
            CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry(timestamp);
            CREATE INDEX IF NOT EXISTS idx_learned_targets_updated ON learned_targets(updated_at);
        """)
        conn.commit()
        self._migrate_plaintext_vault(conn)

    @staticmethod
    def _migrate_plaintext_vault(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT key, value FROM vault").fetchall()
        migrated = False
        for row in rows:
            stored = str(row["value"])
            if is_protected(stored):
                continue
            conn.execute(
                "UPDATE vault SET value = ? WHERE key = ?",
                (protect_text(stored), row["key"]),
            )
            migrated = True
        if migrated:
            conn.commit()

    # ── Checkpoint API ──────────────────────────────────────────

    def save_checkpoint(self, cp: Checkpoint):
        conn = self._get_conn()
        now = time.time()
        conn.execute(
            """INSERT OR REPLACE INTO checkpoints
               (task_id, dsl, state, completed_indices, variables, scope_count, node_info, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cp.task_id, cp.dsl, cp.state,
                json.dumps(cp.completed_indices, ensure_ascii=False),
                json.dumps(cp.variables, ensure_ascii=False),
                cp.scope_count, cp.node_info,
                cp.created_at or now, now,
            ),
        )
        conn.commit()

    def get_checkpoint(self, task_id: str) -> Optional[Checkpoint]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM checkpoints WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return Checkpoint(
            task_id=row["task_id"],
            dsl=row["dsl"],
            state=row["state"],
            completed_indices=json.loads(row["completed_indices"]),
            variables=json.loads(row["variables"]),
            scope_count=row["scope_count"],
            node_info=row["node_info"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_checkpoints(self, limit: int = 20) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT task_id, state, node_info, created_at, updated_at FROM checkpoints ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_checkpoint(self, task_id: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM checkpoints WHERE task_id = ?", (task_id,))
        conn.commit()

    # ── Telemetry API ───────────────────────────────────────────

    def log_telemetry(self, entry: TelemetryEntry):
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO telemetry (feature_name, duration_ms, success, error, strategy, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.feature_name, entry.duration_ms, 1 if entry.success else 0,
             entry.error, entry.strategy, entry.timestamp or time.time()),
        )
        conn.commit()

    def get_telemetry(self, feature_name: str, limit: int = 100) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM telemetry WHERE feature_name = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (feature_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_feature_stats(self, feature_name: str) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(success) as successes,
                      AVG(CASE WHEN success THEN duration_ms END) as avg_duration_ms
               FROM telemetry WHERE feature_name = ?""",
            (feature_name,),
        ).fetchone()
        if row and row["total"]:
            total = row["total"]
            successes = row["successes"] or 0
            return {
                "total": total,
                "successes": successes,
                "failures": total - successes,
                "success_rate": round(successes / total, 3) if total else 0,
                "avg_duration_ms": round(row["avg_duration_ms"], 1) if row["avg_duration_ms"] else 0,
            }
        return {"total": 0, "successes": 0, "failures": 0, "success_rate": 0, "avg_duration_ms": 0}

    def get_all_stats(self) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT COUNT(*) as total_calls,
                      SUM(success) as total_successes,
                      AVG(duration_ms) as avg_duration
               FROM telemetry""",
        ).fetchone()
        return {
            "total_calls": row["total_calls"] or 0,
            "total_successes": row["total_successes"] or 0,
            "avg_duration_ms": round(row["avg_duration"] or 0, 1),
        }

    # ── Learned Target API ────────────────────────────────────────

    def save_learned_target(self, target: LearnedTarget | dict) -> dict:
        conn = self._get_conn()
        now = time.time()
        data = asdict(target) if isinstance(target, LearnedTarget) else dict(target)
        target_id = str(data.get("target_id") or "").strip()
        if not target_id:
            raise ValueError("target_id is required")
        existing = self.get_learned_target(target_id)
        created_at = data.get("created_at") or (existing or {}).get("created_at") or now
        conn.execute(
            """INSERT OR REPLACE INTO learned_targets
               (target_id, app, action, template_path, ocr, uia, offset, context,
                success_count, failure_count, created_at, updated_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target_id,
                json.dumps(data.get("app") or {}, ensure_ascii=False),
                data.get("action") or "click",
                data.get("template_path") or "",
                json.dumps(data.get("ocr") or {}, ensure_ascii=False),
                json.dumps(data.get("uia") or {}, ensure_ascii=False),
                json.dumps(data.get("offset") or {}, ensure_ascii=False),
                json.dumps(data.get("context") or {}, ensure_ascii=False),
                int(data.get("success_count", (existing or {}).get("success_count", 0)) or 0),
                int(data.get("failure_count", (existing or {}).get("failure_count", 0)) or 0),
                created_at,
                now,
                float(data.get("last_used_at", (existing or {}).get("last_used_at", 0)) or 0),
            ),
        )
        conn.commit()
        return self.get_learned_target(target_id) or {}

    def get_learned_target(self, target_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM learned_targets WHERE target_id = ?", (target_id,)).fetchone()
        return self._row_to_learned_target(row) if row else None

    def list_learned_targets(self, app: str = "", limit: int = 100) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM learned_targets ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        items = [self._row_to_learned_target(r) for r in rows]
        if app:
            needle = app.lower()
            items = [t for t in items if needle in json.dumps(t.get("app") or {}, ensure_ascii=False).lower()]
        return items

    def update_target_stats(self, target_id: str, success: bool):
        conn = self._get_conn()
        field = "success_count" if success else "failure_count"
        conn.execute(
            f"UPDATE learned_targets SET {field} = {field} + 1, last_used_at = ?, updated_at = ? WHERE target_id = ?",
            (time.time(), time.time(), target_id),
        )
        conn.commit()

    def delete_learned_target(self, target_id: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM learned_targets WHERE target_id = ?", (target_id,))
        conn.commit()

    def _row_to_learned_target(self, row: sqlite3.Row) -> dict:
        return {
            "target_id": row["target_id"],
            "app": json.loads(row["app"] or "{}"),
            "action": row["action"],
            "template_path": row["template_path"],
            "ocr": json.loads(row["ocr"] or "{}"),
            "uia": json.loads(row["uia"] or "{}"),
            "offset": json.loads(row["offset"] or "{}"),
            "context": json.loads(row["context"] or "{}"),
            "success_count": row["success_count"],
            "failure_count": row["failure_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
        }

    # ── Vault API ───────────────────────────────────────────────

    def vault_set(self, key: str, value: str):
        """Store a secret encrypted for the current Windows user via DPAPI."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO vault (key, value, created_at) VALUES (?, ?, ?)",
            (key, protect_text(value), time.time()),
        )
        conn.commit()

    def vault_get(self, key: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM vault WHERE key = ?", (key,)).fetchone()
        return unprotect_text(row["value"]) if row else None

    def vault_delete(self, key: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM vault WHERE key = ?", (key,))
        conn.commit()

    def vault_list_keys(self) -> list:
        conn = self._get_conn()
        rows = conn.execute("SELECT key FROM vault").fetchall()
        return [r["key"] for r in rows]


# 全局单例
_store: Optional[ExecutionStore] = None


def get_store() -> ExecutionStore:
    global _store
    if _store is None:
        _store = ExecutionStore()
    return _store
