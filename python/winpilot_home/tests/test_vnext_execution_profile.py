"""Pure ExecutionProfile contract and deterministic policy tests."""
from __future__ import annotations

import ast
from collections import UserDict
from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import random

import pytest

from winpilot.vnext.capability_names import (
    InvalidCapability,
    canonical_capabilities,
)
from winpilot.vnext.profiling import (
    CognitiveServices,
    DEFAULT_CAPABILITY_CATALOG,
    ExecutionDepth,
    ExecutionPolicy,
    ExecutionProfile,
    InvalidProfileSchema,
    TaskDimensions,
    TaskProfileProposal,
    UnsupportedCapability,
)


ALL_PROVIDERS = tuple(CognitiveServices._FIELDS)


def _proposal(**overrides) -> TaskProfileProposal:
    values = {
        "dimensions": TaskDimensions(),
        "recommended_depth": ExecutionDepth.STANDARD,
        "minimum_depth": ExecutionDepth.DIRECT,
        "requested_services": CognitiveServices(),
        "broad_capability_hypotheses": ("visual.interact",),
        "upgrade_triggers": ("new cross-application dependency",),
        "downgrade_conditions": ("evidence becomes deterministic",),
        "confidence": 0.95,
        "rationale": ("ordinary semantic pressure",),
    }
    values.update(overrides)
    return TaskProfileProposal(**values)


def test_contracts_are_strict_versioned_and_round_trip_canonically() -> None:
    proposal = _proposal()
    assert TaskProfileProposal.from_dict(proposal.to_dict()) == proposal
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m1", goal_revision=3
    )
    assert ExecutionProfile.from_dict(profile.to_dict()) == profile
    assert proposal.format == "task-profile-proposal/0"
    assert profile.format == "execution-profile/0"

    for invalid in (math.nan, math.inf, -0.1, 1.1, True):
        raw = {**proposal.to_dict(), "confidence": invalid}
        with pytest.raises(InvalidProfileSchema):
            TaskProfileProposal.from_dict(raw)
    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({**proposal.to_dict(), "format": "task-profile-proposal/9"})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "format": "execution-profile/9"})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "unexpected": True})


@pytest.mark.parametrize(
    "field,value",
    (
        ("runtime_version", "vnext"),
        ("application_workflow", ["open", "search"]),
        ("obligation_patch", {"add": "x"}),
        ("button", "submit"),
        ("tool", "click"),
        ("coordinate", [10, 20]),
        ("action", {"kind": "click"}),
        ("run_id", "run-1"),
        ("mission_id", "mission-1"),
        ("frame_id", "frame-1"),
    ),
)
def test_proposal_rejects_runtime_gui_plan_and_identity_authority(field, value) -> None:
    with pytest.raises(InvalidProfileSchema, match="forbidden|non-canonical"):
        TaskProfileProposal.from_dict({**_proposal().to_dict(), field: value})


def test_nested_forbidden_authority_is_rejected_before_policy() -> None:
    raw = _proposal().to_dict()
    raw["rationale"] = [{"runtime_version": "legacy"}]
    with pytest.raises(InvalidProfileSchema, match="runtime_version"):
        TaskProfileProposal.from_dict(raw)


@pytest.mark.parametrize(
    "proposal,reason",
    (
        (None, "proposal_missing"),
        ({"not": "canonical"}, "proposal_schema_invalid"),
        (_proposal(confidence=0.2), "proposal_low_confidence"),
        (
            _proposal(broad_capability_hypotheses=("vendor.secret",)),
            "proposal_unknown_capability:",
        ),
    ),
)
def test_policy_safe_standard_fallback_is_audited(proposal, reason) -> None:
    profile = ExecutionPolicy(min_confidence=0.6).evaluate(
        proposal, mission_id="m1", goal_revision=0
    )
    assert profile.source == "safe_default"
    assert profile.accepted_depth is ExecutionDepth.STANDARD
    assert profile.services == CognitiveServices(
        planner=False,
        obligation_verifier=False,
        mission_verifier=False,
        visual_inspector=False,
        deep_reasoning=False,
    )
    assert any(item.startswith(reason) for item in profile.policy_reasons)
    assert "provider_unavailable:obligation_verifier" in profile.policy_reasons


def test_unknown_or_noncanonical_capabilities_fail_closed() -> None:
    policy = ExecutionPolicy()
    with pytest.raises(UnsupportedCapability):
        policy.validate_proposal(
            _proposal(broad_capability_hypotheses=("vendor.secret",))
        )
    with pytest.raises(InvalidProfileSchema):
        _proposal(broad_capability_hypotheses=("ClickTool",))
    with pytest.raises(UnsupportedCapability):
        policy.evaluate(
            _proposal(),
            mission_id="m",
            goal_revision=0,
            available_capabilities=("visual.interact", "vendor.secret"),
        )


def test_missing_runtime_capability_facts_fail_closed() -> None:
    policy = ExecutionPolicy(provider_availability=("obligation_verifier",))

    accepted = policy.evaluate(
        _proposal(),
        mission_id="m",
        goal_revision=0,
    )
    fallback = policy.safe_default("m", 0)
    explicit = policy.safe_default(
        "m",
        0,
        available_capabilities=("visual.interact",),
    )

    assert accepted.accepted_capabilities == ()
    assert "capability_unavailable:visual.interact" in accepted.policy_reasons
    assert fallback.accepted_capabilities == ()
    assert "capability_unavailable:visual.interact" in fallback.policy_reasons
    assert explicit.accepted_capabilities == ("visual.interact",)


def test_policy_snapshots_can_only_be_tightened() -> None:
    closed = ExecutionPolicy(provider_availability=())
    with pytest.raises(InvalidProfileSchema, match="may only tighten"):
        closed.evaluate(
            _proposal(),
            mission_id="m",
            goal_revision=0,
            provider_availability=("planner",),
        )

    policy = ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    profile = policy.evaluate(
        _proposal(),
        mission_id="m",
        goal_revision=0,
        provider_availability=("obligation_verifier",),
    )
    assert profile.services == CognitiveServices(obligation_verifier=True)

    with pytest.raises(FrozenInstanceError):
        policy.provider_availability = frozenset()  # type: ignore[misc]


def test_catalog_and_accepted_profiles_use_the_single_canonical_owner() -> None:
    with pytest.raises(InvalidProfileSchema, match="unknown canonical"):
        ExecutionPolicy(capability_catalog=("visual.interact", "vendor.secret"))
    with pytest.raises(InvalidCapability, match="array"):
        canonical_capabilities({"visual.interact": False})

    profile = ExecutionPolicy().safe_default(
        "m",
        0,
        available_capabilities=("visual.interact",),
    )
    raw = profile.to_dict()
    raw["accepted_capabilities"] = ["vendor.secret"]
    with pytest.raises(InvalidProfileSchema, match="canonical"):
        ExecutionProfile.from_dict(raw)


def test_availability_normalization_rejects_alias_collisions() -> None:
    policy = ExecutionPolicy(provider_availability=("planner",))
    with pytest.raises(InvalidProfileSchema, match="duplicate normalized key"):
        policy.evaluate(
            _proposal(),
            mission_id="m",
            goal_revision=0,
            provider_availability={" planner": True, "planner": False},
        )
    with pytest.raises(InvalidProfileSchema, match="duplicate normalized key"):
        policy.evaluate(
            _proposal(),
            mission_id="m",
            goal_revision=0,
            available_capabilities={
                " visual.interact": True,
                "visual.interact": False,
            },
        )


def test_profile_id_binds_the_complete_canonical_profile() -> None:
    policy = ExecutionPolicy()
    first = policy.evaluate(
        _proposal(
            upgrade_triggers=("condition-a",),
            confidence=0.8,
        ),
        mission_id="m",
        goal_revision=0,
    )
    second = policy.evaluate(
        _proposal(
            upgrade_triggers=("condition-b",),
            confidence=0.9,
        ),
        mission_id="m",
        goal_revision=0,
    )

    assert first.profile_id != second.profile_id
    assert ExecutionProfile.from_dict(first.to_dict()) == first

    tampered = first.to_dict()
    tampered["confidence"] = 0.7
    with pytest.raises(InvalidProfileSchema, match="does not identify"):
        ExecutionProfile.from_dict(tampered)


def test_signed_zero_confidence_has_one_canonical_profile_identity() -> None:
    policy = ExecutionPolicy(min_confidence=0.0)
    negative = policy.evaluate(
        _proposal(confidence=-0.0),
        mission_id="m",
        goal_revision=0,
    )
    positive = policy.evaluate(
        _proposal(confidence=0.0),
        mission_id="m",
        goal_revision=0,
    )

    assert negative == positive
    assert negative.confidence == 0.0
    assert str(negative.confidence) == "0.0"
    assert negative.profile_id == positive.profile_id


def test_capability_permutations_have_one_canonical_profile_identity() -> None:
    policy = ExecutionPolicy()
    profile = policy.evaluate(
        _proposal(
            broad_capability_hypotheses=(
                "visual.read_text",
                "visual.interact",
            )
        ),
        mission_id="m",
        goal_revision=0,
        available_capabilities=(
            "visual.interact",
            "visual.read_text",
        ),
    )
    permuted = profile.to_dict()
    permuted["accepted_capabilities"] = list(
        reversed(permuted["accepted_capabilities"])
    )
    restored = ExecutionProfile.from_dict(permuted)

    assert restored == profile
    assert restored.structural_fingerprint == profile.structural_fingerprint


def test_malformed_json_mapping_and_deep_or_cyclic_values_fail_closed() -> None:
    policy = ExecutionPolicy()
    hostile = UserDict(_proposal().to_dict())
    mapped = policy.evaluate(hostile, mission_id="m", goal_revision=0)
    assert mapped.source == "safe_default"
    assert "proposal_schema_invalid" in mapped.policy_reasons

    deep: list[object] = []
    cursor = deep
    for _ in range(1200):
        child: list[object] = []
        cursor.append(child)
        cursor = child
    deep_raw = _proposal().to_dict()
    deep_raw["rationale"] = deep
    deep_result = policy.evaluate(deep_raw, mission_id="m", goal_revision=0)
    assert deep_result.source == "safe_default"

    cyclic: list[object] = []
    cyclic.append(cyclic)
    cyclic_raw = _proposal().to_dict()
    cyclic_raw["rationale"] = cyclic
    cyclic_result = policy.evaluate(cyclic_raw, mission_id="m", goal_revision=0)
    assert cyclic_result.source == "safe_default"


def test_provider_and_runtime_availability_can_only_tighten() -> None:
    proposal = _proposal(
        dimensions=TaskDimensions(
            step_dependency="multi_stage",
            application_scope="cross_application",
            artifact_flow="handoff",
            external_wait="persistent",
            irreversibility="high",
            visual_precision="geometric",
            continuous_pointer="cross_frame",
            completion_observability="weak",
            identity_binding="multiple",
            user_interaction="ongoing",
        ),
        recommended_depth=ExecutionDepth.ORCHESTRATED,
        minimum_depth=ExecutionDepth.STANDARD,
        requested_services=CognitiveServices(
            planner=True,
            obligation_verifier=True,
            mission_verifier=True,
            visual_inspector=True,
            deep_reasoning=True,
        ),
        broad_capability_hypotheses=("visual.interact", "pointer.dynamic"),
    )
    policy = ExecutionPolicy(provider_availability={"obligation_verifier": True})
    profile = policy.evaluate(
        proposal,
        mission_id="m1",
        goal_revision=1,
        available_capabilities=("visual.interact",),
    )
    assert profile.accepted_depth is ExecutionDepth.ORCHESTRATED
    assert profile.accepted_capabilities == ("visual.interact",)
    assert profile.services == CognitiveServices(
        planner=False,
        obligation_verifier=True,
        mission_verifier=False,
        visual_inspector=False,
        deep_reasoning=False,
    )
    assert "capability_unavailable:pointer.dynamic" in profile.policy_reasons
    assert "provider_unavailable:planner" in profile.policy_reasons
    with pytest.raises(InvalidProfileSchema):
        ExecutionPolicy(provider_availability=("unknown_role",))


def test_overprofiled_atomic_task_is_safely_reduced() -> None:
    proposal = _proposal(
        recommended_depth=ExecutionDepth.ORCHESTRATED,
        requested_services=CognitiveServices(
            planner=True,
            obligation_verifier=True,
            mission_verifier=True,
            visual_inspector=True,
            deep_reasoning=True,
        ),
    )
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m", goal_revision=2
    )
    assert profile.accepted_depth is ExecutionDepth.STANDARD
    assert not profile.services.planner
    assert not profile.services.mission_verifier
    assert not profile.services.visual_inspector
    assert not profile.services.deep_reasoning
    assert "depth_reduced:no_orchestration_pressure" in profile.policy_reasons


def test_underprofiled_cross_application_handoff_is_raised() -> None:
    proposal = _proposal(
        dimensions=TaskDimensions(
            step_dependency="multi_stage",
            application_scope="cross_application",
            artifact_flow="handoff",
            identity_binding="multiple",
            user_interaction="approval",
        ),
        recommended_depth=ExecutionDepth.DIRECT,
        minimum_depth=ExecutionDepth.DIRECT,
        requested_services=CognitiveServices(
            planner=True, obligation_verifier=False, mission_verifier=True
        ),
    )
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m", goal_revision=0
    )
    assert profile.accepted_depth is ExecutionDepth.ORCHESTRATED
    assert profile.services.planner
    assert profile.services.obligation_verifier
    assert profile.services.mission_verifier
    assert "depth_raised:task_pressure" in profile.policy_reasons


def test_runtime_version_overreach_falls_back_without_selecting_runtime() -> None:
    raw = {**_proposal().to_dict(), "runtime_version": "vnext"}
    profile = ExecutionPolicy().evaluate(raw, mission_id="m", goal_revision=0)
    assert profile.source == "safe_default"
    assert "proposal_schema_invalid" in profile.policy_reasons
    assert "runtime_version" not in profile.to_dict()


def test_property_randomized_policy_is_strict_and_replay_stable() -> None:
    rng = random.Random(17)
    choices = {
        "step_dependency": ("atomic", "local_sequence", "multi_stage"),
        "application_scope": ("current_surface", "single_application", "cross_application", "unknown"),
        "artifact_flow": ("none", "produce", "consume", "handoff"),
        "external_wait": ("none", "bounded", "persistent"),
        "irreversibility": ("low", "medium", "high"),
        "visual_precision": ("ordinary", "fine", "geometric"),
        "continuous_pointer": ("none", "atomic_drag", "cross_frame"),
        "completion_observability": ("deterministic", "visible", "mixed", "weak"),
        "identity_binding": ("none", "target", "recipient", "artifact", "multiple"),
        "user_interaction": ("none", "approval", "clarification", "ongoing"),
    }
    policy = ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    for index in range(120):
        dimensions = TaskDimensions(
            **{name: rng.choice(values) for name, values in choices.items()}
        )
        recommended = rng.choice(tuple(ExecutionDepth))
        minimum = rng.choice(tuple(ExecutionDepth)[: recommended.rank + 1])
        capabilities = tuple(
            sorted(rng.sample(tuple(sorted(DEFAULT_CAPABILITY_CATALOG)), rng.randint(0, 4)))
        )
        proposal = _proposal(
            dimensions=dimensions,
            recommended_depth=recommended,
            minimum_depth=minimum,
            confidence=0.6 + rng.random() * 0.4,
            broad_capability_hypotheses=capabilities,
            requested_services=CognitiveServices(
                planner=bool(rng.getrandbits(1)),
                obligation_verifier=bool(rng.getrandbits(1)),
                mission_verifier=bool(rng.getrandbits(1)),
                visual_inspector=bool(rng.getrandbits(1)),
                deep_reasoning=bool(rng.getrandbits(1)),
            ),
        )
        first = policy.evaluate(proposal, mission_id="m", goal_revision=index)
        second = policy.evaluate(proposal.to_dict(), mission_id="m", goal_revision=index)
        assert first.to_dict() == second.to_dict()
        assert set(first.accepted_capabilities) <= DEFAULT_CAPABILITY_CATALOG
        assert not (
            set(first.to_dict())
            & {"runtime_version", "application_workflow", "tool_sequence", "coordinate"}
        )


def test_execution_profile_package_is_pure_and_has_one_owner() -> None:
    package_root = Path(__file__).parents[1] / "winpilot"
    vnext_root = package_root / "vnext"
    files = {
        vnext_root / "capability_names.py",
        *(vnext_root / "profiling").rglob("*.py"),
    }
    assert {
        path.relative_to(vnext_root).as_posix()
        for path in files
    } == {
        "capability_names.py",
        "profiling/__init__.py",
        "profiling/contracts.py",
        "profiling/policy.py",
    }
    assert not (vnext_root / "execution_profile.py").exists()

    allowed = {
        "__future__",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "math",
        "re",
        "typing",
        "winpilot.vnext.capability_names",
        "winpilot.vnext.profiling.contracts",
        "winpilot.vnext.profiling.policy",
    }
    violations: list[str] = []
    for path in sorted(files):
        relative = path.relative_to(package_root.parent).with_suffix("")
        module = ".".join(relative.parts)
        package = module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    keep = max(0, len(parts) - node.level + 1)
                    prefix = ".".join(parts[:keep])
                    imported = [
                        ".".join(
                            part for part in (prefix, node.module or "") if part
                        )
                    ]
                else:
                    imported = [node.module or ""]
            else:
                continue
            for name in imported:
                if name not in allowed:
                    violations.append(
                        f"{path.relative_to(package_root.parent)}:{node.lineno} -> {name}"
                    )
    assert violations == []
