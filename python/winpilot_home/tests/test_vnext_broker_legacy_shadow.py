"""P3b same-entry legacy Broker shadow tests."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import threading

from winpilot.visual_runtime.windows_desktop_facts import (
    WindowsDesktopFactsError,
    WindowsDesktopFactsReader,
)
from winpilot.vnext.capabilities.contracts import MutationKind
from winpilot.vnext.capabilities.legacy_shadow import (
    BROKER_LEGACY_SHADOW_FORMAT,
    LegacyBrokerShadowObserver,
    attach_legacy_broker_shadow,
    build_legacy_broker_shadow_observer,
    detach_legacy_broker_shadow,
)
from winpilot.vnext.capabilities.names import CANONICAL_CAPABILITIES
from winpilot.vnext.capabilities.registry import legacy_local_capability_registry
from winpilot.vnext.mission import MissionDefinition, MissionReducer, ObligationSpec
from winpilot.vnext.profiling import ExecutionPolicy


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"existing schema for {name}",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
            },
        },
    }


BASE_TOOLS = [_tool(name) for name in (
    "open_app", "launch", "list_windows", "focus_window", "observe",
    "click", "double_click", "type_text", "hotkey", "scroll", "drag",
    "draw_strokes", "mouse_down", "move_mouse", "mouse_up", "wait",
    "capture_artifact", "send_artifact", "discover_mcp_tools",
    "call_mcp_tool", "done", "blocked", "fail",
)]


def _contract() -> dict:
    return {
        "format": "task-contract/1",
        "task_id": "task-1",
        "task": "Complete the current unknown visual task",
        "obligations": [{
            "id": "o1",
            "description": "the requested state exists",
            "acceptance_criteria": ["the requested state is confirmed"],
            "required_capabilities": ["visual.interact"],
        }],
    }


def _profile(run_id: str = "r1"):
    return ExecutionPolicy().safe_default(
        f"mission-{run_id}",
        0,
        available_capabilities=CANONICAL_CAPABILITIES,
    )


class _Trace:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[tuple[str, dict]] = []
        self.fail = fail

    def record_sidecar(self, event: dict, *, run_id: str) -> bool:
        if self.fail:
            raise RuntimeError("trace unavailable")
        self.records.append((run_id, event))
        return True


class _DesktopFactsBackend:
    def __init__(self) -> None:
        self.creation_time_ns = 1_000_000_000
        self.executable_path = r"C:\Program Files\Test Host\host.exe"
        self.process_facts_available = True

    def is_window(self, hwnd):
        return hwnd == 101

    def root_hwnd(self, hwnd):
        return 101 if hwnd == 101 else 0

    def process_id(self, hwnd):
        return 77 if hwnd == 101 else 0

    def process_creation_time_ns(self, process_id):
        assert process_id == 77
        if not self.process_facts_available:
            raise WindowsDesktopFactsError("process facts unavailable")
        return self.creation_time_ns

    def process_executable_path(self, process_id):
        assert process_id == 77
        return self.executable_path

    def window_region(self, hwnd):
        assert hwnd == 101
        return (10, 20, 800, 600)

    def foreground_hwnd(self):
        return 101

    def window_from_point(self, _point):
        return 101


def _facts_readers(backend=None):
    return WindowsDesktopFactsReader(backend or _DesktopFactsBackend())


def _runner(*, frame_id=4, epoch=2, hwnd=101, verified=True, executable="host.exe"):
    frame = SimpleNamespace(
        frame_id=frame_id,
        epoch=epoch,
        hwnd=hwnd,
        image_sha256="a" * 64,
        captured_at=10.5,
    )
    session = SimpleNamespace(
        hwnd=hwnd,
        _target_binding_verified=verified,
        _target_process_id=77,
        _target_process_name=executable,
        _screen_epoch=epoch,
    )
    return SimpleNamespace(
        _frame_authority=SimpleNamespace(current=frame),
        session=session,
        stop_event=threading.Event(),
        run_id="child-r1",
    )


def _observer(trace=None, *, run_id="r1"):
    trace = trace or _Trace()
    desktop_facts = _facts_readers()
    return build_legacy_broker_shadow_observer(
        run_id=run_id,
        contract=_contract(),
        profile=_profile(run_id),
        trace=trace,
        available_capabilities=legacy_local_capability_registry().snapshot(),
        desktop_facts_reader=desktop_facts,
    ), trace


def _observe(observer, runner=None, pointer=None, selected=None, step=1):
    base = list(BASE_TOOLS)
    selected = list(base if selected is None else selected)
    observer.observe_turn(
        runner=runner or _runner(),
        base_tools=base,
        legacy_selected_tools=selected,
        step=step,
        pointer_status=pointer or {"active": False, "button": ""},
    )
    return base, selected


def test_bound_turn_projects_from_existing_schemas_without_changing_legacy_tools() -> None:
    observer, trace = _observer()
    base, selected = _observe(observer)
    record = trace.records[0][1]
    assert record["format"] == BROKER_LEGACY_SHADOW_FORMAT
    assert record["status"] == "projected"
    assert record["child_run_id"] == "child-r1"
    assert record["legacy_base_tool_names"] == [item["function"]["name"] for item in base]
    assert record["legacy_selected_tool_names"] == [item["function"]["name"] for item in selected]
    names = record["broker_projection"]["tool_names"]
    assert "observe" in names and "click" in names and "blocked" in names
    assert "done" not in names and "fail" not in names
    assert all(item["function"]["parameters"]["properties"]["value"]["type"] == "string" for item in base)
    assert record["replay_delta"]["mission_definition"] == observer.definition.to_dict()
    assert len(record["replay_delta"]["accepted_events"]) == 1
    assert len(record["replay_delta"]["evidence_refs"]) == 1


def test_target_truth_is_accepted_as_run_scoped_evidence_and_fully_replayable() -> None:
    observer, _ = _observer()
    _observe(observer)
    assert [item.kind for item in observer.accepted_events] == ["target_bound"]
    evidence = observer.evidence_refs
    assert len(evidence) == 1
    assert evidence[0].mission_id == observer.definition.mission_id
    assert evidence[0].run_id == observer.definition.run_id
    assert evidence[0].trust == "deterministic"
    replayed = MissionReducer.replay(
        observer.definition,
        observer.accepted_events,
        evidence=evidence,
    ).projection
    assert replayed.to_dict() == observer.projection.to_dict()


def test_epoch_change_invalidates_then_rebinds_instead_of_replacing_projection() -> None:
    observer, trace = _observer()
    _observe(observer, runner=_runner(epoch=2), step=1)
    _observe(observer, runner=_runner(frame_id=5, epoch=3), step=2)
    assert [item.kind for item in observer.accepted_events] == [
        "target_bound", "target_invalidated",
    ]
    assert observer.projection.target_binding is None
    _observe(observer, runner=_runner(frame_id=6, epoch=3), step=3)
    assert [item.kind for item in observer.accepted_events] == [
        "target_bound", "target_invalidated", "target_bound",
    ]
    assert observer.projection.target_binding.epoch == 3
    assert MissionReducer.replay(
        observer.definition,
        observer.accepted_events,
        evidence=observer.evidence_refs,
    ).projection.to_dict() == observer.projection.to_dict()
    assert trace.records[1][1]["replay_delta"]["mission_definition"] is None
    assert [item["kind"] for item in trace.records[1][1]["replay_delta"]["accepted_events"]] == [
        "target_invalidated",
    ]
    assert len(trace.records[1][1]["replay_delta"]["evidence_refs"]) == 0
    assert [item["kind"] for item in trace.records[2][1]["replay_delta"]["accepted_events"]] == [
        "target_bound",
    ]
    assert len(trace.records[2][1]["replay_delta"]["evidence_refs"]) == 1


def test_unverified_target_never_exposes_gui_mutation() -> None:
    observer, trace = _observer()
    _observe(observer, runner=_runner(verified=False))
    projection = trace.records[0][1]["broker_projection"]
    assert projection["target_binding_id"] is None
    assert projection["tool_names"] == [
        "observe", "open_app", "launch", "list_windows", "focus_window", "wait", "blocked",
    ]


def test_application_resolution_capability_does_not_unlock_generic_gui_mutation() -> None:
    definition = MissionDefinition(
        mission_id="mission-r1", run_id="r1", original_goal="resolve a target",
        obligations=(ObligationSpec(
            "o1", "a target application is resolved",
            required_capabilities=("application.resolve",),
        ),),
    )
    profile = replace(
        _profile(), accepted_capabilities=("application.resolve",),
    )
    trace = _Trace()
    desktop_facts = _facts_readers()
    observer = LegacyBrokerShadowObserver(
        definition=definition,
        profile=profile,
        trace=trace,
        available_capabilities=legacy_local_capability_registry().snapshot(),
        desktop_facts_reader=desktop_facts,
    )
    _observe(observer, runner=_runner(verified=False))
    names = trace.records[0][1]["broker_projection"]["tool_names"]
    assert names == [
        "observe", "open_app", "launch", "list_windows", "focus_window", "wait", "blocked",
    ]
    assert not {"click", "type_text", "hotkey", "scroll", "drag"}.intersection(names)


def test_held_pointer_uses_real_pointer_status_and_exact_continuation_surface() -> None:
    observer, trace = _observer()
    _observe(observer, pointer={"active": True, "button": "left"})
    record = trace.records[0][1]
    assert record["runtime_facts"]["pointer_disposition"] == "held"
    assert record["broker_projection"]["tool_names"] == [
        "move_mouse", "mouse_up", "wait", "blocked",
    ]


def test_artifact_provider_mcp_and_authorization_are_never_inferred() -> None:
    observer, trace = _observer()
    _observe(observer)
    record = trace.records[0][1]
    assert record["runtime_facts"] == {
        "target_bound": True,
        "authoritative_frame": True,
        "pointer_disposition": "released",
        "artifact_count": 0,
        "executor_offer_count": 0,
        "authorization_scope_count": 0,
    }
    assert not {"capture_artifact", "send_artifact", "discover_mcp_tools", "call_mcp_tool"}.intersection(
        record["broker_projection"]["tool_names"]
    )


def test_local_artifact_executor_requires_real_owner_and_projects_receipted_route() -> None:
    definition = MissionDefinition(
        mission_id="mission-r1", run_id="r1", original_goal="capture an artifact",
        obligations=(ObligationSpec(
            "o1", "the current artifact is captured",
            required_capabilities=("artifact.capture",),
        ),),
    )
    profile = replace(_profile(), accepted_capabilities=("artifact.capture",))
    trace = _Trace()
    desktop_facts = _facts_readers()
    observer = LegacyBrokerShadowObserver(
        definition=definition,
        profile=profile,
        trace=trace,
        available_capabilities=legacy_local_capability_registry().snapshot(),
        desktop_facts_reader=desktop_facts,
    )
    runner = _runner()
    runner.session._managed_artifacts = lambda: (object(), object())
    _observe(observer, runner=runner)
    record = trace.records[0][1]
    assert record["runtime_facts"]["executor_offer_count"] == 1
    assert record["runtime_facts"]["authorization_scope_count"] == 1
    assert record["broker_projection"]["execution_route"] == "deterministic"
    assert record["broker_projection"]["tool_names"] == ["capture_artifact"]


def test_multi_obligation_projection_is_explicitly_unavailable() -> None:
    definition = MissionDefinition(
        mission_id="mission-r1",
        run_id="r1",
        original_goal="complete both result states",
        obligations=(
            ObligationSpec("o1", "first state"),
            ObligationSpec("o2", "second state", depends_on=("o1",)),
        ),
    )
    trace = _Trace()
    desktop_facts = _facts_readers()
    observer = LegacyBrokerShadowObserver(
        definition=definition,
        profile=_profile(),
        trace=trace,
        available_capabilities=CANONICAL_CAPABILITIES,
        desktop_facts_reader=desktop_facts,
    )
    _observe(observer)
    assert trace.records[0][1]["status"] == "unavailable"
    assert trace.records[0][1]["reason"] == "multi_obligation_mission"
    assert observer.accepted_events == ()


def test_profile_identity_mismatch_fails_closed_without_reading_runtime() -> None:
    observer, trace = _observer()
    observer.profile = replace(observer.profile, mission_id="different-mission")
    _observe(observer, runner=SimpleNamespace())
    assert trace.records[0][1]["reason"] == "execution_profile_identity_mismatch"
    assert observer.accepted_events == ()


def test_projection_and_sidecar_are_deterministic_for_identical_inputs() -> None:
    first, first_trace = _observer()
    second, second_trace = _observer()
    _observe(first)
    _observe(second)
    assert first_trace.records == second_trace.records


def test_stale_authoritative_frame_is_not_promoted_to_fresh() -> None:
    observer, trace = _observer()
    runner = _runner(epoch=2)
    runner.session._screen_epoch = 3
    _observe(observer, runner=runner)
    record = trace.records[0][1]
    assert record["runtime_facts"]["authoritative_frame"] is False
    projected = record["broker_projection"]["tool_names"]
    assert not {"click", "type_text", "hotkey", "scroll", "drag"}.intersection(projected)


def test_relevant_schema_change_after_first_turn_fails_closed() -> None:
    observer, trace = _observer()
    _observe(observer, step=1)
    changed = list(BASE_TOOLS)
    changed_click = _tool("click")
    changed_click["function"]["parameters"]["properties"]["x"] = {"type": "integer"}
    changed = [changed_click if item["function"]["name"] == "click" else item for item in changed]
    observer.observe_turn(
        runner=_runner(), base_tools=changed, legacy_selected_tools=changed,
        step=2, pointer_status={"active": False, "button": ""},
    )
    second = trace.records[1][1]
    assert second["status"] == "unavailable"
    assert second["reason"] == "legacy_tool_catalog_changed"
    assert second["broker_projection"] is None


def test_unchanged_target_emits_no_duplicate_replay_facts() -> None:
    observer, trace = _observer()
    _observe(observer, step=1)
    _observe(observer, step=2)
    delta = trace.records[1][1]["replay_delta"]
    assert delta == {
        "mission_definition": None,
        "accepted_events": [],
        "evidence_refs": [],
    }
    assert trace.records[0][1]["outer_turn_index"] == 1
    assert trace.records[1][1]["outer_turn_index"] == 2


def test_same_hwnd_and_pid_with_reused_process_never_uses_cached_binding() -> None:
    backend = _DesktopFactsBackend()
    trace = _Trace()
    observer = build_legacy_broker_shadow_observer(
        run_id="r1",
        contract=_contract(),
        profile=_profile(),
        trace=trace,
        available_capabilities=legacy_local_capability_registry().snapshot(),
        desktop_facts_reader=_facts_readers(backend),
    )
    _observe(observer, step=1)
    first_binding = observer.projection.target_binding
    assert first_binding is not None

    backend.creation_time_ns += 1
    _observe(observer, step=2)

    assert observer.projection.target_binding is None
    assert observer.accepted_events[-1].kind == "target_invalidated"
    assert trace.records[1][1]["broker_projection"]["target_binding_id"] is None

    _observe(observer, runner=_runner(frame_id=4), step=3)
    assert observer.projection.target_binding is None
    assert observer.accepted_events[-1].kind == "target_invalidated"

    _observe(observer, runner=_runner(frame_id=5), step=4)
    second_binding = observer.projection.target_binding
    assert second_binding is not None
    assert second_binding.binding_id != first_binding.binding_id
    assert observer.accepted_events[-1].kind == "target_bound"


def test_target_fact_failure_invalidates_replay_truth_and_projects_unbound() -> None:
    backend = _DesktopFactsBackend()
    trace = _Trace()
    observer = build_legacy_broker_shadow_observer(
        run_id="r1",
        contract=_contract(),
        profile=_profile(),
        trace=trace,
        available_capabilities=legacy_local_capability_registry().snapshot(),
        desktop_facts_reader=_facts_readers(backend),
    )
    _observe(observer, step=1)
    backend.process_facts_available = False

    _observe(observer, runner=_runner(frame_id=5), step=2)

    assert observer.projection.target_binding is None
    assert observer.accepted_events[-1].kind == "target_invalidated"
    assert trace.records[1][1]["status"] == "projected"
    assert trace.records[1][1]["broker_projection"]["target_binding_id"] is None
    replayed = MissionReducer.replay(
        observer.definition,
        observer.accepted_events,
        evidence=observer.evidence_refs,
    ).projection
    assert replayed.to_dict() == observer.projection.to_dict()

    backend.process_facts_available = True
    _observe(observer, runner=_runner(frame_id=5), step=3)
    assert observer.projection.target_binding is None
    _observe(observer, runner=_runner(frame_id=6), step=4)
    assert observer.projection.target_binding is not None


def test_run_scoped_observer_attach_and_cleanup_are_identity_safe() -> None:
    observer, _ = _observer()
    other, _ = _observer(run_id="r2")
    runner = _runner()
    attach_legacy_broker_shadow(runner, observer)
    assert runner._vnext_broker_shadow_observer is observer
    assert detach_legacy_broker_shadow(runner, other) is False
    assert runner._vnext_broker_shadow_observer is observer
    assert detach_legacy_broker_shadow(runner, observer) is True
    assert not hasattr(runner, "_vnext_broker_shadow_observer")


def test_trace_failure_never_escapes_or_changes_legacy_tool_lists() -> None:
    observer, _ = _observer(_Trace(fail=True))
    base = list(BASE_TOOLS)
    selected = base[:5]
    before_base = list(base)
    before_selected = list(selected)
    observer.observe_turn(
        runner=_runner(), base_tools=base, legacy_selected_tools=selected,
        step=1, pointer_status={"active": False, "button": ""},
    )
    assert base == before_base
    assert selected == before_selected


def test_cataloged_projected_gui_tools_keep_single_visual_actor_route() -> None:
    observer, trace = _observer()
    _observe(observer)
    projected = trace.records[0][1]["broker_projection"]["tool_names"]
    # P3b does not connect a provider/deterministic executor offer, so every
    # projected desktop mutation remains a Visual Actor proposal.
    assert trace.records[0][1]["broker_projection"]["execution_route"] == "visual_actor"
    assert any(name in projected for name in ("click", "type_text", "drag"))
    assert MutationKind.GUI.value == "gui"
