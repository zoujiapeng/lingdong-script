from __future__ import annotations

import pytest


def test_interaction_runtime_preflight_requires_a_multimodal_provider(monkeypatch):
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
    assert "interaction.infer" in {
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


def test_static_and_live_capability_checks_are_separate(monkeypatch):
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

    contract = TaskContractCompiler().compile(
        "用QQ给测试联系人发送消息",
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
    assert communication_adapters == {"qq_adapter"}
    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})

    report = CapabilityPlanner.runtime_provider_report(contract)
    assert "runtime.communication_adapter.qq_adapter" in report["missing_capabilities"]
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="runtime.communication_adapter.qq_adapter",
    ):
        CapabilityPlanner.assert_runtime_satisfiable(contract)


def test_adapter_registry_probe_does_not_claim_login_or_installation() -> None:
    from winpilot.tasking.adapters import DomainAdapterRegistry

    probe = DomainAdapterRegistry.probe_registration(
        channel="QQ",
        component="qq_adapter",
    )

    assert probe["registered"] is True
    assert probe["probe_scope"] == "static_registry_only"
    limitations = " ".join(probe["runtime_limitations"]).casefold()
    assert "installed" in limitations
    assert "login" in limitations


def test_typed_stage_runtime_profile_does_not_reclassify_generated_goal(monkeypatch) -> None:
    from winpilot.agent import runtime_profile
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "在当前界面拖动可见对象到目标区域",
        cold_start=True,
    )
    stage = next(item for item in contract.stages if item.type.value == "InferInteraction")

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
    assert profile.stage_type == "InferInteraction"


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
