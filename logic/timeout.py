"""Timeout control — wrap operations with timeout enforcement."""
import time
import threading
from typing import Callable, Optional, Any
from concurrent.futures import ThreadPoolExecutor


class TimeoutControl:
    """Execute operations with a timeout limit.

    If the operation doesn't complete within the timeout,
    it's interrupted (by closing the thread) and an exception is raised.
    """

    def __init__(self, default_timeout: float = 30.0):
        self.default_timeout = default_timeout
        self._executor = ThreadPoolExecutor(max_workers=1)

    def run(self, fn: Callable, *args,
            timeout: Optional[float] = None,
            default_value: Any = None,
            **kwargs) -> Any:
        """Run a function with a timeout.

        Args:
            fn: Function to execute.
            timeout: Timeout in seconds. Uses default if None.
            default_value: Value to return if timeout occurs.

        Returns:
            Function result, or default_value on timeout.
        """
        timeout_val = timeout if timeout is not None else self.default_timeout
        future = self._executor.submit(fn, *args, **kwargs)

        try:
            return future.result(timeout=timeout_val)
        except Exception:
            if default_value is not None:
                return default_value
            raise

    def check_timeout(self, start_time: float, timeout: float) -> bool:
        """Check if a timeout has elapsed since start_time.

        Returns:
            True if NOT timed out (still ok), False if timed out.
        """
        return (time.time() - start_time) < timeout

    def remaining(self, start_time: float, timeout: float) -> float:
        """Get remaining time in seconds before timeout."""
        elapsed = time.time() - start_time
        return max(0.0, timeout - elapsed)

    @staticmethod
    def deadline(timeout: float) -> float:
        """Get a deadline timestamp from now + timeout."""
        return time.time() + timeout

    @staticmethod
    def is_expired(deadline: float) -> bool:
        """Check if a deadline has expired."""
        return time.time() >= deadline
