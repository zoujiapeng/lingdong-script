"""Same-entry, read-only Broker shadow for legacy Visual Actor turns.

The adapter reads existing runtime owners and records what P3 would expose. It
never changes the legacy request, dispatches an action, or manufactures
artifact/provider/MCP availability.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Iterable, Mapping, Protocol

from ...visual_runtime.windows_desktop_facts import (
    WindowsDesktopFactsError,
    WindowsDesktopFactsReader,
)
from ..mission import (
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    project_legacy_contract,
    target_binding_attestation,
)
from ..profiling import ExecutionProfile
from .broker import CapabilityBroker
from .catalog import ToolPolicy, build_tool_catalog, schemas_by_name
from .contracts import MutationKind, PointerDisposition, ToolRoute
from .shadow import BrokerShadowEvaluator
from .state import (
    AuthoritativeFrameState,
    BrokerRuntimeState,
    ExecutorOffer,
    PointerLeaseState,
    TargetState,
)


BROKER_LEGACY_SHADOW_FORMAT = "broker-legacy-shadow/0"


class ShadowTraceWriter(Protocol):
    def record_sidecar(self, event: dict[str, Any], *, run_id: str) -> bool: ...


_GENERIC_POLICIES = (
    ToolPolicy("observe", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=1),
    ToolPolicy("open_app", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("application.resolve", "visual.interact"),
               ("target_unbound", "pointer_released", "runtime_healthy"), order=2),
    ToolPolicy("launch", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("application.resolve", "visual.interact"),
               ("target_unbound", "pointer_released", "runtime_healthy"), order=3),
    ToolPolicy("list_windows", ToolRoute.VISUAL_ACTOR, MutationKind.NONE,
               ("application.resolve", "visual.interact"),
               ("pointer_released", "runtime_healthy"), order=4),
    ToolPolicy("focus_window", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("application.resolve", "visual.interact"),
               ("pointer_released", "runtime_healthy"), order=5),
    ToolPolicy("click", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=10),
    ToolPolicy("double_click", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=11),
    ToolPolicy("type_text", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=12),
    ToolPolicy("hotkey", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=13),
    ToolPolicy("scroll", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=14),
    ToolPolicy("drag", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.interact",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=15),
    ToolPolicy("draw_strokes", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("visual.draw",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=16),
    ToolPolicy("mouse_down", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("pointer.dynamic",),
               ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=17),
    ToolPolicy("move_mouse", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("pointer.dynamic",), ("pointer_held",), order=18),
    ToolPolicy("mouse_up", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
               ("pointer.dynamic",), (), order=19),
    ToolPolicy("wait", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=30),
    ToolPolicy("blocked", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=31),
    ToolPolicy(
        "capture_artifact",
        ToolRoute.DETERMINISTIC,
        MutationKind.EXTERNAL,
        ("artifact.capture",),
        ("target_bound", "authoritative_frame", "runtime_healthy"),
        "local.artifact_capture",
        order=40,
    ),
)


def _tool_names(tools: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        name for name in (
            str(item.get("function", {}).get("name") or "").strip()
            for item in tools if isinstance(item, Mapping)
        ) if name
    )


def _digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pointer_state(run_id: str, status: Mapping[str, Any]) -> PointerLeaseState:
    active = status.get("active")
    button = str(status.get("button") or "").strip()
    if active is False:
        return PointerLeaseState(PointerDisposition.RELEASED)
    if active is True and button:
        lease = "legacy-pointer-" + _digest({"run_id": run_id, "button": button})[:24]
        return PointerLeaseState(PointerDisposition.HELD, lease, True)
    return PointerLeaseState(PointerDisposition.UNKNOWN)


def _local_executor_state(
    runner: Any,
    catalog_tool_names: set[str],
) -> tuple[tuple[ExecutorOffer, ...], tuple[str, ...]]:
    session = getattr(runner, "session", None)
    if (
        "capture_artifact" in catalog_tool_names
        and callable(getattr(session, "_managed_artifacts", None))
    ):
        scope = "local.artifact_capture"
        return (
            (ExecutorOffer(
                executor_id="local-artifact-store",
                route=ToolRoute.DETERMINISTIC,
                supported_capabilities=("artifact.capture",),
                tool_names=("capture_artifact",),
                available=True,
                live=True,
                authorization_scopes=(scope,),
                receipt_kinds=("artifact.sha256",),
            ),),
            (scope,),
        )
    return (), ()


class LegacyBrokerShadowObserver:
    """Run-scoped observer attached to a legacy runner by the outer runtime."""

    def __init__(
        self,
        *,
        definition: MissionDefinition,
        profile: ExecutionProfile,
        trace: ShadowTraceWriter,
        available_capabilities: Iterable[str],
        desktop_facts_reader: WindowsDesktopFactsReader | None = None,
    ) -> None:
        self.definition = definition
        self.profile = profile
        self._trace = trace
        self._available = tuple(sorted(set(available_capabilities)))
        if desktop_facts_reader is not None and not isinstance(
            desktop_facts_reader, WindowsDesktopFactsReader
        ):
            raise TypeError(
                "desktop_facts_reader must be WindowsDesktopFactsReader"
            )
        self._desktop_facts = desktop_facts_reader or WindowsDesktopFactsReader()
        self._evidence: dict[str, EvidenceRef] = {}
        self._reducer = MissionReducer(definition, evidence=lambda: self._evidence)
        self._lock = threading.RLock()
        self._catalog_fingerprint: str | None = None
        self._catalog = None
        self._catalog_source: object | None = None
        self._outer_turn_index = 0
        self._definition_emitted = False
        self._emitted_event_count = 0
        self._emitted_evidence_ids: set[str] = set()
        self._unavailable_reason = (
            "multi_obligation_mission" if len(definition.obligations) != 1 else None
        )

    @property
    def projection(self):
        return self._reducer.projection

    @property
    def accepted_events(self):
        return self._reducer.events

    @property
    def evidence_refs(self) -> tuple[EvidenceRef, ...]:
        return tuple(self._evidence[key] for key in sorted(self._evidence))

    def _frame_and_target(self, runner: Any) -> tuple[AuthoritativeFrameState, TargetState]:
        authority = getattr(runner, "_frame_authority", None)
        frame = getattr(authority, "current", None)
        if frame is None:
            self._invalidate_target("authoritative_frame_unavailable")
            return AuthoritativeFrameState(), TargetState()
        try:
            frame_id, hwnd, epoch = int(frame.frame_id), int(frame.hwnd), int(frame.epoch)
        except (AttributeError, TypeError, ValueError):
            self._invalidate_target("authoritative_frame_identity_invalid")
            return AuthoritativeFrameState(), TargetState()
        if frame_id < 0 or hwnd <= 0 or epoch < 0:
            self._invalidate_target("authoritative_frame_identity_invalid")
            return AuthoritativeFrameState(), TargetState()
        session = getattr(runner, "session", None)
        try:
            session_epoch = int(getattr(session, "_screen_epoch", -1))
        except (TypeError, ValueError):
            session_epoch = -1
        if session_epoch != epoch:
            self._invalidate_target(
                "authoritative_frame_stale", observed_frame_id=frame_id,
            )
            return AuthoritativeFrameState(frame_id, hwnd, epoch, True, False), TargetState()
        frame_state = AuthoritativeFrameState(frame_id, hwnd, epoch, True, True)

        verified = bool(getattr(session, "_target_binding_verified", False))
        target_hwnd = int(getattr(session, "hwnd", 0) or 0)
        process_id = int(getattr(session, "_target_process_id", 0) or 0)
        if not (verified and target_hwnd == hwnd and process_id > 0):
            self._invalidate_target(
                "authoritative_target_unavailable", observed_frame_id=frame_id,
            )
            return frame_state, TargetState()
        current = self._reducer.projection.target_binding
        try:
            physical = self._desktop_facts.read_target_identity(
                hwnd,
                binding_id="target-" + "0" * 24,
                epoch=epoch,
            )
        except WindowsDesktopFactsError:
            self._invalidate_target(
                "authoritative_target_identity_unavailable",
                observed_frame_id=frame_id,
            )
            return frame_state, TargetState()
        if physical.process_id != process_id:
            self._invalidate_target(
                "authoritative_target_process_changed",
                observed_frame_id=frame_id,
            )
            return frame_state, TargetState()
        process_identity = physical.process_identity
        if (
            current is not None
            and current.hwnd == physical.hwnd
            and current.root_hwnd == physical.root_hwnd
            and current.process_id == physical.process_id
            and current.process_identity == physical.process_identity
            and current.epoch == physical.epoch
        ):
            return frame_state, TargetState(
                current.binding_id, current.hwnd, current.epoch, True
            )
        if current is not None:
            prior_evidence = self._evidence.get(
                current.established_by_evidence_ref
            )
            prior_frame_id = (
                None if prior_evidence is None else prior_evidence.frame_id
            )
            self._invalidate_target(
                "authoritative_target_changed", observed_frame_id=frame_id,
            )
            if prior_frame_id is None or frame_id <= prior_frame_id:
                return frame_state, TargetState()
        rebind_after = self._reducer.projection.target_rebind_after_frame_id
        if rebind_after is not None and frame_id <= rebind_after:
            return frame_state, TargetState()
        acquisition_action_id = "legacy-observation-" + _digest({
            "mission_id": self.definition.mission_id,
            "run_id": self.definition.run_id,
            "frame_id": frame_id,
            "epoch": epoch,
            "hwnd": hwnd,
            "process_identity": process_identity,
        })[:24]
        evidence_id = "evidence-target-" + _digest({
            "mission_id": self.definition.mission_id,
            "run_id": self.definition.run_id,
            "acquisition_action_id": acquisition_action_id,
        })[:24]
        obligation_id = self._reducer.projection.active_obligation_id
        if obligation_id is None:
            self._invalidate_target("active_obligation_unavailable")
            return frame_state, TargetState()
        binding, evidence = target_binding_attestation(
            mission_id=self.definition.mission_id,
            run_id=self.definition.run_id,
            obligation_id=obligation_id,
            evidence_id=evidence_id,
            acquisition_action_id=acquisition_action_id,
            hwnd=physical.hwnd,
            root_hwnd=physical.root_hwnd,
            process_id=physical.process_id,
            process_identity=process_identity,
            title_snapshot="",
            epoch=physical.epoch,
            frame_id=frame_id,
            created_at=getattr(frame, "captured_at", None),
        )
        self._evidence[evidence.id] = evidence
        event = self._reducer.event(
            "target_bound",
            payload={"target_binding": binding.to_dict()},
            evidence_refs=(evidence.id,),
            created_at=evidence.created_at,
        )
        self._reducer.apply(event)
        return frame_state, TargetState(
            binding.binding_id, binding.hwnd, binding.epoch, True
        )

    def _invalidate_target(
        self, reason: str, *, observed_frame_id: int | None = None,
    ) -> None:
        binding = self._reducer.projection.target_binding
        if binding is None:
            return
        self._reducer.apply(self._reducer.event(
            "target_invalidated",
            payload={
                "expected_binding_id": binding.binding_id,
                "expected_canonical_identity_sha256": (
                    binding.canonical_identity_sha256
                ),
                "observed_frame_id": observed_frame_id,
                "reason": reason,
            },
        ))

    def _write(self, record: dict[str, Any]) -> bool:
        try:
            return bool(self._trace.record_sidecar(record, run_id=self.definition.run_id))
        except Exception:
            # Trace-only observation can never fail a legacy turn.
            return False

    def _emit(self, record: dict[str, Any]) -> None:
        events = self._reducer.events[self._emitted_event_count:]
        evidence = tuple(
            self._evidence[key] for key in sorted(self._evidence)
            if key not in self._emitted_evidence_ids
        )
        record["replay_delta"] = {
            "mission_definition": (
                self.definition.to_dict() if not self._definition_emitted else None
            ),
            "accepted_events": [item.to_dict() for item in events],
            "evidence_refs": [item.to_dict() for item in evidence],
        }
        if self._write(record):
            self._definition_emitted = True
            self._emitted_event_count += len(events)
            self._emitted_evidence_ids.update(item.id for item in evidence)

    def observe_turn(
        self,
        *,
        runner: Any,
        base_tools: list[dict[str, Any]],
        legacy_selected_tools: list[dict[str, Any]],
        step: int,
        pointer_status: Mapping[str, Any],
    ) -> None:
        with self._lock:
            self._outer_turn_index += 1
            record: dict[str, Any] = {
                "type": "vnext_broker_shadow",
                "format": BROKER_LEGACY_SHADOW_FORMAT,
                "runtime_version": "legacy",
                "child_run_id": str(getattr(runner, "run_id", "") or ""),
                "step": step,
                "outer_turn_index": self._outer_turn_index,
                "status": "unavailable",
                "reason": self._unavailable_reason,
                "legacy_base_tool_names": list(_tool_names(base_tools)),
                "legacy_selected_tool_names": list(_tool_names(legacy_selected_tools)),
                "broker_projection": None,
                "shadow_evaluation": None,
            }
            if self._unavailable_reason:
                self._emit(record)
                return
            if (
                self.profile.mission_id != self.definition.mission_id
                or self.profile.goal_revision != self.definition.goal_revision
            ):
                record["reason"] = "execution_profile_identity_mismatch"
                self._emit(record)
                return
            try:
                if base_tools is self._catalog_source and self._catalog is not None:
                    catalog = self._catalog
                else:
                    schema_names = schemas_by_name(base_tools)
                    policies = tuple(
                        item for item in _GENERIC_POLICIES if item.name in schema_names
                    )
                    if not policies:
                        record["reason"] = "no_supported_legacy_tool_schemas"
                        self._emit(record)
                        return
                    catalog = build_tool_catalog(base_tools, policies)
                    if (
                        self._catalog_fingerprint is not None
                        and catalog.fingerprint != self._catalog_fingerprint
                    ):
                        record["reason"] = "legacy_tool_catalog_changed"
                        self._emit(record)
                        return
                    self._catalog_fingerprint = catalog.fingerprint
                    self._catalog = catalog
                    self._catalog_source = base_tools
                frame, target = self._frame_and_target(runner)
                pointer = _pointer_state(self.definition.run_id, pointer_status)
                executor_offers, authorization_scopes = _local_executor_state(
                    runner, {item.name for item in catalog.tools}
                )
                stop_event = getattr(runner, "stop_event", None)
                canceled = bool(stop_event and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set())
                state = BrokerRuntimeState(
                    turn_index=self._outer_turn_index,
                    available_capabilities=self._available,
                    target=target,
                    frame=frame,
                    pointer=pointer,
                    artifacts=(),
                    executor_offers=executor_offers,
                    authorization_scopes=authorization_scopes,
                    runtime_healthy=not canceled,
                    paused=False,
                    canceled=canceled,
                )
                projection = CapabilityBroker(catalog).project(
                    self._reducer.projection, self.profile, state
                )
                evaluation = BrokerShadowEvaluator(catalog).evaluate(projection)
                record.update({
                    "status": "projected",
                    "reason": None,
                    "mission_revision": self._reducer.projection.revision,
                    "active_obligation_id": self._reducer.projection.active_obligation_id,
                    "profile_id": self.profile.profile_id,
                    "catalog_fingerprint": catalog.fingerprint,
                    "runtime_facts": {
                        "target_bound": target.live,
                        "authoritative_frame": frame.authoritative and frame.fresh,
                        "pointer_disposition": pointer.disposition.value,
                        "artifact_count": 0,
                        "executor_offer_count": len(executor_offers),
                        "authorization_scope_count": len(authorization_scopes),
                    },
                    "broker_projection": projection.to_dict(),
                    "shadow_evaluation": evaluation.to_dict(),
                })
            except Exception as exc:
                record["status"] = "error"
                record["reason"] = type(exc).__name__
            self._emit(record)


def build_legacy_broker_shadow_observer(
    *,
    run_id: str,
    contract: Any,
    profile: ExecutionProfile,
    trace: ShadowTraceWriter,
    available_capabilities: Iterable[str],
    desktop_facts_reader: WindowsDesktopFactsReader | None = None,
) -> LegacyBrokerShadowObserver:
    """Factory for StageRuntime/server; it creates no alternate product path.

    ``available_capabilities`` must come from a local/runtime registry. It is
    deliberately required and must never be copied from a model proposal.
    """
    definition = project_legacy_contract(
        contract,
        mission_id=profile.mission_id,
        run_id=run_id,
    )
    return LegacyBrokerShadowObserver(
        definition=definition,
        profile=profile,
        trace=trace,
        available_capabilities=available_capabilities,
        desktop_facts_reader=desktop_facts_reader,
    )


def attach_legacy_broker_shadow(runner: Any, observer: LegacyBrokerShadowObserver) -> None:
    """Attach the read-only observer without changing the runner interface."""
    runner._vnext_broker_shadow_observer = observer


def detach_legacy_broker_shadow(runner: Any, observer: LegacyBrokerShadowObserver) -> bool:
    """Release run-scoped state only when this exact observer is attached."""
    if getattr(runner, "_vnext_broker_shadow_observer", None) is not observer:
        return False
    delattr(runner, "_vnext_broker_shadow_observer")
    return True


__all__ = [
    "BROKER_LEGACY_SHADOW_FORMAT",
    "ShadowTraceWriter",
    "LegacyBrokerShadowObserver",
    "build_legacy_broker_shadow_observer",
    "attach_legacy_broker_shadow",
    "detach_legacy_broker_shadow",
]
