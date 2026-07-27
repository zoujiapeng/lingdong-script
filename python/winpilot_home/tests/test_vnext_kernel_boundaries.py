"""Static boundaries for the pure, default-unreachable vNext kernel."""
from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "winpilot"
VNEXT_ROOT = PACKAGE_ROOT / "vnext"
RUNTIME_ROOT = VNEXT_ROOT / "runtime"
MODEL_ROLES_ROOT = VNEXT_ROOT / "model_roles"
EVIDENCE_PLANE_ROOT = VNEXT_ROOT / "evidence_plane"
KERNEL_ROOTS = (
    VNEXT_ROOT / "__init__.py",
    VNEXT_ROOT / "mission",
    VNEXT_ROOT / "evidence",
    EVIDENCE_PLANE_ROOT,
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

RUNTIME_FILES = (
    "__init__.py",
    "contracts.py",
    "mission_runtime.py",
    "mutation_attempt.py",
    "mutation_permits.py",
    "ports.py",
    "post_action_reconcile.py",
)

RUNTIME_FORBIDDEN_IMPORT_PREFIXES = (
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
    "win32",
    "pyautogui",
    "winpilot.action",
    "winpilot.agent",
    "winpilot.fast",
    "winpilot.integrations",
    "winpilot.modeling",
    "winpilot.orchestrator",
    "winpilot.server",
    "winpilot.tasking",
    "winpilot.visual_runtime",
    "winpilot.vnext.bridge",
    "winpilot.vnext.model_roles",
    "winpilot.vnext.targeting",
)

LIVE_DESKTOP_IMPORT_PREFIXES = (
    "winpilot.action",
    "winpilot.agent",
    "winpilot.utils.screenshot",
    "winpilot.visual_runtime",
)

ATOMIC_DESKTOP_IMPORTS = (
    "winpilot.action.desktop_queue",
    "winpilot.action.raw_device",
    "winpilot.action.replay_policy",
    "winpilot.agent.frame_authority",
    "winpilot.utils.screenshot",
    "winpilot.visual_runtime.frame_observer",
    "winpilot.visual_runtime.normalized_frame_coordinates",
    "winpilot.visual_runtime.submitted_image_coordinates",
)

MODEL_ROLE_FILES = (
    "__init__.py",
    "common.py",
    "visual_actor.py",
    "visual_obligation_verifier.py",
)

EVIDENCE_PLANE_FILES = (
    "__init__.py",
    "contracts.py",
    "plane.py",
)

MODEL_ROLE_ALLOWED_PRODUCT_PREFIXES = (
    "winpilot.modeling",
    "winpilot.vnext.actor",
    "winpilot.vnext.runtime.contracts",
    "winpilot.vnext.verification",
    "winpilot.vnext.model_roles",
)

MODEL_ROLE_FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "cohere",
    "google.genai",
    "groq",
    "mistralai",
    "ollama",
    "together",
    "win32",
    "pyautogui",
    "winpilot.action",
    "winpilot.agent",
    "winpilot.fast",
    "winpilot.integrations",
    "winpilot.orchestrator",
    "winpilot.server",
    "winpilot.tasking",
    "winpilot.visual_runtime",
    "winpilot.vnext.bridge",
    "winpilot.vnext.desktop_adapter",
    "winpilot.vnext.profiling",
    "winpilot.vnext.targeting",
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


def test_runtime_evidence_plane_is_exact_pure_and_default_unreachable() -> None:
    assert tuple(
        sorted(path.name for path in EVIDENCE_PLANE_ROOT.glob("*.py"))
    ) == EVIDENCE_PLANE_FILES
    importers = {
        path.relative_to(VNEXT_ROOT).as_posix()
        for path in _source_files(VNEXT_ROOT)
        if EVIDENCE_PLANE_ROOT not in path.parents
        and any(
            _matches_prefix(
                imported,
                "winpilot.vnext.evidence_plane",
            )
            for imported in _imports(path)
        )
    }
    assert importers == {
        "desktop_adapter/atomic.py",
        "runtime/mission_runtime.py",
    }


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


def test_minimal_runtime_is_exact_default_unreachable_and_sdk_free() -> None:
    assert tuple(sorted(path.name for path in RUNTIME_ROOT.glob("*.py"))) == (
        RUNTIME_FILES
    )
    violations = [
        f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}"
        for path in _source_files(RUNTIME_ROOT)
        for imported in _imports(path)
        if any(
            _matches_prefix(imported, prefix)
            for prefix in RUNTIME_FORBIDDEN_IMPORT_PREFIXES
        )
    ]
    assert violations == []


def test_minimal_runtime_tool_surface_cannot_expand_silently() -> None:
    from winpilot.vnext.runtime.contracts import (
        APPROVED_DESKTOP_ACTIONS,
        APPROVED_OUTCOME_TOOLS,
        APPROVED_RUNTIME_TOOLS,
    )

    assert APPROVED_DESKTOP_ACTIONS == {
        "click",
        "double_click",
        "scroll",
        "type_text",
        "hotkey",
    }
    assert APPROVED_OUTCOME_TOOLS == {
        "claim_obligation_done",
        "blocked",
    }
    assert APPROVED_RUNTIME_TOOLS == (
        APPROVED_DESKTOP_ACTIONS | APPROVED_OUTCOME_TOOLS
    )


def test_only_atomic_adapter_crosses_the_live_desktop_boundary() -> None:
    atomic = VNEXT_ROOT / "desktop_adapter" / "atomic.py"
    crossings = tuple(
        (path, imported)
        for path in sorted(VNEXT_ROOT.rglob("*.py"))
        for imported in _imports(path)
        if any(
            _matches_prefix(imported, prefix)
            for prefix in LIVE_DESKTOP_IMPORT_PREFIXES
        )
    )
    assert {path for path, _imported in crossings} == {atomic}
    assert tuple(imported for _path, imported in crossings) == (
        ATOMIC_DESKTOP_IMPORTS
    )


def test_live_model_roles_are_exact_isolated_and_use_only_model_runtime() -> None:
    assert tuple(
        sorted(path.name for path in MODEL_ROLES_ROOT.glob("*.py"))
    ) == MODEL_ROLE_FILES
    paths = _source_files(MODEL_ROLES_ROOT)
    forbidden = [
        f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}"
        for path in paths
        for imported in _imports(path)
        if any(
            _matches_prefix(imported, prefix)
            for prefix in MODEL_ROLE_FORBIDDEN_IMPORT_PREFIXES
        )
    ]
    assert forbidden == []
    unexpected_product_imports = [
        f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}"
        for path in paths
        for imported in _imports(path)
        if _matches_prefix(imported, "winpilot")
        and not any(
            _matches_prefix(imported, prefix)
            for prefix in MODEL_ROLE_ALLOWED_PRODUCT_PREFIXES
        )
    ]
    assert unexpected_product_imports == []
    modeling_importers = {
        path.name
        for path in paths
        if any(
            _matches_prefix(imported, "winpilot.modeling")
            for imported in _imports(path)
        )
    }
    assert modeling_importers == {
        "visual_actor.py",
        "visual_obligation_verifier.py",
    }
    vnext_modeling_importers = {
        path.relative_to(VNEXT_ROOT).as_posix()
        for path in _source_files(VNEXT_ROOT)
        if any(
            _matches_prefix(imported, "winpilot.modeling")
            for imported in _imports(path)
        )
    }
    assert vnext_modeling_importers == {
        "model_roles/visual_actor.py",
        "model_roles/visual_obligation_verifier.py",
    }
