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
from winpilot.vnext.json_contract import (
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_KEY_LENGTH,
    MAX_JSON_NODES,
    MIN_SIGNED_64,
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
    arguments = {
        "points": (point,),
        "mode": "click",
        "strength": 0.5,
    }

    with pytest.raises(ToolArgumentValidationError) as non_json:
        validator.validate("interact", arguments)
    assert non_json.value.code == "arguments_not_json"

    arguments["points"] = [point]
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
        (
            {"mode": "click", "points": [[None, 2]]},
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


def test_local_fragment_references_are_supported_and_fail_closed() -> None:
    validator = ToolArgumentValidator(_catalog({
        "$defs": {
            "mode": {"type": "string", "enum": ["click"]},
        },
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mode": {"$ref": "#/$defs/mode"},
        },
        "required": ["mode"],
    }))
    assert validator.validate("interact", {"mode": "click"}) == {
        "mode": "click",
    }
    with pytest.raises(ToolArgumentValidationError) as mismatch:
        validator.validate("interact", {"mode": "drag"})
    assert mismatch.value.code == "schema_mismatch"

    unresolved = ToolArgumentValidator(_catalog({
        "type": "object",
        "properties": {
            "mode": {"$ref": "#/$defs/missing"},
        },
    }))
    with pytest.raises(ToolArgumentValidationError) as failed:
        unresolved.validate("interact", {"mode": "click"})
    assert failed.value.code == "schema_evaluation_failed"


@pytest.mark.parametrize(
    ("keyword", "reference"),
    (
        ("$ref", "https://127.0.0.1:9/schema.json"),
        ("$dynamicRef", "https://example.invalid/schema"),
        ("$recursiveRef", "../schema.json"),
        ("$ref", "child.json"),
        ("$ref", ""),
    ),
)
def test_nonlocal_references_are_rejected_without_network(
    monkeypatch,
    keyword,
    reference,
) -> None:
    calls = []

    def _network_forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("schema validation attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", _network_forbidden)
    schema = {
        "$id": "https://example.invalid/root.json",
        "type": "object",
        "properties": {
            "value": {keyword: reference},
        },
    }
    with pytest.raises(
        ToolSchemaValidationError,
        match="non-local reference",
    ):
        ToolArgumentValidator(_catalog(schema))
    assert calls == []


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


def test_mapping_subclasses_are_rejected_before_iteration() -> None:
    class ExecutableMapping(dict):
        def items(self):
            raise AssertionError("mapping subclass was executed")

    validator = ToolArgumentValidator(_catalog())
    hostile = ExecutableMapping({
        "mode": "click",
        "points": [[1, 2]],
    })
    for value in (hostile, MappingProxyType(hostile)):
        with pytest.raises(ToolArgumentValidationError) as caught:
            validator.validate("interact", value)
        assert caught.value.code == "arguments_not_object"


@pytest.mark.parametrize(
    "bad",
    (
        float("nan"),
        float("inf"),
        object(),
        1 << 80,
        MIN_SIGNED_64 - 1,
        "x" * 1_048_577,
    ),
    ids=(
        "nan",
        "inf",
        "object",
        "high_int",
        "low_int",
        "large_string",
    ),
)
def test_non_json_or_unbounded_values_fail_before_schema(bad) -> None:
    with pytest.raises(ToolArgumentValidationError) as caught:
        ToolArgumentValidator(_catalog()).validate(
            "interact",
            {"mode": "click", "points": [[bad, 2]]},
        )
    assert caught.value.code == "arguments_not_json"


def test_finite_float_and_signed_64_integer_endpoints_are_literal_json() -> None:
    validator = ToolArgumentValidator(_catalog({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "minimum": {"type": "integer"},
            "maximum": {"type": "integer"},
            "finite": {"type": "number"},
        },
        "required": ["minimum", "maximum", "finite"],
    }))
    arguments = {
        "minimum": -(1 << 63),
        "maximum": (1 << 63) - 1,
        "finite": 1e308,
    }
    assert validator.validate("interact", arguments) == arguments


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
        {"k" * (MAX_JSON_KEY_LENGTH + 1): None},
        {
            "mode": "click",
            "points": [[0, 1]],
            "many": [0] * (MAX_JSON_ARRAY_ITEMS + 1),
        },
        {
            f"k{index}": None
            for index in range(MAX_JSON_NODES)
        },
        {
            "chunks": ["\u754c" * 900_000 for _ in range(2)],
        },
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
