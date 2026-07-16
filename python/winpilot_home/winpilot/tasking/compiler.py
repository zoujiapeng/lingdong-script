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
    StateBinding,
    StageType,
    TaskContract,
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

    def compile(
        self,
        task: str,
        *,
        max_steps: int = 40,
        cold_start: bool = False,
    ) -> TaskContract:
        step_budget = max(1, min(400, int(max_steps)))
        intent = self.parser.parse(task)
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

        if intent.interaction.active:
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

        # Interactive drawing is a state-dependent turn, not a request to
        # complete an entire drawing artifact before observing the other actor.
        if "draw" in intent.operation and not intent.interaction.active:
            stage = add(
                StageType.DRAW_ARTIFACT,
                "创建绘图内容",
                {"app": intent.source or "Paint", "subject": intent.subject, "quality": intent.quality, "original_task": task},
                outputs=[ArtifactBinding(name="visual_content", media_type="image/*", source=intent.source or "Paint")],
                allow_skill_replay=True,
            )
            obligation = CompletionObligation(
                id="artifact.semantic_visible",
                predicate="artifact.semantic_visible",
                description="The latest application frame visibly satisfies the requested drawing/content semantics.",
                stage_id=stage.id,
                evidence_providers=[
                    _provider("semantic_visual_judge", "visual.semantic_verify"),
                    _provider("verified_stage_skill", "visual.semantic_verify"),
                ],
            )
            stage.obligations.append(obligation)
            obligations.append(obligation)

        needs_capture = "capture" in intent.operation or (
            "send" in intent.operation and intent.media_type in {"image", "file"}
        )
        if needs_capture:
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

        if "save" in intent.operation and not needs_capture:
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

        if "send" in intent.operation:
            quoted = [
                next((part for part in groups if part), "").strip()
                for groups in re.findall(r'"([^"]{1,500})"|“([^”]{1,500})”|「([^」]{1,500})」', task)
            ]
            text_payload = next((item for item in quoted if item and item != intent.recipient), "")
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
                    {"app": intent.channel, "recipient": intent.recipient, "text": text_payload, "send_mode": intent.send_mode, "original_task": task},
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
            else:
                stage = add(
                    StageType.CREATE_OR_EDIT_ARTIFACT,
                    "执行语义任务阶段",
                    {
                        "goal": task,
                        "original_task": task,
                        "app": intent.source,
                        "source": intent.source or "active_window",
                    },
                )
                obligation = CompletionObligation(
                    id="stage.verified_outcome",
                    predicate="stage.verified_outcome",
                    description="The bounded semantic stage produced independently verified evidence.",
                    stage_id=stage.id,
                    evidence_providers=[_provider("semantic_stage_done", "artifact.create_edit", "visual.semantic_verify")],
                )
                stage.obligations.append(obligation)
                obligations.append(obligation)

        contract = TaskContract(
            task_id=task_id,
            task=task,
            intent=intent,
            stages=stages,
            obligations=obligations,
            final_conditions=[item.id for item in obligations],
            session=session,
            experience_policy="disabled" if cold_start else "allow",
            compiler_version="typed-stage-3",
        )
        return self.planner.plan_contract(contract)

    def compile_graph(
        self,
        task: str,
        *,
        max_steps: int = 40,
        cold_start: bool = False,
    ):
        from ..orchestrator.graph_compiler import task_contract_to_arp
        contract = self.compile(task, max_steps=max_steps, cold_start=cold_start)
        self.planner.assert_satisfiable(contract)
        return contract, task_contract_to_arp(contract)
