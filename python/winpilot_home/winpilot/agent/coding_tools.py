"""Coding sub-agent tools: deterministic filesystem / repo operations.

Separate from the GUI agent's tools.py (which stays lean) — the coding agent
gets real file-editing primitives instead of driving an editor through the
screen. All path tools are confined to a workspace root; run_shell reuses the
existing shell safety guard.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..utils.log import BUS, logger

_MAX_READ_CHARS = 20000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv",
              "venv", ".next", ".cache", "target", "bin", "obj"}
_TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c",
             ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".json", ".yaml", ".yml",
             ".toml", ".md", ".txt", ".html", ".css", ".scss", ".sh", ".ps1",
             ".xml", ".sql", ".vue", ".svelte", ".kt", ".swift", ".gradle", ".cfg",
             ".ini", ".env", ".dockerfile", ".makefile"}


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


def coding_tool_schemas() -> list[dict]:
    """OpenAI tools for the coding sub-agent."""
    return [
        _tool("list_dir", "列出目录下的文件/子目录（自动跳过 node_modules/.git/dist 等）。不传=工作区根",
              {"path": {"type": "string", "description": "相对工作区的目录，省略=根目录"}}),
        _tool("read_file", "读取文本文件内容（带行号）。大文件可用 start/count 分段读",
              {"path": {"type": "string"}, "start": {"type": "integer", "description": "起始行(从1开始)，可选"},
               "count": {"type": "integer", "description": "读取行数，可选"}}, ["path"]),
        _tool("search_text", "在工作区内全文搜索（子串或正则），返回 文件:行号:内容。找定义/用法/报错位置首选",
              {"query": {"type": "string"}, "glob": {"type": "string", "description": "限定文件名通配，如 *.py，可选"},
               "regex": {"type": "boolean", "description": "query 是否按正则，默认false"}}, ["query"]),
        _tool("write_file", "写入/覆盖整个文件（自动建目录）。新建文件或整体重写时用",
              {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        _tool("apply_patch", "对文件做精确查找替换：把 find 唯一匹配的片段替换成 replace。"
              "find 必须在文件中【唯一】出现（多处或0处会报错，请带足够上下文）。小改首选",
              {"path": {"type": "string"}, "find": {"type": "string", "description": "要替换的原文片段(需唯一)"},
               "replace": {"type": "string", "description": "替换后的内容"}}, ["path", "find", "replace"]),
        _tool("run_shell", "在工作区执行命令并返回输出（构建/测试/git/运行）。默认 PowerShell；不支持 && 用 ; 分隔",
              {"command": {"type": "string"}, "cwd": {"type": "string", "description": "相对工作区的子目录，可选"},
               "shell": {"type": "string", "enum": ["powershell", "cmd"]},
               "timeout_s": {"type": "number", "description": "默认60"}}, ["command"]),
        _tool("done", "任务完成时调用（建议附上你做了什么、如何验证的）", {"summary": {"type": "string"}}, ["summary"]),
        _tool("fail", "确认无法完成时调用", {"reason": {"type": "string"}}, ["reason"]),
    ]


class CodingResult:
    def __init__(self, text: str, terminal: str | None = None, summary: str = "") -> None:
        self.text = text
        self.terminal = terminal     # None | 'done' | 'fail'
        self.summary = summary       # one-line fold


class CodingSession:
    """Dispatches coding tools against a confined workspace root."""

    def __init__(self, workspace: Path, stop_event=None) -> None:
        self.workspace = Path(workspace).resolve()
        self.stop_event = stop_event

    # ----------------------------------------------------------- path safety
    def _resolve(self, path: str) -> Path:
        raw = Path(path)
        p = (raw if raw.is_absolute() else self.workspace / raw).resolve()
        try:
            p.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"路径超出工作区: {path}（工作区根 {self.workspace}）") from None
        return p

    @staticmethod
    def _fmt(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    def dispatch(self, name: str, args: dict[str, Any]) -> CodingResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return CodingResult(f"未知工具: {name}")
        try:
            return handler(args)
        except Exception as exc:  # tools must never crash the loop
            logger.exception("coding 工具 %s 异常", name)
            return CodingResult(f"工具 {name} 执行出错: {exc}")

    # ----------------------------------------------------------------- tools
    def _t_list_dir(self, args: dict) -> CodingResult:
        base = self._resolve(str(args.get("path") or "."))
        if not base.exists():
            return CodingResult(f"目录不存在: {args.get('path')}")
        entries = []
        for child in sorted(base.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.is_dir():
                if child.name in _SKIP_DIRS:
                    continue
                entries.append(f"{child.name}/")
            else:
                entries.append(child.name)
        rel = base.relative_to(self.workspace)
        body = f"{rel or '.'}:\n" + "\n".join(entries[:300])
        return CodingResult(body, summary=f"[已折叠] list_dir {len(entries)}项")

    def _t_read_file(self, args: dict) -> CodingResult:
        p = self._resolve(str(args["path"]))
        if not p.is_file():
            return CodingResult(f"文件不存在: {args['path']}")
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return CodingResult(f"非文本文件无法读取: {args['path']}")
        lines = text.splitlines()
        start = max(1, int(args.get("start", 1)))
        count = int(args["count"]) if args.get("count") else len(lines)
        chunk = lines[start - 1: start - 1 + count]
        numbered = "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(chunk))
        truncated = ""
        if len(numbered) > _MAX_READ_CHARS:
            numbered = numbered[:_MAX_READ_CHARS]
            truncated = "\n…(已截断，用 start/count 分段读)"
        return CodingResult(numbered + truncated,
                            summary=f"[已折叠] read_file {p.name} {len(chunk)}行")

    def _t_write_file(self, args: dict) -> CodingResult:
        p = self._resolve(str(args["path"]))
        content = str(args.get("content", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        BUS.publish("action", action="write_file", path=str(p.relative_to(self.workspace)),
                    bytes=len(content.encode("utf-8")))
        return CodingResult(self._fmt({"ok": True, "bytes": len(content.encode("utf-8"))}))

    def _t_apply_patch(self, args: dict) -> CodingResult:
        p = self._resolve(str(args["path"]))
        if not p.is_file():
            return CodingResult(f"文件不存在: {args['path']}")
        find = str(args.get("find", ""))
        replace = str(args.get("replace", ""))
        if not find:
            return CodingResult("apply_patch 需要非空 find")
        text = p.read_text(encoding="utf-8")
        n = text.count(find)
        if n == 0:
            return CodingResult("未找到要替换的片段（find 与文件内容不匹配，请先 read_file 确认原文）")
        if n > 1:
            return CodingResult(f"find 在文件中出现 {n} 次（不唯一）——请带上更多上下文使其唯一")
        p.write_text(text.replace(find, replace, 1), encoding="utf-8")
        BUS.publish("action", action="apply_patch", path=str(p.relative_to(self.workspace)))
        return CodingResult(self._fmt({"ok": True, "replaced": 1}))

    def _t_search_text(self, args: dict) -> CodingResult:
        query = str(args["query"])
        use_regex = bool(args.get("regex", False))
        glob = str(args.get("glob") or "")
        try:
            pat = re.compile(query) if use_regex else None
        except re.error as exc:
            return CodingResult(f"正则错误: {exc}")
        hits: list[str] = []
        for path in self.workspace.rglob("*"):
            if len(hits) >= 200:
                hits.append("…(结果过多，已截断)")
                break
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in _TEXT_EXT:
                continue
            if glob and not path.match(glob):
                continue
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    found = pat.search(line) if pat else (query in line)
                    if found:
                        rel = path.relative_to(self.workspace)
                        hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 200:
                            break
            except (UnicodeDecodeError, OSError):
                continue
        return CodingResult("\n".join(hits) if hits else f'未找到 "{query}"',
                            summary=f"[已折叠] search_text {len(hits)}处")

    def _t_run_shell(self, args: dict) -> CodingResult:
        from ..action import shell
        cwd = self._resolve(str(args["cwd"])) if args.get("cwd") else self.workspace
        result = shell.run(str(args["command"]),
                           shell=str(args.get("shell", "powershell")),
                           timeout_s=float(args.get("timeout_s", 60)),
                           cwd=str(cwd))
        return CodingResult(self._fmt(result))

    def _t_done(self, args: dict) -> CodingResult:
        return CodingResult(f"完成: {args.get('summary', '')}", terminal="done")

    def _t_fail(self, args: dict) -> CodingResult:
        return CodingResult(f"失败: {args.get('reason', '')}", terminal="fail")
