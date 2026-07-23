"""Strict, read-only executable-presence facts for cold launch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
import random
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from winpilot.vnext.targeting.executable_presence import (
    EXECUTABLE_PRESENCE_FORMAT,
    ExecutablePresenceError,
    ExecutablePresenceReader,
    ExecutablePresenceSnapshot,
    PsutilExecutablePresenceBackend,
)

EXACT_PATH = r"C:\Apps\Orbit\Orbit.exe"


def _row(
    process_id: int,
    *,
    path: str | None = EXACT_PATH,
    created: int | None = 1_000,
    name: str | None = "Orbit.exe",
) -> dict[str, object]:
    return {
        "process_id": process_id,
        "creation_time_ns": created,
        "executable_path": path,
        "process_name": name,
    }


class _Backend:
    def __init__(self, rows) -> None:
        self.rows = tuple(rows)
        self.calls = 0

    def read_processes(self):
        self.calls += 1
        return self.rows


def _read(rows, *, clock=lambda: 42.5) -> ExecutablePresenceSnapshot:
    return ExecutablePresenceReader(
        _Backend(rows),
        clock=clock,
    ).read(EXACT_PATH)


def test_presence_reports_only_exact_strong_instances_and_unresolved_peers() -> None:
    backend = _Backend(
        (
            _row(9, path=None),
            _row(7),
            _row(8, created=None),
            _row(6, path=r"D:\Other\Orbit.exe"),
            _row(5, path=None, name="Other.exe"),
        )
    )
    snapshot = ExecutablePresenceReader(
        backend,
        clock=lambda: 42.5,
    ).read(EXACT_PATH)

    assert snapshot.format == EXECUTABLE_PRESENCE_FORMAT
    assert snapshot.executable_path == EXACT_PATH
    assert tuple(item.process_id for item in snapshot.exact_instances) == (7,)
    assert snapshot.exact_instances[0].executable_path == EXACT_PATH
    assert snapshot.exact_instances[0].process_identity.startswith("process-")
    assert snapshot.unresolved_process_ids == (8, 9)
    assert snapshot.captured_at == 42.5
    assert backend.calls == 1


@pytest.mark.parametrize(
    "path",
    (
        r"Apps\Orbit.exe",
        r"C:\Apps\Orbit\Orbit.com",
        r"C:\Apps\Orbit\..\Orbit\Orbit.exe",
        "",
        b"C:\\Apps\\Orbit\\Orbit.exe",
    ),
)
def test_presence_rejects_noncanonical_nonabsolute_or_nonexe_query(
    path: object,
) -> None:
    with pytest.raises(ExecutablePresenceError):
        ExecutablePresenceReader(_Backend(())).read(path)  # type: ignore[arg-type]


def test_snapshot_strict_wire_roundtrip_is_canonical() -> None:
    snapshot = _read((_row(8, created=None), _row(7)))
    raw = snapshot.to_dict()

    assert raw["format"] == "executable-presence/0"
    assert raw["exact_instances"][0]["process_id"] == 7
    assert raw["unresolved_process_ids"] == [8]
    assert ExecutablePresenceSnapshot.from_dict(raw) == snapshot
    assert json.loads(snapshot.to_json()) == raw
    assert (
        ExecutablePresenceSnapshot.from_dict(
            dict(reversed(tuple(raw.items())))
        ).to_json()
        == snapshot.to_json()
    )


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda raw: {
            key: value for key, value in raw.items() if key != "executable_path"
        },
        lambda raw: {**raw, "extra": True},
        lambda raw: {**raw, "format": "executable-presence/1"},
        lambda raw: {**raw, "captured_at": math.nan},
        lambda raw: {**raw, "unresolved_process_ids": [True]},
        lambda raw: {**raw, "unresolved_process_ids": [9, 8]},
        lambda raw: {**raw, "unresolved_process_ids": [8, 8]},
        lambda raw: {
            **raw,
            "unresolved_process_ids": [
                raw["exact_instances"][0]["process_id"],
            ],
        },
        lambda raw: {
            **raw,
            "exact_instances": [
                {
                    **raw["exact_instances"][0],
                    "executable_path": r"D:\Other\Orbit.exe",
                }
            ],
        },
        lambda raw: {
            **raw,
            "exact_instances": [
                {
                    **raw["exact_instances"][0],
                    "process_identity": "process-invented",
                }
            ],
        },
    ),
)
def test_snapshot_rejects_malformed_old_or_contradictory_wire_data(
    rewrite,
) -> None:
    raw = _read((_row(8, created=None), _row(7))).to_dict()
    with pytest.raises(ExecutablePresenceError):
        ExecutablePresenceSnapshot.from_dict(rewrite(raw))


def test_backend_rows_are_strict_and_duplicate_pid_fails_closed() -> None:
    for rows in (
        ({"process_id": 7},),
        (_row(True),),
        (_row(7, created=True),),
        (_row(7, path=1),),
        (_row(7), _row(7)),
    ):
        with pytest.raises(ExecutablePresenceError):
            _read(rows)


def test_random_backend_order_produces_stable_sorted_replay() -> None:
    rows = [
        _row(20, created=None),
        _row(3),
        _row(11, path=None),
        _row(5),
        _row(8, path=r"D:\Other\Orbit.exe"),
    ]
    expected = _read(rows).to_json()
    rng = random.Random(8821)
    for _ in range(80):
        rng.shuffle(rows)
        assert _read(rows).to_json() == expected


class _ConcurrencyGuardedBackend:
    def __init__(self) -> None:
        self._gate = threading.Lock()
        self.calls = 0

    def read_processes(self):
        if not self._gate.acquire(blocking=False):
            raise AssertionError("backend was entered concurrently")
        try:
            self.calls += 1
            time.sleep(0.002)
            return (_row(7),)
        finally:
            self._gate.release()


def test_shared_reader_serializes_backend_access_and_returns_replayable_facts() -> None:
    backend = _ConcurrencyGuardedBackend()
    reader = ExecutablePresenceReader(backend, clock=lambda: 7.0)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _index: reader.read(EXACT_PATH), range(48)))

    assert backend.calls == 48
    assert len({item.to_json() for item in results}) == 1


def test_backend_failure_is_fail_closed() -> None:
    class Broken:
        @staticmethod
        def read_processes():
            raise OSError("process table unavailable")

    with pytest.raises(ExecutablePresenceError, match="unavailable"):
        ExecutablePresenceReader(Broken()).read(EXACT_PATH)


def test_psutil_backend_is_one_read_only_walk(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    class Process:
        def __init__(self, info):
            self.info = info

    def process_iter(attrs):
        calls.append(tuple(attrs))
        return (
            Process(
                {
                    "pid": 7,
                    "create_time": 1.5,
                    "exe": EXACT_PATH,
                    "name": "Orbit.exe",
                }
            ),
            Process(
                {
                    "pid": 8,
                    "create_time": None,
                    "exe": None,
                    "name": "Orbit.exe",
                }
            ),
            Process(
                {
                    "pid": 9,
                    "create_time": 2.0,
                    "exe": "",
                    "name": "Orbit.exe",
                }
            ),
            Process(
                {
                    "pid": 10,
                    "create_time": 2.5,
                    "exe": " ",
                    "name": "\t",
                }
            ),
        )

    fake = SimpleNamespace(
        process_iter=process_iter,
        AccessDenied=type("AccessDenied", (Exception,), {}),
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        ZombieProcess=type("ZombieProcess", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)

    rows = PsutilExecutablePresenceBackend.read_processes()

    assert calls == [("pid", "create_time", "exe", "name")]
    assert rows == (
        _row(7, created=1_500_000_000),
        _row(8, path=None, created=None),
        _row(9, path=None, created=2_000_000_000),
        _row(10, path=None, created=2_500_000_000, name=None),
    )
