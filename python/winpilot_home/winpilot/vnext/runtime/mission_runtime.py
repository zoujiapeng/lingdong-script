"""One-turn, event-sourced minimal Mission Runtime.

The Runtime coordinates injected owners.  It does not call a model SDK,
capture Windows pixels, perform desktop input, discover targets, or own a
second completion/evidence ledger.
"""
from __future__ import annotations

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
    ActorBudgetView,
    ControllerOutcome,
    ControllerTransition,
    MissionRuntimeResult,
    RuntimeSnapshot,
    RuntimeStatus,
    RuntimeTargetIdentity,
    project_actor_turn_input,
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

class MissionRuntimeConfigurationError(ValueError):
    """The injected slice is outside the authorized live-agent surface."""


_MAX_NARRATION_CHARS = 4000


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
        budget_facts_provider: Callable[[int], Mapping[str, Any]] | None = None,
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
        self._reducer = reducer
        self._profile = profile
        self._broker = broker
        self._catalog = catalog
        self._argument_validator = ToolArgumentValidator(catalog)
        self._actor = actor
        self._state = state
        self._controller = controller
        self._registry = evidence_registry
        self._verified_completion_result: str | None = None
        self._latest_narration: str = ""
        self._previous_action_local_intent: str | None = None
        self._task_state = ActorTaskState.initial()
        self._turn_lock = threading.Lock()
        if budget_facts_provider is not None and not callable(
            budget_facts_provider
        ):
            raise TypeError("budget_facts_provider must be callable or null")
        self._budget_facts_provider = budget_facts_provider

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
                "ToolCatalog must contain exactly the minimal live-agent surface"
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
    def verified_completion_result(self) -> str | None:
        """Return Actor-authored result text only after mission verification."""

        if self._reducer.projection.status is not MissionStatus.VERIFIED:
            return None
        return self._verified_completion_result

    @property
    def latest_narration(self) -> str:
        """Return the Actor's most recent progress note in its own words."""

        return self._latest_narration

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
            or snapshot.frame.scope
            not in {"target_window", "full_desktop"}
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
        try:
            latest = self._state.current(
                projection,
                turn_index=turn_index,
            )
        except Exception:
            return False
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

    def _project_budget(
        self,
        turn_index: int,
    ) -> ActorBudgetView | None:
        """Read the mechanical fuel-gauge facts for one Actor turn.

        The gauge is advisory: any provider or contract failure degrades the
        gauge to ``None`` instead of failing the mission.  Spending policy is
        always the Actor's own semantic decision.
        """
        provider = self._budget_facts_provider
        if provider is None:
            return None
        try:
            raw = provider(turn_index)
            if not isinstance(raw, Mapping):
                return None
            return ActorBudgetView(
                turns_used=turn_index,
                max_turns=raw.get("max_turns"),
                model_calls_used=raw.get("model_calls_used"),
                max_model_calls=raw.get("max_model_calls"),
                elapsed_s=raw.get("elapsed_s"),
                max_runtime_s=raw.get("max_runtime_s"),
            )
        except Exception:
            return None

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
        """Project one uniform action-name summary.

        Every action projects to its bare tool name; scroll direction, key
        names and other per-action detail live in the structured
        ``previous_action`` view and the fresh screenshot, not in the
        completed-action history.  Per-action asymmetric enrichment was an
        arbitrary rule that gave the model different information density
        depending on which tool it happened to use.
        """
        return proposal.action

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

    def _record_rejected_transition(
        self,
        envelope: ActorDecisionEnvelope,
        transition: ControllerTransition,
        *,
        error_kind: str | None,
    ) -> None:
        """Record a proven-not-executed rejection into the mission event stream.

        The rejected action is fed back to the same Actor on the next turn as
        ``previous_action`` with its mechanical ``error_kind``, so the model
        can change strategy instead of blindly repeating a click that never
        started.  No physical replay is ever performed; the transition records
        ``input_completed=False`` only.
        """
        try:
            self._accept(
                "transition_recorded",
                payload={
                    "obligation_id": envelope.obligation_id,
                    "action_id": envelope.decision_id,
                    "action": proposal_tool_name(envelope.proposal),
                    "input_completed": False,
                    "visible_effect": transition.visible_effect or "",
                    "error_kind": error_kind or "",
                },
                base_revision=envelope.mission_revision,
            )
        except MissionError:
            raise

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
        if not self._mutation_still_current(
            envelope,
            initial,
            broker_projection,
            turn_index=turn_index,
            stop_event=stop_event,
        ):
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail="Runtime authority changed while waiting for input",
            )
        try:
            transition = self._controller.execute(
                envelope,
                stop_event=stop_event,
            )
        except Exception as exc:
            return self._block_unknown(
                obligation_id=obligation_id,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller raised during execution: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
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
            # Every proven-not-executed rejection is fed back to the Actor
            # with its mechanical error_kind (target/hit-test/format).
            # Whether the failure is worth another attempt is a semantic
            # decision for the same model that sees the error and the next
            # fresh frame; the runtime keeps only the mechanical budget
            # (max_turns/max_model_calls) as the hard stop.  A static
            # "recoverable" whitelist is no longer consulted.
            rejection_evidence_error: Exception | None = None
            try:
                self._record_rejected_transition(
                    envelope,
                    transition,
                    error_kind=transition.error_kind,
                )
            except Exception as exc:
                rejection_evidence_error = exc
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                route=route,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                event_ids=(),
                detail=(
                    "Controller proved input did not execute: error_kind="
                    f"{transition.error_kind or 'unspecified'}; "
                    "no replay occurred"
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
        rejection_feedback: str | None = None,
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
                rejection_feedback=rejection_feedback,
            )
        finally:
            self._turn_lock.release()

    def _run_locked(
        self,
        *,
        turn_index: int,
        stop_event: threading.Event | None,
        rejection_feedback: str | None = None,
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
            budget = self._project_budget(turn_index)
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
                budget=budget,
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
                rejection_feedback=rejection_feedback,
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
                "keyword=%s path=%s expected=%s actual=%s",
                exc.tool_name,
                exc.code,
                exc.keyword or "",
                exc.instance_path,
                exc.expected or "",
                exc.actual or "",
            )
            # The action failed mechanically, but the Actor's own task notes
            # remain useful input for its next attempt. Authority freshness was
            # checked above, and task state carries no execution authority.
            self._task_state = pending_task_state
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
        if type(actor_decision) is ActorTurnDecision:
            narration = actor_decision.narration.strip()
            if narration:
                self._latest_narration = narration[:_MAX_NARRATION_CHARS]
        if type(proposal) is DesktopActionProposal:
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
                detail=(reason or proposal.blocker_kind)[:4000],
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
