#!/usr/bin/env python3
"""Generate and verify the exhaustive, source-backed project file inventory.

The human-written project guide explains architecture and behavior.  This
script supplies the mechanically verifiable appendix: every tracked project
file, every code/config file, basic static symbols/imports, size, line count,
and content hash.  It intentionally reads Git's NUL-delimited path stream so
Chinese names, spaces, and other special paths cannot disappear from the
inventory.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "project-guide" / "generated-file-inventory.md"
SELF_PATH = "scripts/generate_project_guide.py"
OUTPUT_PATH = "docs/project-guide/generated-file-inventory.md"
HUMAN_REFERENCE_PATHS = (
    "docs/project-guide/05-file-reference-typescript-electron.md",
    "docs/project-guide/06-file-reference-winpilot-python.md",
    "docs/project-guide/07-file-reference-support-tests.md",
)

CODE_EXTENSIONS = {
    ".ahk",
    ".bat",
    ".cjs",
    ".cmd",
    ".css",
    ".dsl",
    ".flow",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".mts",
    ".ps1",
    ".py",
    ".pyi",
    ".scss",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

CODE_NAMES = {
    "Dockerfile",
    "Makefile",
    "package.json",
    "pytest.ini",
    "requirements.txt",
}

TEXT_EXTENSIONS = CODE_EXTENSIONS | {
    ".gitattributes",
    ".gitignore",
    ".md",
    ".txt",
}

TS_IMPORT_RE = re.compile(
    r"(?:\bfrom\s+|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)"
    r"['\"]([^'\"]+)['\"]"
)
TS_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?(?:class|function|interface|type|enum|const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
IPC_RE = re.compile(
    r"\b(?:ipcMain\.(?:handle|on)|ipcRenderer\.(?:invoke|send|on))"
    r"\s*\(\s*['\"]([^'\"]+)['\"]"
)
ROUTE_RE = re.compile(
    r"@(?:app|router)\.(?:get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]"
)
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
HTML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class FileFacts:
    path: str
    category: str
    language: str
    size_bytes: int
    line_count: int | None
    sha256: str
    summary: str
    symbols: tuple[str, ...]
    dependencies: tuple[str, ...]
    endpoints: tuple[str, ...]
    parse_note: str


def _git_paths() -> list[str]:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    paths = [
        item.decode("utf-8", "surrogateescape")
        for item in raw.split(b"\0")
        if item
    ]
    if SELF_PATH not in paths and (ROOT / SELF_PATH).is_file():
        paths.append(SELF_PATH)

    guide_dir = ROOT / "docs" / "project-guide"
    if guide_dir.is_dir():
        for path in guide_dir.glob("*.md"):
            rel = path.relative_to(ROOT).as_posix()
            if rel == OUTPUT_PATH or path.name.startswith("_draft-"):
                continue
            if rel not in paths:
                paths.append(rel)

    return sorted(set(paths))


def _is_code_or_config(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.suffix.lower() in CODE_EXTENSIONS or pure.name in CODE_NAMES


def _category(path: str) -> str:
    pure = PurePosixPath(path)
    parts = pure.parts
    name = pure.name.lower()
    suffix = pure.suffix.lower()

    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".exe"}:
        return "asset-or-binary"
    if suffix == ".md" or name in {"readme", "license"}:
        return "documentation"
    if (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or ".test." in name
        or name.endswith("_test.py")
    ):
        return "test"
    if (
        "eval_targets" in parts
        or "e2e" in parts
        or name.endswith("_runner.py")
        or name.endswith("_harness.py")
    ):
        return "e2e-or-harness"
    if suffix in {".flow", ".dsl"} or "templates" in parts or "fixtures" in parts:
        return "fixture-or-template"
    if (
        suffix in {".json", ".yaml", ".yml", ".ini", ".toml"}
        or name.startswith("tsconfig")
        or name in {"package.json", "requirements.txt", "pytest.ini"}
        or path.startswith(".github/")
    ):
        return "configuration"
    if path.startswith("scripts/") or suffix in {".ps1", ".bat", ".cmd", ".sh"}:
        return "developer-script"
    if path.startswith("src/"):
        return "electron-or-typescript-product"
    if path.startswith("python/winpilot_home/winpilot/"):
        return "winpilot-product"
    if suffix in CODE_EXTENSIONS:
        return "supporting-code"
    return "project-asset"


def _language(path: str) -> str:
    pure = PurePosixPath(path)
    suffix = pure.suffix.lower()
    mapping = {
        ".ahk": "AutoHotkey",
        ".bat": "Batch",
        ".cjs": "CommonJS",
        ".cmd": "Batch",
        ".css": "CSS",
        ".dsl": "Flow DSL",
        ".flow": "FlowScript JSON",
        ".gif": "GIF image",
        ".htm": "HTML",
        ".html": "HTML",
        ".ico": "Windows icon",
        ".ini": "INI",
        ".java": "Java",
        ".jpeg": "JPEG image",
        ".jpg": "JPEG image",
        ".js": "JavaScript",
        ".json": "JSON",
        ".jsx": "React JavaScript",
        ".md": "Markdown",
        ".mjs": "ES module JavaScript",
        ".mts": "TypeScript module",
        ".png": "PNG image",
        ".ps1": "PowerShell",
        ".py": "Python",
        ".pyi": "Python typing stub",
        ".scss": "SCSS",
        ".sh": "Shell",
        ".toml": "TOML",
        ".ts": "TypeScript",
        ".tsx": "React TypeScript",
        ".txt": "Text",
        ".yaml": "YAML",
        ".yml": "YAML",
    }
    if pure.name == "requirements.txt":
        return "Python requirements"
    if pure.name == "pytest.ini":
        return "Pytest configuration"
    return mapping.get(suffix, "Binary or extensionless project file")


def _decode_text(data: bytes, path: str) -> tuple[str | None, str]:
    if b"\x00" in data and PurePosixPath(path).suffix.lower() not in {".flow", ".json"}:
        return None, "binary"
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return None, "non-UTF-8 or binary"


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        value = line.strip().lstrip("#/;*").strip()
        if value:
            return value[:240]
    return ""


def _python_facts(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [], [], "", f"Python AST parse failed at line {exc.lineno}: {exc.msg}"

    symbols: list[str] = []
    dependencies: list[str] = []
    endpoints: list[str] = []
    constants: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
            for decorator in getattr(node, "decorator_list", []):
                rendered = _render_python_route(decorator)
                if rendered:
                    endpoints.append(rendered)
        elif isinstance(node, ast.Import):
            dependencies.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            dependencies.append(prefix + (node.module or ""))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append(target.id)

    doc = ast.get_docstring(tree, clean=True) or ""
    summary = doc.splitlines()[0].strip() if doc else ""
    symbols.extend(f"const:{name}" for name in constants[:8])
    return symbols, dependencies, endpoints, summary, ""


def _render_python_route(node: ast.expr) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return ""
    if node.func.attr not in {"get", "post", "put", "patch", "delete", "websocket"}:
        return ""
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return ""
    path = node.args[0].value
    if not isinstance(path, str):
        return ""
    return f"{node.func.attr.upper()} {path}"


def _json_facts(text: str, path: str) -> tuple[list[str], list[str], str, str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [], "", f"JSON parse failed at line {exc.lineno}: {exc.msg}"

    symbols: list[str] = []
    dependencies: list[str] = []
    summary = ""
    if isinstance(value, dict):
        symbols = [f"key:{key}" for key in list(value)[:16]]
        if isinstance(value.get("name"), str):
            summary = str(value["name"])
        if isinstance(value.get("description"), str):
            summary = str(value["description"])
        scripts = value.get("scripts")
        if isinstance(scripts, dict):
            symbols.extend(f"script:{key}" for key in list(scripts)[:16])
        deps = value.get("dependencies")
        if isinstance(deps, dict):
            dependencies.extend(str(key) for key in deps)
        dev_deps = value.get("devDependencies")
        if isinstance(dev_deps, dict):
            dependencies.extend(str(key) for key in dev_deps)
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            kinds = Counter(
                str(item.get("type") or item.get("kind") or "unknown")
                for item in nodes
                if isinstance(item, dict)
            )
            symbols.extend(f"node:{kind}×{count}" for kind, count in kinds.items())
    elif isinstance(value, list):
        symbols = [f"list-items:{len(value)}"]
    return symbols, dependencies, summary, ""


def _ts_facts(text: str) -> tuple[list[str], list[str], list[str], str]:
    symbols = list(dict.fromkeys(TS_SYMBOL_RE.findall(text)))
    dependencies = list(dict.fromkeys(TS_IMPORT_RE.findall(text)))
    endpoints = list(dict.fromkeys(f"IPC {name}" for name in IPC_RE.findall(text)))
    summary = _first_nonempty_line(text)
    if summary.startswith(("import ", "export ", "const ", "type ", "interface ")):
        summary = ""
    return symbols, dependencies, endpoints, summary


def _png_dimensions(data: bytes) -> str:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return f"PNG {width}x{height}"
    return ""


def _fallback_summary(path: str, category: str, language: str) -> str:
    pure = PurePosixPath(path)
    stem = pure.stem.replace("_", " ").replace("-", " ")
    if pure.name == "__init__.py":
        package = ".".join(pure.parent.parts)
        return f"Python 包 {package} 的初始化与公开边界。"
    if category == "test":
        return f"验证 {stem} 对应的行为、回归或合同。"
    if category == "e2e-or-harness":
        return f"为 {stem} 提供端到端运行、靶场或外部 oracle 支撑。"
    if category == "fixture-or-template":
        return f"{stem} 的流程、模板或测试 fixture 数据。"
    if category == "configuration":
        return f"配置 {stem} 对应的构建、运行、依赖或测试行为。"
    if category == "documentation":
        return f"说明 {stem} 对应的项目约定、架构或使用方式。"
    if category == "asset-or-binary":
        return f"项目使用的 {language} 资产。"
    return f"实现或支撑 {stem} 模块；精确公开面见下列静态符号与依赖。"


def _facts(path: str) -> FileFacts:
    full = ROOT / Path(path)
    data = full.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    text, parse_note = _decode_text(data, path)
    category = _category(path)
    language = _language(path)
    line_count = None if text is None else len(text.splitlines())
    symbols: list[str] = []
    dependencies: list[str] = []
    endpoints: list[str] = []
    summary = ""

    suffix = PurePosixPath(path).suffix.lower()
    if text is not None and suffix in {".py", ".pyi"}:
        symbols, dependencies, endpoints, summary, python_note = _python_facts(text)
        parse_note = python_note or parse_note
    elif text is not None and suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts"}:
        symbols, dependencies, endpoints, summary = _ts_facts(text)
    elif text is not None and suffix in {".json", ".flow"}:
        symbols, dependencies, summary, json_note = _json_facts(text, path)
        parse_note = json_note or parse_note
    elif text is not None and suffix == ".md":
        match = HEADING_RE.search(text)
        summary = match.group(1).strip() if match else _first_nonempty_line(text)
    elif text is not None and suffix in {".html", ".htm"}:
        match = HTML_TITLE_RE.search(text)
        summary = re.sub(r"\s+", " ", match.group(1)).strip() if match else _first_nonempty_line(text)
    elif text is not None:
        summary = _first_nonempty_line(text)

    dimensions = _png_dimensions(data)
    if dimensions:
        summary = dimensions
    if not summary:
        summary = _fallback_summary(path, category, language)

    # AST already guarantees Python import syntax.  Preserve leading dots so
    # package-local dependencies such as ``from . import x`` do not disappear
    # from an inventory whose purpose is exact source accounting.
    clean_dependencies = [dep for dep in dependencies if dep]

    return FileFacts(
        path=path,
        category=category,
        language=language,
        size_bytes=len(data),
        line_count=line_count,
        sha256=digest,
        summary=summary.replace("\n", " ")[:300],
        symbols=tuple(dict.fromkeys(symbols)),
        dependencies=tuple(dict.fromkeys(clean_dependencies)),
        endpoints=tuple(dict.fromkeys(endpoints)),
        parse_note=parse_note,
    )


def _compact(values: Iterable[str], *, limit: int = 14) -> str:
    items = list(values)
    shown = items[:limit]
    rendered = ", ".join(f"`{item}`" for item in shown)
    if len(items) > limit:
        rendered += f"，另有 {len(items) - limit} 项"
    return rendered or "—"


def _render(paths: list[str]) -> str:
    facts = [_facts(path) for path in paths if path != OUTPUT_PATH]
    code = [item for item in facts if _is_code_or_config(item.path)]
    categories = Counter(item.category for item in facts)
    languages = Counter(item.language for item in code)

    lines = [
        "# 全项目逐文件机械事实索引",
        "",
        "> 本文件由 `scripts/generate_project_guide.py` 从 `git ls-files -z` 和当前讲解文档生成。",
        "> 它保证路径、行数、字节数、SHA-256、Python AST、TS/JS 明确声明与直接依赖来自真实源码。",
        "> 语义架构、调用关系和通俗伪代码见同目录人工审计章节；本索引负责零遗漏与防漂移。",
        "",
        "## 覆盖摘要",
        "",
        f"- 被索引文件：**{len(facts)}**",
        f"- 代码/测试/配置口径：**{len(code)}**",
        f"- 文本文件总行数：**{sum(item.line_count or 0 for item in facts):,}**",
        f"- 总字节数：**{sum(item.size_bytes for item in facts):,}**",
        "- 生成文件自身被排除，避免自引用 hash；生成脚本自身被纳入代码覆盖。",
        "",
        "### 分类",
        "",
        "| 分类 | 文件数 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in sorted(categories.items()))
    lines.extend([
        "",
        "### 代码与配置语言",
        "",
        "| 语言/格式 | 文件数 |",
        "| --- | ---: |",
    ])
    lines.extend(f"| {name} | {count} |" for name, count in sorted(languages.items()))
    lines.extend(["", "## 逐文件事实", ""])

    current_top = ""
    for item in facts:
        top = item.path.split("/", 1)[0]
        if top != current_top:
            current_top = top
            lines.extend([f"## `{top}`", ""])
        marker = item.path.replace("--", "- -")
        line_text = "binary" if item.line_count is None else str(item.line_count)
        lines.extend([
            f"<!-- PROJECT-FILE: {marker} -->",
            f"### `{item.path}`",
            "",
            f"[打开文件](<../../{item.path}>)",
            "",
            f"- 分类/语言：`{item.category}` / {item.language}",
            f"- 规模：{line_text} 行，{item.size_bytes:,} bytes",
            f"- SHA-256：`{item.sha256}`",
            f"- 源码事实摘要：{item.summary}",
            f"- 顶层符号/结构：{_compact(item.symbols)}",
            f"- 直接静态依赖：{_compact(item.dependencies)}",
            f"- API/IPC 入口：{_compact(item.endpoints)}",
        ])
        if item.parse_note:
            lines.append(f"- 解析说明：{item.parse_note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _verify_markers(rendered: str, paths: list[str]) -> None:
    expected = {path for path in paths if path != OUTPUT_PATH and _is_code_or_config(path)}
    marker_re = re.compile(r"^<!-- PROJECT-FILE: (.+) -->$", re.MULTILINE)
    actual_all = marker_re.findall(rendered)
    actual = {path for path in actual_all if _is_code_or_config(path)}
    duplicates = sorted(path for path, count in Counter(actual_all).items() if count != 1)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if duplicates or missing or extra:
        details = [
            f"duplicates={duplicates[:20]}",
            f"missing={missing[:20]}",
            f"extra={extra[:20]}",
        ]
        raise SystemExit("project guide coverage mismatch: " + "; ".join(details))


def _verify_human_reference_coverage(paths: list[str]) -> None:
    """Require every code/config path to occur in a human semantic volume."""

    missing_volumes = [path for path in HUMAN_REFERENCE_PATHS if not (ROOT / path).is_file()]
    if missing_volumes:
        raise SystemExit(f"missing human project-guide volumes: {missing_volumes}")
    reference_text = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in HUMAN_REFERENCE_PATHS
    )
    expected = {
        path
        for path in paths
        if path != OUTPUT_PATH and _is_code_or_config(path)
    }
    missing = sorted(path for path in expected if path not in reference_text)
    if missing:
        raise SystemExit(
            "human project-guide coverage mismatch: "
            f"missing={missing[:40]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed inventory is current")
    args = parser.parse_args(argv)

    paths = _git_paths()
    rendered = _render(paths)
    _verify_markers(rendered, paths)
    _verify_human_reference_coverage(paths)
    if args.check:
        if not OUTPUT.is_file():
            print(f"missing generated inventory: {OUTPUT.relative_to(ROOT)}", file=sys.stderr)
            return 1
        existing = OUTPUT.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                "generated inventory is stale; run "
                "`python scripts/generate_project_guide.py`",
                file=sys.stderr,
            )
            return 1
        print(f"project guide inventory current: {len(paths) - (OUTPUT_PATH in paths)} files")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    code_count = sum(_is_code_or_config(path) for path in paths if path != OUTPUT_PATH)
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: "
        f"{len(paths) - (OUTPUT_PATH in paths)} files, {code_count} code/config"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
