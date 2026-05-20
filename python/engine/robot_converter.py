"""
DSL → Robot Framework .robot 转换器。

将 Lobster DSL 文本转换为 Robot Framework 可执行的 .robot 文件。

DSL 语法:
    CLICK x y
    CLICK "target text"
    TYPE "text"
    HOTKEY ctrl+c
    WAIT 5
    LAUNCH notepad.exe
    FOCUS "window title"
    SCREENSHOT
    SCROLL up
    DRAG "src" TO "dst"
    IF condition ... ELSE ... END
    LOOP 5 ... END

用法:
    from engine.robot_converter import dsl_to_robot
    robot_content = dsl_to_robot(dsl_text, task_name="My Task")
"""

import re
import os


# ── DSL keyword → Robot Framework keyword mapping ──────────────

_KEYWORD_MAP = {
    "CLICK":       "Click Element",
    "TYPE":        "Type Text",
    "HOTKEY":      "Send Keys",
    "WAIT":        "Wait For Seconds",
    "LAUNCH":      "Launch Program",
    "FOCUS":       "Focus Window",
    "SCREENSHOT":  "Capture Screen",
    "SCROLL":      "Scroll",
    "DRAG":        "Drag And Drop",
    "RIGHT_CLICK": "Right Click",
    "DOUBLE_CLICK":"Double Click",
    "MOVEMOUSE":   "Mouse Move",
    "MOUSEMOVE":   "Mouse Move",
    "KEY_PRESS":   "Press Key",
    "PRESS":       "Press Key",
    "HANDLE_DIALOG":"Handle Dialog",
    "POPUP_CLOSE": "Handle Dialog",
}


def _escape_robot_arg(text: str) -> str:
    """Escape special characters in Robot Framework arguments."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace("${", "\\${")  # escape variable syntax
    return s


def _parse_dsl_line(line: str) -> str:
    """Parse a single DSL line and return the equivalent Robot Framework step."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return ""

    # Comment or empty
    if stripped.startswith("--"):
        return ""

    parts = stripped.split(None, 1)  # split into keyword and rest
    if not parts:
        return ""
    keyword = parts[0].upper()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Parse removable quotes around text
    def _unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
            return s[1:-1]
        return s

    # ── Control flow keywords (handled by indentation) ──
    if keyword == "IF":
        condition = _escape_robot_arg(_unquote(rest))
        return f"IF    {condition}"
    elif keyword == "ELSE":
        return "ELSE"
    elif keyword == "ELSEIF" or keyword == "ELSE IF":
        condition = _escape_robot_arg(_unquote(rest))
        return f"ELSE IF    {condition}"
    elif keyword == "END":
        return "END"
    elif keyword == "LOOP":
        count = rest.strip()
        try:
            int(count)
        except ValueError:
            count = "10"
        return f"FOR    ${{i}}    IN RANGE    {count}"
    elif keyword == "CALL":
        name = _escape_robot_arg(_unquote(rest))
        return f"Run Keyword    {name}"

    # ── Action keywords (resolve name from map) ──
    rf_name = _KEYWORD_MAP.get(keyword)
    if rf_name is None:
        return f"# [RAW] {stripped}"

    if keyword == "CLICK":
        if rest and (rest[0].isdigit() or (rest[0] == "-" and len(rest) > 1 and rest[1].isdigit())):
            coords = rest.split()
            if len(coords) >= 2:
                return f"Click Coordinates    {coords[0]}    {coords[1]}"
        target = _escape_robot_arg(_unquote(rest))
        if target.startswith("img:") or target.startswith("image:"):
            return f"Click Element    {target}"
        return f"Click Element    name:{target}"

    elif keyword == "TYPE":
        text = _escape_robot_arg(_unquote(rest))
        return f"{rf_name}    {text}"

    elif keyword == "RIGHT_CLICK":
        if rest and (rest[0].isdigit() or (rest[0] == "-" and len(rest) > 1 and rest[1].isdigit())):
            coords = rest.split()
            if len(coords) >= 2:
                return f"Click Coordinates    {coords[0]}    {coords[1]}    right"
        target = _escape_robot_arg(_unquote(rest))
        return f"Right Click    {target}"

    elif keyword == "DOUBLE_CLICK":
        if rest and (rest[0].isdigit() or (rest[0] == "-" and len(rest) > 1 and rest[1].isdigit())):
            coords = rest.split()
            if len(coords) >= 2:
                return f"Double Click    {coords[0]}    {coords[1]}"
        target = _escape_robot_arg(_unquote(rest))
        return f"Double Click    {target}"

    elif keyword == "MOVEMOUSE" or keyword == "MOUSEMOVE":
        if rest and (rest[0].isdigit() or (rest[0] == "-" and len(rest) > 1 and rest[1].isdigit())):
            coords = rest.split()
            if len(coords) >= 2:
                return f"Mouse Move    {coords[0]}    {coords[1]}"
        target = _escape_robot_arg(_unquote(rest))
        return f"Mouse Move    {target}"

    elif keyword in ("HANDLE_DIALOG", "POPUP_CLOSE"):
        return f"Handle Dialog"

    elif keyword == "HOTKEY":
        keys_part = _unquote(rest)
        if "+" in keys_part:
            key_parts = keys_part.split("+")
        else:
            key_parts = keys_part.split()
        rf_keys = _to_rpa_hotkey(key_parts)
        return f"Send Keys    {rf_keys}"

    elif keyword == "WAIT":
        try:
            secs = float(rest)
        except ValueError:
            secs = 1.0
        return f"{rf_name}    {secs}"

    elif keyword == "SCREENSHOT":
        dest = rest.strip()
        if dest:
            return f"{rf_name}    {_escape_robot_arg(_unquote(dest))}"
        return rf_name

    elif keyword == "SCROLL":
        direction = rest.strip().lower()
        if direction not in ("up", "down"):
            direction = "down"
        return f"{rf_name}    {direction}"

    elif keyword == "DRAG":
        to_match = re.search(r'(.*?)\s+TO\s+(.*)', rest, re.IGNORECASE)
        if to_match:
            src = _escape_robot_arg(_unquote(to_match.group(1).strip()))
            dst = _escape_robot_arg(_unquote(to_match.group(2).strip()))
            return f"Drag And Drop    {src}    {dst}"
        return f"# DRAG (unparseable): {rest}"

    # Generic: keyword name from map + rest as argument
    if rest:
        return f"{rf_name}    {_escape_robot_arg(_unquote(rest))}"
    return rf_name


def _to_rpa_hotkey(key_parts: list) -> str:
    """Convert key name list to RPA send_keys format.

    ['ctrl', 'c'] → '^c'
    ['ctrl', 'shift', 'esc'] → '+^{ESC}'
    ['alt', 'f4'] → '%{F4}'
    """
    mod_map = {
        "ctrl": "^", "control": "^",
        "alt": "%",
        "shift": "+",
        "win": "\\a", "windows": "\\a",
    }
    result = ""
    modifiers = []
    main_keys = []

    for k in key_parts:
        lower = k.strip().lower()
        if lower in mod_map:
            modifiers.append(mod_map[lower])
        else:
            main_keys.append(k.strip())

    # Build the RPA string
    for mod in modifiers:
        if mod in ("^", "%", "+"):
            result += mod
        else:
            result += mod

    for i, mk in enumerate(main_keys):
        upper = mk.upper()
        if len(mk) == 1:
            result += mk
        elif upper in ("ENTER", "TAB", "ESC", "ESCAPE", "BACKSPACE", "DELETE",
                        "HOME", "END", "PAGEUP", "PAGEDOWN", "SPACE",
                        "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
                        "F10", "F11", "F12", "F13", "F14", "F15",
                        "LEFT", "RIGHT", "UP", "DOWN", "INSERT"):
            result += "{" + upper + "}"
        else:
            result += mk

    return result


def dsl_to_robot(dsl_text: str, task_name: str = "Automated Task",
                  library_path: str = None) -> str:
    """Convert Lobster DSL text to Robot Framework .robot file content.

    Args:
        dsl_text: Lobster DSL source string.
        task_name: Name for the Robot Framework task.
        library_path: Path to DesktopActions library. Default: auto-detect.

    Returns:
        Complete .robot file content as string.
    """
    lines = dsl_text.split("\n")

    # Auto-detect library path relative to project root
    if library_path is None:
        # Try common locations
        candidates = [
            "python/rpa_keywords/desktop_actions.py",
            "../python/rpa_keywords/desktop_actions.py",
            "rpa_keywords/desktop_actions.py",
        ]
        for c in candidates:
            if os.path.exists(c):
                library_path = c.replace("\\", "/")
                break
        if library_path is None:
            library_path = "python/rpa_keywords/desktop_actions.py"

    # Build .robot content
    robot_lines = [
        "*** Settings ***",
        f"Library    {library_path}",
        "",
        "*** Tasks ***",
        task_name,  # task name must NOT be indented in RF
    ]

    indent = "    "  # base indent: task body starts at 4 spaces
    indent_stack = []
    indent_size = 4
    is_first = True

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue

        upper = stripped.split(None, 1)[0].upper() if stripped.split() else ""

        line_indent = indent  # save indent before potential push
        if upper in ("IF", "LOOP"):
            indent_stack.append(indent)
            indent = " " * (4 + indent_size * len(indent_stack))
        elif upper in ("ELSE", "ELSEIF", "ELSE IF"):
            pass  # same indent level
        elif upper == "END":
            if indent_stack:
                indent_stack.pop()
                indent = " " * (4 + indent_size * len(indent_stack)) if indent_stack else "    "
            robot_lines.append(f"{indent}END")
            continue

        robot_line = _parse_dsl_line(stripped)
        if robot_line:
            robot_lines.append(f"{line_indent}{robot_line}")

    robot_lines.append("")  # trailing newline
    return "\n".join(robot_lines)


def dsl_to_robot_file(dsl_text: str, output_path: str, task_name: str = "Automated Task") -> str:
    """Convert DSL and write to .robot file. Returns the output path."""
    content = dsl_to_robot(dsl_text, task_name)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


if __name__ == "__main__":
    # Simple CLI test
    sample_dsl = """\
CLICK 100 200
WAIT 1
TYPE Hello World
HOTKEY ctrl+s
WAIT 0.5
SCREENSHOT
LAUNCH notepad.exe
WAIT 2
TYPE 你好世界
LOOP 3
    CLICK 确认
END
FOCUS 记事本
"""
    print(dsl_to_robot(sample_dsl))
