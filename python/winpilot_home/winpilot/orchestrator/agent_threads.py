"""Small server-owned Thread/Turn history for the primary Agent surface."""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.log import BUS, logger


_MAX_THREADS = 100
_MAX_TURNS = 40
_MAX_HISTORY_MESSAGES = 12
_MAX_HISTORY_CHARS = 16_384
_ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})
_SUCCESS_STATUSES = frozenset({"done", "reply"})
_INCOMPLETE_ASSISTANT_NOTE = "The previous attempt did not complete."
_CHECKPOINT_FORMAT = "agent-thread-continuation/1"
_MAX_CHECKPOINT_BYTES = _MAX_THREADS * (_MAX_HISTORY_CHARS + 4096)
_DEFAULT_CHECKPOINT = (
    Path(__file__).resolve().parents[2]
    / "traces"
    / ".agent-thread-continuation.json"
)


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

    def __init__(self, *, checkpoint_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._threads: dict[str, _Thread] = {}
        self._turns: dict[str, tuple[_Thread, _Turn]] = {}
        self._runs: dict[str, tuple[str, str]] = {}
        self._completed: dict[str, tuple[_Thread, _Turn]] = {}
        self._checkpoint_path = checkpoint_path
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
                        self._completed.pop(old_turn.run_id, None)
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
                or run_id in self._completed
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
            result = event.get("user_result")
            if type(result) is str and result.strip():
                turn.result = str(result).strip()[:16_384]
            turn.updated_at = thread.updated_at = time.time()
            self._runs.pop(run_id, None)
            if turn.result:
                self._completed[run_id] = (thread, turn)
                try:
                    self._save_checkpoint_locked()
                except OSError:
                    logger.exception("Agent Thread continuation checkpoint failed")

    def completed_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a private successful turn for a persisted run projection."""

        with self._lock:
            bound = self._completed.get(str(run_id or ""))
            if bound is None:
                return None
            thread, turn = bound
            return {
                "thread_id": thread.thread_id,
                "turn_id": turn.turn_id,
                "run_id": turn.run_id,
                "goal": turn.goal,
                "status": turn.status,
                "result": turn.result,
                "created_at": turn.created_at,
                "updated_at": turn.updated_at,
            }

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                raise AgentThreadError("thread not found")
            return self._snapshot(thread)

    def snapshots(self, limit: int = _MAX_THREADS) -> list[dict[str, Any]]:
        """Return bounded Thread snapshots, newest activity first."""

        bounded_limit = max(1, min(int(limit), _MAX_THREADS))
        with self._lock:
            threads = sorted(
                self._threads.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )[:bounded_limit]
            return [self._snapshot(thread) for thread in threads]

    @staticmethod
    def _snapshot(thread: _Thread) -> dict[str, Any]:
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
    def _continuation_turns(thread: _Thread) -> list[_Turn]:
        turns = [
            turn for turn in thread.turns
            if turn.status not in _ACTIVE_STATUSES and turn.result
        ][-(_MAX_HISTORY_MESSAGES // 2):]
        while (
            turns
            and sum(len(turn.goal) + len(turn.result) for turn in turns)
            > _MAX_HISTORY_CHARS
        ):
            turns = turns[1:]
        return turns

    @classmethod
    def _history_turns(cls, thread: _Thread) -> list[_Turn]:
        """Keep every terminal turn's goal so a follow-up still sees the task.

        Unlike the success-only continuation checkpoint, the model-facing
        history must retain the original request even when the previous attempt
        failed or was canceled; otherwise a short follow-up ("continue") loses
        the goal and the model re-derives a truncated task from the screen.
        """

        turns = [
            turn for turn in thread.turns
            if turn.status not in _ACTIVE_STATUSES
        ][-(_MAX_HISTORY_MESSAGES // 2):]
        while (
            turns
            and sum(
                len(turn.goal)
                + len(turn.result or _INCOMPLETE_ASSISTANT_NOTE)
                for turn in turns
            )
            > _MAX_HISTORY_CHARS
        ):
            turns = turns[1:]
        return turns

    @classmethod
    def _history(cls, thread: _Thread) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in cls._history_turns(thread):
            messages.append({"role": "user", "content": turn.goal})
            content = turn.result or _INCOMPLETE_ASSISTANT_NOTE
            messages.append({"role": "assistant", "content": content})
        return [dict(item) for item in messages]

    @staticmethod
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AgentThreadError(f"duplicate checkpoint key: {key}")
            value[key] = item
        return value

    @staticmethod
    def _checkpoint_id(value: Any, label: str) -> str:
        if type(value) is not str or not value or len(value) > 128:
            raise AgentThreadError(f"invalid checkpoint {label}")
        return value

    @staticmethod
    def _checkpoint_time(value: Any, label: str) -> float:
        if type(value) not in {int, float} or not math.isfinite(float(value)):
            raise AgentThreadError(f"invalid checkpoint {label}")
        return float(value)

    def _checkpoint_payload_locked(self) -> dict[str, Any]:
        threads = []
        for thread in sorted(
            self._threads.values(),
            key=lambda item: item.updated_at,
        ):
            turns = self._continuation_turns(thread)
            if not turns:
                continue
            threads.append({
                "thread_id": thread.thread_id,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
                "turns": [
                    {
                        "turn_id": turn.turn_id,
                        "run_id": turn.run_id,
                        "goal": turn.goal,
                        "status": turn.status,
                        "result": turn.result,
                        "created_at": turn.created_at,
                        "updated_at": turn.updated_at,
                    }
                    for turn in turns
                ],
            })
        return {
            "format": _CHECKPOINT_FORMAT,
            "saved_at": time.time(),
            "threads": threads,
        }

    def _save_checkpoint_locked(self) -> None:
        path = self._checkpoint_path
        if path is None:
            return
        encoded = json.dumps(
            self._checkpoint_payload_locked(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_CHECKPOINT_BYTES:
            raise OSError("Agent Thread continuation checkpoint is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load_checkpoint(self) -> int:
        path = self._checkpoint_path
        if path is None or not path.is_file():
            return 0
        try:
            if path.stat().st_size > _MAX_CHECKPOINT_BYTES:
                raise AgentThreadError(
                    "Agent Thread continuation checkpoint is too large"
                )
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=self._unique_object,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentThreadError("invalid Agent Thread continuation checkpoint") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("format") != _CHECKPOINT_FORMAT
            or type(payload.get("threads")) is not list
            or len(payload["threads"]) > _MAX_THREADS
        ):
            raise AgentThreadError("invalid Agent Thread continuation checkpoint")
        loaded_threads: dict[str, _Thread] = {}
        loaded_turns: dict[str, tuple[_Thread, _Turn]] = {}
        loaded_runs: dict[str, tuple[_Thread, _Turn]] = {}
        for item in payload["threads"]:
            if not isinstance(item, dict) or type(item.get("turns")) is not list:
                raise AgentThreadError("invalid checkpoint thread")
            thread_id = self._checkpoint_id(item.get("thread_id"), "thread_id")
            if thread_id in loaded_threads:
                raise AgentThreadError("duplicate checkpoint thread_id")
            records = item["turns"]
            if len(records) > _MAX_HISTORY_MESSAGES // 2:
                raise AgentThreadError("checkpoint Thread history is too long")
            thread = _Thread(
                thread_id=thread_id,
                created_at=self._checkpoint_time(item.get("created_at"), "created_at"),
                updated_at=self._checkpoint_time(item.get("updated_at"), "updated_at"),
            )
            history_chars = 0
            for record in records:
                if not isinstance(record, dict):
                    raise AgentThreadError("invalid checkpoint turn")
                turn_id = self._checkpoint_id(record.get("turn_id"), "turn_id")
                run_id = self._checkpoint_id(record.get("run_id"), "run_id")
                goal = record.get("goal")
                result = record.get("result")
                status = record.get("status")
                if (
                    turn_id in loaded_turns
                    or run_id in loaded_runs
                    or type(goal) is not str
                    or not goal
                    or type(result) is not str
                    or not result
                    or type(status) is not str
                    or status in _ACTIVE_STATUSES
                ):
                    raise AgentThreadError("invalid or duplicate checkpoint turn")
                history_chars += len(goal) + len(result)
                turn = _Turn(
                    turn_id=turn_id,
                    goal=goal,
                    status=status,
                    result=result,
                    run_id=run_id,
                    created_at=self._checkpoint_time(
                        record.get("created_at"), "turn created_at",
                    ),
                    updated_at=self._checkpoint_time(
                        record.get("updated_at"), "turn updated_at",
                    ),
                )
                thread.turns.append(turn)
                loaded_turns[turn_id] = (thread, turn)
                loaded_runs[run_id] = (thread, turn)
            if history_chars > _MAX_HISTORY_CHARS:
                raise AgentThreadError("checkpoint Thread history is too large")
            loaded_threads[thread_id] = thread
        with self._lock:
            self._threads = loaded_threads
            self._turns = loaded_turns
            self._runs = {}
            self._completed = loaded_runs
        return len(loaded_threads)

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
                    self._completed.pop(turn.run_id, None)


AGENT_THREADS = AgentThreadStore(checkpoint_path=_DEFAULT_CHECKPOINT)


__all__ = ["AGENT_THREADS", "AgentThreadError", "AgentThreadStore"]
