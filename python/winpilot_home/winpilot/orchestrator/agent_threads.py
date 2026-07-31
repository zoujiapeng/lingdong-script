"""Small server-owned Thread/Turn history for the primary Agent surface."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..utils.log import BUS


_MAX_THREADS = 100
_MAX_TURNS = 40
_MAX_HISTORY_MESSAGES = 12
_MAX_HISTORY_CHARS = 16_384
_ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})


@dataclass(slots=True)
class _Turn:
    turn_id: str
    goal: str
    status: str = "queued"
    result: str = ""
    run_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class _Thread:
    thread_id: str
    turns: list[_Turn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class AgentThreadError(ValueError):
    """Thread or turn state is invalid for the requested transition."""


class AgentThreadStore:
    """Own bounded native conversation history without owning execution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._threads: dict[str, _Thread] = {}
        self._turns: dict[str, tuple[_Thread, _Turn]] = {}
        self._runs: dict[str, tuple[str, str]] = {}
        BUS.add_sink(self.on_event)

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def begin_turn(
        self,
        goal: str,
        *,
        thread_id: str = "",
    ) -> tuple[str, str, list[dict[str, str]]]:
        goal = str(goal or "").strip()
        if not goal:
            raise AgentThreadError("turn input must not be empty")
        with self._lock:
            thread = self._threads.get(thread_id) if thread_id else None
            if thread_id and thread is None:
                raise AgentThreadError("thread not found")
            if thread is None:
                self._prune_threads(reserve=1)
                thread = _Thread(thread_id=self._id("thread"))
                self._threads[thread.thread_id] = thread
            if any(turn.status in _ACTIVE_STATUSES for turn in thread.turns):
                raise AgentThreadError("thread already has an active turn")
            history = self._history(thread)
            turn = _Turn(turn_id=self._id("turn"), goal=goal)
            thread.turns.append(turn)
            if len(thread.turns) > _MAX_TURNS:
                removed = thread.turns[:-_MAX_TURNS]
                thread.turns = thread.turns[-_MAX_TURNS:]
                for old_turn in removed:
                    self._turns.pop(old_turn.turn_id, None)
                    if old_turn.run_id:
                        self._runs.pop(old_turn.run_id, None)
            thread.updated_at = turn.created_at
            self._turns[turn.turn_id] = (thread, turn)
            return thread.thread_id, turn.turn_id, history

    def bind_run(
        self,
        *,
        thread_id: str,
        turn_id: str,
        run_id: str,
    ) -> None:
        with self._lock:
            thread, turn = self._turn(turn_id)
            if (
                thread.thread_id != thread_id
                or turn.status != "queued"
                or turn.run_id
                or not run_id
                or run_id in self._runs
            ):
                raise AgentThreadError("run does not match the queued turn")
            turn.run_id = run_id
            self._runs[run_id] = (thread_id, turn_id)
            turn.updated_at = thread.updated_at = time.time()

    def fail_turn(self, turn_id: str, detail: str) -> None:
        with self._lock:
            thread, turn = self._turn(turn_id)
            if turn.status not in _ACTIVE_STATUSES:
                return
            turn.status = "failed"
            turn.result = str(detail or "turn failed")[:16_384]
            turn.updated_at = thread.updated_at = time.time()

    def on_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "arp_run_done":
            return
        run_id = str(event.get("run_id") or "")
        with self._lock:
            bound = self._runs.get(run_id)
            if bound is None:
                return
            thread_id, turn_id = bound
            thread, turn = self._turn(turn_id)
            if (
                thread.thread_id != thread_id
                or turn.run_id != run_id
                or turn.status not in _ACTIVE_STATUSES
            ):
                return
            status = str(event.get("status") or "failed")
            turn.status = status
            if status in {"done", "reply"}:
                result = event.get("user_result")
                turn.result = (
                    str(result).strip()
                    if type(result) is str and result.strip()
                    else ""
                )[:16_384]
            turn.updated_at = thread.updated_at = time.time()
            self._runs.pop(run_id, None)

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                raise AgentThreadError("thread not found")
            return {
                "format": "agent-thread/1",
                "thread_id": thread.thread_id,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
                "turns": [
                    {
                        "turn_id": turn.turn_id,
                        "goal": turn.goal,
                        "status": turn.status,
                        "result": turn.result,
                        "run_id": turn.run_id,
                        "created_at": turn.created_at,
                        "updated_at": turn.updated_at,
                    }
                    for turn in thread.turns
                ],
            }

    @staticmethod
    def _history(thread: _Thread) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in thread.turns:
            if turn.status not in {"done", "reply"} or not turn.result:
                continue
            messages.extend(
                (
                    {"role": "user", "content": turn.goal},
                    {"role": "assistant", "content": turn.result},
                )
            )
        messages = messages[-_MAX_HISTORY_MESSAGES:]
        while (
            messages
            and sum(len(item["content"]) for item in messages)
            > _MAX_HISTORY_CHARS
        ):
            messages = messages[2:]
        return [dict(item) for item in messages]

    def _turn(self, turn_id: str) -> tuple[_Thread, _Turn]:
        bound = self._turns.get(str(turn_id or ""))
        if bound is None:
            raise AgentThreadError("turn not found")
        return bound

    def _prune_threads(self, *, reserve: int = 0) -> None:
        while len(self._threads) > _MAX_THREADS - reserve:
            terminal = [
                thread
                for thread in self._threads.values()
                if not any(
                    turn.status in _ACTIVE_STATUSES
                    for turn in thread.turns
                )
            ]
            oldest_id = (
                min(terminal, key=lambda thread: thread.updated_at).thread_id
                if terminal
                else ""
            )
            if not oldest_id:
                raise AgentThreadError("too many active threads")
            oldest = self._threads.pop(oldest_id)
            for turn in oldest.turns:
                self._turns.pop(turn.turn_id, None)
                if turn.run_id:
                    self._runs.pop(turn.run_id, None)


AGENT_THREADS = AgentThreadStore()


__all__ = ["AGENT_THREADS", "AgentThreadError", "AgentThreadStore"]
