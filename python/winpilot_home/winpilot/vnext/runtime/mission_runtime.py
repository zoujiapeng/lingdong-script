"""One-turn, event-sourced vNext Mission Runtime.

The Runtime coordinates typed owners.  It never calls a model directly and it
never performs desktop input itself.
"""
from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Protocol

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
from ..evidence import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from ..mission import (
    MissionError,
    MissionReducer,
    MissionStatus,
    ObligationStatus,
    StaleRevisionError,
)
from ..profiling import ExecutionProfile
from ..verification import (
    AcceptedObligationVerification,
    ActionEffectVerificationRequest,
    ActionEffectVerificationResult,
    DeterministicActionEffectVerifier,
    DeterministicMissionCompletionVerifier,
    InvalidVerificationSchema,
    MissionCompletionVerificationRequest,
    MissionCompletionVerificationResult,
    ObligationVerificationRequest,
    ObligationVerificationResult,
    ObligationVerifierPort,
    VerifierAssessment,
)
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

if TYPE_CHECKING:
    from ..verification.visual_model import VisualVerifierAttestation


_PROPOSAL_TOOL = {
    "observe": "observe",
    "request_capability": "request_capability",
    "request_inspection": "request_inspection",
    "claim_obligation_done": "claim_obligation_done",
    "blocked": "blocked",
    "request_replan": "request_replan",
}


class AttestedVisualVerifierPort(Protocol):
    """Read-only visual verifier that returns a locally bound attestation."""

    verifier_kind: str

    def attest(
        self,
        request: ObligationVerificationRequest,
        frame: RuntimeFrame,
        *,
        stop_event: threading.Event | None = None,
    ) -> "VisualVerifierAttestation": ...


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
        obligation_verifier: ObligationVerifierPort | None = None,
        attested_visual_verifier: AttestedVisualVerifierPort | None = None,
        mission_completion_verifier: (
            DeterministicMissionCompletionVerifier | None
        ) = None,
        action_effect_verifier: DeterministicActionEffectVerifier | None = None,
        evidence_registry: EvidenceRegistry | None = None,
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
        if obligation_verifier is not None and not callable(
            getattr(obligation_verifier, "verify", None)
        ):
            raise TypeError("obligation_verifier must provide verify()")
        if obligation_verifier is not None and attested_visual_verifier is not None:
            raise TypeError(
                "generic and attested visual obligation verifiers are mutually exclusive"
            )
        if attested_visual_verifier is not None and (
            not callable(getattr(attested_visual_verifier, "attest", None))
            or getattr(attested_visual_verifier, "verifier_kind", None)
            != "independent_visual_model"
        ):
            raise TypeError(
                "attested_visual_verifier must provide independent_visual_model attest()"
            )
        self._obligation_verifier = obligation_verifier
        self._attested_visual_verifier = attested_visual_verifier
        if mission_completion_verifier is not None and evidence_registry is None:
            raise TypeError(
                "mission completion verifier and evidence registry must be injected together"
            )
        if attested_visual_verifier is not None and evidence_registry is None:
            raise TypeError(
                "attested visual verifier and evidence registry must be injected together"
            )
        if action_effect_verifier is not None and evidence_registry is None:
            raise TypeError(
                "action effect verifier and evidence registry must be injected together"
            )
        if (
            evidence_registry is not None
            and mission_completion_verifier is None
            and attested_visual_verifier is None
            and action_effect_verifier is None
        ):
            raise TypeError(
                "Runtime verification owner and evidence registry must be injected together"
            )
        if mission_completion_verifier is not None:
            if type(mission_completion_verifier) is not DeterministicMissionCompletionVerifier:
                raise TypeError(
                    "mission completion requires DeterministicMissionCompletionVerifier"
                )
        if action_effect_verifier is not None and type(
            action_effect_verifier
        ) is not DeterministicActionEffectVerifier:
            raise TypeError(
                "action effect verification requires DeterministicActionEffectVerifier"
            )
        if evidence_registry is not None:
            if type(evidence_registry) is not EvidenceRegistry:
                raise TypeError("verification requires canonical EvidenceRegistry")
            if reducer.evidence_source is not evidence_registry:
                raise TypeError(
                    "verification and MissionReducer must share one EvidenceRegistry"
                )
            if (
                evidence_registry.mission_id != reducer.definition.mission_id
                or evidence_registry.run_id != reducer.definition.run_id
            ):
                raise TypeError(
                    "mission completion EvidenceRegistry must match Runtime mission/run"
                )
        self._mission_completion_verifier = mission_completion_verifier
        self._action_effect_verifier = action_effect_verifier
        self._evidence_registry = evidence_registry
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

    def _canceled(
        self,
        turn_index: int,
        *,
        event_ids: Iterable[str] = (),
        detail: str = "turn canceled before desktop admission",
    ) -> MissionRuntimeResult:
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
            event_ids=event_ids,
            detail=detail,
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

    def _accept(
        self,
        kind: str,
        *,
        payload: dict,
        evidence_refs=(),
        base_revision: int | None = None,
    ) -> tuple[str, ...]:
        event = self._reducer.event(
            kind,
            payload=payload,
            evidence_refs=tuple(evidence_refs),
            base_revision=base_revision,
        )
        self._reducer.apply(event)
        return (event.event_id,)

    def _obligation_verification_enabled(self) -> bool:
        """Require both deterministic policy admission and an injected port."""

        return bool(
            self._obligation_verifier is not None
            and self._profile.services.obligation_verifier is True
        )

    def _attested_visual_verification_enabled(self) -> bool:
        return bool(
            self._attested_visual_verifier is not None
            and self._profile.services.obligation_verifier is True
        )

    def _freeze_verification_request(
        self,
        obligation_id: str,
    ) -> tuple[ObligationVerificationRequest, int]:
        """Freeze the accepted claim identity and its exact evidence objects."""

        projection = self._reducer.projection
        state = projection.obligation_state(obligation_id)
        if (
            projection.active_obligation_id != obligation_id
            or state.status is not ObligationStatus.CLAIMED
            or not state.claim_fingerprint
        ):
            raise InvalidVerificationSchema(
                "obligation verifier requires the current accepted claim"
            )
        definition = projection.obligation_definition(obligation_id)
        refs = self._reducer.evidence_scope.require(
            state.claim_evidence_refs,
            obligation_id=obligation_id,
        )
        return (
            ObligationVerificationRequest(
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
            ),
            state.verification_attempt_count,
        )

    def _verification_identity_is_current(
        self,
        request: ObligationVerificationRequest,
        *,
        verification_attempt_count: int,
    ) -> bool:
        projection = self._reducer.projection
        if (
            projection.mission_id != request.mission_id
            or projection.run_id != request.run_id
            or projection.revision != request.mission_revision
            or projection.active_obligation_id != request.obligation_id
        ):
            return False
        try:
            state = projection.obligation_state(request.obligation_id)
        except KeyError:
            return False
        return bool(
            state.status is ObligationStatus.CLAIMED
            and state.claim_fingerprint == request.claim_fingerprint
            and state.claim_evidence_refs
            == tuple(ref.id for ref in request.evidence_refs)
            and state.verification_attempt_count == verification_attempt_count
        )

    def _verification_evidence_is_current(
        self,
        request: ObligationVerificationRequest,
    ) -> bool:
        try:
            current = self._reducer.evidence_scope.require(
                (ref.id for ref in request.evidence_refs),
                obligation_id=request.obligation_id,
            )
        except MissionError:
            return False
        return current == request.evidence_refs

    @staticmethod
    def _failed_verification_assessment(
        request: ObligationVerificationRequest,
        reason: str,
    ) -> VerifierAssessment:
        return VerifierAssessment(
            verified=False,
            satisfied_conditions=(),
            missing_conditions=request.required_conditions,
            contradictions=(),
            confidence=None,
            concise_reason=reason,
        )

    @staticmethod
    def _parse_verifier_assessment(raw: Any) -> VerifierAssessment:
        if isinstance(raw, VerifierAssessment):
            return raw
        if isinstance(raw, Mapping):
            return VerifierAssessment.from_dict(dict(raw))
        raise InvalidVerificationSchema(
            "obligation verifier must return VerifierAssessment"
        )

    def _verifier_kind(self) -> str:
        value = getattr(self._obligation_verifier, "verifier_kind", None)
        if type(value) is str and value.strip():
            return value.strip()
        return "injected_deterministic"

    def _verification_event_payload(
        self,
        request: ObligationVerificationRequest,
        result: ObligationVerificationResult,
        assessment: VerifierAssessment,
        *,
        failure_kind: str,
    ) -> dict[str, Any]:
        return {
            **result.to_mission_event_payload(request),
            "reason": assessment.concise_reason,
            "failure_kind": failure_kind,
        }

    def _run_obligation_verifier(
        self,
        *,
        turn_index: int,
        route: ExecutionRoute,
        proposal_kind: str,
        decision_id: str,
        obligation_id: str,
        claim_event_ids: tuple[str, ...],
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        """Run one injected read-only verifier against one frozen claim.

        The port returns an identity-free assessment.  Runtime binds all
        mission/evidence identity and only the Reducer records the outcome.
        """

        def finish(
            status: RuntimeStatus,
            detail: str,
            event_ids: tuple[str, ...] = claim_event_ids,
        ) -> MissionRuntimeResult:
            return self._result(
                status,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=event_ids,
                detail=detail,
            )

        def fail_closed(
            request: ObligationVerificationRequest,
            reason: str,
            failure_kind: str,
        ) -> tuple[VerifierAssessment, ObligationVerificationResult, str]:
            assessment = self._failed_verification_assessment(request, reason)
            return (
                assessment,
                ObligationVerificationResult.bind(
                    request,
                    assessment,
                    verifier_kind="runtime_fail_closed",
                    evidence_refs=(),
                ),
                failure_kind,
            )

        try:
            request, attempt_count = self._freeze_verification_request(obligation_id)
        except (MissionError, InvalidVerificationSchema, ValueError) as exc:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                f"accepted claim could not be frozen for verification: {exc}",
            )
        if self._stopped(stop_event):
            return self._canceled(
                turn_index,
                event_ids=claim_event_ids,
                detail="turn canceled after claim and before verification",
            )

        assessment: VerifierAssessment
        used_evidence: tuple[str, ...] | None = None
        failure_kind = "verification_rejected"
        try:
            assert self._obligation_verifier is not None
            raw = self._obligation_verifier.verify(
                request,
                stop_event=stop_event,
            )
            assessment = self._parse_verifier_assessment(raw)
        except Exception:
            assessment, bound, failure_kind = fail_closed(
                request,
                "injected obligation verifier failed closed",
                "verifier_error",
            )
        else:
            bound = None

        # A late verifier result is never allowed to write after cancellation.
        if self._stopped(stop_event):
            return self._canceled(
                turn_index,
                event_ids=claim_event_ids,
                detail="late obligation verifier result was discarded after cancellation",
            )
        if not self._verification_identity_is_current(
            request,
            verification_attempt_count=attempt_count,
        ):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "Mission claim identity changed while verifier was running",
            )
        if not self._verification_evidence_is_current(request):
            assessment, bound, failure_kind = fail_closed(
                request,
                "claim evidence changed while verifier was running; failed closed",
                "verification_evidence_stale",
            )

        if bound is None:
            try:
                bound = ObligationVerificationResult.bind(
                    request,
                    assessment,
                    verifier_kind=self._verifier_kind(),
                    evidence_refs=used_evidence,
                )
            except Exception:
                assessment, bound, failure_kind = fail_closed(
                    request,
                    "malformed obligation verifier result failed closed",
                    "verifier_schema_error",
                )

        # Revalidate once more immediately before creating the accepted event.
        if not self._verification_identity_is_current(
            request,
            verification_attempt_count=attempt_count,
        ):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "Mission claim identity changed before verification commit",
            )
        if bound.evidence_refs and not self._verification_evidence_is_current(request):
            assessment, bound, failure_kind = fail_closed(
                request,
                "claim evidence changed before verification commit; failed closed",
                "verification_evidence_stale",
            )

        payload = self._verification_event_payload(
            request,
            bound,
            assessment,
            failure_kind=failure_kind,
        )
        try:
            verification_event_ids = self._accept(
                "verification_recorded",
                payload=payload,
                evidence_refs=bound.evidence_refs,
                base_revision=request.mission_revision,
            )
        except MissionError as first_error:
            # A rejected positive assessment must still consume this exact
            # current claim as a failed verification attempt.  Base revision
            # and fingerprint checks prevent this fallback from touching a
            # newer claim.
            if not bound.verified:
                return finish(
                    RuntimeStatus.STATE_REJECTED,
                    str(first_error),
                )
            assessment, bound, failure_kind = fail_closed(
                request,
                "positive verifier result was rejected by Mission policy; failed closed",
                "verification_policy_rejected",
            )
            try:
                verification_event_ids = self._accept(
                    "verification_recorded",
                    payload=self._verification_event_payload(
                        request,
                        bound,
                        assessment,
                        failure_kind=failure_kind,
                    ),
                    evidence_refs=(),
                    base_revision=request.mission_revision,
                )
            except MissionError as fallback_error:
                return finish(
                    RuntimeStatus.STATE_REJECTED,
                    str(fallback_error),
                )
        accepted_ids = claim_event_ids + verification_event_ids
        completion = self._maybe_verify_mission(
            turn_index=turn_index,
            stop_event=stop_event,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            prior_event_ids=accepted_ids,
        )
        if completion is not None:
            return completion
        return finish(
            RuntimeStatus.VERIFICATION_RECORDED,
            assessment.concise_reason,
            accepted_ids,
        )

    def _visual_claim_frame_is_current(
        self,
        request: ObligationVerificationRequest,
        verification_attempt_count: int,
        frozen_frame: RuntimeFrame,
        *,
        turn_index: int,
    ) -> bool:
        if not self._verification_identity_is_current(
            request,
            verification_attempt_count=verification_attempt_count,
        ):
            return False
        try:
            snapshot = self._state.current(
                self._reducer.projection,
                turn_index=turn_index,
            )
        except Exception:
            return False
        frame_state = snapshot.broker_state.frame
        return bool(
            snapshot.broker_state.turn_index == turn_index
            and self._snapshot_matches_mission(snapshot)
            and snapshot.frame == frozen_frame
            and frame_state.authoritative
            and frame_state.fresh
            and frame_state.frame_id == frozen_frame.frame_id
            and frame_state.hwnd == frozen_frame.hwnd
            and frame_state.epoch == frozen_frame.epoch
        )

    def _run_attested_visual_verifier(
        self,
        *,
        turn_index: int,
        route: ExecutionRoute,
        proposal_kind: str,
        decision_id: str,
        obligation_id: str,
        claim_event_ids: tuple[str, ...],
        frozen_frame: RuntimeFrame,
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        """Attest one claim/frame pair and commit only Runtime-bound evidence."""

        # Keep the concrete visual implementation outside MissionRuntime's
        # import graph.  ``visual_model`` itself imports RuntimeFrame, so an
        # eager import here makes direct verifier loading circular through the
        # runtime package initializer.
        from ..verification.visual_model import (
            VisualVerifierAttestation,
            append_visual_verifier_evidence,
        )

        def finish(
            status: RuntimeStatus,
            detail: str,
            event_ids: tuple[str, ...] = claim_event_ids,
        ) -> MissionRuntimeResult:
            return self._result(
                status,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=event_ids,
                detail=detail,
            )

        try:
            claim_request, attempt_count = self._freeze_verification_request(
                obligation_id
            )
        except (MissionError, InvalidVerificationSchema, ValueError) as exc:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                f"accepted claim could not be frozen for visual verification: {exc}",
            )
        if tuple(ref.id for ref in claim_request.evidence_refs) != frozen_frame.evidence_refs:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                "visual verification frame is not the accepted claim frame",
            )

        def record_failure(reason: str, failure_kind: str) -> MissionRuntimeResult:
            if self._stopped(stop_event):
                return self._canceled(
                    turn_index,
                    event_ids=claim_event_ids,
                    detail="visual verification failed after cancellation",
                )
            if not self._visual_claim_frame_is_current(
                claim_request,
                attempt_count,
                frozen_frame,
                turn_index=turn_index,
            ):
                return finish(
                    RuntimeStatus.STALE_DISCARDED,
                    "visual claim or frame changed before failure commit",
                )
            assessment = self._failed_verification_assessment(
                claim_request, reason
            )
            try:
                bound = ObligationVerificationResult.bind(
                    claim_request,
                    assessment,
                    verifier_kind="runtime_fail_closed",
                    evidence_refs=(),
                )
                event_ids = self._accept(
                    "verification_recorded",
                    payload=self._verification_event_payload(
                        claim_request,
                        bound,
                        assessment,
                        failure_kind=failure_kind,
                    ),
                    evidence_refs=(),
                    base_revision=claim_request.mission_revision,
                )
            except (MissionError, InvalidVerificationSchema, ValueError) as exc:
                return finish(RuntimeStatus.STATE_REJECTED, str(exc))
            return finish(
                RuntimeStatus.VERIFICATION_RECORDED,
                assessment.concise_reason,
                claim_event_ids + event_ids,
            )

        if not claim_request.evidence_policy.allow_independent_visual_verifier:
            return record_failure(
                "independent visual verification is disabled by EvidencePolicy",
                "visual_verifier_not_permitted",
            )
        if self._stopped(stop_event):
            return self._canceled(
                turn_index,
                event_ids=claim_event_ids,
                detail="turn canceled before visual verification",
            )

        try:
            assert self._attested_visual_verifier is not None
            attestation = self._attested_visual_verifier.attest(
                claim_request,
                frozen_frame,
                stop_event=stop_event,
            )
        except Exception:
            attestation = None
        if self._stopped(stop_event):
            return self._canceled(
                turn_index,
                event_ids=claim_event_ids,
                detail="late visual verifier result was discarded after cancellation",
            )

        if not isinstance(attestation, VisualVerifierAttestation):
            return record_failure(
                "independent visual verifier failed closed",
                "visual_verifier_error",
            )
        if not self._visual_claim_frame_is_current(
            claim_request,
            attempt_count,
            frozen_frame,
            turn_index=turn_index,
        ):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "visual claim or authoritative frame changed during verification",
            )
        if not self._verification_evidence_is_current(claim_request):
            return record_failure(
                "claim evidence changed during visual verification",
                "verification_evidence_stale",
            )

        registry = self._evidence_registry
        if registry is None:
            return record_failure(
                "canonical visual evidence registry is unavailable",
                "visual_evidence_registry_unavailable",
            )
        try:
            visual_ref = append_visual_verifier_evidence(
                claim_request,
                frozen_frame,
                attestation,
                registry,
                stop_event=stop_event,
            )
        except Exception:
            return record_failure(
                "visual attestation evidence append failed closed",
                "visual_evidence_append_failed",
            )

        if self._stopped(stop_event):
            return self._canceled(
                turn_index,
                event_ids=claim_event_ids,
                detail="visual verification canceled before commit",
            )
        if not self._visual_claim_frame_is_current(
            claim_request,
            attempt_count,
            frozen_frame,
            turn_index=turn_index,
        ):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "visual claim or authoritative frame changed before commit",
            )
        if not self._verification_evidence_is_current(claim_request):
            return record_failure(
                "claim evidence changed before visual verification commit",
                "verification_evidence_stale",
            )
        visual_request = ObligationVerificationRequest(
            mission_id=claim_request.mission_id,
            run_id=claim_request.run_id,
            mission_revision=claim_request.mission_revision,
            obligation_id=claim_request.obligation_id,
            claim_fingerprint=claim_request.claim_fingerprint,
            desired_state=claim_request.desired_state,
            acceptance_criteria=claim_request.acceptance_criteria,
            forbidden_effects=claim_request.forbidden_effects,
            evidence_policy=claim_request.evidence_policy,
            evidence_refs=claim_request.evidence_refs + (visual_ref,),
        )
        try:
            assessment = attestation.assessment
            bound = ObligationVerificationResult.bind(
                visual_request,
                assessment,
                verifier_kind="independent_visual_model",
                evidence_refs=(visual_ref.id,),
            )
        except Exception:
            return record_failure(
                "malformed visual verifier assessment failed closed",
                "visual_verifier_schema_error",
            )

        try:
            verification_event_ids = self._accept(
                "verification_recorded",
                payload=self._verification_event_payload(
                    visual_request,
                    bound,
                    assessment,
                    failure_kind="verification_rejected",
                ),
                evidence_refs=(visual_ref.id,),
                base_revision=claim_request.mission_revision,
            )
        except MissionError as first_error:
            if not bound.verified:
                return finish(RuntimeStatus.STATE_REJECTED, str(first_error))
            return record_failure(
                "positive visual verifier result was rejected by Mission policy",
                "verification_policy_rejected",
            )
        accepted_ids = claim_event_ids + verification_event_ids
        completion = self._maybe_verify_mission(
            turn_index=turn_index,
            stop_event=stop_event,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            prior_event_ids=accepted_ids,
        )
        if completion is not None:
            return completion
        return finish(
            RuntimeStatus.VERIFICATION_RECORDED,
            assessment.concise_reason,
            accepted_ids,
        )

    def _freeze_mission_completion_request(
        self,
    ) -> MissionCompletionVerificationRequest:
        """Freeze required verified obligations from one current revision."""

        projection = self._reducer.projection
        if projection.status is not MissionStatus.AWAITING_MISSION_VERIFICATION:
            raise InvalidVerificationSchema(
                "mission completion requires awaiting_mission_verification"
            )
        required: list[AcceptedObligationVerification] = []
        scope = self._reducer.evidence_scope
        for obligation_id in projection.definition.stable_topological_order:
            definition = projection.obligation_definition(obligation_id)
            if not definition.required:
                continue
            state = projection.obligation_state(obligation_id)
            if (
                state.status is not ObligationStatus.VERIFIED
                or not state.verification_id
                or not state.verified_evidence_refs
            ):
                raise InvalidVerificationSchema(
                    f"required obligation {obligation_id!r} is not exactly verified"
                )
            refs = scope.require(
                state.verified_evidence_refs,
                obligation_id=obligation_id,
            )
            required.append(AcceptedObligationVerification(
                obligation_id=obligation_id,
                accepted_verification_id=state.verification_id,
                verified_evidence_refs=state.verified_evidence_refs,
                evidence_refs=refs,
            ))
        return MissionCompletionVerificationRequest(
            mission_id=projection.mission_id,
            run_id=projection.run_id,
            mission_revision=projection.revision,
            required_obligations=tuple(required),
        )

    def _mission_completion_is_current(
        self,
        request: MissionCompletionVerificationRequest,
    ) -> bool:
        projection = self._reducer.projection
        if (
            projection.mission_id != request.mission_id
            or projection.run_id != request.run_id
            or projection.revision != request.mission_revision
            or projection.status is not MissionStatus.AWAITING_MISSION_VERIFICATION
        ):
            return False
        expected_ids = tuple(
            obligation_id
            for obligation_id in projection.definition.stable_topological_order
            if projection.obligation_definition(obligation_id).required
        )
        if expected_ids != tuple(
            item.obligation_id for item in request.required_obligations
        ):
            return False
        try:
            for accepted in request.required_obligations:
                state = projection.obligation_state(accepted.obligation_id)
                refs = self._reducer.evidence_scope.require(
                    accepted.verified_evidence_refs,
                    obligation_id=accepted.obligation_id,
                )
                if (
                    state.status is not ObligationStatus.VERIFIED
                    or state.verification_id != accepted.accepted_verification_id
                    or state.verified_evidence_refs
                    != accepted.verified_evidence_refs
                    or refs != accepted.evidence_refs
                ):
                    return False
        except (KeyError, MissionError):
            return False
        return True

    def _maybe_verify_mission(
        self,
        *,
        turn_index: int,
        stop_event: threading.Event | None,
        route: ExecutionRoute | None = None,
        proposal_kind: str | None = None,
        decision_id: str | None = None,
        prior_event_ids: tuple[str, ...] = (),
    ) -> MissionRuntimeResult | None:
        """Mechanically close an awaiting mission using the canonical registry."""

        if self._mission_completion_verifier is None:
            return None
        projection = self._reducer.projection
        if projection.status is MissionStatus.VERIFIED:
            return self._result(
                RuntimeStatus.MISSION_VERIFIED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=prior_event_ids,
                detail="mission was already independently verified",
            )
        if projection.status is not MissionStatus.AWAITING_MISSION_VERIFICATION:
            return None

        def finish(status: RuntimeStatus, detail: str) -> MissionRuntimeResult:
            return self._result(
                status,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                event_ids=prior_event_ids,
                detail=detail,
            )

        if self._stopped(stop_event):
            return self._canceled(turn_index, event_ids=prior_event_ids)
        try:
            request = self._freeze_mission_completion_request()
            assessment = self._mission_completion_verifier.verify(request)
            result = MissionCompletionVerificationResult.bind(request, assessment)
            result = MissionCompletionVerificationResult.from_dict(result.to_dict())
        except Exception as exc:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                f"mission completion verification failed closed: {exc}",
            )
        if self._stopped(stop_event):
            return self._canceled(turn_index, event_ids=prior_event_ids)
        if not self._mission_completion_is_current(request):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "Mission verification input changed while verifier was running",
            )

        registry = self._evidence_registry
        assert registry is not None
        try:
            registry.append(result.aggregate_evidence)
            accepted = self._reducer.evidence_scope.require(
                (result.aggregate_evidence.id,)
            )
            if accepted != (result.aggregate_evidence,):
                raise InvalidVerificationSchema(
                    "Reducer did not read back the exact aggregate evidence"
                )
        except Exception as exc:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                f"mission completion aggregate was not accepted: {exc}",
            )
        if self._stopped(stop_event):
            return self._canceled(turn_index, event_ids=prior_event_ids)
        if not self._mission_completion_is_current(request):
            return finish(
                RuntimeStatus.STALE_DISCARDED,
                "Mission identity changed before completion commit",
            )
        try:
            event_ids = self._accept(
                "mission_verified",
                payload=result.to_mission_event_payload(),
                evidence_refs=result.event_evidence_refs,
                base_revision=request.mission_revision,
            )
        except StaleRevisionError as exc:
            return finish(RuntimeStatus.STALE_DISCARDED, str(exc))
        except MissionError as exc:
            # The aggregate is immutable and deterministically identified. A
            # later turn recreates the same result and idempotently retries the
            # event; no pending-completion cache is needed.
            return finish(RuntimeStatus.STATE_REJECTED, str(exc))
        except Exception as exc:
            return finish(
                RuntimeStatus.STATE_REJECTED,
                f"mission completion event was not accepted: {exc}",
            )
        final = self._reducer.projection
        if (
            final.status is not MissionStatus.VERIFIED
            or final.mission_verification_id != result.verification_id
        ):
            return finish(
                RuntimeStatus.STATE_REJECTED,
                "Reducer did not derive the accepted mission verification",
            )
        return self._result(
            RuntimeStatus.MISSION_VERIFIED,
            route=route,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            event_ids=prior_event_ids + event_ids,
            detail="all required obligations and mission aggregate are verified",
        )

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

    def _deterministic_frame_evidence_ref(
        self,
        frame: RuntimeFrame,
        *,
        obligation_id: str,
        kind: str,
        action_id: str | None,
    ) -> tuple[str, str] | None:
        """Select one exact immutable frame proof from the live registry."""

        if self._evidence_registry is None:
            return None
        try:
            refs = self._reducer.evidence_scope.require(
                frame.evidence_refs,
                obligation_id=obligation_id,
            )
        except MissionError:
            return None
        expected_hash = (
            current_frame_payload_hash(frame.commit_identity)
            if kind == CURRENT_FRAME_EVIDENCE_KIND
            else frame_identity_payload_hash(frame.commit_identity)
        )
        matches = tuple(
            ref.id
            for ref in refs
            if ref.kind == kind
            and ref.trust == "deterministic"
            and ref.obligation_id == obligation_id
            and ref.frame_id == frame.frame_id
            and ref.payload_hash == expected_hash
            and ref.action_id == action_id
        )
        return (sorted(matches)[0], expected_hash) if matches else None

    def _verify_action_effect(
        self,
        *,
        obligation_id: str,
        decision_id: str,
        input_completed: bool,
        before: RuntimeFrame,
        after: RuntimeFrame,
        target_binding_valid: bool,
    ) -> ActionEffectVerificationResult | None:
        """Build and register one optional deterministic frame comparison."""

        verifier = self._action_effect_verifier
        registry = self._evidence_registry
        if verifier is None or registry is None:
            return None
        before_proof = self._deterministic_frame_evidence_ref(
            before,
            obligation_id=obligation_id,
            kind=CURRENT_FRAME_EVIDENCE_KIND,
            action_id=None,
        )
        after_proof = self._deterministic_frame_evidence_ref(
            after,
            obligation_id=obligation_id,
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            action_id=decision_id,
        )
        if before_proof is None or after_proof is None:
            return None
        before_ref, before_payload_hash = before_proof
        after_ref, after_payload_hash = after_proof
        try:
            result = verifier.verify(ActionEffectVerificationRequest(
                mission_id=self._reducer.definition.mission_id,
                run_id=self._reducer.definition.run_id,
                obligation_id=obligation_id,
                action_id=decision_id,
                input_completed=input_completed,
                before_frame=before,
                after_frame=after,
                before_frame_evidence_ref=before_ref,
                after_frame_evidence_ref=after_ref,
                before_frame_payload_hash=before_payload_hash,
                after_frame_payload_hash=after_payload_hash,
                target_binding_valid=target_binding_valid,
            ))
            if not isinstance(result, ActionEffectVerificationResult):
                return None
            registry.append(result.evidence)
            accepted = tuple(
                ref for ref in registry.snapshot() if ref.id == result.evidence.id
            )
            if accepted != (result.evidence,):
                return None
            return ActionEffectVerificationResult(
                summary=result.summary,
                evidence=accepted[0],
            )
        except Exception:
            # Comparison is supplemental evidence.  Losing it must not erase a
            # Controller transition whose input and post frame are already
            # closed, nor may it turn the action into a replayable rejection.
            return None

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
        terminal_status = self._reducer.projection.status
        if terminal_status is MissionStatus.VERIFIED:
            return self._result(
                RuntimeStatus.MISSION_VERIFIED,
                detail="mission was already independently verified",
            )
        if terminal_status in {MissionStatus.FAILED, MissionStatus.CANCELED}:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail=f"mission is terminal: {terminal_status.value}",
            )
        if self._stopped(stop_event):
            return self._canceled(turn_index)
        completion = self._maybe_verify_mission(
            turn_index=turn_index,
            stop_event=stop_event,
        )
        if completion is not None:
            return completion
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
        except InvalidActorProposal as exc:
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                route=route,
                detail=str(exc),
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
            if self._attested_visual_verification_enabled():
                return self._run_attested_visual_verifier(
                    turn_index=turn_index,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    obligation_id=obligation_id,
                    claim_event_ids=event_ids,
                    frozen_frame=latest.frame,
                    stop_event=stop_event,
                )
            if self._obligation_verification_enabled():
                return self._run_obligation_verifier(
                    turn_index=turn_index,
                    route=route,
                    proposal_kind=proposal_kind,
                    decision_id=decision_id,
                    obligation_id=obligation_id,
                    claim_event_ids=event_ids,
                    stop_event=stop_event,
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
        if (
            transition.outcome is ControllerOutcome.REJECTED
            and transition.input_completed is False
            and transition.post_frame_id is None
            and not transition.evidence_refs
        ):
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=decision_id,
                detail=(
                    "Controller proved that input did not execute; "
                    "the decision was discarded without action replay"
                ),
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
            action_effect = self._verify_action_effect(
                obligation_id=obligation_id,
                decision_id=decision_id,
                input_completed=bool(transition.input_completed),
                before=latest.frame,
                after=post.frame,
                target_binding_valid=bool(
                    self._snapshot_matches_mission(post)
                    and post.target == latest.target
                ),
            )
            event_evidence_refs = tuple(dict.fromkeys((
                *transition.evidence_refs,
                *(
                    (
                        action_effect.summary.before_frame_evidence_ref,
                        action_effect.summary.after_frame_evidence_ref,
                        action_effect.evidence.id,
                    )
                    if action_effect is not None
                    else ()
                ),
            )))
            event_ids = self._accept(
                "transition_recorded",
                payload={
                    "obligation_id": obligation_id,
                    "action_id": decision_id,
                    "action": proposal.action,
                    "input_completed": transition.input_completed,
                    "visible_effect": transition.visible_effect,
                    "evidence_refs": list(event_evidence_refs),
                    "frame_id": transition.post_frame_id,
                    "action_effect": (
                        action_effect.summary.to_dict()
                        if action_effect is not None
                        else None
                    ),
                },
                evidence_refs=event_evidence_refs,
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
