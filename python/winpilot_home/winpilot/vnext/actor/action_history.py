"""Default-deny projection of one already-validated GUI action.

The canonical ToolArgumentValidator remains the only JSON Schema validator.
This adapter only recognizes explicit ``historyVisible`` annotations on closed
top-level scalar arguments; ambiguous schemas or partial actions are omitted.
"""
from __future__ import annotations

import json
import math
from types import MappingProxyType
from typing import Any, Mapping


_SCALAR_SCHEMA_TYPES = frozenset({"null", "boolean", "integer", "number", "string"})
_VISIBLE_KEYS = frozenset({
    "type", "historyVisible", "title", "description", "enum", "const",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format", "readOnly", "writeOnly",
    "deprecated",
})
_TOP_LEVEL_KEYS = frozenset({
    "type", "additionalProperties", "properties", "required", "title",
    "description", "minProperties", "maxProperties",
})
_MAX_VISIBLE_ARGUMENTS = 16
_MAX_SERIALIZED_CHARS = 1_024


class ActionHistoryProjectionError(ValueError):
    """An action cannot safely cross into the next Actor turn."""


def _history_visible_names(
    schema: Mapping[str, Any],
) -> tuple[tuple[str, ...], frozenset[str]]:
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise ActionHistoryProjectionError("tool schema must be an object")
    if not set(schema) <= _TOP_LEVEL_KEYS:
        raise ActionHistoryProjectionError("history requires an unambiguous object schema")
    if schema.get("additionalProperties") is not False:
        raise ActionHistoryProjectionError("history requires closed top-level arguments")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or any(
        type(name) is not str for name in properties
    ):
        raise ActionHistoryProjectionError("tool properties are not canonical")
    required = schema.get("required")
    if (
        not isinstance(required, (list, tuple))
        or any(type(name) is not str or name not in properties for name in required)
        or len(required) != len(set(required))
    ):
        raise ActionHistoryProjectionError("tool required arguments are not canonical")

    visible = []
    for name, child in properties.items():
        if not isinstance(child, Mapping):
            continue
        annotation = child.get("historyVisible", False)
        if type(annotation) is not bool:
            raise ActionHistoryProjectionError(f"invalid historyVisible for {name!r}")
        if not annotation:
            continue
        if (
            not set(child) <= _VISIBLE_KEYS
            or child.get("writeOnly") is True
            or child.get("type") not in _SCALAR_SCHEMA_TYPES
        ):
            raise ActionHistoryProjectionError(
                f"history-visible argument {name!r} must be an unambiguous scalar"
            )
        visible.append(name)
    if len(visible) > _MAX_VISIBLE_ARGUMENTS:
        raise ActionHistoryProjectionError("too many history-visible arguments")
    return tuple(sorted(visible)), frozenset(required)


def project_action_history_args(
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Copy a complete action only when every actual argument is allowed."""

    if not isinstance(arguments, Mapping) or any(
        type(name) is not str for name in arguments
    ):
        raise ActionHistoryProjectionError("arguments must have string keys")
    visible, required = _history_visible_names(schema)
    if not set(arguments) <= set(visible):
        raise ActionHistoryProjectionError("action has a non-history-visible argument")
    if not required <= set(arguments):
        raise ActionHistoryProjectionError("action is missing a required argument")

    projected = {}
    for name in visible:
        if name not in arguments:
            continue
        value = arguments[name]
        if not (
            value is None
            or type(value) in {str, bool, int}
            or (type(value) is float and math.isfinite(value))
        ):
            raise ActionHistoryProjectionError(
                f"history-visible argument {name!r} is not a finite JSON scalar"
            )
        projected[name] = value
    encoded = json.dumps(
        projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded) > _MAX_SERIALIZED_CHARS:
        raise ActionHistoryProjectionError("projected arguments exceed context bound")
    return MappingProxyType(projected)


def try_project_action_history_args(
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Total post-action adapter; failure omits optional context."""

    try:
        return project_action_history_args(arguments, schema)
    except (ActionHistoryProjectionError, TypeError, ValueError):
        return None


__all__ = [
    "ActionHistoryProjectionError",
    "project_action_history_args",
    "try_project_action_history_args",
]
