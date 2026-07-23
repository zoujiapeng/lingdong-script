"""Read-only target discovery sidecar tests for MissionRuntime."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from threading import Event
from typing import Any, Mapping

import pytest

from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT
from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerRuntimeState,
    CapabilityBroker,
    MutationKind,
    TargetState,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from winpilot.vnext.evidence import (
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    frame_identity_payload_hash,
)
from winpilot.vnext.mission import (
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
)
from winpilot.vnext.profiling import ExecutionPolicy
from winpilot.vnext.runtime import (
    MissionRuntime,
    MutationPermitAuthority,
    RuntimeFrame,
    RuntimeSnapshot,
    RuntimeStatus,
    TargetActivationEnvelope,
    TargetActivationOutcome,
    TargetActivationTransition,
    TargetLaunchEnvelope,
    TargetLaunchOutcome,
    TargetLaunchTransition,
)
from winpilot.vnext.runtime.target_activation import ActivatedTargetIdentity
from winpilot.vnext.targeting import (
    ACTIVATE_TARGET_TOOL_NAME,
    DISCOVER_TARGET_TOOL_NAME,
    TargetActivation,
    TargetCandidate,
    TargetDiscoverySnapshot,
    TargetState as CandidateState,
    activate_target_tool_schema,
    candidate_ref_for,
    discover_target_tool_schema,
    launch_identity_for,
)
from winpilot.visual_runtime.commit_admission import TargetCommitIdentity


class _Actor:
    def __init__(self, *responses: Mapping[str, Any]) -> None:
        self._responses = list(responses)
        self.turns = []

    def propose(self, turn, **_kwargs):
        self.turns.append(turn)
        return dict(self._responses.pop(0))


class _State:
    def __init__(self, snapshots: Mapping[int, RuntimeSnapshot]) -> None:
        self.snapshots = dict(snapshots)
        self.current_override: RuntimeSnapshot | None = None
        self.observe_calls = []
        self.current_calls = []

    def observe(self, _projection, *, turn_index: int, image_region=None):
        self.observe_calls.append((turn_index, image_region))
        return self.snapshots[turn_index]

    def current(self, _projection, *, turn_index: int):
        self.current_calls.append(turn_index)
        return self.current_override or self.snapshots[turn_index]


class _Controller:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, envelope, **_kwargs):
        self.calls.append(envelope)
        raise AssertionError("read-only target discovery reached Controller")


class _Discovery:
    def __init__(self, snapshot: TargetDiscoverySnapshot, *, hook=None) -> None:
        self.snapshot = snapshot
        self.hook = hook
        self.calls = []

    def discover(self, query: str, *, stop_event=None) -> TargetDiscoverySnapshot:
        self.calls.append((query, stop_event))
        if self.hook is not None:
            self.hook()
        return self.snapshot


def _definition() -> MissionDefinition:
    return MissionDefinition(
        mission_id="mission-target-discovery",
        run_id="run-target-discovery",
        original_goal="Open the requested desktop application",
        obligations=(
            ObligationSpec(
                id="open-target",
                desired_state="the requested application is ready",
                acceptance_criteria=("the requested application is visible",),
                required_capabilities=("visual.interact",),
            ),
        ),
    )


def _frame(frame_id: int) -> RuntimeFrame:
    image_b64 = base64.b64encode(f"desktop-{frame_id}".encode()).decode("ascii")
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=0,
        hwnd=1,
        region=(0, 0, 1280, 720),
        scope="full_desktop",
        submitted_size=(1280, 720),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
    )


def _snapshot(frame_id: int, *, turn_index: int) -> RuntimeSnapshot:
    frame = _frame(frame_id)
    return RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=turn_index,
            available_capabilities=("visual.interact",),
            target=TargetState(),
            frame=AuthoritativeFrameState(
                frame_id=frame.frame_id,
                hwnd=frame.hwnd,
                epoch=frame.epoch,
                scope=frame.scope,
                authoritative=True,
                fresh=True,
            ),
        ),
        frame=frame,
        target=None,
    )


def _candidate() -> TargetCandidate:
    target = r"C:\Apps\Orbit.exe"
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation=TargetActivation.OPEN_APP,
            identity=launch_identity_for(kind="exe", target=target),
        ),
        activation=TargetActivation.OPEN_APP,
        state=CandidateState.INSTALLED,
        label="Orbit",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=target,
    )


def _window_candidate(
    hwnd: int = 404,
    *,
    process_id: int = 77,
    process_identity: str = f"process-{'4' * 32}",
    label: str = "Orbit",
) -> TargetCandidate:
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation=TargetActivation.SWITCH_WINDOW,
            identity=f"{hwnd}|{process_id}|{process_identity}",
        ),
        activation=TargetActivation.SWITCH_WINDOW,
        state=CandidateState.VISIBLE,
        label=label,
        provenance=("visible_window",),
        window_title=f"{label} document",
        hwnd=hwnd,
        process_id=process_id,
        process_identity=process_identity,
    )


class _ActivationState(_State):
    def __init__(self, snapshots: Mapping[int, RuntimeSnapshot]) -> None:
        super().__init__(snapshots)
        self.activated_target: ActivatedTargetIdentity | None = None
        self.post_frame: RuntimeFrame | None = None
        self.post_authoritative = True
        self.post_fresh = True

    def set_activated(
        self,
        target: ActivatedTargetIdentity,
        frame: RuntimeFrame,
    ) -> None:
        self.activated_target = target
        self.post_frame = frame

    def current(self, projection, *, turn_index: int):
        self.current_calls.append(turn_index)
        if self.activated_target is None:
            return self.current_override or self.snapshots[turn_index]
        binding = projection.target_binding
        if binding is None:
            raise AssertionError(
                "post-activation authority must be probed with a local binding"
            )
        target = self.activated_target
        frame = self.post_frame
        assert frame is not None
        return RuntimeSnapshot(
            broker_state=BrokerRuntimeState(
                turn_index=turn_index,
                available_capabilities=("visual.interact",),
                target=TargetState(
                    binding_id=binding.binding_id,
                    hwnd=target.hwnd,
                    epoch=frame.epoch,
                    live=True,
                ),
                frame=AuthoritativeFrameState(
                    frame_id=frame.frame_id,
                    hwnd=frame.hwnd,
                    epoch=frame.epoch,
                    scope=frame.scope,
                    authoritative=self.post_authoritative,
                    fresh=self.post_fresh,
                ),
            ),
            frame=frame,
            target=TargetCommitIdentity(
                binding_id=binding.binding_id,
                hwnd=target.hwnd,
                root_hwnd=target.root_hwnd,
                process_id=target.process_id,
                process_identity=target.process_identity,
                epoch=frame.epoch,
                window_region=target.window_region,
            ),
        )


def _record_completed_target(
    state: _ActivationState,
    evidence_registry: EvidenceRegistry,
    envelope: TargetActivationEnvelope | TargetLaunchEnvelope,
    target: ActivatedTargetIdentity,
    *,
    payload_hash_override: str | None = None,
) -> RuntimeFrame:
    raw = base64.b64encode(f"activated:{target.hwnd}".encode()).decode("ascii")
    post = RuntimeFrame(
        frame_id=30,
        epoch=0,
        hwnd=target.hwnd,
        region=target.window_region,
        scope="target_window",
        submitted_size=(900, 600),
        image_b64=raw,
        image_sha256=hashlib.sha256(raw.encode("ascii")).hexdigest(),
    )
    evidence = EvidenceRef(
        id=f"post-action-{envelope.decision_id}",
        mission_id=envelope.mission_id,
        run_id=envelope.run_id,
        obligation_id=envelope.obligation_id,
        kind=POST_ACTION_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=(
            payload_hash_override
            or frame_identity_payload_hash(post.commit_identity)
        ),
        frame_id=post.frame_id,
        action_id=envelope.decision_id,
    )
    evidence_registry.append(evidence)
    post = replace(post, evidence_refs=(evidence.id,))
    state.set_activated(target, post)
    return post


class _ActivationController:
    def __init__(
        self,
        state: _ActivationState,
        evidence: EvidenceRegistry,
        permits: MutationPermitAuthority,
        *,
        outcome: TargetActivationOutcome = TargetActivationOutcome.COMPLETED,
        candidate_ref_override: str | None = None,
        evidence_hash_override: str | None = None,
        consume_permit: bool = True,
    ) -> None:
        self.state = state
        self.evidence = evidence
        self.permits = permits
        self.outcome = outcome
        self.candidate_ref_override = candidate_ref_override
        self.evidence_hash_override = evidence_hash_override
        self.consume_permit = consume_permit
        self.calls: list[TargetActivationEnvelope] = []
        self.permit_results = []
        self.saw_active_admission = False
        self.after_execute = None
        self.transition_mutator = None

    def execute(self, envelope, **_kwargs):
        assert isinstance(envelope, TargetActivationEnvelope)
        self.calls.append(envelope)
        permit = (
            self.permits.begin_admission(envelope)
            if self.consume_permit else None
        )
        if permit is not None:
            self.permit_results.append(permit)
            if permit.allowed:
                self.saw_active_admission = (
                    self.permits.active_activation_admission_matches(
                        envelope.decision_id,
                        frame=envelope.frame,
                        pointer=envelope.pointer,
                        candidate_fingerprint=envelope.candidate_fingerprint,
                        candidate=envelope.candidate,
                    )
                )
        if permit is not None and not permit.allowed:
            return TargetActivationTransition(
                outcome=TargetActivationOutcome.REJECTED,
                action_id=envelope.decision_id,
                candidate_ref=envelope.candidate.candidate_ref,
                mutation_completed=False,
                activated_target=None,
                post_frame_id=None,
                evidence_refs=(),
                failure_reason=permit.reason.value,
            )
        if self.outcome is TargetActivationOutcome.REJECTED:
            return TargetActivationTransition(
                outcome=self.outcome,
                action_id=envelope.decision_id,
                candidate_ref=envelope.candidate.candidate_ref,
                mutation_completed=False,
                activated_target=None,
                post_frame_id=None,
                evidence_refs=(),
                failure_reason="activation_not_applied",
            )
        if self.outcome is TargetActivationOutcome.UNKNOWN:
            return TargetActivationTransition(
                outcome=self.outcome,
                action_id=envelope.decision_id,
                candidate_ref=envelope.candidate.candidate_ref,
                mutation_completed=None,
                activated_target=None,
                post_frame_id=None,
                evidence_refs=(),
                failure_reason="activation_outcome_unknown",
            )
        candidate = envelope.candidate
        assert candidate.hwnd is not None
        assert candidate.process_id is not None
        assert candidate.process_identity is not None
        activated = ActivatedTargetIdentity(
            hwnd=candidate.hwnd,
            root_hwnd=candidate.hwnd,
            process_id=candidate.process_id,
            process_identity=candidate.process_identity,
            window_region=(40, 50, 900, 600),
            title_snapshot=candidate.window_title,
        )
        post = _record_completed_target(
            self.state,
            self.evidence,
            envelope,
            activated,
            payload_hash_override=self.evidence_hash_override,
        )
        transition = TargetActivationTransition(
            outcome=self.outcome,
            action_id=envelope.decision_id,
            candidate_ref=(
                self.candidate_ref_override
                or candidate.candidate_ref
            ),
            mutation_completed=True,
            activated_target=activated,
            post_frame_id=post.frame_id,
            evidence_refs=post.evidence_refs,
            failure_reason="",
        )
        if self.after_execute is not None:
            self.after_execute()
        return (
            self.transition_mutator(transition)
            if self.transition_mutator is not None
            else transition
        )


class _LaunchController:
    def __init__(
        self,
        state: _ActivationState,
        evidence: EvidenceRegistry,
        permits: MutationPermitAuthority,
        *,
        outcome: TargetLaunchOutcome = TargetLaunchOutcome.COMPLETED,
        consume_permit: bool = True,
        candidate_ref_override: str | None = None,
        evidence_hash_override: str | None = None,
    ) -> None:
        self.state = state
        self.evidence = evidence
        self.permits = permits
        self.outcome = outcome
        self.consume_permit = consume_permit
        self.candidate_ref_override = candidate_ref_override
        self.evidence_hash_override = evidence_hash_override
        self.calls: list[TargetLaunchEnvelope] = []
        self.saw_active_admission = False
        self.raise_after_admission = False
        self.transition_mutator = None

    def execute(self, envelope, **_kwargs):
        assert isinstance(envelope, TargetLaunchEnvelope)
        self.calls.append(envelope)
        permit = (
            self.permits.begin_admission(envelope)
            if self.consume_permit else None
        )
        if permit is not None and permit.allowed:
            self.saw_active_admission = (
                self.permits.active_launch_admission_matches(
                    envelope.decision_id,
                    frame=envelope.frame,
                    pointer=envelope.pointer,
                    candidate_fingerprint=envelope.candidate_fingerprint,
                    candidate=envelope.candidate,
                )
            )
        if self.raise_after_admission:
            raise RuntimeError("injected launch controller failure")
        if (
            permit is not None and not permit.allowed
        ) or self.outcome is TargetLaunchOutcome.REJECTED:
            return TargetLaunchTransition(
                outcome=TargetLaunchOutcome.REJECTED,
                action_id=envelope.decision_id,
                candidate_ref=envelope.candidate.candidate_ref,
                launch_request_count=0,
                launch_process_id=None,
                launch_process_identity=None,
                activated_target=None,
                post_frame_id=None,
                evidence_refs=(),
                failure_reason=(
                    permit.reason.value
                    if permit is not None and not permit.allowed
                    else "launch_not_applied"
                ),
            )
        if self.outcome is TargetLaunchOutcome.UNKNOWN:
            return TargetLaunchTransition(
                outcome=self.outcome,
                action_id=envelope.decision_id,
                candidate_ref=envelope.candidate.candidate_ref,
                launch_request_count=1,
                launch_process_id=None,
                launch_process_identity=None,
                activated_target=None,
                post_frame_id=None,
                evidence_refs=(),
                failure_reason="launch_outcome_unknown",
            )
        activated = ActivatedTargetIdentity(
            hwnd=505,
            root_hwnd=505,
            process_id=707,
            process_identity=f"process-{'7' * 32}",
            window_region=(40, 50, 900, 600),
            title_snapshot="Orbit",
        )
        post = _record_completed_target(
            self.state,
            self.evidence,
            envelope,
            activated,
            payload_hash_override=self.evidence_hash_override,
        )
        transition = TargetLaunchTransition(
            outcome=self.outcome,
            action_id=envelope.decision_id,
            candidate_ref=(
                self.candidate_ref_override
                or envelope.candidate.candidate_ref
            ),
            launch_request_count=1,
            launch_process_id=activated.process_id,
            launch_process_identity=activated.process_identity,
            activated_target=activated,
            post_frame_id=post.frame_id,
            evidence_refs=post.evidence_refs,
            failure_reason="",
        )
        return (
            self.transition_mutator(transition)
            if self.transition_mutator is not None
            else transition
        )


def _catalog() -> ToolCatalog:
    common = (
        "target_unbound",
        "authoritative_full_desktop_frame",
        "pointer_released",
        "runtime_healthy",
    )
    return ToolCatalog((
        ToolSpec(
            DISCOVER_TARGET_TOOL_NAME,
            discover_target_tool_schema(),
            ToolRoute.VISUAL_ACTOR,
            MutationKind.NONE,
            required_any_capabilities=("visual.interact",),
            prerequisites=common,
            order=1,
        ),
        ToolSpec(
            ACTIVATE_TARGET_TOOL_NAME,
            activate_target_tool_schema(),
            ToolRoute.VISUAL_ACTOR,
            MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=(*common, "target_candidates_available"),
            order=2,
        ),
    ))


def _discover(query: str = "Orbit") -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "discover_target",
        "query": query,
    }


def _activate(ordinal: int = 1) -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": ACTIVATE_TARGET_TOOL_NAME,
        "args": {"candidate_ordinal": ordinal},
        "local_intent": "make the selected application available",
    }


def _runtime(
    *,
    actor: _Actor,
    state: _State,
    discovery: _Discovery | None,
):
    definition = _definition()
    evidence = EvidenceRegistry(definition.mission_id, definition.run_id)
    reducer = MissionReducer(definition, evidence=evidence)
    catalog = _catalog()
    permits = MutationPermitAuthority(definition.mission_id, definition.run_id)
    controller = _Controller()
    profile = ExecutionPolicy().safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=("visual.interact",),
    )
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        target_discovery=discovery,
        mutation_permits=permits,
    )
    return runtime, reducer, evidence, permits, controller


def _target_acquisition_runtime(
    candidates: tuple[TargetCandidate, ...],
    *,
    actor: _Actor,
    controller_factory,
):
    definition = _definition()
    evidence = EvidenceRegistry(definition.mission_id, definition.run_id)
    reducer = MissionReducer(definition, evidence=evidence)
    catalog = _catalog()
    permits = MutationPermitAuthority(
        definition.mission_id,
        definition.run_id,
    )
    state = _ActivationState({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
        2: _snapshot(12, turn_index=2),
    })
    controller = controller_factory(state, evidence, permits)
    profile = ExecutionPolicy().safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=("visual.interact",),
    )
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        target_discovery=_Discovery(TargetDiscoverySnapshot(
            query="Orbit",
            candidates=candidates,
            captured_at=1.0,
        )),
        mutation_permits=permits,
    )
    return runtime, reducer, evidence, permits, state, controller


def _activation_runtime(
    candidates: tuple[TargetCandidate, ...],
    *,
    actor: _Actor | None = None,
    outcome: TargetActivationOutcome = TargetActivationOutcome.COMPLETED,
    candidate_ref_override: str | None = None,
    evidence_hash_override: str | None = None,
    consume_permit: bool = True,
):
    return _target_acquisition_runtime(
        candidates,
        actor=actor or _Actor(_discover(), _activate()),
        controller_factory=lambda state, evidence, permits: _ActivationController(
            state,
            evidence,
            permits,
            outcome=outcome,
            candidate_ref_override=candidate_ref_override,
            evidence_hash_override=evidence_hash_override,
            consume_permit=consume_permit,
        ),
    )


def _launch_runtime(
    *,
    candidate: TargetCandidate | None = None,
    actor: _Actor | None = None,
    outcome: TargetLaunchOutcome = TargetLaunchOutcome.COMPLETED,
    consume_permit: bool = True,
    candidate_ref_override: str | None = None,
    evidence_hash_override: str | None = None,
):
    return _target_acquisition_runtime(
        (candidate or _candidate(),),
        actor=actor or _Actor(_discover(), _activate()),
        controller_factory=lambda state, evidence, permits: _LaunchController(
            state,
            evidence,
            permits,
            outcome=outcome,
            consume_permit=consume_permit,
            candidate_ref_override=candidate_ref_override,
            evidence_hash_override=evidence_hash_override,
        ),
    )


def test_catalog_discovery_requires_an_injected_read_only_port() -> None:
    with pytest.raises(TypeError, match="requires one read-only"):
        _runtime(
            actor=_Actor(_discover()),
            state=_State({0: _snapshot(10, turn_index=0)}),
            discovery=None,
        )


def test_activation_catalog_requires_reducers_canonical_evidence_registry() -> None:
    definition = _definition()
    reducer = MissionReducer(definition)
    catalog = _catalog()
    profile = ExecutionPolicy().safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=("visual.interact",),
    )

    with pytest.raises(TypeError, match="canonical EvidenceRegistry"):
        MissionRuntime(
            reducer=reducer,
            profile=profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=_Actor(_discover()),
            state=_State({0: _snapshot(10, turn_index=0)}),
            controller=_Controller(),
            target_discovery=_Discovery(TargetDiscoverySnapshot(
                query="Orbit",
                candidates=(),
                captured_at=1.0,
            )),
        )


def test_discovery_snapshot_rejects_more_than_sixteen_notices() -> None:
    with pytest.raises(ValueError, match="notices"):
        TargetDiscoverySnapshot(
            query="Orbit",
            candidates=(),
            captured_at=1.0,
            notices=tuple(f"notice_{index}" for index in range(17)),
        )


def test_discovery_projects_one_turn_choices_without_mutation_or_mission_write() -> None:
    discovery_snapshot = TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    )
    discovery = _Discovery(discovery_snapshot)
    actor = _Actor(_discover(), _discover())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )
    initial_revision = reducer.projection.revision

    first = runtime.run_one_turn(turn_index=0)
    second = runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.OBSERVED
    assert first.accepted_event_ids == ()
    assert second.status is RuntimeStatus.OBSERVED
    assert second.accepted_event_ids == ()
    assert [query for query, _stop in discovery.calls] == ["Orbit", "Orbit"]
    assert reducer.projection.revision == initial_revision
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == permits.consumed_count == 0

    second_turn = actor.turns[1]
    assert [choice.model_dump() for choice in second_turn.target_choices] == [{
        "ordinal": 1,
        "label": "Orbit",
        "state": "installed",
        "window_title": None,
    }]
    assert tuple(tool.name for tool in second_turn.tools) == (
        DISCOVER_TARGET_TOOL_NAME,
        ACTIVATE_TARGET_TOOL_NAME,
    )
    runtime.discard_ephemeral_state()


def test_empty_discovery_projects_one_turn_identity_free_feedback() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
        notices=("installed_catalog_unavailable",),
    ))
    actor = _Actor(_discover(), _discover())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.OBSERVED

    second_turn = actor.turns[1]
    assert second_turn.target_discovery_feedback is not None
    assert second_turn.target_discovery_feedback.model_dump(mode="json") == {
        "format": "actor-target-discovery-feedback/0",
        "query": "Orbit",
        "outcome": "no_candidates",
    }
    assert second_turn.target_choices == ()
    assert tuple(tool.name for tool in second_turn.tools) == (
        DISCOVER_TARGET_TOOL_NAME,
    )
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == 0
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_is_consumed_exactly_once() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.UNAVAILABLE

    assert actor.turns[1].target_discovery_feedback is not None
    assert actor.turns[2].target_discovery_feedback is None
    assert discovery.calls == [("Orbit", None)]
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == 0
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_expires_when_next_turn_is_skipped() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_expires_after_mission_revision_change() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    reducer.apply(reducer.event(
        "obligation_blocked",
        payload={
            "obligation_id": "open-target",
            "blocker_kind": "external_state_changed",
            "reason": "accepted Mission state changed",
        },
    ))
    reducer.apply(reducer.event(
        "obligation_unblocked",
        payload={"obligation_id": "open-target"},
    ))

    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_discard_ephemeral_state_removes_empty_discovery_feedback() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, _reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    runtime.discard_ephemeral_state()
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_pending_candidates_expire_when_the_immediate_next_turn_is_skipped() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _discover())
    state = _State({
        0: _snapshot(10, turn_index=0),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.OBSERVED
    assert actor.turns[1].target_choices == ()
    assert tuple(tool.name for tool in actor.turns[1].tools) == (
        DISCOVER_TARGET_TOOL_NAME,
    )
    assert controller.calls == []
    assert permits.pending_count == permits.consumed_count == 0
    runtime.discard_ephemeral_state()


def test_candidate_authority_drift_rejects_before_the_second_actor_request() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )
    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    state.current_override = _snapshot(12, turn_index=1)

    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert len(actor.turns) == 1
    assert controller.calls == []
    assert permits.pending_count == permits.consumed_count == 0
    runtime.discard_ephemeral_state()


def test_discovery_rejects_a_snapshot_for_a_different_query() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Different application",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover("Orbit"))
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )

    result = runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert "query" in result.detail
    assert runtime._pending_target_discovery is None
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert permits.pending_count == 0
    assert controller.calls == []


def test_mission_change_during_discovery_discards_the_snapshot() -> None:
    holder = {}
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ), hook=lambda: holder["reducer"].apply(holder["reducer"].event(
        "obligation_blocked",
        payload={"obligation_id": "open-target", "reason": "external change"},
    )))
    runtime, reducer, _evidence, permits, controller = _runtime(
        actor=_Actor(_discover()),
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )
    holder["reducer"] = reducer

    result = runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert runtime._pending_target_discovery is None
    assert permits.pending_count == 0
    assert controller.calls == []


def test_stop_during_discovery_cancels_without_retaining_candidates() -> None:
    stop = Event()
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ), hook=stop.set)
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=_Actor(_discover()),
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )

    result = runtime.run_one_turn(turn_index=0, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert discovery.calls == [("Orbit", stop)]
    assert runtime._pending_target_discovery is None
    assert permits.pending_count == 0
    assert controller.calls == []


def test_existing_window_activation_accepts_one_replayable_binding_event() -> None:
    candidate = _window_candidate()
    runtime, reducer, evidence, permits, _state, controller = (
        _activation_runtime((candidate,))
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(result.accepted_event_ids) == 1
    assert [event.kind for event in reducer.events] == ["target_bound"]
    binding = reducer.projection.target_binding
    assert binding is not None
    assert (
        binding.hwnd,
        binding.process_id,
        binding.process_identity,
        binding.acquisition_action_id,
    ) == (
        candidate.hwnd,
        candidate.process_id,
        candidate.process_identity,
        result.decision_id,
    )
    assert controller.calls[0].candidate == candidate
    assert controller.saw_active_admission is True
    assert permits.pending_count == permits.active_count == 0
    assert runtime._active_target_selection_lease is None
    refs = evidence.snapshot()
    assert {ref.kind for ref in refs} == {
        POST_ACTION_FRAME_EVIDENCE_KIND,
        "authoritative_target_binding",
    }
    replayed = MissionReducer.replay(
        reducer.definition,
        reducer.events,
        evidence=evidence,
    )
    assert replayed.projection.target_binding == binding

    # A completed ordinal lease and accepted target cannot execute again.
    replay_attempt = runtime.run_one_turn(turn_index=2)
    assert replay_attempt.status is RuntimeStatus.STATE_REJECTED
    assert len(controller.calls) == 1


def test_installed_exe_launch_uses_same_owner_and_replays_binding() -> None:
    runtime, reducer, evidence, permits, _state, controller = (
        _launch_runtime()
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert len(result.accepted_event_ids) == 1
    assert [event.kind for event in reducer.events] == ["target_bound"]
    assert len(controller.calls) == 1
    envelope = controller.calls[0]
    assert isinstance(envelope, TargetLaunchEnvelope)
    assert envelope.candidate == _candidate()
    assert dict(envelope.proposal.args) == {"candidate_ordinal": 1}
    assert controller.saw_active_admission is True
    assert permits.pending_count == permits.active_count == 0
    binding = reducer.projection.target_binding
    assert binding is not None
    assert (
        binding.hwnd,
        binding.process_id,
        binding.process_identity,
        binding.acquisition_action_id,
    ) == (505, 707, f"process-{'7' * 32}", result.decision_id)
    assert {ref.kind for ref in evidence.snapshot()} == {
        POST_ACTION_FRAME_EVIDENCE_KIND,
        "authoritative_target_binding",
    }
    replayed = MissionReducer.replay(
        reducer.definition,
        reducer.events,
        evidence=evidence,
    )
    assert replayed.projection.target_binding == binding

    assert (
        runtime.run_one_turn(turn_index=2).status
        is RuntimeStatus.STATE_REJECTED
    )
    assert len(controller.calls) == 1


@pytest.mark.parametrize(
    ("kind", "target"),
    (
        ("appid", "Contoso.Orbit_123!Main"),
        ("lnk", r"C:\ProgramData\Microsoft\Windows\Start Menu\Orbit.lnk"),
    ),
)
def test_non_exe_installed_candidate_fails_before_controller(
    kind: str,
    target: str,
) -> None:
    candidate = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation=TargetActivation.OPEN_APP,
            identity=launch_identity_for(kind=kind, target=target),
        ),
        activation=TargetActivation.OPEN_APP,
        state=CandidateState.INSTALLED,
        label="Orbit",
        provenance=("installed_catalog",),
        launch_kind=kind,
        launch_target=target,
    )
    runtime, reducer, evidence, permits, _state, controller = (
        _launch_runtime(candidate=candidate)
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert controller.calls == []
    assert reducer.projection.accepted_event_ids == ()
    assert reducer.projection.target_binding is None
    assert evidence.snapshot() == ()
    assert permits.pending_count == permits.active_count == 0


def test_rejected_launch_is_stale_and_never_replayed() -> None:
    actor = _Actor(_discover(), _activate(), _activate())
    runtime, reducer, evidence, permits, _state, controller = (
        _launch_runtime(
            actor=actor,
            outcome=TargetLaunchOutcome.REJECTED,
        )
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    rejected = runtime.run_one_turn(turn_index=1)
    later = runtime.run_one_turn(turn_index=2)

    assert rejected.status is RuntimeStatus.STALE_DISCARDED
    assert rejected.accepted_event_ids == ()
    assert later.status is RuntimeStatus.UNAVAILABLE
    assert reducer.projection.accepted_event_ids == ()
    assert reducer.projection.target_binding is None
    assert evidence.snapshot() == ()
    assert len(controller.calls) == 1
    assert permits.pending_count == permits.active_count == 0


def test_unknown_launch_accepts_one_block_and_never_replays() -> None:
    actor = _Actor(_discover(), _activate())
    runtime, reducer, evidence, permits, _state, controller = (
        _launch_runtime(
            actor=actor,
            outcome=TargetLaunchOutcome.UNKNOWN,
        )
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    unknown = runtime.run_one_turn(turn_index=1)
    later = runtime.run_one_turn(turn_index=2)

    assert unknown.status is RuntimeStatus.BLOCKED
    assert len(unknown.accepted_event_ids) == 1
    assert later.status is RuntimeStatus.UNAVAILABLE
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None
    assert evidence.snapshot() == ()
    assert len(controller.calls) == 1
    assert len(actor.turns) == 2
    assert permits.pending_count == permits.active_count == 0


def test_launch_controller_exception_blocks_once_without_replay() -> None:
    actor = _Actor(_discover(), _activate())
    runtime, reducer, _evidence, permits, _state, controller = (
        _launch_runtime(actor=actor)
    )
    controller.raise_after_admission = True

    runtime.run_one_turn(turn_index=0)
    failed = runtime.run_one_turn(turn_index=1)
    later = runtime.run_one_turn(turn_index=2)

    assert failed.status is RuntimeStatus.BLOCKED
    assert later.status is RuntimeStatus.UNAVAILABLE
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None
    assert len(controller.calls) == 1
    assert len(actor.turns) == 2
    assert permits.pending_count == permits.active_count == 0


@pytest.mark.parametrize(
    "overrides",
    (
        {"candidate_ref_override": _window_candidate().candidate_ref},
        {"evidence_hash_override": "tampered-frame-hash"},
    ),
)
def test_tampered_launch_transition_never_binds(overrides) -> None:
    runtime, reducer, _evidence, permits, _state, controller = (
        _launch_runtime(**overrides)
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None
    assert len(controller.calls) == 1
    assert permits.pending_count == permits.active_count == 0


def test_launch_process_identity_tamper_never_binds() -> None:
    runtime, reducer, _evidence, _permits, _state, controller = (
        _launch_runtime()
    )
    controller.transition_mutator = lambda transition: transition.model_copy(
        update={"launch_process_id": 708}
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None
    assert len(controller.calls) == 1


def test_unconsumed_launch_permit_blocks_without_binding_or_replay() -> None:
    actor = _Actor(_discover(), _activate())
    runtime, reducer, _evidence, permits, _state, controller = (
        _launch_runtime(
            actor=actor,
            consume_permit=False,
        )
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)
    later = runtime.run_one_turn(turn_index=2)

    assert result.status is RuntimeStatus.BLOCKED
    assert later.status is RuntimeStatus.UNAVAILABLE
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None
    assert len(controller.calls) == 1
    assert len(actor.turns) == 2
    assert permits.pending_count == permits.active_count == 0


def test_candidate_ordinal_resolves_only_against_original_order() -> None:
    first = _window_candidate(404, label="First")
    second = _window_candidate(
        405,
        process_id=78,
        process_identity=f"process-{'5' * 32}",
        label="Second",
    )
    actor = _Actor(_discover(), _activate(2))
    runtime, reducer, _evidence, _permits, _state, controller = (
        _activation_runtime((first, second), actor=actor)
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.ACTION_RECORDED
    assert controller.calls[0].candidate == second
    assert reducer.projection.target_binding.hwnd == second.hwnd


def test_out_of_range_ordinal_exhausts_lease_without_controller_call() -> None:
    runtime, reducer, _evidence, permits, _state, controller = (
        _activation_runtime(
            (_window_candidate(),),
            actor=_Actor(_discover(), _activate(2)),
        )
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert reducer.projection.target_binding is None
    assert controller.calls == []
    assert runtime._active_target_selection_lease is None
    assert permits.pending_count == 0


@pytest.mark.parametrize(
    "outcome,expected",
    (
        (TargetActivationOutcome.REJECTED, RuntimeStatus.STALE_DISCARDED),
        (TargetActivationOutcome.UNKNOWN, RuntimeStatus.BLOCKED),
    ),
)
def test_noncompleted_activation_never_binds_or_replays(
    outcome: TargetActivationOutcome,
    expected: RuntimeStatus,
) -> None:
    runtime, reducer, evidence, permits, _state, controller = (
        _activation_runtime((_window_candidate(),), outcome=outcome)
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is expected
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)
    assert evidence.snapshot() == ()
    assert len(controller.calls) == 1
    assert permits.pending_count == permits.active_count == 0
    assert runtime._active_target_selection_lease is None


def test_unknown_activation_blocks_every_later_turn_without_actor_or_replay() -> None:
    actor = _Actor(_discover(), _activate())
    runtime, reducer, _evidence, _permits, _state, controller = (
        _activation_runtime(
            (_window_candidate(),),
            actor=actor,
            outcome=TargetActivationOutcome.UNKNOWN,
        )
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.BLOCKED
    later = runtime.run_one_turn(turn_index=2)

    assert later.status is RuntimeStatus.UNAVAILABLE
    assert reducer.projection.target_binding is None
    assert len(actor.turns) == 2
    assert len(controller.calls) == 1


def test_unconsumed_activation_permit_blocks_without_binding() -> None:
    runtime, reducer, _evidence, permits, _state, controller = (
        _activation_runtime(
            (_window_candidate(),),
            consume_permit=False,
        )
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert len(controller.calls) == 1
    assert permits.pending_count == 0


def test_controller_exception_closes_active_admission_and_never_replays() -> None:
    actor = _Actor(_discover(), _activate())
    runtime, reducer, _evidence, permits, _state, controller = (
        _activation_runtime((_window_candidate(),), actor=actor)
    )

    def fail_after_admission() -> None:
        raise RuntimeError("injected controller failure")

    controller.after_execute = fail_after_admission
    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert controller.saw_active_admission is True
    assert permits.pending_count == permits.active_count == 0
    assert reducer.projection.target_binding is None
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.UNAVAILABLE
    assert len(actor.turns) == 2
    assert len(controller.calls) == 1


def test_tampered_transition_candidate_never_binds() -> None:
    selected = _window_candidate()
    other = _window_candidate(
        405,
        process_id=78,
        process_identity=f"process-{'5' * 32}",
    )
    runtime, reducer, _evidence, _permits, _state, controller = (
        _activation_runtime(
            (selected,),
            candidate_ref_override=other.candidate_ref,
        )
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)
    assert len(controller.calls) == 1


def test_tampered_post_frame_hash_never_binds() -> None:
    runtime, reducer, evidence, _permits, _state, _controller = (
        _activation_runtime(
            (_window_candidate(),),
            evidence_hash_override="tampered-frame-hash",
        )
    )

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)
    assert {ref.kind for ref in evidence.snapshot()} == {
        POST_ACTION_FRAME_EVIDENCE_KIND
    }


@pytest.mark.parametrize("state_flag", ("post_authoritative", "post_fresh"))
def test_untrusted_post_frame_authority_never_binds(state_flag: str) -> None:
    runtime, reducer, _evidence, _permits, state, _controller = (
        _activation_runtime((_window_candidate(),))
    )
    setattr(state, state_flag, False)

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)


def test_transition_must_report_the_exact_post_frame_evidence_set() -> None:
    runtime, reducer, _evidence, _permits, state, controller = (
        _activation_runtime((_window_candidate(),))
    )

    def add_unreported_frame_evidence() -> None:
        assert state.post_frame is not None
        state.post_frame = replace(
            state.post_frame,
            evidence_refs=(*state.post_frame.evidence_refs, "unreported-evidence"),
        )

    controller.after_execute = add_unreported_frame_evidence
    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)


@pytest.mark.parametrize("field", ("root_hwnd", "window_region"))
def test_tampered_activated_identity_never_binds(field: str) -> None:
    runtime, reducer, _evidence, _permits, _state, controller = (
        _activation_runtime((_window_candidate(),))
    )

    def mutate(transition):
        target = transition.activated_target
        assert target is not None
        replacement = target.model_copy(update={
            field: (
                target.root_hwnd + 1
                if field == "root_hwnd"
                else (41, 50, 900, 600)
            )
        })
        return transition.model_copy(update={"activated_target": replacement})

    controller.transition_mutator = mutate
    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.BLOCKED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)


def test_mission_revision_race_after_activation_never_binds() -> None:
    runtime, reducer, _evidence, _permits, _state, controller = (
        _activation_runtime((_window_candidate(),))
    )
    controller.after_execute = lambda: reducer.apply(reducer.event(
        "obligation_blocked",
        payload={
            "obligation_id": "open-target",
            "blocker_kind": "external_state_changed",
            "reason": "accepted Mission state changed",
        },
    ))

    runtime.run_one_turn(turn_index=0)
    result = runtime.run_one_turn(turn_index=1)

    assert result.status is not RuntimeStatus.ACTION_RECORDED
    assert reducer.projection.target_binding is None
    assert not any(event.kind == "target_bound" for event in reducer.events)
    assert len(controller.calls) == 1
