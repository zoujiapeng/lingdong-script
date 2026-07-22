"""Minimal production ToolCatalog for the click-only vNext canary.

The catalog describes only contracts that the current MissionRuntime and
AtomicClickController can actually consume.  It does not infer tools from an
application, task phrase, or visual layout.
"""
from __future__ import annotations

from ...orchestrator.runtime_admission import RuntimeCapabilitySnapshot
from .contracts import MutationKind, ToolCatalog, ToolRoute, ToolSpec
from .registry import CapabilityOwner, RuntimeCapabilityRegistry


CLICK_LIVE_TOOL_NAMES = (
    "click",
    "double_click",
    "claim_obligation_done",
    "blocked",
)

CLICK_LIVE_CONTROLLER_TOOL_NAMES = (
    "click",
    "double_click",
)

CLICK_LIVE_RUNTIME_SERVICES = (
    "authoritative_frame_input",
    "desktop_controller",
    "mission_verifier",
    "obligation_verifier",
    "visual_actor",
)

_FRAME_PREREQUISITES = (
    "target_bound",
    "authoritative_frame",
    "runtime_healthy",
)
_CLICK_PREREQUISITES = (*_FRAME_PREREQUISITES, "pointer_released")


def _point_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }


def build_click_live_catalog() -> ToolCatalog:
    """Return the deterministic, click-only production catalog.

    ``wait`` and region observation are deliberately absent: P4 MissionRuntime
    has no dedicated wait outcome, and the live RuntimeState does not yet own a
    crop projection. Advertising either would create a selectable contract
    that the single production chain cannot complete.
    """

    return ToolCatalog((
        ToolSpec(
            name="click",
            schema=_point_schema(),
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=_CLICK_PREREQUISITES,
            order=10,
        ),
        ToolSpec(
            name="double_click",
            schema=_point_schema(),
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=_CLICK_PREREQUISITES,
            order=20,
        ),
        ToolSpec(
            name="claim_obligation_done",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "concise_claim": {"type": "string", "minLength": 1},
                },
                "required": ["concise_claim"],
            },
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.NONE,
            prerequisites=_FRAME_PREREQUISITES,
            order=30,
        ),
        ToolSpec(
            name="blocked",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "blocker_kind": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9_]{0,63}$",
                    },
                    "observed_facts": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                },
                "required": ["blocker_kind", "observed_facts"],
            },
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.NONE,
            order=40,
        ),
    ))


def build_click_live_runtime_snapshot() -> RuntimeCapabilitySnapshot:
    """Describe the exact click-only composition assembled in production.

    This is a mechanical inventory, not a task classifier.  The resulting
    immutable object is shared by queue-time admission, ExecutionPolicy,
    RuntimeState, and the CapabilityBroker for the lifetime of a coordinator.
    """

    catalog = build_click_live_catalog()
    return RuntimeCapabilitySnapshot(
        runtime_version="vnext",
        catalog=catalog,
        capability_registry=RuntimeCapabilityRegistry((
            CapabilityOwner(
                "atomic-click-controller",
                ("visual.interact",),
            ),
            CapabilityOwner(
                "authoritative-frame-visual-actor",
                ("visual.read_text",),
            ),
        )),
        controller_tool_names=CLICK_LIVE_CONTROLLER_TOOL_NAMES,
        available_services=CLICK_LIVE_RUNTIME_SERVICES,
    )


__all__ = [
    "CLICK_LIVE_CONTROLLER_TOOL_NAMES",
    "CLICK_LIVE_RUNTIME_SERVICES",
    "CLICK_LIVE_TOOL_NAMES",
    "build_click_live_catalog",
    "build_click_live_runtime_snapshot",
]
