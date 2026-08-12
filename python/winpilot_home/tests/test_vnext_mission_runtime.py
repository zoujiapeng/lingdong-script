from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import threading
from typing import Any, Callable, Mapping

import pytest

from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT, ActorDecisionEnvelope
from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerRuntimeState,
    CapabilityBroker,
    ExecutionMedium,
    ExecutionRoute,
    MutationKind,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    EvidencePolicy,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.profiling import ExecutionPolicy
from winpilot.vnext.runtime import (
    APPROVED_RUNTIME_TOOLS,
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    ActorBudgetView,
    ActorTaskState,
    ActorTurnInput,
    ActorTaskStateUpdate,
    ActorTurnDecision,
    ControllerOutcome,
    ControllerTransition,
    MissionRuntime,
    MissionRuntimeConfigurationError,
    RuntimeContractError,
    RuntimeFrame,
    RuntimeFrameIdentity,
    RuntimeSnapshot,
    RuntimeStatus,
    RuntimeTargetIdentity,
)


MISSION_ID = "mission-runtime"
RUN_ID = "run-runtime"
OBLIGATION_ID = "full-goal"
GOAL = "Make the requested visible state exist"
HWND = 9123
PROCESS_ID = 44
EPOCH = 7
GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "pointer_released",
    "runtime_healthy",
)
DRAG_GATES = (
    "target_bound",
    "authoritative_frame",
    "runtime_healthy",
)


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


def _tool(
    name: str,
    *,
    mutation: MutationKind,
    schema: dict[str, Any],
    order: int,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        schema=schema,
        route=ToolRoute.VISUAL_ACTOR,
        mutation_kind=mutation,
        execution_medium=(
            ExecutionMedium.GUI
            if mutation is MutationKind.GUI
            else ExecutionMedium.CONTROL
        ),
        required_any_capabilities=(
            ("visual.interact",)
            if mutation is MutationKind.GUI
            else ()
        ),
        prerequisites=(
            DRAG_GATES
            if name == "drag"
            else GUI_GATES
            if mutation is MutationKind.GUI
            else ()
        ),
        order=order,
    )


def _catalog(*, include_extra: bool = False) -> ToolCatalog:
    schemas = {
        "click": _point_schema(),
        "double_click": _point_schema(),
        "drag": _drag_schema(),
        "scroll": _scroll_schema(),
        "type_text": _text_schema(),
        "hotkey": _hotkey_schema(),
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
            mutation=MutationKind.GUI,
            schema=schemas[name],
            order=order,
        )
        for order, name in enumerate(
            (
                "click",
                "double_click",
                "drag",
                "scroll",
                "type_text",
                "hotkey",
                "launch_application",
            ),
            start=1,
        )
    ]
    tools.extend(
        (
            _tool(
                "claim_obligation_done",
                mutation=MutationKind.NONE,
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
                order=20,
            ),
            _tool(
                "blocked",
                mutation=MutationKind.NONE,
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
                order=21,
            ),
        )
    )
    if include_extra:
        tools.append(
            _tool(
                "observe",
                mutation=MutationKind.NONE,
                schema=_empty_schema(),
                order=22,
            )
        )
    return ToolCatalog(tuple(tools))


def _action(
    action: str = "click",
    *,
    keep_holding: bool | None = None,
) -> dict[str, Any]:
    args: dict[str, Any]
    if action in {"click", "double_click"}:
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
    remaining_items: list[str] | None = None,
) -> ActorTurnDecision:
    update: dict[str, Any] = {"scratchpad": scratchpad}
    if remaining_items is not None:
        update["remaining_items"] = remaining_items
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

    def _for_turn(self, turn_index: int) -> RuntimeSnapshot:
        snapshot = RuntimeSnapshot(
            broker_state=replace(
                self.snapshot.broker_state,
                turn_index=turn_index,
            ),
            frame=self.snapshot.frame,
            target=self.snapshot.target,
        )
        self.snapshot = snapshot
        return snapshot

    def observe(
        self,
        projection: Any,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot:
        del projection
        self.observe_calls += 1
        return self._for_turn(turn_index)

    def current(
        self,
        projection: Any,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot:
        del projection
        self.current_calls += 1
        if self.current_calls == self.raise_current_on:
            raise RuntimeError("synthetic current-state failure")
        return self._for_turn(turn_index)

    def install_frame(
        self,
        frame: RuntimeFrame,
        *,
        pointer: PointerLeaseState | None = None,
    ) -> None:
        self.snapshot = RuntimeSnapshot(
            broker_state=replace(
                self.snapshot.broker_state,
                frame=frame.broker_frame(),
                pointer=(
                    self.snapshot.broker_state.pointer
                    if pointer is None
                    else pointer
                ),
            ),
            frame=frame,
            target=self.snapshot.target,
        )


class FakeController:
    def __init__(
        self,
        *,
        state: FakeState,
        make_post_frame: Callable[
            [ActorDecisionEnvelope],
            tuple[RuntimeFrame, EvidenceRef],
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
            )
        if self.mode == "unknown_applied":
            return ControllerTransition(
                outcome=ControllerOutcome.UNKNOWN,
                action_id=envelope.decision_id,
                action=action,
                input_completed=True,
            )
        frame, post_evidence = self.make_post_frame(envelope)
        pointer_after = PointerLeaseState(PointerDisposition.RELEASED)
        if action == "drag" and args.get("keep_holding", False):
            before_pointer = self.state.snapshot.broker_state.pointer
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
            post_frame_evidence=(post_evidence,),
            post_frame_id=frame.frame_id,
        )


@dataclass
class Harness:
    reducer: MissionReducer
    registry: EvidenceRegistry
    actor: FakeActor
    state: FakeState
    controller: FakeController
    runtime: MissionRuntime
    catalog: ToolCatalog

    def make_frame(
        self,
        frame_id: int,
        *,
        kind: str,
        action_id: str | None = None,
    ) -> tuple[RuntimeFrame, EvidenceRef]:
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
        suffix = "" if action_id is None else f"-{action_id}"
        ref = EvidenceRef(
            id=f"e-frame-{frame_id}{suffix}",
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            obligation_id=OBLIGATION_ID,
            kind=kind,
            trust="deterministic",
            payload_hash=identity.payload_hash,
            action_id=action_id,
            frame_id=frame_id,
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
            evidence_refs=(ref.id,),
        ), ref


def _definition(
    *,
    obligations: tuple[ObligationSpec, ...] | None = None,
) -> MissionDefinition:
    return MissionDefinition(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        original_goal=GOAL,
        obligations=obligations
        or (
            ObligationSpec(
                id=OBLIGATION_ID,
                desired_state=GOAL,
                acceptance_criteria=("the visible result is confirmed",),
                required_capabilities=("visual.interact",),
                evidence_policy=EvidencePolicy(),
            ),
        ),
    )


def _harness(
    *proposals: ActorTurnDecision | Mapping[str, Any],
    definition: MissionDefinition | None = None,
    catalog: ToolCatalog | None = None,
    bind_target: bool = True,
    profile_provider: bool = False,
    budget_provider: Callable[[int], Mapping[str, Any]] | None = None,
) -> Harness:
    definition = definition or _definition()
    registry = EvidenceRegistry(definition.mission_id, definition.run_id)
    reducer = MissionReducer(definition, evidence=registry)
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
    current_ref = EvidenceRef(
        id="e-frame-1",
        mission_id=definition.mission_id,
        run_id=definition.run_id,
        obligation_id=definition.obligations[0].id,
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=frame_identity.payload_hash,
        frame_id=1,
    )
    target_ref = EvidenceRef(
        id="e-target",
        mission_id=definition.mission_id,
        run_id=definition.run_id,
        obligation_id=definition.obligations[0].id,
        kind="target",
        trust="deterministic",
        payload_hash="target-evidence-payload",
        frame_id=1,
    )
    registry.append_many((current_ref, target_ref))
    binding = TargetBinding(
        binding_id="binding-current",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="opaque-process",
        title_snapshot="",
        established_by_evidence_ref=target_ref.id,
        epoch=EPOCH,
    )
    if bind_target:
        reducer.apply(
            reducer.event(
                "target_bound",
                payload={"target_binding": binding.to_dict()},
                evidence_refs=(current_ref.id, target_ref.id),
            )
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
        evidence_refs=(current_ref.id,),
    )
    snapshot = RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=0,
            available_capabilities=("visual.interact",),
            target=TargetState(
                binding.binding_id,
                binding.hwnd,
                binding.epoch,
                True,
            ),
            frame=frame.broker_frame(),
            pointer=PointerLeaseState(PointerDisposition.RELEASED),
            runtime_healthy=True,
        ),
        frame=frame,
        target=RuntimeTargetIdentity.from_binding(binding),
    )
    state = FakeState(snapshot)
    holder: dict[str, Harness] = {}

    def make_post_frame(
        envelope: ActorDecisionEnvelope,
    ) -> tuple[RuntimeFrame, EvidenceRef]:
        return holder["harness"].make_frame(
            state.snapshot.frame.frame_id + 1,
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            action_id=envelope.decision_id,
        )

    controller = FakeController(
        state=state,
        make_post_frame=make_post_frame,
    )
    profile = ExecutionPolicy(
        provider_availability=(
            ("obligation_verifier",) if profile_provider else ()
        ),
    ).safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=("visual.interact",),
    )
    broker = CapabilityBroker(catalog)
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=broker,
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        evidence_registry=registry,
        budget_facts_provider=budget_provider,
    )
    harness = Harness(
        reducer=reducer,
        registry=registry,
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
    ("click", "double_click", "drag", "scroll", "type_text", "hotkey"),
)
def test_minimal_runtime_executes_each_approved_action_once(action: str) -> None:
    harness = _harness(_action(action))

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.controller.calls == 1
    assert [event.kind for event in harness.reducer.events] == [
        "target_bound",
        "transition_recorded",
    ]
    assert harness.reducer.card.last_transition.action == action


def test_cross_frame_held_drag_preserves_then_releases_one_lease() -> None:
    harness = _harness(
        _action("drag", keep_holding=True),
        _action("drag", keep_holding=True),
        _action("drag", keep_holding=False),
    )

    first = harness.runtime.run_one_turn(turn_index=0)
    first_pointer = harness.state.snapshot.broker_state.pointer
    second = harness.runtime.run_one_turn(turn_index=1)
    second_pointer = harness.state.snapshot.broker_state.pointer
    third = harness.runtime.run_one_turn(turn_index=2)
    third_pointer = harness.state.snapshot.broker_state.pointer

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
    # While the held pointer lease is owned, the model may continue the drag
    # or finish with a non-pointer action (claim/blocked); pointer gestures
    # that would conflict with the held press stay excluded.
    held_tool_names = tuple(
        tool.name for tool in harness.actor.turns[1].tools
    )
    assert "drag" in held_tool_names
    assert "claim_obligation_done" in held_tool_names
    assert "blocked" in held_tool_names
    assert "click" not in held_tool_names
    assert "double_click" not in held_tool_names
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


def test_keep_holding_blocks_when_controller_returns_released_pointer() -> None:
    harness = _harness(_action("drag", keep_holding=True))
    harness.controller.pointer_after_override = PointerLeaseState(
        PointerDisposition.RELEASED
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.BLOCKED
    assert "could not be reconciled" in result.detail
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
    assert [event.kind for event in harness.reducer.events] == [
        "target_bound",
        "obligation_claimed",
        "verification_recorded",
        "mission_verified",
    ]
    claim, verification = harness.reducer.events[1:3]
    assert claim.evidence_refs == ("e-frame-1",)
    assert verification.evidence_refs == ("e-frame-1",)
    assert verification.payload["verifier_kind"] == "actor_claim"
    assert "the requested state is visibly present" not in json.dumps(
        [event.to_dict() for event in harness.reducer.events]
    )


def test_task_state_remaining_items_merge_replace_and_clear() -> None:
    state = ActorTaskState.initial().apply(
        ActorTaskStateUpdate.from_dict(
            {
                "scratchpad": "Start the first phase",
                "remaining_items": ["send the image", "finish the game"],
            }
        )
    )

    revised = state.apply(
        ActorTaskStateUpdate.from_dict(
            {"scratchpad": "The first phase is done"}
        )
    )
    cleared = revised.apply(
        ActorTaskStateUpdate.from_dict(
            {"scratchpad": None, "remaining_items": []}
        )
    )

    assert revised.scratchpad == "The first phase is done"
    assert revised.remaining_items == ("send the image", "finish the game")
    assert cleared.remaining_items == ()


def test_claim_with_actor_declared_remaining_work_soft_rejects_then_recovers() -> None:
    harness = _harness(
        _task_decision(
            _action("click"),
            scratchpad="Music is playing",
            remaining_items=["send playback image", "build and play game"],
        ),
        _task_decision(_claim()),
        _task_decision(
            _claim(),
            scratchpad="Everything requested is complete",
            remaining_items=[],
        ),
    )

    acted = harness.runtime.run_one_turn(turn_index=0)
    premature = harness.runtime.run_one_turn(turn_index=1)

    assert acted.status is RuntimeStatus.ACTION_RECORDED
    assert premature.status is RuntimeStatus.INVALID_PROPOSAL
    assert "unfinished items remain" in premature.detail
    assert harness.runtime.task_state.remaining_items == (
        "send playback image",
        "build and play game",
    )
    assert harness.reducer.projection.status is MissionStatus.RUNNING
    assert all(
        event.kind not in {
            "obligation_claimed",
            "verification_recorded",
            "mission_verified",
        }
        for event in harness.reducer.events
    )

    completed = harness.runtime.run_one_turn(turn_index=2)

    assert completed.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.runtime.task_state.remaining_items == ()


def test_claim_fails_closed_without_fresh_observation() -> None:
    harness = _harness(_claim())
    stale_frame = replace(
        harness.state.snapshot.broker_state.frame,
        fresh=False,
    )
    harness.state.snapshot = RuntimeSnapshot(
        broker_state=replace(
            harness.state.snapshot.broker_state,
            frame=stale_frame,
        ),
        frame=harness.state.snapshot.frame,
        target=harness.state.snapshot.target,
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert result.route is ExecutionRoute.BLOCKED
    assert harness.actor.calls == 0
    assert harness.controller.calls == 0
    assert harness.reducer.projection.status is MissionStatus.RUNNING


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
    reducer_wire = {
        "events": [
            event.to_dict()
            for event in harness.reducer.events
        ],
        "projection": harness.reducer.projection.to_dict(),
        "evidence": [
            reference.to_dict()
            for reference in harness.registry.snapshot()
        ],
    }
    assert sentinel not in json.dumps(
        reducer_wire,
        ensure_ascii=False,
        sort_keys=True,
    )


def test_ephemeral_task_state_crosses_turns_but_not_durable_evidence() -> None:
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
    durable_wire = {
        "events": [event.to_dict() for event in harness.reducer.events],
        "projection": harness.reducer.projection.to_dict(),
        "evidence": [
            reference.to_dict() for reference in harness.registry.snapshot()
        ],
    }
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
    stale = _harness(_task_decision(_action(), **update))
    stale.actor.on_propose = lambda: stale.reducer.apply(
        stale.reducer.event(
            "mission_blocked",
            payload={"reason": "synthetic concurrent change"},
        )
    )
    initial = stale.runtime.task_state

    stale_result = stale.runtime.run_one_turn(turn_index=0)

    assert stale_result.status is RuntimeStatus.STALE_DISCARDED
    assert stale.runtime.task_state == initial

    invalid = _harness(_task_decision(_action("observe"), **update))
    invalid_initial = invalid.runtime.task_state

    invalid_result = invalid.runtime.run_one_turn(turn_index=0)

    assert invalid_result.status is RuntimeStatus.INVALID_PROPOSAL
    assert invalid.runtime.task_state == invalid_initial


def test_schema_rejected_action_keeps_task_state_for_actor_repair() -> None:
    scratchpad = "route right, down, then right"
    invalid_action = _action("click")
    invalid_action["args"]["image_x"] = -1
    harness = _harness(
        _task_decision(invalid_action, scratchpad=scratchpad),
        _blocked(),
    )

    rejected = harness.runtime.run_one_turn(turn_index=0)
    next_turn = harness.runtime.run_one_turn(turn_index=1)

    assert rejected.status is RuntimeStatus.INVALID_PROPOSAL
    assert "expected" not in rejected.detail
    assert next_turn.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 0
    assert harness.runtime.task_state.scratchpad == scratchpad
    assert harness.actor.turns[1].task_state.scratchpad == scratchpad


def test_out_of_frame_point_soft_rejects_for_actor_repair() -> None:
    invalid_action = _action("click")
    invalid_action["args"]["image_x"] = 320
    harness = _harness(invalid_action, _blocked())

    rejected = harness.runtime.run_one_turn(turn_index=0)
    next_turn = harness.runtime.run_one_turn(turn_index=1)

    assert rejected.status is RuntimeStatus.INVALID_PROPOSAL
    assert rejected.detail == (
        "image_x must stay inside the exact submitted image"
    )
    assert next_turn.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 0


def test_pre_action_current_state_failure_is_discarded_before_input() -> None:
    harness = _harness(_action())
    harness.state.raise_current_on = 1

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert harness.controller.calls == 0


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
    assert previous.semantic_summary == "click"
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


def test_actor_receives_only_completed_action_order() -> None:
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
    assert harness.actor.turns[0].completed_action_summaries == ()
    assert harness.actor.turns[1].completed_action_summaries == ("click",)
    assert harness.actor.turns[2].completed_action_summaries == (
        "click",
        "double_click",
    )
    history_wire = json.dumps(
        harness.actor.turns[2].completed_action_summaries
    )
    assert "image_x" not in history_wire
    assert "text" not in history_wire
    assert "local_intent" not in history_wire


def test_completed_action_summaries_keep_safe_direction_and_chord() -> None:
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
    expected = (
        "scroll",
        "scroll",
        "hotkey",
        "type_text",
    )
    assert (
        harness.actor.turns[1].previous_action.semantic_summary
        == "scroll"
    )
    assert (
        harness.actor.turns[2].previous_action.semantic_summary
        == "scroll"
    )
    assert (
        harness.actor.turns[3].previous_action.semantic_summary
        == "hotkey"
    )
    assert harness.actor.turns[4].completed_action_summaries == expected
    transition_summaries = tuple(
        event.payload["action_summary"]
        for event in harness.reducer.events
        if event.kind == "transition_recorded"
    )
    assert transition_summaries == expected
    history_wire = json.dumps(transition_summaries)
    assert "image_x" not in history_wire
    assert "image_y" not in history_wire
    assert "hello" not in history_wire
    assert "local_intent" not in history_wire


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


def test_actor_result_is_discarded_when_mission_revision_changes() -> None:
    harness = _harness(_action())
    harness.actor.on_propose = lambda: harness.reducer.apply(
        harness.reducer.event(
            "mission_blocked",
            payload={"reason": "synthetic concurrent change"},
        )
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert harness.controller.calls == 0


def test_untrusted_pre_frame_is_rejected_before_actor_or_controller() -> None:
    harness = _harness(_action())
    bad_frame = replace(
        harness.state.snapshot.frame,
        evidence_refs=("e-target",),
    )
    harness.state.snapshot = RuntimeSnapshot(
        broker_state=harness.state.snapshot.broker_state,
        frame=bad_frame,
        target=harness.state.snapshot.target,
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert harness.actor.calls == 0
    assert harness.controller.calls == 0


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
    evidence = EvidenceRef(
        id="e-full-desktop-frame",
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        obligation_id=OBLIGATION_ID,
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=identity.payload_hash,
        frame_id=identity.frame_id,
    )
    harness.registry.append(evidence)
    frame = RuntimeFrame(
        frame_id=identity.frame_id,
        epoch=identity.epoch,
        hwnd=identity.hwnd,
        region=identity.region,
        scope=identity.scope,
        submitted_size=identity.submitted_size,
        image_b64=image_b64,
        image_sha256=digest,
        evidence_refs=(evidence.id,),
    )
    target = RuntimeTargetIdentity(
        binding_id="foreground-anchor",
        hwnd=identity.hwnd,
        process_id=99,
        executable_identity="foreground-process",
        epoch=identity.epoch,
    )
    harness.state.snapshot = RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=0,
            available_capabilities=("visual.interact",),
            target=TargetState(
                target.binding_id,
                target.hwnd,
                target.epoch,
                True,
            ),
            frame=frame.broker_frame(),
            pointer=PointerLeaseState(PointerDisposition.RELEASED),
            runtime_healthy=True,
        ),
        frame=frame,
        target=target,
    )

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.actor.calls == 1
    assert harness.controller.calls == 0






@pytest.mark.parametrize("mode", ("unknown", "raise"))
def test_unknown_controller_outcome_is_blocked_and_never_replayed(
    mode: str,
) -> None:
    harness = _harness(_action(), _action())
    harness.controller.mode = mode

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.BLOCKED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    assert harness.reducer.projection.status is MissionStatus.BLOCKED
    assert harness.reducer.events[-1].kind == "obligation_blocked"
    assert "not replayed" in harness.reducer.events[-1].payload["reason"]


def test_applied_input_without_post_frame_continues_with_fresh_observation() -> None:
    harness = _harness(_action(), _blocked())
    harness.controller.mode = "unknown_applied"

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    transition = next(
        event
        for event in harness.reducer.events
        if event.kind == "transition_recorded"
    )
    assert transition.payload == {
        "obligation_id": OBLIGATION_ID,
        "action_id": first.decision_id,
        "action": "click",
        "action_summary": "click",
        "input_completed": True,
    }
    assert transition.evidence_refs == ()
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
    assert [event.kind for event in harness.reducer.events] == [
        "target_bound",
        "transition_recorded",
        "obligation_blocked",
    ]


def test_post_action_state_failure_is_blocked_after_one_input_attempt() -> None:
    harness = _harness(_action(), _action())
    harness.state.raise_current_on = 3

    first = harness.runtime.run_one_turn(turn_index=0)
    second = harness.runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.BLOCKED
    assert second.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == 1
    assert any(
        ref.kind == POST_ACTION_FRAME_EVIDENCE_KIND
        for ref in harness.registry.snapshot()
    )


def test_claim_binds_current_frame_and_completes_deterministically() -> None:
    harness = _harness(_claim())

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.controller.calls == 0
    assert [event.kind for event in harness.reducer.events] == [
        "target_bound",
        "obligation_claimed",
        "verification_recorded",
        "mission_verified",
    ]
    claim, verification, completion = harness.reducer.events[1:]
    assert claim.evidence_refs == ("e-frame-1",)
    assert verification.evidence_refs == ("e-frame-1",)
    assert verification.payload["verified"] is True
    assert verification.payload["verifier_kind"] == "actor_claim"
    assert completion.evidence_refs[0] == "e-frame-1"
    aggregate = next(
        ref
        for ref in harness.registry.snapshot()
        if ref.id == completion.evidence_refs[-1]
    )
    assert aggregate.trust == "deterministic"
    assert aggregate.obligation_id is None
    obligation = harness.reducer.projection.obligation_state(OBLIGATION_ID)
    assert obligation.verified_evidence_refs == ("e-frame-1",)
    assert harness.reducer.projection.status is MissionStatus.VERIFIED


def test_claim_preserves_required_current_frame_evidence_kind() -> None:
    definition = MissionDefinition(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        original_goal=GOAL,
        obligations=(
            ObligationSpec(
                id=OBLIGATION_ID,
                desired_state=GOAL,
                acceptance_criteria=("the visible result is confirmed",),
                required_capabilities=("visual.interact",),
                evidence_policy=EvidencePolicy(
                    required_evidence_kinds=(CURRENT_FRAME_EVIDENCE_KIND,),
                ),
            ),
        ),
    )
    harness = _harness(_claim(), definition=definition)

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    verification = harness.reducer.events[-2]
    assert verification.kind == "verification_recorded"
    assert "e-frame-1" in verification.evidence_refs
    assert verification.payload["verifier_kind"] == "actor_claim"


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
    assert before.reducer.events[-1].kind == "mission_canceled"

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
    assert admission.reducer.events[-1].kind == "mission_canceled"
    assert [event.kind for event in admission.reducer.events] == [
        "target_bound",
        "transition_recorded",
        "mission_canceled",
    ]

    after = _harness(_action())
    after_stop = threading.Event()
    after.controller.stop_after_action = after_stop
    after_result = after.runtime.run_one_turn(
        turn_index=0,
        stop_event=after_stop,
    )

    assert after_result.status is RuntimeStatus.CANCELED
    assert after.controller.calls == 1
    assert [event.kind for event in after.reducer.events] == [
        "target_bound",
        "transition_recorded",
        "mission_canceled",
    ]


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
    assert actor_harness.reducer.events[-1].kind == "mission_canceled"


def test_identical_fake_run_replays_deterministically() -> None:
    def run() -> tuple[Any, ...]:
        harness = _harness(_action(), _claim())
        first = harness.runtime.run_one_turn(turn_index=0)
        second = harness.runtime.run_one_turn(turn_index=1)
        return (
            first,
            second,
            tuple(event.to_dict() for event in harness.reducer.events),
            tuple(ref.to_dict() for ref in harness.registry.snapshot()),
            harness.reducer.projection.to_dict(),
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
        match="one full-goal obligation",
    ):
        _harness(
            _action(),
            definition=_definition(
                obligations=(
                    ObligationSpec(
                        id="first",
                        desired_state=GOAL,
                        required_capabilities=("visual.interact",),
                        evidence_policy=EvidencePolicy(),
                    ),
                    ObligationSpec(id="second", desired_state="second"),
                )
            ),
        )

    with pytest.raises(
        MissionRuntimeConfigurationError,
        match="exactly the minimal live-agent surface",
    ):
        _harness(_action(), catalog=_catalog(include_extra=True))

    with pytest.raises(
        MissionRuntimeConfigurationError,
        match="fixed safe STANDARD profile",
    ):
        _harness(_action(), profile_provider=True)


def test_actor_sees_exact_minimal_tool_surface_and_invalid_action_fails_closed() -> None:
    harness = _harness(_action("unsupported"))

    result = harness.runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert harness.controller.calls == 0
    assert {
        item["name"] for item in harness.actor.turns[0].to_dict()["tools"]
    } == APPROVED_RUNTIME_TOOLS
