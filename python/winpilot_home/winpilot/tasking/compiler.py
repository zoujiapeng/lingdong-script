"""Compile natural-language tasks to typed contracts and executable ARP graphs."""
from __future__ import annotations

import re
import uuid

from .capability import CapabilityPlanner
from .contracts import (
    ArtifactBinding,
    CompletionObligation,
    EvidenceProviderSpec,
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
        intent = self.parser.parse(task)
        stages: list[TaskStage] = []
        obligations: list[CompletionObligation] = []

        def add(
            stage_type: StageType,
            name: str,
            parameters: dict,
            *,
            inputs: list[ArtifactBinding] | None = None,
            outputs: list[ArtifactBinding] | None = None,
            allow_skill_replay: bool = False,
        ) -> TaskStage:
            stage_id = self._stage_id(len(stages) + 1, stage_type)
            stage = TaskStage(
                id=stage_id,
                type=stage_type,
                name=name,
                parameters={**parameters, "max_steps": max_steps},
                depends_on=[stages[-1].id] if stages else [],
                inputs=inputs or [],
                outputs=outputs or [],
                required_capabilities=[],
                obligations=[],
                allow_skill_replay=bool(allow_skill_replay and not cold_start),
            )
            stages.append(stage)
            return stage

        if "draw" in intent.operation:
            stage = add(
                StageType.DRAW_ARTIFACT,
                "创建绘图内容",
                {"app": intent.source or "Paint", "subject": intent.subject, "quality": intent.quality, "original_task": task},
                outputs=[ArtifactBinding(name="visual_content", media_type="image/*", source=intent.source or "Paint")],
                allow_skill_replay=True,
            )
            obligation = CompletionObligation(
                id="artifact.semantic_visible",
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
            if re.search(r"^(?:打开|启动|运行|open|launch|start)\b", task, re.IGNORECASE):
                add(StageType.OPEN_APPLICATION, "打开应用", {"app": intent.destination or task}, allow_skill_replay=True)
            else:
                stage = add(
                    StageType.CREATE_OR_EDIT_ARTIFACT,
                    "执行语义任务阶段",
                    {"goal": task, "original_task": task},
                )
                obligation = CompletionObligation(
                    id="stage.verified_outcome",
                    description="The bounded semantic stage produced independently verified evidence.",
                    stage_id=stage.id,
                    evidence_providers=[_provider("semantic_stage_done", "artifact.create_edit", "visual.semantic_verify")],
                )
                stage.obligations.append(obligation)
                obligations.append(obligation)

        contract = TaskContract(
            task_id=f"task-{uuid.uuid4().hex[:16]}",
            task=task,
            intent=intent,
            stages=stages,
            obligations=obligations,
            final_conditions=[item.id for item in obligations],
            experience_policy="disabled" if cold_start else "allow",
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
