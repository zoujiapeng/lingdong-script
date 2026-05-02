"""Thread pool for parallel detection tasks."""
import concurrent.futures
import threading
from typing import Callable, Any, Optional


class ThreadPoolManager:
    """Manages a thread pool for running parallel detection operations.

    Used for:
    - Running multiple template matching operations concurrently
    - Parallel OCR + image detection
    - Multi-region analysis
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._owns_executor = False

    @classmethod
    def get_instance(cls, max_workers: int = 4) -> 'ThreadPoolManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(max_workers)
        return cls._instance

    @property
    def executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix='auto_engine'
            )
            self._owns_executor = True
        return self._executor

    def submit(self, fn: Callable, *args, **kwargs) -> concurrent.futures.Future:
        """Submit a task to the thread pool."""
        return self.executor.submit(fn, *args, **kwargs)

    def map(self, fn: Callable, *iterables, timeout: Optional[float] = None):
        """Parallel map over iterables."""
        return self.executor.map(fn, *iterables, timeout=timeout)

    def run_all(self, tasks: list[tuple[Callable, list, dict]],
                timeout: Optional[float] = None) -> list[Any]:
        """Run multiple tasks in parallel and collect results.

        Args:
            tasks: List of (fn, args, kwargs) tuples.
            timeout: Overall timeout in seconds.

        Returns:
            List of results in original order.
        """
        futures = []
        for fn, args, kwargs in tasks:
            futures.append(self.executor.submit(fn, *args, **kwargs))

        results = []
        for f in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                results.append(f.result())
            except Exception:
                results.append(None)

        return results

    def shutdown(self, wait: bool = True):
        if self._owns_executor and self._executor:
            self._executor.shutdown(wait=wait)
            self._executor = None
