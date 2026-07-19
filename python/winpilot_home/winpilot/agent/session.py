"""Session memory: cross-task continuity within one server lifetime.

The agent runs each task as a fresh AgentRunner with empty history, so a
follow-up like "再来一次 / 把它关掉 / 刚才那个" has no antecedent and the agent
acts as if starting a brand-new conversation. This module keeps a short log of
recently finished tasks (this session only) so referential commands resolve.

Process-lifetime singleton = one "session"; a server restart clears it, which
matches the intuitive notion of a session boundary. Not persisted to disk.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

_MAX_ENTRIES = 8
_RECENT_FOR_PROMPT = 3
_SUMMARY_CHARS = 160


@dataclass(frozen=True)
class SessionEntry:
    task: str
    status: str
    result: str
    run_id: str
    intent: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)


class SessionMemory:
    """Thread-safe ring of recent finished tasks (newest last)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[SessionEntry] = []
        self._generation = 0

    def append(
        self,
        task: str,
        status: str,
        result: str,
        run_id: str,
        *,
        intent: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        expected_generation: int | None = None,
    ) -> bool:
        entry = SessionEntry(
            task=(task or "")[:200],
            status=status or "",
            result=(result or "")[:_SUMMARY_CHARS],
            run_id=run_id or "",
            intent=dict(intent or {}),
            artifacts=[
                dict(item) for item in (artifacts or [])
                if isinstance(item, dict)
            ][:20],
        )
        with self._lock:
            if (
                expected_generation is not None
                and int(expected_generation) != self._generation
            ):
                return False
            self._entries.append(entry)
            if len(self._entries) > _MAX_ENTRIES:
                self._entries = self._entries[-_MAX_ENTRIES:]
            return True

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def snapshot(
        self,
        n: int = _RECENT_FOR_PROMPT,
    ) -> tuple[int, list[SessionEntry]]:
        """Atomically bind recent context to the current session generation."""
        with self._lock:
            return self._generation, list(self._entries[-n:])

    def recent(self, n: int = _RECENT_FOR_PROMPT) -> list[SessionEntry]:
        with self._lock:
            return list(self._entries[-n:])

    def all(self) -> list[dict]:
        with self._lock:
            return [
                {"task": e.task, "status": e.status, "result": e.result,
                 "run_id": e.run_id, "intent": dict(e.intent),
                 "artifacts": [dict(item) for item in e.artifacts]}
                for e in reversed(self._entries)  # newest first for UI
            ]

    def clear(self) -> int:
        with self._lock:
            self._entries = []
            self._generation += 1
            return self._generation


SESSION = SessionMemory()


def render_context(entries: list[SessionEntry | dict[str, Any]]) -> str:
    """Render bounded prior-run facts without interpreting references."""
    if not entries:
        return ""
    lines = ["[Bounded same-client session facts, oldest to newest]"]
    for i, e in enumerate(entries, start=1):
        if isinstance(e, dict):
            task = str(e.get("task") or "")
            status = str(e.get("status") or "")
            summary = str(e.get("result") or "").replace("\n", " ").strip()
            run_id = str(e.get("run_id") or "")
            artifacts = [
                dict(item) for item in (e.get("artifacts") or [])
                if isinstance(item, dict)
            ]
        else:
            task, status = e.task, e.status
            summary = e.result.replace("\n", " ").strip()
            run_id = e.run_id
            artifacts = [dict(item) for item in e.artifacts]
        lines.append(
            f"{i}. run_id={run_id}; task={task!r}; status={status!r}; result={summary!r}"
        )
        for artifact in artifacts[-3:]:
            compact = {
                key: artifact.get(key)
                for key in (
                    "artifact_id", "managed_path", "mime_type", "source_window",
                    "purpose", "sendable",
                )
                if artifact.get(key) not in (None, "")
            }
            if compact:
                lines.append(f"   artifact={compact!r}")
    lines.append("Prior-run facts are not evidence of the current screen state.")
    return "\n".join(lines)
