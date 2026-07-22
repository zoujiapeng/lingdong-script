from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    FinalPointerGuardDecision,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.agent.frame_authority import FrameAuthority, FrameState
from winpilot.utils.image_capture import VisualCaptureScope
from winpilot.utils.screenshot import Region
from winpilot.vnext.actor import ActorDecisionEnvelope, parse_actor_proposal
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.evidence import (
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    EvidenceRegistryClosed,
    RegistryFrameEvidenceProjector,
    frame_identity_payload_hash,
)
from winpilot.vnext.mission import EvidenceRef
from winpilot.vnext.runtime import (
    ControllerOutcome,
    MutationPermitAuthority,
    RuntimeFrame,
)
from winpilot.visual_runtime.atomic_click_controller import AtomicDesktopController
from winpilot.visual_runtime.atomic_post_action_bridge import (
    AtomicPostActionBridge,
    AtomicPostActionBridgeError,
)
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionReason,
    CommitAdmissionResult,
    CommitAdmissionStatus,
    CommitEnvironment,
    FrameCommitIdentity,
    TargetCommitIdentity,
)
from winpilot.visual_runtime.frame_observer import FrameObserver


IMAGE_BEFORE = "YmVmb3Jl"
IMAGE_AFTER = "YWZ0ZXI="
REGION = Region(10, 20, 800, 600)


def _authority() -> tuple[FrameAuthority, FrameState]:
    authority = FrameAuthority()
    frame = authority.commit(
        epoch=3,
        hwnd=101,
        region=(REGION.left, REGION.top, REGION.width, REGION.height),
        scope="target_window",
        reason="test_pre_action",
        submitted_size=(800, 600),
        image_b64=IMAGE_BEFORE,
        captured_at=1.0,
    )
    return authority, frame


def _identity(frame: FrameState) -> FrameCommitIdentity:
    return FrameCommitIdentity(
        frame.frame_id,
        frame.epoch,
        frame.hwnd,
        frame.region,
        frame.scope,
        frame.submitted_size,
        frame.image_sha256,
    )


def _envelope(
    frame: FrameState,
    *,
    run_id: str = "run-1",
    action: str = "click",
) -> ActorDecisionEnvelope:
    return ActorDecisionEnvelope(
        decision_id="decision-1",
        mission_id="mission-1",
        run_id=run_id,
        mission_revision=4,
        obligation_id="obligation-1",
        frame=_identity(frame),
        target=TargetCommitIdentity(
            "target-1", 101, 101, 7, 3, frame.region,
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=(),
        allowed_evidence_scope=("pre-frame-evidence",),
        proposal=parse_actor_proposal({
            "format": "actor-proposal/0",
            "kind": "desktop_action",
            "action": action,
            "args": {
                "image_x": 400,
                "image_y": 300,
                **(
                    {"direction": "down", "notches": 3}
                    if action == "scroll"
                    else {}
                ),
            },
            "local_intent": "activate the visible semantic target",
        }),
    )


def _admission(envelope: ActorDecisionEnvelope) -> CommitAdmissionResult:
    return CommitAdmissionResult(
        envelope.decision_id,
        CommitAdmissionStatus.ADMITTED,
        CommitAdmissionReason.ADMITTED,
        True,
    )


def _receipt() -> RawActionReceipt:
    return RawActionReceipt(
        "click",
        MutationOutcome.APPLIED,
        RawBackendResult(True, {"sent": 1}),
    )


def _scroll_receipt() -> RawActionReceipt:
    return RawActionReceipt(
        "scroll",
        MutationOutcome.APPLIED,
        RawBackendResult(True, {"sent_notches": -3}),
    )


def _observer(
    authority: FrameAuthority,
    *,
    capture=lambda _hwnd, **_kwargs: IMAGE_AFTER,
) -> FrameObserver:
    return FrameObserver(
        authority,
        capture=capture,
        select_scope=lambda *_args, **_kwargs: VisualCaptureScope(
            REGION,
            "target_window",
            "post_action_target",
        ),
        virtual_desktop=lambda: Region(0, 0, 1920, 1080),
        captured_at=lambda: 2.0,
    )


def _bridge(
    authority: FrameAuthority,
    registry: EvidenceRegistry,
    *,
    capture=lambda _hwnd, **_kwargs: IMAGE_AFTER,
    post_input_settle_delay_s: float = 0.0,
    wait=lambda _seconds: None,
) -> AtomicPostActionBridge:
    return AtomicPostActionBridge(
        frame_authority=authority,
        observer=_observer(authority, capture=capture),
        evidence=registry,
        post_input_settle_delay_s=post_input_settle_delay_s,
        wait=wait,
    )


class _Observation:
    def observe_commit(self, request):
        return CommitEnvironment(
            target=request.target,
            pointer_released=True,
            mutation_allowed=True,
            stop_requested=False,
            canceled=False,
            foreground_hwnd=request.target.hwnd,
            foreground_root_hwnd=request.target.root_hwnd,
            foreground_process_id=request.target.process_id,
            hit_test_point=request.screen_point,
            hit_hwnd=request.target.hwnd,
            hit_root_hwnd=request.target.root_hwnd,
            hit_process_id=request.target.process_id,
        )


class _FinalGuard:
    def check_before_pointer_input(self, _request, _screen_point):
        return FinalPointerGuardDecision.ADMITTED


def _admitted_backend(calls: list[tuple]) -> AdmittedPhysicalClickBackend:
    def execute(x, y, *, button, count, mechanism, hwnd, final_guard):
        decision = final_guard(x, y)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(False, {"final_guard": decision.value})
        calls.append((x, y, button, count, mechanism, hwnd))
        return RawBackendResult(True, {"sent": 1})

    return AdmittedPhysicalClickBackend(execute)


def _controller(
    envelope: ActorDecisionEnvelope,
    authority: FrameAuthority,
    queue: DesktopActionQueue,
    bridge: AtomicPostActionBridge,
    raw_calls: list[tuple],
) -> AtomicDesktopController:
    permits = MutationPermitAuthority(envelope.mission_id, envelope.run_id)
    permits.issue(envelope, turn_index=0, still_current=lambda: True)
    return AtomicDesktopController(
        action_queue=queue,
        frame_authority=authority,
        permits=permits,
        observation=_Observation(),
        final_guard=_FinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=_admitted_backend(raw_calls),
        )),
        post_action=bridge,
        enabled=True,
    )


def test_bridge_cas_commits_and_registers_exact_projectable_frame_evidence() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    projector = RegistryFrameEvidenceProjector(registry)
    bridge = _bridge(authority, registry)

    assert projector.evidence_refs_for(_identity(before)) == ()
    post = bridge.observe_after(
        envelope,
        admission=_admission(envelope),
        raw_receipt=_receipt(),
    )

    committed = authority.current
    assert committed is not None
    assert committed.frame_id == before.frame_id + 1
    assert committed.image_b64 == IMAGE_AFTER
    assert post.post_frame_id == committed.frame_id
    assert post.visible_effect == ""

    evidence, = registry.snapshot()
    identity = _identity(committed)
    assert evidence.id == post.evidence_refs[0]
    assert evidence.mission_id == envelope.mission_id
    assert evidence.run_id == envelope.run_id
    assert evidence.obligation_id == envelope.obligation_id
    assert evidence.action_id == envelope.decision_id
    assert evidence.frame_id == committed.frame_id
    assert evidence.kind == POST_ACTION_FRAME_EVIDENCE_KIND
    assert evidence.trust == "deterministic"
    assert evidence.payload_hash == frame_identity_payload_hash(identity)
    assert projector.evidence_refs_for(identity) == post.evidence_refs
    assert projector.evidence_refs_for(
        replace(identity, image_sha256="0" * 64)
    ) == ()

    runtime_frame = RuntimeFrame(
        frame_id=committed.frame_id,
        epoch=committed.epoch,
        hwnd=committed.hwnd,
        region=committed.region,
        scope=committed.scope,
        submitted_size=committed.submitted_size,
        image_b64=committed.image_b64,
        image_sha256=committed.image_sha256,
        evidence_refs=projector.evidence_refs_for(identity),
    )
    assert runtime_frame.evidence_refs == post.evidence_refs


def test_bridge_accepts_the_exact_scroll_receipt_for_a_scroll_action() -> None:
    authority, before = _authority()
    envelope = _envelope(before, action="scroll")
    registry = EvidenceRegistry("mission-1", "run-1")
    bridge = _bridge(authority, registry)

    post = bridge.observe_after(
        envelope,
        admission=_admission(envelope),
        raw_receipt=_scroll_receipt(),
    )

    evidence, = registry.snapshot()
    assert post.post_frame_id == before.frame_id + 1
    assert evidence.action_id == envelope.decision_id
    with pytest.raises(AtomicPostActionBridgeError, match="exact raw receipt"):
        next_authority, next_before = _authority()
        wrong = _envelope(next_before, action="scroll")
        AtomicPostActionBridge(
            frame_authority=next_authority,
            observer=_observer(next_authority),
            evidence=EvidenceRegistry("mission-1", "run-1"),
        ).observe_after(
            wrong,
            admission=_admission(wrong),
            raw_receipt=_receipt(),
        )


def test_bridge_waits_once_before_capturing_completed_input() -> None:
    authority, before = _authority()
    envelope = _envelope(before, action="scroll")
    registry = EvidenceRegistry("mission-1", "run-1")
    ordering: list[str] = []

    def capture(_hwnd, **_kwargs):
        ordering.append("capture")
        return IMAGE_AFTER

    def wait(seconds: float) -> None:
        assert seconds == 0.12
        ordering.append("wait")

    bridge = _bridge(
        authority,
        registry,
        capture=capture,
        post_input_settle_delay_s=0.12,
        wait=wait,
    )

    post = bridge.observe_after(
        envelope,
        admission=_admission(envelope),
        raw_receipt=_scroll_receipt(),
    )

    assert ordering == ["wait", "capture"]
    assert authority.current is not None
    assert authority.current.image_b64 == IMAGE_AFTER
    assert post.post_frame_id == authority.current.frame_id


def test_bridge_does_not_wait_when_input_did_not_execute() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    capture_calls: list[bool] = []
    waits: list[float] = []
    bridge = _bridge(
        authority,
        registry,
        capture=lambda _hwnd, **_kwargs: capture_calls.append(True) or IMAGE_AFTER,
        post_input_settle_delay_s=0.12,
        wait=waits.append,
    )

    rejected = CommitAdmissionResult(
        envelope.decision_id,
        CommitAdmissionStatus.NOT_STARTED,
        CommitAdmissionReason.POINTER_NOT_RELEASED,
        False,
    )

    bridge.observe_after(
        envelope,
        admission=rejected,
        raw_receipt=None,
    )

    assert capture_calls == [True]
    assert waits == []


def test_not_started_admission_still_gets_a_fresh_bound_frame_without_receipt() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    bridge = _bridge(authority, registry)
    rejected = CommitAdmissionResult(
        envelope.decision_id,
        CommitAdmissionStatus.NOT_STARTED,
        CommitAdmissionReason.POINTER_NOT_RELEASED,
        False,
    )

    post = bridge.observe_after(
        envelope,
        admission=rejected,
        raw_receipt=None,
    )

    evidence, = registry.snapshot()
    assert post.post_frame_id == before.frame_id + 1
    assert evidence.action_id == envelope.decision_id
    assert evidence.frame_id == post.post_frame_id
    assert post.visible_effect == ""


def test_atomic_controller_keeps_bridge_in_physical_lease_but_outside_frame_pin() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    queue = DesktopActionQueue()
    lease_snapshots: list[list[dict]] = []

    def capture(_hwnd, **_kwargs):
        lease_snapshots.append(queue.status()["active_resources"])
        return IMAGE_AFTER

    bridge = _bridge(authority, registry, capture=capture)
    raw_calls: list[tuple] = []
    controller = _controller(envelope, authority, queue, bridge, raw_calls)

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert transition.visible_effect == ""
    assert len(raw_calls) == 1
    assert len(registry) == 1
    assert authority.current is not before
    assert authority.current.frame_id == before.frame_id + 1
    # A successful FrameObserver.commit proves the callback no longer owned
    # FrameAuthority's pin; the queue snapshot proves the physical lease held.
    assert len(lease_snapshots) == 1
    assert any(
        "physical" in lease["resources"]
        for lease in lease_snapshots[0]
    )


def test_atomic_controller_turns_real_bridge_failure_into_unknown_without_replay() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    queue = DesktopActionQueue()
    bridge = _bridge(authority, registry, capture=lambda *_args, **_kwargs: "")
    raw_calls: list[tuple] = []
    controller = _controller(envelope, authority, queue, bridge, raw_calls)

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.UNKNOWN
    assert transition.input_completed is None
    assert transition.evidence_refs == ()
    assert transition.post_frame_id is None
    assert len(raw_calls) == 1
    assert authority.current is before
    assert registry.snapshot() == ()


def test_stale_controller_with_real_bridge_preserves_proven_non_execution() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    authority.commit(
        epoch=before.epoch,
        hwnd=before.hwnd,
        region=before.region,
        scope=before.scope,
        reason="newer_observation",
        submitted_size=before.submitted_size,
        image_b64="bmV3ZXI=",
        captured_at=1.5,
        expected_previous_frame_id=before.frame_id,
    )
    registry = EvidenceRegistry("mission-1", "run-1")
    raw_calls: list[tuple] = []
    controller = _controller(
        envelope,
        authority,
        DesktopActionQueue(),
        _bridge(authority, registry),
        raw_calls,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert transition.post_frame_id is None
    assert transition.evidence_refs == ()
    assert raw_calls == []
    assert registry.snapshot() == ()
    assert authority.current is not before
    assert authority.current.reason == "newer_observation"


def test_bridge_rejects_wrong_owner_or_run_before_capture() -> None:
    authority, before = _authority()
    other_authority = FrameAuthority()
    registry = EvidenceRegistry("mission-1", "run-1")
    with pytest.raises(TypeError, match="share one FrameAuthority"):
        AtomicPostActionBridge(
            frame_authority=authority,
            observer=_observer(other_authority),
            evidence=registry,
        )

    captures: list[bool] = []
    bridge = _bridge(
        authority,
        registry,
        capture=lambda *_args, **_kwargs: captures.append(True) or IMAGE_AFTER,
    )
    wrong_run = _envelope(before, run_id="run-2")
    with pytest.raises(AtomicPostActionBridgeError, match="outside"):
        bridge.observe_after(
            wrong_run,
            admission=_admission(wrong_run),
            raw_receipt=_receipt(),
        )

    assert captures == []
    assert authority.current is before
    assert registry.snapshot() == ()


def test_concurrent_frame_commit_fails_cas_without_registering_evidence() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")

    def racing_capture(_hwnd, **_kwargs):
        authority.commit(
            epoch=3,
            hwnd=101,
            region=before.region,
            scope=before.scope,
            reason="concurrent_observer",
            submitted_size=before.submitted_size,
            image_b64="Y29uY3VycmVudA==",
            captured_at=1.5,
            expected_previous_frame_id=before.frame_id,
        )
        return IMAGE_AFTER

    bridge = _bridge(authority, registry, capture=racing_capture)

    with pytest.raises(RuntimeError, match="changed before commit"):
        bridge.observe_after(
            envelope,
            admission=_admission(envelope),
            raw_receipt=_receipt(),
        )

    assert authority.current is not before
    assert authority.current.reason == "concurrent_observer"
    assert registry.snapshot() == ()


def test_capture_or_registry_failure_never_fabricates_evidence() -> None:
    authority, before = _authority()
    envelope = _envelope(before)
    registry = EvidenceRegistry("mission-1", "run-1")
    empty_capture = _bridge(authority, registry, capture=lambda *_args, **_kwargs: "")

    with pytest.raises(AtomicPostActionBridgeError, match="capture failed"):
        empty_capture.observe_after(
            envelope,
            admission=_admission(envelope),
            raw_receipt=_receipt(),
        )
    assert authority.current is before
    assert registry.snapshot() == ()

    registry.close()
    committing_bridge = _bridge(authority, registry)
    with pytest.raises(EvidenceRegistryClosed):
        committing_bridge.observe_after(
            envelope,
            admission=_admission(envelope),
            raw_receipt=_receipt(),
        )
    assert authority.current is not before
    assert authority.current.frame_id == before.frame_id + 1
    assert registry.snapshot() == ()


def test_projector_is_dynamic_read_only_and_filters_near_matches() -> None:
    authority, before = _authority()
    registry = EvidenceRegistry("mission-1", "run-1")
    projector = RegistryFrameEvidenceProjector(registry)
    identity = _identity(before)
    payload_hash = frame_identity_payload_hash(identity)

    registry.append_many((
        EvidenceRef(
            id="wrong-trust",
            mission_id="mission-1",
            run_id="run-1",
            obligation_id="obligation-1",
            action_id="decision-1",
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            trust="actor_claim",
            frame_id=before.frame_id,
            payload_hash=payload_hash,
        ),
        EvidenceRef(
            id="wrong-payload",
            mission_id="mission-1",
            run_id="run-1",
            obligation_id="obligation-1",
            action_id="decision-1",
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            frame_id=before.frame_id,
            payload_hash="not-the-frame-identity",
        ),
        EvidenceRef(
            id="missing-action",
            mission_id="mission-1",
            run_id="run-1",
            obligation_id="obligation-1",
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            frame_id=before.frame_id,
            payload_hash=payload_hash,
        ),
    ))
    assert projector.evidence_refs_for(identity) == ()

    exact = EvidenceRef(
        id="exact",
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="obligation-1",
        action_id="decision-1",
        kind=POST_ACTION_FRAME_EVIDENCE_KIND,
        trust="deterministic",
        frame_id=before.frame_id,
        payload_hash=payload_hash,
    )
    registry.append(exact)
    registry.close()
    snapshot = registry.snapshot()

    assert projector.evidence_refs_for(identity) == (exact.id,)
    assert registry.snapshot() == snapshot


def test_bridge_and_projector_have_no_bus_or_gui_mutation_owner() -> None:
    package = Path(__file__).parents[1] / "winpilot"
    paths = (
        package / "visual_runtime" / "atomic_post_action_bridge.py",
        package / "vnext" / "evidence" / "frame_projection.py",
    )
    forbidden_imports = {
        "DesktopActionQueue",
        "RawDeviceExecutor",
        "FastGuiController",
        "BUS",
    }
    mutation_calls = {
        "click", "double_click", "drag", "scroll", "type_text", "hotkey",
    }

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert imported.isdisjoint(forbidden_imports)
        assert calls.isdisjoint(mutation_calls)
