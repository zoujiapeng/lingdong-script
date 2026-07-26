"""Deterministic, fail-closed validation for vNext tool arguments.

The Capability Broker decides which tool names are available. This module has
one narrower responsibility: prove that one proposed argument object conforms
to the immutable :class:`ToolSpec` schema before Runtime can dispatch it.
It does not repair arguments, select tools, or interpret GUI semantics.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import CapabilityContractError, ToolCatalog


JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 4096
_MAX_INTEGER = (1 << 63) - 1
_MAX_JSON_STRING_LENGTH = 1_048_576
_MAX_KEY_LENGTH = 256
_MAX_ARRAY_ITEMS = 4096


class ToolSchemaValidationError(CapabilityContractError):
    """A catalog contains a schema Runtime cannot validate safely."""


class ToolArgumentValidationError(CapabilityContractError):
    """A proposed tool call is unknown, non-JSON, or schema-invalid."""

    def __init__(
        self,
        *,
        tool_name: str,
        code: str,
        keyword: str | None = None,
        instance_path: tuple[str | int, ...] = (),
        schema_path: tuple[str | int, ...] = (),
    ) -> None:
        self.tool_name = tool_name
        self.code = code
        self.keyword = keyword
        self.instance_path = instance_path
        self.schema_path = schema_path
        location = _display_path(instance_path)
        detail = f" ({keyword})" if keyword else ""
        super().__init__(
            f"tool {tool_name!r} arguments rejected: "
            f"{code}{detail} at {location}"
        )


def _display_path(path: tuple[str | int, ...]) -> str:
    result = "$"
    for item in path:
        result += f"[{item}]" if isinstance(item, int) else f".{item}"
    return result


def _json_copy(
    value: Any,
    *,
    path: tuple[str | int, ...] = (),
    ancestors: set[int] | None = None,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> Any:
    """Copy finite JSON, accepting tuples/mapping proxies from frozen contracts."""
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"JSON depth exceeded at {_display_path(path)}")
    count = nodes if nodes is not None else [0]
    count[0] += 1
    if count[0] > _MAX_JSON_NODES:
        raise ValueError(f"JSON node limit exceeded at {_display_path(path)}")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise ValueError(
                f"JSON string limit exceeded at {_display_path(path)}"
            )
        return value
    if type(value) is int:
        if abs(value) > _MAX_INTEGER:
            raise ValueError(
                f"integer limit exceeded at {_display_path(path)}"
            )
        return value
    if type(value) is float:
        if math.isfinite(value):
            return 0.0 if value == 0.0 else value
        raise ValueError(f"non-finite number at {_display_path(path)}")
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError(f"non-JSON value at {_display_path(path)}")

    seen = ancestors if ancestors is not None else set()
    marker = id(value)
    if marker in seen:
        raise ValueError(f"cyclic JSON value at {_display_path(path)}")
    seen.add(marker)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError(
                    f"non-string object key at {_display_path(path)}"
                )
            if any(len(key) > _MAX_KEY_LENGTH for key in value):
                raise ValueError(
                    f"object key limit exceeded at {_display_path(path)}"
                )
            return {
                key: _json_copy(
                    child,
                    path=(*path, key),
                    ancestors=seen,
                    depth=depth + 1,
                    nodes=count,
                )
                for key, child in value.items()
            }
        if len(value) > _MAX_ARRAY_ITEMS:
            raise ValueError(
                f"array item limit exceeded at {_display_path(path)}"
            )
        return [
            _json_copy(
                child,
                path=(*path, index),
                ancestors=seen,
                depth=depth + 1,
                nodes=count,
            )
            for index, child in enumerate(value)
        ]
    finally:
        seen.remove(marker)


def _error_sort_key(error: Any) -> tuple[Any, ...]:
    def path_key(path: Any) -> tuple[str, ...]:
        return tuple(
            f"{type(item).__name__}:{item!r}" for item in path
        )

    return (
        path_key(error.absolute_path),
        path_key(error.absolute_schema_path),
        str(error.validator or ""),
    )


class ToolArgumentValidator:
    """Precompiled validator bound to exactly one immutable ``ToolCatalog``."""

    def __init__(self, catalog: ToolCatalog) -> None:
        if type(catalog) is not ToolCatalog:
            raise TypeError("catalog must be ToolCatalog")
        try:
            catalog = ToolCatalog.from_dict(catalog.to_dict())
        except Exception as exc:
            raise ToolSchemaValidationError(
                "tool catalog identity is not canonical"
            ) from exc
        try:
            from jsonschema import Draft202012Validator
            from jsonschema.exceptions import SchemaError
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ToolSchemaValidationError(
                "jsonschema is unavailable; tool arguments cannot be validated"
            ) from exc

        validators: dict[str, Any] = {}
        for spec in catalog.tools:
            schema = spec.to_dict()["schema"]
            dialect = schema.get("$schema")
            if dialect not in (None, JSON_SCHEMA_DIALECT):
                raise ToolSchemaValidationError(
                    f"tool {spec.name!r} declares unsupported "
                    "JSON Schema dialect"
                )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ToolSchemaValidationError(
                    f"tool {spec.name!r} has an invalid JSON Schema"
                ) from exc
            validators[spec.name] = Draft202012Validator(schema)
        self._validators = MappingProxyType(validators)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._validators)

    def validate(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a detached JSON copy iff the arguments are schema-valid."""
        if type(tool_name) is not str or tool_name not in self._validators:
            rendered_name = (
                tool_name if type(tool_name) is str else "<invalid>"
            )
            raise ToolArgumentValidationError(
                tool_name=rendered_name,
                code="unknown_tool",
            )
        if not isinstance(arguments, Mapping):
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="arguments_not_object",
            )
        try:
            value = _json_copy(arguments)
        except ValueError as exc:
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="arguments_not_json",
            ) from exc

        errors = sorted(
            self._validators[tool_name].iter_errors(value),
            key=_error_sort_key,
        )
        if errors:
            error = errors[0]
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="schema_mismatch",
                keyword=str(error.validator or "unknown"),
                instance_path=tuple(error.absolute_path),
                schema_path=tuple(error.absolute_schema_path),
            )
        return value


__all__ = [
    "JSON_SCHEMA_DIALECT",
    "ToolSchemaValidationError",
    "ToolArgumentValidationError",
    "ToolArgumentValidator",
]
