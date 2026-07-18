"""Compile natural-language tasks to typed contracts and executable ARP graphs."""
from __future__ import annotations

import re
import uuid

from .capability import CapabilityPlanner
from .contracts import (
    ArtifactBinding,
    CompletionObligation,
    EvidenceProviderSpec,
    InteractionSessionContract,
    RequirementCoverage,
    StateBinding,
    StageType,
    TaskContract,
    TaskIntent,
    TaskRequirement,
    TaskStage,
)
from .intent import TaskIntentParser


def _provider(id: str, *capabilities: str, description: str = "") -> EvidenceProviderSpec:
    return EvidenceProviderSpec(id=id, capabilities=list(capabilities), description=description)


class TaskContractCompiler:
    def __init__(self, parser: TaskIntentParser | None = None, planner: CapabilityPlanner | None = None) -> None:
        self.parser = parser or TaskIntentParser()
        self.planner = planner or CapabilityPlanner()

    @staticmethod
    def _stage_id(index: int, stage_type: StageType) -> str:
        slug = re.sub(r"(?<!^)(?=[A-Z])", "_", stage_type.value).casefold()
        return f"stage-{index:02d}-{slug}"

    @staticmethod
    def _normalize_session_context(entries: list | None) -> list[dict]:
        normalized: list[dict] = []
        for entry in entries or []:
            if isinstance(entry, dict):
                payload = dict(entry)
            else:
                payload = {
                    key: getattr(entry, key)
                    for key in (
                        "task", "status", "result", "run_id", "intent",
                        "artifacts",
                    )
                    if hasattr(entry, key)
                }
            normalized.append({
                "task": str(payload.get("task") or "")[:400],
                "status": str(payload.get("status") or ""),
                "result": str(payload.get("result") or "")[:500],
                "run_id": str(payload.get("run_id") or ""),
                "intent": (
                    dict(payload.get("intent"))
                    if isinstance(payload.get("intent"), dict)
                    else {}
                ),
                "artifacts": [
                    dict(item) for item in (payload.get("artifacts") or [])
                    if isinstance(item, dict)
                ][:20],
            })
        return normalized[-8:]

    @staticmethod
    def _apply_session_context(
        task: str,
        intent: TaskIntent,
        entries: list[dict],
    ) -> TaskIntent:
        """Resolve short-lived references without enabling long-term memory.

        This is execution context, not experience replay.  It therefore stays
        available during cold-start evaluation and never proves current GUI
        state by itself.
        """

        if not entries:
            return intent
        text = str(task or "")
        reference = bool(re.search(
            r"刚刚|刚才|之前|上次|上一(?:个|次)|那个|它|继续|再来|"
            r"previous|last|just|that|it|continue|again",
            text,
            re.IGNORECASE,
        ))
        if not reference:
            return intent
        previous = entries[-1]
        prior_intent = (
            previous.get("intent")
            if isinstance(previous.get("intent"), dict)
            else {}
        )
        payload = intent.model_dump(mode="json")
        provenance = dict(payload.get("provenance") or {})
        resolved: list[str] = []
        if not str(payload.get("source") or "").strip():
            source = str(
                prior_intent.get("source")
                or prior_intent.get("destination")
                or ""
            ).strip()
            if source:
                payload["source"] = source
                resolved.append("source")
        if not str(payload.get("channel") or "").strip():
            channel = str(prior_intent.get("channel") or "").strip()
            if channel:
                payload["channel"] = channel
                payload["destination"] = payload.get("destination") or channel
                resolved.append("channel")
        if not str(payload.get("recipient") or "").strip():
            recipient = str(prior_intent.get("recipient") or "").strip()
            if recipient:
                payload["recipient"] = recipient
                resolved.append("recipient")

        operations = list(payload.get("operation") or [])
        # In "把刚刚画的画截图/发送", the drawing verb describes the
        # antecedent artifact; it is not a request to draw a second artifact.
        if (
            "draw" in operations
            and any(item in operations for item in ("capture", "send"))
            and re.search(r"刚刚|刚才|之前|上次|previous|last|just", text, re.IGNORECASE)
            and not re.search(r"再画|重画|重新画|draw\s+again|redraw", text, re.IGNORECASE)
        ):
            operations = [item for item in operations if item != "draw"]
            payload["operation"] = operations
            resolved.append("antecedent_artifact")

        ambiguous = list(payload.get("ambiguous_fields") or [])
        if "draw" not in operations:
            ambiguous = [item for item in ambiguous if item != "subject"]
        ambiguous = [
            item for item in ambiguous
            if not str(payload.get(item) or "").strip()
        ]
        payload["ambiguous_fields"] = ambiguous
        if resolved:
            provenance["session_reference"] = ",".join(resolved)
            provenance["session_run_id"] = str(previous.get("run_id") or "")
            payload["provenance"] = provenance
        return TaskIntent.model_validate(payload)

    def compile(
        self,
        task: str,
        *,
        max_steps: int = 40,
        cold_start: bool = False,
        session_context: list | None = None,
        require_gui: bool = False,
    ) -> TaskContract:
        step_budget = max(1, min(400, int(max_steps)))
        normalized_context = self._normalize_session_context(session_context)
        intent = self._apply_session_context(
            task,
            self.parser.parse(task),
            normalized_context,
        )
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        stages: list[TaskStage] = []
        obligations: list[CompletionObligation] = []
        session: InteractionSessionContract | None = None

        def add(
            stage_type: StageType,
            name: str,
            parameters: dict,
            *,
            inputs: list[ArtifactBinding] | None = None,
            outputs: list[ArtifactBinding] | None = None,
            state_inputs: list[StateBinding] | None = None,
            state_outputs: list[StateBinding] | None = None,
            stage_max_steps: int | None = None,
            allow_skill_replay: bool = False,
        ) -> TaskStage:
            stage_id = self._stage_id(len(stages) + 1, stage_type)
            stage = TaskStage(
                id=stage_id,
                type=stage_type,
                name=name,
                parameters={
                    **parameters,
                    "max_steps": min(step_budget, max(1, int(stage_max_steps)))
                    if stage_max_steps is not None else step_budget,
                },
                depends_on=[stages[-1].id] if stages else [],
                inputs=inputs or [],
                outputs=outputs or [],
                state_inputs=state_inputs or [],
                state_outputs=state_outputs or [],
                required_capabilities=[],
                obligations=[],
                allow_skill_replay=bool(allow_skill_replay and not cold_start),
            )
            stages.append(stage)
            return stage

        def add_desktop_goal(
            *,
            goal: str | None = None,
            app: str | None = None,
            verification_policy: str = "full_goal_coverage",
            runtime_resolution_fields: list[str] | None = None,
            surface_output_requested: bool = False,
            lifecycle: str = "bounded",
            preserve_original_intent: bool = True,
        ) -> TaskStage:
            """Keep an open-ended GUI request lossless and visually verifiable.

            Structural intent fields are hints for application discovery only.
            They must not decide whether an otherwise unknown request receives
            the desktop controller, fresh pixels, or root-goal verification.
            """

            resolution_fields = list(runtime_resolution_fields or [])
            stage_goal = str(goal or task)
            stage_app = (
                intent.source or intent.channel or intent.destination
                if app is None
                else str(app or "")
            )
            named_applications = (
                list(intent.applications or [])
                if app is None
                else ([stage_app] if stage_app else [])
            )
            stage = add(
                StageType.EXECUTE_DESKTOP_TASK,
                "执行完整桌面任务",
                {
                    "goal": stage_goal,
                    "original_task": task,
                    "app": stage_app,
                    "named_applications": named_applications,
                    "source": stage_app or "active_window",
                    "verification_policy": verification_policy,
                    "minimum_effect_categories": (
                        2 if verification_policy == "capability_demonstration" else 1
                    ),
                    "forbid_open_only": True,
                    "provider_policy": "gui_primary_optional_external_provider",
                    "preserve_original_intent": preserve_original_intent,
                    "runtime_resolution_fields": resolution_fields,
                    "surface_output_mode": (
                        "continuous_pointer" if surface_output_requested else ""
                    ),
                    "surface_output_subject": (
                        intent.subject if surface_output_requested else ""
                    ),
                    "surface_output_quality": (
                        intent.quality if surface_output_requested else ""
                    ),
                    "subject": intent.subject if surface_output_requested else "",
                    "quality": intent.quality if surface_output_requested else "",
                    "lifecycle": lifecycle,
                },
                allow_skill_replay=True,
            )
            stage.required_capabilities.append("visual.adaptive_interaction")
            if surface_output_requested:
                stage.required_capabilities.append("visual.surface_manipulation")
            obligation = CompletionObligation(
                id="desktop.full_goal_verified",
                predicate="desktop.full_goal_verified",
                description=(
                    "Fresh target-bound visual evidence and the executed action trace jointly prove every "
                    "user-visible clause in the original desktop request; merely opening an app or typing "
                    "a description of the requested result is insufficient."
                    if preserve_original_intent
                    else
                    "Fresh target-bound visual evidence and the executed action trace prove that the "
                    "requested visible surface output exists before later artifact or delivery stages run."
                ),
                stage_id=stage.id,
                evidence_providers=[
                    _provider(
                        "desktop_closed_loop_verifier",
                        "desktop.execute", "visual.semantic_verify", "visual.observe",
                    ),
                ],
            )
            stage.obligations.append(obligation)
            obligations.append(obligation)
            return stage

        response_only = bool(
            "respond" in intent.operation
            and not any(
                operation in intent.operation
                for operation in (
                    "open", "draw", "capture", "save", "send", "operate",
                    "interact", "demonstrate",
                )
            )
        )
        client_gui_goal = bool(
            require_gui
            and not response_only
            and intent.domain not in {
                "workspace", "web_research", "data", "automation",
            }
        )

        if intent.interaction.active and not client_gui_goal:
            interaction = intent.interaction
            target = interaction.target or intent.source or "active_window"
            typed_direct_manipulation_verifier = bool(
                interaction.mode == "direct_visual_manipulation"
                and interaction.session_mode == "bounded"
                and interaction.primary_action_modality == "drag"
            )
            session = InteractionSessionContract(
                session_id=f"session-{task_id.removeprefix('task-')}",
                mode=interaction.mode,
                lifecycle=interaction.session_mode,
                actors=interaction.actors,
                role_assignments=interaction.role_assignments,
                initial_turn_owner=interaction.turn_owner,
                primary_action_modality=interaction.primary_action_modality,
                target=target,
                continuation_policy=interaction.continuation_policy,
                max_agent_turns_per_activation=1,
                wait_for_external_change=interaction.expected_external_change,
                requested_max_steps=step_budget,
                # One verified semantic turn normally consumes a planner and a
                # verifier call.  Derive the live-loop budgets from the API's
                # requested max_steps instead of silently ignoring it.
                max_semantic_turns=max(1, (step_budget + 1) // 2),
                max_replans=step_budget // 4,
                max_model_calls=max(2, step_budget),
                external_change_timeout_s=300.0,
            )
            scene = StateBinding(
                name=session.authoritative_state_ref,
                state_type="authoritative_visual_frame",
                source=target,
                scope="target_window",
            )
            surface = StateBinding(
                name=session.surface_region_ref,
                state_type="bound_interaction_surface",
                source=target,
                scope=interaction.region_hint or "infer_from_scene",
            )
            region = StateBinding(
                name=session.interaction_region_ref,
                state_type="per_turn_interaction_region",
                source=target,
                scope=interaction.region_hint or "infer_from_scene",
            )
            protocol = StateBinding(
                name="interaction_protocol",
                state_type="interaction_protocol",
                source=target,
            )
            turn_effect = StateBinding(
                name="turn_effect",
                state_type="verified_local_effect_candidate",
                source=target,
            )

            observe_stage = add(
                StageType.OBSERVE_SCENE,
                "绑定并观察实时界面",
                {
                    "app": intent.source,
                    "source": target,
                    "authoritative_state_ref": scene.name,
                    "surface_region_ref": surface.name,
                    "interaction_region_ref": region.name,
                    "region_hint": interaction.region_hint,
                    "intent_signals": interaction.signals,
                    "intent_confidence": interaction.confidence,
                    "original_task": task,
                },
                state_outputs=[scene, surface],
                stage_max_steps=2,
            )
            observe_obligation = CompletionObligation(
                id="interaction.scene_bound",
                predicate="interaction.scene_bound",
                description=(
                    "A fresh authoritative frame is bound to the intended target window and a stable "
                    "interaction surface authority; a full-desktop or unrelated frame is insufficient."
                ),
                stage_id=observe_stage.id,
                evidence_providers=[
                    _provider(
                        "bound_scene_observer",
                        "window.focus", "visual.observe", "visual.bind_target", "visual.region_bind",
                    ),
                ],
            )
            observe_stage.obligations.append(observe_obligation)
            obligations.append(observe_obligation)

            infer_stage = add(
                StageType.INFER_INTERACTION,
                "推断交互规则和当前轮次",
                {
                    "session_id": session.session_id,
                    "mode_candidate": interaction.mode,
                    "actors": [actor.model_dump(mode="json") for actor in interaction.actors],
                    "role_assignments": interaction.role_assignments,
                    "turn_owner_candidate": interaction.turn_owner,
                    "action_modalities": interaction.action_modalities,
                    "primary_action_modality": interaction.primary_action_modality,
                    "intent_signals": interaction.signals,
                    "original_task": task,
                },
                state_inputs=[scene, surface],
                state_outputs=[protocol, region],
                stage_max_steps=3,
            )
            infer_obligation = CompletionObligation(
                id="interaction.protocol_inferred",
                predicate="interaction.protocol_inferred",
                description=(
                    "Participants, role constraints, legal interaction region, action semantics, and current "
                    "turn are inferred from both the task and the authoritative scene."
                ),
                stage_id=infer_stage.id,
                evidence_providers=[
                    _provider("interaction_protocol_inference", "interaction.infer", "visual.observe"),
                ],
            )
            infer_stage.obligations.append(infer_obligation)
            obligations.append(infer_obligation)

            perform_stage = add(
                StageType.PERFORM_TURN,
                "执行一个语义交互回合",
                {
                    "session_id": session.session_id,
                    "turn_scope": "one_semantic_turn",
                    "turn_guard": "only_when_agent_owns_turn",
                    "on_external_turn": "defer_without_mutation",
                    "max_agent_turns": session.max_agent_turns_per_activation,
                    "allow_controller_transaction": True,
                    "action_modalities": interaction.action_modalities,
                    "primary_action_modality": interaction.primary_action_modality,
                    "original_task": task,
                },
                state_inputs=[scene, surface, region, protocol],
                state_outputs=[turn_effect],
                stage_max_steps=4,
            )
            perform_obligation = CompletionObligation(
                id="interaction.turn_resolved",
                predicate="interaction.turn_resolved",
                description=(
                    "The current semantic turn is safely resolved: the agent commits exactly one legal turn "
                    "when action is required, or makes no mutation when an external actor owns the turn or "
                    "the latest frame is awaiting independent terminal proof.  An agent turn may contain "
                    "multiple controller pointer events."
                ),
                stage_id=perform_stage.id,
                evidence_providers=[
                    _provider(
                        "bound_interaction_transaction",
                        "interaction.perform", "interaction.prepare_input_mode",
                        "controller.transaction",
                    ),
                ],
            )
            perform_stage.obligations.append(perform_obligation)
            obligations.append(perform_obligation)

            verify_stage = add(
                StageType.VERIFY_LOCAL_EFFECT,
                "验证本回合局部结果",
                {
                    "session_id": session.session_id,
                    "verification_scope": "declared_effect_regions_within_bound_surface",
                    "reject_unrelated_desktop_change": True,
                    "accept_verified_defer": True,
                    "verification_policy": (
                        "typed_direct_manipulation_completion"
                        if typed_direct_manipulation_verifier
                        else "semantic_visual_effect"
                    ),
                    "original_task": task,
                },
                state_inputs=[surface, region, protocol, turn_effect],
                state_outputs=[StateBinding(
                    name="verified_turn_effect",
                    state_type="verified_local_effect",
                    source=target,
                )],
                stage_max_steps=2,
            )
            verify_obligation = CompletionObligation(
                id="interaction.local_effect_verified",
                predicate="interaction.local_effect_verified",
                description=(
                    "A fresh post-action frame confirms the intended semantic effect in a declared effect "
                    "region inside the stable interaction surface, confirms an authorized no-mutation defer "
                    "for an external turn, or "
                    "independently confirms that no action is needed because the task is already terminal; "
                    "unrelated desktop pixel changes do not count."
                ),
                stage_id=verify_stage.id,
                evidence_providers=(
                    [
                        _provider(
                            "typed_direct_manipulation_completion",
                            "interaction.verify_transition", "visual.observe",
                        ),
                        _provider(
                            "grounded_transaction_progress",
                            "interaction.verify_local_effect",
                            "interaction.verify_transition",
                            "controller.transaction", "visual.observe",
                        ),
                        _provider(
                            "local_semantic_effect_verifier",
                            "interaction.verify_local_effect", "visual.semantic_verify", "visual.observe",
                        ),
                    ]
                    if typed_direct_manipulation_verifier
                    else [
                        _provider(
                            "grounded_transaction_progress",
                            "interaction.verify_local_effect",
                            "interaction.verify_transition",
                            "controller.transaction", "visual.observe",
                        ),
                        _provider(
                            "local_semantic_effect_verifier",
                            "interaction.verify_local_effect", "visual.semantic_verify", "visual.observe",
                        ),
                        *(
                            [_provider(
                                "typed_freehand_turn_completion",
                                "interaction.verify_transition",
                                "interaction.prepare_input_mode",
                                "visual.observe",
                            )]
                            if session.lifecycle == "persistent" else []
                        ),
                    ]
                ),
            )
            verify_stage.obligations.append(verify_obligation)
            obligations.append(verify_obligation)

            terminal_obligation = CompletionObligation(
                id="interaction.task_completed",
                predicate="interaction.task_completed",
                description=(
                    "An independent semantic check on the latest authoritative frame proves that the "
                    "original bounded interaction is terminally complete, or the activation's contracted "
                    "number of agent turns has a verified semantic effect and control is explicitly yielded. "
                    "An armed external-change watcher alone is not task completion."
                ),
                stage_id=verify_stage.id,
                evidence_providers=(
                    [
                        _provider(
                            "typed_direct_manipulation_completion",
                            "interaction.verify_transition", "visual.observe",
                        ),
                        _provider(
                            "interaction_terminal_verifier",
                            "interaction.verify_local_effect", "visual.semantic_verify", "visual.observe",
                        ),
                    ]
                    if typed_direct_manipulation_verifier
                    else [
                        _provider(
                            "interaction_terminal_verifier",
                            "interaction.verify_local_effect", "visual.semantic_verify", "visual.observe",
                        ),
                    ]
                ),
            )
            verify_stage.obligations.append(terminal_obligation)
            obligations.append(terminal_obligation)

            if session.wait_for_external_change:
                wait_stage = add(
                    StageType.WAIT_FOR_EXTERNAL_CHANGE,
                    "等待外部参与者更新界面",
                    {
                        "session_id": session.session_id,
                        "continuation_policy": session.continuation_policy,
                        "watch_scope": session.surface_region_ref,
                        "wait_mode": "block_until_verified_change",
                        "timeout_s": session.external_change_timeout_s,
                        "original_task": task,
                    },
                    state_inputs=[surface, protocol],
                    state_outputs=[StateBinding(
                        name="external_change_event",
                        state_type="verified_external_change",
                        source=target,
                        scope="bound_interaction_surface",
                    )],
                    stage_max_steps=1,
                )
                # Waiting is a continuation mechanism, not a final condition.
                # A persistent session may prove a visible terminal state on
                # its first turn and route directly from VerifyLocalEffect to
                # END without ever entering this stage.  The stage still emits
                # run-scoped external-change evidence for traceability, while
                # its required capabilities remain enforced by _STAGE_BASE.

        # Unknown in-app workflows stay as one lossless desktop goal.  The GUI
        # runner receives the original request plus an explicit application
        # constraint and must provide fresh visual/action evidence for the
        # entire goal.  This prevents a composite command from degrading into
        # an OpenApplication-only contract merely because a downstream verb is
        # not part of the small structural grammar.
        # Values such as the active application, destination surface, file, or
        # recipient can often be discovered only after observing the live
        # desktop.  Keep those tasks lossless and defer unresolved facts to the
        # same general GUI agent instead of rejecting them before it has seen
        # the machine.  This is a field-resolution policy, not a task-specific
        # workflow: the original goal remains the source of truth.
        runtime_resolution_fields = [
            field
            for field in intent.ambiguous_fields
            if field in {"channel", "recipient"}
        ]
        deferred_desktop_resolution = bool(
            "send" in intent.operation
            and (
                runtime_resolution_fields
                or intent.send_mode == "continuous_chat"
            )
        )
        surface_output_requested = bool(
            "draw" in intent.operation and not intent.interaction.active
        )
        communication_app = str(intent.channel or "").strip().casefold()
        surface_applications = [
            app
            for app in (intent.applications or [])
            if str(app or "").strip().casefold() != communication_app
        ]
        multi_application_surface_workflow = bool(
            surface_output_requested and len(surface_applications) > 1
        )
        full_desktop_execution = bool(
            client_gui_goal
            or (
                any(item in intent.operation for item in ("operate", "demonstrate"))
                and not intent.interaction.active
            )
            or deferred_desktop_resolution
            or multi_application_surface_workflow
        )
        surface_stage_only = bool(
            surface_output_requested and not full_desktop_execution
        )
        if full_desktop_execution or surface_stage_only:
            preserve_original_intent = bool(
                full_desktop_execution
                or set(intent.operation) <= {"draw"}
            )
            stage_goal = task
            if not preserve_original_intent:
                stage_goal = (
                    "Create the requested visible surface output of "
                    f"{intent.subject or 'the requested subject'} in the user-selected application."
                )
            add_desktop_goal(
                goal=stage_goal,
                app=None if full_desktop_execution else intent.source,
                verification_policy=(
                    "capability_demonstration"
                    if "demonstrate" in intent.operation
                    else (
                        "full_goal_coverage"
                        if preserve_original_intent
                        else "surface_output"
                    )
                ),
                runtime_resolution_fields=(
                    runtime_resolution_fields if full_desktop_execution else []
                ),
                surface_output_requested=surface_output_requested,
                lifecycle=(
                    "persistent"
                    if full_desktop_execution
                    and intent.send_mode == "continuous_chat"
                    else "bounded"
                ),
                preserve_original_intent=preserve_original_intent,
            )

        needs_capture = "capture" in intent.operation or (
            "send" in intent.operation and intent.media_type in {"image", "file"}
        )
        if needs_capture and not full_desktop_execution:
            stage = add(
                StageType.CAPTURE_ARTIFACT,
                "创建受管理工件",
                {"source": intent.source or "active_window", "scope": intent.capture_scope, "artifact_ref": "primary_artifact"},
                outputs=[ArtifactBinding(name="primary_artifact", media_type="image/png", source=intent.source or "active_window")],
            )
            obligation = CompletionObligation(
                id="artifact.current_run_valid",
                predicate="artifact.current_run_valid",
                description="A non-empty, hashed, current-run artifact was captured from the requested source.",
                stage_id=stage.id,
                artifact_ref="primary_artifact",
                evidence_providers=[_provider("artifact_store_capture", "artifact.capture", "artifact.hash")],
            )
            stage.obligations.append(obligation)
            obligations.append(obligation)

        if (
            "save" in intent.operation
            and not needs_capture
            and "operate" not in intent.operation
            and not full_desktop_execution
        ):
            stage = add(StageType.SAVE_ARTIFACT, "保存工件", {"source": intent.source or "active_window"})
            obligation = CompletionObligation(
                id="artifact.saved",
                predicate="artifact.saved",
                description="The requested artifact was saved and its current-run hash verified.",
                stage_id=stage.id,
                evidence_providers=[_provider("artifact_store_save", "artifact.save", "artifact.hash")],
            )
            stage.obligations.append(obligation)
            obligations.append(obligation)

        if "send" in intent.operation and not full_desktop_execution:
            text_payload = str(intent.message_text or "").strip()
            authorized_message = StateBinding(
                name="authorized_outbound_message",
                state_type="authorized_message_content",
                source=task_id,
                scope="current_run",
            )
            if intent.media_type not in {"image", "file"}:
                draft_inputs: list[StateBinding] = []
                if intent.message_content_source == "generate":
                    draft = StateBinding(
                        name="outbound_message_draft",
                        state_type="generated_message_content",
                        source=task_id,
                        scope="current_run",
                    )
                    generate_stage = add(
                        StageType.GENERATE_MESSAGE_CONTENT,
                        "生成待发送文本",
                        {
                            "instruction": task,
                            "app": intent.channel,
                            "recipient": intent.recipient,
                            "message_ref": draft.name,
                        },
                        state_outputs=[draft],
                        stage_max_steps=1,
                    )
                    generate_obligation = CompletionObligation(
                        id="message.content_generated",
                        predicate="message.content_generated",
                        description=(
                            "A non-empty outbound draft is grounded in the user's instruction "
                            "without inventing unavailable personal or current facts."
                        ),
                        stage_id=generate_stage.id,
                        evidence_providers=[
                            _provider("text_message_generator", "message.generate"),
                        ],
                    )
                    generate_stage.obligations.append(generate_obligation)
                    obligations.append(generate_obligation)
                    draft_inputs = [draft]

                review_stage = add(
                    StageType.REVIEW_OR_AUTHORIZE,
                    "审查并授权待发送文本",
                    {
                        "text": text_payload,
                        "content_source": intent.message_content_source,
                        "draft_ref": "outbound_message_draft",
                        "message_ref": authorized_message.name,
                        "recipient": intent.recipient,
                        "app": intent.channel,
                    },
                    state_inputs=draft_inputs,
                    state_outputs=[authorized_message],
                    stage_max_steps=1,
                )
                review_obligation = CompletionObligation(
                    id="message.content_authorized",
                    predicate="message.content_authorized",
                    description=(
                        "The exact non-empty outbound text is current-run content and was "
                        "authorized by the user's send request before any communication mutation."
                    ),
                    stage_id=review_stage.id,
                    recipient=intent.recipient,
                    evidence_providers=[
                        _provider("message_contract_review", "message.authorize"),
                    ],
                )
                review_stage.obligations.append(review_obligation)
                obligations.append(review_obligation)

            open_stage = add(
                StageType.OPEN_CONVERSATION,
                "打开目标会话",
                {"app": intent.channel, "recipient": intent.recipient},
            )
            open_obligation = CompletionObligation(
                id="conversation.correct_recipient",
                predicate="conversation.correct_recipient",
                description="The communication adapter resolved and opened the requested recipient context.",
                stage_id=open_stage.id,
                recipient=intent.recipient,
                evidence_providers=[_provider("communication_adapter_context", "conversation.open", "recipient.resolve")],
            )
            open_stage.obligations.append(open_obligation)
            obligations.append(open_obligation)

            if intent.media_type in {"image", "file"}:
                send_stage = add(
                    StageType.SEND_ARTIFACT,
                    "发送受管理工件",
                    {"app": intent.channel, "recipient": intent.recipient, "artifact_ref": "primary_artifact", "send_mode": intent.send_mode},
                    inputs=[ArtifactBinding(name="primary_artifact", media_type="image/png")],
                )
                send_obligation = CompletionObligation(
                    id="delivery.real_send_action",
                    predicate="delivery.real_send_action",
                    description="The selected artifact was staged and followed by a real send action in the correct conversation.",
                    stage_id=send_stage.id,
                    artifact_ref="primary_artifact",
                    recipient=intent.recipient,
                    evidence_providers=[_provider("communication_adapter_send", "communication.send_artifact", "delivery.send_action")],
                )
            else:
                send_stage = add(
                    StageType.SEND_TEXT,
                    "发送文本",
                    {
                        "app": intent.channel,
                        "recipient": intent.recipient,
                        "text": text_payload,
                        "message_ref": authorized_message.name,
                        "send_mode": intent.send_mode,
                        "original_task": task,
                    },
                    state_inputs=[authorized_message],
                )
                send_obligation = CompletionObligation(
                    id="delivery.real_send_action",
                    predicate="delivery.real_send_action",
                    description="A real outbound text send action occurred in the correct conversation.",
                    stage_id=send_stage.id,
                    recipient=intent.recipient,
                    evidence_providers=[_provider("communication_adapter_send", "communication.send_text", "delivery.send_action")],
                )
            send_stage.obligations.append(send_obligation)
            obligations.append(send_obligation)

            verify_stage = add(
                StageType.VERIFY_DELIVERY,
                "验证送达证据",
                {"app": intent.channel, "recipient": intent.recipient, "artifact_ref": "primary_artifact" if intent.media_type in {"image", "file"} else ""},
            )
            delivery_obligation = CompletionObligation(
                id="delivery.visible_outbound",
                predicate="delivery.visible_outbound",
                description="Post-send evidence in the correct conversation corresponds to the outbound artifact/text; composer preview alone is insufficient.",
                stage_id=verify_stage.id,
                artifact_ref="primary_artifact" if intent.media_type in {"image", "file"} else "",
                recipient=intent.recipient,
                evidence_providers=[
                    _provider("communication_adapter_visible_outbound", "delivery.verify", "visual.artifact_match", "visual.read_text"),
                ],
            )
            verify_stage.obligations.append(delivery_obligation)
            obligations.append(delivery_obligation)

        if not stages:
            if "open" in intent.operation and intent.destination:
                stage = add(
                    StageType.OPEN_APPLICATION,
                    "打开应用",
                    {"app": intent.destination},
                    allow_skill_replay=True,
                )
                obligation = CompletionObligation(
                    id="application.open",
                    predicate="application.open",
                    description=(
                        "The requested application was opened and its target "
                        "window was focused by the local controller."
                    ),
                    stage_id=stage.id,
                    evidence_providers=[
                        _provider("local_controller", "app.open", "window.focus"),
                        _provider("verified_stage_skill", "app.open", "window.focus"),
                    ],
                )
                stage.obligations.append(obligation)
                obligations.append(obligation)
            elif (
                client_gui_goal
                and "respond" not in intent.operation
            ):
                # The visible-client product is a GUI agent.  A small parser
                # vocabulary may enrich an unknown command, but an empty parse
                # must not strip the GUI controller from that command.
                add_desktop_goal()
            else:
                stage_type, stage_name, required = {
                    "desktop": (
                        StageType.CREATE_OR_EDIT_ARTIFACT,
                        "执行可见界面语义任务",
                        ("artifact.create_edit", "visual.semantic_verify"),
                    ),
                    "workspace": (
                        StageType.EXECUTE_WORKSPACE_TASK,
                        "执行工作区任务",
                        ("workspace.execute",),
                    ),
                    "web_research": (
                        StageType.RESEARCH_INFORMATION,
                        "检索并核验公开信息",
                        ("research.web",),
                    ),
                    "data": (
                        StageType.TRANSFORM_DATA,
                        "处理结构化数据",
                        ("data.transform",),
                    ),
                    "automation": (
                        StageType.MANAGE_AUTOMATION,
                        "管理计划任务",
                        ("automation.manage",),
                    ),
                }.get(
                    intent.domain,
                    (
                        StageType.EXECUTE_SEMANTIC_TASK,
                        "执行通用语义任务",
                        ("semantic.execute",),
                    ),
                )
                stage = add(
                    stage_type,
                    stage_name,
                    {
                        "goal": task,
                        "original_task": task,
                        "response_mode": (
                            "conversation"
                            if "respond" in intent.operation
                            else "task"
                        ),
                        "domain": intent.domain,
                        "resource_types": list(intent.resource_types),
                        "app": intent.source if intent.domain == "desktop" else "",
                        "source": (
                            intent.source or "active_window"
                            if intent.domain == "desktop" else ""
                        ),
                    },
                )
                stage.required_capabilities.extend(required)
                obligation = CompletionObligation(
                    id="stage.verified_outcome",
                    predicate="stage.verified_outcome",
                    description="The bounded semantic stage produced independently verified evidence.",
                    stage_id=stage.id,
                    evidence_providers=[_provider("semantic_stage_done", *required)],
                )
                stage.obligations.append(obligation)
                obligations.append(obligation)

        requirements: list[TaskRequirement] = []
        desktop_stage = next((
            item for item in stages
            if item.type == StageType.EXECUTE_DESKTOP_TASK
        ), None)
        if desktop_stage is not None:
            predicates = [item.evidence_predicate for item in desktop_stage.obligations]
            requirements.append(TaskRequirement(
                id="requirement-01-full-desktop-goal",
                text=task,
                kind=(
                    "capability_demonstration"
                    if "demonstrate" in intent.operation
                    else "desktop_workflow"
                ),
                stage_ids=[desktop_stage.id],
                completion_predicates=predicates,
                source_span=[0, len(task)],
                metadata={
                    "application_constraint": str(
                        desktop_stage.parameters.get("app") or ""
                    ),
                    "coverage": "original_text_lossless",
                },
            ))
        else:
            for index, stage in enumerate(stages, start=1):
                if not stage.obligations:
                    continue
                requirements.append(TaskRequirement(
                    id=f"requirement-{index:02d}-{stage.id}",
                    text=stage.name,
                    kind=stage.type.value,
                    stage_ids=[stage.id],
                    completion_predicates=[
                        item.evidence_predicate for item in stage.obligations
                    ],
                    source_span=[0, len(task)],
                    metadata={
                        "original_task": task,
                        "operation": list(intent.operation),
                    },
                ))

        stage_ids = {item.id for item in stages}
        final_predicates = {item.evidence_predicate for item in obligations}
        covered_requirement_ids = [
            item.id for item in requirements
            if item.stage_ids
            and set(item.stage_ids) <= stage_ids
            and item.completion_predicates
            and set(item.completion_predicates) <= final_predicates
        ]
        uncovered_requirement_ids = [
            item.id for item in requirements
            if item.id not in covered_requirement_ids and item.required
        ]
        requirement_coverage = RequirementCoverage(
            complete=bool(requirements) and not uncovered_requirement_ids,
            strategy=(
                "original_text_semantic_stage"
                if desktop_stage is not None
                else "typed_stage_obligations"
            ),
            covered_requirement_ids=covered_requirement_ids,
            uncovered_requirement_ids=uncovered_requirement_ids,
            notes=(
                ["Every user-visible clause remains in one full-goal desktop stage."]
                if desktop_stage is not None
                else ["Structural operations are bound to typed stage obligations."]
            ),
        )

        clarification_questions: list[str] = []
        # Channel/recipient ambiguity is intentionally resolved against the
        # live desktop by ExecuteDesktopTask.  Compile-time clarification is
        # reserved for information that observation cannot supply.
        if "subject" in intent.ambiguous_fields:
            clarification_questions.append("请说明要绘制的具体内容。")

        contract = TaskContract(
            task_id=task_id,
            task=task,
            intent=intent,
            stages=stages,
            obligations=obligations,
            final_conditions=[item.id for item in obligations],
            requirements=requirements,
            requirement_coverage=requirement_coverage,
            clarification_questions=clarification_questions,
            session_context=normalized_context,
            session=session,
            experience_policy="disabled" if cold_start else "allow",
            compiler_version="typed-stage-5",
        )
        return self.planner.plan_contract(contract)

    def compile_graph(
        self,
        task: str,
        *,
        max_steps: int = 40,
        cold_start: bool = False,
        session_context: list | None = None,
        require_gui: bool = False,
        allow_unsatisfied: bool = False,
    ):
        from ..orchestrator.graph_compiler import task_contract_to_arp
        contract = self.compile(
            task,
            max_steps=max_steps,
            cold_start=cold_start,
            session_context=session_context,
            require_gui=require_gui,
        )
        if not allow_unsatisfied:
            self.planner.assert_satisfiable(contract)
        return contract, task_contract_to_arp(contract)
