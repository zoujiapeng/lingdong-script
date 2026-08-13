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
from winpilot.visual_runtime.frame_authority import FrameAuthority
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
from winpilot.vnext.mission import TargetBinding
from winpilot.vnext.runtime import (
    ControllerOutcome,
    ControllerTransition,
    RuntimeCommitIdentity,
    RuntimeContractError,
    RuntimeFrameIdentity,
    RuntimeTargetIdentity,
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


def _map_submitted_test_point(
    image_x: object,
    image_y: object,
    *,
    region: tuple[int, int, int, int] | None = None,
    submitted_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    physical_region = REGION if region is None else region
    size = (
        (physical_region[2], physical_region[3])
        if submitted_size is None
        else submitted_size
    )
    frame_arguments, _ = SubmittedImageCoordinateMapper(size).map_point_pair(
        {"image_x": image_x, "image_y": image_y},
        image_keys=("image_x", "image_y"),
        frame_keys=("frame_x", "frame_y"),
        context="atomic adapter test",
    )
    return NormalizedFrameCoordinateMapper(physical_region).map_point(
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
        enforce_foreground: bool = True,
    ) -> RawBackendResult:
        self.calls.append(
            (
                "click",
                (x, y, button, count, mechanism, hwnd, enforce_foreground),
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
    raw: _RawHarness
    trace: _Trace
    wait_calls: list[float]
    adapter: AtomicDesktopAdapter
    envelope: ActorDecisionEnvelope
    commit_identity: RuntimeCommitIdentity
    before_image_b64: str | None = None
    after_image_b64: str | None = None

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        still_current: Callable[[], bool] = lambda: True,
    ):
        if not still_current():
            return _AttemptResult(transition=None, controller_error=None)
        try:
            transition = self.adapter.execute(
                self.envelope,
                stop_event=stop_event,
            )
            return _AttemptResult(transition=transition, controller_error=None)
        except Exception as exc:
            return _AttemptResult(transition=None, controller_error=exc)


@dataclass
class _AttemptResult:
    transition: ControllerTransition | None
    controller_error: Exception | None
    execute_call_count: int = 1
    active_admission_closed: bool = True


def _proposal(action: str) -> dict[str, Any]:
    args = {
        "click": {"image_x": 50.0, "image_y": 40.0},
        "double_click": {"image_x": 50.0, "image_y": 40.0},
        "drag": {
            "image_path": [[50.0, 40.0], [80.0, 60.0]],
            "keep_holding": False,
        },
        "scroll": {
            "image_x": 50.0,
            "image_y": 40.0,
            "amount": -240,
        },
        "type_text": {"text": "hello"},
        "hotkey": {"keys": ["ctrl", "l"]},
        "launch_application": {"app": "notepad", "arguments": ""},
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
        mapped_path = tuple(
            _map_submitted_test_point(point[0], point[1])
            for point in drag_arguments["image_path"]
        )
        drag_guard_points = (expected_hit_point, *mapped_path[1:])
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
    raw = _RawHarness(raw_result)
    trace = _Trace(trace_fail)
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
        raw_device=raw.executor(),
        receipt_trace_sink=trace,
        post_input_settle_delay_s=post_input_settle_delay_s,
        wait=settle_wait,
    )
    envelope = _envelope(before, binding, action)
    if action_args is not None:
        envelope = _replace_action_args(envelope, action_args)
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
        raw=raw,
        trace=trace,
        wait_calls=wait_calls,
        adapter=adapter,
        envelope=envelope,
        commit_identity=RuntimeCommitIdentity(
            frame_identity,
            RuntimeTargetIdentity.from_binding(binding),
        ),
        before_image_b64=before_b64,
        after_image_b64=after_b64,
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


def test_screen_first_click_is_admitted_on_any_visible_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Screen-first frames let the pointer operate any window the model sees.

    The authoritative frame covers the whole virtual desktop, so the bound
    window does not need to be the hit root at the click point and the
    pre-input foreground activation is skipped: the model points at what it
    sees and the OS raises the clicked window.  The old binding is neither an
    admission fact nor a queue key for this default route.
    """
    other_hwnd = HWND + 1
    desktop = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    authority = FrameAuthority()
    before_b64 = _jpeg(20)
    after_b64 = _jpeg(220)
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=before_b64,
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: after_b64,
        virtual_desktop=lambda: Region(*desktop),
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
    probe = _Probe(
        LiveTargetSnapshot(
            hwnd=HWND,
            root_hwnd=HWND,
            process_id=PROCESS_ID,
            executable_identity=binding.executable_identity,
            region=REGION,
            foreground_root_hwnd=HWND,
        ),
        expected_hit_point=_map_submitted_test_point(
            50.0,
            40.0,
            region=desktop,
            submitted_size=(1120, 630),
        ),
        hit_root=other_hwnd,
    )

    class _Workspace:
        def __init__(self) -> None:
            self.binding = binding
            self.finish_calls: list[dict[str, Any]] = []

        def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None:
            return probe.snapshot(hwnd)

        def hit_test_root(self, x: int, y: int) -> int | None:
            return probe.hit_test_root(x, y)

        def finish_action(self, **kwargs: Any) -> None:
            self.finish_calls.append(dict(kwargs))

    workspace = _Workspace()
    raw = _RawHarness(True)
    trace = _Trace(False)
    queue_calls: list[dict[str, Any]] = []

    def queue_run(_action, callback, **kwargs):
        queue_calls.append(dict(kwargs))
        return callback()

    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.DESKTOP_ACTION_QUEUE.run",
        queue_run,
    )
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=probe,
        desktop_workspace=workspace,
        raw_device=raw.executor(),
        receipt_trace_sink=trace,
        post_input_settle_delay_s=0.0,
    )
    envelope = _replace_envelope(
        _envelope(before, binding, "click"),
        target_binding_id="obsolete-target-binding",
    )
    commit_identity = RuntimeCommitIdentity(
        RuntimeFrameIdentity(
            frame_id=before.frame_id,
            epoch=before.epoch,
            hwnd=before.hwnd,
            region=before.region,
            scope=before.scope,
            submitted_size=before.submitted_size,
            image_sha256=before.image_sha256,
        ),
        RuntimeTargetIdentity.from_binding(binding),
    )
    attempt = adapter.execute(envelope)
    assert attempt is not None

    assert attempt.outcome is ControllerOutcome.COMPLETED
    assert attempt.input_completed is True
    assert len(raw.calls) == 1
    assert raw.calls[0][0] == "click"
    assert raw.calls[0][1][:2] == _map_submitted_test_point(
        50.0,
        40.0,
        region=desktop,
        submitted_size=(1120, 630),
    )
    assert raw.calls[0][1][6] is False
    assert len(queue_calls) == 1
    assert queue_calls[0]["scope"] == "physical"
    assert queue_calls[0]["target_hwnd"] is None
    assert queue_calls[0]["metadata"]["target_binding_id"] is None
    assert workspace.finish_calls == []
    assert trace.records[0][1]["target_hwnd"] is None
    assert trace.records[0][1]["target_binding_id"] is None
    assert authority.current is not None
    assert authority.current.frame_id == 2
    assert authority.current.scope == "full_desktop"
    assert authority.current.region == desktop

    # Removing binding admission must not weaken the authoritative frame CAS.
    stale = adapter.execute(envelope)
    assert stale.outcome is ControllerOutcome.REJECTED
    assert stale.input_completed is False
    assert len(raw.calls) == 1
    assert len(queue_calls) == 1


def test_screen_first_scroll_does_not_carry_the_old_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
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
    raw = _RawHarness(True)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=_Probe(
            LiveTargetSnapshot(
                hwnd=HWND,
                root_hwnd=HWND,
                process_id=PROCESS_ID,
                executable_identity=binding.executable_identity,
                region=REGION,
                foreground_root_hwnd=HWND + 1,
            ),
            hit_root=HWND + 1,
        ),
        raw_device=raw.executor(),
        post_input_settle_delay_s=0.0,
    )

    attempt = adapter.execute(_envelope(before, binding, "scroll"))

    assert attempt.input_completed is True
    assert raw.calls == [(
        "scroll",
        (
            *_map_submitted_test_point(
                50.0,
                40.0,
                region=desktop,
                submitted_size=(1120, 630),
            ),
            -240,
            None,
        ),
    )]


def test_screen_first_held_drag_skips_bound_window_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
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
    start = _map_submitted_test_point(
        50.0, 40.0, region=desktop, submitted_size=(1120, 630)
    )
    end = _map_submitted_test_point(
        80.0, 60.0, region=desktop, submitted_size=(1120, 630)
    )
    probe = _Probe(
        LiveTargetSnapshot(
            hwnd=HWND,
            root_hwnd=HWND,
            process_id=PROCESS_ID,
            executable_identity=binding.executable_identity,
            region=REGION,
            foreground_root_hwnd=HWND + 1,
        ),
        expected_hit_point=(start, end),
        hit_root=HWND + 1,
    )
    raw = _RawHarness(True)
    trace = _Trace(False)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=probe,
        raw_device=raw.executor(),
        receipt_trace_sink=trace,
        post_input_settle_delay_s=0.0,
    )
    envelope = _replace_action_args(
        _envelope(before, binding, "drag"),
        {
            "image_path": [[50.0, 40.0], [80.0, 60.0]],
            "keep_holding": True,
        },
    )

    try:
        attempt = adapter.execute(envelope)
        assert attempt.input_completed is True
        assert raw.calls[0][0] == "drag"
        assert raw.calls[0][1][5] is None
        assert trace.records[0][0].outcome is MutationOutcome.APPLIED
    finally:
        adapter.close()


def test_screen_first_click_ignores_dead_hit_test_and_keeps_desktop_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-selected desktop coordinate is the pointer target."""
    other_hwnd = HWND + 1
    desktop = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetForegroundWindow",
        lambda: other_hwnd,
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetAncestor",
        lambda hwnd, _flag: hwnd,
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
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
    probe = _Probe(
        LiveTargetSnapshot(
            hwnd=HWND,
            root_hwnd=HWND,
            process_id=PROCESS_ID,
            executable_identity=binding.executable_identity,
            region=REGION,
            foreground_root_hwnd=HWND,
        ),
        expected_hit_point=(0, 0),
        hit_root=None,
    )
    raw = _RawHarness(True)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=probe,
        raw_device=raw.executor(),
        receipt_trace_sink=_Trace(False),
        post_input_settle_delay_s=0.0,
    )
    envelope = _envelope(before, binding, "click")
    commit_identity = RuntimeCommitIdentity(
        RuntimeFrameIdentity(
            frame_id=before.frame_id,
            epoch=before.epoch,
            hwnd=before.hwnd,
            region=before.region,
            scope=before.scope,
            submitted_size=before.submitted_size,
            image_sha256=before.image_sha256,
        ),
        RuntimeTargetIdentity.from_binding(binding),
    )
    attempt = adapter.execute(envelope)
    assert attempt is not None

    assert attempt.outcome is ControllerOutcome.COMPLETED
    assert attempt.input_completed is True
    assert len(raw.calls) == 1
    assert raw.calls[0][0] == "click"
    assert raw.calls[0][1][5] is None
    assert raw.calls[0][1][6] is False
    assert probe.snapshot_calls == 0
    assert probe.hit_test_calls == 0
    assert authority.current is not None
    assert authority.current.frame_id == 2
    assert authority.current.scope == "full_desktop"


def test_screen_first_pointer_never_falls_back_to_old_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = (0, 0, 1920, 1080)
    desktop_hwnd = HWND + 400
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetForegroundWindow",
        lambda: 0,
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetDesktopWindow",
        lambda: desktop_hwnd,
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
        captured_at=lambda: 2.0,
    )
    binding = TargetBinding(
        binding_id="obsolete-target-binding",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="C:/gone.exe",
        title_snapshot="Gone",
        established_by_evidence_ref="evidence-target-bound",
        epoch=EPOCH,
    )

    class _NoBindingProbe:
        def snapshot(self, _hwnd: int) -> None:
            raise AssertionError("screen-first input must not inspect binding")

        def hit_test_root(self, _x: int, _y: int) -> None:
            raise AssertionError("screen-first input must not use hit-test admission")

    raw = _RawHarness(True)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=_NoBindingProbe(),
        raw_device=raw.executor(),
        post_input_settle_delay_s=0.0,
    )

    transition = adapter.execute(_envelope(before, binding, "click"))

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    # The physical pointer backend receives no HWND.  The desktop HWND only
    # satisfies RawDevice's typed context; the obsolete binding is unused.
    assert raw.calls[0][1][5] is None
    assert HWND not in raw.calls[0][1]
    assert authority.current is not None
    assert authority.current.scope == "full_desktop"


@pytest.mark.parametrize("action", ("type_text", "hotkey"))
def test_screen_first_keyboard_uses_live_foreground_not_old_binding(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = (0, 0, 1920, 1080)
    live_foreground = HWND + 91
    child_foreground = live_foreground + 1
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetForegroundWindow",
        lambda: child_foreground,
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetAncestor",
        lambda hwnd, _flag: live_foreground if hwnd == child_foreground else hwnd,
    )
    set_foreground_calls: list[int] = []
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.SetForegroundWindow",
        lambda hwnd: set_foreground_calls.append(hwnd),
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
        captured_at=lambda: 2.0,
    )
    binding = TargetBinding(
        binding_id="target-binding",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="C:/app.exe",
        title_snapshot="Old",
        established_by_evidence_ref="evidence-target-bound",
        epoch=EPOCH,
    )
    probe = _Probe(
        LiveTargetSnapshot(
            hwnd=HWND + 5,
            root_hwnd=HWND + 5,
            process_id=PROCESS_ID + 5,
            executable_identity="C:/replacement.exe",
            region=(50, 50, 640, 480),
            foreground_root_hwnd=live_foreground,
        )
    )
    raw = _RawHarness(True)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=probe,
        raw_device=raw.executor(),
        receipt_trace_sink=_Trace(False),
        post_input_settle_delay_s=0.0,
    )

    transition = adapter.execute(_envelope(before, binding, action))

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert len(raw.calls) == 1
    assert raw.calls[0][0] == action
    assert raw.calls[0][1][-1] == live_foreground
    assert probe.snapshot_calls == 0
    assert probe.hit_test_calls == 0
    assert set_foreground_calls == []


def test_screen_first_pointer_and_post_frame_ignore_missing_old_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.virtual_screen_region",
        lambda: Region(*desktop),
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetForegroundWindow",
        lambda: HWND + 30,
    )
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.GetAncestor",
        lambda hwnd, _flag: hwnd,
    )
    set_foreground_calls: list[int] = []
    monkeypatch.setattr(
        "winpilot.vnext.desktop_adapter.atomic.win32gui.SetForegroundWindow",
        lambda hwnd: set_foreground_calls.append(hwnd),
    )
    authority = FrameAuthority()
    before = authority.commit(
        epoch=EPOCH,
        hwnd=HWND,
        region=desktop,
        scope="full_desktop",
        reason="screen-first",
        submitted_size=(1120, 630),
        image_b64=_jpeg(20),
        captured_at=1.0,
        expected_previous_frame_id=0,
    )
    observer = FrameObserver(
        authority,
        capture=lambda _hwnd, **_kwargs: _jpeg(220),
        virtual_desktop=lambda: Region(*desktop),
        captured_at=lambda: 2.0,
    )
    binding = TargetBinding(
        binding_id="target-binding",
        hwnd=HWND,
        process_id=PROCESS_ID,
        executable_identity="C:/gone.exe",
        title_snapshot="Gone",
        established_by_evidence_ref="evidence-target-bound",
        epoch=EPOCH,
    )

    class _MissingProbe:
        def snapshot(self, _hwnd: int) -> None:
            raise AssertionError("full-desktop input must not inspect old binding")

        def hit_test_root(self, _x: int, _y: int) -> None:
            raise AssertionError("full-desktop input must not hit-test admission")

    raw = _RawHarness(True)
    adapter = AtomicDesktopAdapter(
        frame_authority=authority,
        frame_observer=observer,
        frozen_target_binding=binding,
        target_probe=_MissingProbe(),
        raw_device=raw.executor(),
        receipt_trace_sink=_Trace(False),
        post_input_settle_delay_s=0.0,
    )

    transition = adapter.execute(_envelope(before, binding, "click"))

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert len(raw.calls) == 1
    assert authority.current is not None
    assert authority.current.frame_id == 2
    assert authority.current.scope == "full_desktop"
    assert authority.current.region == desktop
    assert set_foreground_calls == []


def test_exact_type_text_is_absent_from_ordinary_trace(
    tmp_path: Path,
) -> None:
    sentinel = "SENTINEL-exact-type-text-31"
    harness = _harness(
        "type_text",
        action_args={"text": sentinel},
    )

    result = harness.run()
    assert result.controller_error is None
    assert result.transition is not None
    assert result.transition.outcome is ControllerOutcome.COMPLETED

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
            "target_title",
            "target_process",
        }
        for _receipt, metadata in harness.trace.records
    )




def test_action_effect_snapshot_rejects_nested_extra_fields() -> None:
    harness = _harness("click")
    attempt = harness.run()
    transition = attempt.transition
    assert transition is not None
    wire = transition.to_dict()
    assert wire["action_effect"] is not None
    assert "reasoning" not in wire["action_effect"]
    assert set(wire["action_effect"]) <= {
        "format",
        "comparator_version",
        "action_id",
        "input_completed",
        "before_frame_id",
        "after_frame_id",
        "before_frame_evidence_ref",
        "after_frame_evidence_ref",
        "before_frame_payload_hash",
        "after_frame_payload_hash",
        "payload_hash",
        "target_binding_valid",
        "frame_comparable",
        "local_visual_change",
        "change_ratio",
        "reason",
        "semantic_completion",
        "evidence_ref",
    }


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


def test_guard_rejection_backend_fact_error_kind_reaches_transition() -> None:
    from winpilot.vnext.desktop_adapter import atomic

    class _Envelope:
        decision_id = "decision-guard-test"

    receipt = RawActionReceipt(
        action="click",
        outcome=MutationOutcome.NOT_APPLIED,
        backend_result=RawBackendResult(False, {
            "error_kind": "target_window_lost",
            "error": "target window lost foreground",
        }),
    )
    transition = atomic.AtomicDesktopAdapter._rejected(
        _Envelope(),
        "click",
        error_kind=atomic.AtomicDesktopAdapter._receipt_error_kind(receipt),
    )

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert transition.error_kind == "target_window_lost"


def test_receipt_error_kind_prefers_explicit_and_ignores_empty_facts() -> None:
    from winpilot.vnext.desktop_adapter import atomic

    explicit = RawActionReceipt(
        action="click",
        outcome=MutationOutcome.NOT_STARTED,
        error_kind="hit_test_changed",
    )
    assert (
        atomic.AtomicDesktopAdapter._receipt_error_kind(explicit)
        == "hit_test_changed"
    )

    fact_based = RawActionReceipt(
        action="drag",
        outcome=MutationOutcome.NOT_APPLIED,
        backend_result=RawBackendResult(False, {
            "error_kind": "target_window_lost",
        }),
    )
    assert (
        atomic.AtomicDesktopAdapter._receipt_error_kind(fact_based)
        == "target_window_lost"
    )

    empty = RawActionReceipt(
        action="drag",
        outcome=MutationOutcome.NOT_APPLIED,
        backend_result=RawBackendResult(False, {
            "error": "no machine kind",
        }),
    )
    assert atomic.AtomicDesktopAdapter._receipt_error_kind(empty) is None


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












def test_drag_traces_every_image_path_point_in_physical_order() -> None:
    submitted = {
        "image_path": [[50.0, 40.0], [80.0, 40.0], [80.0, 60.0]],
        "keep_holding": False,
    }
    harness = _harness("drag", action_args=submitted)
    events: list[dict[str, Any]] = []
    from winpilot.utils.log import BUS

    BUS.add_sink(events.append)
    try:
        result = harness.run()
    finally:
        BUS.remove_sink(events.append)

    assert result.controller_error is None
    assert result.transition is not None
    assert result.transition.outcome is ControllerOutcome.COMPLETED
    dispatch = next(
        event
        for event in events
        if event.get("type") == "vnext_pointer_dispatch"
    )
    assert dispatch["image_coordinates"] == submitted
    assert dispatch["submitted_image_size"] == [100, 80]
    assert dispatch["frame_region"] == [10, 20, 100, 80]
    assert dispatch["physical_arguments"] == {
        "kind": "pointer_drag",
        "x1": 60,
        "y1": 60,
        "x2": 90,
        "y2": 80,
        "button": "left",
        "points": [[90, 60]],
        "started_held": False,
        "keep_holding": False,
    }


def test_held_drag_releases_at_current_point_without_an_extra_move() -> None:
    begin_args = {
        "image_path": [[50.0, 40.0], [80.0, 60.0]],
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
            "image_path": [[80.0, 60.0]],
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

        second = harness.adapter.execute(finish_envelope)

        assert second.outcome is ControllerOutcome.COMPLETED
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
            "image_path": [[50.0, 40.0], [80.0, 60.0]],
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
        "winpilot/orchestrator/live_agent.py"
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

def test_launch_visible_effect_surfaces_existing_instance_note() -> None:
    """A launch that reused a running instance must tell the model so."""
    from winpilot.vnext.desktop_adapter.atomic import AtomicDesktopAdapter

    adapter = AtomicDesktopAdapter.__new__(AtomicDesktopAdapter)

    # via=existing -> note projected
    receipt_existing = RawActionReceipt(
        action="launch_application",
        outcome=MutationOutcome.APPLIED,
        backend_result=RawBackendResult(
            True,
            {
                "via": "existing",
                "target": "C:\\QQ\\QQ.exe",
                "activated_hwnd": 33557054,
                "existing": True,
                "note": "already running: activated the existing window; no new instance was started",
            },
        ),
    )
    effect = adapter._launch_visible_effect(receipt_existing, "launch_application")
    assert "already running" in effect
    assert "no new instance" in effect

    # via=path (fresh start) -> empty effect
    receipt_fresh = RawActionReceipt(
        action="launch_application",
        outcome=MutationOutcome.APPLIED,
        backend_result=RawBackendResult(
            True,
            {"via": "path", "target": "C:\\QQ\\QQ.exe"},
        ),
    )
    assert adapter._launch_visible_effect(receipt_fresh, "launch_application") == ""

    # non-launch action -> empty effect
    receipt_click = RawActionReceipt(
        action="click",
        outcome=MutationOutcome.APPLIED,
        backend_result=RawBackendResult(True, {}),
    )
    assert adapter._launch_visible_effect(receipt_click, "click") == ""

    # fallback when note missing but via=existing
    receipt_existing_no_note = RawActionReceipt(
        action="launch_application",
        outcome=MutationOutcome.APPLIED,
        backend_result=RawBackendResult(
            True,
            {"via": "existing", "target": "C:\\QQ\\QQ.exe"},
        ),
    )
    effect_fallback = adapter._launch_visible_effect(
        receipt_existing_no_note, "launch_application"
    )
    assert "already-running" in effect_fallback


def test_launch_reuses_launcher_feedback_without_claiming_activation() -> None:
    harness = _harness("launch_application")
    harness.adapter._launcher = lambda _app, _arguments: {
        "ok": True,
        "via": "existing",
        "target": "C:\\QQ\\QQ.exe",
        "candidate_count": 2,
        "note": "already running: found 2 visible candidates",
    }

    transition = harness.adapter.execute(harness.envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert transition.visible_effect == (
        "already running: found 2 visible candidates"
    )
    receipt = harness.trace.records[0][0]
    assert receipt.backend_result is not None
    facts = dict(receipt.backend_result.facts)
    assert facts["candidate_count"] == 2
    assert "activated_hwnd" not in facts
    assert "activated" not in facts["note"]

def test_hotkey_accepts_string_chord_via_canonical_normalization() -> None:
    harness = _harness("hotkey", action_args={"keys": "ctrl+l"})
    attempt = harness.run()
    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    (kind, payload) = harness.raw.calls[-1]
    assert kind == "hotkey"
    chord, _hwnd = payload
    assert tuple(chord.keys) == ("ctrl", "l")


def test_hotkey_folds_aliases_and_orders_chord_modifiers() -> None:
    harness = _harness(
        "hotkey",
        action_args={"keys": ["shift", "command", "s"]},
    )
    attempt = harness.run()
    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
    (kind, payload) = harness.raw.calls[-1]
    assert kind == "hotkey"
    chord, _hwnd = payload
    assert tuple(chord.keys) == ("shift", "win", "s")


def test_extra_action_arguments_are_tolerated_not_rejected() -> None:
    """Surplus keys must not burn an Actor turn on a format-repair round."""
    harness = _harness(
        "click",
        action_args={
            "image_x": 50.0,
            "image_y": 40.0,
            "region": {"left": 1, "top": 2, "width": 3, "height": 4},
            "note": "模型多余字段",
        },
    )
    attempt = harness.run()
    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED


def test_launch_tolerates_extra_arguments() -> None:
    harness = _harness("launch_application")
    prepared = harness.adapter._prepare_action(
        harness.envelope,
        harness.envelope.proposal.model_copy(update={
            "args": {
                "app": "notepad",
                "arguments": "",
                "window": "extra",
            }
        }),
    )
    assert prepared.action == "launch_application"


def test_hotkey_tolerates_extra_arguments() -> None:
    harness = _harness(
        "hotkey",
        action_args={"keys": ["ctrl", "s"], "delay_ms": 5},
    )
    attempt = harness.run()
    assert attempt.controller_error is None
    assert attempt.transition is not None
    assert attempt.transition.outcome is ControllerOutcome.COMPLETED
