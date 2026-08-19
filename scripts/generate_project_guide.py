#!/usr/bin/env python3
"""Print a lightweight, on-demand inventory of tracked project files.

The report is intentionally not committed.  Git already owns file identity and
history; this helper only makes paths and a few top-level symbols easy to scan.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXTENSIONS = {".py", ".pyi"}
TYPESCRIPT_EXTENSIONS = {".cjs", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}
JSON_EXTENSIONS = {".json"}
TS_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:class|function|interface|type|enum|const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class FileFact:
    path: str
    category: str
    symbols: tuple[str, ...]
    error: str = ""


def _tracked_paths(prefix: str) -> list[str]:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    normalized_prefix = prefix.replace("\\", "/").lstrip("./")
    paths: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        path = item.decode("utf-8", "surrogateescape")
        if normalized_prefix and not path.startswith(normalized_prefix):
            continue
        if (ROOT / path).is_file():
            paths.append(path)
    return sorted(paths)


def _category(path: str) -> str:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    parts = pure.parts
    if "tests" in parts or name.startswith("test_") or ".test." in name:
        return "test"
    if pure.suffix.lower() == ".md":
        return "documentation"
    if path.startswith("scripts/"):
        return "script"
    if path.startswith("python/winpilot_home/winpilot/"):
        return "winpilot"
    if path.startswith("src/"):
        return "electron"
    if pure.suffix.lower() in {".json", ".toml", ".yaml", ".yml"}:
        return "configuration"
    return "other"


def _python_symbols(text: str) -> tuple[str, ...]:
    tree = ast.parse(text)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(f"def {node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    names.append(target.id)
    return tuple(names)


def _json_symbols(text: str) -> tuple[str, ...]:
    value = json.loads(text)
    if isinstance(value, dict):
        return tuple(str(key) for key in value.keys())
    if isinstance(value, list):
        return (f"list[{len(value)}]",)
    return (type(value).__name__,)


def _fact(path: str) -> FileFact:
    full = ROOT / path
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in PYTHON_EXTENSIONS | TYPESCRIPT_EXTENSIONS | JSON_EXTENSIONS:
        return FileFact(path=path, category=_category(path), symbols=())
    try:
        text = full.read_text(encoding="utf-8-sig")
        if suffix in PYTHON_EXTENSIONS:
            symbols = _python_symbols(text)
        elif suffix in TYPESCRIPT_EXTENSIONS:
            symbols = tuple(dict.fromkeys(TS_SYMBOL_RE.findall(text)))
        else:
            symbols = _json_symbols(text)
        return FileFact(path=path, category=_category(path), symbols=symbols)
    except (OSError, SyntaxError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return FileFact(
            path=path,
            category=_category(path),
            symbols=(),
            error=f"{type(exc).__name__}: {exc}",
        )


def _render(facts: list[FileFact]) -> str:
    lines = [
        "# On-demand project path and symbol report",
        "",
        "> Generated from the current working tree with `git ls-files -z`.",
        "> This report contains no content hashes and is not a committed project artifact.",
        "",
        f"Tracked files shown: **{len(facts)}**",
        "",
    ]
    for fact in facts:
        symbols = ", ".join(f"`{name}`" for name in fact.symbols[:12]) or "—"
        if len(fact.symbols) > 12:
            symbols += f" (+{len(fact.symbols) - 12})"
        lines.append(f"- `{fact.path}` | {fact.category} | {symbols}")
        if fact.error:
            lines.append(f"  - parse note: {fact.error}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default="",
        help="only include tracked paths starting with this repository-relative prefix",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the report to this explicit path instead of stdout",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="scan tracked paths and parse supported source files without requiring a generated file",
    )
    args = parser.parse_args(argv)

    facts = [_fact(path) for path in _tracked_paths(args.prefix)]
    errors = [fact for fact in facts if fact.error]
    if args.check:
        if errors:
            for fact in errors:
                print(f"{fact.path}: {fact.error}", file=sys.stderr)
            return 1
        print(f"project path scan ok: {len(facts)} tracked files")
        return 0

    rendered = _render(facts)
    if args.output is None:
        sys.stdout.write(rendered)
        return 0

    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
