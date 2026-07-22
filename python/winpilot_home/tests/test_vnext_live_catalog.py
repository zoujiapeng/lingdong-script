"""Negative and property tests for the incremental production catalog."""
from __future__ import annotations

import random
from itertools import combinations

import pytest

from winpilot.action.raw_device import (
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
)
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
    LIVE_CONTROLLER_TOOL_NAMES,
    LIVE_TOOL_NAMES,
    build_live_catalog,
    build_live_runtime_snapshot,
)


def test_live_catalog_advertises_only_currently_executable_contracts() -> None:
    catalog = build_live_catalog()

    assert tuple(tool.name for tool in catalog.tools) == LIVE_TOOL_NAMES
    assert set(LIVE_TOOL_NAMES) == {
        "click",
        "double_click",
        "scroll",
        "type_text",
        "hotkey",
        "claim_obligation_done",
        "blocked",
    }
    assert "wait" not in LIVE_TOOL_NAMES
    assert all(tool.route is ToolRoute.VISUAL_ACTOR for tool in catalog.tools)
    assert catalog.get("click").mutation_kind is MutationKind.GUI
    assert catalog.get("double_click").mutation_kind is MutationKind.GUI
    assert catalog.get("scroll").mutation_kind is MutationKind.GUI
    assert catalog.get("type_text").mutation_kind is MutationKind.GUI
    assert catalog.get("hotkey").mutation_kind is MutationKind.GUI
    assert all(
        catalog.get(name).mutation_kind is MutationKind.NONE
        for name in ("claim_obligation_done", "blocked")
    )


def test_live_catalog_history_visibility_is_explicit_and_default_deny() -> None:
    catalog = build_live_catalog()
    expected = {
        "click": {"image_x", "image_y"},
        "double_click": {"image_x", "image_y"},
        "scroll": {"image_x", "image_y", "direction", "notches"},
        "type_text": set(),
        "hotkey": set(),
    }

    for tool_name, visible_names in expected.items():
        properties = catalog.get(tool_name).schema["properties"]
        assert {
            name
            for name, schema in properties.items()
            if schema.get("historyVisible") is True
        } == visible_names


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


def test_type_text_contract_is_content_only_strict_and_bounded() -> None:
    catalog = build_live_catalog()
    validator = ToolArgumentValidator(catalog)
    schema = catalog.get("type_text").schema

    assert schema["additionalProperties"] is False
    assert schema["required"] == ("text",)
    assert set(schema["properties"]) == {"text"}
    text_schema = schema["properties"]["text"]
    assert text_schema["type"] == "string"
    assert text_schema["minLength"] == 1
    assert text_schema["maxLength"] == MAX_TYPE_TEXT_CODEPOINTS
    for text in ("a", "中文🙂", "x" * MAX_TYPE_TEXT_CODEPOINTS):
        assert validator.validate("type_text", {"text": text}) == {"text": text}

    for arguments in (
        {},
        {"text": ""},
        {"text": "x" * (MAX_TYPE_TEXT_CODEPOINTS + 1)},
        {"text": True},
        {"text": "ok", "image_x": 1, "image_y": 2},
        {"text": "ok", "frame_id": 1},
        {"text": "ok", "mission_id": "invented"},
    ):
        with pytest.raises(ToolArgumentValidationError):
            validator.validate("type_text", arguments)


def test_hotkey_contract_matches_canonical_chord_domain() -> None:
    catalog = build_live_catalog()
    validator = ToolArgumentValidator(catalog)
    schema = catalog.get("hotkey").schema
    properties = schema["properties"]
    primary_keys = tuple(sorted(
        CANONICAL_HOTKEY_KEYS - set(CANONICAL_HOTKEY_MODIFIERS)
    ))

    assert schema["additionalProperties"] is False
    assert schema["required"] == ("modifiers", "key")
    assert set(properties) == {"modifiers", "key"}
    assert properties["modifiers"]["type"] == "array"
    assert properties["modifiers"]["items"]["type"] == "string"
    assert properties["modifiers"]["items"]["enum"] == CANONICAL_HOTKEY_MODIFIERS
    assert properties["modifiers"]["maxItems"] == 4
    assert properties["modifiers"]["uniqueItems"] is True
    assert properties["key"]["type"] == "string"
    assert properties["key"]["enum"] == primary_keys

    rng = random.Random(20260723)
    valid = [
        {"modifiers": list(modifiers), "key": key}
        for size in range(5)
        for modifiers in combinations(CANONICAL_HOTKEY_MODIFIERS, size)
        for key in rng.sample(primary_keys, min(3, len(primary_keys)))
    ]
    rng.shuffle(valid)
    for arguments in valid:
        rng.shuffle(arguments["modifiers"])
        assert validator.validate("hotkey", arguments) == arguments
        ordered = tuple(
            modifier
            for modifier in CANONICAL_HOTKEY_MODIFIERS
            if modifier in arguments["modifiers"]
        )
        assert HotkeyChord((*ordered, arguments["key"])).keys == (
            *ordered,
            arguments["key"],
        )

    for arguments in (
        {},
        {"modifiers": []},
        {"key": "enter"},
        {"modifiers": "ctrl", "key": "a"},
        {"modifiers": ["ctrl", "ctrl"], "key": "a"},
        {"modifiers": ["CTRL"], "key": "a"},
        {"modifiers": [], "key": "ctrl"},
        {"modifiers": [], "key": "unknown"},
        {"modifiers": [], "key": True},
        {"modifiers": [], "key": "a", "keys": ["a"]},
        {"modifiers": [], "key": "a", "image_x": 1},
        {"modifiers": [], "key": "a", "run_id": "invented"},
    ):
        with pytest.raises(ToolArgumentValidationError):
            validator.validate("hotkey", arguments)


def test_live_runtime_snapshot_has_exact_generic_controller_parity() -> None:
    snapshot = build_live_runtime_snapshot()
    gui_tools = tuple(sorted(
        tool.name
        for tool in snapshot.catalog.tools
        if tool.mutation_kind is MutationKind.GUI
    ))

    assert snapshot.controller_tool_names == gui_tools
    assert set(LIVE_CONTROLLER_TOOL_NAMES) == set(gui_tools) == {
        "click", "double_click", "scroll", "type_text", "hotkey",
    }
    owner_ids = tuple(
        owner.owner_id for owner in snapshot.capability_registry.owners
    )
    assert "atomic-desktop-input-controller" in owner_ids
    assert "atomic-point-input-controller" not in owner_ids


def test_non_mutating_contracts_are_strict_and_executable() -> None:
    catalog = build_live_catalog()
    validator = ToolArgumentValidator(catalog)

    claim = catalog.get("claim_obligation_done")
    assert claim.mutation_kind is MutationKind.NONE
    description = claim.schema["description"]
    assert isinstance(description, str) and description.strip()
    assert "independent verification" in description.lower()
    assert "does not mark the obligation or mission complete" in description.lower()
    for forbidden in (
        "qq", "paint", "netease", "douyin", "benchmark", "test case",
        "image_x", "image_y", "coordinate", "mission_id", "run_id",
        "frame_id", "evidence_id", "verification_id",
    ):
        assert forbidden not in description.lower()

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
