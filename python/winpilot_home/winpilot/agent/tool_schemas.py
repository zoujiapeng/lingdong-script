"""OpenAI function-calling schemas for the unified WinPilot Agent.

Keep this file compact: every description is sent to the LLM. Runtime dispatch
belongs in ``tools.py``; Coding Runtime only changes the available tool set,
not the runner.
"""
from __future__ import annotations

from copy import deepcopy

from ..action import shell
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


def _tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
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
    "paste_image_file",
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


_DRAWING_EXECUTE_SUBGOAL_PROPS = {
    "subgoal",
    "target_window",
    "draw",
    "draws",
    "verify_texts",
    "constraints",
}


# Drawing is one visible transaction plus a grounded final verdict.  Hiding the
# generic todo mutators prevents models from spending parallel tool calls on
# eight plan items, then replacing that plan (and often inventing a destructive
# clear/restart step) instead of inspecting the canvas and calling done.
_DRAWING_GUI_TOOLS = _LEAN_GUI_TOOLS - {"revise_plan", "complete_subgoal"}


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
    "draw_strokes": "Draw screen-coordinate strokes.",
    "wait": "Wait for a condition.",
    "screenshot": "Capture screen with compact nonvisual summary.",
    "paste_image_file": "Paste a PNG/JPEG into the focused app as an image.",
    "zoom": "Capture a screen rectangle with compact nonvisual summary.",
    "coordinate_diagnostics": "Report physical screen/capture/window/DPI geometry.",
    "coordinate_click_test": "Click physical screen x/y once and report coordinate evidence.",
    "revise_plan": "Create/replace todo plan.",
    "complete_subgoal": "Mark a todo item complete.",
    "record_facts": "Bind visible facts to one subject and source in the per-run working set.",
    "vlm_describe": "Ask VLM about a screen/window region.",
    "vlm_verify": "Strictly verify visible pixels against completion criteria.",
    "done": "Finish only after verified evidence.",
    "blocked": "Pause safely when the real UI cannot be grounded without guessing.",
    "fail": "Finish with an honest failure reason.",
}

_DRAWING_TOOL_DESCRIPTIONS = {
    **_LEAN_TOOL_DESCRIPTIONS,
    "execute_subgoal": (
        "Controller drawing routine. Use launch/focus first. For a detailed creature, "
        "submit one complete 15-30 part-labelled outline-only draws[] call: head/body, "
        "pose-visible ears/eyes, nose/mouth, at least two pose-visible limbs/haunches with "
        "paws, tail, and at least two "
        "decorative details. For a polished organic creature, do not stack ellipses and "
        "rectangles: give the head, body, and visible limbs expressive custom contours as "
        "normalized_strokes=[[[x,y],...]]] in canvas-relative 0..1000 coordinates, with "
        "6-16 waypoints per contour (repeat the first point to close it). Reserve circles/"
        "ellipses for small eyes, pupils, nose, paws, or decorations; never use rectangles "
        "for natural limbs. Never put four parallel capsule legs under an oval; use a coherent "
        "seated/side pose with attached legs and hind haunches. Closed shapes are not filled. Do not clear a draft or split "
        "the creature across todo-plan calls. Do not use it to open/discover an app."
    ),
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


def _prune_execute_subgoal_properties(tool: dict, allowed: set[str]) -> dict:
    if _tool_name(tool) != "execute_subgoal":
        return tool
    fn = tool.get("function") or {}
    params = fn.get("parameters") or {}
    props = params.get("properties")
    if isinstance(props, dict):
        params["properties"] = {key: value for key, value in props.items() if key in allowed}
    return tool


def _apply_tool_profile(tools: list[dict], profile: str) -> list[dict]:
    """Return a task-profiled schema list without changing dispatch capability."""
    if profile in {"", "standard", "full"}:
        return tools
    if profile == "lean_gui":
        return [
            _compact_tool_descriptions(tool)
            for tool in tools
            if _tool_name(tool) in _LEAN_GUI_TOOLS
        ]
    if profile == "drawing_gui":
        return [
            _prune_execute_subgoal_properties(
                _compact_tool_descriptions(tool, _DRAWING_TOOL_DESCRIPTIONS),
                _DRAWING_EXECUTE_SUBGOAL_PROPS,
            )
            for tool in tools
            if _tool_name(tool) in _DRAWING_GUI_TOOLS
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
            props["frame_strokes"] = {
                "type": "array",
                "description": (
                    "Strokes relative to the current whole frame in 0..1000. Each stroke needs "
                    "at least two [x,y] points."
                ),
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "items": point_schema("One normalized stroke point."),
                },
            }
            parameters["required"] = [
                *(item for item in required if item != "strokes"),
                "frame_strokes",
            ]
            fn["description"] = (
                "Draw frame_strokes in the current authoritative frame using 0..1000 "
                "coordinates; the runtime maps every point to physical screen pixels."
            )
        elif name == "run_action_batch":
            actions = props.get("actions")
            items = actions.get("items") if isinstance(actions, dict) else None
            if isinstance(items, dict):
                items["properties"] = {
                    "action": {
                        "type": "string",
                        "enum": [
                            "observe",
                            "click",
                            "click_element",
                            "type_into_element",
                            "type_text",
                            "hotkey",
                            "scroll",
                            "move_mouse",
                            "drag",
                            "draw_strokes",
                            "select_color",
                            "focus_window",
                            "wait",
                            "wait_change",
                            "wait_stable",
                            "read_text",
                            "zoom",
                            "clipboard_write",
                            "clipboard_paste",
                        ],
                        "description": (
                            "Required canonical controller action name. Native-vision batches "
                            "never accept shorthand objects with the action field omitted."
                        ),
                    },
                }
                items["required"] = ["action"]
                items["description"] = (
                    "One canonical {'action': '<name>', ...} entry in a native-vision grounded "
                    "batch; every entry must include action. Never use shorthand. For one spatial "
                    "action, call click/scroll/drag/draw_strokes directly instead of creating a "
                    "one-item batch. The whole batch may contain "
                    "at most one spatial mutation; click/click_element/type_into_element/scroll/"
                    "move_mouse/drag/draw_strokes/select_color count whether grounded by frame_*, "
                    "element_id, or semantic labels/anchors. Keyboard-only type_text/hotkey, waits, "
                    "and verification may accompany that one mutation. Put the next spatial action "
                    "in a new turn after the next real post-action frame. Coordinate actions must use "
                    "the current whole-frame 0..1000 contract: click/scroll/move_mouse use "
                    "frame_x/frame_y; drag uses frame_x1/frame_y1/frame_x2/frame_y2 and optional "
                    "frame_points; draw_strokes uses frame_strokes. Never send physical x/y, "
                    "x1/y1/x2/y2, points, or strokes. normalized_strokes is allowed only for "
                    "controller canvas-relative 0..1000 points that will be mapped through the "
                    "latest detected canvas. Non-coordinate actions keep their normal fields."
                )
            fn["description"] = (
                "Execute a short controller-managed multi-step transaction grounded in the current "
                "authoritative frame. Every child must include its canonical action field. For a "
                "single spatial action use the direct click/scroll/drag/draw_strokes tool, not this "
                "batch. A batch may contain at most one spatial mutation; keyboard-only input, waits, "
                "and checks may share that transaction; defer every later spatial action until the next real "
                "post-action frame. Every nested coordinate action must use frame_* values in "
                "0..1000; the runtime maps them through that exact frame and rejects mixed/physical "
                "coordinates."
            )
    return adapted


def tool_schemas(
    planning: bool = False,
    coding_runtime: bool = False,
    allow_shell: bool = True,
    allow_files: bool = True,
    profile: str = "standard",
    native_vision: bool = False,
) -> list[dict]:
    """Return the OpenAI tools array for the unified Agent.

    When coding_runtime=True, repository-specific tools are added to the same
    AgentRunner tool set instead of routing to a separate coding sub-agent.
    """
    tools = []
    include_file_tools = allow_files or coding_runtime

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
            "Draw multiple independent strokes. Each stroke is [[x,y], ...].",
            {
                "strokes": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                },
                "duration": _prop("number", "Duration per stroke in seconds."),
            },
            ["strokes"],
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
                    "description": "Optional outline-only canvas primitive. For detailed scenes, use draws[] with normalized boxes/strokes instead of an unsupported semantic object name. Closed primitives are outlines, never filled layers.",
                    "properties": {
                        "shape": _prop("string", "Outline-only triangle/rectangle/circle/ellipse/line/check. Unknown object names and filled shapes are unsupported."),
                        "color": _prop("string", "Optional visible color. It is strict only when the user explicitly requested it; otherwise the controller may reuse the current color."),
                        "part": _prop("string", "Semantic scene part, for example head, body, left eye, tail, window, roof. Required on detailed scenes."),
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
                    "description": "Ordered outline-only primitives. For a detailed creature, send one complete 15-30 primitive routine: separate large head/body, pose-visible ears/eyes, nose and mouth, at least two pose-visible limbs/haunches with paws, tail, plus at least two labelled decorative details. For polished organic subjects, head/body/visible limbs should use 6-16 point normalized_strokes contours; when a small model supplies only labelled core boxes, the runtime compiles them into organic contours before execution. Never place four parallel capsule legs under an oval. Use multiple colors only when requested; otherwise keep the current/one style color. Closed shapes are outlines, never filled layers.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shape": _prop("string", "Outline-only triangle/rectangle/circle/ellipse/line/check; filled shapes are unsupported."),
                            "color": _prop("string", "Optional visible color; strict only when explicitly requested by the user."),
                            "part": _prop("string", "Semantic scene part. Label every detailed primitive explicitly, including left/right and front/back distinctions."),
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
                                "description": "Custom canvas-relative strokes in 0..1000 coordinates. Use 6-16 waypoints for an organic contour and repeat the first point to close it.",
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
                    "items": {
                        "type": "object",
                        "description": (
                            "Allowed action values: observe, click, click_element, "
                            "type_into_element, type_text, hotkey, scroll, drag, draw_strokes, select_color, "
                            "focus_window, wait, wait_change, wait_stable, "
                            "read_text, zoom, clipboard_write, clipboard_paste. "
                            "Use either {'action':'click','element_id':1} or shorthand "
                            "{'click': {'element_id':1}}; {'hotkey':['enter']} is OK. click_element accepts "
                            "label/expected_region/visual_anchor/fallback_text/role "
                            "and may do one targeted observe(query=label) retry. "
                            "Use type_into_element for locate+click+optional clear+type."
                            " For draw_strokes, strokes are physical screen pixels. Use normalized_strokes "
                            "for 0..1000 canvas-relative points; the controller maps them through the latest canvas."
                        ),
                    },
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
            _tool("revise_plan", "Create or replace a short ordered todo plan.",
                  {"steps": {"type": "array", "items": {"type": "string"}}}, ["steps"]),
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
        _tool("mouse_down", "Press and hold a mouse button.",
              {"button": _prop("string", "Default left.", enum=["left", "right", "middle"])}),
        _tool("mouse_up", "Release a mouse button.",
              {"button": _prop("string", "Default left.", enum=["left", "right", "middle"])}),
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
        _tool("hold_key", "Hold keys for a duration in milliseconds, then release.",
              {"keys": {"type": "array", "items": {"type": "string"}},
               "duration_ms": _prop("integer")}, ["keys", "duration_ms"]),
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
    profiled = _apply_tool_profile(tools, profile)
    if native_vision:
        return _native_frame_coordinate_tools(profiled)
    return profiled
