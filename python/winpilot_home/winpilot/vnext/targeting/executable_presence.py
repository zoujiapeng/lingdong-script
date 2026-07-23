"""Read-only presence facts for one canonical Windows executable path."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
import json
import math
import ntpath
import threading
import time

from ...process_identity import (
    MAX_PROCESS_EXECUTABLE_PATH_CHARS,
    ProcessIdentityError,
    canonical_executable_path as _canonical_executable_path,
)
from .windows_facts import MAX_PROCESS_FACTS, ProcessTargetFact

EXECUTABLE_PRESENCE_FORMAT = "executable-presence/0"
_ROW_FIELDS = frozenset("process_id creation_time_ns executable_path process_name".split())
_SNAPSHOT_FIELDS = frozenset(
    "format executable_path exact_instances unresolved_process_ids captured_at".split()
)


class ExecutablePresenceError(ValueError):
    """Presence input or OS facts are incomplete or non-canonical."""


def canonical_executable_path(value: object) -> str:
    try:
        return _canonical_executable_path(value)
    except ProcessIdentityError as exc:
        raise ExecutablePresenceError(str(exc)) from exc


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise ExecutablePresenceError(f"{name} is invalid")
    if "\x00" in value or len(value) > MAX_PROCESS_EXECUTABLE_PATH_CHARS:
        raise ExecutablePresenceError(f"{name} is invalid")
    return value


def _row(value: object) -> tuple[int, int | None, str | None, str | None]:
    if type(value) is not dict or set(value) != _ROW_FIELDS:
        raise ExecutablePresenceError("process row has an invalid schema")
    pid, created = value["process_id"], value["creation_time_ns"]
    if type(pid) is not int or pid <= 0:
        raise ExecutablePresenceError("process_id must be positive")
    if created is not None and (type(created) is not int or created <= 0):
        raise ExecutablePresenceError("creation_time_ns must be positive or null")
    path = _optional_text(value["executable_path"], "executable_path")
    name = _optional_text(value["process_name"], "process_name")
    return pid, created, path, name


def _bounded_rows(value: object) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ExecutablePresenceError("process sample must be an iterable")
    rows: list[object] = []
    for row in value:
        if len(rows) >= MAX_PROCESS_FACTS:
            raise ExecutablePresenceError("process sample is too large")
        rows.append(row)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class ExecutablePresenceSnapshot:
    executable_path: str
    exact_instances: tuple[ProcessTargetFact, ...]
    unresolved_process_ids: tuple[int, ...]
    captured_at: float
    format: str = EXECUTABLE_PRESENCE_FORMAT

    def __post_init__(self) -> None:
        path = canonical_executable_path(self.executable_path)
        exact, unresolved = self.exact_instances, self.unresolved_process_ids
        if self.format != EXECUTABLE_PRESENCE_FORMAT:
            raise ExecutablePresenceError("unsupported presence format")
        if type(exact) is not tuple or any(
            type(item) is not ProcessTargetFact for item in exact
        ):
            raise ExecutablePresenceError("exact_instances are not canonical")
        exact_pids = tuple(item.process_id for item in exact)
        path_key = ntpath.normcase(path)
        if (
            len(exact) + len(unresolved) > MAX_PROCESS_FACTS
            or tuple(sorted(exact, key=lambda item: item.process_id)) != exact
            or len(exact_pids) != len(set(exact_pids))
            or any(ntpath.normcase(item.executable_path) != path_key for item in exact)
        ):
            raise ExecutablePresenceError("exact instance identity is invalid")
        if (
            type(unresolved) is not tuple
            or any(type(pid) is not int or pid <= 0 for pid in unresolved)
            or tuple(sorted(set(unresolved))) != unresolved
            or set(exact_pids).intersection(unresolved)
        ):
            raise ExecutablePresenceError("unresolved PIDs are not canonical")
        if (
            type(self.captured_at) not in {int, float}
            or not math.isfinite(float(self.captured_at))
            or float(self.captured_at) < 0
        ):
            raise ExecutablePresenceError("captured_at must be finite")
        object.__setattr__(self, "executable_path", path)
        object.__setattr__(self, "captured_at", float(self.captured_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "executable_path": self.executable_path,
            "exact_instances": [asdict(item) for item in self.exact_instances],
            "unresolved_process_ids": list(self.unresolved_process_ids),
            "captured_at": self.captured_at,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ExecutablePresenceSnapshot":
        if type(raw) is not dict or set(raw) != _SNAPSHOT_FIELDS:
            raise ExecutablePresenceError("presence snapshot has an invalid schema")
        instances, unresolved = raw["exact_instances"], raw["unresolved_process_ids"]
        if type(instances) is not list or type(unresolved) is not list:
            raise ExecutablePresenceError("presence collections must be arrays")
        try:
            result = cls(
                format=raw["format"],
                executable_path=raw["executable_path"],
                exact_instances=tuple(ProcessTargetFact(**item) for item in instances),
                unresolved_process_ids=tuple(unresolved),
                captured_at=raw["captured_at"],
            )
        except Exception as exc:
            raise ExecutablePresenceError("presence snapshot is invalid") from exc
        if result.to_dict() != raw:
            raise ExecutablePresenceError("presence snapshot is not canonical")
        return result


class PsutilExecutablePresenceBackend:
    @staticmethod
    def read_processes() -> tuple[Mapping[str, object], ...]:
        import psutil

        rows: list[Mapping[str, object]] = []
        for process in psutil.process_iter(["pid", "create_time", "exe", "name"]):
            try:
                info = process.info
                pid, created = info.get("pid"), info.get("create_time")
                if type(pid) is not int or pid <= 0:
                    continue
                created_ns = None
                if type(created) in {int, float} and math.isfinite(float(created)):
                    created_ns = (
                        int(float(created) * 1_000_000_000) if created > 0 else None
                    )
                rows.append(
                    {
                        "process_id": pid,
                        "creation_time_ns": created_ns,
                        "executable_path": info.get("exe"),
                        "process_name": info.get("name"),
                    }
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return tuple(rows)


class ExecutablePresenceReader:
    """Thread-safe projection over a fresh, read-only process sample."""

    def __init__(
        self,
        backend: object | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        resolved = backend or PsutilExecutablePresenceBackend()
        if not callable(getattr(resolved, "read_processes", None)) or not callable(
            clock
        ):
            raise TypeError("presence backend and clock must be callable")
        self._backend, self._clock, self._lock = resolved, clock, threading.Lock()

    def read(self, executable_path: str) -> ExecutablePresenceSnapshot:
        target = canonical_executable_path(executable_path)
        try:
            with self._lock:
                rows = _bounded_rows(self._backend.read_processes())
                captured_at = self._clock()
            exact: list[ProcessTargetFact] = []
            unresolved: list[int] = []
            seen: set[int] = set()
            target_key = ntpath.normcase(target)
            target_name = ntpath.basename(target).casefold()
            for raw in rows:
                pid, created, path, name = _row(raw)
                if pid in seen:
                    raise ExecutablePresenceError("duplicate process_id")
                seen.add(pid)
                resolved_path = ntpath.normpath(path) if path else None
                basename = ntpath.basename(path or name or "").casefold()
                if basename != target_name:
                    continue
                if path and resolved_path == path and ntpath.isabs(path):
                    if ntpath.normcase(path) != target_key:
                        continue
                    if created is not None:
                        exact.append(
                            ProcessTargetFact.from_raw(
                                {
                                    "process_id": pid,
                                    "creation_time_ns": created,
                                    "executable_path": target,
                                }
                            )
                        )
                        continue
                unresolved.append(pid)
            return ExecutablePresenceSnapshot(
                target,
                tuple(sorted(exact, key=lambda item: item.process_id)),
                tuple(sorted(unresolved)), captured_at,
            )
        except ExecutablePresenceError:
            raise
        except Exception as exc:
            raise ExecutablePresenceError(
                "executable presence source is unavailable"
            ) from exc
