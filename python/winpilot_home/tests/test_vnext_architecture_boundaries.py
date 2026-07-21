"""Static ownership guards for the vNext Agent core."""
from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

from winpilot.vnext.capabilities import (
    CapabilityContractError,
    MutationKind,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)


VNEXT_ROOT = Path(__file__).parents[1] / "winpilot" / "vnext"
DIRECT_MODEL_SDKS = (
    "anthropic",
    "cohere",
    "google.genai",
    "google.generativeai",
    "groq",
    "mistralai",
    "ollama",
    "openai",
    "together",
)


def _source_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.py") if path.is_file()))


def _module_name(path: Path) -> str:
    relative = path.relative_to(VNEXT_ROOT.parent.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> tuple[tuple[str, str, int], ...]:
    """Return (resolved module, imported symbol, line) without importing code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    current = _module_name(path)
    package = current if path.name == "__init__.py" else current.rpartition(".")[0]
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, "", node.lineno) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        raw_module = f"{'.' * node.level}{node.module or ''}"
        module = resolve_name(raw_module, package) if node.level else raw_module
        for alias in node.names:
            found.append((module, alias.name, node.lineno))
    return tuple(found)


def _matches_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value == prefix or value.startswith(f"{prefix}.") for prefix in prefixes)


def test_vnext_does_not_import_legacy_gui_owners_or_direct_model_sdks() -> None:
    forbidden_modules = (
        "winpilot.action.raw_device",
        "winpilot.agent.loop",
        "winpilot.agent.tools",
    )
    forbidden_symbols = {
        "AgentRunner",
        "RawDevice",
        "RawDeviceExecutor",
        "ToolSession",
    }
    offenders: list[tuple[str, int, str]] = []
    for path in _source_files(VNEXT_ROOT):
        for module, symbol, line in _imports(path):
            imported_target = f"{module}.{symbol}" if symbol else module
            if (
                _matches_prefix(module, forbidden_modules)
                or imported_target == "winpilot.agent.loop"
                or symbol in forbidden_symbols
                or _matches_prefix(module, DIRECT_MODEL_SDKS)
                or _matches_prefix(imported_target, DIRECT_MODEL_SDKS)
            ):
                offenders.append((str(path.relative_to(VNEXT_ROOT)), line, imported_target))
    assert offenders == []


def test_actor_contracts_do_not_import_controller_or_mission_reducer() -> None:
    actor_root = VNEXT_ROOT / "actor"
    forbidden_modules = (
        "winpilot.action.desktop_queue",
        "winpilot.action.executor",
        "winpilot.action.raw_device",
        "winpilot.fast.controller",
        "winpilot.vnext.mission.reducer",
    )
    forbidden_symbols = {
        "Controller",
        "DesktopActionQueue",
        "FastGuiController",
        "MissionReducer",
        "RawDeviceExecutor",
    }
    offenders = []
    for path in _source_files(actor_root):
        for module, symbol, line in _imports(path):
            if _matches_prefix(module, forbidden_modules) or symbol in forbidden_symbols:
                offenders.append((str(path.relative_to(VNEXT_ROOT)), line, module, symbol))
    assert offenders == []


def test_runtime_package_references_ports_not_real_device_implementations() -> None:
    """The pure P4 Runtime may import MissionReducer, but not device owners."""
    runtime_root = VNEXT_ROOT / "runtime"
    forbidden_modules = (
        "winpilot.action.desktop_queue",
        "winpilot.action.executor",
        "winpilot.action.raw_device",
        "winpilot.agent.loop",
        "winpilot.agent.tools",
        "winpilot.fast.controller",
    )
    forbidden_symbols = {
        "AgentRunner",
        "DesktopActionQueue",
        "FastGuiController",
        "RawDevice",
        "RawDeviceExecutor",
        "ToolSession",
    }
    offenders = []
    for path in _source_files(runtime_root):
        for module, symbol, line in _imports(path):
            if _matches_prefix(module, forbidden_modules) or symbol in forbidden_symbols:
                offenders.append((str(path.relative_to(VNEXT_ROOT)), line, module, symbol))
    assert offenders == []


def test_tool_catalog_reserves_gui_mutation_for_visual_actor() -> None:
    schema = {"type": "object", "additionalProperties": False}
    catalog = ToolCatalog((
        ToolSpec("click", schema, ToolRoute.VISUAL_ACTOR, MutationKind.GUI),
        ToolSpec("observe", schema, ToolRoute.VISUAL_ACTOR, MutationKind.NONE),
    ))
    assert all(
        tool.route is ToolRoute.VISUAL_ACTOR
        for tool in catalog.tools
        if tool.mutation_kind is MutationKind.GUI
    )

    for route in (ToolRoute.DETERMINISTIC, ToolRoute.PROVIDER):
        with pytest.raises(CapabilityContractError, match="cannot mutate the GUI"):
            ToolCatalog((ToolSpec("unsafe_gui", schema, route, MutationKind.GUI),))
