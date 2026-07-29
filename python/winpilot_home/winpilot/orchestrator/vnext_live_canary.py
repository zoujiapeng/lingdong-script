"""Sole product composition root for the minimal live vNext canary.

The module accepts only the Electron-provided, already-bound desktop target.
It validates that identity, composes the C1-C3 typed owners, and exposes one
``AGENT_GOAL`` handler.  It never discovers, selects, activates, launches, or
recovers a target, and it has no legacy fallback.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Protocol

from ..config import TRACES_DIR
from ..action.executor import (
    ADMITTED_PHYSICAL_CLICK_BACKEND,
    ADMITTED_PHYSICAL_DRAG_BACKEND,
    ADMITTED_PHYSICAL_HOTKEY_BACKEND,
    ADMITTED_PHYSICAL_SCROLL_BACKEND,
    ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND,
)
from ..action.raw_device import (
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    MAX_TYPE_TEXT_CODEPOINTS,
    RawActionReceipt,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from ..agent.frame_authority import FrameAuthority, FrameState
from ..model_adapter import build_model_adapter, model_profile_from_config
from ..modeling.contracts import ModelAttempt
from ..modeling.openai_transport import create_openai_client
from ..modeling.runtime import ModelCallRuntime
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS
from ..utils.screenshot import Region, virtual_screen_region, window_region
from ..visual_runtime.frame_observer import FrameObserver
from ..vnext.capabilities import (
    BrokerRuntimeState,
    CapabilityBroker,
    ExecutionMedium,
    InteractionMode,
    MutationKind,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from ..vnext.desktop_adapter import (
    AtomicDesktopAdapter,
    LiveTargetSnapshot,
)
from ..vnext.evidence import EvidenceRegistry
from ..vnext.evidence_plane import (
    FrameArtifactWriter,
    RuntimeEvidenceJournal,
    RuntimeEvidencePlane,
    canonical_sha256,
    runtime_evidence_report_sha256,
)
from ..vnext.mission import (
    EvidencePolicy,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
)
from ..vnext.model_roles import (
    VISUAL_ACTOR_MODEL_ROLE,
    VISUAL_VERIFIER_KIND,
    VISUAL_VERIFIER_MODEL_ROLE,
    LiveVisualActor,
    LiveVisualObligationVerifier,
)
from ..vnext.profiling import ExecutionPolicy
from ..vnext.runtime import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    MissionRuntime,
    MissionRuntimeResult,
    MutationPermitAuthority,
    RuntimeFrame,
    RuntimeFrameIdentity,
    RuntimeSnapshot,
    RuntimeStatus,
    RuntimeTargetIdentity,
)
from .contracts import (
    ArpEdge,
    ArpGraph,
    ArpNode,
    ArpNodeKind,
    ArpPolicy,
    ArpRetryPolicy,
)
from .runtime import ArpExecution, NodeResult


_RUNTIME_VERSION = "vnext"
_OBLIGATION_ID = "full-goal"
_RUNTIME_EVIDENCE_ROOT = TRACES_DIR / "vnext_runtime_evidence"
_TARGET_MAX_AGE_MS = 15_000
_FUTURE_CLOCK_SKEW_MS = 2_000
_MAX_SUBMITTED_SIDE = 1120
_MAX_VISUAL_VERIFICATION_ATTEMPTS = 3
_MAX_CONSECUTIVE_INVALID_PROPOSALS = 2
_MAX_CONSECUTIVE_ACTOR_ERRORS = 2
_NON_WHITESPACE_PATTERN = r"\S"
_BLOCKER_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_APPROVED_ACTIONS = frozenset(
    {"click", "double_click", "drag", "scroll", "type_text", "hotkey"}
)
_GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "pointer_released",
    "runtime_healthy",
)
_CONTINUE_STATUSES = frozenset(
    {
        RuntimeStatus.ACTION_RECORDED,
        RuntimeStatus.PROPOSAL_SUPPRESSED,
        RuntimeStatus.STALE_DISCARDED,
        RuntimeStatus.VERIFICATION_RECORDED,
    }
)
_PRIVATE_RUNTIME_EVIDENCE = "private_runtime_evidence"
_RUNTIME_EVIDENCE_REPORT_STATUSES = frozenset(
    {"complete", "incomplete", "verification_failed"}
)
_PUBLIC_ACTION_EFFECT_FIELDS = frozenset(
    {
        "format",
        "comparator_version",
        "action_id",
        "input_completed",
        "before_frame_id",
        "after_frame_id",
        "before_frame_evidence_ref",
        "after_frame_evidence_ref",
        "before_frame_payload_hash",
        "after_frame_payload_hash",
        "target_binding_valid",
        "frame_comparable",
        "local_visual_change",
        "change_ratio",
        "reason",
        "semantic_completion",
        "evidence_ref",
        "payload_hash",
    }
)


@dataclass(frozen=True, slots=True)
class _Budget:
    max_turns: int
    max_runtime_s: int
    max_model_calls: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_turns": self.max_turns,
            "max_runtime_s": self.max_runtime_s,
            "max_model_calls": self.max_model_calls,
        }


@dataclass(frozen=True, slots=True)
class _ModelConfig:
    api: dict[str, Any]
    base_url: str
    api_key: str
    model: str
    request_timeout_s: float
    total_timeout_s: float


@dataclass(frozen=True, slots=True)
class _TargetFacts:
    hwnd: int
    root_hwnd: int
    process_id: int
    executable_identity: str
    title: str
    class_name: str
    region: tuple[int, int, int, int]
    foreground_root_hwnd: int | None


class _SessionPort(Protocol):
    @property
    def model_calls(self) -> int: ...

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: Any,
    ) -> MissionRuntimeResult: ...

    def completion_snapshot(self) -> Mapping[str, Any]: ...

    def turn_snapshot(self) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _CompositionDependencies:
    wall_clock: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic
    target_reader: Callable[[int], _TargetFacts | None] | None = None
    hit_test_root: Callable[[int, int], int | None] | None = None
    client_factory: Callable[..., Any] | None = None
    session_builder: Callable[["_SessionBuild"], _SessionPort] | None = None
    runtime_evidence_root: Path | None = None


@dataclass(frozen=True, slots=True)
class _FrozenTarget:
    hwnd: int
    root_hwnd: int
    process_id: int
    executable_identity: str
    title: str
    class_name: str
    region: tuple[int, int, int, int]
    captured_at_ms: int
    binding_id: str
    probe: "_BoundTargetProbe"

    def trace_identity(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "hwnd": self.hwnd,
            "root_hwnd": self.root_hwnd,
            "process_id": self.process_id,
            "class_name": self.class_name,
            "region": list(self.region),
            "executable_identity_sha256": hashlib.sha256(
                self.executable_identity.encode("utf-8")
            ).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class _SessionBuild:
    graph: ArpGraph
    goal: str
    run_id: str
    mission_id: str
    target: _FrozenTarget
    budget: _Budget
    model: _ModelConfig
    dependencies: _CompositionDependencies
    deadline: Callable[[], float]
    interaction_mode: InteractionMode = InteractionMode.AUTO
    pause_controller: Any = None


def _plain_dict(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{name} must be a plain JSON object")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    actual = set(value)
    if actual != keys:
        raise ValueError(
            f"{name} fields mismatch: "
            f"missing={sorted(keys - actual)!r}, "
            f"unknown={sorted(actual - keys)!r}"
        )


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    result = float(value)
    if not 0 < result < float("inf"):
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _fnv1a_utf16(text: str) -> str:
    """Return the stable UTF-16 FNV-1a key used by live graph identity."""

    value = 0x811C9DC5
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        value = ((value ^ code_unit) * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def build_exact_live_vnext_graph(goal: str) -> ArpGraph:
    """Project one full goal into the exact graph accepted by the live runtime."""

    if type(goal) is not str:
        raise ValueError("vNext live canary goal must be a literal string")
    normalized_goal = goal.strip()
    if not normalized_goal:
        raise ValueError("vNext live canary requires a non-empty full goal")
    graph = ArpGraph(
        id=f"vnext-live-canary-{_fnv1a_utf16(normalized_goal)}",
        name=normalized_goal,
        description="",
        nodes=[
            ArpNode(
                id="start",
                kind=ArpNodeKind.START,
                name="start",
                config={},
                retry=ArpRetryPolicy(
                    max_attempts=1,
                    delay_ms=0,
                    backoff=1,
                ),
                disabled=False,
                x=0,
                y=0,
            ),
            ArpNode(
                id="full-goal",
                kind=ArpNodeKind.AGENT_GOAL,
                name="full goal",
                config={"goal": normalized_goal},
                retry=ArpRetryPolicy(
                    max_attempts=1,
                    delay_ms=0,
                    backoff=1,
                ),
                disabled=False,
                x=0,
                y=100,
            ),
            ArpNode(
                id="end",
                kind=ArpNodeKind.END,
                name="end",
                config={},
                retry=ArpRetryPolicy(
                    max_attempts=1,
                    delay_ms=0,
                    backoff=1,
                ),
                disabled=False,
                x=0,
                y=200,
            ),
        ],
        edges=[
            ArpEdge(
                id="start-to-full-goal",
                source="start",
                target="full-goal",
                branch="next",
                label="",
            ),
            ArpEdge(
                id="full-goal-to-end",
                source="full-goal",
                target="end",
                branch="next",
                label="",
            ),
        ],
        variables={},
        policy=ArpPolicy(
            max_node_visits=1,
            fail_fast=True,
            require_evidence_on_done=True,
            allow_shell=False,
            allow_desktop_mutation=True,
        ),
        metadata={},
    )
    _validate_exact_graph(graph)
    return graph


def _validate_exact_graph(graph: ArpGraph) -> str:
    if type(graph) is not ArpGraph:
        raise ValueError("vNext live canary requires canonical ArpGraph")
    if graph.format != "arp/1":
        raise ValueError("vNext live canary requires arp/1")
    if graph.description != "" or graph.variables != {} or graph.metadata != {}:
        raise ValueError(
            "vNext live canary graph description, variables, and metadata must be empty"
        )
    if len(graph.nodes) != 3 or len(graph.edges) != 2:
        raise ValueError(
            "vNext live canary requires exactly start, one AGENT_GOAL, and end"
        )
    expected_nodes = (
        ("start", ArpNodeKind.START, "start", {}, 0.0),
        (
            "full-goal",
            ArpNodeKind.AGENT_GOAL,
            "full goal",
            None,
            100.0,
        ),
        ("end", ArpNodeKind.END, "end", {}, 200.0),
    )
    goal = ""
    for node, expected in zip(graph.nodes, expected_nodes, strict=True):
        node_id, kind, name, config, y = expected
        if (
            node.id != node_id
            or node.kind is not kind
            or node.name != name
            or node.disabled
            or node.timeout_s is not None
            or float(node.x) != 0.0
            or float(node.y) != y
            or node.retry.max_attempts != 1
            or node.retry.delay_ms != 0
            or float(node.retry.backoff) != 1.0
        ):
            raise ValueError(f"vNext live canary node {node_id!r} is not exact")
        if kind is ArpNodeKind.AGENT_GOAL:
            if set(node.config) != {"goal"}:
                raise ValueError("AGENT_GOAL config must contain only goal")
            raw_goal = node.config.get("goal")
            if type(raw_goal) is not str or not raw_goal.strip():
                raise ValueError("AGENT_GOAL requires a non-empty full goal")
            if raw_goal != raw_goal.strip():
                raise ValueError("AGENT_GOAL goal must already be trimmed")
            goal = raw_goal
        elif node.config != config:
            raise ValueError(f"vNext live canary node {node_id!r} config must be empty")
    expected_edges = (
        ("start-to-full-goal", "start", "full-goal"),
        ("full-goal-to-end", "full-goal", "end"),
    )
    for edge, expected in zip(graph.edges, expected_edges, strict=True):
        edge_id, source, target = expected
        if (
            edge.id != edge_id
            or edge.source != source
            or edge.target != target
            or edge.branch != "next"
            or edge.label != ""
        ):
            raise ValueError(f"vNext live canary edge {edge_id!r} is not exact")
    policy = graph.policy
    if (
        policy.max_node_visits != 1
        or policy.fail_fast is not True
        or policy.require_evidence_on_done is not True
        or policy.allow_shell is not False
        or policy.allow_desktop_mutation is not True
    ):
        raise ValueError("vNext live canary graph policy is not exact")
    expected_graph_id = f"vnext-live-canary-{_fnv1a_utf16(goal)}"
    if graph.id != expected_graph_id or graph.name != goal:
        raise ValueError("vNext live canary graph identity does not bind the full goal")
    return goal


def _freeze_budget(config_snapshot: dict[str, Any]) -> _Budget:
    vnext = _plain_dict(config_snapshot.get("vnext"), "config.vnext")
    if vnext.get("live_canary_enabled") is not True:
        raise ValueError(
            "vNext live canary is disabled; no legacy fallback was run"
        )
    values = {
        name: _positive_int(vnext.get(name), f"config.vnext.{name}")
        for name in ("max_turns", "max_runtime_s", "max_model_calls")
    }
    if values["max_turns"] > 100:
        raise ValueError("config.vnext.max_turns exceeds the bounded canary")
    if values["max_runtime_s"] > 3600:
        raise ValueError("config.vnext.max_runtime_s exceeds the bounded canary")
    if values["max_model_calls"] > 2 * values["max_turns"]:
        raise ValueError(
            "config.vnext.max_model_calls exceeds Actor plus Verifier call bounds"
        )
    return _Budget(**values)


def _freeze_model(
    config_snapshot: dict[str, Any],
    budget: _Budget,
) -> _ModelConfig:
    api = deepcopy(_plain_dict(config_snapshot.get("api"), "config.api"))
    base_url = str(api.get("base_url") or "").strip()
    api_key = str(api.get("api_key") or "").strip()
    model = str(api.get("model") or "").strip()
    if not base_url or not api_key or not model:
        raise ValueError(
            "vNext live canary requires one configured vision model endpoint"
        )
    profile = model_profile_from_config(api)
    if not profile.supports_vision:
        raise ValueError("configured vNext live canary model is not vision-capable")
    request_timeout = min(
        _positive_number(
            api.get("request_timeout_s"),
            "config.api.request_timeout_s",
        ),
        float(budget.max_runtime_s),
    )
    total_timeout = min(
        _positive_number(
            api.get("request_total_timeout_s"),
            "config.api.request_total_timeout_s",
        ),
        float(budget.max_runtime_s),
    )
    return _ModelConfig(
        api=api,
        base_url=base_url,
        api_key=api_key,
        model=model,
        request_timeout_s=request_timeout,
        total_timeout_s=total_timeout,
    )


def _process_executable_identity(process_id: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(process_id),
    )
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return ""
        value = buffer.value.strip()
        return os.path.normcase(os.path.normpath(value)) if value else ""
    finally:
        kernel32.CloseHandle(handle)


def _read_win32_target(hwnd: int) -> _TargetFacts | None:
    try:
        import win32gui
        import win32process

        value = int(hwnd)
        if (
            not value
            or not win32gui.IsWindow(value)
            or not win32gui.IsWindowVisible(value)
            or win32gui.IsIconic(value)
        ):
            return None
        root = int(win32gui.GetAncestor(value, 2) or value)
        _thread_id, process_id = win32process.GetWindowThreadProcessId(value)
        executable = _process_executable_identity(int(process_id))
        region = window_region(value)
        if region.width <= 0 or region.height <= 0:
            return None
        foreground = int(win32gui.GetForegroundWindow() or 0)
        foreground_root = (
            int(win32gui.GetAncestor(foreground, 2) or foreground)
            if foreground
            else None
        )
        if not executable:
            return None
        return _TargetFacts(
            hwnd=value,
            root_hwnd=root,
            process_id=int(process_id),
            executable_identity=executable,
            title=str(win32gui.GetWindowText(value) or ""),
            class_name=str(win32gui.GetClassName(value) or ""),
            region=(
                region.left,
                region.top,
                region.width,
                region.height,
            ),
            foreground_root_hwnd=foreground_root,
        )
    except Exception:
        return None


def _hit_test_win32_root(x: int, y: int) -> int | None:
    try:
        import win32gui
        import win32process

        hwnd = int(win32gui.WindowFromPoint((int(x), int(y))) or 0)
        if not hwnd:
            return None
        root = int(win32gui.GetAncestor(hwnd, 2) or hwnd)
        root_owner = int(win32gui.GetAncestor(root, 3) or root)
        if root_owner == root:
            return root

        # WinUI can surface ordinary in-app controls through visible,
        # owned top-level popup site bridges. GA_ROOT intentionally stops at
        # that bridge, while GA_ROOTOWNER reaches the application window.
        # Collapse the popup only within one process; cross-process overlays
        # and unrelated sibling windows remain distinct.
        _root_thread, root_pid = (
            win32process.GetWindowThreadProcessId(root)
        )
        _owner_thread, owner_pid = (
            win32process.GetWindowThreadProcessId(root_owner)
        )
        return root_owner if int(root_pid) == int(owner_pid) else root
    except Exception:
        return None


class _BoundTargetProbe:
    """Read-only validator for one exact, already-supplied target identity."""

    def __init__(
        self,
        expected: _TargetFacts,
        *,
        reader: Callable[[int], _TargetFacts | None],
        hit_test_root: Callable[[int, int], int | None],
    ) -> None:
        self._expected = expected
        self._reader = reader
        self._hit_test = hit_test_root

    def _matches(self, current: _TargetFacts | None) -> bool:
        expected = self._expected
        return bool(
            type(current) is _TargetFacts
            and current.hwnd == expected.hwnd
            and current.root_hwnd == expected.root_hwnd == expected.hwnd
            and current.process_id == expected.process_id
            and current.executable_identity == expected.executable_identity
            and current.class_name == expected.class_name
            and current.region == expected.region
            and current.foreground_root_hwnd == expected.hwnd
        )

    def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None:
        current = self._reader(int(hwnd))
        if not self._matches(current):
            return None
        assert current is not None
        return LiveTargetSnapshot(
            hwnd=current.hwnd,
            root_hwnd=current.root_hwnd,
            process_id=current.process_id,
            executable_identity=current.executable_identity,
            region=current.region,
            foreground_root_hwnd=current.foreground_root_hwnd,
        )

    def hit_test_root(self, x: int, y: int) -> int | None:
        value = self._hit_test(int(x), int(y))
        return value if type(value) is int and value > 0 else None


def _freeze_prebound_target(
    graph: ArpGraph,
    inputs: dict[str, Any],
    *,
    dependencies: _CompositionDependencies,
) -> _FrozenTarget:
    _exact_keys(inputs, {"execution_context"}, "inputs")
    context = _plain_dict(inputs["execution_context"], "execution_context")
    _exact_keys(
        context,
        {
            "format",
            "entrypoint",
            "task_id",
            "submitted_at",
            "desktop_handoff",
            "target_window",
        },
        "execution_context",
    )
    if (
        context.get("format") != "execution-context/1"
        or context.get("entrypoint") != "native_agent_cockpit"
        or context.get("task_id") != graph.id
    ):
        raise ValueError(
            "vNext live canary requires the matching native Electron execution context"
        )
    submitted_at = _positive_int(
        context.get("submitted_at"),
        "execution_context.submitted_at",
    )
    handoff = _plain_dict(
        context.get("desktop_handoff"),
        "execution_context.desktop_handoff",
    )
    _exact_keys(
        handoff,
        {
            "required",
            "strategy",
            "yielded",
            "control_surface_pid",
            "prepared_at",
            "reason",
        },
        "execution_context.desktop_handoff",
    )
    control_pid = _positive_int(
        handoff.get("control_surface_pid"),
        "execution_context.desktop_handoff.control_surface_pid",
    )
    prepared_at = _positive_int(
        handoff.get("prepared_at"),
        "execution_context.desktop_handoff.prepared_at",
    )
    if handoff.get("required") is not True:
        raise ValueError("Electron desktop handoff was not required")
    if handoff.get("yielded") is not True:
        raise ValueError("Electron desktop handoff did not yield foreground")
    if (
        handoff.get("strategy")
        != "foreground_after_control_surface_yield"
    ):
        raise ValueError("Electron desktop handoff strategy is not exact")
    if type(handoff.get("reason")) is not str:
        raise ValueError("Electron desktop handoff reason is not a string")
    if prepared_at < submitted_at:
        raise ValueError("Electron desktop handoff predates goal submission")
    target = _plain_dict(
        context.get("target_window"),
        "execution_context.target_window",
    )
    _exact_keys(
        target,
        {
            "format",
            "hwnd",
            "title",
            "process_id",
            "class_name",
            "rect",
            "captured_at",
            "foreground_verified",
            "usable",
            "failure_type",
            "reason",
        },
        "execution_context.target_window",
    )
    hwnd = _positive_int(target.get("hwnd"), "target_window.hwnd")
    process_id = _positive_int(
        target.get("process_id"),
        "target_window.process_id",
    )
    captured_at = _positive_int(
        target.get("captured_at"),
        "target_window.captured_at",
    )
    title = target.get("title")
    class_name = target.get("class_name")
    rect = target.get("rect")
    if (
        target.get("format") != "desktop-target/1"
        or target.get("foreground_verified") is not True
        or target.get("usable") is not True
        or target.get("failure_type") != ""
        or target.get("reason") != ""
        or type(title) is not str
        or not title
        or type(class_name) is not str
        or not class_name
        or type(rect) is not list
        or len(rect) != 4
        or any(type(item) is not int for item in rect)
        or rect[2] <= rect[0]
        or rect[3] <= rect[1]
        or process_id == control_pid
        or captured_at < prepared_at
    ):
        raise ValueError("Electron target descriptor is not an exact usable target")
    now_ms = int(dependencies.wall_clock() * 1000)
    age_ms = now_ms - captured_at
    if age_ms > _TARGET_MAX_AGE_MS or age_ms < -_FUTURE_CLOCK_SKEW_MS:
        raise ValueError("Electron target descriptor is stale or future-dated")
    reader = dependencies.target_reader or _read_win32_target
    hit_test = dependencies.hit_test_root or _hit_test_win32_root
    current = reader(hwnd)
    expected_region = (
        rect[0],
        rect[1],
        rect[2] - rect[0],
        rect[3] - rect[1],
    )
    mismatches: list[str] = []
    if type(current) is not _TargetFacts:
        mismatches.append("unavailable")
    else:
        if current.hwnd != hwnd:
            mismatches.append("hwnd")
        if current.root_hwnd != hwnd:
            mismatches.append("root_hwnd")
        if current.process_id != process_id:
            mismatches.append("process_id")
        if not current.executable_identity:
            mismatches.append("executable_identity")
        if current.title != title:
            mismatches.append("title")
        if current.class_name != class_name:
            mismatches.append("class_name")
        if current.region != expected_region:
            mismatches.append("region")
        if current.foreground_root_hwnd != hwnd:
            mismatches.append("foreground_root_hwnd")
    if mismatches:
        raise ValueError(
            "current Electron target mismatch: " + ",".join(mismatches)
        )
    assert current is not None
    binding_material = {
        "hwnd": hwnd,
        "root_hwnd": current.root_hwnd,
        "process_id": process_id,
        "executable_identity": current.executable_identity,
        "class_name": class_name,
        "region": list(expected_region),
        "captured_at": captured_at,
    }
    digest = hashlib.sha256(
        json.dumps(
            binding_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    probe = _BoundTargetProbe(
        current,
        reader=reader,
        hit_test_root=hit_test,
    )
    if probe.snapshot(hwnd) is None:
        raise ValueError("pre-bound target changed during admission")
    return _FrozenTarget(
        hwnd=hwnd,
        root_hwnd=current.root_hwnd,
        process_id=process_id,
        executable_identity=current.executable_identity,
        title=title,
        class_name=class_name,
        region=expected_region,
        captured_at_ms=captured_at,
        binding_id=f"vnext-target-{digest[:24]}",
        probe=probe,
    )


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _tool_catalog() -> ToolCatalog:
    point = _object_schema(
        {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        ["image_x", "image_y"],
    )
    schemas = {
        "click": point,
        "double_click": point,
        "drag": _object_schema(
            {
                "image_x1": {"type": "number", "minimum": 0},
                "image_y1": {"type": "number", "minimum": 0},
                "image_x2": {"type": "number", "minimum": 0},
                "image_y2": {"type": "number", "minimum": 0},
            },
            ["image_x1", "image_y1", "image_x2", "image_y2"],
        ),
        "scroll": _object_schema(
            {
                "image_x": {"type": "number", "minimum": 0},
                "image_y": {"type": "number", "minimum": 0},
                "amount": {
                    "type": "integer",
                    "minimum": -1200,
                    "maximum": 1200,
                    "not": {"const": 0},
                },
            },
            ["image_x", "image_y", "amount"],
        ),
        "type_text": _object_schema(
            {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_TYPE_TEXT_CODEPOINTS,
                }
            },
            ["text"],
        ),
        "hotkey": _object_schema(
            {
                "keys": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": sorted(CANONICAL_HOTKEY_KEYS),
                    },
                    "minItems": 1,
                    "maxItems": len(CANONICAL_HOTKEY_MODIFIERS) + 1,
                    "uniqueItems": True,
                }
            },
            ["keys"],
        ),
        "claim_obligation_done": _object_schema(
            {
                "concise_claim": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 16384,
                    "pattern": _NON_WHITESPACE_PATTERN,
                }
            },
            ["concise_claim"],
        ),
        "blocked": _object_schema(
            {
                "blocker_kind": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": _BLOCKER_KIND_PATTERN,
                },
                "observed_facts": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 16384,
                        "pattern": _NON_WHITESPACE_PATTERN,
                    },
                    "maxItems": 64,
                    "uniqueItems": True,
                },
            },
            ["blocker_kind", "observed_facts"],
        ),
    }
    names = (
        "click",
        "double_click",
        "drag",
        "scroll",
        "type_text",
        "hotkey",
        "claim_obligation_done",
        "blocked",
    )
    tools = []
    for index, name in enumerate(names, start=1):
        order = (
            20
            if name == "claim_obligation_done"
            else 21
            if name == "blocked"
            else index
        )
        mutation = (
            MutationKind.GUI
            if name in _APPROVED_ACTIONS
            else MutationKind.NONE
        )
        tools.append(
            ToolSpec(
                name=name,
                schema=schemas[name],
                route=ToolRoute.VISUAL_ACTOR,
                mutation_kind=mutation,
                execution_medium=(
                    ExecutionMedium.GUI
                    if mutation is MutationKind.GUI
                    else ExecutionMedium.CONTROL
                ),
                required_any_capabilities=(
                    ("visual.interact",)
                    if mutation is MutationKind.GUI
                    else ()
                ),
                prerequisites=(
                    _GUI_GATES if mutation is MutationKind.GUI else ()
                ),
                order=order,
            )
        )
    return ToolCatalog(tuple(tools))


def _jpeg_payload(image_b64: str) -> bytes | None:
    try:
        payload = base64.b64decode(image_b64, validate=True)
    except Exception:
        return None
    if (
        len(payload) < 4
        or payload[:2] != b"\xff\xd8"
        or payload[-2:] != b"\xff\xd9"
    ):
        return None
    return payload


def _jpeg_dimensions(image_b64: str) -> tuple[int, int] | None:
    """Read JPEG SOF dimensions without accepting marker-only payloads."""

    payload = _jpeg_payload(image_b64)
    if payload is None:
        return None
    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(payload):
        if payload[offset] != 0xFF:
            return None
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            return None
        marker = payload[offset]
        offset += 1
        if marker == 0xD9:
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(payload):
            return None
        segment_length = int.from_bytes(
            payload[offset : offset + 2],
            "big",
        )
        if segment_length < 2 or offset + segment_length > len(payload):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(
                payload[offset + 3 : offset + 5],
                "big",
            )
            width = int.from_bytes(
                payload[offset + 5 : offset + 7],
                "big",
            )
            if width <= 0 or height <= 0:
                return None
            return width, height
        if marker == 0xDA:
            return None
        offset += segment_length
    return None


def _submitted_size(
    region: Region,
    *,
    max_side: int = _MAX_SUBMITTED_SIDE,
) -> tuple[int, int]:
    scale = min(
        1.0,
        max(1, int(max_side)) / max(1, region.width, region.height),
    )
    return (
        max(1, int(region.width * scale)),
        max(1, int(region.height * scale)),
    )


def _capture_exact_target_jpeg(
    hwnd: int,
    *,
    region: Region,
) -> str:
    """Capture only an unchanged, fully visible target rectangle."""

    if type(region) is not Region or region.width < 1 or region.height < 1:
        return ""
    desktop = virtual_screen_region()
    if region.clamp_to(desktop) != region:
        return ""
    image_b64 = capture_window_jpeg_b64(
        int(hwnd),
        max_side=_MAX_SUBMITTED_SIDE,
        region=region,
    )
    if _jpeg_dimensions(image_b64) != _submitted_size(region):
        return ""
    return image_b64


def _jpeg_is_valid(image_b64: str) -> bool:
    return _jpeg_dimensions(image_b64) is not None


class _LiveRuntimeState:
    """Project the existing frame authority into the Runtime state port."""

    def __init__(
        self,
        *,
        reducer: MissionReducer,
        registry: EvidenceRegistry,
        binding: TargetBinding,
        target_evidence: EvidenceRef,
        target_probe: _BoundTargetProbe,
        authority: FrameAuthority,
        observer: FrameObserver,
        pause_controller: Any,
        interaction_mode: InteractionMode = InteractionMode.AUTO,
    ) -> None:
        self._reducer = reducer
        self._registry = registry
        self._binding = binding
        self._target_evidence = target_evidence
        self._probe = target_probe
        self._authority = authority
        self._observer = observer
        self._pause_controller = pause_controller
        self._interaction_mode = InteractionMode(interaction_mode)

    def _frame_ref(self, state: FrameState) -> EvidenceRef:
        identity = RuntimeFrameIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
        )
        digest = hashlib.sha256(
            (
                f"{self._registry.run_id}|{state.frame_id}|"
                f"{identity.payload_hash}"
            ).encode("utf-8")
        ).hexdigest()
        return EvidenceRef(
            id=f"vnext-current-frame-{digest[:24]}",
            mission_id=self._registry.mission_id,
            run_id=self._registry.run_id,
            obligation_id=_OBLIGATION_ID,
            kind=CURRENT_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            payload_hash=identity.payload_hash,
            frame_id=state.frame_id,
            created_at=state.captured_at,
        )

    def _capture_current(self) -> FrameState:
        first = self._probe.snapshot(self._binding.hwnd)
        if first is None:
            raise RuntimeError("pre-bound target identity is no longer current")
        previous = self._authority.current
        expected_previous = previous.frame_id if previous is not None else 0
        captured = self._observer.capture(
            epoch=self._binding.epoch,
            hwnd=self._binding.hwnd,
            phase="observe",
            provided_region=Region(*first.region),
            expected_previous_frame_id=expected_previous,
        )
        if (
            captured is None
            or captured.scope != "target_window"
            or not _jpeg_is_valid(captured.image_b64)
            or _jpeg_dimensions(captured.image_b64)
            != captured.submitted_size
            or self._probe.snapshot(self._binding.hwnd) != first
        ):
            raise RuntimeError("fresh target-window JPEG capture failed closed")
        committed = self._observer.commit(captured)
        if (
            committed.hwnd != self._binding.hwnd
            or committed.epoch != self._binding.epoch
            or committed.region != first.region
            or committed.scope != "target_window"
            or self._probe.snapshot(self._binding.hwnd) != first
        ):
            raise RuntimeError("target changed while the frame was committed")
        frame_ref = self._frame_ref(committed)
        self._registry.append(frame_ref)
        event = self._reducer.event(
            "target_bound",
            payload={"target_binding": self._binding.to_dict()},
            evidence_refs=(self._target_evidence.id, frame_ref.id),
        )
        self._reducer.apply(event)
        BUS.publish(
            "vnext_gui_observation",
            runtime_version=_RUNTIME_VERSION,
            no_legacy_fallback=True,
            run_id=self._registry.run_id,
            mission_id=self._registry.mission_id,
            obligation_id=_OBLIGATION_ID,
            interaction_mode=self._interaction_mode.value,
            execution_medium=ExecutionMedium.CONTROL.value,
            observation_kind="fresh_authoritative_target_frame",
            target_binding_id=self._binding.binding_id,
            target_hwnd=self._binding.hwnd,
            frame_id=committed.frame_id,
            frame_epoch=committed.epoch,
            frame_scope=committed.scope,
            authoritative=True,
            fresh=True,
            evidence_refs=[frame_ref.id],
            parameter_visibility=_PRIVATE_RUNTIME_EVIDENCE,
        )
        return committed

    def _runtime_frame(self, state: FrameState) -> RuntimeFrame:
        identity = RuntimeFrameIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
        )
        refs = tuple(
            sorted(
                ref.id
                for ref in self._registry.snapshot()
                if ref.obligation_id == _OBLIGATION_ID
                and ref.frame_id == state.frame_id
                and ref.payload_hash == identity.payload_hash
                and ref.kind
                in {
                    CURRENT_FRAME_EVIDENCE_KIND,
                    POST_ACTION_FRAME_EVIDENCE_KIND,
                }
            )
        )
        if not refs:
            raise RuntimeError("authoritative frame has no run-scoped evidence")
        return RuntimeFrame(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_b64=state.image_b64,
            image_sha256=state.image_sha256,
            evidence_refs=refs,
        )

    def _snapshot(self, state: FrameState, *, turn_index: int) -> RuntimeSnapshot:
        current_target = self._probe.snapshot(self._binding.hwnd)
        if (
            current_target is None
            or state.hwnd != current_target.hwnd
            or state.region != current_target.region
            or state.scope != "target_window"
        ):
            raise RuntimeError("current target/frame identity no longer matches")
        frame = self._runtime_frame(state)
        refs_by_id = {
            ref.id: ref for ref in self._registry.snapshot()
        }
        is_unreconciled_post_frame = any(
            refs_by_id[evidence_id].kind == POST_ACTION_FRAME_EVIDENCE_KIND
            for evidence_id in frame.evidence_refs
        )
        paused = bool(
            getattr(self._pause_controller, "paused", False)
        ) and not is_unreconciled_post_frame
        return RuntimeSnapshot(
            broker_state=BrokerRuntimeState(
                turn_index=turn_index,
                available_capabilities=("visual.interact",),
                interaction_mode=self._interaction_mode,
                target=TargetState(
                    binding_id=self._binding.binding_id,
                    hwnd=self._binding.hwnd,
                    epoch=self._binding.epoch,
                    live=True,
                ),
                frame=frame.broker_frame(
                    authoritative=True,
                    fresh=True,
                ),
                pointer=PointerLeaseState(PointerDisposition.RELEASED),
                runtime_healthy=True,
                # Pause is an outer safe-boundary gate. Marking a completed
                # atomic action's post-frame paused would invalidate reconcile.
                paused=paused,
                canceled=False,
            ),
            frame=frame,
            target=RuntimeTargetIdentity.from_binding(self._binding),
        )

    def observe(
        self,
        _projection: Any,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot:
        return self._snapshot(
            self._capture_current(),
            turn_index=turn_index,
        )

    def current(
        self,
        _projection: Any,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot:
        state = self._authority.current
        if state is None:
            raise RuntimeError("authoritative frame is unavailable")
        return self._snapshot(state, turn_index=turn_index)


class _ModelCallCounter:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._count = 0
        self._active_roles: set[str] = set()
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def begin(self, model_role: str) -> None:
        with self._lock:
            if model_role in self._active_roles:
                raise RuntimeError("vNext model role is already in flight")
            if self._count + len(self._active_roles) >= self._maximum:
                raise RuntimeError("vNext model-call budget exhausted")
            self._active_roles.add(model_role)

    def record_attempt(self, model_role: str) -> None:
        with self._lock:
            if model_role not in self._active_roles:
                return
            self._active_roles.remove(model_role)
            self._count += 1

    def finish(self, model_role: str) -> None:
        with self._lock:
            self._active_roles.discard(model_role)


def _remaining_model_timeout(
    build: _SessionBuild,
    configured_timeout_s: float,
) -> float:
    remaining = float(build.deadline()) - float(
        build.dependencies.monotonic()
    )
    if not 0 < remaining < float("inf"):
        raise RuntimeError("vNext runtime time budget exhausted")
    return min(float(configured_timeout_s), remaining)


class _DeadlineModelAdapter:
    """Add the frozen run's remaining deadline to every SDK request."""

    def __init__(self, adapter: Any, build: _SessionBuild) -> None:
        self._adapter = adapter
        self._build = build

    def prepare_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        prepared = self._adapter.prepare_chat_completion(**kwargs)
        if type(prepared) is not dict:
            raise RuntimeError("model adapter returned a non-object request")
        request = deepcopy(prepared)
        request["timeout"] = _remaining_model_timeout(
            self._build,
            min(
                self._build.model.request_timeout_s,
                self._build.model.total_timeout_s,
            ),
        )
        tools = request.get("tools")
        if tools is not None:
            if type(tools) is not list:
                raise RuntimeError("Visual Actor tools must be an array")
            for tool in tools:
                if type(tool) is not dict:
                    raise RuntimeError("Visual Actor tool must be an object")
                function = tool.get("function")
                if type(function) is not dict:
                    raise RuntimeError(
                        "Visual Actor tool function must be an object"
                    )
                if function.get("name") not in _APPROVED_ACTIONS:
                    continue
                parameters = function.get("parameters")
                properties = (
                    parameters.get("properties")
                    if type(parameters) is dict
                    else None
                )
                local_intent = (
                    properties.get("local_intent")
                    if type(properties) is dict
                    else None
                )
                if (
                    type(local_intent) is not dict
                    or local_intent.get("type") != "string"
                    or local_intent.get("minLength") != 1
                ):
                    raise RuntimeError(
                        "Visual Actor local_intent schema is unavailable"
                    )
                local_intent["pattern"] = _NON_WHITESPACE_PATTERN
        return request

    def send_prepared_chat_completion(
        self,
        params: dict[str, Any],
    ) -> Any:
        return self._adapter.send_prepared_chat_completion(params)


def _close_resources(
    resources: tuple[tuple[str, Any], ...],
) -> tuple[Exception, ...]:
    errors: list[Exception] = []
    for label, resource in resources:
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception as exc:
            errors.append(
                RuntimeError(f"{label} close failed: {exc}")
            )
    return tuple(errors)


class _BudgetedActor:
    def __init__(self, role: LiveVisualActor, counter: _ModelCallCounter) -> None:
        self._role = role
        self._counter = counter

    def propose(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self._counter.begin(VISUAL_ACTOR_MODEL_ROLE)
        try:
            return self._role.propose(*args, **kwargs)
        finally:
            self._counter.finish(VISUAL_ACTOR_MODEL_ROLE)


class _BudgetedVerifier:
    verifier_kind = VISUAL_VERIFIER_KIND

    def __init__(
        self,
        role: LiveVisualObligationVerifier,
        counter: _ModelCallCounter,
    ) -> None:
        self._role = role
        self._counter = counter

    def assess(self, *args: Any, **kwargs: Any) -> Any:
        self._counter.begin(VISUAL_VERIFIER_MODEL_ROLE)
        try:
            return self._role.assess(*args, **kwargs)
        finally:
            self._counter.finish(VISUAL_VERIFIER_MODEL_ROLE)


class _RawReceiptTrace:
    def record(
        self,
        receipt: RawActionReceipt,
        *,
        metadata: Mapping[str, Any],
    ) -> None:
        BUS.publish(
            "vnext_raw_receipt",
            runtime_version=_RUNTIME_VERSION,
            no_legacy_fallback=True,
            run_id=str(metadata.get("run_id") or ""),
            mission_id=str(metadata.get("mission_id") or ""),
            decision_id=str(metadata.get("decision_id") or ""),
            controller_action=str(
                metadata.get("controller_action") or ""
            ),
            raw_action=receipt.action,
            outcome=receipt.outcome.value,
            target_hwnd=int(metadata.get("target_hwnd") or 0),
            target_binding_id=str(
                metadata.get("target_binding_id") or ""
            ),
            frame_id=int(metadata.get("frame_id") or 0),
            frame_epoch=int(metadata.get("frame_epoch") or 0),
            input_completed=receipt.input_completed,
            error_kind=receipt.error_kind,
            parameter_visibility=_PRIVATE_RUNTIME_EVIDENCE,
        )


def _rebuild_runtime_evidence_anchor(
    plane: RuntimeEvidencePlane,
    journal: RuntimeEvidenceJournal,
    *,
    close_journal: Callable[[], None],
) -> tuple[Any, dict[str, Any]]:
    expected_head = journal.head
    run_directory = journal.run_directory
    close_journal()
    report = RuntimeEvidencePlane.rebuild_report(
        run_directory,
        plane.mission_id,
        plane.run_id,
        expected_journal_head_sha256=expected_head,
    )
    anchor = {
        "mission_id": plane.mission_id,
        "run_id": plane.run_id,
        "journal_head_sha256": report.journal_head_sha256,
        "journal_event_count": report.journal_event_count,
        "report_sha256": runtime_evidence_report_sha256(report),
        "report_status": report.status.value,
        "missing_evidence_count": len(report.missing_evidence),
        "verification_failure_count": len(
            report.verification_failures
        ),
    }
    return report, anchor


def _publish_runtime_evidence_anchor(anchor: Mapping[str, Any]) -> None:
    BUS.publish(
        "runtime_evidence_sealed",
        runtime_version=_RUNTIME_VERSION,
        no_legacy_fallback=True,
        **dict(anchor),
    )


class _LiveCanarySession:
    def __init__(
        self,
        *,
        runtime: MissionRuntime,
        reducer: MissionReducer,
        registry: EvidenceRegistry,
        permits: MutationPermitAuthority,
        authority: FrameAuthority,
        binding: TargetBinding,
        counter: _ModelCallCounter,
        clients: tuple[Any, Any],
        runtime_evidence: RuntimeEvidencePlane | None = None,
        frame_artifacts: FrameArtifactWriter | None = None,
        evidence_journal: RuntimeEvidenceJournal | None = None,
    ) -> None:
        evidence_resources = (
            runtime_evidence,
            frame_artifacts,
            evidence_journal,
        )
        if any(item is not None for item in evidence_resources) and not all(
            item is not None for item in evidence_resources
        ):
            raise ValueError(
                "runtime evidence plane, artifact writer, and journal "
                "must be injected together"
            )
        if runtime_evidence is not None:
            if frame_artifacts is None or evidence_journal is None:
                raise ValueError(
                    "runtime evidence resources must be injected together"
                )
            if (
                runtime_evidence.journal is not evidence_journal
                or runtime_evidence.mission_id
                != evidence_journal.mission_id
                or runtime_evidence.run_id != evidence_journal.run_id
                or frame_artifacts.run_directory
                != evidence_journal.run_directory
            ):
                raise ValueError(
                    "runtime evidence resources must share one run scope"
                )
        self._runtime = runtime
        self._reducer = reducer
        self._registry = registry
        self._permits = permits
        self._authority = authority
        self._binding = binding
        self._counter = counter
        self._clients = clients
        self._runtime_evidence = runtime_evidence
        self._frame_artifacts = frame_artifacts
        self._evidence_journal = evidence_journal
        self._evidence_journal_close_attempted = False
        self._runtime_evidence_anchor: dict[str, Any] | None = None
        self._closed = False
        self._lock = threading.Lock()

    @property
    def model_calls(self) -> int:
        return self._counter.count

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: Any,
    ) -> MissionRuntimeResult:
        return self._runtime.run_one_turn(
            turn_index=turn_index,
            stop_event=stop_event,
        )

    def completion_snapshot(self) -> Mapping[str, Any]:
        projection = self._reducer.projection
        obligation = projection.obligation_state(_OBLIGATION_ID)
        frame = self._authority.current
        refs_by_id = {
            ref.id: ref for ref in self._registry.snapshot()
        }
        verified_bindings = [
            {
                "id": ref.id,
                "mission_id": ref.mission_id,
                "run_id": ref.run_id,
                "obligation_id": ref.obligation_id,
                "frame_id": ref.frame_id,
            }
            for evidence_id in obligation.verified_evidence_refs
            if (ref := refs_by_id.get(evidence_id)) is not None
        ]
        return {
            "mission_id": projection.mission_id,
            "run_id": projection.run_id,
            "mission_revision": projection.revision,
            "mission_status": projection.status.value,
            "mission_verification_id": projection.mission_verification_id,
            "obligation_verification_id": obligation.verification_id,
            "verified_evidence_refs": list(
                obligation.verified_evidence_refs
            ),
            "verified_evidence_bindings": verified_bindings,
            "accepted_event_ids": list(projection.accepted_event_ids),
            "target_binding_id": self._binding.binding_id,
            "target_hwnd": self._binding.hwnd,
            "frame_id": frame.frame_id if frame is not None else None,
            "frame_epoch": frame.epoch if frame is not None else None,
            "result": self._runtime.verified_completion_result,
        }

    def turn_snapshot(self) -> Mapping[str, Any]:
        projection = self._reducer.projection
        frame = self._authority.current
        transition = projection.last_transition
        return {
            "mission_revision": projection.revision,
            "mission_status": projection.status.value,
            "target_binding_id": self._binding.binding_id,
            "target_hwnd": self._binding.hwnd,
            "frame_id": frame.frame_id if frame is not None else None,
            "frame_epoch": frame.epoch if frame is not None else None,
            "frame_evidence_refs": [
                ref.id
                for ref in self._registry.snapshot()
                if frame is not None and ref.frame_id == frame.frame_id
            ],
            "action_effect": (
                None
                if transition is None
                or transition.action_effect is None
                else transition.action_effect.to_dict()
            ),
        }

    @property
    def runtime_evidence_anchor(self) -> Mapping[str, Any] | None:
        if self._runtime_evidence_anchor is None:
            return None
        return deepcopy(self._runtime_evidence_anchor)

    def _close_evidence_journal_once(self) -> None:
        journal = self._evidence_journal
        if journal is None or self._evidence_journal_close_attempted:
            return
        self._evidence_journal_close_attempted = True
        journal.close()

    def _seal_runtime_evidence(self) -> None:
        plane = self._runtime_evidence
        journal = self._evidence_journal
        if plane is None or journal is None:
            return
        report, anchor = _rebuild_runtime_evidence_anchor(
            plane,
            journal,
            close_journal=self._close_evidence_journal_once,
        )
        self._runtime_evidence_anchor = anchor
        _publish_runtime_evidence_anchor(anchor)
        if (
            self._reducer.projection.status is MissionStatus.VERIFIED
            and (
                report.status.value != "complete"
                or report.missing_evidence
                or report.verification_failures
            )
        ):
            raise RuntimeError(
                "verified mission lacks complete runtime evidence"
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        errors: list[Exception] = []
        try:
            self._seal_runtime_evidence()
        except Exception:
            errors.append(
                RuntimeError("runtime evidence seal failed")
            )
            try:
                self._close_evidence_journal_once()
            except Exception:
                errors.append(
                    RuntimeError(
                        "runtime evidence journal close failed"
                    )
                )
        errors.extend(
            _close_resources(
                (
                    ("mutation permit authority", self._permits),
                    ("evidence registry", self._registry),
                    ("Visual Actor client", self._clients[0]),
                    (
                        "Visual ObligationVerifier client",
                        self._clients[1],
                    ),
                )
            )
        )
        if errors:
            raise ExceptionGroup(
                "vNext live canary session cleanup failed",
                errors,
            )


def _model_attempt_trace(
    *,
    build: _SessionBuild,
    model_role: str,
    counter: _ModelCallCounter,
) -> Callable[[ModelAttempt], None]:
    def record(attempt: ModelAttempt) -> None:
        counter.record_attempt(model_role)
        BUS.publish(
            "vnext_model_call",
            runtime_version=_RUNTIME_VERSION,
            no_legacy_fallback=True,
            run_id=build.run_id,
            mission_id=build.mission_id,
            model=build.model.model,
            model_role=model_role,
            attempt=attempt.attempt,
            ok=attempt.ok,
            request_fingerprint=attempt.request_fingerprint,
            elapsed_ms=attempt.elapsed_ms,
            error_kind=attempt.error_kind,
            retryable=attempt.retryable,
        )

    return record


def _target_evidence(
    build: _SessionBuild,
) -> tuple[EvidenceRef, TargetBinding]:
    payload = build.target.trace_identity()
    payload_hash = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    evidence_id = (
        "vnext-target-evidence-"
        + hashlib.sha256(
            f"{build.run_id}|{payload_hash}".encode("utf-8")
        ).hexdigest()[:24]
    )
    evidence = EvidenceRef(
        id=evidence_id,
        mission_id=build.mission_id,
        run_id=build.run_id,
        obligation_id=_OBLIGATION_ID,
        kind="target_binding",
        trust="deterministic",
        payload_hash=payload_hash,
        created_at=build.target.captured_at_ms / 1000.0,
    )
    binding = TargetBinding(
        binding_id=build.target.binding_id,
        hwnd=build.target.hwnd,
        process_id=build.target.process_id,
        executable_identity=build.target.executable_identity,
        title_snapshot=build.target.title,
        established_by_evidence_ref=evidence.id,
        epoch=build.target.captured_at_ms,
    )
    return evidence, binding


def _create_runtime_evidence(
    build: _SessionBuild,
) -> tuple[
    RuntimeEvidencePlane,
    FrameArtifactWriter,
    RuntimeEvidenceJournal,
]:
    root = (
        _RUNTIME_EVIDENCE_ROOT
        if build.dependencies.runtime_evidence_root is None
        else build.dependencies.runtime_evidence_root
    )
    if not isinstance(root, Path):
        raise TypeError("runtime_evidence_root must be a pathlib.Path")
    root = root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    journal = RuntimeEvidenceJournal.create(
        root / build.run_id,
        build.mission_id,
        build.run_id,
    )
    try:
        artifacts = FrameArtifactWriter.create(journal.run_directory)
        plane = RuntimeEvidencePlane(
            build.mission_id,
            build.run_id,
            journal=journal,
        )
    except Exception as exc:
        cleanup_errors = _close_resources(
            (("runtime evidence journal", journal),)
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext evidence resource setup and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise
    return plane, artifacts, journal


def _seal_created_runtime_evidence(
    plane: RuntimeEvidencePlane,
    journal: RuntimeEvidenceJournal,
) -> dict[str, Any]:
    close_attempted = False

    def close_once() -> None:
        nonlocal close_attempted
        if close_attempted:
            return
        close_attempted = True
        journal.close()

    try:
        _report, anchor = _rebuild_runtime_evidence_anchor(
            plane,
            journal,
            close_journal=close_once,
        )
        _publish_runtime_evidence_anchor(anchor)
        return anchor
    except Exception as exc:
        try:
            close_once()
        except Exception:
            raise ExceptionGroup(
                "vNext runtime evidence seal and cleanup failed",
                [
                    exc,
                    RuntimeError(
                        "runtime evidence journal close failed"
                    ),
                ],
            ) from exc
        raise


def _seal_unstarted_runtime_evidence(
    build: _SessionBuild,
) -> dict[str, Any]:
    plane, _frame_artifacts, journal = _create_runtime_evidence(build)
    return _seal_created_runtime_evidence(plane, journal)


def _build_live_session(build: _SessionBuild) -> _LiveCanarySession:
    runtime_evidence, frame_artifacts, evidence_journal = (
        _create_runtime_evidence(build)
    )
    try:
        age_ms = (
            int(build.dependencies.wall_clock() * 1000)
            - build.target.captured_at_ms
        )
        if (
            age_ms > _TARGET_MAX_AGE_MS
            or age_ms < -_FUTURE_CLOCK_SKEW_MS
        ):
            raise RuntimeError(
                "pre-bound Electron target expired before vNext execution"
            )
        if build.target.probe.snapshot(build.target.hwnd) is None:
            raise RuntimeError(
                "pre-bound target changed before vNext composition"
            )
        definition = MissionDefinition(
            mission_id=build.mission_id,
            run_id=build.run_id,
            original_goal=build.goal,
            obligations=(
                ObligationSpec(
                    id=_OBLIGATION_ID,
                    desired_state=build.goal,
                    acceptance_criteria=(build.goal,),
                    required_capabilities=("visual.interact",),
                    evidence_policy=EvidencePolicy(
                        allow_independent_visual_verifier=True,
                        max_verification_attempts=(
                            _MAX_VISUAL_VERIFICATION_ATTEMPTS
                        ),
                        uncertain_policy="blocked",
                    ),
                ),
            ),
            source="vnext-live-canary",
            experience_policy="disabled",
        )
    except Exception as exc:
        try:
            _seal_created_runtime_evidence(
                runtime_evidence,
                evidence_journal,
            )
        except Exception as cleanup_exc:
            raise ExceptionGroup(
                "vNext evidence admission and cleanup failed",
                [exc, cleanup_exc],
            ) from exc
        raise
    registry: EvidenceRegistry | None = None
    try:
        registry = EvidenceRegistry(build.mission_id, build.run_id)
        reducer = MissionReducer(definition, evidence=registry)
        target_evidence, binding = _target_evidence(build)
        registry.append(target_evidence)
        reducer.apply(
            reducer.event(
                "target_bound",
                payload={"target_binding": binding.to_dict()},
                evidence_refs=(target_evidence.id,),
            )
        )
        authority = FrameAuthority()
        observer = FrameObserver(
            authority,
            capture=_capture_exact_target_jpeg,
            max_submitted_side=_MAX_SUBMITTED_SIDE,
        )
        state = _LiveRuntimeState(
            reducer=reducer,
            registry=registry,
            binding=binding,
            target_evidence=target_evidence,
            target_probe=build.target.probe,
            authority=authority,
            observer=observer,
            pause_controller=build.pause_controller,
            interaction_mode=build.interaction_mode,
        )
    except Exception as exc:
        cleanup_errors: list[Exception] = []
        try:
            _seal_created_runtime_evidence(
                runtime_evidence,
                evidence_journal,
            )
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        cleanup_errors.extend(
            _close_resources((("evidence registry", registry),))
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext evidence composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise
    permits: MutationPermitAuthority | None = None
    try:
        permits = MutationPermitAuthority(build.mission_id, build.run_id)
        raw = RawDeviceExecutor(
            RawDeviceBackends(
                admitted_physical_click=ADMITTED_PHYSICAL_CLICK_BACKEND,
                admitted_physical_drag=ADMITTED_PHYSICAL_DRAG_BACKEND,
                admitted_physical_scroll=ADMITTED_PHYSICAL_SCROLL_BACKEND,
                admitted_physical_type_text=ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND,
                admitted_physical_hotkey=ADMITTED_PHYSICAL_HOTKEY_BACKEND,
            )
        )
        controller = AtomicDesktopAdapter(
            frame_authority=authority,
            frame_observer=observer,
            frozen_target_binding=binding,
            target_probe=build.target.probe,
            mutation_permits=permits,
            raw_device=raw,
            receipt_trace_sink=_RawReceiptTrace(),
            runtime_evidence_plane=runtime_evidence,
            frame_artifact_writer=frame_artifacts,
        )
    except Exception as exc:
        cleanup_errors = []
        try:
            _seal_created_runtime_evidence(
                runtime_evidence,
                evidence_journal,
            )
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        cleanup_errors.extend(
            _close_resources(
                (
                    ("mutation permit authority", permits),
                    ("evidence registry", registry),
                )
            )
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext live canary owner composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise
    raw_clients: list[Any] = []
    try:
        for _role in (VISUAL_ACTOR_MODEL_ROLE, VISUAL_VERIFIER_MODEL_ROLE):
            raw_clients.append(
                create_openai_client(
                    base_url=build.model.base_url,
                    api_key=build.model.api_key,
                    timeout=_remaining_model_timeout(
                        build,
                        min(
                            build.model.request_timeout_s,
                            build.model.total_timeout_s,
                        ),
                    ),
                    client_factory=build.dependencies.client_factory,
                )
            )
        actor_client = _DeadlineModelAdapter(
            build_model_adapter(
                raw_clients[0],
                build.model.api,
            ),
            build,
        )
        verifier_client = _DeadlineModelAdapter(
            build_model_adapter(
                raw_clients[1],
                build.model.api,
            ),
            build,
        )
        counter = _ModelCallCounter(build.budget.max_model_calls)
        actor = _BudgetedActor(
            LiveVisualActor(
                client=actor_client,
                model=build.model.model,
                runtime=ModelCallRuntime(
                    clock=build.dependencies.monotonic,
                ),
                total_timeout_s=_remaining_model_timeout(
                    build,
                    build.model.total_timeout_s,
                ),
                on_attempt=_model_attempt_trace(
                    build=build,
                    model_role=VISUAL_ACTOR_MODEL_ROLE,
                    counter=counter,
                ),
            ),
            counter,
        )
        verifier = _BudgetedVerifier(
            LiveVisualObligationVerifier(
                client=verifier_client,
                model=build.model.model,
                runtime=ModelCallRuntime(
                    clock=build.dependencies.monotonic,
                ),
                total_timeout_s=_remaining_model_timeout(
                    build,
                    build.model.total_timeout_s,
                ),
                on_attempt=_model_attempt_trace(
                    build=build,
                    model_role=VISUAL_VERIFIER_MODEL_ROLE,
                    counter=counter,
                ),
            ),
            counter,
        )
        catalog = _tool_catalog()
        profile = ExecutionPolicy(
            provider_availability=("obligation_verifier",),
        ).safe_default(
            build.mission_id,
            definition.goal_revision,
            available_capabilities=("visual.interact",),
        )
        runtime = MissionRuntime(
            reducer=reducer,
            profile=profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=actor,
            state=state,
            controller=controller,
            verifier=verifier,
            evidence_registry=registry,
            mutation_permits=permits,
            runtime_evidence_plane=runtime_evidence,
        )
        return _LiveCanarySession(
            runtime=runtime,
            reducer=reducer,
            registry=registry,
            permits=permits,
            authority=authority,
            binding=binding,
            counter=counter,
            clients=(raw_clients[0], raw_clients[1]),
            runtime_evidence=runtime_evidence,
            frame_artifacts=frame_artifacts,
            evidence_journal=evidence_journal,
        )
    except Exception as exc:
        cleanup_errors = []
        try:
            _seal_created_runtime_evidence(
                runtime_evidence,
                evidence_journal,
            )
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        cleanup_errors.extend(
            _close_resources(
                (
                    ("mutation permit authority", permits),
                    ("evidence registry", registry),
                    *tuple(
                        (f"model client {index}", client)
                        for index, client in enumerate(
                            raw_clients,
                            start=1,
                        )
                    ),
                )
            )
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext live canary composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise


class _DeadlineStop:
    def __init__(
        self,
        outer: Any,
        *,
        clock: Callable[[], float],
        deadline: Callable[[], float],
    ) -> None:
        self._outer = outer
        self._clock = clock
        self._deadline = deadline
        self._local = threading.Event()

    def is_set(self) -> bool:
        return bool(
            self._outer.is_set()
            or self._local.is_set()
            or self._clock() >= self._deadline()
        )

    def set(self) -> None:
        self._local.set()

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        timeout_deadline = (
            None
            if timeout is None
            else self._clock() + max(0.0, timeout)
        )
        while not self.is_set():
            remaining = self._deadline() - self._clock()
            if timeout_deadline is not None:
                remaining = min(
                    remaining,
                    timeout_deadline - self._clock(),
                )
            if remaining <= 0:
                break
            self._outer.wait(min(0.05, remaining))
        return self.is_set()


def _completion_binding_error(
    snapshot: Mapping[str, Any],
    *,
    mission_id: str,
    run_id: str,
    target: _FrozenTarget,
) -> str | None:
    verified_refs = snapshot.get("verified_evidence_refs")
    bindings = snapshot.get("verified_evidence_bindings")
    frame_id = snapshot.get("frame_id")
    if (
        snapshot.get("mission_id") != mission_id
        or snapshot.get("run_id") != run_id
        or snapshot.get("mission_status") != MissionStatus.VERIFIED.value
        or type(snapshot.get("mission_verification_id")) is not str
        or not snapshot.get("mission_verification_id")
        or type(snapshot.get("obligation_verification_id")) is not str
        or not snapshot.get("obligation_verification_id")
        or type(verified_refs) is not list
        or not verified_refs
        or any(
            type(item) is not str or not item
            for item in verified_refs
        )
        or len(verified_refs) != len(set(verified_refs))
        or type(bindings) is not list
        or len(bindings) != len(verified_refs)
        or snapshot.get("target_binding_id") != target.binding_id
        or snapshot.get("target_hwnd") != target.hwnd
        or type(frame_id) is not int
        or frame_id <= 0
        or snapshot.get("frame_epoch") != target.captured_at_ms
    ):
        return (
            "vNext Runtime status lacked exact run-bound "
            "verified mission evidence"
        )
    binding_ids: list[str] = []
    for binding in bindings:
        if (
            type(binding) is not dict
            or set(binding)
            != {
                "id",
                "mission_id",
                "run_id",
                "obligation_id",
                "frame_id",
            }
            or binding.get("id") not in verified_refs
            or binding.get("mission_id") != mission_id
            or binding.get("run_id") != run_id
            or binding.get("obligation_id") != _OBLIGATION_ID
            or binding.get("frame_id") != frame_id
        ):
            return (
                "vNext Runtime status lacked exact run-bound "
                "verified mission evidence"
            )
        binding_ids.append(binding["id"])
    if set(binding_ids) != set(verified_refs) or len(binding_ids) != len(
        set(binding_ids)
    ):
        return (
            "vNext Runtime status lacked exact run-bound "
            "verified mission evidence"
        )
    return None


def _public_runtime_detail(status: RuntimeStatus) -> str:
    """Project only a stable typed status onto product-visible surfaces."""

    return f"vNext Runtime returned {status.value}"


def _public_identity(value: Any) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or any(
            not (character.isalnum() or character in "-_.:")
            for character in value
        )
    ):
        return ""
    return value


def _public_label(value: Any) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 96
        or any(
            not (character.isalnum() or character in "-_./:")
            for character in value
        )
    ):
        return ""
    return value


def _public_sha256(value: Any) -> str | None:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        return None
    return value


def _public_optional_positive_int(value: Any) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


def _public_optional_nonnegative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _public_identity_list(value: Any) -> list[str]:
    if type(value) not in {list, tuple}:
        return []
    result: list[str] = []
    for item in value:
        identity = _public_identity(item)
        if identity:
            result.append(identity)
    return result


def _public_action_effect(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if not _PUBLIC_ACTION_EFFECT_FIELDS.issubset(value):
        return None
    result: dict[str, Any] = {
        "format": _public_label(value.get("format")),
        "comparator_version": _public_label(
            value.get("comparator_version")
        ),
        "action_id": _public_identity(value.get("action_id")),
        "input_completed": value.get("input_completed"),
        "before_frame_id": _public_optional_positive_int(
            value.get("before_frame_id")
        ),
        "after_frame_id": _public_optional_positive_int(
            value.get("after_frame_id")
        ),
        "before_frame_evidence_ref": _public_identity(
            value.get("before_frame_evidence_ref")
        ),
        "after_frame_evidence_ref": _public_identity(
            value.get("after_frame_evidence_ref")
        ),
        "before_frame_payload_hash": _public_sha256(
            value.get("before_frame_payload_hash")
        ),
        "after_frame_payload_hash": _public_sha256(
            value.get("after_frame_payload_hash")
        ),
        "target_binding_valid": value.get("target_binding_valid"),
        "frame_comparable": value.get("frame_comparable"),
        "local_visual_change": value.get("local_visual_change"),
        "change_ratio": value.get("change_ratio"),
        "reason": value.get("reason"),
        "semantic_completion": value.get("semantic_completion"),
        "evidence_ref": _public_identity(value.get("evidence_ref")),
        "payload_hash": _public_sha256(value.get("payload_hash")),
    }
    ratio = result["change_ratio"]
    if ratio is not None:
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not 0.0 <= float(ratio) <= 1.0
        ):
            return None
        result["change_ratio"] = float(ratio)
    if (
        not result["format"]
        or not result["comparator_version"]
        or not result["action_id"]
        or type(result["input_completed"]) is not bool
        or result["before_frame_id"] is None
        or result["after_frame_id"] is None
        or not result["before_frame_evidence_ref"]
        or not result["after_frame_evidence_ref"]
        or result["before_frame_payload_hash"] is None
        or result["after_frame_payload_hash"] is None
        or type(result["target_binding_valid"]) is not bool
        or type(result["frame_comparable"]) is not bool
        or result["local_visual_change"]
        not in {"changed", "unchanged", "unknown"}
        or result["reason"]
        not in {
            "exact_payload",
            "aligned_pixel_delta",
            "input_not_executed",
            "target_binding_changed",
            "frame_not_comparable",
            "decode_failed",
            "decoded_size_mismatch",
            "comparison_failed",
        }
        or result["semantic_completion"] != "unknown"
        or not result["evidence_ref"]
        or result["payload_hash"] is None
    ):
        return None
    return result


def _public_turn_snapshot(
    value: Any,
    *,
    target: _FrozenTarget,
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    mission_status = source.get("mission_status")
    if mission_status not in {status.value for status in MissionStatus}:
        mission_status = ""
    return {
        "mission_revision": _public_optional_nonnegative_int(
            source.get("mission_revision")
        ),
        "mission_status": mission_status,
        "target_binding_id": target.binding_id,
        "target_hwnd": target.hwnd,
        "frame_id": _public_optional_positive_int(
            source.get("frame_id")
        ),
        "frame_epoch": _public_optional_nonnegative_int(
            source.get("frame_epoch")
        ),
        "frame_evidence_refs": _public_identity_list(
            source.get("frame_evidence_refs")
        ),
        "action_effect": _public_action_effect(
            source.get("action_effect")
        ),
    }


def _public_completion_snapshot(
    value: Any,
    *,
    mission_id: str,
    run_id: str,
    target: _FrozenTarget,
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    mission_status = source.get("mission_status")
    if mission_status not in {status.value for status in MissionStatus}:
        mission_status = ""
    result = source.get("result")
    if (
        type(result) is not str
        or not result.strip()
        or len(result) > 16_384
    ):
        result = None
    frame_id = _public_optional_positive_int(source.get("frame_id"))
    bindings: list[dict[str, Any]] = []
    raw_bindings = source.get("verified_evidence_bindings")
    if type(raw_bindings) in {list, tuple}:
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, Mapping):
                continue
            identity = _public_identity(raw_binding.get("id"))
            binding_frame_id = _public_optional_positive_int(
                raw_binding.get("frame_id")
            )
            if not identity or binding_frame_id is None:
                continue
            bindings.append(
                {
                    "id": identity,
                    "mission_id": mission_id,
                    "run_id": run_id,
                    "obligation_id": _OBLIGATION_ID,
                    "frame_id": binding_frame_id,
                }
            )
    return {
        "mission_id": mission_id,
        "run_id": run_id,
        "mission_revision": _public_optional_nonnegative_int(
            source.get("mission_revision")
        ),
        "mission_status": mission_status,
        "mission_verification_id": (
            _public_identity(source.get("mission_verification_id"))
            or None
        ),
        "obligation_verification_id": (
            _public_identity(source.get("obligation_verification_id"))
            or None
        ),
        "verified_evidence_refs": _public_identity_list(
            source.get("verified_evidence_refs")
        ),
        "verified_evidence_bindings": bindings,
        "accepted_event_ids": _public_identity_list(
            source.get("accepted_event_ids")
        ),
        "target_binding_id": target.binding_id,
        "target_hwnd": target.hwnd,
        "frame_id": frame_id,
        "frame_epoch": _public_optional_nonnegative_int(
            source.get("frame_epoch")
        ),
        "result": result,
    }


def _runtime_evidence_audit_locator(
    mission_id: str,
    run_id: str,
) -> str:
    digest = hashlib.sha256(
        (
            "vnext-runtime-evidence-audit\0"
            f"{mission_id}\0{run_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"vnext-audit-{digest[:32]}"


def _unavailable_runtime_evidence_summary(
    mission_id: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": "incomplete",
        "journal_head_sha256": None,
        "journal_event_count": 0,
        "report_sha256": None,
        "audit_locator": _runtime_evidence_audit_locator(
            mission_id,
            run_id,
        ),
        "parameter_visibility": _PRIVATE_RUNTIME_EVIDENCE,
    }


def _public_runtime_evidence_summary(
    value: Any,
    *,
    mission_id: str,
    run_id: str,
) -> dict[str, Any]:
    fallback = _unavailable_runtime_evidence_summary(
        mission_id,
        run_id,
    )
    if not isinstance(value, Mapping):
        return fallback
    source = value.get("runtime_evidence")
    if not isinstance(source, Mapping):
        source = value
    status = source.get("status", source.get("report_status"))
    head = _public_sha256(source.get("journal_head_sha256"))
    report_sha = _public_sha256(source.get("report_sha256"))
    event_count = source.get("journal_event_count")
    scope_matches = (
        value.get("mission_id") == mission_id
        and value.get("run_id") == run_id
    )
    if (
        not scope_matches
        or status not in _RUNTIME_EVIDENCE_REPORT_STATUSES
        or type(event_count) is not int
        or event_count < 0
        or head is None
        or report_sha is None
    ):
        return fallback
    return {
        "available": True,
        "status": status,
        "journal_head_sha256": head,
        "journal_event_count": event_count,
        "report_sha256": report_sha,
        "audit_locator": _runtime_evidence_audit_locator(
            mission_id,
            run_id,
        ),
        "parameter_visibility": _PRIVATE_RUNTIME_EVIDENCE,
    }


def _interaction_mode_from_goal_admission(
    context: Mapping[str, Any],
    *,
    goal: str,
) -> InteractionMode:
    runtime_state = context.get("runtime_state")
    if not isinstance(runtime_state, Mapping):
        return InteractionMode.AUTO
    raw = runtime_state.get("goal_admission")
    if raw is None:
        return InteractionMode.AUTO
    if not isinstance(raw, Mapping):
        raise RuntimeError("vNext goal admission audit is not an object")
    expected_fields = {
        "format",
        "goal",
        "execution_constraint",
        "route",
        "runtime_version",
        "reason",
        "no_legacy_fallback",
    }
    if set(raw) != expected_fields:
        raise RuntimeError("vNext goal admission audit fields are not exact")
    constraint = raw.get("execution_constraint")
    expected_reason = (
        "explicit_gui_required"
        if constraint == InteractionMode.GUI_REQUIRED.value
        else "gui_first_auto_default"
        if constraint == InteractionMode.AUTO.value
        else ""
    )
    if (
        raw.get("format") != "goal-admission/1"
        or raw.get("goal") != goal
        or raw.get("route") != "live_gui"
        or raw.get("runtime_version") != _RUNTIME_VERSION
        or raw.get("reason") != expected_reason
        or raw.get("no_legacy_fallback") is not True
    ):
        raise RuntimeError("vNext goal admission audit is inconsistent")
    return InteractionMode(constraint)


class _PreparedHandler:
    def __init__(
        self,
        *,
        graph: ArpGraph,
        goal: str,
        target: _FrozenTarget,
        budget: _Budget,
        model: _ModelConfig,
        dependencies: _CompositionDependencies,
    ) -> None:
        self._graph = graph
        self._goal = goal
        self._target = target
        self._budget = budget
        self._model = model
        self._dependencies = dependencies
        self._claimed = False
        self._lock = threading.Lock()

    def _wait_if_paused(
        self,
        pause_controller: Any,
        stop_event: Any,
        deadline: list[float],
    ) -> bool:
        if stop_event.is_set():
            return False
        waiter = getattr(pause_controller, "wait_if_paused", None)
        if not callable(waiter):
            return not stop_event.is_set()
        started = self._dependencies.monotonic()
        allowed = waiter(stop_event)
        # Pause is not execution time; this mirrors the outer ARP safe-boundary
        # timeout extension while preserving one frozen per-run budget.
        deadline[0] += max(
            0.0,
            self._dependencies.monotonic() - started,
        )
        return bool(allowed and not stop_event.is_set())

    def __call__(
        self,
        node: ArpNode,
        context: dict[str, Any],
        stop_event: Any,
    ) -> NodeResult:
        with self._lock:
            if self._claimed:
                return NodeResult(
                    ok=False,
                    branch="fail",
                    error="vNext live canary handler is call-once",
                )
            self._claimed = True
        if node.kind is not ArpNodeKind.AGENT_GOAL or node.id != "full-goal":
            return NodeResult(
                ok=False,
                branch="fail",
                error="vNext composition owns only the exact AGENT_GOAL node",
            )
        run_id = str(context.get("run_id") or "").strip()
        if not run_id:
            return NodeResult(
                ok=False,
                branch="fail",
                error="vNext run identity is unavailable",
            )
        runtime_state = context.get("runtime_state")
        if type(runtime_state) is not dict:
            return NodeResult(
                ok=False,
                branch="fail",
                error="vNext private runtime state is unavailable",
            )
        runtime_state.pop("user_result", None)
        mission_id = (
            "vnext-mission-"
            + hashlib.sha256(
                f"{run_id}|{self._graph.id}".encode("utf-8")
            ).hexdigest()[:24]
        )
        interaction_mode = _interaction_mode_from_goal_admission(
            context,
            goal=self._goal,
        )
        runtime_evidence_anchor: Mapping[str, Any] | None = None
        runtime_evidence_summary = (
            _unavailable_runtime_evidence_summary(
                mission_id,
                run_id,
            )
        )
        budget_wire = self._budget.to_dict()
        BUS.publish(
            "vnext_canary_start",
            run_id=run_id,
            mission_id=mission_id,
            runtime_version=_RUNTIME_VERSION,
            runtime_budget=budget_wire,
            no_legacy_fallback=True,
            interaction_mode=interaction_mode.value,
            model=self._model.model,
            target=self._target.trace_identity(),
        )
        pause_controller = context.get("pause_controller")
        deadline = [
            self._dependencies.monotonic()
            + float(self._budget.max_runtime_s)
        ]
        bounded_stop = _DeadlineStop(
            stop_event,
            clock=self._dependencies.monotonic,
            deadline=lambda: deadline[0],
        )
        session: _SessionPort | None = None
        turns_used = 0
        consecutive_invalid_proposals = 0
        consecutive_actor_errors = 0
        terminal_status = RuntimeStatus.STATE_REJECTED
        detail = ""
        accepted_event_ids: tuple[str, ...] = ()
        session_build = _SessionBuild(
            graph=self._graph,
            goal=self._goal,
            run_id=run_id,
            mission_id=mission_id,
            target=self._target,
            budget=self._budget,
            model=self._model,
            dependencies=self._dependencies,
            deadline=lambda: deadline[0],
            interaction_mode=interaction_mode,
            pause_controller=pause_controller,
        )
        try:
            if not self._wait_if_paused(
                pause_controller,
                stop_event,
                deadline,
            ):
                if self._dependencies.session_builder is None:
                    runtime_evidence_anchor = (
                        _seal_unstarted_runtime_evidence(
                            session_build
                        )
                    )
                    runtime_evidence_summary = (
                        _public_runtime_evidence_summary(
                            runtime_evidence_anchor,
                            mission_id=mission_id,
                            run_id=run_id,
                        )
                    )
                terminal_status = RuntimeStatus.CANCELED
                detail = "run canceled before vNext session composition"
            else:
                builder = (
                    self._dependencies.session_builder
                    or _build_live_session
                )
                session = builder(session_build)
                for turn_index in range(self._budget.max_turns):
                    if not self._wait_if_paused(
                        pause_controller,
                        stop_event,
                        deadline,
                    ):
                        terminal_status = RuntimeStatus.CANCELED
                        detail = "run canceled at a vNext turn boundary"
                        break
                    if bounded_stop.is_set():
                        terminal_status = (
                            RuntimeStatus.CANCELED
                            if stop_event.is_set()
                            else RuntimeStatus.STATE_REJECTED
                        )
                        detail = (
                            "run canceled"
                            if stop_event.is_set()
                            else "vNext runtime time budget exhausted"
                        )
                        break
                    if session.model_calls >= self._budget.max_model_calls:
                        terminal_status = RuntimeStatus.STATE_REJECTED
                        detail = "vNext model-call budget exhausted"
                        break
                    result = session.run_one_turn(
                        turn_index=turn_index,
                        stop_event=bounded_stop,
                    )
                    turns_used += 1
                    terminal_status = result.status
                    detail = _public_runtime_detail(result.status)
                    if result.status is RuntimeStatus.INVALID_PROPOSAL:
                        consecutive_invalid_proposals += 1
                    else:
                        consecutive_invalid_proposals = 0
                    if result.status is RuntimeStatus.ACTOR_ERROR:
                        consecutive_actor_errors += 1
                    else:
                        consecutive_actor_errors = 0
                    redecision_budget_available = bool(
                        turn_index + 1 < self._budget.max_turns
                        and session.model_calls
                        < self._budget.max_model_calls
                        and not bounded_stop.is_set()
                    )
                    invalid_proposal_recovery_available = (
                        result.status is RuntimeStatus.INVALID_PROPOSAL
                        and consecutive_invalid_proposals
                        < _MAX_CONSECUTIVE_INVALID_PROPOSALS
                        and redecision_budget_available
                    )
                    actor_error_recovery_available = (
                        result.status is RuntimeStatus.ACTOR_ERROR
                        and consecutive_actor_errors
                        < _MAX_CONSECUTIVE_ACTOR_ERRORS
                        and redecision_budget_available
                    )
                    if (
                        result.status is RuntimeStatus.UNAVAILABLE
                        and bool(
                            getattr(
                                pause_controller,
                                "paused",
                                False,
                            )
                        )
                        and not stop_event.is_set()
                    ):
                        terminal_status = RuntimeStatus.STALE_DISCARDED
                        detail = (
                            "pause reached the safe boundary before "
                            "Actor admission"
                        )
                    if (
                        result.status is RuntimeStatus.CANCELED
                        and not stop_event.is_set()
                        and self._dependencies.monotonic()
                        >= deadline[0]
                    ):
                        terminal_status = RuntimeStatus.STATE_REJECTED
                        detail = "vNext runtime time budget exhausted"
                    accepted_event_ids = result.accepted_event_ids
                    try:
                        raw_turn_snapshot = session.turn_snapshot()
                        turn_snapshot = _public_turn_snapshot(
                            raw_turn_snapshot,
                            target=self._target,
                        )
                    except Exception:
                        turn_snapshot = _public_turn_snapshot(
                            {},
                            target=self._target,
                        )
                    BUS.publish(
                        "vnext_canary_turn",
                        run_id=run_id,
                        mission_id=mission_id,
                        runtime_version=_RUNTIME_VERSION,
                        no_legacy_fallback=True,
                        turn_index=turn_index,
                        status=terminal_status.value,
                        runtime_status=result.status.value,
                        proposal_kind=result.proposal_kind,
                        decision_id=result.decision_id,
                        accepted_event_ids=list(
                            result.accepted_event_ids
                        ),
                        recovery=(
                            {
                                "kind": (
                                    "bounded_invalid_proposal_redecision"
                                    if result.status
                                    is RuntimeStatus.INVALID_PROPOSAL
                                    else "bounded_actor_error_redecision"
                                ),
                                "consecutive_attempt": (
                                    consecutive_invalid_proposals
                                    if result.status
                                    is RuntimeStatus.INVALID_PROPOSAL
                                    else consecutive_actor_errors
                                ),
                                "max_consecutive_attempts": (
                                    _MAX_CONSECUTIVE_INVALID_PROPOSALS
                                    if result.status
                                    is RuntimeStatus.INVALID_PROPOSAL
                                    else _MAX_CONSECUTIVE_ACTOR_ERRORS
                                ),
                                "available": (
                                    invalid_proposal_recovery_available
                                    if result.status
                                    is RuntimeStatus.INVALID_PROPOSAL
                                    else actor_error_recovery_available
                                ),
                            }
                            if result.status
                            in {
                                RuntimeStatus.INVALID_PROPOSAL,
                                RuntimeStatus.ACTOR_ERROR,
                            }
                            else None
                        ),
                        runtime_snapshot=turn_snapshot,
                    )
                    if terminal_status is RuntimeStatus.MISSION_VERIFIED:
                        break
                    if (
                        invalid_proposal_recovery_available
                        or actor_error_recovery_available
                    ):
                        continue
                    if terminal_status not in _CONTINUE_STATUSES:
                        break
                else:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = "vNext turn budget exhausted"
        except Exception:
            terminal_status = RuntimeStatus.STATE_REJECTED
            detail = "vNext live canary failed closed"
        finally:
            raw_completion_snapshot: dict[str, Any] = {
                "mission_id": mission_id,
                "run_id": run_id,
                "target_binding_id": self._target.binding_id,
                "target_hwnd": self._target.hwnd,
            }
            model_calls = 0
            if session is not None:
                try:
                    model_calls = session.model_calls
                    observed = session.completion_snapshot()
                    raw_completion_snapshot = (
                        dict(observed)
                        if isinstance(observed, Mapping)
                        else {}
                    )
                except Exception:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = "vNext completion snapshot failed closed"
                if terminal_status is RuntimeStatus.MISSION_VERIFIED:
                    binding_error = _completion_binding_error(
                        raw_completion_snapshot,
                        mission_id=mission_id,
                        run_id=run_id,
                        target=self._target,
                    )
                    if binding_error is not None:
                        terminal_status = RuntimeStatus.STATE_REJECTED
                        detail = binding_error
                try:
                    session.close()
                except Exception:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    cleanup_detail = "vNext session cleanup failed closed"
                    detail = (
                        f"{detail}; {cleanup_detail}"
                        if detail
                        else cleanup_detail
                    )
                try:
                    runtime_evidence_anchor = getattr(
                        session,
                        "runtime_evidence_anchor",
                        None,
                    )
                    runtime_evidence_summary = (
                        _public_runtime_evidence_summary(
                            runtime_evidence_anchor,
                            mission_id=mission_id,
                            run_id=run_id,
                        )
                    )
                except Exception:
                    runtime_evidence_summary = (
                        _unavailable_runtime_evidence_summary(
                            mission_id,
                            run_id,
                        )
                    )
                if (
                    self._dependencies.session_builder is None
                    and terminal_status
                    is RuntimeStatus.MISSION_VERIFIED
                    and runtime_evidence_summary["available"]
                    is not True
                ):
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = (
                        "vNext runtime evidence summary failed closed"
                    )
            snapshot = _public_completion_snapshot(
                raw_completion_snapshot,
                mission_id=mission_id,
                run_id=run_id,
                target=self._target,
            )
            completion_result = snapshot.pop("result", None)
            if terminal_status is RuntimeStatus.MISSION_VERIFIED:
                if type(completion_result) is str:
                    runtime_state["user_result"] = completion_result
                elif self._dependencies.session_builder is None:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = (
                        "vNext verified completion result failed closed"
                    )
            output = {
                **snapshot,
                "runtime_version": _RUNTIME_VERSION,
                "runtime_budget": budget_wire,
                "no_legacy_fallback": True,
                "status": terminal_status.value,
                "turns_used": turns_used,
                "model_calls": model_calls,
                "accepted_event_ids": list(accepted_event_ids),
                "runtime_evidence": runtime_evidence_summary,
            }
            BUS.publish(
                "vnext_canary_done",
                run_id=run_id,
                mission_id=mission_id,
                runtime_version=_RUNTIME_VERSION,
                runtime_budget=budget_wire,
                no_legacy_fallback=True,
                status=terminal_status.value,
                turns_used=turns_used,
                model_calls=model_calls,
                detail=detail[:500],
                runtime_evidence=runtime_evidence_summary,
                target_binding_id=self._target.binding_id,
                target_hwnd=self._target.hwnd,
                frame_id=snapshot.get("frame_id"),
                frame_epoch=snapshot.get("frame_epoch"),
                mission_verification_id=snapshot.get(
                    "mission_verification_id"
                ),
                obligation_verification_id=snapshot.get(
                    "obligation_verification_id"
                ),
                verified_evidence_refs=snapshot.get(
                    "verified_evidence_refs",
                    [],
                ),
            )
        if terminal_status is not RuntimeStatus.MISSION_VERIFIED:
            return NodeResult(
                ok=False,
                branch=(
                    "canceled"
                    if terminal_status is RuntimeStatus.CANCELED
                    else "fail"
                ),
                output=output,
                error=detail or terminal_status.value,
            )
        evidence = {
            "predicate": "vnext.mission_verified",
            "value": True,
            "source": "vnext_independent_visual_verifier",
            "verified": True,
            "run_id": run_id,
            "metadata": {
                "runtime_version": _RUNTIME_VERSION,
                "no_legacy_fallback": True,
                "mission_id": mission_id,
                "mission_verification_id": snapshot.get(
                    "mission_verification_id"
                ),
                "obligation_verification_id": snapshot.get(
                    "obligation_verification_id"
                ),
                "verified_evidence_refs": snapshot.get(
                    "verified_evidence_refs",
                    [],
                ),
                "target_binding_id": self._target.binding_id,
                "target_hwnd": self._target.hwnd,
                "frame_id": snapshot.get("frame_id"),
                "frame_epoch": snapshot.get("frame_epoch"),
            },
        }
        return NodeResult(
            ok=True,
            branch="next",
            output=output,
            evidence=[evidence],
        )


def prepare_live_vnext_execution(
    graph: ArpGraph,
    inputs: dict[str, Any],
    config_snapshot: dict[str, Any],
    *,
    run_manager: Any,
    _dependencies: _CompositionDependencies | None = None,
) -> ArpExecution:
    """Validate and register one default-unreachable live vNext execution."""

    dependencies = _dependencies or _CompositionDependencies()
    if type(config_snapshot) is not dict:
        raise ValueError("vNext live canary config snapshot must be a dict")
    frozen_config = deepcopy(config_snapshot)
    goal = _validate_exact_graph(graph)
    budget = _freeze_budget(frozen_config)
    model = _freeze_model(frozen_config, budget)
    frozen_inputs = deepcopy(_plain_dict(inputs, "inputs"))
    target = _freeze_prebound_target(
        graph,
        frozen_inputs,
        dependencies=dependencies,
    )
    prepare = getattr(run_manager, "prepare", None)
    if not callable(prepare):
        raise TypeError("run_manager must provide prepare()")
    handler = _PreparedHandler(
        graph=graph,
        goal=goal,
        target=target,
        budget=budget,
        model=model,
        dependencies=dependencies,
    )
    execution = prepare(
        graph,
        {ArpNodeKind.AGENT_GOAL: handler},
        inputs=frozen_inputs,
    )
    if not isinstance(execution, ArpExecution):
        raise TypeError("run_manager.prepare() must return ArpExecution")
    execution.runtime_state.update(
        {
            "runtime_version": _RUNTIME_VERSION,
            "runtime_budget": budget.to_dict(),
            "no_legacy_fallback": True,
            "experience_policy": "disabled",
            "target_binding_id": target.binding_id,
        }
    )
    return execution


__all__ = [
    "build_exact_live_vnext_graph",
    "prepare_live_vnext_execution",
]
