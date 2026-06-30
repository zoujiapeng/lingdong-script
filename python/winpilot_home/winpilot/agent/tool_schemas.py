"""OpenAI function-calling schemas for WinPilot GUI tools.

Keep this file compact: every description is sent to the LLM. Runtime dispatch
belongs in ``tools.py``.
"""
from __future__ import annotations

from ..action import shell
from ..memory import experience
from ..perception import vlm

_EXPECT_SCHEMA = {
    "type": "object",
    "description": (
        "Verification condition. Examples: "
        '{"text_appears":"Save"}, {"text_gone":"Loading"}, '
        '{"window_appears":"Notepad"}, {"screen_stable":true}, '
        '{"file_exists":"C:\\\\path\\\\file.txt"}, '
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


def tool_schemas(planning: bool = False) -> list[dict]:
    """Return the OpenAI tools array for the GUI agent."""
    tools = []

    if shell.shell_enabled():
        tools.extend([
            _tool(
                "run_shell",
                "Run PowerShell/cmd and return stdout, stderr, and exit code.",
                {
                    "command": _prop("string", "Command to run."),
                    "shell": _prop("string", "Default powershell.", enum=["powershell", "cmd"]),
                    "timeout_s": _prop("number", "Default 30 seconds."),
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
        _tool("list_windows", "List visible top-level windows with title, hwnd, class, and geometry."),
        _tool(
            "observe",
            "Observe a window and return structured UI elements. Omit hwnd for current target/foreground window. Use query for long lists/desktops when the target element may be omitted from the default output.",
            {
                "hwnd": _prop("integer", "Window handle."),
                "include_icons": _prop("boolean", "Whether to include CV icon candidates."),
                "query": _prop("string", "Optional case-insensitive element text filter."),
                "max_elements": _prop("integer", "Maximum elements to print after filtering. Default 160."),
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
        _tool("focus_window", "Bring the given hwnd to the foreground and set it as target.",
              {"hwnd": _prop("integer", "Window handle.")}, ["hwnd"]),
        _tool("find_app", "Check whether an app/process is running, including hidden/tray windows.",
              {"name": _prop("string", "Process/app name substring.")}, ["name"]),
        _tool("find_program", "Find locally installed programs by display name or partial name.",
              {"name": _prop("string", "Program display name or substring.")}, ["name"]),
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
        _tool("launch", "Launch a program by exe name, path, shell command, or installed app display name.",
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
            }),
        ),
        _tool(
            "type_text",
            "Type literal text. Optionally focus an element first.",
            _expectable({
                "text": _prop("string", "Literal text."),
                "element_id": _prop("integer", "Optional input element id."),
            }),
            ["text"],
        ),
        _tool(
            "hotkey",
            "Press a keyboard shortcut, e.g. ['ctrl','s'], ['enter'], ['alt','f4'].",
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
        _tool("read_text", "Read visible text from a window using fused perception/OCR.",
              {"hwnd": _prop("integer", "Window handle. Omit for current target.")}),
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
        _tool(
            "search_files",
            "Search user files by name. Do not use this to launch installed apps.",
            {
                "query": _prop("string", "Filename keyword or wildcard."),
                "max_results": _prop("integer", "Default 50."),
                "path": _prop("string", "Optional directory scope."),
            },
            ["query"],
        ),
        _tool("done", "Finish only after verified evidence shows the task is complete.",
              {"summary": _prop("string", "Completion summary.")}, ["summary"]),
        _tool("fail", "Finish with an honest failure reason when the task cannot be completed.",
              {"reason": _prop("string", "Failure reason.")}, ["reason"]),
    ])

    if planning:
        tools.extend([
            _tool("revise_plan", "Create or replace a short ordered todo plan.",
                  {"steps": {"type": "array", "items": {"type": "string"}}}, ["steps"]),
            _tool("complete_subgoal", "Mark a todo item complete by 1-based index.",
                  {"index": _prop("integer", "1-based plan index.")}, ["index"]),
        ])

    if experience.enabled():
        tools.extend([
            _tool("remember", "Store a user-requested long-term memory note.",
                  {"note": _prop("string"), "app": _prop("string", "Optional app name.")}, ["note"]),
            _tool("forget", "Delete matching long-term memory notes by query.",
                  {"query": _prop("string")}, ["query"]),
            _tool("reuse_skill", "Replay a matching previously successful workflow when appropriate.",
                  {"task": _prop("string"), "recipe_id": _prop("string", "Optional recipe id.")}),
        ])

    if vlm.is_configured():
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
        _tool("screenshot", "Capture the full screen as base64 JPEG plus compact nonvisual pixel summaries. Text-only models cannot inspect the base64 directly; if likely_canvas_summary is present, prefer it over the whole-image summary for canvas/drawing verification.",
              {"quality": _prop("integer", "JPEG quality 1-100.")}),
        _tool("hold_key", "Hold keys for a duration in milliseconds, then release.",
              {"keys": {"type": "array", "items": {"type": "string"}},
               "duration_ms": _prop("integer")}, ["keys", "duration_ms"]),
        _tool("list_displays", "Enumerate connected monitors."),
        _tool("zoom", "Capture a screen rectangle as base64 JPEG plus compact nonvisual pixel summaries. Text-only models cannot inspect the base64 directly; if likely_canvas_summary is present, prefer it over the whole-image summary for canvas/drawing verification.",
              {"x": _prop("integer"), "y": _prop("integer"),
               "width": _prop("integer"), "height": _prop("integer"),
               "quality": _prop("integer", "JPEG quality 1-100.")},
              ["x", "y", "width", "height"]),
        _tool("list_installed_apps", "List installed applications from the Windows registry."),
        _tool("list_dir", "List files and subdirectories in a directory.",
              {"path": _prop("string", "Directory path. Omit for current directory.")}),
        _tool("read_file", "Read a text file with line numbers.",
              {"path": _prop("string"), "start": _prop("integer"), "count": _prop("integer")}, ["path"]),
        _tool("write_file", "Write or overwrite a file.",
              {"path": _prop("string"), "content": _prop("string")}, ["path", "content"]),
        _tool("search_text", "Search for text across files in a directory.",
              {"query": _prop("string"), "path": _prop("string"), "glob": _prop("string"),
               "regex": _prop("boolean")}, ["query"]),
    ])
    return tools
