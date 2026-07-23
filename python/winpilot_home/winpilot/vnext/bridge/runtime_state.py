"""Mechanical bridge from canonical desktop owners to vNext Runtime ports.

The bridge owns no screen, target, pointer, permit, or Mission state.  It
projects injected read-only owners and refuses to repair or replace any
identity which has drifted.  ``observe`` is the only method which captures or
publishes a frame; ``current`` is a zero-capture view of the existing
``FrameAuthority`` state.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import math
import threading
import time

from ...agent.frame_authority import FrameAuthority, FrameState
from ...visual_runtime.commit_admission import (
    CommitAdmissionRequest,
    FrameCommitIdentity,
    TargetCommitIdentity,
)
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.windows_desktop_facts import RuntimeCommitState
from ..actor import ImageRegion
from ..capabilities import (
    ArtifactState,
    AuthoritativeFrameState,
    BrokerRuntimeState,
    ExecutorOffer,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
)
from ..evidence.frame_projection import (
    CURRENT_FRAME_EVIDENCE_FORMAT,
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from ..evidence.registry import EvidenceRegistry
from ..mission import EvidenceRef, MissionProjection, TargetBinding
from ..runtime.contracts import RuntimeFrame, RuntimeSnapshot
from ..runtime.mutation_permits import MutationPermitAuthority


class RuntimeStateBridgeError(RuntimeError):
    """A canonical identity or read-only runtime fact could not be proved."""


@dataclass(frozen=True, slots=True)
class RuntimeReadState:
    """One injected, immutable reading of non-frame Runtime authorities.

    The producer remains responsible for the live pointer, adapter, MCP,
    authorization, pause, cancellation, and stop owners.  This value merely
    prevents the bridge from inventing defaults or keeping shadow booleans.
    """

    available_capabilities: tuple[str, ...]
    pointer: PointerLeaseState
    runtime_healthy: bool
    paused: bool
    canceled: bool
    stop_requested: bool
    artifacts: tuple[ArtifactState, ...] = ()
    executor_offers: tuple[ExecutorOffer, ...] = ()
    authorization_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.pointer, PointerLeaseState):
            raise TypeError("pointer must be PointerLeaseState")
        for name in ("runtime_healthy", "paused", "canceled", "stop_requested"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be boolean")

        # BrokerRuntimeState is the canonical validator for all Broker-facing
        # arrays.  Reuse it instead of maintaining a second schema here.
        try:
            validated = BrokerRuntimeState(
                turn_index=0,
                available_capabilities=self.available_capabilities,
                pointer=self.pointer,
                artifacts=self.artifacts,
                executor_offers=self.executor_offers,
                authorization_scopes=self.authorization_scopes,
                runtime_healthy=self.runtime_healthy,
                paused=self.paused,
                canceled=self.canceled,
            )
        except Exception as exc:
            raise TypeError(f"invalid read-only Runtime state: {exc}") from exc
        object.__setattr__(
            self, "available_capabilities", validated.available_capabilities,
        )
        object.__setattr__(self, "artifacts", validated.artifacts)
        object.__setattr__(self, "executor_offers", validated.executor_offers)
        object.__setattr__(
            self, "authorization_scopes", validated.authorization_scopes,
        )

    @property
    def pointer_released(self) -> bool:
        return bool(
            self.pointer.disposition is PointerDisposition.RELEASED
            and self.pointer.physically_owned is False
        )


class RuntimeStateBridge:
    """Implement RuntimeStatePort and RuntimeCommitStatePort without ownership.

    ``target`` is the immutable target accepted for this run.  The injected
    ``read_target_identity`` must re-read the current exact HWND/root/PID/
    epoch/geometry identity; a mismatch rejects the operation and never
    replaces ``target``.
    """

    def __init__(
        self,
        *,
        frame_authority: FrameAuthority,
        observer: FrameObserver,
        evidence: EvidenceRegistry,
        target: TargetCommitIdentity,
        mutation_permits: MutationPermitAuthority,
        read_target_identity: Callable[[], TargetCommitIdentity | None],
        read_runtime_state: Callable[[], RuntimeReadState],
        frame_max_age_s: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(frame_authority, FrameAuthority):
            raise TypeError("frame_authority must be FrameAuthority")
        if not isinstance(observer, FrameObserver):
            raise TypeError("observer must be FrameObserver")
        if observer.authority is not frame_authority:
            raise TypeError("observer and bridge must share one FrameAuthority")
        if not isinstance(evidence, EvidenceRegistry):
            raise TypeError("evidence must be EvidenceRegistry")
        if not isinstance(target, TargetCommitIdentity):
            raise TypeError("target must be TargetCommitIdentity")
        if not isinstance(mutation_permits, MutationPermitAuthority):
            raise TypeError("mutation_permits must be MutationPermitAuthority")
        if (
            mutation_permits.mission_id != evidence.mission_id
            or mutation_permits.run_id != evidence.run_id
        ):
            raise TypeError(
                "permit authority and evidence registry must share mission/run"
            )
        if not callable(read_target_identity):
            raise TypeError("read_target_identity must be callable")
        if not callable(read_runtime_state):
            raise TypeError("read_runtime_state must be callable")
        if (
            type(frame_max_age_s) not in (int, float)
            or not math.isfinite(float(frame_max_age_s))
            or float(frame_max_age_s) <= 0
        ):
            raise TypeError("frame_max_age_s must be a positive finite number")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._frames = frame_authority
        self._observer = observer
        self._evidence = evidence
        self._target = target
        self._permits = mutation_permits
        self._read_target_identity = read_target_identity
        self._read_runtime_state = read_runtime_state
        self._frame_max_age_s = float(frame_max_age_s)
        self._clock = clock
        self._observe_lock = threading.RLock()

    @staticmethod
    def _frame_identity(state: FrameState) -> FrameCommitIdentity:
        return FrameCommitIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
        )

    def _runtime_state(self) -> RuntimeReadState:
        state = self._read_runtime_state()
        if not isinstance(state, RuntimeReadState):
            raise RuntimeStateBridgeError(
                "read_runtime_state returned an invalid value"
            )
        return state

    def _validate_projection(self, projection: MissionProjection) -> TargetBinding:
        if not isinstance(projection, MissionProjection):
            raise TypeError("projection must be MissionProjection")
        if (
            projection.mission_id != self._evidence.mission_id
            or projection.run_id != self._evidence.run_id
        ):
            raise RuntimeStateBridgeError(
                "Mission projection is outside bridge mission/run scope"
            )
        binding = projection.target_binding
        if binding is None:
            raise RuntimeStateBridgeError("accepted Mission target is unavailable")
        target = self._target
        if (
            binding.binding_id != target.binding_id
            or binding.hwnd != target.hwnd
            or binding.root_hwnd != target.root_hwnd
            or binding.process_id != target.process_id
            or binding.process_identity != target.process_identity
            or binding.epoch != target.epoch
        ):
            raise RuntimeStateBridgeError(
                "accepted Mission target does not match the frozen Runtime target"
            )
        return binding

    def _current_target(self) -> TargetCommitIdentity:
        try:
            current = self._read_target_identity()
        except Exception as exc:
            raise RuntimeStateBridgeError(
                "current target identity is unavailable"
            ) from exc
        if not isinstance(current, TargetCommitIdentity):
            raise RuntimeStateBridgeError("current target identity is unavailable")
        if current != self._target:
            raise RuntimeStateBridgeError(
                "target HWND, root, process identity, epoch, or geometry changed"
            )
        return self._target

    def _validate_frame(self, state: FrameState, target: TargetCommitIdentity) -> None:
        if state.hwnd != target.hwnd or state.epoch != target.epoch:
            raise RuntimeStateBridgeError(
                "authoritative frame does not belong to the frozen target epoch"
            )
        if state.scope == "target_window" and state.region != target.window_region:
            raise RuntimeStateBridgeError(
                "authoritative target-window frame geometry changed"
            )

    @staticmethod
    def _active_obligation(projection: MissionProjection) -> str | None:
        value = projection.active_obligation_id
        return value if value else None

    def _evidence_id(
        self,
        projection: MissionProjection,
        frame: FrameCommitIdentity,
        payload_hash: str,
    ) -> str:
        material = {
            "format": "current-frame-evidence-identity/0",
            "mission_id": projection.mission_id,
            "run_id": projection.run_id,
            "obligation_id": self._active_obligation(projection),
            "frame": frame.to_dict(),
            "payload_hash": payload_hash,
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return f"evidence-current-frame-{digest}"

    def _register_current_frame(
        self,
        projection: MissionProjection,
        state: FrameState,
    ) -> EvidenceRef:
        identity = self._frame_identity(state)
        payload_hash = current_frame_payload_hash(identity)
        ref = EvidenceRef(
            id=self._evidence_id(projection, identity, payload_hash),
            mission_id=projection.mission_id,
            run_id=projection.run_id,
            obligation_id=self._active_obligation(projection),
            kind=CURRENT_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            payload_hash=payload_hash,
            frame_id=state.frame_id,
            created_at=state.committed_at,
        )
        return self._evidence.append(ref)

    def _frame_evidence_refs(
        self,
        projection: MissionProjection,
        frame: FrameCommitIdentity,
    ) -> tuple[str, ...]:
        obligation_id = self._active_obligation(projection)
        current_hash = current_frame_payload_hash(frame)
        post_action_hash = frame_identity_payload_hash(frame)
        refs = []
        for ref in self._evidence.snapshot():
            if (
                ref.trust != "deterministic"
                or ref.frame_id != frame.frame_id
                or ref.obligation_id != obligation_id
            ):
                continue
            if (
                ref.kind == CURRENT_FRAME_EVIDENCE_KIND
                and ref.payload_hash == current_hash
            ):
                refs.append(ref.id)
            elif (
                ref.kind == POST_ACTION_FRAME_EVIDENCE_KIND
                and ref.payload_hash == post_action_hash
                and bool(ref.action_id)
            ):
                refs.append(ref.id)
        return tuple(sorted(refs))

    def _snapshot(
        self,
        projection: MissionProjection,
        *,
        turn_index: int,
        frame_state: FrameState | None,
    ) -> RuntimeSnapshot:
        if type(turn_index) is not int or turn_index < 0:
            raise RuntimeStateBridgeError(
                "turn_index must be a non-negative integer"
            )
        self._validate_projection(projection)
        target = self._current_target()
        read_state = self._runtime_state()
        runtime_frame = None
        if frame_state is not None:
            self._validate_frame(frame_state, target)
            identity = self._frame_identity(frame_state)
            runtime_frame = RuntimeFrame(
                frame_id=frame_state.frame_id,
                epoch=frame_state.epoch,
                hwnd=frame_state.hwnd,
                region=frame_state.region,
                scope=frame_state.scope,
                submitted_size=frame_state.submitted_size,
                image_b64=frame_state.image_b64,
                image_sha256=frame_state.image_sha256,
                evidence_refs=self._frame_evidence_refs(projection, identity),
            )
        return RuntimeSnapshot(
            broker_state=BrokerRuntimeState(
                turn_index=turn_index,
                available_capabilities=read_state.available_capabilities,
                target=TargetState(
                    binding_id=target.binding_id,
                    hwnd=target.hwnd,
                    epoch=target.epoch,
                    live=True,
                ),
                frame=(
                    runtime_frame.broker_frame(fresh=frame_state.is_fresh_for(
                        epoch=target.epoch,
                        max_age_s=self._frame_max_age_s,
                        now=self._clock(),
                    ))
                    if runtime_frame is not None
                    else AuthoritativeFrameState()
                ),
                pointer=read_state.pointer,
                artifacts=read_state.artifacts,
                executor_offers=read_state.executor_offers,
                authorization_scopes=read_state.authorization_scopes,
                runtime_healthy=read_state.runtime_healthy,
                paused=read_state.paused,
                canceled=read_state.canceled,
            ),
            frame=runtime_frame,
            target=target,
        )

    def observe(
        self,
        projection: MissionProjection,
        *,
        turn_index: int,
        image_region: ImageRegion | None = None,
    ) -> RuntimeSnapshot:
        if image_region is not None:
            raise RuntimeStateBridgeError(
                "image_region observation is not implemented by this bridge"
            )
        with self._observe_lock:
            self._validate_projection(projection)
            target = self._current_target()
            previous = self._frames.current
            previous_id = previous.frame_id if previous is not None else 0
            captured = self._observer.capture(
                epoch=target.epoch,
                hwnd=target.hwnd,
                phase="runtime_observe",
                action="observe",
                expected_previous_frame_id=previous_id,
            )
            if captured is None:
                raise RuntimeStateBridgeError("authoritative frame capture failed")
            if captured.hwnd != target.hwnd or captured.epoch != target.epoch:
                raise RuntimeStateBridgeError(
                    "captured frame does not belong to the frozen target epoch"
                )
            capture_region = (
                captured.region.left,
                captured.region.top,
                captured.region.width,
                captured.region.height,
            )
            if captured.scope == "target_window" and capture_region != target.window_region:
                raise RuntimeStateBridgeError("captured target geometry changed")
            self._current_target()
            try:
                committed = self._observer.commit(captured)
            except Exception as exc:
                raise RuntimeStateBridgeError(
                    "authoritative frame changed before CAS commit"
                ) from exc
            self._current_target()
            self._register_current_frame(projection, committed)
            return self._snapshot(
                projection,
                turn_index=turn_index,
                frame_state=committed,
            )

    def current(
        self,
        projection: MissionProjection,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot:
        with self._observe_lock:
            return self._snapshot(
                projection,
                turn_index=turn_index,
                frame_state=self._frames.current,
            )

    def _active_permit_matches(
        self,
        request: CommitAdmissionRequest,
        pointer: PointerLeaseState,
    ) -> bool:
        """Read transient commit authority from its sole public owner."""

        return self._permits.active_admission_matches(
            request.request_id,
            frame=request.frame,
            target=request.target,
            pointer=pointer,
        )

    def read_commit_state(
        self,
        request: CommitAdmissionRequest,
    ) -> RuntimeCommitState:
        if not isinstance(request, CommitAdmissionRequest):
            raise TypeError("request must be CommitAdmissionRequest")
        state = self._runtime_state()
        try:
            target_current = (
                request.target == self._target
                and self._current_target() == self._target
            )
        except RuntimeStateBridgeError:
            target_current = False
        return RuntimeCommitState(
            pointer_released=state.pointer_released,
            mutation_allowed=bool(
                target_current
                and self._active_permit_matches(request, state.pointer)
            ),
            stop_requested=state.stop_requested,
            canceled=state.canceled,
        )

__all__ = [
    "CURRENT_FRAME_EVIDENCE_FORMAT",
    "CURRENT_FRAME_EVIDENCE_KIND",
    "RuntimeReadState",
    "RuntimeStateBridge",
    "RuntimeStateBridgeError",
    "current_frame_payload_hash",
]
