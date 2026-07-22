"""Production composition root for the click-only vNext desktop canary.

The coordinator does not understand applications or GUI layouts.  It binds
one client-handoff window, composes the existing typed owners, and advances
one :class:`MissionRuntime` turn at a time.  Unsupported capabilities and all
identity drift fail closed; this module never constructs legacy handlers.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import threading
import time
from typing import Any, Protocol

from ...action.desktop_queue import DESKTOP_ACTION_QUEUE, DesktopActionQueue
from ...action.executor import ADMITTED_PHYSICAL_CLICK_BACKEND
from ...action.raw_device import RawDeviceBackends, RawDeviceExecutor
from ...agent.frame_authority import FrameAuthority
from ...config import CONFIG
from ...orchestrator.desktop_runtime import (
    DesktopRuntimeCoordinatorContext,
    DesktopRuntimeCoordinatorResult,
    DesktopRuntimeCoordinatorStatus,
)
from ...tasking.contracts import TaskContract
from ...tasking.execution_context import (
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)
from ...visual_runtime.atomic_click_controller import AtomicClickController
from ...visual_runtime.atomic_post_action_bridge import AtomicPostActionBridge
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.windows_desktop_facts import (
    WindowsDesktopCommitObservation,
    WindowsDesktopFactsReader,
    WindowsPhysicalClickFinalGuard,
)
from ..actor.model import OpenAIVisualActor
from ..capabilities import CapabilityBroker, PointerLeaseState
from ..capabilities.live_catalog import build_click_live_catalog
from ..evidence import EvidenceRegistry
from ..mission import (
    EvidenceRef,
    MissionReducer,
    MissionStatus,
    TargetBinding,
    project_legacy_contract,
)
from ..profiling import ExecutionPolicy
from ..runtime import MissionRuntime, MutationPermitAuthority, RuntimeStatus
from ..verification import DeterministicMissionCompletionVerifier
from ..verification.model_provider import OpenAIVisualVerifierModel
from ..verification.visual_model import ReadOnlyVisualVerifierAdapter
from .runtime_state import RuntimeReadState, RuntimeStateBridge


DEFAULT_MAX_TURNS = 24
_LIVE_CAPABILITIES = ("visual.interact",)
_RECOVERABLE_TURN_STATUSES = frozenset({
    RuntimeStatus.ACTION_RECORDED,
    RuntimeStatus.INVALID_PROPOSAL,
    RuntimeStatus.OBSERVED,
    RuntimeStatus.STALE_DISCARDED,
    RuntimeStatus.VERIFICATION_RECORDED,
})


class TraceSidecarPort(Protocol):
    def record_sidecar(self, event: dict[str, Any], *, run_id: str) -> bool: ...


class LiveDesktopCoordinatorError(RuntimeError):
    """The selected vNext run cannot safely continue."""


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


class LiveDesktopRuntimeCoordinator:
    """Compose and run one isolated, click-only vNext Mission."""

    def __init__(
        self,
        *,
        api_config: Mapping[str, Any],
        trace_sidecar: TraceSidecarPort | None = None,
        action_queue: DesktopActionQueue = DESKTOP_ACTION_QUEUE,
        raw_device: RawDeviceExecutor | None = None,
        facts_reader: WindowsDesktopFactsReader | None = None,
        observer_factory: Callable[[FrameAuthority], FrameObserver] | None = None,
        actor_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        verifier_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        window_exists_resolver: Callable[[int], bool] | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
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
        if raw_device is not None and not isinstance(raw_device, RawDeviceExecutor):
            raise TypeError("raw_device must be RawDeviceExecutor")
        if facts_reader is not None and not isinstance(
            facts_reader, WindowsDesktopFactsReader
        ):
            raise TypeError("facts_reader must be WindowsDesktopFactsReader")
        if type(max_turns) is not int or not 1 <= max_turns <= 1000:
            raise ValueError("max_turns must be an integer from 1 through 1000")
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
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._api_config = dict(api_config)
        self._trace_sidecar = trace_sidecar
        self._action_queue = action_queue
        self._raw_device = raw_device or RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=ADMITTED_PHYSICAL_CLICK_BACKEND,
        ))
        self._facts = facts_reader or WindowsDesktopFactsReader()
        self._observer_factory = observer_factory or FrameObserver
        self._actor_factory = actor_factory or OpenAIVisualActor.from_api_config
        self._verifier_factory = verifier_factory or (
            lambda api: ReadOnlyVisualVerifierAdapter(
                OpenAIVisualVerifierModel.from_api_config(api)
            )
        )
        self._window_exists = window_exists_resolver
        self._max_turns = max_turns
        self._clock = clock

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
    ) -> Any:
        definition = reducer.definition
        binding_material = {
            "format": "vnext-target-binding-identity/0",
            "mission_id": definition.mission_id,
            "run_id": definition.run_id,
            "handoff_hwnd": handoff_hwnd,
        }
        binding_id = "target-" + _digest(binding_material)[:24]
        target = self._facts.read_target_identity(
            handoff_hwnd,
            binding_id=binding_id,
            epoch=0,
        )
        handoff_process_id = int(handoff.get("process_id") or 0)
        handoff_rect = tuple(handoff.get("rect") or ())
        if handoff.get("foreground_verified") is not True:
            raise LiveDesktopCoordinatorError(
                "desktop handoff target was not foreground-verified"
            )
        if handoff_process_id != target.process_id:
            raise LiveDesktopCoordinatorError(
                "desktop handoff process identity changed"
            )
        if handoff_rect != target.window_region:
            raise LiveDesktopCoordinatorError(
                "desktop handoff target geometry changed"
            )
        binding_core = {
            "binding_id": binding_id,
            "hwnd": target.hwnd,
            "process_id": target.process_id,
            "executable_identity": f"pid:{target.process_id}",
            "title_snapshot": str(handoff.get("title") or ""),
            "epoch": target.epoch,
        }
        evidence_hash = _digest({
            "format": "vnext-target-binding-evidence/0",
            "mission_id": definition.mission_id,
            "run_id": definition.run_id,
            "target": target.to_dict(),
            "binding": binding_core,
        })
        evidence_id = "evidence-target-" + evidence_hash[:24]
        binding = TargetBinding(
            **binding_core,
            established_by_evidence_ref=evidence_id,
        )
        ref = EvidenceRef(
            id=evidence_id,
            mission_id=definition.mission_id,
            run_id=definition.run_id,
            kind="authoritative_target_binding",
            trust="deterministic",
            payload_hash=evidence_hash,
            created_at=float(self._clock()),
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
        target: Any,
    ) -> tuple[MissionRuntime, tuple[str, ...]]:
        frames = FrameAuthority()
        observer = self._observer_factory(frames)
        if not isinstance(observer, FrameObserver) or observer.authority is not frames:
            raise TypeError("observer_factory must share the supplied FrameAuthority")
        permits = MutationPermitAuthority(
            reducer.definition.mission_id,
            reducer.definition.run_id,
        )

        def current_target():
            return self._facts.read_target_identity(
                target.hwnd,
                binding_id=target.binding_id,
                epoch=target.epoch,
            )

        def read_runtime_state() -> RuntimeReadState:
            stopped = context.stop_event.is_set()
            return RuntimeReadState(
                available_capabilities=_LIVE_CAPABILITIES,
                pointer=PointerLeaseState(),
                runtime_healthy=not stopped,
                paused=False,
                canceled=stopped,
                stop_requested=stopped,
            )

        state = RuntimeStateBridge(
            frame_authority=frames,
            observer=observer,
            evidence=evidence,
            target=target,
            mutation_permits=permits,
            read_target_identity=current_target,
            read_runtime_state=read_runtime_state,
        )
        facts_observation = WindowsDesktopCommitObservation(self._facts, state)
        controller = AtomicClickController(
            action_queue=self._action_queue,
            frame_authority=frames,
            permits=permits,
            observation=facts_observation,
            final_guard=WindowsPhysicalClickFinalGuard(self._facts),
            raw_device=self._raw_device,
            post_action=AtomicPostActionBridge(
                frame_authority=frames,
                observer=observer,
                evidence=evidence,
            ),
            enabled=True,
        )
        catalog = build_click_live_catalog()
        profile = ExecutionPolicy(
            provider_availability=("obligation_verifier",),
        ).safe_default(
            reducer.definition.mission_id,
            reducer.definition.goal_revision,
            reason="live_runtime_profiler_not_connected",
            available_capabilities=_LIVE_CAPABILITIES,
            provider_availability=("obligation_verifier",),
        )
        runtime = MissionRuntime(
            reducer=reducer,
            profile=profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=self._actor_factory(self._api_config),
            state=state,
            controller=controller,
            mutation_permits=permits,
            attested_visual_verifier=self._verifier_factory(self._api_config),
            mission_completion_verifier=DeterministicMissionCompletionVerifier(),
            evidence_registry=evidence,
        )
        return runtime, tuple(tool.name for tool in catalog.tools)

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
            definition = project_legacy_contract(
                contract,
                mission_id=f"mission-{context.run_id}",
                run_id=context.run_id,
            )
            evidence = EvidenceRegistry(definition.mission_id, definition.run_id)
            reducer = MissionReducer(definition, evidence=evidence)
            hwnd, handoff = self._handoff_target(context)
            target = self._bind_target(
                reducer=reducer,
                evidence=evidence,
                handoff_hwnd=hwnd,
                handoff=handoff,
            )
            runtime, tool_names = self._runtime(
                context, reducer, evidence, target,
            )
            self._trace(context.run_id, {
                "type": "vnext_mission_runtime_started",
                "runtime_version": "vnext",
                "mission_id": definition.mission_id,
                "mission_revision": reducer.projection.revision,
                "active_obligation_id": reducer.projection.active_obligation_id,
                "tool_names": list(tool_names),
                "profile_source": "safe_default",
            })

            for turn_index in range(self._max_turns):
                if context.stop_event.is_set() or not context.wait_if_paused():
                    return DesktopRuntimeCoordinatorResult(
                        DesktopRuntimeCoordinatorStatus.CANCELED,
                        error="vNext desktop Runtime was canceled",
                    )
                result = runtime.run_one_turn(
                    turn_index=turn_index,
                    stop_event=context.stop_event,
                )
                projection = reducer.projection
                self._trace(context.run_id, {
                    "type": "vnext_mission_runtime_turn",
                    "runtime_version": "vnext",
                    "turn_index": turn_index,
                    "status": result.status.value,
                    "route": None if result.route is None else result.route.value,
                    "proposal_kind": result.proposal_kind,
                    "decision_id": result.decision_id,
                    "accepted_event_ids": list(result.accepted_event_ids),
                    "mission_revision": projection.revision,
                    "mission_status": projection.status.value,
                    "active_obligation_id": projection.active_obligation_id,
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
                runtime.mutation_permits.close()
            if evidence is not None:
                evidence.close()


def build_live_desktop_runtime_coordinator(
    *,
    trace_sidecar: TraceSidecarPort | None = None,
) -> LiveDesktopRuntimeCoordinator:
    """Build the selected product coordinator without starting a run."""

    return LiveDesktopRuntimeCoordinator(
        api_config=CONFIG.get("api", default={}) or {},
        trace_sidecar=trace_sidecar,
    )


__all__ = [
    "DEFAULT_MAX_TURNS",
    "LiveDesktopCoordinatorError",
    "LiveDesktopRuntimeCoordinator",
    "TraceSidecarPort",
    "build_live_desktop_runtime_coordinator",
]
