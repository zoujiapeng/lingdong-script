from __future__ import annotations

from threading import Event

import pytest

import winpilot.vnext.targeting.fresh_windows_discovery as discovery_module

from winpilot.vnext.targeting.contracts import MAX_TARGET_CANDIDATES
from winpilot.vnext.targeting.fresh_windows_discovery import (
    FreshWindowsTargetDiscovery,
)
from winpilot.vnext.targeting.windows_facts import (
    WindowsTargetFactsError,
    WindowsTargetFactsReader,
)


class ChangingBackend:
    def __init__(self) -> None:
        self.process_reads = 0
        self.window_reads = 0
        self._generation = 0

    def read_processes(self):
        self.process_reads += 1
        self._generation += 1
        generation = self._generation
        return ({
            "process_id": 30 + generation,
            "creation_time_ns": 1_000_000_000 + generation,
            "executable_path": rf"C:\Apps\Orbit\orbit-{generation}.exe",
        },)

    def read_windows(self):
        self.window_reads += 1
        generation = self._generation
        return ({
            "hwnd": 100 + generation,
            "process_id": 30 + generation,
            "title": "MiXeD 应用 workspace",
            "visible": True,
        },)


class FailingBackend:
    def __init__(self) -> None:
        self.process_reads = 0
        self.window_reads = 0

    def read_processes(self):
        self.process_reads += 1
        raise OSError("process inventory unavailable")

    def read_windows(self):
        self.window_reads += 1
        raise AssertionError("window inventory must not follow a failed process read")


def test_construction_is_lazy_and_each_discovery_reads_one_fresh_snapshot() -> None:
    backend = ChangingBackend()
    reader_creations = []
    installed_calls = []

    def reader_factory():
        reader_creations.append(True)
        return WindowsTargetFactsReader(backend, clock=lambda: 10.0)

    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=reader_factory,
        installed_resolver=lambda query, limit: (
            installed_calls.append((query, limit)) or ()
        ),
        clock=lambda: 20.0,
    )

    assert reader_creations == []
    assert backend.process_reads == 0
    assert backend.window_reads == 0

    first = discovery.discover("MiXeD 应用")

    assert len(reader_creations) == 1
    assert backend.process_reads == 1
    assert backend.window_reads == 1
    assert installed_calls == [("MiXeD 应用", MAX_TARGET_CANDIDATES)]
    assert first.query == "MiXeD 应用"
    assert first.captured_at == 10.0
    assert [candidate.hwnd for candidate in first.candidates] == [101]

    second = discovery.discover("MiXeD 应用")

    assert len(reader_creations) == 2
    assert backend.process_reads == 2
    assert backend.window_reads == 2
    assert installed_calls == [
        ("MiXeD 应用", MAX_TARGET_CANDIDATES),
        ("MiXeD 应用", MAX_TARGET_CANDIDATES),
    ]
    assert [candidate.hwnd for candidate in second.candidates] == [102]
    assert second.captured_at == 10.0
    assert (
        first.candidates[0].process_identity
        != second.candidates[0].process_identity
    )


def test_windows_fact_source_failure_is_fail_closed_before_installed_lookup() -> None:
    backend = FailingBackend()
    installed_calls = []
    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda query, limit: installed_calls.append(
            (query, limit)
        ),
    )

    with pytest.raises(
        WindowsTargetFactsError,
        match="Windows target facts source is unavailable",
    ):
        discovery.discover("Orbit")

    assert backend.process_reads == 1
    assert backend.window_reads == 0
    assert installed_calls == []


def test_reader_factory_must_return_the_canonical_read_only_reader() -> None:
    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=lambda: object(),
        installed_resolver=lambda _query, _limit: (),
    )

    with pytest.raises(TypeError, match="WindowsTargetFactsReader"):
        discovery.discover("Orbit")


def test_pre_canceled_discovery_does_not_sample_windows_or_installed_apps() -> None:
    backend = ChangingBackend()
    installed_calls = []
    stop = Event()
    stop.set()
    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda query, limit: installed_calls.append(
            (query, limit)
        ) or (),
    )

    with pytest.raises(RuntimeError, match="canceled"):
        discovery.discover("Orbit", stop_event=stop)

    assert backend.process_reads == backend.window_reads == 0
    assert installed_calls == []


def test_stop_during_installed_iteration_aborts_before_candidates_escape() -> None:
    backend = ChangingBackend()
    stop = Event()

    def installed(_query, _limit):
        yield {"name": "Orbit", "kind": "exe", "target": r"C:\\Orbit.exe"}
        stop.set()
        yield {"name": "Orbit 2", "kind": "exe", "target": r"C:\\Orbit2.exe"}

    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=installed,
    )

    with pytest.raises(RuntimeError, match="canceled"):
        discovery.discover("Orbit", stop_event=stop)

    assert backend.process_reads == backend.window_reads == 1


def test_adapter_passes_the_query_to_target_discovery_without_rewriting(
    monkeypatch,
) -> None:
    backend = ChangingBackend()
    observed = []
    marker = object()

    class SpyDiscovery:
        def __init__(self, **_kwargs):
            pass

        def discover(self, query):
            observed.append(query)
            return marker

    monkeypatch.setattr(discovery_module, "TargetDiscovery", SpyDiscovery)
    discovery = FreshWindowsTargetDiscovery(
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda _query, _limit: (),
    )

    result = discovery.discover("  MiXeD 应用  ")

    assert result is marker
    assert observed == ["  MiXeD 应用  "]
    assert backend.process_reads == 1
    assert backend.window_reads == 1


@pytest.mark.parametrize("dependency", ("factory", "installed", "clock"))
def test_constructor_rejects_non_callable_dependencies(dependency: str) -> None:
    kwargs = {
        "facts_reader_factory": lambda: WindowsTargetFactsReader(
            ChangingBackend()
        ),
        "installed_resolver": lambda _query, _limit: (),
        "clock": lambda: 1.0,
    }
    kwargs[{
        "factory": "facts_reader_factory",
        "installed": "installed_resolver",
        "clock": "clock",
    }[dependency]] = None if dependency != "factory" else 1

    if dependency in {"factory", "installed"}:
        kwargs[{
            "factory": "facts_reader_factory",
            "installed": "installed_resolver",
        }[dependency]] = 1

    with pytest.raises(TypeError, match="callable"):
        FreshWindowsTargetDiscovery(**kwargs)
