"""Focused fake-port tests for the default-unreachable atomic vNext bridge."""
from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Callable

import cv2
import numpy as np
import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalDragBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.desktop_queue import DesktopActionCanceled
from winpilot.action.replay_policy import MutationOutcome
from winpilot.agent.frame_authority import FrameAuthority
from winpilot.utils.screenshot import Region
from winpilot.visual_runtime import (
    FrameObserver,
    NormalizedFrameCoordinateMapper,
    SubmittedImageCoordinateMapper,
)
from winpilot.vnext.actor import (
    ACTOR_ENVELOPE_FORMAT,
    ACTOR_PROPOSAL_FORMAT,
    ActorDecisionEnvelope,
    InvalidActorEnvelope,
)
from winpilot.vnext.actor.envelope import _decision_id
from winpilot.vnext.desktop_adapter import (
    AtomicDesktopAdapter,
    DesktopWorkspace,
    LiveTargetSnapshot,
    WorkspaceSurface,
)
from winpilot.vnext.evidence_plane import (
    FrameArtifactWriter,
    RuntimeEvidenceJournal,
    RuntimeEvidencePlane,
    RuntimeEvidencePlaneError,
)
import winpilot.vnext.evidence_plane.journal as evidence_journal_module
from winpilot.vnext.mission import TargetBinding
from winpilot.vnext.runtime import (
    ControllerOutcome,
    MutationPermitAuthority,
    RuntimeCommitIdentity,
    RuntimeFrameIdentity,
    RuntimeTargetIdentity,
    call_once_with_permit,
)


MISSION_ID = "mission-atomic"
RUN_ID = "run-atomic"
OBLIGATION_ID = "full-goal"
HWND = 901
PROCESS_ID = 77
EPOCH = 4
REGION = (10, 20, 100, 80)
POINT = (60, 60)
CURRENT_EVIDENCE_ID = "evidence-current-frame"


def _jpeg(level: int) -> str:
    image = np.full((REGION[3], REGION[2], 3), level, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _map_submitted_test_point(image_x: object, image_y: object) -> tuple[int, int]:
    frame_arguments, _ = SubmittedImageCoordinateMapper(
        (REGION[2], REGION[3])
    ).map_point_pair(
        {"image_x": image_x, "image_y": image_y},
        image_keys=("image_x", "image_y"),
        frame_keys=("frame_x", "frame_y"),
        context="atomic adapter test",
    )
    return NormalizedFrameCoordinateMapper(REGION).map_point(
        frame_arguments["frame_x"],
        frame_arguments["frame_y"],
    )


@dataclass
class _Probe:
    current: LiveTargetSnapshot
    hit_root: int | None = HWND
    expected_hit_point: (
        tuple[int, int] | tuple[tuple[int, int], ...]
    ) = POINT
    on_snapshot: Callable[[int], None] | None = None
    on_hit_test: Callable[[int], None] | None = None

    def __post_init__(self) -> None:
        self.snapshot_calls = 0
        self.hit_test_calls = 0

    def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None:
        assert hwnd == HWND
        self.snapshot_calls += 1
        if self.on_snapshot is not None:
            self.on_snapshot(self.snapshot_calls)
        return self.current

    def hit_test_root(self, x: int, y: int) -> int | None:
        expected = self.expected_hit_point
        if expected and isinstance(expected[0], tuple):
            assert (x, y) in expected
        else:
            assert (x, y) == expected
        self.hit_test_calls += 1
        if self.on_hit_test is not None:
            self.on_hit_test(self.hit_test_calls)
        return self.hit_root


@dataclass
class _Trace:
    fail: bool = False

    def __post_init__(self) -> None:
        self.records: list[
            tuple[RawActionReceipt, dict[str, Any]]
        ] = []

    def record(
        self,
        receipt: RawActionReceipt,
        *,
        metadata,
    ) -> None:
        self.records.append((receipt, dict(metadata)))
        if self.fail:
            raise RuntimeError("trace unavailable")


@dataclass
class _RawHarness:
    result: bool | None = True
    before_guard: Callable[[], None] | None = None
    between_double_guards: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def click(
        self,
        x,
        y,
        *,
        button,
        count,
        mechanism,
        hwnd,
        final_guard,
    ) -> RawBackendResult:
        self.calls.append(
            (
                "click",
                (x, y, button, count, mechanism, hwnd),
            )
        )
        if count is ClickCount.DOUBLE and self.between_double_guards:
            first = final_guard(x, y)
            if first is not FinalPointerGuardDecision.ADMITTED:
                return RawBackendResult(
                    False,
                    {"guard_decision": first.value},
                )
            hook = self.between_double_guards
            self.between_double_guards = None
            hook()
            second = final_guard(x, y)
            if second is not FinalPointerGuardDecision.ADMITTED:
                return RawBackendResult(
                    None,
                    {"guard_decision": second.value},
                )
            return RawBackendResult(
                self.result,
                {"via": "physical_sendinput"},
            )
        self._run_before_guard()
        decision = final_guard(x, y)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        return RawBackendResult(self.result, {"via": "physical_sendinput"})

    def scroll(
        self,
        x,
        y,
        amount,
        *,
        hwnd,
        final_guard,
    ) -> RawBackendResult:
        self.calls.append(("scroll", (x, y, amount, hwnd)))
        self._run_before_guard()
        decision = final_guard(x, y)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        return RawBackendResult(self.result, {"via": "physical_sendinput"})

    def drag(
        self,
        x1,
        y1,
        x2,
        y2,
        *,
        hwnd,
        points=(),
        final_guard,
        pointer_already_held=False,
        keep_holding=False,
        hold_token=None,
        cleanup_only=False,
    ) -> RawBackendResult:
        if cleanup_only:
            self.calls.append(("drag_cleanup", (hold_token, hwnd)))
            return RawBackendResult(
                True,
                {
                    "via": "physical_sendinput",
                    "segment_phase": "cleanup",
                    "held_before": True,
                    "held_after": False,
                },
            )
        arguments = (
            (
                x1,
                y1,
                x2,
                y2,
                tuple(points),
                hwnd,
                pointer_already_held,
                keep_holding,
                hold_token,
            )
            if pointer_already_held or keep_holding
            else (
                (x1, y1, x2, y2, tuple(points), hwnd)
                if points
                else (x1, y1, x2, y2, hwnd)
            )
        )
        self.calls.append(("drag", arguments))
        self._run_before_guard()
        decision = final_guard(x1, y1)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        facts: dict[str, Any] = {"via": "physical_sendinput"}
        if pointer_already_held or keep_holding:
            facts.update(
                segment_phase=(
                    "continue"
                    if pointer_already_held and keep_holding
                    else "finish"
                    if pointer_already_held
                    else "begin"
                ),
                held_before=pointer_already_held,
                held_after=keep_holding,
            )
        return RawBackendResult(self.result, facts)

    def type_text(
        self,
        text,
        *,
        hwnd,
        final_guard,
    ) -> RawBackendResult:
        self.calls.append(("type_text", (text, hwnd)))
        self._run_before_guard()
        decision = final_guard()
        if decision is not FinalKeyboardGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        return RawBackendResult(
            self.result,
            {
                "via": "physical_sendinput",
                "requested_codepoints": len(text),
                "completed_codepoints": len(text),
            },
        )

    def hotkey(
        self,
        chord,
        *,
        hwnd,
        final_guard,
    ) -> RawBackendResult:
        self.calls.append(("hotkey", (chord, hwnd)))
        self._run_before_guard()
        decision = final_guard()
        if decision is not FinalKeyboardGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        return RawBackendResult(
            self.result,
            {
                "via": "physical_sendinput",
                "key_count": len(chord.keys),
            },
        )

    def executor(self) -> RawDeviceExecutor:
        return RawDeviceExecutor(
            RawDeviceBackends(
                admitted_physical_click=AdmittedPhysicalClickBackend(
                    self.click
                ),
                admitted_physical_drag=AdmittedPhysicalDragBackend(
                    self.drag
                ),
                admitted_physical_scroll=AdmittedPhysicalScrollBackend(
                    self.scroll
                ),
                admitted_physical_type_text=AdmittedPhysicalTypeTextBackend(
                    self.type_text
                ),
                admitted_physical_hotkey=AdmittedPhysicalHotkeyBackend(
                    self.hotkey
                ),
            )
        )

    def _run_before_guard(self) -> None:
        hook = self.before_guard
        self.before_guard = None
        if hook is not None:
            hook()


@dataclass
class _Harness:
    authority: FrameAuthority
    observer: FrameObserver
    binding: TargetBinding
    probe: _Probe
    permits: MutationPermitAuthority
    raw: _RawHarness
    trace: _Trace
    wait_calls: list[float]
    adapter: AtomicDesktopAdapter
    envelope: ActorDecisionEnvelope
    commit_identity: RuntimeCommitIdentity
    runtime_evidence: RuntimeEvidencePlane | None = None
    evidence_journal: RuntimeEvidenceJournal | None = None
    frame_artifact_writer: FrameArtifactWriter | None = None
    evidence_run_directory: Path | None = None
    before_image_b64: str | None = None
    after_image_b64: str | None = None

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        still_current: Callable[[], bool] = lambda: True,
    ):
        return call_once_with_permit(
            authority=self.permits,
            envelope=self.envelope,
            turn_index=0,
            commit_identity=self.commit_identity,
            still_current=still_current,
            execute=lambda envelope: self.adapter.execute(
                envelope,
                stop_event=stop_event,
            ),
        )


def _proposal(action: str) -> dict[str, Any]:
    args = {
        "click": {"image_x": 50.0, "image_y": 40.0},
        "double_click": {"image_x": 50.0, "image_y": 40.0},
        "drag": {
            "image_x1": 50.0,
            "image_y1": 40.0,
            "image_x2": 80.0,
            "image_y2": 60.0,
        },
        "scroll": {
            "image_x": 50.0,
            "image_y": 40.0,
            "amount": -240,
        },
        "type_text": {"text": "hello"},
        "hotkey": {"keys": ["ctrl", "l"]},
    }[action]
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": action,
        "args": args,
        "local_intent": "perform one local action",
    }


def _projection_id(state, binding: TargetBinding, action: str) -> str:
    material = {
        "format": "broker-projection/1",
        "mission_id": MISSION_ID,
        "run_id": RUN_ID,
        "catalog_fingerprint": "2" * 64,
        "profile_id": "fixed-standard",
        "state_fingerprint": "3" * 64,
        "mission_revision": 0,
        "obligation_id": OBLIGATION_ID,
        "turn_index": 0,
        "execution_route": "visual_actor",
        "executor_id": None,
        "target_binding_id": binding.binding_id,
        "frame_id": state.frame_id,
        "frame_scope": state.scope,
        "pointer_lease_id": None,
        "tool_names": [action],
        "active_grant_ids": [],
        "missing_capabilities": [],
        "projection_reasons": [],
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "projection-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]


def _envelope(state, binding: TargetBinding, action: str):
    material = {
        "format": ACTOR_ENVELOPE_FORMAT,
        "mission_id": MISSION_ID,
        "run_id": RUN_ID,
        "mission_revision": 0,
        "mission_projection_sha256": "1" * 64,
        "accepted_mission_event_ids": ["event-target-bound"],
        "obligation_id": OBLIGATION_ID,
        "turn_index": 0,
        "catalog_fingerprint": "2" * 64,
        "profile_id": "fixed-standard",
        "state_fingerprint": "3" * 64,
        "frame_id": state.frame_id,
        "frame_epoch": state.epoch,
        "frame_hwnd": state.hwnd,
        "frame_scope": state.scope,
        "frame_region": list(state.region),
        "submitted_image_size": list(state.submitted_size),
        "frame_image_sha256": state.image_sha256,
        "target_binding_id": binding.binding_id,
        "pointer_disposition": "released",
        "pointer_lease_id": None,
        "broker_projection_id": _projection_id(state, binding, action),
        "capability_grant_ids": [],
        "allowed_evidence_scope": [CURRENT_EVIDENCE_ID],
        "proposal": _proposal(action),
    }
    return ActorDecisionEnvelope.from_dict(
        {"decision_id": _decision_id(material), **material}
    )


def _replace_envelope(
    envelope: ActorDecisionEnvelope,
    **changes: Any,
) -> ActorDecisionEnvelope:
    raw = envelope.to_dict()
    raw.update(changes)
    material = dict(raw)
    material.pop("decision_id")
    raw["decision_id"] = _decision_id(material)
    return ActorDecisionEnvelope.from_dict(raw)


def _replace_action_args(
    envelope: ActorDecisionEnvelope,
    args: dict[str, Any],
) -> ActorDecisionEnvelope:
    raw = envelope.to_dict()
    raw["proposal"]["args"] = args
    material = dict(raw)
    material.pop("decision_id")
    raw["decision_id"] = _decision_id(material)
    return ActorDecisionEnvelope.from_dict(raw)


def _broker_projection(
    envelope: ActorDecisionEnvelope,
) -> dict[str, Any]:
    return {
        "format": "broker-projection/1",
        "projection_id": envelope.broker_projection_id,
        "mission_id": envelope.mission_id,
        "run_id": envelope.run_id,
        "catalog_fingerprint": envelope.catalog_fingerprint,
        "profile_id": envelope.profile_id,
        "state_fingerprint": envelope.state_fingerprint,
        "mission_revision": envelope.mission_revision,
        "obligation_id": envelope.obligation_id,
        "turn_index": envelope.turn_index,
        "execution_route": "visual_actor",
        "executor_id": None,
        "target_binding_id": envelope.target_binding_id,
        "frame_id": envelope.frame_id,
        "frame_scope": envelope.frame_scope,
        "pointer_lease_id": envelope.pointer_lease_id,
        "tool_names": [envelope.proposal.action],
        "active_grants": [
            {"grant_id": grant_id}
            for grant_id in envelope.capability_grant_ids
        ],
        "missing_capabilities": [],
        "projection_reasons": [],
    }


def _harness(
    action: str,
    *,
    raw_result: bool | None = True,
    trace_fail: bool = False,
    post_input_settle_delay_s: float = 0.12,
    wait: Callable[[float], None] | None = None,
    with_runtime_evidence: bool = False,
    evidence_run_directory: Path | None = None,
    action_args: dict[str, Any] | None = None,
    expected_hit_point: tuple[int, int] = POINT,
) -> _Harness:
    authority = FrameAuthority()
    before_b64 = _jpeg(20)
    after_b64 = _jpeg(220)
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=REGION,
        scope="target_window",
        reason="prebound",
        submitted_size=(REGION[2], REGION[3]),
        image_b64=before_b64,
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: after_b64,
        virtual_desktop=lambda: Region(0, 0, 1920, 1080),
        captured_at=lambda: 2.0,
    )
    binding = TargetBinding(
        binding_id="target-binding",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="C:/app.exe",
        title_snapshot="App",
        established_by_evidence_ref="evidence-target-bound",
        epoch=EPOCH,
    )
    drag_guard_points: tuple[tuple[int, int], ...] | None = None
    if action == "drag":
        drag_arguments = action_args or _proposal("drag")["args"]
        drag_guard_points = (
            expected_hit_point,
            *(
                _map_submitted_test_point(point[0], point[1])
                for point in drag_arguments.get("image_points", [])
            ),
            _map_submitted_test_point(
                drag_arguments["image_x2"],
                drag_arguments["image_y2"],
            ),
        )
    probe = _Probe(
        LiveTargetSnapshot(
            hwnd=HWND,
            root_hwnd=HWND,
            process_id=PROCESS_ID,
            executable_identity=binding.executable_identity,
            region=REGION,
            foreground_root_hwnd=HWND,
        ),
        expected_hit_point=drag_guard_points or expected_hit_point,
    )
    permits = MutationPermitAuthority(MISSION_ID, RUN_ID)
    raw = _RawHarness(raw_result)
    trace = _Trace(trace_fail)
    if evidence_run_directory is not None:
        with_runtime_evidence = True
        evidence_journal = RuntimeEvidenceJournal.create(
            evidence_run_directory,
            MISSION_ID,
            RUN_ID,
        )
        frame_artifact_writer = FrameArtifactWriter.create(
            evidence_run_directory
        )
        runtime_evidence = RuntimeEvidencePlane(
            MISSION_ID,
            RUN_ID,
            clock_ns=lambda: 1_500_000_000,
            journal=evidence_journal,
        )
    else:
        evidence_journal = None
        frame_artifact_writer = None
        runtime_evidence = (
            RuntimeEvidencePlane(
                MISSION_ID,
                RUN_ID,
                clock_ns=lambda: 123_456_789,
            )
            if with_runtime_evidence
            else None
        )
    wait_calls: list[float] = []

    def settle_wait(delay: float) -> None:
        wait_calls.append(delay)
        if wait is not None:
            wait(delay)

    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=probe,
        mutation_permits=permits,
        raw_device=raw.executor(),
        receipt_trace_sink=trace,
        runtime_evidence_plane=runtime_evidence,
        frame_artifact_writer=frame_artifact_writer,
        post_input_settle_delay_s=post_input_settle_delay_s,
        wait=settle_wait,
    )
    envelope = _envelope(before, binding, action)
    if action_args is not None:
        envelope = _replace_action_args(envelope, action_args)
    if runtime_evidence is not None:
        runtime_evidence.record_actor_decision(
            actor_envelope=envelope.to_dict(),
            tool_schema={"type": "object"},
            broker_projection=_broker_projection(envelope),
        )
    frame_identity = RuntimeFrameIdentity(
        frame_id=before.frame_id,
        epoch=before.epoch,
        hwnd=before.hwnd,
        region=before.region,
        scope=before.scope,
        submitted_size=before.submitted_size,
        image_sha256=before.image_sha256,
    )
    return _Harness(
        authority=authority,
        observer=observer,
        binding=binding,
        probe=probe,
        permits=permits,
        raw=raw,
        trace=trace,
        wait_calls=wait_calls,
        adapter=adapter,
        envelope=envelope,
        commit_identity=RuntimeCommitIdentity(
            frame_identity,
            RuntimeTargetIdentity.from_binding(binding),
        ),
        runtime_evidence=runtime_evidence,
        evidence_journal=evidence_journal,
        frame_artifact_writer=frame_artifact_writer,
        evidence_run_directory=evidence_run_directory,
        before_image_b64=before_b64,
        after_image_b64=after_b64,
    )


def _record_controller_transition(
    harness: _Harness,
    transition: Any,
) -> None:
    plane = harness.runtime_evidence
    assert plane is not None
    current = harness.authority.current
    assert current is not None
    plane.record_controller_transition(
        decision_id=harness.envelope.decision_id,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=hashlib.sha256(
            harness.envelope.to_json().encode("utf-8")
        ).hexdigest(),
        transition=transition.to_dict(),
        post_frame_identity=RuntimeFrameIdentity(
            frame_id=current.frame_id,
            epoch=current.epoch,
            hwnd=current.hwnd,
            region=current.region,
            scope=current.scope,
            submitted_size=current.submitted_size,
            image_sha256=current.image_sha256,
        ).to_dict(),
    )


def _fail_journal_event(
    monkeypatch: pytest.MonkeyPatch,
    event_kind: str,
) -> None:
    original_write_all = evidence_journal_module._write_all
    needle = f'"event_kind":"{event_kind}"'.encode("ascii")

    def fail_selected_append(stream, value: bytes) -> None:
        if needle in bytes(value):
            raise OSError("synthetic durable append failure")
        original_write_all(stream, value)

    monkeypatch.setattr(
        evidence_journal_module,
        "_write_all",
        fail_selected_append,
    )


@pytest.mark.parametrize(
    ("action", "raw_name"),
    (
        ("click", "click"),
        ("double_click", "click"),
        ("drag", "drag"),
        ("scroll", "scroll"),
        ("type_text", "type_text"),
        ("hotkey", "hotkey"),
    ),
)
def test_exact_atomic_surface_completes_once_with_effect(
    action: str,
    raw_name: str,
) -> None:
    harness = _harness(action)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.execute_call_count == 1
    assert attempt.active_admission_closed is True
    transition = attempt.transition
    assert transition is not None
    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert transition.post_frame_id == 2
    assert len(transition.post_frame_evidence) == 1
    assert transition.action_effect is not None
    assert transition.action_effect_evidence is not None
    assert transition.action_effect.after_frame_evidence_ref == (
        transition.post_frame_evidence[0].id
    )
    assert len(harness.raw.calls) == 1
    assert harness.wait_calls == [0.12]
    assert harness.raw.calls[0][0] == raw_name
    assert harness.authority.current is not None
    assert harness.authority.current.frame_id == 2
    assert len(harness.trace.records) == 1
    receipt, metadata = harness.trace.records[0]
    assert receipt.outcome is MutationOutcome.APPLIED
    assert metadata["decision_id"] == harness.envelope.decision_id

    if action in {"click", "double_click"}:
        _, values = harness.raw.calls[0]
        assert values[:2] == POINT
        assert values[2].value == "left"
        assert values[3] is (
            ClickCount.DOUBLE
            if action == "double_click"
            else ClickCount.SINGLE
        )
        assert values[4] is ClickMechanism.PHYSICAL
    elif action == "scroll":
        assert harness.raw.calls[0][1] == (*POINT, -240, HWND)


def test_atomic_adapter_records_admission_and_single_raw_receipt() -> None:
    harness = _harness("click", with_runtime_evidence=True)
    plane = harness.runtime_evidence
    assert plane is not None

    attempt = harness.run()

    transition = attempt.transition
    assert transition is not None
    assert transition.outcome is ControllerOutcome.COMPLETED
    assert len(harness.raw.calls) == 1
    assert len(plane.admission_records) == 1
    admission = plane.admission_records[0]
    assert admission.decision_id == harness.envelope.decision_id
    assert admission.mutation_permit_id == attempt.permit_id
    assert (
        admission.controller_route
        == "vnext_atomic_desktop_adapter"
    )
    assert admission.accepted_timestamp_ns == 123_456_789
    assert plane.action_bindings == ()

    current = harness.authority.current
    assert current is not None
    plane.record_controller_transition(
        decision_id=harness.envelope.decision_id,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=hashlib.sha256(
            harness.envelope.to_json().encode("utf-8")
        ).hexdigest(),
        transition=transition.to_dict(),
        post_frame_identity=RuntimeFrameIdentity(
            frame_id=current.frame_id,
            epoch=current.epoch,
            hwnd=current.hwnd,
            region=current.region,
            scope=current.scope,
            submitted_size=current.submitted_size,
            image_sha256=current.image_sha256,
        ).to_dict(),
    )
    assert len(plane.action_bindings) == 1
    assert (
        plane.action_bindings[0].raw_action_receipt[
            "input_completed"
        ]
        is True
    )
    assert plane.action_bindings[0].physical_attempt_count == 1


@pytest.mark.parametrize(
    (
        "action",
        "submitted_arguments",
        "physical_arguments",
        "raw_device_route",
    ),
    (
        (
            "click",
            {"image_x": 50.0, "image_y": 40.0},
            {
                "kind": "pointer_click",
                "x": 60,
                "y": 60,
                "button": "left",
                "click_count": 1,
            },
            "raw_device.admitted_physical_click/0",
        ),
        (
            "double_click",
            {"image_x": 50.0, "image_y": 40.0},
            {
                "kind": "pointer_click",
                "x": 60,
                "y": 60,
                "button": "left",
                "click_count": 2,
            },
            "raw_device.admitted_physical_click/0",
        ),
        (
            "drag",
            {
                "image_x1": 50.0,
                "image_y1": 40.0,
                "image_x2": 80.0,
                "image_y2": 60.0,
            },
            {
                "kind": "pointer_drag",
                "x1": 60,
                "y1": 60,
                "x2": 90,
                "y2": 80,
                "button": "left",
            },
            "raw_device.admitted_physical_drag/0",
        ),
        (
            "drag",
            {
                "image_x1": 50.0,
                "image_y1": 40.0,
                "image_x2": 80.0,
                "image_y2": 60.0,
                "image_points": [[60.0, 20.0], [70.0, 50.0]],
            },
            {
                "kind": "pointer_drag",
                "x1": 60,
                "y1": 60,
                "x2": 90,
                "y2": 80,
                "button": "left",
                "points": [[70, 40], [80, 70]],
            },
            "raw_device.admitted_physical_drag/0",
        ),
        (
            "scroll",
            {
                "image_x": 37.0,
                "image_y": 29.0,
                "amount": -713,
            },
            {
                "kind": "wheel",
                "x": 47,
                "y": 49,
                "signed_wheel_amount": -713,
                "event_wheel_delta": -85_560,
            },
            "raw_device.admitted_physical_scroll/0",
        ),
        (
            "type_text",
            {"text": "PRIVATE-TEXT-admitted-8c71"},
            {
                "kind": "type_text",
                "text": "PRIVATE-TEXT-admitted-8c71",
            },
            "raw_device.admitted_physical_type_text/0",
        ),
        (
            "hotkey",
            {"keys": ["ctrl", "alt", "shift", "f11"]},
            {
                "kind": "hotkey",
                "modifiers": ["ctrl", "alt", "shift"],
                "key": "f11",
            },
            "raw_device.admitted_physical_hotkey/0",
        ),
    ),
)
def test_durable_attempt_records_exact_submitted_and_physical_arguments(
    tmp_path: Path,
    action: str,
    submitted_arguments: dict[str, Any],
    physical_arguments: dict[str, Any],
    raw_device_route: str,
) -> None:
    run_directory = tmp_path / "evidence-run"
    expected_hit_point = POINT
    if action in {"click", "double_click", "scroll"}:
        expected_hit_point = _map_submitted_test_point(
            submitted_arguments["image_x"],
            submitted_arguments["image_y"],
        )
        assert expected_hit_point == (
            physical_arguments["x"],
            physical_arguments["y"],
        )
    elif action == "drag":
        assert _map_submitted_test_point(
            submitted_arguments["image_x1"],
            submitted_arguments["image_y1"],
        ) == (physical_arguments["x1"], physical_arguments["y1"])
        assert _map_submitted_test_point(
            submitted_arguments["image_x2"],
            submitted_arguments["image_y2"],
        ) == (physical_arguments["x2"], physical_arguments["y2"])
        if "image_points" in submitted_arguments:
            assert [
                _map_submitted_test_point(*point)
                for point in submitted_arguments["image_points"]
            ] == [tuple(point) for point in physical_arguments["points"]]
    harness = _harness(
        action,
        action_args=submitted_arguments,
        evidence_run_directory=run_directory,
        expected_hit_point=expected_hit_point,
    )
    journal = harness.evidence_journal
    plane = harness.runtime_evidence
    writer = harness.frame_artifact_writer
    assert journal is not None
    assert plane is not None
    assert writer is not None

    try:
        result = harness.run()

        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.COMPLETED
        assert len(harness.raw.calls) == 1
        assert len(plane.physical_attempt_records) == 1
        attempt = plane.physical_attempt_records[0].to_dict()
        assert attempt["decision_id"] == harness.envelope.decision_id
        assert attempt["action_id"] == harness.envelope.decision_id
        assert attempt["action"] == action
        assert attempt["attempt_index"] == 1
        assert attempt["submitted_arguments"] == submitted_arguments
        assert attempt["physical_arguments"] == physical_arguments
        assert attempt["raw_device_route"] == raw_device_route
        assert attempt["mechanism"] == "physical_sendinput"
        raw_action, raw_arguments = harness.raw.calls[0]
        if action in {"click", "double_click"}:
            assert raw_action == "click"
            assert raw_arguments[:2] == (
                physical_arguments["x"],
                physical_arguments["y"],
            )
        elif action == "scroll":
            assert raw_action == "scroll"
            assert raw_arguments == (
                physical_arguments["x"],
                physical_arguments["y"],
                physical_arguments["signed_wheel_amount"],
                HWND,
            )
        elif action == "drag":
            assert raw_action == "drag"
            if "points" in physical_arguments:
                assert raw_arguments == (
                    physical_arguments["x1"],
                    physical_arguments["y1"],
                    physical_arguments["x2"],
                    physical_arguments["y2"],
                    tuple(
                        tuple(point)
                        for point in physical_arguments["points"]
                    ),
                    HWND,
                )
            else:
                assert raw_arguments == (
                    physical_arguments["x1"],
                    physical_arguments["y1"],
                    physical_arguments["x2"],
                    physical_arguments["y2"],
                    HWND,
                )
        elif action == "type_text":
            assert raw_action == "type_text"
            assert raw_arguments == (
                physical_arguments["text"],
                HWND,
            )
        else:
            assert raw_action == "hotkey"
            chord, hwnd = raw_arguments
            assert tuple(chord.keys) == tuple(submitted_arguments["keys"])
            assert hwnd == HWND

        references = {
            reference.role.value: reference
            for reference in plane.frame_artifact_references
        }
        assert set(references) == {"before", "after"}
        expected_images = {
            "before": harness.before_image_b64,
            "after": harness.after_image_b64,
        }
        expected_frames = {"before": 1, "after": 2}
        expected_timestamps = {
            "before": 1_000_000_000,
            "after": 2_000_000_000,
        }
        for role, reference in references.items():
            encoded = expected_images[role]
            assert encoded is not None
            expected_bytes = base64.b64decode(encoded)
            artifact_path = run_directory / reference.artifact_uri
            assert artifact_path.read_bytes() == expected_bytes
            assert reference.frame_id == expected_frames[role]
            assert (
                reference.capture_timestamp_ns
                == expected_timestamps[role]
            )
            assert reference.raw_jpeg_sha256 == hashlib.sha256(
                expected_bytes
            ).hexdigest()
            assert (
                reference.runtime_frame_image_sha256
                == hashlib.sha256(encoded.encode("ascii")).hexdigest()
            )
            assert (
                reference.frame_identity["image_sha256"]
                == reference.runtime_frame_image_sha256
            )
            reference_wire = reference.to_dict()
            assert writer.read_frame(
                mission_id=MISSION_ID,
                run_id=RUN_ID,
                decision_id=harness.envelope.decision_id,
                action_id=harness.envelope.decision_id,
                role=role,
                frame_identity=reference_wire["frame_identity"],
                artifact_uri=reference.artifact_uri,
                sha256=reference.raw_jpeg_sha256,
            ) == expected_bytes

        journal.close()
        replayed_journal = RuntimeEvidenceJournal.open_read_only(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )
        replayed = RuntimeEvidencePlane.from_journal(replayed_journal)
        assert (
            replayed.physical_attempt_records[0].to_dict()
            == attempt
        )
    finally:
        journal.close()


@pytest.mark.parametrize(
    ("event_kind", "receipt_error"),
    (
        ("admission", "evidence_admission_rejected"),
        ("physical_attempt", "evidence_attempt_rejected"),
    ),
)
def test_durable_pre_input_append_failure_never_calls_raw_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_kind: str,
    receipt_error: str,
) -> None:
    harness = _harness(
        "click",
        evidence_run_directory=tmp_path / "evidence-run",
    )
    journal = harness.evidence_journal
    assert journal is not None
    _fail_journal_event(monkeypatch, event_kind)

    try:
        result = harness.run()

        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.REJECTED
        assert result.transition.input_completed is False
        assert harness.raw.calls == []
        assert len(harness.trace.records) == 1
        receipt, _metadata = harness.trace.records[0]
        assert receipt.outcome is MutationOutcome.NOT_STARTED
        assert receipt.error_kind == receipt_error
        assert journal.writable is False
    finally:
        journal.close()


def test_durable_pre_admission_not_started_replays_as_incomplete(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "evidence-run"
    harness = _harness(
        "click",
        evidence_run_directory=run_directory,
    )
    journal = harness.evidence_journal
    assert journal is not None
    stopped = threading.Event()
    stopped.set()

    try:
        result = harness.run(stop_event=stopped)

        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.REJECTED
        assert harness.raw.calls == []
        journal.close()

        report = RuntimeEvidencePlane.rebuild_report(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )
        decision_id = harness.envelope.decision_id
        assert report.status.value == "incomplete"
        assert report.verification_failures == ()
        assert f"{decision_id}_raw_receipt" not in report.missing_evidence
        assert f"{decision_id}_admission" in report.missing_evidence
        assert f"{decision_id}_physical_attempt" in report.missing_evidence
        assert f"{decision_id}_action_binding" in report.missing_evidence
    finally:
        journal.close()


def test_durable_receipt_append_failure_is_unknown_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(
        "type_text",
        evidence_run_directory=tmp_path / "evidence-run",
    )
    journal = harness.evidence_journal
    plane = harness.runtime_evidence
    assert journal is not None
    assert plane is not None
    _fail_journal_event(monkeypatch, "raw_receipt")

    try:
        result = harness.run()

        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.UNKNOWN
        assert result.transition.input_completed is True
        assert len(harness.raw.calls) == 1
        assert len(plane.physical_attempt_records) == 1
        assert plane.action_bindings == ()
        assert journal.writable is False

        replay = harness.adapter.execute(harness.envelope)

        assert replay.outcome is ControllerOutcome.REJECTED
        assert len(harness.raw.calls) == 1
        assert len(plane.physical_attempt_records) == 1
    finally:
        journal.close()


def test_durable_transition_append_failure_keeps_one_raw_without_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = tmp_path / "evidence-run"
    harness = _harness(
        "click",
        evidence_run_directory=run_directory,
    )
    journal = harness.evidence_journal
    plane = harness.runtime_evidence
    assert journal is not None
    assert plane is not None

    try:
        result = harness.run()
        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.COMPLETED
        assert len(harness.raw.calls) == 1
        _fail_journal_event(monkeypatch, "controller_transition")

        with pytest.raises(
            RuntimeEvidencePlaneError,
            match="durable evidence append failed closed",
        ):
            _record_controller_transition(harness, result.transition)

        assert journal.writable is False
        assert len(harness.raw.calls) == 1
        assert plane.action_bindings == ()
        assert all(
            event.event_kind != "controller_transition"
            for event in journal.events
        )
        report = plane.build_report(
            artifact_reader=lambda uri: (
                run_directory / uri
            ).read_bytes()
        )
        assert report.status.value == "incomplete"
        assert (
            f"{harness.envelope.decision_id}_controller_transition"
            in report.missing_evidence
        )
        assert (
            f"{harness.envelope.decision_id}_action_binding"
            in report.missing_evidence
        )

        replay = harness.adapter.execute(harness.envelope)

        assert replay.outcome is ControllerOutcome.REJECTED
        assert len(harness.raw.calls) == 1
        assert len(plane.physical_attempt_records) == 1
        assert plane.action_bindings == ()
    finally:
        journal.close()


def test_exact_type_text_is_absent_from_ordinary_trace(
    tmp_path: Path,
) -> None:
    sentinel = "SENTINEL-exact-type-text-31"
    run_directory = tmp_path / "evidence-run"
    harness = _harness(
        "type_text",
        action_args={"text": sentinel},
        evidence_run_directory=run_directory,
    )
    journal = harness.evidence_journal
    plane = harness.runtime_evidence
    assert journal is not None
    assert plane is not None

    try:
        result = harness.run()
        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.COMPLETED
        _record_controller_transition(harness, result.transition)
        journal.close()

        trace_wire = [
            {
                "receipt": receipt.to_dict(),
                "metadata": metadata,
            }
            for receipt, metadata in harness.trace.records
        ]
        assert sentinel not in json.dumps(
            trace_wire,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert all(
            set(metadata)
            == {
                "phase",
                "controller_action",
                "decision_id",
                "mission_id",
                "run_id",
                "obligation_id",
                "frame_id",
                "frame_epoch",
                "target_hwnd",
                "target_binding_id",
            }
            for _receipt, metadata in harness.trace.records
        )

        journal_text = (
            run_directory / "evidence.jsonl"
        ).read_text(encoding="utf-8")
        report = plane.build_report(
            artifact_reader=lambda uri: (
                run_directory / uri
            ).read_bytes()
        )
        report_text = json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        assert report.status.value == "complete"
        assert sentinel in journal_text
        assert sentinel in report_text
    finally:
        journal.close()


def test_action_effect_snapshot_rejects_nested_extra_fields() -> None:
    harness = _harness("click", with_runtime_evidence=True)
    plane = harness.runtime_evidence
    assert plane is not None
    attempt = harness.run()
    transition = attempt.transition
    assert transition is not None
    wire = transition.to_dict()
    assert wire["action_effect"] is not None
    wire["action_effect"]["reasoning"] = "forbidden"
    current = harness.authority.current
    assert current is not None

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="action effect is not canonical",
    ):
        plane.record_controller_transition(
            decision_id=harness.envelope.decision_id,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=hashlib.sha256(
                harness.envelope.to_json().encode("utf-8")
            ).hexdigest(),
            transition=wire,
            post_frame_identity=RuntimeFrameIdentity(
                frame_id=current.frame_id,
                epoch=current.epoch,
                hwnd=current.hwnd,
                region=current.region,
                scope=current.scope,
                submitted_size=current.submitted_size,
                image_sha256=current.image_sha256,
            ).to_dict(),
        )


def test_applied_order_is_raw_then_wait_then_capture(monkeypatch) -> None:
    observed: list[tuple[str, float | int]] = []
    holder: dict[str, _Harness] = {}
    stop = threading.Event()

    def wait(delay: float) -> None:
        harness = holder["harness"]
        assert len(harness.raw.calls) == 1
        assert harness.authority.current is not None
        assert harness.authority.current.frame_id == 1
        observed.append(("wait", delay))
        stop.set()

    harness = _harness("click", wait=wait)
    holder["harness"] = harness
    original_capture = harness.observer._capture

    def capture(*args, **kwargs):
        assert observed == [("wait", 0.12)]
        observed.append(("capture", len(harness.raw.calls)))
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(harness.observer, "_capture", capture)

    attempt = harness.run(stop_event=stop)

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    assert attempt.transition.post_frame_id == 2
    assert stop.is_set()
    assert observed == [("wait", 0.12), ("capture", 1)]


def test_zero_settle_delay_does_not_call_wait() -> None:
    harness = _harness(
        "scroll",
        post_input_settle_delay_s=0,
        wait=lambda _delay: pytest.fail("zero delay must not call wait"),
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    assert harness.wait_calls == []


@pytest.mark.parametrize(
    ("raw_result", "expected_outcome", "input_completed"),
    (
        (False, ControllerOutcome.REJECTED, False),
        (None, ControllerOutcome.UNKNOWN, None),
    ),
)
def test_non_completed_raw_receipt_does_not_wait(
    raw_result: bool | None,
    expected_outcome: ControllerOutcome,
    input_completed: bool | None,
) -> None:
    harness = _harness("click", raw_result=raw_result)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is expected_outcome
    assert attempt.transition.input_completed is input_completed
    assert harness.wait_calls == []
    assert len(harness.raw.calls) == 1


def test_wait_exception_after_completed_input_is_unknown_without_replay() -> None:
    def fail_wait(_delay: float) -> None:
        raise RuntimeError("settle clock failed")

    harness = _harness("type_text", wait=fail_wait)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is True
    assert attempt.transition.post_frame_id == 2
    assert len(attempt.transition.post_frame_evidence) == 1
    assert harness.wait_calls == [0.12]
    assert len(harness.raw.calls) == 1
    assert harness.authority.current is not None
    assert harness.authority.current.frame_id == 2
    assert harness.trace.records[0][0].outcome is MutationOutcome.APPLIED


@pytest.mark.parametrize(
    ("delay", "error_type"),
    (
        (True, TypeError),
        (False, TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        (-0.01, ValueError),
        (1.01, ValueError),
    ),
)
def test_invalid_post_input_settle_delay_is_rejected(
    delay: float,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type, match="post_input_settle_delay_s"):
        _harness(
            "click",
            post_input_settle_delay_s=delay,
        )


@pytest.mark.parametrize("wait", (None, 0, object()))
def test_noncallable_wait_is_rejected_even_for_zero_delay(wait: Any) -> None:
    harness = _harness("click")

    with pytest.raises(TypeError, match="wait must be callable"):
        AtomicDesktopAdapter(
            frame_authority=harness.authority,
            frame_observer=harness.observer,
            frozen_target_binding=harness.binding,
            target_probe=harness.probe,
            mutation_permits=harness.permits,
            raw_device=harness.raw.executor(),
            receipt_trace_sink=harness.trace,
            post_input_settle_delay_s=0,
            wait=wait,
        )


def test_queue_acquisition_race_revalidates_before_raw(
    monkeypatch,
) -> None:
    harness = _harness("click")
    from winpilot.vnext.desktop_adapter import atomic

    def queue_run(_action, callback, **kwargs):
        assert kwargs["target_hwnd"] == HWND
        assert kwargs["scope"] == "physical"
        harness.probe.current = LiveTargetSnapshot(
            hwnd=HWND,
            root_hwnd=HWND,
            process_id=PROCESS_ID + 1,
            executable_identity=harness.binding.executable_identity,
            region=REGION,
            foreground_root_hwnd=HWND,
        )
        return callback()

    monkeypatch.setattr(atomic.DESKTOP_ACTION_QUEUE, "run", queue_run)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert attempt.transition.input_completed is False
    assert harness.raw.calls == []
    assert len(harness.trace.records) == 1
    assert harness.trace.records[0][0].outcome is MutationOutcome.NOT_STARTED
    assert (
        harness.trace.records[0][0].error_kind
        == "target_changed_after_queue"
    )


def test_queue_receives_exact_run_and_frame_metadata(monkeypatch) -> None:
    harness = _harness("hotkey")
    from winpilot.vnext.desktop_adapter import atomic

    observed: dict[str, Any] = {}

    def queue_run(_action, callback, **kwargs):
        observed.update(kwargs)
        return callback()

    monkeypatch.setattr(atomic.DESKTOP_ACTION_QUEUE, "run", queue_run)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    assert observed["target_hwnd"] == HWND
    assert observed["scope"] == "physical"
    assert observed["cancel_event"] is None
    assert observed["metadata"] == {
        "owner": "vnext_atomic_desktop_adapter",
        "mission_id": MISSION_ID,
        "run_id": RUN_ID,
        "obligation_id": OBLIGATION_ID,
        "decision_id": harness.envelope.decision_id,
        "frame_id": 1,
        "frame_epoch": EPOCH,
        "frame_hwnd": HWND,
        "target_binding_id": harness.binding.binding_id,
    }


def test_queue_acquisition_rechecks_live_runtime_state_before_raw(
    monkeypatch,
) -> None:
    harness = _harness("type_text")
    from winpilot.vnext.desktop_adapter import atomic

    live = [True]

    def queue_run(_action, callback, **_kwargs):
        live[0] = False
        return callback()

    monkeypatch.setattr(atomic.DESKTOP_ACTION_QUEUE, "run", queue_run)

    attempt = harness.run(still_current=lambda: live[0])

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert attempt.transition.input_completed is False
    assert harness.raw.calls == []
    assert len(harness.trace.records) == 1
    assert harness.trace.records[0][0].outcome is MutationOutcome.NOT_STARTED
    assert (
        harness.trace.records[0][0].error_kind
        == "permit_changed_after_queue"
    )


def test_unknown_raw_result_is_never_replayed() -> None:
    harness = _harness("double_click", raw_result=None)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is None
    assert attempt.transition.post_frame_id == 2
    assert len(harness.raw.calls) == 1
    assert len(harness.trace.records) == 1
    assert (
        harness.trace.records[0][0].outcome
        is MutationOutcome.OUTCOME_UNKNOWN
    )


def test_stale_authoritative_frame_rejects_without_raw() -> None:
    harness = _harness("hotkey")
    harness.authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=REGION,
        scope="target_window",
        reason="intervening-frame",
        submitted_size=(REGION[2], REGION[3]),
        image_b64=_jpeg(40),
        captured_at=1.5,
        expected_previous_frame_id=1,
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.permits.active_count == 0
    assert harness.trace.records[0][0].error_kind == (
        "stale_authoritative_frame"
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"hwnd": HWND + 1, "root_hwnd": HWND + 1},
        {"root_hwnd": HWND + 1},
        {"process_id": PROCESS_ID + 1},
        {"executable_identity": "C:/other.exe"},
        {"region": (11, 20, 100, 80)},
        {"foreground_root_hwnd": None},
        {"foreground_root_hwnd": HWND + 1},
    ),
)
def test_complete_live_target_identity_is_required_before_queue(
    changes: dict[str, Any],
) -> None:
    from dataclasses import replace

    harness = _harness("type_text")
    harness.probe.current = replace(harness.probe.current, **changes)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.trace.records[0][0].outcome is MutationOutcome.NOT_STARTED


def test_pointer_hit_test_is_required_before_queue() -> None:
    harness = _harness("scroll")
    harness.probe.hit_root = HWND + 1

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.trace.records[0][0].error_kind == "hit_test_changed"


def test_pointer_hit_test_rejects_an_incorrect_mapped_point() -> None:
    submitted_arguments = {
        "image_x": 37.0,
        "image_y": 29.0,
        "amount": -713,
    }
    mapped_point = _map_submitted_test_point(
        submitted_arguments["image_x"],
        submitted_arguments["image_y"],
    )
    assert mapped_point == (47, 49)
    assert mapped_point != POINT
    harness = _harness(
        "scroll",
        action_args=submitted_arguments,
        expected_hit_point=POINT,
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.trace.records[0][0].error_kind == "hit_test_changed"


def test_pre_set_stop_rejects_before_raw() -> None:
    harness = _harness("click")
    stop = threading.Event()
    stop.set()

    attempt = harness.run(stop_event=stop)

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.wait_calls == []
    assert harness.trace.records[0][0].error_kind == "stop_requested"


def test_queue_cancellation_rejects_without_raw(monkeypatch) -> None:
    harness = _harness("type_text")
    from winpilot.vnext.desktop_adapter import atomic

    def canceled(*_args, **_kwargs):
        raise DesktopActionCanceled("synthetic queue cancel")

    monkeypatch.setattr(atomic.DESKTOP_ACTION_QUEUE, "run", canceled)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert harness.raw.calls == []
    assert harness.trace.records[0][0].error_kind == (
        "desktop_queue_canceled"
    )


def test_final_pointer_guard_rechecks_target_without_replay() -> None:
    from dataclasses import replace

    harness = _harness("click")
    harness.raw.before_guard = lambda: setattr(
        harness.probe,
        "current",
        replace(
            harness.probe.current,
            process_id=PROCESS_ID + 1,
        ),
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.REJECTED
    assert attempt.transition.input_completed is False
    assert len(harness.raw.calls) == 1
    assert harness.trace.records[0][0].outcome is MutationOutcome.NOT_APPLIED


def test_partial_double_click_guard_failure_is_unknown_and_not_replayed() -> None:
    harness = _harness("double_click")
    harness.raw.between_double_guards = lambda: setattr(
        harness.probe,
        "hit_root",
        HWND + 1,
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is None
    assert len(harness.raw.calls) == 1
    assert (
        harness.trace.records[0][0].outcome
        is MutationOutcome.OUTCOME_UNKNOWN
    )


def test_applied_input_with_unavailable_post_capture_is_unknown() -> None:
    harness = _harness("hotkey")
    harness.observer._capture = lambda *_args, **_kwargs: ""

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is True
    assert attempt.transition.post_frame_id is None
    assert len(harness.raw.calls) == 1
    assert harness.authority.current is not None
    assert harness.authority.current.frame_id == 1
    assert harness.trace.records[0][0].outcome is MutationOutcome.APPLIED


def test_target_change_after_capture_prevents_post_commit() -> None:
    from dataclasses import replace

    harness = _harness("type_text")

    def mutate_after_capture(call: int) -> None:
        if call == 5:
            harness.probe.current = replace(
                harness.probe.current,
                executable_identity="C:/replacement.exe",
            )

    harness.probe.on_snapshot = mutate_after_capture

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is True
    assert len(harness.raw.calls) == 1
    assert harness.authority.current is not None
    assert harness.authority.current.frame_id == 1


def test_target_change_after_post_commit_prevents_completed_claim() -> None:
    from dataclasses import replace

    harness = _harness("type_text")

    def mutate_after_commit(call: int) -> None:
        if call == 6:
            harness.probe.current = replace(
                harness.probe.current,
                foreground_root_hwnd=HWND + 1,
            )

    harness.probe.on_snapshot = mutate_after_commit

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.UNKNOWN
    assert attempt.transition.input_completed is True
    assert len(harness.raw.calls) == 1
    assert harness.authority.current is not None
    assert harness.authority.current.frame_id == 2


def test_post_capture_uses_exact_predecessor_cas(monkeypatch) -> None:
    harness = _harness("scroll")
    seen: dict[str, Any] = {}
    original_capture = harness.observer.capture

    def capture(**kwargs):
        seen.update(kwargs)
        return original_capture(**kwargs)

    monkeypatch.setattr(harness.observer, "capture", capture)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    assert seen["expected_previous_frame_id"] == 1
    assert seen["epoch"] == EPOCH
    assert seen["hwnd"] == HWND
    assert seen["provided_region"] == Region(*REGION)


def test_trace_sink_failure_does_not_change_controller_fact() -> None:
    harness = _harness("type_text", trace_fail=True)

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    assert attempt.transition.action_effect is not None
    assert len(harness.raw.calls) == 1
    assert len(harness.trace.records) == 1


def test_held_drag_releases_at_current_point_without_an_extra_move() -> None:
    begin_args = {
        "image_x1": 50.0,
        "image_y1": 40.0,
        "image_x2": 80.0,
        "image_y2": 60.0,
        "keep_holding": True,
    }
    harness = _harness("drag", action_args=begin_args)

    try:
        first = harness.run()
        assert first.controller_error is None
        assert first.transition is not None
        assert first.transition.outcome is ControllerOutcome.COMPLETED
        held = harness.adapter.pointer_state()
        assert held.disposition.value == "held"
        assert held.physically_owned is True
        assert held.lease_id is not None
        assert harness.raw.calls == [
            (
                "drag",
                (
                    60,
                    60,
                    90,
                    80,
                    (),
                    HWND,
                    False,
                    True,
                    held.lease_id,
                ),
            )
        ]

        current = harness.authority.current
        assert current is not None
        finish_args = {
            "image_x1": 80.0,
            "image_y1": 60.0,
            "image_x2": 80.0,
            "image_y2": 60.0,
            "keep_holding": False,
        }
        finish_envelope = _replace_action_args(
            _envelope(current, harness.binding, "drag"),
            finish_args,
        )
        finish_envelope = _replace_envelope(
            finish_envelope,
            turn_index=1,
            pointer_disposition="held",
            pointer_lease_id=held.lease_id,
        )
        harness.probe.expected_hit_point = ((90, 80), (90, 80))
        frame_identity = RuntimeFrameIdentity(
            frame_id=current.frame_id,
            epoch=current.epoch,
            hwnd=current.hwnd,
            region=current.region,
            scope=current.scope,
            submitted_size=current.submitted_size,
            image_sha256=current.image_sha256,
        )

        second = call_once_with_permit(
            authority=harness.permits,
            envelope=finish_envelope,
            turn_index=1,
            commit_identity=RuntimeCommitIdentity(
                frame_identity,
                RuntimeTargetIdentity.from_binding(harness.binding),
            ),
            still_current=lambda: True,
            execute=harness.adapter.execute,
        )

        assert second.controller_error is None
        assert second.transition is not None
        assert second.transition.outcome is ControllerOutcome.COMPLETED
        released = harness.adapter.pointer_state()
        assert released.disposition.value == "released"
        assert released.lease_id is None
        assert harness.raw.calls[1] == (
            "drag",
            (
                90,
                80,
                90,
                80,
                (),
                HWND,
                True,
                False,
                held.lease_id,
            ),
        )
    finally:
        harness.adapter.close()


def test_adapter_close_retries_then_releases_an_unfinished_held_drag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(
        "drag",
        action_args={
            "image_x1": 50.0,
            "image_y1": 40.0,
            "image_x2": 80.0,
            "image_y2": 60.0,
            "keep_holding": True,
        },
    )

    try:
        result = harness.run()
        assert result.controller_error is None
        assert result.transition is not None
        assert result.transition.outcome is ControllerOutcome.COMPLETED
        lease_id = harness.adapter.pointer_state().lease_id
        assert lease_id is not None

        original_release = harness.adapter._raw.release_held_drag
        cleanup_attempts = 0

        def flaky_release(token: str) -> RawActionReceipt:
            nonlocal cleanup_attempts
            cleanup_attempts += 1
            if cleanup_attempts == 1:
                raise RuntimeError("transient cleanup failure")
            return original_release(token)

        monkeypatch.setattr(
            harness.adapter._raw,
            "release_held_drag",
            flaky_release,
        )
        harness.adapter.close()

        state = harness.adapter.pointer_state()
        assert state.disposition.value == "released"
        assert state.lease_id is None
        assert cleanup_attempts == 2
        assert harness.raw.calls[-1] == (
            "drag_cleanup",
            (lease_id, HWND),
        )
    finally:
        harness.adapter.close()


def test_closed_adapter_rejects_new_input() -> None:
    harness = _harness("click")
    harness.adapter.close()

    with pytest.raises(RuntimeError, match="adapter is closed"):
        harness.adapter.execute(harness.envelope)

    assert harness.raw.calls == []


def test_deterministic_replay_produces_identical_evidence_and_effect() -> None:
    first = _harness("click").run().transition
    second = _harness("click").run().transition

    assert first is not None
    assert second is not None
    assert first.outcome is ControllerOutcome.COMPLETED
    assert second.outcome is ControllerOutcome.COMPLETED
    assert first.post_frame_evidence == second.post_frame_evidence
    assert first.action_effect == second.action_effect
    assert first.action_effect_evidence == second.action_effect_evidence


def test_adapter_package_is_one_narrow_single_composition_owner() -> None:
    home = Path(__file__).resolve().parents[1]
    package = home / "winpilot" / "vnext" / "desktop_adapter"
    assert tuple(sorted(path.name for path in package.glob("*.py"))) == (
        "__init__.py",
        "atomic.py",
        "workspace.py",
    )
    atomic_source = (package / "atomic.py").read_text(encoding="utf-8")
    lowered = atomic_source.lower()
    tree = ast.parse(atomic_source)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    imported_parts = {
        part
        for module in imported_modules
        for part in module.lower().split(".")
    }
    called_names = {
        (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        ).lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "desktopactionqueue" not in called_names
    assert "rawdeviceexecutor" not in called_names
    assert "evidenceregistry" not in lowered
    for forbidden in (
        "model_call_runtime",
        "openai",
        "anthropic",
        "electron",
        "server",
        "mcp",
        "target_acquisition",
        "planner",
        "inspector",
        "profiler",
    ):
        assert forbidden not in imported_parts
    for forbidden_call in (
        "activate_window",
        "launch",
        "draw",
        "held_pointer",
    ):
        assert forbidden_call not in called_names

    product_importers = []
    package_root = home / "winpilot"
    for path in package_root.rglob("*.py"):
        if package in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        if "vnext.desktop_adapter" in source:
            product_importers.append(path.relative_to(home).as_posix())
    assert product_importers == [
        "winpilot/orchestrator/vnext_live_canary.py"
    ]


def test_execute_revalidates_a_forged_envelope_instance() -> None:
    harness = _harness("click")
    forged = ActorDecisionEnvelope.from_dict(harness.envelope.to_dict())
    forged.__dict__["frame_id"] = forged.frame_id + 1

    with pytest.raises(
        InvalidActorEnvelope,
        match="not canonical",
    ):
        harness.adapter.execute(forged)

    assert harness.raw.calls == []
    assert harness.trace.records == []


@pytest.mark.parametrize(
    "changes",
    (
        {"frame_id": 2},
        {"frame_epoch": EPOCH + 1},
        {"frame_hwnd": HWND + 1},
        {"frame_region": [11, 20, 100, 80]},
        {"submitted_image_size": [99, 80]},
        {"frame_image_sha256": "f" * 64},
        {"target_binding_id": "different-binding"},
    ),
)
def test_every_mutable_frame_or_binding_identity_field_fails_closed(
    changes: dict[str, Any],
) -> None:
    harness = _harness("click")
    harness.envelope = _replace_envelope(harness.envelope, **changes)

    transition = harness.adapter.execute(harness.envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert harness.raw.calls == []


@pytest.mark.parametrize(
    ("amount", "outcome"),
    (
        (-1201, ControllerOutcome.REJECTED),
        (-1200, ControllerOutcome.COMPLETED),
        (0, ControllerOutcome.REJECTED),
        (1200, ControllerOutcome.COMPLETED),
        (1201, ControllerOutcome.REJECTED),
    ),
)
def test_scroll_uses_fixed_standard_bounds(
    amount: int,
    outcome: ControllerOutcome,
) -> None:
    harness = _harness("scroll")
    harness.envelope = _replace_action_args(
        harness.envelope,
        {"image_x": 50.0, "image_y": 40.0, "amount": amount},
    )

    attempt = harness.run()

    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is outcome
    assert len(harness.raw.calls) == (
        1 if outcome is ControllerOutcome.COMPLETED else 0
    )


def test_completed_click_keeps_old_action_atomic_then_offers_new_foreground() -> None:
    harness = _harness("click")
    foreground = [HWND]
    next_hwnd = HWND + 100

    def surface(hwnd: int) -> WorkspaceSurface | None:
        if hwnd == HWND:
            return WorkspaceSurface(
                hwnd=HWND,
                root_hwnd=HWND,
                process_id=PROCESS_ID,
                executable_identity=harness.binding.executable_identity,
                title="Old",
                class_name="OldWindow",
                region=REGION,
                foreground_root_hwnd=foreground[0],
            )
        if hwnd == next_hwnd:
            return WorkspaceSurface(
                hwnd=next_hwnd,
                root_hwnd=next_hwnd,
                process_id=PROCESS_ID + 1,
                executable_identity="C:/next.exe",
                title="Next",
                class_name="NextWindow",
                region=(200, 100, 640, 480),
                foreground_root_hwnd=foreground[0],
            )
        return None

    workspace = DesktopWorkspace(
        binding=harness.binding,
        surface=surface(HWND),
        reader=surface,
        foreground_reader=lambda: foreground[0],
        hit_test_root=lambda _x, _y: HWND,
        wait=lambda _seconds: None,
    )

    def settle(_delay: float) -> None:
        foreground[0] = next_hwnd

    adapter = AtomicDesktopAdapter(
        frame_authority=harness.authority,
        frame_observer=harness.observer,
        frozen_target_binding=harness.binding,
        target_probe=workspace,
        desktop_workspace=workspace,
        mutation_permits=harness.permits,
        raw_device=harness.raw.executor(),
        receipt_trace_sink=harness.trace,
        post_input_settle_delay_s=0.12,
        wait=settle,
    )

    harness.adapter = adapter
    attempt = harness.run()
    assert attempt.controller_error is None
    assert attempt.transition is not None
    transition = attempt.transition

    assert (
        transition.outcome is ControllerOutcome.COMPLETED
    ), [item[0].error_kind for item in harness.trace.records]
    assert transition.input_completed is True
    assert workspace.binding == harness.binding
    successor = workspace.observe()
    assert successor.replaces_binding
    assert successor.surface.hwnd == next_hwnd
