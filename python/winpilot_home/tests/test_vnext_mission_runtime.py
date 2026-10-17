from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import threading
from typing import Any, Callable, Mapping

import pytest

from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT, ActorDecisionEnvelope
from winpilot.vnext.mission import (
    MissionState,
    MissionStatus,
    TargetBinding,
)
from winpilot.vnext.runtime import (
    APPROVED_RUNTIME_TOOLS,
    ActorBudgetView,
    ActorToolView,
    ActorTaskState,
    ActorTaskStateError,
    ActorTurnInput,
    ActorTaskStateUpdate,
    ActorTurnDecision,
    ControllerOutcome,
    ControllerTransition,
    MissionRuntime,
    MissionRuntimeConfigurationError,
    PointerDisposition,
    PointerLeaseState,
    RuntimeContractError,
    RuntimeFrame,
    RuntimeFrameIdentity,
    RuntimeSnapshot,
    RuntimeStatus,
    RuntimeTargetIdentity,
)


MISSION_ID = "mission-runtime"
RUN_ID = "run-runtime"
GOAL = "Make the requested visible state exist"
HWND = 9123
PROCESS_ID = 44
EPOCH = 7
def _empty_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def _point_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }


def _drag_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x1": {"type": "number", "minimum": 0},
            "image_y1": {"type": "number", "minimum": 0},
            "image_x2": {"type": "number", "minimum": 0},
            "image_y2": {"type": "number", "minimum": 0},
            "keep_holding": {"type": "boolean"},
        },
        "required": ["image_x1", "image_y1", "image_x2", "image_y2"],
    }


def _scroll_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
            "amount": {
                "type": "integer",
                "minimum": -1200,
                "maximum": 1200,
                "not": {"const": 0},
            },
        },
        "required": ["image_x", "image_y", "amount"],
    }


def _text_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 16384},
        },
        "required": ["text"],
    }


def _hotkey_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
            },
        },
        "required": ["keys"],
    }


def _move_schema() -> dict[str, Any]:
    return _point_schema()


def _sequence_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sequence": {"type": "string", "minLength": 1},
        },
        "required": ["sequence"],
    }


def _tool(
    name: str,
    *,
    requires_local_intent: bool,
    schema: dict[str, Any],
) -> ActorToolView:
    return ActorToolView(
        name=name,
        schema=schema,
        requires_local_intent=requires_local_intent,
    )


def _catalog(*, omit_last: bool = False) -> tuple[ActorToolView, ...]:
    schemas = {
        "click": _point_schema(),
        "double_click": _point_schema(),
        "move_mouse": _move_schema(),
        "drag": _drag_schema(),
        "scroll": _scroll_schema(),
        "type_text": _text_schema(),
        "hotkey": _hotkey_schema(),
        "input_sequence": _sequence_schema(),
        "launch_application": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "app": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
                "arguments": {
                    "type": "string",
                    "maxLength": 4096,
                },
            },
            "required": ["app", "arguments"],
        },
    }
    tools = [
        _tool(
            name,
            requires_local_intent=True,
            schema=schemas[name],
        )
        for name in (
                "click",
                "double_click",
                "move_mouse",
                "drag",
                "scroll",
                "type_text",
                "hotkey",
                "input_sequence",
                "launch_application",
        )
    ]
    tools.extend(
        (
            _tool(
                "observe",
                requires_local_intent=False,
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 16384,
                        },
                    },
                    "required": ["question"],
                },
            ),
            _tool(
                "claim_obligation_done",
                requires_local_intent=False,
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "concise_claim": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 16384,
                        },
                    },
                    "required": ["concise_claim"],
                },
            ),
            _tool(
                "blocked",
                requires_local_intent=False,
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "blocker_kind": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 192,
                        },
                        "observed_facts": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 16384,
                            },
                            "maxItems": 64,
                            "uniqueItems": True,
                        },
                    },
                    "required": ["blocker_kind", "observed_facts"],
                },
            ),
        )
    )
    return tuple(tools[:-1] if omit_last else tools)


def _action(
    action: str = "click",
    *,
    keep_holding: bool | None = None,
) -> dict[str, Any]:
    args: dict[str, Any]
    if action in {"click", "double_click"}:
        args = {"image_x": 10, "image_y": 20}
    elif action == "move_mouse":
        args = {"image_x": 10, "image_y": 20}
    elif action == "drag":
        args = {
            "image_x1": 10,
            "image_y1": 20,
            "image_x2": 30,
            "image_y2": 40,
        }
        if keep_holding is not None:
            args["keep_holding"] = keep_holding
    elif action == "scroll":
        args = {"image_x": 10, "image_y": 20, "amount": -120}
    elif action == "type_text":
        args = {"text": "hello"}
    elif action == "hotkey":
        args = {"keys": ["ctrl", "a"]}
    elif action == "input_sequence":
        args = {"sequence": "move 10,20; click left"}
    else:
        args = {}
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": action,
        "args": args,
        "local_intent": f"perform one {action}",
    }


def _claim() -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "claim_obligation_done",
        "concise_claim": "the requested state is visibly present",
    }


def _observe() -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "observe",
        "observation_kind": "full",
        "image_region": None,
        "question": "Refresh the whole desktop before choosing the next step",
    }


def _blocked() -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "blocked",
        "blocker_kind": "visible_precondition_missing",
        "observed_facts": ["the required control is not visible"],
    }


def _task_decision(
    proposal: Mapping[str, Any],
    *,
    scratchpad: str | None = None,
    todos: list[dict[str, str]] | None = None,
) -> ActorTurnDecision:
    update: dict[str, Any] = {"scratchpad": scratchpad}
    if todos is not None:
        update["todos"] = todos
    return ActorTurnDecision(
        proposal=proposal,
        task_state_update=ActorTaskStateUpdate.from_dict(update),
    )


class FakeActor:
    def __init__(
        self,
        *proposals: ActorTurnDecision | Mapping[str, Any],
    ) -> None:
        self.proposals = list(proposals)
        self.turns: list[ActorTurnInput] = []
        self.rejection_feedbacks: list[str | None] = []
        self.calls = 0
        self.on_propose: Callable[[], None] | None = None
        self.raise_error: Exception | None = None

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
        rejection_feedback: str | None = None,
    ) -> ActorTurnDecision | Mapping[str, Any]:
        assert image_b64
        assert image_media_type == "image/jpeg"
        assert submitted_size == (320, 240)
        assert stop_event is None or isinstance(stop_event, threading.Event)
        self.calls += 1
        self.turns.append(turn)
        self.rejection_feedbacks.append(rejection_feedback)
        if self.on_propose is not None:
            self.on_propose()
        if self.raise_error is not None:
            raise self.raise_error
        if not self.proposals:
            raise AssertionError("unexpected Actor call")
        proposal = self.proposals.pop(0)
        return proposal if type(proposal) is ActorTurnDecision else dict(proposal)


class FakeState:
    def __init__(self, snapshot: RuntimeSnapshot) -> None:
        self.snapshot = snapshot
        self.observe_calls = 0
        self.current_calls = 0
        self.raise_current_on: int | None = None

    def observe(self) -> RuntimeSnapshot:
        self.observe_calls += 1
        return self.snapshot

    def install_frame(
        self,
        frame: RuntimeFrame,
        *,
        pointer: PointerLeaseState | None = None,
    ) -> None:
        self.snapshot = RuntimeSnapshot(
            frame=frame,
            target=self.snapshot.target,
            pointer=(self.snapshot.pointer if pointer is None else pointer),
            paused=self.snapshot.paused,
            canceled=self.snapshot.canceled,
        )


class FakeController:
    def __init__(
        self,
        *,
        state: FakeState,
        make_post_frame: Callable[
            [ActorDecisionEnvelope],
            RuntimeFrame,
        ],
    ) -> None:
        self.state = state
        self.make_post_frame = make_post_frame
        self.mode = "completed"
        self.calls = 0
        self.rejection_error_kind: str | None = None
        self.envelopes: list[ActorDecisionEnvelope] = []
        self.stop_before_admission: threading.Event | None = None
        self.stop_after_action: threading.Event | None = None
        self.pointer_after_override: PointerLeaseState | None = None

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None = None,
    ) -> ControllerTransition:
        self.calls += 1
        self.envelopes.append(envelope)
        args = envelope.proposal.model_dump(mode="json")["args"]
        action = envelope.proposal.action
        if self.stop_before_admission is not None:
            self.stop_before_admission.set()
        if self.mode == "raise":
            raise RuntimeError("synthetic controller failure")
        if self.mode == "rejected":
            return ControllerTransition(
                outcome=ControllerOutcome.REJECTED,
                action_id=envelope.decision_id,
                action=action,
                input_completed=False,
                error_kind=self.rejection_error_kind,
            )
        if self.mode == "unknown":
            return ControllerTransition(
                outcome=ControllerOutcome.UNKNOWN,
                action_id=envelope.decision_id,
                action=action,
                input_completed=None,
                error_kind=self.rejection_error_kind,
            )
        if self.mode == "unknown_applied":
            return ControllerTransition(
                outcome=ControllerOutcome.UNKNOWN,
                action_id=envelope.decision_id,
                action=action,
                input_completed=True,
            )
        frame = self.make_post_frame(envelope)
        pointer_after = PointerLeaseState(PointerDisposition.RELEASED)
        if action == "drag" and args.get("keep_holding", False):
            before_pointer = self.state.snapshot.pointer
            pointer_after = PointerLeaseState(
                PointerDisposition.HELD,
                (
                    before_pointer.lease_id
                    if before_pointer.disposition is PointerDisposition.HELD
                    else "pointer-lease-runtime"
                ),
                True,
            )
        if self.pointer_after_override is not None:
            pointer_after = self.pointer_after_override
        self.state.install_frame(frame, pointer=pointer_after)
        if self.stop_after_action is not None:
            self.stop_after_action.set()
        return ControllerTransition(
            outcome=ControllerOutcome.COMPLETED,
            action_id=envelope.decision_id,
            action=action,
            input_completed=True,
            visible_effect="post-action frame captured",
            post_frame_id=frame.frame_id,
        )


@dataclass
class Harness:
    reducer: MissionState
    actor: FakeActor
    state: FakeState
    controller: FakeController
    runtime: MissionRuntime
    catalog: tuple[ActorToolView, ...]

    def make_frame(
        self,
        frame_id: int,
    ) -> RuntimeFrame:
        image_b64 = f"ZnJhbWUt{frame_id:02d}"
        digest = hashlib.sha256(image_b64.encode("ascii")).hexdigest()
        identity = RuntimeFrameIdentity(
            frame_id=frame_id,
            epoch=EPOCH,
            hwnd=HWND,
            region=(100, 200, 640, 480),
            scope="target_window",
            submitted_size=(320, 240),
            image_sha256=digest,
        )
        return RuntimeFrame(
            frame_id=frame_id,
            epoch=EPOCH,
            hwnd=HWND,
            region=identity.region,
            scope=identity.scope,
            submitted_size=identity.submitted_size,
            image_b64=image_b64,
            image_sha256=digest,
        )


def _harness(
    *proposals: ActorTurnDecision | Mapping[str, Any],
    mission: MissionState | None = None,
    catalog: tuple[ActorToolView, ...] | None = None,
    bind_target: bool = True,
    budget_provider: Callable[[int], Mapping[str, Any]] | None = None,
) -> Harness:
    actor = FakeActor(*(proposals or (_action(),)))
    catalog = catalog or _catalog()

    image_b64 = "ZnJhbWUtMDE="
    digest = hashlib.sha256(image_b64.encode("ascii")).hexdigest()
    frame_identity = RuntimeFrameIdentity(
        frame_id=1,
        epoch=EPOCH,
        hwnd=HWND,
        region=(100, 200, 640, 480),
        scope="target_window",
        submitted_size=(320, 240),
        image_sha256=digest,
    )
    binding = TargetBinding(
        binding_id="binding-current",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="opaque-process",
        title_snapshot="",
        epoch=EPOCH,
    )
    reducer = mission or MissionState(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        original_goal=GOAL,
        target_binding=binding if bind_target else None,
    )
    frame = RuntimeFrame(
        frame_id=1,
        epoch=EPOCH,
        hwnd=HWND,
        region=frame_identity.region,
        scope=frame_identity.scope,
        submitted_size=frame_identity.submitted_size,
        image_b64=image_b64,
        image_sha256=digest,
    )
    snapshot = RuntimeSnapshot(
        frame=frame,
        target=RuntimeTargetIdentity.from_binding(binding),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
    )
    state = FakeState(snapshot)
    holder: dict[str, Harness] = {}

    def make_post_frame(
        envelope: ActorDecisionEnvelope,
    ) -> RuntimeFrame:
        del envelope
        return holder["harness"].make_frame(
            state.snapshot.frame.frame_id + 1,
        )

    controller = FakeController(
        state=state,
        make_post_frame=make_post_frame,
    )
    runtime = MissionRuntime(
        mission=reducer,
        tools=catalog,
        actor=actor,
        state=state,
        controller=controller,
        budget_facts_provider=budget_provider,
    )
    harness = Harness(
        reducer=reducer,
        actor=actor,
        state=state,
        controller=controller,
        runtime=runtime,
        catalog=catalog,
    )
    holder["harness"] = harness
    return harness


@pytest.mark.parametrize(
    "action",
    (
        "click", "double_click", "move_mouse", "drag", "scroll",
        "type_text", "hotkey", "input_sequence",
    ),
)
def test_minimal_runtime_executes_each_approved_action_once(action: str) -> None:
    harness = _harness(_action(action))

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1
    assert harness.reducer.revision == 1
    assert harness.reducer.card.last_transition.action == action


def test_observe_requests_a_fresh_turn_without_physical_input() -> None:
    harness = _harness(_observe())

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.OBSERVATION_REQUESTED
    assert result.proposal_kind == "observe"
    assert result.detail == _observe()["question"]
    assert harness.state.observe_calls == 1
    assert harness.controller.calls == 0
    assert harness.reducer.revision == 0


def test_cross_frame_held_drag_preserves_then_releases_one_lease() -> None:
    harness = _harness(
        _action("drag", keep_holding=True),
        _action("drag", keep_holding=True),
        _action("drag", keep_holding=False),
    )

    first = harness.runtime.run_one_turn(turn_index=0)
    first_pointer = harness.state.snapshot.pointer
    second = harness.runtime.run_one_turn(turn_index=1)
    second_pointer = harness.state.snapshot.pointer
    third = harness.runtime.run_one_turn(turn_index=2)
    third_pointer = harness.state.snapshot.pointer

    assert (
        first.status,
        second.status,
        third.status,
    ) == (
        RuntimeStatus.ACTION_RECORDED,
        RuntimeStatus.ACTION_RECORDED,
        RuntimeStatus.ACTION_RECORDED,
    )
    assert first_pointer.disposition is PointerDisposition.HELD
    assert first_pointer.physically_owned is True
    assert second_pointer == first_pointer
    assert third_pointer == PointerLeaseState(PointerDisposition.RELEASED)
    assert harness.actor.turns[0].pointer_held is False
    assert harness.actor.turns[1].pointer_held is True
    assert harness.actor.turns[2].pointer_held is True
    # A held button remains ordinary physical input state. It must not hide
    # mouse, wheel, or keyboard actions that a human can combine with it.
    held_tool_names = tuple(
        tool.name for tool in harness.actor.turns[1].tools
    )
    assert "drag" in held_tool_names
    assert "click" in held_tool_names
    assert "double_click" in held_tool_names
    assert "scroll" in held_tool_names
    assert "hotkey" in held_tool_names
    assert "input_sequence" in held_tool_names
    assert "claim_obligation_done" in held_tool_names
    assert "blocked" in held_tool_names
    assert [
        envelope.pointer_disposition
        for envelope in harness.controller.envelopes
    ] == ["released", "held", "held"]
    assert [
        envelope.pointer_lease_id
        for envelope in harness.controller.envelopes
    ] == [
        None,
        "pointer-lease-runtime",
        "pointer-lease-runtime",
    ]


def test_runtime_does_not_second_guess_controller_pointer_state() -> None:
    harness = _harness(_action("drag", keep_holding=True))
    harness.controller.pointer_after_override = PointerLeaseState(
        PointerDisposition.RELEASED
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1


def test_actor_can_verify_already_satisfied_goal_without_mutation() -> None:
    harness = _harness(_claim())

    assert harness.runtime.verified_completion_result is None
    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert (
        harness.runtime.verified_completion_result
        == "the requested state is visibly present"
    )
    assert harness.actor.calls == 1
    assert harness.controller.calls == 0
    assert harness.actor.turns[0].frame.authoritative is True
    assert harness.actor.turns[0].frame.fresh is True
    assert harness.reducer.status is MissionStatus.VERIFIED
    assert "the requested state is visibly present" not in json.dumps(
        harness.reducer.to_dict()
    )


def test_task_state_todos_derive_remaining_items() -> None:
    state = ActorTaskState.initial().apply(
        ActorTaskStateUpdate.from_dict(
            {
                "scratchpad": "Start the first phase",
                "todos": [
                    {
                        "content": "send the image",
                        "activeForm": "sending the image",
                        "status": "completed",
                    },
                    {
                        "content": "finish the game",
                        "activeForm": "finishing the game",
                        "status": "in_progress",
                    },
                ],
            }
        )
    )

    revised = state.apply(
        ActorTaskStateUpdate.from_dict(
            {"scratchpad": "The first phase is done"}
        )
    )

    assert revised.scratchpad == "The first phase is done"
    assert revised.remaining_items == ("finish the game",)

    cleared = revised.apply(
        ActorTaskStateUpdate.from_dict({"todos": []})
    )

    assert cleared.remaining_items == ()


def test_task_state_todos_merge_replace_and_clear() -> None:
    state = ActorTaskState.initial().apply(
        ActorTaskStateUpdate.from_dict(
            {
                "todos": [
                    {
                        "content": "draft the answer",
                        "activeForm": "drafting the answer",
                        "status": "completed",
                    },
                    {
                        "content": "verify the answer",
                        "activeForm": "verifying the answer",
                        "status": "in_progress",
                    },
                ]
            }
        )
    )

    assert [item.status for item in state.todos] == ["completed", "in_progress"]
    assert [item.content for item in state.todos] == [
        "draft the answer",
        "verify the answer",
    ]

    revised = state.apply(
        ActorTaskStateUpdate.from_dict(
            {
                "todos": [
                    {
                        "content": "verify the answer",
                        "activeForm": "verifying the answer",
                        "status": "completed",
                    }
                ]
            }
        )
    )

    assert [item.content for item in revised.todos] == ["verify the answer"]
    assert revised.todos[0].status == "completed"

    cleared = revised.apply(
        ActorTaskStateUpdate.from_dict({"todos": []})
    )

    assert cleared.todos == ()


def test_task_state_todos_reject_unknown_status() -> None:
    with pytest.raises(ActorTaskStateError):
        ActorTaskStateUpdate.from_dict(
            {
                "todos": [
                    {
                        "content": "x",
                        "activeForm": "doing x",
                        "status": "done",
                    }
                ]
            }
        )


def test_task_state_todos_reject_multiple_in_progress() -> None:
    with pytest.raises(ActorTaskStateError):
        ActorTaskStateUpdate.from_dict(
            {
                "todos": [
                    {
                        "content": "a",
                        "activeForm": "doing a",
                        "status": "in_progress",
                    },
                    {
                        "content": "b",
                        "activeForm": "doing b",
                        "status": "in_progress",
                    },
                ]
            }
        )


def test_visible_claim_supersedes_stale_actor_todos() -> None:
    harness = _harness(
        _task_decision(
            _action("click"),
            scratchpad="Music is playing",
            todos=[
                {
                    "content": "send playback image",
                    "activeForm": "sending playback image",
                    "status": "pending",
                },
                {
                    "content": "build and play game",
                    "activeForm": "building and playing game",
                    "status": "pending",
                },
            ],
        ),
        _task_decision(_claim()),
    )

    acted = harness.runtime.run_one_turn(turn_index=0)
    premature = harness.runtime.run_one_turn(turn_index=1)

    assert acted.status is RuntimeStatus.ACTION_RECORDED
    assert premature.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.runtime.task_state.remaining_items == (
        "send playback image",
        "build and play game",
    )
    assert harness.reducer.status is MissionStatus.VERIFIED


def test_type_text_is_absent_from_mission_bus_and_reducer_state() -> None:
    sentinel = "SENTINEL-mission-bus-type-text-31"
    proposal = _action("type_text")
    proposal["args"]["text"] = sentinel
    harness = _harness(proposal)

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert (
        harness.controller.envelopes[0].proposal.args["text"]
        == sentinel
    )
    reducer_wire = harness.reducer.to_dict()
    assert sentinel not in json.dumps(
        reducer_wire,
        ensure_ascii=False,
        sort_keys=True,
    )


def test_ephemeral_task_state_crosses_turns_but_not_mission_history() -> None:
    sentinel = "PRIVATE-GROUP-SUMMARY-7f4c"
    harness = _harness(
        _task_decision(
            _action("click"),
            scratchpad=(
                f"Summarize groups. Covered {sentinel}; one group remains. "
                f"{sentinel}: discussing a release. "
                f"Artifact C:/private/{sentinel}.txt. "
                "Stop when every visible group has a concise summary."
            ),
        ),
        _task_decision(
            _action("scroll"),
            scratchpad=f"All visible groups including {sentinel} summarized.",
        ),
    )

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.ACTION_RECORDED
    state = harness.runtime.task_state
    assert sentinel in harness.actor.turns[1].task_state.scratchpad
    assert state.scratchpad == (
        f"All visible groups including {sentinel} summarized."
    )
    durable_wire = harness.reducer.to_dict()
    assert sentinel not in json.dumps(durable_wire, ensure_ascii=False)
    assert "task_state_update" not in json.dumps(
        durable_wire,
        ensure_ascii=False,
    )


def test_actor_turn_carries_the_fuel_gauge_facts() -> None:
    """One mechanical budget gauge: spent vs max turns, calls, and seconds."""
    harness = _harness(
        _task_decision(_action("click")),
        budget_provider=lambda turn_index: {
            "max_turns": 60,
            "max_model_calls": 120,
            "max_runtime_s": 1800,
            "model_calls_used": 7 + turn_index,
            "elapsed_s": 42,
        },
    )

    result = harness.runtime.run_one_turn(turn_index=3)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    budget = harness.actor.turns[0].budget
    assert budget is not None
    assert budget.turns_used == 3
    assert budget.max_turns == 60
    assert budget.model_calls_used == 10
    assert budget.max_model_calls == 120
    assert budget.elapsed_s == 42
    assert budget.max_runtime_s == 1800


def test_budget_gauge_never_fails_a_turn() -> None:
    """A broken or missing gauge degrades to null, never to a mission error."""
    def broken(turn_index: int) -> dict[str, int]:
        del turn_index
        raise RuntimeError("gauge down")

    harness = _harness(
        _task_decision(_action("click")),
        budget_provider=broken,
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.actor.turns[0].budget is None


def test_auxiliary_task_state_failure_does_not_discard_a_valid_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(_task_decision(_action("click"), scratchpad="next"))
    initial = harness.runtime.task_state
    monkeypatch.setattr(
        ActorTaskState,
        "apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ActorTaskStateError("scratchpad unavailable")
        ),
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1
    assert harness.runtime.task_state == initial


def test_actor_turn_rejects_an_invalid_budget_view() -> None:
    with pytest.raises(RuntimeContractError):
        ActorBudgetView(
            turns_used=61,
            max_turns=60,
            model_calls_used=0,
            max_model_calls=120,
            elapsed_s=0,
            max_runtime_s=1800,
        )


def test_runtime_keeps_the_actor_latest_narration() -> None:
    """The Actor's own words survive as the run's honest progress note."""
    harness = _harness(
        _task_decision(
            _action("click"),
            scratchpad=None,
        ).__class__(
            proposal=_action("click"),
            task_state_update=ActorTaskStateUpdate.from_dict(
                {"scratchpad": None}
            ),
            narration="画板已打开，正在画猫的轮廓。",
        ),
        _task_decision(
            _action("drag"),
            scratchpad=None,
        ).__class__(
            proposal=_action("drag"),
            task_state_update=ActorTaskStateUpdate.from_dict(
                {"scratchpad": None}
            ),
            narration="轮廓画好了，现在补上耳朵和胡须。",
        ),
    )

    harness.runtime.run_one_turn(turn_index=0)
    assert harness.runtime.latest_narration == "画板已打开，正在画猫的轮廓。"
    harness.runtime.run_one_turn(turn_index=1)
    assert harness.runtime.latest_narration == "轮廓画好了，现在补上耳朵和胡须。"


def test_stale_or_unsupported_actor_decision_does_not_commit_task_state() -> None:
    update = {"scratchpad": "must_not_commit private-item"}
    invalid = _harness(_task_decision(_action("observe"), **update))
    invalid_initial = invalid.runtime.task_state

    invalid_result = invalid.runtime.run_one_turn(turn_index=0)

    assert invalid_result.status is RuntimeStatus.INVALID_PROPOSAL
    assert invalid.runtime.task_state == invalid_initial


def test_surplus_or_out_of_frame_action_args_reach_the_mechanical_owner() -> None:
    scratchpad = "route right, down, then right"
    action = _action("click")
    action["args"]["image_x"] = -1
    action["args"]["surplus_hint"] = "ignored by the mechanical owner"
    harness = _harness(
        _task_decision(action, scratchpad=scratchpad),
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1
    assert harness.runtime.task_state.scratchpad == scratchpad


def test_out_of_frame_point_reaches_the_physical_backend() -> None:
    action = _action("click")
    action["args"]["image_x"] = 321
    harness = _harness(action)

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1


def test_extent_style_edge_coordinate_is_admitted_for_nearest_pixel_mapping() -> None:
    edge_action = _action("click")
    edge_action["args"]["image_x"] = 320
    edge_action["args"]["image_y"] = 240
    harness = _harness(edge_action)

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1


def test_duplicate_current_state_probe_is_not_an_input_gate() -> None:
    harness = _harness(_action())
    harness.state.raise_current_on = 1

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1
    assert harness.state.current_calls == 0


def test_run_one_turn_forwards_rejection_feedback_to_the_actor() -> None:
    harness = _harness(_blocked())

    result = harness.runtime.run_one_turn(
        turn_index=0,
        rejection_feedback="tool arguments rejected: tool=hotkey",
    )

    assert result.status is RuntimeStatus.BLOCKED
    assert harness.actor.rejection_feedbacks == [
        "tool arguments rejected: tool=hotkey"
    ]


def test_previous_action_view_keeps_one_bounded_completed_intent() -> None:
    harness = _harness(
        _action("click"),
        _blocked(),
    )
    assert harness.runtime.run_one_turn(turn_index=0).status is RuntimeStatus.ACTION_RECORDED

    result = harness.runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert harness.actor.calls == 2
    previous = harness.actor.turns[1].previous_action
    assert previous is not None
    assert previous.action == "click"
    assert previous.semantic_summary == "click: perform one click"
    assert previous.local_intent == "perform one click"
    assert previous.input_completed is True
    assert previous.visible_effect == "post-action frame captured"
    wire = harness.actor.turns[1].to_dict()
    assert "mission_id" not in str(wire)
    assert "run_id" not in str(wire)
    assert "frame_id" not in str(wire)
    assert "binding-current" not in str(wire)
    assert str(HWND) not in str(wire)


def test_previous_action_intent_does_not_depend_on_private_evidence() -> None:
    harness = _harness(_action("click"), _blocked())
    assert harness.runtime.run_one_turn(turn_index=0).status is RuntimeStatus.ACTION_RECORDED

    result = harness.runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    previous = harness.actor.turns[1].previous_action
    assert previous is not None
    assert previous.local_intent == "perform one click"


def test_rejected_action_replaces_previous_with_proven_not_executed() -> None:
    harness = _harness(
        _action("click"),
        _action("scroll"),
        _blocked(),
    )
    completed = harness.runtime.run_one_turn(turn_index=0)
    harness.controller.mode = "rejected"
    rejected = harness.runtime.run_one_turn(turn_index=1)
    blocked = harness.runtime.run_one_turn(turn_index=2)

    assert completed.status is RuntimeStatus.ACTION_RECORDED
    assert rejected.status is RuntimeStatus.STALE_DISCARDED
    assert rejected.detail == (
        "Controller proved input did not execute: "
        "error_kind=unspecified; no replay occurred"
    )
    assert blocked.status is RuntimeStatus.BLOCKED
    previous = harness.actor.turns[2].previous_action
    assert previous is not None
    assert previous.action == "scroll"
    assert previous.input_completed is False
    assert previous.error_kind == ""


def test_rejected_transition_error_kind_reaches_actor_previous_action() -> None:
    harness = _harness(
        _action("click"),
        _action("drag"),
        _blocked(),
    )
    assert harness.runtime.run_one_turn(turn_index=0).status is RuntimeStatus.ACTION_RECORDED
    harness.controller.mode = "rejected"
    harness.controller.rejection_error_kind = "target_window_lost"
    rejected = harness.runtime.run_one_turn(turn_index=1)
    blocked = harness.runtime.run_one_turn(turn_index=2)

    assert rejected.status is RuntimeStatus.STALE_DISCARDED
    assert rejected.detail == (
        "Controller proved input did not execute: "
        "error_kind=target_window_lost; no replay occurred"
    )
    assert blocked.status is RuntimeStatus.BLOCKED
    previous = harness.actor.turns[2].previous_action
    assert previous is not None
    assert previous.action == "drag"
    assert previous.input_completed is False
    assert previous.error_kind == "target_window_lost"


def test_actor_receives_completed_action_order_with_semantic_intent() -> None:
    harness = _harness(
        _action("click"),
        _action("double_click"),
        _claim(),
    )

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)
    verified = harness.runtime.run_one_turn(turn_index=2)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.ACTION_RECORDED
    assert verified.status is RuntimeStatus.MISSION_VERIFIED


def test_previous_action_keeps_safe_direction_and_chord() -> None:
    scroll_up = _action("scroll")
    scroll_up["args"]["amount"] = 120
    harness = _harness(
        _action("scroll"),
        scroll_up,
        _action("hotkey"),
        _action("type_text"),
        _claim(),
    )

    for turn_index in range(4):
        result = harness.runtime.run_one_turn(turn_index=turn_index)
        assert result.status is RuntimeStatus.ACTION_RECORDED
    verified = harness.runtime.run_one_turn(turn_index=4)

    assert verified.status is RuntimeStatus.MISSION_VERIFIED
    assert (
        harness.actor.turns[1].previous_action.semantic_summary
        == "scroll: perform one scroll"
    )
    assert (
        harness.actor.turns[2].previous_action.semantic_summary
        == "scroll: perform one scroll"
    )
    assert (
        harness.actor.turns[3].previous_action.semantic_summary
        == "hotkey: perform one hotkey"
    )
    assert harness.reducer.last_transition is not None
    assert harness.reducer.last_transition.action == "type_text"
    history_wire = json.dumps(harness.reducer.to_dict())
    assert "image_x" not in history_wire
    assert "image_y" not in history_wire
    assert "hello" not in history_wire
    assert "perform one type_text" not in history_wire


def test_completed_action_intent_is_bounded_without_copying_action_args() -> None:
    action = _action("type_text")
    action["args"]["text"] = "credential-value-that-must-not-enter-history"
    action["local_intent"] = "记录进度 " + ("阶段" * 300)
    harness = _harness(action, _claim())

    recorded = harness.runtime.run_one_turn(turn_index=0)
    verified = harness.runtime.run_one_turn(turn_index=1)

    assert recorded.status is RuntimeStatus.ACTION_RECORDED
    assert verified.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.reducer.last_transition is not None
    assert harness.reducer.last_transition.action == "type_text"
    assert "credential-value" not in json.dumps(harness.reducer.to_dict())


def test_repeated_actions_are_executed_without_task_level_suppression() -> None:
    harness = _harness(
        _action("click"),
        _action("click"),
        _action("hotkey"),
        _action("hotkey"),
        _action("type_text"),
        _action("type_text"),
    )

    statuses = tuple(
        harness.runtime.run_one_turn(turn_index=index).status
        for index in range(6)
    )

    assert all(
        status is RuntimeStatus.ACTION_RECORDED
        for status in statuses
    )
    assert harness.controller.calls == 6


def test_full_desktop_snapshot_is_not_rejected_by_window_binding() -> None:
    harness = _harness(_claim())
    image_b64 = "ZnVsbC1kZXNrdG9wLWZyYW1l"
    digest = hashlib.sha256(image_b64.encode("ascii")).hexdigest()
    identity = RuntimeFrameIdentity(
        frame_id=2,
        epoch=4,
        hwnd=999,
        region=(-1200, 0, 3120, 1080),
        scope="full_desktop",
        submitted_size=(320, 240),
        image_sha256=digest,
    )
    frame = RuntimeFrame(
        frame_id=identity.frame_id,
        epoch=identity.epoch,
        hwnd=identity.hwnd,
        region=identity.region,
        scope=identity.scope,
        submitted_size=identity.submitted_size,
        image_b64=image_b64,
        image_sha256=digest,
    )
    target = RuntimeTargetIdentity(
        binding_id="foreground-anchor",
        hwnd=identity.hwnd,
        process_id=99,
        executable_identity="foreground-process",
        epoch=identity.epoch,
    )
    harness.state.snapshot = RuntimeSnapshot(
        frame=frame,
        target=target,
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.actor.calls == 1
    assert harness.controller.calls == 0






def test_unknown_controller_outcome_returns_to_actor_without_replay() -> None:
    harness = _harness(_action(), _blocked())
    harness.controller.mode = "unknown"

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(
        turn_index=1,
        rejection_feedback=first.detail,
    )

    assert first.status is RuntimeStatus.STALE_DISCARDED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    assert harness.state.observe_calls == 2
    assert harness.actor.rejection_feedbacks == [None, first.detail]
    assert "click" in first.detail
    assert "perform one click" in first.detail
    assert harness.reducer.status is MissionStatus.BLOCKED


def test_controller_exception_feeds_back_and_never_replays() -> None:
    harness = _harness(_action(), _action())
    harness.controller.mode = "raise"

    first = harness.runtime.run_one_turn(turn_index=0)
    harness.controller.mode = "completed"
    second = harness.runtime.run_one_turn(turn_index=1)

    # A controller exception is not a mission-blocking verdict: it is fed
    # back to the same Actor, the next turn keeps running, and the failed
    # action is never replayed.
    assert first.status is RuntimeStatus.STALE_DISCARDED
    assert "synthetic controller failure" in first.detail
    assert second.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 2
    assert harness.reducer.status is MissionStatus.RUNNING


def test_release_cleanup_unknown_is_blocked_and_never_replayed() -> None:
    harness = _harness(_action(), _action())
    harness.controller.mode = "unknown"
    harness.controller.rejection_error_kind = "release_cleanup_incomplete"

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.BLOCKED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    assert harness.reducer.status is MissionStatus.BLOCKED


def test_applied_input_without_post_frame_continues_with_fresh_observation() -> None:
    harness = _harness(_action(), _blocked())
    harness.controller.mode = "unknown_applied"

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    transition = harness.reducer.last_transition
    assert transition is not None
    assert transition.to_dict() == {
        "action_id": first.decision_id,
        "action": "click",
        "input_completed": True,
        "visible_effect": "",
        "frame_id": None,
        "error_kind": None,
    }
    previous = harness.actor.turns[1].previous_action
    assert previous is not None
    assert previous.action == "click"
    assert previous.input_completed is True
    assert previous.local_intent == "perform one click"


def test_rejected_controller_proves_no_input_and_is_not_replayed() -> None:
    harness = _harness(_action(), _blocked())
    harness.controller.mode = "rejected"

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.STALE_DISCARDED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    # The rejected action is recorded as a proven-not-executed transition so
    # the same Actor receives it as previous_action on the next turn instead
    # of silently retrying the same dead target.
    assert harness.reducer.status is MissionStatus.BLOCKED
    assert harness.reducer.last_transition is not None
    assert harness.reducer.last_transition.input_completed is False


def test_duplicate_post_action_state_probe_is_not_an_input_gate() -> None:
    harness = _harness(_action(), _action())
    harness.state.raise_current_on = 3

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 2
    assert harness.state.current_calls == 0
    assert harness.state.snapshot.frame.frame_id == 3


def test_claim_binds_current_frame_and_completes_deterministically() -> None:
    harness = _harness(_claim())

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.controller.calls == 0
    assert harness.reducer.status is MissionStatus.VERIFIED


def test_actor_completion_is_not_gated_by_auxiliary_history() -> None:
    harness = _harness(_claim())

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert result.detail == ""
    assert harness.runtime.verified_completion_result == (
        "the requested state is visibly present"
    )
    assert harness.runtime.latest_narration == (
        "the requested state is visibly present"
    )


def test_actor_completion_has_no_evidence_gate() -> None:
    harness = _harness(_claim())

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.reducer.status is MissionStatus.VERIFIED


def test_pre_turn_and_post_action_cancellation_are_explicit() -> None:
    before = _harness(_action())
    before_stop = threading.Event()
    before_stop.set()

    before_result = before.runtime.run_one_turn(
        turn_index=0,
        stop_event=before_stop,
    )

    assert before_result.status is RuntimeStatus.CANCELED
    assert before.actor.calls == 0
    assert before.controller.calls == 0
    assert before.reducer.status is MissionStatus.CANCELED

    admission = _harness(
        _action(),
    )
    admission_stop = threading.Event()
    admission.controller.stop_before_admission = admission_stop
    admission_result = admission.runtime.run_one_turn(
        turn_index=0,
        stop_event=admission_stop,
    )

    assert admission_result.status is RuntimeStatus.CANCELED
    assert admission.controller.calls == 1
    assert admission.reducer.status is MissionStatus.CANCELED
    assert admission.reducer.last_transition is not None

    after = _harness(_action())
    after_stop = threading.Event()
    after.controller.stop_after_action = after_stop
    after_result = after.runtime.run_one_turn(
        turn_index=0,
        stop_event=after_stop,
    )

    assert after_result.status is RuntimeStatus.CANCELED
    assert after.controller.calls == 1
    assert after.reducer.status is MissionStatus.CANCELED
    assert after.reducer.last_transition is not None


def test_actor_exception_after_stop_is_cancellation() -> None:
    actor_harness = _harness(_action())
    actor_stop = threading.Event()
    actor_harness.actor.on_propose = actor_stop.set
    actor_harness.actor.raise_error = RuntimeError("model call canceled")

    actor_result = actor_harness.runtime.run_one_turn(
        turn_index=0,
        stop_event=actor_stop,
    )

    assert actor_result.status is RuntimeStatus.CANCELED
    assert actor_harness.controller.calls == 0
    assert actor_harness.reducer.status is MissionStatus.CANCELED


def test_claim_completes_when_stop_fires_while_actor_decides() -> None:
    harness = _harness(_claim())
    stop = threading.Event()
    harness.actor.on_propose = stop.set

    result = harness.runtime.run_one_turn(turn_index=0, stop_event=stop)

    # The Actor's completion judgment has no physical action to guard: a
    # deadline or cancel that landed while the model was deciding must not
    # turn an already-made claim into a failure.
    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.reducer.status is MissionStatus.VERIFIED
    assert harness.runtime.verified_completion_result == (
        "the requested state is visibly present"
    )


def test_action_still_canceled_when_stop_fires_while_actor_decides() -> None:
    harness = _harness(_action())
    stop = threading.Event()
    harness.actor.on_propose = stop.set

    result = harness.runtime.run_one_turn(turn_index=0, stop_event=stop)

    # Physical actions keep their mechanical guard: no input may start once
    # the run is stopped.
    assert result.status is RuntimeStatus.CANCELED
    assert harness.controller.calls == 0
    assert harness.reducer.status is MissionStatus.CANCELED


def test_identical_fake_run_replays_deterministically() -> None:
    def run() -> tuple[Any, ...]:
        harness = _harness(_action(), _claim())
        first = harness.runtime.run_one_turn(turn_index=0)
        second = harness.runtime.run_one_turn(turn_index=1)
        return (
            first,
            second,
            harness.reducer.to_dict(),
            tuple(turn.to_dict() for turn in harness.actor.turns),
        )

    assert run() == run()


def test_minimal_runtime_rejects_scope_expansion_at_construction() -> None:
    with pytest.raises(
        MissionRuntimeConfigurationError,
        match="pre-bound target",
    ):
        _harness(_action(), bind_target=False)

    with pytest.raises(
        MissionRuntimeConfigurationError,
        match="exactly the minimal live-agent surface",
    ):
        _harness(_action(), catalog=_catalog(omit_last=True))


def test_actor_sees_exact_minimal_tool_surface_and_invalid_action_fails_closed() -> None:
    harness = _harness(_action("unsupported"))

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert harness.controller.calls == 0
    assert {
        item["name"] for item in harness.actor.turns[0].to_dict()["tools"]
    } == APPROVED_RUNTIME_TOOLS
