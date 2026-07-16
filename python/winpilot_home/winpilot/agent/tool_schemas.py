"""OpenAI function-calling schemas for the unified WinPilot Agent.

Keep this file compact: every description is sent to the LLM. Runtime dispatch
belongs in ``tools.py``; Coding Runtime only changes the available tool set,
not the runner.
"""
from __future__ import annotations

from copy import deepcopy

from ..action import shell
from ..fast.action_contract import supported_actions
from ..memory import experience
from ..perception import vlm

_EXPECT_SCHEMA = {
    "type": "object",
    "description": (
        "Verification condition. Examples: "
        '{"text_appears":"Save"}, {"text_gone":"Loading"}, '
        '{"window_appears":"Notepad"}, {"window_title":"file.txt - Notepad"}, '
        '{"screen_stable":true}, '
        '{"file_exists":"C:\\\\path\\\\file.txt"}, '
        '{"file_format":{"path":"C:\\\\path\\\\image.png","format":"png"}}, '
        '{"process_running":"notepad"}, '
        '{"shell_true":"Test-Path C:\\\\x"}'
    ),
}

_SUPPORTED_CONTROLLER_ACTIONS = list(supported_actions())


def _batch_region_schema(description: str = "Physical screen ROI used for local visual change measurement.") -> dict:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
        },
        "required": ["x", "y", "width", "height"],
    }


def _batch_frame_region_schema(description: str) -> dict:
    coordinate = {"type": "number", "minimum": 0, "maximum": 1000}
    return {
        "type": "object",
        "description": description,
        "properties": {
            "x": coordinate, "y": coordinate,
            "width": coordinate, "height": coordinate,
        },
        "required": ["x", "y", "width", "height"],
    }


def _batch_action_schema(*, native_frame: bool = False) -> dict:
    """Return the typed controller operator union used by run_action_batch.

    Runtime compatibility still accepts historical shorthand, but the model
    receives a canonical discriminated shape for every supported action.  This
    prevents useful arguments (notably select_color.color and visual-wait ROI)
    from disappearing behind an untyped ``params`` object.
    """

    text = {"type": "string"}
    boolean = {"type": "boolean"}
    number = {"type": "number"}
    integer = {"type": "integer"}
    string_array = {"type": "array", "items": {"type": "string"}}
    point = {
        "type": "array", "minItems": 2, "maxItems": 2,
        "items": {"type": "number" if native_frame else "integer"},
    }
    points = {"type": "array", "items": point}
    strokes = {"type": "array", "items": points}
    common = {
        "optional": boolean,
        "expect": deepcopy(_EXPECT_SCHEMA),
        "require_screen_change": boolean,
        "require_transient": boolean,
        (
            "effect_frame_region" if native_frame else "effect_region"
        ): (
            _batch_frame_region_schema(
                "Effect ROI in the current authoritative whole-frame 0..1000 coordinate system."
            ) if native_frame else _batch_region_schema()
        ),
    }

    def variant(name: str, properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "const": name},
                **common,
                **(properties or {}),
            },
            "required": ["action", *required],
            "additionalProperties": False,
        }

    anchor = {
        "label": text, "text_query": text, "expected_region": text,
        "visual_anchor": text, "fallback_text": string_array, "role": text,
        "anchor": {"type": "object"},
    }
    click_options = {
        "button": {"type": "string", "enum": ["left", "right", "middle"]},
        "double": boolean, "physical": boolean, "prefer_uia_invoke": boolean,
        "visual_servo": boolean,
    }
    if native_frame:
        direct_click = {
            "frame_x": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_y": {"type": "number", "minimum": 0, "maximum": 1000},
            "element_id": integer,
            **anchor,
            **click_options,
        }
        scroll_fields = {
            "frame_x": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_y": {"type": "number", "minimum": 0, "maximum": 1000},
            "amount": integer,
        }
        pointer_transition_fields = {
            "frame_x": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_y": {"type": "number", "minimum": 0, "maximum": 1000},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
        }
        drag_fields = {
            "frame_x1": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_y1": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_x2": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_y2": {"type": "number", "minimum": 0, "maximum": 1000},
            "frame_points": points,
            "duration": number,
        }
        draw_fields = {"frame_strokes": strokes, "duration": number}
    else:
        direct_click = {"x": integer, "y": integer, "element_id": integer, **anchor, **click_options}
        scroll_fields = {"x": integer, "y": integer, "amount": integer}
        pointer_transition_fields = {
            "x": integer,
            "y": integer,
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
        }
        drag_fields = {
            "x1": integer, "y1": integer, "x2": integer, "y2": integer,
            "points": points, "duration": number,
        }
        draw_fields = {
            "strokes": strokes,
            "normalized_strokes": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {
                        "type": "array", "minItems": 2, "maxItems": 2,
                        "items": {"type": "number", "minimum": 0, "maximum": 1000},
                    },
                },
            },
            "duration": number,
        }

    roi_wait = {
        ("frame_region" if native_frame else "region"): (
            _batch_frame_region_schema(
                "Exact interaction ROI in the current authoritative whole-frame 0..1000 coordinate system."
            ) if native_frame else _batch_region_schema(
                "Exact interaction/action ROI to watch. Omit only to reuse the preceding action ROI or bound target window."
            )
        ),
        "timeout_s": number, "min_ratio": number, "required": boolean,
    }
    variants = [
        variant("observe", {
            "hwnd": integer, "query": text, "max_elements": integer,
            "full": boolean, "changed_only": boolean, "include_icons": boolean,
            "require_match": boolean,
        }),
        variant("click", direct_click),
        variant("click_element", {**anchor, **click_options}),
        variant("type_into_element", {
            **anchor, "text": text, "clear": boolean, "expect_text": {"oneOf": [boolean, text]},
        }, ("text",)),
        variant("type_text", {"text": text, "element_id": integer}, ("text",)),
        variant("hotkey", {"keys": string_array, "value": string_array}, ("keys",)),
        variant("scroll", scroll_fields, (("frame_x", "frame_y", "amount") if native_frame else ("x", "y", "amount"))),
        variant("move_mouse", {
            key: value for key, value in scroll_fields.items() if key != "amount"
        }, (("frame_x", "frame_y") if native_frame else ("x", "y"))),
        variant(
            "mouse_down",
            pointer_transition_fields,
            (("frame_x", "frame_y") if native_frame else ("x", "y")),
        ),
        variant(
            "mouse_up",
            pointer_transition_fields,
            (("frame_x", "frame_y") if native_frame else ("x", "y")),
        ),
        variant(
            "hold_key",
            {
                "keys": string_array,
                "duration_ms": {"type": "integer", "minimum": 1, "maximum": 30_000},
            },
            ("keys", "duration_ms"),
        ),
        variant("drag", drag_fields, (("frame_x1", "frame_y1", "frame_x2", "frame_y2") if native_frame else ("x1", "y1", "x2", "y2"))),
        variant("draw_strokes", draw_fields, (("frame_strokes",) if native_frame else ())),
        variant("select_color", {
            "color": text, "name": text, "strict": boolean, "required_by_user": boolean,
            "expected_region": text,
        }, ("color",)),
        variant("focus_window", {"hwnd": integer, "title": text}),
        variant("launch", {"command": text}, ("command",)),
        variant("wait", {
            "condition": deepcopy(_EXPECT_SCHEMA), "duration_s": number, "seconds": number,
            "duration_ms": number, "ms": number, "time": number,
        }),
        variant("wait_change", roi_wait),
        variant("wait_for_visual_change", roi_wait),
        variant("wait_stable", {
            ("frame_region" if native_frame else "region"): (
                _batch_frame_region_schema(
                    "ROI to settle in the current authoritative whole-frame 0..1000 coordinate system."
                ) if native_frame else _batch_region_schema()
            ), "stable_ms": integer,
            "timeout_s": number, "max_ratio": number, "required": boolean,
        }),
        variant("ensure_document_surface", {"timeout_s": number, "required": boolean}),
        variant("read_text", {"hwnd": integer, "query": text, "require_match": boolean}),
        variant("zoom", {
            "x": integer, "y": integer, "width": integer, "height": integer,
        }, ("x", "y", "width", "height")),
        variant("screenshot", {"path": text, "hwnd": integer}),
        variant("clipboard_read"),
        variant("clipboard_write", {"text": text}, ("text",)),
        variant("clipboard_paste"),
    ]
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _SUPPORTED_CONTROLLER_ACTIONS},
        },
        "required": ["action"],
        "oneOf": variants,
        "description": (
            "Canonical typed controller action. Runtime shorthand remains accepted for old scripts, "
            "but model-generated actions must use the explicit action discriminator and its declared fields."
        ),
    }


def _tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    *,
    parameter_schema: dict | None = None,
) -> dict:
    parameters = {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
    }
    if parameter_schema:
        parameters.update(deepcopy(parameter_schema))
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def _prop(kind: str, description: str = "", **extra) -> dict:
    spec = {"type": kind}
    if description:
        spec["description"] = description
    spec.update(extra)
    return spec


def _expectable(props: dict | None = None) -> dict:
    out = dict(props or {})
    out["expect"] = _EXPECT_SCHEMA
    return out


def _tool_name(tool: dict) -> str:
    return str((tool.get("function") or {}).get("name") or "")


def _promote_tools(tools: list[dict], names: list[str], before: str) -> None:
    wanted = set(names)
    promoted = [tool for tool in tools if _tool_name(tool) in wanted]
    if not promoted:
        return
    rest = [tool for tool in tools if _tool_name(tool) not in wanted]
    insert_at = next((idx for idx, tool in enumerate(rest) if _tool_name(tool) == before), len(rest))
    tools[:] = [*rest[:insert_at], *promoted, *rest[insert_at:]]


_LEAN_GUI_TOOLS = {
    "list_windows",
    "scan_windows",
    "focus_window",
    "open_app",
    "launch",
    "observe",
    "screen_watch",
    "read_text",
    "execute_subgoal",
    "run_action_batch",
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "wait",
    "screenshot",
    "capture_artifact",
    "paste_image_file",
    "open_conversation",
    "send_text",
    "send_artifact",
    "verify_delivery",
    "zoom",
    "coordinate_diagnostics",
    "coordinate_click_test",
    "revise_plan",
    "complete_subgoal",
    "record_facts",
    "vlm_describe",
    "vlm_verify",
    "done",
    "blocked",
    "fail",
}


# Native-vision models already receive the authoritative frame after each
# transaction.  Keep their operator close to screenshot -> one typed action ->
# fresh screenshot.  The larger deterministic controller remains available to
# text-only models, where it compensates for weaker direct pixel grounding.
_NATIVE_VISION_LEAN_TOOLS = {
    "list_windows",
    "focus_window",
    "open_app",
    "launch",
    "observe",
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "wait",
    "revise_plan",
    "complete_subgoal",
    "record_facts",
    "done",
    "blocked",
    "fail",
}


_LEAN_TOOL_DESCRIPTIONS = {
    "list_windows": "List visible windows.",
    "scan_windows": "Focus/observe matching windows.",
    "focus_window": "Bring hwnd to foreground.",
    "open_app": "Resolve, open, focus, and bind an app in one local routine.",
    "find_app": "Find a running app/process.",
    "find_program": "Find an installed program.",
    "launch": "Launch an app/program/URL.",
    "observe": "Observe target/foreground window; use query/full/changed_only when needed.",
    "screen_watch": "Cheap screen change watcher.",
    "read_text": "Read visible text from target/window.",
    "execute_subgoal": (
        "Controller GUI routine. Include concrete input, click/clicks, draw/draws, "
        "or verify_texts; subgoal alone is invalid."
    ),
    "run_action_batch": "Execute a short grounded GUI batch in the local controller.",
    "click": "Click element_id or x/y.",
    "type_text": "Type literal text into the focused control.",
    "hotkey": "Press shortcut keys.",
    "scroll": "Scroll at x/y.",
    "drag": "Drag from x1/y1 to x2/y2.",
    "draw_strokes": (
        "Draw exactly one of strokes (grounded physical px) or normalized_strokes "
        "(0..1000 on one light work surface detected from current target pixels). "
        "Give only key vertices/curve anchors, normally 4-16 points per path; the executor "
        "connects them continuously, so never enumerate pixel-sized or equal-step points."
    ),
    "wait": "Wait for a condition.",
    "screenshot": "Capture screen with compact nonvisual summary.",
    "paste_image_file": "Paste a PNG/JPEG into the focused app as an image.",
    "zoom": "Capture a screen rectangle with compact nonvisual summary.",
    "coordinate_diagnostics": "Report physical screen/capture/window/DPI geometry.",
    "coordinate_click_test": "Click physical screen x/y once and report coordinate evidence.",
    "revise_plan": (
        "Change the live todo at any time: steps replaces it; operations accepts "
        "{op:add,goal,index?}, {op:update,index,goal}, {op:remove,index}, or "
        "{op:move,index,to_index}, {op:reopen,index,reason?}. Indices are "
        "current and 1-based; steps may be [] to clear the list."
    ),
    "complete_subgoal": "Mark a todo item complete.",
    "record_facts": "Bind visible facts to one subject and source in the per-run working set.",
    "vlm_describe": "Ask VLM about a screen/window region.",
    "vlm_verify": "Strictly verify visible pixels against completion criteria.",
    "done": "Finish only after verified evidence.",
    "blocked": "Pause safely when the real UI cannot be grounded without guessing.",
    "fail": "Finish with an honest failure reason.",
}

def _strip_nested_descriptions(node: object) -> None:
    if isinstance(node, dict):
        node.pop("description", None)
        for value in node.values():
            _strip_nested_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            _strip_nested_descriptions(item)


def _compact_tool_descriptions(tool: dict, descriptions: dict[str, str] | None = None) -> dict:
    compact = deepcopy(tool)
    fn = compact.get("function") or {}
    name = str(fn.get("name") or "")
    description_map = descriptions or _LEAN_TOOL_DESCRIPTIONS
    if name in description_map:
        fn["description"] = description_map[name]
    parameters = fn.get("parameters")
    if isinstance(parameters, dict):
        _strip_nested_descriptions(parameters)
    return compact


def _apply_tool_profile(
    tools: list[dict],
    profile: str,
    required_tool_names: set[str] | None = None,
) -> list[dict]:
    """Return a task-profiled schema list without changing dispatch capability."""
    required = set(required_tool_names or ())
    if profile in {"", "standard", "full"}:
        return tools
    if profile in {"lean_gui", "drawing_gui"}:
        return [
            _compact_tool_descriptions(tool)
            for tool in tools
            if _tool_name(tool) in (_LEAN_GUI_TOOLS | required)
        ]
    return tools


def _native_frame_coordinate_tools(tools: list[dict]) -> list[dict]:
    """Expose image-relative coordinates to native-vision models.

    The runtime maps these fields through the exact authoritative window frame.
    Hiding physical x/y prevents a multimodal model from copying coordinates
    from a resized JPEG into desktop space.
    """
    # coordinate_click_test accepts raw physical x/y and is intentionally not
    # exposed to a model that can see the frame. The normal click tool now
    # performs the same real device action after a deterministic frame mapping.
    adapted = [
        deepcopy(tool)
        for tool in tools
        if _tool_name(tool) != "coordinate_click_test"
    ]

    def coord(description: str) -> dict:
        return _prop("number", description, minimum=0, maximum=1000)

    def point_schema(description: str) -> dict:
        return {
            "type": "array",
            "description": description,
            "items": coord("Normalized frame coordinate, 0..1000."),
            "minItems": 2,
            "maxItems": 2,
        }

    for tool in adapted:
        fn = tool.get("function") or {}
        name = str(fn.get("name") or "")
        parameters = fn.get("parameters") or {}
        props = parameters.get("properties")
        required = list(parameters.get("required") or [])
        if not isinstance(props, dict):
            continue

        if name == "click":
            props.pop("x", None)
            props.pop("y", None)
            props["frame_x"] = coord("Horizontal position in the current whole frame: 0=left, 1000=right edge.")
            props["frame_y"] = coord("Vertical position in the current whole frame: 0=top, 1000=bottom edge.")
            parameters["required"] = [item for item in required if item not in {"x", "y"}]
            fn["description"] = (
                "Click an observed element_id or a point in the current authoritative frame using "
                "frame_x/frame_y in 0..1000. For visual targets prefer frame coordinates; do not "
                "manually scale image pixels into physical screen x/y."
            )
        elif name in {"scroll", "move_mouse"}:
            props.pop("x", None)
            props.pop("y", None)
            props["frame_x"] = coord("Horizontal position in the current whole frame, 0..1000.")
            props["frame_y"] = coord("Vertical position in the current whole frame, 0..1000.")
            parameters["required"] = [
                *(item for item in required if item not in {"x", "y"}),
                "frame_x",
                "frame_y",
            ]
            fn["description"] = (
                ("Scroll at" if name == "scroll" else "Move the mouse to")
                + " frame_x/frame_y in the current authoritative frame (0..1000)."
            )
        elif name == "drag":
            for key in ("x1", "y1", "x2", "y2", "points"):
                props.pop(key, None)
            props.update({
                "frame_x1": coord("Drag start x in the current whole frame, 0..1000."),
                "frame_y1": coord("Drag start y in the current whole frame, 0..1000."),
                "frame_x2": coord("Drag end x in the current whole frame, 0..1000."),
                "frame_y2": coord("Drag end y in the current whole frame, 0..1000."),
                "frame_points": {
                    "type": "array",
                    "description": "Optional intermediate [x,y] points, each relative to the current whole frame in 0..1000.",
                    "items": point_schema("One normalized intermediate point."),
                },
            })
            parameters["required"] = [
                *(item for item in required if item not in {"x1", "y1", "x2", "y2"}),
                "frame_x1", "frame_y1", "frame_x2", "frame_y2",
            ]
            fn["description"] = (
                "Drag between frame_x1/frame_y1 and frame_x2/frame_y2 in the current "
                "authoritative frame (0..1000), optionally through frame_points."
            )
        elif name == "draw_strokes":
            props.pop("strokes", None)
            props.pop("normalized_strokes", None)
            parameters.pop("oneOf", None)
            props["frame_strokes"] = {
                "type": "array",
                "maxItems": 16,
                "description": (
                    "Strokes relative to the current whole frame in 0..1000. Each stroke needs "
                    "at least two [x,y] points. Send at most 16 independent paths in one turn; "
                    "add later detail after the fresh frame. Give only key vertices or curve "
                    "anchors, normally 4-16 points per path; the executor connects anchors "
                    "continuously. Never enumerate pixel-sized or equal-step points along a line "
                    "or curve."
                ),
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 16,
                    "items": point_schema("One normalized stroke point."),
                },
            }
            parameters["required"] = [
                *(item for item in required if item != "strokes"),
                "frame_strokes",
            ]
            fn["description"] = (
                "Create continuous input paths in the current authoritative frame. Use this "
                "instead of repeated click calls for freehand marks, handwriting, signatures, "
                "or canvas paths. Put all independent paths already grounded in this frame into "
                "one frame_strokes array using 0..1000 coordinates; the runtime maps every point "
                "to physical screen pixels. Use only key vertices/curve anchors, normally 4-16 "
                "points per path; do not emit dense equal-step samples because the executor connects "
                "successive anchors continuously. If the current frame reports work_surface_frame_bounds, "
                "they are already inset from scrollbars and window edges. Keep the whole intended "
                "composition comfortably inside them; each stroke must start and keep every authored "
                "point inside those whole-frame bounds. The bounds do not create "
                "another coordinate space. Prefer one coherent complete base composition over several "
                "successive whole-layout alternatives."
            )
        elif name == "run_action_batch":
            actions = props.get("actions")
            items = actions.get("items") if isinstance(actions, dict) else None
            if isinstance(items, dict):
                typed_items = _batch_action_schema(native_frame=True)
                items.clear()
                items.update(typed_items)
                items["description"] = (
                    "One canonical {'action': '<name>', ...} entry in a native-vision grounded "
                    "batch; every entry must include action and its typed arguments. Never use shorthand. "
                    "For one spatial action with no deterministic setup, use the direct click/scroll/drag/draw_strokes "
                    "tool. A transaction has at most one spatial mutation authored in raw frame coordinates; "
                    "A local transaction may combine deterministic semantic/UIA actions, keyboard input, "
                    "wait_for_visual_change, and one raw frame-coordinate gesture without another LLM call. "
                    "Semantic targets may use element_id or Controller-resolved labels/anchors. "
                    "Do not chain two raw frame-coordinate gestures after the first mutation because the "
                    "frame may be stale; defer that gesture until the next real post-action frame. Coordinate actions use "
                    "the current whole-frame 0..1000 contract: click/scroll/move_mouse use "
                    "frame_x/frame_y; drag uses frame_x1/frame_y1/frame_x2/frame_y2 and optional "
                    "frame_points; draw_strokes uses frame_strokes. Never send physical x/y, "
                    "x1/y1/x2/y2, points, strokes, or normalized_strokes. Non-coordinate "
                    "actions keep their normal fields."
                )
            fn["description"] = (
                "Execute a short controller-managed multi-step transaction grounded in the current "
                "authoritative frame. Every child must include its canonical action field and typed inputs. "
                "Use this when deterministic Controller steps, keyboard input and ROI-scoped visual waits "
                "belong to one verified transaction. Every nested coordinate action must use frame_* values in "
                "0..1000; the runtime maps them through that exact frame and rejects mixed/physical "
                "coordinates."
            )
    return adapted


def _native_vision_lean_tools(
    tools: list[dict],
    profile: str,
    required_tool_names: set[str] | None = None,
) -> list[dict]:
    if profile not in {"lean_gui", "drawing_gui"}:
        return tools
    allowed = _NATIVE_VISION_LEAN_TOOLS | set(required_tool_names or ())
    return [
        tool for tool in tools
        if _tool_name(tool) in allowed
    ]


def _specialize_stage_tools(tools: list[dict], stage_type: str) -> list[dict]:
    """Tighten model-facing schemas for a typed stage.

    The direct pointer renderer remains a Controller capability. DrawArtifact
    asks the model for a compact semantic scene plan instead of permitting a
    one-primitive shorthand that invites long corrective pixel paths.
    """
    if str(stage_type or "") != "DrawArtifact":
        return tools
    specialized = deepcopy(tools)
    for tool in specialized:
        function = tool.get("function") or {}
        if str(function.get("name") or "") != "execute_subgoal":
            continue
        parameters = function.get("parameters") or {}
        properties = parameters.get("properties") or {}
        properties.pop("draw", None)
        draws = properties.get("draws")
        if isinstance(draws, dict):
            draws["minItems"] = 1
            draws["maxItems"] = 16
            draws["description"] = (
                "Required compact semantic scene plan. Include every major visible part needed "
                "to recognize the requested subject in this one ordered list. Use one conventional "
                "coherent view: multipart subjects need a readable shared silhouette and attached "
                "spatial relationships, not detached or vertically stacked symbol-like primitives. Give each part a "
                "semantic label and either an outline primitive with box geometry or concise "
                "canvas-relative normalized_strokes. Later calls may contain only localized "
                "missing-part repairs."
            )
            item = draws.get("items")
            if isinstance(item, dict):
                item["required"] = ["part"]
                item_properties = item.get("properties") or {}
                shape = item_properties.get("shape")
                if isinstance(shape, dict):
                    shape["enum"] = [
                        "triangle", "rectangle", "circle", "ellipse", "line", "check",
                    ]
                    shape["description"] = "Supported outline primitive; omit when normalized_strokes is supplied."
                box = item_properties.get("box")
                if isinstance(box, dict):
                    box["description"] = (
                        "Canonical canvas-relative endpoint box [x1,y1,x2,y2] in 0..1000. "
                        "x2/y2 are the lower-right endpoint, never width/height."
                    )
                box_mode = item_properties.get("box_mode")
                if isinstance(box_mode, dict):
                    box_mode["enum"] = ["endpoints"]
                    box_mode["description"] = "Required endpoint interpretation for primitive box geometry."
                item["anyOf"] = [
                    {"required": ["part", "shape", "box", "box_mode"]},
                    {"required": ["part", "normalized_strokes"]},
                ]
        parameters["required"] = ["subgoal", "draws"]
        function["description"] = (
            "DrawArtifact scene planner and local renderer. Submit one ordered draws list with "
            "the subject's major recognizable parts; the Controller maps it to the bound canvas, "
            "executes continuous pointer paths, and returns a fresh frame. Do not submit a singular "
            "draw shorthand or dense frame-coordinate paths."
        )
    return specialized


def tool_schemas(
    planning: bool = False,
    coding_runtime: bool = False,
    allow_shell: bool = True,
    allow_files: bool = True,
    profile: str = "standard",
    native_vision: bool = False,
    required_capabilities: tuple[str, ...] | list[str] = (),
    stage_tools: tuple[str, ...] | list[str] = (),
    stage_type: str = "",
) -> list[dict]:
    """Return the OpenAI tools array for the unified Agent.

    When coding_runtime=True, repository-specific tools are added to the same
    AgentRunner tool set instead of routing to a separate coding sub-agent.
    """
    tools = []
    include_file_tools = allow_files or coding_runtime
    from ..tasking.capability import tools_for_capabilities
    required_tool_names = set(stage_tools) | tools_for_capabilities(required_capabilities)

    if allow_shell and shell.shell_enabled():
        tools.extend([
            _tool(
                "run_shell",
                "Run PowerShell/cmd and return stdout, stderr, and exit code.",
                {
                    "command": _prop("string", "Command to run."),
                    "shell": _prop("string", "Default powershell.", enum=["powershell", "cmd"]),
                    "timeout_s": _prop("number", "Default 30 seconds."),
                    "cwd": _prop("string", "Optional working directory. In Coding Runtime this is relative to the coding workspace."),
                },
                ["command"],
            ),
            _tool(
                "run_detached",
                "Start an independent background PowerShell script and return its PID.",
                {
                    "script": _prop("string", "Full PowerShell script."),
                    "name": _prop("string", "Optional task name for the temp script."),
                },
                ["script"],
            ),
        ])

    tools.extend([
        _tool(
            "schedule_task",
            "Create a persistent WinPilot Agent scheduled task. The future run enters the same AgentRunner path as /api/chat; use for user-requested reminders or delayed/recurring agent work, not for immediate steps.",
            {
                "task": _prop("string", "Natural-language task to run later."),
                "name": _prop("string", "Optional schedule name."),
                "run_at": _prop("string", "Optional ISO datetime. If omitted, delay_seconds is used."),
                "delay_seconds": _prop("number", "Optional delay from now."),
                "interval_seconds": _prop("number", "Optional recurrence interval."),
                "enabled": _prop("boolean", "Default true."),
                "max_steps": _prop("integer", "Optional max AgentRunner steps."),
            },
            ["task"],
        ),
        _tool(
            "list_scheduled_tasks",
            "List persistent WinPilot Agent scheduled tasks.",
            {"enabled_only": _prop("boolean", "Only return enabled schedules.")},
        ),
        _tool(
            "cancel_scheduled_task",
            "Disable a persistent WinPilot Agent scheduled task by id.",
            {"id": _prop("string", "Schedule id.")},
            ["id"],
        ),
        _tool("list_windows", "List visible top-level windows with title, hwnd, class, and geometry."),
        _tool(
            "observe",
            "Observe a window and return structured UI elements. Omit hwnd for current target/foreground window. Use query for long lists/desktops when the target element may be omitted from the default output.",
            {
                "hwnd": _prop("integer", "Window handle."),
                "include_icons": _prop("boolean", "Whether to include CV icon candidates."),
                "query": _prop("string", "Optional case-insensitive element text filter."),
                "max_elements": _prop("integer", "Maximum elements to print after filtering. Default 160."),
                "full": _prop("boolean", "Force full OCR/UIA observation; useful when text/menu grounding is needed."),
                "changed_only": _prop("boolean", "Observe only the latest screen_watch changed ROI when available."),
            },
        ),
        _tool(
            "scan_windows",
            "Focus and observe several matching windows. Use when an app has multiple or hidden windows.",
            {
                "query": _prop("string", "Title/app substring, e.g. QQ, WeChat, Chrome."),
                "max_windows": _prop("integer", "Maximum matching windows to scan."),
            },
            ["query"],
        ),
        _tool(
            "focus_window",
            "Bring a window to the foreground by hwnd or visible title and set it as target.",
            {
                "hwnd": _prop("integer", "Window handle, preferred after list_windows/scan_windows."),
                "title": _prop("string", "Visible title substring when hwnd is not known."),
            },
        ),
        _tool(
            "open_app",
            "Open a desktop app in one local routine: reuse/focus an existing visible window, or resolve and launch a background/not-running app, wait for its visible window, and establish target_hwnd. Prefer this over find_app/tray/Shell/launch loops when the task simply says to open an app.",
            {
                "name": _prop("string", "Human app/display name, for example Paint, QQ, or Calculator."),
            },
            ["name"],
        ),
        _tool("find_app", "Check whether an app/process is running, including hidden/tray windows.",
              {"name": _prop("string", "Process/app name substring.")}, ["name"]),
        _tool("find_program", "Find locally installed programs by display name or partial name.",
              {"name": _prop("string", "Program display name or substring.")}, ["name"]),
        _tool(
            "web_search",
            "Search the public web and return compact titles, URLs, and snippets. Use this when the user expects you to discover an unknown URL or current external information; use github_search for repository-specific lookup.",
            {
                "query": _prop("string", "Focused search query."),
                "max_results": _prop("integer", "1-10 results; default 5."),
                "timeout_s": _prop("number", "Network timeout, default 10 seconds."),
            },
            ["query"],
        ),
        _tool(
            "tray_click",
            "Click a system tray icon by name; useful for apps minimized to tray.",
            {
                "name": _prop("string", "Tray icon name substring."),
                "double": _prop("boolean", "Whether to double-click."),
                "button": _prop("string", "Mouse button.", enum=["left", "right"]),
            },
            ["name"],
        ),
        _tool("launch", "Launch a program, installed app, or URL. For webpages, pass the URL directly, not 'start msedge ...'.",
              _expectable({"command": _prop("string", "Launch command or app name.")}), ["command"]),
        _tool(
            "click",
            "Click an observed element_id, or absolute screen coordinates x/y.",
            _expectable({
                "element_id": _prop("integer", "Element id from observe."),
                "x": _prop("integer", "Screen x coordinate."),
                "y": _prop("integer", "Screen y coordinate."),
                "button": _prop("string", "Mouse button.", enum=["left", "right", "middle"]),
                "double": _prop("boolean", "Whether to double-click."),
                "physical": _prop("boolean", "Bypass UIA Invoke and send a real mouse press/release. Use for press-state or coordinate tests."),
            }),
        ),
        _tool(
            "type_text",
            "Type literal text into an already-focused keyboard-first control. For labeled/search/message inputs that still need locating or an Apply/Search/Send click, prefer execute_subgoal or run_action_batch/type_into_element.",
            _expectable({
                "text": _prop("string", "Literal text."),
                "element_id": _prop("integer", "Optional input element id."),
            }),
            ["text"],
        ),
        _tool(
            "hotkey",
            "Press a keyboard shortcut, e.g. ['ctrl','s'], ['enter'], ['alt','f4']. Use type_text for printable text like 6+7=. In GUI-only tasks, do not use browser refresh or DevTools shortcuts unless the user explicitly asks.",
            _expectable({"keys": {"type": "array", "items": {"type": "string"}}}),
            ["keys"],
        ),
        _tool(
            "scroll",
            "Scroll at screen coordinates x/y by amount.",
            {
                "x": _prop("integer", "Screen x coordinate."),
                "y": _prop("integer", "Screen y coordinate."),
                "amount": _prop("integer", "Wheel amount."),
            },
            ["x", "y", "amount"],
        ),
        _tool(
            "drag",
            "Drag from x1/y1 to x2/y2, optionally through intermediate points.",
            {
                "x1": _prop("integer"), "y1": _prop("integer"),
                "x2": _prop("integer"), "y2": _prop("integer"),
                "points": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                "duration": _prop("number", "Total duration in seconds."),
            },
            ["x1", "y1", "x2", "y2"],
        ),
        _tool(
            "draw_strokes",
            "Create one or more continuous input paths in a single call. Use this instead "
            "of repeated clicks or one model round-trip per path when the same target surface "
            "and coordinates are already grounded. Provide exactly one coordinate source: "
            "strokes for already-grounded physical screen pixels, or normalized_strokes for "
            "0..1000 paths mapped through one work surface detected from current target-window pixels. "
            "Give key vertices/curve anchors only, normally 4-16 points per path; the executor "
            "connects anchors continuously, so do not enumerate equal-step/pixel-sized points.",
            {
                "strokes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "description": "Already-grounded physical screen-pixel paths.",
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 16,
                        "items": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "integer"},
                        },
                    },
                },
                "normalized_strokes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "description": (
                        "Paths relative to a runtime-detected light work surface: 0=left/top, "
                        "1000=right/bottom. Every coordinate must be within 0..1000; invalid "
                        "or ambiguous surface grounding rejects the whole action. Use only key "
                        "vertices/curve anchors, normally 4-16 points per path."
                    ),
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 16,
                        "items": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "number", "minimum": 0, "maximum": 1000},
                        },
                    },
                },
                "duration": _prop("number", "Duration per stroke in seconds."),
            },
            parameter_schema={
                "oneOf": [
                    {"required": ["strokes"]},
                    {"required": ["normalized_strokes"]},
                ],
            },
        ),
        _tool("wait", "Wait until a condition is true.",
              {"condition": _EXPECT_SCHEMA, "timeout_s": _prop("number", "Default 10 seconds.")}, ["condition"]),
        _tool(
            "execute_subgoal",
            "Controller-first GUI routine: locate/type/click/format/draw/wait/verify inside the local controller. Must include input, click/clicks, draw/draws, or verify_texts; format/formats is also supported for rich text. The subgoal alone is only a label and is invalid. Use formats instead of one hotkey per LLM round. For printable keyboard sequences such as 12*34=, prefer input.text or type_text. If ambiguous or needs_llm_reason, use normal tools. Verify-only execute_subgoal requires an existing target window/session hwnd.",
            {
                "subgoal": _prop("string", "Short label for the routine; this is not executable by itself."),
                "target_window": {
                    "type": "object",
                    "description": "Optional target scope.",
                    "properties": {
                        "hwnd": _prop("integer", "Target hwnd."),
                        "title": _prop("string", "Title hint."),
                        "process": _prop("string", "Process/app hint."),
                    },
                },
                "input": {
                    "type": "object",
                    "description": "Optional locate+type routine.",
                    "properties": {
                        "label": _prop("string", "Input/search label or nearby text."),
                        "text": _prop("string", "Exact literal text to type."),
                        "clear": _prop("boolean", "Default true: Ctrl+A before typing."),
                        "expected_region": _prop("string", "Rough region, e.g. top/right/center."),
                        "fallback_text": {"type": "array", "items": {"type": "string"}},
                        "expect_text": {"oneOf": [{"type": "boolean"}, {"type": "string"}]},
                    },
                },
                "click": {
                    "type": "object",
                    "description": "Optional button/menu click.",
                    "properties": {
                        "label": _prop("string", "Visible label."),
                        "role": _prop("string", "Default button."),
                        "expected_region": _prop("string", "Optional rough region."),
                        "fallback_text": {"type": "array", "items": {"type": "string"}},
                        "button": _prop("string", "Mouse button.", enum=["left", "right", "middle"]),
                        "double": _prop("boolean", "Double click."),
                        "physical": _prop("boolean", "Use a real mouse press instead of UIA Invoke."),
                        "prefer_uia_invoke": _prop("boolean", "Opt into accessibility Invoke instead of the default real mouse press."),
                        "visual_servo": _prop("boolean", "Locally re-estimate and correct the target immediately before clicking."),
                    },
                },
                "clicks": {
                    "type": "array",
                    "description": "Optional ordered clicks.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": _prop("string", "Visible label."),
                            "role": _prop("string", "Default button."),
                            "expected_region": _prop("string", "Optional rough region."),
                            "fallback_text": {"type": "array", "items": {"type": "string"}},
                            "button": _prop("string", "Mouse button.", enum=["left", "right", "middle"]),
                            "physical": _prop("boolean", "Use a real mouse press instead of UIA Invoke."),
                            "prefer_uia_invoke": _prop("boolean", "Opt into accessibility Invoke instead of the default real mouse press."),
                            "visual_servo": _prop("boolean", "Locally re-estimate and correct the target immediately before clicking."),
                            "double": _prop("boolean", "Double click."),
                        },
                    },
                },
                "format": {
                    "type": "object",
                    "description": "Optional rich-text formatting routine executed locally.",
                    "properties": {
                        "range": _prop("string", "first_line, body_after_first_line, current_line, or all.", enum=["first_line", "body_after_first_line", "current_line", "all"]),
                        "bold": _prop("boolean", "Apply bold to the selected range when true."),
                        "italic": _prop("boolean", "Apply italic to the selected range when true."),
                        "alignment": _prop("string", "Paragraph alignment.", enum=["left", "center", "right", "justify"]),
                    },
                },
                "formats": {
                    "type": "array",
                    "description": "Ordered rich-text formatting operations; combine heading and body formatting in one controller call.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "range": _prop("string", "first_line, body_after_first_line, current_line, or all.", enum=["first_line", "body_after_first_line", "current_line", "all"]),
                            "bold": _prop("boolean"),
                            "italic": _prop("boolean"),
                            "alignment": _prop("string", enum=["left", "center", "right", "justify"]),
                        },
                    },
                },
                "draw": {
                    "type": "object",
                    "description": "Optional outline-only canvas primitive with explicit geometry. Visual quality is verified from the rendered frame.",
                    "properties": {
                        "shape": _prop("string", "Outline-only triangle/rectangle/circle/ellipse/line/check. Unknown object names and filled shapes are unsupported."),
                        "color": _prop("string", "Optional visible color. It is strict only when the user explicitly requested it; otherwise the controller may reuse the current color."),
                        "part": _prop("string", "Optional semantic label used for trace evidence and bounded repairs."),
                        "expected_region": _prop("string", "Rough canvas region."),
                        "box": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                            "description": "For line: [x1,y1,x2,y2] endpoints by default. For other shapes: [x,y,width,height]. All values are 0..1000 relative to the detected canvas.",
                        },
                        "box_mode": _prop("string", "Optional line box interpretation.", enum=["endpoints", "xywh"]),
                        "normalized_strokes": {
                            "type": "array",
                            "description": "Custom strokes in canvas-relative 0..1000 coordinates; each stroke is [[x,y],...].",
                            "items": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                        },
                        "duration": _prop("number", "Seconds per stroke; default 0.28."),
                        "strokes": {
                            "type": "array",
                            "description": "Optional absolute screen-coordinate strokes.",
                            "items": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                        },
                    },
                },
                "draws": {
                    "type": "array",
                    "description": "Ordered outline-only primitives with explicit canvas-relative geometry. The controller maps and executes them; the fresh post-action frame judges semantic quality.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shape": _prop("string", "Outline-only triangle/rectangle/circle/ellipse/line/check; filled shapes are unsupported."),
                            "color": _prop("string", "Optional visible color; strict only when explicitly requested by the user."),
                            "part": _prop("string", "Optional semantic label for trace evidence and targeted repair."),
                            "expected_region": _prop("string", "Rough canvas region."),
                            "box": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 4,
                                "maxItems": 4,
                                "description": "For line: [x1,y1,x2,y2] endpoints by default. For other shapes: [x,y,width,height], all relative to the 0..1000 detected canvas.",
                            },
                            "box_mode": _prop("string", "Optional line box interpretation.", enum=["endpoints", "xywh"]),
                            "normalized_strokes": {
                                "type": "array",
                                "description": "Explicit canvas-relative strokes in 0..1000 coordinates; repeat the first point only when the intended path is closed.",
                                "items": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                            },
                            "duration": _prop("number", "Seconds per stroke; default 0.28."),
                            "strokes": {
                                "type": "array",
                                "description": "Optional absolute screen-coordinate strokes.",
                                "items": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                            },
                        },
                    },
                },
                "verify_texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Visible strings/states to verify.",
                },
                "constraints": {
                    "type": "object",
                    "description": "Optional constraints; {'wait':false} skips stable wait.",
                },
            },
            ["subgoal"],
        ),
        _tool(
            "run_action_batch",
            "Execute a short low-latency GUI action batch without an LLM round-trip between each action. Use only for obvious grounded sequences; it stops on the first failed/blocked action by default. The controller may append a compatibility-named auto_visual_verify item, but that item is only a pixel-effect summary and is never semantic completion evidence.",
            {
                "subgoal": _prop("string", "Optional short label for what this batch should accomplish."),
                "actions": {
                    "type": "array",
                    "items": _batch_action_schema(native_frame=False),
                },
                "stop_on_error": _prop("boolean", "Default true."),
                "sync": {
                    "oneOf": [{"type": "boolean"}, {"type": "object"}],
                    "description": (
                        "Optional cheap post-batch screen sync. Omit for default stable sync "
                        "after mutating batches; set false to disable. Fields: mode='stable' "
                        "or 'change', stable_ms, timeout_s, required."
                    ),
                },
            },
            ["actions"],
        ),
        _tool(
            "screen_watch",
            "Low-cost screen change watcher. It continuously or manually samples cheap frame diffs and returns changed ROIs; it does not do OCR/VLM.",
            {
                "action": _prop("string", "start, stop, status, or sample.", enum=["start", "stop", "status", "sample"]),
            },
            ["action"],
        ),
        _tool("read_text", "Read visible text from a window. With query, use a fast UIA text index before fused/OCR fallback.",
              {
                  "hwnd": _prop("integer", "Window handle. Omit for current target."),
                  "query": _prop("string", "Optional exact visible text/substring to find."),
                  "require_non_input": _prop("boolean", "Exclude edit/search/input controls so a result cannot pass only because the typed text remains in its input field."),
              }),
        _tool("find_image", "Find an image template on screen and return matching coordinates.",
              {"template": _prop("string", "Template name.")}, ["template"]),
        _tool(
            "capture_template",
            "Capture a screen region as a reusable image template.",
            {
                "left": _prop("integer"), "top": _prop("integer"),
                "width": _prop("integer"), "height": _prop("integer"),
                "name": _prop("string", "Template name."),
            },
            ["left", "top", "width", "height", "name"],
        ),
        _tool("color_probe", "Read color information at a screen point.",
              {"x": _prop("integer"), "y": _prop("integer")}, ["x", "y"]),
        _tool("undo_last", "Undo or clean up the most recent reversible action from this run."),
        _tool("done", "Finish only after verified evidence shows the task is complete.",
              {"summary": _prop("string", "Completion summary.")}, ["summary"]),
        _tool(
            "blocked",
            "Safely pause and ask the user when the real UI, target, or result cannot be grounded without guessing. This bypasses automatic retry.",
            {
                "reason": _prop("string", "Exact visual ambiguity or external blocker."),
                "evidence": _prop("string", "What is actually visible/known, without guesses."),
                "question": _prop("string", "The smallest user confirmation needed to continue safely."),
            },
            ["reason", "question"],
        ),
        _tool("fail", "Finish with an honest failure reason when the task cannot be completed.",
              {"reason": _prop("string", "Failure reason.")}, ["reason"]),
    ])
    if allow_files:
        tools.append(_tool(
            "search_files",
            "Fast local filename/folder search. Uses Everything syntax when available (for example ext:pdf or dm:today); do not use it to launch installed apps.",
            {
                "query": _prop("string", "Filename keyword or wildcard."),
                "max_results": _prop("integer", "Default 50."),
                "path": _prop("string", "Optional directory scope."),
                "kind": {
                    "type": "string",
                    "enum": ["any", "file", "folder"],
                    "description": "Return files, folders, or both. Default any.",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extensions without dots, for example [pdf, docx].",
                },
                "sort": {
                    "type": "string",
                    "enum": ["path", "name", "date_modified", "size"],
                    "description": "Result order. Default path.",
                },
            },
            ["query"],
        ))
    _promote_tools(tools, ["open_app"], before="find_app")
    _promote_tools(tools, ["execute_subgoal"], before="click")

    if planning:
        tools.extend([
            _tool(
                "revise_plan",
                (
                    "Change the live ordered todo plan at any time. Provide exactly "
                    "one of: steps to replace the list, or operations to add, update, "
                    "remove, move, or reopen items without discarding unrelated progress."
                ),
                {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Complete replacement ordered list; [] explicitly clears it.",
                    },
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Atomic mutations applied in order using current 1-based indices.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["add", "update", "remove", "move", "reopen"],
                                },
                                "index": {
                                    "type": "integer",
                                    "description": "Source/edit/remove index; add insertion index, or omit to append.",
                                },
                                "to_index": {
                                    "type": "integer",
                                    "description": "Destination index for move.",
                                },
                                "goal": {
                                    "type": "string",
                                    "description": "New item text for add/update.",
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "Why prior completion evidence is invalid for reopen.",
                                },
                            },
                            "required": ["op"],
                            "oneOf": [
                                {
                                    "properties": {"op": {"enum": ["add"]}},
                                    "required": ["op", "goal"],
                                },
                                {
                                    "properties": {"op": {"enum": ["update"]}},
                                    "required": ["op", "index", "goal"],
                                },
                                {
                                    "properties": {"op": {"enum": ["remove"]}},
                                    "required": ["op", "index"],
                                },
                                {
                                    "properties": {"op": {"enum": ["move"]}},
                                    "required": ["op", "index", "to_index"],
                                },
                                {
                                    "properties": {"op": {"enum": ["reopen"]}},
                                    "required": ["op", "index"],
                                },
                            ],
                        },
                    },
                },
                parameter_schema={
                    "oneOf": [
                        {"required": ["steps"]},
                        {"required": ["operations"]},
                    ],
                },
            ),
            _tool("complete_subgoal", "Mark a todo item complete by 1-based index.",
                  {"index": _prop("integer", "1-based plan index.")}, ["index"]),
        ])

    tools.append(_tool(
        "record_facts",
        (
            "Record facts from the current visible source in a per-run working set. "
            "For multi-page research, call this before leaving each source page; bind every field "
            "to its subject and source URL. This is scratch state, not long-term memory."
        ),
        {
            "subject": _prop("string", "Entity/item these facts belong to."),
            "facts": {
                "type": "object",
                "description": "Visible field-value pairs. Use an explicit not-visible value instead of guessing.",
                "additionalProperties": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                    ],
                },
            },
            "source_url": _prop("string", "URL copied/read from the current page address bar."),
            "source_title": _prop("string", "Current visible page/window title."),
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        ["subject", "facts"],
    ))

    if experience.enabled():
        tools.extend([
            _tool("remember", "Store a user-requested long-term memory note with applicability/evidence.",
                  {
                      "note": _prop("string"),
                      "kind": _prop("string", "fact, preference, rule, or summary."),
                      "title": _prop("string", "Optional short title."),
                      "app": _prop("string", "Optional app name."),
                      "anchors": {
                          "type": "array",
                          "items": {"type": "string"},
                          "description": "Optional recall anchors.",
                      },
                      "conditions": {
                          "type": "array",
                          "items": {"type": "string"},
                          "description": "Optional applicability conditions.",
                      },
                      "evidence": {
                          "type": "array",
                          "items": {"type": "string"},
                          "description": "Optional evidence required before relying on this note.",
                      },
                  }, ["note"]),
            _tool("forget", "Retire matching long-term memory notes by query.",
                  {"query": _prop("string")}, ["query"]),
            _tool("reuse_skill", "Replay a matching previously successful workflow when appropriate.",
                  {
                      "task": _prop("string"),
                      "memory_id": _prop("string", "Preferred memory id."),
                      "recipe_id": _prop("string", "Legacy recipe id."),
                  }),
            _tool("memory_search", "Search long-term memory; returns compact matches.",
                  {"query": _prop("string"), "limit": _prop("integer", "Default 8.")}, ["query"]),
            _tool("memory_open", "Open one memory in summary/full/source/lineage mode.",
                  {
                      "memory_id": _prop("string"),
                      "mode": _prop("string", "Default summary.", enum=["summary", "full", "source", "lineage"]),
                  }, ["memory_id"]),
            _tool("memory_update", "Edit one long-term memory.",
                  {
                      "memory_id": _prop("string"),
                      "title": _prop("string"),
                      "summary": _prop("string"),
                      "body": _prop("string"),
                      "status": _prop("string"),
                      "confidence": _prop("number"),
                      "conditions": {"type": "array", "items": {"type": "string"}},
                      "evidence": {"type": "array", "items": {"type": "string"}},
                      "anchors": {"type": "array", "items": {"type": "string"}},
                      "supersedes": _prop("string", "Optional old memory id."),
                  }, ["memory_id"]),
            _tool("memory_retire", "Retire one long-term memory by id.",
                  {"memory_id": _prop("string")}, ["memory_id"]),
        ])

    if vlm.is_configured() and not native_vision:
        tools.append(_tool(
            "vlm_describe",
            "Ask the visual model to describe a screen/window region.",
            {
                "question": _prop("string"),
                "left": _prop("integer"), "top": _prop("integer"),
                "width": _prop("integer"), "height": _prop("integer"),
            },
            ["question"],
        ))
        tools.append(_tool(
            "vlm_verify",
            "Strictly judge whether visible pixels satisfy explicit completion criteria. Use for object, quality, or detail claims; a failed verdict means continue or fail honestly.",
            {
                "criteria": _prop("string", "Concrete visible success criteria."),
                "left": _prop("integer"), "top": _prop("integer"),
                "width": _prop("integer"), "height": _prop("integer"),
            },
            ["criteria"],
        ))


    if coding_runtime:
        tools.extend([
            _tool(
                "apply_patch",
                "Coding Runtime: precise single-file replacement. The find text must occur exactly once; re-read the file after patching.",
                {
                    "path": _prop("string", "Path relative to the coding workspace."),
                    "find": _prop("string", "Original text fragment; must be unique."),
                    "replace": _prop("string", "Replacement text."),
                },
                ["path", "find", "replace"],
            ),
            _tool(
                "github_search",
                "Coding Runtime: search GitHub repositories and return clone_url candidates when the user names an open-source project without a URL.",
                {
                    "query": _prop("string", "Repository/project name or keywords."),
                    "max_results": _prop("integer", "Default 5, maximum 10."),
                },
                ["query"],
            ),
        ])

    tools.extend([
        _tool("clipboard_read", "Read the current system clipboard text content."),
        _tool("clipboard_write", "Write text to the system clipboard.",
              {"text": _prop("string")}, ["text"]),
        _tool("clipboard_paste", "Paste the current clipboard content via Ctrl+V."),
        _tool("cursor_position", "Return the current mouse cursor position."),
        _tool("move_mouse", "Move the mouse cursor without clicking.",
              {"x": _prop("integer"), "y": _prop("integer")}, ["x", "y"]),
        _tool(
            "screenshot",
            "Capture the screen. Give path to save a real PNG/JPEG for a later GUI upload; use window=true to save only the active target window instead of using shell commands.",
            {
                "quality": _prop("integer", "JPEG quality 1-100."),
                "path": _prop("string", "Optional absolute .png/.jpg/.jpeg destination."),
                "window": _prop("boolean", "Capture the current target window only."),
                "hwnd": _prop("integer", "Optional window handle when window=true."),
                "x": _prop("integer", "Optional region left when not using window."),
                "y": _prop("integer", "Optional region top when not using window."),
                "width": _prop("integer", "Optional region width."),
                "height": _prop("integer", "Optional region height."),
                "overwrite": _prop("boolean", "Allow replacing an existing destination file."),
            },
        ),
        _tool(
            "paste_image_file",
            "Place a local PNG/JPEG on the clipboard and paste it as an image into the focused chat/editor. Prefer this over hunting for an attachment icon, then verify the pasted preview or sent image bubble.",
            {
                "path": _prop("string", "Absolute path to a PNG, JPEG, BMP, or WebP image."),
                "hwnd": _prop("integer", "Optional target window handle."),
            },
            ["path"],
        ),
        _tool(
            "capture_artifact",
            "Capture a user-task artifact into the current run's managed store and return artifact_id. This is distinct from a transient model observation frame.",
            {
                "scope": _prop("string", "window, screen, canvas, or region."),
                "source_hwnd": _prop("integer", "Optional source window; defaults to the bound target."),
                "region": {"type": "object"},
            },
        ),
        _tool(
            "open_conversation",
            "Open and bind a recipient conversation through the registered domain adapter.",
            {"app": _prop("string"), "recipient": _prop("string")},
            ["app", "recipient"],
        ),
        _tool(
            "send_text",
            "Send text through the bound communication adapter, recording a real send action.",
            {"text": _prop("string")},
            ["text"],
        ),
        _tool(
            "send_artifact",
            "Send a current-run managed artifact_id through the bound communication adapter. Never invent a path.",
            {"artifact_id": _prop("string"), "app": _prop("string"), "recipient": _prop("string")},
            ["artifact_id"],
        ),
        _tool(
            "verify_delivery",
            "Verify post-send visible evidence in the correct conversation. A composer preview alone always fails.",
            {"artifact_id": _prop("string"), "text": _prop("string")},
        ),
        _tool("list_displays", "Enumerate connected monitors."),
        _tool(
            "coordinate_diagnostics",
            "Report physical screen, screenshot capture, monitor, target-window, DPI, and optional point geometry. Use when clicks appear offset, ROI coordinates may be wrong, or screenshot coordinates may not match real mouse coordinates. Read-only.",
            {
                "hwnd": _prop("integer", "Optional target window hwnd; defaults to current target window."),
                "x": _prop("integer", "Optional physical screen x point to probe."),
                "y": _prop("integer", "Optional physical screen y point to probe."),
            },
        ),
        _tool(
            "coordinate_click_test",
            "Perform one real mouse click at physical screen x/y through the same executor path as normal GUI actions, then report cursor/window-relative evidence and optional visible-text verification. Use only for explicit coordinate calibration/testing.",
            {
                "x": _prop("integer", "Physical screen x coordinate."),
                "y": _prop("integer", "Physical screen y coordinate."),
                "hwnd": _prop("integer", "Optional target window hwnd; defaults to current target window."),
                "button": _prop("string", "Mouse button.", enum=["left", "right", "middle"]),
                "double": _prop("boolean", "Whether to double-click."),
                "wait_ms": _prop("integer", "Wait before verification, default 250ms."),
                "expect_text": _prop("string", "Optional visible text expected after the click."),
            },
            ["x", "y"],
        ),
        _tool("zoom", "Capture a screen rectangle as base64 JPEG plus compact nonvisual pixel summaries. Text-only models cannot inspect the base64 directly; if likely_canvas_summary is present, prefer it over the whole-image summary for canvas/drawing verification.",
              {"x": _prop("integer"), "y": _prop("integer"),
               "width": _prop("integer"), "height": _prop("integer"),
               "quality": _prop("integer", "JPEG quality 1-100.")},
              ["x", "y", "width", "height"]),
        _tool(
            "list_installed_apps",
            "List installed applications from the Windows registry with stable pagination. Follow next_offset until complete=true when the user asks for every application.",
            {
                "offset": _prop("integer", "Zero-based page offset; default 0."),
                "limit": _prop("integer", "Page size 1-200; default 80."),
            },
        ),
    ])
    if include_file_tools:
        tools.extend([
            _tool("list_dir", "List files and subdirectories. In Coding Runtime, paths are relative to the configured coding workspace.",
                  {"path": _prop("string", "Directory path. Omit for current directory/workspace root.")}),
            _tool("read_file", "Read a text file with line numbers. In Coding Runtime, paths are workspace-confined.",
                  {"path": _prop("string"), "start": _prop("integer"), "count": _prop("integer")}, ["path"]),
            _tool("write_file", "Write or overwrite a file. In Coding Runtime, sensitive config/secret writes are guarded.",
                  {"path": _prop("string"), "content": _prop("string")}, ["path", "content"]),
            _tool("search_text", "Search for text across files. In Coding Runtime, searches the workspace by default.",
                  {"query": _prop("string"), "path": _prop("string"), "glob": _prop("string"),
                   "regex": _prop("boolean")}, ["query"]),
        ])
    profiled = _apply_tool_profile(tools, profile, required_tool_names)
    if required_tool_names:
        always = {"done", "blocked", "fail", "observe", "focus_window", "list_windows", "revise_plan", "complete_subgoal"}
        allowed = required_tool_names | always
        profiled = [tool for tool in profiled if _tool_name(tool) in allowed]
    profiled = _specialize_stage_tools(profiled, stage_type)
    if native_vision:
        return _native_frame_coordinate_tools(
            _native_vision_lean_tools(profiled, profile, required_tool_names)
        )
    return profiled
