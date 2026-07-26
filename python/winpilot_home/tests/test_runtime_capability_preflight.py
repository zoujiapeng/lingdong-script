from __future__ import annotations

import pytest


def _fictional_communication_provider_type():
    from winpilot.tasking.adapters import CommunicationAdapter

    class NebulaRelayProvider(CommunicationAdapter):
        id = "nebula_relay"
        app_name = "Nebula Relay"
        channels = ("Nebula Relay",)

        def open_conversation(self, recipient: str, *, stage_id: str = ""):
            return {"ok": True, "recipient": recipient}

        def resolve_composer(self):
            return {"ok": True}

        def paste_or_attach_artifact(self, artifact_id: str, *, stage_id: str = ""):
            return {"ok": True, "artifact_id": artifact_id}

        def send_current_composer(self, artifact_id: str = "", *, stage_id: str = ""):
            return {"ok": True, "artifact_id": artifact_id}

        def send_text(self, text: str, *, stage_id: str = ""):
            return {"ok": True, "text": text}

        def verify_latest_outbound(
            self,
            *,
            artifact_id: str = "",
            text: str = "",
            stage_id: str = "",
        ):
            return {"ok": True, "artifact_id": artifact_id, "text": text}

    return NebulaRelayProvider


def test_visual_model_runtime_preflight_requires_a_multimodal_provider(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.tasking.capability import (
        CapabilityPlanner,
        CapabilityUnsatisfiedError,
    )
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "拖动当前界面里的滑块到目标区域",
        cold_start=True,
    )
    assert "desktop.execute" in {
        capability
        for stage in contract.stages
        for capability in stage.execution.required_capabilities
    }

    monkeypatch.setattr(vlm, "is_configured", lambda: False)
    assert "runtime.multimodal_model" in (
        CapabilityPlanner.runtime_missing_capabilities(contract)
    )
    with pytest.raises(CapabilityUnsatisfiedError, match="runtime.multimodal_model"):
        CapabilityPlanner.assert_runtime_satisfiable(contract)


def test_static_and_live_capability_checks_are_separate(monkeypatch, isolated_config):
    from types import SimpleNamespace

    from winpilot.perception import vlm
    from winpilot.tasking import capability as capability_mod
    from winpilot.tasking.capability import CapabilityPlanner
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "点击当前页面中可见的继续按钮",
        cold_start=True,
    )
    CapabilityPlanner.assert_satisfiable(contract)
    isolated_config({
        "api": {
            "base_url": "https://model.test/v1",
            "api_key": "test-api-key",
            "model": "test-text-model",
        },
    })
    # The live half of this test needs explicit provider and platform facts.
    # Never inherit a developer machine's configured model, model metadata, or
    # the CI host OS when asserting that a fully available runtime has no gaps.
    monkeypatch.setattr(capability_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        vlm,
        "_settings",
        lambda: {
            "enabled": True,
            "provider": "hermetic-test",
            "model": "hermetic-vision-model",
            "supports_vision": True,
        },
    )
    assert CapabilityPlanner.runtime_missing_capabilities(contract) == set()


def test_semantic_verifier_fails_preflight_when_vlm_is_unconfigured(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.tasking.capability import (
        CapabilityPlanner,
        CapabilityUnsatisfiedError,
    )
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("画一个任意几何图案", cold_start=True)
    assert "visual.semantic_verify" in {
        capability
        for stage in contract.stages
        for capability in stage.execution.verifier_requirements
    }

    monkeypatch.setattr(vlm, "is_configured", lambda: False)
    with pytest.raises(CapabilityUnsatisfiedError, match="runtime.multimodal_model"):
        CapabilityPlanner.assert_runtime_satisfiable(contract)


def test_semantic_verifier_rejects_configured_text_only_model(monkeypatch):
    from winpilot.perception import vlm
    from winpilot.tasking.capability import (
        CapabilityPlanner,
        CapabilityUnsatisfiedError,
    )
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("画一个任意几何图案", cold_start=True)
    monkeypatch.setattr(vlm, "is_configured", lambda: True)
    monkeypatch.setattr(
        vlm,
        "_settings",
        lambda: {
            "enabled": True,
            "base_url": "https://text-only.example/v1",
            "model": "text-only-model",
        },
    )

    report = CapabilityPlanner.runtime_provider_report(contract)
    assert report["visual_model"]["configured"] is True
    assert report["visual_model"]["supports_vision"] is False
    assert "runtime.multimodal_vision_support" in report["missing_capabilities"]
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="runtime.multimodal_vision_support",
    ):
        CapabilityPlanner.assert_runtime_satisfiable(contract)


def test_selected_unregistered_communication_adapter_fails_before_run(monkeypatch):
    from winpilot.tasking.adapters import DomainAdapterRegistry
    from winpilot.tasking.capability import (
        CapabilityPlanner,
        CapabilityUnsatisfiedError,
    )
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.intent import TaskIntentParser

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    provider_type = _fictional_communication_provider_type()
    DomainAdapterRegistry.register(provider_type)
    parser = TaskIntentParser(semantic_extractor=lambda _task: {
        "channel": "Nebula Relay",
        "recipient": "Test Contact",
    })
    contract = TaskContractCompiler(parser=parser).compile(
        "Send a message to Test Contact.",
        cold_start=True,
    )
    communication_adapters = {
        stage.execution.adapter
        for stage in contract.stages
        if stage.execution
        and any(
            capability.startswith((
                "conversation.", "recipient.", "communication.", "delivery.",
            ))
            for capability in (
                stage.execution.required_capabilities
                + stage.execution.verifier_requirements
            )
        )
    }
    assert communication_adapters == {"nebula_relay_adapter"}
    assert DomainAdapterRegistry.unregister(provider_type) is True

    report = CapabilityPlanner.runtime_provider_report(contract)
    assert (
        "runtime.communication_adapter.nebula_relay_adapter"
        in report["missing_capabilities"]
    )
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="runtime.communication_adapter.nebula_relay_adapter",
    ):
        CapabilityPlanner.assert_runtime_satisfiable(contract)


def test_adapter_registry_probe_does_not_claim_login_or_installation(monkeypatch) -> None:
    from winpilot.tasking.adapters import DomainAdapterRegistry

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    assert DomainAdapterRegistry.probe_registration(
        channel="Nebula Relay",
        component="nebula_relay_adapter",
    )["registered"] is False

    provider_type = _fictional_communication_provider_type()
    DomainAdapterRegistry.register(provider_type)
    probe = DomainAdapterRegistry.probe_registration(
        channel="Nebula Relay",
        component="nebula_relay_adapter",
    )

    assert probe["registered"] is True
    assert probe["probe_scope"] == "static_registry_only"
    limitations = " ".join(probe["runtime_limitations"]).casefold()
    assert "installed" in limitations
    assert "login" in limitations
    assert DomainAdapterRegistry.unregister(provider_type) is True
    assert DomainAdapterRegistry.probe_registration(
        channel="Nebula Relay",
        component="nebula_relay_adapter",
    )["registered"] is False


def test_desktop_stage_runtime_profile_does_not_reclassify_generated_goal(monkeypatch) -> None:
    from winpilot.agent import runtime_profile
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "在当前界面拖动可见对象到目标区域",
        cold_start=True,
    )
    stage = next(
        item for item in contract.stages
        if item.type.value == "ExecuteDesktopTask"
    )

    def unexpected_raw_classifier(_task: str) -> bool:
        raise AssertionError("typed stage must not invoke raw task classifiers")

    monkeypatch.setattr(runtime_profile, "_is_coding_task", unexpected_raw_classifier)
    monkeypatch.setattr(runtime_profile, "_is_gui_only_task", unexpected_raw_classifier)

    profile = runtime_profile.build_runtime_profile(
        "inspect and patch repository code",
        task_contract=contract,
        stage=stage,
    )

    assert profile.coding is False
    assert profile.stage_type == "ExecuteDesktopTask"


def test_legacy_runtime_profile_keeps_raw_classifier_fallback(monkeypatch) -> None:
    from winpilot.agent import runtime_profile

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runtime_profile,
        "_is_coding_task",
        lambda task: calls.append(("coding", task)) or True,
    )
    monkeypatch.setattr(
        runtime_profile,
        "_is_gui_only_task",
        lambda task: calls.append(("gui", task)) or False,
    )

    profile = runtime_profile.build_runtime_profile("legacy direct coding task")

    assert profile.coding is True
    assert calls == [
        ("coding", "legacy direct coding task"),
        ("gui", "legacy direct coding task"),
    ]
