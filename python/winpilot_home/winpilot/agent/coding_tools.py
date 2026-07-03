"""Coding Runtime tools: deterministic filesystem / repo operations.

These tools are now used by the single WinPilot AgentRunner as an optional
capability, not by a separately planned coding sub-agent. All path tools are
confined to a workspace root; run_shell reuses the existing shell safety guard.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
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
    """Legacy full schema set for Coding Runtime tools.

    The unified AgentRunner uses agent.tool_schemas with coding_runtime=True so
    tool names are not duplicated. This function remains for compatibility with
    older tests and external imports.
    """
    return [
        _tool("list_dir", "列出目录下的文件/子目录（自动跳过 node_modules/.git/dist 等）。不传=工作区根",
              {"path": {"type": "string", "description": "相对工作区的目录，省略=根目录"}}),
        _tool("read_file", "读取文本文件内容（带行号）。大文件可用 start/count 分段读",
              {"path": {"type": "string"}, "start": {"type": "integer", "description": "起始行(从1开始)，可选"},
               "count": {"type": "integer", "description": "读取行数，可选"}}, ["path"]),
        _tool("search_text", "在工作区内全文搜索（子串或正则），返回 文件:行号:内容。找定义/用法/报错位置首选",
              {"query": {"type": "string"}, "glob": {"type": "string", "description": "限定文件名通配，如 *.py，可选"},
               "regex": {"type": "boolean", "description": "query 是否按正则，默认false"}}, ["query"]),
        _tool("github_search", "在 GitHub 上搜索开源仓库。用户只给项目名/开源项目名但没给 URL 时必须先用它找 clone_url，不要在本地目录里瞎找",
              {"query": {"type": "string", "description": "仓库名/关键词，如 cc-haha"},
               "max_results": {"type": "integer", "description": "默认5，最多10"}}, ["query"]),
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


def coding_runtime_tool_names(include_terminal: bool = False) -> set[str]:
    names = {
        "list_dir",
        "read_file",
        "search_text",
        "github_search",
        "write_file",
        "apply_patch",
        "run_shell",
    }
    if include_terminal:
        names.update({"done", "fail"})
    return names


class CodingSession:
    """Dispatches Coding Runtime tools against a confined workspace root."""

    def __init__(self, workspace: Path, stop_event=None, task: str = "") -> None:
        self.workspace = Path(workspace).resolve()
        self.stop_event = stop_event
        self.task = task or ""

    # ----------------------------------------------------------- path safety
    def _resolve(self, path: str) -> Path:
        raw = Path(path)
        p = (raw if raw.is_absolute() else self.workspace / raw).resolve()
        try:
            p.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"路径超出工作区: {path}（工作区根 {self.workspace}）") from None
        return p

    def _rel_display(self, path: Path) -> str:
        """Stable workspace-relative path text for model-facing tool output."""
        rel = path.relative_to(self.workspace)
        return "." if str(rel) == "." else rel.as_posix()

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

    def _is_sensitive_config_path(self, path: Path) -> bool:
        name = path.name.lower()
        parts = {part.lower() for part in path.parts}
        if name in {".env", ".env.local", ".env.production", ".env.development"}:
            return True
        if name.endswith((".key", ".pem", ".p12", ".pfx")):
            return True
        sensitive_words = ("secret", "credential", "credentials", "token", "apikey", "api_key")
        if any(word in name for word in sensitive_words):
            return True
        return bool(parts & {"secrets", "credentials"})

    def _task_allows_sensitive_config_write(self) -> bool:
        task = self.task.lower()
        allow_terms = (
            ".env", "env", "api key", "apikey", "token", "secret", "credential",
            "credentials", "configure", "configuration", "config", "settings",
            "配置", "设置", "密钥", "令牌", "凭据", "写入", "修改", "更新",
        )
        return any(term in task for term in allow_terms)

    def _guard_sensitive_config_write(self, path: Path) -> CodingResult | None:
        if not self._is_sensitive_config_path(path):
            return None
        if self._task_allows_sensitive_config_write():
            return None
        rel = path.relative_to(self.workspace)
        return CodingResult(self._fmt({
            "ok": False,
            "blocked": True,
            "path": str(rel),
            "reason": (
                "Refusing to modify a sensitive configuration/secret file because "
                "the user did not explicitly ask to configure or edit credentials. "
                "Verify runnable commands first; if credentials are missing, report "
                "the required variables instead of rewriting the file."
            ),
        }))

    def _task_allows_project_edit(self) -> bool:
        task = self.task.lower()
        if not task.strip():
            return True
        edit_terms = (
            "create", "write", "modify", "edit", "fix", "patch", "refactor",
            "implement", "add", "remove", "delete", "configure", "config",
            "创建", "新建", "写", "修改", "修复", "改进", "重构", "实现",
            "添加", "删除", "配置", "设置",
        )
        return any(term in task for term in edit_terms)

    def _task_is_run_only(self) -> bool:
        task = self.task.lower()
        run_terms = (
            "clone", "github", "open-source", "opensource", "run", "start",
            "launch", "install", "setup", "verify", "test",
            "\u514b\u9686", "\u5f00\u6e90", "\u8dd1\u8d77\u6765", "\u8fd0\u884c",
            "\u542f\u52a8", "\u6253\u5f00", "\u5b89\u88c5", "\u9a8c\u8bc1",
        )
        return any(term in task for term in run_terms)

    def _guard_unrequested_project_edit(self, path: Path) -> CodingResult | None:
        if self._task_allows_project_edit():
            return None
        if not self._task_is_run_only():
            return None
        rel = path.relative_to(self.workspace)
        return CodingResult(self._fmt({
            "ok": False,
            "blocked": True,
            "path": str(rel),
            "reason": (
                "Refusing to edit or create project files because this task is "
                "a clone/run/verification task, not an implementation or "
                "configuration request. Use existing commands and report how to "
                "run the project instead."
            ),
        }))

    def _guard_sensitive_config_shell(self, command: str) -> CodingResult | None:
        if self._task_allows_sensitive_config_write():
            return None
        cmd = command.lower()
        if not re.search(r"(?<![\w.-])\.env(?:\.[\w.-]+)?(?![\w.-])", cmd):
            return None
        write_patterns = (
            r"\b(cp|copy|copy-item|mv|move|move-item|ren|rename|rename-item)\b",
            r"\b(set-content|add-content|out-file|new-item|ni)\b",
            r"\b(git\s+checkout|git\s+restore)\b",
            r">\s*[^;&|]*\.env(?:\.[\w.-]+)?",
        )
        if not any(re.search(pattern, cmd) for pattern in write_patterns):
            return None
        return CodingResult(self._fmt({
            "ok": False,
            "blocked": True,
            "command": command,
            "reason": (
                "Refusing to run a shell command that appears to write a sensitive "
                "configuration/secret file (.env). The user did not explicitly ask "
                "to configure credentials; use read-only verification and report "
                "missing variables instead."
            ),
        }))

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
        body = f"{self._rel_display(base)}:\n" + "\n".join(entries[:300])
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
        guard = self._guard_unrequested_project_edit(p)
        if guard is not None:
            return guard
        guard = self._guard_sensitive_config_write(p)
        if guard is not None:
            return guard
        content = str(args.get("content", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        BUS.publish("action", action="write_file", path=self._rel_display(p),
                    bytes=len(content.encode("utf-8")))
        return CodingResult(self._fmt({"ok": True, "bytes": len(content.encode("utf-8"))}))

    def _t_apply_patch(self, args: dict) -> CodingResult:
        p = self._resolve(str(args["path"]))
        if not p.is_file():
            return CodingResult(f"文件不存在: {args['path']}")
        guard = self._guard_unrequested_project_edit(p)
        if guard is not None:
            return guard
        guard = self._guard_sensitive_config_write(p)
        if guard is not None:
            return guard
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
        BUS.publish("action", action="apply_patch", path=self._rel_display(p))
        return CodingResult(self._fmt({"ok": True, "replaced": 1}))

    def _t_search_text(self, args: dict) -> CodingResult:
        query = str(args["query"])
        use_regex = bool(args.get("regex", False))
        glob = str(args.get("glob") or "")
        base = self._resolve(str(args.get("path") or "."))
        if not base.is_dir():
            return CodingResult(f"搜索目录不存在: {args.get('path') or '.'}")
        try:
            pat = re.compile(query) if use_regex else None
        except re.error as exc:
            return CodingResult(f"正则错误: {exc}")
        hits: list[str] = []
        for path in base.rglob("*"):
            if len(hits) >= 200:
                hits.append("…(结果过多，已截断)")
                break
            try:
                rel_parts = path.relative_to(self.workspace).parts
            except ValueError:
                continue
            if not path.is_file() or any(part in _SKIP_DIRS for part in rel_parts):
                continue
            if path.suffix.lower() not in _TEXT_EXT:
                continue
            if glob and not path.match(glob):
                continue
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    found = pat.search(line) if pat else (query in line)
                    if found:
                        hits.append(f"{self._rel_display(path)}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 200:
                            break
            except (UnicodeDecodeError, OSError):
                continue
        return CodingResult("\n".join(hits) if hits else f'未找到 "{query}"',
                            summary=f"[已折叠] search_text {len(hits)}处")

    def _t_github_search(self, args: dict) -> CodingResult:
        query = str(args["query"]).strip()
        if not query:
            return CodingResult("github_search 需要 query")
        max_results = max(1, min(10, int(args.get("max_results", 5) or 5)))
        # Search exact-ish repo names first, then fall back to a broader query.
        queries = [f"{query} in:name", query] if " " not in query else [query]
        last_error = ""
        for q in queries:
            url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
                "q": q,
                "sort": "stars",
                "order": "desc",
                "per_page": str(max_results),
            })
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "WinPilot-CodingRuntime",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                last_error = str(exc)
                continue
            items = data.get("items") or []
            if not items:
                continue
            rows = []
            for i, repo in enumerate(items[:max_results], 1):
                rows.append({
                    "rank": i,
                    "full_name": repo.get("full_name", ""),
                    "html_url": repo.get("html_url", ""),
                    "clone_url": repo.get("clone_url", ""),
                    "stars": repo.get("stargazers_count", 0),
                    "language": repo.get("language", ""),
                    "description": repo.get("description", ""),
                    "updated_at": repo.get("updated_at", ""),
                })
            return CodingResult(
                self._fmt({"ok": True, "query": q, "results": rows}),
                summary=f"[已折叠] github_search {len(rows)}个仓库",
            )
        if last_error:
            return CodingResult(self._fmt({"ok": False, "error": last_error}))
        return CodingResult(self._fmt({"ok": True, "query": query, "results": []}))

    def _t_run_shell(self, args: dict) -> CodingResult:
        from ..action import shell
        cwd = self._resolve(str(args["cwd"])) if args.get("cwd") else self.workspace
        command = str(args["command"])
        guard = self._guard_sensitive_config_shell(command)
        if guard is not None:
            return guard
        result = shell.run(command,
                           shell=str(args.get("shell", "powershell")),
                           timeout_s=float(args.get("timeout_s", 60)),
                           cwd=str(cwd))
        return CodingResult(self._fmt(result))

    def _t_done(self, args: dict) -> CodingResult:
        return CodingResult(f"完成: {args.get('summary', '')}", terminal="done")

    def _t_fail(self, args: dict) -> CodingResult:
        return CodingResult(f"失败: {args.get('reason', '')}", terminal="fail")
