"""One-turn, event-sourced minimal Mission Runtime.

The Runtime coordinates injected owners.  It does not call a model SDK,
capture Windows pixels, perform desktop input, discover targets, or own a
second completion/evidence ledger.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping

from ..actor import (
    ActorDecisionEnvelope,
    BlockedReport,
    ClaimObligationDone,
    DesktopActionProposal,
    InvalidActorProposal,
    ObserveProposal,
    bind_actor_decision,
    parse_actor_proposal,
    proposal_tool_name,
)
from ..mission import (
    MissionError,
    MissionReducer,
    MissionStatus,
)
from .contracts import (
    APPROVED_DESKTOP_ACTIONS,
    APPROVED_RUNTIME_TOOLS,
    ActorBudgetView,
    ActorToolView,
    ControllerOutcome,
    ControllerTransition,
    MissionRuntimeResult,
    PointerDisposition,
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
        tools: tuple[ActorToolView, ...],
        actor: ActorPort,
        state: RuntimeStatePort,
        controller: ControllerPort,
        budget_facts_provider: Callable[[int], Mapping[str, Any]] | None = None,
    ) -> None:
        if type(reducer) is not MissionReducer:
            raise TypeError("reducer must be MissionReducer")
        for value, method, name in (
            (actor, "propose", "actor"),
            (state, "observe", "state"),
            (controller, "execute", "controller"),
        ):
            if not callable(getattr(value, method, None)):
                raise TypeError(f"{name} must provide {method}()")
        self._validate_minimal_scope(
            reducer=reducer,
            tools=tools,
        )
        self._reducer = reducer
        self._tools = tuple(tools)
        self._tool_names = frozenset(tool.name for tool in self._tools)
        self._actor = actor
        self._state = state
        self._controller = controller
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
        tools: tuple[ActorToolView, ...],
    ) -> None:
        definition = reducer.definition
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
        ):
            raise MissionRuntimeConfigurationError(
                "obligation is not the single visual full-goal contract"
            )
        if obligation.evidence_policy.allow_independent_visual_verifier:
            raise MissionRuntimeConfigurationError(
                "live Actor claim must not enable a second visual verifier"
            )
        if not isinstance(tools, tuple) or any(
            type(tool) is not ActorToolView for tool in tools
        ):
            raise MissionRuntimeConfigurationError(
                "tools must be canonical ActorToolView values"
            )
        names = tuple(tool.name for tool in tools)
        if (
            frozenset(names) != APPROVED_RUNTIME_TOOLS
            or len(names) != len(set(names))
        ):
            raise MissionRuntimeConfigurationError(
                "tools must contain exactly the minimal live-agent surface"
            )
        for tool in tools:
            if tool.requires_local_intent != (
                tool.name in APPROVED_DESKTOP_ACTIONS
            ):
                raise MissionRuntimeConfigurationError(
                    f"invalid local-intent contract for {tool.name!r}"
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
        """Return the Actor-authored completion result, if it claimed one."""

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
        proposal_kind: str | None = None,
        decision_id: str | None = None,
        detail: str = "",
    ) -> MissionRuntimeResult:
        return MissionRuntimeResult(
            status=status,
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            detail=detail,
        )

    def _accept(
        self,
        kind: str,
        *,
        payload: Mapping[str, Any],
        base_revision: int,
    ) -> None:
        event = self._reducer.event(
            kind,
            payload=dict(payload),
            base_revision=base_revision,
        )
        self._reducer.apply(event)

    def _cancel(
        self,
        *,
        detail: str = "run cancellation was requested",
    ) -> MissionRuntimeResult:
        projection = self._reducer.projection
        if projection.status is MissionStatus.CANCELED:
            return self._result(
                RuntimeStatus.CANCELED,
                detail=detail,
            )
        if projection.status is MissionStatus.VERIFIED:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail=f"cannot cancel terminal mission: {projection.status.value}",
            )
        try:
            self._accept(
                "mission_canceled",
                payload={"reason": detail},
                base_revision=projection.revision,
            )
        except MissionError as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail=f"cancellation was not accepted: {exc}",
            )
        return self._result(
            RuntimeStatus.CANCELED,
            detail=detail,
        )

    def _block_unknown(
        self,
        *,
        obligation_id: str,
        proposal_kind: str,
        decision_id: str,
        detail: str,
    ) -> MissionRuntimeResult:
        block_error: MissionError | None = None
        for _ in range(3):
            projection = self._reducer.projection
            if projection.active_obligation_id != obligation_id:
                break
            try:
                self._accept(
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
            proposal_kind=proposal_kind,
            decision_id=decision_id,
            detail=detail,
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

    def _claim_and_complete(
        self,
        proposal: ClaimObligationDone,
        envelope: ActorDecisionEnvelope,
    ) -> MissionRuntimeResult:
        # The Actor's completion judgment on the current real screen is the
        # semantic truth. No mechanical gate may veto it: neither the deadline
        # nor a cancel that arrived while the model was deciding may turn an
        # already-made claim into a failure. Cache the claim and narration
        # before any auxiliary bookkeeping so later failures cannot lose them.
        obligation_id = envelope.obligation_id
        proposal_kind = "claim_obligation_done"
        self._verified_completion_result = proposal.concise_claim
        self._latest_narration = proposal.concise_claim
        diagnostics: list[str] = []
        try:
            self._accept(
                "actor_completed",
                payload={"obligation_id": obligation_id},
                base_revision=envelope.mission_revision,
            )
        except Exception as exc:
            diagnostics.append(f"completion history unavailable: {exc}")
        return self._result(
            RuntimeStatus.MISSION_VERIFIED,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            detail="; ".join(diagnostics),
        )

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
        *,
        stop_event: threading.Event | None,
    ) -> MissionRuntimeResult:
        proposal_kind = proposal.kind
        obligation_id = envelope.obligation_id
        if self._stopped(stop_event):
            return self._cancel(detail="run was canceled before input executed")
        try:
            transition = self._controller.execute(
                envelope,
                stop_event=stop_event,
            )
        except Exception as exc:
            # A mechanical exception is fed back to the same Actor with the
            # real reason; the next fresh frame shows whether anything
            # actually happened. Nothing is replayed either way. Only a
            # confirmed release-cleanup failure is a physical boundary.
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller raised during execution: "
                    f"{type(exc).__name__}: {exc}; no replay occurred; "
                    "inspect the next fresh screen"
                ),
            )
        if type(transition) is not ControllerTransition:
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller returned an invalid typed transition; "
                    "no replay occurred; inspect the next fresh screen"
                ),
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
            return self._result(
                RuntimeStatus.STALE_DISCARDED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "Controller could not prove the mutation outcome; "
                    "no replay occurred; inspect the next fresh screen"
                ),
            )
        if transition.outcome is ControllerOutcome.UNKNOWN:
            if transition.error_kind == "release_cleanup_incomplete":
                return self._block_unknown(
                    obligation_id=obligation_id,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=(
                        "Controller could not confirm physical input release; "
                        "no further mutation is safe"
                    ),
                )
            if transition.input_completed is not True:
                return self._result(
                    RuntimeStatus.STALE_DISCARDED,
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=(
                        "Controller could not prove whether "
                        f"{proposal.action} completed for intent "
                        f"{proposal.local_intent[:500]!r}; "
                        "no replay occurred; inspect the next fresh screen"
                    ),
                )
            diagnostics: list[str] = []
            try:
                self._accept(
                    "transition_recorded",
                    payload={
                        "obligation_id": obligation_id,
                        "action_id": envelope.decision_id,
                        "action": proposal.action,
                        "action_summary": proposal.action,
                        "input_completed": True,
                    },
                    base_revision=envelope.mission_revision,
                )
            except Exception as exc:
                # The controller already knows SendInput completed.  A local
                # journal failure is diagnostic only and must not turn that
                # physical fact into a rejection or invite a replay.
                diagnostics.append(f"action history unavailable: {exc}")
            self._previous_action_local_intent = proposal.local_intent
            if self._stopped(stop_event):
                return self._cancel(
                    detail="run was canceled after the admitted action",
                )
            return self._result(
                RuntimeStatus.ACTION_RECORDED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(
                    "OS input completed; its effect will be recovered from "
                    "the next fresh observation"
                    + ("; " + "; ".join(diagnostics) if diagnostics else "")
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
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
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
        diagnostics: list[str] = []
        try:
            self._accept(
                "transition_recorded",
                payload={
                    "obligation_id": obligation_id,
                    "action_id": envelope.decision_id,
                    "action": proposal.action,
                    "action_summary": proposal.action,
                    "input_completed": True,
                    "visible_effect": transition.visible_effect,
                    "frame_id": transition.post_frame_id,
                },
                base_revision=envelope.mission_revision,
            )
        except Exception as exc:
            # Bookkeeping is advisory once the OS accepted the input.  The next
            # fresh screen is the source of truth; never turn a completed
            # physical action into a terminal mission block because an
            # evidence/event append failed.
            diagnostics.append(f"action history unavailable: {exc}")
        self._previous_action_local_intent = proposal.local_intent
        if self._stopped(stop_event):
            return self._cancel(
                detail="run was canceled after the admitted action",
            )
        return self._result(
            RuntimeStatus.ACTION_RECORDED,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            detail="; ".join(diagnostics),
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
        if projection.status is MissionStatus.BLOCKED:
            return self._result(
                RuntimeStatus.BLOCKED,
                detail=f"mission is not actionable: {projection.status.value}",
            )
        if self._stopped(stop_event):
            return self._cancel()
        try:
            initial = self._state.observe()
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail=str(exc),
            )
        if type(initial) is not RuntimeSnapshot:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
                detail="state.observe() did not return RuntimeSnapshot",
            )
        if initial.canceled:
            return self._cancel(detail="runtime cancellation was requested")
        if initial.paused:
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                detail="runtime is paused",
            )
        if initial.pointer.disposition is PointerDisposition.UNKNOWN:
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                detail="physical pointer state is unknown",
            )
        if (
            initial.pointer.disposition is PointerDisposition.HELD
            and not initial.pointer.physically_owned
        ):
            return self._result(
                RuntimeStatus.UNAVAILABLE,
                detail="held pointer is not physically owned by this runtime",
            )
        try:
            budget = self._project_budget(turn_index)
            turn = project_actor_turn_input(
                self._reducer.card,
                initial,
                self._tools,
                task_state=self._task_state,
                previous_action_local_intent=(
                    self._previous_action_local_intent
                ),
                budget=budget,
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.ACTOR_ERROR,
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
                detail=str(exc),
            )
        except Exception as exc:
            if self._stopped(stop_event):
                return self._cancel(
                    detail="run was canceled during the Actor call"
                )
            return self._result(
                RuntimeStatus.ACTOR_ERROR,
                detail=str(exc),
            )
        raw = actor_decision
        pending_task_state = self._task_state
        if type(actor_decision) is ActorTurnDecision:
            raw = dict(actor_decision.proposal)
            try:
                pending_task_state = self._task_state.apply(
                    actor_decision.task_state_update
                )
            except ActorTaskStateError:
                pending_task_state = self._task_state
        try:
            proposal = parse_actor_proposal(raw)
        except InvalidActorProposal as exc:
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                detail=str(exc),
            )
        proposal_kind = proposal.kind
        try:
            tool_name = proposal_tool_name(proposal)
            if (
                tool_name not in APPROVED_RUNTIME_TOOLS
                or tool_name not in self._tool_names
            ):
                return self._result(
                    RuntimeStatus.UNAVAILABLE,
                    proposal_kind=proposal_kind,
                    detail="proposal is outside the live Actor tool surface",
                )
            if type(proposal) is DesktopActionProposal:
                if proposal.action not in APPROVED_DESKTOP_ACTIONS:
                    return self._result(
                        RuntimeStatus.INVALID_PROPOSAL,
                        proposal_kind=proposal_kind,
                        detail="desktop action is not approved",
                    )
            envelope = bind_actor_decision(
                mission_reducer=self._reducer,
                proposal=proposal,
                turn_index=turn_index,
                frame_id=initial.frame.frame_id,
                frame_epoch=initial.frame.epoch,
                frame_hwnd=initial.frame.hwnd,
                frame_scope=initial.frame.scope,
                pointer_disposition=initial.pointer.disposition.value,
                pointer_lease_id=initial.pointer.lease_id,
                frame_region=initial.frame.region,
                submitted_image_size=initial.frame.submitted_size,
                frame_image_sha256=initial.frame.image_sha256,
            )
        except InvalidActorProposal as exc:
            # A malformed observation proposal is a correctable model error,
            # not a broken Runtime authority boundary. Keep its working state
            # so the ordinary next turn can use a fresh frame.
            self._task_state = pending_task_state
            return self._result(
                RuntimeStatus.INVALID_PROPOSAL,
                proposal_kind=proposal_kind,
                detail=str(exc),
            )
        except Exception as exc:
            return self._result(
                RuntimeStatus.STATE_REJECTED,
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
                stop_event=stop_event,
            )
        if type(proposal) is ObserveProposal:
            # Observation is a model-owned yield point, not physical input or
            # a verification verdict. The outer live loop captures a fresh
            # whole-screen frame on its next ordinary turn.
            return self._result(
                RuntimeStatus.OBSERVATION_REQUESTED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=proposal.question,
            )
        if type(proposal) is ClaimObligationDone:
            return self._claim_and_complete(
                proposal,
                envelope,
            )
        if type(proposal) is BlockedReport:
            reason = "; ".join(proposal.observed_facts)
            try:
                self._accept(
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
                    proposal_kind=proposal_kind,
                    decision_id=envelope.decision_id,
                    detail=str(exc),
                )
            return self._result(
                RuntimeStatus.BLOCKED,
                proposal_kind=proposal_kind,
                decision_id=envelope.decision_id,
                detail=(reason or proposal.blocker_kind)[:4000],
            )
        return self._result(
            RuntimeStatus.INVALID_PROPOSAL,
            proposal_kind=proposal_kind,
            decision_id=envelope.decision_id,
            detail="proposal kind is not supported by minimal Runtime",
        )


__all__ = [
    "MissionRuntime",
    "MissionRuntimeConfigurationError",
]
