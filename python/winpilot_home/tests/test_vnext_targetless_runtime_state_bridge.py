from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
from typing import Callable

import pytest

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.utils.image_capture import VisualCaptureScope
from winpilot.utils.screenshot import Region
from winpilot.vnext.bridge.runtime_state import (
    DesktopObservationIdentity,
    RuntimeReadState,
    RuntimeStateBridge,
    RuntimeStateBridgeError,
)
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    MissionDefinition,
    MissionProjection,
    ObligationProjection,
    ObligationStatus,
    target_binding_attestation,
)
from winpilot.vnext.runtime import MutationPermitAuthority
from winpilot.visual_runtime.commit_admission import TargetCommitIdentity
from winpilot.visual_runtime.frame_observer import FrameCapture, FrameObserver


IMAGE = "ZnVsbC1kZXNrdG9wLWZyYW1l"
DESKTOP_REGION = Region(-1280, 0, 3200, 1080)
TARGET_REGION = Region(40, 60, 900, 700)
PROCESS_IDENTITY = f"process-{'3' * 32}"
DESKTOP = DesktopObservationIdentity(
    hwnd=1,
    region=DESKTOP_REGION,
    epoch=0,
)


class _DesktopReader:
    def __init__(self, identity: DesktopObservationIdentity) -> None:
        self._identity = identity
        self._lock = threading.Lock()
        self.calls = 0

    def set(self, identity: DesktopObservationIdentity) -> None:
        with self._lock:
            self._identity = identity

    def read(self) -> DesktopObservationIdentity:
        with self._lock:
            self.calls += 1
            return self._identity


class _TargetReader:
    def __init__(self, target: TargetCommitIdentity | None) -> None:
        self._target = target
        self.calls: list[tuple[int, str, int]] = []

    def read(
        self,
        hwnd: int,
        binding_id: str,
        epoch: int,
    ) -> TargetCommitIdentity | None:
        self.calls.append((hwnd, binding_id, epoch))
        target = self._target
        if target is None:
            return None
        if (hwnd, binding_id, epoch) != (
            target.hwnd,
            target.binding_id,
            target.epoch,
        ):
            return None
        return target


class _CountingObserver(FrameObserver):
    def __init__(
        self,
        authority: FrameAuthority,
        *,
        capture: Callable[..., str],
        desktop_region: Region = DESKTOP_REGION,
        rewrite_capture: Callable[[FrameCapture], FrameCapture] | None = None,
        after_commit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            authority,
            capture=capture,
            select_scope=lambda *_args, **_kwargs: VisualCaptureScope(
                TARGET_REGION,
                "target_window",
                "test_bound_target",
            ),
            virtual_desktop=lambda: desktop_region,
            captured_at=lambda: 123.0,
        )
        self.capture_calls = 0
        self.commit_calls = 0
        self._rewrite_capture = rewrite_capture
        self._after_commit = after_commit

    def capture(self, **kwargs) -> FrameCapture | None:
        self.capture_calls += 1
        captured = super().capture(**kwargs)
        if captured is not None and self._rewrite_capture is not None:
            return self._rewrite_capture(captured)
        return captured

    def commit(self, captured: FrameCapture):
        self.commit_calls += 1
        state = super().commit(captured)
        if self._after_commit is not None:
            self._after_commit()
        return state


def _binding_for(target: TargetCommitIdentity):
    binding, _evidence = target_binding_attestation(
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="obligation-1",
        evidence_id="target-proof-1",
        acquisition_action_id="target-acquisition-1",
        hwnd=target.hwnd,
        root_hwnd=target.root_hwnd,
        process_id=target.process_id,
        process_identity=target.process_identity,
        title_snapshot="Bound target",
        epoch=target.epoch,
    )
    return binding


def _target() -> TargetCommitIdentity:
    provisional = TargetCommitIdentity(
        binding_id="provisional-binding",
        hwnd=101,
        root_hwnd=101,
        process_id=7,
        process_identity=PROCESS_IDENTITY,
        epoch=3,
        window_region=(
            TARGET_REGION.left,
            TARGET_REGION.top,
            TARGET_REGION.width,
            TARGET_REGION.height,
        ),
    )
    binding = _binding_for(provisional)
    return replace(provisional, binding_id=binding.binding_id)


def _projection(
    target: TargetCommitIdentity | None,
) -> MissionProjection:
    definition = MissionDefinition.from_goal(
        "complete the visible task",
        mission_id="mission-1",
        run_id="run-1",
    )
    return MissionProjection(
        definition=definition,
        revision=2,
        obligation_states=(
            ObligationProjection("obligation-1", ObligationStatus.ACTIVE),
        ),
        active_obligation_id="obligation-1",
        target_binding=None if target is None else _binding_for(target),
    )


def _read_state() -> RuntimeReadState:
    return RuntimeReadState(
        available_capabilities=("visual.interact",),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        runtime_healthy=True,
        paused=False,
        canceled=False,
        stop_requested=False,
    )


def _bridge(
    *,
    desktop_reader: _DesktopReader | None = None,
    target_reader: _TargetReader | None = None,
    capture: Callable[..., str] | None = None,
    rewrite_capture: Callable[[FrameCapture], FrameCapture] | None = None,
    after_commit: Callable[[], None] | None = None,
    desktop_region: Region = DESKTOP_REGION,
):
    authority = FrameAuthority()
    registry = EvidenceRegistry("mission-1", "run-1")
    resolved_desktop_reader = desktop_reader or _DesktopReader(DESKTOP)
    resolved_target_reader = target_reader or _TargetReader(None)
    observer = _CountingObserver(
        authority,
        capture=capture or (lambda _hwnd, **_kwargs: IMAGE),
        desktop_region=desktop_region,
        rewrite_capture=rewrite_capture,
        after_commit=after_commit,
    )
    bridge = RuntimeStateBridge(
        frame_authority=authority,
        observer=observer,
        evidence=registry,
        mutation_permits=MutationPermitAuthority("mission-1", "run-1"),
        read_target_identity=resolved_target_reader.read,
        read_desktop_identity=resolved_desktop_reader.read,
        read_runtime_state=_read_state,
        frame_max_age_s=30.0,
        clock=lambda: 123.0,
    )
    return (
        bridge,
        observer,
        registry,
        resolved_desktop_reader,
        resolved_target_reader,
    )


def test_targetless_observe_and_current_project_one_full_desktop_authority() -> None:
    bridge, observer, registry, desktop_reader, target_reader = _bridge()
    projection = _projection(None)

    observed = bridge.observe(projection, turn_index=0)
    current = bridge.current(projection, turn_index=0)

    assert observed.target is None
    assert observed.frame is not None
    assert observed.frame.hwnd == DESKTOP.hwnd
    assert observed.frame.epoch == DESKTOP.epoch
    assert observed.frame.region == (
        DESKTOP_REGION.left,
        DESKTOP_REGION.top,
        DESKTOP_REGION.width,
        DESKTOP_REGION.height,
    )
    assert observed.frame.scope == "full_desktop"
    assert observed.broker_state.target.live is False
    assert observed.broker_state.frame.scope == "full_desktop"
    assert current.authority_key == observed.authority_key
    assert observer.capture_calls == observer.commit_calls == 1
    assert len(registry) == 1
    assert desktop_reader.calls >= 3
    assert target_reader.calls == []


def test_targetless_empty_current_does_not_call_bound_target_reader() -> None:
    bridge, observer, registry, _desktop_reader, target_reader = _bridge()

    current = bridge.current(_projection(None), turn_index=0)

    assert current.target is None
    assert current.frame is None
    assert current.broker_state.target.live is False
    assert observer.capture_calls == observer.commit_calls == 0
    assert len(registry) == 0
    assert target_reader.calls == []


def test_targetless_concurrent_observe_serializes_one_frame_authority() -> None:
    bridge, observer, registry, _desktop_reader, target_reader = _bridge()
    projection = _projection(None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = tuple(
            pool.map(
                lambda turn: bridge.observe(projection, turn_index=turn),
                range(16),
            )
        )

    assert sorted(item.frame.frame_id for item in snapshots) == list(range(1, 17))
    assert observer.capture_calls == observer.commit_calls == 16
    assert len(registry) == 16
    assert target_reader.calls == []


@pytest.mark.parametrize(
    "changes",
    (
        {"hwnd": 2},
        {"region": Region(-1280, 0, 3199, 1080)},
    ),
)
def test_targetless_topology_drift_during_capture_fails_before_commit(
    changes,
) -> None:
    desktop_reader = _DesktopReader(DESKTOP)

    def capture(_hwnd, **_kwargs):
        desktop_reader.set(replace(DESKTOP, **changes))
        return IMAGE

    bridge, observer, registry, _desktop, target_reader = _bridge(
        desktop_reader=desktop_reader,
        capture=capture,
    )

    with pytest.raises(RuntimeStateBridgeError):
        bridge.observe(_projection(None), turn_index=0)

    assert observer.capture_calls == 1
    assert observer.commit_calls == 0
    assert observer.authority.current is None
    assert len(registry) == 0
    assert target_reader.calls == []


def test_targetless_topology_drift_after_commit_never_writes_evidence() -> None:
    desktop_reader = _DesktopReader(DESKTOP)
    bridge, observer, registry, _desktop, target_reader = _bridge(
        desktop_reader=desktop_reader,
        after_commit=lambda: desktop_reader.set(replace(DESKTOP, hwnd=2)),
    )

    with pytest.raises(
        RuntimeStateBridgeError,
        match="desktop identity changed during frame commit",
    ):
        bridge.observe(_projection(None), turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 1
    assert observer.authority.current is not None
    assert len(registry) == 0
    assert target_reader.calls == []


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda captured: replace(captured, hwnd=captured.hwnd + 1),
        lambda captured: replace(
            captured,
            region=Region(
                captured.region.left,
                captured.region.top,
                captured.region.width - 1,
                captured.region.height,
            ),
        ),
        lambda captured: replace(captured, scope="target_window"),
        lambda captured: replace(captured, epoch=captured.epoch + 1),
    ),
    ids=("wrong-hwnd", "wrong-region", "wrong-scope", "wrong-epoch"),
)
def test_targetless_capture_identity_mismatch_fails_closed(rewrite) -> None:
    bridge, observer, registry, _desktop_reader, target_reader = _bridge(
        rewrite_capture=rewrite,
    )

    with pytest.raises(RuntimeStateBridgeError):
        bridge.observe(_projection(None), turn_index=0)

    assert observer.capture_calls == 1
    assert observer.commit_calls == 0
    assert observer.authority.current is None
    assert len(registry) == 0
    assert target_reader.calls == []


def test_targetless_region_not_equal_to_observer_desktop_cannot_be_committed() -> None:
    identity_region = Region(-1280, 0, 3199, 1080)
    desktop_reader = _DesktopReader(
        DesktopObservationIdentity(hwnd=1, region=identity_region, epoch=0)
    )
    bridge, observer, registry, _desktop, target_reader = _bridge(
        desktop_reader=desktop_reader,
        desktop_region=DESKTOP_REGION,
    )

    with pytest.raises(RuntimeStateBridgeError):
        bridge.observe(_projection(None), turn_index=0)

    assert observer.capture_calls == 1
    assert observer.commit_calls == 0
    assert observer.authority.current is None
    assert len(registry) == 0
    assert target_reader.calls == []


def test_current_rejects_desktop_topology_drift_without_recapture() -> None:
    bridge, observer, registry, desktop_reader, target_reader = _bridge()
    projection = _projection(None)
    bridge.observe(projection, turn_index=0)
    desktop_reader.set(
        replace(
            DESKTOP,
            region=Region(-1280, -200, 3200, 1280),
        )
    )

    with pytest.raises(RuntimeStateBridgeError):
        bridge.current(projection, turn_index=0)

    assert observer.capture_calls == observer.commit_calls == 1
    assert len(registry) == 1
    assert target_reader.calls == []


def test_one_bridge_switches_from_targetless_desktop_to_accepted_bound_target() -> None:
    target = _target()
    target_reader = _TargetReader(target)
    bridge, observer, registry, desktop_reader, _target_reader = _bridge(
        target_reader=target_reader,
    )

    targetless = bridge.observe(_projection(None), turn_index=0)
    desktop_calls = desktop_reader.calls
    bound = bridge.observe(_projection(target), turn_index=1)
    current = bridge.current(_projection(target), turn_index=1)

    assert targetless.target is None
    assert targetless.frame is not None
    assert targetless.frame.scope == "full_desktop"
    assert bound.target == target
    assert bound.frame is not None
    assert bound.frame.scope == "target_window"
    assert bound.frame.hwnd == target.hwnd
    assert bound.frame.region == target.window_region
    assert bound.broker_state.target.live is True
    assert bound.broker_state.frame.scope == "target_window"
    assert current.authority_key == bound.authority_key
    assert observer.capture_calls == observer.commit_calls == 2
    assert len(registry) == 2
    assert desktop_reader.calls == desktop_calls
    assert len(target_reader.calls) >= 3
    assert set(target_reader.calls) == {
        (target.hwnd, target.binding_id, target.epoch),
    }


@pytest.mark.parametrize(
    "kwargs",
    (
        {"hwnd": 0, "region": DESKTOP_REGION, "epoch": 0},
        {"hwnd": 1, "region": Region(0, 0, 0, 1080), "epoch": 0},
        {"hwnd": 1, "region": DESKTOP_REGION, "epoch": -1},
        {"hwnd": 1, "region": DESKTOP_REGION, "epoch": False},
        {"hwnd": 1, "region": Region(0.0, 0, 1920, 1080), "epoch": 0},
    ),
)
def test_desktop_observation_identity_rejects_invalid_topology(kwargs) -> None:
    with pytest.raises((TypeError, ValueError)):
        DesktopObservationIdentity(**kwargs)
