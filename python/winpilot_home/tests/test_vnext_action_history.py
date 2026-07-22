"""Default-deny tests for one-turn action argument projection."""
from __future__ import annotations

import json
import math
import random

import pytest

from winpilot.vnext.actor.action_history import (
    ActionHistoryProjectionError,
    project_action_history_args,
    try_project_action_history_args,
)


def _schema(properties: dict, *, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required if required is not None else list(properties),
    }


def test_complete_visible_action_is_stable_detached_and_immutable() -> None:
    schema = _schema({
        "image_x": {"type": "number", "historyVisible": True},
        "image_y": {"type": "number", "historyVisible": True},
    })
    baseline = None
    rng = random.Random(73)
    for _ in range(20):
        items = [("image_x", 12.5), ("image_y", 30)]
        rng.shuffle(items)
        projected = project_action_history_args(dict(items), schema)
        encoded = json.dumps(dict(projected), sort_keys=True, separators=(",", ":"))
        baseline = encoded if baseline is None else baseline
        assert encoded == baseline
    with pytest.raises(TypeError):
        projected["image_x"] = 99


@pytest.mark.parametrize(
    "arguments,schema",
    (
        ({"text": "secret"}, _schema({"text": {"type": "string"}})),
        (
            {"image_x": 1, "label": "private"},
            _schema({
                "image_x": {"type": "number", "historyVisible": True},
                "label": {"type": "string"},
            }),
        ),
        (
            {"modifiers": ["ctrl"], "key": "a"},
            _schema({
                "modifiers": {"type": "array", "items": {"type": "string"}},
                "key": {"type": "string"},
            }),
        ),
    ),
)
def test_any_unapproved_actual_argument_omits_the_whole_action(arguments, schema) -> None:
    with pytest.raises(ActionHistoryProjectionError, match="non-history-visible"):
        project_action_history_args(arguments, schema)


@pytest.mark.parametrize(
    "schema",
    (
        {"type": "array", "items": {}},
        {"type": "object", "properties": {}, "required": []},
        {"type": "object", "additionalProperties": False, "required": []},
        _schema({"value": {"type": "string", "historyVisible": "yes"}}),
        _schema({"value": {"type": "string", "historyVisible": True, "writeOnly": True}}),
        _schema({"value": {"type": "array", "historyVisible": True}}),
        _schema({"value": {"type": "string", "historyVisible": True, "$ref": "#/$defs/v"}}),
        _schema({"value": {"type": "string", "historyVisible": True, "allOf": [{}]}}),
        {
            **_schema({"value": {"type": "string", "historyVisible": True}}),
            "allOf": [{"properties": {"value": {"writeOnly": True}}}],
        },
        {
            "type": "object", "additionalProperties": False,
            "properties": {"known": {"type": "string"}},
            "required": ["unknown"],
        },
    ),
)
def test_malformed_or_ambiguous_schema_fails_closed(schema) -> None:
    with pytest.raises(ActionHistoryProjectionError):
        project_action_history_args({"value": "x"}, schema)


@pytest.mark.parametrize("bad", (math.nan, math.inf, [1], {"value": 1}, object()))
def test_visible_value_must_be_one_finite_json_scalar(bad) -> None:
    with pytest.raises(ActionHistoryProjectionError):
        project_action_history_args(
            {"value": bad},
            _schema({"value": {"type": "number", "historyVisible": True}}),
        )


def test_bounds_missing_required_and_same_named_property() -> None:
    visible = {"type": "string", "historyVisible": True}
    with pytest.raises(ActionHistoryProjectionError, match="missing a required"):
        project_action_history_args(
            {"image_x": 12},
            _schema({"image_x": visible, "image_y": visible}),
        )
    with pytest.raises(ActionHistoryProjectionError, match="context bound"):
        project_action_history_args(
            {"value": "x" * 1_100}, _schema({"value": visible}),
        )
    too_many = {f"v{index}": visible for index in range(17)}
    with pytest.raises(ActionHistoryProjectionError, match="too many"):
        project_action_history_args({name: "x" for name in too_many}, _schema(too_many))
    assert dict(project_action_history_args(
        {"historyVisible": 7},
        _schema({"historyVisible": {"type": "number", "historyVisible": True}}),
    )) == {"historyVisible": 7}


def test_total_adapter_never_raises_and_never_returns_partial_arguments() -> None:
    assert try_project_action_history_args(
        {"dynamic": "secret"},
        {"type": "object", "additionalProperties": True, "properties": {}, "required": []},
    ) is None
    assert try_project_action_history_args(
        {"image_x": 10, "private": "secret"},
        _schema({
            "image_x": {"type": "number", "historyVisible": True},
            "private": {"type": "string"},
        }),
    ) is None
