from __future__ import annotations

import base64
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import winpilot.vnext.bridge.coordinator as coordinator_module

from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.orchestrator.desktop_runtime import (
    DesktopRuntimeCoordinatorContext,
    DesktopRuntimeCoordinatorStatus,
)
from winpilot.tasking.compiler import TaskContractCompiler
from winpilot.utils.image_capture import VisualCaptureScope
from winpilot.utils.screenshot import Region
from winpilot.visual_runtime.atomic_click_controller import (
    AtomicDesktopController,
)
from winpilot.visual_runtime.frame_observer import FrameObserver
from winpilot.visual_runtime.windows_desktop_facts import WindowsDesktopFactsReader
from winpilot.vnext.verification import VerifierAssessment
from winpilot.vnext.actor.model import OpenAIVisualActor
from winpilot.vnext.verification.visual_model import ReadOnlyVisualVerifierAdapter
from winpilot.vnext.bridge.coordinator import (
    LiveDesktopCoordinatorError,
    LiveDesktopRuntimeCoordinator,
    build_live_desktop_runtime_coordinator,
)
from winpilot.vnext.capabilities.live_catalog import (
    build_live_runtime_snapshot,
)


class Backend:
    def __init__(self) -> None:
        self.live = {100, 101, 111}
        self.roots = {100: 100, 101: 100, 111: 100}
        self.pids = {100: 7, 101: 7, 111: 7}
        self.region = (10, 20, 800, 600)
        self.foreground = 100
        self.hit = 111

    def is_window(self, hwnd):
        return hwnd in self.live

    def root_hwnd(self, hwnd):
        return self.roots[hwnd]

    def process_id(self, hwnd):
        return self.pids[hwnd]

    def window_region(self, hwnd):
        assert hwnd == 101
        return self.region

    def foreground_hwnd(self):
        return self.foreground

    def window_from_point(self, _point):
        return self.hit


class ScriptedActor:
    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.calls = []

    def propose(self, turn, *, image_b64, submitted_size, stop_event=None):
        self.calls.append((turn, image_b64, submitted_size, stop_event))
        return self.proposals.pop(0)


class PositiveVisualModel:
    def __init__(self) -> None:
        self.calls = []

    def assess(self, request, *, stop_event=None):
        self.calls.append((request, stop_event))
        return VerifierAssessment(
            verified=True,
            satisfied_conditions=request.acceptance_criteria,
            missing_conditions=(),
            contradictions=(),
            confidence=1.0,
            concise_reason="visible result satisfies every requested condition",
        )


class Trace:
    def __init__(self) -> None:
        self.records = []

    def record_sidecar(self, event, *, run_id):
        self.records.append((run_id, dict(event)))
        return True


def _task_contract(*, max_steps=18) -> dict:
    goal = "Complete the currently visible unknown interface task"
    return TaskContractCompiler().compile(
        goal,
        max_steps=max_steps,
        cold_start=True,
        require_gui=True,
    ).model_dump(mode="json")


def _context(*, contract=None, stop_event=None):
    stopped = stop_event or threading.Event()
    return DesktopRuntimeCoordinatorContext(
        run_id="arp-run-1",
        graph={
            "format": "arp-graph/1",
            "metadata": {"task_contract": contract or _task_contract()},
        },
        inputs={
            "execution_context": {
                "format": "desktop-execution-context/1",
                "entrypoint": "native_agent_cockpit",
                "desktop_handoff": {
                    "required": True,
                    "strategy": "yield_before_capture",
                    "yielded": True,
                    "control_surface_pid": 999,
                    "prepared_at": int(time.time() * 1000),
                    "reason": "",
                },
                "target_window": {
                    "format": "desktop-target/1",
                    "hwnd": 101,
                    "title": "Unknown visual surface",
                    "process_id": 7,
                    "class_name": "Surface",
                    "rect": [10, 20, 810, 620],
                    "captured_at": int(time.time() * 1000),
                    "foreground_verified": True,
                    "usable": True,
                    "failure_type": "",
                    "reason": "",
                },
            },
        },
        stop_event=stopped,
        wait_if_paused=lambda: not stopped.is_set(),
        is_paused=lambda: False,
    )


def _observer(authority):
    pixels = np.zeros((600, 800, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", pixels)
    assert ok
    image_b64 = base64.b64encode(encoded.tobytes()).decode("ascii")

    def select_scope(hwnd, **_kwargs):
        return VisualCaptureScope(
            Region(10, 20, 800, 600),
            "target_window",
            "test_exact_target",
            target_hwnd=hwnd,
            target_verified=True,
        )

    return FrameObserver(
        authority,
        capture=lambda _hwnd, *, region: image_b64,
        select_scope=select_scope,
        captured_at=lambda: 10.0,
    )


def _coordinator(
    actor,
    verifier_model,
    *,
    runtime_snapshot=None,
    trace=None,
    raw_calls=None,
    raw_scroll_calls=None,
    raw_scroll_completed=True,
    raw_type_calls=None,
    raw_hotkey_calls=None,
    omit_admitted_backend=None,
    max_turns=6,
    frame_max_age_s=30.0,
    clock=lambda: 10.0,
):
    calls = raw_calls if raw_calls is not None else []
    snapshot = (
        build_live_runtime_snapshot()
        if runtime_snapshot is None
        else runtime_snapshot
    )

    def raw_click(x, y, button, count, mechanism, hwnd=None, *, final_guard):
        decision = final_guard(x, y)
        calls.append((x, y, button, count, mechanism, hwnd, decision))
        return RawBackendResult(
            decision is FinalPointerGuardDecision.ADMITTED,
            {"final_guard": decision.value},
        )

    scroll_calls = raw_scroll_calls if raw_scroll_calls is not None else []

    def raw_scroll(x, y, amount, hwnd=None, *, final_guard):
        decision = final_guard(x, y)
        scroll_calls.append((x, y, amount, hwnd, decision))
        return RawBackendResult(
            (
                raw_scroll_completed
                if decision is FinalPointerGuardDecision.ADMITTED
                else False
            ),
            {"final_guard": decision.value},
        )

    type_calls = raw_type_calls if raw_type_calls is not None else []

    def raw_type_text(text, hwnd=None, *, final_guard):
        decision = final_guard()
        type_calls.append((text, hwnd, decision))
        completed = decision is FinalKeyboardGuardDecision.ADMITTED
        return RawBackendResult(completed, {
            "guard_decision": decision.value,
            "requested_codepoints": len(text),
            "completed_codepoints": len(text) if completed else 0,
        })

    hotkey_calls = raw_hotkey_calls if raw_hotkey_calls is not None else []

    def raw_hotkey(chord, hwnd=None, *, final_guard):
        decision = final_guard()
        hotkey_calls.append((chord, hwnd, decision))
        return RawBackendResult(
            decision is FinalKeyboardGuardDecision.ADMITTED,
            {"guard_decision": decision.value, "key_count": len(chord.keys)},
        )

    backends = {
        "click": AdmittedPhysicalClickBackend(raw_click),
        "scroll": AdmittedPhysicalScrollBackend(raw_scroll),
        "type_text": AdmittedPhysicalTypeTextBackend(raw_type_text),
        "hotkey": AdmittedPhysicalHotkeyBackend(raw_hotkey),
    }
    if omit_admitted_backend is not None:
        backends[omit_admitted_backend] = None

    return LiveDesktopRuntimeCoordinator(
        api_config={"unused": True},
        runtime_snapshot=snapshot,
        trace_sidecar=trace,
        action_queue=DesktopActionQueue(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=backends["click"],
            admitted_physical_scroll=backends["scroll"],
            admitted_physical_type_text=backends["type_text"],
            admitted_physical_hotkey=backends["hotkey"],
        )),
        facts_reader=WindowsDesktopFactsReader(Backend()),
        observer_factory=_observer,
        actor_factory=lambda _api: actor,
        verifier_factory=lambda _api: ReadOnlyVisualVerifierAdapter(
            verifier_model
        ),
        window_exists_resolver=lambda hwnd: hwnd == 101,
        max_turns=max_turns,
        frame_max_age_s=frame_max_age_s,
        clock=clock,
    )


def _click(
    *,
    image_x: int = 100,
    image_y: int = 100,
    local_intent: str = "select the visible target on the current frame",
):
    return {
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "action": "click",
        "args": {"image_x": image_x, "image_y": image_y},
        "local_intent": local_intent,
    }


def _claim():
    return {
        "format": "actor-proposal/0",
        "kind": "claim_obligation_done",
        "concise_claim": "the current visual task shows its completed state",
    }


def _blocked():
    return {
        "format": "actor-proposal/0",
        "kind": "blocked",
        "blocker_kind": "visual_uncertainty",
        "observed_facts": ["the current pixels do not identify one safe action"],
    }


def _scroll(*, direction="down", notches=4):
    return {
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "action": "scroll",
        "args": {
            "image_x": 100,
            "image_y": 100,
            "direction": direction,
            "notches": notches,
        },
        "local_intent": "move the visible content in the intended direction",
    }


def _type_text(text):
    return {
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "action": "type_text",
        "args": {"text": text},
        "local_intent": "enter the exact requested content in the focused field",
    }


def _hotkey(*, modifiers, key):
    return {
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "action": "hotkey",
        "args": {"modifiers": list(modifiers), "key": key},
        "local_intent": "apply the selected keyboard chord to the focused field",
    }


def _invalid_click():
    proposal = _click()
    proposal["args"] = {"image_x": "not-a-coordinate", "image_y": 100}
    return proposal


def test_live_coordinator_uses_one_actor_controller_and_independent_verifier():
    actor = ScriptedActor([_click(), _claim()])
    verifier = PositiveVisualModel()
    raw_calls = []
    trace = Trace()

    result = _coordinator(
        actor, verifier, trace=trace, raw_calls=raw_calls,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    output = result.output_copy()
    assert output["runtime_version"] == "vnext"
    assert output["mission_status"] == "verified"
    assert output["turn_count"] == 2
    assert output["tool_names"] == [
        tool.name for tool in build_live_runtime_snapshot().catalog.tools
    ]
    assert len(actor.calls) == 2
    assert len(verifier.calls) == 1
    assert len(raw_calls) == 1
    second_turn = actor.calls[1][0]
    assert second_turn.previous_action is not None
    assert second_turn.previous_action.action == "click"
    assert dict(second_turn.previous_action.arguments) == {
        "image_x": 100,
        "image_y": 100,
    }
    assert second_turn.previous_action.local_intent == (
        "select the visible target on the current frame"
    )
    assert second_turn.previous_action.input_completed is True
    assert second_turn.previous_action.post_action_frame_reconciled is True
    assert second_turn.previous_action.target_binding_valid is True
    assert second_turn.mission.unchanged_frame_streak == 1
    assert second_turn.mission.last_transition.local_visual_change == "unchanged"
    assert second_turn.mission.last_transition.frame_comparable is True
    assert "semantic_completion" not in second_turn.mission.last_transition.model_dump()
    assert raw_calls[0][0:2] == (110, 120)
    assert raw_calls[0][-1] is FinalPointerGuardDecision.ADMITTED
    assert [record[1]["type"] for record in trace.records] == [
        "vnext_mission_runtime_started",
        "vnext_mission_runtime_turn",
        "vnext_mission_runtime_turn",
        "vnext_mission_runtime_finished",
    ]
    assert all(run_id == "arp-run-1" for run_id, _record in trace.records)
    action_trace = trace.records[1][1]
    assert action_trace["step"] == 1
    assert action_trace["model_role"] == "visual_actor"
    assert action_trace["previous_action_projected"] is False
    assert action_trace["accepted_tool_name"] == "click"
    assert action_trace["accepted_events"] == [{
        "event_id": action_trace["accepted_event_ids"][0],
        "kind": "transition_recorded",
    }]
    assert action_trace["local_visual_change"] == "unchanged"
    assert action_trace["frame_comparable"] is True
    assert action_trace["no_progress_streak"] == 1
    claim_trace = trace.records[2][1]
    assert claim_trace["step"] == 2
    assert claim_trace["model_role"] == "visual_actor"
    assert claim_trace["previous_action_projected"] is True
    assert claim_trace["accepted_tool_name"] == "claim_obligation_done"
    assert [item["kind"] for item in claim_trace["accepted_events"]] == [
        "obligation_claimed",
        "verification_recorded",
        "mission_verified",
    ]
    assert {
        item["event_id"] for item in claim_trace["accepted_events"]
    } == set(claim_trace["accepted_event_ids"])


def test_live_coordinator_projects_previous_action_into_provider_request() -> None:
    requests = []

    def tool_call(name, arguments):
        return SimpleNamespace(
            type="function",
            function=SimpleNamespace(
                name=name,
                arguments=json.dumps(arguments),
            ),
        )

    responses = [
        tool_call("click", {
            "image_x": 100,
            "image_y": 100,
            "local_intent": "focus the visible target",
        }),
        tool_call("claim_obligation_done", {
            "concise_claim": "the requested state is now visible",
        }),
    ]

    def create(**request):
        requests.append(request)
        call = responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[call],
        ))])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    actor = OpenAIVisualActor(client, model="test-visual-actor")

    result = _coordinator(actor, PositiveVisualModel()).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert len(requests) == 2
    assert [message["role"] for message in requests[1]["messages"]] == [
        "system", "assistant", "tool", "user",
    ]
    previous = json.loads(
        requests[1]["messages"][1]["tool_calls"][0]["function"]["arguments"]
    )
    assert previous == {
        "image_x": 100,
        "image_y": 100,
        "local_intent": "focus the visible target",
    }
    effect = json.loads(requests[1]["messages"][2]["content"])
    assert effect["semantic_completion"] == "unknown"
    assert effect["causal_attribution"] == "unknown"


def test_repeated_unchanged_actions_are_replay_derived_actor_feedback() -> None:
    actor = ScriptedActor([
        _click(image_x=100, image_y=100, local_intent="select the first target"),
        _click(image_x=180, image_y=140, local_intent="select the second target"),
        _claim(),
    ])
    verifier = PositiveVisualModel()
    trace = Trace()
    raw_calls = []

    result = _coordinator(
        actor,
        verifier,
        trace=trace,
        raw_calls=raw_calls,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert len(actor.calls) == 3
    assert len(raw_calls) == 2
    assert len(verifier.calls) == 1
    assert actor.calls[0][0].previous_action is None
    assert actor.calls[1][0].previous_action is not None
    assert actor.calls[2][0].previous_action is not None
    assert dict(actor.calls[1][0].previous_action.arguments) == {
        "image_x": 100, "image_y": 100,
    }
    assert dict(actor.calls[2][0].previous_action.arguments) == {
        "image_x": 180, "image_y": 140,
    }
    assert actor.calls[1][0].previous_action.local_intent == "select the first target"
    assert actor.calls[2][0].previous_action.local_intent == "select the second target"
    assert actor.calls[1][0].mission.unchanged_frame_streak == 1
    assert actor.calls[2][0].mission.unchanged_frame_streak == 2
    turn_records = [
        record for _run_id, record in trace.records
        if record["type"] == "vnext_mission_runtime_turn"
    ]
    assert [record["no_progress_streak"] for record in turn_records] == [
        1,
        2,
        None,
    ]
    assert [record["step"] for record in turn_records] == [1, 2, 3]
    assert [record["model_role"] for record in turn_records] == [
        "visual_actor", "visual_actor", "visual_actor",
    ]
    assert [record["accepted_tool_name"] for record in turn_records] == [
        "click", "click", "claim_obligation_done",
    ]


def test_accepted_blocked_report_is_the_only_proof_of_accepted_blocked_tool() -> None:
    actor = ScriptedActor([_blocked()])
    verifier = PositiveVisualModel()
    trace = Trace()
    raw_calls = []

    result = _coordinator(
        actor, verifier, trace=trace, raw_calls=raw_calls,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert raw_calls == []
    assert verifier.calls == []
    turn = next(
        record for _run_id, record in trace.records
        if record["type"] == "vnext_mission_runtime_turn"
    )
    assert turn["step"] == 1
    assert turn["model_role"] == "visual_actor"
    assert turn["proposal_kind"] == "blocked"
    assert turn["accepted_tool_name"] == "blocked"
    assert turn["accepted_events"] == [{
        "event_id": turn["accepted_event_ids"][0],
        "kind": "obligation_blocked",
    }]


def test_turn_trace_never_reattributes_a_previous_action_effect() -> None:
    actor = ScriptedActor([_click(), _invalid_click(), _claim()])
    trace = Trace()

    result = _coordinator(
        actor,
        PositiveVisualModel(),
        trace=trace,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    turns = [
        record for _run_id, record in trace.records
        if record["type"] == "vnext_mission_runtime_turn"
    ]
    assert turns[0]["local_visual_change"] == "unchanged"
    assert turns[0]["no_progress_streak"] == 1
    assert turns[1]["status"] == "invalid_proposal"
    assert turns[1]["step"] == 2
    assert turns[1]["model_role"] == "visual_actor"
    assert turns[1]["accepted_tool_name"] is None
    assert turns[1]["accepted_event_ids"] == []
    assert turns[1]["accepted_events"] == []
    assert turns[1]["local_visual_change"] is None
    assert turns[1]["frame_comparable"] is None
    assert turns[1]["no_progress_streak"] is None


def test_turn_rejected_before_actor_has_no_model_role_or_selected_tool() -> None:
    actor = ScriptedActor([_click()])
    trace = Trace()

    result = _coordinator(
        actor,
        PositiveVisualModel(),
        trace=trace,
        frame_max_age_s=0.5,
        clock=lambda: 100.0,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []
    turn = next(
        record for _run_id, record in trace.records
        if record["type"] == "vnext_mission_runtime_turn"
    )
    assert turn["proposal_kind"] is None
    assert turn["model_role"] is None
    assert turn["accepted_tool_name"] is None
    assert turn["accepted_event_ids"] == []
    assert turn["accepted_events"] == []


def test_actor_error_retains_explicit_turn_model_role() -> None:
    class RaisingActor:
        def propose(self, turn, **kwargs):
            raise RuntimeError("provider request failed")

    trace = Trace()

    result = _coordinator(
        RaisingActor(),
        PositiveVisualModel(),
        trace=trace,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    turn = next(
        record for _run_id, record in trace.records
        if record["type"] == "vnext_mission_runtime_turn"
    )
    assert turn["status"] == "actor_error"
    assert turn["proposal_kind"] is None
    assert turn["model_role"] == "visual_actor"
    assert turn["previous_action_projected"] is False
    assert turn["accepted_events"] == []


def test_live_turn_sidecar_uses_real_trace_recorder_without_product_bus_events(
    tmp_path, monkeypatch,
) -> None:
    from winpilot.recorder import trace as trace_module
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace_module, "TRACES_DIR", tmp_path)
    recorder = trace_module.TraceRecorder()
    product_events = None
    raw_calls = []
    verifier = PositiveVisualModel()
    try:
        recorder.start("arp-run-1")
        product_events = BUS.subscribe()
        result = _coordinator(
            ScriptedActor([_click(), _claim()]),
            verifier,
            trace=recorder,
            raw_calls=raw_calls,
        ).run(_context())
        recorder.stop()
        published = []
        while not product_events.empty():
            published.append(product_events.get_nowait())
    finally:
        if product_events is not None:
            BUS.unsubscribe(product_events)
        BUS.remove_sink(recorder._on_event)

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert len(raw_calls) == 1
    assert len(verifier.calls) == 1
    assert all(
        event.get("type") != "vnext_mission_runtime_turn"
        for event in published
    )
    records = [
        json.loads(line)
        for line in (tmp_path / "arp-run-1.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    turns = [
        record for record in records
        if record.get("type") == "vnext_mission_runtime_turn"
    ]
    assert [turn["step"] for turn in turns] == [1, 2]
    assert [turn["model_role"] for turn in turns] == [
        "visual_actor", "visual_actor",
    ]
    assert [turn["previous_action_projected"] for turn in turns] == [
        False, True,
    ]
    assert [turn["accepted_tool_name"] for turn in turns] == [
        "click", "claim_obligation_done",
    ]
    assert [item["kind"] for item in turns[0]["accepted_events"]] == [
        "transition_recorded",
    ]
    assert [item["kind"] for item in turns[1]["accepted_events"]] == [
        "obligation_claimed", "verification_recorded", "mission_verified",
    ]
    serialized = json.dumps(turns, ensure_ascii=False)
    for forbidden in (
        "messages", "image_b64", "image_url", "image_x", "image_y",
        '"previous_action":', "local_intent", "historyVisible",
    ):
        assert forbidden not in serialized


def test_live_coordinator_scroll_uses_same_actor_controller_and_verifier_chain():
    actor = ScriptedActor([_scroll(direction="down", notches=5), _claim()])
    verifier = PositiveVisualModel()
    raw_scroll_calls = []

    result = _coordinator(
        actor,
        verifier,
        raw_scroll_calls=raw_scroll_calls,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert len(raw_scroll_calls) == 1
    x, y, amount, hwnd, decision = raw_scroll_calls[0]
    assert x >= 10 and y >= 20
    assert amount == -5
    assert hwnd == 101
    assert decision is FinalPointerGuardDecision.ADMITTED
    assert len(verifier.calls) == 1
    assert "scroll" in {tool.name for tool in actor.calls[0][0].tools}


def test_uncertain_scroll_blocks_without_retry_claim_or_verifier_call():
    actor = ScriptedActor([_scroll(direction="up", notches=4), _claim()])
    verifier = PositiveVisualModel()
    raw_scroll_calls = []

    result = _coordinator(
        actor,
        verifier,
        raw_scroll_calls=raw_scroll_calls,
        raw_scroll_completed=None,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert len(raw_scroll_calls) == 1
    assert len(actor.calls) == 1
    assert verifier.calls == []


def test_live_controller_catalog_and_raw_inventory_are_exact() -> None:
    raw = RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_click=AdmittedPhysicalClickBackend(lambda: None),
        admitted_physical_scroll=AdmittedPhysicalScrollBackend(lambda: None),
        admitted_physical_type_text=AdmittedPhysicalTypeTextBackend(lambda: None),
        admitted_physical_hotkey=AdmittedPhysicalHotkeyBackend(lambda: None),
    ))
    snapshot = build_live_runtime_snapshot()

    assert frozenset(snapshot.controller_tool_names) == frozenset(
        AtomicDesktopController.supported_tool_names
    )
    assert raw.admitted_action_names == tuple(sorted(
        AtomicDesktopController.required_raw_action_names
    ))


@pytest.mark.parametrize("missing", ["click", "scroll", "type_text", "hotkey"])
def test_live_coordinator_rejects_incomplete_raw_inventory_before_actor(
    missing,
) -> None:
    actor = ScriptedActor([_click()])

    with pytest.raises(
        LiveDesktopCoordinatorError, match="raw backend inventory"
    ):
        _coordinator(
            actor,
            PositiveVisualModel(),
            omit_admitted_backend=missing,
        )

    assert actor.calls == []


def test_keyboard_turn_sidecar_records_only_tool_identity_not_content() -> None:
    secret_text = "p4b1-private-text-7419"
    type_calls = []
    hotkey_calls = []
    trace = Trace()
    actor = ScriptedActor([
        _type_text(secret_text),
        _hotkey(modifiers=("ctrl", "shift"), key="q"),
        _claim(),
    ])

    result = _coordinator(
        actor,
        PositiveVisualModel(),
        trace=trace,
        raw_type_calls=type_calls,
        raw_hotkey_calls=hotkey_calls,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert type_calls[0][0] == secret_text
    assert hotkey_calls[0][0] == HotkeyChord(("ctrl", "shift", "q"))
    turns = [
        event for _run_id, event in trace.records
        if event["type"] == "vnext_mission_runtime_turn"
    ]
    assert [turn["accepted_tool_name"] for turn in turns] == [
        "type_text", "hotkey", "claim_obligation_done",
    ]
    wire = json.dumps(turns, ensure_ascii=False)
    assert secret_text not in wire
    for forbidden in ('"modifiers"', '"key"', '"ctrl"', '"shift"'):
        assert forbidden not in wire


def test_one_runtime_snapshot_drives_state_profile_broker_and_trace(monkeypatch):
    snapshot = build_live_runtime_snapshot()
    trace = Trace()
    observed = {
        "profile_available": None,
        "profile_providers": None,
        "broker_inputs": [],
    }

    original_safe_default = coordinator_module.ExecutionPolicy.safe_default
    original_project = coordinator_module.CapabilityBroker.project

    def safe_default(policy, *args, **kwargs):
        observed["profile_available"] = tuple(
            kwargs["available_capabilities"]
        )
        observed["profile_providers"] = tuple(
            kwargs["provider_availability"]
        )
        return original_safe_default(policy, *args, **kwargs)

    def project(broker, mission, profile, state, grants=()):
        observed["broker_inputs"].append((
            broker.catalog,
            state.available_capabilities,
            profile.accepted_capabilities,
            profile.services.obligation_verifier,
        ))
        return original_project(broker, mission, profile, state, grants)

    monkeypatch.setattr(
        coordinator_module.ExecutionPolicy, "safe_default", safe_default,
    )
    monkeypatch.setattr(
        coordinator_module.CapabilityBroker, "project", project,
    )

    coordinator = _coordinator(
        ScriptedActor([_click(), _claim()]),
        PositiveVisualModel(),
        runtime_snapshot=snapshot,
        trace=trace,
    )
    result = coordinator.run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE
    assert coordinator.runtime_capability_snapshot is snapshot
    expected_capabilities = snapshot.available_capabilities
    expected_providers = (
        ("obligation_verifier",)
        if "obligation_verifier" in snapshot.available_services
        else ()
    )
    expected_profile_capabilities = tuple(sorted(
        {"visual.interact"} & set(expected_capabilities)
    ))
    assert observed["profile_available"] == expected_capabilities
    assert observed["profile_providers"] == expected_providers
    assert observed["broker_inputs"]
    assert all(
        catalog is snapshot.catalog
        and available == expected_capabilities
        and accepted == expected_profile_capabilities
        and verifier_enabled == bool(expected_providers)
        for catalog, available, accepted, verifier_enabled
        in observed["broker_inputs"]
    )
    started = trace.records[0][1]
    assert started["runtime_capability_snapshot_fingerprint"] == (
        snapshot.fingerprint
    )
    assert started["tool_catalog_fingerprint"] == snapshot.catalog.fingerprint
    assert started["tool_names"] == [
        tool.name for tool in snapshot.catalog.tools
    ]


def test_missing_contract_fails_before_actor_or_raw_device():
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    raw_calls = []
    context = _context()
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph={"metadata": {}},
        inputs=context.inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(
        actor, verifier, raw_calls=raw_calls,
    ).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert result.output is None
    assert actor.calls == []
    assert verifier.calls == []
    assert raw_calls == []


def test_invalid_handoff_fails_closed_without_model_or_legacy_fallback():
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    raw_calls = []
    context = _context()
    inputs = dict(context.inputs)
    inputs["execution_context"] = dict(inputs["execution_context"])
    inputs["execution_context"]["entrypoint"] = "api"
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph=context.graph,
        inputs=inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(
        actor, verifier, raw_calls=raw_calls,
    ).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []
    assert verifier.calls == []
    assert raw_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("foreground_verified", False),
        ("process_id", 8),
    ),
)
def test_handoff_identity_mismatch_fails_before_actor(field, value):
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    context = _context()
    inputs = dict(context.inputs)
    execution_context = dict(inputs["execution_context"])
    target_window = dict(execution_context["target_window"])
    target_window[field] = value
    execution_context["target_window"] = target_window
    inputs["execution_context"] = execution_context
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph=context.graph,
        inputs=inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(actor, verifier).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []
    assert verifier.calls == []


def test_getwindowrect_and_dwm_geometry_may_differ_before_current_binding():
    actor = ScriptedActor([_click(), _claim()])
    verifier = PositiveVisualModel()
    context = _context()
    inputs = dict(context.inputs)
    execution_context = dict(inputs["execution_context"])
    target_window = dict(execution_context["target_window"])
    target_window["rect"] = [5, 15, 815, 625]
    execution_context["target_window"] = target_window
    inputs["execution_context"] = execution_context
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph=context.graph,
        inputs=inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(actor, verifier).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.DONE


def test_malformed_handoff_geometry_fails_before_actor():
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    context = _context()
    inputs = dict(context.inputs)
    execution_context = dict(inputs["execution_context"])
    target_window = dict(execution_context["target_window"])
    target_window["rect"] = [10, 20, 10, 620]
    execution_context["target_window"] = target_window
    inputs["execution_context"] = execution_context
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph=context.graph,
        inputs=inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(actor, verifier).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []
    assert verifier.calls == []


def test_control_surface_that_did_not_yield_is_rejected_before_actor():
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    context = _context()
    inputs = dict(context.inputs)
    execution_context = dict(inputs["execution_context"])
    handoff = dict(execution_context["desktop_handoff"])
    handoff["yielded"] = False
    execution_context["desktop_handoff"] = handoff
    inputs["execution_context"] = execution_context
    context = DesktopRuntimeCoordinatorContext(
        run_id=context.run_id,
        graph=context.graph,
        inputs=inputs,
        stop_event=context.stop_event,
        wait_if_paused=context.wait_if_paused,
        is_paused=context.is_paused,
    )

    result = _coordinator(actor, verifier).run(context)

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []
    assert verifier.calls == []


def test_turn_budget_is_explicit_and_never_fabricates_completion():
    actor = ScriptedActor([_invalid_click(), _invalid_click()])
    verifier = PositiveVisualModel()

    result = _coordinator(
        actor, verifier, max_turns=2,
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert "turn_budget_exhausted" in result.error
    assert len(actor.calls) == 2
    assert verifier.calls == []


def test_canonical_contract_budget_caps_runtime_turns() -> None:
    actor = ScriptedActor([_invalid_click() for _ in range(20)])
    verifier = PositiveVisualModel()
    trace = Trace()

    result = _coordinator(
        actor, verifier, trace=trace, max_turns=100,
    ).run(_context(contract=_task_contract(max_steps=3)))

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert len(actor.calls) == 3
    assert trace.records[0][1]["turn_budget"] == 3


def test_conflicting_contract_budgets_fail_before_actor() -> None:
    contract = _task_contract(max_steps=3)
    contract["session"] = {
        "session_id": "session-1",
        "mode": "bounded",
        "requested_max_steps": 4,
    }
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()

    result = _coordinator(actor, verifier).run(_context(contract=contract))

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []


@pytest.mark.parametrize("budget", (None, 0, -1, "3"))
def test_invalid_or_missing_stage_budget_fails_closed(budget) -> None:
    contract = _task_contract(max_steps=3)
    parameters = contract["stages"][0]["parameters"]
    if budget is None:
        parameters.pop("max_steps")
    else:
        parameters["max_steps"] = budget
    actor = ScriptedActor([_click()])

    result = _coordinator(actor, PositiveVisualModel()).run(
        _context(contract=contract)
    )

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []


def test_click_canary_rejects_a_second_executable_stage() -> None:
    contract = _task_contract(max_steps=3)
    extra = dict(contract["stages"][0])
    extra["id"] = "stage-extra"
    extra["type"] = "OpenApplication"
    contract["stages"].append(extra)
    actor = ScriptedActor([_click()])

    result = _coordinator(actor, PositiveVisualModel()).run(
        _context(contract=contract)
    )

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == []


def test_stale_frame_after_actor_decision_never_reaches_raw_device() -> None:
    now = [10.0]
    raw_calls = []

    class AgingActor:
        calls = 0

        def propose(self, *_args, **_kwargs):
            self.calls += 1
            now[0] = 11.0
            return _click()

    actor = AgingActor()
    result = _coordinator(
        actor,
        PositiveVisualModel(),
        raw_calls=raw_calls,
        max_turns=2,
        frame_max_age_s=0.5,
        clock=lambda: now[0],
    ).run(_context())

    assert result.status is DesktopRuntimeCoordinatorStatus.FAILED
    assert actor.calls == 1
    assert raw_calls == []


def test_pause_during_actor_decision_discards_click_before_raw_device() -> None:
    entered = threading.Event()
    released = threading.Event()
    paused = threading.Event()
    stopped = threading.Event()
    raw_calls = []
    finished = []

    class BlockingActor:
        def propose(self, *_args, **_kwargs):
            entered.set()
            assert released.wait(2.0)
            return _click()

    base = _context(stop_event=stopped)

    def wait_if_paused():
        while paused.is_set() and not stopped.is_set():
            time.sleep(0.01)
        return not stopped.is_set()

    context = DesktopRuntimeCoordinatorContext(
        run_id=base.run_id,
        graph=base.graph,
        inputs=base.inputs,
        stop_event=stopped,
        wait_if_paused=wait_if_paused,
        is_paused=paused.is_set,
    )
    coordinator = _coordinator(
        BlockingActor(), PositiveVisualModel(), raw_calls=raw_calls,
    )
    thread = threading.Thread(
        target=lambda: finished.append(coordinator.run(context)),
    )
    thread.start()
    assert entered.wait(1.0)
    paused.set()
    released.set()
    time.sleep(0.1)
    assert raw_calls == []
    stopped.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert raw_calls == []
    assert finished[0].status is DesktopRuntimeCoordinatorStatus.CANCELED


def test_factory_accepts_only_injected_physical_owner() -> None:
    raw = RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_click=AdmittedPhysicalClickBackend(lambda: None),
        admitted_physical_scroll=AdmittedPhysicalScrollBackend(lambda: None),
        admitted_physical_type_text=AdmittedPhysicalTypeTextBackend(lambda: None),
        admitted_physical_hotkey=AdmittedPhysicalHotkeyBackend(lambda: None),
    ))
    coordinator = build_live_desktop_runtime_coordinator(
        api_config={}, raw_device=raw,
    )

    assert isinstance(coordinator, LiveDesktopRuntimeCoordinator)
    expected_snapshot = build_live_runtime_snapshot()
    assert type(coordinator.runtime_capability_snapshot) is type(expected_snapshot)
    assert coordinator.runtime_capability_snapshot.to_dict() == (
        expected_snapshot.to_dict()
    )
    with pytest.raises(LiveDesktopCoordinatorError, match="raw backend inventory"):
        build_live_desktop_runtime_coordinator(
            api_config={}, raw_device=RawDeviceExecutor(RawDeviceBackends()),
        )
    with pytest.raises(TypeError, match="composition root"):
        build_live_desktop_runtime_coordinator(api_config={}, raw_device=None)


def test_preexisting_cancellation_performs_zero_model_or_input_calls():
    stopped = threading.Event()
    stopped.set()
    actor = ScriptedActor([_click()])
    verifier = PositiveVisualModel()
    raw_calls = []

    result = _coordinator(
        actor, verifier, raw_calls=raw_calls,
    ).run(_context(stop_event=stopped))

    assert result.status is DesktopRuntimeCoordinatorStatus.CANCELED
    assert actor.calls == []
    assert verifier.calls == []
    assert raw_calls == []


def test_coordinator_source_has_no_app_benchmark_phrase_or_coordinate_rules():
    source = Path(
        "python/winpilot_home/winpilot/vnext/bridge/coordinator.py"
    ).read_text(encoding="utf-8").casefold()
    for forbidden in (
        "precise-click", "game-drag", "qq", "douyin", "paint", "krita",
        "benchmark", "fixed_coordinate", "legacy fallback",
    ):
        assert forbidden not in source
