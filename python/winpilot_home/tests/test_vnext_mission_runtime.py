"""P4a MissionRuntime ownership, binding, and replay tests.

These tests exercise only injected ports.  They intentionally do not import
the legacy AgentRunner, ToolSession, frame capture, or desktop executor.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
from dataclasses import dataclass, replace
from threading import Event
from typing import Any, Callable, Mapping

import pytest

from winpilot.vnext.actor import ActorDecisionEnvelope, InvalidActorProposal
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
from winpilot.vnext.capabilities.live_catalog import build_live_catalog
from winpilot.vnext.evidence import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry as CanonicalEvidenceRegistry,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from winpilot.vnext.mission import (
    EvidencePolicy,
    EvidenceRef,
    MissionCard,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    ObligationStatus,
    target_binding_attestation,
)
from winpilot.vnext.profiling import DEFAULT_CAPABILITY_CATALOG, ExecutionPolicy
from winpilot.vnext.runtime import (
    ControllerOutcome,
    ControllerTransition,
    MissionRuntime,
    MutationPermitAuthority,
    RuntimeContractError,
    RuntimeFrame,
    RuntimeSnapshot,
    RuntimeStatus,
)
from winpilot.vnext.runtime import mission_runtime as mission_runtime_module
from winpilot.vnext.verification import (
    DeterministicActionEffectVerifier,
    DeterministicMissionCompletionVerifier,
    ObligationVerificationRequest,
    VerifierAssessment,
    forbidden_effect_condition,
)
from winpilot.vnext.verification.visual_model import (
    ReadOnlyVisualVerifierAdapter,
    VisualVerifierModelRequest,
)
from winpilot.visual_runtime.commit_admission import TargetCommitIdentity


ALL_CAPABILITIES = tuple(sorted(DEFAULT_CAPABILITY_CATALOG))
VALID_PIXEL_IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAACklEQVQIHWMAAgAABAABDTukuQAAAABJRU5ErkJggg=="
)
PROCESS_IDENTITY = f"process-{'1' * 32}"
REPLACEMENT_PROCESS_IDENTITY = f"process-{'2' * 32}"


class EvidenceRegistry:
    def __init__(self) -> None:
        self.refs: dict[str, EvidenceRef] = {}

    def add(self, ref: EvidenceRef) -> None:
        assert ref.id not in self.refs
        self.refs[ref.id] = ref

    def snapshot(self) -> tuple[EvidenceRef, ...]:
        return tuple(self.refs.values())


def _append_evidence(registry: Any, ref: EvidenceRef) -> None:
    if isinstance(registry, CanonicalEvidenceRegistry):
        registry.append(ref)
    else:
        registry.add(ref)


class ScriptedActor:
    def __init__(
        self,
        proposal: Mapping[str, Any],
        *,
        after_propose: Callable[[], None] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.proposal = dict(proposal)
        self.after_propose = after_propose
        self.raises = raises
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
        if self.raises is not None:
            raise self.raises
        return dict(self.proposal)


class NoCallTargetDiscovery:
    def discover(self, _query: str, *, stop_event=None):
        raise AssertionError("bound Runtime unexpectedly requested target discovery")


class ScriptedVerifier:
    verifier_kind = "deterministic_first"

    def __init__(
        self,
        response: VerifierAssessment | Mapping[str, Any] | object,
        *,
        after_verify: Callable[[], None] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.response = response
        self.after_verify = after_verify
        self.raises = raises
        self.calls: list[tuple[ObligationVerificationRequest, Event | None]] = []

    def verify(
        self,
        request: ObligationVerificationRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment | Mapping[str, Any] | object:
        self.calls.append((request, stop_event))
        if self.after_verify is not None:
            self.after_verify()
        if self.raises is not None:
            raise self.raises
        return self.response


class ScriptedVisualModel:
    def __init__(
        self,
        response: VerifierAssessment | Mapping[str, Any] | object,
        *,
        after_assess: Callable[[], None] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.response = response
        self.after_assess = after_assess
        self.raises = raises
        self.calls: list[tuple[VisualVerifierModelRequest, Event | None]] = []

    def assess(
        self,
        request: VisualVerifierModelRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment | Mapping[str, Any] | object:
        self.calls.append((request, stop_event))
        if self.after_assess is not None:
            self.after_assess()
        if self.raises is not None:
            raise self.raises
        return self.response


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
        permits: MutationPermitAuthority,
        *,
        outcome: ControllerOutcome = ControllerOutcome.COMPLETED,
        raises: Exception | None = None,
        consume_permit: bool = True,
    ) -> None:
        self.state = state
        self.registry = registry
        self.outcome = outcome
        self.raises = raises
        self.permits = permits
        self.consume_permit = consume_permit
        self.permit_results = []
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
        if self.consume_permit:
            permit = self.permits.consume(envelope)
            self.permit_results.append(permit)
            assert permit.allowed is True
        self.state.action_executed = True
        if self.raises is not None:
            raise self.raises
        evidence_refs: tuple[str, ...] = ()
        if self.outcome in {ControllerOutcome.COMPLETED, ControllerOutcome.REJECTED}:
            evidence_id = f"evidence-{envelope.decision_id}"
            post_frame = self.state.post_action.frame
            assert post_frame is not None
            _append_evidence(self.registry, EvidenceRef(
                id=evidence_id,
                mission_id=envelope.mission_id,
                run_id=envelope.run_id,
                obligation_id=envelope.obligation_id,
                kind=POST_ACTION_FRAME_EVIDENCE_KIND,
                trust="deterministic",
                payload_hash=frame_identity_payload_hash(
                    post_frame.commit_identity
                ),
                frame_id=post_frame.frame_id,
                action_id=envelope.decision_id,
            ))
            evidence_refs = (evidence_id,)
            self.state.post_action = replace(
                self.state.post_action,
                frame=replace(
                    post_frame,
                    evidence_refs=evidence_refs,
                ),
            )
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


class CanonicalPostFrameController:
    """Test double that emits the same post-frame evidence as the live bridge."""

    def __init__(
        self,
        state: ScriptedState,
        registry: CanonicalEvidenceRegistry,
        permits: MutationPermitAuthority,
    ) -> None:
        self.state = state
        self.registry = registry
        self.permits = permits
        self.calls: list[ActorDecisionEnvelope] = []

    def execute(self, envelope, *, stop_event=None):
        self.calls.append(envelope)
        permit = self.permits.consume(envelope)
        assert permit.allowed is True
        post = self.state.post_action.frame
        assert post is not None
        payload_hash = frame_identity_payload_hash(post.commit_identity)
        evidence = EvidenceRef(
            id=f"post-action-{envelope.decision_id}",
            mission_id=envelope.mission_id,
            run_id=envelope.run_id,
            obligation_id=envelope.obligation_id,
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            payload_hash=payload_hash,
            frame_id=post.frame_id,
            action_id=envelope.decision_id,
        )
        self.registry.append(evidence)
        self.state.post_action = replace(
            self.state.post_action,
            frame=replace(post, evidence_refs=(evidence.id,)),
        )
        self.state.action_executed = True
        return ControllerTransition(
            outcome=ControllerOutcome.COMPLETED,
            action_id=envelope.decision_id,
            action=envelope.proposal.action,
            input_completed=True,
            evidence_refs=(evidence.id,),
            post_frame_id=post.frame_id,
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
    registry: Any
    actor: ScriptedActor
    state: ScriptedState
    controller: SpyController
    runtime: MissionRuntime
    profile: Any
    catalog: ToolCatalog
    permits: MutationPermitAuthority
    verifier: ScriptedVerifier | None


@dataclass
class CompletionHarness:
    reducer: MissionReducer
    registry: CanonicalEvidenceRegistry
    actor: ScriptedActor
    state: ScriptedState
    controller: SpyController
    runtime: MissionRuntime
    verifier: DeterministicMissionCompletionVerifier
    profile: Any
    catalog: ToolCatalog
    permits: MutationPermitAuthority


@dataclass
class VisualHarness:
    reducer: MissionReducer
    registry: CanonicalEvidenceRegistry
    actor: ScriptedActor
    state: ScriptedState
    controller: SpyController
    runtime: MissionRuntime
    model: ScriptedVisualModel
    adapter: ReadOnlyVisualVerifierAdapter
    frame: RuntimeFrame
    profile: Any
    catalog: ToolCatalog
    permits: MutationPermitAuthority


class FailOnceMissionCompletionReducer(MissionReducer):
    def __init__(self, definition: MissionDefinition, *, evidence: Any) -> None:
        super().__init__(definition, evidence=evidence)
        self.reject_next_completion = True

    def apply(self, event):
        kind = event.kind if hasattr(event, "kind") else event.get("kind")
        if kind == "mission_verified" and self.reject_next_completion:
            self.reject_next_completion = False
            raise RuntimeError("simulated event persistence failure")
        return super().apply(event)


def _schema(name: str, properties: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": dict(properties or {}),
                **({"required": ["x", "y"]} if name == "click" else {}),
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


def _target_attestation(
    *,
    evidence_id: str = "target-proof",
    acquisition_action_id: str = "target-acquisition-1",
    hwnd: int = 101,
    root_hwnd: int | None = None,
    process_id: int = 7,
    process_identity: str = PROCESS_IDENTITY,
    title_snapshot: str = "Current application",
    epoch: int = 3,
):
    return target_binding_attestation(
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="o1",
        evidence_id=evidence_id,
        acquisition_action_id=acquisition_action_id,
        hwnd=hwnd,
        root_hwnd=hwnd if root_hwnd is None else root_hwnd,
        process_id=process_id,
        process_identity=process_identity,
        title_snapshot=title_snapshot,
        epoch=epoch,
    )


def _bind_target(reducer: MissionReducer, registry: Any) -> None:
    binding, evidence = _target_attestation()
    _append_evidence(registry, evidence)
    reducer.apply(reducer.event(
        "target_bound",
        payload={"target_binding": binding.to_dict()},
        evidence_refs=(evidence.id,),
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
    target_state = target or TargetState(
        _target_attestation(hwnd=frame.hwnd, epoch=frame.epoch)[0].binding_id,
        frame.hwnd,
        frame.epoch,
        True,
    )
    target_identity = runtime_target or TargetCommitIdentity(
        binding_id=str(target_state.binding_id),
        hwnd=int(target_state.hwnd),
        root_hwnd=int(target_state.hwnd),
        process_id=7,
        process_identity=PROCESS_IDENTITY,
        epoch=int(target_state.epoch),
        window_region=(0, 0, 1280, 720),
    )
    return RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=turn_index,
            available_capabilities=ALL_CAPABILITIES,
            target=target_state,
            frame=AuthoritativeFrameState(
                frame.frame_id,
                frame.hwnd,
                frame.epoch,
                frame.scope,
                True,
                True,
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


def test_runtime_snapshot_rejects_previous_wire_version() -> None:
    for old_format in ("runtime-snapshot/0", "runtime-snapshot/1"):
        with pytest.raises(RuntimeContractError, match="format"):
            replace(_snapshot(_frame(9)), format=old_format)


def _set_harness_turn(harness: Harness, turn_index: int) -> None:
    """Advance the injected RuntimeState without inventing any OS semantics."""

    def advance(snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
        return replace(
            snapshot,
            broker_state=replace(snapshot.broker_state, turn_index=turn_index),
        )

    harness.state.initial = advance(harness.state.initial)
    harness.state.current_snapshot = advance(harness.state.current_snapshot)
    harness.state.post_action = advance(harness.state.post_action)


def _add_frame_evidence(registry: Any, frame: RuntimeFrame) -> None:
    for evidence_id in frame.evidence_refs:
        _append_evidence(registry, EvidenceRef(
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
        "format": "actor-proposal/1",
        "kind": "desktop_action",
        "action": "click",
        "args": {"x": x, "y": y},
        "local_intent": "activate the visible semantic target",
    }


def _live_click(x: int = 320, y: int = 240) -> dict[str, Any]:
    return {
        "format": "actor-proposal/1",
        "kind": "desktop_action",
        "action": "click",
        "args": {"image_x": x, "image_y": y},
        "local_intent": "activate the visible semantic target",
    }


def _type_text(text: str) -> dict[str, Any]:
    return {
        "format": "actor-proposal/1",
        "kind": "desktop_action",
        "action": "type_text",
        "args": {"text": text},
        "local_intent": "enter text into the currently focused control",
    }


def _hotkey(*modifiers: str, key: str) -> dict[str, Any]:
    return {
        "format": "actor-proposal/1",
        "kind": "desktop_action",
        "action": "hotkey",
        "args": {"modifiers": list(modifiers), "key": key},
        "local_intent": "apply the selected keyboard command to the focused control",
    }


def _claim() -> dict[str, Any]:
    return {
        "format": "actor-proposal/1",
        "kind": "claim_obligation_done",
        "concise_claim": "the requested local state is now visible",
    }


def _verified_assessment(
    *conditions: str,
    reason: str = "trusted evidence covers every required condition",
) -> VerifierAssessment:
    return VerifierAssessment(
        verified=True,
        satisfied_conditions=conditions,
        missing_conditions=(),
        contradictions=(),
        confidence=1.0,
        concise_reason=reason,
    )


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
    actor_error: Exception | None = None,
    controller_outcome: ControllerOutcome = ControllerOutcome.COMPLETED,
    controller_error: Exception | None = None,
    broker_factory: Callable[[ToolCatalog], CapabilityBroker] | None = None,
    catalog: ToolCatalog | None = None,
    pointer_cleanup: Any = None,
    consume_permit: bool = True,
    obligation_verifier: ScriptedVerifier | None = None,
    verifier_enabled: bool = False,
) -> Harness:
    mission = definition or _definition(capability)
    registry = CanonicalEvidenceRegistry(mission.mission_id, mission.run_id)
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
    actor = ScriptedActor(
        proposal, after_propose=actor_hook, raises=actor_error,
    )
    permits = MutationPermitAuthority(mission.mission_id, mission.run_id)
    controller = SpyController(
        state, registry, permits, outcome=controller_outcome, raises=controller_error,
        consume_permit=consume_permit,
    )
    catalog = catalog or _catalog()
    profile = ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    if verifier_enabled:
        profile = replace(
            profile,
            services=replace(profile.services, obligation_verifier=True),
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
        target_discovery=(
            NoCallTargetDiscovery()
            if any(tool.name == "discover_target" for tool in catalog.tools)
            else None
        ),
        pointer_cleanup=pointer_cleanup,
        mutation_permits=permits,
        obligation_verifier=obligation_verifier,
        evidence_registry=registry,
    )
    return Harness(
        reducer, registry, actor, state, controller, runtime, profile, catalog, permits,
        obligation_verifier,
    )


def _canonical_action_harness(
    proposal: Mapping[str, Any],
    *,
    catalog: ToolCatalog,
) -> Harness:
    mission = _definition()
    registry = CanonicalEvidenceRegistry(mission.mission_id, mission.run_id)
    reducer = MissionReducer(mission, evidence=registry)
    target_binding, target_proof = _target_attestation()
    registry.append(target_proof)
    reducer.apply(reducer.event(
        "target_bound",
        payload={"target_binding": target_binding.to_dict()},
        evidence_refs=(target_proof.id,),
    ))

    def pixel_frame(frame_id: int, evidence_refs=()):
        return RuntimeFrame(
            frame_id=frame_id,
            epoch=3,
            hwnd=101,
            region=(0, 0, 1280, 720),
            scope="target_window",
            submitted_size=(1, 1),
            image_b64=VALID_PIXEL_IMAGE,
            image_sha256=hashlib.sha256(
                VALID_PIXEL_IMAGE.encode("ascii")
            ).hexdigest(),
            evidence_refs=evidence_refs,
        )

    current_id = "current-frame-9"
    pre_frame = pixel_frame(9, (current_id,))
    registry.append(EvidenceRef(
        id=current_id,
        mission_id=mission.mission_id,
        run_id=mission.run_id,
        obligation_id="o1",
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=current_frame_payload_hash(pre_frame.commit_identity),
        frame_id=pre_frame.frame_id,
    ))
    pre = _snapshot(pre_frame)
    post = _snapshot(pixel_frame(10))
    state = ScriptedState(pre, post_action=post)
    actor = ScriptedActor(proposal)
    permits = MutationPermitAuthority(mission.mission_id, mission.run_id)
    controller = CanonicalPostFrameController(state, registry, permits)
    profile = ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        target_discovery=(
            NoCallTargetDiscovery()
            if any(tool.name == "discover_target" for tool in catalog.tools)
            else None
        ),
        mutation_permits=permits,
        action_effect_verifier=DeterministicActionEffectVerifier(),
        evidence_registry=registry,
    )
    return Harness(
        reducer, registry, actor, state, controller, runtime, profile, catalog,
        permits, None,
    )


def _action_effect_harness():
    harness = _canonical_action_harness(_click(), catalog=_catalog())
    return (
        harness.runtime,
        harness.reducer,
        harness.registry,
        harness.actor,
        harness.state,
        harness.controller,
    )


def _visual_harness(
    *,
    evidence_mode: str = "current_frame",
    allow_visual: bool = True,
    uncertain_policy: str = "blocked",
    response: VerifierAssessment | Mapping[str, Any] | object | None = None,
    model_hook: Callable[[], None] | None = None,
    model_error: Exception | None = None,
) -> VisualHarness:
    base = _definition()
    policy = EvidencePolicy(
        allow_independent_visual_verifier=allow_visual,
        uncertain_policy=uncertain_policy,
    )
    definition = replace(
        base,
        obligations=(replace(base.obligations[0], evidence_policy=policy),),
    )
    registry = CanonicalEvidenceRegistry(
        definition.mission_id, definition.run_id
    )
    reducer = MissionReducer(definition, evidence=registry)
    target_binding, target_proof = _target_attestation()
    registry.append(target_proof)
    reducer.apply(reducer.event(
        "target_bound",
        payload={"target_binding": target_binding.to_dict()},
        evidence_refs=(target_proof.id,),
    ))
    frame = _frame(9, salt=evidence_mode)
    if evidence_mode == "current_frame":
        frame_kind = CURRENT_FRAME_EVIDENCE_KIND
        frame_hash = current_frame_payload_hash(frame.commit_identity)
        action_id = None
    elif evidence_mode == "post_action":
        frame_kind = POST_ACTION_FRAME_EVIDENCE_KIND
        frame_hash = frame_identity_payload_hash(frame.commit_identity)
        action_id = "decision-before-claim"
    else:
        raise AssertionError(evidence_mode)
    registry.append(EvidenceRef(
        id=frame.evidence_refs[0],
        mission_id=definition.mission_id,
        run_id=definition.run_id,
        obligation_id="o1",
        kind=frame_kind,
        trust="deterministic",
        payload_hash=frame_hash,
        frame_id=frame.frame_id,
        action_id=action_id,
    ))
    snapshot = _snapshot(frame)
    state = ScriptedState(snapshot)
    actor = ScriptedActor(_claim())
    permits = MutationPermitAuthority(definition.mission_id, definition.run_id)
    controller = SpyController(state, EvidenceRegistry(), permits)
    catalog = _catalog()
    profile = ExecutionPolicy().safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    profile = replace(
        profile,
        services=replace(profile.services, obligation_verifier=True),
    )
    visual_response = response if response is not None else _verified_assessment(
        "the requested state is visibly confirmed"
    )
    model = ScriptedVisualModel(
        visual_response,
        after_assess=model_hook,
        raises=model_error,
    )
    adapter = ReadOnlyVisualVerifierAdapter(model)
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        mutation_permits=permits,
        attested_visual_verifier=adapter,
        evidence_registry=registry,
    )
    return VisualHarness(
        reducer=reducer,
        registry=registry,
        actor=actor,
        state=state,
        controller=controller,
        runtime=runtime,
        model=model,
        adapter=adapter,
        frame=frame,
        profile=profile,
        catalog=catalog,
        permits=permits,
    )


def _completion_harness(
    *,
    awaiting: bool = True,
    fail_once: bool = False,
) -> CompletionHarness:
    mission = _definition()
    registry = CanonicalEvidenceRegistry(mission.mission_id, mission.run_id)
    reducer_type = FailOnceMissionCompletionReducer if fail_once else MissionReducer
    reducer = reducer_type(mission, evidence=registry)
    target_binding, target_proof = _target_attestation()
    registry.append(target_proof)
    reducer.apply(reducer.event(
        "target_bound",
        payload={"target_binding": target_binding.to_dict()},
        evidence_refs=(target_proof.id,),
    ))
    frame = _frame(9)
    frame_proof = EvidenceRef(
        id=frame.evidence_refs[0],
        mission_id=mission.mission_id,
        run_id=mission.run_id,
        obligation_id="o1",
        kind="frame",
        trust="deterministic",
        payload_hash=f"payload-{frame.evidence_refs[0]}",
        frame_id=frame.frame_id,
    )
    registry.append(frame_proof)
    if awaiting:
        reducer.apply(reducer.event(
            "obligation_claimed",
            payload={"obligation_id": "o1"},
            evidence_refs=(frame_proof.id,),
        ))
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "o1",
                "verified": True,
                "verification_id": "verification-o1",
                "satisfied_criteria": ["the requested state is visibly confirmed"],
                "evidence_refs": [frame_proof.id],
            },
            evidence_refs=(frame_proof.id,),
        ))
        assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    snapshot = _snapshot(frame)
    state = ScriptedState(snapshot)
    actor = ScriptedActor(_claim())
    catalog = _catalog()
    profile = ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    if not awaiting:
        profile = replace(
            profile,
            services=replace(profile.services, obligation_verifier=True),
        )
    permits = MutationPermitAuthority(mission.mission_id, mission.run_id)
    controller = SpyController(state, registry, permits)
    obligation_verifier = (
        ScriptedVerifier(_verified_assessment(
            "the requested state is visibly confirmed"
        ))
        if not awaiting else None
    )
    verifier = DeterministicMissionCompletionVerifier()
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        mutation_permits=permits,
        obligation_verifier=obligation_verifier,
        mission_completion_verifier=verifier,
        evidence_registry=registry,
    )
    return CompletionHarness(
        reducer=reducer,
        registry=registry,
        actor=actor,
        state=state,
        controller=controller,
        runtime=runtime,
        verifier=verifier,
        profile=profile,
        catalog=catalog,
        permits=permits,
    )


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
        _target_attestation()[0].binding_id,
        101,
        101,
        8,
        REPLACEMENT_PROCESS_IDENTITY,
        3,
        (0, 0, 1280, 720),
    )
    initial = _snapshot(_frame(9), runtime_target=target)
    harness = _harness(_click(), initial=initial)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert "accepted Mission state" in result.detail
    assert harness.actor.calls == []
    assert harness.controller.calls == []


@pytest.mark.parametrize(
    "changes",
    (
        {"root_hwnd": 202},
        {"process_identity": REPLACEMENT_PROCESS_IDENTITY},
    ),
)
def test_initial_runtime_target_rejects_each_strong_identity_mismatch(
    changes,
) -> None:
    baseline = _snapshot(_frame(9))
    assert baseline.target is not None
    initial = replace(
        baseline,
        target=replace(baseline.target, **changes),
    )
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
    assert envelope.target.binding_id == _target_attestation()[0].binding_id
    assert envelope.target.root_hwnd == 101
    assert envelope.target.process_id == 7
    assert envelope.pointer == PointerLeaseState(PointerDisposition.RELEASED)
    assert envelope.proposal.action == "click"
    assert len(harness.controller.permit_results) == 1
    assert harness.controller.permit_results[0].allowed is True
    assert harness.permits.pending_count == 0
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


def test_runtime_registers_action_effect_in_the_same_transition_event() -> None:
    runtime, reducer, registry, actor, _state, controller = _action_effect_harness()
    before = len(reducer.events)

    result = runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(actor.calls) == 1
    assert len(controller.calls) == 1
    accepted = reducer.events[before:]
    assert [event.kind for event in accepted] == ["transition_recorded"]
    transition = reducer.projection.last_transition
    assert transition is not None
    assert transition.action_effect is not None
    assert transition.action_effect.local_visual_change.value == "unchanged"
    assert transition.action_effect.semantic_completion == "unknown"
    assert reducer.projection.obligation_state("o1").no_progress_streak == 1
    assert transition.action_effect.evidence_ref in {
        ref.id for ref in registry.snapshot()
    }


def test_action_effect_failure_does_not_erase_or_replay_closed_action(
    monkeypatch,
) -> None:
    runtime, reducer, _registry, actor, _state, controller = _action_effect_harness()
    monkeypatch.setattr(
        DeterministicActionEffectVerifier,
        "verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("comparison unavailable")
        ),
    )
    before = len(reducer.events)

    result = runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(actor.calls) == 1
    assert len(controller.calls) == 1
    assert [event.kind for event in reducer.events[before:]] == [
        "transition_recorded"
    ]
    assert reducer.projection.last_transition is not None
    assert reducer.projection.last_transition.action_effect is None
    assert reducer.projection.obligation_state("o1").no_progress_streak == 0


def test_accepted_transition_projects_exactly_one_run_local_previous_action() -> None:
    first = _live_click(111, 222)
    first["local_intent"] = "activate the first currently visible target"
    harness = _canonical_action_harness(first, catalog=build_live_catalog())
    before = len(harness.reducer.events)

    first_result = harness.runtime.run_one_turn(turn_index=4)

    assert first_result.status is RuntimeStatus.ACTION_RECORDED
    assert harness.actor.calls[0][0].previous_action is None
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == ["transition_recorded"]
    # The previous-action view is an ephemeral Runtime aid, never Mission truth.
    payload = accepted[0].payload
    assert "local_intent" not in payload
    assert "args" not in payload
    assert "arguments" not in payload
    assert first["local_intent"] not in json.dumps(
        accepted[0].to_dict(), ensure_ascii=False, sort_keys=True,
    )

    second = _live_click(333, 444)
    second["local_intent"] = "activate the second currently visible target"
    harness.actor.proposal = second
    _set_harness_turn(harness, 5)
    second_result = harness.runtime.run_one_turn(turn_index=5)

    assert second_result.status is RuntimeStatus.ACTION_RECORDED
    receipt = harness.actor.calls[1][0].previous_action
    assert receipt is not None
    assert receipt.action == "click"
    assert receipt.local_intent == first["local_intent"]
    assert dict(receipt.arguments) == {"image_x": 111, "image_y": 222}
    assert receipt.input_completed is True
    assert receipt.post_action_frame_reconciled is True
    assert receipt.visible_effect == ""


def test_transient_observation_failure_does_not_consume_previous_action() -> None:
    first = _live_click(111, 222)
    first["local_intent"] = "activate the first currently visible target"
    harness = _canonical_action_harness(first, catalog=build_live_catalog())
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    pending = harness.runtime._pending_actor_action
    assert pending is not None

    original_observe = harness.state.observe

    def fail_observe_once(*_args, **_kwargs):
        raise RuntimeError("transient frame observation failure")

    harness.state.observe = fail_observe_once  # type: ignore[method-assign]
    _set_harness_turn(harness, 5)
    rejected = harness.runtime.run_one_turn(turn_index=5)

    assert rejected.status is RuntimeStatus.STATE_REJECTED
    assert len(harness.actor.calls) == 1
    assert harness.runtime._pending_actor_action is pending

    harness.state.observe = original_observe  # type: ignore[method-assign]
    harness.actor.proposal = _live_click(333, 444)
    _set_harness_turn(harness, 6)
    resumed = harness.runtime.run_one_turn(turn_index=6)

    assert resumed.status is RuntimeStatus.ACTION_RECORDED
    receipt = harness.actor.calls[1][0].previous_action
    assert receipt is not None
    assert receipt.local_intent == first["local_intent"]


def test_mission_replay_or_a_new_runtime_does_not_reconstruct_previous_action() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    replayed = MissionReducer.replay(
        harness.reducer.definition,
        harness.reducer.events,
        evidence=harness.registry,
    )
    actor = ScriptedActor(_claim())
    permits = MutationPermitAuthority("mission-1", "run-1")
    controller = SpyController(harness.state, harness.registry, permits)
    runtime = MissionRuntime(
        reducer=replayed,
        profile=harness.profile,
        broker=CapabilityBroker(harness.catalog),
        catalog=harness.catalog,
        actor=actor,
        state=harness.state,
        controller=controller,
        target_discovery=NoCallTargetDiscovery(),
        mutation_permits=permits,
    )

    _set_harness_turn(harness, 5)
    runtime.run_one_turn(turn_index=5)

    assert len(actor.calls) == 1
    assert actor.calls[0][0].previous_action is None


@pytest.mark.parametrize(
    "failure",
    ("unknown", "reconciliation_failed", "proved_not_executed"),
)
def test_unaccepted_controller_outcome_never_reaches_a_later_actor_turn(
    failure: str,
) -> None:
    outcome = (
        ControllerOutcome.UNKNOWN
        if failure == "unknown"
        else ControllerOutcome.REJECTED
        if failure == "proved_not_executed"
        else ControllerOutcome.COMPLETED
    )
    harness = _harness(_click(), controller_outcome=outcome)

    if failure == "reconciliation_failed":
        harness.controller.transition_mutator = lambda _envelope, transition: replace(
            transition, post_frame_id=transition.post_frame_id + 100,
        )
    elif failure == "proved_not_executed":
        harness.controller.transition_mutator = lambda _envelope, transition: replace(
            transition,
            input_completed=False,
            evidence_refs=(),
            post_frame_id=None,
            visible_effect="",
        )

    first = harness.runtime.run_one_turn(turn_index=4)

    expected = (
        RuntimeStatus.STALE_DISCARDED
        if failure == "proved_not_executed" else RuntimeStatus.BLOCKED
    )
    assert first.status is expected
    assert all(event.kind != "transition_recorded" for event in harness.reducer.events)
    assert harness.runtime._pending_actor_action is None

    if harness.reducer.projection.status is MissionStatus.BLOCKED:
        harness.reducer.apply(harness.reducer.event(
            "obligation_unblocked", payload={"obligation_id": "o1"},
        ))
    harness.actor.proposal = _click(601, 402)
    _set_harness_turn(harness, 5)
    harness.runtime.run_one_turn(turn_index=5)

    assert len(harness.actor.calls) == 2
    assert harness.actor.calls[1][0].previous_action is None


@pytest.mark.parametrize("terminal", (MissionStatus.FAILED, MissionStatus.CANCELED))
def test_terminal_turn_consumes_pending_actor_action_without_projection(
    terminal: MissionStatus,
) -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    actor_calls = len(harness.actor.calls)
    harness.reducer.apply(harness.reducer.event(
        f"mission_{terminal.value}", payload={"reason": "terminal test"},
    ))

    result = harness.runtime.run_one_turn(turn_index=5)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert len(harness.actor.calls) == actor_calls
    assert harness.runtime._pending_actor_action is None


def test_canceled_turn_consumes_previous_action_instead_of_delaying_it() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    stop = Event()
    stop.set()

    canceled = harness.runtime.run_one_turn(turn_index=5, stop_event=stop)

    assert canceled.status is RuntimeStatus.CANCELED
    assert len(harness.actor.calls) == 1
    harness.actor.proposal = _live_click(610, 410)
    _set_harness_turn(harness, 6)
    resumed = harness.runtime.run_one_turn(turn_index=6)
    assert resumed.status is RuntimeStatus.ACTION_RECORDED
    assert harness.actor.calls[1][0].previous_action is None


def test_mission_revision_change_invalidates_previous_action() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    harness.reducer.apply(harness.reducer.event(
        "obligation_blocked",
        payload={
            "obligation_id": "o1",
            "blocker_kind": "transient_test",
            "reason": "advance only the accepted Mission revision",
        },
    ))
    harness.reducer.apply(harness.reducer.event(
        "obligation_unblocked", payload={"obligation_id": "o1"},
    ))
    harness.actor.proposal = _live_click(620, 420)
    _set_harness_turn(harness, 5)

    harness.runtime.run_one_turn(turn_index=5)

    assert harness.actor.calls[1][0].previous_action is None


def test_target_change_does_not_cross_project_previous_action() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    current_binding = harness.reducer.projection.target_binding
    assert current_binding is not None
    harness.reducer.apply(harness.reducer.event(
        "target_invalidated",
        payload={
            "expected_binding_id": current_binding.binding_id,
            "expected_canonical_identity_sha256": (
                current_binding.canonical_identity_sha256
            ),
            "observed_frame_id": None,
            "reason": "window identity changed",
        },
    ))
    replacement, replacement_proof = _target_attestation(
        evidence_id="target-proof-2",
        acquisition_action_id="target-acquisition-2",
        hwnd=202,
        process_id=7,
        process_identity=REPLACEMENT_PROCESS_IDENTITY,
        title_snapshot="Replacement application",
        epoch=4,
    )
    harness.registry.append(replacement_proof)
    harness.reducer.apply(harness.reducer.event(
        "target_bound",
        payload={"target_binding": replacement.to_dict()},
        evidence_refs=(replacement_proof.id,),
    ))
    frame = _frame(12, hwnd=202, epoch=4)
    harness.state.post_action = _snapshot(
        frame,
        turn_index=5,
        target=TargetState(replacement.binding_id, 202, 4, True),
        runtime_target=TargetCommitIdentity(
            binding_id=replacement.binding_id,
            hwnd=replacement.hwnd,
            root_hwnd=replacement.root_hwnd,
            process_id=replacement.process_id,
            process_identity=replacement.process_identity,
            epoch=replacement.epoch,
            window_region=(0, 0, 1280, 720),
        ),
    )
    harness.actor.proposal = _live_click(630, 430)

    harness.runtime.run_one_turn(turn_index=5)

    assert len(harness.actor.calls) == 2
    assert harness.actor.calls[1][0].mission.target_title == "Replacement application"
    assert harness.actor.calls[1][0].previous_action is None


def test_previous_action_must_still_be_in_the_current_broker_tool_surface() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None
    post_frame = harness.state.post_action.frame
    assert post_frame is not None
    harness.state.post_action = _snapshot(
        post_frame,
        turn_index=5,
        pointer=PointerLeaseState(PointerDisposition.HELD, "lease-2", True),
    )
    harness.actor.proposal = _live_click(640, 440)

    result = harness.runtime.run_one_turn(turn_index=5)

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert len(harness.actor.calls) == 2
    assert "click" not in tuple(tool.name for tool in harness.actor.calls[1][0].tools)
    assert harness.actor.calls[1][0].previous_action is None


def test_history_sidecar_exception_cannot_change_or_replay_accepted_action(
    monkeypatch,
) -> None:
    harness = _harness(_click())

    def reject_history(*_args, **_kwargs):
        raise RuntimeError("optional history adapter failed")

    monkeypatch.setattr(
        mission_runtime_module,
        "try_project_action_history_args",
        reject_history,
    )
    before = len(harness.reducer.events)

    first = harness.runtime.run_one_turn(turn_index=4)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert len(harness.controller.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "transition_recorded"
    ]
    assert harness.reducer.projection.status is MissionStatus.RUNNING
    assert harness.runtime._pending_actor_action is None

    harness.actor.proposal = _click(650, 450)
    _set_harness_turn(harness, 5)
    second = harness.runtime.run_one_turn(turn_index=5)
    assert second.status is RuntimeStatus.ACTION_RECORDED
    assert harness.actor.calls[1][0].previous_action is None
    assert len(harness.controller.calls) == 2
    assert harness.controller.calls[0].decision_id != harness.controller.calls[1].decision_id


def test_actor_error_consumes_previous_action_instead_of_replaying_context() -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED
    assert harness.runtime._pending_actor_action is not None

    harness.actor.raises = RuntimeError("provider failed after request admission")
    _set_harness_turn(harness, 5)
    failed = harness.runtime.run_one_turn(turn_index=5)

    assert failed.status is RuntimeStatus.ACTOR_ERROR
    assert harness.actor.calls[1][0].previous_action is not None
    assert harness.runtime._pending_actor_action is None

    harness.actor.raises = None
    harness.actor.proposal = _claim()
    _set_harness_turn(harness, 6)
    harness.runtime.run_one_turn(turn_index=6)
    assert harness.actor.calls[2][0].previous_action is None


def test_oversized_injected_local_intent_cannot_poison_following_turn() -> None:
    proposal = _live_click()
    proposal["local_intent"] = "x" * 241
    harness = _canonical_action_harness(
        proposal, catalog=build_live_catalog(),
    )

    first = harness.runtime.run_one_turn(turn_index=4)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    harness.actor.proposal = _claim()
    _set_harness_turn(harness, 5)
    harness.runtime.run_one_turn(turn_index=5)
    assert len(harness.actor.calls) == 2
    assert harness.actor.calls[1][0].previous_action is None
    assert harness.runtime._pending_actor_action is None


def test_missing_action_effect_proof_omits_previous_action_context(
    monkeypatch,
) -> None:
    harness = _canonical_action_harness(
        _live_click(), catalog=build_live_catalog(),
    )
    monkeypatch.setattr(
        DeterministicActionEffectVerifier,
        "verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("comparison unavailable")
        ),
    )
    assert harness.runtime.run_one_turn(turn_index=4).status is RuntimeStatus.ACTION_RECORDED

    harness.actor.proposal = _claim()
    _set_harness_turn(harness, 5)
    harness.runtime.run_one_turn(turn_index=5)

    assert harness.reducer.projection.last_transition is not None
    assert harness.reducer.projection.last_transition.action_effect is None
    assert harness.actor.calls[1][0].previous_action is None


@pytest.mark.parametrize(
    "proposal,raw_values",
    (
        (_type_text("private-message-934781"), ("private-message-934781",)),
        (_hotkey("ctrl", "shift", key="f12"), ("ctrl", "shift", "f12")),
    ),
)
def test_keyboard_argument_values_omit_the_entire_previous_action(
    proposal: Mapping[str, Any],
    raw_values: tuple[str, ...],
) -> None:
    harness = _harness(proposal, catalog=build_live_catalog())
    before = len(harness.reducer.events)

    first = harness.runtime.run_one_turn(turn_index=4)

    assert first.status is RuntimeStatus.ACTION_RECORDED
    transition = harness.reducer.events[before]
    assert transition.kind == "transition_recorded"
    encoded_event = json.dumps(
        transition.to_dict(), ensure_ascii=False, sort_keys=True,
    )
    for raw in raw_values:
        assert raw not in encoded_event

    harness.actor.proposal = _claim()
    _set_harness_turn(harness, 5)
    harness.runtime.run_one_turn(turn_index=5)
    assert harness.actor.calls[1][0].previous_action is None
    assert harness.runtime._pending_actor_action is None


def test_proven_rejection_without_post_observation_is_stale_not_unknown() -> None:
    harness = _harness(_click(), controller_outcome=ControllerOutcome.REJECTED)

    def remove_post_observation(
        _envelope: ActorDecisionEnvelope,
        transition: ControllerTransition,
    ) -> ControllerTransition:
        return replace(
            transition,
            evidence_refs=(),
            post_frame_id=None,
            visible_effect="",
        )

    harness.controller.transition_mutator = remove_post_observation
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert "did not execute" in result.detail
    assert len(harness.controller.calls) == 1
    assert len(harness.reducer.events) == before
    assert harness.reducer.projection.status is MissionStatus.RUNNING


@pytest.mark.parametrize(
    "race",
    (
        "revision", "obligation", "frame", "scope", "hash", "target",
        "target_root", "target_pid", "target_process_identity",
        "target_geometry", "pointer", "grant", "broker",
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
        changed_binding, _changed_evidence = _target_attestation(
            acquisition_action_id="target-acquisition-race",
            hwnd=202,
            epoch=3,
        )
        current = _snapshot(
            _frame(9, hwnd=202),
            target=TargetState(changed_binding.binding_id, 202, 3, True),
        )
    elif race in {
        "target_root", "target_pid", "target_process_identity", "target_geometry",
    }:
        target_identity = TargetCommitIdentity(
            binding_id=_target_attestation()[0].binding_id,
            hwnd=101,
            root_hwnd=(202 if race == "target_root" else 101),
            process_id=(8 if race == "target_pid" else 7),
            process_identity=(
                REPLACEMENT_PROCESS_IDENTITY
                if race in {"target_pid", "target_process_identity"}
                else PROCESS_IDENTITY
            ),
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


@pytest.mark.parametrize(
    "arguments",
    (
        {"x": "320", "y": 240},
        {"x": 320},
        {"x": 320, "y": 240, "nearby_text": "repair me"},
    ),
)
def test_desktop_action_arguments_fail_closed_before_controller(arguments: dict) -> None:
    harness = _harness({**_click(), "args": arguments})
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.INVALID_PROPOSAL
    assert "arguments rejected" in result.detail
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert len(harness.reducer.events) == before


def test_actor_schema_error_is_recoverable_without_controller_admission() -> None:
    harness = _harness(
        _click(),
        actor_error=InvalidActorProposal("provider arguments missed the tool schema"),
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.INVALID_PROPOSAL
    assert "missed the tool schema" in result.detail
    assert len(harness.actor.calls) == 1
    assert harness.controller.calls == []
    assert harness.permits.pending_count == 0
    assert len(harness.reducer.events) == before


def test_actor_invariant_error_is_terminal_without_controller_admission() -> None:
    harness = _harness(_click(), actor_error=RuntimeError("actor invariant failed"))

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.ACTOR_ERROR
    assert "actor invariant failed" in result.detail
    assert harness.controller.calls == []
    assert harness.permits.pending_count == 0


def test_controller_cannot_report_completed_without_consuming_runtime_permit() -> None:
    harness = _harness(_click(), consume_permit=False)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.BLOCKED
    assert "without consuming Runtime permit" in result.detail
    assert len(harness.controller.calls) == 1
    assert harness.controller.permit_results == []
    assert harness.permits.pending_count == 0
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_blocked",
    ]


def test_claim_is_only_claimed_and_replays_from_accepted_event() -> None:
    harness = _harness(_claim())
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


def test_injected_verifier_requires_profile_admission_and_port() -> None:
    assessment = _verified_assessment("the requested state is visibly confirmed")
    disabled_port = ScriptedVerifier(assessment)
    profile_disabled = _harness(
        _claim(),
        obligation_verifier=disabled_port,
        verifier_enabled=False,
    )

    disabled_result = profile_disabled.runtime.run_one_turn(turn_index=4)

    assert profile_disabled.profile.services.obligation_verifier is False
    assert disabled_result.status is RuntimeStatus.VERIFICATION_REQUIRED
    assert disabled_port.calls == []
    assert [event.kind for event in profile_disabled.reducer.events[-1:]] == [
        "obligation_claimed"
    ]

    port_missing = _harness(
        _claim(),
        obligation_verifier=None,
        verifier_enabled=True,
    )
    enabled_result = port_missing.runtime.run_one_turn(turn_index=4)

    assert port_missing.profile.services.obligation_verifier is True
    assert enabled_result.status is RuntimeStatus.VERIFICATION_REQUIRED
    assert [event.kind for event in port_missing.reducer.events[-1:]] == [
        "obligation_claimed"
    ]


def test_runtime_binds_one_verifier_call_and_records_only_obligation_verification() -> None:
    base = _definition()
    forbidden = "unrelated existing content is changed"
    definition = replace(
        base,
        obligations=(replace(base.obligations[0], forbidden_effects=(forbidden,)),),
    )
    assessment = _verified_assessment(
        "the requested state is visibly confirmed",
        forbidden_effect_condition(forbidden),
    )
    verifier = ScriptedVerifier(assessment)
    harness = _harness(
        _claim(),
        definition=definition,
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(verifier.calls) == 1
    request, observed_stop = verifier.calls[0]
    assert observed_stop is None
    assert request.mission_id == "mission-1"
    assert request.run_id == "run-1"
    assert request.mission_revision == before + 1
    assert request.obligation_id == "o1"
    assert request.claim_fingerprint == harness.reducer.projection.obligation_state(
        "o1"
    ).claim_fingerprint
    assert request.acceptance_criteria == definition.obligations[0].acceptance_criteria
    assert request.forbidden_effects == (forbidden,)
    assert tuple(ref.id for ref in request.evidence_refs) == (
        "frame-proof-9-base",
    )
    assert all(ref.trust == "deterministic" for ref in request.evidence_refs)
    assert harness.controller.calls == []

    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    verification_event = accepted[1]
    assert verification_event.payload["checked_forbidden_effects"] == (forbidden,)
    assert verification_event.payload["verification_id"].startswith("verification-")
    assert set(result.accepted_event_ids) == {event.event_id for event in accepted}
    state = harness.reducer.projection.obligation_state("o1")
    assert state.status is ObligationStatus.VERIFIED
    assert state.verification_id == verification_event.payload["verification_id"]
    assert state.verified_claim_fingerprint == state.claim_fingerprint
    assert state.verification_attempt_count == 1
    assert harness.reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    assert harness.reducer.projection.mission_verification_id is None
    assert all(event.kind != "mission_verified" for event in harness.reducer.events)

    replayed = MissionReducer.replay(
        harness.reducer.definition,
        harness.reducer.events,
        evidence=harness.registry.snapshot(),
    )
    assert replayed.projection.to_dict() == harness.reducer.projection.to_dict()


def test_negative_verifier_result_consumes_exact_claim_fingerprint() -> None:
    assessment = VerifierAssessment(
        verified=False,
        satisfied_conditions=(),
        missing_conditions=("the requested state is visibly confirmed",),
        contradictions=(),
        confidence=0.8,
        concise_reason="the current frame does not prove the requested state",
    )
    verifier = ScriptedVerifier(assessment)
    harness = _harness(
        _claim(),
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(verifier.calls) == 1
    assert harness.controller.calls == []
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    event = harness.reducer.events[-1]
    assert event.payload["verified"] is False
    state = harness.reducer.projection.obligation_state("o1")
    assert state.status is ObligationStatus.BLOCKED
    assert state.verified_claim_fingerprint == state.claim_fingerprint
    assert state.verification_attempt_count == 1
    assert harness.reducer.projection.status is MissionStatus.BLOCKED
    assert harness.reducer.projection.mission_verification_id is None


@pytest.mark.parametrize("failure", ("exception", "malformed"))
def test_verifier_failure_records_false_once_and_never_retries_claim(
    failure: str,
) -> None:
    verifier = ScriptedVerifier(
        ({"verified": "true"} if failure == "malformed" else object()),
        raises=(RuntimeError("verifier unavailable") if failure == "exception" else None),
    )
    harness = _harness(
        _claim(),
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(verifier.calls) == 1
    assert harness.controller.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    assert accepted[-1].payload["verified"] is False
    assert accepted[-1].payload["failure_kind"] in {
        "verifier_error",
        "verifier_schema_error",
    }
    assert accepted[-1].evidence_refs == ()
    state = harness.reducer.projection.obligation_state("o1")
    assert state.verified_claim_fingerprint == state.claim_fingerprint
    assert state.verification_attempt_count == 1

    event_count = len(harness.reducer.events)
    harness.runtime.run_one_turn(turn_index=4)
    assert len(verifier.calls) == 1
    assert len(harness.reducer.events) == event_count


def test_positive_assessment_rejected_by_evidence_policy_fails_closed() -> None:
    base = _definition()
    definition = replace(
        base,
        obligations=(replace(
            base.obligations[0],
            evidence_policy=EvidencePolicy(
                required_evidence_kinds=("application_receipt",),
            ),
        ),),
    )
    verifier = ScriptedVerifier(
        _verified_assessment("the requested state is visibly confirmed")
    )
    harness = _harness(
        _claim(),
        definition=definition,
        obligation_verifier=verifier,
        verifier_enabled=True,
    )

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(verifier.calls) == 1
    event = harness.reducer.events[-1]
    assert event.kind == "verification_recorded"
    assert event.payload["verified"] is False
    assert event.payload["failure_kind"] == "verification_policy_rejected"
    assert event.evidence_refs == ()
    state = harness.reducer.projection.obligation_state("o1")
    assert state.verification_attempt_count == 1
    assert state.verified_claim_fingerprint == state.claim_fingerprint
    assert harness.reducer.projection.status is MissionStatus.BLOCKED
    assert harness.controller.calls == []


@pytest.mark.parametrize("evidence_race", ("removed", "replaced"))
def test_verifier_cannot_accept_evidence_that_changed_during_call(
    evidence_race: str,
) -> None:
    holder: dict[str, Harness] = {}

    def change_evidence() -> None:
        harness = holder["harness"]
        evidence_id = "frame-proof-9-base"
        if evidence_race == "removed":
            del harness.registry._refs[evidence_id]
        else:
            original = harness.registry._refs[evidence_id]
            harness.registry._refs[evidence_id] = replace(
                original,
                payload_hash="replacement-payload",
            )

    verifier = ScriptedVerifier(
        _verified_assessment("the requested state is visibly confirmed"),
        after_verify=change_evidence,
    )
    harness = _harness(
        _claim(),
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    holder["harness"] = harness
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(verifier.calls) == 1
    assert harness.controller.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    assert accepted[-1].payload["verified"] is False
    assert accepted[-1].payload["failure_kind"] == "verification_evidence_stale"
    assert accepted[-1].evidence_refs == ()
    state = harness.reducer.projection.obligation_state("o1")
    assert state.verified_claim_fingerprint == state.claim_fingerprint
    assert state.verification_attempt_count == 1


def test_verifier_result_is_discarded_if_claim_revision_changes_during_call() -> None:
    holder: dict[str, Harness] = {}

    def advance_revision() -> None:
        reducer = holder["harness"].reducer
        reducer.apply(reducer.event(
            "obligation_blocked",
            payload={
                "obligation_id": "o1",
                "blocker_kind": "external_state_changed",
                "reason": "a concurrent accepted event changed the claim state",
            },
        ))

    verifier = ScriptedVerifier(
        _verified_assessment("the requested state is visibly confirmed"),
        after_verify=advance_revision,
    )
    harness = _harness(
        _claim(),
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    holder["harness"] = harness
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert len(verifier.calls) == 1
    assert harness.controller.calls == []
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_claimed",
        "obligation_blocked",
    ]
    assert all(event.kind != "verification_recorded" for event in harness.reducer.events)


def test_stop_during_verifier_discards_late_result_without_verification_event() -> None:
    stop = Event()
    verifier = ScriptedVerifier(
        _verified_assessment("the requested state is visibly confirmed"),
        after_verify=stop.set,
    )
    harness = _harness(
        _claim(),
        obligation_verifier=verifier,
        verifier_enabled=True,
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert len(verifier.calls) == 1
    assert verifier.calls[0][1] is stop
    assert harness.controller.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == ["obligation_claimed"]
    assert set(result.accepted_event_ids) == {accepted[0].event_id}
    assert harness.reducer.projection.obligation_state("o1").status is ObligationStatus.CLAIMED
    assert all(event.kind != "verification_recorded" for event in harness.reducer.events)


@pytest.mark.parametrize("evidence_mode", ("current_frame", "post_action"))
def test_attested_visual_verifier_commits_only_real_visual_evidence_and_replays(
    evidence_mode: str,
) -> None:
    harness = _visual_harness(evidence_mode=evidence_mode)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(harness.model.calls) == 1
    assert harness.controller.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    claim_event, verification_event = accepted
    assert claim_event.evidence_refs == harness.frame.evidence_refs
    assert len(verification_event.evidence_refs) == 1
    visual_id = verification_event.evidence_refs[0]
    assert visual_id not in claim_event.evidence_refs
    visual_ref = harness.reducer.evidence_scope.require((visual_id,))[0]
    assert visual_ref.trust == "independent_visual_model"
    assert visual_ref.kind == "visual_verifier_assessment"
    assert verification_event.payload["verifier_kind"] == "independent_visual_model"
    assert verification_event.payload["evidence_refs"] == (visual_id,)
    state = harness.reducer.projection.obligation_state("o1")
    assert state.status is ObligationStatus.VERIFIED
    assert state.claim_evidence_refs == harness.frame.evidence_refs
    assert state.verified_evidence_refs == (visual_id,)
    frame_ref = harness.reducer.evidence_scope.require(harness.frame.evidence_refs)[0]
    if evidence_mode == "current_frame":
        assert frame_ref.kind == CURRENT_FRAME_EVIDENCE_KIND
        assert frame_ref.action_id is None
    else:
        assert frame_ref.kind == POST_ACTION_FRAME_EVIDENCE_KIND
        assert frame_ref.action_id == "decision-before-claim"

    replayed = MissionReducer.replay(
        harness.reducer.definition,
        harness.reducer.events,
        evidence=harness.registry,
    )
    assert replayed.projection.to_dict() == harness.reducer.projection.to_dict()
    event_count = len(harness.reducer.events)
    harness.runtime.run_one_turn(turn_index=5)
    assert len(harness.model.calls) == 1
    assert len(harness.reducer.events) == event_count


def test_visual_policy_denial_fails_before_model_and_consumes_claim_once() -> None:
    harness = _visual_harness(allow_visual=False)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert harness.model.calls == []
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    assert accepted[-1].payload["verified"] is False
    assert accepted[-1].payload["failure_kind"] == "visual_verifier_not_permitted"
    assert accepted[-1].evidence_refs == ()
    assert all(
        ref.trust != "independent_visual_model"
        for ref in harness.registry.snapshot()
    )
    harness.runtime.run_one_turn(turn_index=5)
    assert harness.model.calls == []


def test_stale_frame_discards_visual_result_without_verification_or_evidence() -> None:
    holder: dict[str, VisualHarness] = {}

    def change_frame() -> None:
        harness = holder["harness"]
        harness.state.current_snapshot = _snapshot(_frame(11, salt="replacement"))

    harness = _visual_harness(model_hook=change_frame)
    holder["harness"] = harness
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert len(harness.model.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_claimed"
    ]
    assert all(
        ref.trust != "independent_visual_model"
        for ref in harness.registry.snapshot()
    )


@pytest.mark.parametrize("failure", ("malformed", "exception", "append_conflict"))
def test_visual_failure_is_recorded_once_and_enters_uncertain_policy(
    failure: str,
    monkeypatch,
) -> None:
    from winpilot.vnext.verification import visual_model as visual_model_module

    if failure == "append_conflict":
        def reject_append(*_args, **_kwargs):
            raise ValueError("conflicting visual evidence identity")

        monkeypatch.setattr(
            visual_model_module,
            "append_visual_verifier_evidence",
            reject_append,
        )

    response: VerifierAssessment | Mapping[str, Any] | object = {
        **_verified_assessment(
            "the requested state is visibly confirmed"
        ).to_dict(),
        "mission_id": "invented",
    }
    harness = _visual_harness(
        response=(response if failure == "malformed" else None),
        model_error=(RuntimeError("model rejected") if failure == "exception" else None),
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(harness.model.calls) == 1
    accepted = harness.reducer.events[before:]
    assert [event.kind for event in accepted] == [
        "obligation_claimed",
        "verification_recorded",
    ]
    assert accepted[-1].payload["verified"] is False
    assert accepted[-1].payload["failure_kind"] in {
        "visual_verifier_error",
        "visual_evidence_append_failed",
    }
    assert accepted[-1].evidence_refs == ()
    assert harness.reducer.projection.status is MissionStatus.BLOCKED
    event_count = len(harness.reducer.events)
    harness.runtime.run_one_turn(turn_index=5)
    assert len(harness.model.calls) == 1
    assert len(harness.reducer.events) == event_count


def test_negative_visual_assessment_uses_real_evidence_and_fail_policy() -> None:
    assessment = VerifierAssessment(
        verified=False,
        satisfied_conditions=(),
        missing_conditions=("the requested state is visibly confirmed",),
        contradictions=(),
        confidence=0.9,
        concise_reason="the visible result is not present",
    )
    harness = _visual_harness(
        response=assessment,
        uncertain_policy="fail",
    )
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.VERIFICATION_RECORDED
    assert len(harness.model.calls) == 1
    event = harness.reducer.events[before + 1]
    assert event.kind == "verification_recorded"
    assert event.payload["verified"] is False
    assert event.payload["verifier_kind"] == "independent_visual_model"
    assert len(event.evidence_refs) == 1
    assert harness.reducer.evidence_scope.require(event.evidence_refs)[0].trust == (
        "independent_visual_model"
    )
    assert harness.reducer.projection.status is MissionStatus.FAILED
    harness.runtime.run_one_turn(turn_index=5)
    assert len(harness.model.calls) == 1


def test_stop_during_visual_verifier_accepts_only_the_claim() -> None:
    stop = Event()
    harness = _visual_harness(model_hook=stop.set)
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert len(harness.model.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_claimed"
    ]
    assert all(
        ref.trust != "independent_visual_model"
        for ref in harness.registry.snapshot()
    )


def test_visual_and_generic_verifiers_cannot_be_injected_together() -> None:
    harness = _visual_harness()
    common = dict(
        reducer=harness.reducer,
        profile=harness.profile,
        broker=CapabilityBroker(harness.catalog),
        catalog=harness.catalog,
        actor=harness.actor,
        state=harness.state,
        controller=harness.controller,
        mutation_permits=harness.permits,
    )
    generic = ScriptedVerifier(_verified_assessment(
        "the requested state is visibly confirmed"
    ))

    with pytest.raises(TypeError, match="mutually exclusive"):
        MissionRuntime(
            **common,
            obligation_verifier=generic,
            attested_visual_verifier=harness.adapter,
            evidence_registry=harness.registry,
        )
    with pytest.raises(TypeError, match="injected together"):
        MissionRuntime(
            **common,
            attested_visual_verifier=harness.adapter,
        )


def test_mission_completion_requires_concrete_verifier_and_shared_registry() -> None:
    harness = _harness(_claim())
    verifier = DeterministicMissionCompletionVerifier()
    canonical = CanonicalEvidenceRegistry("mission-1", "run-1")
    common = dict(
        reducer=harness.reducer,
        profile=harness.profile,
        broker=CapabilityBroker(harness.catalog),
        catalog=harness.catalog,
        actor=harness.actor,
        state=harness.state,
        controller=harness.controller,
        mutation_permits=harness.permits,
    )

    with pytest.raises(TypeError, match="injected together"):
        MissionRuntime(**common, mission_completion_verifier=verifier)
    with pytest.raises(TypeError, match="share one EvidenceRegistry"):
        MissionRuntime(**common, evidence_registry=canonical)
    runtime = MissionRuntime(
        **common,
        mission_completion_verifier=verifier,
        evidence_registry=harness.registry,
    )
    assert runtime.reducer is harness.reducer
    with pytest.raises(TypeError, match="share one EvidenceRegistry"):
        MissionRuntime(
            **common,
            mission_completion_verifier=verifier,
            evidence_registry=canonical,
        )

    class PretendVerifier(DeterministicMissionCompletionVerifier):
        pass

    completed = _completion_harness()
    catalog = _catalog()
    with pytest.raises(TypeError, match="DeterministicMissionCompletionVerifier"):
        MissionRuntime(
            reducer=completed.reducer,
            profile=completed.profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=completed.actor,
            state=completed.state,
            controller=harness.controller,
            mission_completion_verifier=PretendVerifier(),
            evidence_registry=completed.registry,
        )


def test_action_effect_verifier_requires_the_reducer_shared_registry() -> None:
    harness = _harness(_click())
    common = dict(
        reducer=harness.reducer,
        profile=harness.profile,
        broker=CapabilityBroker(harness.catalog),
        catalog=harness.catalog,
        actor=harness.actor,
        state=harness.state,
        controller=harness.controller,
        mutation_permits=harness.permits,
        action_effect_verifier=DeterministicActionEffectVerifier(),
    )

    with pytest.raises(TypeError, match="injected together"):
        MissionRuntime(**common)
    with pytest.raises(TypeError, match="share one EvidenceRegistry"):
        MissionRuntime(
            **common,
            evidence_registry=CanonicalEvidenceRegistry("mission-1", "run-1"),
        )


def test_awaiting_mission_is_verified_before_state_broker_or_actor() -> None:
    harness = _completion_harness()
    assert harness.profile.services.mission_verifier is False
    before_events = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert harness.state.observe_calls == []
    assert harness.state.current_calls == []
    assert harness.actor.calls == []
    assert len(harness.reducer.events) == before_events + 1
    event = harness.reducer.events[-1]
    assert event.kind == "mission_verified"
    aggregate_id = event.payload["aggregate_evidence_id"]
    aggregate_hash = event.payload["aggregate_payload_hash"]
    aggregate = harness.reducer.evidence_scope.require((aggregate_id,))[0]
    assert aggregate.kind == "mission_completion_aggregate"
    assert aggregate.trust == "deterministic"
    assert aggregate.payload_hash == aggregate_hash
    assert event.evidence_refs == ("frame-proof-9-base", aggregate_id)
    assert harness.reducer.projection.status is MissionStatus.VERIFIED

    replayed = MissionReducer.replay(
        harness.reducer.definition,
        harness.reducer.events,
        evidence=harness.registry,
    )
    assert replayed.projection.to_dict() == harness.reducer.projection.to_dict()


def test_last_obligation_verification_reuses_completion_helper_without_profile_gate() -> None:
    harness = _completion_harness(awaiting=False)
    assert harness.profile.services.obligation_verifier is True
    assert harness.profile.services.mission_verifier is False
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.MISSION_VERIFIED
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_claimed",
        "verification_recorded",
        "mission_verified",
    ]
    assert len(harness.actor.calls) == 1
    assert harness.reducer.projection.status is MissionStatus.VERIFIED
    assert set(result.accepted_event_ids) == {
        event.event_id for event in harness.reducer.events[before:]
    }


def test_completion_event_apply_failure_recovers_from_same_orphan_aggregate() -> None:
    harness = _completion_harness(fail_once=True)
    before_registry = len(harness.registry)
    before_events = len(harness.reducer.events)

    first = harness.runtime.run_one_turn(turn_index=4)

    assert first.status is RuntimeStatus.STATE_REJECTED
    assert "simulated event persistence failure" in first.detail
    assert len(harness.registry) == before_registry + 1
    assert len(harness.reducer.events) == before_events
    assert harness.reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    assert harness.actor.calls == []
    assert harness.state.observe_calls == []

    second = harness.runtime.run_one_turn(turn_index=5)

    assert second.status is RuntimeStatus.MISSION_VERIFIED
    assert len(harness.registry) == before_registry + 1
    assert harness.reducer.projection.status is MissionStatus.VERIFIED
    assert [event.kind for event in harness.reducer.events[before_events:]] == [
        "mission_verified"
    ]


@pytest.mark.parametrize("race", ("exception", "stale", "canceled"))
def test_completion_failure_never_commits_a_late_or_invalid_result(race: str) -> None:
    harness = _completion_harness()
    original = harness.verifier.verify
    stop = Event()

    def raced(request):
        if race == "exception":
            raise RuntimeError("completion verifier failed")
        assessment = original(request)
        if race == "stale":
            harness.reducer._projection = replace(
                harness.reducer.projection,
                revision=harness.reducer.projection.revision + 1,
            )
        else:
            stop.set()
        return assessment

    harness.verifier.verify = raced
    before_registry = len(harness.registry)
    before_events = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(
        turn_index=4,
        stop_event=(stop if race == "canceled" else None),
    )

    expected = {
        "exception": RuntimeStatus.STATE_REJECTED,
        "stale": RuntimeStatus.STALE_DISCARDED,
        "canceled": RuntimeStatus.CANCELED,
    }[race]
    assert result.status is expected
    assert len(harness.registry) == before_registry
    assert len(harness.reducer.events) == before_events
    assert harness.actor.calls == []


def test_orphan_evidence_is_not_added_to_completion_event_and_terminal_is_idempotent() -> None:
    harness = _completion_harness()
    orphan = EvidenceRef(
        id="unrelated-mission-proof",
        mission_id="mission-1",
        run_id="run-1",
        kind="oracle",
        trust="deterministic",
        payload_hash="unrelated",
    )
    harness.registry.append(orphan)

    first = harness.runtime.run_one_turn(turn_index=4)

    assert first.status is RuntimeStatus.MISSION_VERIFIED
    assert orphan.id not in harness.reducer.events[-1].evidence_refs
    event_count = len(harness.reducer.events)
    registry_count = len(harness.registry)

    second = harness.runtime.run_one_turn(turn_index=5)

    assert second.status is RuntimeStatus.MISSION_VERIFIED
    assert len(harness.reducer.events) == event_count
    assert len(harness.registry) == registry_count
    assert harness.actor.calls == []


@pytest.mark.parametrize("with_completion", (False, True))
@pytest.mark.parametrize(
    ("terminal", "expected"),
    (
        (MissionStatus.VERIFIED, RuntimeStatus.MISSION_VERIFIED),
        (MissionStatus.FAILED, RuntimeStatus.STATE_REJECTED),
        (MissionStatus.CANCELED, RuntimeStatus.STATE_REJECTED),
    ),
)
def test_terminal_mission_never_observes_or_mutates_desktop(
    with_completion: bool,
    terminal: MissionStatus,
    expected: RuntimeStatus,
) -> None:
    harness = _completion_harness(awaiting=terminal is MissionStatus.VERIFIED)
    if terminal is MissionStatus.VERIFIED:
        assert harness.runtime.run_one_turn(turn_index=3).status is RuntimeStatus.MISSION_VERIFIED
    else:
        harness.reducer.apply(harness.reducer.event(
            f"mission_{terminal.value}", payload={"reason": "terminal test"}
        ))
    if with_completion:
        runtime = harness.runtime
    else:
        runtime = MissionRuntime(
            reducer=harness.reducer,
            profile=harness.profile,
            broker=CapabilityBroker(harness.catalog),
            catalog=harness.catalog,
            actor=harness.actor,
            state=harness.state,
            controller=harness.controller,
            mutation_permits=harness.permits,
        )
    observe_count = len(harness.state.observe_calls)
    current_count = len(harness.state.current_calls)
    actor_count = len(harness.actor.calls)
    controller_count = len(harness.controller.calls)
    event_count = len(harness.reducer.events)

    result = runtime.run_one_turn(turn_index=4)

    assert result.status is expected
    assert len(harness.state.observe_calls) == observe_count
    assert len(harness.state.current_calls) == current_count
    assert len(harness.actor.calls) == actor_count
    assert len(harness.controller.calls) == controller_count
    assert len(harness.reducer.events) == event_count


@pytest.mark.parametrize("invalid_scope", ("missing", "other_run", "other_obligation"))
def test_claim_rejects_missing_or_out_of_scope_frame_evidence(invalid_scope: str) -> None:
    harness = _harness({
        "format": "actor-proposal/1",
        "kind": "claim_obligation_done",
        "concise_claim": "the current state appears complete",
    })
    evidence_id = harness.state.initial.frame.evidence_refs[0]
    if invalid_scope == "missing":
        frame = replace(harness.state.initial.frame, evidence_refs=())
        snapshot = replace(harness.state.initial, frame=frame)
        harness.state.initial = harness.state.current_snapshot = snapshot
    else:
            original = harness.registry._refs[evidence_id]
            harness.registry._refs[evidence_id] = replace(
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
        "format": "actor-proposal/1",
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
        "format": "actor-proposal/1",
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
        "format": "actor-proposal/1",
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
            "format": "actor-proposal/1",
            "kind": "request_inspection",
            "region": {"x": 0, "y": 0, "width": 200, "height": 200},
            "question": "Describe the local geometry",
        },
        {
            "format": "actor-proposal/1",
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


@pytest.mark.parametrize(
    "mismatch",
    ("action_id", "frame_id", "obligation_id", "trust", "post_frame_scope"),
)
def test_controller_evidence_must_bind_exact_transition_identity(mismatch: str) -> None:
    harness = _harness(_click())

    def corrupt(
        envelope: ActorDecisionEnvelope,
        transition: ControllerTransition,
    ) -> ControllerTransition:
        evidence_id = transition.evidence_refs[0]
        if mismatch == "post_frame_scope":
            post_frame = harness.state.post_action.frame
            assert post_frame is not None
            harness.state.post_action = replace(
                harness.state.post_action,
                frame=replace(
                    post_frame,
                    evidence_refs=tuple(
                        ref for ref in post_frame.evidence_refs if ref != evidence_id
                    ),
                ),
            )
            return transition
        ref = harness.registry._refs[evidence_id]
        changes = {
            "action_id": "other-decision",
            "frame_id": 999,
            "obligation_id": "other-obligation",
            "trust": "model_visual",
        }
        harness.registry._refs[evidence_id] = replace(
            ref,
            **{mismatch: changes[mismatch]},
        )
        return transition

    harness.controller.transition_mutator = corrupt
    before = len(harness.reducer.events)

    result = harness.runtime.run_one_turn(turn_index=4)

    assert result.status is RuntimeStatus.BLOCKED
    assert len(harness.controller.calls) == 1
    assert [event.kind for event in harness.reducer.events[before:]] == [
        "obligation_blocked",
    ]


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
