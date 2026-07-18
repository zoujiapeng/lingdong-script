"""Session memory: cross-task continuity within one server lifetime.

The agent runs each task as a fresh AgentRunner with empty history, so a
follow-up like "再来一次 / 把它关掉 / 刚才那个" has no antecedent and the agent
acts as if starting a brand-new conversation. This module keeps a short log of
recently finished tasks (this session only) so referential commands resolve.

Process-lifetime singleton = one "session"; a server restart clears it, which
matches the intuitive notion of a session boundary. Not persisted to disk.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

_MAX_ENTRIES = 8
_RECENT_FOR_PROMPT = 3
_SUMMARY_CHARS = 160
_REFERENCE_WORDS = (
    "again",
    "same",
    "previous",
    "last",
    "continue",
    "repeat",
    "it",
    "that",
)
_REFERENCE_PHRASES = (
    "do it again",
    "same one",
    "last one",
    "previous one",
    "close it",
)
_REFERENCE_CUES_CN = (
    "再来",
    "再",
    "继续",
    "刚才",
    "上一个",
    "上次",
    "之前",
    "同样",
    "重复",
    "那个",
    "它",
    "把它",
)


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
    """Compact prompt block ('' when empty). Injected at the history tail."""
    if not entries:
        return ""
    lines = ["【本会话此前已完成的任务（从旧到新）】"]
    for i, e in enumerate(entries, start=1):
        if isinstance(e, dict):
            task = str(e.get("task") or "")
            status = str(e.get("status") or "")
            summary = str(e.get("result") or "").replace("\n", " ").strip()
        else:
            task, status = e.task, e.status
            summary = e.result.replace("\n", " ").strip()
        lines.append(f'{i}. "{task}" → {status}：{summary}')
    lines.append(
        "若当前指令含'再来一次/再来/重复/刚才那个/那个/把它关了/继续'等指代，"
        "通常指上面【最后一条】任务——据此理解并执行，必要时仍先 observe 确认当前界面。"
    )
    lines.append(
        "This session context is only for resolving references; do not treat it "
        "as evidence that the current screen is already in that state."
    )
    return "\n".join(lines)


def should_include_context(task: str) -> bool:
    """Return True when the new task appears to refer to prior session state."""
    text = (task or "").casefold()
    if not text:
        return False
    if any(phrase in text for phrase in _REFERENCE_PHRASES):
        return True
    if any(cue in text for cue in _REFERENCE_CUES_CN):
        return True
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in _REFERENCE_WORDS)
