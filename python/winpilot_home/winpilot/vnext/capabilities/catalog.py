"""Build a ToolCatalog from the existing schema owner plus vNext policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import MutationKind, ToolCatalog, ToolRoute, ToolSpec


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    name: str
    route: ToolRoute | str
    mutation_kind: MutationKind | str
    required_any_capabilities: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    authorization_scope: str | None = None
    order: int = 100


def schemas_by_name(openai_tools: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw in openai_tools:
        function = raw.get("function") if isinstance(raw, Mapping) else None
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters")
        if name and isinstance(parameters, Mapping):
            if name in result:
                raise ValueError(f"duplicate tool schema {name!r}")
            result[name] = parameters
    return result


def build_tool_catalog(
    openai_tools: Iterable[Mapping[str, Any]],
    policies: Iterable[ToolPolicy],
) -> ToolCatalog:
    """Attach policy to schemas without copying action implementations."""
    schemas = schemas_by_name(openai_tools)
    specs = []
    for policy in policies:
        if policy.name not in schemas:
            raise ValueError(f"missing schema for catalog tool {policy.name!r}")
        specs.append(ToolSpec(
            name=policy.name,
            schema=schemas[policy.name],
            route=policy.route,
            mutation_kind=policy.mutation_kind,
            required_any_capabilities=policy.required_any_capabilities,
            prerequisites=policy.prerequisites,
            authorization_scope=policy.authorization_scope,
            order=policy.order,
        ))
    return ToolCatalog(tuple(specs))


__all__ = ["ToolPolicy", "schemas_by_name", "build_tool_catalog"]
