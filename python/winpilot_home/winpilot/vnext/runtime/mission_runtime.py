"""One-turn, event-sourced minimal Mission Runtime.

The Runtime coordinates injected owners.  It does not call a model SDK,
capture Windows pixels, perform desktop input, discover targets, or own a
second completion/evidence ledger.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Iterable, Mapping

from ...utils.log import logger
from ..actor import (
    ActorDecisionEnvelope,
    BlockedReport,
    ClaimObligationDone,
    DesktopActionProposal,
    InvalidActorProposal,
    bind_actor_decision,
    parse_actor_proposal,
    proposal_tool_name,
)
from ..capabilities import (
    BrokerProjection,
    CapabilityBroker,
    ExecutionRoute,
    MutationKind,
    ToolArgumentValidationError,
    ToolArgumentValidator,
    ToolCatalog,
    ToolRoute,
)
from ..evidence import EvidenceRegistry
from ..evidence_plane import (
    ActionExecutionBinding,
    RuntimeEvidencePlane,
)
from ..mission import (
    EvidenceRef,
    MissionError,
    MissionReducer,
    MissionStatus,
    ObligationStatus,
    StaleRevisionError,
    is_safe_action_summary,
)
from ..profiling import ExecutionDepth, ExecutionProfile
from ..verification import (
    AcceptedObligationVerification,
    DeterministicMissionCompletionVerifier,
    MissionCompletionVerificationRequest,
    MissionCompletionVerificationResult,
    ObligationVerificationRequest,
    ObligationVerificationResult,
    VerifierAssessment,
)
from .contracts import (
    APPROVED_DESKTOP_ACTIONS,
    APPROVED_RUNTIME_TOOLS,
    CURRENT_FRAME_EVIDENCE_KIND,
    MAX_COMPLETED_ACTION_HISTORY,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    ControllerOutcome,
    ControllerTransition,
    MissionRuntimeResult,
    RuntimeSnapshot,
    RuntimeStatus,
    RuntimeTargetIdentity,
    project_actor_turn_input,
)
from .mutation_attempt import call_once_with_permit
from .mutation_permits import (
    MutationPermitAuthority,
    MutationPermitError,
)
from .ports import (
    ActorPort,
    ControllerPort,
    RuntimeStatePort,
)
from .post_action_reconcile import (
    reconcile_post_action,
    validate_controller_evidence_scope,
)
from .task_state import (
    ActorTaskState,
    ActorTaskStateError,
    ActorTurnDecision,
)

_RECOVERABLE_NOT_STARTED_ERROR_KINDS = frozenset(
    {
        "desktop_queue_canceled",
        "frame_changed_after_queue",
        "frame_unavailable",
        "hit_test_changed",
        "hit_test_changed_after_queue",
        "permit_commit_identity_changed",
        "permit_runtime_state_changed",
        "permit_runtime_state_unavailable",
        "permit_changed_after_queue",
        "point_outside_target_after_queue",
        "stale_authoritative_frame",
        "target_changed_after_queue",
        "target_identity_changed",
        "target_unavailable",
    }
)


class MissionRuntimeConfigurationError(ValueError):
    """The injected slice is outside the authorized minimal canary."""


class MissionRuntime:
    """Coordinate exactly one bounded Actor decision per call."""

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
        evidence_registry: EvidenceRegistry,
        mutation_permits: MutationPermitAuthority | None = None,
        runtime_evidence_plane: RuntimeEvidencePlane | None = None,
    ) -> None:
        if type(reducer) is not MissionReducer:
            raise TypeError("reducer must be MissionReducer")
        if type(profile) is not ExecutionProfile:
            raise TypeError("profile must be ExecutionProfile")
        if type(broker) is not CapabilityBroker:
            raise TypeError("broker must be CapabilityBroker")
        if type(catalog) is not ToolCatalog or broker.catalog != catalog:
            raise TypeError(
                "Runtime and Broker must share one ToolCatalog"
            )
        if type(evidence_registry) is not EvidenceRegistry:
            raise TypeError(
                "evidence_registry must be EvidenceRegistry"
            )
        if reducer.evidence_source is not evidence_registry:
            raise TypeError(
                "Runtime and MissionReducer must share one EvidenceRegistry"
            )
        if (
            runtime_evidence_plane is not None
            and type(runtime_evidence_plane) is not RuntimeEvidencePlane
        ):
            raise TypeError(
                "runtime_evidence_plane must be RuntimeEvidencePlane or null"
            )
        for value, method, name in (
            (actor, "propose", "actor"),
            (state, "observe", "state"),
            (state, "current", "state"),
            (controller, "execute", "controller"),
        ):
            if not callable(getattr(value, method, None)):
                raise TypeError(f"{name} must provide {method}()")
        self._validate_minimal_scope(
            reducer=reducer,
            profile=profile,
            catalog=catalog,
            registry=evidence_registry,
        )
        permits = mutation_permits or MutationPermitAuthority(
            reducer.definition.mission_id,
            reducer.definition.run_id,
        )
        if type(permits) is not MutationPermitAuthority:
            raise TypeError(
                "mutation_permits must be MutationPermitAuthority"
            )
        if (
            permits.mission_id != reducer.definition.mission_id
            or permits.run_id != reducer.definition.run_id
        ):
            raise TypeError(
                "mutation permit scope must match mission/run"
            )
        if runtime_evidence_plane is not None and (
            runtime_evidence_plane.mission_id
            != reducer.definition.mission_id
            or runtime_evidence_plane.run_id
            != reducer.definition.run_id
        ):
            raise TypeError(
                "Runtime evidence plane scope must match mission/run"
            )
        if (
            getattr(controller, "runtime_evidence_plane", None)
            is not runtime_evidence_plane
        ):
            raise TypeError(
                "Runtime and Controller must share one evidence plane"
            )
        self._reducer = reducer
        self._profile = profile
        self._broker = broker
        self._catalog = catalog
        self._argument_validator = ToolArgumentValidator(catalog)
        self._actor = actor
        self._state = state
        self._controller = controller
        self._registry = evidence_registry
        self._mutation_permits = permits
        self._runtime_evidence = runtime_evidence_plane
        self._verified_completion_result: str | None = None
        self._previous_action_local_intent: str | None = None
        self._task_state = ActorTaskState.initial()
        self._turn_lock = threading.Lock()

    @staticmethod
    def _validate_minimal_scope(
        *,
        reducer: MissionReducer,
        profile: ExecutionProfile,
        catalog: ToolCatalog,
        registry: EvidenceRegistry,
    ) -> None:
        definition = reducer.definition
        if (
            registry.mission_id != definition.mission_id
            or registry.run_id != definition.run_id
        ):
            raise MissionRuntimeConfigurationError(
                "EvidenceRegistry is outside mission/run"
            )
        if len(definition.obligations) != 1:
            raise MissionRuntimeConfigurationError(
                "minimal Runtime requires one full-goal obligation"
            )
        obligation = definition.obligations[0]
        if (
            not obligation.required
            or obligation.desired_state != definition.original_goal
            or obligation.depends_on
            or obligation.input_artifact_refs
            or obligation.output_binding_names
            or obligation.required_capabilities != ("visual.interact",)
        ):
            raise MissionRuntimeConfigurationError(
                "obligation is not the single visual full-goal contract"
            )
        if obligation.evidence_policy.allow_independent_visual_verifier:
            raise MissionRuntimeConfigurationError(
                "live Actor claim must not enable a second visual verifier"
            )
        services = profile.services
        if (
            profile.mission_id != definition.mission_id
            or profile.goal_revision != definition.goal_revision
            or profile.accepted_depth is not ExecutionDepth.STANDARD
            or profile.source != "safe_default"
            or profile.accepted_capabilities != ("visual.interact",)
            or profile.upgrade_triggers
            or profile.downgrade_conditions
            or services.planner
            or services.obligation_verifier
            or services.mission_verifier
            or services.visual_inspector
            or services.deep_reasoning
        ):
            raise MissionRuntimeConfigurationError(
                "Runtime requires the fixed safe STANDARD profile"
            )
        names = frozenset(tool.name for tool in catalog.tools)
        if names != APPROVED_RUNTIME_TOOLS:
            raise MissionRuntimeConfigurationError(
                "ToolCatalog must contain exactly the minimal canary surface"
            )
        for tool in catalog.tools:
            if tool.route is not ToolRoute.VISUAL_ACTOR:
                raise MissionRuntimeConfigurationError(
                    "minimal tools must be Visual Actor owned"
                )
            expected = (
                MutationKind.GUI
                if tool.name in APPROVED_DESKTOP_ACTIONS
                else MutationKind.NONE
            )
            if tool.mutation_kind is not expected:
                raise MissionRuntimeConfigurationError(
                    f"invalid mutation contract for {tool.name!r}"
                )
        binding = reducer.projection.target_binding
        if binding is None:
            raise MissionRuntimeConfigurationError(
                "minimal Runtime requires a pre-bound target"
            )
        RuntimeTargetIdentity.from_binding(binding)

    @property
    def reducer(self) -> MissionReducer:
        return self._reducer

    @property
    def mutation_permits(self) -> MutationPermitAuthority:
        return self._mutation_permits

    @property
    def verified_completion_result(self) -> str | None:
        """Return Actor-authored result text only after mission verification."""

        if self._reducer.projection.status is not MissionStatus.VERIFIED:
            return None
        return self._verified_completion_result

    @property
    def task_state(self) -> ActorTaskState:
        """Return the current in-memory, non-authoritative Actor notes."""

        return self._task_state

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
    ) -> MissionRuntimeResult:
        return MissionRuntimeResult(
            status=status,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            accepted_event_ids=tuple(event_ids),
            detail=detail,
        )

    def _accept(
        self,
        kind: str,
        *,
        payload: Mapping[str, Any],
        base_revision: int,
        evidence_refs: Iterable[str] = (),
    ) -> tuple[str, ...]:
        event = self._reducer.event(
            kind,
            payload=dict(payload),
            base_revision=base_revision,
            evidence_refs=tuple(evidence_refs),
        )
        self._reducer.apply(event)
        return (event.event_id,)

    def _cancel(
        self,
        *,
        prior_event_ids: Iterable[str] = (),
        detail: str = "run cancellation was requested",
    ) -> MissionRuntimeResult:
        projection = self._reducer.projection
        if projection.status is MissionStatus.CANCELED:
            return self._result(
                RuntimeStatus.CANCELED,
                event_ids=prior_event_ids,
                detail=detail,
            )
        if projection.status in {
            MissionStatus.VERIFIED,
            MissionStatus.FAILED,
        }:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                event_ids=prior_event_ids,
                detail=f"cannot cancel terminal mission: {projection.status.value}",
            )
        try:
            event_ids = self._accept(
                "mission_canceled",
                payload={"reason": detail},
                base_revision=projection.revision,
            )
        except MissionError as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                event_ids=prior_event_ids,
                detail=f"cancellation was not accepted: {exc}",
            )
        return self._result(
            RuntimeStatus.CANCELED,
            event_ids=(*tuple(prior_event_ids), *event_ids),
            detail=detail,
        )

    def _snapshot_matches_mission(
        self,
        snapshot: RuntimeSnapshot,
        *,
        turn_index: int,
    ) -> bool:
        projection = self._reducer.projection
        binding = projection.target_binding
        if binding is None:
            return False
        if snapshot.broker_state.turn_index != turn_index:
            return False
        if snapshot.target != RuntimeTargetIdentity.from_binding(binding):
            return False
        if (
            snapshot.frame.hwnd != binding.hwnd
            or snapshot.frame.epoch != binding.epoch
            or snapshot.frame.scope != "target_window"
        ):
            return False
        registry = {ref.id: ref for ref in self._registry.snapshot()}
        if not snapshot.frame.evidence_refs or any(
            evidence_id not in registry
            for evidence_id in snapshot.frame.evidence_refs
        ):
            return False
        payload_hash = snapshot.frame.identity.payload_hash
        return all(
            registry[evidence_id].mission_id == projection.mission_id
            and registry[evidence_id].run_id == projection.run_id
            and registry[evidence_id].obligation_id
            == projection.active_obligation_id
            and registry[evidence_id].kind
            in {
                CURRENT_FRAME_EVIDENCE_KIND,
                POST_ACTION_FRAME_EVIDENCE_KIND,
            }
            and registry[evidence_id].trust == "deterministic"
            and registry[evidence_id].frame_id == snapshot.frame.frame_id
            and registry[evidence_id].payload_hash == payload_hash
            for evidence_id in snapshot.frame.evidence_refs
        )

    def _project(self, snapshot: RuntimeSnapshot) -> BrokerProjection:
        return self._broker.project(
            self._reducer.projection,
            self._profile,
            snapshot.broker_state,
        )

    def _latest_matches(
        self,
        initial: RuntimeSnapshot,
        broker_projection: BrokerProjection,
        *,
        turn_index: int,
    ) -> bool:
        projection = self._reducer.projection
        if (
            projection.revision != broker_projection.mission_revision
            or projection.active_obligation_id
            != broker_projection.obligation_id
        ):
            return False
        latest = self._state.current(
            projection,
            turn_index=turn_index,
        )
        if (
            not self._snapshot_matches_mission(
                latest,
                turn_index=turn_index,
            )
            or latest.authority_key != initial.authority_key
        ):
            return False
        return self._project(latest) == broker_projection

    def _mutation_still_current(
        self,
        envelope: ActorDecisionEnvelope,
        initial: RuntimeSnapshot,
        broker_projection: BrokerProjection,
        *,
        turn_index: int,
        stop_event: threading.Event | None,
    ) -> bool:
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
        try:
            return self._latest_matches(
                initial,
                broker_projection,
                turn_index=turn_index,
            )
        except Exception:
            return False

    def _block_unknown(
        self,
        *,
        obligation_id: str,
        route: ExecutionRoute,
        proposal_kind: str,
        decision_id: str,
        detail: str,
    ) -> MissionRuntimeResult:
        self._mutation_permits.close()
        event_ids: tuple[str, ...] = ()
        block_error: MissionError | None = None
        for _ in range(3):
            projection = self._reducer.projection
            if projection.active_obligation_id != obligation_id:
                break
            try:
                event_ids = self._accept(
                    "obligation_blocked",
                    payload={
                        "obligation_id": obligation_id,
                        "blocker_kind": "controller_outcome_unknown",
                        "reason": (
                            "desktop mutation outcome is unknown; "
                            "the action was not replayed"
                        ),
                    },
                    base_revision=projection.revision,
                )
                block_error = None
                break
            except MissionError as exc:
                block_error = exc
        if block_error is not None:
            detail = (
                f"{detail}; mutation authority is closed; "
                f"block was not accepted: {block_error}"
            )
        return self._result(
            RuntimeStatus.BLOCKED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            event_ids=event_ids,
            detail=detail,
        )

    def _verification_request(
        self,
        obligation_id: str,
    ) -> ObligationVerificationRequest:
        projection = self._reducer.projection
        state = projection.obligation_state(obligation_id)
        if (
            projection.active_obligation_id != obligation_id
            or state.status is not ObligationStatus.CLAIMED
            or not state.claim_fingerprint
        ):
            raise ValueError(
                "visual verification requires the current accepted claim"
            )
        definition = projection.obligation_definition(obligation_id)
        refs = self._reducer.evidence_scope.require(
            state.claim_evidence_refs,
            obligation_id=obligation_id,
        )
        return ObligationVerificationRequest(
            mission_id=projection.mission_id,
            run_id=projection.run_id,
            mission_revision=projection.revision,
            obligation_id=obligation_id,
            claim_fingerprint=state.claim_fingerprint,
            desired_state=definition.desired_state,
            acceptance_criteria=definition.acceptance_criteria,
            forbidden_effects=definition.forbidden_effects,
            evidence_policy=definition.evidence_policy,
            evidence_refs=refs,
        )

    def _completed_action_history(
        self,
        obligation_id: str,
    ) -> tuple[str, ...]:
        summaries: list[str] = []
        for event in self._reducer.events:
            if event.kind != "transition_recorded":
                continue
            payload = event.payload
            action = payload.get("action")
            summary = payload.get("action_summary")
            if summary is None:
                summary = action
            if (
                payload.get("obligation_id") == obligation_id
                and payload.get("input_completed") is True
                and type(action) is str
                and type(summary) is str
                and is_safe_action_summary(action, summary)
            ):
                summaries.append(summary)
        return tuple(summaries[-MAX_COMPLETED_ACTION_HISTORY:])

    def _safe_action_summary(
        self,
        proposal: DesktopActionProposal,
    ) -> str:
        """Project useful action semantics without text, coordinates, or intent."""

        action = proposal.action
        args = dict(proposal.args)
        if action == "scroll":
            amount = args.get("amount")
            if type(amount) is int:
                if amount < 0:
                    return "scroll_down"
                if amount > 0:
                    return "scroll_up"
        if action == "hotkey":
            keys = args.get("keys")
            if isinstance(keys, (list, tuple)):
                summary = "hotkey_" + "_".join(keys)
                if is_safe_action_summary(action, summary):
                    return summary
        return action

    def _complete_mission(
        self,
        *,
        route: ExecutionRoute,
        proposal_kind: str,
        decision_id: str,
        prior_event_ids: Iterable[str],
    ) -> MissionRuntimeResult:
        projection = self._reducer.projection
        if projection.status is not MissionStatus.AWAITING_MISSION_VERIFICATION:
            return self._result(
                RuntimeStatus.VERIFICATION_RECORDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=prior_event_ids,
            )
        obligation = self._reducer.definition.obligations[0]
        state = projection.obligation_state(obligation.id)
        if (
            state.status is not ObligationStatus.VERIFIED
            or not state.verification_id
            or not state.verified_evidence_refs
        ):
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=prior_event_ids,
                detail="verified obligation lacks exact accepted evidence",
            )
        try:
            refs = self._reducer.evidence_scope.require(
                state.verified_evidence_refs,
                obligation_id=obligation.id,
            )
            request = MissionCompletionVerificationRequest(
                mission_id=projection.mission_id,
                run_id=projection.run_id,
                mission_revision=projection.revision,
                required_obligations=(
                    AcceptedObligationVerification(
                        obligation_id=obligation.id,
                        accepted_verification_id=state.verification_id,
                        verified_evidence_refs=state.verified_evidence_refs,
                        evidence_refs=refs,
                    ),
                ),
            )
            verifier = DeterministicMissionCompletionVerifier()
            result = MissionCompletionVerificationResult.bind(
                request,
                verifier.verify(request),
            )
            self._registry.append(result.aggregate_evidence)
            event_ids = self._accept(
                "mission_verified",
                payload=result.to_mission_event_payload(),
                evidence_refs=result.event_evidence_refs,
                base_revision=request.mission_revision,
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=prior_event_ids,
                detail=f"mission completion failed closed: {exc}",
            )
        if self._reducer.projection.status is not MissionStatus.VERIFIED:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=(*tuple(prior_event_ids), *event_ids),
                detail="Reducer did not derive mission verification",
            )
        return self._result(
            RuntimeStatus.MISSION_VERIFIED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            event_ids=(*tuple(prior_event_ids), *event_ids),
        )

    def _claim_and_complete(
        self,
        proposal: ClaimObligationDone,
        envelope: ActorDecisionEnvelope,
        frozen: RuntimeSnapshot,
        *,
        route: ExecutionRoute,
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        obligation_id = envelope.obligation_id
        proposal_kind = "claim_obligation_done"
        if not frozen.frame.evidence_refs:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail="claim requires current run-scoped frame evidence",
            )
        try:
            claim_ids = self._accept(
                "obligation_claimed",
                payload={"obligation_id": obligation_id},
                evidence_refs=frozen.frame.evidence_refs,
                base_revision=envelope.mission_revision,
            )
            request = self._verification_request(obligation_id)
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=f"claim was not accepted: {exc}",
            )
        if self._stopped(stop_event):
            return self._cancel(
                prior_event_ids=claim_ids,
                detail="run was canceled before claim completion",
            )
        try:
            assessment = VerifierAssessment(
                verified=True,
                satisfied_conditions=request.required_conditions,
                missing_conditions=(),
                contradictions=(),
                confidence=None,
                concise_reason=proposal.concise_claim,
            )
            result = ObligationVerificationResult.bind(
                request,
                assessment,
                verifier_kind="actor_claim",
                evidence_refs=tuple(
                    ref.id for ref in request.trusted_evidence_refs
                ),
            )
            verification_payload = result.to_mission_event_payload(
                request
            )
            verification_ids = self._accept(
                "verification_recorded",
                payload=verification_payload,
                evidence_refs=result.event_evidence_refs,
                base_revision=request.mission_revision,
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                event_ids=claim_ids,
                detail=f"actor claim completion failed closed: {exc}",
            )
        event_ids = (*claim_ids, *verification_ids)
        completion = self._complete_mission(
            route=route,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            prior_event_ids=event_ids,
        )
        if completion.status is RuntimeStatus.MISSION_VERIFIED:
            self._verified_completion_result = proposal.concise_claim
        return completion

    @staticmethod
    def _actor_envelope_sha256(
        envelope: ActorDecisionEnvelope,
    ) -> str:
        return hashlib.sha256(
            envelope.to_json().encode("utf-8")
        ).hexdigest()

    def _record_actor_decision(
        self,
        proposal: DesktopActionProposal,
        envelope: ActorDecisionEnvelope,
        broker_projection: BrokerProjection,
    ) -> None:
        plane = self._runtime_evidence
        if plane is None:
            return
        canonical_envelope = ActorDecisionEnvelope.from_dict(
            envelope.to_dict()
        )
        canonical_projection = BrokerProjection.from_dict(
            broker_projection.to_dict()
        )
        canonical_proposal = canonical_envelope.proposal
        if type(canonical_proposal) is not DesktopActionProposal:
            raise TypeError("evidence requires the bound desktop proposal")
        tool_wire = self._catalog.get(canonical_proposal.action).to_dict()
        plane.record_actor_decision(
            actor_envelope=canonical_envelope.to_dict(),
            tool_schema=tool_wire["schema"],
            broker_projection=canonical_projection.to_dict(),
        )

    def _record_controller_transition(
        self,
        envelope: ActorDecisionEnvelope,
        transition: ControllerTransition,
        *,
        post_frame_identity: Mapping[str, Any] | None = None,
    ) -> ActionExecutionBinding | None:
        plane = self._runtime_evidence
        if plane is None:
            return None
        return plane.record_controller_transition(
            decision_id=envelope.decision_id,
            mission_id=envelope.mission_id,
            run_id=envelope.run_id,
            actor_envelope_sha256=self._actor_envelope_sha256(envelope),
            transition=transition.to_dict(),
            post_frame_identity=(
                None
                if post_frame_identity is None
                else dict(post_frame_identity)
            ),
        )

    def _run_action(
        self,
        proposal: DesktopActionProposal,
        envelope: ActorDecisionEnvelope,
        initial: RuntimeSnapshot,
        broker_projection: BrokerProjection,
        *,
        route: ExecutionRoute,
        turn_index: int,
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        proposal_kind = proposal.kind
        obligation_id = envelope.obligation_id
        try:
            attempt = call_once_with_permit(
                authority=self._mutation_permits,
                envelope=envelope,
                turn_index=turn_index,
                commit_identity=initial.commit_identity,
                still_current=lambda: self._mutation_still_current(
                    envelope,
                    initial,
                    broker_projection,
                    turn_index=turn_index,
                    stop_event=stop_event,
                ),
                execute=lambda admitted: self._controller.execute(
                    admitted,
                    stop_event=stop_event,
                ),
            )
        except MutationPermitError as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=str(exc),
            )
        if attempt.execute_call_count != 1:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail="Controller attempt was not call-once",
            )
        if attempt.controller_error is not None:
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller raised after mutation admission: "
                    f"{attempt.controller_error}"
                ),
            )
        transition = attempt.transition
        if type(transition) is not ControllerTransition:
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail="Controller returned an invalid typed transition",
            )
        identity_matches = bool(
            transition.action_id == envelope.decision_id
            and transition.action == proposal.action
        )
        if (
            identity_matches
            and transition.outcome is ControllerOutcome.REJECTED
            and transition.input_completed is False
            and self._stopped(stop_event)
        ):
            return self._cancel(
                detail="run was canceled before input executed"
            )
        if identity_matches and transition.outcome in {
            ControllerOutcome.REJECTED,
            ControllerOutcome.UNKNOWN,
        }:
            rejection_evidence_error: Exception | None = None
            try:
                action_binding = self._record_controller_transition(
                    envelope,
                    transition,
                )
            except Exception as exc:
                if transition.input_completed is False:
                    # No input reached the OS, so a diagnostic journal failure
                    # must not turn a harmless stale proposal into a terminal
                    # mission failure.  The next turn can safely observe a
                    # fresh frame and decide again.  Executed or unknown input
                    # still takes the fail-closed path below.
                    action_binding = None
                    rejection_evidence_error = exc
                else:
                    return self._block_unknown(
                        obligation_id=obligation_id,
                        route=route,
                        proposal_kind=proposal_kind,
                        decision_id=envelope.decision_id,
                        detail=(
                            "Controller outcome evidence failed closed after "
                            f"admission: {exc}"
                        ),
                    )
        if (
            transition.outcome is not ControllerOutcome.REJECTED
            and (
                attempt.pending_permit_revoked
                or not attempt.active_admission_closed
            )
        ):
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller reported mutation without consuming Runtime permit"
                ),
            )
        if not identity_matches:
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail="Controller could not prove the mutation outcome",
            )
        if transition.outcome is ControllerOutcome.UNKNOWN:
            if transition.input_completed is not True:
                return self._block_unknown(
                    obligation_id=obligation_id,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail="Controller could not prove the mutation outcome",
                )
            try:
                event_ids = self._accept(
                    "transition_recorded",
                    payload={
                        "obligation_id": obligation_id,
                        "action_id": envelope.decision_id,
                        "action": proposal.action,
                        "action_summary": self._safe_action_summary(
                            proposal
                        ),
                        "input_completed": True,
                    },
                    base_revision=envelope.mission_revision,
                )
            except Exception as exc:
                return self._block_unknown(
                    obligation_id=obligation_id,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=(
                        "Completed input could not be recorded before a fresh "
                        f"observation: {exc}"
                    ),
                )
            self._previous_action_local_intent = proposal.local_intent
            if self._stopped(stop_event):
                return self._cancel(
                    prior_event_ids=event_ids,
                    detail="run was canceled after the admitted action",
                )
            return self._result(
                RuntimeStatus.ACTION_RECORDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                event_ids=event_ids,
                detail=(
                    "OS input completed; its effect will be recovered from "
                    "the next fresh observation"
                ),
            )
        if transition.outcome is ControllerOutcome.REJECTED:
            if self._stopped(stop_event):
                return self._cancel(
                    detail="run was canceled before input executed"
                )
            if action_binding is not None:
                error_kind = action_binding.raw_action_receipt[
                    "error_kind"
                ]
                if (
                    error_kind
                    not in _RECOVERABLE_NOT_STARTED_ERROR_KINDS
                ):
                    return self._result(
                        RuntimeStatus.STATE_REJECTED,
                        route=route,
                        proposal_kind=proposal_kind,
                        decision_id=envelope.decision_id,
                        detail=(
                            "Controller rejected input with a "
                            "non-recoverable precondition"
                        ),
                    )
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller proved input did not execute; no replay occurred"
                    + (
                        "; rejection diagnostic evidence was unavailable: "
                        f"{rejection_evidence_error}"
                        if rejection_evidence_error is not None
                        else ""
                    )
                ),
            )
        try:
            validate_controller_evidence_scope(
                transition,
                self._registry,
                mission_id=self._reducer.definition.mission_id,
                run_id=self._reducer.definition.run_id,
                obligation_id=obligation_id,
                action_id=envelope.decision_id,
            )
            self._registry.append_many(transition.produced_evidence)
            after = self._state.current(
                self._reducer.projection,
                turn_index=turn_index,
            )
            reconcile_post_action(
                transition,
                initial,
                after,
                self._registry,
                mission_id=self._reducer.definition.mission_id,
                run_id=self._reducer.definition.run_id,
                obligation_id=obligation_id,
                action_id=envelope.decision_id,
                action=proposal.action,
                keep_holding=proposal.model_dump(mode="json")[
                    "args"
                ].get("keep_holding", False),
            )
            self._record_controller_transition(
                envelope,
                transition,
                post_frame_identity=after.frame.identity.to_dict(),
            )
            event_ids = self._accept(
                "transition_recorded",
                payload={
                    "obligation_id": obligation_id,
                    "action_id": envelope.decision_id,
                    "action": proposal.action,
                    "action_summary": self._safe_action_summary(
                        proposal
                    ),
                    "input_completed": True,
                    "visible_effect": transition.visible_effect,
                    "frame_id": transition.post_frame_id,
                    **(
                        {}
                        if transition.action_effect is None
                        else {
                            "action_effect": (
                                transition.action_effect.to_dict()
                            )
                        }
                    ),
                },
                evidence_refs=transition.event_evidence_refs,
                base_revision=envelope.mission_revision,
            )
        except Exception as exc:
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=f"Controller result could not be reconciled: {exc}",
            )
        self._previous_action_local_intent = proposal.local_intent
        if self._stopped(stop_event):
            return self._cancel(
                prior_event_ids=event_ids,
                detail="run was canceled after the admitted action",
            )
        return self._result(
            RuntimeStatus.ACTION_RECORDED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            event_ids=event_ids,
        )

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: threading.Event | None = None,
    ) -> MissionRuntimeResult:
        if type(turn_index) is not int or turn_index < 0:
            raise ValueError(
                "turn_index must be a non-negative integer"
            )
        if not self._turn_lock.acquire(blocking=False):
            return self._result(
                RuntimeStatus.BUSY,
                detail="another Runtime turn owns admission",
            )
        try:
            return self._run_locked(
                turn_index=turn_index,
                stop_event=stop_event,
            )
        finally:
            self._turn_lock.release()

    def _run_locked(
        self,
        *,
        turn_index: int,
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        projection = self._reducer.projection
        if projection.status is MissionStatus.VERIFIED:
            return self._result(
                RuntimeStatus.MISSION_VERIFIED,
                detail="mission was already verified",
            )
        if projection.status is MissionStatus.CANCELED:
            return self._result(
                RuntimeStatus.CANCELED,
                detail="mission was already canceled",
            )
        if projection.status in {
            MissionStatus.FAILED,
            MissionStatus.BLOCKED,
        }:
            return self._result(
                RuntimeStatus.BLOCKED,
                detail=f"mission is not actionable: {projection.status.value}",
            )
        if self._mutation_permits.closed:
            return self._result(
                RuntimeStatus.BLOCKED,
                detail=(
                    "desktop mutation authority is closed after an "
                    "unreconciled outcome"
                ),
            )
        if self._stopped(stop_event):
            return self._cancel()
        try:
            initial = self._state.observe(
                projection,
                turn_index=turn_index,
            )
            if not self._snapshot_matches_mission(
                initial,
                turn_index=turn_index,
            ):
                raise ValueError(
                    "Runtime snapshot does not match accepted target/evidence"
                )
            broker_projection = self._project(initial)
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail=str(exc),
            )
        route = broker_projection.execution_route
        if route is not ExecutionRoute.VISUAL_ACTOR:
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                route=route,
                detail="Broker route is not Visual Actor",
            )
        try:
            turn = project_actor_turn_input(
                self._reducer.card,
                initial.broker_state.frame,
                self._catalog,
                broker_projection,
                task_state=self._task_state,
                previous_action_local_intent=(
                    self._previous_action_local_intent
                ),
                completed_action_summaries=(
                    self._completed_action_history(
                        projection.active_obligation_id or ""
                    )
                ),
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.ACTOR_ERROR,
                route=route,
                detail=str(exc),
            )
        try:
            actor_decision = self._actor.propose(
                turn,
                image_b64=initial.frame.image_b64,
                image_media_type=initial.frame.media_type,
                submitted_size=initial.frame.submitted_size,
                stop_event=stop_event,
            )
        except InvalidActorProposal as exc:
            if self._stopped(stop_event):
                return self._cancel(
                    detail="run was canceled during the Actor call"
                )
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                route=route,
                detail=str(exc),
            )
        except Exception as exc:
            if self._stopped(stop_event):
                return self._cancel(
                    detail="run was canceled during the Actor call"
                )
            return self._result(
                RuntimeStatus.ACTOR_ERROR,
                route=route,
                detail=str(exc),
            )
        if self._stopped(stop_event):
            return self._cancel(
                detail="run was canceled while Actor was deciding"
            )
        raw = actor_decision
        pending_task_state = self._task_state
        if type(actor_decision) is ActorTurnDecision:
            raw = dict(actor_decision.proposal)
            try:
                pending_task_state = self._task_state.apply(
                    actor_decision.task_state_update
                )
            except ActorTaskStateError as exc:
                return self._result(
                    RuntimeStatus.INVALID_PROPOSAL,
                    route=route,
                    detail=str(exc),
                )
        try:
            proposal = parse_actor_proposal(raw)
        except InvalidActorProposal as exc:
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                route=route,
                detail=str(exc),
            )
        proposal_kind = proposal.kind
        try:
            if not self._latest_matches(
                initial,
                broker_projection,
                turn_index=turn_index,
            ):
                return self._result(
                    RuntimeStatus.STALE_DISCARDED,
                    route=route,
                    proposal_kind=proposal_kind,
                    detail="Runtime authority changed while Actor was deciding",
                )
            tool_name = proposal_tool_name(proposal)
            if (
                tool_name not in APPROVED_RUNTIME_TOOLS
                or tool_name not in broker_projection.tool_names
            ):
                return self._result(
                    RuntimeStatus.UNAVAILABLE,
                    route=route,
                    proposal_kind=proposal_kind,
                    detail="proposal is outside the minimal Broker surface",
                )
            if type(proposal) is DesktopActionProposal:
                if proposal.action not in APPROVED_DESKTOP_ACTIONS:
                    return self._result(
                        RuntimeStatus.INVALID_PROPOSAL,
                        route=route,
                        proposal_kind=proposal_kind,
                        detail="desktop action is not approved",
                    )
                self._argument_validator.validate(
                    proposal.action,
                    proposal.model_dump(mode="json")["args"],
                )
            visible_scope = tuple(
                evidence_id
                for evidence_id in initial.frame.evidence_refs
                if evidence_id
                in self._reducer.projection.evidence_refs
            )
            envelope = bind_actor_decision(
                mission_reducer=self._reducer,
                profile=self._profile,
                state=initial.broker_state,
                catalog=self._catalog,
                projection=broker_projection,
                proposal=proposal,
                frame_region=initial.frame.region,
                submitted_image_size=initial.frame.submitted_size,
                frame_image_sha256=initial.frame.image_sha256,
                allowed_evidence_scope=visible_scope,
            )
        except ToolArgumentValidationError as exc:
            logger.warning(
                "vNext tool arguments rejected: tool=%s code=%s "
                "keyword=%s path=%s",
                exc.tool_name,
                exc.code,
                exc.keyword or "",
                exc.instance_path,
            )
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                route=route,
                proposal_kind=proposal_kind,
                detail=str(exc),
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                route=route,
                proposal_kind=proposal_kind,
                detail=str(exc),
            )
        self._task_state = pending_task_state
        if type(proposal) is DesktopActionProposal:
            try:
                self._record_actor_decision(
                    proposal,
                    envelope,
                    broker_projection,
                )
            except Exception as exc:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=f"Evidence decision failed closed: {exc}",
                )
            return self._run_action(
                proposal,
                envelope,
                initial,
                broker_projection,
                route=route,
                turn_index=turn_index,
                stop_event=stop_event,
            )
        if type(proposal) is ClaimObligationDone:
            return self._claim_and_complete(
                proposal,
                envelope,
                initial,
                route=route,
                stop_event=stop_event,
            )
        if type(proposal) is BlockedReport:
            reason = "; ".join(proposal.observed_facts)
            try:
                event_ids = self._accept(
                    "obligation_blocked",
                    payload={
                        "obligation_id": envelope.obligation_id,
                        "blocker_kind": proposal.blocker_kind,
                        "reason": reason or proposal.blocker_kind,
                    },
                    base_revision=envelope.mission_revision,
                )
            except MissionError as exc:
                return self._result(
                    RuntimeStatus.STATE_REJECTED,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=str(exc),
                )
            return self._result(
                RuntimeStatus.BLOCKED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                event_ids=event_ids,
            )
        return self._result(
            RuntimeStatus.INVALID_PROPOSAL,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            detail="proposal kind is not supported by minimal Runtime",
        )


__all__ = [
    "MissionRuntime",
    "MissionRuntimeConfigurationError",
]
