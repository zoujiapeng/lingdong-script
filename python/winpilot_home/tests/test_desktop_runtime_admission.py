"""Pure contract tests for the single desktop-runtime admission owner."""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from winpilot.orchestrator.runtime_admission import (
    CANONICAL_RUNTIME_SERVICES,
    DesktopRuntimeAdmission,
    RuntimeCapabilitySnapshot,
    vnext_full_goal_stage,
)
from winpilot.orchestrator.runtime_selection import DesktopRuntimeSelection
from winpilot.tasking.capability import (
    CapabilityPlanner,
    CapabilityUnsatisfiedError,
)
from winpilot.tasking.compiler import TaskContractCompiler
from winpilot.tasking.contracts import StageType, TaskContract
from winpilot.vnext.capabilities.live_catalog import (
    LIVE_CONTROLLER_TOOL_NAMES,
    build_live_catalog,
)
from winpilot.vnext.capabilities.registry import (
    CapabilityOwner,
    RuntimeCapabilityRegistry,
)
from winpilot.vnext.mission import project_legacy_contract


def _full_goal_contract() -> TaskContract:
    return TaskContractCompiler().compile(
        "Complete the task visible in the current window.",
        require_gui=True,
        cold_start=True,
        max_steps=8,
    )


def _snapshot(
    *,
    capabilities: tuple[str, ...] = ("visual.interact", "visual.read_text"),
    services: tuple[str, ...] | None = None,
) -> RuntimeCapabilitySnapshot:
    return RuntimeCapabilitySnapshot(
        runtime_version="vnext",
        catalog=build_live_catalog(),
        capability_registry=RuntimeCapabilityRegistry((
            CapabilityOwner("live-visual-runtime", capabilities),
        )),
        controller_tool_names=LIVE_CONTROLLER_TOOL_NAMES,
        available_services=(
            tuple(sorted(CANONICAL_RUNTIME_SERVICES))
            if services is None
            else services
        ),
    )


def test_snapshot_serialization_and_fingerprint_ignore_capability_input_order() -> None:
    expected = _snapshot()
    rng = random.Random(230_917)

    for _ in range(50):
        capabilities = ["visual.interact", "visual.read_text"]
        rng.shuffle(capabilities)
        replayed = _snapshot(capabilities=tuple(capabilities))

        assert replayed.to_dict() == expected.to_dict()
        assert replayed.fingerprint == expected.fingerprint
        assert replayed.available_capabilities == (
            "visual.interact",
            "visual.read_text",
        )


def test_vnext_rejects_an_unknown_canonical_mission_capability(monkeypatch) -> None:
    from winpilot.orchestrator import runtime_admission as admission_module

    monkeypatch.setattr(
        admission_module,
        "project_legacy_contract",
        lambda *_args, **_kwargs: SimpleNamespace(obligations=(
            SimpleNamespace(
                required=True,
                required_capabilities=(
                    "visual.interact",
                    "visual.read_text",
                    "visual.not_registered",
                ),
            ),
        )),
    )

    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="unknown canonical capability",
    ):
        DesktopRuntimeAdmission.admit(
            DesktopRuntimeSelection.from_config("vnext"),
            _full_goal_contract(),
            runtime_snapshot=_snapshot(),
        )


def test_vnext_rejects_missing_visual_read_text_capability() -> None:
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match=r"missing canonical capabilities: visual\.read_text",
    ):
        DesktopRuntimeAdmission.admit(
            DesktopRuntimeSelection.from_config("vnext"),
            _full_goal_contract(),
            runtime_snapshot=_snapshot(capabilities=("visual.interact",)),
        )


def test_vnext_rejects_a_missing_required_cognitive_service() -> None:
    services = tuple(
        service
        for service in sorted(CANONICAL_RUNTIME_SERVICES)
        if service != "mission_verifier"
    )

    with pytest.raises(
        CapabilityUnsatisfiedError,
        match=r"missing required services: mission_verifier",
    ):
        DesktopRuntimeAdmission.admit(
            DesktopRuntimeSelection.from_config("vnext"),
            _full_goal_contract(),
            runtime_snapshot=_snapshot(services=services),
        )


@pytest.mark.parametrize(
    "controller_tools",
    (("click",), ("click", "double_click", "type_text")),
)
def test_snapshot_rejects_catalog_controller_gui_tool_disagreement(
    controller_tools: tuple[str, ...],
) -> None:
    with pytest.raises(
        ValueError,
        match="catalog/controller GUI tools differ",
    ):
        RuntimeCapabilitySnapshot(
            runtime_version="vnext",
            catalog=build_live_catalog(),
            capability_registry=RuntimeCapabilityRegistry((
                CapabilityOwner(
                    "live-visual-runtime",
                    ("visual.interact", "visual.read_text"),
                ),
            )),
            controller_tool_names=controller_tools,
            available_services=tuple(sorted(CANONICAL_RUNTIME_SERVICES)),
        )


def test_legacy_admission_delegates_existing_checks_in_their_original_order(
    monkeypatch,
) -> None:
    calls: list[tuple[str, TaskContract]] = []
    contract = _full_goal_contract()
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_satisfiable",
        staticmethod(lambda value: calls.append(("static", value))),
    )
    monkeypatch.setattr(
        CapabilityPlanner,
        "assert_runtime_satisfiable",
        staticmethod(lambda value: calls.append(("runtime", value))),
    )

    decision = DesktopRuntimeAdmission.admit(
        DesktopRuntimeSelection.from_config("legacy"),
        contract,
    )

    assert calls == [("static", contract), ("runtime", contract)]
    assert decision.runtime_version == "legacy"
    assert decision.reasons == ("legacy_static_and_runtime_preflight_passed",)


def test_vnext_ignores_legacy_satisfiability_fields_but_keeps_coverage_guard() -> None:
    contract = _full_goal_contract()
    contract.satisfiable = False
    contract.missing_capabilities = ["legacy.fake_missing"]
    assert contract.stages[0].execution is not None
    contract.stages[0].execution.satisfiable = False
    contract.stages[0].execution.missing_capabilities = ["legacy.fake_missing"]
    contract.stages[0].execution.exposed_capabilities = []

    decision = DesktopRuntimeAdmission.admit(
        DesktopRuntimeSelection.from_config("vnext"),
        contract,
        runtime_snapshot=_snapshot(),
    )

    assert decision.admitted is True
    assert decision.required_capabilities == (
        "visual.interact",
        "visual.read_text",
    )

    contract.requirement_coverage.complete = False
    contract.requirement_coverage.covered_requirement_ids = []
    contract.requirement_coverage.uncovered_requirement_ids = [
        contract.requirements[0].id
    ]
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="uncovered user requirements",
    ):
        DesktopRuntimeAdmission.admit(
            DesktopRuntimeSelection.from_config("vnext"),
            contract,
            runtime_snapshot=_snapshot(),
        )


def test_real_legacy_unknown_capability_stays_metadata_and_does_not_reject() -> None:
    contract = _full_goal_contract()
    contract.stages[0].required_capabilities.append("legacy.opaque_capability")
    definition = project_legacy_contract(
        contract,
        mission_id="admission-test-mission",
        run_id="admission-test-run",
    )

    assert "legacy.opaque_capability" in definition.metadata[
        "legacy_unknown_capabilities"
    ]
    decision = DesktopRuntimeAdmission.admit(
        DesktopRuntimeSelection.from_config("vnext"),
        contract,
        runtime_snapshot=_snapshot(),
    )
    assert decision.admitted is True


def test_vnext_requires_a_contract_and_one_full_goal_desktop_stage() -> None:
    selection = DesktopRuntimeSelection.from_config("vnext")
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="requires task-contract/1",
    ):
        DesktopRuntimeAdmission.admit(
            selection,
            None,
            runtime_snapshot=_snapshot(),
        )

    contract = _full_goal_contract()
    contract.stages[0].type = StageType.OPEN_APPLICATION
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="one full-goal ExecuteDesktopTask",
    ):
        vnext_full_goal_stage(contract)
    with pytest.raises(
        CapabilityUnsatisfiedError,
        match="one full-goal ExecuteDesktopTask",
    ):
        DesktopRuntimeAdmission.admit(
            selection,
            contract,
            runtime_snapshot=_snapshot(),
        )
