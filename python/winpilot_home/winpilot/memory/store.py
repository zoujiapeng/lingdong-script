"""Thread-safe SQLite store for WinPilot Memory Graph."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .. import config
from ..utils.log import logger
from .types import NODE_KINDS


def _now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def _json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return default


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _clean_list(values: Any, max_items: int = 8, max_chars: int = 300) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text[:max_chars])
        if len(out) >= max_items:
            break
    return out


class MemoryStore:
    """Small SQLite graph store.

    The store is deliberately fail-soft: if SQLite init/query fails, methods
    degrade to empty memory and log warnings instead of crashing the agent.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.MEMORY_DB_PATH)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self.disabled = False
        self.fts_enabled = False
        self._open()

    # ------------------------------------------------------------------ init
    def _open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.path),
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._init_schema()
        except sqlite3.DatabaseError as exc:
            self.disabled = True
            self._conn = None
            logger.warning("memory graph disabled: sqlite init failed: %s", exc)
        except OSError as exc:
            self.disabled = True
            self._conn = None
            logger.warning("memory graph disabled: db path unavailable: %s", exc)

    def _init_schema(self) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_nodes (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    app TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    source TEXT NOT NULL DEFAULT '',
                    manual INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS memory_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_edges_from
                    ON memory_edges(from_id, kind);
                CREATE INDEX IF NOT EXISTS idx_memory_edges_to
                    ON memory_edges(to_id, kind);
                CREATE TABLE IF NOT EXISTS memory_anchors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'keyword',
                    value TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0
                );
                CREATE INDEX IF NOT EXISTS idx_memory_anchors_value
                    ON memory_anchors(value);
                CREATE TABLE IF NOT EXISTS memory_conditions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS memory_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT,
                    run_id TEXT NOT NULL DEFAULT '',
                    event TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    success INTEGER,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_attempts_node
                    ON memory_attempts(node_id, run_id);
                CREATE TABLE IF NOT EXISTS memory_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL DEFAULT '',
                    task TEXT NOT NULL DEFAULT '',
                    pack_text TEXT NOT NULL DEFAULT '',
                    selected_ids_json TEXT NOT NULL DEFAULT '[]',
                    scores_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                """
            )
        self._init_fts()
        self.set_meta("schema_version", "1")

    def _init_fts(self) -> None:
        if self._conn is None:
            return
        try:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(node_id UNINDEXED, title, summary, body, anchors)
                    """
                )
            self.fts_enabled = True
        except sqlite3.DatabaseError as exc:
            self.fts_enabled = False
            logger.warning("memory graph FTS unavailable; using LIKE search: %s", exc)

    # ------------------------------------------------------------------ utils
    @property
    def conn(self) -> sqlite3.Connection | None:
        if self.disabled:
            return None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None

    def _row_node(self, row: sqlite3.Row | None, hydrate: bool = True) -> dict[str, Any] | None:
        if row is None:
            return None
        node = dict(row)
        node["payload"] = _json_loads(node.pop("payload_json", "{}"), {})
        node["manual"] = bool(node.get("manual"))
        if hydrate:
            node["conditions"] = self.conditions(node["id"])
            node["evidence"] = self.evidence(node["id"])
            node["anchors"] = self.anchors(node["id"])
            node["artifacts"] = self.artifacts(node["id"])
        return node

    def _refresh_fts(self, node_id: str) -> None:
        if not self.fts_enabled or self.conn is None:
            return
        try:
            row = self.conn.execute(
                "SELECT title, summary, body FROM memory_nodes WHERE id=?",
                (node_id,),
            ).fetchone()
            if not row:
                self.conn.execute("DELETE FROM memory_fts WHERE node_id=?", (node_id,))
                return
            anchors = " ".join(
                r["value"] for r in self.conn.execute(
                    "SELECT value FROM memory_anchors WHERE node_id=?", (node_id,)
                ).fetchall()
            )
            self.conn.execute("DELETE FROM memory_fts WHERE node_id=?", (node_id,))
            self.conn.execute(
                "INSERT INTO memory_fts(node_id, title, summary, body, anchors) VALUES (?, ?, ?, ?, ?)",
                (node_id, row["title"], row["summary"], row["body"], anchors),
            )
        except sqlite3.DatabaseError as exc:
            self.fts_enabled = False
            logger.warning("memory graph FTS update failed; falling back: %s", exc)

    def _mark_disabled(self, exc: Exception) -> None:
        self.disabled = True
        logger.warning("memory graph disabled after sqlite error: %s", exc)

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: str = "") -> str:
        if self.conn is None:
            return default
        try:
            row = self.conn.execute("SELECT value FROM memory_meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return default

    def set_meta(self, key: str, value: str) -> None:
        if self.conn is None:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO memory_meta(key, value) VALUES (?, ?)",
                    (key, value),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    # ------------------------------------------------------------------ CRUD
    def add_node(
        self,
        kind: str,
        title: str,
        summary: str = "",
        body: str = "",
        *,
        app: str = "",
        status: str = "active",
        confidence: float = 0.5,
        payload: dict[str, Any] | None = None,
        source: str = "",
        manual: bool = False,
        node_id: str | None = None,
        expires_at: int | None = None,
        conditions: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        anchors: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        if self.conn is None:
            return {}
        node_id = node_id or new_id()
        kind = kind if kind in NODE_KINDS else "fact"
        conds = _clean_list(conditions, 8)
        evid = _clean_list(evidence, 8)
        if kind == "observation":
            status = "provisional"
            expires_at = expires_at or (_now() + 3600)
        if kind == "skill" and (not conds or not evid):
            kind = "procedure"
            if status == "active":
                status = "provisional"
        ts = _now()
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_nodes(
                        id, kind, title, summary, body, status, confidence, app,
                        created_at, updated_at, expires_at, source, manual, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM memory_nodes WHERE id=?), ?),
                        ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        kind,
                        (title or "")[:500],
                        (summary or "")[:1000],
                        (body or "")[:20000],
                        status,
                        float(confidence),
                        (app or "")[:200],
                        node_id,
                        ts,
                        ts,
                        expires_at,
                        (source or "")[:500],
                        1 if manual else 0,
                        _json_dumps(payload or {}),
                    ),
                )
                self._replace_child_rows("memory_conditions", node_id, conds)
                self._replace_child_rows("memory_evidence", node_id, evid)
                self._replace_anchors(node_id, _clean_list(anchors, 12, 200))
                self._refresh_fts(node_id)
            return self.get_node(node_id) or {}
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return {}

    def _replace_child_rows(self, table: str, node_id: str, values: list[str]) -> None:
        assert self.conn is not None
        self.conn.execute(f"DELETE FROM {table} WHERE node_id=?", (node_id,))
        for text in values:
            self.conn.execute(
                f"INSERT INTO {table}(node_id, text, required) VALUES (?, ?, 1)",
                (node_id, text),
            )

    def _replace_anchors(self, node_id: str, values: list[str]) -> None:
        assert self.conn is not None
        self.conn.execute("DELETE FROM memory_anchors WHERE node_id=?", (node_id,))
        for value in _clean_list(values, 12, 200):
            self.conn.execute(
                "INSERT INTO memory_anchors(node_id, kind, value, weight) VALUES (?, 'keyword', ?, 1.0)",
                (node_id, value),
            )

    def get_node(self, node_id: str, hydrate: bool = True) -> dict[str, Any] | None:
        if self.conn is None or not node_id:
            return None
        try:
            row = self.conn.execute(
                "SELECT * FROM memory_nodes WHERE id=?",
                (node_id,),
            ).fetchone()
            return self._row_node(row, hydrate=hydrate)
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return None

    def list_nodes(
        self,
        kinds: Iterable[str] | None = None,
        include_inactive: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        params: list[Any] = []
        where: list[str] = []
        if kinds:
            ks = [k for k in kinds if k in NODE_KINDS]
            if ks:
                where.append("kind IN (%s)" % ",".join("?" for _ in ks))
                params.extend(ks)
        if not include_inactive:
            where.append("status NOT IN ('retired', 'superseded')")
            where.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_now())
        sql = "SELECT * FROM memory_nodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
            return [self._row_node(row) for row in rows if row is not None]
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    def update_node(self, node_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        if self.conn is None or not patch:
            return self.get_node(node_id)
        allowed = {
            "kind",
            "title",
            "summary",
            "body",
            "status",
            "confidence",
            "app",
            "expires_at",
            "source",
            "manual",
            "payload",
        }
        set_parts: list[str] = []
        params: list[Any] = []
        for key, value in patch.items():
            if key not in allowed:
                continue
            col = "payload_json" if key == "payload" else key
            if key == "payload":
                value = _json_dumps(value)
            elif key == "manual":
                value = 1 if value else 0
            set_parts.append(f"{col}=?")
            params.append(value)
        try:
            with self._lock, self.conn:
                if set_parts:
                    set_parts.append("updated_at=?")
                    params.append(_now())
                    params.append(node_id)
                    self.conn.execute(
                        f"UPDATE memory_nodes SET {', '.join(set_parts)} WHERE id=?",
                        params,
                    )
                if "conditions" in patch:
                    self._replace_child_rows(
                        "memory_conditions", node_id, _clean_list(patch.get("conditions"), 8)
                    )
                if "evidence" in patch:
                    self._replace_child_rows(
                        "memory_evidence", node_id, _clean_list(patch.get("evidence"), 8)
                    )
                if "anchors" in patch:
                    self._replace_anchors(node_id, patch.get("anchors") or [])
                self._refresh_fts(node_id)
            return self.get_node(node_id)
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return None

    def delete_node(self, node_id: str) -> bool:
        if self.conn is None:
            return False
        try:
            with self._lock, self.conn:
                cur = self.conn.execute("DELETE FROM memory_nodes WHERE id=?", (node_id,))
                for table in (
                    "memory_edges",
                    "memory_anchors",
                    "memory_conditions",
                    "memory_evidence",
                    "memory_attempts",
                    "memory_artifacts",
                ):
                    if table == "memory_edges":
                        self.conn.execute(
                            "DELETE FROM memory_edges WHERE from_id=? OR to_id=?",
                            (node_id, node_id),
                        )
                    else:
                        self.conn.execute(f"DELETE FROM {table} WHERE node_id=?", (node_id,))
                if self.fts_enabled:
                    self.conn.execute("DELETE FROM memory_fts WHERE node_id=?", (node_id,))
            return cur.rowcount > 0
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return False

    def retire_node(self, node_id: str, status: str = "retired") -> bool:
        return bool(self.update_node(node_id, {"status": status}))

    # ---------------------------------------------------------------- relations
    def add_edge(
        self,
        from_id: str,
        to_id: str,
        kind: str,
        weight: float = 1.0,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.conn is None:
            return {}
        try:
            with self._lock, self.conn:
                cur = self.conn.execute(
                    """
                    INSERT INTO memory_edges(from_id, to_id, kind, weight, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (from_id, to_id, kind, float(weight), _json_dumps(payload or {}), _now()),
                )
                edge_id = int(cur.lastrowid)
            return {
                "id": edge_id,
                "from_id": from_id,
                "to_id": to_id,
                "kind": kind,
                "weight": weight,
                "payload": payload or {},
            }
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return {}

    def related(
        self,
        node_id: str,
        kinds: Iterable[str] | None = None,
        direction: str = "both",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        params: list[Any] = []
        clauses: list[str] = []
        if direction in ("out", "both"):
            clauses.append("from_id=?")
            params.append(node_id)
        if direction in ("in", "both"):
            clauses.append("to_id=?")
            params.append(node_id)
        where = "(" + " OR ".join(clauses or ["from_id=?"]) + ")"
        if not clauses:
            params.append(node_id)
        if kinds:
            ks = list(kinds)
            where += " AND kind IN (%s)" % ",".join("?" for _ in ks)
            params.extend(ks)
        params.append(limit)
        try:
            rows = self.conn.execute(
                f"SELECT * FROM memory_edges WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            out = []
            for row in rows:
                edge = dict(row)
                edge["payload"] = _json_loads(edge.pop("payload_json", "{}"), {})
                other_id = edge["to_id"] if edge["from_id"] == node_id else edge["from_id"]
                edge["node"] = self.get_node(other_id)
                out.append(edge)
            return out
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    # ---------------------------------------------------------------- children
    def add_anchor(self, node_id: str, value: str, kind: str = "keyword", weight: float = 1.0) -> None:
        if self.conn is None or not value:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    "INSERT INTO memory_anchors(node_id, kind, value, weight) VALUES (?, ?, ?, ?)",
                    (node_id, kind, value[:200], float(weight)),
                )
                self._refresh_fts(node_id)
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def add_condition(self, node_id: str, text: str, required: bool = True) -> None:
        if self.conn is None or not text:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    "INSERT INTO memory_conditions(node_id, text, required) VALUES (?, ?, ?)",
                    (node_id, text[:300], 1 if required else 0),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def add_evidence(self, node_id: str, text: str, required: bool = True) -> None:
        if self.conn is None or not text:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    "INSERT INTO memory_evidence(node_id, text, required) VALUES (?, ?, ?)",
                    (node_id, text[:300], 1 if required else 0),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def conditions(self, node_id: str) -> list[str]:
        return self._strings("memory_conditions", node_id)

    def evidence(self, node_id: str) -> list[str]:
        return self._strings("memory_evidence", node_id)

    def anchors(self, node_id: str) -> list[str]:
        return self._strings("memory_anchors", node_id, column="value")

    def _strings(self, table: str, node_id: str, column: str = "text") -> list[str]:
        if self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                f"SELECT {column} FROM {table} WHERE node_id=? ORDER BY id",
                (node_id,),
            ).fetchall()
            return [str(row[column]) for row in rows if str(row[column]).strip()]
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    def add_artifact(
        self,
        node_id: str,
        kind: str,
        uri: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.conn is None or not uri:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    """
                    INSERT INTO memory_artifacts(node_id, kind, uri, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (node_id, kind, uri, _json_dumps(payload or {}), _now()),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def artifacts(self, node_id: str) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                "SELECT * FROM memory_artifacts WHERE node_id=? ORDER BY id",
                (node_id,),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["payload"] = _json_loads(item.pop("payload_json", "{}"), {})
                out.append(item)
            return out
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    # ---------------------------------------------------------------- attempts
    def record_attempt(
        self,
        node_id: str | None,
        event: str,
        *,
        run_id: str = "",
        status: str = "",
        success: bool | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self.conn is None:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    """
                    INSERT INTO memory_attempts(node_id, run_id, event, status, success, detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id or "",
                        run_id or "",
                        event,
                        status,
                        None if success is None else (1 if success else 0),
                        _json_dumps(detail or {}),
                        _now(),
                    ),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def attempts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                "SELECT * FROM memory_attempts WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [self._attempt_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    def attempts_for_node(self, node_id: str) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        try:
            rows = self.conn.execute(
                "SELECT * FROM memory_attempts WHERE node_id=? ORDER BY id",
                (node_id,),
            ).fetchall()
            return [self._attempt_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    @staticmethod
    def _attempt_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["success"] = None if item["success"] is None else bool(item["success"])
        item["detail"] = _json_loads(item.pop("detail_json", "{}"), {})
        return item

    # ---------------------------------------------------------------- packs/search
    def record_pack(
        self,
        task: str,
        pack_text: str,
        selected_ids: list[str],
        scores: dict[str, Any],
        run_id: str = "",
    ) -> None:
        if self.conn is None:
            return
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    """
                    INSERT INTO memory_packs(run_id, task, pack_text, selected_ids_json, scores_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id or "",
                        task[:500],
                        pack_text[:10000],
                        _json_dumps(selected_ids),
                        _json_dumps(scores),
                        _now(),
                    ),
                )
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)

    def search(self, query: str, limit: int = 20) -> list[tuple[dict[str, Any], float]]:
        if self.conn is None:
            return []
        query = (query or "").strip()
        if not query:
            return [(node, 0.0) for node in self.list_nodes(limit=limit)]
        if self.fts_enabled:
            rows = self._fts_search(query, limit)
            if rows:
                return rows
        return self._like_search(query, limit)

    def _fts_search(self, query: str, limit: int) -> list[tuple[dict[str, Any], float]]:
        if self.conn is None:
            return []
        terms = [t for t in query.replace('"', " ").split() if t]
        fts_query = " ".join(f"{t}*" for t in terms) if terms else f'"{query}"'
        try:
            rows = self.conn.execute(
                """
                SELECT n.*, bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_nodes n ON n.id = memory_fts.node_id
                WHERE memory_fts MATCH ?
                  AND n.status NOT IN ('retired', 'superseded')
                  AND (n.expires_at IS NULL OR n.expires_at > ?)
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, _now(), limit),
            ).fetchall()
            return [
                (self._row_node(row) or {}, max(0.1, 10.0 - float(row["rank"] or 0.0)))
                for row in rows
            ]
        except sqlite3.DatabaseError:
            self.fts_enabled = False
            return []

    def _like_search(self, query: str, limit: int) -> list[tuple[dict[str, Any], float]]:
        if self.conn is None:
            return []
        needle = f"%{query.lower()}%"
        try:
            rows = self.conn.execute(
                """
                SELECT DISTINCT n.*
                FROM memory_nodes n
                LEFT JOIN memory_anchors a ON a.node_id = n.id
                WHERE n.status NOT IN ('retired', 'superseded')
                  AND (n.expires_at IS NULL OR n.expires_at > ?)
                  AND (
                    lower(n.title) LIKE ? OR lower(n.summary) LIKE ?
                    OR lower(n.body) LIKE ? OR lower(n.app) LIKE ?
                    OR lower(a.value) LIKE ? OR lower(n.payload_json) LIKE ?
                  )
                ORDER BY n.updated_at DESC
                LIMIT ?
                """,
                (_now(), needle, needle, needle, needle, needle, needle, limit),
            ).fetchall()
            return [(self._row_node(row) or {}, 1.0) for row in rows]
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
            return []

    def clear(self) -> None:
        if self.conn is None:
            return
        try:
            with self._lock, self.conn:
                for table in (
                    "memory_edges",
                    "memory_anchors",
                    "memory_conditions",
                    "memory_evidence",
                    "memory_attempts",
                    "memory_artifacts",
                    "memory_packs",
                    "memory_nodes",
                ):
                    self.conn.execute(f"DELETE FROM {table}")
                if self.fts_enabled:
                    self.conn.execute("DELETE FROM memory_fts")
        except sqlite3.DatabaseError as exc:
            self._mark_disabled(exc)
