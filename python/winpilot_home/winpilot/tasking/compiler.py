"""Compile natural-language tasks to typed contracts and executable ARP graphs."""
from __future__ import annotations

import re
import uuid

from .capability import CapabilityPlanner
from .contracts import (
    ArtifactBinding,
    CompletionObligation,
    EvidenceProviderSpec,
    RequirementCoverage,
    StateBinding,
    StageType,
    TaskContract,
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
        # Same-client context is carried as bounded source facts, including in
        # cold-start runs.  The compiler never resolves pronouns, inherits an
        # application/recipient, or rewrites operations from those facts; the
        # visual model interprets the original task and context together.
        intent = self.parser.parse(task)
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        stages: list[TaskStage] = []
        obligations: list[CompletionObligation] = []
        session = None

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
            runtime_resolution_fields: list[str] | None = None,
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
                    "verification_policy": "full_goal_coverage",
                    "provider_policy": "gui_primary_optional_external_provider",
                    # ExecuteDesktopTask is deliberately lossless.  Parsed
                    # nouns and verbs may help discover an application, but
                    # they must never switch the visual Agent into a drawing,
                    # game, board, or other compiler-authored task mode.
                    "preserve_original_intent": True,
                    "runtime_resolution_fields": resolution_fields,
                },
                allow_skill_replay=True,
            )
            stage.required_capabilities.append("visual.adaptive_interaction")
            obligation = CompletionObligation(
                id="desktop.full_goal_verified",
                predicate="desktop.full_goal_verified",
                description=(
                    "The delivered result must satisfy every clause in the original desktop request in the "
                    "modality the user requested. GUI mutations require fresh target-bound visual and causal "
                    "action evidence; a prose claim that a GUI mutation succeeded is not evidence. A requested "
                    "answer or explanation must instead be verified for correctness against the current frame."
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

        # `require_gui` is an entrypoint contract, not a hint for the intent
        # parser.  The same visible client must not silently change runtime
        # because the text happened to contain words such as draw, game, teach,
        # board, explain, or demonstrate.  The model sees the complete request
        # and the live frame, then decides whether the next step is an action or
        # an explanation.
        client_gui_goal = bool(require_gui)

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
            and runtime_resolution_fields
        )
        visual_output_request = bool(
            "draw" in intent.operation and not intent.interaction.active
        )
        full_desktop_execution = bool(
            client_gui_goal
            or intent.interaction.active
            or any(item in intent.operation for item in ("operate", "demonstrate"))
            or deferred_desktop_resolution
            # A pure visual-output request still needs the desktop Agent, but
            # it receives no special mode, schema, verifier, subject, or
            # quality contract.  It is just another lossless GUI goal.
            or visual_output_request
        )
        if full_desktop_execution:
            add_desktop_goal(
                goal=task,
                app=None,
                runtime_resolution_fields=runtime_resolution_fields,
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
                kind="desktop_workflow",
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
        # A real GUI request must reach its first frame before asking for facts
        # that the visible application may already answer.  Non-GUI typed
        # workflows retain their compile-time clarification contract.
        if (
            desktop_stage is None
            and not intent.interaction.active
            and "subject" in intent.ambiguous_fields
        ):
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
