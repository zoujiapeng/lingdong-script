"""Capability selection and preflight satisfiability for typed task stages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import CapabilitySelection, StageType, TaskContract, TaskStage


CAPABILITY_TO_TOOLS: dict[str, tuple[str, ...]] = {
    "app.open": ("open_app",),
    "window.focus": ("focus_window",),
    "visual.observe": ("observe", "screenshot"),
    "visual.read_text": ("read_text",),
    "visual.semantic_verify": ("vlm_verify", "observe"),
    "visual.artifact_match": ("verify_delivery",),
    "controller.transaction": ("run_action_batch", "execute_subgoal"),
    "drawing.render": ("draw_strokes", "drag"),
    "artifact.create_edit": ("type_text", "run_action_batch"),
    "artifact.capture": ("capture_artifact", "screenshot"),
    "artifact.hash": ("capture_artifact",),
    "artifact.read": ("capture_artifact",),
    "artifact.save": ("capture_artifact",),
    "conversation.open": ("open_conversation",),
    "recipient.resolve": ("open_conversation", "read_text"),
    "communication.send_text": ("send_text",),
    "communication.send_artifact": ("send_artifact", "paste_image_file"),
    "delivery.send_action": ("send_artifact", "hotkey", "click"),
    "delivery.verify": ("verify_delivery", "read_text", "observe"),
}

_COMPONENT_CAPABILITIES: dict[str, set[str]] = {
    "local_controller": {
        "app.open", "window.focus", "visual.observe", "visual.read_text",
        "controller.transaction", "artifact.save",
    },
    "artifact_store": {"artifact.capture", "artifact.hash", "artifact.read"},
    "llm_visual_stage": {
        "drawing.render", "artifact.create_edit", "visual.semantic_verify",
        "visual.observe", "controller.transaction", "app.open", "window.focus",
    },
    "qq_adapter": {
        "app.open", "window.focus", "visual.observe", "visual.read_text",
        "controller.transaction", "conversation.open", "recipient.resolve",
        "communication.send_text", "communication.send_artifact",
        "delivery.send_action", "delivery.verify", "visual.artifact_match",
        "artifact.read", "artifact.hash",
    },
    "wechat_adapter": {
        "app.open", "window.focus", "visual.observe", "visual.read_text",
        "controller.transaction", "conversation.open", "recipient.resolve",
        "communication.send_text", "communication.send_artifact",
        "delivery.send_action", "delivery.verify", "visual.artifact_match",
        "artifact.read", "artifact.hash",
    },
}

_STAGE_BASE: dict[StageType, set[str]] = {
    StageType.OPEN_APPLICATION: {"app.open", "window.focus"},
    StageType.FOCUS_WINDOW: {"window.focus", "visual.observe"},
    StageType.CREATE_OR_EDIT_ARTIFACT: {"artifact.create_edit", "visual.semantic_verify"},
    StageType.DRAW_ARTIFACT: {
        "app.open", "window.focus", "controller.transaction",
        "drawing.render", "visual.semantic_verify",
    },
    StageType.CAPTURE_ARTIFACT: {"window.focus", "artifact.capture", "artifact.hash"},
    StageType.SAVE_ARTIFACT: {"artifact.save", "artifact.hash"},
    StageType.OPEN_CONVERSATION: {"conversation.open", "recipient.resolve", "visual.read_text"},
    StageType.RESOLVE_RECIPIENT: {"recipient.resolve", "visual.read_text"},
    StageType.SEND_TEXT: {"communication.send_text", "delivery.send_action"},
    # Keep capture/verify available in this stage as recovery capabilities.  A
    # stale or absent upstream artifact can be recaptured without restarting
    # the task, and a send implementation can immediately produce the same
    # evidence contract consumed by VerifyDelivery.
    StageType.SEND_ARTIFACT: {
        "artifact.read", "artifact.capture", "communication.send_artifact",
        "delivery.send_action", "delivery.verify",
    },
    StageType.VERIFY_ARTIFACT: {"artifact.hash", "visual.semantic_verify"},
    StageType.VERIFY_DELIVERY: {"delivery.verify", "visual.artifact_match", "visual.read_text"},
}


class CapabilityUnsatisfiedError(ValueError):
    pass


@dataclass(frozen=True)
class ComponentChoice:
    name: str
    reason: str


class CapabilityPlanner:
    def _components_for(self, stage: TaskStage, channel: str) -> list[ComponentChoice]:
        choices = [ComponentChoice("local_controller", "deterministic desktop operations")]
        if stage.type in {
            StageType.CAPTURE_ARTIFACT, StageType.SAVE_ARTIFACT,
            StageType.SEND_ARTIFACT, StageType.VERIFY_ARTIFACT, StageType.VERIFY_DELIVERY,
        }:
            choices.append(ComponentChoice("artifact_store", "managed artifact identity"))
        if stage.type in {StageType.DRAW_ARTIFACT, StageType.CREATE_OR_EDIT_ARTIFACT, StageType.VERIFY_ARTIFACT}:
            choices.append(ComponentChoice("llm_visual_stage", "semantic content judgement"))
        if stage.type in {
            StageType.OPEN_CONVERSATION, StageType.RESOLVE_RECIPIENT,
            StageType.SEND_TEXT, StageType.SEND_ARTIFACT, StageType.VERIFY_DELIVERY,
        }:
            adapter = {"qq": "qq_adapter", "wechat": "wechat_adapter"}.get(channel.casefold())
            if adapter:
                choices.append(ComponentChoice(adapter, f"{channel} communication domain"))
        return choices

    @staticmethod
    def _select_verifier_paths(stage: TaskStage, exposed: set[str]) -> tuple[set[str], dict[str, str]]:
        """Select one satisfiable, low-cost provider path per obligation."""
        requirements: set[str] = set()
        selected: dict[str, str] = {}
        for obligation in stage.obligations:
            ranked = sorted(
                enumerate(obligation.evidence_providers),
                key=lambda item: (
                    len(set(item[1].capabilities) - exposed),
                    len(item[1].capabilities),
                    item[0],
                ),
            )
            if not ranked:
                continue
            provider = ranked[0][1]
            selected[obligation.id] = provider.id
            requirements.update(provider.capabilities)
        return requirements, selected

    def plan_stage(self, stage: TaskStage, *, channel: str = "") -> CapabilitySelection:
        required = set(stage.required_capabilities) | set(_STAGE_BASE.get(stage.type, set()))
        choices = self._components_for(stage, channel)
        exposed: set[str] = set()
        for choice in choices:
            exposed.update(_COMPONENT_CAPABILITIES.get(choice.name, set()))
        verifier, selected_providers = self._select_verifier_paths(stage, exposed)
        combined = required | verifier
        missing = combined - exposed

        expansions: list[str] = []
        # A semantic executor is the bounded fallback for unknown UI stages,
        # but it does not invent communication or artifact-store capabilities.
        if missing & _COMPONENT_CAPABILITIES["llm_visual_stage"] and all(
            choice.name != "llm_visual_stage" for choice in choices
        ):
            choices.append(ComponentChoice("llm_visual_stage", "satisfiability expansion"))
            exposed.update(_COMPONENT_CAPABILITIES["llm_visual_stage"])
            expansions.append("added llm_visual_stage")
            missing = combined - exposed

        tools = sorted({
            tool
            for capability in combined
            for tool in CAPABILITY_TO_TOOLS.get(capability, ())
        })
        adapter_choice = next((choice.name for choice in choices if choice.name.endswith("_adapter")), "")
        model_role = "semantic_stage" if any(choice.name == "llm_visual_stage" for choice in choices) else "none"
        if stage.type == StageType.DRAW_ARTIFACT:
            # The model describes a semantic, canvas-relative scene.  Raw
            # pointer primitives, target binding, fresh frames, and semantic
            # verification stay behind the local Controller transaction.  This
            # reserves the two model decisions for base composition and one
            # bounded repair instead of rediscovering/focusing the same window.
            model_tools = [
                "execute_subgoal", "done", "blocked", "fail",
            ]
        elif model_role == "semantic_stage":
            model_tools = tools
        else:
            model_tools = []
        return CapabilitySelection(
            required_capabilities=sorted(required),
            verifier_requirements=sorted(verifier),
            exposed_capabilities=sorted(exposed),
            missing_capabilities=sorted(missing),
            tools=tools,
            model_tools=model_tools,
            controller_tools=tools,
            controller="stage_runtime+local_controller",
            adapter=adapter_choice,
            model_role=model_role,
            satisfiable=not missing,
            expansions=expansions,
            selected_evidence_providers=selected_providers,
        )

    def plan_contract(self, contract: TaskContract) -> TaskContract:
        stages: list[TaskStage] = []
        missing: set[str] = set()
        for stage in contract.stages:
            selection = self.plan_stage(stage, channel=contract.intent.channel)
            stages.append(stage.model_copy(update={"execution": selection}))
            missing.update(selection.missing_capabilities)
        return contract.model_copy(update={
            "stages": stages,
            "satisfiable": not missing,
            "missing_capabilities": sorted(missing),
        })

    @staticmethod
    def assert_satisfiable(contract: TaskContract) -> None:
        missing = set(contract.missing_capabilities)
        for stage in contract.stages:
            if stage.execution:
                missing.update(stage.execution.missing_capabilities)
                combined = set(stage.execution.required_capabilities) | set(stage.execution.verifier_requirements)
                if not combined <= set(stage.execution.exposed_capabilities):
                    missing.update(combined - set(stage.execution.exposed_capabilities))
        if missing or not contract.satisfiable:
            raise CapabilityUnsatisfiedError(
                "task contract has no executable path; missing capabilities: "
                + ", ".join(sorted(missing or {"unknown"}))
            )


def tools_for_capabilities(capabilities: Iterable[str]) -> set[str]:
    return {
        tool for capability in capabilities
        for tool in CAPABILITY_TO_TOOLS.get(str(capability), ())
    }
