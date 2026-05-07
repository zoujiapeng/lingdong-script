"""
Parse pseudocode (from generate_flow_script) into typed steps for supervised execution.

Each pseudocode line like:
  "点击 '确定'"  →  {"type": "click", "target": "确定", "line": 1}
  "输入 'Hello'"  →  {"type": "type", "text": "Hello", "line": 2}
  "等待 2秒"     →  {"type": "wait", "duration": 2, "line": 3}
"""

import re
from typing import Any


def parse_pseudocode(pseudocode: str) -> list[dict[str, Any]]:
    """Parse pseudocode text into a list of typed step dicts."""
    steps: list[dict[str, Any]] = []
    for i, raw_line in enumerate(pseudocode.strip().split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            continue
        step = _parse_one(line, i)
        if step:
            steps.append(step)
    return steps


def _parse_one(line: str, line_num: int) -> dict[str, Any] | None:
    common = {"raw": line, "line": line_num}

    # 点击 / 文字点击 / CLICK
    m = re.match(r'(?:点击|文字点击|CLICK)\s+[\'\"](.+?)[\'\"]', line)
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

    # 等待 / WAIT (e.g. 等待 2秒, 等待 5 秒, wait 3)
    m = re.match(r'(?:等待|WAIT|wait)\s+(\d+(?:\.\d+)?)\s*秒?', line)
    if m:
        return {**common, "type": "wait", "duration": float(m.group(1))}

    # 滚动 / SCROLL (e.g. 滚动 down 3, 滚动 up)
    m = re.match(r'(?:滚动|SCROLL)\s+(up|down|left|right)(?:\s+(\d+))?', line)
    if m:
        return {**common, "type": "scroll", "direction": m.group(1),
                "amount": int(m.group(2)) if m.group(2) else 3}

    # 运行命令 / RUN (e.g. 运行命令 "echo hello" 超时=10)
    m = re.match(r'(?:运行命令|RUN|run)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        timeout = 30
        tm = re.search(r'超时[=:](\d+)', line)
        if tm:
            timeout = int(tm.group(1))
        return {**common, "type": "run_command", "command": m.group(1), "timeout": timeout}

    # 启动 / 打开 / LAUNCH / OPEN (e.g. 启动 "notepad.exe")
    m = re.match(r'(?:启动|打开|LAUNCH|OPEN)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "launch", "target": m.group(1)}

    # 聚焦 / 焦点 / FOCUS (e.g. 聚焦 "记事本")
    m = re.match(r'(?:聚焦|焦点|FOCUS)\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "focus", "title": m.group(1)}

    # 截图 / SCREENSHOT
    m = re.match(r'(?:截图|SCREENSHOT)', line)
    if m:
        return {**common, "type": "screenshot"}

    # 如果 ... 否则 (condition) — complex, mark as condition
    if re.match(r'如果\s+', line):
        return {**common, "type": "condition"}

    # 循环 (loop)
    m = re.match(r'循环\s+(\d+)\s*次', line)
    if m:
        return {**common, "type": "loop", "count": int(m.group(1))}

    # 子流程 (sub_flow)
    m = re.match(r'子流程\s+[\'\"](.+?)[\'\"]', line)
    if m:
        return {**common, "type": "sub_flow", "name": m.group(1)}

    # Fallback: treat as unknown
    return {**common, "type": "unknown"}
