"""Shared contract for actions accepted by the low-latency controller."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "hold_key",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "launch",
    "wait",
    "wait_change",
    "wait_for_visual_change",
    "wait_stable",
    "ensure_document_surface",
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
    "hotkey",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "hold_key",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "launch",
    "ensure_document_surface",
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
    "run_action_batch",
    "execute_subgoal",
    "open_app",
    "paste_image_file",
    "mouse_down",
    "mouse_up",
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
    # AgentRunner maps model-authored frame coordinates to physical screen
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
        "click", "scroll", "move_mouse", "mouse_down", "mouse_up",
        "drag", "draw_strokes", "zoom",
    }:
        return "screen_physical_px"
    return "semantic"


def action_source_reference_system(args: dict[str, Any]) -> str | None:
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


def batch_action_name(action: dict[str, Any]) -> str:
    """Return the action name for explicit and shorthand batch formats."""
    name = str(action.get("action") or action.get("tool") or "").strip()
    if name:
        return name
    if len(action) == 1:
        return str(next(iter(action))).strip()
    return ""


def batch_action_args(action: dict[str, Any]) -> dict[str, Any]:
    if action.get("action") or action.get("tool"):
        explicit = {
            key: value
            for key, value in action.items()
            if key not in {"action", "tool", "params"}
        }
        params = action.get("params")
        return {**(params if isinstance(params, dict) else {}), **explicit}
    if len(action) == 1:
        _name, value = next(iter(action.items()))
        return dict(value) if isinstance(value, dict) else {"value": value}
    return {}


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
    if name in {"mouse_down", "mouse_up"} and str(
        args.get("button") or "left"
    ).casefold() not in {"left", "right", "middle"}:
        return "requires button left, right, or middle"
    if name in {"mouse_down", "mouse_up"} and (
        (args.get("x") is None) != (args.get("y") is None)
    ):
        return "requires x and y together when prepositioning the pointer"
    if name in {"scroll", "move_mouse"} and not all(args.get(key) is not None for key in ("x", "y")):
        return "requires x and y"
    if name == "scroll" and args.get("amount") is None:
        return "requires amount"
    if name == "drag" and not all(args.get(key) is not None for key in ("x1", "y1", "x2", "y2")):
        return "requires x1, y1, x2, and y2"
    if name == "focus_window" and not str(args.get("title") or "").strip():
        return "requires title"
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
    """Validate action shape without importing the heavyweight controller."""
    if not isinstance(actions, list) or not actions:
        return ["config.actions must be a non-empty array"]
    errors: list[str] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"action {index} must be an object")
            continue
        name = batch_action_name(action)
        if not name:
            errors.append(f"action {index} has no action/tool name")
        elif name not in ALLOWED_BATCH_ACTIONS:
            errors.append(f"action {index} uses unsupported controller action {name!r}")
        else:
            missing = _missing_action_fields(name, batch_action_args(action))
            if missing:
                errors.append(f"action {index} ({name}) {missing}")
    # A raw button hold may never cross a Controller/model boundary.  Permit
    # multiple sequential gestures, but require each one to be balanced in
    # this exact batch and allow only bounded movement/wait primitives while
    # the button is held.  Atomic ``drag`` remains the preferred common path.
    held_button = ""
    held_at = 0
    allowed_while_held = {
        "move_mouse", "wait", "wait_change", "wait_for_visual_change",
        "wait_stable",
    }
    for index, action in enumerate(actions if isinstance(actions, list) else [], start=1):
        if not isinstance(action, dict):
            continue
        name = batch_action_name(action)
        args = batch_action_args(action)
        button = str(args.get("button") or "left").casefold()
        if name == "mouse_down":
            if held_button:
                errors.append(
                    f"action {index} (mouse_down) cannot press {button} while "
                    f"{held_button} from action {held_at} is still held"
                )
            else:
                held_button = button
                held_at = index
            continue
        if name == "mouse_up":
            if not held_button:
                errors.append(
                    f"action {index} (mouse_up) has no matching mouse_down in this batch"
                )
            elif button != held_button:
                errors.append(
                    f"action {index} (mouse_up) releases {button}, but action "
                    f"{held_at} pressed {held_button}"
                )
            else:
                held_button = ""
                held_at = 0
            continue
        if held_button and name and name not in allowed_while_held:
            errors.append(
                f"action {index} ({name}) is not allowed while mouse button "
                f"{held_button} from action {held_at} is held"
            )
    if held_button:
        errors.append(
            f"action {held_at} (mouse_down) has no matching mouse_up in this batch"
        )
    return errors
