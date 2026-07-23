"""One-shot, read-only Windows facts for vNext target discovery.

The operating-system catalog is sampled once per instance of
``WindowsTargetFactsAdapter``.  Repeated semantic aliases are then matched
against the immutable sample; they never trigger another process or window
walk.  This module only reports identity facts.  It cannot focus or launch a
window, mutate Mission state, or choose a target.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import math
import ntpath
import time
from typing import Protocol, runtime_checkable
import unicodedata

from ...process_identity import (
    MAX_PROCESS_EXECUTABLE_PATH_CHARS,
    ProcessIdentityError,
)
from .contracts import process_identity_for


WINDOWS_TARGET_FACTS_FORMAT = "windows-target-facts/0"
MAX_PROCESS_FACTS = 16_384
MAX_WINDOW_FACTS = 8_192
MAX_EXECUTABLE_PATH_CHARS = MAX_PROCESS_EXECUTABLE_PATH_CHARS
MAX_WINDOW_TITLE_CHARS = 512
MAX_PROCESS_QUERY_CHARS = 512


class WindowsTargetFactsError(ValueError):
    """The backend could not provide a complete, canonical facts sample."""


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise WindowsTargetFactsError(f"{name} must be a positive integer")
    return value


def _bounded_text(
    value: object,
    name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise WindowsTargetFactsError(f"{name} must be a string")
    result = value.strip()
    if (not result and not allow_empty) or "\x00" in result:
        raise WindowsTargetFactsError(f"{name} is invalid")
    if len(result) > maximum:
        raise WindowsTargetFactsError(f"{name} is too long")
    return result


def _strict_row(
    value: object,
    *,
    fields: frozenset[str],
    name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WindowsTargetFactsError(f"{name} must be a mapping")
    if set(value) != fields or any(type(key) is not str for key in value):
        raise WindowsTargetFactsError(f"{name} has an invalid schema")
    return value


def _bounded_rows(value: object, *, name: str, maximum: int) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise WindowsTargetFactsError(f"{name} must be an iterable of rows")
    rows: list[object] = []
    for row in value:
        if len(rows) >= maximum:
            raise WindowsTargetFactsError(f"{name} exceeds its bounded size")
        rows.append(row)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class ProcessTargetFact:
    process_id: int
    creation_time_ns: int
    executable_path: str
    process_identity: str

    def __post_init__(self) -> None:
        process_id = _positive_int(self.process_id, "process_id")
        creation_time_ns = _positive_int(
            self.creation_time_ns, "creation_time_ns",
        )
        path = _bounded_text(
            self.executable_path,
            "executable_path",
            maximum=MAX_EXECUTABLE_PATH_CHARS,
        )
        if not ntpath.isabs(path):
            raise WindowsTargetFactsError("executable_path must be absolute")
        path = ntpath.normpath(path)
        try:
            expected = process_identity_for(
                process_id=process_id,
                creation_time_ns=creation_time_ns,
                executable_path=path,
            )
        except ProcessIdentityError as exc:
            raise WindowsTargetFactsError(str(exc)) from exc
        if self.process_identity != expected:
            raise WindowsTargetFactsError(
                "process_identity does not match PID, creation time, and executable path"
            )
        object.__setattr__(self, "executable_path", path)

    @classmethod
    def from_raw(cls, raw: object) -> "ProcessTargetFact":
        row = _strict_row(
            raw,
            fields=frozenset({
                "process_id", "creation_time_ns", "executable_path",
            }),
            name="process row",
        )
        process_id = _positive_int(row["process_id"], "process_id")
        creation_time_ns = _positive_int(
            row["creation_time_ns"], "creation_time_ns",
        )
        path = _bounded_text(
            row["executable_path"],
            "executable_path",
            maximum=MAX_EXECUTABLE_PATH_CHARS,
        )
        try:
            process_identity = process_identity_for(
                process_id=process_id,
                creation_time_ns=creation_time_ns,
                executable_path=path,
            )
        except ProcessIdentityError as exc:
            raise WindowsTargetFactsError(str(exc)) from exc
        return cls(
            process_id=process_id,
            creation_time_ns=creation_time_ns,
            executable_path=path,
            process_identity=process_identity,
        )


@dataclass(frozen=True, slots=True)
class WindowTargetFact:
    hwnd: int
    process_id: int
    title: str
    visible: bool

    def __post_init__(self) -> None:
        _positive_int(self.hwnd, "hwnd")
        _positive_int(self.process_id, "window process_id")
        title = _bounded_text(
            self.title,
            "window title",
            maximum=MAX_WINDOW_TITLE_CHARS,
            allow_empty=True,
        )
        if type(self.visible) is not bool:
            raise WindowsTargetFactsError("visible must be boolean")
        object.__setattr__(self, "title", title)

    @classmethod
    def from_raw(cls, raw: object) -> "WindowTargetFact":
        row = _strict_row(
            raw,
            fields=frozenset({"hwnd", "process_id", "title", "visible"}),
            name="window row",
        )
        return cls(
            hwnd=row["hwnd"],
            process_id=row["process_id"],
            title=row["title"],
            visible=row["visible"],
        )


@dataclass(frozen=True, slots=True)
class WindowsTargetFactsSnapshot:
    processes: tuple[ProcessTargetFact, ...]
    windows: tuple[WindowTargetFact, ...]
    captured_at: float
    format: str = WINDOWS_TARGET_FACTS_FORMAT

    def __post_init__(self) -> None:
        if self.format != WINDOWS_TARGET_FACTS_FORMAT:
            raise WindowsTargetFactsError("unsupported Windows target facts format")
        if (
            type(self.processes) is not tuple
            or len(self.processes) > MAX_PROCESS_FACTS
            or any(type(item) is not ProcessTargetFact for item in self.processes)
        ):
            raise WindowsTargetFactsError("processes must be a bounded fact tuple")
        if (
            type(self.windows) is not tuple
            or len(self.windows) > MAX_WINDOW_FACTS
            or any(type(item) is not WindowTargetFact for item in self.windows)
        ):
            raise WindowsTargetFactsError("windows must be a bounded fact tuple")
        if tuple(sorted(self.processes, key=lambda item: item.process_id)) != self.processes:
            raise WindowsTargetFactsError("processes must use canonical PID order")
        if tuple(sorted(self.windows, key=lambda item: item.hwnd)) != self.windows:
            raise WindowsTargetFactsError("windows must use canonical HWND order")
        process_ids = tuple(item.process_id for item in self.processes)
        hwnds = tuple(item.hwnd for item in self.windows)
        if len(process_ids) != len(set(process_ids)):
            raise WindowsTargetFactsError("process_id values must be unique")
        if len(hwnds) != len(set(hwnds)):
            raise WindowsTargetFactsError("hwnd values must be unique")
        if any(item.process_id not in set(process_ids) for item in self.windows):
            raise WindowsTargetFactsError("window has no strong process identity")
        if (
            type(self.captured_at) not in (int, float)
            or not math.isfinite(float(self.captured_at))
            or float(self.captured_at) < 0
        ):
            raise WindowsTargetFactsError(
                "captured_at must be finite and non-negative"
            )
        object.__setattr__(self, "captured_at", float(self.captured_at))


@runtime_checkable
class WindowsTargetFactsBackend(Protocol):
    """Read-only raw Windows facts; each method is invoked once per sample."""

    def read_processes(self) -> Iterable[Mapping[str, object]]: ...
    def read_windows(self) -> Iterable[Mapping[str, object]]: ...


class PsutilWin32TargetFactsBackend:
    """Production source using one psutil walk and one Win32 window walk."""

    @staticmethod
    def read_processes() -> tuple[Mapping[str, object], ...]:
        import psutil

        rows: list[Mapping[str, object]] = []
        for process in psutil.process_iter(["pid", "create_time", "exe"]):
            try:
                info = process.info
                process_id = info.get("pid")
                created = info.get("create_time")
                executable = info.get("exe")
                if (
                    type(process_id) is not int
                    or process_id <= 0
                    or type(created) not in (int, float)
                    or not math.isfinite(float(created))
                    or float(created) <= 0
                    or type(executable) is not str
                    or not executable.strip()
                ):
                    continue
                rows.append({
                    "process_id": process_id,
                    "creation_time_ns": int(float(created) * 1_000_000_000),
                    "executable_path": executable,
                })
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return tuple(rows)

    @staticmethod
    def read_windows() -> tuple[Mapping[str, object], ...]:
        import win32gui
        import win32process

        rows: list[Mapping[str, object]] = []

        def collect(hwnd: int, _unused: object) -> bool:
            try:
                _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
                title = win32gui.GetWindowText(hwnd)
                visible = bool(win32gui.IsWindowVisible(hwnd))
                if title or visible:
                    rows.append({
                        "hwnd": int(hwnd),
                        "process_id": int(process_id),
                        "title": title,
                        "visible": visible,
                    })
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
        return tuple(rows)


class WindowsTargetFactsReader:
    """Create one canonical immutable sample without desktop mutation."""

    def __init__(
        self,
        backend: WindowsTargetFactsBackend | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        resolved = backend or PsutilWin32TargetFactsBackend()
        if not isinstance(resolved, WindowsTargetFactsBackend):
            raise TypeError("backend must implement WindowsTargetFactsBackend")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._backend = resolved
        self._clock = clock

    def read(self) -> WindowsTargetFactsSnapshot:
        try:
            raw_processes = _bounded_rows(
                self._backend.read_processes(),
                name="process rows",
                maximum=MAX_PROCESS_FACTS,
            )
            raw_windows = _bounded_rows(
                self._backend.read_windows(),
                name="window rows",
                maximum=MAX_WINDOW_FACTS,
            )
            processes = tuple(sorted(
                (ProcessTargetFact.from_raw(row) for row in raw_processes),
                key=lambda item: item.process_id,
            ))
            process_ids = {item.process_id for item in processes}
            windows = tuple(sorted(
                (
                    fact
                    for row in raw_windows
                    for fact in (WindowTargetFact.from_raw(row),)
                    if fact.process_id in process_ids
                ),
                key=lambda item: item.hwnd,
            ))
            captured_at = self._clock()
            return WindowsTargetFactsSnapshot(
                processes=processes,
                windows=windows,
                captured_at=captured_at,
            )
        except WindowsTargetFactsError:
            raise
        except Exception as exc:
            raise WindowsTargetFactsError(
                "Windows target facts source is unavailable"
            ) from exc


def _normalized_process_term(value: object) -> str:
    if type(value) is not str:
        raise WindowsTargetFactsError("process query must be a string")
    result = unicodedata.normalize("NFKC", value).casefold().strip()
    if not result or len(result) > MAX_PROCESS_QUERY_CHARS or "\x00" in result:
        raise WindowsTargetFactsError("process query is invalid")
    return result.strip('"')


class WindowsTargetFactsAdapter:
    """TargetDiscovery-compatible views over one immutable OS sample."""

    def __init__(self, snapshot: WindowsTargetFactsSnapshot) -> None:
        if not isinstance(snapshot, WindowsTargetFactsSnapshot):
            raise TypeError("snapshot must be WindowsTargetFactsSnapshot")
        self._snapshot = snapshot
        self._process_by_id = {
            item.process_id: item for item in snapshot.processes
        }
        windows_by_pid: dict[int, list[WindowTargetFact]] = {}
        for window in snapshot.windows:
            windows_by_pid.setdefault(window.process_id, []).append(window)
        self._windows_by_pid = {
            process_id: tuple(windows)
            for process_id, windows in windows_by_pid.items()
        }

    @property
    def snapshot(self) -> WindowsTargetFactsSnapshot:
        return self._snapshot

    @staticmethod
    def _window_view(window: WindowTargetFact) -> dict[str, object]:
        return {
            "hwnd": window.hwnd,
            "pid": window.process_id,
            "title": window.title,
            "visible": window.visible,
        }

    def visible_windows(self) -> tuple[dict[str, object], ...]:
        return tuple(
            self._window_view(window)
            for window in self._snapshot.windows
            if window.visible
        )

    def process_identity(self, process_id: int) -> str:
        process_id = _positive_int(process_id, "process_id")
        fact = self._process_by_id.get(process_id)
        if fact is None:
            raise WindowsTargetFactsError("process identity is unavailable")
        return fact.process_identity

    def process_windows(self, query: str) -> dict[str, object]:
        query_value = _normalized_process_term(query)
        needle = query_value.removesuffix(".exe")
        matches: list[ProcessTargetFact] = []
        for process in self._snapshot.processes:
            path = ntpath.normcase(ntpath.normpath(unicodedata.normalize(
                "NFKC", process.executable_path,
            ))).casefold()
            basename = ntpath.basename(path)
            stem = ntpath.splitext(basename)[0]
            query_path = ntpath.normcase(ntpath.normpath(query_value)).casefold()
            if (
                needle == basename
                or needle == stem
                or (ntpath.isabs(query_value) and query_path == path)
            ):
                matches.append(process)
        windows = tuple(
            self._window_view(window)
            for process in matches
            for window in self._windows_by_pid.get(process.process_id, ())
        )
        return {
            "running": bool(matches),
            "process_count": len(matches),
            "pids": tuple(process.process_id for process in matches),
            "windows": windows,
            "has_visible_window": any(
                bool(window["visible"]) for window in windows
            ),
        }


__all__ = [
    "MAX_PROCESS_FACTS", "MAX_WINDOW_FACTS",
    "ProcessTargetFact", "PsutilWin32TargetFactsBackend",
    "WINDOWS_TARGET_FACTS_FORMAT", "WindowTargetFact",
    "WindowsTargetFactsAdapter", "WindowsTargetFactsBackend",
    "WindowsTargetFactsError", "WindowsTargetFactsReader",
    "WindowsTargetFactsSnapshot",
]
