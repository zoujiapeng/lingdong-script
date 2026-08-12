"""Deterministic, fail-closed validation for vNext tool arguments.

The Capability Broker decides which tool names are available. This module has
one narrower responsibility: prove that one proposed argument object conforms
to the immutable :class:`ToolSpec` schema before Runtime can dispatch it.
It does not repair arguments, select tools, or interpret GUI semantics.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from ..json_contract import BoundedJSONError, bounded_json_copy
from .contracts import CapabilityContractError, ToolCatalog


JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_REFERENCE_KEYWORDS = frozenset({
    "$ref",
    "$dynamicRef",
    "$recursiveRef",
})


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
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.code = code
        self.keyword = keyword
        self.instance_path = instance_path
        self.schema_path = schema_path
        self.expected = expected
        self.actual = actual
        location = _display_path(instance_path)
        detail = f" ({keyword})" if keyword else ""
        shape = ""
        if expected is not None:
            shape += f" expected={expected}"
        if actual is not None:
            shape += f" actual={actual}"
        super().__init__(
            f"tool {tool_name!r} arguments rejected: "
            f"{code}{detail} at {location}{shape}"
        )


def _display_path(path: tuple[str | int, ...]) -> str:
    result = "$"
    for item in path:
        result += f"[{item}]" if type(item) is int else f".{item}"
    return result


def _json_type_name(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    if type(value) is str:
        return "string"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    return type(value).__name__


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


def _reject_nonlocal_references(
    value: Any,
    *,
    path: tuple[str | int, ...] = (),
) -> None:
    if type(value) is dict:
        for key, child in value.items():
            child_path = (*path, key)
            if key in _REFERENCE_KEYWORDS and (
                type(child) is not str or not child.startswith("#")
            ):
                raise ToolSchemaValidationError(
                    "tool schema contains a non-local reference at "
                    f"{_display_path(child_path)}"
                )
            _reject_nonlocal_references(child, path=child_path)
    elif type(value) is list:
        for index, child in enumerate(value):
            _reject_nonlocal_references(
                child,
                path=(*path, index),
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
            from referencing import Registry
            from referencing.exceptions import NoSuchResource
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ToolSchemaValidationError(
                "jsonschema is unavailable; tool arguments cannot be validated"
            ) from exc

        validators: dict[str, Any] = {}

        def _deny_remote_retrieval(uri: str) -> Any:
            raise NoSuchResource(ref=uri)

        hermetic_registry = Registry(
            retrieve=_deny_remote_retrieval,
        )
        for spec in catalog.tools:
            schema = spec.to_dict()["schema"]
            dialect = schema.get("$schema")
            if dialect not in (None, JSON_SCHEMA_DIALECT):
                raise ToolSchemaValidationError(
                    f"tool {spec.name!r} declares unsupported "
                    "JSON Schema dialect"
                )
            _reject_nonlocal_references(schema)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ToolSchemaValidationError(
                    f"tool {spec.name!r} has an invalid JSON Schema"
                ) from exc
            validators[spec.name] = Draft202012Validator(
                schema,
                registry=hermetic_registry,
            )
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
        if type(arguments) is not dict:
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="arguments_not_object",
            )
        try:
            value = bounded_json_copy(arguments)
        except (BoundedJSONError, RecursionError) as exc:
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="arguments_not_json",
            ) from exc

        try:
            errors = sorted(
                self._validators[tool_name].iter_errors(value),
                key=_error_sort_key,
            )
        except Exception as exc:
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="schema_evaluation_failed",
            ) from exc
        if errors:
            error = errors[0]
            expected = None
            actual = None
            if error.validator == "type":
                required_types = error.validator_value
                if type(required_types) is str:
                    expected = required_types
                elif type(required_types) in (list, tuple):
                    expected = "|".join(str(item) for item in required_types)
                actual = _json_type_name(error.instance)
            raise ToolArgumentValidationError(
                tool_name=tool_name,
                code="schema_mismatch",
                keyword=str(error.validator or "unknown"),
                instance_path=tuple(error.absolute_path),
                schema_path=tuple(error.absolute_schema_path),
                expected=expected,
                actual=actual,
            )
        return value


__all__ = [
    "JSON_SCHEMA_DIALECT",
    "ToolSchemaValidationError",
    "ToolArgumentValidationError",
    "ToolArgumentValidator",
]
