"""P4a MissionRuntime ownership, binding, and replay tests.

These tests exercise only injected ports.  They intentionally do not import
the legacy AgentRunner, ToolSession, frame capture, or desktop executor.
"""
from __future__ import annotations

import base64
import hashlib
import random
from dataclasses import dataclass, replace
from threading import Event
from typing import Any, Callable, Mapping

import pytest

from winpilot.vnext.actor import ActorDecisionEnvelope
from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerProjection,
    BrokerRuntimeState,
    CapabilityBroker,
    CapabilityGrant,
    ExecutionRoute,
    ExecutorOffer,
    MutationKind,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
    ToolCatalog,
    ToolPolicy,
    ToolRoute,
    build_tool_catalog,
)
from winpilot.vnext.mission import (
    EvidenceRef,
    MissionCard,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    ObligationStatus,
    TargetBinding,
)
from winpilot.vnext.profiling import DEFAULT_CAPABILITY_CATALOG, ExecutionPolicy
from winpilot.vnext.runtime import (
    ControllerOutcome,
    ControllerTransition,
    MissionRuntime,
    RuntimeContractError,
    RuntimeFrame,
    RuntimeSnapshot,
    RuntimeStatus,
)
from winpilot.visual_runtime.commit_admission import TargetCommitIdentity


ALL_CAPABILITIES = tuple(sorted(DEFAULT_CAPABILITY_CATALOG))


class EvidenceRegistry:
    def __init__(self) -> None:
        self.refs: dict[str, EvidenceRef] = {}

    def add(self, ref: EvidenceRef) -> None:
        assert ref.id not in self.refs
        self.refs[ref.id] = ref

    def snapshot(self) -> tuple[EvidenceRef, ...]:
        return tuple(self.refs.values())


class ScriptedActor:
    def __init__(
        self,
        proposal: Mapping[str, Any],
        *,
        after_propose: Callable[[], None] | None = None,
    ) -> None:
        self.proposal = dict(proposal)
        self.after_propose = after_propose
        self.calls: list[tuple[Any, str, tuple[int, int], Event | None]] = []

    def propose(
        self,
        turn: Any,
        *,
        image_b64: str,
        submitted_size: tuple[int, int],
        stop_event: Event | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append((turn, image_b64, submitted_size, stop_event))
        if self.after_propose is not None:
            self.after_propose()
        return dict(self.proposal)


class ScriptedState:
    def __init__(
        self,
        initial: RuntimeSnapshot,
        *,
        current: RuntimeSnapshot | None = None,
        post_action: RuntimeSnapshot | None = None,
    ) -> None:
        self.initial = initial
        self.current_snapshot = current or initial
        self.post_action = post_action or initial
        self.action_executed = False
        self.observe_calls: list[tuple[int, Any]] = []
        self.current_calls: list[int] = []

    def observe(
        self,
        projection: Any,
        *,
        turn_index: int,
        image_region: Any = None,
    ) -> RuntimeSnapshot:
        self.observe_calls.append((turn_index, image_region))
        return self.post_action if self.action_executed else self.initial

    def current(self, projection: Any, *, turn_index: int) -> RuntimeSnapshot:
        self.current_calls.append(turn_index)
        return self.post_action if self.action_executed else self.current_snapshot


class SpyController:
    def __init__(
        self,
        state: ScriptedState,
        registry: EvidenceRegistry,
        *,
        outcome: ControllerOutcome = ControllerOutcome.COMPLETED,
        raises: Exception | None = None,
    ) -> None:
        self.state = state
        self.registry = registry
        self.outcome = outcome
        self.raises = raises
        self.calls: list[ActorDecisionEnvelope] = []
        self.transition_mutator: (
            Callable[[ActorDecisionEnvelope, ControllerTransition], ControllerTransition] | None
        ) = None

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: Event | None = None,
    ) -> ControllerTransition:
        assert isinstance(envelope, ActorDecisionEnvelope)
        self.calls.append(envelope)
        self.state.action_executed = True
        if self.raises is not None:
            raise self.raises
        evidence_refs: tuple[str, ...] = ()
        if self.outcome is ControllerOutcome.COMPLETED:
            evidence_id = f"evidence-{envelope.decision_id}"
            self.registry.add(EvidenceRef(
                id=evidence_id,
                mission_id=envelope.mission_id,
                run_id=envelope.run_id,
                obligation_id=envelope.obligation_id,
                kind="action_effect",
                trust="deterministic",
                payload_hash=f"payload-{envelope.decision_id}",
                frame_id=self.state.post_action.frame.frame_id,
                action_id=envelope.decision_id,
            ))
            evidence_refs = (evidence_id,)
        proposal = envelope.proposal
        transition = ControllerTransition(
            outcome=self.outcome,
            action_id=envelope.decision_id,
            action=proposal.action,
            input_completed=(
                True if self.outcome is ControllerOutcome.COMPLETED
                else False if self.outcome is ControllerOutcome.REJECTED
                else None
            ),
            visible_effect=(
                "the requested local control visibly changed"
                if self.outcome is ControllerOutcome.COMPLETED else ""
            ),
            evidence_refs=evidence_refs,
            post_frame_id=self.state.post_action.frame.frame_id,
        )
        return (
            self.transition_mutator(envelope, transition)
            if self.transition_mutator is not None else transition
        )


class SpyCleanup:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def release_pointer(
        self,
        lease_id: str,
        *,
        reason: str = "runtime_canceled",
    ) -> bool:
        self.calls.append((lease_id, reason))
        return True


class RacingReducer(MissionReducer):
    """Expose a post-Actor projection race without accepting an event."""

    def __init__(self, definition: MissionDefinition, *, evidence: Any, race: str) -> None:
        super().__init__(definition, evidence=evidence)
        self.race = race
        self.raced = False

    @property
    def projection(self):
        projection = super().projection
        if not self.raced:
            return projection
        if self.race == "revision":
            return replace(projection, revision=projection.revision + 1)
        if self.race == "obligation":
            states = tuple(
                replace(item, status=(
                    ObligationStatus.ACTIVE if item.id == "o2" else ObligationStatus.PENDING
                ))
                for item in projection.obligation_states
            )
            return replace(
                projection,
                active_obligation_id="o2",
                obligation_states=states,
            )
        raise AssertionError(self.race)

    @property
    def card(self) -> MissionCard:
        return MissionCard.from_projection(self.projection)


class RacingBroker(CapabilityBroker):
    def __init__(self, catalog: ToolCatalog, race: str) -> None:
        super().__init__(catalog)
        self.race = race
        self.calls = 0

    def project(self, *args: Any, **kwargs: Any) -> BrokerProjection:
        projection = super().project(*args, **kwargs)
        self.calls += 1
        if self.calls < 2:
            return projection
        if self.race == "grant":
            return replace(projection, active_grants=())
        if self.race == "broker":
            return replace(projection, projection_id=f"{projection.projection_id}-changed")
        raise AssertionError(self.race)


@dataclass
class Harness:
    reducer: MissionReducer
    registry: EvidenceRegistry
    actor: ScriptedActor
    state: ScriptedState
    controller: SpyController
    runtime: MissionRuntime
    profile: Any
    catalog: ToolCatalog


def _schema(name: str, properties: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": dict(properties or {}),
            },
        },
    }


def _catalog() -> ToolCatalog:
    policies = (
        ToolPolicy("observe", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=1),
        ToolPolicy(
            "click", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
            ("visual.interact",),
            ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"),
            order=2,
        ),
        ToolPolicy("wait", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=3),
        ToolPolicy("request_capability", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=4),
        ToolPolicy("claim_obligation_done", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=5),
        ToolPolicy("blocked", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=6),
        ToolPolicy(
            "send_text", ToolRoute.PROVIDER, MutationKind.EXTERNAL,
            ("communication.send_text",),
            ("recipient_bound", "adapter_live", "receipt_capable", "runtime_healthy"),
            "external.send", 7,
        ),
    )
    schemas = tuple(
        _schema(
            item.name,
            {"x": {"type": "number"}, "y": {"type": "number"}}
            if item.name == "click" else {},
        )
        for item in policies
    )
    return build_tool_catalog(schemas, policies)


def _definition(
    capability: str = "visual.interact",
    *,
    two_obligations: bool = False,
) -> MissionDefinition:
    obligations = [ObligationSpec(
        id="o1",
        desired_state="the requested unknown GUI state exists",
        acceptance_criteria=("the requested state is visibly confirmed",),
        required_capabilities=(capability,),
    )]
    if two_obligations:
        obligations.append(ObligationSpec(
            id="o2",
            desired_state="a second independent state exists",
            acceptance_criteria=("the second state is confirmed",),
            required_capabilities=(capability,),
        ))
    return MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="Complete the current unknown GUI task",
        obligations=tuple(obligations),
    )


def _bind_target(reducer: MissionReducer, registry: EvidenceRegistry) -> None:
    registry.add(EvidenceRef(
        id="target-proof",
        mission_id="mission-1",
        run_id="run-1",
        kind="target",
        trust="deterministic",
        payload_hash="target-proof-payload",
    ))
    reducer.apply(reducer.event(
        "target_bound",
        payload=TargetBinding(
            binding_id="target-1",
            hwnd=101,
            process_id=7,
            executable_identity="opaque-process",
            title_snapshot="Current application",
            established_by_evidence_ref="target-proof",
            epoch=3,
        ).to_dict(),
        evidence_refs=("target-proof",),
    ))


def _frame(
    frame_id: int,
    *,
    hwnd: int = 101,
    epoch: int = 3,
    scope: str = "target_window",
    salt: str = "",
) -> RuntimeFrame:
    raw = f"frame:{frame_id}:{hwnd}:{epoch}:{salt}".encode()
    image_b64 = base64.b64encode(raw).decode("ascii")
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=epoch,
        hwnd=hwnd,
        region=(0, 0, 1280, 720),
        scope=scope,
        submitted_size=(1280, 720),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        evidence_refs=(f"frame-proof-{frame_id}-{salt or 'base'}",),
    )


def _snapshot(
    frame: RuntimeFrame,
    *,
    turn_index: int = 4,
    pointer: PointerLeaseState | None = None,
    target: TargetState | None = None,
    runtime_target: TargetCommitIdentity | None = None,
    paused: bool = False,
    canceled: bool = False,
    offers: tuple[ExecutorOffer, ...] = (),
    authorizations: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    target_state = target or TargetState("target-1", frame.hwnd, frame.epoch, True)
    target_identity = runtime_target or TargetCommitIdentity(
        binding_id=str(target_state.binding_id),
        hwnd=int(target_state.hwnd),
        root_hwnd=int(target_state.hwnd),
        process_id=7,
        epoch=int(target_state.epoch),
        window_region=(0, 0, 1280, 720),
    )
    return RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=turn_index,
            available_capabilities=ALL_CAPABILITIES,
            target=target_state,
            frame=AuthoritativeFrameState(
                frame.frame_id, frame.hwnd, frame.epoch, True, True,
            ),
            pointer=pointer or PointerLeaseState(PointerDisposition.RELEASED),
            executor_offers=offers,
            authorization_scopes=authorizations,
            paused=paused,
            canceled=canceled,
        ),
        frame=frame,
        target=target_identity,
    )


def _add_frame_evidence(registry: EvidenceRegistry, frame: RuntimeFrame) -> None:
    for evidence_id in frame.evidence_refs:
        registry.add(EvidenceRef(
            id=evidence_id,
            mission_id="mission-1",
            run_id="run-1",
            obligation_id="o1",
            kind="frame",
            trust="deterministic",
            payload_hash=f"payload-{evidence_id}",
            frame_id=frame.frame_id,
        ))


def _click(x: int = 320, y: int = 240) -> dict[str, Any]:
    return {
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "action": "click",
        "args": {"x": x, "y": y},
        "local_intent": "activate the visible semantic target",
    }


def _harness(
    proposal: Mapping[str, Any],
    *,
    capability: str = "visual.interact",
    definition: MissionDefinition | None = None,
    reducer_factory: Callable[[MissionDefinition, EvidenceRegistry], MissionReducer] | None = None,
    current: RuntimeSnapshot | None = None,
    initial: RuntimeSnapshot | None = None,
    post_action: RuntimeSnapshot | None = None,
    actor_hook: Callable[[], None] | None = None,
    controller_outcome: ControllerOutcome = ControllerOutcome.COMPLETED,
    controller_error: Exception | None = None,
    broker_factory: Callable[[ToolCatalog], CapabilityBroker] | None = None,
    pointer_cleanup: Any = None,
) -> Harness:
    registry = EvidenceRegistry()
    mission = definition or _definition(capability)
    reducer = (
        reducer_factory(mission, registry)
        if reducer_factory else MissionReducer(mission, evidence=registry)
    )
    _bind_target(reducer, registry)
    pre_frame = _frame(9)
    post_frame = _frame(10)
    _add_frame_evidence(registry, pre_frame)
    _add_frame_evidence(registry, post_frame)
    pre = initial or _snapshot(pre_frame)
    post = post_action or _snapshot(post_frame)
    state = ScriptedState(pre, current=current, post_action=post)
    actor = ScriptedActor(proposal, after_propose=actor_hook)
    controller = SpyController(
        state, registry, outcome=controller_outcome, raises=controller_error,
    )
    catalog = _catalog()
    profile = ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    broker = broker_factory(catalog) if broker_factory else CapabilityBroker(catalog)
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=broker,
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        pointer_cleanup=pointer_cleanup,
    )
    return Harness(reducer, registry, actor, state, controller, runtime, profile, catalog)


def test_runtime_frame_rejects_image_hash_mismatch() -> None:
    with pytest.raises(RuntimeContractError, match="image_sha256"):
        replace(_frame(9), image_sha256="0" * 64)


@pytest.mark.parametrize(
    "field,value,error",
    (
        ("frame_id", 0, "frame_id"),
        ("scope", "", "scope"),
    ),
)
def test_runtime_frame_rejects_incomplete_commit_identity(
    field: str,
    value: object,
    error: str,
) -> None:
    with pytest.raises(RuntimeContractError, match=error):
        replace(_frame(9), **{field: value})


@pytest.mark.parametrize("field", ("binding_id", "hwnd", "epoch"))
def test_runtime_snapshot_rejects_broker_target_identity_disagreement(field: str) -> None:
    snapshot = _snapshot(_frame(9))
    target = snapshot.target
    assert target is not None
    changes = {
        "binding_id": "different-binding",
        "hwnd": 202,
        "epoch": 4,
    }
    with pytest.raises(RuntimeContractError, match="target payload"):
        replace(snapshot, target=replace(target, **{field: changes[field]}))


def test_initial_runtime_target_must_match_accepted_mission_process() -> None:
    target = TargetCommitIdentity(
        "target-1", 101, 101, 8, 3, (0, 0, 1280, 720),
    )
    initial = _snapshot(_frame(9), runtime_target=target)
    harness = _harness(_click(), initial=initial)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert "accepted Mission state" in result.detail
    assert harness.actor.calls == []
    assert harness.controller.calls == []


def test_visual_route_binds_one_actor_proposal_to_one_runtime_envelope() -> None:
    harness = _harness(_click())
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(harness.actor.calls) == 1
    assert len(harness.controller.calls) == 1
    envelope = harness.controller.calls[0]
    assert isinstance(envelope, ActorDecisionEnvelope)
    assert envelope.mission_id == "mission-1"
    assert envelope.run_id == "run-1"
    assert envelope.mission_revision == before
    assert envelope.obligation_id == "o1"
    assert envelope.frame.frame_id == 9
    assert envelope.frame.epoch == 3
    assert envelope.frame.hwnd == 101
    assert envelope.frame.scope == "target_window"
    assert envelope.frame.image_sha256 == _frame(9).image_sha256
    assert envelope.target.binding_id == "target-1"
    assert envelope.target.root_hwnd == 101
    assert envelope.target.process_id == 7
    assert envelope.pointer == PointerLeaseState(PointerDisposition.RELEASED)
    assert envelope.proposal.action == "click"
    assert len(harness.reducer.events) == before + 1
    assert harness.reducer.events[-1].kind == "transition_recorded"
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.ACTIVE


@pytest.mark.parametrize("outcome", (ControllerOutcome.COMPLETED, ControllerOutcome.REJECTED))
def test_action_records_transition_but_never_claims_or_completes(outcome: ControllerOutcome) -> None:
    harness = _harness(_click(), controller_outcome=outcome)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(harness.controller.calls) == 1
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == ["transition_recorded"]
    state = harness.reducer.projection.obligation_state("o1")
    assert state.status is ObligationStatus.ACTIVE
    assert state.claim_fingerprint is None
    assert harness.reducer.projection.last_transition.input_completed is (
        outcome is ControllerOutcome.COMPLETED
    )


@pytest.mark.parametrize(
    "race",
    (
        "revision", "obligation", "frame", "scope", "hash", "target",
        "target_root", "target_pid", "target_geometry", "pointer", "grant", "broker",
    ),
)
def test_post_actor_identity_or_authority_change_discards_without_execution(race: str) -> None:
    definition = _definition(two_obligations=race == "obligation")
    holder: dict[str, Any] = {}

    def reducer_factory(mission: MissionDefinition, registry: EvidenceRegistry) -> MissionReducer:
        reducer = RacingReducer(mission, evidence=registry, race=race)
        holder["reducer"] = reducer
        return reducer

    def hook() -> None:
        reducer = holder.get("reducer")
        if race in {"revision", "obligation"}:
            reducer.raced = True

    pre = _snapshot(_frame(9))
    current = pre
    grants: tuple[CapabilityGrant, ...] = ()
    broker_factory: Callable[[ToolCatalog], CapabilityBroker] | None = None
    if race == "frame":
        current = _snapshot(_frame(11))
    elif race == "scope":
        current = _snapshot(_frame(9, scope="full_desktop"))
    elif race == "hash":
        current = _snapshot(_frame(9, salt="changed-pixels"))
    elif race == "target":
        current = _snapshot(
            _frame(9, hwnd=202),
            target=TargetState("target-2", 202, 3, True),
        )
    elif race in {"target_root", "target_pid", "target_geometry"}:
        target_identity = TargetCommitIdentity(
            binding_id="target-1",
            hwnd=101,
            root_hwnd=(202 if race == "target_root" else 101),
            process_id=(8 if race == "target_pid" else 7),
            epoch=3,
            window_region=(
                (1, 0, 1280, 720)
                if race == "target_geometry" else (0, 0, 1280, 720)
            ),
        )
        current = _snapshot(_frame(9), runtime_target=target_identity)
    elif race == "pointer":
        current = _snapshot(
            _frame(9),
            pointer=PointerLeaseState(PointerDisposition.HELD, "lease-1", True),
        )
    elif race in {"grant", "broker"}:
        broker_factory = lambda catalog: RacingBroker(catalog, race)

    harness = _harness(
        _click(),
        definition=definition,
        reducer_factory=reducer_factory,
        initial=pre,
        current=current,
        actor_hook=hook,
        broker_factory=broker_factory,
    )
    if race == "grant":
        grants = (CapabilityGrant.issue(
            mission_id="mission-1",
            run_id="run-1",
            capability="visual.draw",
            obligation_id="o1",
            mission_revision=harness.reducer.projection.revision,
            issued_turn=4,
            expires_after_turns=2,
            policy_reason="test-scoped grant",
        ),)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4, grants=grants)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


def test_stop_requested_after_actor_discards_late_decision() -> None:
    stop = Event()
    harness = _harness(_click(), actor_hook=stop.set)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


@pytest.mark.parametrize("route", ("provider", "blocked"))
def test_non_visual_route_never_calls_actor_or_same_turn_fallback(route: str) -> None:
    proposal = _click()
    if route == "provider":
        offer = ExecutorOffer(
            executor_id="provider-1",
            route=ToolRoute.PROVIDER,
            supported_capabilities=("communication.send_text",),
            tool_names=("send_text",),
            available=True,
            live=True,
            authorization_scopes=("external.send",),
            receipt_kinds=("delivery.receipt",),
        )
        initial = _snapshot(
            _frame(9),
            offers=(offer,),
            authorizations=("external.send", "recipient.bound"),
        )
        harness = _harness(
            proposal,
            capability="communication.send_text",
            initial=initial,
            post_action=initial,
        )
        expected = RuntimeStatus.ROUTE_DEFERRED
    else:
        initial = _snapshot(_frame(9), paused=True)
        harness = _harness(proposal, initial=initial, post_action=initial)
        expected = RuntimeStatus.UNAVAILABLE
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is expected
    assert harness.actor.calls == []
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


def test_actor_cannot_smuggle_provider_tool_into_controller() -> None:
    proposal = {
        **_click(),
        "action": "send_text",
        "args": {"text": "must not be sent"},
    }
    harness = _harness(proposal)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


def test_claim_is_only_claimed_and_replays_from_accepted_event() -> None:
    proposal = {
        "format": "actor-proposal/0",
        "kind": "claim_obligation_done",
        "concise_claim": "the requested local state is now visible",
    }
    harness = _harness(proposal)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_REQUIRED
    assert harness.controller.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == ["obligation_claimed"]
    state = harness.reducer.projection.obligation_state("o1")
    assert state.status is ObligationStatus.CLAIMED
    assert state.claim_fingerprint
    assert state.verification_id is None
    replayed = MissionReducer.replay(
        harness.reducer.definition,
        harness.reducer.events,
        evidence=harness.registry.snapshot(),
    )
    assert replayed.projection.to_dict() == harness.reducer.projection.to_dict()

    event_count = len(harness.reducer.events)
    repeated = harness.runtime.run_one_turn(turn_index=4)
    assert repeated.status is RuntimeStatus.STATE_REJECTED
    assert len(harness.reducer.events) == event_count
    assert harness.controller.calls == []


@pytest.mark.parametrize("invalid_scope", ("missing", "other_run", "other_obligation"))
def test_claim_rejects_missing_or_out_of_scope_frame_evidence(invalid_scope: str) -> None:
    harness = _harness({
        "format": "actor-proposal/0",
        "kind": "claim_obligation_done",
        "concise_claim": "the current state appears complete",
    })
    evidence_id = harness.state.initial.frame.evidence_refs[0]
    if invalid_scope == "missing":
        frame = replace(harness.state.initial.frame, evidence_refs=())
        snapshot = replace(harness.state.initial, frame=frame)
        harness.state.initial = harness.state.current_snapshot = snapshot
    else:
        original = harness.registry.refs[evidence_id]
        harness.registry.refs[evidence_id] = replace(
            original,
            run_id=("other-run" if invalid_scope == "other_run" else original.run_id),
            obligation_id=(
                "other-obligation"
                if invalid_scope == "other_obligation" else original.obligation_id
            ),
        )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


def test_blocked_report_does_not_promote_actor_observations_to_verified_facts() -> None:
    harness = _harness({
        "format": "actor-proposal/0",
        "kind": "blocked",
        "blocker_kind": "unexpected_dialog",
        "observed_facts": ("A dialog covers the current target",),
    })
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.BLOCKED
    assert harness.controller.calls == []
    assert [event.kind for event in harness.reducer.events[before:]] == ["obligation_blocked"]
    assert harness.reducer.projection.verified_facts == ()
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.BLOCKED


def test_observe_refreshes_pixels_without_claim_or_completion_event() -> None:
    harness = _harness({
        "format": "actor-proposal/0",
        "kind": "observe",
        "observation_kind": "image_region",
        "image_region": {"x": 100, "y": 80, "width": 300, "height": 200},
        "question": "Show the small control more clearly",
    })
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.OBSERVED
    assert harness.controller.calls == []
    assert len(harness.state.observe_calls) == 2
    assert harness.state.observe_calls[-1][1] is not None
    assert len(harness.reducer.events) == before
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.ACTIVE


def test_observe_rejects_region_outside_submitted_image_without_second_capture() -> None:
    harness = _harness({
        "format": "actor-proposal/0",
        "kind": "observe",
        "observation_kind": "image_region",
        "image_region": {"x": 1200, "y": 700, "width": 200, "height": 100},
        "question": "Inspect this region",
    })
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.INVALID_PROPOSAL
    assert len(harness.state.observe_calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


@pytest.mark.parametrize(
    "proposal",
    (
        {
            "format": "actor-proposal/0",
            "kind": "request_inspection",
            "region": {"x": 0, "y": 0, "width": 200, "height": 200},
            "question": "Describe the local geometry",
        },
        {
            "format": "actor-proposal/0",
            "kind": "request_replan",
            "reason_kind": "strategy_exhausted",
            "observed_conflicts": ("the expected state is absent",),
            "failed_strategy_summaries": ("one local semantic route failed",),
        },
    ),
)
def test_p6_services_remain_unavailable_without_fallback(proposal: Mapping[str, Any]) -> None:
    harness = _harness(proposal)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


@pytest.mark.parametrize("failure", ("unknown", "exception"))
def test_uncertain_controller_outcome_is_never_replayed_and_blocks(failure: str) -> None:
    harness = _harness(
        _click(),
        controller_outcome=(
            ControllerOutcome.UNKNOWN if failure == "unknown" else ControllerOutcome.COMPLETED
        ),
        controller_error=(RuntimeError("transport lost after input") if failure == "exception" else None),
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.BLOCKED
    assert len(harness.controller.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == ["obligation_blocked"]
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.BLOCKED


@pytest.mark.parametrize("mismatch", ("decision", "action", "post_frame"))
def test_controller_identity_mismatch_blocks_after_one_call_without_replay(mismatch: str) -> None:
    harness = _harness(_click())

    def corrupt(
        envelope: ActorDecisionEnvelope,
        transition: ControllerTransition,
    ) -> ControllerTransition:
        if mismatch == "decision":
            return replace(transition, action_id="wrong-decision")
        if mismatch == "action":
            return replace(transition, action="drag")
        return replace(transition, post_frame_id=999)

    harness.controller.transition_mutator = corrupt
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.BLOCKED
    assert len(harness.controller.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == ["obligation_blocked"]
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.BLOCKED


def test_canceled_held_pointer_uses_only_deterministic_cleanup() -> None:
    held = _snapshot(
        _frame(9),
        pointer=PointerLeaseState(PointerDisposition.HELD, "lease-1", True),
        canceled=True,
    )
    cleanup = SpyCleanup()
    harness = _harness(
        _click(),
        initial=held,
        current=held,
        post_action=held,
        pointer_cleanup=cleanup,
    )
    stop = Event()
    stop.set()
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert harness.actor.calls == []
    assert harness.controller.calls == []
    assert len(cleanup.calls) == 1
    assert cleanup.calls[0] == ("lease-1", "runtime_canceled")
    assert len(harness.reducer.events) == before


def test_randomized_clicks_never_expand_one_proposal_into_multiple_transactions() -> None:
    rng = random.Random(41)
    for _ in range(12):
        harness = _harness(_click(rng.randrange(0, 1280), rng.randrange(0, 720)))
        before = len(harness.reducer.events)
        result = harness.runtime.run_one_turn(turn_index=4)
        assert result.status is RuntimeStatus.ACTION_RECORDED
        assert len(harness.actor.calls) == 1
        assert len(harness.controller.calls) == 1
        assert [event.kind for event in harness.reducer.events[before:]] == [
            "transition_recorded"
        ]
