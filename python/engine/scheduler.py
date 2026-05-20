"""
节点调度系统: 基于 APScheduler 的任务队列管理
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor as APThreadPool


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 5
    HIGH = 10
    URGENT = 20


@dataclass(order=True)
class ScheduledTask:
    priority: int
    task_id: str = field(compare=False)
    dsl: str = field(compare=False)
    callback: Optional[Callable] = field(compare=False, default=None)
    created_at: float = field(compare=False, default_factory=time.time)
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)


class TaskScheduler:
    """
    优先级任务调度器 — 基于 APScheduler BackgroundScheduler
    兼容旧版 TaskScheduler API: submit / cancel / start / stop / get_queue_status / on_complete
    """

    def __init__(self, executor, max_workers: int = 1):
        self._executor = executor
        self._max_workers = max_workers

        # APScheduler 后台调度器
        self._aps = BackgroundScheduler(
            executors={"default": APThreadPool(max_workers)},
            job_defaults={"coalesce": True, "max_instances": 1},
        )

        self._running: Dict[str, ScheduledTask] = {}
        self._history: List[dict] = []
        self._cancelled: set = set()
        self._lock = threading.Lock()
        self._on_complete_callbacks: List[Callable] = []

    def start(self):
        if not self._aps.running:
            self._aps.start()

    def stop(self):
        if self._aps.running:
            self._aps.shutdown(wait=False)
        self._executor.stop()

    def submit(
        self,
        task_id: str,
        dsl: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        callback: Optional[Callable] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        task = ScheduledTask(
            priority=-priority.value,
            task_id=task_id,
            dsl=dsl,
            callback=callback,
            metadata=metadata or {},
        )

        # 用 APScheduler 的 job 调度执行（立即执行一次）
        self._aps.add_job(
            self._execute_task,
            args=[task],
            id=task_id,
            replace_existing=True,
            name=f"dsl-{task_id[:8]}",
        )
        return task_id

    def cancel(self, task_id: str):
        self._cancelled.add(task_id)
        with self._lock:
            if task_id in self._running:
                self._executor.stop()
        try:
            self._aps.remove_job(task_id)
        except Exception:
            pass

    def get_queue_status(self) -> dict:
        jobs = []
        try:
            jobs = [{"id": j.id, "name": j.name, "next_run": str(j.next_run_time)}
                    for j in self._aps.get_jobs()]
        except Exception:
            pass
        return {
            "apscheduler_jobs": len(jobs),
            "jobs": jobs,
            "running": list(self._running.keys()),
            "history": self._history[-20:],
        }

    def on_complete(self, callback: Callable):
        self._on_complete_callbacks.append(callback)

    def _execute_task(self, task: ScheduledTask):
        if task.task_id in self._cancelled:
            self._cancelled.discard(task.task_id)
            return

        with self._lock:
            self._running[task.task_id] = task

        start = time.time()
        try:
            self._executor.run_dsl_sync(task.dsl)
            status = "success"
            error = None
        except Exception as e:
            status = "error"
            error = str(e)
        finally:
            elapsed = time.time() - start
            with self._lock:
                self._running.pop(task.task_id, None)
                self._history.append({
                    "task_id": task.task_id,
                    "status": status,
                    "elapsed": round(elapsed, 2),
                    "error": error,
                })

            if task.callback:
                try:
                    task.callback(status=status, error=error, elapsed=elapsed)
                except Exception:
                    pass

            for cb in self._on_complete_callbacks:
                try:
                    cb(task_id=task.task_id, status=status)
                except Exception:
                    pass
