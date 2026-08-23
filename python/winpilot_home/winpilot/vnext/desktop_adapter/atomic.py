"""One atomic bridge from an Actor decision to admitted physical input.

This module is deliberately the only vNext owner allowed to cross from the
pure Actor/Runtime contracts into the existing live desktop primitives.  A
full-desktop frame is screen-first: its pixel identity, not an old window
binding, is the action authority.  A target-window crop is only an observation
choice; it never redirects, focuses, or vetoes the model's physical input.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import hashlib
import math
import ntpath
import threading
import time
from types import MappingProxyType
from typing import Any, Protocol

from ...action.desktop_queue import (
    DESKTOP_ACTION_QUEUE,
    DesktopActionCanceled,
)
from ...action.raw_device import (
    ClickCount,
    ClickMechanism,
    HotkeyChord,
    InputEventKind,
    MAX_POINTER_DURATION_MS,
    MAX_TYPE_TEXT_CODEPOINTS,
    MouseButton,
    PhysicalInputEvent,
    RawActionContext,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceExecutor,
    confirmed_input_event_prefix,
)
from ...action.replay_policy import MutationOutcome
from ...utils.log import BUS
from ...utils.replay_frames import save_pending_click_frame
from ...utils.screenshot import Region, virtual_screen_region
from ...visual_runtime.frame_authority import FrameAuthority, FrameState
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.input_sequence import parse_input_sequence
from ...visual_runtime.keyboard_contracts import (
    CANONICAL_HOTKEY_KEYS,
    canonical_hotkey_args,
    normalize_hotkey_args,
)
from ..actor import ActorDecisionEnvelope, DesktopActionProposal
from ..mission import TargetBinding
from ..runtime import (
    ControllerOutcome,
    ControllerTransition,
    PointerDisposition,
    PointerLeaseState,
    RuntimeFrame,
    RuntimeFrameIdentity,
    RuntimeTargetIdentity,
)


_ACTIONS = frozenset(
    {
        "click",
        "double_click",
        "move_mouse",
        "drag",
        "scroll",
        "type_text",
        "hotkey",
        "input_sequence",
        "launch_application",
    }
)
_JPEG_MEDIA_TYPE = "image/jpeg"
_MAX_SCROLL_AMOUNT = 1200
_MAX_LAUNCH_EFFECT_CHARS = 200
# One spatial turn can make an Actor call followed by a Grounder call.  Keep
# the emergency lease longer than their combined production timeout; normal
# release is the next drag segment or session.close(), not this fallback.
_DEFAULT_HELD_POINTER_TIMEOUT_S = 300.0

PhysicalRegion = tuple[int, int, int, int]
PhysicalPoint = tuple[int, int]
TraceScalar = bool | int | float | str | None


@dataclass(frozen=True, slots=True)
class LiveTargetSnapshot:
    """One read-only observation of the already-bound desktop target."""

    hwnd: int
    root_hwnd: int
    process_id: int
    executable_identity: str
    region: PhysicalRegion
    foreground_root_hwnd: int | None

    def __post_init__(self) -> None:
        for name in ("hwnd", "root_hwnd", "process_id"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            type(self.executable_identity) is not str
            or not self.executable_identity
        ):
            raise ValueError("executable_identity must be non-empty")
        region = tuple(self.region)
        if (
            len(region) != 4
            or any(type(value) is not int for value in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise ValueError(
                "region must be (left, top, positive width, positive height)"
            )
        foreground = self.foreground_root_hwnd
        if foreground is not None and (
            type(foreground) is not int or foreground <= 0
        ):
            raise ValueError(
                "foreground_root_hwnd must be positive or null"
            )
        object.__setattr__(self, "region", region)


class DesktopTargetProbe(Protocol):
    """Read current mechanical facts without acquiring or changing a target."""

    def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None: ...


class DesktopWorkspacePort(DesktopTargetProbe, Protocol):
    """Optional observation workspace; never an input authority."""

    @property
    def binding(self) -> TargetBinding: ...

class RawReceiptTraceSink(Protocol):
    """Best-effort sink for the complete immutable RawDevice receipt."""

    def record(
        self,
        receipt: RawActionReceipt,
        *,
        metadata: Mapping[str, TraceScalar],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _PreparedAction:
    action: str
    point: PhysicalPoint | None = None
    end_point: PhysicalPoint | None = None
    points: tuple[PhysicalPoint, ...] | None = None
    button: MouseButton = MouseButton.LEFT
    held_keys: tuple[str, ...] = ()
    duration_ms: int = 0
    press_duration_ms: int = 35
    events: tuple[PhysicalInputEvent, ...] | None = None
    keep_holding: bool = False
    hold_control_present: bool = False
    started_held: bool = False
    pointer_lease_id: str | None = None
    amount: int | None = None
    text: str | None = None
    chord: HotkeyChord | None = None
    repeat: int = 1
    launch_app: str | None = None
    launch_arguments: str | None = None


@dataclass(frozen=True, slots=True)
class _PinnedExecution:
    before_frame: RuntimeFrame | None
    receipt: RawActionReceipt
    raw_device_called: bool = False


@dataclass(frozen=True, slots=True)
class _HeldPointerLease:
    lease_id: str
    cursor: PhysicalPoint
    input_hwnd: int | None
    button: MouseButton
    expires_at: float


class AtomicDesktopAdapter:
    """Execute exactly one admitted desktop proposal through existing owners."""

    def __init__(
        self,
        *,
        frame_authority: FrameAuthority,
        frame_observer: FrameObserver,
        frozen_target_binding: TargetBinding,
        target_probe: DesktopTargetProbe,
        desktop_workspace: DesktopWorkspacePort | None = None,
        raw_device: RawDeviceExecutor,
        receipt_trace_sink: RawReceiptTraceSink | None = None,
        launcher: Callable[[str, str], Mapping[str, Any]] | None = None,
        post_input_settle_delay_s: float = 1.0,
        wait: Callable[[float], Any] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        held_pointer_timeout_s: float = _DEFAULT_HELD_POINTER_TIMEOUT_S,
        desktop_shell: bool = False,
    ) -> None:
        if not isinstance(frame_authority, FrameAuthority):
            raise TypeError("frame_authority must be FrameAuthority")
        if not isinstance(frame_observer, FrameObserver):
            raise TypeError("frame_observer must be FrameObserver")
        if getattr(frame_observer, "_authority", None) is not frame_authority:
            raise TypeError(
                "FrameObserver and AtomicDesktopAdapter must share FrameAuthority"
            )
        if type(frozen_target_binding) is not TargetBinding:
            raise TypeError(
                "frozen_target_binding must be canonical TargetBinding"
            )
        frozen_target_binding = TargetBinding.from_dict(
            frozen_target_binding.to_dict()
        )
        probe: DesktopTargetProbe = (
            desktop_workspace
            if desktop_workspace is not None
            else target_probe
        )
        if not callable(getattr(probe, "snapshot", None)):
            raise TypeError("target_probe must provide snapshot()")
        if desktop_workspace is not None:
            workspace_binding = getattr(
                desktop_workspace,
                "binding",
                None,
            )
            if (
                type(workspace_binding) is not TargetBinding
            ):
                raise TypeError(
                    "desktop_workspace must expose a target binding"
                )
        if type(raw_device) is not RawDeviceExecutor:
            raise TypeError("raw_device must be RawDeviceExecutor")
        if launcher is not None and not callable(launcher):
            raise TypeError("launcher must be callable or null")
        if type(desktop_shell) is not bool:
            raise TypeError("desktop_shell must be boolean")
        if receipt_trace_sink is not None and not callable(
            getattr(receipt_trace_sink, "record", None)
        ):
            raise TypeError("receipt_trace_sink must provide record()")
        if (
            isinstance(post_input_settle_delay_s, bool)
            or not isinstance(post_input_settle_delay_s, (int, float))
        ):
            raise TypeError(
                "post_input_settle_delay_s must be a finite number"
            )
        settle_delay = float(post_input_settle_delay_s)
        if (
            not math.isfinite(settle_delay)
            or settle_delay < 0.0
            or settle_delay > 1.0
        ):
            raise ValueError(
                "post_input_settle_delay_s must be in the range 0..1"
            )
        if not callable(wait):
            raise TypeError("wait must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(held_pointer_timeout_s, bool)
            or not isinstance(held_pointer_timeout_s, (int, float))
            or not math.isfinite(float(held_pointer_timeout_s))
            or not 1.0 <= float(held_pointer_timeout_s) <= 300.0
        ):
            raise ValueError(
                "held_pointer_timeout_s must be in the range 1..300"
            )

        self._authority = frame_authority
        self._observer = frame_observer
        self._fixed_binding = frozen_target_binding
        self._workspace = desktop_workspace
        self._probe = probe
        self._raw = raw_device
        self._launcher = launcher
        self._desktop_shell = desktop_shell
        self._trace = receipt_trace_sink
        self._post_input_settle_delay_s = settle_delay
        self._wait = wait
        self._clock = clock
        self._held_pointer_timeout_s = float(held_pointer_timeout_s)
        self._pointer_lock = threading.RLock()
        self._held_pointer: _HeldPointerLease | None = None
        self._pointer_uncertain = False
        self._closed = False

    @property
    def _is_desktop_shell(self) -> bool:
        # The desktop-shell flag only describes the initial no-foreground
        # Progman binding.  Once the workspace adopts a real window the flag
        # retires, so the adapter must follow the live workspace instead of
        # keeping a frozen copy that would keep cropping frames.
        if self._workspace is not None:
            return bool(
                getattr(self._workspace, "desktop_shell", self._desktop_shell)
            )
        return self._desktop_shell

    @property
    def _binding(self) -> TargetBinding:
        workspace = self._workspace
        return (
            self._fixed_binding
            if workspace is None
            else workspace.binding
        )

    @property
    def frozen_target_binding(self) -> TargetBinding:
        return self._binding

    def pointer_state(self) -> PointerLeaseState:
        with self._pointer_lock:
            lease = self._held_pointer
            if (
                lease is not None
                and not self._pointer_uncertain
                and self._clock() >= lease.expires_at
            ):
                self._release_held_pointer()
                lease = self._held_pointer
            if lease is None:
                return PointerLeaseState(PointerDisposition.RELEASED)
            if self._pointer_uncertain:
                return PointerLeaseState(
                    PointerDisposition.UNKNOWN,
                    lease.lease_id,
                    False,
                )
            return PointerLeaseState(
                PointerDisposition.HELD,
                lease.lease_id,
                True,
            )

    def close(self) -> None:
        with self._pointer_lock:
            if self._closed:
                return
            released = DESKTOP_ACTION_QUEUE.run(
                "atomic_input_cleanup",
                self._release_all_input_locked,
                scope="physical",
            )
            if released:
                self._closed = True
        if not released:
            raise RuntimeError("held pointer cleanup could not be confirmed")

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None = None,
    ) -> ControllerTransition:
        with self._pointer_lock:
            if self._closed:
                raise RuntimeError("atomic desktop adapter is closed")
            return self._execute_open(
                envelope,
                stop_event=stop_event,
            )

    def _execute_open(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None,
    ) -> ControllerTransition:
        transition: ControllerTransition | None = None
        held_before = self._held_pointer is not None
        try:
            transition = self._execute_once(
                envelope,
                stop_event=stop_event,
            )
        except Exception:
            self._release_held_pointer()
            raise
        if transition.error_kind == "release_cleanup_incomplete":
            return transition
        preserves_hold = bool(
            transition.outcome is ControllerOutcome.COMPLETED
            and transition.input_completed
            and self._held_pointer is not None
            and (
                (
                    type(envelope.proposal) is DesktopActionProposal
                    and envelope.proposal.action == "drag"
                    and envelope.proposal.args.get("keep_holding") is True
                )
                or (
                    held_before
                    and self._held_pointer is not None
                )
            )
        )
        rejected_without_input_preserves_hold = bool(
            held_before
            and self._held_pointer is not None
            and not self._pointer_uncertain
            and transition.outcome is ControllerOutcome.REJECTED
            and transition.input_completed is False
            and not (stop_event is not None and stop_event.is_set())
        )
        if (
            not preserves_hold
            and not rejected_without_input_preserves_hold
            and not self._release_held_pointer()
        ):
            return self._unknown_transition(
                envelope,
                transition.action,
                input_completed=transition.input_completed,
                error_kind="release_cleanup_incomplete",
            )
        return transition

    def _release_all_input_locked(self) -> bool:
        from ...action.executor import release_held_input_state

        released = False
        for _attempt in range(2):
            pointer_released = self._release_held_pointer_locked()
            sequence_released = release_held_input_state()
            released = pointer_released and sequence_released
            if released:
                break
        return released

    def _execute_once(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None = None,
    ) -> ControllerTransition:
        """Execute one envelope once; no branch in this method replays input."""

        if type(envelope) is not ActorDecisionEnvelope:
            raise TypeError(
                "AtomicDesktopAdapter requires ActorDecisionEnvelope"
            )
        proposal = envelope.proposal
        if type(proposal) is not DesktopActionProposal:
            raise ValueError(
                "AtomicDesktopAdapter accepts only desktop_action proposals"
            )
        action = proposal.action
        if action not in _ACTIONS:
            raise ValueError("desktop action is outside the atomic surface")

        try:
            prepared = self._prepare_action(envelope, proposal)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            error_kind = f"invalid_atomic_action: {detail}"[:500]
            receipt = self._not_started(action, error_kind)
            self._record_receipt(
                receipt,
                envelope,
                phase="prepare",
            )
            return self._rejected(
                envelope,
                action,
                error_kind=receipt.error_kind,
            )
        try:
            matched, pinned = self._authority.run_if_current(
                envelope,
                lambda: self._execute_pinned(
                    envelope,
                    prepared,
                    stop_event=stop_event,
                ),
            )
        except Exception:
            receipt = self._unknown(action, "atomic_callback_exception")
            self._record_receipt(
                receipt,
                envelope,
                phase="authority_callback",
            )
            return self._unknown_transition(
                envelope,
                action,
                input_completed=None,
                error_kind=receipt.error_kind,
            )

        if not matched or pinned is None:
            receipt = self._not_started(action, "stale_authoritative_frame")
            self._record_receipt(
                receipt,
                envelope,
                phase="frame_pin",
            )
            return self._rejected(envelope, action, error_kind=receipt.error_kind)

        receipt = pinned.receipt
        self._record_trace_receipt(receipt, envelope, phase="raw_device")
        if not pinned.raw_device_called:
            if receipt.input_completed is False:
                return self._rejected(
                    envelope,
                    action,
                    error_kind=self._receipt_error_kind(receipt),
                )
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
                error_kind=self._receipt_error_kind(receipt),
            )
        if receipt.input_completed is False:
            return self._rejected(
                envelope,
                action,
                error_kind=self._receipt_error_kind(receipt),
            )
        if (
            receipt.input_completed is True
            and self._post_input_settle_delay_s > 0.0
        ):
            try:
                self._wait(self._post_input_settle_delay_s)
            except Exception:
                # Settling only improves the model's next picture.  It cannot
                # rewrite input the physical backend already completed.
                pass
        if pinned.before_frame is None:
            if receipt.input_completed is True:
                return ControllerTransition(
                    outcome=ControllerOutcome.COMPLETED,
                    action_id=envelope.decision_id,
                    action=action,
                    input_completed=True,
                    visible_effect=self._completed_input_effect(
                        receipt, prepared,
                    ),
                )
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
                error_kind=self._receipt_error_kind(receipt),
            )

        post_frame: RuntimeFrame | None = None
        try:
            post_frame = self._capture_post_action(
                prepared,
                pinned.before_frame,
            )
        except Exception:
            if receipt.input_completed is True:
                return ControllerTransition(
                    outcome=ControllerOutcome.COMPLETED,
                    action_id=envelope.decision_id,
                    action=action,
                    input_completed=True,
                    visible_effect=self._completed_input_effect(
                        receipt, prepared,
                    ),
                )
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
                error_kind=self._receipt_error_kind(receipt),
            )

        if receipt.input_completed is None:
            return self._unknown_transition(
                envelope,
                action,
                input_completed=None,
                post_frame=post_frame,
                error_kind=self._receipt_error_kind(receipt),
            )

        return ControllerTransition(
            outcome=ControllerOutcome.COMPLETED,
            action_id=envelope.decision_id,
            action=action,
            input_completed=True,
            visible_effect=self._completed_input_effect(receipt, prepared),
            post_frame_id=post_frame.frame_id,
        )

    def _execute_pinned(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        *,
        stop_event: threading.Event | None,
    ) -> _PinnedExecution:
        state = self._authority.current
        if state is None:
            return _PinnedExecution(
                None,
                self._not_started(prepared.action, "frame_unavailable"),
            )
        try:
            before_frame = self._runtime_frame(state)
        except Exception:
            return _PinnedExecution(
                None,
                self._not_started(prepared.action, "invalid_pre_frame"),
            )

        # The submitted frame is the model's authority.  A window snapshot is
        # only useful as launch/capture metadata; it cannot veto physical input.
        initial = (
            self._safe_snapshot()
            if before_frame.scope == "target_window"
            else None
        )
        if stop_event is not None and stop_event.is_set():
            return _PinnedExecution(
                before_frame,
                self._not_started(prepared.action, "stop_requested"),
            )

        raw_started = False

        def invoke_raw() -> RawActionReceipt:
            nonlocal raw_started
            if stop_event is not None and stop_event.is_set():
                return self._not_started(
                    prepared.action,
                    "stop_requested_after_queue",
                )
            self._publish_physical_attempt(
                envelope,
                prepared,
                before_frame,
            )
            raw_started = True
            receipt = self._run_raw(
                envelope,
                prepared,
                before_frame,
                initial,
                stop_event=stop_event,
            )
            if self._receipt_error_kind(receipt) != "release_cleanup_incomplete":
                return receipt
            try:
                released = self._release_all_input_locked()
            except Exception:
                released = False
            if not released:
                return receipt
            return replace(
                receipt,
                error_kind="input_outcome_unknown_after_release_cleanup",
            )

        try:
            screen_first = before_frame.scope == "full_desktop"
            receipt = DESKTOP_ACTION_QUEUE.run(
                prepared.action,
                invoke_raw,
                # Physical input is process-global.  A window-specific queue
                # key belongs only to an explicit target-window crop; the
                # default full-screen route must not inherit its old anchor.
                target_hwnd=(
                    None if screen_first else self._binding.hwnd
                ),
                scope="physical",
                metadata={
                    "owner": "vnext_atomic_desktop_adapter",
                    "mission_id": envelope.mission_id,
                    "run_id": envelope.run_id,
                    "decision_id": envelope.decision_id,
                    "frame_id": envelope.frame_id,
                    "frame_epoch": envelope.frame_epoch,
                    "frame_hwnd": envelope.frame_hwnd,
                    "target_binding_id": (
                        None
                        if screen_first
                        else envelope.target_binding_id
                    ),
                },
                coalesce_nested=True,
                cancel_event=stop_event,
            )
        except DesktopActionCanceled:
            receipt = self._not_started(
                prepared.action,
                "desktop_queue_canceled",
            )
        except Exception:
            receipt = (
                self._unknown(prepared.action, "desktop_queue_exception")
                if raw_started
                else self._unknown(
                    prepared.action,
                    "desktop_queue_boundary_exception",
                )
            )
        return _PinnedExecution(before_frame, receipt, raw_started)

    def _run_raw(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before_frame: RuntimeFrame,
        initial_target: LiveTargetSnapshot | None,
        *,
        stop_event: threading.Event | None,
    ) -> RawActionReceipt:
        if prepared.action == "launch_application":
            return self._run_launch(
                envelope,
                prepared,
                before_frame,
                initial_target,
                stop_event=stop_event,
            )
        input_hwnd = (
            None
            if before_frame.scope == "full_desktop"
            else initial_target.hwnd
            if initial_target is not None
            else self._binding.hwnd
        )
        context = RawActionContext(
            target_hwnd=input_hwnd,
            current_hwnd=input_hwnd,
        )

        if prepared.action == "input_sequence":
            assert prepared.events is not None
            receipt = self._raw.input_sequence(
                context,
                prepared.events,
                enforce_foreground=False,
                stop_event=stop_event,
            )
            confirmed_events = confirmed_input_event_prefix(
                prepared.events,
                receipt,
            )
            if confirmed_events:
                with self._pointer_lock:
                    held = self._held_pointer
                    if held is not None and any(
                        event.kind is InputEventKind.MOUSE_UP
                        and event.button is held.button
                        for event in confirmed_events
                    ):
                        self._held_pointer = None
                        self._pointer_uncertain = False
            return receipt

        if prepared.point is not None:
            expected_point = prepared.point

            if prepared.action in {"click", "double_click"}:
                return self._raw.click(
                    context,
                    expected_point[0],
                    expected_point[1],
                    prepared.button,
                    (
                        ClickCount.DOUBLE
                        if prepared.action == "double_click"
                        else ClickCount.SINGLE
                    ),
                    ClickMechanism.PHYSICAL,
                    press_duration_ms=prepared.press_duration_ms,
                    enforce_foreground=False,
                    stop_event=stop_event,
                )
            if prepared.action == "move_mouse":
                return self._raw.move_mouse(
                    context,
                    expected_point[0],
                    expected_point[1],
                    duration_ms=prepared.duration_ms,
                    enforce_foreground=False,
                    stop_event=stop_event,
                )
            if prepared.action == "drag":
                assert prepared.end_point is not None
                arguments: dict[str, Any] = {}
                if prepared.points:
                    arguments["points"] = list(prepared.points)
                if prepared.hold_control_present:
                    arguments.update(
                        pointer_already_held=prepared.started_held,
                        keep_holding=prepared.keep_holding,
                        hold_token=prepared.pointer_lease_id,
                    )
                if prepared.held_keys:
                    arguments["held_keys"] = prepared.held_keys
                receipt = self._raw.drag(
                    context,
                    expected_point[0],
                    expected_point[1],
                    prepared.end_point[0],
                    prepared.end_point[1],
                    button=prepared.button,
                    duration_ms=prepared.duration_ms,
                    enforce_foreground=False,
                    stop_event=stop_event,
                    **arguments,
                )
                return self._update_pointer_after_drag(
                    prepared,
                    receipt,
                    input_hwnd=input_hwnd,
                )
            assert prepared.action == "scroll" and prepared.amount is not None
            return self._raw.scroll(
                context,
                expected_point[0],
                expected_point[1],
                prepared.amount,
                enforce_foreground=False,
                stop_event=stop_event,
            )

        if prepared.action == "type_text":
            assert prepared.text is not None
            return self._raw.type_text(
                context,
                prepared.text,
                enforce_foreground=False,
                stop_event=stop_event,
            )
        assert prepared.action == "hotkey" and prepared.chord is not None
        return self._raw.hotkey(
            context,
            prepared.chord,
            repeat=prepared.repeat,
            enforce_foreground=False,
            stop_event=stop_event,
        )

    def _run_launch(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before_frame: RuntimeFrame,
        initial_target: LiveTargetSnapshot | None,
        *,
        stop_event: threading.Event | None,
    ) -> RawActionReceipt:
        """Execute one generic application launch through the injected launcher."""
        if stop_event is not None and stop_event.is_set():
            return self._not_started(
                prepared.action,
                "stop_requested",
            )
        launcher = self._launcher
        if launcher is None:
            return self._not_started(
                prepared.action,
                "launch_application_unavailable",
            )
        app = prepared.launch_app or ""
        arguments = prepared.launch_arguments or ""
        try:
            result = launcher(app, arguments)
        except Exception:
            return self._not_started(
                prepared.action,
                "launch_application_failed",
            )
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            return self._not_started(
                prepared.action,
                "launch_application_failed",
            )
        via = str(result.get("via") or "generic")
        facts = {
            "via": via,
            "target": str(result.get("target") or "")[:512],
        }
        activated_hwnd = result.get("activated_hwnd")
        if activated_hwnd:
            try:
                facts["activated_hwnd"] = int(activated_hwnd)
            except (TypeError, ValueError):
                pass
        if via == "existing":
            facts["existing"] = True
            candidate_count = result.get("candidate_count")
            if type(candidate_count) is int and candidate_count >= 0:
                facts["candidate_count"] = candidate_count
            note = result.get("note")
            if isinstance(note, str) and note.strip():
                facts["note"] = note.strip()[:_MAX_LAUNCH_EFFECT_CHARS]
            elif activated_hwnd:
                facts["note"] = (
                    "already running: activated the existing window; "
                    "no new instance was started"
                )
            else:
                facts["note"] = (
                    "already running: an existing window was found; "
                    "no new instance was started"
                )
        return RawActionReceipt(
            action=prepared.action,
            outcome=MutationOutcome.APPLIED,
            backend_result=RawBackendResult(True, facts),
        )

    @staticmethod
    def _launch_visible_effect(
        receipt: RawActionReceipt,
        action: str,
    ) -> str:
        """Surface the launch outcome fact to the model's previous-action view.

        A GUI agent decides "is the app open" from what it sees; a
        tray-hidden or covered window can look closed.  When launch reused a
        running instance, the note is projected into ``visible_effect`` so
        the model reads "activated existing window, no new instance" instead
        of concluding it must start a fresh (possibly login-requiring)
        instance.
        """
        if action != "launch_application" or receipt.backend_result is None:
            return ""
        facts = dict(receipt.backend_result.facts) or {}
        note = facts.get("note")
        if isinstance(note, str) and note:
            return note[:_MAX_LAUNCH_EFFECT_CHARS]
        via = str(facts.get("via") or "")
        if via == "existing":
            if facts.get("activated_hwnd"):
                return (
                    "activated an already-running instance; "
                    "no new one started"
                )
            return "found an already-running instance; no new one started"
        return ""

    @classmethod
    def _completed_input_effect(
        cls,
        receipt: RawActionReceipt,
        prepared: _PreparedAction,
    ) -> str:
        """Describe only the completed mechanical input, never its outcome."""

        if prepared.action == "launch_application":
            return cls._launch_visible_effect(receipt, prepared.action)
        if (
            prepared.action == "hotkey"
            and receipt.input_completed is True
            and prepared.chord is not None
        ):
            keys = "+".join(prepared.chord.keys)
            return (
                "physical keyboard receipt: pressed "
                f"{keys} {prepared.repeat} time(s); inspect the current "
                "screen to judge the resulting state"
            )
        return ""

    def _update_pointer_after_drag(
        self,
        prepared: _PreparedAction,
        receipt: RawActionReceipt,
        *,
        input_hwnd: int,
    ) -> RawActionReceipt:
        if not prepared.hold_control_present:
            return receipt
        facts = (
            {}
            if receipt.backend_result is None
            else dict(receipt.backend_result.facts)
        )
        held_after = facts.get("held_after")
        if receipt.input_completed is True:
            if prepared.keep_holding:
                if (
                    held_after is not True
                    or prepared.pointer_lease_id is None
                    or prepared.end_point is None
                ):
                    with self._pointer_lock:
                        if (
                            self._held_pointer is None
                            and prepared.pointer_lease_id is not None
                        ):
                            self._held_pointer = _HeldPointerLease(
                                lease_id=prepared.pointer_lease_id,
                                cursor=prepared.end_point or (0, 0),
                                input_hwnd=input_hwnd,
                                button=prepared.button,
                                expires_at=self._clock(),
                            )
                        self._pointer_uncertain = True
                    return self._unknown(
                        "drag",
                        "held_pointer_state_unconfirmed",
                    )
                with self._pointer_lock:
                    self._held_pointer = _HeldPointerLease(
                        lease_id=prepared.pointer_lease_id,
                        cursor=prepared.end_point,
                        input_hwnd=input_hwnd,
                        button=prepared.button,
                        expires_at=(
                            self._clock() + self._held_pointer_timeout_s
                        ),
                    )
                    self._pointer_uncertain = False
            else:
                if prepared.started_held and held_after is not False:
                    with self._pointer_lock:
                        self._pointer_uncertain = True
                    return self._unknown(
                        "drag",
                        "held_pointer_release_unconfirmed",
                    )
                with self._pointer_lock:
                    self._held_pointer = None
                    self._pointer_uncertain = False
            return receipt

        if receipt.input_completed is False:
            return receipt

        if prepared.started_held or prepared.keep_holding:
            with self._pointer_lock:
                if self._held_pointer is None and prepared.pointer_lease_id:
                    self._held_pointer = _HeldPointerLease(
                        lease_id=prepared.pointer_lease_id,
                        cursor=(
                            prepared.end_point
                            if prepared.end_point is not None
                            else prepared.point
                            if prepared.point is not None
                            else (0, 0)
                        ),
                        input_hwnd=input_hwnd,
                        button=prepared.button,
                        expires_at=self._clock(),
                    )
                self._pointer_uncertain = True
        return receipt

    def _release_held_pointer(self) -> bool:
        with self._pointer_lock:
            if self._held_pointer is None:
                self._pointer_uncertain = False
                return True
            return DESKTOP_ACTION_QUEUE.run(
                "held_pointer_cleanup",
                self._release_held_pointer_locked,
                scope="physical",
            )

    def _release_held_pointer_locked(self) -> bool:
        lease = self._held_pointer
        if lease is None:
            self._pointer_uncertain = False
            return True
        try:
            receipt = self._raw.release_held_drag(lease.lease_id)
        except Exception:
            self._pointer_uncertain = True
            return False
        if receipt.input_completed is not True:
            self._pointer_uncertain = True
            return False
        self._held_pointer = None
        self._pointer_uncertain = False
        return True

    def _capture_post_action(
        self,
        prepared: _PreparedAction,
        before: RuntimeFrame,
    ) -> RuntimeFrame:
        if before.scope == "full_desktop":
            region = virtual_screen_region()
            region_tuple = (
                region.left,
                region.top,
                region.width,
                region.height,
            )
            captured = self._observer.capture(
                epoch=before.epoch,
                hwnd=before.hwnd,
                phase="post_action",
                action=prepared.action,
                provided_region=region,
                expected_previous_frame_id=before.frame_id,
            )
            if captured is None or captured.scope != "full_desktop":
                raise RuntimeError("post-action frame capture unavailable")
            self._validate_jpeg(captured.image_b64)
            current = self._authority.current
            if current is None or not self._frame_state_matches(current, before):
                raise RuntimeError("frame changed before post-action commit")
            committed = self._observer.commit(captured)
            if (
                committed.frame_id != before.frame_id + 1
                or committed.epoch != before.epoch
                or committed.hwnd != before.hwnd
                or committed.scope != "full_desktop"
                or committed.region != region_tuple
            ):
                raise RuntimeError("post-action frame commit identity changed")
            return self._runtime_frame(committed)

        first = self._safe_snapshot()
        # A crop is an observation hint, not action authority.  Prefer fresh
        # window geometry when it exists and fall back to the exact region the
        # model saw when the window disappears or changes during the action.
        region = Region(*(first.region if first is not None else before.region))
        expected_scope = "target_window"
        region_tuple = (
            region.left,
            region.top,
            region.width,
            region.height,
        )
        captured = self._observer.capture(
            epoch=self._binding.epoch,
            hwnd=self._binding.hwnd,
            phase="post_action",
            action=prepared.action,
            provided_region=region,
            expected_previous_frame_id=before.frame_id,
            scope_override=(
                "target_window" if self._is_desktop_shell else None
            ),
        )
        if captured is None or captured.scope != expected_scope:
            raise RuntimeError("post-action frame capture unavailable")
        self._validate_jpeg(captured.image_b64)

        current = self._authority.current
        if current is None or not self._frame_state_matches(current, before):
            raise RuntimeError("frame changed before post-action commit")

        committed = self._observer.commit(captured)
        if (
            committed.frame_id != before.frame_id + 1
            or committed.epoch != self._binding.epoch
            or committed.hwnd != self._binding.hwnd
            or committed.scope != expected_scope
            or committed.region != region_tuple
        ):
            raise RuntimeError("post-action frame commit identity changed")

        return self._runtime_frame(committed)

    def _prepare_action(
        self,
        envelope: ActorDecisionEnvelope,
        proposal: DesktopActionProposal,
    ) -> _PreparedAction:
        action = proposal.action
        args = dict(proposal.args)
        if action in {"click", "double_click"}:
            self._require_keys(args, {"image_x", "image_y"})
            point = self._map_point(envelope, args)
            try:
                button = MouseButton(str(args.get("button", "left")).casefold())
            except Exception as exc:
                raise ValueError("click button is invalid") from exc
            press_duration_ms = args.get("press_duration_ms", 35)
            if (
                type(press_duration_ms) is not int
                or not 0 <= press_duration_ms <= 2_000
            ):
                raise ValueError("click press_duration_ms must be in 0..2000")
            return _PreparedAction(
                action=action,
                point=point,
                button=button,
                press_duration_ms=press_duration_ms,
            )
        if action == "move_mouse":
            self._require_keys(args, {"image_x", "image_y"})
            duration_ms = args.get("duration_ms", 0)
            if (
                type(duration_ms) is not int
                or not 0 <= duration_ms <= MAX_POINTER_DURATION_MS
            ):
                raise ValueError("move_mouse duration_ms must be in 0..30000")
            return _PreparedAction(
                action=action,
                point=self._map_point(envelope, args),
                duration_ms=duration_ms,
            )
        if action == "scroll":
            self._require_keys(args, {"image_x", "image_y", "amount"})
            amount = args["amount"]
            if (
                type(amount) is not int
                or amount == 0
                or abs(amount) > _MAX_SCROLL_AMOUNT
            ):
                raise ValueError(
                    "scroll amount must be a non-zero integer in -1200..1200"
                )
            point = self._map_point(envelope, args)
            return _PreparedAction(
                action=action,
                point=point,
                amount=amount,
            )
        if action == "drag":
            if "image_path" not in args:
                raise ValueError(
                    "atomic action arguments are missing required fields"
                )
            hold_control_present = True
            keep_holding = args.get("keep_holding", False)
            if type(keep_holding) is not bool:
                raise ValueError("keep_holding must be boolean")
            started_held = envelope.pointer_disposition == "held"
            try:
                button = MouseButton(str(args.get("button", "left")).casefold())
            except Exception as exc:
                raise ValueError("drag button is invalid") from exc
            held_keys = tuple(canonical_hotkey_args(
                normalize_hotkey_args(args.get("held_keys", ()))
            ))
            if (
                len(held_keys) != len(set(held_keys))
                or any(key not in CANONICAL_HOTKEY_KEYS for key in held_keys)
            ):
                raise ValueError("drag held_keys contains an invalid key")
            duration_ms = args.get("duration_ms", 400)
            if (
                type(duration_ms) is not int
                or not 0 <= duration_ms <= MAX_POINTER_DURATION_MS
            ):
                raise ValueError("drag duration_ms must be in 0..30000")
            raw_path = args["image_path"]
            minimum = 1 if started_held else 2
            if (
                not isinstance(raw_path, (list, tuple))
                or len(raw_path) < minimum
            ):
                raise ValueError(
                    f"image_path must contain at least {minimum} point"
                    f"{'s' if minimum != 1 else ''}"
                )
            mapped_path: list[PhysicalPoint] = []
            for index, raw_point in enumerate(raw_path):
                if (
                    not isinstance(raw_point, (list, tuple))
                    or len(raw_point) != 2
                ):
                    raise ValueError(f"image_path[{index}] must be [x,y]")
                point_args = {
                    "image_x": raw_point[0],
                    "image_y": raw_point[1],
                }
                mapped_path.append(self._map_point(envelope, point_args))
            pointer_lease_id: str | None = None
            if started_held:
                with self._pointer_lock:
                    lease = self._held_pointer
                    if lease is None or self._pointer_uncertain:
                        raise ValueError(
                            "held drag continuation has no known physical press"
                        )
                    start = lease.cursor
                    pointer_lease_id = lease.lease_id
                    button = lease.button
                movement = mapped_path
            else:
                start = mapped_path[0]
                movement = mapped_path[1:]
            end = movement[-1]
            points = tuple(movement[:-1])
            if not started_held and keep_holding:
                digest = hashlib.sha256(
                    (
                        f"{envelope.mission_id}|{envelope.run_id}|"
                        f"{envelope.decision_id}"
                    ).encode("utf-8")
                ).hexdigest()
                pointer_lease_id = f"pointer-lease-{digest[:24]}"
            return _PreparedAction(
                action=action,
                point=start,
                end_point=end,
                points=points,
                button=button,
                held_keys=held_keys,
                duration_ms=duration_ms,
                keep_holding=keep_holding,
                hold_control_present=hold_control_present,
                started_held=started_held,
                pointer_lease_id=pointer_lease_id,
            )
        if action == "input_sequence":
            self._require_keys(args, {"sequence"})
            steps = parse_input_sequence(args["sequence"])
            physical_events: list[PhysicalInputEvent] = []
            mapped_points: list[PhysicalPoint] = []
            for step in steps:
                kind = InputEventKind(step.kind)
                if kind is InputEventKind.MOVE:
                    point_args = {
                        "image_x": step.image_x,
                        "image_y": step.image_y,
                    }
                    x, y = self._map_point(envelope, point_args)
                    mapped_points.append((x, y))
                    physical_events.append(PhysicalInputEvent(
                        kind,
                        x=x,
                        y=y,
                        duration_ms=step.duration_ms,
                    ))
                elif kind is InputEventKind.MOVE_RELATIVE:
                    assert step.image_x is not None and step.image_y is not None
                    dx, dy = self._map_relative_delta(
                        envelope,
                        step.image_x,
                        step.image_y,
                    )
                    physical_events.append(PhysicalInputEvent(
                        kind,
                        x=dx,
                        y=dy,
                        duration_ms=step.duration_ms,
                    ))
                elif kind in {InputEventKind.MOUSE_DOWN, InputEventKind.MOUSE_UP}:
                    physical_events.append(PhysicalInputEvent(
                        kind,
                        button=MouseButton(step.button),
                    ))
                elif kind in {InputEventKind.KEY_DOWN, InputEventKind.KEY_UP}:
                    physical_events.append(PhysicalInputEvent(kind, key=step.key))
                elif kind is InputEventKind.TYPE_TEXT:
                    physical_events.append(PhysicalInputEvent(kind, text=step.text))
                elif kind in {InputEventKind.WHEEL, InputEventKind.HWHEEL}:
                    physical_events.append(PhysicalInputEvent(kind, amount=step.amount))
                else:
                    physical_events.append(PhysicalInputEvent(
                        kind,
                        duration_ms=step.duration_ms,
                    ))
            return _PreparedAction(
                action=action,
                point=mapped_points[0] if mapped_points else None,
                points=tuple(mapped_points[1:]),
                events=tuple(physical_events),
            )
        if action == "type_text":
            self._require_keys(args, {"text"})
            text = args["text"]
            if (
                type(text) is not str
                or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS
            ):
                raise ValueError("type_text exceeds the strict raw contract")
            return _PreparedAction(
                action=action,
                text=text,
            )
        if action == "hotkey":
            self._require_keys(args, {"keys"})
            keys = canonical_hotkey_args(normalize_hotkey_args(args["keys"]))
            if not keys:
                raise ValueError("hotkey keys must be an array")
            repeat = args.get("repeat", 1)
            if type(repeat) is not int or not 1 <= repeat <= 100:
                raise ValueError("hotkey repeat must be in 1..100")
            return _PreparedAction(
                action=action,
                chord=HotkeyChord(tuple(keys)),
                repeat=repeat,
            )
        if action == "launch_application":
            if "app" not in args:
                raise ValueError(
                    "atomic launch arguments are missing required fields"
                )
            app = args["app"]
            launch_arguments = args.get("arguments", "")
            if type(app) is not str or not app.strip() or len(app) > 512:
                raise ValueError("launch_application app is invalid")
            if (
                type(launch_arguments) is not str
                or len(launch_arguments) > 4096
            ):
                raise ValueError("launch_application arguments are invalid")
            return _PreparedAction(
                action=action,
                launch_app=app.strip(),
                launch_arguments=launch_arguments,
            )
        raise ValueError("unsupported atomic desktop action")

    @staticmethod
    def _physical_attempt_arguments(
        prepared: _PreparedAction,
    ) -> dict[str, Any]:
        """Describe the exact typed payload submitted to RawDevice."""

        if prepared.action in {"click", "double_click"}:
            assert prepared.point is not None
            arguments = {
                "kind": "pointer_click",
                "x": prepared.point[0],
                "y": prepared.point[1],
                "button": prepared.button.value,
                "click_count": (
                    2 if prepared.action == "double_click" else 1
                ),
            }
            if prepared.press_duration_ms != 35:
                arguments["press_duration_ms"] = prepared.press_duration_ms
            return arguments
        if prepared.action == "move_mouse":
            assert prepared.point is not None
            arguments = {
                "kind": "pointer_move",
                "x": prepared.point[0],
                "y": prepared.point[1],
            }
            if prepared.duration_ms:
                arguments["duration_ms"] = prepared.duration_ms
            return arguments
        if prepared.action == "scroll":
            assert prepared.point is not None
            assert prepared.amount is not None
            return {
                "kind": "wheel",
                "x": prepared.point[0],
                "y": prepared.point[1],
                "signed_wheel_amount": prepared.amount,
                "event_wheel_delta": prepared.amount,
            }
        if prepared.action == "drag":
            assert prepared.point is not None
            assert prepared.end_point is not None
            arguments = {
                "kind": "pointer_drag",
                "x1": prepared.point[0],
                "y1": prepared.point[1],
                "x2": prepared.end_point[0],
                "y2": prepared.end_point[1],
                "button": prepared.button.value,
            }
            if prepared.held_keys:
                arguments["held_keys"] = list(prepared.held_keys)
            if prepared.duration_ms != 400:
                arguments["duration_ms"] = prepared.duration_ms
            if prepared.points:
                arguments["points"] = [
                    [point[0], point[1]]
                    for point in prepared.points
                ]
            if prepared.hold_control_present:
                arguments.update(
                    started_held=prepared.started_held,
                    keep_holding=prepared.keep_holding,
                )
            return arguments
        if prepared.action == "input_sequence":
            assert prepared.events is not None
            arguments: dict[str, Any] = {
                "kind": "input_sequence",
                "event_count": len(prepared.events),
                "pointer_event_count": sum(
                    event.kind in {
                        InputEventKind.MOVE,
                        InputEventKind.MOVE_RELATIVE,
                        InputEventKind.MOUSE_DOWN,
                        InputEventKind.MOUSE_UP,
                        InputEventKind.WHEEL,
                        InputEventKind.HWHEEL,
                    }
                    for event in prepared.events
                ),
                "keyboard_event_count": sum(
                    event.kind in {
                        InputEventKind.KEY_DOWN,
                        InputEventKind.KEY_UP,
                        InputEventKind.TYPE_TEXT,
                    }
                    for event in prepared.events
                ),
            }
            events: list[dict[str, Any]] = []
            pointer_events: list[dict[str, int | str | None]] = []
            for index, event in enumerate(prepared.events):
                serialized: dict[str, Any] = {"kind": event.kind.value}
                if event.kind in {
                    InputEventKind.MOVE,
                    InputEventKind.MOVE_RELATIVE,
                }:
                    serialized.update(
                        x=event.x,
                        y=event.y,
                        duration_ms=event.duration_ms,
                    )
                    pointer_events.append({
                        "index": index,
                        "kind": event.kind.value,
                        "x": event.x,
                        "y": event.y,
                        "duration_ms": event.duration_ms,
                    })
                elif event.kind in {
                    InputEventKind.MOUSE_DOWN,
                    InputEventKind.MOUSE_UP,
                }:
                    assert event.button is not None
                    serialized["button"] = event.button.value
                    pointer_events.append({
                        "index": index,
                        "kind": event.kind.value,
                        "button": event.button.value,
                    })
                elif event.kind in {InputEventKind.WHEEL, InputEventKind.HWHEEL}:
                    serialized["amount"] = event.amount
                    pointer_events.append({
                        "index": index,
                        "kind": event.kind.value,
                        "amount": event.amount,
                    })
                elif event.kind in {InputEventKind.KEY_DOWN, InputEventKind.KEY_UP}:
                    serialized["key"] = event.key
                elif event.kind is InputEventKind.TYPE_TEXT:
                    assert event.text is not None
                    serialized["codepoint_count"] = len(event.text)
                else:
                    serialized["duration_ms"] = event.duration_ms
                events.append(serialized)
            arguments["events"] = events
            arguments["pointer_events"] = pointer_events
            return arguments
        if prepared.action == "type_text":
            assert prepared.text is not None
            return {
                "kind": "type_text",
                "text": prepared.text,
                "codepoint_count": len(prepared.text),
            }
        if prepared.action == "launch_application":
            assert prepared.launch_app is not None
            return {
                "kind": "launch_application",
                "app": prepared.launch_app,
                "arguments": prepared.launch_arguments or "",
            }
        assert prepared.action == "hotkey"
        assert prepared.chord is not None
        arguments = {
            "kind": "hotkey",
            "modifiers": list(prepared.chord.keys[:-1]),
            "key": prepared.chord.keys[-1],
        }
        if prepared.repeat != 1:
            arguments["repeat"] = prepared.repeat
        return arguments

    def _publish_physical_attempt(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        frame: RuntimeFrame,
    ) -> None:
        """Publish the exact physical attempt to the single durable trace."""

        source = {
            key: value
            for key, value in envelope.proposal.args.items()
            if key in {
                "image_x",
                "image_y",
                "image_x1",
                "image_y1",
                "image_x2",
                "image_y2",
                "image_path",
                "amount",
                "keep_holding",
                "button",
                "held_keys",
                "duration_ms",
                "press_duration_ms",
                "text",
                "modifiers",
                "key",
                "app",
                "arguments",
            }
        }
        if isinstance(source.get("image_path"), (list, tuple)):
            source["image_path"] = [
                list(point)
                if isinstance(point, (list, tuple))
                else point
                for point in source["image_path"]
            ]
        if isinstance(source.get("held_keys"), (list, tuple)):
            source["held_keys"] = list(source["held_keys"])
        if isinstance(source.get("modifiers"), (list, tuple)):
            source["modifiers"] = list(source["modifiers"])
        physical_arguments = self._physical_attempt_arguments(prepared)
        binding = (
            None
            if envelope.frame_scope == "full_desktop"
            else self._binding
        )
        target_identity = (
            {}
            if binding is None
            else {
                "executable_identity": binding.executable_identity,
                "title_snapshot": binding.title_snapshot,
            }
        )
        replay_frame = None
        if prepared.action in {"click", "double_click"}:
            try:
                replay_frame = save_pending_click_frame(
                    run_id=envelope.run_id,
                    decision_id=envelope.decision_id,
                    image_b64=frame.image_b64,
                )
            except Exception:
                replay_frame = None
        try:
            BUS.publish(
                "vnext_physical_attempt",
                runtime_version="vnext",
                no_legacy_fallback=True,
                run_id=envelope.run_id,
                mission_id=envelope.mission_id,
                decision_id=envelope.decision_id,
                action=prepared.action,
                frame_id=frame.frame_id,
                frame_epoch=frame.epoch,
                frame_scope=frame.scope,
                submitted_image_size=list(envelope.submitted_image_size),
                frame_region=list(envelope.frame_region),
                image_coordinates=source,
                physical_arguments=physical_arguments,
                target_identity=target_identity,
                timestamp_ns=time.time_ns(),
                parameter_visibility="physical_attempt",
                **({"replay_frame": replay_frame} if replay_frame else {}),
            )
        except Exception:
            # Diagnostics must never reject or replay physical input.
            pass

    @staticmethod
    def _raw_device_route(action: str) -> str:
        return {
            "move_mouse": "raw_device.admitted_physical_move/0",
            "click": "raw_device.admitted_physical_click/0",
            "double_click": "raw_device.admitted_physical_click/0",
            "drag": "raw_device.admitted_physical_drag/0",
            "scroll": "raw_device.admitted_physical_scroll/0",
            "type_text": "raw_device.admitted_physical_type_text/0",
            "hotkey": "raw_device.admitted_physical_hotkey/0",
            "input_sequence": "raw_device.admitted_physical_sequence/0",
            "launch_application": "desktop_launcher.generic/0",
        }[action]

    @staticmethod
    def _require_keys(
        args: Mapping[str, Any],
        expected: set[str],
    ) -> None:
        # Required-subset check: the Actor schema already forbids extras at
        # the model boundary, so the mechanical layer tolerates surplus keys
        # instead of burning an Actor turn on a format-repair round.  Missing
        # required fields still fail closed.
        if not expected <= set(args):
            raise ValueError("atomic action arguments are missing required fields")

    @staticmethod
    def _map_point(
        envelope: ActorDecisionEnvelope,
        args: Mapping[str, Any],
        *,
        image_keys: tuple[str, str] = ("image_x", "image_y"),
    ) -> PhysicalPoint:
        """Map one submitted-image point directly to its captured screen pixel."""

        image_width, image_height = envelope.submitted_image_size
        left, top, frame_width, frame_height = envelope.frame_region
        raw_x, raw_y = (args[image_keys[0]], args[image_keys[1]])
        if type(raw_x) not in {int, float} or type(raw_y) not in {int, float}:
            raise ValueError("image coordinates must be exact JSON numbers")
        image_x, image_y = float(raw_x), float(raw_y)
        if not (math.isfinite(image_x) and math.isfinite(image_y)):
            raise ValueError("image coordinates must be finite")
        return (
            round(
                left
                + image_x * max(0, frame_width - 1)
                / max(1, image_width - 1)
            ),
            round(
                top
                + image_y * max(0, frame_height - 1)
                / max(1, image_height - 1)
            ),
        )

    @staticmethod
    def _map_relative_delta(
        envelope: ActorDecisionEnvelope,
        image_dx: float,
        image_dy: float,
    ) -> PhysicalPoint:
        """Scale a submitted-image delta into virtual-desktop pixel units."""

        submitted_width, submitted_height = envelope.submitted_image_size
        _left, _top, frame_width, frame_height = envelope.frame_region

        def scale(value: float, image_extent: int, frame_extent: int) -> int:
            if not math.isfinite(value):
                raise ValueError("relative pointer delta must be finite")
            mapped = round(
                value * max(1, frame_extent - 1) / max(1, image_extent - 1)
            )
            # Preserve deliberate sub-pixel intent instead of silently
            # turning a non-zero model gesture into a mechanical no-op.
            if mapped == 0 and value != 0:
                return 1 if value > 0 else -1
            return mapped

        delta = (
            scale(image_dx, submitted_width, frame_width),
            scale(image_dy, submitted_height, frame_height),
        )
        if delta == (0, 0):
            raise ValueError("relative pointer move cannot be zero")
        return delta

    def _safe_snapshot(self) -> LiveTargetSnapshot | None:
        try:
            snapshot = self._probe.snapshot(self._binding.hwnd)
        except Exception:
            return None
        return snapshot if type(snapshot) is LiveTargetSnapshot else None

    @staticmethod
    def _frame_state_matches(
        state: FrameState,
        frame: RuntimeFrame,
    ) -> bool:
        return state.commit_identity == (
            frame.frame_id,
            frame.epoch,
            frame.hwnd,
            frame.region,
            frame.scope,
            frame.submitted_size,
            frame.image_sha256,
        )

    @classmethod
    def _runtime_frame(
        cls,
        state: FrameState,
    ) -> RuntimeFrame:
        identity = cls._runtime_frame_identity(state)
        return RuntimeFrame(
            frame_id=identity.frame_id,
            epoch=identity.epoch,
            hwnd=identity.hwnd,
            region=identity.region,
            scope=identity.scope,
            submitted_size=identity.submitted_size,
            image_b64=state.image_b64,
            image_sha256=identity.image_sha256,
            media_type=identity.media_type,
        )

    @classmethod
    def _runtime_frame_identity(
        cls,
        state: FrameState,
    ) -> RuntimeFrameIdentity:
        cls._validate_jpeg(state.image_b64)
        return RuntimeFrameIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
            media_type=_JPEG_MEDIA_TYPE,
        )

    @staticmethod
    def _validate_jpeg(image_b64: str) -> None:
        try:
            raw = base64.b64decode(image_b64, validate=True)
        except Exception as exc:
            raise ValueError("frame payload is not strict base64") from exc
        if (
            len(raw) < 4
            or not raw.startswith(b"\xff\xd8\xff")
            or not raw.endswith(b"\xff\xd9")
        ):
            raise ValueError("frame payload is not an explicit JPEG")

    @staticmethod
    def _raw_action(action: str) -> str:
        return "click" if action == "double_click" else action

    @classmethod
    def _not_started(
        cls,
        action: str,
        error_kind: str,
    ) -> RawActionReceipt:
        return RawActionReceipt(
            action=cls._raw_action(action),
            outcome=MutationOutcome.NOT_STARTED,
            error_kind=error_kind,
        )

    @classmethod
    def _unknown(
        cls,
        action: str,
        error_kind: str,
    ) -> RawActionReceipt:
        return RawActionReceipt(
            action=cls._raw_action(action),
            outcome=MutationOutcome.OUTCOME_UNKNOWN,
            error_kind=error_kind,
        )

    @staticmethod
    def _receipt_error_kind(receipt: RawActionReceipt) -> str | None:
        """Return the machine rejection kind of a not-executed receipt.

        The raw receipt itself may carry the kind (not-started/unknown), or the
        physical backend may have attached it to the result facts.  Reading
        facts here keeps the RawActionReceipt contract (certain outcomes carry
        no error_kind) while still feeding the real mechanical reason to the
        Actor's ``previous_action.error_kind``.
        """
        if receipt.error_kind:
            return receipt.error_kind
        backend_result = receipt.backend_result
        if backend_result is None:
            return None
        facts = backend_result.facts
        if not isinstance(facts, Mapping):
            return None
        kind = facts.get("error_kind")
        if type(kind) is str and kind and len(kind) <= 64:
            return kind
        guard = facts.get("guard_decision")
        if type(guard) is str and guard and len(guard) <= 48:
            return f"final_guard_{guard}"
        return None

    @staticmethod
    def _rejected(
        envelope: ActorDecisionEnvelope,
        action: str,
        *,
        error_kind: str | None = None,
    ) -> ControllerTransition:
        return ControllerTransition(
            outcome=ControllerOutcome.REJECTED,
            action_id=envelope.decision_id,
            action=action,
            input_completed=False,
            error_kind=error_kind,
        )

    @staticmethod
    def _unknown_transition(
        envelope: ActorDecisionEnvelope,
        action: str,
        *,
        input_completed: bool | None,
        post_frame: RuntimeFrame | None = None,
        error_kind: str | None = None,
    ) -> ControllerTransition:
        return ControllerTransition(
            outcome=ControllerOutcome.UNKNOWN,
            action_id=envelope.decision_id,
            action=action,
            input_completed=input_completed,
            post_frame_id=(
                None if post_frame is None else post_frame.frame_id
            ),
            error_kind=error_kind,
        )

    def _receipt_metadata(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> Mapping[str, TraceScalar]:
        screen_first = envelope.frame_scope == "full_desktop"
        binding = None if screen_first else self._binding
        process_name = (
            ""
            if binding is None
            else ntpath.basename(binding.executable_identity)
        )
        return MappingProxyType(
            {
                "phase": phase,
                "controller_action": envelope.proposal.action,
                "decision_id": envelope.decision_id,
                "mission_id": envelope.mission_id,
                "run_id": envelope.run_id,
                "frame_id": envelope.frame_id,
                "frame_epoch": envelope.frame_epoch,
                "target_hwnd": (
                    None if screen_first else envelope.frame_hwnd
                ),
                "target_binding_id": (
                    None
                    if screen_first
                    else envelope.target_binding_id
                ),
                "target_title": (
                    "" if binding is None else binding.title_snapshot
                ),
                "target_process": process_name,
            }
        )

    def _record_trace_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> None:
        sink = self._trace
        if sink is None:
            return
        try:
            sink.record(
                receipt,
                metadata=self._receipt_metadata(envelope, phase=phase),
            )
        except Exception:
            pass

    def _record_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> bool:
        self._record_trace_receipt(
            receipt,
            envelope,
            phase=phase,
        )
        return True


__all__ = [
    "AtomicDesktopAdapter",
    "DesktopTargetProbe",
    "LiveTargetSnapshot",
    "RawReceiptTraceSink",
]
