"""Minimal production ToolCatalog for the live vNext point-input canary.

The catalog describes only contracts that the current MissionRuntime and
AtomicDesktopController can actually consume.  It does not infer tools from an
application, task phrase, or visual layout.
"""
from __future__ import annotations

from ...orchestrator.runtime_admission import RuntimeCapabilitySnapshot
from .contracts import MutationKind, ToolCatalog, ToolRoute, ToolSpec
from .registry import CapabilityOwner, RuntimeCapabilityRegistry


LIVE_TOOL_NAMES = (
    "click",
    "double_click",
    "scroll",
    "claim_obligation_done",
    "blocked",
)

LIVE_CONTROLLER_TOOL_NAMES = (
    "click",
    "double_click",
    "scroll",
)

LIVE_RUNTIME_SERVICES = (
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


def _scroll_schema() -> dict[str, object]:
    schema = _point_schema()
    properties = dict(schema["properties"])
    properties["direction"] = {
        "type": "string",
        "enum": ["up", "down"],
        "description": (
            "Viewport navigation direction: up reveals content above; "
            "down reveals content below."
        ),
    }
    properties["notches"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": 12,
        "description": "Positive wheel-notch magnitude; larger moves farther.",
    }
    schema["properties"] = properties
    schema["required"] = ["image_x", "image_y", "direction", "notches"]
    return schema


def build_live_catalog() -> ToolCatalog:
    """Return the deterministic production catalog for migrated primitives.

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
            name="scroll",
            schema=_scroll_schema(),
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=_CLICK_PREREQUISITES,
            order=30,
        ),
        ToolSpec(
            name="claim_obligation_done",
            schema={
                "type": "object",
                "description": (
                    "Request independent verification of whether the active obligation "
                    "satisfies its acceptance criteria using the current authoritative "
                    "frame and accepted evidence. Calling this tool does not mark the "
                    "obligation or mission complete."
                ),
                "additionalProperties": False,
                "properties": {
                    "concise_claim": {"type": "string", "minLength": 1},
                },
                "required": ["concise_claim"],
            },
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.NONE,
            prerequisites=_FRAME_PREREQUISITES,
            order=40,
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
            order=50,
        ),
    ))


def build_live_runtime_snapshot() -> RuntimeCapabilitySnapshot:
    """Describe the exact point-input composition assembled in production.

    This is a mechanical inventory, not a task classifier.  The resulting
    immutable object is shared by queue-time admission, ExecutionPolicy,
    RuntimeState, and the CapabilityBroker for the lifetime of a coordinator.
    """

    catalog = build_live_catalog()
    return RuntimeCapabilitySnapshot(
        runtime_version="vnext",
        catalog=catalog,
        capability_registry=RuntimeCapabilityRegistry((
            CapabilityOwner(
                "atomic-point-input-controller",
                ("visual.interact",),
            ),
            CapabilityOwner(
                "authoritative-frame-visual-actor",
                ("visual.read_text",),
            ),
        )),
        controller_tool_names=LIVE_CONTROLLER_TOOL_NAMES,
        available_services=LIVE_RUNTIME_SERVICES,
    )


__all__ = [
    "LIVE_CONTROLLER_TOOL_NAMES",
    "LIVE_RUNTIME_SERVICES",
    "LIVE_TOOL_NAMES",
    "build_live_catalog",
    "build_live_runtime_snapshot",
]
