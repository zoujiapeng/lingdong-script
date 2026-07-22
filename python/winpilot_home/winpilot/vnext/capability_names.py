"""Single dependency-light owner for broad vNext capability names."""
from __future__ import annotations

import re


CAPABILITY_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z0-9_]+)+")
CANONICAL_CAPABILITIES = frozenset({
    "application.resolve",
    "artifact.capture",
    "artifact.read",
    "communication.bind_recipient",
    "communication.send_artifact",
    "communication.send_text",
    "communication.verify_delivery",
    "external.wait",
    "mcp.office",
    "pointer.dynamic",
    "visual.draw",
    "visual.interact",
    "visual.read_text",
})


class InvalidCapability(ValueError):
    pass


def canonical_capability(value: object) -> str:
    if type(value) is not str:
        raise InvalidCapability("capability name must be a string")
    name = value.strip()
    if not CAPABILITY_PATTERN.fullmatch(name):
        raise InvalidCapability(f"invalid capability name {name!r}")
    if name not in CANONICAL_CAPABILITIES:
        raise InvalidCapability(f"unknown canonical capability {name!r}")
    return name


def canonical_capabilities(values: object) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise InvalidCapability("capabilities must be an array")
    try:
        result = tuple(canonical_capability(item) for item in values)
    except TypeError as exc:
        raise InvalidCapability("capabilities must be an array") from exc
    if len(result) != len(set(result)):
        raise InvalidCapability("capabilities must be unique")
    return tuple(sorted(result))


__all__ = [
    "CAPABILITY_PATTERN",
    "CANONICAL_CAPABILITIES",
    "InvalidCapability",
    "canonical_capability",
    "canonical_capabilities",
]
