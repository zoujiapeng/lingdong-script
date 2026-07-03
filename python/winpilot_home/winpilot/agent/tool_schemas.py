"""OpenAI function-calling schemas for the unified WinPilot Agent.

Keep this file compact: every description is sent to the LLM. Runtime dispatch
belongs in ``tools.py``; Coding Runtime only changes the available tool set,
not the runner.
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
        '{"window_appears":"Notepad"}, {"window_title":"file.txt - Notepad"}, '
        '{"screen_stable":true}, '
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


def tool_schemas(
    planning: bool = False,
    coding_runtime: bool = False,
    allow_shell: bool = True,
) -> list[dict]:
    """Return the OpenAI tools array for the unified Agent.

    When coding_runtime=True, repository-specific tools are added to the same
    AgentRunner tool set instead of routing to a separate coding sub-agent.
    """
    tools = []

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
            "Press a keyboard shortcut, e.g. ['ctrl','s'], ['enter'], ['alt','f4']. Use type_text for printable text like 6+7=.",
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
            "run_action_batch",
            "Execute a short low-latency GUI action batch without an LLM round-trip between each action. Use only for obvious grounded sequences; it stops on the first failed/blocked action by default. The controller may append an internal auto_visual_verify result after visual mutations.",
            {
                "subgoal": _prop("string", "Optional short label for what this batch should accomplish."),
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "description": (
                            "Allowed action values: observe, click, click_element, "
                            "type_into_element, type_text, hotkey, scroll, drag, draw_strokes, "
                            "focus_window, wait, wait_change, wait_stable, "
                            "read_text, zoom, clipboard_write, clipboard_paste. "
                            "Use either {'action':'click','element_id':1} or shorthand "
                            "{'click': {'element_id':1}}; {'hotkey':['enter']} is OK. click_element accepts "
                            "label/expected_region/visual_anchor/fallback_text/role "
                            "and may do one targeted observe(query=label) retry. "
                            "Use type_into_element for locate+click+optional clear+type."
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
            _tool("remember", "Store a user-requested long-term memory note with applicability/evidence.",
                  {
                      "note": _prop("string"),
                      "app": _prop("string", "Optional app name."),
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
    return tools
