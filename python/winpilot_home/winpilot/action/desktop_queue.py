"""Single-writer desktop action queue.

Windows has one foreground window, one keyboard focus, and one physical cursor.
This queue is the shared guard for real desktop mutations so agent, replay,
recovery, and future Flow real-executor paths do not emit competing input.
"""
from __future__ import annotations

import functools
import itertools
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from ..utils.log import BUS

T = TypeVar("T")


class DesktopActionQueue:
    """Synchronous single-writer queue with reentrant nested-action support."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._ids = itertools.count(1)
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "reentrant": 0,
            "last_job_id": "",
            "last_action": "",
            "last_elapsed_ms": 0,
            "last_wait_ms": 0,
        }

    def status(self) -> dict[str, Any]:
        with self._metrics_lock:
            return dict(self._metrics)

    def run(
        self,
        action: str,
        fn: Callable[[], T],
        *,
        target_hwnd: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        depth = int(getattr(self._local, "depth", 0) or 0)
        job_id = f"daq-{next(self._ids)}"
        queued_at = time.monotonic()
        with self._metrics_lock:
            self._metrics["submitted"] += 1
            if depth:
                self._metrics["reentrant"] += 1

        with self._lock:
            wait_ms = int((time.monotonic() - queued_at) * 1000)
            self._local.depth = depth + 1
            started = time.monotonic()
            BUS.publish(
                "desktop_action_queue",
                state="start",
                job_id=job_id,
                action=action,
                target_hwnd=target_hwnd,
                reentrant=bool(depth),
                depth=depth,
                wait_ms=wait_ms,
                metadata=metadata or {},
            )
            try:
                result = fn()
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                with self._metrics_lock:
                    self._metrics["failed"] += 1
                    self._metrics["last_job_id"] = job_id
                    self._metrics["last_action"] = action
                    self._metrics["last_elapsed_ms"] = elapsed_ms
                    self._metrics["last_wait_ms"] = wait_ms
                BUS.publish(
                    "desktop_action_queue",
                    state="error",
                    job_id=job_id,
                    action=action,
                    target_hwnd=target_hwnd,
                    reentrant=bool(depth),
                    depth=depth,
                    elapsed_ms=elapsed_ms,
                    wait_ms=wait_ms,
                    error=str(exc)[:300],
                )
                raise
            finally:
                self._local.depth = depth

            elapsed_ms = int((time.monotonic() - started) * 1000)
            with self._metrics_lock:
                self._metrics["completed"] += 1
                self._metrics["last_job_id"] = job_id
                self._metrics["last_action"] = action
                self._metrics["last_elapsed_ms"] = elapsed_ms
                self._metrics["last_wait_ms"] = wait_ms
            BUS.publish(
                "desktop_action_queue",
                state="done",
                job_id=job_id,
                action=action,
                target_hwnd=target_hwnd,
                elapsed_ms=elapsed_ms,
                wait_ms=wait_ms,
            )
            return result


DESKTOP_ACTION_QUEUE = DesktopActionQueue()


def queued_desktop_action(action: str):
    """Decorator for public executor functions that mutate the desktop."""

    def decorate(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            target_hwnd = kwargs.get("hwnd")
            try:
                target = int(target_hwnd) if target_hwnd not in (None, "") else None
            except (TypeError, ValueError):
                target = None
            return DESKTOP_ACTION_QUEUE.run(
                action,
                lambda: fn(*args, **kwargs),
                target_hwnd=target,
            )

        return wrapper

    return decorate
