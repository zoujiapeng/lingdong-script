from __future__ import annotations

import base64
import hashlib
import json

import pytest

from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerRuntimeState,
    TargetState,
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
from winpilot.vnext.runtime.contracts import RuntimeFrame, RuntimeSnapshot
from winpilot.vnext.runtime.target_activation import (
    ActivatedTargetIdentity,
    TargetActivationTransition,
)
from winpilot.vnext.runtime.target_binding_commit import (
    commit_target_binding,
    target_binding_evidence_id,
)
from winpilot.visual_runtime.commit_admission import TargetCommitIdentity


def _definition() -> MissionDefinition:
    return MissionDefinition(
        mission_id="mission-acquire",
        run_id="run-acquire",
        original_goal="Open the requested target",
        obligations=(
            ObligationSpec(
                id="open-target",
                desired_state="the target is ready",
                acceptance_criteria=("the correct target is visible",),
                required_capabilities=("visual.interact",),
            ),
        ),
    )


def _target() -> ActivatedTargetIdentity:
    return ActivatedTargetIdentity(
        hwnd=404,
        root_hwnd=400,
        process_id=77,
        process_identity=f"process-{'4' * 32}",
        window_region=(40, 50, 900, 600),
        title_snapshot="Workspace",
    )


def _frame(
    target: ActivatedTargetIdentity | None = None,
    *,
    evidence_refs: tuple[str, ...] = ("evidence-post-acquire",),
) -> RuntimeFrame:
    selected = target or _target()
    image_b64 = base64.b64encode(b"target-frame").decode("ascii")
    return RuntimeFrame(
        frame_id=30,
        epoch=0,
        hwnd=selected.hwnd,
        region=selected.window_region,
        scope="target_window",
        submitted_size=(900, 600),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        evidence_refs=evidence_refs,
    )


def _transition(
    target: ActivatedTargetIdentity,
    frame: RuntimeFrame,
    *,
    action_id: str,
) -> TargetActivationTransition:
    return TargetActivationTransition(
        outcome="completed",
        action_id=action_id,
        candidate_ref=f"target-{'a' * 24}",
        mutation_completed=True,
        activated_target=target,
        post_frame_id=frame.frame_id,
        evidence_refs=frame.evidence_refs,
        failure_reason="",
    )


def _register_post_evidence(
    registry: EvidenceRegistry,
    target: ActivatedTargetIdentity,
    frame: RuntimeFrame,
    *,
    action_id: str,
) -> EvidenceRef:
    del target
    return registry.append(EvidenceRef(
        id=frame.evidence_refs[0],
        mission_id="mission-acquire",
        run_id="run-acquire",
        obligation_id="open-target",
        kind=POST_ACTION_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=frame_identity_payload_hash(frame.commit_identity),
        frame_id=frame.frame_id,
        action_id=action_id,
    ))


class _State:
    def __init__(
        self,
        target: ActivatedTargetIdentity,
        frame: RuntimeFrame,
        *,
        authoritative: bool = True,
        fresh: bool = True,
        root_hwnd: int | None = None,
        hook=None,
    ) -> None:
        self.target = target
        self.frame = frame
        self.authoritative = authoritative
        self.fresh = fresh
        self.root_hwnd = root_hwnd
        self.hook = hook
        self.calls = 0

    def current(self, projection, *, turn_index: int) -> RuntimeSnapshot:
        self.calls += 1
        binding = projection.target_binding
        assert binding is not None
        if self.hook is not None:
            self.hook()
        target = self.target
        frame = self.frame
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
                    authoritative=self.authoritative,
                    fresh=self.fresh,
                ),
            ),
            frame=frame,
            target=TargetCommitIdentity(
                binding_id=binding.binding_id,
                hwnd=target.hwnd,
                root_hwnd=(
                    target.root_hwnd
                    if self.root_hwnd is None
                    else self.root_hwnd
                ),
                process_id=target.process_id,
                process_identity=target.process_identity,
                epoch=frame.epoch,
                window_region=target.window_region,
            ),
        )


def _owners():
    definition = _definition()
    registry = EvidenceRegistry(definition.mission_id, definition.run_id)
    return MissionReducer(definition, evidence=registry), registry


def _commit(reducer, registry, state, *, action_id="decision-acquire"):
    target = _target()
    frame = state.frame
    if reducer.projection.target_binding is None:
        _register_post_evidence(
            registry,
            target,
            frame,
            action_id=action_id,
        )
    return commit_target_binding(
        reducer=reducer,
        registry=registry,
        state=state,
        mission_id="mission-acquire",
        run_id="run-acquire",
        base_revision=0,
        obligation_id="open-target",
        acquisition_action_id=action_id,
        target=target,
        transition=_transition(target, frame, action_id=action_id),
        authority_epoch=frame.epoch,
        turn_index=3,
    )


def test_commit_appends_one_attestation_then_accepts_one_replayable_event() -> None:
    reducer, registry = _owners()
    target = _target()
    frame = _frame(target)

    result = _commit(
        reducer,
        registry,
        _State(target, frame),
    )
    binding = result.binding
    evidence = result.evidence
    event_id = result.event_id

    assert [event.event_id for event in reducer.events] == [event_id]
    assert [event.kind for event in reducer.events] == ["target_bound"]
    assert reducer.projection.target_binding == binding
    assert registry.snapshot() == (
        result.post_action.evidence_refs[0],
        evidence,
    )
    assert reducer.events[0].evidence_refs == (evidence.id,)
    replayed = MissionReducer.replay(
        reducer.definition,
        reducer.events,
        evidence=registry,
    )
    assert replayed.projection.target_binding == binding


def test_evidence_identity_is_canonical_and_acquisition_kind_neutral() -> None:
    target = _target()
    encoded = json.dumps(
        {
            "format": "vnext-target-binding-evidence-id/1",
            "mission_id": "mission-acquire",
            "run_id": "run-acquire",
            "acquisition_action_id": "launch-or-switch-1",
            "hwnd": target.hwnd,
            "root_hwnd": target.root_hwnd,
            "process_id": target.process_id,
            "process_identity": target.process_identity,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = "evidence-target-" + hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()[:24]

    assert target_binding_evidence_id(
        "mission-acquire",
        "run-acquire",
        "launch-or-switch-1",
        target,
    ) == expected


def test_stale_revision_rejects_before_state_or_evidence_write() -> None:
    reducer, registry = _owners()
    target = _target()
    frame = _frame(target)
    state = _State(target, frame)

    with pytest.raises(ValueError, match="stale or inconsistent"):
        commit_target_binding(
            reducer=reducer,
            registry=registry,
            state=state,
            mission_id="mission-acquire",
            run_id="run-acquire",
            base_revision=1,
            obligation_id="open-target",
            acquisition_action_id="decision-stale",
            target=target,
            transition=_transition(
                target,
                frame,
                action_id="decision-stale",
            ),
            authority_epoch=frame.epoch,
            turn_index=3,
        )

    assert state.calls == 0
    assert registry.snapshot() == ()
    assert reducer.events == ()


def test_revision_race_during_read_never_appends_or_accepts_binding() -> None:
    reducer, registry = _owners()

    def change_mission() -> None:
        reducer.apply(reducer.event(
            "obligation_blocked",
            payload={
                "obligation_id": "open-target",
                "blocker_kind": "external_state_changed",
                "reason": "Mission changed during target read",
            },
        ))

    state = _State(_target(), _frame(), hook=change_mission)
    with pytest.raises(ValueError, match="Mission authority changed"):
        _commit(reducer, registry, state)

    assert len(registry.snapshot()) == 1
    assert [event.kind for event in reducer.events] == ["obligation_blocked"]
    assert reducer.projection.target_binding is None


@pytest.mark.parametrize(
    "state",
    (
        _State(_target(), _frame(), authoritative=False),
        _State(_target(), _frame(), fresh=False),
        _State(_target(), _frame(), root_hwnd=401),
    ),
)
def test_tampered_runtime_authority_is_rejected_without_write(state: _State) -> None:
    reducer, registry = _owners()

    with pytest.raises(ValueError, match="post frame"):
        _commit(reducer, registry, state)

    assert len(registry.snapshot()) == 1
    assert reducer.events == ()
    assert reducer.projection.target_binding is None


def test_tampered_transition_frame_is_rejected_without_binding_write() -> None:
    reducer, registry = _owners()
    target = _target()
    frame = _frame(target)
    state = _State(target, frame)
    _register_post_evidence(
        registry,
        target,
        frame,
        action_id="decision-tampered",
    )
    transition = _transition(
        target,
        frame,
        action_id="decision-tampered",
    ).model_copy(update={"post_frame_id": frame.frame_id + 1})

    with pytest.raises(ValueError, match="post frame"):
        commit_target_binding(
            reducer=reducer,
            registry=registry,
            state=state,
            mission_id="mission-acquire",
            run_id="run-acquire",
            base_revision=0,
            obligation_id="open-target",
            acquisition_action_id="decision-tampered",
            target=target,
            transition=transition,
            authority_epoch=frame.epoch,
            turn_index=3,
        )

    assert state.calls == 1
    assert len(registry.snapshot()) == 1
    assert reducer.events == ()


def test_conflicting_canonical_evidence_identity_never_accepts_event() -> None:
    reducer, registry = _owners()
    target = _target()
    evidence_id = target_binding_evidence_id(
        "mission-acquire", "run-acquire", "decision-acquire", target,
    )
    registry.append(EvidenceRef(
        id=evidence_id,
        mission_id="mission-acquire",
        run_id="run-acquire",
        obligation_id="open-target",
        kind="authoritative_target_binding",
        trust="deterministic",
        payload_hash="tampered-payload",
        frame_id=30,
        action_id="decision-acquire",
    ))

    with pytest.raises(ValueError):
        _commit(reducer, registry, _State(target, _frame(target)))

    assert len(registry.snapshot()) == 2
    assert reducer.events == ()
    assert reducer.projection.target_binding is None


def test_completed_binding_cannot_accept_a_second_event() -> None:
    reducer, registry = _owners()
    state = _State(_target(), _frame())
    _commit(reducer, registry, state)

    with pytest.raises(ValueError, match="stale or inconsistent"):
        _commit(reducer, registry, state, action_id="decision-second")

    assert [event.kind for event in reducer.events] == ["target_bound"]
    assert len(registry.snapshot()) == 2
