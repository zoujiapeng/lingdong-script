"""Pure deterministic Capability Broker; it never inspects GUI semantics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..capability_names import CANONICAL_CAPABILITIES
from ..mission import MissionProjection, MissionStatus
from ..profiling import ExecutionProfile
from .contracts import (
    CapabilityGrant,
    ExecutionMedium,
    ExecutionRoute,
    InteractionMode,
    MutationKind,
    PointerDisposition,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from .state import (
    ArtifactState,
    BrokerProjection,
    BrokerRuntimeState,
    ExecutorOffer,
    _broker_projection_id,
)

@dataclass(frozen=True, slots=True)
class CapabilityBroker:
    """Project one bounded tool surface from immutable accepted facts."""

    catalog: ToolCatalog

    def __post_init__(self) -> None:
        if type(self.catalog) is not ToolCatalog:
            raise TypeError("catalog must be ToolCatalog")

    @staticmethod
    def _active_id(mission: MissionProjection) -> str:
        return str(mission.active_obligation_id or "mission")

    @staticmethod
    def _target_bound(mission: MissionProjection, state: BrokerRuntimeState) -> bool:
        expected = mission.target_binding
        target = state.target
        return bool(
            expected is not None
            and target.live
            and target.binding_id == expected.binding_id
            and target.hwnd == expected.hwnd
            and target.epoch == expected.epoch
        )

    @classmethod
    def _frame_valid(cls, mission: MissionProjection, state: BrokerRuntimeState) -> bool:
        if not cls._target_bound(mission, state):
            return False
        frame, target = state.frame, state.target
        return bool(
            frame.authoritative
            and frame.fresh
            and frame.frame_id is not None
            and frame.hwnd == target.hwnd
            and frame.epoch == target.epoch
            and frame.scope == "target_window"
        )

    @staticmethod
    def _runtime_writable(state: BrokerRuntimeState) -> bool:
        return bool(
            state.runtime_healthy
            and not state.paused
            and not state.canceled
        )

    @staticmethod
    def _valid_artifacts(
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> tuple[ArtifactState, ...]:
        if mission.active_obligation_id is None:
            return ()
        active = mission.obligation_definition(mission.active_obligation_id)
        required_inputs = set(active.input_artifact_refs)
        if not required_inputs:
            return ()
        valid = []
        for artifact in state.artifacts:
            expected = mission.artifact_bindings.get(artifact.binding_name)
            if (
                expected is not None
                and (
                    artifact.binding_name in required_inputs
                    or expected.artifact_id in required_inputs
                )
                and artifact.run_id == mission.run_id
                and artifact.artifact_id == expected.artifact_id
                and artifact.content_sha256 == expected.sha256
                and artifact.sendable
            ):
                valid.append(artifact)
        return tuple(valid)

    @classmethod
    def _prerequisites_met(
        cls,
        tool: ToolSpec,
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> bool:
        bound = cls._target_bound(mission, state)
        frame = cls._frame_valid(mission, state)
        valid_artifacts = cls._valid_artifacts(mission, state)
        live_offers = tuple(
            item for item in state.executor_offers if item.available and item.live
        )
        checks = {
            "target_bound": bound,
            "authoritative_frame": frame,
            "pointer_released": (
                state.pointer.disposition is PointerDisposition.RELEASED
            ),
            "pointer_held": (
                state.pointer.disposition is PointerDisposition.HELD
                and state.pointer.physically_owned
            ),
            "current_run_artifact": bool(valid_artifacts),
            "recipient_bound": "recipient.bound" in state.authorization_scopes,
            "adapter_live": any(
                item.route is ToolRoute.PROVIDER for item in live_offers
            ),
            "mcp_discovered": any(
                "mcp.office" in item.supported_capabilities
                for item in live_offers
            ),
            "receipt_capable": any(item.receipt_kinds for item in live_offers),
            "runtime_healthy": cls._runtime_writable(state),
        }
        return all(checks[item] for item in tool.prerequisites)

    @staticmethod
    def _pointer_is_owned_held(state: BrokerRuntimeState) -> bool:
        return bool(
            state.pointer.disposition is PointerDisposition.HELD
            and state.pointer.physically_owned
            and state.pointer.lease_id
        )

    @classmethod
    def _tool_eligible(
        cls,
        tool: ToolSpec,
        mission: MissionProjection,
        state: BrokerRuntimeState,
        capabilities: set[str],
    ) -> bool:
        if tool.route is not ToolRoute.VISUAL_ACTOR:
            return False
        pointer_held = cls._pointer_is_owned_held(state)
        if state.pointer.disposition is PointerDisposition.UNKNOWN:
            return False
        if (
            state.pointer.disposition is PointerDisposition.HELD
            and (not pointer_held or tool.name != "drag")
        ):
            return False
        if (
            state.interaction_mode is InteractionMode.GUI_REQUIRED
            and tool.execution_medium not in {
                ExecutionMedium.GUI,
                ExecutionMedium.CONTROL,
            }
        ):
            return False
        if (
            state.interaction_mode is InteractionMode.GUI_REQUIRED
            and not cls._frame_valid(mission, state)
        ):
            # A non-mutating control decision may satisfy an already-complete
            # or read-only goal, but it counts as GUI execution only when it
            # is grounded in a fresh authoritative observation of the bound
            # target.  GUI mutation tools already require the same frame
            # below; keeping the gate here also covers claim/blocked tools
            # without manufacturing an input action.
            return False
        if tool.required_any_capabilities and not (
            set(tool.required_any_capabilities) & capabilities
        ):
            return False
        if (
            tool.authorization_scope
            and tool.authorization_scope not in state.authorization_scopes
        ):
            return False
        if tool.mutation_kind is MutationKind.GUI:
            if (
                mission.status is not MissionStatus.RUNNING
                or not cls._runtime_writable(state)
                or not cls._target_bound(mission, state)
                or not cls._frame_valid(mission, state)
                or not tool.required_any_capabilities
            ):
                return False
        elif tool.mutation_kind is not MutationKind.NONE:
            return False
        return cls._prerequisites_met(tool, mission, state)

    def _finish(
        self,
        mission: MissionProjection,
        profile: ExecutionProfile,
        state: BrokerRuntimeState,
        *,
        route: ExecutionRoute,
        tool_names: Iterable[str],
        grants: tuple[CapabilityGrant, ...],
        missing: tuple[str, ...],
        reasons: tuple[str, ...],
        executor_id: str | None = None,
    ) -> BrokerProjection:
        names = tuple(tool_names)
        active_grants = tuple(sorted(grants, key=lambda item: item.grant_id))
        bound = self._target_bound(mission, state)
        valid_frame = self._frame_valid(mission, state)
        target_binding_id = state.target.binding_id if bound else None
        frame_id = state.frame.frame_id if valid_frame else None
        frame_scope = state.frame.scope if valid_frame else None
        obligation_id = self._active_id(mission)
        projection_id = _broker_projection_id(
            mission_id=mission.mission_id,
            run_id=mission.run_id,
            catalog_fingerprint=self.catalog.fingerprint,
            profile_id=profile.profile_id,
            state_fingerprint=state.fingerprint,
            mission_revision=mission.revision,
            obligation_id=obligation_id,
            turn_index=state.turn_index,
            execution_route=route,
            executor_id=executor_id,
            target_binding_id=target_binding_id,
            frame_id=frame_id,
            frame_scope=frame_scope,
            pointer_lease_id=state.pointer.lease_id,
            tool_names=names,
            active_grants=active_grants,
            missing_capabilities=missing,
            projection_reasons=reasons,
        )
        return BrokerProjection(
            projection_id=projection_id,
            mission_id=mission.mission_id,
            run_id=mission.run_id,
            catalog_fingerprint=self.catalog.fingerprint,
            profile_id=profile.profile_id,
            state_fingerprint=state.fingerprint,
            mission_revision=mission.revision,
            obligation_id=obligation_id,
            turn_index=state.turn_index,
            execution_route=route,
            executor_id=executor_id,
            target_binding_id=target_binding_id,
            frame_id=frame_id,
            frame_scope=frame_scope,
            pointer_lease_id=state.pointer.lease_id,
            tool_names=names,
            active_grants=active_grants,
            missing_capabilities=missing,
            projection_reasons=reasons,
        )

    def _named_nonmutation_tools(
        self,
        names: Iterable[str],
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> tuple[str, ...]:
        result = []
        for name in names:
            try:
                tool = self.catalog.get(name)
            except KeyError:
                continue
            if (
                tool.route is ToolRoute.VISUAL_ACTOR
                and tool.mutation_kind is MutationKind.NONE
                and (
                    state.interaction_mode is not InteractionMode.GUI_REQUIRED
                    or (
                        tool.execution_medium is ExecutionMedium.CONTROL
                        and self._frame_valid(mission, state)
                    )
                )
                and self._prerequisites_met(tool, mission, state)
            ):
                result.append(name)
        return tuple(result)

    def _offer(
        self,
        required: set[str],
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> tuple[ExecutorOffer, tuple[str, ...]] | None:
        if state.interaction_mode is InteractionMode.GUI_REQUIRED:
            return None
        for offer in sorted(
            state.executor_offers,
            key=lambda item: (item.route is ToolRoute.PROVIDER, item.executor_id),
        ):
            if not (offer.available and offer.live) or not required:
                continue
            if not required <= set(offer.supported_capabilities):
                continue
            if not set(offer.authorization_scopes) <= set(
                state.authorization_scopes
            ):
                continue
            tools = []
            valid = True
            for name in offer.tool_names:
                try:
                    tool = self.catalog.get(name)
                except KeyError:
                    valid = False
                    break
                tool_capabilities = set(tool.required_any_capabilities)
                if (
                    tool.route is not offer.route
                    or not tool_capabilities
                    or not tool_capabilities.intersection(required)
                    or not tool_capabilities.intersection(
                        offer.supported_capabilities
                    )
                    or not self._prerequisites_met(tool, mission, state)
                ):
                    valid = False
                    break
                if (
                    tool.authorization_scope
                    and (
                        tool.authorization_scope
                        not in offer.authorization_scopes
                        or tool.authorization_scope
                        not in state.authorization_scopes
                    )
                ):
                    valid = False
                    break
                if (
                    tool.mutation_kind is MutationKind.EXTERNAL
                    and not offer.receipt_kinds
                ):
                    valid = False
                    break
                tools.append(name)
            if valid and tools:
                return offer, tuple(
                    sorted(
                        tools,
                        key=lambda name: (self.catalog.get(name).order, name),
                    )
                )
        return None

    def project(
        self,
        mission: MissionProjection,
        profile: ExecutionProfile,
        state: BrokerRuntimeState,
        grants: Iterable[CapabilityGrant] = (),
    ) -> BrokerProjection:
        if (
            type(mission) is not MissionProjection
            or type(profile) is not ExecutionProfile
            or type(state) is not BrokerRuntimeState
        ):
            raise TypeError(
                "Broker requires MissionProjection, ExecutionProfile, and "
                "BrokerRuntimeState"
            )
        supplied_grants = tuple(grants)
        if any(not isinstance(item, CapabilityGrant) for item in supplied_grants):
            raise TypeError("grants must contain CapabilityGrant values")

        active_id = self._active_id(mission)
        if (
            profile.mission_id != mission.mission_id
            or profile.goal_revision != mission.definition.goal_revision
        ):
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=(),
                grants=(),
                missing=(),
                reasons=("execution_profile_identity_mismatch",),
            )

        if (
            mission.status is not MissionStatus.RUNNING
            or mission.active_obligation_id is None
        ):
            tools = self._named_nonmutation_tools(
                ("wait", "blocked"), mission, state
            )
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=tools,
                grants=(),
                missing=(),
                reasons=("mission_not_actionable",),
            )
        if not self._runtime_writable(state):
            tools = self._named_nonmutation_tools(
                ("wait", "blocked"), mission, state
            )
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=tools,
                grants=(),
                missing=(),
                reasons=("runtime_not_writable",),
            )
        pointer_held = self._pointer_is_owned_held(state)
        if (
            state.pointer.disposition is PointerDisposition.UNKNOWN
            or (
                state.pointer.disposition is PointerDisposition.HELD
                and not pointer_held
            )
        ):
            tools = self._named_nonmutation_tools(
                ("wait", "blocked"), mission, state
            )
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=tools,
                grants=(),
                missing=(),
                reasons=("pointer_not_owned",),
            )

        obligation = mission.obligation_definition(active_id)
        required = set(obligation.required_capabilities)
        # CapabilityGrant is content-addressed, not an authority signature.
        # The Mission reducer does not yet project accepted capability_granted
        # events into authoritative grant state, so caller-supplied grants
        # remain inert until that authority owner exists.
        valid_grants: tuple[CapabilityGrant, ...] = ()
        unknown = required - CANONICAL_CAPABILITIES
        if unknown:
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=(),
                grants=valid_grants,
                missing=(),
                reasons=("unknown_required_capability",),
            )
        missing_runtime = tuple(
            sorted(required - set(state.available_capabilities))
        )
        if missing_runtime:
            tools = self._named_nonmutation_tools(
                ("request_capability", "blocked"), mission, state
            )
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=tools,
                grants=valid_grants,
                missing=missing_runtime,
                reasons=("required_capability_unavailable",),
            )
        missing_profile = tuple(
            sorted(required - set(profile.accepted_capabilities))
        )
        if missing_profile:
            tools = self._named_nonmutation_tools(
                ("request_capability", "blocked"), mission, state
            )
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=tools,
                grants=valid_grants,
                missing=missing_profile,
                reasons=("required_capability_not_accepted",),
            )

        offered = None if pointer_held else self._offer(
            required,
            mission,
            state,
        )
        if offered is not None:
            offer, tools = offered
            route = (
                ExecutionRoute.DETERMINISTIC
                if offer.route is ToolRoute.DETERMINISTIC
                else ExecutionRoute.PROVIDER
            )
            return self._finish(
                mission,
                profile,
                state,
                route=route,
                tool_names=tools,
                grants=valid_grants,
                missing=(),
                reasons=("live_executor_offer_selected",),
                executor_id=offer.executor_id,
            )

        capabilities = required
        tools = tuple(
            tool.name
            for tool in self.catalog.tools
            if self._tool_eligible(
                tool, mission, state, capabilities
            )
        )
        if not tools:
            return self._finish(
                mission,
                profile,
                state,
                route=ExecutionRoute.BLOCKED,
                tool_names=(),
                grants=valid_grants,
                missing=(),
                reasons=(
                    ("gui_required_route_unavailable",)
                    if state.interaction_mode is InteractionMode.GUI_REQUIRED
                    else ("no_tool_prerequisites_satisfied",)
                ),
            )
        return self._finish(
            mission,
            profile,
            state,
            route=ExecutionRoute.VISUAL_ACTOR,
            tool_names=tools,
            grants=valid_grants,
            missing=(),
            reasons=(
                (
                    "gui_required_policy_enforced",
                    "visual_actor_surface_projected",
                )
                if state.interaction_mode is InteractionMode.GUI_REQUIRED
                else ("visual_actor_surface_projected",)
            ),
        )


__all__ = ["CapabilityBroker"]
