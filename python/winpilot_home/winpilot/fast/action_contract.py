"""Shared contract for actions accepted by the low-latency controller."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator


ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "set_value",
    "hotkey",
    "scroll",
    "move_mouse",
    "hold_key",
    "drag",
    "draw_strokes",
    "focus_window",
    "set_window_frame",
    "launch",
    "wait",
    "wait_change",
    "wait_for_visual_change",
    "wait_stable",
    "read_text",
    "zoom",
    "screenshot",
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
}

BATCH_MUTATING_ACTIONS = {
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "set_value",
    "hotkey",
    "scroll",
    "move_mouse",
    "hold_key",
    "drag",
    "draw_strokes",
    "focus_window",
    "set_window_frame",
    "launch",
    "clipboard_paste",
}

BATCH_OBSERVE_ACTIONS = {
    "observe",
    "read_text",
    "zoom",
    "screenshot",
    "clipboard_read",
    "auto_visual_verify",
}

BATCH_CLIPBOARD_ACTIONS = {
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
}

VISUAL_TRANSACTION_ACTIONS = BATCH_MUTATING_ACTIONS | {
    "double_click",
    "run_action_batch",
    "execute_subgoal",
    "open_app",
    "hold_key",
}


@dataclass(frozen=True)
class ActionTarget:
    """The exact visual/window coordinate frame an action is bound to."""

    hwnd: int | None = None
    reference_system: str = "semantic"
    source_reference_system: str | None = None
    frame_id: str | int | None = None
    epoch: int | None = None
    region: dict[str, int] | None = None


@dataclass(frozen=True)
class ActionPolicy:
    """Execution semantics kept out of model-generated action arguments."""

    requires_post_frame: bool = False
    interruptible: bool = True
    retry_budget: int = 0


@dataclass(frozen=True)
class ActionEnvelope:
    """Thin operator-style contract between parsing, control, and tracing."""

    action_id: str
    type: str
    inputs: dict[str, Any] = field(default_factory=dict)
    target: ActionTarget = field(default_factory=ActionTarget)
    policy: ActionPolicy = field(default_factory=ActionPolicy)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AtomicAction:
    """One model-declared action after syntax-only normalization."""

    ordinal: int
    name: str
    inputs: dict[str, Any]
    source_path: str
    is_container: bool = False


def _region_dict(value: Any) -> dict[str, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        aliases = dict(zip(("left", "top", "width", "height"), value))
    elif isinstance(value, dict):
        aliases = {
            "left": value.get("left", value.get("x")),
            "top": value.get("top", value.get("y")),
            "width": value.get("width"),
            "height": value.get("height"),
        }
    else:
        aliases = {
            "left": getattr(value, "left", None),
            "top": getattr(value, "top", None),
            "width": getattr(value, "width", None),
            "height": getattr(value, "height", None),
        }
    if any(item is None for item in aliases.values()):
        return None
    try:
        return {key: int(item) for key, item in aliases.items()}
    except (TypeError, ValueError):
        return None


def action_reference_system(name: str, args: dict[str, Any]) -> str:
    # Callers map model-authored frame coordinates to physical screen
    # coordinates before dispatch.  ``coordinate_space`` deliberately records
    # their provenance, not the units of the executable x/y values.  Keep the
    # operator boundary honest about both rather than labelling physical input
    # as normalized.
    if args.get("source_frame_coordinates") is not None:
        return "screen_physical_px"
    explicit = str(
        args.get("reference_system")
        or args.get("coordinate_space")
        or ""
    ).strip()
    if explicit:
        return explicit
    if any(key in args for key in {
        "frame_x", "frame_y", "frame_x1", "frame_y1",
        "frame_x2", "frame_y2", "frame_strokes",
    }):
        return "frame_normalized_0_1000"
    if args.get("normalized_strokes") is not None:
        return "canvas_normalized_0_1000"
    if name in {
        "click", "double_click", "scroll", "move_mouse",
        "drag", "draw_strokes", "zoom",
    }:
        return "screen_physical_px"
    return "semantic"


def action_source_reference_system(args: dict[str, Any]) -> str | None:
    if args.get("source_image_coordinates") is not None:
        return "submitted_image_pixels"
    if args.get("source_frame_coordinates") is not None:
        return "frame_normalized_0_1000"
    return None


def build_action_envelope(
    name: str,
    args: dict[str, Any],
    *,
    action_id: str,
    hwnd: int | None = None,
    frame_id: str | int | None = None,
    epoch: int | None = None,
    region: Any = None,
    retry_budget: int = 0,
) -> ActionEnvelope:
    """Create a canonical action record without changing controller behavior."""
    action_name = str(name or "").strip()
    if not action_name:
        raise ValueError("action envelope requires a non-empty type")
    return ActionEnvelope(
        action_id=str(action_id),
        type=action_name,
        inputs=dict(args or {}),
        target=ActionTarget(
            hwnd=int(hwnd) if hwnd else None,
            reference_system=action_reference_system(action_name, args or {}),
            source_reference_system=action_source_reference_system(args or {}),
            frame_id=frame_id,
            epoch=int(epoch) if epoch is not None else None,
            region=_region_dict(region),
        ),
        policy=ActionPolicy(
            requires_post_frame=action_name in VISUAL_TRANSACTION_ACTIONS,
            interruptible=action_name in {
                "observe", "read_text", "wait", "wait_change",
                "wait_stable", "screenshot", "zoom", "clipboard_read",
                "drag", "draw_strokes", "type_text", "type_into_element",
                "run_action_batch", "execute_subgoal",
            },
            retry_budget=max(0, int(retry_budget)),
        ),
    )


def supported_actions() -> tuple[str, ...]:
    """Stable runtime action list for prompt/operator capability projection."""
    return tuple(sorted(ALLOWED_BATCH_ACTIONS))


def _action_name(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def parse_atomic_action(
    value: Any,
    *,
    ordinal: int = 0,
    source_path: str = "$",
) -> AtomicAction | None:
    """Parse explicit or shorthand action JSON without adding task semantics."""
    if not isinstance(value, dict):
        return None
    action_or_tool = value.get("action") or value.get("tool")
    explicit_name = action_or_tool or value.get("name")
    if explicit_name:
        name = _action_name(explicit_name)
        params = value.get("params")
        discriminator_keys = {"action", "tool", "params"}
        if not action_or_tool:
            discriminator_keys.add("name")
        inputs = {
            **(params if isinstance(params, dict) else {}),
            **{
                key: item
                for key, item in value.items()
                if key not in discriminator_keys
            },
        }
    elif len(value) == 1:
        raw_name, raw_inputs = next(iter(value.items()))
        name = _action_name(raw_name)
        if isinstance(raw_inputs, dict):
            inputs = dict(raw_inputs)
        elif name == "hotkey":
            keys = list(raw_inputs) if isinstance(raw_inputs, (list, tuple)) else [raw_inputs]
            inputs = {"keys": [str(key) for key in keys]}
        elif name in {"type_text", "type_into_element"}:
            inputs = {"text": str(raw_inputs)}
        else:
            inputs = {"value": raw_inputs}
    else:
        return None
    if not name:
        return None
    return AtomicAction(
        ordinal=max(0, int(ordinal)),
        name=name,
        inputs=inputs,
        source_path=str(source_path or "$"),
    )


def canonicalize_batch_actions(
    actions: Any,
) -> tuple[tuple[AtomicAction, ...], list[str]]:
    """Parse each declared batch child exactly once, preserving its position."""
    if not isinstance(actions, list) or not actions:
        return (), ["config.actions must be a non-empty array"]
    canonical: list[AtomicAction] = []
    errors: list[str] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"action {index} must be an object")
            continue
        parsed = parse_atomic_action(
            action,
            ordinal=index - 1,
            source_path=f"actions[{index - 1}]",
        )
        if parsed is None:
            errors.append(f"action {index} has no action/tool name")
            continue
        canonical.append(parsed)
    return tuple(canonical), errors


def iter_atomic_actions(
    tool_name: str,
    args: dict[str, Any],
    *,
    include_container: bool = False,
) -> Iterator[AtomicAction]:
    """Yield direct or container child actions in their declared order."""
    root_name = _action_name(tool_name)
    root_inputs = dict(args or {})
    is_container = root_name in {"run_action_batch", "execute_subgoal"}
    if not is_container:
        if root_name:
            yield AtomicAction(0, root_name, root_inputs, "$")
        return
    ordinal = 0
    if include_container:
        yield AtomicAction(ordinal, root_name, root_inputs, "$", is_container=True)
        ordinal += 1

    declared: list[tuple[str, str, Any]] = []
    if root_name == "execute_subgoal":
        declared.append(("click_element", "click", root_inputs.get("click")))
        clicks = root_inputs.get("clicks")
        if isinstance(clicks, list):
            declared.extend(("click_element", f"clicks[{index}]", item) for index, item in enumerate(clicks))
        declared.extend([
            ("type_into_element", "input", root_inputs.get("input")),
            ("draw_strokes", "draw", root_inputs.get("draw")),
            ("drag", "drag", root_inputs.get("drag")),
        ])
        for field, action_name in (("draws", "draw_strokes"), ("drags", "drag")):
            values = root_inputs.get(field)
            if isinstance(values, list):
                declared.extend((action_name, f"{field}[{index}]", item) for index, item in enumerate(values))

    for action_name, source_path, inputs in declared:
        if not isinstance(inputs, dict):
            continue
        yield AtomicAction(ordinal, action_name, dict(inputs), source_path)
        ordinal += 1
    children = root_inputs.get("actions")
    for index, child in enumerate(children if isinstance(children, list) else []):
        atomic = parse_atomic_action(child, ordinal=ordinal, source_path=f"actions[{index}]")
        if atomic is not None:
            yield atomic
            ordinal += 1


def batch_action_name(action: dict[str, Any]) -> str:
    """Compatibility wrapper around the canonical action parser."""
    parsed = parse_atomic_action(action)
    return parsed.name if parsed is not None else ""


def batch_action_args(action: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_atomic_action(action)
    return dict(parsed.inputs) if parsed is not None else {}


def _missing_action_fields(name: str, args: dict[str, Any]) -> str:
    anchor_keys = {"element_id", "x", "label", "text_query", "fallback_text", "anchor"}
    if name == "click" and not (
        args.get("element_id") is not None
        or (args.get("x") is not None and args.get("y") is not None)
        or any(args.get(key) not in (None, "", []) for key in anchor_keys - {"element_id", "x"})
    ):
        return "requires coordinates, element_id, or a visual anchor"
    if name == "click_element" and not any(
        args.get(key) not in (None, "", []) for key in ("label", "text_query", "fallback_text", "anchor")
    ):
        return "requires label, text_query, fallback_text, or anchor"
    if name in {"type_text", "type_into_element", "clipboard_write"} and "text" not in args:
        return "requires text"
    if name == "set_value":
        if "text" not in args and "value" not in args:
            return "requires text"
        # A value write is addressed, never aimed: it replaces what a control
        # holds, so the caller must say which control.
        if not (
            (args.get("x") is not None and args.get("y") is not None)
            or args.get("element_id") is not None
            or str(args.get("automation_id") or "").strip()
            or any(
                str(args.get(key) or "").strip()
                for key in ("label", "text_query", "anchor")
            )
        ):
            return "requires x and y, element_id, automation_id, or a label"
    if name == "set_window_frame":
        if not any(args.get(key) is not None for key in ("x", "y", "width", "height")):
            return "requires at least one of x, y, width, height"
        for key in ("x", "y", "width", "height"):
            if args.get(key) is None:
                continue
            if isinstance(args.get(key), bool) or not isinstance(args.get(key), (int, float)):
                return f"{key} must be a number"
            if not math.isfinite(float(args[key])):
                return f"{key} must be finite"
        if args.get("width") is not None and float(args["width"]) <= 0:
            return "width must be positive"
        if args.get("height") is not None and float(args["height"]) <= 0:
            return "height must be positive"
    if name == "hotkey" and not (args.get("keys") or args.get("value")):
        return "requires keys"
    if name == "hold_key" and not (args.get("keys") or args.get("value")):
        return "requires keys"
    if name == "hold_key" and not isinstance(args.get("duration_ms"), (int, float)):
        return "requires duration_ms"
    if name == "hold_key" and (
        isinstance(args.get("duration_ms"), bool)
        or not 1 <= float(args.get("duration_ms")) <= 30_000
    ):
        return "requires duration_ms in 1..30000"
    if name in {"scroll", "move_mouse"} and not all(args.get(key) is not None for key in ("x", "y")):
        return "requires x and y"
    if name == "scroll" and args.get("amount") is None:
        return "requires amount"
    if name == "drag" and not all(args.get(key) is not None for key in ("x1", "y1", "x2", "y2")):
        return "requires x1, y1, x2, and y2"
    if name == "focus_window" and not (
        str(args.get("title") or "").strip() or args.get("hwnd") is not None
    ):
        return "requires title or hwnd"
    if name == "launch" and not str(args.get("command") or "").strip():
        return "requires command"
    condition = args.get("condition")
    if name == "wait" and not (
        (isinstance(condition, dict) and any(value not in (None, "", []) for value in condition.values()))
        or any(
            isinstance(args.get(key), (int, float))
            for key in (
                "duration_s", "seconds", "time", "duration_ms", "ms", "value",
            )
        )
    ):
        return "requires condition or duration (numeric)"
    if name in {"wait_change", "wait_for_visual_change"}:
        region = args.get("region") or args.get("effect_region")
        if region is not None and not (
            isinstance(region, dict)
            and all(region.get(key) is not None for key in ("x", "y", "width", "height"))
        ):
            return "region requires x, y, width, and height"
    return ""


def validate_batch_actions(actions: Any) -> list[str]:
    """Validate action shape against the direct controller surface."""
    if (
        isinstance(actions, (list, tuple))
        and actions
        and all(isinstance(action, AtomicAction) for action in actions)
    ):
        canonical = tuple(actions)
        errors: list[str] = []
    else:
        canonical, errors = canonicalize_batch_actions(actions)
    if not canonical:
        return errors or ["config.actions must be a non-empty array"]
    for action in canonical:
        index = action.ordinal + 1
        name = action.name
        if name not in ALLOWED_BATCH_ACTIONS:
            errors.append(f"action {index} uses unsupported controller action {name!r}")
        else:
            missing = _missing_action_fields(name, action.inputs)
            if missing:
                errors.append(f"action {index} ({name}) {missing}")
    return errors
