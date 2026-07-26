"""Strict deterministic tests for the vNext tool-argument validation seam."""
from __future__ import annotations

from types import MappingProxyType

import pytest

from winpilot.vnext.capabilities import (
    JSON_SCHEMA_DIALECT,
    MutationKind,
    ToolArgumentValidationError,
    ToolArgumentValidator,
    ToolCatalog,
    ToolRoute,
    ToolSchemaValidationError,
    ToolSpec,
)


GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "pointer_released",
    "runtime_healthy",
)


def _catalog(schema: dict | None = None) -> ToolCatalog:
    return ToolCatalog((ToolSpec(
        name="interact",
        schema=schema or {
            "$schema": JSON_SCHEMA_DIALECT,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["click", "drag"]},
                "strength": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "points": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
            },
            "required": ["mode", "points"],
        },
        route=ToolRoute.VISUAL_ACTOR,
        mutation_kind=MutationKind.GUI,
        required_any_capabilities=("visual.interact",),
        prerequisites=GUI_GATES,
    ),))


def test_valid_arguments_return_a_detached_json_copy() -> None:
    validator = ToolArgumentValidator(_catalog())
    point = [10, 20]
    arguments = MappingProxyType({
        "points": (point,),
        "mode": "click",
        "strength": 0.5,
    })

    result = validator.validate("interact", arguments)

    assert result == {
        "points": [[10, 20]],
        "mode": "click",
        "strength": 0.5,
    }
    assert result is not arguments
    point[0] = 999
    assert result["points"] == [[10, 20]]
    assert validator.tool_names == ("interact",)


@pytest.mark.parametrize(
    ("arguments", "keyword", "path"),
    (
        ({"mode": "click"}, "required", ()),
        (
            {"mode": "click", "points": [[1, 2]], "extra": True},
            "additionalProperties",
            (),
        ),
        ({"mode": "tap", "points": [[1, 2]]}, "enum", ("mode",)),
        ({"mode": "click", "points": []}, "minItems", ("points",)),
        (
            {
                "mode": "click",
                "points": [[1, 2], [3, 4], [5, 6], [7, 8]],
            },
            "maxItems",
            ("points",),
        ),
        (
            {"mode": "click", "points": [[-1, 2]]},
            "minimum",
            ("points", 0, 0),
        ),
        (
            {"mode": "click", "points": [[1, 2]], "strength": 2},
            "maximum",
            ("strength",),
        ),
        (
            {"mode": "click", "points": [[1, "2"]]},
            "type",
            ("points", 0, 1),
        ),
        (
            {"mode": "click", "points": [[True, 2]]},
            "type",
            ("points", 0, 0),
        ),
    ),
)
def test_schema_constraints_reject_without_coercion(
    arguments,
    keyword,
    path,
) -> None:
    with pytest.raises(ToolArgumentValidationError) as caught:
        ToolArgumentValidator(_catalog()).validate(
            "interact",
            arguments,
        )
    assert caught.value.code == "schema_mismatch"
    assert caught.value.keyword == keyword
    assert caught.value.instance_path == path


def test_composition_keywords_are_supported() -> None:
    validator = ToolArgumentValidator(_catalog({
        "type": "object",
        "minProperties": 1,
        "additionalProperties": False,
        "properties": {
            "condition": {
                "oneOf": [
                    {"type": "boolean", "const": True},
                    {"type": "number", "exclusiveMinimum": 0},
                ],
            },
        },
        "required": ["condition"],
    }))
    assert validator.validate("interact", {"condition": True}) == {
        "condition": True,
    }
    assert validator.validate("interact", {"condition": 0.1}) == {
        "condition": 0.1,
    }
    with pytest.raises(ToolArgumentValidationError, match="schema_mismatch"):
        validator.validate("interact", {"condition": 0})


@pytest.mark.parametrize(
    "schema",
    (
        {"type": "object", "required": "mode"},
        {"type": "object", "properties": []},
        {"type": "object", "additionalProperties": "no"},
        {
            "$schema": "https://example.invalid/schema",
            "type": "object",
        },
    ),
)
def test_invalid_or_unsupported_schema_fails_closed(schema) -> None:
    with pytest.raises(ToolSchemaValidationError):
        ToolArgumentValidator(_catalog(schema))


def test_unknown_tool_and_non_object_arguments_fail_closed() -> None:
    validator = ToolArgumentValidator(_catalog())
    with pytest.raises(ToolArgumentValidationError) as unknown:
        validator.validate("missing", {})
    assert unknown.value.code == "unknown_tool"
    with pytest.raises(ToolArgumentValidationError) as not_object:
        validator.validate("interact", [])  # type: ignore[arg-type]
    assert not_object.value.code == "arguments_not_object"


@pytest.mark.parametrize(
    "bad",
    (
        float("nan"),
        float("inf"),
        object(),
        1 << 80,
        "x" * 1_048_577,
    ),
    ids=("nan", "inf", "object", "large_int", "large_string"),
)
def test_non_json_or_unbounded_values_fail_before_schema(bad) -> None:
    with pytest.raises(ToolArgumentValidationError) as caught:
        ToolArgumentValidator(_catalog()).validate(
            "interact",
            {"mode": "click", "points": [[bad, 2]]},
        )
    assert caught.value.code == "arguments_not_json"


def test_cycles_depth_nodes_and_non_string_keys_fail_closed() -> None:
    validator = ToolArgumentValidator(_catalog())
    cyclic: dict = {}
    cyclic["self"] = cyclic
    deep = leaf = {}
    for index in range(80):
        child = {"index": index}
        leaf["next"] = child
        leaf = child
    values = (
        {1: "not-json"},
        cyclic,
        deep,
        {"mode": "click", "points": [[0, 1]], "many": [0] * 4097},
    )
    for value in values:
        with pytest.raises(ToolArgumentValidationError) as caught:
            validator.validate("interact", value)
        assert caught.value.code == "arguments_not_json"


def test_error_selection_is_stable_under_property_order() -> None:
    validator = ToolArgumentValidator(_catalog())
    failures = []
    for arguments in (
        {
            "points": [[-1, "bad"]],
            "mode": "wrong",
            "extra": 1,
        },
        {
            "extra": 1,
            "mode": "wrong",
            "points": [[-1, "bad"]],
        },
    ):
        for _ in range(3):
            with pytest.raises(ToolArgumentValidationError) as caught:
                validator.validate("interact", arguments)
            failures.append((
                caught.value.code,
                caught.value.keyword,
                caught.value.instance_path,
                caught.value.schema_path,
                str(caught.value),
            ))
    assert len(set(failures)) == 1


def test_catalog_content_identity_is_revalidated_before_compilation() -> None:
    catalog = _catalog()
    object.__setattr__(catalog, "fingerprint", "0" * 64)
    with pytest.raises(
        ToolSchemaValidationError,
        match="identity is not canonical",
    ):
        ToolArgumentValidator(catalog)
