"""One product-level capability admission owner for frozen desktop runtimes.

Queue-time admission is deliberately separate from the per-turn Capability
Broker.  It validates that the selected runtime composition can represent the
canonical broad requirements of the task contract; it never selects GUI
tools, targets, routes, or recovery tactics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from ..tasking.capability import CapabilityPlanner, CapabilityUnsatisfiedError
from ..tasking.contracts import TASK_CONTRACT_FORMAT, StageType, TaskContract, TaskStage
from ..vnext.capabilities.contracts import MutationKind, ToolCatalog
from ..vnext.capabilities.registry import RuntimeCapabilityRegistry
from ..vnext.capability_names import InvalidCapability, canonical_capability
from ..vnext.mission import MissionDefinition, project_legacy_contract
from .runtime_selection import DesktopRuntimeSelection


RUNTIME_CAPABILITY_SNAPSHOT_FORMAT = "runtime-capability-snapshot/0"
RUNTIME_ADMISSION_DECISION_FORMAT = "runtime-admission-decision/0"

CANONICAL_RUNTIME_SERVICES = frozenset({
    "authoritative_frame_input",
    "desktop_controller",
    "mission_verifier",
    "obligation_verifier",
    "visual_actor",
})
_VNEXT_REQUIRED_SERVICES = tuple(sorted(CANONICAL_RUNTIME_SERVICES))
_SERVICE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _unique_strings(values: object, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a sequence of strings")
    try:
        result = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be a sequence of strings") from exc
    if any(type(item) is not str or not item.strip() for item in result):
        raise TypeError(f"{name} must contain non-empty strings")
    normalized = tuple(item.strip() for item in result)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class RuntimeCapabilitySnapshot:
    """Immutable composition facts shared by admission, Runtime, and Broker."""

    runtime_version: str
    catalog: ToolCatalog
    capability_registry: RuntimeCapabilityRegistry
    controller_tool_names: tuple[str, ...]
    available_services: tuple[str, ...]
    format: str = RUNTIME_CAPABILITY_SNAPSHOT_FORMAT
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.format != RUNTIME_CAPABILITY_SNAPSHOT_FORMAT:
            raise ValueError("unsupported runtime capability snapshot format")
        if self.runtime_version not in {"legacy", "vnext"}:
            raise ValueError("runtime capability snapshot has invalid runtime_version")
        if not isinstance(self.catalog, ToolCatalog):
            raise TypeError("runtime capability snapshot catalog must be ToolCatalog")
        if not isinstance(self.capability_registry, RuntimeCapabilityRegistry):
            raise TypeError(
                "runtime capability snapshot registry must be RuntimeCapabilityRegistry"
            )
        controller_tools = _unique_strings(
            self.controller_tool_names, "controller_tool_names"
        )
        services = _unique_strings(self.available_services, "available_services")
        unknown_services = set(services) - CANONICAL_RUNTIME_SERVICES
        if unknown_services:
            raise ValueError(
                "runtime capability snapshot has unknown services: "
                + ", ".join(sorted(unknown_services))
            )
        object.__setattr__(self, "controller_tool_names", controller_tools)
        object.__setattr__(self, "available_services", services)

        gui_tools = tuple(sorted(
            tool.name for tool in self.catalog.tools
            if tool.mutation_kind is MutationKind.GUI
        ))
        if controller_tools != gui_tools:
            raise ValueError(
                "runtime capability snapshot catalog/controller GUI tools differ"
            )

        available = set(self.available_capabilities)
        catalog_capabilities = {
            capability
            for tool in self.catalog.tools
            for capability in tool.required_any_capabilities
        }
        if not catalog_capabilities <= available:
            raise ValueError(
                "runtime capability snapshot catalog requires unavailable capabilities"
            )
        if gui_tools and "desktop_controller" not in services:
            raise ValueError(
                "runtime capability snapshot GUI tools require desktop_controller"
            )
        if "visual.interact" in available and not gui_tools:
            raise ValueError(
                "runtime capability snapshot visual.interact has no controller tool"
            )
        if "visual.read_text" in available and not {
            "authoritative_frame_input", "visual_actor",
        } <= set(services):
            raise ValueError(
                "runtime capability snapshot visual.read_text has no frame-input service"
            )
        mechanically_backed = set(catalog_capabilities)
        if {
            "authoritative_frame_input", "visual_actor",
        } <= set(services):
            mechanically_backed.add("visual.read_text")
        unbacked = available - mechanically_backed
        if unbacked:
            raise ValueError(
                "runtime capability snapshot has unbacked capabilities: "
                + ", ".join(sorted(unbacked))
            )

        material = self._fingerprint_material()
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        object.__setattr__(
            self, "fingerprint", hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        )

    @property
    def available_capabilities(self) -> tuple[str, ...]:
        return self.capability_registry.snapshot()

    def _fingerprint_material(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "runtime_version": self.runtime_version,
            "catalog_fingerprint": self.catalog.fingerprint,
            "owners": [
                {
                    "owner_id": owner.owner_id,
                    "capabilities": list(owner.capabilities),
                    "live": owner.live,
                }
                for owner in self.capability_registry.owners
            ],
            "controller_tool_names": list(self.controller_tool_names),
            "available_services": list(self.available_services),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._fingerprint_material(),
            "available_capabilities": list(self.available_capabilities),
            "catalog_tool_names": [tool.name for tool in self.catalog.tools],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RuntimeAdmissionDecision:
    """Accepted queue-time decision; rejected decisions raise before prepare."""

    runtime_version: str
    required_capabilities: tuple[str, ...]
    available_capabilities: tuple[str, ...] | None
    required_services: tuple[str, ...]
    available_services: tuple[str, ...] | None
    reasons: tuple[str, ...]
    snapshot_fingerprint: str | None = None
    admitted: bool = True
    format: str = RUNTIME_ADMISSION_DECISION_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_ADMISSION_DECISION_FORMAT:
            raise ValueError("unsupported runtime admission decision format")
        if self.runtime_version not in {"legacy", "vnext"}:
            raise ValueError("runtime admission decision has invalid runtime_version")
        if self.admitted is not True:
            raise ValueError("only accepted runtime admission decisions are retained")
        for name in ("required_capabilities", "required_services", "reasons"):
            object.__setattr__(self, name, _unique_strings(getattr(self, name), name))
        for name in ("available_capabilities", "available_services"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _unique_strings(value, name))
        if self.snapshot_fingerprint is not None and not _SHA256.fullmatch(
            self.snapshot_fingerprint
        ):
            raise ValueError("runtime admission snapshot fingerprint is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "runtime_version": self.runtime_version,
            "admitted": self.admitted,
            "required_capabilities": list(self.required_capabilities),
            "available_capabilities": (
                None
                if self.available_capabilities is None
                else list(self.available_capabilities)
            ),
            "required_services": list(self.required_services),
            "available_services": (
                None
                if self.available_services is None
                else list(self.available_services)
            ),
            "reasons": list(self.reasons),
            "snapshot_fingerprint": self.snapshot_fingerprint,
        }


def vnext_full_goal_stage(contract: TaskContract) -> TaskStage:
    """Return the one lossless desktop stage accepted by the current vNext."""

    if not isinstance(contract, TaskContract):
        raise TypeError("runtime admission contract must be TaskContract")
    if contract.format != TASK_CONTRACT_FORMAT:
        raise CapabilityUnsatisfiedError(
            "selected vNext runtime requires task-contract/1"
        )
    if contract.compiler_version != "typed-stage-5":
        raise CapabilityUnsatisfiedError(
            "selected vNext runtime requires a lossless typed-stage-5 contract"
        )
    CapabilityPlanner.assert_requirement_coverage(contract)
    if (
        len(contract.stages) != 1
        or contract.stages[0].type is not StageType.EXECUTE_DESKTOP_TASK
    ):
        raise CapabilityUnsatisfiedError(
            "selected vNext runtime requires one full-goal ExecuteDesktopTask"
        )
    return contract.stages[0]


def _vnext_requirements(
    contract: TaskContract,
) -> tuple[MissionDefinition, tuple[str, ...], tuple[str, ...]]:
    vnext_full_goal_stage(contract)
    definition = project_legacy_contract(
        contract,
        mission_id=f"admission-{contract.task_id}",
        run_id=f"admission-run-{contract.task_id}",
    )
    required_obligations = tuple(
        item for item in definition.obligations if item.required
    )
    if len(required_obligations) != 1:
        raise CapabilityUnsatisfiedError(
            "selected vNext runtime requires one required full-goal obligation"
        )
    required: set[str] = set()
    try:
        for obligation in required_obligations:
            required.update(
                canonical_capability(item)
                for item in obligation.required_capabilities
            )
    except InvalidCapability as exc:
        raise CapabilityUnsatisfiedError(
            "selected vNext runtime received an unknown canonical capability"
        ) from exc
    return definition, tuple(sorted(required)), _VNEXT_REQUIRED_SERVICES


class DesktopRuntimeAdmission:
    """Dispatch one queue-time admission from the immutable runtime selection."""

    @staticmethod
    def admit(
        selection: DesktopRuntimeSelection,
        contract: TaskContract | None,
        *,
        runtime_snapshot: RuntimeCapabilitySnapshot | None = None,
    ) -> RuntimeAdmissionDecision:
        if not isinstance(selection, DesktopRuntimeSelection):
            raise TypeError("runtime admission selection must be Runtime-owned")

        if selection.runtime_version == "legacy":
            if runtime_snapshot is not None:
                raise ValueError("legacy runtime cannot consume a vNext snapshot")
            if contract is None:
                return RuntimeAdmissionDecision(
                    runtime_version="legacy",
                    required_capabilities=(),
                    available_capabilities=None,
                    required_services=(),
                    available_services=None,
                    reasons=("legacy_graph_without_task_contract",),
                )
            if not isinstance(contract, TaskContract):
                raise TypeError("runtime admission contract must be TaskContract")
            # Preserve the legacy static/provider checks and their ordering.
            CapabilityPlanner.assert_satisfiable(contract)
            CapabilityPlanner.assert_runtime_satisfiable(contract)
            required = {
                capability
                for stage in contract.stages
                if stage.execution is not None
                for capability in (
                    *stage.execution.required_capabilities,
                    *stage.execution.verifier_requirements,
                )
            }
            return RuntimeAdmissionDecision(
                runtime_version="legacy",
                required_capabilities=tuple(sorted(required)),
                available_capabilities=None,
                required_services=(),
                available_services=None,
                reasons=("legacy_static_and_runtime_preflight_passed",),
            )

        if contract is None:
            raise CapabilityUnsatisfiedError(
                "selected vNext runtime requires task-contract/1"
            )
        if not isinstance(runtime_snapshot, RuntimeCapabilitySnapshot):
            raise CapabilityUnsatisfiedError(
                "selected vNext runtime has no capability composition snapshot"
            )
        if runtime_snapshot.runtime_version != selection.runtime_version:
            raise CapabilityUnsatisfiedError(
                "selected runtime and capability snapshot identities differ"
            )
        _definition, required, required_services = _vnext_requirements(contract)
        available = runtime_snapshot.available_capabilities
        missing = tuple(sorted(set(required) - set(available)))
        if missing:
            raise CapabilityUnsatisfiedError(
                "selected vNext runtime is missing canonical capabilities: "
                + ", ".join(missing)
            )
        missing_services = tuple(sorted(
            set(required_services) - set(runtime_snapshot.available_services)
        ))
        if missing_services:
            raise CapabilityUnsatisfiedError(
                "selected vNext runtime is missing required services: "
                + ", ".join(missing_services)
            )
        return RuntimeAdmissionDecision(
            runtime_version="vnext",
            required_capabilities=required,
            available_capabilities=available,
            required_services=required_services,
            available_services=runtime_snapshot.available_services,
            reasons=("vnext_canonical_composition_admitted",),
            snapshot_fingerprint=runtime_snapshot.fingerprint,
        )


__all__ = [
    "CANONICAL_RUNTIME_SERVICES",
    "RUNTIME_ADMISSION_DECISION_FORMAT",
    "RUNTIME_CAPABILITY_SNAPSHOT_FORMAT",
    "DesktopRuntimeAdmission",
    "RuntimeAdmissionDecision",
    "RuntimeCapabilitySnapshot",
    "vnext_full_goal_stage",
]
