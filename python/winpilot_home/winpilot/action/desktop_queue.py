"""Single-writer desktop action queue.

Windows has one foreground window, one keyboard focus, and one physical cursor.
This queue is the shared guard for real desktop mutations so agent, replay,
recovery, and future Flow real-executor paths do not emit competing input.
"""
from __future__ import annotations

import functools
import inspect
import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from ..utils.log import BUS

T = TypeVar("T")
ResourceScope = Literal["physical", "window", "clipboard", "physical_clipboard"]


class DesktopActionCanceled(RuntimeError):
    """Raised when a queued action is canceled before acquiring resources."""


@dataclass(frozen=True)
class _ResourceWaiter:
    ticket: int
    resources: tuple[str, ...]
    thread_id: int


def _resource_conflict(left: str, right: str) -> bool:
    if left == right:
        return True
    if left == "window:*" and right.startswith("window:"):
        return True
    if right == "window:*" and left.startswith("window:"):
        return True
    return False


def _resource_sets_conflict(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return any(_resource_conflict(a, b) for a in left for b in right)


@dataclass
class _QueuedDesktopJob:
    job_id: str
    action: str
    fn: Callable[[], Any]
    target_hwnd: int | None = None
    scope: ResourceScope = "physical"
    resources: tuple[str, ...] = ()
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
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    def snapshot(self, include_result: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "action": self.action,
            "target_hwnd": self.target_hwnd,
            "scope": self.scope,
            "resources": list(self.resources),
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
    """Fair resource queue for physical, per-window, and clipboard actions."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._ids = itertools.count(1)
        self._async_ids = itertools.count(1)
        self._async_jobs: dict[str, _QueuedDesktopJob] = {}
        self._async_lock = threading.Lock()
        self._resource_condition = threading.Condition(threading.RLock())
        self._resource_tickets = itertools.count(1)
        self._resource_waiters: list[_ResourceWaiter] = []
        self._active_resources: dict[int, tuple[str, ...]] = {}
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
            "parallel_peak": 0,
            "scope_counts": {
                "physical": 0,
                "window": 0,
                "clipboard": 0,
                "physical_clipboard": 0,
            },
        }

    def status(self) -> dict[str, Any]:
        with self._metrics_lock:
            metrics = dict(self._metrics)
        with self._async_lock:
            status_counts = {
                "queued": 0,
                "waiting_resources": 0,
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
        with self._resource_condition:
            metrics["resource_waiters"] = len(self._resource_waiters)
            metrics["active_resources"] = [
                {"lease_id": lease_id, "resources": list(resources)}
                for lease_id, resources in self._active_resources.items()
            ]
            targets: dict[str, dict[str, int]] = {}
            for waiter in self._resource_waiters:
                for resource in waiter.resources:
                    if resource.startswith("window:"):
                        targets.setdefault(resource, {"active": 0, "waiting": 0})["waiting"] += 1
            for resources in self._active_resources.values():
                for resource in resources:
                    if resource.startswith("window:"):
                        targets.setdefault(resource, {"active": 0, "waiting": 0})["active"] += 1
            metrics["window_resources"] = targets
        return metrics

    @staticmethod
    def _resources_for(scope: ResourceScope, target_hwnd: int | None) -> tuple[str, ...]:
        target = int(target_hwnd or 0)
        window_resource = f"window:{target}" if target > 0 else "window:*"
        if scope == "window":
            if target <= 0:
                raise ValueError("window-scoped desktop action requires target_hwnd")
            return (window_resource,)
        if scope == "clipboard":
            return ("clipboard",)
        if scope == "physical_clipboard":
            return tuple(sorted(("clipboard", "physical", window_resource)))
        if scope != "physical":
            raise ValueError(f"unknown desktop action scope: {scope}")
        return tuple(sorted(("physical", window_resource)))

    def _acquire_resources(
        self,
        resources: tuple[str, ...],
        cancel_event: threading.Event | None = None,
    ) -> int:
        waiter = _ResourceWaiter(
            ticket=next(self._resource_tickets),
            resources=resources,
            thread_id=threading.get_ident(),
        )
        with self._resource_condition:
            self._resource_waiters.append(waiter)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._resource_waiters.remove(waiter)
                    self._resource_condition.notify_all()
                    raise DesktopActionCanceled("desktop action canceled while waiting for resources")
                position = self._resource_waiters.index(waiter)
                earlier_conflict = any(
                    _resource_sets_conflict(other.resources, resources)
                    for other in self._resource_waiters[:position]
                )
                active_conflict = any(
                    _resource_sets_conflict(active, resources)
                    for active in self._active_resources.values()
                )
                if not earlier_conflict and not active_conflict:
                    self._resource_waiters.remove(waiter)
                    self._active_resources[waiter.ticket] = resources
                    active_count = len(self._active_resources)
                    with self._metrics_lock:
                        self._metrics["parallel_peak"] = max(
                            int(self._metrics.get("parallel_peak", 0) or 0),
                            active_count,
                        )
                    self._resource_condition.notify_all()
                    return waiter.ticket
                self._resource_condition.wait(timeout=0.05)

    def _release_resources(self, lease_id: int) -> None:
        with self._resource_condition:
            self._active_resources.pop(lease_id, None)
            self._resource_condition.notify_all()

    def submit(
        self,
        action: str,
        fn: Callable[[], T],
        *,
        target_hwnd: int | None = None,
        scope: ResourceScope = "physical",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Queue a desktop action; non-conflicting resources may run together."""
        resources = self._resources_for(scope, target_hwnd)
        job_id = f"daq-job-{next(self._async_ids)}"
        job = _QueuedDesktopJob(
            job_id=job_id,
            action=action,
            fn=fn,
            target_hwnd=target_hwnd,
            scope=scope,
            resources=resources,
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
            scope=scope,
            resources=list(resources),
            metadata=job.metadata,
        )
        threading.Thread(
            target=self._execute_async_job,
            args=(job,),
            name=f"WinPilotDesktopAction-{job_id[-6:]}",
            daemon=True,
        ).start()
        return job_id

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if not job:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        if job.done.is_set():
            return {"ok": False, "status": job.status, "job_id": job_id}
        if job.status == "running":
            job.cancel_requested = True
            job.cancel_event.set()
            return {
                "ok": False,
                "status": job.status,
                "error": "job already running; desktop actions cannot be interrupted safely",
                "job_id": job_id,
            }
        job.cancel_requested = True
        job.cancel_event.set()
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

    def _execute_async_job(self, job: _QueuedDesktopJob) -> None:
        if job.cancel_requested:
            self._finish_canceled_job(job)
            return
        job.status = "waiting_resources"
        BUS.publish(
            "desktop_action_queue_job",
            state="waiting_resources",
            job_id=job.job_id,
            action=job.action,
            target_hwnd=job.target_hwnd,
            scope=job.scope,
            resources=list(job.resources),
            metadata=job.metadata,
        )

        def on_acquired(wait_ms: int) -> None:
            job.started_at = time.monotonic()
            job.wait_ms = wait_ms
            job.status = "running"
            BUS.publish(
                "desktop_action_queue_job",
                state="running",
                job_id=job.job_id,
                action=job.action,
                target_hwnd=job.target_hwnd,
                scope=job.scope,
                resources=list(job.resources),
                wait_ms=job.wait_ms,
                metadata=job.metadata,
            )

        try:
            job.result = self.run(
                job.action,
                job.fn,
                target_hwnd=job.target_hwnd,
                scope=job.scope,
                metadata={**job.metadata, "async_job_id": job.job_id},
                cancel_event=job.cancel_event,
                queued_at=job.submitted_at,
                on_acquired=on_acquired,
            )
        except DesktopActionCanceled:
            self._finish_canceled_job(job)
            return
        except Exception as exc:
            job.finished_at = time.monotonic()
            job.elapsed_ms = int((job.finished_at - (job.started_at or job.submitted_at)) * 1000)
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
                scope=job.scope,
                resources=list(job.resources),
                elapsed_ms=job.elapsed_ms,
                wait_ms=job.wait_ms,
                error=job.error,
            )
        else:
            job.finished_at = time.monotonic()
            job.elapsed_ms = int((job.finished_at - (job.started_at or job.submitted_at)) * 1000)
            job.status = "done"
            with self._metrics_lock:
                self._metrics["async_completed"] += 1
            BUS.publish(
                "desktop_action_queue_job",
                state="done",
                job_id=job.job_id,
                action=job.action,
                target_hwnd=job.target_hwnd,
                scope=job.scope,
                resources=list(job.resources),
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
            scope=job.scope,
            resources=list(job.resources),
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
        scope: ResourceScope = "physical",
        metadata: dict[str, Any] | None = None,
        coalesce_nested: bool = False,
        cancel_event: threading.Event | None = None,
        queued_at: float | None = None,
        on_acquired: Callable[[int], None] | None = None,
    ) -> T:
        depth = int(getattr(self._local, "depth", 0) or 0)
        resources = self._resources_for(scope, target_hwnd)
        job_id = f"daq-{next(self._ids)}"
        queued = queued_at if queued_at is not None else time.monotonic()
        with self._metrics_lock:
            self._metrics["submitted"] += 1
            if depth:
                self._metrics["reentrant"] += 1
            scope_counts = self._metrics.setdefault("scope_counts", {})
            scope_counts[scope] = int(scope_counts.get(scope, 0) or 0) + 1

        held_resources = tuple(getattr(self._local, "resources", ()) or ())
        lease_id: int | None = None
        if depth:
            if not set(resources).issubset(set(held_resources)):
                raise RuntimeError(
                    "nested desktop action cannot escalate resources: "
                    f"held={held_resources} requested={resources}"
                )
            wait_ms = 0
        else:
            lease_id = self._acquire_resources(resources, cancel_event)
            if cancel_event is not None and cancel_event.is_set():
                self._release_resources(lease_id)
                raise DesktopActionCanceled("desktop action canceled before execution")
            wait_ms = int((time.monotonic() - queued) * 1000)
        try:
            if on_acquired is not None:
                on_acquired(wait_ms)
            self._local.depth = depth + 1
            self._local.resources = resources if not depth else held_resources
            previous_coalesce = bool(getattr(self._local, "coalesce_nested", False))
            self._local.coalesce_nested = previous_coalesce or bool(coalesce_nested)
            started = time.monotonic()
            BUS.publish(
                "desktop_action_queue",
                state="start",
                job_id=job_id,
                action=action,
                target_hwnd=target_hwnd,
                scope=scope,
                resources=list(resources),
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
                    scope=scope,
                    resources=list(resources),
                    reentrant=bool(depth),
                    depth=depth,
                    elapsed_ms=elapsed_ms,
                    wait_ms=wait_ms,
                    error=str(exc)[:300],
                )
                raise
            finally:
                self._local.depth = depth
                self._local.resources = held_resources
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
                scope=scope,
                resources=list(resources),
                elapsed_ms=elapsed_ms,
                wait_ms=wait_ms,
            )
            return result
        finally:
            if lease_id is not None:
                self._release_resources(lease_id)

    def coalescing_nested_actions(self) -> bool:
        return bool(
            int(getattr(self._local, "depth", 0) or 0) > 0
            and getattr(self._local, "coalesce_nested", False)
        )


DESKTOP_ACTION_QUEUE = DesktopActionQueue()


def queued_desktop_action(action: str, *, scope: ResourceScope = "physical"):
    """Decorator for public executor functions that mutate the desktop."""

    def decorate(fn: Callable[..., T]) -> Callable[..., T]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if DESKTOP_ACTION_QUEUE.coalescing_nested_actions():
                return fn(*args, **kwargs)
            try:
                target_hwnd = signature.bind_partial(*args, **kwargs).arguments.get("hwnd")
            except TypeError:
                target_hwnd = kwargs.get("hwnd")
            try:
                target = int(target_hwnd) if target_hwnd not in (None, "") else None
            except (TypeError, ValueError):
                target = None
            return DESKTOP_ACTION_QUEUE.run(
                action,
                lambda: fn(*args, **kwargs),
                target_hwnd=target,
                scope=scope,
            )

        return wrapper

    return decorate
