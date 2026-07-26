"""Static boundaries for the pure, default-unreachable vNext kernel."""
from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "winpilot"
VNEXT_ROOT = PACKAGE_ROOT / "vnext"
KERNEL_ROOTS = (
    VNEXT_ROOT / "__init__.py",
    VNEXT_ROOT / "mission",
    VNEXT_ROOT / "evidence",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "cohere",
    "google.genai",
    "groq",
    "mistralai",
    "ollama",
    "together",
    "cv2",
    "numpy",
    "pydantic",
    "win32",
    "winpilot.action",
    "winpilot.agent",
    "winpilot.fast",
    "winpilot.orchestrator",
    "winpilot.server",
    "winpilot.tasking",
    "winpilot.vnext.actor",
    "winpilot.vnext.bridge",
    "winpilot.vnext.capabilities",
    "winpilot.vnext.profiling",
    "winpilot.vnext.runtime",
    "winpilot.vnext.targeting",
    "winpilot.vnext.verification",
)


def _source_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,)
    return tuple(sorted(root.rglob("*.py")))


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    package = module.rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                keep = max(0, len(parts) - node.level + 1)
                prefix = ".".join(parts[:keep])
                imported = ".".join(part for part in (prefix, node.module or "") if part)
            else:
                imported = node.module or ""
            found.append(imported)
    return tuple(found)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def test_kernel_imports_only_stdlib_or_its_own_pure_packages() -> None:
    paths = tuple(
        path
        for root in KERNEL_ROOTS
        for path in _source_files(root)
    )
    violations = [
        f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}"
        for path in paths
        for imported in _imports(path)
        if any(_matches_prefix(imported, prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert violations == []


def test_vnext_root_has_no_eager_or_lazy_imports() -> None:
    root = VNEXT_ROOT / "__init__.py"
    tree = ast.parse(root.read_text(encoding="utf-8"), filename=str(root))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "__getattr__"
        for node in tree.body
    )


def test_existing_product_code_cannot_reach_vnext_by_import() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if VNEXT_ROOT in path.parents:
            continue
        for imported in _imports(path):
            if _matches_prefix(imported, "winpilot.vnext"):
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}"
                )
    assert violations == []
