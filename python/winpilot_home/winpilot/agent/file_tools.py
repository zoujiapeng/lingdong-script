"""File-management tool handlers for the GUI agent.

These are intentionally small, generic filesystem helpers exposed to the main
agent. They are separate from desktop GUI handlers so `tools.py` stays focused
on window/session state and screen interaction.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..utils.log import BUS
from .tool_result import ToolResult

_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
    ".next",
    ".cache",
    "target",
    "bin",
    "obj",
}


class FileToolMixin:
    def _t_list_dir(self, args: dict) -> ToolResult:
        path = str(args.get("path") or ".")
        p = Path(path).resolve()
        if not p.exists():
            return ToolResult(f"目录不存在: {path}")
        if not p.is_dir():
            return ToolResult(f"不是目录: {path}")
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.is_dir():
                if child.name in _SKIP_DIRS:
                    continue
                entries.append(f"  {child.name}/")
            else:
                entries.append(f"  {child.name}")
        body = f"{p}:\n" + "\n".join(entries[:300])
        return ToolResult(body, summary=f"[已折叠] list_dir {len(entries)}项")

    def _t_read_file(self, args: dict) -> ToolResult:
        p = Path(str(args["path"])).resolve()
        if not p.is_file():
            return ToolResult(f"文件不存在: {args['path']}")
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(f"非文本文件: {args['path']}")
        lines = text.splitlines()
        start = max(1, int(args.get("start", 1)))
        count = int(args["count"]) if args.get("count") else len(lines)
        chunk = lines[start - 1: start - 1 + count]
        numbered = "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(chunk))
        if len(numbered) > 20000:
            numbered = numbered[:20000] + "\n...(已截断)"
        return ToolResult(numbered, summary=f"[已折叠] read_file {p.name} {len(chunk)}行")

    def _t_write_file(self, args: dict) -> ToolResult:
        p = Path(str(args["path"])).resolve()
        content = str(args.get("content", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        BUS.publish("action", action="write_file", path=str(p), bytes=len(content.encode("utf-8")))
        return ToolResult(
            self._fmt({"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))})
        )

    def _t_search_text(self, args: dict) -> ToolResult:
        query = str(args.get("query", ""))
        search_path = Path(str(args.get("path", "."))).resolve()
        glob_pattern = str(args.get("glob", "*"))
        is_regex = bool(args.get("regex", False))
        if not search_path.is_dir():
            return ToolResult(f"搜索目录不存在: {search_path}")
        matches = []
        pattern = re.compile(query) if is_regex else None
        try:
            for f in sorted(search_path.rglob(glob_pattern)):
                if any(skip in f.parts for skip in _SKIP_DIRS):
                    continue
                if not f.is_file():
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    hit = bool(pattern.search(line)) if is_regex else (query in line)
                    if hit:
                        rel = f.relative_to(search_path)
                        matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(matches) >= 50:
                            break
                if len(matches) >= 50:
                    break
        except Exception as exc:
            return ToolResult(f"搜索异常: {exc}")
        return ToolResult(
            "\n".join(matches) or f'未找到 "{query}" 的匹配',
            summary=f"[已折叠] search_text {len(matches)}个匹配",
        )
