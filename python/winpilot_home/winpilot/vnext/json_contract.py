"""Shared bounded validation for canonical vNext JSON values.

The validator accepts only the literal Python values produced by a JSON
decoder: ``None``, plain ``dict``/``list`` containers, and exact
``str``/``bool``/``int``/``float`` scalars.  It never invokes caller-defined
mapping or sequence implementations and never coerces values.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any


MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 4096
MAX_JSON_STRING_LENGTH = 1_048_576
MAX_JSON_ARRAY_ITEMS = 4094
MAX_JSON_KEY_LENGTH = 256
MAX_JSON_UTF8_BYTES = 4_194_304
MIN_SIGNED_64 = -(1 << 63)
MAX_SIGNED_64 = (1 << 63) - 1


class BoundedJSONError(ValueError):
    """A value is not literal JSON or exceeds the shared resource budget."""


def _display_path(path: tuple[str | int, ...]) -> str:
    rendered = "$"
    for item in path:
        rendered += f"[{item}]" if type(item) is int else f".{item}"
    return rendered


def bounded_json_copy(
    value: Any,
    *,
    _path: tuple[str | int, ...] = (),
    _ancestors: set[int] | None = None,
    _depth: int = 0,
    _nodes: list[int] | None = None,
    _utf8_bytes: list[int] | None = None,
) -> Any:
    """Return a detached copy of one bounded literal JSON value."""
    location = _display_path(_path)
    if _depth > MAX_JSON_DEPTH:
        raise BoundedJSONError(f"JSON depth exceeds {MAX_JSON_DEPTH} at {location}")

    nodes = _nodes if _nodes is not None else [0]
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES:
        raise BoundedJSONError(
            f"JSON node count exceeds {MAX_JSON_NODES} at {location}"
        )
    utf8_bytes = _utf8_bytes if _utf8_bytes is not None else [0]

    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise BoundedJSONError(
                f"JSON string length exceeds {MAX_JSON_STRING_LENGTH} at {location}"
            )
        try:
            encoded_length = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise BoundedJSONError(
                f"JSON string contains an invalid Unicode scalar at {location}"
            ) from exc
        utf8_bytes[0] += encoded_length
        if utf8_bytes[0] > MAX_JSON_UTF8_BYTES:
            raise BoundedJSONError(
                f"JSON UTF-8 byte count exceeds {MAX_JSON_UTF8_BYTES} at {location}"
            )
        return value
    if type(value) is int:
        if not MIN_SIGNED_64 <= value <= MAX_SIGNED_64:
            raise BoundedJSONError(
                f"JSON integer is outside signed-64 range at {location}"
            )
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise BoundedJSONError(f"JSON float must be finite at {location}")
        return value
    if type(value) not in (dict, list):
        raise BoundedJSONError(
            f"non-literal JSON value of type {type(value).__name__} at {location}"
        )

    ancestors = _ancestors if _ancestors is not None else set()
    marker = id(value)
    if marker in ancestors:
        raise BoundedJSONError(f"cyclic JSON value at {location}")
    ancestors.add(marker)
    try:
        if type(value) is dict:
            copied: dict[str, Any] = {}
            for key, child in value.items():
                if type(key) is not str:
                    raise BoundedJSONError(
                        f"JSON object key must be a literal string at {location}"
                    )
                if len(key) > MAX_JSON_KEY_LENGTH:
                    raise BoundedJSONError(
                        f"JSON object key length exceeds {MAX_JSON_KEY_LENGTH} "
                        f"at {location}"
                    )
                try:
                    key_bytes = len(key.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise BoundedJSONError(
                        f"JSON object key contains an invalid Unicode scalar "
                        f"at {location}"
                    ) from exc
                utf8_bytes[0] += key_bytes
                if utf8_bytes[0] > MAX_JSON_UTF8_BYTES:
                    raise BoundedJSONError(
                        f"JSON UTF-8 byte count exceeds {MAX_JSON_UTF8_BYTES} "
                        f"at {location}"
                    )
                copied[key] = bounded_json_copy(
                    child,
                    _path=(*_path, key),
                    _ancestors=ancestors,
                    _depth=_depth + 1,
                    _nodes=nodes,
                    _utf8_bytes=utf8_bytes,
                )
            return copied

        if len(value) > MAX_JSON_ARRAY_ITEMS:
            raise BoundedJSONError(
                f"JSON array length exceeds {MAX_JSON_ARRAY_ITEMS} at {location}"
            )
        return [
            bounded_json_copy(
                child,
                _path=(*_path, index),
                _ancestors=ancestors,
                _depth=_depth + 1,
                _nodes=nodes,
                _utf8_bytes=utf8_bytes,
            )
            for index, child in enumerate(value)
        ]
    finally:
        ancestors.remove(marker)


def freeze_bounded_json(value: Any) -> Any:
    """Validate and recursively freeze one bounded literal JSON value."""
    copied = bounded_json_copy(value)

    def freeze(item: Any) -> Any:
        if type(item) is dict:
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if type(item) is list:
            return tuple(freeze(child) for child in item)
        return item

    return freeze(copied)


__all__ = [
    "BoundedJSONError",
    "MAX_JSON_ARRAY_ITEMS",
    "MAX_JSON_DEPTH",
    "MAX_JSON_KEY_LENGTH",
    "MAX_JSON_NODES",
    "MAX_JSON_STRING_LENGTH",
    "MAX_JSON_UTF8_BYTES",
    "MAX_SIGNED_64",
    "MIN_SIGNED_64",
    "bounded_json_copy",
    "freeze_bounded_json",
]
