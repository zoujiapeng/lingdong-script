"""
Memory — short-term (recent steps) and long-term (persistent knowledge).
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "agent_memory"


def _ensure_dir():
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


class ShortTermMemory:
    """Sliding window of recent steps (in-memory only)."""

    def __init__(self, max_items: int = 10):
        self._items: list[dict] = []
        self._max = max_items

    def push(self, item: dict):
        self._items.append(item)
        if len(self._items) > self._max:
            self._items.pop(0)

    def get_all(self) -> list[dict]:
        return list(self._items)

    def clear(self):
        self._items.clear()


class LongTermMemory:
    """Persistent key-value memory stored on disk."""

    def __init__(self, namespace: str = "agent"):
        _ensure_dir()
        self._path = _MEMORY_DIR / f"{namespace}.json"
        self._store: dict = {}
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                self._store = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._store = {}

    def _save(self):
        _ensure_dir()
        try:
            self._path.write_text(
                json.dumps(self._store, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def store(self, key: str, value: str, metadata: dict = None):
        self._store[key] = {
            "value": value,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        self._save()

    def query(self, goal: str, k: int = 3) -> list[str]:
        """Simple keyword match — returns last k relevant entries."""
        words = goal.lower().split()
        scored = []
        for key, entry in self._store.items():
            score = sum(1 for w in words if w in key.lower())
            if score > 0:
                scored.append((score, entry["value"]))
        scored.sort(key=lambda x: (-x[0], -self._store.get(x[1], {}).get("timestamp", 0)))
        return [v for _, v in scored[:k]]

    def clear(self):
        self._store.clear()
        self._save()
