"""Ownership contract for the shared mechanical core the vNext line runs on.

The vNext product line executes directly on a small set of shared mechanical
modules (physical input, frame capture, window readback, app launch).  They
sit outside ``winpilot.vnext`` but are vNext-owned:

- the product line must be able to reach every one of them (deleting a legacy
  package must never take the mechanical layer down with it);
- they must never import legacy or planning layers (legacy deletion can never
  cascade into them);
- legacy verification / perception-stack reach from the product line is
  pinned and shrink-only: the pinned edges below are the Phase D deletion
  checklist.  Removing an edge means deleting the legacy consumer or
  decoupling it; adding one fails the suite.

``action/verify.py``, ``utils/trace_payload.py`` and the perception
OCR/fusion/VLM stack are deliberately NOT mechanical core: they are legacy
verification/evidence layers.
"""
from __future__ import annotations

import ast
from pathlib import Path

from vnext_import_boundary_ast import analyze_path


PACKAGE_ROOT = Path(__file__).parents[1] / "winpilot"
LIVE_COMPOSITION_MODULE = "winpilot.orchestrator.live_agent"

MECHANICAL_CORE_MODULES = (
    "winpilot.action.desktop_queue",
    "winpilot.action.executor",
    "winpilot.action.raw_device",
    "winpilot.action.replay_policy",
    "winpilot.action.safety",
    "winpilot.action.shell",
    "winpilot.utils.dpi",
    "winpilot.utils.image_capture",
    "winpilot.utils.log",
    "winpilot.utils.screenshot",
    "winpilot.visual_runtime.frame_authority",
    "winpilot.visual_runtime.frame_observer",
    "winpilot.visual_runtime.keyboard_contracts",
    "winpilot.perception.apps",
    "winpilot.perception.model",
    "winpilot.perception.uia",
)

CORE_FORBIDDEN_IMPORT_PREFIXES = (
    "winpilot.agent",
    "winpilot.tasking",
    "winpilot.fast",
    "winpilot.recorder",
    "winpilot.memory",
    "winpilot.integrations",
    "winpilot.modeling",
    "winpilot.eval",
    "winpilot.orchestrator",
    "winpilot.server",
    "winpilot.vnext",
    "openai",
    "anthropic",
    "pydantic",
)

LEGACY_VERIFICATION_MODULES = (
    "winpilot.action.verify",
    "winpilot.utils.trace_payload",
    "winpilot.perception.fusion",
    "winpilot.perception.incremental",
    "winpilot.perception.ocr",
    "winpilot.perception.reducer",
    "winpilot.perception.surface_binding",
    "winpilot.perception.vision_cv",
    "winpilot.perception.vlm",
    "winpilot.perception.win32",
    "winpilot.perception.world",
)

# Fixed-point of legacy verification-stack reach from the product line.
# Empty since 2026-08-05: the vNext line has zero references to the legacy
# verification/perception stack.  Any new edge here fails the suite; the
# legacy verification modules now only serve their own legacy consumers and
# die with them.
PINNED_LEGACY_VERIFICATION_REACH = frozenset()

# Same shrink-only contract for the mechanical core's own perception leaks.
# Empty: the capture core is fully decoupled from the legacy perception stack.
PINNED_CORE_PERCEPTION_LEAKS = frozenset()

CORE_PERCEPTION_OWNED = (
    "winpilot.perception.app_aliases",
    "winpilot.perception.apps",
    "winpilot.perception.model",
    "winpilot.perception.search",
    "winpilot.perception.uia",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def _module_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        module = _module_name(path)
        paths[module] = path
        if path.name == "__init__.py":
            paths[module.removesuffix(".__init__")] = path
    return paths


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
                imported = ".".join(
                    part for part in (prefix, node.module or "") if part
                )
            else:
                imported = node.module or ""
            found.append(imported)
            if imported:
                found.extend(
                    f"{imported}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
    return tuple(found)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _reachable_from_product_line() -> set[str]:
    module_paths = _module_paths()
    reachable: set[str] = set()
    pending = [LIVE_COMPOSITION_MODULE]
    while pending:
        module = pending.pop()
        if module in reachable:
            continue
        path = module_paths.get(module)
        if path is None:
            continue
        reachable.add(module)
        for imported in _imports(path):
            if _matches_prefix(imported, "winpilot") and imported not in reachable:
                pending.append(imported)
    return reachable


def test_mechanical_core_modules_exist() -> None:
    module_paths = _module_paths()
    missing = [
        module for module in MECHANICAL_CORE_MODULES if module not in module_paths
    ]
    assert missing == []


def test_every_mechanical_core_module_is_reachable_from_the_product_line() -> None:
    """The whole mechanical core must be statically reachable from the vNext
    composition root, so deleting a legacy package can never orphan it."""
    unreachable = sorted(
        set(MECHANICAL_CORE_MODULES) - _reachable_from_product_line()
    )
    assert unreachable == []


def test_mechanical_core_modules_never_import_legacy_or_planning_layers() -> None:
    module_paths = _module_paths()
    violations = [
        f"{module} -> {imported}"
        for module in MECHANICAL_CORE_MODULES
        for imported in _imports(module_paths[module])
        if any(
            _matches_prefix(imported, prefix)
            for prefix in CORE_FORBIDDEN_IMPORT_PREFIXES
        )
    ]
    assert violations == []


def test_mechanical_core_perception_leaks_are_pinned_shrink_only() -> None:
    module_paths = _module_paths()
    actual = frozenset(
        {
            (module, imported)
            for module in MECHANICAL_CORE_MODULES
            for imported in _imports(module_paths[module])
            if imported in LEGACY_VERIFICATION_MODULES
        }
    )
    unexpected = sorted(actual - PINNED_CORE_PERCEPTION_LEAKS)
    assert unexpected == []


def test_product_line_legacy_verification_reach_is_pinned_shrink_only() -> None:
    """Direct edges from the product-line closure into the legacy
    verification stack must never grow.  Deleting an edge (by removing the
    legacy consumer) is the Phase D checklist; the pin must only shrink."""
    module_paths = _module_paths()
    module_paths = _module_paths()
    actual = frozenset(
        {
            (module, imported)
            for module in _reachable_from_product_line()
            for imported in _imports(module_paths[module])
            if imported in LEGACY_VERIFICATION_MODULES
        }
    )
    unexpected = sorted(actual - PINNED_LEGACY_VERIFICATION_REACH)
    assert unexpected == []
