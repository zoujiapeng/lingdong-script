"""Shared asynchronous job runtime with explicit resource leases."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .utils.log import BUS


JobRunner = Callable[[threading.Event], Any]
JobFinished = Callable[["RuntimeJob"], None]


@dataclass
class RuntimeJob:
    id: str
    kind: str
    runner: JobRunner
    resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    on_finish: JobFinished | None = None

    def snapshot(self, include_result: bool = True) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "resources": list(self.resources),
            "metadata": dict(self.metadata),
            "result": self.result if include_result else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class RuntimeJobManager:
    ACTIVE = {"queued", "waiting_resources", "running", "canceling"}

    def __init__(self, retention: int = 200) -> None:
        self.retention = max(20, int(retention))
        self._lock = threading.RLock()
        self._jobs: dict[str, RuntimeJob] = {}
        self._resource_locks: dict[str, threading.Lock] = {}

    def submit(
        self,
        kind: str,
        runner: JobRunner,
        *,
        resources: tuple[str, ...] | list[str] = (),
        metadata: dict[str, Any] | None = None,
        job_id: str = "",
        stop_event: threading.Event | None = None,
        on_finish: JobFinished | None = None,
    ) -> RuntimeJob:
        identifier = job_id or f"{kind}-{uuid.uuid4().hex[:12]}"
        job = RuntimeJob(
            id=identifier,
            kind=str(kind),
            runner=runner,
            resources=tuple(sorted(set(str(item) for item in resources if str(item)))),
            metadata=dict(metadata or {}),
            stop_event=stop_event or threading.Event(),
            on_finish=on_finish,
        )
        with self._lock:
            if identifier in self._jobs:
                raise ValueError(f"runtime job already exists: {identifier}")
            self._jobs[identifier] = job
            self._prune_locked()
        BUS.publish("runtime_job", state="queued", **job.snapshot(include_result=False))
        job.thread = threading.Thread(
            target=self._run,
            args=(job,),
            name=f"runtime-{kind}-{identifier[-6:]}",
            daemon=True,
        )
        job.thread.start()
        return job

    def get(self, job_id: str) -> RuntimeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> RuntimeJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status not in self.ACTIVE:
                return job
            job.status = "canceling"
            job.stop_event.set()
        BUS.publish("runtime_job", state="canceling", **job.snapshot(include_result=False))
        return job

    def busy(self, *, kind: str = "", resource: str = "") -> bool:
        with self._lock:
            return any(
                job.status in self.ACTIVE
                and (not kind or job.kind == kind)
                and (not resource or resource in job.resources)
                for job in self._jobs.values()
            )

    def active(self, *, kind: str = "", resource: str = "") -> list[RuntimeJob]:
        """Return active jobs ordered oldest-first for cancellation/status APIs."""
        with self._lock:
            jobs = [
                job for job in self._jobs.values()
                if job.status in self.ACTIVE
                and (not kind or job.kind == kind)
                and (not resource or resource in job.resources)
            ]
            return sorted(jobs, key=lambda item: item.created_at)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [item.snapshot() for item in jobs[:max(1, int(limit))]]

    def clear_finished(self) -> int:
        with self._lock:
            finished = [key for key, job in self._jobs.items() if job.status not in self.ACTIVE]
            for key in finished:
                self._jobs.pop(key, None)
            return len(finished)

    def _run(self, job: RuntimeJob) -> None:
        locks: list[threading.Lock] = []
        try:
            job.status = "waiting_resources" if job.resources else "running"
            for resource in job.resources:
                with self._lock:
                    lock = self._resource_locks.setdefault(resource, threading.Lock())
                while not lock.acquire(timeout=0.1):
                    if job.stop_event.is_set():
                        job.status = "canceled"
                        return
                locks.append(lock)
            if job.stop_event.is_set():
                job.status = "canceled"
                return
            job.status = "running"
            job.started_at = time.time()
            BUS.publish("runtime_job", state="running", **job.snapshot(include_result=False))
            job.result = job.runner(job.stop_event)
            if job.stop_event.is_set() and not (
                isinstance(job.result, dict) and job.result.get("ok") is True
            ):
                job.status = "canceled"
            elif isinstance(job.result, dict) and job.result.get("ok") is False:
                job.status = "failed"
                job.error = str(job.result.get("error") or "runtime job returned ok=false")
            else:
                job.status = "done"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        finally:
            for lock in reversed(locks):
                lock.release()
            job.finished_at = time.time()
            if job.on_finish:
                try:
                    job.on_finish(job)
                except Exception as exc:
                    BUS.publish(
                        "runtime_job_callback_error",
                        job_id=job.id,
                        kind=job.kind,
                        error=str(exc),
                    )
            job.done.set()
            BUS.publish("runtime_job", state=job.status, **job.snapshot())
            with self._lock:
                self._prune_locked()

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self.retention:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status not in self.ACTIVE),
            key=lambda item: item.finished_at or item.created_at,
        )
        for job in finished[:max(0, len(self._jobs) - self.retention)]:
            self._jobs.pop(job.id, None)


RUNTIME_JOBS = RuntimeJobManager()
