"""
Parse pseudocode (from generate_flow_script) into typed steps for supervised execution.

Each pseudocode line like:
  "点击 '确定'"       →  {"type": "click", "target": "确定", "line": 1}
  "输入 'Hello'"      →  {"type": "type", "text": "Hello", "line": 2}
  "等待 2秒"          →  {"type": "wait", "duration": 2, "line": 3}

Also supports indentation-based blocks for conditions and loops:
  循环 3 次
    点击 '确定'
    等待 1 秒
  →  {"type": "loop", "count": 3, "body": [...], ...}

And inline conditions:
  如果出现 '确定' 则点击 '确定'
  →  {"type": "condition", "condition": "ocr_contains('确定')",
       "true_branch": [{"type": "click", "target": "确定"}], ...}

Also resolves target_window context across sequential steps.
Supports unquoted Chinese commands as fallback (e.g. "点击 确定" → click).
"""

import re
from typing import Any


def parse_pseudocode(pseudocode: str) -> list[dict[str, Any]]:
    """Parse pseudocode text into a list of typed step dicts."""
    raw_lines = pseudocode.strip().split("\n")
    lines = _dedent_lines(raw_lines)
    steps, _ = _parse_block(lines, 0, -1)
    _resolve_window_context(steps)
    _attach_verify_recursive(steps)
    return steps


def _dedent_lines(lines: list[str]) -> list[str]:
    """Remove common leading whitespace from all lines."""
    indents = []
    for line in lines:
        if line.strip():
            stripped = line.lstrip()
            indents.append(len(line) - len(stripped))
    if not indents:
        return list(lines)
    common = min(indents)
    if common == 0:
        return list(lines)
    return [line[common:] if len(line) >= common else line for line in lines]


def _parse_block(lines: list[str], start: int, parent_indent: int
                 ) -> tuple[list[dict[str, Any]], int]:
    """Parse a block of lines at or above parent_indent.

    Returns (steps, next_line_index).
    """
    steps: list[dict[str, Any]] = []
    i = start
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue

        indent = len(raw) - len(raw.lstrip())
        if indent <= parent_indent and i > start:
            break  # return to parent

        common = {"raw": stripped, "line": i + 1}

        # ── Condition: inline or block ──
        if re.match(r'如果', stripped):
            step, consumed = _parse_condition(lines, i, indent, common)
            if step:
                steps.append(step)
            i += consumed if consumed > 0 else 1
            continue

        # ── Otherwise branch (standalone, after a condition) ──
        if re.match(r'否则(?:\s+|$)', stripped):
            break  # Let the parent condition consume this

        # ── Loop ──
        m = re.match(r'循环\s+(\d+)\s*次', stripped)
        if m:
            body, next_i = _parse_block(lines, i + 1, indent)
            step = {**common, "type": "loop", "count": int(m.group(1))}
            if body:
                step["body"] = body
            steps.append(step)
            i = next_i
            continue

        # ── Sub flow ──
        m = re.match(r'子流程\s+[\'\"](.+?)[\'\"]', stripped)
        if m:
            body, next_i = _parse_block(lines, i + 1, indent)
            step = {**common, "type": "sub_flow", "name": m.group(1)}
            if body:
                step["body"] = body
            steps.append(step)
            i = next_i
            continue

        # ── Single line step ──
        step = _parse_one(stripped, i + 1)
        if step and step["type"] != "unknown":
            steps.append(step)
        i += 1

    return steps, i


def _parse_condition(lines: list[str], idx: int, indent: int, common: dict
                     ) -> tuple[dict[str, Any] | None, int]:
    """Parse a condition line and its branches. Returns (step, consumed_lines)."""
    line = lines[idx].strip()
    consumed = 1

    # ── Inline: 如果出现 'x' 则点击 'y' [否则点击 'z'] ──
    # Note: \s* between tokens handles both "则点击" and "则 点击"
    m = re.match(
        r'如果出现\s+[\'\"](.+?)[\'\"]\s+则\s*'
        r'(?:点击|CLICK)\s*[\'\"](.+?)[\'\"]',
        line
    )
    if m:
        condition_text = m.group(1)
        true_target = m.group(2)
        step: dict[str, Any] = {
            **common, "type": "condition",
            "condition": f"ocr_contains('{condition_text}')",
            "true_branch": [
                {"type": "click", "target": true_target, "raw": f"点击 '{true_target}'", "line": idx + 1},
            ],
            "false_branch": [],
        }
        # Check next line for "否则"
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            m2 = re.match(r'否则\s+(?:点击|CLICK)\s+[\'\"](.+?)[\'\"]', next_line)
            if m2:
                step["false_branch"] = [
                    {"type": "click", "target": m2.group(1),
                     "raw": f"点击 '{m2.group(1)}'", "line": idx + 2},
                ]
                consumed = 2
        return step, consumed

    # ── Generic inline: 如果 <condition> 则 <action> ──
    m = re.match(r'如果\s+(.+?)\s+则\s+(.+)', line)
    if m:
        cond_desc = m.group(1).strip()
        action_text = m.group(2).strip()
        action_step = _parse_one(action_text, idx + 1) or {"type": "unknown", "raw": action_text}
        step = {
            **common, "type": "condition",
            "condition": cond_desc,
            "true_branch": [action_step] if action_step["type"] != "unknown" else [],
            "false_branch": [],
        }
        # 否则 on next line
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            m2 = re.match(r'否则\s+(.+)', next_line)
            if m2:
                else_action = _parse_one(m2.group(1).strip(), idx + 2) or {"type": "unknown"}
                if else_action["type"] != "unknown":
                    step["false_branch"] = [else_action]
                consumed = 2
        return step, consumed

    # ── Block format: 如果 <condition> on its own line, body indented ──
    cond_text = re.sub(r'^如果\s+', '', line, count=1).strip()
    true_branch, next_i = _parse_block(lines, idx + 1, indent)
    consumed = next_i - idx

    false_branch: list[dict] = []
    # Check if next block starts with "否则"
    if next_i < len(lines):
        else_line = lines[next_i].strip()
        if re.match(r'否则(?:\s+|$)', else_line):
            else_text = re.sub(r'^否则\s*', '', else_line, count=1).strip()
            if else_text:
                # Inline else action
                else_step = _parse_one(else_text, next_i + 1)
                if else_step and else_step["type"] != "unknown":
                    false_branch = [else_step]
            # Check for indented else body
            false_branch2, next_i2 = _parse_block(lines, next_i + 1, indent)
            if false_branch2:
                false_branch = false_branch2
            consumed = next_i2 - idx

    if not true_branch and not false_branch:
        return None, 1  # Could not parse

    return {
        **common, "type": "condition",
        "condition": cond_text,
        "true_branch": true_branch,
        "false_branch": false_branch,
    }, consumed


def _strip_quotes(text: str) -> str:
    """Strip paired quotes from LLM-generated text if present."""
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        return t[1:-1]
    return t


# ── 并验证 (verify) suffix handling ──

_VERIFY_SUFFIX_MAP = {
    "进程启动": lambda s: [{"method": "process_running",
                            "name": s.get("target") or s.get("title", "")}],
    "进程停止": lambda s: [{"method": "process_running",
                            "name": s.get("target") or s.get("title", ""),
                            "wait_exit": True}],
    "窗口出现": lambda s: [{"method": "window_active",
                            "title": s.get("target") or s.get("title", "")}],
    "文件存在": lambda s: [{"method": "file_exists",
                            "path": s.get("path") or s.get("target", "")}],
    "文件包含": lambda s: [{"method": "file_contains",
                            "path": s.get("path", ""),
                            "text": s.get("content", s.get("text", ""))}],
    "文字出现": lambda s: [{"method": "ocr_text",
                            "query": s.get("target") or s.get("text", "")}],
    "屏幕变化": lambda s: [{"method": "screen_change"}],
    "图像出现": lambda s: [{"method": "image_exists",
                            "template": s.get("target", "")}],
}


def _parse_verify_from_line(raw: str, step: dict) -> list[dict] | None:
    """Parse 并验证 suffix from a raw pseudocode line into a verify chain.

    Supports:
      ...并验证进程启动
      ...并验证窗口出现
      ...并验证文件存在
      ...并验证文件包含
      ...并验证文字出现
      ...并验证屏幕变化
      ...并验证图像出现
      ...并验证 ocr_contains('text')
      ...并验证 process_running('name')
      ...并验证 file_contains('path', 'text')
    """
    m = re.search(r'并验证\s*(.+)$', raw)
    if not m:
        return None

    verify_text = m.group(1).strip()

    # Check known Chinese shorthand keywords
    for key, builder in _VERIFY_SUFFIX_MAP.items():
        if key in verify_text:
            return builder(step)

    # Try parsing as a function call: method_name('arg1', 'arg2')
    func_m = re.match(r'(\w+)\s*\((.+)\)', verify_text)
    if func_m:
        method = func_m.group(1)
        args_str = func_m.group(2)
        args = re.findall(r"['\"](.+?)['\"]", args_str)
        params: dict = {"method": method}
        if len(args) >= 1:
            # Map common arg positions
            method_lo = method.lower()
            if method_lo in ("file_contains", "file_not_contains"):
                params["path"] = args[0]
                if len(args) >= 2:
                    params["text"] = args[1]
            elif method_lo in ("process_running", "process_not_running",
                               "window_exists", "window_not_exists",
                               "ocr_contains", "ocr_not_contains",
                               "image_exists", "image_not_exists",
                               "file_exists", "file_not_exists"):
                params["name"] = args[0]
                params["query"] = args[0]
            else:
                params["query"] = args[0] if args else ""
        return [params]

    return None


def _attach_verify_recursive(steps: list[dict]):
    """Traverse steps and attach verify metadata from the raw line."""
    for step in steps:
        raw = step.get("raw", "")
        verify = _parse_verify_from_line(raw, step)
        if verify:
            step["verify"] = verify
        # Recurse into branches/bodies
        for branch_key in ("true_branch", "false_branch", "body"):
            branch = step.get(branch_key, [])
            if branch:
                _attach_verify_recursive(branch)


def _parse_one(line: str, line_num: int) -> dict[str, Any] | None:
    """Parse a single pseudocode line into a step dict.

    Tries quoted patterns first, then unquoted Chinese fallbacks.
    """
    common = {"raw": line, "line": line_num}

    # ═══════════════════════════════════════════
    # Quoted patterns (existing behavior)
    # ═══════════════════════════════════════════

    # 文字点击 / TEXT_CLICK — OCR-based click
    m = re.match(r'(?:文字点击|TEXT_CLICK)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "text_click", "target": m.group(1)}

    # 点击 / CLICK — coordinate/image/OCR click
    m = re.match(r'(?:点击|CLICK)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "click", "target": m.group(1)}

    # 点击图片 / IMAGE_CLICK
    m = re.match(r'点击图片\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "image_click", "target": m.group(1)}

    # 输入 / TYPE / 键入
    m = re.match(r'(?:输入|TYPE|键入)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "type", "text": m.group(1)}

    # 按键 / HOTKEY / 按下 (e.g. 按键 ctrl+c, 按键 alt+F4)
    m = re.match(r'(?:按键|HOTKEY|按下)\s+(.+)', line)
    if m:
        keys_str = m.group(1).strip()
        keys = [k.strip() for k in re.split(r'[+＋]', keys_str)]
        return {**common, "type": "hotkey", "keys": keys}

    # 运行命令 / RUN / run
    m = re.match(r'(?:运行命令|RUN|run)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        timeout = 30
        tm = re.search(r'超时[=:](\d+)', line)
        if tm:
            timeout = int(tm.group(1))
        return {**common, "type": "run_command", "command": m.group(1), "timeout": timeout}

    # 启动 / 打开 / LAUNCH / OPEN
    m = re.match(r'(?:启动|打开|LAUNCH|OPEN)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "launch", "target": m.group(1)}

    # 聚焦 / 焦点 / FOCUS
    m = re.match(r'(?:聚焦|焦点|FOCUS)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "focus", "title": m.group(1)}

    # 等待 / WAIT / wait
    m = re.match(r'(?:等待|WAIT|wait)\s+(\d+(?:\.\d+)?)\s*秒?', line)
    if m:
        return {**common, "type": "wait", "duration": float(m.group(1))}

    # 滚动 / SCROLL
    m = re.match(r'(?:滚动|SCROLL)\s+(up|down|left|right)(?:\s+(\d+))?', line)
    if m:
        return {**common, "type": "scroll", "direction": m.group(1),
                "amount": int(m.group(2)) if m.group(2) else 3}

    # 截图 / SCREENSHOT
    m = re.match(r'(?:截图|SCREENSHOT)', line)
    if m:
        return {**common, "type": "screenshot"}

    # 拖拽 / DRAG / 拖动 — supports "拖拽 \"A\" \"B\"", "拖拽 \"A\" 到 \"B\"", and unquoted variants
    m = re.match(r'(?:拖拽|DRAG|拖动)\s+[\'\"](.+?)[\'\"]\s*(?:到|至|to|TO)?\s*[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "drag", "from_target": m.group(1), "to_target": m.group(2)}

    # 读取文件 / FILE_READ / 读文件
    m = re.match(r'(?:读取文件|FILE_READ|读文件)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "file_read", "path": m.group(1)}

    # 写入文件 / FILE_WRITE / 写文件 (格式: 写入文件 "路径" "内容")
    m = re.match(r'(?:写入文件|FILE_WRITE|写文件)\s+[\'\"](.+?)[\'\"]\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "file_write", "path": m.group(1), "content": m.group(2)}

    # 保存文件 / FILE_SAVE / 另存为 (格式: 保存文件 "路径")
    m = re.match(r'(?:保存文件|FILE_SAVE|另存为)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "file_save", "path": m.group(1)}

    # ═══════════════════════════════════════════
    # Unquoted Chinese fallback patterns (new)
    # ═══════════════════════════════════════════

    # Strip verify suffix for clean matching on unquoted patterns
    _verify_suffix_re = r'(?:\s*并验证.*)?$'

    # 点击/单击 <target> (unquoted)
    m = re.match(r'(?:点击|单击|CLICK)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "click", "target": _strip_quotes(m.group(1))}

    # 打开/运行 <target> (unquoted) — treat as launch
    m = re.match(r'(?:打开|运行|启动)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "launch", "target": _strip_quotes(m.group(1))}

    # 输入/键入 <text> (unquoted)
    m = re.match(r'(?:输入|键入)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "type", "text": _strip_quotes(m.group(1))}

    # 关闭窗口
    m = re.match(r'关闭(?:窗口)?', line)
    if m:
        return {**common, "type": "hotkey", "keys": ["alt", "f4"]}

    # 按下 <key> / 按 <key>
    m = re.match(r'(?:按下|按)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        keys_str = _strip_quotes(m.group(1))
        keys = [k.strip() for k in re.split(r'[+＋\s,，]', keys_str) if k.strip()]
        return {**common, "type": "hotkey", "keys": keys}

    # 移动鼠标到 <x> <y>
    m = re.match(r'移动(?:鼠标)?(?:到)?\s+(\d+)\s*,?\s*(\d+)', line)
    if m:
        return {**common, "type": "mouse_move", "x": int(m.group(1)), "y": int(m.group(2))}

    # 右键单击/双击/双击 <target>
    m = re.match(r'(?:右键单击|双击)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        click_type = "right" if "右键" in line else "double"
        return {**common, "type": "click", "target": _strip_quotes(m.group(1)),
                "click_type": click_type}

    # 文字点击 <target> (unquoted)
    m = re.match(r'文字点击\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "text_click", "target": _strip_quotes(m.group(1))}

    # 读取文件/读文件 <path> (unquoted)
    m = re.match(r'(?:读取文件|读文件)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "file_read", "path": _strip_quotes(m.group(1))}

    # 写入文件/写文件 <path> <content> (unquoted, space-separated)
    m = re.match(r'(?:写入文件|写文件)\s+(.+?)\s{2,}(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "file_write", "path": _strip_quotes(m.group(1)),
                "content": _strip_quotes(m.group(2))}

    # 保存文件/另存为 <path> (unquoted)
    m = re.match(r'(?:保存文件|另存为)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "file_save", "path": _strip_quotes(m.group(1))}

    # 拖拽/拖动 <from> 到/至/to <to> (unquoted)
    m = re.match(r'(?:拖拽|拖动)\s+(.+?)\s+(?:到|至|to|TO)\s+(.+?)' + _verify_suffix_re, line)
    if m:
        return {**common, "type": "drag", "from_target": _strip_quotes(m.group(1)),
                "to_target": _strip_quotes(m.group(2))}

    # Fallback: treat as unknown
    return {**common, "type": "unknown"}


def _resolve_window_context(steps: list[dict[str, Any]]):
    """Infer target_window for GUI steps based on preceding launch/focus steps."""
    current_window = ""
    for step in steps:
        step_type = step.get("type", "")
        if step_type == "launch":
            current_window = step.get("target", "")
            step["target_window"] = current_window
        elif step_type == "focus":
            current_window = step.get("title", "")
            step["target_window"] = current_window
        elif step_type in ("click", "text_click", "type", "hotkey", "image_click",
                           "scroll", "screenshot", "drag", "file_read", "file_write", "file_save"):
            if current_window:
                step["target_window"] = current_window
        elif step_type in ("condition", "loop", "sub_flow"):
            # Propagate window context into branches/bodies
            _resolve_window_context_in_step(step, current_window)


def _resolve_window_context_in_step(step: dict, current_window: str):
    """Propagate window context into condition branches and loop bodies."""
    for branch_key in ("true_branch", "false_branch", "body"):
        branch = step.get(branch_key, [])
        if branch:
            saved = ""
            for sub in branch:
                st = sub.get("type", "")
                if st == "launch":
                    saved = sub.get("target", "")
                    sub["target_window"] = saved
                elif st == "focus":
                    saved = sub.get("title", "")
                    sub["target_window"] = saved
                elif st in ("click", "type", "hotkey", "image_click", "scroll", "screenshot"):
                    sub["target_window"] = sub.get("target_window") or current_window or saved
