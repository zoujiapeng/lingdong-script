"""One admitted Windows CreateProcessW mechanism; no shell or fallback."""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
from typing import Any, Callable

import win32process

from ..process_identity import (
    ProcessIdentityError,
    canonical_executable_path,
    process_identity_for,
)
from .raw_device import (
    FinalTargetLaunchGuard,
    FinalTargetLaunchGuardDecision,
    RawTargetLaunchContext,
    RawTargetLaunchReceipt,
)
from .replay_policy import MutationOutcome


@dataclass(frozen=True, slots=True)
class WindowsCreatedProcess:
    process_handle: object
    thread_handle: object
    process_id: int


@dataclass(frozen=True, slots=True)
class WindowsLaunchProcessIdentity:
    process_id: int
    creation_time_ns: int
    executable_path: str
    process_identity: str

    def __post_init__(self) -> None:
        try:
            path = canonical_executable_path(self.executable_path)
            expected = process_identity_for(
                process_id=self.process_id,
                creation_time_ns=self.creation_time_ns,
                executable_path=path,
            )
        except ProcessIdentityError as exc:
            raise ValueError("launched process facts are not canonical") from exc
        if self.process_identity != expected:
            raise ValueError("launched process identity does not match its facts")
        object.__setattr__(self, "executable_path", path)


class PyWin32CreateProcessWApi:
    """Unicode CreateProcessW binding with explicit handle ownership."""

    @staticmethod
    def create_process_w(executable_path: str) -> WindowsCreatedProcess:
        process, thread, process_id, _ = win32process.CreateProcess(
            executable_path,
            None,
            None,
            None,
            False,
            0,
            None,
            None,
            win32process.STARTUPINFO(),
        )
        return WindowsCreatedProcess(process, thread, process_id)

    @staticmethod
    def process_id(process_handle: object) -> int:
        return int(win32process.GetProcessId(process_handle) or 0)

    @staticmethod
    def close_handle(handle: object) -> bool:
        handle.Close()
        return True


def _receipt(
    outcome: MutationOutcome,
    count: int | None,
    error: str | None = None,
    identity: WindowsLaunchProcessIdentity | None = None,
) -> RawTargetLaunchReceipt:
    return RawTargetLaunchReceipt(
        outcome,
        count,
        process_id=None if identity is None else identity.process_id,
        process_identity=None if identity is None else identity.process_identity,
        error_kind=error,
    )


class WindowsCreateProcessWBackend:
    """Run one guarded EXE request and close thread then process exactly once."""

    def __init__(
        self,
        process_identity_reader: Callable[[int], WindowsLaunchProcessIdentity],
        *,
        api: Any | None = None,
    ) -> None:
        resolved = api or PyWin32CreateProcessWApi()
        required = ("create_process_w", "process_id", "close_handle")
        if any(not callable(getattr(resolved, name, None)) for name in required):
            raise TypeError("api does not implement the CreateProcessW boundary")
        if not callable(process_identity_reader):
            raise TypeError("process_identity_reader must be callable")
        self._api, self._read_identity = resolved, process_identity_reader

    def __call__(
        self,
        context: RawTargetLaunchContext,
        *,
        final_guard: FinalTargetLaunchGuard,
    ) -> RawTargetLaunchReceipt:
        if not isinstance(context, RawTargetLaunchContext) or not callable(final_guard):
            return _receipt(MutationOutcome.NOT_STARTED, 0, "invalid_launch_input")
        try:
            decision = final_guard()
        except Exception:
            decision = FinalTargetLaunchGuardDecision.AUTHORITY_CHANGED
        if not isinstance(decision, FinalTargetLaunchGuardDecision):
            decision = FinalTargetLaunchGuardDecision.AUTHORITY_CHANGED
        if decision is not FinalTargetLaunchGuardDecision.ADMITTED:
            return _receipt(
                MutationOutcome.NOT_STARTED,
                0,
                f"final_guard_{decision.value}",
            )

        try:
            created = self._api.create_process_w(context.executable_path)
        except Exception:
            return _receipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                1,
                "create_process_outcome_unknown",
            )
        if not isinstance(created, WindowsCreatedProcess):
            return _receipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                1,
                "create_process_outcome_unknown",
            )

        distinct_handles = self._distinct_handles(
            created.thread_handle,
            created.process_handle,
        )
        process_valid = self._valid_handle(created.process_handle)
        thread_closed = self._close_once(created.thread_handle)
        observed = None
        handle_pid, observed_path = 0, ""
        if distinct_handles and process_valid:
            try:
                handle_pid = self._api.process_id(created.process_handle)
                observed = self._read_identity(handle_pid)
                observed_path = RawTargetLaunchContext(
                    observed.executable_path
                ).executable_path
            except Exception:
                pass
        process_closed = bool(
            distinct_handles and self._close_once(created.process_handle)
        )
        exact = bool(
            isinstance(observed, WindowsLaunchProcessIdentity)
            and type(created.process_id) is int
            and created.process_id > 0
            and type(handle_pid) is int
            and observed.process_id == created.process_id == handle_pid
            and ntpath.normcase(observed_path).casefold()
            == ntpath.normcase(context.executable_path).casefold()
        )
        if not (thread_closed and process_closed and exact):
            return _receipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                1,
                "launched_process_identity_unknown",
            )
        try:
            return _receipt(MutationOutcome.APPLIED, 1, identity=observed)
        except (TypeError, ValueError):
            return _receipt(
                MutationOutcome.OUTCOME_UNKNOWN,
                1,
                "launched_process_identity_unknown",
            )

    def _close_once(self, handle: object) -> bool:
        if not self._valid_handle(handle):
            return False
        try:
            return self._api.close_handle(handle) is True
        except Exception:
            return False

    @staticmethod
    def _valid_handle(handle: object) -> bool:
        return (
            handle is not None
            and type(handle) is not bool
            and (type(handle) is not int or handle > 0)
        )

    @staticmethod
    def _distinct_handles(first: object, second: object) -> bool:
        if first is second:
            return False
        if type(first) is int and type(second) is int:
            return first != second
        return True


__all__ = [
    "PyWin32CreateProcessWApi",
    "WindowsCreatedProcess",
    "WindowsCreateProcessWBackend",
    "WindowsLaunchProcessIdentity",
]
