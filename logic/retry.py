"""Retry mechanism — execute operations with retry on failure."""
import time
from typing import Callable, Optional, Any


class RetryMechanism:
    """Retry an operation with configurable retry count, delay, and backoff.

    Supports:
    - Fixed retry count
    - Exponential backoff
    - Custom retry conditions
    - Per-try callbacks
    """

    def __init__(self, max_retries: int = 3,
                 base_delay: float = 1.0,
                 backoff: float = 2.0,
                 max_delay: float = 60.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff = backoff
        self.max_delay = max_delay

    def run(self, fn: Callable, *args,
            retry_condition: Optional[Callable[[Any], bool]] = None,
            on_retry: Optional[Callable[[int, Exception], None]] = None,
            **kwargs) -> tuple[bool, Any]:
        """Execute a function with retry.

        Args:
            fn: Function to execute.
            retry_condition: If provided, determines whether to retry based on result.
                             If None, retries on any exception.
            on_retry: Called before each retry with (attempt, exception).

        Returns:
            (success, result_or_exception) tuple.
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                result = fn(*args, **kwargs)

                if retry_condition is not None and attempt < self.max_retries:
                    if retry_condition(result):
                        last_exception = Exception(f"Retry condition not met: {result}")
                        if on_retry:
                            on_retry(attempt, last_exception)
                        self._wait(attempt)
                        continue

                return True, result

            except Exception as e:
                last_exception = e
                if on_retry:
                    on_retry(attempt, e)

                if attempt >= self.max_retries:
                    return False, e

                self._wait(attempt)

        return False, last_exception

    def run_until(self, fn: Callable, *args,
                  predicate: Callable[[Any], bool],
                  timeout: float = 30.0,
                  poll_interval: float = 0.5,
                  **kwargs) -> tuple[bool, Any]:
        """Keep trying until the predicate is satisfied or timeout.

        Args:
            fn: Function to execute.
            predicate: Called with the result. Return True = success.
            timeout: Max total time.
            poll_interval: Wait between attempts.

        Returns:
            (success, last_result).
        """
        start = time.time()
        last_result = None

        while time.time() - start < timeout:
            try:
                last_result = fn(*args, **kwargs)
                if predicate(last_result):
                    return True, last_result
            except Exception as e:
                last_result = e

            time.sleep(poll_interval)

        return False, last_result

    def _wait(self, attempt: int):
        """Sleep with exponential backoff."""
        delay = min(self.base_delay * (self.backoff ** attempt), self.max_delay)
        time.sleep(delay)
