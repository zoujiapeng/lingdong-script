from __future__ import annotations

from threading import Event

import pytest

import winpilot.vnext.targeting.fresh_windows_discovery as discovery_module
import winpilot.vnext.bridge.coordinator as coordinator_module

from winpilot.vnext.capabilities.live_catalog import build_live_runtime_snapshot
from winpilot.vnext.targeting.contracts import MAX_TARGET_CANDIDATES
from winpilot.vnext.targeting.fresh_windows_discovery import (
    FreshWindowsTargetDiscovery,
)
from winpilot.vnext.targeting.windows_facts import (
    WindowsTargetFactsError,
    WindowsTargetFactsReader,
)
from winpilot.visual_runtime.atomic_click_controller import (
    REQUIRED_RAW_ACTION_NAMES,
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
        supported_launch_kinds=("exe",),
        facts_reader_factory=reader_factory,
        installed_resolver=lambda query, limit, kinds: (
            installed_calls.append((query, limit, kinds)) or ()
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
    assert installed_calls == [
        ("MiXeD 应用", MAX_TARGET_CANDIDATES, ("exe",)),
    ]
    assert first.query == "MiXeD 应用"
    assert first.captured_at == 10.0
    assert [candidate.hwnd for candidate in first.candidates] == [101]

    second = discovery.discover("MiXeD 应用")

    assert len(reader_creations) == 2
    assert backend.process_reads == 2
    assert backend.window_reads == 2
    assert installed_calls == [
        ("MiXeD 应用", MAX_TARGET_CANDIDATES, ("exe",)),
        ("MiXeD 应用", MAX_TARGET_CANDIDATES, ("exe",)),
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
        supported_launch_kinds=("exe",),
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda query, limit, kinds: installed_calls.append(
            (query, limit, kinds)
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
        supported_launch_kinds=("exe",),
        facts_reader_factory=lambda: object(),
        installed_resolver=lambda _query, _limit, _kinds: (),
    )

    with pytest.raises(TypeError, match="WindowsTargetFactsReader"):
        discovery.discover("Orbit")


def test_pre_canceled_discovery_does_not_sample_windows_or_installed_apps() -> None:
    backend = ChangingBackend()
    installed_calls = []
    stop = Event()
    stop.set()
    discovery = FreshWindowsTargetDiscovery(
        supported_launch_kinds=("exe",),
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda query, limit, kinds: installed_calls.append(
            (query, limit, kinds)
        ) or (),
    )

    with pytest.raises(RuntimeError, match="canceled"):
        discovery.discover("Orbit", stop_event=stop)

    assert backend.process_reads == backend.window_reads == 0
    assert installed_calls == []


def test_stop_during_installed_iteration_aborts_before_candidates_escape() -> None:
    backend = ChangingBackend()
    stop = Event()

    def installed(_query, _limit, _kinds):
        yield {"name": "Orbit", "kind": "exe", "target": r"C:\\Orbit.exe"}
        stop.set()
        yield {"name": "Orbit 2", "kind": "exe", "target": r"C:\\Orbit2.exe"}

    discovery = FreshWindowsTargetDiscovery(
        supported_launch_kinds=("exe",),
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
    init_kwargs = []
    marker = object()

    class SpyDiscovery:
        def __init__(self, **kwargs):
            init_kwargs.append(kwargs)

        def discover(self, query):
            observed.append(query)
            return marker

    monkeypatch.setattr(discovery_module, "TargetDiscovery", SpyDiscovery)
    discovery = FreshWindowsTargetDiscovery(
        supported_launch_kinds=("exe",),
        facts_reader_factory=lambda: WindowsTargetFactsReader(backend),
        installed_resolver=lambda _query, _limit, _kinds: (),
    )

    result = discovery.discover("  MiXeD 应用  ")

    assert result is marker
    assert observed == ["  MiXeD 应用  "]
    assert init_kwargs[0]["supported_launch_kinds"] == ("exe",)
    assert backend.process_reads == 1
    assert backend.window_reads == 1


def test_installed_catalog_builds_bounded_general_and_supported_lanes(
    monkeypatch,
) -> None:
    from winpilot.perception import apps

    general = [
        {
            "name": f"Orbit Store {index:02d}",
            "kind": "appid",
            "target": f"Vendor.Orbit{index}!App",
        }
        for index in range(MAX_TARGET_CANDIDATES - 1)
    ]
    executable = {
        "name": "Orbit Native",
        "kind": "exe",
        "target": r"C:\Orbit.exe",
    }
    general.extend([
        executable,
        *(
            {
                "name": f"Overflow Store {index:02d}",
                "kind": "appid",
                "target": f"Vendor.Overflow{index}!App",
            }
            for index in range(8)
        ),
    ])
    supported = [
        executable,
        *(
            {
                "name": f"Orbit Native {index:02d}",
                "kind": "exe",
                "target": rf"C:\Orbit-{index}.exe",
            }
            for index in range(20)
        ),
    ]
    calls = []

    def resolve(query, max_results, *, launch_kinds=None):
        calls.append((query, max_results, launch_kinds))
        return general if launch_kinds is None else supported

    monkeypatch.setattr(apps, "resolve", resolve)

    first = tuple(discovery_module._installed_apps(
        "Orbit",
        MAX_TARGET_CANDIDATES,
        ("exe",),
    ))
    second = tuple(discovery_module._installed_apps(
        "Orbit",
        MAX_TARGET_CANDIDATES,
        ("exe",),
    ))

    expected = (
        *general[:MAX_TARGET_CANDIDATES],
        *supported[1:MAX_TARGET_CANDIDATES],
    )
    assert first == second == expected
    assert len(first) == MAX_TARGET_CANDIDATES * 2 - 1
    assert calls == [
        ("Orbit", MAX_TARGET_CANDIDATES, None),
        ("Orbit", MAX_TARGET_CANDIDATES, ("exe",)),
    ] * 2


@pytest.mark.parametrize("dependency", ("factory", "installed", "clock"))
def test_constructor_rejects_non_callable_dependencies(dependency: str) -> None:
    kwargs = {
        "supported_launch_kinds": ("exe",),
        "facts_reader_factory": lambda: WindowsTargetFactsReader(
            ChangingBackend()
        ),
        "installed_resolver": lambda _query, _limit, _kinds: (),
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


@pytest.mark.parametrize(
    "supported",
    (["exe"], ("EXE",), ("exe", "exe"), ("unknown",)),
)
def test_malformed_launch_capability_is_rejected_before_reader_creation(
    supported,
) -> None:
    reader_creations = []

    with pytest.raises(ValueError, match="supported_launch_kinds"):
        FreshWindowsTargetDiscovery(
            supported_launch_kinds=supported,
            facts_reader_factory=lambda: reader_creations.append(True),
            installed_resolver=lambda _query, _limit, _kinds: (),
        )

    assert reader_creations == []


def test_product_composition_injects_only_the_executable_launch_capability(
    monkeypatch,
) -> None:
    observed = []

    class SpyFreshDiscovery:
        def __init__(self, **kwargs):
            observed.append(kwargs)

        def discover(self, _query, *, stop_event=None):
            raise AssertionError("composition test must not read target facts")

    class RawInventory:
        @property
        def admitted_action_names(self):
            return REQUIRED_RAW_ACTION_NAMES

    monkeypatch.setattr(
        coordinator_module,
        "FreshWindowsTargetDiscovery",
        SpyFreshDiscovery,
    )

    coordinator_module.LiveDesktopRuntimeCoordinator(
        api_config={},
        raw_device=RawInventory(),
        runtime_snapshot=build_live_runtime_snapshot(),
    )

    assert observed == [{
        "supported_launch_kinds": ("exe",),
        "clock": coordinator_module.time.time,
    }]
