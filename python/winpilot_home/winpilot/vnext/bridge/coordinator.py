"""Production composition root for the incremental vNext desktop canary.

The coordinator does not understand applications or GUI layouts.  It binds
one client-handoff window, composes the existing typed owners, and advances
one :class:`MissionRuntime` turn at a time.  Unsupported capabilities and all
identity drift fail closed; this module never constructs legacy handlers.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import math
import threading
import time
from typing import Any, Protocol

from ...action.desktop_queue import DESKTOP_ACTION_QUEUE, DesktopActionQueue
from ...agent.frame_authority import FrameAuthority
from ...orchestrator.desktop_runtime import (
    DesktopRuntimeCoordinatorContext,
    DesktopRuntimeCoordinatorResult,
    DesktopRuntimeCoordinatorStatus,
)
from ...orchestrator.runtime_admission import (
    RuntimeCapabilitySnapshot,
    vnext_full_goal_stage,
)
from ...tasking.contracts import TaskContract
from ...tasking.execution_context import (
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)
from ...utils.screenshot import virtual_screen_region
from ...visual_runtime.atomic_click_controller import (
    AdmittedRawDeviceInventoryPort,
    AtomicDesktopController,
    REQUIRED_RAW_ACTION_NAMES,
    SUPPORTED_DESKTOP_TOOL_NAMES,
    TargetActivationObservation,
)
from ...visual_runtime.atomic_post_action_bridge import AtomicPostActionBridge
from ...visual_runtime.commit_admission import TargetCommitIdentity
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.windows_desktop_facts import (
    WindowsDesktopCommitObservation,
    WindowsDesktopFactsError,
    WindowsDesktopFactsReader,
    WindowsPhysicalInputFinalGuard,
)
from ..actor.model import OpenAIVisualActor
from ..capabilities import CapabilityBroker, PointerLeaseState
from ..capabilities.live_catalog import build_live_runtime_snapshot
from ..evidence import EvidenceRegistry
from ..mission import (
    MissionReducer,
    MissionStatus,
    project_legacy_contract,
    target_binding_attestation,
)
from ..profiling import ExecutionPolicy
from ..runtime import (
    ActivatedTargetIdentity,
    MissionRuntime,
    MutationPermitAuthority,
    RuntimeStatus,
    TargetActivationEnvelope,
)
from ..targeting import FreshWindowsTargetDiscovery
from ..verification import (
    DeterministicActionEffectVerifier,
    DeterministicMissionCompletionVerifier,
)
from ..verification.model_provider import OpenAIVisualVerifierModel
from ..verification.visual_model import ReadOnlyVisualVerifierAdapter
from .runtime_state import (
    DesktopObservationIdentity,
    RuntimeReadState,
    RuntimeStateBridge,
)


DEFAULT_MAX_TURNS = 24
DEFAULT_FRAME_MAX_AGE_S = 30.0
_RECOVERABLE_TURN_STATUSES = frozenset({
    RuntimeStatus.ACTION_RECORDED,
    RuntimeStatus.INVALID_PROPOSAL,
    RuntimeStatus.OBSERVED,
    RuntimeStatus.STALE_DISCARDED,
    RuntimeStatus.VERIFICATION_RECORDED,
})


class TraceSidecarPort(Protocol):
    def record_sidecar(self, event: dict[str, Any], *, run_id: str) -> bool: ...


class _ActorProjectionProbe:
    """Record one non-sensitive Actor-input fact for the trace sidecar."""

    def __init__(self, actor: Any) -> None:
        self._actor = actor
        self.previous_action_projected: bool | None = None

    def reset(self) -> None:
        self.previous_action_projected = None

    def propose(self, turn: Any, **kwargs: Any) -> Any:
        self.previous_action_projected = turn.previous_action is not None
        return self._actor.propose(turn, **kwargs)


class LiveDesktopCoordinatorError(RuntimeError):
    """The selected vNext run cannot safely continue."""


class _WindowsTargetActivationObservation:
    """Map one canonical Windows facts owner into the activation port."""

    def __init__(self, facts: WindowsDesktopFactsReader) -> None:
        if not isinstance(facts, WindowsDesktopFactsReader):
            raise TypeError("facts must be WindowsDesktopFactsReader")
        self._facts = facts

    def observe_activation(
        self,
        envelope: TargetActivationEnvelope,
    ) -> TargetActivationObservation:
        if not isinstance(envelope, TargetActivationEnvelope):
            raise TypeError("envelope must be TargetActivationEnvelope")
        candidate_hwnd = envelope.candidate.hwnd
        if candidate_hwnd is None:
            raise LiveDesktopCoordinatorError(
                "existing-window activation requires a candidate hwnd"
            )
        facts = self._facts.read_activation_facts(candidate_hwnd)
        target = facts.target
        return TargetActivationObservation(
            activated_target=ActivatedTargetIdentity(
                hwnd=target.hwnd,
                root_hwnd=target.root_hwnd,
                process_id=target.process_id,
                process_identity=target.process_identity,
                window_region=facts.window_region,
                title_snapshot=facts.title_snapshot,
            ),
            exact_foreground=facts.exact_foreground,
        )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


def _digest(value: Mapping[str, Any]) -> str:
    wire = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _windows_desktop_identity() -> DesktopObservationIdentity:
    """Read the physical desktop surface without acquiring a second owner."""

    import win32gui

    return DesktopObservationIdentity(
        hwnd=int(win32gui.GetDesktopWindow()),
        region=virtual_screen_region(),
    )


def _accepted_turn_events(
    reducer: MissionReducer,
    event_ids: tuple[str, ...],
) -> tuple[Any, ...] | None:
    """Resolve only events that the canonical Reducer actually accepted.

    The sidecar is observational: an inconsistent Runtime result must remain
    visible as unresolved telemetry rather than being completed from nearby
    Mission state or event ordering.
    """

    if not event_ids:
        return ()
    if len(set(event_ids)) != len(event_ids):
        return None
    requested = set(event_ids)
    by_id: dict[str, Any] = {}
    for event in reducer.events:
        if event.event_id in by_id:
            return None
        by_id[event.event_id] = event
    if any(event_id not in by_id for event_id in requested):
        return None
    return tuple(
        event for event in reducer.events if event.event_id in requested
    )


def _accepted_turn_tool_name(
    result: Any,
    accepted_events: tuple[Any, ...] | None,
) -> str | None:
    """Return a tool only when accepted Mission facts prove it."""

    if accepted_events is None:
        return None
    if (
        result.status is RuntimeStatus.ACTION_RECORDED
        and result.proposal_kind == "desktop_action"
    ):
        transitions = tuple(
            event for event in accepted_events if event.kind == "transition_recorded"
        )
        if len(transitions) != 1:
            return None
        payload = transitions[0].payload
        action = payload.get("action")
        if (
            type(action) is str
            and action
            and payload.get("action_id") == result.decision_id
        ):
            return action
        return None
    if (
        result.proposal_kind == "claim_obligation_done"
        and any(event.kind == "obligation_claimed" for event in accepted_events)
    ):
        return "claim_obligation_done"
    if (
        result.proposal_kind == "blocked"
        and any(event.kind == "obligation_blocked" for event in accepted_events)
    ):
        return "blocked"
    return None


class LiveDesktopRuntimeCoordinator:
    """Compose and run one isolated vNext Mission over migrated primitives."""

    def __init__(
        self,
        *,
        api_config: Mapping[str, Any],
        raw_device: Any,
        runtime_snapshot: RuntimeCapabilitySnapshot,
        trace_sidecar: TraceSidecarPort | None = None,
        action_queue: DesktopActionQueue = DESKTOP_ACTION_QUEUE,
        facts_reader: WindowsDesktopFactsReader | None = None,
        observer_factory: Callable[[FrameAuthority], FrameObserver] | None = None,
        actor_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        verifier_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        window_exists_resolver: Callable[[int], bool] | None = None,
        desktop_identity_reader: Callable[[], DesktopObservationIdentity] | None = None,
        target_discovery: Any | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        frame_max_age_s: float = DEFAULT_FRAME_MAX_AGE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(api_config, Mapping):
            raise TypeError("api_config must be a mapping")
        if trace_sidecar is not None and not callable(
            getattr(trace_sidecar, "record_sidecar", None)
        ):
            raise TypeError("trace_sidecar must provide record_sidecar()")
        if not isinstance(action_queue, DesktopActionQueue):
            raise TypeError("action_queue must be DesktopActionQueue")
        if not isinstance(raw_device, AdmittedRawDeviceInventoryPort):
            raise TypeError(
                "raw_device must be supplied by the product composition root"
            )
        if not isinstance(runtime_snapshot, RuntimeCapabilitySnapshot):
            raise TypeError("runtime_snapshot must be RuntimeCapabilitySnapshot")
        if runtime_snapshot.runtime_version != "vnext":
            raise ValueError("live coordinator requires a vNext capability snapshot")
        if runtime_snapshot.controller_tool_names != SUPPORTED_DESKTOP_TOOL_NAMES:
            raise LiveDesktopCoordinatorError(
                "live capability snapshot and Controller tools differ"
            )
        if raw_device.admitted_action_names != REQUIRED_RAW_ACTION_NAMES:
            raise LiveDesktopCoordinatorError(
                "live Controller has no exact raw backend inventory"
            )
        if facts_reader is not None and not isinstance(
            facts_reader, WindowsDesktopFactsReader
        ):
            raise TypeError("facts_reader must be WindowsDesktopFactsReader")
        if type(max_turns) is not int or not 1 <= max_turns <= 1000:
            raise ValueError("max_turns must be an integer from 1 through 1000")
        if (
            type(frame_max_age_s) not in (int, float)
            or not math.isfinite(float(frame_max_age_s))
            or float(frame_max_age_s) <= 0
        ):
            raise ValueError("frame_max_age_s must be a positive number")
        for name, factory in (
            ("observer_factory", observer_factory),
            ("actor_factory", actor_factory),
            ("verifier_factory", verifier_factory),
        ):
            if factory is not None and not callable(factory):
                raise TypeError(f"{name} must be callable or null")
        if window_exists_resolver is not None and not callable(
            window_exists_resolver
        ):
            raise TypeError("window_exists_resolver must be callable or null")
        if desktop_identity_reader is not None and not callable(
            desktop_identity_reader
        ):
            raise TypeError("desktop_identity_reader must be callable or null")
        if target_discovery is not None and not callable(
            getattr(target_discovery, "discover", None)
        ):
            raise TypeError("target_discovery must provide discover()")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._api_config = dict(api_config)
        self._trace_sidecar = trace_sidecar
        self._action_queue = action_queue
        # vNext owns semantic coordination, not physical-device construction.
        # The exact name-only inventory was proven above before any Actor exists;
        # the product composition root still supplies the sole physical owner.
        self._raw_device = raw_device
        self._runtime_snapshot = runtime_snapshot
        self._facts = facts_reader or WindowsDesktopFactsReader()
        self._observer_factory = observer_factory or FrameObserver
        self._actor_factory = actor_factory or OpenAIVisualActor.from_api_config
        self._verifier_factory = verifier_factory or (
            lambda api: ReadOnlyVisualVerifierAdapter(
                OpenAIVisualVerifierModel.from_api_config(api)
            )
        )
        self._window_exists = window_exists_resolver
        self._read_desktop_identity = (
            desktop_identity_reader or _windows_desktop_identity
        )
        self._target_discovery = target_discovery or FreshWindowsTargetDiscovery(
            clock=clock,
        )
        self._max_turns = max_turns
        self._frame_max_age_s = float(frame_max_age_s)
        self._clock = clock

    @property
    def runtime_capability_snapshot(self) -> RuntimeCapabilitySnapshot:
        """The one immutable composition inventory used for this coordinator."""

        return self._runtime_snapshot

    def _turn_budget(self, contract: TaskContract) -> int:
        """Read one canonical full-goal budget and cap it locally."""

        try:
            desktop_stage = vnext_full_goal_stage(contract)
        except (TypeError, ValueError) as exc:
            raise LiveDesktopCoordinatorError(str(exc)) from exc
        raw_stage_budget = desktop_stage.parameters.get("max_steps")
        if type(raw_stage_budget) is not int or not 1 <= raw_stage_budget <= 400:
            raise LiveDesktopCoordinatorError(
                "selected vNext run has no valid canonical turn budget"
            )
        if contract.session is not None:
            session_budget = contract.session.requested_max_steps
            if session_budget != raw_stage_budget:
                raise LiveDesktopCoordinatorError(
                    "selected vNext run has conflicting canonical turn budgets"
                )
        return min(self._max_turns, raw_stage_budget)

    def _trace(self, run_id: str, event: dict[str, Any]) -> None:
        writer = self._trace_sidecar
        if writer is None:
            return
        try:
            writer.record_sidecar(event, run_id=run_id)
        except Exception:
            # Trace is an observer, never a second product-state owner.
            return

    @staticmethod
    def _contract(context: DesktopRuntimeCoordinatorContext) -> TaskContract:
        graph = _plain_json(context.graph)
        metadata = graph.get("metadata") if isinstance(graph, dict) else None
        raw = metadata.get("task_contract") if isinstance(metadata, dict) else None
        if not isinstance(raw, dict):
            raise LiveDesktopCoordinatorError(
                "selected vNext run has no canonical task-contract/1"
            )
        try:
            contract = TaskContract.model_validate(raw)
        except Exception as exc:
            raise LiveDesktopCoordinatorError(
                "selected vNext run has an invalid task-contract/1"
            ) from exc
        if contract.format != "task-contract/1":
            raise LiveDesktopCoordinatorError(
                "selected vNext run has an unsupported task contract"
            )
        return contract

    def _handoff_target(
        self, context: DesktopRuntimeCoordinatorContext
    ) -> tuple[int, dict[str, Any]]:
        inputs = _plain_json(context.inputs)
        execution_context = normalize_execution_context(inputs)
        kwargs: dict[str, Any] = {}
        if self._window_exists is not None:
            kwargs["window_exists_resolver"] = self._window_exists
        hwnd, failure = validated_handoff_target(execution_context, **kwargs)
        if not hwnd:
            failure_type = (
                str(failure.get("failure_type") or "target_binding_missing")
                if isinstance(failure, dict)
                else "target_binding_missing"
            )
            raise LiveDesktopCoordinatorError(
                f"desktop handoff target rejected ({failure_type})"
            )
        conflict = foreground_handoff_conflict(execution_context, hwnd)
        if conflict is not None:
            raise LiveDesktopCoordinatorError(
                "desktop handoff target rejected "
                f"({str(conflict.get('failure_type') or 'control_surface_handoff_failed')})"
            )
        target = execution_context.get("target_window")
        return hwnd, target if isinstance(target, dict) else {}

    def _bind_target(
        self,
        *,
        reducer: MissionReducer,
        evidence: EvidenceRegistry,
        handoff_hwnd: int,
        handoff: Mapping[str, Any],
    ) -> TargetCommitIdentity:
        definition = reducer.definition
        acquisition_action_id = "handoff-" + _digest({
            "format": "vnext-handoff-acquisition/2",
            "mission_id": definition.mission_id,
            "run_id": definition.run_id,
            "handoff_hwnd": handoff_hwnd,
            "handoff_process_identity": handoff.get("process_identity"),
            "captured_at": handoff.get("captured_at"),
        })[:24]
        try:
            physical_target = self._facts.read_target_identity(
                handoff_hwnd,
                binding_id="target-" + "0" * 24,
                epoch=0,
            )
        except WindowsDesktopFactsError as exc:
            raise LiveDesktopCoordinatorError(
                "desktop handoff target identity is unavailable"
            ) from exc
        handoff_process_id = int(handoff.get("process_id") or 0)
        handoff_root_hwnd = int(handoff.get("root_hwnd") or 0)
        handoff_process_identity = str(handoff.get("process_identity") or "")
        if handoff.get("foreground_verified") is not True:
            raise LiveDesktopCoordinatorError(
                "desktop handoff target was not foreground-verified"
            )
        if (
            handoff_process_id != physical_target.process_id
            or handoff_root_hwnd != physical_target.root_hwnd
            or handoff_process_identity != physical_target.process_identity
        ):
            raise LiveDesktopCoordinatorError(
                "desktop handoff process identity changed"
            )
        process_identity = physical_target.process_identity
        evidence_id = "evidence-target-" + _digest({
            "format": "vnext-target-binding-evidence-id/1",
            "mission_id": definition.mission_id,
            "run_id": definition.run_id,
            "acquisition_action_id": acquisition_action_id,
            "hwnd": physical_target.hwnd,
            "root_hwnd": physical_target.root_hwnd,
            "process_id": physical_target.process_id,
            "process_identity": process_identity,
        })[:24]
        obligation_id = reducer.projection.active_obligation_id
        if obligation_id is None:
            raise LiveDesktopCoordinatorError(
                "desktop handoff has no active mission obligation"
            )
        binding, ref = target_binding_attestation(
            mission_id=definition.mission_id,
            run_id=definition.run_id,
            obligation_id=obligation_id,
            evidence_id=evidence_id,
            acquisition_action_id=acquisition_action_id,
            hwnd=physical_target.hwnd,
            root_hwnd=physical_target.root_hwnd,
            process_id=physical_target.process_id,
            process_identity=process_identity,
            title_snapshot=str(handoff.get("title") or ""),
            epoch=physical_target.epoch,
            created_at=float(self._clock()),
        )
        target = TargetCommitIdentity(
            binding_id=binding.binding_id,
            hwnd=physical_target.hwnd,
            root_hwnd=physical_target.root_hwnd,
            process_id=physical_target.process_id,
            process_identity=process_identity,
            epoch=physical_target.epoch,
            window_region=physical_target.window_region,
        )
        evidence.append(ref)
        reducer.apply(reducer.event(
            "target_bound",
            payload={"target_binding": binding.to_dict()},
            evidence_refs=(ref.id,),
            created_at=ref.created_at,
        ))
        return target

    def _runtime(
        self,
        context: DesktopRuntimeCoordinatorContext,
        reducer: MissionReducer,
        evidence: EvidenceRegistry,
    ) -> tuple[MissionRuntime, tuple[str, ...], _ActorProjectionProbe]:
        frames = FrameAuthority()
        observer = self._observer_factory(frames)
        if not isinstance(observer, FrameObserver) or observer.authority is not frames:
            raise TypeError("observer_factory must share the supplied FrameAuthority")
        permits = MutationPermitAuthority(
            reducer.definition.mission_id,
            reducer.definition.run_id,
        )

        def current_target(
            hwnd: int,
            binding_id: str,
            epoch: int,
        ) -> TargetCommitIdentity | None:
            return self._facts.read_target_identity(
                hwnd,
                binding_id=binding_id,
                epoch=epoch,
            )

        def read_runtime_state() -> RuntimeReadState:
            stopped = context.stop_event.is_set()
            return RuntimeReadState(
                available_capabilities=(
                    self._runtime_snapshot.available_capabilities
                ),
                pointer=PointerLeaseState(),
                runtime_healthy=not stopped,
                paused=bool(context.is_paused()),
                canceled=stopped,
                stop_requested=stopped,
            )

        state = RuntimeStateBridge(
            frame_authority=frames,
            observer=observer,
            evidence=evidence,
            mutation_permits=permits,
            read_target_identity=current_target,
            read_desktop_identity=self._read_desktop_identity,
            read_runtime_state=read_runtime_state,
            frame_max_age_s=self._frame_max_age_s,
            clock=self._clock,
        )
        facts_observation = WindowsDesktopCommitObservation(self._facts, state)
        activation_observation = _WindowsTargetActivationObservation(
            self._facts
        )
        post_action = AtomicPostActionBridge(
            frame_authority=frames,
            observer=observer,
            evidence=evidence,
            activation_observation=activation_observation,
        )
        controller = AtomicDesktopController(
            action_queue=self._action_queue,
            frame_authority=frames,
            permits=permits,
            observation=facts_observation,
            final_guard=WindowsPhysicalInputFinalGuard(self._facts),
            raw_device=self._raw_device,
            post_action=post_action,
            activation_observation=activation_observation,
            activation_post_action=post_action,
            enabled=True,
        )
        catalog = self._runtime_snapshot.catalog
        provider_availability = (
            ("obligation_verifier",)
            if "obligation_verifier" in self._runtime_snapshot.available_services
            else ()
        )
        profile = ExecutionPolicy(
            provider_availability=provider_availability,
        ).safe_default(
            reducer.definition.mission_id,
            reducer.definition.goal_revision,
            reason="live_runtime_profiler_not_connected",
            available_capabilities=(
                self._runtime_snapshot.available_capabilities
            ),
            provider_availability=provider_availability,
        )
        actor_probe = _ActorProjectionProbe(self._actor_factory(self._api_config))
        runtime = MissionRuntime(
            reducer=reducer,
            profile=profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=actor_probe,
            state=state,
            controller=controller,
            target_discovery=self._target_discovery,
            mutation_permits=permits,
            attested_visual_verifier=self._verifier_factory(self._api_config),
            mission_completion_verifier=DeterministicMissionCompletionVerifier(),
            action_effect_verifier=DeterministicActionEffectVerifier(),
            evidence_registry=evidence,
        )
        return runtime, tuple(tool.name for tool in catalog.tools), actor_probe

    @staticmethod
    def _failed(status: str) -> DesktopRuntimeCoordinatorResult:
        return DesktopRuntimeCoordinatorResult(
            DesktopRuntimeCoordinatorStatus.FAILED,
            error=f"vNext desktop Runtime did not complete ({status})",
        )

    def run(
        self, context: DesktopRuntimeCoordinatorContext
    ) -> DesktopRuntimeCoordinatorResult:
        if not isinstance(context, DesktopRuntimeCoordinatorContext):
            raise TypeError("context must be DesktopRuntimeCoordinatorContext")
        evidence: EvidenceRegistry | None = None
        runtime: MissionRuntime | None = None
        try:
            if context.stop_event.is_set() or not context.wait_if_paused():
                return DesktopRuntimeCoordinatorResult(
                    DesktopRuntimeCoordinatorStatus.CANCELED,
                    error="vNext desktop Runtime was canceled before admission",
                )
            contract = self._contract(context)
            turn_budget = self._turn_budget(contract)
            definition = project_legacy_contract(
                contract,
                mission_id=f"mission-{context.run_id}",
                run_id=context.run_id,
            )
            evidence = EvidenceRegistry(definition.mission_id, definition.run_id)
            reducer = MissionReducer(definition, evidence=evidence)
            hwnd, handoff = self._handoff_target(context)
            self._bind_target(
                reducer=reducer,
                evidence=evidence,
                handoff_hwnd=hwnd,
                handoff=handoff,
            )
            runtime, tool_names, actor_probe = self._runtime(
                context, reducer, evidence,
            )
            self._trace(context.run_id, {
                "type": "vnext_mission_runtime_started",
                "runtime_version": "vnext",
                "mission_id": definition.mission_id,
                "mission_revision": reducer.projection.revision,
                "active_obligation_id": reducer.projection.active_obligation_id,
                "tool_names": list(tool_names),
                "runtime_capability_snapshot_fingerprint": (
                    self._runtime_snapshot.fingerprint
                ),
                "tool_catalog_fingerprint": (
                    self._runtime_snapshot.catalog.fingerprint
                ),
                "profile_source": "safe_default",
                "turn_budget": turn_budget,
            })

            for turn_index in range(turn_budget):
                if context.stop_event.is_set() or not context.wait_if_paused():
                    return DesktopRuntimeCoordinatorResult(
                        DesktopRuntimeCoordinatorStatus.CANCELED,
                        error="vNext desktop Runtime was canceled",
                    )
                actor_probe.reset()
                result = runtime.run_one_turn(
                    turn_index=turn_index,
                    stop_event=context.stop_event,
                )
                projection = reducer.projection
                transition = projection.last_transition
                effect = (
                    transition.action_effect
                    if result.status is RuntimeStatus.ACTION_RECORDED
                    and transition is not None
                    and transition.obligation_id == projection.active_obligation_id
                    and transition.action_id == result.decision_id
                    else None
                )
                active_state = (
                    projection.obligation_state(projection.active_obligation_id)
                    if projection.active_obligation_id is not None
                    else None
                )
                accepted_turn_events = _accepted_turn_events(
                    reducer, result.accepted_event_ids,
                )
                self._trace(context.run_id, {
                    "type": "vnext_mission_runtime_turn",
                    "runtime_version": "vnext",
                    "turn_index": turn_index,
                    "step": turn_index + 1,
                    "model_role": (
                        "visual_actor"
                        if actor_probe.previous_action_projected is not None
                        else None
                    ),
                    "previous_action_projected": (
                        actor_probe.previous_action_projected
                    ),
                    "status": result.status.value,
                    "route": None if result.route is None else result.route.value,
                    "proposal_kind": result.proposal_kind,
                    "accepted_tool_name": _accepted_turn_tool_name(
                        result, accepted_turn_events,
                    ),
                    "decision_id": result.decision_id,
                    "accepted_event_ids": list(result.accepted_event_ids),
                    "accepted_events": (
                        [
                            {"event_id": event.event_id, "kind": event.kind}
                            for event in accepted_turn_events
                        ]
                        if accepted_turn_events is not None
                        else None
                    ),
                    "mission_revision": projection.revision,
                    "mission_status": projection.status.value,
                    "active_obligation_id": projection.active_obligation_id,
                    "local_visual_change": (
                        effect.local_visual_change.value
                        if effect is not None
                        else None
                    ),
                    "frame_comparable": (
                        effect.frame_comparable if effect is not None else None
                    ),
                    "no_progress_streak": (
                        active_state.no_progress_streak
                        if effect is not None and active_state is not None
                        else None
                    ),
                    "detail": result.detail[:512],
                })
                if (
                    result.status is RuntimeStatus.MISSION_VERIFIED
                    and projection.status is MissionStatus.VERIFIED
                ):
                    output = {
                        "runtime_version": "vnext",
                        "mission_id": definition.mission_id,
                        "mission_status": projection.status.value,
                        "mission_revision": projection.revision,
                        "accepted_event_ids": list(projection.accepted_event_ids),
                        "turn_count": turn_index + 1,
                        "tool_names": list(tool_names),
                    }
                    self._trace(context.run_id, {
                        "type": "vnext_mission_runtime_finished",
                        **output,
                    })
                    return DesktopRuntimeCoordinatorResult(
                        DesktopRuntimeCoordinatorStatus.DONE,
                        output=output,
                    )
                if result.status is RuntimeStatus.CANCELED:
                    return DesktopRuntimeCoordinatorResult(
                        DesktopRuntimeCoordinatorStatus.CANCELED,
                        error="vNext desktop Runtime was canceled",
                    )
                if projection.status in {
                    MissionStatus.BLOCKED,
                    MissionStatus.CANCELED,
                    MissionStatus.FAILED,
                }:
                    return self._failed(projection.status.value)
                if result.status not in _RECOVERABLE_TURN_STATUSES:
                    return self._failed(result.status.value)
            return self._failed("turn_budget_exhausted")
        except LiveDesktopCoordinatorError as exc:
            self._trace(context.run_id, {
                "type": "vnext_mission_runtime_rejected",
                "runtime_version": "vnext",
                "error_kind": type(exc).__name__,
                "reason": str(exc)[:512],
            })
            return self._failed("runtime_contract_rejected")
        except Exception as exc:
            self._trace(context.run_id, {
                "type": "vnext_mission_runtime_failed",
                "runtime_version": "vnext",
                "error_kind": type(exc).__name__,
            })
            return self._failed(type(exc).__name__)
        finally:
            if runtime is not None:
                runtime.discard_ephemeral_state()
                runtime.mutation_permits.close()
            if evidence is not None:
                evidence.close()


def build_live_desktop_runtime_coordinator(
    *,
    api_config: Mapping[str, Any],
    raw_device: Any,
    trace_sidecar: TraceSidecarPort | None = None,
) -> LiveDesktopRuntimeCoordinator:
    """Build the selected product coordinator without starting a run."""

    runtime_snapshot = build_live_runtime_snapshot()
    return LiveDesktopRuntimeCoordinator(
        api_config=api_config,
        raw_device=raw_device,
        runtime_snapshot=runtime_snapshot,
        trace_sidecar=trace_sidecar,
    )


__all__ = [
    "DEFAULT_FRAME_MAX_AGE_S", "DEFAULT_MAX_TURNS",
    "LiveDesktopCoordinatorError",
    "LiveDesktopRuntimeCoordinator",
    "TraceSidecarPort",
    "build_live_desktop_runtime_coordinator",
]
