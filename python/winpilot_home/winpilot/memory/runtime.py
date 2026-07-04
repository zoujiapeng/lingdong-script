"""Runtime entry point for preparing Memory Packs."""
from __future__ import annotations

from typing import Any

from .. import config
from .migration import migrate_json_if_needed
from .packer import pack
from .retriever import recall
from .store import MemoryStore
from .types import MemoryPack


def prepare_run_memory(
    task: str,
    runtime_profile: dict[str, Any] | None = None,
    session_context: Any = None,
    run_id: str = "",
    *,
    store: MemoryStore | None = None,
) -> MemoryPack:
    """Build a compact Memory Pack for a run.

    The pipeline is TaskFrame parsing -> hard filters -> recall -> graph
    expansion/rerank -> pack. Failures degrade to an empty pack.
    """
    runtime_profile = runtime_profile or {}
    memory_store = store or MemoryStore(config.MEMORY_DB_PATH)
    if memory_store.disabled:
        return MemoryPack(text="", selected_ids=[], scores={}, candidates=[])
    try:
        migrate_json_if_needed(memory_store, config.MEMORY_PATH)
        notes_only = bool(runtime_profile.get("notes_only"))
        _frame, candidates = recall(
            task,
            memory_store,
            runtime_profile,
            session_context,
            notes_only=notes_only,
            limit=12,
        )
        return pack(task, candidates, memory_store, run_id=run_id)
    except Exception:
        # Long-term memory must never block the agent.
        return MemoryPack(text="", selected_ids=[], scores={}, candidates=[])
