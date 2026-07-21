"""Read-only P3 shadow metrics; labels never feed back into Broker."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .contracts import CapabilityContractError, ToolCatalog
from .state import BrokerProjection


BROKER_SHADOW_RESULT_FORMAT = "broker-shadow-result/0"


@dataclass(frozen=True, slots=True)
class NecessaryToolExpectation:
    required_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    complete_relevance_label: bool = False

    def __post_init__(self) -> None:
        groups = []
        for raw in self.required_groups:
            group = tuple(sorted(set(str(item).strip() for item in raw if str(item).strip())))
            if not group:
                raise CapabilityContractError("necessary tool group cannot be empty")
            groups.append(group)
        if len(groups) != len(set(groups)):
            raise CapabilityContractError("necessary tool groups must be unique")
        object.__setattr__(self, "required_groups", tuple(groups))
        forbidden = tuple(sorted(set(
            str(item).strip() for item in self.forbidden_tools if str(item).strip()
        )))
        object.__setattr__(self, "forbidden_tools", forbidden)
        if type(self.complete_relevance_label) is not bool:
            raise CapabilityContractError("complete_relevance_label must be boolean")


@dataclass(frozen=True, slots=True)
class BrokerShadowResult:
    projection_id: str
    actual_legacy_tool: str | None
    actual_tool_allowed: bool | None
    necessary_tool_recall: float | None
    missing_required_groups: tuple[tuple[str, ...], ...]
    forbidden_tools_exposed: tuple[str, ...]
    projected_tool_count: int
    projected_schema_chars: int
    complete_relevance_label: bool
    format: str = BROKER_SHADOW_RESULT_FORMAT

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "projection_id": self.projection_id,
            "actual_legacy_tool": self.actual_legacy_tool,
            "actual_tool_allowed": self.actual_tool_allowed,
            "necessary_tool_recall": self.necessary_tool_recall,
            "missing_required_groups": [list(item) for item in self.missing_required_groups],
            "forbidden_tools_exposed": list(self.forbidden_tools_exposed),
            "projected_tool_count": self.projected_tool_count,
            "projected_schema_chars": self.projected_schema_chars,
            "complete_relevance_label": self.complete_relevance_label,
        }


class BrokerShadowEvaluator:
    def __init__(self, catalog: ToolCatalog) -> None:
        self._catalog = catalog

    def evaluate(
        self,
        projection: BrokerProjection,
        *,
        actual_legacy_tool: str | None = None,
        expectation: NecessaryToolExpectation | None = None,
    ) -> BrokerShadowResult:
        projected = set(projection.tool_names)
        actual = str(actual_legacy_tool or "").strip() or None
        actual_allowed = None if actual is None else actual in projected
        missing: tuple[tuple[str, ...], ...] = ()
        forbidden: tuple[str, ...] = ()
        recall: float | None = None
        complete = bool(expectation and expectation.complete_relevance_label)
        if expectation is not None:
            missing = tuple(
                group for group in expectation.required_groups
                if not projected.intersection(group)
            )
            forbidden = tuple(sorted(projected.intersection(expectation.forbidden_tools)))
            if complete:
                total = len(expectation.required_groups)
                recall = 1.0 if total == 0 else (total - len(missing)) / total
        schema_chars = sum(
            len(json.dumps(
                self._catalog.get(name).to_dict()["schema"],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ))
            for name in projection.tool_names
        )
        return BrokerShadowResult(
            projection_id=projection.projection_id,
            actual_legacy_tool=actual,
            actual_tool_allowed=actual_allowed,
            necessary_tool_recall=recall,
            missing_required_groups=missing,
            forbidden_tools_exposed=forbidden,
            projected_tool_count=len(projection.tool_names),
            projected_schema_chars=schema_chars,
            complete_relevance_label=complete,
        )


__all__ = [
    "BROKER_SHADOW_RESULT_FORMAT",
    "NecessaryToolExpectation",
    "BrokerShadowResult",
    "BrokerShadowEvaluator",
]
