"""Negative and property tests for the incremental production catalog."""
from __future__ import annotations

import random

import pytest

from winpilot.vnext.capabilities.argument_validation import (
    ToolArgumentValidationError,
    ToolArgumentValidator,
)
from winpilot.vnext.capabilities.contracts import (
    MutationKind,
    ToolCatalog,
    ToolRoute,
)
from winpilot.vnext.capabilities.live_catalog import (
    LIVE_TOOL_NAMES,
    build_live_catalog,
)


def test_live_catalog_advertises_only_currently_executable_contracts() -> None:
    catalog = build_live_catalog()

    assert tuple(tool.name for tool in catalog.tools) == LIVE_TOOL_NAMES
    assert set(LIVE_TOOL_NAMES) == {
        "click",
        "double_click",
        "scroll",
        "claim_obligation_done",
        "blocked",
    }
    assert "wait" not in LIVE_TOOL_NAMES
    assert all(tool.route is ToolRoute.VISUAL_ACTOR for tool in catalog.tools)
    assert catalog.get("click").mutation_kind is MutationKind.GUI
    assert catalog.get("double_click").mutation_kind is MutationKind.GUI
    assert catalog.get("scroll").mutation_kind is MutationKind.GUI
    assert all(
        catalog.get(name).mutation_kind is MutationKind.NONE
        for name in ("claim_obligation_done", "blocked")
    )


def test_click_contract_is_exactly_submitted_image_xy() -> None:
    validator = ToolArgumentValidator(build_live_catalog())
    rng = random.Random(8701)

    for _ in range(100):
        arguments = {
            "image_x": rng.uniform(0, 20_000),
            "image_y": rng.uniform(0, 20_000),
        }
        assert validator.validate("click", arguments) == arguments
        assert validator.validate("double_click", arguments) == arguments

    rejected = (
        {},
        {"image_x": 1},
        {"image_x": 1, "image_y": 2, "local_intent": "extra"},
        {"image_x": -1, "image_y": 2},
        {"image_x": True, "image_y": 2},
        {"x": 1, "y": 2},
        {"image_x": 1, "image_y": 2, "frame_id": 9},
    )
    for arguments in rejected:
        with pytest.raises(ToolArgumentValidationError):
            validator.validate("click", arguments)


def test_scroll_contract_is_explicit_strict_and_bounded() -> None:
    catalog = build_live_catalog()
    validator = ToolArgumentValidator(catalog)
    rng = random.Random(20260722)

    properties = catalog.get("scroll").schema["properties"]
    assert properties["direction"]["enum"] == ("up", "down")
    assert properties["notches"]["minimum"] == 1
    assert properties["notches"]["maximum"] == 12

    for _ in range(100):
        arguments = {
            "image_x": rng.uniform(0, 20_000),
            "image_y": rng.uniform(0, 20_000),
            "direction": rng.choice(("up", "down")),
            "notches": rng.randint(1, 12),
        }
        assert validator.validate("scroll", arguments) == arguments

    for arguments in (
        {"image_x": 1, "image_y": 2},
        {"image_x": 1, "image_y": 2, "direction": "up"},
        {"image_x": 1, "image_y": 2, "direction": "left", "notches": 1},
        {"image_x": 1, "image_y": 2, "direction": "up", "notches": 0},
        {"image_x": 1, "image_y": 2, "direction": "up", "notches": True},
        {"image_x": 1, "image_y": 2, "direction": "up", "notches": 13},
        {"image_x": 1, "image_y": 2, "direction": "down", "notches": -1},
        {"x": 1, "y": 2, "direction": "down", "notches": 1},
        {
            "image_x": 1, "image_y": 2, "direction": "down", "notches": 1,
            "amount": -1,
        },
        {
            "image_x": 1, "image_y": 2, "direction": "down", "notches": 1,
            "frame_id": 7,
        },
    ):
        with pytest.raises(ToolArgumentValidationError):
            validator.validate("scroll", arguments)


def test_non_mutating_contracts_are_strict_and_executable() -> None:
    validator = ToolArgumentValidator(build_live_catalog())

    with pytest.raises(ToolArgumentValidationError):
        validator.validate("claim_obligation_done", {
            "concise_claim": "Visible result exists",
            "verification_id": "invented",
        })
    with pytest.raises(ToolArgumentValidationError):
        validator.validate("blocked", {
            "blocker_kind": "Visual Uncertainty",
            "observed_facts": [],
        })


def test_live_catalog_replay_is_deterministic_and_deeply_immutable() -> None:
    first = build_live_catalog()
    second = build_live_catalog()
    replayed = ToolCatalog.from_dict(first.to_dict())

    assert first.fingerprint == second.fingerprint == replayed.fingerprint
    assert first.to_dict() == second.to_dict() == replayed.to_dict()
    with pytest.raises(TypeError):
        first.get("click").schema["properties"]["image_x"]["type"] = "string"
