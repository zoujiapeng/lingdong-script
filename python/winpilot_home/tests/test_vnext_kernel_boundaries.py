"""Static boundaries for the pure, default-unreachable vNext kernel."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from vnext_import_boundary_ast import (
    ImportAnalysis,
    analyze_path,
    analyze_source,
)


PACKAGE_ROOT = Path(__file__).parents[1] / "winpilot"
VNEXT_ROOT = PACKAGE_ROOT / "vnext"
RUNTIME_ROOT = VNEXT_ROOT / "runtime"
MODEL_ROLES_ROOT = VNEXT_ROOT / "model_roles"
EVIDENCE_PLANE_ROOT = VNEXT_ROOT / "evidence_plane"
LIVE_COMPOSITION_ROOT = (
    PACKAGE_ROOT / "orchestrator" / "vnext_live_canary.py"
)
LIVE_COMPOSITION_MODULE = "winpilot.orchestrator.vnext_live_canary"
LIVE_COMPOSITION_VNEXT_MODULES = (
    "winpilot.vnext.capabilities",
    "winpilot.vnext.desktop_adapter",
    "winpilot.vnext.evidence",
    "winpilot.vnext.evidence_plane",
    "winpilot.vnext.mission",
    "winpilot.vnext.model_roles",
    "winpilot.vnext.profiling",
    "winpilot.vnext.runtime",
)
LIVE_COMPOSITION_VNEXT_BINDINGS = (
    ("winpilot.vnext.capabilities", "BrokerRuntimeState", None),
    ("winpilot.vnext.capabilities", "CapabilityBroker", None),
    ("winpilot.vnext.capabilities", "ExecutionMedium", None),
    ("winpilot.vnext.capabilities", "MutationKind", None),
    ("winpilot.vnext.capabilities", "PointerDisposition", None),
    ("winpilot.vnext.capabilities", "PointerLeaseState", None),
    ("winpilot.vnext.capabilities", "TargetState", None),
    ("winpilot.vnext.capabilities", "ToolCatalog", None),
    ("winpilot.vnext.capabilities", "ToolRoute", None),
    ("winpilot.vnext.capabilities", "ToolSpec", None),
    ("winpilot.vnext.desktop_adapter", "AtomicDesktopAdapter", None),
    ("winpilot.vnext.desktop_adapter", "DesktopWorkspace", None),
    ("winpilot.vnext.desktop_adapter", "LiveTargetSnapshot", None),
    ("winpilot.vnext.desktop_adapter", "WorkspaceObservation", None),
    ("winpilot.vnext.desktop_adapter", "WorkspaceSurface", None),
    ("winpilot.vnext.evidence", "EvidenceRegistry", None),
    ("winpilot.vnext.evidence_plane", "FrameArtifactWriter", None),
    ("winpilot.vnext.evidence_plane", "RuntimeEvidenceJournal", None),
    ("winpilot.vnext.evidence_plane", "RuntimeEvidencePlane", None),
    ("winpilot.vnext.evidence_plane", "canonical_sha256", None),
    (
        "winpilot.vnext.evidence_plane",
        "runtime_evidence_report_sha256",
        None,
    ),
    ("winpilot.vnext.mission", "EvidenceRef", None),
    ("winpilot.vnext.mission", "MissionDefinition", None),
    ("winpilot.vnext.mission", "MissionReducer", None),
    ("winpilot.vnext.mission", "MissionStatus", None),
    ("winpilot.vnext.mission", "ObligationSpec", None),
    ("winpilot.vnext.mission", "TargetBinding", None),
    ("winpilot.vnext.model_roles", "VISUAL_ACTOR_MODEL_ROLE", None),
    ("winpilot.vnext.model_roles", "LiveVisualActor", None),
    ("winpilot.vnext.profiling", "ExecutionPolicy", None),
    ("winpilot.vnext.runtime", "CURRENT_FRAME_EVIDENCE_KIND", None),
    ("winpilot.vnext.runtime", "MissionRuntime", None),
    ("winpilot.vnext.runtime", "MissionRuntimeResult", None),
    ("winpilot.vnext.runtime", "MutationPermitAuthority", None),
    ("winpilot.vnext.runtime", "POST_ACTION_FRAME_EVIDENCE_KIND", None),
    ("winpilot.vnext.runtime", "RuntimeFrame", None),
    ("winpilot.vnext.runtime", "RuntimeFrameIdentity", None),
    ("winpilot.vnext.runtime", "RuntimeSnapshot", None),
    ("winpilot.vnext.runtime", "RuntimeStatus", None),
    ("winpilot.vnext.runtime", "RuntimeTargetIdentity", None),
)
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
    "task_state.py",
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
)

EVIDENCE_PLANE_FILES = (
    "__init__.py",
    "artifacts.py",
    "contracts.py",
    "journal.py",
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


def _analyze_path(path: Path) -> ImportAnalysis:
    return analyze_path(path, module_name=_module_name(path))


def _sorted_root_bindings() -> tuple[
    tuple[str, str | None, str | None],
    ...,
]:
    return tuple(
        sorted(
            LIVE_COMPOSITION_VNEXT_BINDINGS,
            key=lambda item: (
                item[0],
                item[1] or "",
                item[2] or "",
            ),
        )
    )


def _root_static_imports_are_exact(analysis: ImportAnalysis) -> bool:
    return (
        analysis.static_modules("winpilot.vnext")
        == LIVE_COMPOSITION_VNEXT_MODULES
        and analysis.static_bindings("winpilot.vnext")
        == _sorted_root_bindings()
    )


def _dynamic_boundary_calls(
    analysis: ImportAnalysis,
    *,
    composition_root: bool,
):
    if composition_root:
        return analysis.dynamic_calls
    return analysis.dynamic_calls_reaching(
        "winpilot.vnext",
        LIVE_COMPOSITION_MODULE,
    )


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


def test_only_one_composition_root_can_reach_live_vnext_by_import() -> None:
    analyses = {
        path: _analyze_path(path)
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if VNEXT_ROOT not in path.parents
    }
    importer_bindings: dict[
        Path,
        tuple[tuple[str, str | None, str | None], ...],
    ] = {}
    for path, analysis in analyses.items():
        bindings = analysis.static_bindings("winpilot.vnext")
        if bindings:
            importer_bindings[path] = bindings
    assert set(importer_bindings) == {LIVE_COMPOSITION_ROOT}
    assert _root_static_imports_are_exact(
        analyses[LIVE_COMPOSITION_ROOT]
    )

    composition_importers = {
        path
        for path, analysis in analyses.items()
        if path != LIVE_COMPOSITION_ROOT
        and analysis.static_bindings(
            LIVE_COMPOSITION_MODULE
        )
    }
    assert composition_importers == {PACKAGE_ROOT / "server.py"}

    dynamic_violations = [
        (
            path.relative_to(PACKAGE_ROOT.parent),
            call.kind,
            call.targets,
            call.target_unknown,
            call.lineno,
        )
        for path, analysis in analyses.items()
        for call in _dynamic_boundary_calls(
            analysis,
            composition_root=path == LIVE_COMPOSITION_ROOT,
        )
    ]
    assert dynamic_violations == []


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "import importlib\n"
            "importlib.import_module('winpilot.vnext.runtime')",
            id="direct-importlib",
        ),
        pytest.param(
            "__import__('winpilot.vnext.runtime')",
            id="direct-builtin",
        ),
        pytest.param(
            "import importlib as il\n"
            "il.import_module('winpilot.vnext.runtime')",
            id="importlib-module-alias",
        ),
        pytest.param(
            "from importlib import import_module as load\n"
            "load('winpilot.vnext.runtime')",
            id="from-import-alias",
        ),
        pytest.param(
            "from builtins import __import__ as load\n"
            "load('winpilot.vnext.runtime')",
            id="from-builtins-alias",
        ),
        pytest.param(
            "import builtins as b\n"
            "b.__import__('winpilot.vnext.runtime')",
            id="builtins-module-alias",
        ),
        pytest.param(
            "import importlib\n"
            "load = importlib.import_module\n"
            "load('winpilot.vnext.runtime')",
            id="assigned-importlib",
        ),
        pytest.param(
            "import importlib as il\n"
            "load = il.import_module\n"
            "load('winpilot.vnext.runtime')",
            id="assigned-importlib-module-alias",
        ),
        pytest.param(
            "load = __import__\n"
            "load('winpilot.vnext.runtime')",
            id="assigned-builtin",
        ),
        pytest.param(
            "import builtins as b\n"
            "load = b.__import__\n"
            "load('winpilot.vnext.runtime')",
            id="assigned-builtins-module-alias",
        ),
        pytest.param(
            "load = __import__\n"
            "load2 = load\n"
            "load2('winpilot.vnext.runtime')",
            id="two-hop-assignment",
        ),
        pytest.param(
            "import importlib\n"
            "a = b = importlib.import_module\n"
            "b('winpilot.vnext.runtime')",
            id="chained-assignment",
        ),
        pytest.param(
            "from typing import Callable\n"
            "import importlib\n"
            "load: Callable = importlib.import_module\n"
            "load('winpilot.vnext.runtime')",
            id="annotated-assignment",
        ),
        pytest.param(
            "def run():\n"
            "    import importlib\n"
            "    load = importlib.import_module\n"
            "    load('winpilot.vnext.runtime')\n"
            "run()",
            id="function-local",
        ),
        pytest.param(
            "load = __import__\n"
            "def outer():\n"
            "    def inner():\n"
            "        load('winpilot.vnext.runtime')\n"
            "    inner()\n"
            "outer()",
            id="closure",
        ),
        pytest.param(
            "def outer():\n"
            "    def inner():\n"
            "        load('winpilot.vnext.runtime')\n"
            "    load = __import__\n"
            "    inner()\n"
            "outer()",
            id="closure-late-binding",
        ),
        pytest.param(
            "def run():\n"
            "    load('winpilot.vnext.runtime')\n"
            "load = __import__\n"
            "run()",
            id="global-late-binding",
        ),
        pytest.param(
            "import importlib\n"
            "def run(load=importlib.import_module):\n"
            "    load('winpilot.vnext.runtime')\n"
            "run()",
            id="default-argument-capture",
        ),
        pytest.param(
            "load = __import__\n"
            "if condition:\n"
            "    load = lambda _name: None\n"
            "load('winpilot.vnext.runtime')",
            id="branch-preserves-capability",
        ),
        pytest.param(
            "def outer(flag):\n"
            "    if flag:\n"
            "        def inner():\n"
            "            load('winpilot.vnext.runtime')\n"
            "    load = __import__\n"
            "    inner()\n"
            "outer(True)",
            id="branch-closure-late-binding",
        ),
        pytest.param(
            "run = (lambda: load('winpilot.vnext.runtime')) "
            "if flag else (lambda: None)\n"
            "load = __import__\n"
            "run()",
            id="conditional-expression-closure-late-binding",
        ),
        pytest.param(
            "__import__('winpilot.' + 'vnext.runtime')",
            id="constant-concatenation",
        ),
        pytest.param(
            "load = __import__\n"
            "module = 'winpilot.vnext.runtime'\n"
            "load(module)",
            id="assigned-module-name",
        ),
        pytest.param(
            "prefix = 'winpilot'\n"
            "subsystem = 'runtime'\n"
            "__import__(f'{prefix}.vnext.{subsystem}')",
            id="static-f-string",
        ),
        pytest.param(
            "import importlib.util\n"
            "importlib.import_module('winpilot.vnext.runtime')",
            id="importlib-submodule-binds-package",
        ),
        pytest.param(
            "import importlib\n"
            "importlib.import_module("
            "name='winpilot.vnext.runtime')",
            id="keyword-module-name",
        ),
        pytest.param(
            "import importlib\n"
            "module = 'winpilot.' + 'vnext.runtime'\n"
            "importlib.import_module(name=module)",
            id="keyword-assigned-module-name",
        ),
        pytest.param(
            "load = __import__\n"
            "module = 'winpilot.orchestrator.' + "
            "'vnext_live_canary'\n"
            "load(module)",
            id="dynamic-composition-root",
        ),
    ),
)
def test_ast_import_analyzer_rejects_dynamic_vnext_bypasses(
    source: str,
) -> None:
    analysis = analyze_source(
        source,
        module_name="winpilot.synthetic_product",
    )
    assert analysis.dynamic_calls_reaching(
        "winpilot.vnext",
        LIVE_COMPOSITION_MODULE,
    )


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "__import__('os')",
            id="module-body",
        ),
        pytest.param(
            "def shutdown():\n"
            "    __import__('os')",
            id="uninvoked-function",
        ),
        pytest.param(
            "shutdown = lambda: __import__('os')",
            id="uninvoked-lambda",
        ),
    ),
)
def test_ast_import_analyzer_rejects_any_root_dynamic_import(
    source: str,
) -> None:
    analysis = analyze_source(
        source,
        module_name=LIVE_COMPOSITION_MODULE,
    )
    assert analysis.dynamic_calls
    assert _dynamic_boundary_calls(
        analysis,
        composition_root=True,
    ) == analysis.dynamic_calls


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "__import__('os')",
            id="unrelated-builtin-import",
        ),
        pytest.param(
            "def shutdown():\n"
            "    __import__('os')",
            id="unrelated-uninvoked-function",
        ),
        pytest.param(
            "import importlib\n"
            "importlib.import_module('json')",
            id="unrelated-importlib-import",
        ),
        pytest.param(
            "load = __import__\n"
            "def ordinary(_name):\n"
            "    return None\n"
            "load = ordinary\n"
            "load('winpilot.vnext.runtime')",
            id="ordinary-rebind",
        ),
        pytest.param(
            "load = __import__\n"
            "def run(load):\n"
            "    load('winpilot.vnext.runtime')\n"
            "run(lambda _name: None)",
            id="parameter-shadow",
        ),
        pytest.param(
            "def ordinary(_name):\n"
            "    return None\n"
            "load = __import__\n"
            "def run():\n"
            "    load('winpilot.vnext.runtime')\n"
            "load = ordinary\n"
            "run()",
            id="global-rebind-before-call",
        ),
        pytest.param(
            "def ordinary(_name):\n"
            "    return None\n"
            "def outer():\n"
            "    load = __import__\n"
            "    def inner():\n"
            "        load('winpilot.vnext.runtime')\n"
            "    load = ordinary\n"
            "    inner()\n"
            "outer()",
            id="closure-rebind-before-call",
        ),
        pytest.param(
            "def ordinary(_name):\n"
            "    return None\n"
            "def outer(flag):\n"
            "    if flag:\n"
            "        load = __import__\n"
            "        def inner():\n"
            "            load('winpilot.vnext.runtime')\n"
            "    load = ordinary\n"
            "    inner()\n"
            "outer(True)",
            id="branch-closure-rebind-before-call",
        ),
        pytest.param(
            "def ordinary(_name):\n"
            "    return None\n"
            "load = __import__\n"
            "run = (lambda: load('winpilot.vnext.runtime')) "
            "if flag else (lambda: None)\n"
            "load = ordinary\n"
            "run()",
            id="conditional-expression-closure-rebind",
        ),
        pytest.param(
            "load = __import__\n"
            "if condition:\n"
            "    load = lambda _name: None\n"
            "else:\n"
            "    load = lambda _name: None\n"
            "load('winpilot.vnext.runtime')",
            id="all-branches-rebind",
        ),
        pytest.param(
            "module = choose_module()\n"
            "__import__(module)",
            id="unknown-target",
        ),
        pytest.param(
            "# winpilot.vnext.runtime\n"
            "note = 'winpilot.vnext.runtime'",
            id="comment-and-string-only",
        ),
    ),
)
def test_ast_import_analyzer_allows_safe_dynamic_imports_and_shadowing(
    source: str,
) -> None:
    analysis = analyze_source(
        source,
        module_name="winpilot.synthetic_product",
    )
    assert _dynamic_boundary_calls(
        analysis,
        composition_root=False,
    ) == ()


def test_ast_import_analyzer_enforces_exact_static_root_allowlist() -> None:
    allowed_source = "\n".join(
        f"from {module} import {name}"
        for module, name, asname in LIVE_COMPOSITION_VNEXT_BINDINGS
        if name is not None and asname is None
    )
    allowed = analyze_source(
        allowed_source,
        module_name=LIVE_COMPOSITION_MODULE,
    )
    assert _root_static_imports_are_exact(allowed)
    assert allowed.dynamic_calls == ()

    expanded = analyze_source(
        allowed_source
        + "\nfrom winpilot.vnext.targeting import TargetFinder",
        module_name=LIVE_COMPOSITION_MODULE,
    )
    assert not _root_static_imports_are_exact(expanded)

    second_importer = analyze_source(
        "from winpilot.vnext.runtime import MissionRuntime",
        module_name="winpilot.server",
    )
    assert second_importer.static_bindings("winpilot.vnext")
    hidden_second_importer = analyze_source(
        "def hidden():\n"
        "    from winpilot.vnext.runtime import MissionRuntime",
        module_name="winpilot.server",
    )
    assert hidden_second_importer.static_bindings("winpilot.vnext")


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
        "drag",
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
    assert modeling_importers == {"visual_actor.py"}
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
    }
