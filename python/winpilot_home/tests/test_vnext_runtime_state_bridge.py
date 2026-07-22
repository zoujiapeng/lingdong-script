from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import random
import threading

import pytest

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.utils.image_capture import VisualCaptureScope
from winpilot.utils.screenshot import Region
from winpilot.vnext.actor import ActorDecisionEnvelope, ImageRegion, parse_actor_proposal
from winpilot.vnext.bridge.runtime_state import (
    CURRENT_FRAME_EVIDENCE_KIND,
    RuntimeReadState,
    RuntimeStateBridge,
    RuntimeStateBridgeError,
    current_frame_payload_hash,
)
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.evidence import EvidenceRegistry, EvidenceRegistryClosed
from winpilot.vnext.mission import (
    EvidenceRef,
    MissionDefinition,
    MissionProjection,
    ObligationProjection,
    ObligationStatus,
    TargetBinding,
)
from winpilot.vnext.runtime import MutationPermitAuthority
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionRequest,
    CommitInputKind,
    CommitRoute,
    FrameCommitIdentity,
    TargetCommitIdentity,
)
from winpilot.visual_runtime.frame_observer import FrameObserver


REGION = Region(10, 20, 640, 480)
IMAGE = "ZnJhbWU="


class _TargetReader:
    def __init__(self, target: TargetCommitIdentity | None) -> None:
        self._target = target
        self._lock = threading.Lock()

    def set(self, target: TargetCommitIdentity | None) -> None:
        with self._lock:
            self._target = target

    def read(self) -> TargetCommitIdentity | None:
        with self._lock:
            return self._target


class _CountingObserver(FrameObserver):
    def __init__(
        self,
        authority: FrameAuthority,
        *,
        capture,
        region: Region = REGION,
    ) -> None:
        super().__init__(
            authority,
            capture=capture,
            select_scope=lambda *_args, **_kwargs: VisualCaptureScope(
                region, "target_window", "test_target",
            ),
            virtual_desktop=lambda: Region(0, 0, 1920, 1080),
            captured_at=lambda: 123.0,
        )
        self.capture_calls = 0
        self.commit_calls = 0
        self._count_lock = threading.Lock()

    def capture(self, **kwargs):
        with self._count_lock:
            self.capture_calls += 1
        return super().capture(**kwargs)

    def commit(self, captured):
        with self._count_lock:
            self.commit_calls += 1
        return super().commit(captured)


def _target(**changes) -> TargetCommitIdentity:
    values = {
        "binding_id": "target-1",
        "hwnd": 101,
        "root_hwnd": 101,
        "process_id": 7,
        "epoch": 3,
        "window_region": (REGION.left, REGION.top, REGION.width, REGION.height),
    }
    values.update(changes)
    return TargetCommitIdentity(**values)


def _projection(
    target: TargetCommitIdentity | None = None,
    *,
    mission_id: str = "mission-1",
    run_id: str = "run-1",
) -> MissionProjection:
    definition = MissionDefinition.from_goal(
        "complete the visible task",
        mission_id=mission_id,
        run_id=run_id,
    )
    binding = None
    if target is not None:
        binding = TargetBinding(
            binding_id=target.binding_id,
            hwnd=target.hwnd,
            process_id=target.process_id,
            executable_identity="test.exe",
            title_snapshot="Test",
            established_by_evidence_ref="target-proof",
            epoch=target.epoch,
        )
    return MissionProjection(
        definition=definition,
        revision=2,
        obligation_states=(
            ObligationProjection("obligation-1", ObligationStatus.ACTIVE),
        ),
        active_obligation_id="obligation-1",
        target_binding=binding,
    )


def _read_state(
    *,
    pointer: PointerLeaseState | None = None,
    stop_requested: bool = False,
    canceled: bool = False,
) -> RuntimeReadState:
    return RuntimeReadState(
        available_capabilities=("visual.interact",),
        pointer=pointer or PointerLeaseState(PointerDisposition.RELEASED),
        runtime_healthy=True,
        paused=False,
        stop_requested=stop_requested,
        canceled=canceled,
    )


def _bridge(
    *,
    target: TargetCommitIdentity | None = None,
    authority: FrameAuthority | None = None,
    registry: EvidenceRegistry | None = None,
    reader: _TargetReader | None = None,
    capture=None,
    runtime_state=None,
    frame_max_age_s=30.0,
    clock=lambda: 123.0,
):
    resolved_target = target or _target()
    resolved_authority = authority or FrameAuthority()
    resolved_registry = (
        registry if registry is not None else EvidenceRegistry("mission-1", "run-1")
    )
    resolved_reader = reader or _TargetReader(resolved_target)
    observer = _CountingObserver(
        resolved_authority,
        capture=capture or (lambda _hwnd, **_kwargs: IMAGE),
    )
    permits = MutationPermitAuthority(
        resolved_registry.mission_id, resolved_registry.run_id,
    )
    bridge = RuntimeStateBridge(
        frame_authority=resolved_authority,
        observer=observer,
        evidence=resolved_registry,
        target=resolved_target,
        mutation_permits=permits,
        read_target_identity=resolved_reader.read,
        read_runtime_state=runtime_state or (lambda: _read_state()),
        frame_max_age_s=frame_max_age_s,
        clock=clock,
    )
    return bridge, observer, permits, resolved_registry, resolved_reader


def test_current_frame_freshness_expires_without_fabricating_new_identity() -> None:
    now = [123.0]
    target = _target()
    bridge, observer, _permits, _registry, _reader = _bridge(
        target=target,
        frame_max_age_s=5.0,
        clock=lambda: now[0],
    )

    observed = bridge.observe(_projection(target), turn_index=0)
    assert observed.broker_state.frame.fresh is True
    now[0] = 128.001
    current = bridge.current(_projection(target), turn_index=0)

    assert current.broker_state.frame.fresh is False
    assert current.frame is not None and observed.frame is not None
    assert current.frame.authority_key == observed.frame.authority_key
    assert current.authority_key != observed.authority_key
    assert observer.capture_calls == observer.commit_calls == 1


def _envelope(
    snapshot,
    *,
    decision_id: str = "decision-1",
    pointer: PointerLeaseState | None = None,
) -> ActorDecisionEnvelope:
    assert snapshot.frame is not None and snapshot.target is not None
    return ActorDecisionEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=2,
        obligation_id="obligation-1",
        frame=snapshot.frame.commit_identity,
        target=snapshot.target,
        pointer=pointer or PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=(),
        allowed_evidence_scope=snapshot.frame.evidence_refs,
        proposal=parse_actor_proposal({
            "format": "actor-proposal/0",
            "kind": "desktop_action",
            "action": "click",
            "args": {"image_x": 100, "image_y": 120},
            "local_intent": "activate the visible target",
        }),
    )


def _request(envelope: ActorDecisionEnvelope, **changes) -> CommitAdmissionRequest:
    values = {
        "request_id": envelope.decision_id,
        "frame": envelope.frame,
        "target": envelope.target,
        "route": CommitRoute.PHYSICAL,
        "input_kind": CommitInputKind.POINT,
        "screen_point": (100, 120),
    }
    values.update(changes)
    return CommitAdmissionRequest(**values)


def test_observe_commits_once_and_registers_exact_deterministic_evidence() -> None:
    target = _target()
    projection = _projection(target)
    bridge, observer, _permits, registry, _reader = _bridge(target=target)

    snapshot = bridge.observe(projection, turn_index=4)

    assert observer.capture_calls == 1
    assert observer.commit_calls == 1
    assert snapshot.frame is not None
    assert snapshot.frame.frame_id == 1
    assert snapshot.target is target
    assert snapshot.broker_state.target.binding_id == target.binding_id
    assert snapshot.broker_state.frame.authoritative is True
    evidence, = registry.snapshot()
    assert snapshot.frame.evidence_refs == (evidence.id,)
    assert evidence.kind == CURRENT_FRAME_EVIDENCE_KIND
    assert evidence.trust == "deterministic"
    assert evidence.mission_id == projection.mission_id
    assert evidence.run_id == projection.run_id
    assert evidence.obligation_id == projection.active_obligation_id
    assert evidence.frame_id == snapshot.frame.frame_id
    assert evidence.action_id is None
    assert evidence.payload_hash == current_frame_payload_hash(
        snapshot.frame.commit_identity
    )


def test_current_is_zero_capture_and_never_registers_evidence() -> None:
    target = _target()
    projection = _projection(target)
    bridge, observer, _permits, registry, _reader = _bridge(target=target)

    empty = bridge.current(projection, turn_index=0)
    assert empty.frame is None
    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0

    observed = bridge.observe(projection, turn_index=1)
    evidence_count = len(registry)
    current = bridge.current(projection, turn_index=1)
    assert current.authority_key == observed.authority_key
    assert observer.capture_calls == observer.commit_calls == 1
    assert len(registry) == evidence_count


def test_image_region_is_explicitly_rejected_without_side_effects() -> None:
    target = _target()
    projection = _projection(target)
    bridge, observer, _permits, registry, _reader = _bridge(target=target)

    with pytest.raises(RuntimeStateBridgeError, match="image_region.*not implemented"):
        bridge.observe(
            projection,
            turn_index=0,
            image_region=ImageRegion(x=0, y=0, width=100, height=100),
        )

    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0


@pytest.mark.parametrize(
    "changed",
    (
        {"binding_id": "target-2"},
        {"hwnd": 102},
        {"root_hwnd": 102},
        {"process_id": 8},
        {"epoch": 4},
        {"window_region": (11, 20, 640, 480)},
    ),
)
def test_live_target_drift_fails_closed_and_never_switches_target(changed) -> None:
    target = _target()
    projection = _projection(target)
    reader = _TargetReader(_target(**changed))
    bridge, observer, _permits, registry, _reader = _bridge(
        target=target, reader=reader,
    )

    with pytest.raises(RuntimeStateBridgeError, match="changed"):
        bridge.observe(projection, turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0
    reader.set(target)
    assert bridge.observe(projection, turn_index=1).target is target


@pytest.mark.parametrize(
    "binding_change",
    (
        {"binding_id": "target-2"},
        {"hwnd": 202},
        {"process_id": 99},
        {"epoch": 9},
    ),
)
def test_projection_target_mismatch_fails_before_capture(binding_change) -> None:
    target = _target()
    changed_target = _target(**binding_change)
    bridge, observer, _permits, registry, _reader = _bridge(target=target)

    with pytest.raises(RuntimeStateBridgeError, match="accepted Mission target"):
        bridge.observe(_projection(changed_target), turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0


def test_missing_target_and_wrong_mission_scope_fail_closed() -> None:
    target = _target()
    bridge, observer, _permits, registry, _reader = _bridge(target=target)

    with pytest.raises(RuntimeStateBridgeError, match="target is unavailable"):
        bridge.current(_projection(None), turn_index=0)
    with pytest.raises(RuntimeStateBridgeError, match="mission/run"):
        bridge.current(
            _projection(target, mission_id="mission-other", run_id="run-other"),
            turn_index=0,
        )
    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0


def test_target_change_during_capture_does_not_commit_or_write_evidence() -> None:
    target = _target()
    projection = _projection(target)
    reader = _TargetReader(target)

    def capture(_hwnd, **_kwargs):
        reader.set(_target(window_region=(11, 20, 640, 480)))
        return IMAGE

    bridge, observer, _permits, registry, _reader = _bridge(
        target=target, reader=reader, capture=capture,
    )

    with pytest.raises(RuntimeStateBridgeError, match="changed"):
        bridge.observe(projection, turn_index=0)

    assert observer.capture_calls == 1
    assert observer.commit_calls == 0
    assert observer.authority.current is None
    assert len(registry) == 0


def test_authority_cas_conflict_writes_no_current_frame_evidence() -> None:
    target = _target()
    projection = _projection(target)
    authority = FrameAuthority()

    def capture(_hwnd, **_kwargs):
        authority.commit(
            epoch=target.epoch,
            hwnd=target.hwnd,
            region=target.window_region,
            scope="target_window",
            reason="competing_owner",
            submitted_size=(640, 480),
            image_b64="ZXh0ZXJuYWw=",
            captured_at=100.0,
        )
        return IMAGE

    bridge, observer, _permits, registry, _reader = _bridge(
        target=target, authority=authority, capture=capture,
    )

    with pytest.raises(RuntimeStateBridgeError, match="CAS"):
        bridge.observe(projection, turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 1
    assert authority.current is not None
    assert authority.current.reason == "competing_owner"
    assert len(registry) == 0


def test_closed_registry_never_returns_an_unevidenced_observation() -> None:
    target = _target()
    projection = _projection(target)
    registry = EvidenceRegistry("mission-1", "run-1")
    bridge, observer, _permits, _registry, _reader = _bridge(
        target=target, registry=registry,
    )
    registry.close()

    with pytest.raises(EvidenceRegistryClosed):
        bridge.observe(projection, turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 1
    assert bridge.current(projection, turn_index=0).frame.evidence_refs == ()


@pytest.mark.parametrize(
    "epoch,region,error",
    (
        (4, (10, 20, 640, 480), "target epoch"),
        (3, (11, 20, 640, 480), "geometry changed"),
    ),
)
def test_current_rejects_stale_authority_identity(epoch, region, error) -> None:
    target = _target()
    projection = _projection(target)
    authority = FrameAuthority()
    authority.commit(
        epoch=epoch,
        hwnd=target.hwnd,
        region=region,
        scope="target_window",
        reason="stale_epoch",
        submitted_size=(640, 480),
        image_b64=IMAGE,
        captured_at=1.0,
    )
    bridge, observer, _permits, _registry, _reader = _bridge(
        target=target, authority=authority,
    )

    with pytest.raises(RuntimeStateBridgeError, match=error):
        bridge.current(projection, turn_index=0)

    assert observer.capture_calls == 0


def test_concurrent_observations_are_serialized_into_unique_cas_frames() -> None:
    target = _target()
    projection = _projection(target)
    calls = 0
    calls_lock = threading.Lock()

    def capture(_hwnd, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
            value = calls
        return f"ZnJhbWU{value}="

    bridge, observer, _permits, registry, _reader = _bridge(
        target=target, capture=capture,
    )
    count = 24
    with ThreadPoolExecutor(max_workers=12) as pool:
        snapshots = list(
            pool.map(
                lambda turn: bridge.observe(projection, turn_index=turn),
                range(count),
            )
        )

    frame_ids = sorted(snapshot.frame.frame_id for snapshot in snapshots)
    evidence_ids = {
        snapshot.frame.evidence_refs[0] for snapshot in snapshots
    }
    assert frame_ids == list(range(1, count + 1))
    assert len(evidence_ids) == count
    assert observer.capture_calls == observer.commit_calls == calls == count
    assert len(registry) == count


def test_randomized_frame_geometry_replays_deterministically() -> None:
    rng = random.Random(20260722)
    for index in range(40):
        left, top = rng.randint(-200, 300), rng.randint(-100, 200)
        width, height = rng.randint(32, 1400), rng.randint(32, 900)
        epoch = rng.randint(0, 20)
        region = Region(left, top, width, height)
        target = _target(
            epoch=epoch,
            window_region=(left, top, width, height),
        )
        authority = FrameAuthority()
        registry = EvidenceRegistry("mission-1", "run-1")
        reader = _TargetReader(target)
        observer = _CountingObserver(
            authority,
            capture=lambda _hwnd, **_kwargs: f"cmFuZG9tLWZyYW1lLQ{index}=",
            region=region,
        )
        bridge = RuntimeStateBridge(
            frame_authority=authority,
            observer=observer,
            evidence=registry,
            target=target,
            mutation_permits=MutationPermitAuthority("mission-1", "run-1"),
            read_target_identity=reader.read,
            read_runtime_state=lambda: _read_state(),
            frame_max_age_s=30.0,
            clock=lambda: 123.0,
        )

        first = bridge.observe(_projection(target), turn_index=index)
        replay = bridge.current(_projection(target), turn_index=index)

        assert replay.authority_key == first.authority_key
        assert first.frame.region == (left, top, width, height)
        assert first.frame.epoch == epoch
        assert observer.capture_calls == observer.commit_calls == 1


def test_commit_state_uses_exact_active_admission_and_never_caches_allowance() -> None:
    target = _target()
    projection = _projection(target)
    bridge, _observer, permits, _registry, _reader = _bridge(target=target)
    snapshot = bridge.observe(projection, turn_index=0)
    envelope = _envelope(snapshot)
    request = _request(envelope)

    assert bridge.read_commit_state(request).mutation_allowed is False
    permits.issue(envelope, turn_index=0, still_current=lambda: True)
    assert bridge.read_commit_state(request).mutation_allowed is False
    assert permits.begin_admission(envelope).allowed is True
    assert bridge.read_commit_state(request).mutation_allowed is True
    assert permits.finish_admission(envelope.decision_id) is True
    assert bridge.read_commit_state(request).mutation_allowed is False


def test_commit_state_rejects_wrong_request_identity_and_target_drift() -> None:
    target = _target()
    projection = _projection(target)
    bridge, _observer, permits, _registry, reader = _bridge(target=target)
    snapshot = bridge.observe(projection, turn_index=0)
    envelope = _envelope(snapshot)
    permits.issue(envelope, turn_index=0, still_current=lambda: True)
    assert permits.begin_admission(envelope).allowed is True

    assert bridge.read_commit_state(
        _request(envelope, request_id="decision-other")
    ).mutation_allowed is False
    assert bridge.read_commit_state(
        _request(
            envelope,
            frame=replace(envelope.frame, image_sha256="0" * 64),
        )
    ).mutation_allowed is False
    assert bridge.read_commit_state(
        _request(
            envelope,
            target=replace(envelope.target, window_region=(11, 20, 640, 480)),
        )
    ).mutation_allowed is False

    reader.set(_target(window_region=(12, 20, 640, 480)))
    assert bridge.read_commit_state(_request(envelope)).mutation_allowed is False


@pytest.mark.parametrize(
    "pointer,stop_requested,canceled",
    (
        (PointerLeaseState(PointerDisposition.RELEASED), False, False),
        (PointerLeaseState(PointerDisposition.HELD, "lease-1", True), False, False),
        (PointerLeaseState(PointerDisposition.UNKNOWN), True, False),
        (PointerLeaseState(PointerDisposition.RELEASED), False, True),
    ),
)
def test_commit_state_projects_injected_pointer_stop_and_cancel(
    pointer, stop_requested, canceled,
) -> None:
    target = _target()
    state = _read_state(
        pointer=pointer,
        stop_requested=stop_requested,
        canceled=canceled,
    )
    bridge, _observer, permits, _registry, _reader = _bridge(
        target=target,
        runtime_state=lambda: state,
    )
    snapshot = bridge.observe(_projection(target), turn_index=0)
    envelope = _envelope(snapshot, pointer=pointer)
    permits.issue(envelope, turn_index=0, still_current=lambda: True)
    assert permits.begin_admission(envelope).allowed is True

    commit = bridge.read_commit_state(_request(envelope))

    assert commit.pointer_released is state.pointer_released
    assert commit.stop_requested is stop_requested
    assert commit.canceled is canceled
    assert commit.mutation_allowed is True


def test_concurrent_permit_reads_follow_authority_without_shadow_state() -> None:
    target = _target()
    bridge, _observer, permits, _registry, _reader = _bridge(target=target)
    snapshot = bridge.observe(_projection(target), turn_index=0)
    envelope = _envelope(snapshot)
    request = _request(envelope)
    permits.issue(envelope, turn_index=0, still_current=lambda: True)
    assert permits.begin_admission(envelope).allowed is True

    with ThreadPoolExecutor(max_workers=16) as pool:
        before = list(pool.map(lambda _i: bridge.read_commit_state(request), range(64)))
    assert all(item.mutation_allowed for item in before)

    assert permits.finish_admission(envelope.decision_id) is True
    with ThreadPoolExecutor(max_workers=16) as pool:
        after = list(pool.map(lambda _i: bridge.read_commit_state(request), range(64)))
    assert not any(item.mutation_allowed for item in after)


def test_constructor_rejects_duplicate_owners_and_scope_mismatch() -> None:
    target = _target()
    first = FrameAuthority()
    second = FrameAuthority()
    observer = _CountingObserver(first, capture=lambda _hwnd, **_kwargs: IMAGE)
    registry = EvidenceRegistry("mission-1", "run-1")
    permits = MutationPermitAuthority("mission-1", "run-other")

    with pytest.raises(TypeError, match="share one FrameAuthority"):
        RuntimeStateBridge(
            frame_authority=second,
            observer=observer,
            evidence=registry,
            target=target,
            mutation_permits=MutationPermitAuthority("mission-1", "run-1"),
            read_target_identity=lambda: target,
            read_runtime_state=lambda: _read_state(),
            frame_max_age_s=30.0,
            clock=lambda: 123.0,
        )
    with pytest.raises(TypeError, match="share mission/run"):
        RuntimeStateBridge(
            frame_authority=first,
            observer=observer,
            evidence=registry,
            target=target,
            mutation_permits=permits,
            read_target_identity=lambda: target,
            read_runtime_state=lambda: _read_state(),
            frame_max_age_s=30.0,
            clock=lambda: 123.0,
        )


def test_forged_or_non_deterministic_frame_evidence_is_not_projected() -> None:
    target = _target()
    projection = _projection(target)
    bridge, _observer, _permits, registry, _reader = _bridge(target=target)
    observed = bridge.observe(projection, turn_index=0)
    frame = observed.frame
    assert frame is not None
    registry.append(EvidenceRef(
        id="forged-frame-proof",
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="obligation-1",
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        trust="independent_visual_model",
        payload_hash=current_frame_payload_hash(frame.commit_identity),
        frame_id=frame.frame_id,
    ))
    registry.append(EvidenceRef(
        id="wrong-payload-proof",
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="obligation-1",
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash="wrong-payload",
        frame_id=frame.frame_id,
    ))

    current = bridge.current(projection, turn_index=0)

    assert current.frame.evidence_refs == observed.frame.evidence_refs
