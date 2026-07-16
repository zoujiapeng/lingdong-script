"""Capability selection and preflight satisfiability for typed task stages."""
from __future__ import annotations

from dataclasses import dataclass
import os
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
    "visual.bind_target": ("list_windows", "focus_window", "observe"),
    "visual.region_bind": ("observe",),
    "interaction.infer": ("observe", "vlm_describe"),
    "interaction.perform": ("execute_subgoal", "run_action_batch"),
    "interaction.verify_local_effect": ("observe", "vlm_verify"),
    "interaction.wait_external_change": (
        "wait_for_visual_change", "run_action_batch", "observe",
    ),
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
        "interaction.infer",
    },
    "interaction_controller": {
        "window.focus", "visual.observe", "visual.bind_target", "visual.region_bind",
        "controller.transaction", "interaction.perform",
        "interaction.verify_local_effect", "interaction.wait_external_change",
    },
}

_COMMUNICATION_ADAPTER_CAPABILITIES = {
    "app.open", "window.focus", "visual.observe", "visual.read_text",
    "controller.transaction", "conversation.open", "recipient.resolve",
    "communication.send_text", "communication.send_artifact",
    "delivery.send_action", "delivery.verify", "visual.artifact_match",
    "artifact.read", "artifact.hash",
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
    StageType.OBSERVE_SCENE: {
        "window.focus", "visual.observe", "visual.bind_target", "visual.region_bind",
    },
    StageType.INFER_INTERACTION: {"visual.observe", "interaction.infer"},
    StageType.PERFORM_TURN: {"interaction.perform", "controller.transaction", "visual.observe"},
    StageType.VERIFY_LOCAL_EFFECT: {
        "interaction.verify_local_effect", "visual.semantic_verify", "visual.observe",
    },
    StageType.WAIT_FOR_EXTERNAL_CHANGE: {"interaction.wait_external_change", "visual.observe"},
}


class CapabilityUnsatisfiedError(ValueError):
    pass


@dataclass(frozen=True)
class ComponentChoice:
    name: str
    reason: str


class CapabilityPlanner:
    @staticmethod
    def _component_capabilities(component: str) -> set[str]:
        if component.endswith("_adapter"):
            from .adapters import DomainAdapterRegistry

            probe = DomainAdapterRegistry.probe_registration(component=component)
            return set(_COMMUNICATION_ADAPTER_CAPABILITIES) if probe["registered"] else set()
        return set(_COMPONENT_CAPABILITIES.get(component, set()))

    def _components_for(self, stage: TaskStage, channel: str) -> list[ComponentChoice]:
        choices = [ComponentChoice("local_controller", "deterministic desktop operations")]
        if stage.type in {
            StageType.OBSERVE_SCENE, StageType.INFER_INTERACTION,
            StageType.PERFORM_TURN, StageType.VERIFY_LOCAL_EFFECT,
            StageType.WAIT_FOR_EXTERNAL_CHANGE,
        }:
            choices.append(ComponentChoice("interaction_controller", "target-bound visual interaction session"))
        if stage.type in {
            StageType.CAPTURE_ARTIFACT, StageType.SAVE_ARTIFACT,
            StageType.SEND_ARTIFACT, StageType.VERIFY_ARTIFACT, StageType.VERIFY_DELIVERY,
        }:
            choices.append(ComponentChoice("artifact_store", "managed artifact identity"))
        if stage.type in {
            StageType.DRAW_ARTIFACT, StageType.CREATE_OR_EDIT_ARTIFACT, StageType.VERIFY_ARTIFACT,
            StageType.INFER_INTERACTION, StageType.VERIFY_LOCAL_EFFECT,
        }:
            choices.append(ComponentChoice("llm_visual_stage", "semantic content judgement"))
        if stage.type in {
            StageType.OPEN_CONVERSATION, StageType.RESOLVE_RECIPIENT,
            StageType.SEND_TEXT, StageType.SEND_ARTIFACT, StageType.VERIFY_DELIVERY,
        }:
            from .adapters import DomainAdapterRegistry

            adapter = DomainAdapterRegistry.component_for_channel(channel)
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
            exposed.update(self._component_capabilities(choice.name))
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
        if stage.type == StageType.INFER_INTERACTION:
            model_role = "interaction_semantic_planner"
        elif stage.type == StageType.VERIFY_LOCAL_EFFECT:
            model_role = "interaction_semantic_verifier"
        elif stage.type == StageType.DRAW_ARTIFACT:
            model_role = "visual_artifact_planner"
        else:
            model_role = (
                "semantic_stage"
                if any(choice.name == "llm_visual_stage" for choice in choices)
                else "none"
            )
        if stage.type == StageType.DRAW_ARTIFACT:
            # The model describes a semantic, canvas-relative scene.  Raw
            # pointer primitives, target binding, fresh frames, and semantic
            # verification stay behind the local Controller transaction.  This
            # reserves the two model decisions for base composition and one
            # bounded repair instead of rediscovering/focusing the same window.
            model_tools = [
                "execute_subgoal", "done", "blocked", "fail",
            ]
        elif stage.type in {
            StageType.INFER_INTERACTION,
            StageType.PERFORM_TURN,
            StageType.VERIFY_LOCAL_EFFECT,
            StageType.WAIT_FOR_EXTERNAL_CHANGE,
            StageType.OBSERVE_SCENE,
        }:
            # Interaction stages use a strict JSON VLM boundary and a local
            # state machine.  No raw desktop tools are exposed to that model;
            # the Controller receives the validated typed transaction only.
            model_tools = []
        elif model_role == "semantic_stage":
            model_tools = tools
        else:
            model_tools = []
        controller_tools = sorted(
            tool for tool in tools
            if tool not in {"vlm_describe", "vlm_verify"}
        )
        return CapabilitySelection(
            required_capabilities=sorted(required),
            verifier_requirements=sorted(verifier),
            exposed_capabilities=sorted(exposed),
            missing_capabilities=sorted(missing),
            tools=tools,
            model_tools=model_tools,
            controller_tools=controller_tools,
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

    @staticmethod
    def runtime_provider_report(contract: TaskContract) -> dict[str, object]:
        """Probe non-negotiable runtime providers before a run is queued.

        Static satisfiability proves that an implementation path exists in the
        selected profile.  This second check proves that the local machine can
        actually instantiate the providers that path selected.  It stays
        deliberately narrow: an application window may legitimately be opened
        by an earlier stage, while an absent Windows input runtime or visual
        model cannot be repaired from inside the graph.
        """
        required = {
            capability
            for stage in contract.stages
            for capability in (
                (stage.execution.required_capabilities if stage.execution else [])
                + (stage.execution.verifier_requirements if stage.execution else [])
            )
        }
        missing: set[str] = set()
        limitations: set[str] = set()
        adapter_probes: list[dict[str, object]] = []
        desktop_required = any(
            capability.startswith((
                "app.", "window.", "controller.", "visual.",
                "interaction.", "conversation.", "recipient.",
                "communication.", "delivery.",
            ))
            for capability in required
        )
        if desktop_required and os.name != "nt":
            missing.add("runtime.windows_desktop")

        semantic_vision_required = bool(
            {"interaction.infer", "visual.semantic_verify"} & required
        )
        visual_model: dict[str, object] = {
            "required": semantic_vision_required,
            "configured": False,
            "supports_vision": False,
            "model": "",
        }
        if semantic_vision_required:
            from ..model_adapter import model_supports_vision
            from ..perception import vlm

            configured = bool(vlm.is_configured())
            settings = dict(vlm._settings() or {})  # resolved, secret-free fields are copied below
            model = str(settings.get("model") or "")
            supports_vision = bool(
                settings.get("supports_vision") is True
                or model_supports_vision(model, str(settings.get("provider") or ""))
            )
            visual_model.update({
                "configured": configured,
                "supports_vision": supports_vision,
                "model": model,
            })
            if not configured:
                missing.add("runtime.multimodal_model")
            elif not supports_vision:
                missing.add("runtime.multimodal_vision_support")

        communication_required = any(
            capability.startswith(("conversation.", "recipient.", "communication.", "delivery."))
            for capability in required
        )
        if communication_required:
            from .adapters import DomainAdapterRegistry

            seen: set[tuple[str, str]] = set()
            for stage in contract.stages:
                execution = stage.execution
                stage_capabilities = set(
                    (execution.required_capabilities if execution else [])
                    + (execution.verifier_requirements if execution else [])
                )
                if not any(
                    capability.startswith(("conversation.", "recipient.", "communication.", "delivery."))
                    for capability in stage_capabilities
                ):
                    continue
                channel = str(stage.parameters.get("app") or contract.intent.channel or "").strip()
                component = str(execution.adapter if execution else "").strip()
                key = (channel.casefold(), component.casefold())
                if key in seen:
                    continue
                seen.add(key)
                probe = DomainAdapterRegistry.probe_registration(
                    channel=channel,
                    component=component,
                )
                adapter_probes.append(probe)
                limitations.update(str(item) for item in probe["runtime_limitations"])
                if not probe["registered"]:
                    identity = component or channel or "unselected"
                    slug = "".join(
                        character if character.isalnum() else "_"
                        for character in identity.casefold()
                    ).strip("_") or "unselected"
                    missing.add(f"runtime.communication_adapter.{slug}")

        return {
            "required_capabilities": sorted(required),
            "missing_capabilities": sorted(missing),
            "runtime_limitations": sorted(limitations),
            "visual_model": visual_model,
            "adapter_probes": adapter_probes,
        }

    @classmethod
    def runtime_missing_capabilities(cls, contract: TaskContract) -> set[str]:
        return set(cls.runtime_provider_report(contract)["missing_capabilities"])

    @classmethod
    def assert_runtime_satisfiable(cls, contract: TaskContract) -> None:
        missing = cls.runtime_missing_capabilities(contract)
        if missing:
            raise CapabilityUnsatisfiedError(
                "task contract is statically valid but runtime providers are unavailable: "
                + ", ".join(sorted(missing))
            )


def tools_for_capabilities(capabilities: Iterable[str]) -> set[str]:
    return {
        tool for capability in capabilities
        for tool in CAPABILITY_TO_TOOLS.get(str(capability), ())
    }
