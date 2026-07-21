from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import threading

import pytest

from winpilot.orchestrator.contracts import (
    ArpEdge,
    ArpGraph,
    ArpNode,
    ArpNodeKind,
    ArpPolicy,
    ArpRunStatus,
)
from winpilot.orchestrator.runtime import ArpExecution, NodeResult
from winpilot.orchestrator.runtime_selection import (
    DesktopRuntimeSelection,
    DesktopRuntimeUnavailable,
    InvalidDesktopRuntimeSelection,
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


def test_default_legacy_selection_preserves_existing_handler_execution() -> None:
    calls: list[str] = []

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


def test_server_reads_runtime_config_once_and_freezes_selection(monkeypatch) -> None:
    import winpilot.server as server

    runtime_reads: list[tuple[str, ...]] = []

    class FlippingConfig:
        def get(self, *path: str, default=None):
            if path == ("vnext", "runtime_version"):
                runtime_reads.append(path)
                return "legacy" if len(runtime_reads) == 1 else "vnext"
            if path in {
                ("vnext", "profiler_shadow"),
                ("vnext", "broker_shadow"),
            }:
                return {"enabled": False}
            return default

    captured: dict[str, object] = {}

    class Runs:
        @staticmethod
        def prepare(graph, handlers, *, inputs, desktop_runtime_selection):
            captured["selection"] = desktop_runtime_selection
            return ArpExecution(
                graph,
                handlers,
                inputs=inputs,
                desktop_runtime_selection=desktop_runtime_selection,
            )

    class Jobs:
        @staticmethod
        def submit(*_args, **_kwargs):
            captured["submitted"] = True

    monkeypatch.setattr(server, "CONFIG", FlippingConfig())
    monkeypatch.setattr(server, "ARP_RUNS", Runs())
    monkeypatch.setattr(server, "RUNTIME_JOBS", Jobs())
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)

    graph = _task_stage_graph(metadata={
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
        assert selection.runtime_version == "legacy"
        assert execution.desktop_runtime_selection is selection
        assert runtime_reads == [("vnext", "runtime_version")]
        # The fake config would now return vNext, but the existing run cannot switch.
        assert server.CONFIG.get("vnext", "runtime_version") == "vnext"
        assert execution.desktop_runtime_selection.runtime_version == "legacy"
        assert captured["submitted"] is True
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)


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
    assert ast.unparse(call.args[0]) == (
        "CONFIG.get('vnext', 'runtime_version', default=None)"
    )
