"""Text protocol parsing for text-only desktop agents.

The runtime loop accepts a simple line-oriented protocol instead of function
calling so providers like DeepSeek can drive tools reliably.
"""

from __future__ import annotations

import re


def has_done_marker(content: str) -> bool:
    if not content:
        return False
    return bool(re.search(
        r"(?im)^\s*(?:done|summary|final|完成|总结|總結|鎬荤粨)\s*[:：]",
        content,
    ))


def tool_label_pattern() -> str:
    # The mojibake labels keep compatibility with old prompts in this repo.
    return r"(?:Tool|tool|工具|宸ュ叿)"


def args_label_pattern() -> str:
    return r"(?:Args|args|Arguments|arguments|参数|參數|鍙傛暟)"


def parse_params_section(text: str) -> dict:
    args: dict = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        match = re.match(r"([A-Za-z_][\w.-]*)\s*[:：]\s*(.*)", raw_line.strip())
        if match:
            key = match.group(1)
            val = match.group(2).strip().strip("'\"")
            if val == "|":
                indent = len(raw_line) - len(raw_line.lstrip())
                val_lines: list[str] = []
                i += 1
                while i < len(lines):
                    if lines[i].strip() == "":
                        i += 1
                        continue
                    cur_indent = len(lines[i]) - len(lines[i].lstrip())
                    if cur_indent > indent:
                        val_lines.append(lines[i].rstrip())
                        i += 1
                    else:
                        break
                args[key] = "\n".join(val_lines)
                continue
            args[key] = val
        i += 1
    return args


def parse_tool_blocks(content: str) -> list[tuple[str, dict]]:
    if not content:
        return []
    tool_label = tool_label_pattern()
    args_label = args_label_pattern()
    blocks = re.split(rf'\n(?=\s*{tool_label}\s*[:：]\s*\S+)', content)
    results: list[tuple[str, dict]] = []
    for block in blocks:
        lines = block.splitlines()
        tool_name = ""
        args_lines: list[str] = []
        in_args = False
        for line in lines:
            tool_match = re.match(rf"^\s*{tool_label}\s*[:：]\s*([A-Za-z_][\w.-]*)", line)
            if tool_match:
                tool_name = tool_match.group(1).strip()
                in_args = False
                continue
            if re.match(rf"^\s*{args_label}\s*[:：]\s*$", line):
                in_args = True
                continue
            if in_args:
                if not line.strip():
                    break
                args_lines.append(line)
        if not tool_name:
            continue
        args = parse_params_section("\n".join(args_lines)) if args_lines else {}
        results.append((tool_name, args))
    return results


def parse_response(content: str) -> tuple[str, str, dict]:
    if not content:
        return "error", "", {}
    tool_blocks = parse_tool_blocks(content)
    if tool_blocks:
        name, args = tool_blocks[0]
        return "tool", name, args
    if has_done_marker(content):
        return "done", "", {"summary": content}
    return "error", "", {"raw": content[:200]}
