"""Pure deterministic Capability Broker; it never inspects GUI semantics."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from ..mission import MissionProjection, MissionStatus
from ..profiling import ExecutionProfile
from .contracts import (
    CapabilityContractError,
    CapabilityGrant,
    ExecutionRoute,
    MutationKind,
    PointerDisposition,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from ..capability_names import CANONICAL_CAPABILITIES
from .state import ArtifactState, BrokerProjection, BrokerRuntimeState, ExecutorOffer


_TERMINAL = frozenset({
    MissionStatus.VERIFIED,
    MissionStatus.FAILED,
    MissionStatus.CANCELED,
})
_POINTER_HELD_TOOLS = ("move_mouse", "mouse_up", "wait", "blocked")
_POINTER_UNKNOWN_TOOLS = ("mouse_up", "wait", "blocked")


class CapabilityBroker:
    def __init__(self, catalog: ToolCatalog) -> None:
        if not isinstance(catalog, ToolCatalog):
            raise TypeError("catalog must be ToolCatalog")
        self.catalog = catalog

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
            frame.authoritative and frame.fresh and frame.frame_id is not None
            and frame.hwnd == target.hwnd and frame.epoch == target.epoch
        )

    @staticmethod
    def _valid_artifacts(
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> tuple[ArtifactState, ...]:
        valid = []
        for artifact in state.artifacts:
            expected = mission.artifact_bindings.get(artifact.binding_name)
            if (
                expected is not None
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
            "target_unbound": not bound,
            "target_bound": bound,
            "authoritative_frame": frame,
            "pointer_released": state.pointer.disposition is PointerDisposition.RELEASED,
            "pointer_held": (
                state.pointer.disposition is PointerDisposition.HELD
                and state.pointer.physically_owned
            ),
            "current_run_artifact": bool(valid_artifacts),
            "recipient_bound": "recipient.bound" in state.authorization_scopes,
            "adapter_live": any(item.route is ToolRoute.PROVIDER for item in live_offers),
            "mcp_discovered": any("mcp.office" in item.supported_capabilities for item in live_offers),
            "receipt_capable": any(item.receipt_kinds for item in live_offers),
            "runtime_healthy": state.runtime_healthy and not state.paused and not state.canceled,
        }
        return all(checks[item] for item in tool.prerequisites)

    @staticmethod
    def _projection_id(material: dict) -> str:
        raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return f"projection-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    def _finish(
        self,
        mission: MissionProjection,
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
        material = {
            "catalog": self.catalog.fingerprint,
            "mission_id": mission.mission_id,
            "run_id": mission.run_id,
            "revision": mission.revision,
            "obligation_id": self._active_id(mission),
            "route": route.value,
            "executor_id": executor_id,
            "target_binding_id": state.target.binding_id,
            "pointer_lease_id": state.pointer.lease_id,
            "turn_index": state.turn_index,
            "tools": names,
            "grants": tuple(item.grant_id for item in grants),
            "missing": missing,
            "reasons": reasons,
        }
        return BrokerProjection(
            projection_id=self._projection_id(material),
            mission_revision=mission.revision,
            obligation_id=self._active_id(mission),
            execution_route=route,
            executor_id=executor_id,
            target_binding_id=state.target.binding_id,
            pointer_lease_id=state.pointer.lease_id,
            tool_names=names,
            active_grants=grants,
            missing_capabilities=missing,
            projection_reasons=reasons,
        )

    def _named_safe_tools(
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
            if tool.route is ToolRoute.VISUAL_ACTOR and self._prerequisites_met(tool, mission, state):
                result.append(name)
        return tuple(result)

    def _offer(
        self,
        required: set[str],
        mission: MissionProjection,
        state: BrokerRuntimeState,
    ) -> tuple[ExecutorOffer, tuple[str, ...]] | None:
        for offer in sorted(state.executor_offers, key=lambda item: (
            item.route is ToolRoute.PROVIDER, item.executor_id,
        )):
            if not (offer.available and offer.live) or not required:
                continue
            if not required <= set(offer.supported_capabilities):
                continue
            if not set(offer.authorization_scopes) <= set(state.authorization_scopes):
                continue
            tools = []
            valid = True
            for name in offer.tool_names:
                try:
                    tool = self.catalog.get(name)
                except KeyError:
                    valid = False
                    break
                if tool.route is not offer.route or not self._prerequisites_met(tool, mission, state):
                    valid = False
                    break
                tool_capabilities = set(tool.required_any_capabilities)
                if tool_capabilities and (
                    not tool_capabilities.intersection(required)
                    or not tool_capabilities.intersection(offer.supported_capabilities)
                ):
                    valid = False
                    break
                if tool.authorization_scope and tool.authorization_scope not in state.authorization_scopes:
                    valid = False
                    break
                if tool.mutation_kind is MutationKind.EXTERNAL and not offer.receipt_kinds:
                    valid = False
                    break
                tools.append(name)
            if valid and tools:
                return offer, tuple(sorted(tools, key=lambda name: (
                    self.catalog.get(name).order, name,
                )))
        return None

    def project(
        self,
        mission: MissionProjection,
        profile: ExecutionProfile,
        state: BrokerRuntimeState,
        grants: Iterable[CapabilityGrant] = (),
    ) -> BrokerProjection:
        if not isinstance(mission, MissionProjection) or not isinstance(profile, ExecutionProfile):
            raise TypeError("Broker requires MissionProjection and ExecutionProfile")
        active_id = self._active_id(mission)
        if profile.mission_id != mission.mission_id or profile.goal_revision != mission.definition.goal_revision:
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=(), grants=(), missing=(),
                reasons=("execution_profile_identity_mismatch",),
            )
        valid_grants = tuple(sorted((
            grant for grant in grants
            if grant.valid_for(
                mission_id=mission.mission_id, run_id=mission.run_id,
                obligation_id=active_id, mission_revision=mission.revision,
                turn_index=state.turn_index,
            )
            and grant.capability in state.available_capabilities
        ), key=lambda item: item.grant_id))

        if mission.status in _TERMINAL or mission.active_obligation_id is None:
            tools = self._named_safe_tools(("blocked",), mission, state)
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=tools,
                grants=valid_grants, missing=(), reasons=("mission_not_actionable",),
            )

        obligation = mission.obligation_definition(active_id)
        required = set(obligation.required_capabilities)
        unknown = required - CANONICAL_CAPABILITIES
        if unknown:
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=(),
                grants=valid_grants, missing=(), reasons=("unknown_required_capability",),
            )
        missing = tuple(sorted(required - set(state.available_capabilities)))
        if missing:
            tools = self._named_safe_tools(("request_capability", "blocked"), mission, state)
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=tools,
                grants=valid_grants, missing=missing,
                reasons=("required_capability_unavailable",),
            )

        if state.pointer.disposition is PointerDisposition.HELD:
            tools = self._named_safe_tools(_POINTER_HELD_TOOLS, mission, state)
            return self._finish(
                mission, state, route=ExecutionRoute.VISUAL_ACTOR, tool_names=tools,
                grants=valid_grants, missing=(), reasons=("pointer_lease_held",),
            )
        if state.pointer.disposition is PointerDisposition.UNKNOWN:
            tools = self._named_safe_tools(_POINTER_UNKNOWN_TOOLS, mission, state)
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=tools,
                grants=valid_grants, missing=(), reasons=("pointer_state_unknown",),
            )
        if not state.runtime_healthy or state.paused or state.canceled:
            tools = self._named_safe_tools(("wait", "blocked"), mission, state)
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=tools,
                grants=valid_grants, missing=(), reasons=("runtime_not_writable",),
            )

        offered = self._offer(required, mission, state)
        if offered is not None:
            offer, tools = offered
            route = (
                ExecutionRoute.DETERMINISTIC
                if offer.route is ToolRoute.DETERMINISTIC else ExecutionRoute.PROVIDER
            )
            return self._finish(
                mission, state, route=route, tool_names=tools, grants=valid_grants,
                missing=(), reasons=("live_executor_offer_selected",),
                executor_id=offer.executor_id,
            )

        capabilities = required | (
            set(profile.accepted_capabilities) & set(state.available_capabilities)
        ) | {
            item.capability for item in valid_grants
        }
        tools = []
        for tool in self.catalog.tools:
            if tool.route is not ToolRoute.VISUAL_ACTOR:
                continue
            if tool.required_any_capabilities and not (
                set(tool.required_any_capabilities) & capabilities
            ):
                continue
            if tool.authorization_scope and tool.authorization_scope not in state.authorization_scopes:
                continue
            if self._prerequisites_met(tool, mission, state):
                tools.append(tool.name)
        if not tools:
            return self._finish(
                mission, state, route=ExecutionRoute.BLOCKED, tool_names=(),
                grants=valid_grants, missing=(), reasons=("no_tool_prerequisites_satisfied",),
            )
        return self._finish(
            mission, state, route=ExecutionRoute.VISUAL_ACTOR, tool_names=tools,
            grants=valid_grants, missing=(), reasons=("visual_actor_surface_projected",),
        )


__all__ = ["CapabilityBroker"]
