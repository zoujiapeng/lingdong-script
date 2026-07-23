from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import random

import pytest

from winpilot.vnext.targeting.contracts import process_identity_for
from winpilot.vnext.targeting.discovery import TargetDiscovery
from winpilot.vnext.targeting.windows_facts import (
    ProcessTargetFact,
    WINDOWS_TARGET_FACTS_FORMAT,
    WindowTargetFact,
    WindowsTargetFactsAdapter,
    WindowsTargetFactsError,
    WindowsTargetFactsReader,
    WindowsTargetFactsSnapshot,
)


def _process(
    process_id: int = 31,
    *,
    path: str = r"C:\Apps\Orbit\orbit.exe",
    created: int = 1_700_000_000_123_456_000,
) -> dict[str, object]:
    return {
        "process_id": process_id,
        "creation_time_ns": created,
        "executable_path": path,
    }


def _window(
    hwnd: int = 101,
    *,
    process_id: int = 31,
    title: str = "Orbit document",
    visible: bool = True,
) -> dict[str, object]:
    return {
        "hwnd": hwnd,
        "process_id": process_id,
        "title": title,
        "visible": visible,
    }


class Backend:
    def __init__(self, *, processes=(), windows=()) -> None:
        self.processes = tuple(processes)
        self.windows = tuple(windows)
        self.process_reads = 0
        self.window_reads = 0

    def read_processes(self):
        self.process_reads += 1
        return self.processes

    def read_windows(self):
        self.window_reads += 1
        return self.windows


def _read(*, processes=(), windows=()):
    backend = Backend(processes=processes, windows=windows)
    snapshot = WindowsTargetFactsReader(
        backend, clock=lambda: 123.25,
    ).read()
    return backend, snapshot


def test_reader_samples_each_os_catalog_once_and_builds_typed_snapshot():
    backend, snapshot = _read(
        processes=(_process(),), windows=(_window(),),
    )

    assert backend.process_reads == 1
    assert backend.window_reads == 1
    assert snapshot.format == WINDOWS_TARGET_FACTS_FORMAT
    assert snapshot.captured_at == 123.25
    assert snapshot.processes[0].process_identity == process_identity_for(
        process_id=31,
        creation_time_ns=1_700_000_000_123_456_000,
        executable_path=r"C:\Apps\Orbit\orbit.exe",
    )
    assert snapshot.windows == (
        WindowTargetFact(101, 31, "Orbit document", True),
    )


def test_adapter_reuses_one_snapshot_for_many_alias_queries():
    backend, snapshot = _read(
        processes=(_process(),),
        windows=(_window(101, visible=False),),
    )
    adapter = WindowsTargetFactsAdapter(snapshot)

    for term in ("orbit.exe", "orbit", r"C:\Apps\Orbit\orbit.exe"):
        state = adapter.process_windows(term)
        assert state["running"] is True
        assert state["pids"] == (31,)
        assert state["windows"][0]["hwnd"] == 101

    assert adapter.visible_windows() == ()
    assert backend.process_reads == 1
    assert backend.window_reads == 1


def test_directory_name_does_not_create_a_false_process_match():
    _, snapshot = _read(processes=(
        _process(path=r"C:\Apps\Orbit\orbit.exe"),
    ))

    state = WindowsTargetFactsAdapter(snapshot).process_windows("apps")

    assert state["running"] is False
    assert state["windows"] == ()


def test_process_name_substring_does_not_suppress_a_distinct_application():
    _, snapshot = _read(processes=(
        _process(path=r"C:\Apps\Orbital\orbital-helper.exe"),
    ))

    state = WindowsTargetFactsAdapter(snapshot).process_windows("orbit")

    assert state["running"] is False
    assert state["process_count"] == 0


def test_target_discovery_alias_fanout_never_rescans_windows_or_processes():
    backend, snapshot = _read(
        processes=(_process(),),
        windows=(_window(101, visible=False),),
    )
    adapter = WindowsTargetFactsAdapter(snapshot)
    installed = ({
        "name": "Orbit Workspace",
        "kind": "exe",
        "target": r"C:\Apps\Orbit\orbit.exe",
        "process_name": "orbit.exe",
        "aliases": ["orbit", "orbit workspace", "workspace"],
    },)
    discovery = TargetDiscovery(
        installed_resolver=lambda _query, _limit: installed,
        visible_windows=adapter.visible_windows,
        process_windows=adapter.process_windows,
        process_identity=adapter.process_identity,
        clock=lambda: 200,
    )

    result = discovery.discover("Orbit Workspace")

    assert [(item.hwnd, item.state.value) for item in result.candidates] == [
        (101, "recoverable"),
    ]
    assert backend.process_reads == 1
    assert backend.window_reads == 1


def test_process_identity_detects_pid_reuse_by_time_or_executable_path():
    _, first = _read(processes=(_process(),))
    _, later = _read(processes=(_process(created=1_700_000_000_123_456_001),))
    _, other_binary = _read(processes=(_process(path=r"C:\Other\orbit.exe"),))

    identities = {
        first.processes[0].process_identity,
        later.processes[0].process_identity,
        other_binary.processes[0].process_identity,
    }
    assert len(identities) == 3


def test_window_fact_is_bound_to_hwnd_and_pid_and_uses_process_identity():
    _, snapshot = _read(
        processes=(_process(31), _process(32, path=r"C:\Apps\Other.exe")),
        windows=(_window(101, process_id=31), _window(102, process_id=32)),
    )
    adapter = WindowsTargetFactsAdapter(snapshot)

    assert adapter.process_windows("orbit")["windows"] == ({
        "hwnd": 101,
        "pid": 31,
        "title": "Orbit document",
        "visible": True,
    },)
    assert adapter.process_identity(31) != adapter.process_identity(32)


def test_well_formed_window_without_strong_process_identity_is_not_exposed():
    _, snapshot = _read(
        processes=(_process(31),),
        windows=(_window(101, process_id=999), _window(102, process_id=31)),
    )

    assert tuple(item.hwnd for item in snapshot.windows) == (102,)


@pytest.mark.parametrize(
    "row, message",
    [
        ({**_process(), "unexpected": True}, "schema"),
        ({"process_id": 31, "creation_time_ns": 1}, "schema"),
        ({**_process(), "process_id": True}, "positive integer"),
        ({**_process(), "creation_time_ns": 1.5}, "positive integer"),
        ({**_process(), "executable_path": "orbit.exe"}, "absolute"),
        ({**_process(), "executable_path": r"\Apps\orbit.exe"}, "absolute"),
        ({**_process(), "executable_path": "C:\\bad\x00.exe"}, "invalid"),
    ],
)
def test_dirty_process_rows_fail_closed(row, message):
    with pytest.raises(WindowsTargetFactsError, match=message):
        _read(processes=(row,))


@pytest.mark.parametrize(
    "row, message",
    [
        ({**_window(), "unexpected": True}, "schema"),
        ({"hwnd": 101, "process_id": 31, "title": "Orbit"}, "schema"),
        ({**_window(), "hwnd": True}, "positive integer"),
        ({**_window(), "process_id": 1.25}, "positive integer"),
        ({**_window(), "title": 7}, "must be a string"),
        ({**_window(), "visible": 1}, "must be boolean"),
    ],
)
def test_dirty_window_rows_fail_closed(row, message):
    with pytest.raises(WindowsTargetFactsError, match=message):
        _read(processes=(_process(),), windows=(row,))


@pytest.mark.parametrize("field", ["processes", "windows"])
def test_duplicate_runtime_identity_fails_closed(field):
    processes = (_process(), _process()) if field == "processes" else (_process(),)
    windows = (_window(), _window()) if field == "windows" else ()

    with pytest.raises(WindowsTargetFactsError, match="must be unique"):
        _read(processes=processes, windows=windows)


@pytest.mark.parametrize("captured_at", [True, -1, float("inf"), float("nan")])
def test_invalid_capture_time_fails_closed(captured_at):
    backend = Backend(processes=(_process(),), windows=())
    with pytest.raises(WindowsTargetFactsError, match="captured_at"):
        WindowsTargetFactsReader(
            backend, clock=lambda: captured_at,
        ).read()


def test_source_failure_is_not_fabricated_as_an_empty_snapshot():
    class BrokenBackend(Backend):
        def read_processes(self):
            raise PermissionError("denied")

    with pytest.raises(WindowsTargetFactsError, match="source is unavailable"):
        WindowsTargetFactsReader(BrokenBackend()).read()


@pytest.mark.parametrize("query", ["", "   ", 7, "x" * 513, "bad\x00name"])
def test_invalid_process_query_fails_closed(query):
    _, snapshot = _read(processes=(_process(),))
    with pytest.raises(WindowsTargetFactsError, match="query"):
        WindowsTargetFactsAdapter(snapshot).process_windows(query)


@pytest.mark.parametrize("process_id", [0, -1, True, 1.5, 999])
def test_unknown_or_invalid_process_identity_lookup_fails_closed(process_id):
    _, snapshot = _read(processes=(_process(),))
    with pytest.raises(WindowsTargetFactsError):
        WindowsTargetFactsAdapter(snapshot).process_identity(process_id)


def test_direct_contract_rejects_forged_process_identity():
    with pytest.raises(WindowsTargetFactsError, match="does not match"):
        ProcessTargetFact(
            process_id=31,
            creation_time_ns=100,
            executable_path=r"C:\Apps\Orbit.exe",
            process_identity="process-" + "0" * 32,
        )


def test_direct_snapshot_rejects_window_without_process_cross_reference():
    process = ProcessTargetFact.from_raw(_process(31))
    with pytest.raises(WindowsTargetFactsError, match="strong process identity"):
        WindowsTargetFactsSnapshot(
            processes=(process,),
            windows=(WindowTargetFact(101, 32, "Other", True),),
            captured_at=1,
        )


def test_direct_snapshot_rejects_unknown_format_and_noncanonical_order():
    first = ProcessTargetFact.from_raw(_process(31))
    second = ProcessTargetFact.from_raw(_process(32, path=r"C:\Apps\Other.exe"))
    with pytest.raises(WindowsTargetFactsError, match="unsupported"):
        WindowsTargetFactsSnapshot(
            processes=(first,), windows=(), captured_at=1, format="future/9",
        )
    with pytest.raises(WindowsTargetFactsError, match="canonical PID order"):
        WindowsTargetFactsSnapshot(
            processes=(second, first), windows=(), captured_at=1,
        )


def test_input_order_does_not_change_canonical_snapshot():
    processes = [
        _process(31),
        _process(32, path=r"C:\Apps\Other.exe"),
        _process(33, path=r"C:\Apps\Third.exe"),
    ]
    windows = [
        _window(101, process_id=31),
        _window(102, process_id=32),
        _window(103, process_id=33),
    ]
    expected = _read(processes=processes, windows=windows)[1]
    rng = random.Random(8128)
    for _ in range(30):
        rng.shuffle(processes)
        rng.shuffle(windows)
        assert _read(processes=processes, windows=windows)[1] == expected


def test_snapshot_and_facts_are_immutable():
    _, snapshot = _read(processes=(_process(),), windows=(_window(),))

    with pytest.raises(FrozenInstanceError):
        snapshot.captured_at = 2
    with pytest.raises(FrozenInstanceError):
        snapshot.processes[0].process_id = 99
    with pytest.raises(FrozenInstanceError):
        snapshot.windows[0].hwnd = 99


def test_adapter_does_not_turn_a_stale_snapshot_into_a_mutable_ledger():
    _, snapshot = _read(processes=(_process(),), windows=(_window(),))
    adapter = WindowsTargetFactsAdapter(snapshot)

    first = adapter.visible_windows()
    first[0]["title"] = "mutated caller copy"

    assert adapter.visible_windows()[0]["title"] == "Orbit document"
    assert adapter.snapshot is snapshot


def test_process_fact_path_normalization_preserves_identity_validation():
    raw = _process(path=r"C:\Apps\Orbit\..\Orbit\orbit.exe")
    fact = ProcessTargetFact.from_raw(raw)

    assert fact.executable_path == r"C:\Apps\Orbit\orbit.exe"
    assert replace(fact) == fact
