"""One-turn, event-sourced vNext Mission Runtime.

The Runtime coordinates typed owners.  It never calls a model directly and it
never performs desktop input itself.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Iterable

from ..actor import (
    ActorDecisionEnvelope,
    BlockedReport,
    CapabilityRequest,
    ClaimObligationDone,
    DesktopActionProposal,
    InspectionRequest,
    InvalidActorProposal,
    ObserveProposal,
    ReplanRequest,
    actor_proposal_to_json,
    parse_actor_proposal,
    project_actor_turn_input,
)
from ..capabilities import (
    BrokerProjection,
    CapabilityBroker,
    CapabilityGrant,
    ExecutionRoute,
    PointerDisposition,
    ToolCatalog,
    ToolArgumentValidationError,
    ToolArgumentValidator,
    ToolRoute,
)
from ..mission import MissionError, MissionReducer
from ..profiling import ExecutionProfile
from .contracts import (
    ControllerOutcome,
    ControllerTransition,
    MissionRuntimeResult,
    RuntimeFrame,
    RuntimeSnapshot,
    RuntimeStatus,
)
from .ports import ActorPort, ControllerPort, PointerCleanupPort, RuntimeStatePort
from .mutation_permits import MutationPermitAuthority, MutationPermitError


_PROPOSAL_TOOL = {
    "observe": "observe",
    "request_capability": "request_capability",
    "request_inspection": "request_inspection",
    "claim_obligation_done": "claim_obligation_done",
    "blocked": "blocked",
    "request_replan": "request_replan",
}


class MissionRuntime:
    """Coordinate exactly one local obligation decision per call."""

    def __init__(
        self,
        *,
        reducer: MissionReducer,
        profile: ExecutionProfile,
        broker: CapabilityBroker,
        catalog: ToolCatalog,
        actor: ActorPort,
        state: RuntimeStatePort,
        controller: ControllerPort,
        pointer_cleanup: PointerCleanupPort | None = None,
        mutation_permits: MutationPermitAuthority | None = None,
    ) -> None:
        if not isinstance(reducer, MissionReducer):
            raise TypeError("reducer must be MissionReducer")
        if not isinstance(profile, ExecutionProfile):
            raise TypeError("profile must be ExecutionProfile")
        if not isinstance(broker, CapabilityBroker) or broker.catalog != catalog:
            raise TypeError("broker and Runtime must share one ToolCatalog")
        self._reducer = reducer
        self._profile = profile
        self._broker = broker
        self._catalog = catalog
        self._argument_validator = ToolArgumentValidator(catalog)
        self._actor = actor
        self._state = state
        self._controller = controller
        self._pointer_cleanup = pointer_cleanup
        self._mutation_permits = mutation_permits or MutationPermitAuthority(
            reducer.definition.mission_id,
            reducer.definition.run_id,
        )
        if (
            self._mutation_permits.mission_id != reducer.definition.mission_id
            or self._mutation_permits.run_id != reducer.definition.run_id
        ):
            raise TypeError("mutation permit authority must match Runtime mission/run")
        self._turn_lock = threading.Lock()

    @property
    def reducer(self) -> MissionReducer:
        return self._reducer

    @property
    def mutation_permits(self) -> MutationPermitAuthority:
        return self._mutation_permits

    @staticmethod
    def _stopped(stop_event: threading.Event | None) -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    @staticmethod
    def _result(
        status: RuntimeStatus,
        *,
        route: ExecutionRoute | None = None,
        proposal_kind: str | None = None,
        decision_id: str | None = None,
        event_ids: Iterable[str] = (),
        detail: str = "",
        pointer_cleanup_attempted: bool = False,
    ) -> MissionRuntimeResult:
        return MissionRuntimeResult(
            status=status,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            accepted_event_ids=tuple(event_ids),
            detail=detail,
            pointer_cleanup_attempted=pointer_cleanup_attempted,
        )

    def _canceled(self, turn_index: int) -> MissionRuntimeResult:
        attempted = False
        try:
            snapshot = self._state.current(
                self._reducer.projection, turn_index=turn_index
            )
            pointer = snapshot.broker_state.pointer
            if (
                pointer.disposition is PointerDisposition.HELD
                and pointer.physically_owned
                and pointer.lease_id
                and self._pointer_cleanup is not None
            ):
                attempted = True
                self._pointer_cleanup.release_pointer(
                    pointer.lease_id, reason="runtime_canceled"
                )
        except Exception:
            # Cancellation must never resume the Actor or replay an action.
            pass
        return self._result(
            RuntimeStatus.CANCELED,
            detail="turn canceled before desktop admission",
            pointer_cleanup_attempted=attempted,
        )

    def _project(
        self,
        snapshot: RuntimeSnapshot,
        grants: tuple[CapabilityGrant, ...],
    ) -> BrokerProjection:
        return self._broker.project(
            self._reducer.projection,
            self._profile,
            snapshot.broker_state,
            grants,
        )

    def _snapshot_matches_mission(self, snapshot: RuntimeSnapshot) -> bool:
        """Compare frozen OS commit identity with accepted Mission identity."""

        binding = self._reducer.projection.target_binding
        target = snapshot.target
        if binding is None:
            return target is None and not snapshot.broker_state.target.live
        if target is None:
            return False
        return bool(
            target.binding_id == binding.binding_id
            and target.hwnd == binding.hwnd
            and target.process_id == binding.process_id
            and target.epoch == binding.epoch
        )

    def _mutation_still_current(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        turn_index: int,
        grants: tuple[CapabilityGrant, ...],
        stop_event: threading.Event | None,
    ) -> bool:
        """Read-only revalidation performed when Controller consumes a permit."""

        if self._stopped(stop_event):
            return False
        projection = self._reducer.projection
        if (
            projection.mission_id != envelope.mission_id
            or projection.run_id != envelope.run_id
            or projection.revision != envelope.mission_revision
            or projection.active_obligation_id != envelope.obligation_id
        ):
            return False
        latest = self._state.current(projection, turn_index=turn_index)
        if (
            latest.broker_state.turn_index != turn_index
            or not self._snapshot_matches_mission(latest)
            or latest.frame is None
            or latest.frame.commit_identity != envelope.frame
            or latest.frame.evidence_refs != envelope.allowed_evidence_scope
            or latest.target != envelope.target
            or latest.broker_state.pointer != envelope.pointer
        ):
            return False
        broker = self._project(latest, grants)
        tool_name = self._proposal_tool(envelope.proposal)
        return bool(
            broker.execution_route is ExecutionRoute.VISUAL_ACTOR
            and broker.projection_id == envelope.broker_projection_id
            and tuple(grant.grant_id for grant in broker.active_grants)
            == envelope.capability_grant_ids
            and self._tool_admitted(tool_name, broker)
        )

    @staticmethod
    def _proposal_tool(proposal: object) -> str:
        if isinstance(proposal, DesktopActionProposal):
            return proposal.action
        return _PROPOSAL_TOOL.get(str(getattr(proposal, "kind", "")), "")

    def _tool_admitted(self, tool_name: str, projection: BrokerProjection) -> bool:
        if not tool_name or tool_name not in projection.tool_names:
            return False
        try:
            spec = self._catalog.get(tool_name)
        except KeyError:
            return False
        return spec.route is ToolRoute.VISUAL_ACTOR

    def _decision_id(
        self,
        *,
        turn_index: int,
        projection: BrokerProjection,
        proposal: object,
    ) -> str:
        material = "|".join((
            self._reducer.definition.mission_id,
            self._reducer.definition.run_id,
            str(self._reducer.projection.revision),
            str(turn_index),
            projection.projection_id,
            actor_proposal_to_json(proposal),  # type: ignore[arg-type]
        ))
        return "decision-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def _envelope(
        self,
        *,
        decision_id: str,
        snapshot: RuntimeSnapshot,
        projection: BrokerProjection,
        proposal: object,
    ) -> ActorDecisionEnvelope:
        frame = snapshot.frame
        if frame is None:
            raise ValueError("Visual Actor decision has no authoritative frame")
        target = snapshot.target
        pointer = snapshot.broker_state.pointer
        if projection.target_binding_id != (
            None if target is None else target.binding_id
        ):
            raise ValueError("Broker and Runtime target identity disagree")
        if projection.pointer_lease_id != pointer.lease_id:
            raise ValueError("Broker and Runtime pointer identity disagree")
        return ActorDecisionEnvelope(
            decision_id=decision_id,
            mission_id=self._reducer.definition.mission_id,
            run_id=self._reducer.definition.run_id,
            mission_revision=self._reducer.projection.revision,
            obligation_id=str(self._reducer.projection.active_obligation_id or ""),
            frame=frame.commit_identity,
            target=target,
            pointer=pointer,
            broker_projection_id=projection.projection_id,
            capability_grant_ids=tuple(
                grant.grant_id for grant in projection.active_grants
            ),
            allowed_evidence_scope=frame.evidence_refs,
            proposal=proposal,  # type: ignore[arg-type]
        )

    def _accept(self, kind: str, *, payload: dict, evidence_refs=()) -> tuple[str, ...]:
        event = self._reducer.event(
            kind, payload=payload, evidence_refs=tuple(evidence_refs)
        )
        self._reducer.apply(event)
        return (event.event_id,)

    def _block_unknown(
        self,
        obligation_id: str,
        *,
        route: ExecutionRoute,
        proposal_kind: str,
        decision_id: str,
        detail: str,
    ) -> MissionRuntimeResult:
        try:
            event_ids = self._accept(
                "obligation_blocked",
                payload={
                    "obligation_id": obligation_id,
                    "blocker_kind": "controller_outcome_unknown",
                    "reason": "desktop mutation outcome is unknown; action was not replayed",
                },
            )
        except MissionError as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail=str(exc),
            )
        return self._result(
            RuntimeStatus.BLOCKED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            event_ids=event_ids,
            detail=detail,
        )

    def _transition_evidence_matches(
        self,
        transition: ControllerTransition,
        *,
        decision_id: str,
        obligation_id: str,
        post_frame: RuntimeFrame,
    ) -> bool:
        """Require current, deterministic evidence for this exact transition."""

        evidence_refs = tuple(getattr(transition, "evidence_refs", ()) or ())
        frame_evidence = tuple(getattr(post_frame, "evidence_refs", ()) or ())
        frame_id = getattr(post_frame, "frame_id", None)
        if not evidence_refs or not set(evidence_refs) <= set(frame_evidence):
            return False
        try:
            refs = self._reducer.evidence_scope.require(
                evidence_refs,
                obligation_id=obligation_id,
            )
        except MissionError:
            return False
        return any(
            ref.obligation_id == obligation_id
            and ref.action_id == decision_id
            and ref.frame_id == frame_id
            and ref.trust == "deterministic"
            for ref in refs
        )

    def run_one_turn(
        self,
        *,
        turn_index: int,
        grants: Iterable[CapabilityGrant] = (),
        stop_event: threading.Event | None = None,
    ) -> MissionRuntimeResult:
        if type(turn_index) is not int or turn_index < 0:
            raise ValueError("turn_index must be a non-negative integer")
        if not self._turn_lock.acquire(blocking=False):
            return self._result(RuntimeStatus.BUSY, detail="another Runtime turn owns admission")
        try:
            return self._run_locked(
                turn_index=turn_index,
                grants=tuple(grants),
                stop_event=stop_event,
            )
        finally:
            self._turn_lock.release()

    def _run_locked(
        self,
        *,
        turn_index: int,
        grants: tuple[CapabilityGrant, ...],
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        try:
            initial = self._state.observe(
                self._reducer.projection,
                turn_index=turn_index,
                image_region=None,
            )
            if initial.broker_state.turn_index != turn_index:
                raise ValueError("Broker state turn_index does not match Runtime turn")
            if not self._snapshot_matches_mission(initial):
                raise ValueError("Runtime target identity does not match accepted Mission state")
            broker_projection = self._project(initial, grants)
        except Exception as exc:
            return self._result(RuntimeStatus.STATE_REJECTED, detail=str(exc))
        route = broker_projection.execution_route
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        if route is not ExecutionRoute.VISUAL_ACTOR:
            status = (
                RuntimeStatus.ROUTE_DEFERRED
                if route in {ExecutionRoute.PROVIDER, ExecutionRoute.DETERMINISTIC}
                else RuntimeStatus.UNAVAILABLE
            )
            return self._result(
                status,
                route=route,
                detail="Broker route is not owned by Visual Actor",
            )
        if initial.frame is None:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                detail="Visual Actor route has no frame payload",
            )
        try:
            turn = project_actor_turn_input(
                self._reducer.card,
                initial.broker_state.frame,
                self._catalog,
                broker_projection,
            )
            raw = self._actor.propose(
                turn,
                image_b64=initial.frame.image_b64,
                submitted_size=initial.frame.submitted_size,
                stop_event=stop_event,
            )
        except Exception as exc:
            return self._result(RuntimeStatus.ACTOR_ERROR, route=route, detail=str(exc))
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        try:
            proposal = parse_actor_proposal(raw)
        except InvalidActorProposal as exc:
            return self._result(RuntimeStatus.INVALID_PROPOSAL, route=route, detail=str(exc))

        proposal_kind = proposal.kind
        try:
            current_projection = self._reducer.projection
            latest = self._state.current(current_projection, turn_index=turn_index)
            latest_matches_mission = self._snapshot_matches_mission(latest)
            latest_broker = self._project(latest, grants)
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                detail=str(exc),
            )
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        if (
            current_projection.revision != broker_projection.mission_revision
            or current_projection.active_obligation_id != broker_projection.obligation_id
            or not latest_matches_mission
            or latest.authority_key != initial.authority_key
            or latest_broker != broker_projection
            or latest_broker.execution_route is not ExecutionRoute.VISUAL_ACTOR
        ):
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                route=route,
                proposal_kind=proposal_kind,
                detail="Runtime authority changed while Actor was deciding",
            )
        tool_name = self._proposal_tool(proposal)
        if not self._tool_admitted(tool_name, latest_broker):
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                route=route,
                proposal_kind=proposal_kind,
                detail="proposal tool is outside the latest Broker projection",
            )
        if isinstance(proposal, DesktopActionProposal):
            try:
                self._argument_validator.validate(tool_name, proposal.args)
            except ToolArgumentValidationError as exc:
                return self._result(
                    RuntimeStatus.INVALID_PROPOSAL,
                    route=route,
                    proposal_kind=proposal_kind,
                    detail=str(exc),
                )
        try:
            decision_id = self._decision_id(
                turn_index=turn_index,
                projection=latest_broker,
                proposal=proposal,
            )
            envelope = self._envelope(
                decision_id=decision_id,
                snapshot=latest,
                projection=latest_broker,
                proposal=proposal,
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                detail=str(exc),
            )
        obligation_id = envelope.obligation_id

        if isinstance(proposal, ObserveProposal):
            region = proposal.image_region
            if region is not None and (
                region.x + region.width > latest.frame.submitted_size[0]
                or region.y + region.height > latest.frame.submitted_size[1]
            ):
                return self._result(
                    RuntimeStatus.INVALID_PROPOSAL,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    detail="observation region is outside the submitted image",
                )
            try:
                self._state.observe(
                    self._reducer.projection,
                    turn_index=turn_index,
                    image_region=region,
                )
            except Exception as exc:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    detail=str(exc),
                )
            return self._result(
                RuntimeStatus.OBSERVED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
            )

        if isinstance(proposal, ClaimObligationDone):
            if not latest.frame.evidence_refs:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    detail="claim requires current run-scoped frame evidence",
                )
            try:
                event_ids = self._accept(
                    "obligation_claimed",
                    payload={"obligation_id": obligation_id},
                    evidence_refs=latest.frame.evidence_refs,
                )
            except MissionError as exc:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    detail=str(exc),
                )
            return self._result(
                RuntimeStatus.VERIFICATION_REQUIRED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=event_ids,
            )

        if isinstance(proposal, BlockedReport):
            reason = "; ".join(proposal.observed_facts) or proposal.blocker_kind
            try:
                event_ids = self._accept(
                    "obligation_blocked",
                    payload={
                        "obligation_id": obligation_id,
                        "blocker_kind": proposal.blocker_kind,
                        "reason": reason,
                    },
                )
            except MissionError as exc:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    detail=str(exc),
                )
            return self._result(
                RuntimeStatus.BLOCKED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=event_ids,
            )

        if isinstance(proposal, (CapabilityRequest, InspectionRequest, ReplanRequest)):
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail="requested read-only service is not enabled in P4a",
            )

        if not isinstance(proposal, DesktopActionProposal):
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
            )
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        try:
            self._mutation_permits.issue(
                envelope,
                turn_index=turn_index,
                still_current=lambda: self._mutation_still_current(
                    envelope,
                    turn_index=turn_index,
                    grants=grants,
                    stop_event=stop_event,
                ),
            )
        except MutationPermitError as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail=str(exc),
            )
        permit_was_pending = False
        try:
            transition = self._controller.execute(envelope, stop_event=stop_event)
        except Exception as exc:
            self._mutation_permits.revoke(decision_id)
            return self._block_unknown(
                obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail=f"Controller raised after admission: {exc}",
            )
        else:
            permit_was_pending = self._mutation_permits.revoke(decision_id)
        if permit_was_pending and transition.outcome is not ControllerOutcome.REJECTED:
            return self._block_unknown(
                obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail="Controller returned a mutation outcome without consuming Runtime permit",
            )
        if (
            transition.action_id != decision_id
            or transition.action != proposal.action
            or transition.outcome is ControllerOutcome.UNKNOWN
        ):
            return self._block_unknown(
                obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail="Controller could not prove the mutation outcome",
            )
        try:
            post = self._state.current(self._reducer.projection, turn_index=turn_index)
            if post.frame is None or post.frame.frame_id != transition.post_frame_id:
                raise ValueError("Controller post frame is not current authority")
            if not self._transition_evidence_matches(
                transition,
                decision_id=decision_id,
                obligation_id=obligation_id,
                post_frame=post.frame,
            ):
                raise ValueError("Controller transition evidence identity is not current")
            event_ids = self._accept(
                "transition_recorded",
                payload={
                    "obligation_id": obligation_id,
                    "action": proposal.action,
                    "input_completed": transition.input_completed,
                    "visible_effect": transition.visible_effect,
                    "evidence_refs": list(transition.evidence_refs),
                    "frame_id": transition.post_frame_id,
                },
                evidence_refs=transition.evidence_refs,
            )
        except (MissionError, ValueError) as exc:
            return self._block_unknown(
                obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail=f"Controller result could not be reconciled: {exc}",
            )
        return self._result(
            RuntimeStatus.ACTION_RECORDED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            event_ids=event_ids,
        )


__all__ = ["MissionRuntime"]
