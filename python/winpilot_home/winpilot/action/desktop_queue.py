"""Single-writer desktop action queue.

Windows has one foreground window, one keyboard focus, and one physical cursor.
This queue is the shared guard for real desktop mutations so agent, replay,
recovery, and future Flow real-executor paths do not emit competing input.
"""
from __future__ import annotations

import functools
import itertools
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ..utils.log import BUS

T = TypeVar("T")


@dataclass
class _QueuedDesktopJob:
    job_id: str
    action: str
    fn: Callable[[], Any]
    target_hwnd: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    submitted_at: float = field(default_factory=time.monotonic)
    started_at: float = 0.0
    finished_at: float = 0.0
    wait_ms: int = 0
    elapsed_ms: int = 0
    result: Any = None
    error: str = ""
    cancel_requested: bool = False
    done: threading.Event = field(default_factory=threading.Event)

    def snapshot(self, include_result: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "action": self.action,
            "target_hwnd": self.target_hwnd,
            "status": self.status,
            "wait_ms": self.wait_ms,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "metadata": dict(self.metadata),
        }
        if include_result:
            data["result"] = self.result
        return data


class DesktopActionQueue:
    """Single-writer desktop queue with sync and background-job entry points."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._ids = itertools.count(1)
        self._async_ids = itertools.count(1)
        self._async_queue: queue.Queue[_QueuedDesktopJob] = queue.Queue()
        self._async_jobs: dict[str, _QueuedDesktopJob] = {}
        self._async_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "reentrant": 0,
            "async_submitted": 0,
            "async_completed": 0,
            "async_failed": 0,
            "async_canceled": 0,
            "last_job_id": "",
            "last_action": "",
            "last_elapsed_ms": 0,
            "last_wait_ms": 0,
        }

    def status(self) -> dict[str, Any]:
        with self._metrics_lock:
            metrics = dict(self._metrics)
        with self._async_lock:
            status_counts = {
                "queued": 0,
                "running": 0,
                "done": 0,
                "error": 0,
                "canceled": 0,
            }
            latest = sorted(
                self._async_jobs.values(),
                key=lambda job: job.submitted_at,
                reverse=True,
            )[:10]
            for job in self._async_jobs.values():
                if job.status in status_counts:
                    status_counts[job.status] += 1
            metrics["async_status"] = status_counts
            metrics["async_latest"] = [job.snapshot() for job in latest]
        return metrics

    def _ensure_worker(self) -> None:
        with self._async_lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="WinPilotDesktopActionQueue",
                daemon=True,
            )
            self._worker.start()

    def submit(
        self,
        action: str,
        fn: Callable[[], T],
        *,
        target_hwnd: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Queue a desktop mutation for background execution.

        This is the async runtime entry point for future replay/recovery/Flow
        integrations. The existing public executor path still uses ``run()``
        synchronously, so behavior stays stable while the async API is adopted.
        """
        job_id = f"daq-job-{next(self._async_ids)}"
        job = _QueuedDesktopJob(
            job_id=job_id,
            action=action,
            fn=fn,
            target_hwnd=target_hwnd,
            metadata=dict(metadata or {}),
        )
        with self._async_lock:
            self._async_jobs[job_id] = job
        with self._metrics_lock:
            self._metrics["async_submitted"] += 1
        BUS.publish(
            "desktop_action_queue_job",
            state="queued",
            job_id=job_id,
            action=action,
            target_hwnd=target_hwnd,
            metadata=job.metadata,
        )
        self._async_queue.put(job)
        self._ensure_worker()
        return job_id

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if not job:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        if job.done.is_set():
            return {"ok": False, "status": job.status, "job_id": job_id}
        if job.status == "running":
            job.cancel_requested = True
            return {
                "ok": False,
                "status": job.status,
                "error": "job already running; physical desktop actions cannot be interrupted safely",
                "job_id": job_id,
            }
        job.cancel_requested = True
        return {"ok": True, "status": job.status, "job_id": job_id}

    def wait(self, job_id: str, timeout_s: float | None = None) -> dict[str, Any]:
        job = self._job(job_id)
        if not job:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        if not job.done.wait(timeout=timeout_s):
            return {"ok": False, "status": job.status, "error": "timeout", "job_id": job_id}
        ok = job.status == "done"
        return {"ok": ok, **job.snapshot(include_result=ok)}

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if not job:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        return {"ok": True, **job.snapshot(include_result=job.done.is_set())}

    def _job(self, job_id: str) -> _QueuedDesktopJob | None:
        with self._async_lock:
            return self._async_jobs.get(job_id)

    def _worker_loop(self) -> None:
        while True:
            job = self._async_queue.get()
            try:
                self._execute_async_job(job)
            finally:
                self._async_queue.task_done()

    def _execute_async_job(self, job: _QueuedDesktopJob) -> None:
        if job.cancel_requested:
            self._finish_canceled_job(job)
            return
        job.started_at = time.monotonic()
        job.wait_ms = int((job.started_at - job.submitted_at) * 1000)
        job.status = "running"
        BUS.publish(
            "desktop_action_queue_job",
            state="running",
            job_id=job.job_id,
            action=job.action,
            target_hwnd=job.target_hwnd,
            wait_ms=job.wait_ms,
            metadata=job.metadata,
        )
        try:
            job.result = self.run(
                job.action,
                job.fn,
                target_hwnd=job.target_hwnd,
                metadata={**job.metadata, "async_job_id": job.job_id},
            )
        except Exception as exc:
            job.finished_at = time.monotonic()
            job.elapsed_ms = int((job.finished_at - job.started_at) * 1000)
            job.error = str(exc)[:300]
            job.status = "error"
            with self._metrics_lock:
                self._metrics["async_failed"] += 1
            BUS.publish(
                "desktop_action_queue_job",
                state="error",
                job_id=job.job_id,
                action=job.action,
                target_hwnd=job.target_hwnd,
                elapsed_ms=job.elapsed_ms,
                wait_ms=job.wait_ms,
                error=job.error,
            )
        else:
            job.finished_at = time.monotonic()
            job.elapsed_ms = int((job.finished_at - job.started_at) * 1000)
            job.status = "done"
            with self._metrics_lock:
                self._metrics["async_completed"] += 1
            BUS.publish(
                "desktop_action_queue_job",
                state="done",
                job_id=job.job_id,
                action=job.action,
                target_hwnd=job.target_hwnd,
                elapsed_ms=job.elapsed_ms,
                wait_ms=job.wait_ms,
            )
        finally:
            job.done.set()

    def _finish_canceled_job(self, job: _QueuedDesktopJob) -> None:
        job.finished_at = time.monotonic()
        job.wait_ms = int((job.finished_at - job.submitted_at) * 1000)
        job.status = "canceled"
        with self._metrics_lock:
            self._metrics["async_canceled"] += 1
        BUS.publish(
            "desktop_action_queue_job",
            state="canceled",
            job_id=job.job_id,
            action=job.action,
            target_hwnd=job.target_hwnd,
            wait_ms=job.wait_ms,
            metadata=job.metadata,
        )
        job.done.set()

    def run(
        self,
        action: str,
        fn: Callable[[], T],
        *,
        target_hwnd: int | None = None,
        metadata: dict[str, Any] | None = None,
        coalesce_nested: bool = False,
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
            previous_coalesce = bool(getattr(self._local, "coalesce_nested", False))
            self._local.coalesce_nested = previous_coalesce or bool(coalesce_nested)
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
                self._local.coalesce_nested = previous_coalesce

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

    def coalescing_nested_actions(self) -> bool:
        return bool(
            int(getattr(self._local, "depth", 0) or 0) > 0
            and getattr(self._local, "coalesce_nested", False)
        )


DESKTOP_ACTION_QUEUE = DesktopActionQueue()


def queued_desktop_action(action: str):
    """Decorator for public executor functions that mutate the desktop."""

    def decorate(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if DESKTOP_ACTION_QUEUE.coalescing_nested_actions():
                return fn(*args, **kwargs)
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
