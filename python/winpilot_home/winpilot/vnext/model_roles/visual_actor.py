"""One semantic Visual Actor backed by the shared model call Runtime."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...modeling import (
    ModelAttempt,
    ModelCallRuntime,
    ModelRetryPolicy,
    invoke_chat_completion,
)
from ..actor import (
    ACTOR_PROPOSAL_FORMAT,
    InvalidActorProposal,
    actor_proposal_to_json,
    parse_actor_proposal,
)
from ..runtime.contracts import (
    APPROVED_DESKTOP_ACTIONS,
    APPROVED_RUNTIME_TOOLS,
    TASK_STATE_UPDATE_SCHEMA,
    ActorTaskStateError,
    ActorTaskStateUpdate,
    ActorToolView,
    ActorTurnDecision,
    ActorTurnInput,
)
from .common import (
    LiveModelRoleError,
    member,
    narration_content,
    require_empty_content,
    require_jpeg,
    require_positive_int,
    require_positive_timeout,
    require_text,
    response_message,
    strict_json_object,
)


VISUAL_ACTOR_MODEL_ROLE = "vnext_visual_actor"
VISUAL_ACTOR_SYSTEM_CONTRACT = """\
Use original_goal, current JPEG, task_state, previous action, and tools. Choose
one tool, or batch independent non-GUI tools; put dependencies later. Briefly
narrate. Coordinates are exact submitted-image pixels. Chat and act naturally.
Revise task_state; runtime does not classify or route. Keep dependent unfinished
work in remaining_items. If visibly complete, call claim_obligation_done only
when none remain. If no action can advance, call blocked. When pointer_held is
true, it persists across actions; continue or release deliberately. Pace long
goals with budget facts. If actions add no new
information, step back to the whole screen and revise the approach toward
original_goal."""

INITIAL_ACTOR_SYSTEM_CONTRACT = """\
You are a Windows desktop agent on the user's real computer. Answer chat
questions directly in the user's language. The observe_desktop tool grants
access to the live screen; use it when you need to look at or operate the
desktop. You may chat, act, or interleave both freely: narrate what you are
doing briefly when useful. Tool calls in one response run concurrently; put
dependent steps in later responses after reading their results. Keep any
useful phase in task_state_update and dependent unfinished work in its
remaining_items; revise both as you learn. For
mixed requests, respond naturally while you work and summarize at the end.
Never invent results; verify by looking at the screen."""

_TOOL_DESCRIPTIONS = {
    "move_mouse": (
        "Move the real pointer to one point in the current image and leave it "
        "there. Set duration_ms for smooth hover or game movement."
    ),
    "click": (
        "Click one point once in the current image with any mouse button. "
        "press_duration_ms controls how long the button stays down."
    ),
    "double_click": (
        "Double-click one point in the current image with any mouse button."
    ),
    "drag": (
        "Drag through image_path written as `x1,y1 x2,y2 ...` in exact order. "
        "The runtime draws one straight segment between every adjacent pair: "
        "put a point at each visible turn and keep constrained routes near the "
        "center of their permitted path. The first pair is where to press and "
        "later pairs are the route. "
        "held_keys presses those keyboard keys before the mouse and releases "
        "them after the mouse, so modified drags and game gestures stay one "
        "physical action. keep_holding preserves the same mouse and held-key "
        "presses for the next visual turn."
    ),
    "scroll": (
        "Scroll at one point using a native wheel delta: amount < 0 scrolls "
        "down, amount > 0 scrolls up, and 120 is one ordinary notch. Typical "
        "page movement is 360 to 720 in magnitude."
    ),
    "type_text": (
        "Enter the requested literal characters into the focused editable "
        "control; selected text is replaced."
    ),
    "hotkey": (
        "Press one key or one ordered key chord through the physical keyboard; "
        "this never enters literal text. Set repeat for repeated presses in one "
        "mechanically reported action. Standard named keys include volumeup, "
        "volumedown, volumemute, arrows, function keys, and browser keys. "
        "Volume keys move relative to the current system level; inspect the "
        "next screen to judge the resulting level."
    ),
    "input_sequence": (
        "Run one atomic human-like mouse/keyboard timeline. Separate commands "
        "with semicolons: `move x,y duration_ms`; `mouse_down button`; "
        "`mouse_up button`; `key_down key`; `key_up key`; `click button`; "
        "`press key`; `wheel amount`; `hwheel amount`; `wait duration_ms`. "
        "Wheel amounts are native deltas (120 is one ordinary notch). "
        "Use `move_by dx,dy duration_ms` for relative raw/game movement. "
        "Absolute coordinates and relative deltas are from the current image. "
        "Use this when a modifier or mouse button must stay down while the "
        "other device moves. "
        "Keys/buttons still held at the end are released automatically."
    ),
    "launch_application": (
        "Start one installed application or file/URL association by display "
        "name, alias, or path. This is a generic launch, not a click."
    ),
    "observe": (
        "Request a fresh whole-desktop frame without physical input. Use this "
        "to leave a repetitive local-tool chain, refresh visual context, and "
        "reconsider the next step."
    ),
    "claim_obligation_done": (
        "Finish without further mutation when the current image shows the "
        "requested result and your remaining_items has no unfinished work."
    ),
    "blocked": (
        "Report a visible blocker only when no supplied action can advance the goal."
    ),
}

_MODEL_TOOL_NAMES = {
    "hotkey": "press_key",
    "observe": "refresh_desktop",
}
_RUNTIME_TOOL_NAMES = {
    model_name: runtime_name
    for runtime_name, model_name in _MODEL_TOOL_NAMES.items()
}
_STRICT_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "type",
    }
)
_LOCALLY_ENFORCED_SCHEMA_KEYS = frozenset({"not", "uniqueItems"})
_FLOW_FRAGMENT_REQUEST_CHARS = 16_384
_VISUAL_HISTORY_DEPTH = 4
_VISUAL_UTILITY_HISTORY_DEPTH = 4
_STANDALONE_CONNECTOR_CALL_ALLOWANCE = 4
_CONNECTOR_SEARCH_TOOL = "search_connector_tools"
_CONNECTOR_CALL_TOOL = "call_connector_tool"
_CONNECTOR_TOOLS = frozenset({_CONNECTOR_SEARCH_TOOL, _CONNECTOR_CALL_TOOL})
_FLOW_FRAGMENT_TOOL = "propose_flow_fragment"
_STATUS_AGENT_TOOL = "get_agent_status"
_STATUS_MEMORY_TOOL = "get_memory_status"
_STATUS_CAPABILITIES_TOOL = "get_capabilities"
_STATUS_TOOLS = frozenset(
    {
        _STATUS_AGENT_TOOL,
        _STATUS_MEMORY_TOOL,
        _STATUS_CAPABILITIES_TOOL,
    }
)
_STANDALONE_STATUS_CALL_ALLOWANCE = 2
_SHELL_TOOL = "run_shell"
_DETACHED_SHELL_TOOL = "run_detached"
_WRITE_FILE_TOOL = "write_file"
_LOCAL_UTILITY_TOOLS = frozenset({_DETACHED_SHELL_TOOL, _WRITE_FILE_TOOL})
_STANDALONE_SHELL_CALL_ALLOWANCE = 4
_SHELL_MAX_COMMAND_CHARS = 16_384
_WINDOW_TEXT_TOOL = "read_window_text"
_STANDALONE_WINDOW_TEXT_ALLOWANCE = 2
_WINDOW_TEXT_MAX_CHARS = 6000
_CAPTURE_SCREEN_TOOL = "capture_screen"
_STANDALONE_CAPTURE_ALLOWANCE = 2
_LIST_WINDOWS_TOOL = "list_windows"
_STANDALONE_LIST_WINDOWS_ALLOWANCE = 1
_PASTE_IMAGE_TOOL = "paste_image_file"
_STANDALONE_PASTE_IMAGE_ALLOWANCE = 2
_VISUAL_UTILITY_TOOLS = frozenset({
    *_STATUS_TOOLS,
    *_CONNECTOR_TOOLS,
    _SHELL_TOOL,
    *_LOCAL_UTILITY_TOOLS,
    _WINDOW_TEXT_TOOL,
    _CAPTURE_SCREEN_TOOL,
    _LIST_WINDOWS_TOOL,
    _PASTE_IMAGE_TOOL,
})
_CAPTURE_OUTPUT_MAX_CHARS = 6000
# Optional tool-schema fields and their JSON defaults.  The model may omit
# them; the runtime fills the default so the tool schema and the validator
# never disagree.
_OPTIONAL_TOOL_DEFAULTS = {
    _CAPTURE_SCREEN_TOOL: {"window": False},
    _DETACHED_SHELL_TOOL: {"name": "task"},
}


@dataclass(frozen=True, slots=True)
class _InitialExchange:
    messages: tuple[dict[str, Any], ...]
    tool_call_id: str


@dataclass(slots=True)
class _VisualExchange:
    messages: tuple[dict[str, Any], ...]
    tool_call_id: str
    runtime_tool_name: str
    result_content: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VisualActorTurnAdmission:
    """One text-first Actor result before any desktop lease is acquired."""

    kind: str
    reply: str = ""
    narration: str = ""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _repeated_tool_result(
    result: Mapping[str, Any],
    *,
    same_as_tool_call_id: str,
    repeat_count: int,
) -> dict[str, Any]:
    """Mark an identical result while keeping the latest feedback usable."""

    repeated = dict(result)
    repeated.update({
        "result_unchanged": True,
        "new_tool_information": False,
        "same_as_tool_call_id": same_as_tool_call_id,
        "repeat_count": repeat_count,
        "message": (
            "Executed; this tool returned the same output as the referenced "
            "call."
        ),
    })
    if result.get("error") and "ok" not in repeated:
        repeated["ok"] = False
    return repeated


def _project_tool_result(
    seen: dict[tuple[str, str], tuple[str, int]],
    *,
    name: str,
    call_id: str,
    result: Mapping[str, Any],
    model_calls_remaining: int | None = None,
) -> dict[str, Any]:
    """Annotate repeated execution without choosing the model's context."""

    comparable = dict(result)
    comparable.pop("full_output_path", None)
    try:
        key = (name, _canonical_json(comparable))
    except (TypeError, ValueError):
        projected = dict(result)
        if model_calls_remaining is not None:
            projected["model_calls_remaining"] = model_calls_remaining
        return projected
    previous = seen.get(key)
    if previous is None:
        seen[key] = (call_id, 0)
        projected = dict(result)
        if model_calls_remaining is not None:
            projected["model_calls_remaining"] = model_calls_remaining
        return projected
    first_call_id, previous_count = previous
    repeat_count = previous_count + 1
    seen[key] = (first_call_id, repeat_count)
    projected = _repeated_tool_result(
        result,
        same_as_tool_call_id=first_call_id,
        repeat_count=repeat_count,
    )
    if model_calls_remaining is not None:
        projected["model_calls_remaining"] = model_calls_remaining
    return projected


def _projected_tool_message(
    seen: dict[tuple[str, str], tuple[str, int]],
    *,
    name: str,
    call_id: str,
    result: Mapping[str, Any],
    model_calls_remaining: int | None = None,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": _canonical_json(
            _project_tool_result(
                seen,
                name=name,
                call_id=call_id,
                result=result,
                model_calls_remaining=model_calls_remaining,
            )
        ),
    }


def _finish_reason(response: Any) -> str:
    choices = member(response, "choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        return ""
    return str(member(choices[0], "finish_reason") or "").casefold()


def _truncation_messages(response: Any) -> list[dict[str, Any]]:
    """Retry without feeding an incomplete tool payload back to the model."""

    del response
    return [
        {
            "role": "user",
            "content": "Output was truncated. Continue with a shorter tool call.",
        },
    ]


def _actionable_tool_result(
    name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep tool failures in-band so the same model can change course."""

    output = dict(result)
    failed = bool(
        output.get("ok") is False
        or output.get("error")
        or output.get("timeout") is True
        or output.get("stopped") is True
    )
    if not failed or output.get("next_step"):
        return output
    if output.get("timeout") is True:
        next_step = (
            "this attempt timed out; use any partial output and current state "
            "to choose the next step"
        )
    elif name == _SHELL_TOOL and output.get("exit_code") not in (None, 0):
        next_step = "use the output to correct the command or choose another tool"
    elif output.get("stopped") is True:
        next_step = "do not retry unless the run continues"
    else:
        next_step = "change the arguments, inspect current state, or choose another tool"
    output["next_step"] = next_step
    return output


def _actionable_tool_error(name: str, exc: Exception) -> dict[str, Any]:
    detail = str(exc).strip() or type(exc).__name__
    return _actionable_tool_result(
        name,
        {"ok": False, "error": detail[:2000]},
    )


def _tool_function_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool, Mapping) else None
    name = function.get("name") if isinstance(function, Mapping) else None
    return name if type(name) is str else ""


def _decision_id(turn: ActorTurnInput, image_b64: str) -> str:
    digest = hashlib.sha256()
    digest.update(turn.to_json().encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(image_b64.encode("ascii")).digest())
    return f"visual-actor-{digest.hexdigest()[:24]}"


def _initial_decision_id(
    goal: str,
    history: Sequence[Mapping[str, Any]],
) -> str:
    digest = hashlib.sha256()
    for message in history:
        digest.update(_canonical_json(message).encode("utf-8"))
        digest.update(b"\0")
    digest.update(goal.encode("utf-8"))
    return f"visual-actor-initial-{digest.hexdigest()[:24]}"


def _thread_messages(
    history: Sequence[Mapping[str, Any]],
    goal: str,
) -> list[dict[str, str]]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise LiveModelRoleError("Actor thread history must be a sequence")
    messages: list[dict[str, str]] = []
    for raw in history:
        if not isinstance(raw, Mapping) or set(raw) != {"role", "content"}:
            raise LiveModelRoleError(
                "Actor thread history messages require only role and content"
            )
        role = raw.get("role")
        content = raw.get("content")
        if role not in {"user", "assistant"} or type(content) is not str:
            raise LiveModelRoleError(
                "Actor thread history supports only text user and assistant messages"
            )
        if not content.strip():
            raise LiveModelRoleError("Actor thread history content must not be empty")
        messages.append({"role": role, "content": content})
    # Preserve the native prefix supplied by the Thread owner.  The Actor
    # validates its shape but does not choose which successful turns the model
    # may see or silently rebuild the provider's cache prefix.
    messages.append({"role": "user", "content": goal})
    return messages


def _strict_provider_schema(
    raw: dict[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    """Project the Broker schema into OpenAI's strict supported subset.

    ``not`` and ``uniqueItems`` are enforced again by Runtime's canonical
    Broker argument admission before mutation. They cannot be sent in a strict
    Structured Outputs schema, so only those two assertions are omitted here.
    Unknown keywords fail closed instead of reaching the provider.
    """
    unsupported = set(raw) - (
        _STRICT_SCHEMA_KEYS | _LOCALLY_ENFORCED_SCHEMA_KEYS
    )
    if unsupported:
        raise LiveModelRoleError(
            f"{path} has unsupported strict schema keywords: "
            f"{sorted(unsupported)!r}"
        )
    if "not" in raw and raw["not"] != {"const": 0}:
        raise LiveModelRoleError(
            f"{path}.not is not the locally enforced nonzero assertion"
        )
    if "uniqueItems" in raw and raw["uniqueItems"] is not True:
        raise LiveModelRoleError(
            f"{path}.uniqueItems must be true when present"
        )

    projected = {
        key: value
        for key, value in raw.items()
        if key not in _LOCALLY_ENFORCED_SCHEMA_KEYS
    }
    properties = projected.get("properties")
    if properties is not None:
        if type(properties) is not dict or any(
            type(name) is not str or type(schema) is not dict
            for name, schema in properties.items()
        ):
            raise LiveModelRoleError(
                f"{path}.properties must map names to schemas"
            )
        projected["properties"] = {
            name: _strict_provider_schema(
                schema,
                path=f"{path}.properties.{name}",
            )
            for name, schema in properties.items()
        }
    items = projected.get("items")
    if items is not None:
        if type(items) is not dict:
            raise LiveModelRoleError(f"{path}.items must be a schema")
        projected["items"] = _strict_provider_schema(
            items,
            path=f"{path}.items",
        )
    alternatives = projected.get("anyOf")
    if alternatives is not None:
        if (
            type(alternatives) is not list
            or not alternatives
            or any(type(schema) is not dict for schema in alternatives)
        ):
            raise LiveModelRoleError(
                f"{path}.anyOf must be a non-empty schema array"
            )
        projected["anyOf"] = [
            _strict_provider_schema(
                schema,
                path=f"{path}.anyOf[{index}]",
            )
            for index, schema in enumerate(alternatives)
        ]
    if projected.get("type") == "object":
        if type(properties) is not dict:
            raise LiveModelRoleError(
                f"{path} object schema requires properties"
            )
        required = projected.get("required")
        if type(required) is not list or len(required) != len(set(required)):
            raise LiveModelRoleError(
                f"{path} required must be a de-duplicated list"
            )
        missing_required = set(required) - set(properties)
        if missing_required:
            raise LiveModelRoleError(
                f"{path} required names are not properties: "
                f"{sorted(missing_required)!r}"
            )
        optional_without_default = {
            name
            for name, schema in properties.items()
            if name not in required and "default" not in schema
        }
        if optional_without_default:
            raise LiveModelRoleError(
                f"{path} optional properties need an explicit default: "
                f"{sorted(optional_without_default)!r}"
            )
        if projected.get("additionalProperties") is not False:
            raise LiveModelRoleError(
                f"{path} must forbid extras"
            )
    return projected


def _actor_parameters_from_schema(
    name: str,
    raw: Mapping[str, Any],
    *,
    requires_local_intent: bool,
) -> dict[str, Any]:
    if (
        type(raw) is not dict
        or raw.get("type") != "object"
        or type(raw.get("properties")) is not dict
    ):
        raise LiveModelRoleError(
            f"Actor tool {name!r} requires an object JSON schema"
        )
    parameters = dict(raw)
    properties = dict(raw["properties"])
    required = raw.get("required", [])
    if type(required) is not list or any(type(item) is not str for item in required):
        raise LiveModelRoleError(
            f"Actor tool {name!r} has invalid required fields"
        )
    required = list(required)
    if "task_state_update" in properties or "task_state_update" in required:
        raise LiveModelRoleError(
            f"Actor tool {name!r} shadows task_state_update"
        )
    properties["task_state_update"] = {
        **TASK_STATE_UPDATE_SCHEMA,
        "default": {"scratchpad": None},
    }
    if requires_local_intent:
        if "local_intent" in properties or "local_intent" in required:
            raise LiveModelRoleError(
                f"Actor tool {name!r} shadows local_intent"
            )
        properties["local_intent"] = {
            "type": "string",
            "default": "",
            "maxLength": 16384,
        }
    parameters["properties"] = properties
    parameters["required"] = required
    parameters["additionalProperties"] = False
    return _strict_provider_schema(
        parameters,
        path=f"Actor tool {name!r}",
    )


def _actor_parameters(
    tool: ActorToolView,
    *,
    pointer_held: bool,
) -> dict[str, Any]:
    raw = tool.to_dict()["schema"]
    if tool.name == "drag":
        properties = dict(raw["properties"])
        required = list(raw.get("required", []))
        if "image_path" not in required:
            required.append("image_path")
        # Array-shaped paths caused a real provider to serialize one coordinate
        # pair thousands of times until the response was truncated, both for
        # nested and flat arrays. Keep the model-facing shape as one ordinary
        # string and pair it once at the semantic boundary.
        # The canonical Runtime and physical device still receive an ordered
        # list of points, with no arbitrary path-length limit.
        properties["image_path"] = {
            "type": "string",
            "minLength": 1,
        }
        # Keep the model-facing gesture compact.  The canonical boundary also
        # accepts arrays, comma-separated text and ``shift+w``; the model only
        # needs to name the keys, not reason about the internal key catalog.
        if "held_keys" in properties:
            properties["held_keys"] = {
                "type": "string",
                "default": "",
            }
        raw = {
            **raw,
            "properties": properties,
            "required": required,
        }
    return _actor_parameters_from_schema(
        tool.name,
        raw,
        requires_local_intent=tool.requires_local_intent,
    )


def _with_task_state(
    definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Let the Actor revise its free-form working state on utility calls."""

    for tool in definitions:
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            continue
        properties = parameters.get("properties")
        if not isinstance(properties, dict):
            continue
        properties["task_state_update"] = {
            **deepcopy(TASK_STATE_UPDATE_SCHEMA),
            "default": {"scratchpad": None},
        }
    return definitions


def _tool_definitions(turn: ActorTurnInput) -> list[dict[str, Any]]:
    names = tuple(tool.name for tool in turn.tools)
    if not names or not set(names) <= APPROVED_RUNTIME_TOOLS:
        raise LiveModelRoleError(
            "Visual Actor tools are outside the minimal Runtime surface"
        )
    return [
        {
            "type": "function",
            "function": {
                "name": _MODEL_TOOL_NAMES.get(tool.name, tool.name),
                "description": (
                    "Continue the currently held drag through image_path "
                    "written as `x1,y1 x2,y2 ...`; the physical "
                    "cursor is already the start. keep_holding decides whether "
                    "to remain pressed."
                    if tool.name == "drag" and turn.pointer_held
                    else _TOOL_DESCRIPTIONS[tool.name]
                ),
                "parameters": _actor_parameters(
                    tool,
                    pointer_held=turn.pointer_held,
                ),
                "strict": True,
            },
        }
        for tool in turn.tools
    ]


def _decode_arguments(raw: Any) -> dict[str, Any]:
    return strict_json_object(raw, "Actor tool arguments")


def _canonical_drag_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Pair the model-facing path string for the canonical Runtime contract."""

    raw_path = arguments.get("image_path")
    if isinstance(raw_path, str):
        values = re.findall(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",
            raw_path,
        )
        if not values or len(values) % 2:
            raise InvalidActorProposal(
                "drag image_path must contain complete x,y coordinate pairs"
            )
        canonical_path = [
            [float(values[index]), float(values[index + 1])]
            for index in range(0, len(values), 2)
        ]
    elif not isinstance(raw_path, (list, tuple)) or not raw_path:
        raise InvalidActorProposal(
            "drag image_path must be `x1,y1 x2,y2 ...`"
        )
    # Accept prior array representations as forgiving compatibility paths; the
    # provider is shown only the simpler string schema.
    elif all(
        isinstance(point, (list, tuple)) and len(point) == 2
        for point in raw_path
    ):
        canonical_path = [list(point) for point in raw_path]
    else:
        if len(raw_path) % 2:
            raise InvalidActorProposal(
                "drag image_path must contain complete x,y coordinate pairs"
            )
        canonical_path = [
            [raw_path[index], raw_path[index + 1]]
            for index in range(0, len(raw_path), 2)
        ]
    canonical = {**arguments, "image_path": canonical_path}
    canonical.setdefault("button", "left")
    # Keep the Actor semantic: it forwards the model's compact key spelling.
    # The desktop adapter already owns forgiving physical-key normalization.
    if not canonical.get("held_keys"):
        canonical.pop("held_keys", None)
    canonical.setdefault("duration_ms", 400)
    return canonical


def _canonical_desktop_arguments(
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    canonical = dict(arguments)
    if name in {"click", "double_click"}:
        canonical.setdefault("button", "left")
        canonical.setdefault("press_duration_ms", 35)
    elif name == "move_mouse":
        canonical.setdefault("duration_ms", 0)
    return canonical


def _flow_fragment_tool_definition(
    allowed_node_types: Sequence[str],
) -> dict[str, Any]:
    node_types = list(dict.fromkeys(allowed_node_types))
    if not node_types or any(type(item) is not str or not item for item in node_types):
        raise LiveModelRoleError("flow fragment node catalog is invalid")
    return {
        "type": "function",
        "function": {
            "name": _FLOW_FRAGMENT_TOOL,
            "description": (
                "Propose one editable, non-running flow fragment using only "
                "the supplied node catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 1000},
                    "nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "type": {"type": "string", "enum": node_types},
                                "label": {"type": "string", "maxLength": 120},
                                "params_json": {"type": "string", "minLength": 2, "maxLength": 4096},
                            },
                            "required": ["local_id", "type", "label", "params_json"],
                            "additionalProperties": False,
                        },
                    },
                    "edges": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string", "minLength": 1, "maxLength": 64},
                                "target": {"type": "string", "minLength": 1, "maxLength": 64},
                                "source_port": {"type": "string", "minLength": 1, "maxLength": 64},
                            },
                            "required": ["source", "target", "source_port"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "nodes", "edges"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _flow_fragment_from_response(
    response: Any,
    catalog_ports: Mapping[str, Sequence[str]],
    catalog_params: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    message = response_message(response)
    require_empty_content(message)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        raise LiveModelRoleError("flow fragment proposal requires exactly one tool call")
    call = calls[0]
    function = member(call, "function")
    if member(call, "type") != "function" or function is None:
        raise LiveModelRoleError("flow fragment proposal must be a function call")
    if member(function, "name") != _FLOW_FRAGMENT_TOOL:
        raise LiveModelRoleError("Actor returned an unapproved flow fragment tool")
    arguments = _decode_arguments(member(function, "arguments"))
    if set(arguments) != {"summary", "nodes", "edges"}:
        raise LiveModelRoleError("flow fragment proposal fields mismatch")
    summary = arguments["summary"]
    raw_nodes = arguments["nodes"]
    raw_edges = arguments["edges"]
    if type(summary) is not str or len(summary) > 1000:
        raise LiveModelRoleError("flow fragment summary is invalid")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= 32:
        raise LiveModelRoleError("flow fragment must contain 1 to 32 nodes")
    if not isinstance(raw_edges, list) or len(raw_edges) > 64:
        raise LiveModelRoleError("flow fragment contains too many edges")

    nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    node_types: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping) or set(raw) != {
            "local_id", "type", "label", "params_json"
        }:
            raise LiveModelRoleError(f"flow fragment node {index + 1} fields mismatch")
        local_id = raw["local_id"]
        node_type = raw["type"]
        label = raw["label"]
        params_json = raw["params_json"]
        if type(local_id) is not str or not local_id.strip() or len(local_id) > 64:
            raise LiveModelRoleError(f"flow fragment node {index + 1} id is invalid")
        local_id = local_id.strip()
        if local_id in ids:
            raise LiveModelRoleError("flow fragment contains duplicate node ids")
        if type(node_type) is not str or node_type not in catalog_ports:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} type is unavailable")
        if type(label) is not str or len(label) > 120:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} label is invalid")
        if type(params_json) is not str or not 2 <= len(params_json) <= 4096:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} params are invalid")
        params = strict_json_object(
            params_json,
            f"flow fragment node {local_id!r} params_json",
        )
        unknown_params = set(params) - catalog_params[node_type]
        if unknown_params:
            raise LiveModelRoleError(
                f"flow fragment node {local_id!r} uses unavailable params: "
                + ", ".join(sorted(unknown_params))
            )
        ids.add(local_id)
        node_types[local_id] = node_type
        nodes.append({
            "local_id": local_id,
            "type": node_type,
            "label": label.strip(),
            "params": params,
        })

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    seen_branches: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, Mapping) or set(raw) != {
            "source", "target", "source_port"
        }:
            raise LiveModelRoleError(f"flow fragment edge {index + 1} fields mismatch")
        source, target, source_port = (
            raw["source"], raw["target"], raw["source_port"]
        )
        if not all(type(item) is str and item.strip() for item in (source, target, source_port)):
            raise LiveModelRoleError(f"flow fragment edge {index + 1} is invalid")
        source, target, source_port = source.strip(), target.strip(), source_port.strip()
        if source not in ids or target not in ids:
            raise LiveModelRoleError("flow fragment edge references an unknown node")
        if source == target:
            raise LiveModelRoleError("flow fragment edge cannot target its source node")
        if source_port not in catalog_ports[node_types[source]]:
            raise LiveModelRoleError(
                f"flow fragment node {source!r} has no {source_port!r} output"
            )
        key = (source, target, source_port)
        if key in seen_edges:
            raise LiveModelRoleError("flow fragment contains a duplicate edge")
        branch = (source, source_port)
        if branch in seen_branches:
            raise LiveModelRoleError(
                "flow fragment output branch can target only one node"
            )
        seen_edges.add(key)
        seen_branches.add(branch)
        edges.append({"source": source, "target": target, "source_port": source_port})

    outgoing = {local_id: [] for local_id in ids}
    indegree = {local_id: 0 for local_id in ids}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    roots = [local_id for local_id, degree in indegree.items() if degree == 0]
    ordered = 0
    while roots:
        current = roots.pop()
        ordered += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                roots.append(target)
    if ordered != len(nodes):
        raise LiveModelRoleError("flow fragment must not contain a directed cycle")
    if len(nodes) > 1:
        if not edges:
            raise LiveModelRoleError(
                "multi-node flow fragment requires internal edges"
            )
        neighbours = {local_id: set() for local_id in ids}
        for edge in edges:
            neighbours[edge["source"]].add(edge["target"])
            neighbours[edge["target"]].add(edge["source"])
        pending = [next(iter(ids))]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(neighbours[current] - visited)
        if visited != ids:
            raise LiveModelRoleError(
                "flow fragment must not contain disconnected nodes"
            )
    return {"summary": summary.strip(), "nodes": nodes, "edges": edges}


def _runtime_name_from_model_call(name: str, raw_arguments: Any) -> str:
    del raw_arguments
    return _RUNTIME_TOOL_NAMES.get(name, name)


def _proposal_from_response(
    response: Any,
    allowed_names: frozenset[str],
    available_runtime_names: frozenset[str],
) -> ActorTurnDecision:
    message = response_message(response)
    narration = narration_content(message)
    tool_calls = member(message, "tool_calls")
    if (
        isinstance(tool_calls, (str, bytes))
        or not isinstance(tool_calls, Sequence)
        or len(tool_calls) != 1
    ):
        raise LiveModelRoleError(
            "Visual Actor must return exactly one tool call"
        )
    call = tool_calls[0]
    call_type = member(call, "type")
    if call_type != "function":
        raise LiveModelRoleError("Visual Actor tool call must be a function")
    function = member(call, "function")
    if function is None:
        raise LiveModelRoleError("Visual Actor tool call has no function")
    name = member(function, "name")
    if type(name) is not str or name not in allowed_names:
        raise LiveModelRoleError("Visual Actor returned an unapproved tool")
    arguments = _decode_arguments(member(function, "arguments"))
    try:
        state_update = ActorTaskStateUpdate.from_dict(
            arguments.pop("task_state_update", None)
        )
    except ActorTaskStateError as exc:
        raise InvalidActorProposal(str(exc)) from exc
    local_intent = arguments.pop("local_intent", None)
    runtime_name = _RUNTIME_TOOL_NAMES.get(name, name)
    if runtime_name not in available_runtime_names:
        raise LiveModelRoleError("Visual Actor returned an unavailable action")
    if runtime_name == "drag":
        arguments = _canonical_drag_arguments(arguments)
    elif runtime_name in {"click", "double_click", "move_mouse"}:
        arguments = _canonical_desktop_arguments(runtime_name, arguments)
    if runtime_name in APPROVED_DESKTOP_ACTIONS:
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "desktop_action",
            "action": runtime_name,
            "args": arguments,
            "local_intent": local_intent or "",
        }
    elif runtime_name == "observe":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "observe",
            "observation_kind": "full",
            "image_region": None,
            "question": arguments.get("question"),
        }
    elif runtime_name == "claim_obligation_done":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "claim_obligation_done",
            **arguments,
        }
    elif runtime_name == "blocked":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "blocked",
            **arguments,
        }
    else:  # pragma: no cover - guarded by the exact minimal tool set
        raise LiveModelRoleError("Visual Actor returned an unsupported outcome")
    proposal = parse_actor_proposal(raw_proposal)
    return ActorTurnDecision(
        proposal=json.loads(actor_proposal_to_json(proposal)),
        task_state_update=state_update,
        narration=narration,
    )


def _history_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str] | None:
    """Keep one compact provider-native GUI call for the next visual turn."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    raw_arguments = (
        member(function, "arguments") if function is not None else None
    )
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(raw_arguments) is not str
    ):
        return None
    try:
        arguments = _decode_arguments(raw_arguments)
    except LiveModelRoleError:
        return None
    arguments.pop("task_state_update", None)
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _canonical_json(arguments),
                },
            }
        ],
    }
    return assistant, call_id, _runtime_name_from_model_call(name, raw_arguments)


def _utility_history_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Keep recent complete provider-native utility exchanges.

    Utility calls run inside one visual decision before its terminal GUI call.
    Their exact arguments and results can remain necessary on the next screen
    (for example, a saved screenshot path after launching a chat app).  Keep
    the provider's assistant/tool structure, but strip private reasoning and
    task-state patches just like the terminal GUI history.  Whole batches are
    atomic so no assistant tool call is retained without its result.
    """

    exchanges: list[tuple[dict[str, Any], ...]] = []
    index = 0
    while index < len(messages):
        raw_assistant = messages[index]
        index += 1
        if raw_assistant.get("role") != "assistant":
            continue
        raw_calls = raw_assistant.get("tool_calls")
        if (
            isinstance(raw_calls, (str, bytes))
            or not isinstance(raw_calls, Sequence)
            or not raw_calls
        ):
            continue
        calls: list[dict[str, Any]] = []
        call_ids: list[str] = []
        valid = True
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                valid = False
                break
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            name = (
                function.get("name")
                if isinstance(function, Mapping)
                else None
            )
            raw_arguments = (
                function.get("arguments")
                if isinstance(function, Mapping)
                else None
            )
            if (
                type(call_id) is not str
                or not call_id
                or type(name) is not str
                or name not in _VISUAL_UTILITY_TOOLS
                or type(raw_arguments) is not str
            ):
                valid = False
                break
            try:
                arguments = _decode_arguments(raw_arguments)
            except LiveModelRoleError:
                valid = False
                break
            arguments.pop("task_state_update", None)
            call_ids.append(call_id)
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _canonical_json(arguments),
                },
            })
        if not valid or len(call_ids) != len(set(call_ids)):
            continue

        results: list[dict[str, Any]] = []
        while index < len(messages) and messages[index].get("role") == "tool":
            results.append(deepcopy(dict(messages[index])))
            index += 1
        if [result.get("tool_call_id") for result in results] != call_ids:
            continue
        exchanges.append((
            {
                "role": "assistant",
                "content": "",
                "tool_calls": calls,
            },
            *results,
        ))

    retained = exchanges[-_VISUAL_UTILITY_HISTORY_DEPTH:]
    return tuple(
        message
        for exchange in retained
        for message in exchange
    )


def _tool_format_repair(
    response: Any,
    format_error: str,
) -> tuple[dict[str, Any], str] | None:
    """Extract one failed tool call so the same model can repair it.

    The structured format error is returned to the same model before any
    physical input is executed.  Repaired proposals are parsed again and may
    receive further exact feedback while the shared model-call budget remains;
    the runtime never replays after input.
    """
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = member(function, "arguments") if function is not None else None
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(arguments) is not str
    ):
        return None
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        ],
    }
    return assistant, call_id


def _connector_tool_definitions(gateway: Any) -> list[dict[str, Any]]:
    if gateway is None or getattr(gateway, "available", False) is not True:
        return []
    return _with_task_state([
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_SEARCH_TOOL,
                "description": "Find a connected-service tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "connector": {"type": "string"},
                    },
                    "required": ["query", "connector"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_CALL_TOOL,
                "description": "Call a discovered tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "minLength": 1},
                        "arguments_json": {"type": "string", "minLength": 2},
                    },
                    "required": ["ref", "arguments_json"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ])


def _status_tool_definitions() -> list[dict[str, Any]]:
    """Read-only Actor-owned status tools resolved without a desktop lease."""

    return _with_task_state([
        {
            "type": "function",
            "function": {
                "name": _STATUS_AGENT_TOOL,
                "description": "Read agent status.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _STATUS_MEMORY_TOOL,
                "description": "Read memory status.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _STATUS_CAPABILITIES_TOOL,
                "description": "Read current capabilities.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ])


def _shell_tool_definitions(runner: Any) -> list[dict[str, Any]]:
    """Expose the generic local primitives implemented by the active runner."""

    definitions = [
        {
            "type": "function",
            "function": {
                "name": _SHELL_TOOL,
                "description": (
                    "Run PowerShell or cmd for code, files, processes, and "
                    "command output. Every call starts a fresh shell process, "
                    "so directory changes never persist between calls; pass "
                    "cwd for project-local commands. ok and exit_code describe "
                    "only the child process; they never provide a physical "
                    "mouse or keyboard receipt. Use the desktop input tools "
                    "for real input."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _SHELL_MAX_COMMAND_CHARS,
                            "description": "Command line.",
                        },
                        "shell": {
                            "type": "string",
                            "enum": ["powershell", "cmd"],
                        },
                        "timeout_s": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "cwd": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Working directory for this one command.",
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]
    if callable(getattr(runner, "run_detached", None)):
        definitions.append({
            "type": "function",
            "function": {
                "name": _DETACHED_SHELL_TOOL,
                "description": (
                    "Start a PowerShell script as an independent background "
                    "process for GUI apps, servers, or other long-running work. "
                    "Returns immediately with a launch receipt and pollable "
                    "log/status paths; it never claims the script completed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _SHELL_MAX_COMMAND_CHARS,
                            "description": "Literal PowerShell script body.",
                        },
                        "name": {
                            "type": "string",
                            "default": "task",
                            "description": "Short process label.",
                        },
                    },
                    "required": ["script"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        })
    if callable(getattr(runner, "write_file", None)):
        definitions.append({
            "type": "function",
            "function": {
                "name": _WRITE_FILE_TOOL,
                "description": (
                    "Write literal UTF-8 content to a file without shell escaping."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Absolute or current-directory path.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete literal file content.",
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        })
    return _with_task_state(definitions)


def _window_text_tool_definitions() -> list[dict[str, Any]]:
    """One generic read-only window-text tool for the visual line."""

    return [
        {
            "type": "function",
            "function": {
                "name": _WINDOW_TEXT_TOOL,
                "description": "Read current window text.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _capture_screen_tool_definitions() -> list[dict[str, Any]]:
    """One generic screen/window capture tool that saves a PNG/JPEG file.

    The model needs real image files for tasks like "send a screenshot to my
    phone".  The system PrintScreen / Snipping Tool flow is a modal overlay
    the model cannot operate reliably, so the runtime exposes its own capture
    primitive instead of forcing the model to script one.
    """

    return [
        {
            "type": "function",
            "function": {
                "name": _CAPTURE_SCREEN_TOOL,
                "description": "Save a screen or window image.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 512,
                            "description": "Absolute .png/.jpg path.",
                        },
                        "window": {
                            "type": "boolean",
                            "default": False,
                            "description": "Current window only.",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _list_windows_tool_definitions() -> list[dict[str, Any]]:
    """One generic read-only window inventory tool."""

    return [
        {
            "type": "function",
            "function": {
                "name": _LIST_WINDOWS_TOOL,
                "description": "List visible windows.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _paste_image_tool_definitions() -> list[dict[str, Any]]:
    """One generic image-paste tool: file -> clipboard -> Ctrl+V."""

    return [
        {
            "type": "function",
            "function": {
                "name": _PASTE_IMAGE_TOOL,
                "description": "Paste an image file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 512,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _status_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only status tool call."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name not in _STATUS_TOOLS:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor status tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor status tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    # Status tools declare no parameters; surplus fields (for example the
    # optional task_state_update scratchpad) are tolerated like owned tools.
    for field in tuple(arguments):
        arguments.pop(field, None)
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor status content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_status(
    provider: Any,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one Actor-owned status query from the injected Runtime facts."""

    if provider is None or not callable(getattr(provider, "resolve", None)):
        return _actionable_tool_result(
            name,
            {"ok": False, "error": "status provider is unavailable"},
        )
    try:
        result = provider.resolve(name)
    except Exception as exc:
        return _actionable_tool_error(name, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            name,
            TypeError("status provider returned a non-object result"),
        )
    return _actionable_tool_result(name, result)


def _window_text_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only window-text tool call."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _WINDOW_TEXT_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor window-text tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor window-text tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    if type(arguments) is not dict:
        raise LiveModelRoleError("Actor window-text arguments must be a JSON object")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor window-text content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_window_text(
    reader: Any,
    turn: ActorTurnInput,
) -> dict[str, Any]:
    """Read the bound target window's visible text through the injected reader."""
    if reader is None or not callable(reader):
        return _actionable_tool_result(
            _WINDOW_TEXT_TOOL,
            {"ok": False, "error": "window-text reader is unavailable"},
        )
    try:
        result = reader(turn)
    except Exception as exc:
        return _actionable_tool_error(_WINDOW_TEXT_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _WINDOW_TEXT_TOOL,
            TypeError("window-text reader returned a non-object result"),
        )
    return _actionable_tool_result(_WINDOW_TEXT_TOOL, result)


def _owned_tool_exchange(
    response: Any,
    name: str,
    *,
    expected_arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only/utility tool call (generic pattern).

    ``expected_arguments`` maps each field name to the required type
    (``str`` or ``bool``).  Fields declared optional in the tool schema (with
    a JSON ``default``) may be omitted by the model; the default is filled in
    before the type check so the tool schema and the runtime validator agree.
    The model may also attach the optional ``task_state_update`` scratchpad
    field to any call; it is popped before validation so utility tools do not
    need their own scratchpad schema.
    """
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    call_name = member(function, "name") if function is not None else None
    if call_name != name:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError(f"Actor {name} tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError(f"Actor {name} tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    arguments.pop("task_state_update", None)
    for field in set(arguments) - set(expected_arguments):
        arguments.pop(field, None)
    for field, expected_type in expected_arguments.items():
        if field not in arguments:
            # Optional schema field with a default: fill the default so the
            # model is never punished for omitting an optional argument.
            default = _OPTIONAL_TOOL_DEFAULTS.get(name, {}).get(field)
            if default is not None:
                arguments[field] = default
                continue
            raise LiveModelRoleError(
                f"{name} is missing required field {field!r}"
            )
        if type(arguments[field]) is not expected_type:
            raise LiveModelRoleError(
                f"{name}.{field} must be {expected_type.__name__}"
            )
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError(f"Actor {name} content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_capture_screen(
    capture_runner: Any,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Save a screen/window capture through the injected runner."""
    if capture_runner is None or not callable(capture_runner):
        return _actionable_tool_result(
            _CAPTURE_SCREEN_TOOL,
            {"ok": False, "error": "capture runner is unavailable"},
        )
    try:
        result = capture_runner(
            path=str(arguments.get("path") or ""),
            window=bool(arguments.get("window")),
        )
    except Exception as exc:
        return _actionable_tool_error(_CAPTURE_SCREEN_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _CAPTURE_SCREEN_TOOL,
            TypeError("capture runner returned a non-object result"),
        )
    output = dict(result)
    for key in ("error", "detail"):
        value = output.get(key)
        if isinstance(value, str) and len(value) > _CAPTURE_OUTPUT_MAX_CHARS:
            output[key] = value[:_CAPTURE_OUTPUT_MAX_CHARS]
    return _actionable_tool_result(_CAPTURE_SCREEN_TOOL, output)


def _call_list_windows(
    windows_reader: Any,
) -> dict[str, Any]:
    """Return the visible window inventory through the injected reader."""
    if windows_reader is None or not callable(windows_reader):
        return _actionable_tool_result(
            _LIST_WINDOWS_TOOL,
            {"ok": False, "error": "window inventory is unavailable"},
        )
    try:
        result = windows_reader()
    except Exception as exc:
        return _actionable_tool_error(_LIST_WINDOWS_TOOL, exc)
    if isinstance(result, Mapping):
        return _actionable_tool_result(_LIST_WINDOWS_TOOL, result)
    if not isinstance(result, list):
        return _actionable_tool_error(
            _LIST_WINDOWS_TOOL,
            TypeError("window inventory returned a non-list result"),
        )
    bounded: list[dict[str, Any]] = []
    for item in result[:24]:
        if not isinstance(item, Mapping):
            continue
        bounded.append({
            key: item.get(key)
            for key in (
                "hwnd",
                "title",
                "class_name",
                "pid",
                "process_name",
                "rect",
                "foreground",
                "visible",
                "minimized",
            )
            if key in item
        })
    return {"ok": True, "windows": bounded, "count": len(bounded)}


def _call_paste_image(
    paste_runner: Any,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Paste an image file into the current target through the injected runner."""
    if paste_runner is None or not callable(paste_runner):
        return _actionable_tool_result(
            _PASTE_IMAGE_TOOL,
            {"ok": False, "error": "image-paste runner is unavailable"},
        )
    try:
        result = paste_runner(path=str(arguments.get("path") or ""))
    except Exception as exc:
        return _actionable_tool_error(_PASTE_IMAGE_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _PASTE_IMAGE_TOOL,
            TypeError("image-paste runner returned a non-object result"),
        )
    return _actionable_tool_result(_PASTE_IMAGE_TOOL, result)


def _connector_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name not in _CONNECTOR_TOOLS:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor connector tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor connector tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    expected = (
        {"query", "connector"}
        if name == _CONNECTOR_SEARCH_TOOL
        else {"ref", "arguments_json"}
    )
    for field in set(arguments) - expected:
        arguments.pop(field, None)
    if not expected.issubset(arguments) or not all(
        type(arguments[key]) is str for key in expected
    ):
        raise LiveModelRoleError(
            f"{name} requires {sorted(expected)!r} text fields"
        )
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor connector content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_connector(
    gateway: Any,
    name: str,
    arguments: Mapping[str, Any],
    *,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    if gateway is None or getattr(gateway, "available", False) is not True:
        return _actionable_tool_result(
            name,
            {"ok": False, "error": "connector gateway is unavailable"},
        )
    try:
        if name == _CONNECTOR_SEARCH_TOOL:
            result = gateway.search(
                query=arguments["query"],
                connector=arguments["connector"],
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        elif name == _CONNECTOR_CALL_TOOL:
            result = gateway.call(
                ref=arguments["ref"],
                arguments_json=arguments["arguments_json"],
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        else:  # pragma: no cover - exact connector tool set above
            return _actionable_tool_result(
                name,
                {"ok": False, "error": "unsupported connector tool"},
            )
    except Exception as exc:
        return _actionable_tool_error(name, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            name,
            TypeError("connector gateway returned a non-object result"),
        )
    return _actionable_tool_result(name, result)


def _shell_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned ``run_shell`` tool call (connector pattern)."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _SHELL_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor shell tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor shell tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    command = arguments.get("command")
    if type(command) is not str or not command.strip():
        raise LiveModelRoleError("run_shell requires a non-empty command")
    if len(command) > _SHELL_MAX_COMMAND_CHARS:
        raise LiveModelRoleError("run_shell command exceeds the bounded budget")
    shell_name = arguments.get("shell")
    if shell_name not in (None, "powershell", "cmd"):
        raise LiveModelRoleError("run_shell shell must be powershell or cmd")
    timeout_s = arguments.get("timeout_s")
    if timeout_s is not None and (
        type(timeout_s) is not int or timeout_s < 1
    ):
        raise LiveModelRoleError("run_shell timeout_s must be a positive integer")
    cwd = arguments.get("cwd")
    if cwd is not None and (type(cwd) is not str or not cwd.strip()):
        raise LiveModelRoleError("run_shell cwd must be a non-empty string")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor shell content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_shell(
    runner: Any,
    arguments: Mapping[str, Any],
    *,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one shell command through the injected runner."""
    if runner is None or not callable(getattr(runner, "run", None)):
        return _actionable_tool_result(
            _SHELL_TOOL,
            {"ok": False, "error": "shell runner is unavailable"},
        )
    requested_cwd = arguments.get("cwd")
    cwd = (
        str(requested_cwd).strip()
        if type(requested_cwd) is str and requested_cwd.strip()
        else None
    )
    try:
        result = runner.run(
            command=str(arguments.get("command") or ""),
            shell=str(arguments.get("shell") or "powershell"),
            timeout_s=float(arguments.get("timeout_s") or 30),
            cwd=cwd,
            stop_event=stop_event,
        )
    except Exception as exc:
        return _actionable_tool_error(_SHELL_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _SHELL_TOOL,
            TypeError("shell runner returned a non-object result"),
        )
    output = dict(result)
    output["cwd"] = (
        str(Path(cwd).expanduser().resolve())
        if cwd is not None
        else str(Path.cwd())
    )
    # A child process exiting cleanly says nothing about any synthetic desktop
    # input it may have attempted.  Keep that mechanical distinction explicit
    # instead of letting exit code 0 impersonate a RawDevice receipt.
    output["completion_scope"] = "child_process_exit_only"
    output["physical_input_receipt"] = False
    stdout = output.pop("stdout", "")
    stderr = output.pop("stderr", "")
    if not isinstance(output.get("output"), str) or not output.get("output"):
        parts = [
            value
            for value in (stdout, stderr)
            if isinstance(value, str) and value
        ]
        output["output"] = "\n".join(parts) or "(no output)"
    # stdout/stderr duplicate the combined output from the native runner.
    # The runner owns the one generic output boundary and supplies a full path
    # when it truncates; do not discard another slice here.
    return _actionable_tool_result(_SHELL_TOOL, output)


def _local_utility_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Parse one generic detached-process or literal-file call."""

    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    function = member(calls[0], "function")
    name = member(function, "name") if function is not None else None
    if name == _DETACHED_SHELL_TOOL:
        return _owned_tool_exchange(
            response,
            _DETACHED_SHELL_TOOL,
            expected_arguments={"script": str, "name": str},
        )
    if name == _WRITE_FILE_TOOL:
        return _owned_tool_exchange(
            response,
            _WRITE_FILE_TOOL,
            expected_arguments={"path": str, "content": str},
        )
    return None


def _call_local_utility(
    runner: Any,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one local primitive through the same injected shell backend."""

    method_name = (
        "run_detached" if name == _DETACHED_SHELL_TOOL else "write_file"
    )
    method = getattr(runner, method_name, None) if runner is not None else None
    if not callable(method):
        return _actionable_tool_result(
            name,
            {"ok": False, "error": f"{name} runner is unavailable"},
        )
    try:
        if name == _DETACHED_SHELL_TOOL:
            result = method(
                script=str(arguments.get("script") or ""),
                name=str(arguments.get("name") or "task"),
            )
        elif name == _WRITE_FILE_TOOL:
            result = method(
                path=str(arguments.get("path") or ""),
                content=str(arguments.get("content") or ""),
            )
        else:  # pragma: no cover - exact local utility set above
            raise LiveModelRoleError(f"unsupported local utility: {name}")
    except Exception as exc:
        return _actionable_tool_error(name, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            name,
            TypeError(f"{name} runner returned a non-object result"),
        )
    return _actionable_tool_result(name, result)


def _initial_calls_from_response(
    response: Any,
) -> list[dict[str, Any]]:
    """Return the raw tool-call list of the initial text turn."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if calls is None:
        return []
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise LiveModelRoleError("Actor initial tool calls must be a sequence")
    return list(calls)


def _initial_call_envelope(
    call: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Validate transcript fields before any call in the batch can execute."""
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor initial tool call must be a function")
    call_id = member(call, "id")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor initial tool call requires an id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if type(name) is not str or name not in {
        "observe_desktop",
        *_STATUS_TOOLS,
        *_CONNECTOR_TOOLS,
    }:
        raise LiveModelRoleError(
            f"Actor initial turn returned an unapproved tool: {name!r}"
        )
    arguments = _decode_arguments(member(function, "arguments"))
    return call_id, name, arguments


def _execute_initial_call(
    call: Mapping[str, Any],
    *,
    status_provider: Any,
    connector_gateway: Any,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one non-terminal initial tool call and return its result."""
    _call_id, name, arguments = _initial_call_envelope(call)
    if name in _STATUS_TOOLS:
        if status_provider is None or not callable(
            getattr(status_provider, "resolve", None)
        ):
            return _actionable_tool_result(
                str(name),
                {"ok": False, "error": "status provider is unavailable"},
            )
        # Status tools declare no parameters; surplus fields are tolerated.
        for field in tuple(arguments):
            arguments.pop(field, None)
        try:
            result = status_provider.resolve(name)
        except Exception as exc:
            return _actionable_tool_error(str(name), exc)
        if not isinstance(result, Mapping):
            return _actionable_tool_error(
                str(name),
                TypeError("status provider returned a non-object result"),
            )
        return _actionable_tool_result(str(name), result)
    if name is not None and name in _CONNECTOR_TOOLS:
        try:
            return _call_connector(
                connector_gateway,
                name,
                arguments,
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        except Exception as exc:
            return _actionable_tool_error(str(name), exc)
    raise LiveModelRoleError(
        f"Actor initial turn returned an unapproved tool: {name!r}"
    )


def _visual_batch_call(
    call: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Reuse the existing single-tool parsers for one non-GUI batch item."""

    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [deepcopy(call)],
            },
        }],
    }
    exchange = None
    if name in _STATUS_TOOLS:
        exchange = _status_exchange(response)
    elif name == _SHELL_TOOL:
        exchange = _shell_exchange(response)
    elif name in _LOCAL_UTILITY_TOOLS:
        exchange = _local_utility_exchange(response)
    elif name == _WINDOW_TEXT_TOOL:
        exchange = _window_text_exchange(response)
    elif name == _CAPTURE_SCREEN_TOOL:
        exchange = _owned_tool_exchange(
            response,
            _CAPTURE_SCREEN_TOOL,
            expected_arguments={"path": str, "window": bool},
        )
    elif name == _LIST_WINDOWS_TOOL:
        exchange = _owned_tool_exchange(
            response,
            _LIST_WINDOWS_TOOL,
            expected_arguments={},
        )
    elif name in _CONNECTOR_TOOLS:
        exchange = _connector_exchange(response)
    if exchange is None:
        raise LiveModelRoleError(
            "Visual tool batches may contain only independent non-GUI tools"
        )
    _assistant, call_id, parsed_name, arguments = exchange
    return call_id, parsed_name, arguments


def _execute_visual_batch_call(
    name: str,
    arguments: Mapping[str, Any],
    *,
    status_provider: Any,
    shell_runner: Any,
    connector_gateway: Any,
    window_text_reader: Any,
    capture_runner: Any,
    windows_reader: Any,
    turn: ActorTurnInput,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one model-selected non-GUI batch item."""
    if name in _STATUS_TOOLS:
        return _call_status(status_provider, name, arguments)
    if name == _SHELL_TOOL:
        return _call_shell(
            shell_runner,
            arguments,
            stop_event=stop_event,
        )
    if name in _LOCAL_UTILITY_TOOLS:
        return _call_local_utility(shell_runner, name, arguments)
    if name == _WINDOW_TEXT_TOOL:
        return _call_window_text(window_text_reader, turn)
    if name == _CAPTURE_SCREEN_TOOL:
        return _call_capture_screen(capture_runner, arguments)
    if name == _LIST_WINDOWS_TOOL:
        return _call_list_windows(windows_reader)
    return _call_connector(
        connector_gateway,
        name,
        arguments,
        timeout_s=timeout_s,
        stop_event=stop_event,
    )


def _initial_admission_from_response(
    response: Any,
    messages: Sequence[Mapping[str, Any]],
) -> tuple[VisualActorTurnAdmission, _InitialExchange | None]:
    message = response_message(response)
    content = member(message, "content")
    calls = _initial_calls_from_response(response)
    if not calls:
        if type(content) is not str or not content.strip():
            raise LiveModelRoleError(
                "Actor initial turn returned neither a reply nor a desktop observation"
            )
        return VisualActorTurnAdmission(
            kind="reply",
            reply=content.strip(),
        ), None
    if content is not None and type(content) is not str:
        raise LiveModelRoleError(
            "Actor initial content must be text or null"
        )
    observe_calls = [
        call for call in calls
        if member(member(call, "function"), "name") == "observe_desktop"
    ]
    if len(observe_calls) != 1:
        raise LiveModelRoleError(
            "Actor initial turn must request at most one desktop observation"
        )
    call = observe_calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    arguments = member(function, "arguments") if function is not None else None
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor initial tool call requires an id")
    if _decode_arguments(arguments):
        raise LiveModelRoleError("observe_desktop does not accept arguments")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "observe_desktop",
                    "arguments": arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return (
        VisualActorTurnAdmission(
            kind="desktop",
            narration=(content or "").strip(),
        ),
        _InitialExchange(
            messages=tuple(deepcopy([*messages, assistant])),
            tool_call_id=call_id,
        ),
    )


class FlowFragmentProposer:
    """One inert editor proposal with no desktop or conversation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._called = False
        self._state_lock = threading.Lock()

    def propose_flow_fragment(
        self,
        request: Mapping[str, Any],
        *,
        catalog_ports: Mapping[str, Sequence[str]],
        catalog_params: Mapping[str, frozenset[str]],
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise LiveModelRoleError("flow fragment request must be an object")
        canonical_request = _canonical_json(request)
        if len(canonical_request) > _FLOW_FRAGMENT_REQUEST_CHARS:
            raise LiveModelRoleError("flow fragment request exceeds the bounded context")
        ports = {
            key: tuple(value)
            for key, value in catalog_ports.items()
            if type(key) is str and key
        }
        if len(ports) != len(catalog_ports) or any(
            not value or any(type(port) is not str or not port for port in value)
            for value in ports.values()
        ):
            raise LiveModelRoleError("flow fragment output-port catalog is invalid")
        params = {
            key: frozenset(value)
            for key, value in catalog_params.items()
            if type(key) is str and key and isinstance(value, frozenset)
        }
        if set(params) != set(ports) or any(
            any(type(name) is not str or not name for name in names)
            for names in params.values()
        ):
            raise LiveModelRoleError("flow fragment param catalog is invalid")
        with self._state_lock:
            if self._called:
                raise LiveModelRoleError("flow fragment proposer is call-once")
            self._called = True
        outcome = invoke_chat_completion(
            self._client,
            decision_id=_initial_decision_id(canonical_request, ()),
            model_role=VISUAL_ACTOR_MODEL_ROLE,
            purpose="flow_fragment_proposal",
            provider_request={
                "model": self._model,
                "messages": [{"role": "user", "content": canonical_request}],
                "tools": [_flow_fragment_tool_definition(tuple(ports))],
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_completion_tokens": self._max_output_tokens,
            },
            policy=ModelRetryPolicy(
                max_attempts=self._max_attempts,
                total_timeout_s=self._total_timeout_s,
            ),
            stop_event=stop_event,
            on_attempt=self._on_attempt,
            runtime=self._runtime,
        )
        if self._on_response is not None:
            try:
                self._on_response(outcome.response)
            except Exception:
                pass
        return _flow_fragment_from_response(outcome.response, ports, params)


class LiveVisualActor:
    """Visual decisions with one bounded native exchange and no mutation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 4096,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        on_activity: Callable[[Mapping[str, Any]], None] | None = None,
        connector_gateway: Any | None = None,
        status_provider: Any | None = None,
        shell_runner: Any | None = None,
        window_text_reader: Any | None = None,
        capture_runner: Any | None = None,
        windows_reader: Any | None = None,
        paste_runner: Any | None = None,
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        if on_activity is not None and not callable(on_activity):
            raise LiveModelRoleError("on_activity must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._on_activity = on_activity
        self._connector_gateway = (
            connector_gateway
            if getattr(connector_gateway, "available", False) is True
            else None
        )
        self._status_provider = (
            status_provider
            if callable(getattr(status_provider, "resolve", None))
            else None
        )
        self._shell_runner = (
            shell_runner
            if callable(getattr(shell_runner, "run", None))
            else None
        )
        self._window_text_reader = (
            window_text_reader if callable(window_text_reader) else None
        )
        self._capture_runner = (
            capture_runner if callable(capture_runner) else None
        )
        self._windows_reader = (
            windows_reader if callable(windows_reader) else None
        )
        self._paste_runner = (
            paste_runner if callable(paste_runner) else None
        )
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._visual_history: list[_VisualExchange] = []
        self._initial_exchange: _InitialExchange | None = None
        self._initial_turn_started = False
        self._initial_turn_terminal = False
        self._pending_task_state: ActorTaskStateUpdate | None = None
        self._state_lock = threading.Lock()

    @property
    def standalone_model_call_allowance(self) -> int:
        """Finite fallback for direct role users outside the live Runtime."""

        local_allowance = (
            _STANDALONE_STATUS_CALL_ALLOWANCE
            if self._status_provider is not None
            else 0
        ) + (
            _STANDALONE_CONNECTOR_CALL_ALLOWANCE
            if self._connector_gateway is not None
            else 0
        ) + (
            _STANDALONE_SHELL_CALL_ALLOWANCE
            if self._shell_runner is not None
            else 0
        ) + (
            _STANDALONE_WINDOW_TEXT_ALLOWANCE
            if self._window_text_reader is not None
            else 0
        ) + (
            _STANDALONE_CAPTURE_ALLOWANCE
            if self._capture_runner is not None
            else 0
        ) + (
            _STANDALONE_LIST_WINDOWS_ALLOWANCE
            if self._windows_reader is not None
            else 0
        ) + (
            _STANDALONE_PASTE_IMAGE_ALLOWANCE
            if self._paste_runner is not None
            else 0
        )
        return 3 + local_allowance

    def _record_response(self, response: Any) -> None:
        if self._on_response is not None:
            try:
                self._on_response(response)
            except Exception:
                # Usage telemetry is a sidecar and never changes task semantics.
                pass
        try:
            narration = narration_content(response_message(response))
        except Exception:
            return
        if narration:
            self._emit_activity(kind="message", message=narration)

    def _emit_activity(self, **activity: Any) -> None:
        if self._on_activity is None:
            return
        try:
            self._on_activity(activity)
        except Exception:
            # User-facing progress is a projection, never an execution owner.
            pass

    def _record_tool_start(self, call: Any) -> None:
        function = member(call, "function")
        name = member(function, "name") if function is not None else None
        if type(name) is not str or not name:
            return
        raw_arguments = member(function, "arguments")
        try:
            arguments = _decode_arguments(raw_arguments)
        except LiveModelRoleError:
            arguments = {}
        state_update = arguments.pop("task_state_update", None)
        try:
            patch = ActorTaskStateUpdate.from_dict(state_update)
        except ActorTaskStateError:
            patch = ActorTaskStateUpdate()
        if patch.changes_state:
            self._pending_task_state = (
                patch
                if self._pending_task_state is None
                else self._pending_task_state.merged_with(patch)
            )
            visible_state = (
                patch.scratchpad
                if patch.scratchpad is not None
                else _canonical_json(
                    {"remaining_items": list(patch.remaining_items or ())}
                )
            )
            self._emit_activity(
                kind="task_state",
                message=visible_state[:8192],
                task_state=visible_state[:8192],
            )
        local_intent = arguments.pop("local_intent", None)
        runtime_name = _runtime_name_from_model_call(name, raw_arguments)
        command = arguments.get("command") if name == _SHELL_TOOL else None
        if type(command) is str and command.strip():
            message = command.strip()
        elif type(local_intent) is str and local_intent.strip():
            message = local_intent.strip()
        elif arguments:
            message = _canonical_json(arguments)
        else:
            message = runtime_name
        activity: dict[str, Any] = {
            "kind": "tool_start",
            "tool": runtime_name,
            "message": message[:4096],
        }
        if type(command) is str and command.strip():
            activity["command"] = command.strip()[:16_384]
        self._emit_activity(**activity)

    def _record_tool_result(
        self,
        name: str,
        result: Mapping[str, Any],
    ) -> None:
        failed = bool(
            result.get("ok") is False
            or result.get("error")
            or result.get("timeout") is True
            or result.get("stopped") is True
        )
        error = result.get("error")
        output = (
            result.get("output")
            or result.get("stdout")
            or result.get("stderr")
        )
        details: list[str] = []
        for candidate in (error, output):
            if type(candidate) is not str or not candidate.strip():
                continue
            candidate = candidate.strip()
            if candidate not in details:
                details.append(candidate)
        detail = "\n".join(details)
        if not detail:
            exit_code = result.get("exit_code")
            detail = (
                f"exit code {exit_code}"
                if exit_code is not None
                else ("failed" if failed else "completed")
            )
        next_step = result.get("next_step")
        if failed and type(next_step) is str and next_step.strip():
            detail = f"{detail}\nNext: {next_step.strip()}"
        self._emit_activity(
            kind="tool_result",
            tool=_RUNTIME_TOOL_NAMES.get(name, name),
            message=detail.strip()[:4096],
            ok=not failed,
        )

    def _model_call_allowance(self, model_call_limit: int | None) -> int:
        # The production Runtime always supplies the task's remaining model
        # budget. Direct role users retain a finite fallback for malformed or
        # endlessly repeated provider output.
        if model_call_limit is None:
            model_call_limit = self.standalone_model_call_allowance
        if type(model_call_limit) is not int or model_call_limit < 1:
            raise LiveModelRoleError("Actor model-call allowance is invalid")
        return model_call_limit

    def begin_turn(
        self,
        goal: str,
        *,
        history: Sequence[Mapping[str, Any]] = (),
        stop_event: threading.Event | None = None,
        _model_call_limit: int | None = None,
    ) -> VisualActorTurnAdmission:
        """Reply directly or request the first desktop observation."""

        if type(goal) is not str or not goal.strip():
            raise LiveModelRoleError("goal must not be empty")
        goal = goal.strip()
        with self._state_lock:
            if self._initial_turn_started:
                raise LiveModelRoleError("Actor initial turn is call-once")
            self._initial_turn_started = True
        system_message = {
            "role": "system",
            "content": INITIAL_ACTOR_SYSTEM_CONTRACT,
        }
        messages = _thread_messages(history, goal)
        terminal_tools = [
            {
                "type": "function",
                "function": {
                    "name": "observe_desktop",
                    "description": "Observe the desktop.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_state_update": {
                                **deepcopy(TASK_STATE_UPDATE_SCHEMA),
                                "default": {"scratchpad": None},
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        ]
        status_tools = (
            _status_tool_definitions()
            if self._status_provider is not None
            else []
        )
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        provider_request = {
            "model": self._model,
            "messages": [system_message, *messages],
            "tools": [
                *terminal_tools,
                *status_tools,
                *connector_tools,
            ],
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        admission: VisualActorTurnAdmission | None = None
        exchange: _InitialExchange | None = None
        model_call_allowance = self._model_call_allowance(_model_call_limit)
        seen_tool_results: dict[tuple[str, str], tuple[str, int]] = {}
        for model_call_index in range(model_call_allowance):
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_initial_decision_id(goal, history),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="reply_observe_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=self._max_attempts,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            model_calls_remaining = model_call_allowance - model_call_index - 1
            if _finish_reason(outcome.response) == "length":
                messages.extend(_truncation_messages(outcome.response))
                provider_request["messages"] = [system_message, *messages]
                continue
            try:
                calls = _initial_calls_from_response(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            # The model may emit several tool calls in one response (status,
            # shell, connector, then observe_desktop).  Execute every
            # non-terminal call and keep observe_desktop as the terminal
            # admission.  A bare text reply ends the initial turn.
            if not calls:
                try:
                    admission, exchange = _initial_admission_from_response(
                        outcome.response,
                        messages,
                    )
                    break
                except LiveModelRoleError as exc:
                    if model_calls_remaining <= 0:
                        raise InvalidActorProposal(str(exc)) from exc
                    repair = _tool_format_repair(outcome.response, str(exc))
                    if repair is None:
                        raise InvalidActorProposal(str(exc)) from exc
                    assistant, call_id = repair
                    messages.extend(
                        (
                            assistant,
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": _canonical_json(
                                    {
                                        "tool_format_error": str(exc),
                                        "model_calls_remaining": (
                                            model_calls_remaining
                                        ),
                                        "action": (
                                            "return exactly one valid tool call "
                                            "with the required fields"
                                        ),
                                    }
                                ),
                            },
                        )
                    )
                    provider_request["messages"] = [system_message, *messages]
                    continue
            preflight_error = ""
            try:
                envelopes = [_initial_call_envelope(call) for call in calls]
                call_ids = [
                    call_id for call_id, _name, _arguments in envelopes
                ]
                if len(set(call_ids)) != len(call_ids):
                    raise LiveModelRoleError(
                        "Actor initial tool-call ids must be unique"
                    )
                observe_envelopes = [
                    envelope
                    for envelope in envelopes
                    if envelope[1] == "observe_desktop"
                ]
                if len(observe_envelopes) > 1:
                    raise LiveModelRoleError(
                        "Actor initial turn must request at most one "
                        "desktop observation"
                    )
                if observe_envelopes:
                    observe_arguments = dict(observe_envelopes[0][2])
                    try:
                        ActorTaskStateUpdate.from_dict(
                            observe_arguments.pop(
                                "task_state_update",
                                None,
                            )
                        )
                    except ActorTaskStateError as exc:
                        raise LiveModelRoleError(str(exc)) from exc
                    if observe_arguments:
                        raise LiveModelRoleError(
                            "observe_desktop does not accept arguments"
                        )
            except LiveModelRoleError as exc:
                preflight_error = str(exc)
            if preflight_error:
                if model_calls_remaining <= 0:
                    raise LiveModelRoleError(preflight_error)
                repair = _tool_format_repair(
                    outcome.response,
                    preflight_error,
                )
                feedback = {
                    "tool_format_error": preflight_error,
                    "tools_executed": 0,
                    "model_calls_remaining": model_calls_remaining,
                    "action": (
                        "return a valid tool batch; independent calls may "
                        "remain together"
                    ),
                }
                if repair is not None:
                    repaired_assistant, repaired_call_id = repair
                    messages.extend(
                        (
                            repaired_assistant,
                            {
                                "role": "tool",
                                "tool_call_id": repaired_call_id,
                                "content": _canonical_json(feedback),
                            },
                        )
                    )
                else:
                    # A malformed multi-call batch cannot be echoed as a
                    # provider tool exchange. No call ran, so ask the same
                    # model to reissue the batch without removing its tools.
                    messages.extend(
                        (
                            {
                                "role": "assistant",
                                "content": narration_content(
                                    response_message(outcome.response)
                                ),
                            },
                            {
                                "role": "user",
                                "content": _canonical_json(feedback),
                            },
                        )
                    )
                provider_request["messages"] = [system_message, *messages]
                continue
            terminal = None
            executed_any = False
            message = response_message(outcome.response)
            content = member(message, "content")
            reasoning = member(message, "reasoning_content")
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": member(call, "id"),
                        "type": "function",
                        "function": {
                            "name": member(member(call, "function"), "name"),
                            "arguments": member(
                                member(call, "function"),
                                "arguments",
                            ),
                        },
                    }
                    for call in calls
                ],
            }
            if type(reasoning) is str:
                assistant["reasoning_content"] = reasoning
            pending_tool_messages: list[Mapping[str, Any]] = []
            executable_calls: list[Any] = []
            for call in calls:
                call_function = member(call, "function")
                call_name = (
                    member(call_function, "name")
                    if call_function is not None
                    else None
                )
                if call_name == "observe_desktop":
                    terminal = call
                    continue
                self._record_tool_start(call)
                executable_calls.append(call)

            def execute(call: Mapping[str, Any]) -> dict[str, Any]:
                return _execute_initial_call(
                    call,
                    status_provider=self._status_provider,
                    connector_gateway=self._connector_gateway,
                    timeout_s=min(30.0, self._total_timeout_s),
                    stop_event=stop_event,
                )

            try:
                if len(executable_calls) > 1:
                    # Tool calls in one model response are one model-selected
                    # parallel batch. Dependencies remain separate responses;
                    # preserve call order only for the provider transcript.
                    with ThreadPoolExecutor(
                        thread_name_prefix="visual-actor-tool",
                    ) as executor:
                        results = list(executor.map(execute, executable_calls))
                else:
                    results = [execute(call) for call in executable_calls]
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc

            for call, result in zip(executable_calls, results, strict=True):
                call_function = member(call, "function")
                call_name = (
                    member(call_function, "name")
                    if call_function is not None
                    else None
                )
                self._record_tool_result(str(call_name or "tool"), result)
                executed_any = True
                call_id = member(call, "id")
                if type(call_id) is not str or not call_id:
                    raise InvalidActorProposal(
                        "Actor initial tool call requires an id"
                    )
                try:
                    _call_id, canonical_name, _arguments = (
                        _initial_call_envelope(call)
                    )
                except LiveModelRoleError:
                    canonical_name = str(call_name or "tool")
                provider_result = _project_tool_result(
                    seen_tool_results,
                    name=canonical_name,
                    call_id=call_id,
                    result=result,
                    model_calls_remaining=model_calls_remaining,
                )
                pending_tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(provider_result),
                    }
                )
            if terminal is not None:
                terminal_id = member(terminal, "id")
                # Keep the full assistant tool-call list and every executed
                # result in the exchange so the provider transcript stays
                # consistent; observe_desktop is the terminal admission.  The
                # visual turn appends the synthetic observation result after
                # these messages.
                exchange = _InitialExchange(
                    messages=tuple(
                        deepcopy(
                            [*messages, assistant, *pending_tool_messages]
                        )
                    ),
                    tool_call_id=terminal_id,
                )
                admission = VisualActorTurnAdmission(
                    kind="desktop",
                    narration=(content or "").strip(),
                )
                self._record_tool_start(terminal)
                break
            if not executed_any:
                raise InvalidActorProposal(
                    "Actor initial turn returned an unapproved tool"
                )
            messages.extend((assistant, *pending_tool_messages))
            provider_request["messages"] = [system_message, *messages]
        if admission is None:
            raise LiveModelRoleError(
                "Actor initial turn exhausted its model-call allowance"
            )
        self._initial_exchange = deepcopy(exchange)
        self._initial_turn_terminal = admission.kind == "reply"
        return admission

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
        _model_call_limit: int | None = None,
        rejection_feedback: str | None = None,
    ) -> ActorTurnDecision:
        if self._initial_turn_terminal:
            raise LiveModelRoleError(
                "Actor cannot observe after a terminal direct reply"
            )
        if type(turn) is not ActorTurnInput:
            raise LiveModelRoleError(
                "Visual Actor requires canonical ActorTurnInput"
            )
        if not (
            turn.frame.image_available
            and turn.frame.authoritative
            and turn.frame.fresh
        ):
            raise LiveModelRoleError(
                "Visual Actor requires the current authoritative fresh frame"
            )
        image_b64, submitted_size = require_jpeg(
            image_b64, image_media_type, submitted_size
        )
        terminal_tools = _tool_definitions(turn)
        status_tools = (
            _status_tool_definitions()
            if self._status_provider is not None
            else []
        )
        shell_tools = (
            _shell_tool_definitions(self._shell_runner)
            if self._shell_runner is not None
            else []
        )
        window_text_tools = (
            _window_text_tool_definitions()
            if self._window_text_reader is not None
            else []
        )
        capture_tools = (
            _capture_screen_tool_definitions()
            if self._capture_runner is not None
            else []
        )
        windows_tools = (
            _list_windows_tool_definitions()
            if self._windows_reader is not None
            else []
        )
        paste_tools = (
            _paste_image_tool_definitions()
            if self._paste_runner is not None
            else []
        )
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        context = turn.to_dict()
        context.pop("tools")
        previous_action = context.get("previous_action")
        if (
            isinstance(previous_action, dict)
            and previous_action.get("input_completed") is False
            and previous_action.get("error_kind")
        ):
            previous_action["next_step"] = (
                "The physical action did not run. Use the current image and "
                "error_kind to change arguments or choose another tool."
            )
        context["submitted_size"] = list(submitted_size)
        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _canonical_json(context),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            "data:image/jpeg;base64,"
                            f"{image_b64}"
                        )
                    },
                },
            ],
        }
        native_context: list[dict[str, Any]] = []
        initial_exchange = self._initial_exchange
        if initial_exchange is not None:
            native_context.extend(deepcopy(initial_exchange.messages))
            native_context.append(
                {
                    "role": "tool",
                    "tool_call_id": initial_exchange.tool_call_id,
                    "content": _canonical_json(
                        {"desktop_observation": "available"}
                    ),
                }
            )
        previous = self._visual_history[-1] if self._visual_history else None
        if (
            previous is not None
            and turn.previous_action is not None
            and turn.previous_action.action == previous.runtime_tool_name
        ):
            previous.result_content = {
                "previous_action": turn.previous_action.to_dict()
            }
        else:
            self._visual_history = []
        for exchange in self._visual_history:
            native_context.extend(deepcopy(exchange.messages))
            native_context.append(
                {
                    "role": "tool",
                    "tool_call_id": exchange.tool_call_id,
                    "content": _canonical_json(exchange.result_content),
                }
            )
        if rejection_feedback:
            native_context.append(
                {
                    "role": "user",
                    "content": (
                        "Runtime feedback from the previous attempt: "
                        f"{rejection_feedback}. Use the current screen and "
                        "return one valid tool call or an independent tool batch."
                    ),
                }
            )
        current_exchange = [user_message]
        provider_request = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": VISUAL_ACTOR_SYSTEM_CONTRACT,
                },
                *native_context,
                *current_exchange,
            ],
            "tools": [
                *terminal_tools,
                *status_tools,
                *shell_tools,
                *window_text_tools,
                *capture_tools,
                *windows_tools,
                *paste_tools,
                *connector_tools,
            ],
            # MiMo's documented Chat Completions surface supports only
            # ``auto``. One GUI/outcome call ends the turn; independent local
            # queries may share one model-selected batch.
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = None
        decision = None
        model_call_allowance = self._model_call_allowance(_model_call_limit)
        seen_tool_results: dict[tuple[str, str], tuple[str, int]] = {}
        for model_call_index in range(model_call_allowance):
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_decision_id(turn, image_b64),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="propose_action_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=self._max_attempts,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            model_calls_remaining = model_call_allowance - model_call_index - 1
            if _finish_reason(outcome.response) == "length":
                truncation = _truncation_messages(outcome.response)
                current_exchange.extend(truncation)
                provider_request["messages"].extend(truncation)
                continue
            try:
                visual_calls = _initial_calls_from_response(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            envelopes: list[tuple[str, str, dict[str, Any]]] = []
            if len(visual_calls) > 1:
                try:
                    envelopes = [
                        _visual_batch_call(call) for call in visual_calls
                    ]
                except LiveModelRoleError:
                    envelopes = []
            if envelopes:
                message = response_message(outcome.response)
                content = member(message, "content")
                if content is not None and type(content) is not str:
                    raise InvalidActorProposal(
                        "Actor visual content must be text or null"
                    )
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [
                        {
                            "id": member(call, "id"),
                            "type": "function",
                            "function": {
                                "name": member(member(call, "function"), "name"),
                                "arguments": member(
                                    member(call, "function"), "arguments"
                                ),
                            },
                        }
                        for call in visual_calls
                    ],
                }
                reasoning = member(message, "reasoning_content")
                if type(reasoning) is str:
                    assistant["reasoning_content"] = reasoning
                for call in visual_calls:
                    self._record_tool_start(call)

                def execute_visual(
                    envelope: tuple[str, str, dict[str, Any]],
                ) -> dict[str, Any]:
                    _call_id, name, arguments = envelope
                    return _execute_visual_batch_call(
                        name,
                        arguments,
                        status_provider=self._status_provider,
                        shell_runner=self._shell_runner,
                        connector_gateway=self._connector_gateway,
                        window_text_reader=self._window_text_reader,
                        capture_runner=self._capture_runner,
                        windows_reader=self._windows_reader,
                        turn=turn,
                        timeout_s=min(30.0, self._total_timeout_s),
                        stop_event=stop_event,
                    )

                with ThreadPoolExecutor(
                    thread_name_prefix="visual-actor-tool",
                ) as executor:
                    results = list(executor.map(execute_visual, envelopes))
                batch_messages: list[dict[str, Any]] = [assistant]
                for call, envelope, result in zip(
                    visual_calls, envelopes, results, strict=True
                ):
                    call_id, name, _arguments = envelope
                    self._record_tool_result(name, result)
                    batch_messages.append(
                        _projected_tool_message(
                            seen_tool_results,
                            name=name,
                            call_id=call_id,
                            result=result,
                            model_calls_remaining=model_calls_remaining,
                        )
                    )
                current_exchange.extend(batch_messages)
                provider_request["messages"].extend(batch_messages)
                continue
            status = None
            if self._status_provider is not None:
                try:
                    status = _status_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if status is not None:
                assistant, call_id, name, arguments = status
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_status(
                    self._status_provider,
                    name,
                    arguments,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            shell = None
            if self._shell_runner is not None:
                try:
                    shell = _shell_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if shell is not None:
                assistant, call_id, name, arguments = shell
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_shell(
                    self._shell_runner,
                    arguments,
                    stop_event=stop_event,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            local_utility = None
            if self._shell_runner is not None:
                try:
                    local_utility = _local_utility_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if local_utility is not None:
                assistant, call_id, name, arguments = local_utility
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_local_utility(
                    self._shell_runner,
                    name,
                    arguments,
                )
                self._record_tool_result(name, result)
                utility_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(utility_messages)
                provider_request["messages"].extend(utility_messages)
                continue
            window_text = None
            if self._window_text_reader is not None:
                try:
                    window_text = _window_text_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if window_text is not None:
                assistant, call_id, name, arguments = window_text
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_window_text(
                    self._window_text_reader,
                    turn,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            capture = None
            if self._capture_runner is not None:
                try:
                    capture = _owned_tool_exchange(
                        outcome.response,
                        _CAPTURE_SCREEN_TOOL,
                        expected_arguments={
                            "path": str,
                            "window": bool,
                        },
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if capture is not None:
                assistant, call_id, name, arguments = capture
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_capture_screen(
                    self._capture_runner,
                    arguments,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            windows = None
            if self._windows_reader is not None:
                try:
                    windows = _owned_tool_exchange(
                        outcome.response,
                        _LIST_WINDOWS_TOOL,
                        expected_arguments={},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if windows is not None:
                assistant, call_id, name, arguments = windows
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_list_windows(self._windows_reader)
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            paste = None
            if self._paste_runner is not None:
                try:
                    paste = _owned_tool_exchange(
                        outcome.response,
                        _PASTE_IMAGE_TOOL,
                        expected_arguments={"path": str},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if paste is not None:
                assistant, call_id, name, arguments = paste
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_paste_image(
                    self._paste_runner,
                    arguments,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            try:
                connector = _connector_exchange(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            if connector is None:
                try:
                    decision = _proposal_from_response(
                        outcome.response,
                        frozenset(
                            {
                                *(
                                    _tool_function_name(tool)
                                    for tool in terminal_tools
                                ),
                                *(
                                    _MODEL_TOOL_NAMES.get(tool.name, tool.name)
                                    for tool in turn.tools
                                ),
                            }
                        ),
                        frozenset(tool.name for tool in turn.tools),
                    )
                    self._record_tool_start(
                        _initial_calls_from_response(outcome.response)[0]
                    )
                    if self._pending_task_state is not None:
                        decision = ActorTurnDecision(
                            proposal=decision.proposal,
                            task_state_update=self._pending_task_state,
                            narration=decision.narration,
                        )
                    self._pending_task_state = None
                    break
                except InvalidActorProposal as exc:
                    if model_calls_remaining <= 0:
                        raise
                    repair = _tool_format_repair(
                        outcome.response,
                        str(exc),
                    )
                    if repair is None:
                        raise
                    assistant, call_id = repair
                    repair_messages = [
                        assistant,
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _canonical_json(
                                {
                                    "tool_format_error": str(exc),
                                    "model_calls_remaining": (
                                        model_calls_remaining
                                    ),
                                    "action": (
                                        "return exactly one valid tool call "
                                        "with the required fields"
                                    ),
                                }
                            ),
                        },
                    ]
                    current_exchange.extend(repair_messages)
                    provider_request["messages"].extend(repair_messages)
                    continue
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            assistant, call_id, name, arguments = connector
            self._record_tool_start(
                _initial_calls_from_response(outcome.response)[0]
            )
            result = _call_connector(
                self._connector_gateway,
                name,
                arguments,
                timeout_s=min(30.0, self._total_timeout_s),
                stop_event=stop_event,
            )
            self._record_tool_result(name, result)
            connector_messages = [
                assistant,
                _projected_tool_message(
                    seen_tool_results,
                    name=name,
                    call_id=call_id,
                    result=result,
                    model_calls_remaining=model_calls_remaining,
                ),
            ]
            current_exchange.extend(connector_messages)
            provider_request["messages"].extend(connector_messages)
        if outcome is None or decision is None:
            raise LiveModelRoleError(
                "Actor visual turn exhausted its model-call allowance"
            )
        exchange = _history_exchange(outcome.response)
        if exchange is None:
            self._visual_history = []
        else:
            utility_messages = _utility_history_messages(
                current_exchange[1:]
            )
            image_message = {
                "role": "user",
                "content": [
                    deepcopy(item)
                    for item in user_message["content"]
                    if item.get("type") == "image_url"
                ],
            }
            self._visual_history.append(
                _VisualExchange(
                    messages=tuple(
                        deepcopy([
                            image_message,
                            *utility_messages,
                            exchange[0],
                        ])
                    ),
                    tool_call_id=exchange[1],
                    runtime_tool_name=exchange[2],
                )
            )
            del self._visual_history[:-_VISUAL_HISTORY_DEPTH]
        self._initial_exchange = None
        return decision


__all__ = [
    "VISUAL_ACTOR_MODEL_ROLE",
    "VISUAL_ACTOR_SYSTEM_CONTRACT",
    "INITIAL_ACTOR_SYSTEM_CONTRACT",
    "LiveVisualActor",
    "VisualActorTurnAdmission",
]
