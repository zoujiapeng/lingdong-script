"""Feedback rules for Memory Graph."""
from __future__ import annotations

from typing import Iterable

from .store import MemoryStore
from .types import ACTION_KINDS

EVICT_MIN_USES = 3


def _payload_scores(node: dict) -> tuple[int, int]:
    payload = node.get("payload") or {}
    return int(payload.get("uses") or 0), int(payload.get("successes") or 0)


def apply_feedback_to_ids(
    store: MemoryStore,
    node_ids: Iterable[str],
    success: bool,
    *,
    run_id: str = "",
    event: str = "feedback",
) -> None:
    seen = []
    for node_id in node_ids:
        rid = str(node_id or "").strip()
        if rid and rid not in seen:
            seen.append(rid)
    for node_id in seen:
        node = store.get_node(node_id)
        if not node:
            continue
        payload = dict(node.get("payload") or {})
        uses, successes = _payload_scores(node)
        uses += 1
        if success:
            successes += 1
        payload["uses"] = uses
        payload["successes"] = successes
        status = node.get("status") or "active"
        confidence = float(node.get("confidence") or 0.5)
        if success:
            status = "active"
            confidence = min(0.98, confidence + 0.1)
        else:
            confidence = max(0.05, confidence - 0.15)
            if (
                uses >= EVICT_MIN_USES
                and successes == 0
                and node.get("kind") in ACTION_KINDS
                and not node.get("manual")
            ):
                status = "retired"
        store.record_attempt(
            node_id,
            event,
            run_id=run_id,
            status="success" if success else "failure",
            success=success,
        )
        store.update_node(
            node_id,
            {"payload": payload, "status": status, "confidence": confidence},
        )


def apply_run_feedback(run_id: str, status: str, *, store: MemoryStore | None = None) -> None:
    """Apply final run feedback from recorded attempts.

    packed/retrieved attempts are intentionally ignored. replay_failed remains
    negative even if live fallback later completed the task.
    """
    if not run_id:
        return
    memory_store = store or MemoryStore()
    attempts = memory_store.attempts_for_run(run_id)
    replay_ok = []
    replay_failed = []
    used = []
    for attempt in attempts:
        node_id = str(attempt.get("node_id") or "")
        if not node_id:
            continue
        event = attempt.get("event")
        if event == "replay_ok":
            replay_ok.append(node_id)
        elif event == "replay_failed":
            replay_failed.append(node_id)
        elif event == "used":
            used.append(node_id)
    if status == "done":
        apply_feedback_to_ids(memory_store, replay_ok, True, run_id=run_id, event="run_feedback")
        failed_only = [rid for rid in replay_failed if rid not in replay_ok]
        apply_feedback_to_ids(memory_store, failed_only, False, run_id=run_id, event="run_feedback")
    else:
        apply_feedback_to_ids(memory_store, replay_ok + replay_failed + used, False, run_id=run_id, event="run_feedback")
