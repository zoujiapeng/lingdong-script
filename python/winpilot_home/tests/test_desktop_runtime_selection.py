from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import threading
from types import ModuleType

import pytest
from pydantic import ValidationError

from winpilot.orchestrator.contracts import (
    ArpEdge,
    ArpGraph,
    ArpNode,
    ArpNodeKind,
    ArpPolicy,
    ArpRunRequest,
    ArpRunStatus,
)
from winpilot.orchestrator.desktop_runtime import (
    DesktopRuntimeCoordinatorContractError,
    DesktopRuntimeCoordinatorContext,
    DesktopRuntimeCoordinatorResult,
    DesktopRuntimeCoordinatorStatus,
)
from winpilot.orchestrator.runtime import ArpExecution, ArpRunManager, NodeResult
from winpilot.orchestrator.runtime_selection import (
    DesktopRuntimeSelection,
    DesktopRuntimeUnavailable,
    InvalidDesktopRuntimeSelection,
)
from winpilot.tasking.capability import (
    CapabilityPlanner,
    CapabilityUnsatisfiedError,
)
from winpilot.tasking.compiler import TaskContractCompiler
from winpilot.vnext.capabilities.live_catalog import (
    build_live_runtime_snapshot,
)


def _task_stage_graph(*, metadata: dict | None = None) -> ArpGraph:
    return ArpGraph(
        id="runtime-selection-graph",
        name="runtime selection",
        nodes=[
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="task",
                kind=ArpNodeKind.TASK_STAGE,
                config={
                    "stage": {
                        "id": "task",
                        "type": "ExecuteDesktopTask",
                        "name": "runtime selection probe",
                        "execution": {"satisfiable": True},
                    },
                },
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        edges=[
            ArpEdge(id="start-task", source="start", target="task"),
            ArpEdge(id="task-end", source="task", target="end"),
        ],
        policy=ArpPolicy(require_evidence_on_done=False),
        metadata=dict(metadata or {}),
    )


def _vnext_task_stage_graph(*, metadata: dict | None = None) -> ArpGraph:
    contract, graph = TaskContractCompiler().compile_graph(
        "Complete the task visible in the current window.",
        max_steps=8,
        cold_start=True,
        require_gui=True,
    )
    assert contract.format == "task-contract/1"
    assert contract.compiler_version == "typed-stage-5"
    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    graph.metadata.update(metadata or {})
    return graph


_DEFAULT_COORDINATOR = object()


def _install_live_coordinator_factory(
    monkeypatch,
    *,
    coordinator=_DEFAULT_COORDINATOR,
    failure: Exception | None = None,
    raw_devices: list | None = None,
):
    calls = []

    class Coordinator:
        runtime_capability_snapshot = build_live_runtime_snapshot()

        def run(self, _context):
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
                output={"runtime": "vnext"},
            )

    resolved = Coordinator() if coordinator is _DEFAULT_COORDINATOR else coordinator
    module = ModuleType("winpilot.vnext.bridge.coordinator")

    def build_live_desktop_runtime_coordinator(
        *, api_config, raw_device, trace_sidecar,
    ):
        assert isinstance(api_config, dict)
        assert raw_device is not None
        if raw_devices is not None:
            raw_devices.append(raw_device)
        calls.append(trace_sidecar)
        if failure is not None:
            raise failure
        return resolved

    module.build_live_desktop_runtime_coordinator = (
        build_live_desktop_runtime_coordinator
    )
    monkeypatch.setitem(
        sys.modules, "winpilot.vnext.bridge.coordinator", module,
    )
    return resolved, calls


def _install_server_run_capture(monkeypatch, server, *, calls=None):
    captured: dict[str, object] = {}
    call_log: list[str] = calls if calls is not None else []

    class Config:
        @staticmethod
        def get(*_path: str, default=None):
            return default

    class Runs:
        @staticmethod
        def prepare(
            graph,
            handlers,
            *,
            inputs,
            desktop_runtime_selection,
            desktop_runtime_coordinator,
        ):
            call_log.append("prepare")
            captured.update({
                "handlers": handlers,
                "selection": desktop_runtime_selection,
                "coordinator": desktop_runtime_coordinator,
            })
            return ArpExecution(
                graph,
                handlers,
                inputs=inputs,
                desktop_runtime_selection=desktop_runtime_selection,
                desktop_runtime_coordinator=desktop_runtime_coordinator,
            )

    class Jobs:
        @staticmethod
        def submit(*_args, **_kwargs):
            call_log.append("job")

    monkeypatch.setattr(server, "CONFIG", Config())
    monkeypatch.setattr(server, "ARP_RUNS", Runs())
    monkeypatch.setattr(server, "RUNTIME_JOBS", Jobs())
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)
    return captured, call_log


@pytest.mark.parametrize("raw", ["", "Legacy", "VNEXT", " vnext ", True, 1, {}, []])
def test_runtime_selection_rejects_every_unknown_or_coerced_value(raw: object) -> None:
    with pytest.raises(InvalidDesktopRuntimeSelection):
        DesktopRuntimeSelection.from_config(raw)


def test_runtime_selection_is_strict_immutable_and_defaults_to_legacy() -> None:
    defaulted = DesktopRuntimeSelection.from_config(None)
    assert defaulted.runtime_version == "legacy"
    assert DesktopRuntimeSelection.from_config("legacy") == defaulted
    assert DesktopRuntimeSelection.from_config("vnext").runtime_version == "vnext"

    with pytest.raises(FrozenInstanceError):
        defaulted.runtime_version = "vnext"  # type: ignore[misc]


def test_product_run_request_accepts_only_explicit_runtime_versions() -> None:
    graph = _task_stage_graph()

    assert ArpRunRequest(graph=graph).runtime_version is None
    assert ArpRunRequest(graph=graph, runtime_version="vnext").runtime_version == "vnext"
    with pytest.raises(ValidationError):
        ArpRunRequest(graph=graph, runtime_version="automatic")


def test_execution_context_uses_run_owned_selection_not_untrusted_payloads() -> None:
    selection = DesktopRuntimeSelection.from_config("legacy")
    graph = _task_stage_graph(metadata={
        "runtime_version": "vnext",
        "task_profile": {"runtime_version": "vnext"},
        "task": "vnext",
    })
    execution = ArpExecution(
        graph,
        inputs={
            "runtime_version": "vnext",
            "desktop_runtime_selection": {"runtime_version": "vnext"},
            "task": "vnext",
        },
        desktop_runtime_selection=selection,
    )

    context = execution._context()  # internal handler contract under test
    assert execution.desktop_runtime_selection is selection
    assert execution.to_dict()["runtime_version"] == "legacy"
    assert context["desktop_runtime_selection"] is selection
    assert context["desktop_runtime_selection"].runtime_version == "legacy"

    with pytest.raises(AttributeError):
        execution.desktop_runtime_selection = DesktopRuntimeSelection.from_config("vnext")


def test_explicit_vnext_fails_before_any_graph_handler_or_task_stage_mutation() -> None:
    calls: list[str] = []

    def forbidden_handler(_node, _context, _stop_event) -> NodeResult:
        calls.append("legacy_task_stage")
        return NodeResult(ok=True)

    execution = ArpExecution(
        _task_stage_graph(),
        {ArpNodeKind.TASK_STAGE: forbidden_handler},
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
    )

    snapshot = execution.run()

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert snapshot["error"] == DesktopRuntimeUnavailable.MESSAGE
    assert calls == []
    assert execution.history == []
    assert execution.outputs == {}
    assert execution.runtime_state == {}


def test_coordinator_contracts_reject_malformed_or_non_json_values() -> None:
    stop = threading.Event()
    with pytest.raises(DesktopRuntimeCoordinatorContractError, match="format"):
        DesktopRuntimeCoordinatorContext(
            run_id="run-1",
            graph={},
            inputs={},
            stop_event=stop,
            wait_if_paused=lambda: True,
            is_paused=lambda: False,
            format="desktop-runtime-coordinator-context/0",
        )
    with pytest.raises(DesktopRuntimeCoordinatorContractError, match="format"):
        DesktopRuntimeCoordinatorContext(
            run_id="run-1",
            graph={},
            inputs={},
            stop_event=stop,
            wait_if_paused=lambda: True,
            is_paused=lambda: False,
            format="desktop-runtime-coordinator-context/9",
        )
    with pytest.raises(DesktopRuntimeCoordinatorContractError, match="JSON"):
        DesktopRuntimeCoordinatorContext(
            run_id="run-1",
            graph={},
            inputs={"opaque": object()},
            stop_event=stop,
            wait_if_paused=lambda: True,
            is_paused=lambda: False,
        )
    with pytest.raises(DesktopRuntimeCoordinatorContractError, match="requires an error"):
        DesktopRuntimeCoordinatorResult(
            status=DesktopRuntimeCoordinatorStatus.FAILED,
        )
    with pytest.raises(DesktopRuntimeCoordinatorContractError, match="cannot carry an error"):
        DesktopRuntimeCoordinatorResult(
            status=DesktopRuntimeCoordinatorStatus.DONE,
            error="contradiction",
        )


def test_coordinator_cannot_be_injected_into_legacy_selection() -> None:
    class Coordinator:
        def run(self, _context):
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
            )

    with pytest.raises(ValueError, match="requires vnext"):
        ArpExecution(
            _task_stage_graph(),
            desktop_runtime_coordinator=Coordinator(),
        )


def test_explicit_vnext_coordinator_owns_run_once_and_bypasses_handlers(
    monkeypatch,
) -> None:
    import winpilot.orchestrator.runtime as runtime_module

    coordinator_calls: list[DesktopRuntimeCoordinatorContext] = []
    handler_calls: list[str] = []
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        runtime_module.BUS,
        "publish",
        lambda event, **payload: events.append((event, payload)),
    )

    def forbidden_handler(_node, _context, _stop_event) -> NodeResult:
        handler_calls.append("legacy_task_stage")
        return NodeResult(ok=True)

    class Coordinator:
        def run(
            self, context: DesktopRuntimeCoordinatorContext,
        ) -> DesktopRuntimeCoordinatorResult:
            coordinator_calls.append(context)
            assert context.run_id == execution.run_id
            assert context.stop_event is execution.stop_event
            assert context.wait_if_paused() is True
            assert context.is_paused() is False
            with pytest.raises(TypeError):
                context.inputs["runtime_version"] = "legacy"  # type: ignore[index]
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
                output={"runtime": "vnext"},
            )

    execution = ArpExecution(
        _task_stage_graph(),
        {ArpNodeKind.TASK_STAGE: forbidden_handler},
        inputs={"runtime_version": "untrusted"},
        run_id="vnext-coordinator-once",
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=Coordinator(),
    )

    snapshot = execution.run()

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"] == {"runtime": "vnext"}
    assert len(coordinator_calls) == 1
    assert handler_calls == []
    assert execution.history == []
    assert execution.outputs == {}
    assert [event for event, _payload in events] == [
        "arp_run_start",
        "arp_run_done",
    ]
    assert all(payload["run_id"] == execution.run_id for _event, payload in events)


def test_run_manager_prepare_passes_the_exact_vnext_coordinator() -> None:
    calls: list[DesktopRuntimeCoordinatorContext] = []

    class Coordinator:
        def run(
            self, context: DesktopRuntimeCoordinatorContext,
        ) -> DesktopRuntimeCoordinatorResult:
            calls.append(context)
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
                output={"owner": "vnext"},
            )

    coordinator = Coordinator()
    manager = ArpRunManager()
    execution = manager.prepare(
        _task_stage_graph(),
        {ArpNodeKind.TASK_STAGE: lambda *_args: pytest.fail(
            "legacy handler must not run"
        )},
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=coordinator,
    )

    snapshot = manager.run_prepared(execution)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"] == {"owner": "vnext"}
    assert len(calls) == 1
    assert calls[0].run_id == execution.run_id
    assert calls[0].stop_event is execution.stop_event


def test_vnext_coordinator_uses_existing_pause_gate() -> None:
    entered = threading.Event()
    coordinator_calls: list[str] = []
    finished: list[dict] = []

    class Coordinator:
        def run(
            self, context: DesktopRuntimeCoordinatorContext,
        ) -> DesktopRuntimeCoordinatorResult:
            coordinator_calls.append("vnext")
            entered.set()
            assert context.wait_if_paused() is True
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
                output={"resumed": True},
            )

    execution = ArpExecution(
        _task_stage_graph(),
        run_id="vnext-shared-pause",
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=Coordinator(),
    )
    assert execution.pause() is True
    thread = threading.Thread(target=lambda: finished.append(execution.run()))
    thread.start()
    assert not entered.wait(0.05)
    assert execution.resume() is True
    assert entered.wait(1.0)
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert coordinator_calls == ["vnext"]
    assert finished[0]["status"] == ArpRunStatus.DONE.value


def test_cancel_while_vnext_is_paused_never_enters_coordinator() -> None:
    coordinator_calls: list[str] = []
    finished: list[dict] = []

    class Coordinator:
        def run(
            self, _context: DesktopRuntimeCoordinatorContext,
        ) -> DesktopRuntimeCoordinatorResult:
            coordinator_calls.append("vnext")
            return DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.DONE,
            )

    execution = ArpExecution(
        _task_stage_graph(),
        run_id="vnext-cancel-before-entry",
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=Coordinator(),
    )
    assert execution.pause() is True
    thread = threading.Thread(target=lambda: finished.append(execution.run()))
    thread.start()
    assert not coordinator_calls
    execution.cancel()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert coordinator_calls == []
    assert finished[0]["status"] == ArpRunStatus.CANCELED.value
    assert finished[0]["error"] == "run canceled"


@pytest.mark.parametrize(
    ("coordinator_result", "error"),
    [
        (
            DesktopRuntimeCoordinatorResult(
                status=DesktopRuntimeCoordinatorStatus.UNAVAILABLE,
                error="vNext owner unavailable",
            ),
            "vNext owner unavailable",
        ),
        (RuntimeError("coordinator exploded"), "coordinator exploded"),
    ],
)
def test_vnext_coordinator_failure_never_falls_back_to_legacy(
    coordinator_result: DesktopRuntimeCoordinatorResult | Exception,
    error: str,
) -> None:
    handler_calls: list[str] = []
    coordinator_calls: list[str] = []

    class Coordinator:
        def run(
            self, _context: DesktopRuntimeCoordinatorContext,
        ) -> DesktopRuntimeCoordinatorResult:
            coordinator_calls.append("vnext")
            if isinstance(coordinator_result, Exception):
                raise coordinator_result
            return coordinator_result

    execution = ArpExecution(
        _task_stage_graph(),
        {
            ArpNodeKind.TASK_STAGE: lambda *_args: (
                handler_calls.append("legacy") or NodeResult(ok=True)
            ),
        },
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=Coordinator(),
    )

    snapshot = execution.run()

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert snapshot["error"] == error
    assert coordinator_calls == ["vnext"]
    assert handler_calls == []
    assert execution.history == []
    assert execution.outputs == {}


def test_malformed_coordinator_result_fails_without_legacy_fallback() -> None:
    handler_calls: list[str] = []

    class Coordinator:
        def run(self, _context):
            return {"status": "done"}

    execution = ArpExecution(
        _task_stage_graph(),
        {
            ArpNodeKind.TASK_STAGE: lambda *_args: (
                handler_calls.append("legacy") or NodeResult(ok=True)
            ),
        },
        desktop_runtime_selection=DesktopRuntimeSelection.from_config("vnext"),
        desktop_runtime_coordinator=Coordinator(),
    )

    snapshot = execution.run()

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert "invalid result" in snapshot["error"]
    assert handler_calls == []


def test_default_legacy_selection_preserves_existing_handler_execution(
    monkeypatch,
) -> None:
    import winpilot.orchestrator.runtime as runtime_module

    calls: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(
        runtime_module.BUS,
        "publish",
        lambda event, **_payload: events.append(event),
    )

    def handler(_node, context, _stop_event) -> NodeResult:
        calls.append(context["desktop_runtime_selection"].runtime_version)
        return NodeResult(ok=True, output={"legacy": True})

    execution = ArpExecution(
        _task_stage_graph(),
        {ArpNodeKind.TASK_STAGE: handler},
    )

    snapshot = execution.run()

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert calls == ["legacy"]
    assert events == [
        "arp_run_start",
        "arp_node_start",
        "arp_node_done",
        "arp_node_start",
        "arp_node_done",
        "arp_node_start",
        "arp_node_done",
        "arp_run_done",
    ]


def test_stage_runtime_defence_rejects_vnext_before_build_runner_or_state_write(
    monkeypatch,
) -> None:
    import winpilot.tasking.stage_runtime as stage_runtime

    built: list[object] = []
    monkeypatch.setattr(
        stage_runtime,
        "build_runner",
        lambda *_args, **_kwargs: built.append(object()),
    )
    context = {
        "run_id": "vnext-stage-direct-call",
        "runtime_state": {},
        "desktop_runtime_selection": DesktopRuntimeSelection.from_config("vnext"),
    }
    node = ArpNode(
        id="task",
        kind=ArpNodeKind.TASK_STAGE,
        config={"stage": {"not": "parsed"}},
    )

    with pytest.raises(DesktopRuntimeUnavailable, match="not available"):
        stage_runtime.StageRuntime().execute(node, context, threading.Event())

    assert built == []
    assert context["runtime_state"] == {}


@pytest.mark.parametrize(
    ("selected_version", "later_version"),
    (("legacy", "vnext"), ("vnext", "legacy")),
)
def test_server_reads_runtime_config_once_and_freezes_selection(
    monkeypatch,
    selected_version,
    later_version,
) -> None:
    import winpilot.server as server

    runtime_reads: list[tuple[str, ...]] = []

    class FlippingConfig:
        def get(self, *path: str, default=None):
            if path == ("vnext", "runtime_version"):
                runtime_reads.append(path)
                return selected_version if len(runtime_reads) == 1 else later_version
            if path in {
                ("vnext", "profiler_shadow"),
                ("vnext", "broker_shadow"),
            }:
                if selected_version == "vnext":
                    raise AssertionError("vNext must not read legacy shadow config")
                return {"enabled": False}
            return default

    captured: dict[str, object] = {}

    class Runs:
        @staticmethod
        def prepare(
            graph,
            handlers,
            *,
            inputs,
            desktop_runtime_selection,
            desktop_runtime_coordinator,
        ):
            captured["selection"] = desktop_runtime_selection
            captured["coordinator"] = desktop_runtime_coordinator
            captured["handlers"] = handlers
            return ArpExecution(
                graph,
                handlers,
                inputs=inputs,
                desktop_runtime_selection=desktop_runtime_selection,
                desktop_runtime_coordinator=desktop_runtime_coordinator,
            )

    class Jobs:
        @staticmethod
        def submit(*_args, **_kwargs):
            captured["submitted"] = True

    monkeypatch.setattr(server, "CONFIG", FlippingConfig())
    monkeypatch.setattr(server, "ARP_RUNS", Runs())
    monkeypatch.setattr(server, "RUNTIME_JOBS", Jobs())
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)
    coordinator, factory_calls = _install_live_coordinator_factory(monkeypatch)

    graph_builder = (
        _task_stage_graph
        if selected_version == "legacy"
        else _vnext_task_stage_graph
    )
    graph = graph_builder(metadata={
        "runtime_version": "vnext",
        "task_profile": {"runtime_version": "vnext"},
    })
    execution = server._start_arp_execution(
        graph,
        {
            "runtime_version": "vnext",
            "desktop_runtime_selection": "vnext",
            "profiler": {"runtime_version": "vnext"},
        },
    )
    try:
        selection = captured["selection"]
        assert isinstance(selection, DesktopRuntimeSelection)
        assert selection.runtime_version == selected_version
        assert execution.desktop_runtime_selection is selection
        assert runtime_reads == [("vnext", "runtime_version")]
        # The fake config now flips, but the existing run cannot switch.
        assert server.CONFIG.get("vnext", "runtime_version") == later_version
        assert execution.desktop_runtime_selection.runtime_version == selected_version
        assert captured["coordinator"] is (
            None if selected_version == "legacy" else coordinator
        )
        assert (captured["handlers"] is not None) is (
            selected_version == "legacy"
        )
        assert factory_calls == (
            [] if selected_version == "legacy" else [server.trace_mod.RECORDER]
        )
        assert captured["submitted"] is True
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


def test_explicit_product_run_selection_is_atomic_and_ignores_semantic_fields(
    monkeypatch,
) -> None:
    import winpilot.server as server

    config_reads = []

    class Config:
        @staticmethod
        def get(*path: str, default=None):
            if path == ("vnext", "runtime_version"):
                config_reads.append(path)
                return "legacy"
            if path in {
                ("vnext", "profiler_shadow"),
                ("vnext", "broker_shadow"),
            }:
                raise AssertionError("vNext must not read legacy shadow config")
            return default

    captured = {}

    class Runs:
        @staticmethod
        def prepare(
            graph,
            handlers,
            *,
            inputs,
            desktop_runtime_selection,
            desktop_runtime_coordinator,
        ):
            captured["selection"] = desktop_runtime_selection
            captured["coordinator"] = desktop_runtime_coordinator
            captured["handlers"] = handlers
            return ArpExecution(
                graph,
                handlers,
                inputs=inputs,
                desktop_runtime_selection=desktop_runtime_selection,
                desktop_runtime_coordinator=desktop_runtime_coordinator,
            )

    class Jobs:
        @staticmethod
        def submit(*_args, **_kwargs):
            return None

    monkeypatch.setattr(server, "CONFIG", Config())
    monkeypatch.setattr(server, "ARP_RUNS", Runs())
    monkeypatch.setattr(server, "RUNTIME_JOBS", Jobs())
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)
    coordinator, factory_calls = _install_live_coordinator_factory(monkeypatch)

    execution = server._start_arp_execution(
        _vnext_task_stage_graph(metadata={"runtime_version": "legacy"}),
        {"runtime_version": "legacy"},
        runtime_version="vnext",
    )
    try:
        assert captured["selection"].runtime_version == "vnext"
        assert execution.desktop_runtime_selection.runtime_version == "vnext"
        assert captured["coordinator"] is coordinator
        assert captured["handlers"] is None
        assert factory_calls == [server.trace_mod.RECORDER]
        assert config_reads == []
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


def test_vnext_server_admission_does_not_call_legacy_capability_preflight(
    monkeypatch,
) -> None:
    import winpilot.server as server

    graph = _vnext_task_stage_graph()
    captured, calls = _install_server_run_capture(monkeypatch, server)
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_satisfiable",
        staticmethod(lambda _contract: pytest.fail(
            "vNext admission must not call legacy static capability planning"
        )),
    )
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_runtime_satisfiable",
        staticmethod(lambda _contract: pytest.fail(
            "vNext admission must not probe legacy runtime providers"
        )),
    )
    coordinator, _factory_calls = _install_live_coordinator_factory(monkeypatch)

    execution = server._start_arp_execution(
        graph,
        {},
        runtime_version="vnext",
    )
    try:
        assert calls == ["prepare", "job"]
        assert captured["coordinator"] is coordinator
        assert captured["handlers"] is None
        assert coordinator.runtime_capability_snapshot is not None
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


def test_vnext_server_composes_all_admitted_raw_backends_without_legacy(
    monkeypatch,
) -> None:
    import winpilot.server as server
    from winpilot.visual_runtime.atomic_click_controller import (
        AtomicDesktopController,
    )

    captured, calls = _install_server_run_capture(monkeypatch, server)
    raw_devices = []
    coordinator, _factory_calls = _install_live_coordinator_factory(
        monkeypatch,
        raw_devices=raw_devices,
    )
    monkeypatch.setattr(
        server,
        "default_handlers",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit vNext composition must not construct legacy handlers"
        ),
    )

    execution = server._start_arp_execution(
        _vnext_task_stage_graph(),
        {},
        runtime_version="vnext",
    )
    try:
        assert calls == ["prepare", "job"]
        assert captured["coordinator"] is coordinator
        assert captured["handlers"] is None
        assert len(raw_devices) == 1
        assert raw_devices[0].admitted_action_names == tuple(sorted(
            AtomicDesktopController.required_raw_action_names
        ))
        assert raw_devices[0].admitted_action_names == (
            "activate_target",
            "click",
            "hotkey",
            "launch_target",
            "scroll",
            "type_text",
        )
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


def test_vnext_missing_launch_backend_fails_before_prepare_without_fallback(
    monkeypatch,
) -> None:
    import winpilot.action.windows_target_launch as launch_module
    import winpilot.server as server

    calls: list[str] = []
    _captured, calls = _install_server_run_capture(
        monkeypatch,
        server,
        calls=calls,
    )
    monkeypatch.setattr(
        server,
        "default_handlers",
        lambda *_args, **_kwargs: calls.append("legacy_fallback"),
    )

    def unavailable_launch_backend(*_args, **_kwargs):
        raise RuntimeError("launch backend unavailable")

    monkeypatch.setattr(
        launch_module,
        "WindowsCreateProcessWBackend",
        unavailable_launch_backend,
    )
    _install_live_coordinator_factory(monkeypatch)

    with pytest.raises(
        RuntimeError,
        match=r"vNext desktop runtime coordinator construction failed \(RuntimeError\)",
    ):
        server._start_arp_execution(
            _vnext_task_stage_graph(),
            {},
            runtime_version="vnext",
        )

    assert calls == []


def test_vnext_admission_failure_precedes_prepare_model_controller_job_and_fallback(
    monkeypatch,
) -> None:
    import winpilot.action.executor as executor_module
    import winpilot.server as server
    from winpilot.action.raw_device import AdmittedPhysicalClickBackend

    calls: list[str] = []
    _captured, calls = _install_server_run_capture(
        monkeypatch,
        server,
        calls=calls,
    )

    class Coordinator:
        runtime_capability_snapshot = build_live_runtime_snapshot()

        def run(self, _context):
            calls.append("model_or_controller")
            pytest.fail("an unadmitted run must never enter the live coordinator")

    monkeypatch.setattr(
        server,
        "default_handlers",
        lambda *_args, **_kwargs: calls.append("legacy_fallback"),
    )
    monkeypatch.setattr(
        executor_module,
        "ADMITTED_PHYSICAL_CLICK_BACKEND",
        AdmittedPhysicalClickBackend(
            lambda *_args, **_kwargs: calls.append("controller_mutation"),
        ),
    )
    _install_live_coordinator_factory(monkeypatch, coordinator=Coordinator())

    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="requires task-contract/1",
    ):
        server._start_arp_execution(
            _task_stage_graph(),
            {},
            runtime_version="vnext",
        )

    assert calls == []


def test_legacy_server_without_contract_skips_capability_preflight(
    monkeypatch,
) -> None:
    import winpilot.server as server

    captured, calls = _install_server_run_capture(monkeypatch, server)
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_satisfiable",
        staticmethod(lambda _contract: pytest.fail(
            "legacy graphs without a contract remain admissible"
        )),
    )
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_runtime_satisfiable",
        staticmethod(lambda _contract: pytest.fail(
            "legacy graphs without a contract do not probe providers"
        )),
    )

    execution = server._start_arp_execution(
        _task_stage_graph(),
        {},
        runtime_version="legacy",
    )
    try:
        assert captured["handlers"] is not None
        assert calls == ["prepare", "job"]
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


def test_legacy_server_preserves_static_then_runtime_preflight_order(
    monkeypatch,
) -> None:
    import winpilot.server as server

    graph = _vnext_task_stage_graph()
    calls: list[str] = []
    _captured, calls = _install_server_run_capture(
        monkeypatch,
        server,
        calls=calls,
    )
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_satisfiable",
        staticmethod(lambda _contract: calls.append("static")),
    )
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_runtime_satisfiable",
        staticmethod(lambda _contract: calls.append("runtime")),
    )

    execution = server._start_arp_execution(
        graph,
        {},
        runtime_version="legacy",
    )
    try:
        assert calls == ["static", "runtime", "prepare", "job"]
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


@pytest.mark.parametrize(
    ("factory_value", "factory_failure", "error_kind"),
    (
        (None, None, "TypeError"),
        (_DEFAULT_COORDINATOR, RuntimeError("secret provider detail"), "RuntimeError"),
    ),
)
def test_vnext_coordinator_construction_failure_is_fail_closed_before_prepare(
    monkeypatch,
    factory_value,
    factory_failure,
    error_kind,
) -> None:
    import winpilot.server as server

    class Config:
        @staticmethod
        def get(*path: str, default=None):
            if path in {
                ("vnext", "profiler_shadow"),
                ("vnext", "broker_shadow"),
            }:
                raise AssertionError("vNext must not read legacy shadow config")
            return default

    monkeypatch.setattr(server, "CONFIG", Config())
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)
    monkeypatch.setattr(
        server, "default_handlers",
        lambda *_args, **_kwargs: pytest.fail("legacy handlers must not be built"),
    )
    monkeypatch.setattr(
        server.ARP_RUNS, "prepare",
        lambda *_args, **_kwargs: pytest.fail("run must not be prepared"),
    )
    monkeypatch.setattr(
        server.RUNTIME_JOBS, "submit",
        lambda *_args, **_kwargs: pytest.fail("job must not be submitted"),
    )
    _resolved, factory_calls = _install_live_coordinator_factory(
        monkeypatch,
        coordinator=factory_value,
        failure=factory_failure,
    )

    with pytest.raises(
        RuntimeError,
        match=rf"coordinator construction failed \({error_kind}\)",
    ) as raised:
        server._start_arp_execution(
            _task_stage_graph(), {}, runtime_version="vnext",
        )

    assert "secret provider detail" not in str(raised.value)
    assert factory_calls == [server.trace_mod.RECORDER]


def test_unknown_product_runtime_config_fails_before_prepare_or_job_submit(
    monkeypatch,
) -> None:
    import winpilot.server as server

    class InvalidConfig:
        @staticmethod
        def get(*path: str, default=None):
            if path == ("vnext", "runtime_version"):
                return "automatic"
            return default

    monkeypatch.setattr(server, "CONFIG", InvalidConfig())
    monkeypatch.setattr(
        server.ARP_RUNS,
        "prepare",
        lambda *_args, **_kwargs: pytest.fail("prepare must not run"),
    )
    monkeypatch.setattr(
        server.RUNTIME_JOBS,
        "submit",
        lambda *_args, **_kwargs: pytest.fail("job must not be submitted"),
    )

    with pytest.raises(InvalidDesktopRuntimeSelection, match="automatic"):
        server._start_arp_execution(_task_stage_graph(), {})


def test_runtime_selection_has_one_product_owner_and_no_semantic_selector() -> None:
    package_root = Path(__file__).parents[1] / "winpilot"
    calls: list[tuple[Path, ast.Call, ast.AST | None]] = []
    admission_calls: list[tuple[Path, ast.AST | None]] = []
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "admit"
                and isinstance(func.value, ast.Name)
                and func.value.id == "DesktopRuntimeAdmission"
            ):
                admission_owner: ast.AST | None = node
                while admission_owner is not None and not isinstance(
                    admission_owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    admission_owner = parents.get(admission_owner)
                admission_calls.append((path, admission_owner))
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "from_config"
                and isinstance(func.value, ast.Name)
                and func.value.id == "DesktopRuntimeSelection"
            ):
                continue
            owner: ast.AST | None = node
            while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                owner = parents.get(owner)
            calls.append((path, node, owner))

    assert len(calls) == 1
    path, call, owner = calls[0]
    assert path.name == "server.py"
    assert isinstance(owner, ast.FunctionDef)
    assert owner.name == "_start_arp_execution"
    assert len(call.args) == 1
    assert ast.unparse(call.args[0]) == "selected_runtime_version"
    owner_source = ast.unparse(owner)
    assert "CONFIG.get('vnext', 'runtime_version', default=None)" in owner_source
    assert "runtime_version is None" in owner_source
    assert len(admission_calls) == 1
    admission_path, admission_owner = admission_calls[0]
    assert admission_path.name == "server.py"
    assert isinstance(admission_owner, ast.FunctionDef)
    assert admission_owner.name == "_start_arp_execution"
    assert "CapabilityPlanner.assert_" not in owner_source
