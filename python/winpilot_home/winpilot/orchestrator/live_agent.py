"""Product composition root for the minimal live vNext desktop loop.

Electron supplies the initial target.  A small DesktopWorkspace then owns
causal window hand-offs between atomic actions; it does not plan application
workflows or provide a legacy fallback.
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
import uuid

from ..config import CONFIG, TRACES_DIR
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
from ..visual_runtime.frame_authority import FrameAuthority, FrameState
from ..integrations.connector_gateway import ConnectorGateway
from ..model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
    native_provider_protocol,
)
from ..modeling.contracts import ModelAttempt
from ..modeling.openai_transport import create_openai_client
from ..modeling.provider_protocols import ProviderProtocolAdapter
from ..modeling.runtime import ModelCallRuntime
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS
from ..utils.screenshot import Region, virtual_screen_region, window_region
from ..visual_runtime.frame_observer import FrameObserver
from ..vnext.capabilities import (
    BrokerRuntimeState,
    CapabilityBroker,
    ExecutionMedium,
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
    DesktopWorkspace,
    LiveTargetSnapshot,
    WorkspaceObservation,
    WorkspaceSurface,
)
from ..vnext.evidence import EvidenceRegistry
from ..vnext.mission import (
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
)
from ..vnext.model_roles import (
    FlowFragmentProposer,
    INITIAL_ACTOR_SYSTEM_CONTRACT,
    VISUAL_ACTOR_MODEL_ROLE,
    LiveVisualActor,
    VisualActorTurnAdmission,
)
from ..vnext.profiling import ExecutionPolicy
from ..vnext.runtime import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    MissionRuntime,
    MissionRuntimeResult,
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
_TARGET_MAX_AGE_MS = 60_000
_FUTURE_CLOCK_SKEW_MS = 2_000
_MAX_SUBMITTED_SIDE = 1120
_MAX_REJECTION_FEEDBACK_CHARS = 512
_NON_WHITESPACE_PATTERN = r"\S"
_BLOCKER_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_APPROVED_ACTIONS = frozenset(
    {
        "click",
        "double_click",
        "drag",
        "scroll",
        "type_text",
        "hotkey",
        "launch_application",
    }
)
_GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "pointer_released",
    "runtime_healthy",
)
_DRAG_GATES = (
    "target_bound",
    "authoritative_frame",
    "runtime_healthy",
)
_CONTINUE_STATUSES = frozenset(
    {
        RuntimeStatus.ACTION_RECORDED,
        RuntimeStatus.STALE_DISCARDED,
        RuntimeStatus.VERIFICATION_RECORDED,
    }
)
_PRIVATE_RUNTIME_EVIDENCE = "private_runtime_evidence"
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
        rejection_feedback: str | None = None,
    ) -> MissionRuntimeResult: ...

    def completion_snapshot(self) -> Mapping[str, Any]: ...

    def turn_snapshot(self) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _CompositionDependencies:
    wall_clock: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic
    target_reader: Callable[[int], _TargetFacts | None] | None = None
    foreground_reader: Callable[[], int | None] | None = None
    hit_test_root: Callable[[int, int], int | None] | None = None
    client_factory: Callable[..., Any] | None = None
    session_builder: Callable[["_SessionBuild"], _SessionPort] | None = None
    connector_factory: Callable[[], Any] | None = None
    provider_transport_factory: Callable[[], Any] | None = None
    config_snapshot: Callable[[], dict[str, Any]] | None = None
    shell_factory: Callable[[], Any] | None = None


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
    reader: Callable[[int], "_TargetFacts | None"]
    foreground_reader: Callable[[], int | None]
    hit_test_root: Callable[[int, int], int | None]
    desktop_shell: bool = False

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
            "binding_mode": (
                "desktop_shell" if self.desktop_shell else "window"
            ),
        }


class DesktopLeaseError(ValueError):
    """A desktop lease transition or target binding was rejected."""


class DesktopLeaseSlot:
    """One run-bound target handoff delivered after the Actor requests it."""

    def __init__(
        self,
        *,
        graph: ArpGraph,
        dependencies: _CompositionDependencies,
        thread_id: str,
        turn_id: str,
    ) -> None:
        self.lease_id = f"lease-{uuid.uuid4().hex}"
        self._graph = graph
        self._dependencies = dependencies
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._run_id = ""
        self._state = "idle"
        self._target: _FrozenTarget | None = None
        self._error = ""
        self._condition = threading.Condition(threading.RLock())

    def request(self, run_id: str) -> dict[str, Any]:
        with self._condition:
            if self._state != "idle" or not run_id:
                raise DesktopLeaseError("desktop lease cannot be requested")
            self._run_id = run_id
            self._state = "waiting"
            snapshot = self.snapshot()
        BUS.publish(
            "desktop_lease_requested",
            run_id=run_id,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
            lease_id=self.lease_id,
            status="waiting",
        )
        return snapshot

    def bind(
        self,
        *,
        run_id: str,
        lease_id: str,
        inputs: dict[str, Any],
    ) -> _FrozenTarget:
        with self._condition:
            if (
                self._state != "waiting"
                or run_id != self._run_id
                or lease_id != self.lease_id
            ):
                raise DesktopLeaseError("desktop lease does not match the run")
            self._state = "validating"
        try:
            target = _freeze_prebound_target(
                self._graph,
                deepcopy(inputs),
                dependencies=self._dependencies,
            )
        except Exception as exc:
            with self._condition:
                rejected = self._state == "validating"
                if rejected:
                    self._state = "waiting"
                    self._error = str(exc)
                detail = self._error or str(exc)
                self._condition.notify_all()
            if rejected:
                BUS.publish(
                    "desktop_lease_rejected",
                    run_id=run_id,
                    thread_id=self._thread_id,
                    turn_id=self._turn_id,
                    lease_id=self.lease_id,
                    status="waiting",
                    error=str(exc),
                )
            raise DesktopLeaseError(detail) from exc
        with self._condition:
            if self._state != "validating":
                raise DesktopLeaseError(
                    "desktop lease closed during target validation"
                )
            self._target = target
            self._error = ""
            self._state = "delivered"
            self._condition.notify_all()
        BUS.publish(
            "desktop_lease_bound",
            run_id=run_id,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
            lease_id=self.lease_id,
            status="delivered",
            target=target.trace_identity(),
        )
        return target

    def fail(
        self,
        *,
        run_id: str,
        lease_id: str,
        reason: str,
    ) -> None:
        with self._condition:
            if (
                self._state not in {"waiting", "validating"}
                or run_id != self._run_id
                or lease_id != self.lease_id
            ):
                raise DesktopLeaseError("desktop lease does not match the run")
            self._state = "closed"
            self._error = str(reason or "desktop handoff failed")
            self._condition.notify_all()
        BUS.publish(
            "desktop_lease_failed",
            run_id=run_id,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
            lease_id=self.lease_id,
            status="closed",
            error=self._error,
        )

    def wait(
        self,
        *,
        stop_event: Any,
        deadline: Callable[[], float],
        clock: Callable[[], float],
    ) -> _FrozenTarget:
        with self._condition:
            while self._state in {"waiting", "validating"}:
                if stop_event.is_set():
                    self._state = "closed"
                    self._error = "run canceled while awaiting desktop lease"
                    self._condition.notify_all()
                    break
                remaining = float(deadline()) - float(clock())
                if remaining <= 0:
                    self._state = "closed"
                    self._error = "desktop lease timed out"
                    self._condition.notify_all()
                    break
                self._condition.wait(timeout=min(0.1, remaining))
            if self._state == "delivered" and self._target is not None:
                return self._target
            raise DesktopLeaseError(
                self._error or "desktop lease closed without a target"
            )

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "format": "desktop-lease/1",
                "run_id": self._run_id,
                "thread_id": self._thread_id,
                "turn_id": self._turn_id,
                "lease_id": self.lease_id,
                "status": self._state,
                "error": self._error,
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
    actor_bundle: PreparedLiveActor
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
        raise ValueError("vNext live Agent goal must be a literal string")
    normalized_goal = goal.strip()
    if not normalized_goal:
        raise ValueError("vNext live Agent requires a non-empty full goal")
    graph = ArpGraph(
        id=f"vnext-live-agent-{_fnv1a_utf16(normalized_goal)}",
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
        raise ValueError("vNext live Agent requires canonical ArpGraph")
    if graph.format != "arp/1":
        raise ValueError("vNext live Agent requires arp/1")
    if graph.description != "" or graph.variables != {} or graph.metadata != {}:
        raise ValueError(
            "vNext live Agent graph description, variables, and metadata must be empty"
        )
    if len(graph.nodes) != 3 or len(graph.edges) != 2:
        raise ValueError(
            "vNext live Agent requires exactly start, one AGENT_GOAL, and end"
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
            raise ValueError(f"vNext live Agent node {node_id!r} is not exact")
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
            raise ValueError(f"vNext live Agent node {node_id!r} config must be empty")
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
            raise ValueError(f"vNext live Agent edge {edge_id!r} is not exact")
    policy = graph.policy
    if (
        policy.max_node_visits != 1
        or policy.fail_fast is not True
        or policy.require_evidence_on_done is not True
        or policy.allow_shell is not False
        or policy.allow_desktop_mutation is not True
    ):
        raise ValueError("vNext live Agent graph policy is not exact")
    expected_graph_id = f"vnext-live-agent-{_fnv1a_utf16(goal)}"
    if graph.id != expected_graph_id or graph.name != goal:
        raise ValueError("vNext live Agent graph identity does not bind the full goal")
    return goal


def _freeze_budget(config_snapshot: dict[str, Any]) -> _Budget:
    vnext = _plain_dict(config_snapshot.get("vnext"), "config.vnext")
    if vnext.get("live_agent_enabled") is not True:
        raise ValueError(
            "vNext live Agent is disabled; no legacy fallback was run"
        )
    values = {
        name: _positive_int(vnext.get(name), f"config.vnext.{name}")
        for name in ("max_turns", "max_runtime_s", "max_model_calls")
    }
    if values["max_turns"] > 100:
        raise ValueError("config.vnext.max_turns exceeds the bounded live Agent")
    if values["max_runtime_s"] > 3600:
        raise ValueError("config.vnext.max_runtime_s exceeds the bounded live Agent")
    if values["max_model_calls"] > 2 * values["max_turns"]:
        raise ValueError(
            "config.vnext.max_model_calls exceeds Actor plus one "
            "Grounder-or-Verifier call per turn"
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
    native_protocol = native_provider_protocol(api)
    if not api_key or not model or (not base_url and native_protocol is None):
        raise ValueError(
            "vNext live Agent requires one configured vision model endpoint; "
            "an explicit native provider may use its default base URL"
        )
    profile = model_profile_from_config(api)
    if not profile.supports_vision:
        raise ValueError("configured vNext live Agent model is not vision-capable")
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


class _LiveAgentStatusProvider:
    """Read-only Actor-owned status facts resolved without a desktop lease."""

    _STATUS_FORMAT = "agent-status/1"
    _MEMORY_FORMAT = "memory-status/1"
    _CAPABILITIES_FORMAT = "capabilities/1"

    def __init__(
        self,
        *,
        runtime_version: str,
        model: str,
        budget: _Budget,
        run_id: str,
        mission_id: str,
        thread_id: str,
        turn_id: str,
        live_agent_enabled: bool,
        experience_policy: str,
        connector_available: bool,
        config_experience_memory: bool,
        memory_store_present: bool,
    ) -> None:
        self._runtime_version = runtime_version
        self._model = model
        self._budget = budget
        self._run_id = run_id
        self._mission_id = mission_id
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._live_agent_enabled = live_agent_enabled
        self._experience_policy = experience_policy
        self._connector_available = connector_available
        self._config_experience_memory = config_experience_memory
        self._memory_store_present = memory_store_present

    def resolve(self, name: str) -> dict[str, Any]:
        if name == "get_agent_status":
            return self._agent_status()
        if name == "get_memory_status":
            return self._memory_status()
        if name == "get_capabilities":
            return self._capabilities()
        raise ValueError(f"unknown Actor status tool: {name!r}")

    def _agent_status(self) -> dict[str, Any]:
        return {
            "format": self._STATUS_FORMAT,
            "runtime_version": self._runtime_version,
            "model": self._model,
            "live_agent_enabled": self._live_agent_enabled,
            "budget": self._budget.to_dict(),
            "run_id": self._run_id,
            "mission_id": self._mission_id,
            "thread_id": self._thread_id,
            "turn_id": self._turn_id,
            "experience_policy": self._experience_policy,
            "connector_available": self._connector_available,
        }

    def _memory_status(self) -> dict[str, Any]:
        enabled = self._experience_policy == "allow"
        return {
            "format": self._MEMORY_FORMAT,
            "long_term_experience_memory": (
                "enabled" if enabled else "disabled"
            ),
            "enabled": enabled,
            "experience_policy": self._experience_policy,
            "reason": (
                "long-term experience memory is active in this Agent chain"
                if enabled
                else (
                    "vNext live Agent chain experience_policy=disabled; "
                    "no memory retrieval or experience write is attached"
                )
            ),
            "config_agent_experience_memory": self._config_experience_memory,
            "store_present": self._memory_store_present,
        }

    def _capabilities(self) -> dict[str, Any]:
        return {
            "format": self._CAPABILITIES_FORMAT,
            "direct_reply": True,
            "status_queries": (
                "get_agent_status",
                "get_memory_status",
                "get_capabilities",
            ),
            "connector": (
                "search_connector_tools, call_connector_tool"
                if self._connector_available
                else "unavailable"
            ),
            "observe_desktop": True,
            "desktop_actions": (
                "click",
                "double_click",
                "drag",
                "scroll",
                "type_text",
                "hotkey",
                "launch_application",
            ),
            "outcome_tools": ("claim_obligation_done", "blocked"),
        }


def _window_text_reader_factory() -> Any:
    """Read-only UIA text of the exact bound target window (frame hwnd).

    The reader is called inside the Visual Actor's own turn with the
    projected frame, so the text it returns always describes the same window
    the current screenshot shows — no UIA-vs-pixels mismatch is possible.
    """
    _WINDOW_TEXT_FORMAT = "window-text/1"
    _WINDOW_TEXT_MAX_CHARS = 6000

    def read(turn: Any) -> dict[str, Any]:
        frame = getattr(turn, "frame", None)
        hwnd = getattr(frame, "window_hwnd", None) if frame is not None else None
        if not hwnd:
            return {
                "format": _WINDOW_TEXT_FORMAT,
                "window_hwnd": None,
                "element_count": 0,
                "text": "",
                "error": (
                    "no bound target window in the current frame; "
                    "observe the desktop first"
                ),
            }
        try:
            from ..perception.uia import collect as uia_collect

            elements = uia_collect(int(hwnd))
        except Exception as exc:
            return {
                "format": _WINDOW_TEXT_FORMAT,
                "window_hwnd": int(hwnd),
                "element_count": 0,
                "text": "",
                "error": f"{type(exc).__name__}: {exc}",
            }
        lines: list[str] = []
        for element in elements:
            text = str(getattr(element, "text", "") or "").strip()
            if text and text not in lines:
                lines.append(text)
        joined = "\n".join(lines)
        return {
            "format": _WINDOW_TEXT_FORMAT,
            "window_hwnd": int(hwnd),
            "element_count": len(lines),
            "text": joined[:_WINDOW_TEXT_MAX_CHARS],
        }

    return read


def _capture_runner_factory() -> Any:
    """vNext capture runner: pure-logic wrapper over the action backend."""

    from ..action.clipboard_ops import capture_screen_backend
    from ..vnext.desktop_ops import capture_screen

    def runner(*, path: str, window: bool) -> dict[str, Any]:
        return capture_screen(
            capture_screen_backend,
            path=path,
            window=window,
        )

    return runner


def _windows_reader_factory() -> Any:
    """vNext window inventory runner: pure-logic wrapper over the backend."""

    from ..action.clipboard_ops import list_windows_backend
    from ..vnext.desktop_ops import list_windows

    return lambda: list_windows(list_windows_backend)


def _paste_runner_factory() -> Any:
    """vNext image-paste runner: pure-logic wrapper over the backend."""

    from ..action.clipboard_ops import paste_image_backend
    from ..vnext.desktop_ops import paste_image

    def runner(*, path: str) -> dict[str, Any]:
        return paste_image(paste_image_backend, path=path)

    return runner


def _read_win32_foreground_root() -> int | None:
    try:
        import win32gui
        import win32process

        foreground = int(win32gui.GetForegroundWindow() or 0)
        if not foreground:
            return None
        root = int(win32gui.GetAncestor(foreground, 2) or foreground)
        if win32gui.IsWindowEnabled(root):
            return root

        _thread_id, root_pid = (
            win32process.GetWindowThreadProcessId(root)
        )
        owned: list[int] = []

        def collect(hwnd: int, _extra: Any) -> bool:
            candidate = int(hwnd)
            if (
                candidate == root
                or not win32gui.IsWindowVisible(candidate)
                or not win32gui.IsWindowEnabled(candidate)
                or win32gui.IsIconic(candidate)
                or int(win32gui.GetAncestor(candidate, 2) or candidate)
                != candidate
            ):
                return True
            _candidate_thread, candidate_pid = (
                win32process.GetWindowThreadProcessId(candidate)
            )
            if int(candidate_pid) != int(root_pid):
                return True
            owner = int(win32gui.GetWindow(candidate, 4) or 0)
            while owner:
                if owner == root:
                    owned.append(candidate)
                    break
                owner = int(win32gui.GetWindow(owner, 4) or 0)
            return True

        win32gui.EnumWindows(collect, None)
        if len(owned) == 1:
            return owned[0]
        return root if root > 0 else None
    except Exception:
        return None


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
        # Maximized Win32 windows commonly expose an invisible resize border
        # a few pixels outside the virtual desktop (for example x=-10).  The
        # Actor can neither see nor interact with that border, so bind the
        # target to the visible intersection instead of rejecting the first
        # frame because an exact screenshot cannot include off-screen pixels.
        region = window_region(value).clamp_to(virtual_screen_region())
        if region.width <= 0 or region.height <= 0:
            return None
        foreground_root = _read_win32_foreground_root()
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
        foreground = _read_win32_foreground_root()
        if not foreground or root == foreground:
            return root
        _root_thread, root_pid = win32process.GetWindowThreadProcessId(root)
        _foreground_thread, foreground_pid = (
            win32process.GetWindowThreadProcessId(foreground)
        )
        return foreground if int(root_pid) == int(foreground_pid) else root
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


def _workspace_surface(
    facts: _TargetFacts | None,
) -> WorkspaceSurface | None:
    if type(facts) is not _TargetFacts:
        return None
    try:
        return WorkspaceSurface(
            hwnd=facts.hwnd,
            root_hwnd=facts.root_hwnd,
            process_id=facts.process_id,
            executable_identity=facts.executable_identity,
            title=facts.title,
            class_name=facts.class_name,
            region=facts.region,
            foreground_root_hwnd=facts.foreground_root_hwnd,
        )
    except (TypeError, ValueError):
        return None


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
            "vNext live Agent requires the matching native Electron execution context"
        )
    _positive_int(
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
            "binding_mode",
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
    binding_mode = target.get("binding_mode")
    desktop_shell = binding_mode == "desktop_shell"
    if not desktop_shell and binding_mode != "window":
        raise ValueError("Electron target binding_mode is not exact")
    if (
        target.get("format") != "desktop-target/1"
        or (not desktop_shell and target.get("foreground_verified") is not True)
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
    foreground_reader = dependencies.foreground_reader
    if foreground_reader is None:
        if dependencies.target_reader is None:
            foreground_reader = _read_win32_foreground_root
        else:
            def foreground_reader() -> int | None:
                facts = reader(hwnd)
                return (
                    None
                    if facts is None
                    else facts.foreground_root_hwnd
                )
    hit_test = dependencies.hit_test_root or _hit_test_win32_root
    current = reader(hwnd)
    submitted_region = Region(
        rect[0],
        rect[1],
        rect[2] - rect[0],
        rect[3] - rect[1],
    )
    visible_region = submitted_region.clamp_to(virtual_screen_region())
    if visible_region.width <= 0 or visible_region.height <= 0:
        raise ValueError("Electron target has no visible desktop intersection")
    expected_region = (
        visible_region.left,
        visible_region.top,
        visible_region.width,
        visible_region.height,
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
        if current.class_name != class_name:
            mismatches.append("class_name")
        if current.region != expected_region:
            mismatches.append("region")
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
        "binding_mode": "desktop_shell" if desktop_shell else "window",
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
        reader=reader,
        foreground_reader=foreground_reader,
        hit_test_root=hit_test,
        desktop_shell=desktop_shell,
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
                "image_points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "maxItems": 32,
                    "default": [],
                },
                "keep_holding": {"type": "boolean", "default": False},
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
        "launch_application": _object_schema(
            {
                "app": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                },
                "arguments": {
                    "type": "string",
                    "maxLength": 4096,
                },
            },
            ["app", "arguments"],
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
        "launch_application",
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
                    _DRAG_GATES
                    if name == "drag"
                    else _GUI_GATES
                ),
                order=order,
            )
        )
    return ToolCatalog(tuple(tools))


def _launch_application_primitive(
    app: str,
    arguments: str,
) -> dict[str, Any]:
    """One generic Windows application launch without any app adapter."""
    from ..perception.apps import launch_application

    result = launch_application(app, arguments)
    if isinstance(result, Mapping):
        return dict(result)
    return {"ok": False, "error": "launcher returned a non-object result"}


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
        workspace: DesktopWorkspace | None = None,
        binding: TargetBinding | None = None,
        target_evidence: EvidenceRef | None = None,
        target_probe: _BoundTargetProbe | None = None,
        authority: FrameAuthority,
        observer: FrameObserver,
        pause_controller: Any,
    ) -> None:
        if workspace is None:
            if (
                type(binding) is not TargetBinding
                or target_probe is None
            ):
                raise TypeError(
                    "runtime state requires DesktopWorkspace or legacy target"
                )
            initial = target_probe.snapshot(binding.hwnd)
            if initial is None:
                raise ValueError("legacy target probe is unavailable")

            def legacy_reader(hwnd: int) -> WorkspaceSurface | None:
                snapshot = target_probe.snapshot(hwnd)
                if snapshot is None:
                    return None
                return WorkspaceSurface(
                    hwnd=snapshot.hwnd,
                    root_hwnd=snapshot.root_hwnd,
                    process_id=snapshot.process_id,
                    executable_identity=snapshot.executable_identity,
                    title=binding.title_snapshot,
                    class_name="",
                    region=snapshot.region,
                    foreground_root_hwnd=snapshot.foreground_root_hwnd,
                )

            def legacy_foreground_reader() -> int | None:
                snapshot = target_probe.snapshot(binding.hwnd)
                return (
                    None
                    if snapshot is None
                    else snapshot.foreground_root_hwnd
                )

            workspace = DesktopWorkspace(
                binding=binding,
                surface=legacy_reader(binding.hwnd),
                reader=legacy_reader,
                foreground_reader=legacy_foreground_reader,
                hit_test_root=target_probe.hit_test_root,
            )
            if (
                target_evidence is not None
                and target_evidence.id
                != binding.established_by_evidence_ref
            ):
                raise ValueError("legacy target evidence disagrees")
        self._reducer = reducer
        self._registry = registry
        self._workspace = workspace
        self._authority = authority
        self._observer = observer
        self._pause_controller = pause_controller
        self._pointer_state_reader: Callable[[], PointerLeaseState] | None = None

    def bind_pointer_state_reader(
        self,
        reader: Callable[[], PointerLeaseState],
    ) -> None:
        if not callable(reader):
            raise TypeError("pointer_state_reader must be callable")
        if self._pointer_state_reader is not None:
            raise RuntimeError("pointer_state_reader is already bound")
        state = reader()
        if type(state) is not PointerLeaseState:
            raise TypeError("pointer_state_reader returned an invalid state")
        self._pointer_state_reader = reader

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

    def _prepare_workspace_adoption(
        self,
        observation: WorkspaceObservation,
    ) -> tuple[TargetBinding, EvidenceRef] | None:
        if not observation.replaces_binding:
            return None
        previous = self._workspace.binding
        surface = observation.surface
        material = {
            "previous_binding_id": previous.binding_id,
            "causal_action_id": observation.causal_action_id,
            "causal_action": observation.causal_action,
            "generation": observation.generation + 1,
            "hwnd": surface.hwnd,
            "root_hwnd": surface.root_hwnd,
            "process_id": surface.process_id,
            "executable_identity_sha256": hashlib.sha256(
                surface.executable_identity.encode("utf-8")
            ).hexdigest(),
            "class_name": surface.class_name,
            "region": list(surface.region),
        }
        payload_hash = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        evidence = EvidenceRef(
            id=f"vnext-workspace-target-{payload_hash[:24]}",
            mission_id=self._registry.mission_id,
            run_id=self._registry.run_id,
            obligation_id=_OBLIGATION_ID,
            kind="target_binding",
            trust="deterministic",
            payload_hash=payload_hash,
        )
        binding = TargetBinding(
            binding_id=f"vnext-workspace-{payload_hash[:24]}",
            hwnd=surface.hwnd,
            process_id=surface.process_id,
            executable_identity=surface.executable_identity,
            title_snapshot=surface.title,
            established_by_evidence_ref=evidence.id,
            epoch=previous.epoch + 1,
        )
        return binding, evidence

    def _capture_current(self) -> FrameState:
        for attempt in range(2):
            binding = self._workspace.binding
            previous = self._authority.current
            expected_previous = (
                previous.frame_id if previous is not None else 0
            )
            desktop_shell = bool(
                getattr(self._workspace, "desktop_shell", False)
            )
            crop_enabled = bool(
                CONFIG.get(
                    "controller",
                    "target_window_capture_enabled",
                    default=False,
                )
            )
            observation = None
            try:
                observation = self._workspace.observe()
            except RuntimeError:
                if desktop_shell or crop_enabled:
                    raise
            prepared = (
                None
                if observation is None
                else self._prepare_workspace_adoption(observation)
            )
            replacement = prepared is not None
            if prepared is not None:
                binding, target_evidence = prepared
                first = observation.surface.to_live_snapshot()
                # A successor is a real foreground window, so an initial
                # desktop-shell label retires for this screen-first capture.
                desktop_shell = False
            else:
                target_evidence = None
                first = None
            screen_first = not desktop_shell and not crop_enabled
            if not screen_first and first is None:
                first = self._workspace.snapshot(binding.hwnd)
                if first is None:
                    raise RuntimeError(
                        "workspace target identity is no longer current"
                    )
            # Screen-first frames.  The model sees the whole real virtual
            # desktop by default so it can react to windows it never bound;
            # a window crop is an explicit opt-in (``target_window_capture_
            # enabled``) and the desktop shell keeps its labelled window
            # scope for the full-desktop surface it binds.
            if desktop_shell or crop_enabled:
                capture_region = Region(*first.region)
                expected_scope = "target_window"
                scope_override = (
                    "target_window" if desktop_shell else None
                )
            else:
                capture_region = virtual_screen_region()
                expected_scope = "full_desktop"
                scope_override = "full_desktop"
            captured = self._observer.capture(
                epoch=binding.epoch,
                hwnd=binding.hwnd,
                phase="observe",
                provided_region=capture_region,
                expected_previous_frame_id=expected_previous,
                scope_override=scope_override,
            )
            if (
                captured is None
                or captured.hwnd != binding.hwnd
                or captured.epoch != binding.epoch
                or captured.scope != expected_scope
                or (
                    captured.region.left,
                    captured.region.top,
                    captured.region.width,
                    captured.region.height,
                )
                != (
                    capture_region.left,
                    capture_region.top,
                    capture_region.width,
                    capture_region.height,
                )
                or not _jpeg_is_valid(captured.image_b64)
                or _jpeg_dimensions(captured.image_b64)
                != captured.submitted_size
            ):
                if replacement:
                    self._workspace.discard(observation)
                    if attempt == 0:
                        continue
                raise RuntimeError(
                    "fresh screen-first JPEG capture failed closed"
                )
            if screen_first and not replacement:
                break
            validated = (
                self._workspace.validate(observation)
                if replacement
                else self._workspace.snapshot(binding.hwnd)
            )
            current = (
                None
                if validated is None
                else validated.to_live_snapshot()
                if replacement
                else validated
            )
            if current == first:
                break
            if replacement:
                self._workspace.discard(observation)
            if attempt > 0:
                raise RuntimeError("target changed during frame capture")
        committed = self._observer.commit(captured)
        frame_ref = self._frame_ref(committed)
        previous_binding = None
        if replacement:
            previous_binding = self._workspace.binding
            self._workspace.adopt(observation, binding)
            assert target_evidence is not None
            self._registry.append_many((target_evidence, frame_ref))
        else:
            self._registry.append(frame_ref)
        event = self._reducer.event(
            "target_bound",
            payload={"target_binding": binding.to_dict()},
            evidence_refs=(
                binding.established_by_evidence_ref,
                frame_ref.id,
            ),
        )
        self._reducer.apply(event)
        if previous_binding is not None:
            BUS.publish(
                "vnext_workspace_target_adopted",
                runtime_version=_RUNTIME_VERSION,
                no_legacy_fallback=True,
                run_id=self._registry.run_id,
                mission_id=self._registry.mission_id,
                obligation_id=_OBLIGATION_ID,
                previous_target_binding_id=previous_binding.binding_id,
                target_binding_id=binding.binding_id,
                target_hwnd=binding.hwnd,
                generation=observation.generation + 1,
                causal_action=observation.causal_action,
                parameter_visibility=_PRIVATE_RUNTIME_EVIDENCE,
            )
        BUS.publish(
            "vnext_gui_observation",
            runtime_version=_RUNTIME_VERSION,
            no_legacy_fallback=True,
            run_id=self._registry.run_id,
            mission_id=self._registry.mission_id,
            obligation_id=_OBLIGATION_ID,
            execution_medium=ExecutionMedium.CONTROL.value,
            observation_kind="fresh_authoritative_target_frame",
            target_binding_id=binding.binding_id,
            target_hwnd=binding.hwnd,
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
        binding = self._workspace.binding
        if (
            state.scope not in {"target_window", "full_desktop"}
            or state.hwnd != binding.hwnd
        ):
            raise RuntimeError("current target/frame identity no longer matches")
        if state.scope == "target_window":
            current_target = self._workspace.snapshot(binding.hwnd)
            if (
                current_target is None
                or state.region != current_target.region
            ):
                raise RuntimeError(
                    "current target/frame identity no longer matches"
                )
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
                target=TargetState(
                    binding_id=binding.binding_id,
                    hwnd=binding.hwnd,
                    epoch=binding.epoch,
                    live=True,
                ),
                frame=frame.broker_frame(
                    authoritative=True,
                    fresh=True,
                ),
                pointer=(
                    PointerLeaseState(PointerDisposition.RELEASED)
                    if self._pointer_state_reader is None
                    else self._pointer_state_reader()
                ),
                runtime_healthy=True,
                # Pause is an outer safe-boundary gate. Marking a completed
                # atomic action's post-frame paused would invalidate reconcile.
                paused=paused,
                canceled=False,
            ),
            frame=frame,
            target=RuntimeTargetIdentity.from_binding(binding),
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
        self._active_reservations: dict[str, int] = {}
        self._active_fingerprints: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(
                0,
                self._maximum
                - self._count
                - sum(self._active_reservations.values()),
            )

    def begin(self, model_role: str, *, reserve: int) -> int:
        if type(reserve) is not int or reserve <= 0:
            raise ValueError("model-call reservation must be positive")
        with self._lock:
            if model_role in self._active_reservations:
                raise RuntimeError("vNext model role is already in flight")
            reserved = sum(self._active_reservations.values())
            available = self._maximum - self._count - reserved
            if available <= 0:
                raise RuntimeError("vNext model-call budget exhausted")
            admitted = min(reserve, available)
            self._active_reservations[model_role] = admitted
            self._active_fingerprints[model_role] = set()
            return admitted

    def record_attempt(
        self,
        model_role: str,
        request_fingerprint: str,
    ) -> None:
        with self._lock:
            remaining = self._active_reservations.get(model_role)
            fingerprints = self._active_fingerprints.get(model_role)
            if remaining is None or fingerprints is None:
                return
            if request_fingerprint in fingerprints:
                return
            if remaining <= 0:
                return
            fingerprints.add(request_fingerprint)
            self._active_reservations[model_role] = remaining - 1
            self._count += 1

    def finish(self, model_role: str) -> None:
        with self._lock:
            self._active_reservations.pop(model_role, None)
            self._active_fingerprints.pop(model_role, None)


def _remaining_timeout(
    *,
    clock: Callable[[], float],
    deadline: Callable[[], float],
    configured_timeout_s: float,
) -> float:
    remaining = float(deadline()) - float(clock())
    if not 0 < remaining < float("inf"):
        raise RuntimeError("vNext runtime time budget exhausted")
    return min(float(configured_timeout_s), remaining)


class _DeadlineModelAdapter:
    """Add the frozen run's remaining deadline to every SDK request."""

    def __init__(
        self,
        adapter: Any,
        *,
        model: _ModelConfig,
        clock: Callable[[], float],
        deadline: Callable[[], float],
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._clock = clock
        self._deadline = deadline

    def prepare_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        # Deadline and schema guards belong to the canonical Actor request.
        # Native adapters translate only after these generic invariants hold,
        # so the Runtime freezes the exact provider wire request.
        request = deepcopy(kwargs)
        request["timeout"] = _remaining_timeout(
            clock=self._clock,
            deadline=self._deadline,
            configured_timeout_s=min(
                self._model.request_timeout_s,
                self._model.total_timeout_s,
            ),
        )
        prepared = self._adapter.prepare_chat_completion(**request)
        if type(prepared) is not dict:
            raise RuntimeError("model adapter returned a non-object request")
        return prepared

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
    def __init__(self, role: Any, counter: _ModelCallCounter) -> None:
        self._role = role
        self._counter = counter

    def propose(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        admitted = self._counter.begin(
            VISUAL_ACTOR_MODEL_ROLE,
            reserve=max(1, self._counter.remaining),
        )
        try:
            kwargs["_model_call_limit"] = admitted
            return self._role.propose(*args, **kwargs)
        finally:
            self._counter.finish(VISUAL_ACTOR_MODEL_ROLE)

    def begin_turn(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> VisualActorTurnAdmission:
        admitted = self._counter.begin(
            VISUAL_ACTOR_MODEL_ROLE,
            reserve=max(1, self._counter.remaining),
        )
        try:
            kwargs["_model_call_limit"] = admitted
            return self._role.begin_turn(*args, **kwargs)
        finally:
            self._counter.finish(VISUAL_ACTOR_MODEL_ROLE)

    def propose_flow_fragment(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._counter.begin(VISUAL_ACTOR_MODEL_ROLE, reserve=1)
        try:
            return self._role.propose_flow_fragment(*args, **kwargs)
        finally:
            self._counter.finish(VISUAL_ACTOR_MODEL_ROLE)


class PreparedLiveActor:
    """Exactly-once model resources shared by text admission and visual work."""

    def __init__(
        self,
        *,
        raw_client: Any,
        actor: _BudgetedActor,
        counter: _ModelCallCounter,
        usage_total: dict[str, Any],
    ) -> None:
        self.raw_client = raw_client
        self.actor = actor
        self.counter = counter
        self._usage_total = usage_total
        self._closed = False
        self._lock = threading.Lock()

    @property
    def usage_total(self) -> dict[str, Any]:
        return deepcopy(self._usage_total)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        errors = _close_resources(
            (("Visual Actor client", self.raw_client),)
        )
        if errors:
            raise ExceptionGroup(
                "Visual Actor resource cleanup failed",
                list(errors),
            )


class _RawReceiptTrace:
    def record(
        self,
        receipt: RawActionReceipt,
        *,
        metadata: Mapping[str, Any],
    ) -> None:
        target_window = {
            key: value
            for key, value in {
                "title": str(metadata.get("target_title") or "").strip(),
                "process": str(metadata.get("target_process") or "").strip(),
            }.items()
            if value
        }
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
            **({"target_window": target_window} if target_window else {}),
            parameter_visibility=_PRIVATE_RUNTIME_EVIDENCE,
        )


class _LiveAgentSession:
    def __init__(
        self,
        *,
        runtime: MissionRuntime,
        reducer: MissionReducer,
        registry: EvidenceRegistry,
        authority: FrameAuthority,
        binding: TargetBinding,
        counter: _ModelCallCounter,
        client: Any,
        controller: AtomicDesktopAdapter | None = None,
    ) -> None:
        self._runtime = runtime
        self._reducer = reducer
        self._registry = registry
        self._authority = authority
        self._binding = binding
        self._counter = counter
        self._client = client
        self._controller = controller
        self._closed = False
        self._lock = threading.Lock()

    @property
    def model_calls(self) -> int:
        return self._counter.count

    @property
    def latest_narration(self) -> str:
        """The Actor's most recent progress note in its own words."""

        try:
            return str(self._runtime.latest_narration or "")
        except Exception:
            return ""

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: Any,
        rejection_feedback: str | None = None,
    ) -> MissionRuntimeResult:
        return self._runtime.run_one_turn(
            turn_index=turn_index,
            stop_event=stop_event,
            rejection_feedback=rejection_feedback,
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
            "target_binding_id": (
                projection.target_binding.binding_id
                if projection.target_binding is not None
                else None
            ),
            "target_hwnd": (
                projection.target_binding.hwnd
                if projection.target_binding is not None
                else None
            ),
            "target_epoch": (
                projection.target_binding.epoch
                if projection.target_binding is not None
                else None
            ),
            "frame_id": frame.frame_id if frame is not None else None,
            "frame_hwnd": frame.hwnd if frame is not None else None,
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
            "target_binding_id": (
                projection.target_binding.binding_id
                if projection.target_binding is not None
                else None
            ),
            "target_hwnd": (
                projection.target_binding.hwnd
                if projection.target_binding is not None
                else None
            ),
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

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        errors: list[Exception] = []
        if self._controller is not None:
            try:
                self._controller.close()
            except Exception as exc:
                errors.append(exc)
        errors.extend(_close_resources(
            (
                ("evidence registry", self._registry),
                ("Visual Actor client", self._client),
            )
        ))
        if errors:
            raise ExceptionGroup(
                "vNext live Agent session cleanup failed",
                errors,
            )


def _model_attempt_trace(
    *,
    run_id: str,
    mission_id: str,
    model: _ModelConfig,
    model_role: str,
    counter: _ModelCallCounter,
    publish_run_telemetry: bool = True,
) -> Callable[[ModelAttempt], None]:
    def record(attempt: ModelAttempt) -> None:
        counter.record_attempt(
            model_role,
            attempt.request_fingerprint,
        )
        if not publish_run_telemetry:
            return
        BUS.publish(
            "vnext_model_call",
            runtime_version=_RUNTIME_VERSION,
            no_legacy_fallback=True,
            run_id=run_id,
            mission_id=mission_id,
            model=model.model,
            model_role=model_role,
            attempt=attempt.attempt,
            ok=attempt.ok,
            request_fingerprint=attempt.request_fingerprint,
            elapsed_ms=attempt.elapsed_ms,
            error_kind=attempt.error_kind,
            retryable=attempt.retryable,
        )

    return record


def _build_actor_resources(
    *,
    run_id: str,
    mission_id: str,
    budget: _Budget,
    model: _ModelConfig,
    dependencies: _CompositionDependencies,
    deadline: Callable[[], float],
    publish_run_telemetry: bool = True,
    role_factory: Callable[..., Any] = LiveVisualActor,
    status_provider: _LiveAgentStatusProvider | None = None,
) -> PreparedLiveActor:
    raw_client: Any | None = None
    try:
        request_timeout = _remaining_timeout(
            clock=dependencies.monotonic,
            deadline=deadline,
            configured_timeout_s=min(
                model.request_timeout_s,
                model.total_timeout_s,
            ),
        )
        native_protocol = native_provider_protocol(model.api)
        if native_protocol is None:
            raw_client = create_openai_client(
                base_url=model.base_url,
                api_key=model.api_key,
                timeout=request_timeout,
                client_factory=dependencies.client_factory,
                api_config=model.api,
            )
            model_adapter: Any = build_model_adapter(raw_client, model.api)
        else:
            raw_headers = model.api.get("custom_headers")
            if raw_headers is None:
                raw_headers = model.api.get("headers")
            if raw_headers is not None and not isinstance(raw_headers, Mapping):
                raise ValueError("config.api.custom_headers must be an object")
            raw_client = ProviderProtocolAdapter(
                family=native_protocol,
                base_url=model.base_url,
                api_key=model.api_key,
                model=model.model,
                timeout=request_timeout,
                custom_headers=(
                    dict(raw_headers)
                    if isinstance(raw_headers, Mapping)
                    else None
                ),
                transport=(
                    dependencies.provider_transport_factory()
                    if dependencies.provider_transport_factory is not None
                    else None
                ),
            )
            model_adapter = raw_client
        counter = _ModelCallCounter(budget.max_model_calls)
        actor_client = _DeadlineModelAdapter(
            model_adapter,
            model=model,
            clock=dependencies.monotonic,
            deadline=deadline,
        )
        usage_total = empty_usage_total()
        usage_profile = model_profile_from_config(model.api)

        def record_usage(response: Any) -> None:
            tool_shapes = _model_tool_shapes(response)
            response_shape = _model_response_shape(response)
            if publish_run_telemetry and response_shape:
                BUS.publish(
                    "vnext_model_response_shape",
                    runtime_version=_RUNTIME_VERSION,
                    no_legacy_fallback=True,
                    run_id=run_id,
                    mission_id=mission_id,
                    model_role=VISUAL_ACTOR_MODEL_ROLE,
                    **response_shape,
                )
            if publish_run_telemetry and tool_shapes:
                BUS.publish(
                    "vnext_model_tool_shape",
                    runtime_version=_RUNTIME_VERSION,
                    no_legacy_fallback=True,
                    run_id=run_id,
                    mission_id=mission_id,
                    model_role=VISUAL_ACTOR_MODEL_ROLE,
                    calls=tool_shapes,
                )
            step_usage = accumulate_usage(
                usage_total,
                response,
                profile=usage_profile,
                api=model.api,
            )
            if step_usage is None:
                return
            if not publish_run_telemetry:
                return
            BUS.publish(
                "agent_usage",
                runtime_version=_RUNTIME_VERSION,
                no_legacy_fallback=True,
                run_id=run_id,
                mission_id=mission_id,
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                step_usage=step_usage,
                total=deepcopy(usage_total),
            )

        def publish_actor_activity(activity: Mapping[str, Any]) -> None:
            if not publish_run_telemetry:
                return
            kind = str(activity.get("kind") or "activity")
            ok = activity.get("ok")
            BUS.publish(
                "live_agent_activity",
                runtime_version=_RUNTIME_VERSION,
                no_legacy_fallback=True,
                run_id=run_id,
                mission_id=mission_id,
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                kind=kind,
                phase=(
                    "acting"
                    if kind in {"tool_start", "tool_result"}
                    else "deciding"
                ),
                tool=str(activity.get("tool") or ""),
                content=str(activity.get("message") or "")[:4096],
                command=str(activity.get("command") or "")[:16_384],
                task_state=str(activity.get("task_state") or "")[:8192],
                status=(
                    "failed"
                    if ok is False
                    else "done"
                    if kind == "tool_result"
                    else "running"
                    if kind == "tool_start"
                    else "updated"
                    if kind == "task_state"
                    else ""
                ),
            )

        role_kwargs: dict[str, Any] = {
            "client": actor_client,
            "model": model.model,
            "runtime": ModelCallRuntime(clock=dependencies.monotonic),
            "total_timeout_s": _remaining_timeout(
                clock=dependencies.monotonic,
                deadline=deadline,
                configured_timeout_s=model.total_timeout_s,
            ),
            "on_attempt": _model_attempt_trace(
                run_id=run_id,
                mission_id=mission_id,
                model=model,
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                counter=counter,
                publish_run_telemetry=publish_run_telemetry,
            ),
            "on_response": record_usage,
            "max_attempts": _configured_max_attempts(model.api),
        }
        if role_factory is LiveVisualActor:
            role_kwargs["on_activity"] = publish_actor_activity
            role_kwargs["connector_gateway"] = (
                dependencies.connector_factory()
                if dependencies.connector_factory is not None
                else None
            )
            if status_provider is not None:
                role_kwargs["status_provider"] = status_provider
            if dependencies.shell_factory is not None:
                role_kwargs["shell_runner"] = dependencies.shell_factory()
            role_kwargs["window_text_reader"] = _window_text_reader_factory()
            role_kwargs["capture_runner"] = _capture_runner_factory()
            role_kwargs["windows_reader"] = _windows_reader_factory()
            role_kwargs["paste_runner"] = _paste_runner_factory()
        actor = _BudgetedActor(role_factory(**role_kwargs), counter)
        return PreparedLiveActor(
            raw_client=raw_client,
            actor=actor,
            counter=counter,
            usage_total=usage_total,
        )
    except Exception as exc:
        cleanup_errors = _close_resources(
            (("Visual Actor client", raw_client),)
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "Visual Actor composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise


def _model_tool_shapes(response: Any) -> list[dict[str, Any]]:
    """Project model calls to non-content diagnostics at the parse boundary."""

    def value(container: Any, name: str) -> Any:
        if isinstance(container, Mapping):
            return container.get(name)
        return getattr(container, name, None)

    choices = value(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        return []
    message = value(choices[0], "message")
    calls = value(message, "tool_calls")
    if not isinstance(calls, (list, tuple)):
        return []
    projected: list[dict[str, Any]] = []
    for call in calls:
        function = value(call, "function")
        name = value(function, "name")
        raw_arguments = value(function, "arguments")
        if type(name) is not str:
            continue
        arguments: dict[str, Any] = {}
        if type(raw_arguments) is str:
            try:
                decoded = json.loads(raw_arguments)
            except (TypeError, ValueError):
                decoded = None
            if type(decoded) is dict:
                arguments = decoded
        argument_types = {
            key: (
                "null"
                if item is None
                else "bool"
                if type(item) is bool
                else "number"
                if type(item) in {int, float}
                else "string"
                if type(item) is str
                else "array"
                if type(item) is list
                else "object"
                if type(item) is dict
                else type(item).__name__
            )
            for key, item in sorted(arguments.items())
        }
        discriminator = arguments.get("action", arguments.get("status"))
        projected.append({
            "name": name,
            "discriminator": (
                discriminator if type(discriminator) is str else ""
            ),
            "argument_types": argument_types,
        })
    return projected


def _model_response_shape(response: Any) -> dict[str, Any]:
    """Project finish mechanics without response text or argument values."""

    def value(container: Any, name: str) -> Any:
        if isinstance(container, Mapping):
            return container.get(name)
        return getattr(container, name, None)

    choices = value(response, "choices")
    if not isinstance(choices, (list, tuple)):
        return {}
    if len(choices) != 1:
        return {"choice_count": len(choices)}
    choice = choices[0]
    message = value(choice, "message")
    content = value(message, "content")
    calls = value(message, "tool_calls")
    return {
        "choice_count": 1,
        "finish_reason": str(value(choice, "finish_reason") or ""),
        "content_type": "null" if content is None else type(content).__name__,
        "content_chars": len(content) if isinstance(content, str) else 0,
        "tool_call_count": len(calls) if isinstance(calls, (list, tuple)) else 0,
    }


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


def _build_live_session(build: _SessionBuild) -> _LiveAgentSession:
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
                ),
            ),
            source="vnext-live-agent",
            experience_policy="disabled",
        )
    except Exception as exc:
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
        initial_surface = _workspace_surface(
            build.target.reader(binding.hwnd)
        )
        if initial_surface is None:
            raise RuntimeError(
                "initial target is unavailable to DesktopWorkspace"
            )
        workspace = DesktopWorkspace(
            binding=binding,
            surface=initial_surface,
            reader=lambda hwnd: _workspace_surface(
                build.target.reader(hwnd)
            ),
            foreground_reader=build.target.foreground_reader,
            hit_test_root=build.target.hit_test_root,
            desktop_shell=build.target.desktop_shell,
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
            workspace=workspace,
            authority=authority,
            observer=observer,
            pause_controller=build.pause_controller,
        )
    except Exception as exc:
        cleanup_errors: list[Exception] = []
        cleanup_errors.extend(
            _close_resources((("evidence registry", registry),))
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise
    try:
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
            target_probe=workspace,
            desktop_workspace=workspace,
            raw_device=raw,
            receipt_trace_sink=_RawReceiptTrace(),
            launcher=_launch_application_primitive,
            desktop_shell=build.target.desktop_shell,
        )
        state.bind_pointer_state_reader(controller.pointer_state)
    except Exception as exc:
        cleanup_errors = []
        cleanup_errors.extend(
            _close_resources(
                (
                    ("evidence registry", registry),
                )
            )
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext live Agent owner composition and cleanup failed",
                [exc, *cleanup_errors],
            ) from exc
        raise
    try:
        actor_bundle = build.actor_bundle
        counter = actor_bundle.counter
        catalog = _tool_catalog()
        profile = ExecutionPolicy(
            provider_availability=(),
        ).safe_default(
            build.mission_id,
            definition.goal_revision,
            available_capabilities=("visual.interact",),
        )
        # One mechanical fuel gauge per turn: remaining turns, model calls
        # and wall time as facts only.  The Actor decides how to pace long
        # multi-part goals; the runtime never spends on its behalf.
        frozen_budget = build.budget
        budget_clock = build.dependencies.monotonic
        budget_deadline = build.deadline

        def budget_facts(turn_index: int) -> dict[str, int]:
            del turn_index
            remaining_s = int(
                float(budget_deadline()) - float(budget_clock())
            )
            elapsed_s = max(0, frozen_budget.max_runtime_s - remaining_s)
            return {
                "max_turns": frozen_budget.max_turns,
                "max_model_calls": frozen_budget.max_model_calls,
                "max_runtime_s": frozen_budget.max_runtime_s,
                "model_calls_used": counter.count,
                "elapsed_s": elapsed_s,
            }

        runtime = MissionRuntime(
            reducer=reducer,
            profile=profile,
            broker=CapabilityBroker(catalog),
            catalog=catalog,
            actor=actor_bundle.actor,
            state=state,
            controller=controller,
            evidence_registry=registry,
            budget_facts_provider=budget_facts,
        )
        return _LiveAgentSession(
            runtime=runtime,
            reducer=reducer,
            registry=registry,
            authority=authority,
            binding=binding,
            counter=counter,
            client=actor_bundle,
            controller=controller,
        )
    except Exception as exc:
        cleanup_errors = []
        cleanup_errors.extend(
            _close_resources(
                (
                    ("evidence registry", registry),
                    ("model resources", build.actor_bundle),
                )
            )
        )
        if cleanup_errors:
            raise ExceptionGroup(
                "vNext live Agent composition and cleanup failed",
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
) -> str | None:
    verified_refs = snapshot.get("verified_evidence_refs")
    bindings = snapshot.get("verified_evidence_bindings")
    frame_id = snapshot.get("frame_id")
    target_binding_id = snapshot.get("target_binding_id")
    target_hwnd = snapshot.get("target_hwnd")
    target_epoch = snapshot.get("target_epoch")
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
        or type(target_binding_id) is not str
        or not target_binding_id
        or type(target_hwnd) is not int
        or target_hwnd <= 0
        or type(target_epoch) is not int
        or target_epoch <= 0
        or snapshot.get("frame_hwnd") != target_hwnd
        or type(frame_id) is not int
        or frame_id <= 0
        or snapshot.get("frame_epoch") != target_epoch
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


def classify_live_agent_failure_stage(
    terminal_status: RuntimeStatus,
    detail: str,
    *,
    lease_state: Mapping[str, Any],
    stage_hint: str = "",
) -> str:
    """Classify one terminal vNext failure into a product-visible stage.

    Stages: model_routing, desktop_handoff, target_binding, screenshot,
    tool_format, physical_input, result_verification.  The classification is
    mechanical; it never rewrites the underlying detail string.
    """
    if terminal_status is RuntimeStatus.MISSION_VERIFIED:
        return ""
    if terminal_status is RuntimeStatus.CANCELED:
        return "canceled"
    if stage_hint:
        return stage_hint
    status = terminal_status.value
    if status in {"invalid_proposal"}:
        return "tool_format"
    if status in {"actor_error", "unavailable"}:
        return "model_routing"
    # ``blocked`` is the Actor's own semantic verdict ("I cannot advance"),
    # not a physical-input failure.  Keep it out of the system-failure
    # buckets; it is the model layer deciding to stop.
    if status in {"blocked"}:
        return "model_routing"
    lease_status = str(lease_state.get("status") or "")
    lease_error = str(lease_state.get("error") or "")
    if status == "state_rejected":
        lowered = f"{detail} {lease_error}".lower()
        if "desktop lease" in lowered or "handoff" in lowered:
            return "desktop_handoff"
        if "target" in lowered and (
            "mismatch" in lowered
            or "missing" in lowered
            or "stale" in lowered
            or "expired" in lowered
            or "descriptor" in lowered
            or "binding" in lowered
        ):
            return "target_binding"
        if "jpeg" in lowered or "capture" in lowered or "frame" in lowered:
            return "screenshot"
        if "verification" in lowered or "verify" in lowered or (
            "completion result" in lowered
        ):
            return "result_verification"
        if "budget" in lowered or "time budget" in lowered:
            return "model_routing"
        if "connector" in lowered:
            return "model_routing"
    if lease_status in {"closed"} and lease_error:
        lowered = lease_error.lower()
        if "target" in lowered or "binding" in lowered:
            return "target_binding"
        return "desktop_handoff"
    # A state_rejected with no recognized keyword is a Runtime/controller
    # precondition failure (physical input gate, permit, evidence), not an
    # automatic model-routing verdict.  Surface it as physical_input so the
    # system layer is not silently attributed to the model.
    if status == "state_rejected":
        return "physical_input"
    return "model_routing"


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
        "target_binding_id": (
            _public_identity(source.get("target_binding_id"))
            or target.binding_id
        ),
        "target_hwnd": (
            _public_optional_positive_int(source.get("target_hwnd"))
            or target.hwnd
        ),
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
        "target_binding_id": (
            _public_identity(source.get("target_binding_id"))
            or target.binding_id
        ),
        "target_hwnd": (
            _public_optional_positive_int(source.get("target_hwnd"))
            or target.hwnd
        ),
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
    """Per-action durable evidence recording was removed (Model-first).

    The run output keeps an explicit unavailable marker so downstream
    consumers never mistake the absence of a journal for a complete one.
    """
    del value
    return _unavailable_runtime_evidence_summary(
        mission_id,
        run_id,
    )


def _validate_goal_admission(
    context: Mapping[str, Any],
    *,
    goal: str,
) -> None:
    runtime_state = context.get("runtime_state")
    if not isinstance(runtime_state, Mapping):
        return
    raw = runtime_state.get("goal_admission")
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise RuntimeError("vNext goal admission audit is not an object")
    expected_fields = {
        "format",
        "goal",
        "route",
        "runtime_version",
        "reason",
        "no_legacy_fallback",
    }
    if set(raw) != expected_fields:
        raise RuntimeError("vNext goal admission audit fields are not exact")
    if (
        raw.get("format") != "goal-admission/1"
        or raw.get("goal") != goal
        or raw.get("route") != "live_agent"
        or raw.get("runtime_version") != _RUNTIME_VERSION
        or raw.get("reason") != "single_semantic_agent"
        or raw.get("no_legacy_fallback") is not True
    ):
        raise RuntimeError("vNext goal admission audit is inconsistent")


class _PreparedHandler:
    def __init__(
        self,
        *,
        graph: ArpGraph,
        goal: str,
        budget: _Budget,
        model: _ModelConfig,
        dependencies: _CompositionDependencies,
        history: tuple[dict[str, str], ...],
        desktop_lease: DesktopLeaseSlot,
        thread_id: str,
        turn_id: str,
    ) -> None:
        self._graph = graph
        self._goal = goal
        self._budget = budget
        self._model = model
        self._dependencies = dependencies
        self._history = tuple(deepcopy(history))
        self._desktop_lease = desktop_lease
        self._thread_id = thread_id
        self._turn_id = turn_id
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

    def _status_provider(
        self,
        run_id: str,
        mission_id: str,
    ) -> _LiveAgentStatusProvider:
        from ..memory import experience

        config_snapshot = _plain_dict(
            self._dependencies.config_snapshot(),
            "config snapshot",
        ) if self._dependencies.config_snapshot is not None else {}
        live_agent_enabled = bool(
            isinstance(config_snapshot.get("vnext"), dict)
            and config_snapshot["vnext"].get("live_agent_enabled") is True
        )
        config_experience_memory = bool(
            isinstance(config_snapshot.get("agent"), dict)
            and config_snapshot["agent"].get("experience_memory") is True
        )
        try:
            store_present = experience.memory_store_present()
        except Exception:
            store_present = False
        return _LiveAgentStatusProvider(
            runtime_version=_RUNTIME_VERSION,
            model=self._model.model,
            budget=self._budget,
            run_id=run_id,
            mission_id=mission_id,
            thread_id=self._thread_id,
            turn_id=self._turn_id,
            live_agent_enabled=live_agent_enabled,
            experience_policy="disabled",
            connector_available=(
                self._dependencies.connector_factory is not None
            ),
            config_experience_memory=config_experience_memory,
            memory_store_present=store_present,
        )

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
                    error="vNext live Agent handler is call-once",
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
        runtime_state.pop("model_usage", None)
        mission_id = (
            "vnext-mission-"
            + hashlib.sha256(
                f"{run_id}|{self._graph.id}".encode("utf-8")
            ).hexdigest()[:24]
        )
        _validate_goal_admission(
            context,
            goal=self._goal,
        )
        budget_wire = self._budget.to_dict()
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
        actor_bundle: PreparedLiveActor | None = None
        try:
            status_provider = self._status_provider(run_id, mission_id)
            actor_bundle = _build_actor_resources(
                run_id=run_id,
                mission_id=mission_id,
                budget=self._budget,
                model=self._model,
                dependencies=self._dependencies,
                deadline=lambda: deadline[0],
                status_provider=status_provider,
            )
            BUS.publish(
                "live_agent_start",
                run_id=run_id,
                mission_id=mission_id,
                runtime_version=_RUNTIME_VERSION,
                runtime_budget=budget_wire,
                no_legacy_fallback=True,
                model=self._model.model,
            )
            if not self._wait_if_paused(
                pause_controller,
                stop_event,
                deadline,
            ):
                raise RuntimeError(
                    "run canceled before the initial Actor turn"
                )
            admission = actor_bundle.actor.begin_turn(
                self._goal,
                history=self._history,
                stop_event=bounded_stop,
            )
            if admission.narration.strip():
                BUS.publish(
                    "live_agent_message",
                    run_id=run_id,
                    mission_id=mission_id,
                    runtime_version=_RUNTIME_VERSION,
                    narration=admission.narration.strip(),
                    phase="starting",
                )
            if admission.kind == "reply":
                runtime_state["user_result"] = admission.reply
                model_usage = actor_bundle.usage_total
                runtime_state["model_usage"] = model_usage
                output = {
                    "runtime_version": _RUNTIME_VERSION,
                    "runtime_budget": budget_wire,
                    "no_legacy_fallback": True,
                    "status": "reply",
                    "turns_used": 1,
                    "model_calls": actor_bundle.counter.count,
                }
                if model_usage.get("prompt_tokens") or model_usage.get(
                    "completion_tokens"
                ):
                    output["model_usage"] = model_usage
                BUS.publish(
                    "live_agent_done",
                    run_id=run_id,
                    mission_id=mission_id,
                    runtime_version=_RUNTIME_VERSION,
                    runtime_budget=budget_wire,
                    no_legacy_fallback=True,
                    status="reply",
                    turns_used=1,
                    model_calls=actor_bundle.counter.count,
                    model_usage=model_usage,
                    detail="",
                )
                actor_bundle.close()
                return NodeResult(
                    ok=True,
                    branch="reply",
                    output=output,
                )
            if admission.kind != "desktop":
                raise RuntimeError(
                    "initial Actor turn returned an unsupported result"
                )
            runtime_state["desktop_lease"] = (
                self._desktop_lease.request(run_id)
            )
            target = self._desktop_lease.wait(
                stop_event=bounded_stop,
                deadline=lambda: deadline[0],
                clock=self._dependencies.monotonic,
            )
            runtime_state["desktop_lease"] = (
                self._desktop_lease.snapshot()
            )
            runtime_state["target_binding_id"] = target.binding_id
        except Exception as exc:
            runtime_state["desktop_lease"] = (
                self._desktop_lease.snapshot()
            )
            model_usage = (
                actor_bundle.usage_total
                if actor_bundle is not None
                else empty_usage_total()
            )
            runtime_state["model_usage"] = model_usage
            if actor_bundle is not None:
                try:
                    actor_bundle.close()
                except Exception:
                    pass
            detail = str(exc) or "vNext initial Actor turn failed"
            failure_stage = classify_live_agent_failure_stage(
                (
                    RuntimeStatus.CANCELED
                    if stop_event.is_set()
                    else RuntimeStatus.STATE_REJECTED
                ),
                detail,
                lease_state=self._desktop_lease.snapshot(),
                stage_hint=(
                    "desktop_handoff"
                    if isinstance(exc, DesktopLeaseError)
                    and "target" not in detail.lower()
                    else "target_binding"
                    if isinstance(exc, DesktopLeaseError)
                    else "model_routing"
                ),
            )
            BUS.publish(
                "live_agent_done",
                run_id=run_id,
                mission_id=mission_id,
                runtime_version=_RUNTIME_VERSION,
                runtime_budget=budget_wire,
                no_legacy_fallback=True,
                status=(
                    RuntimeStatus.CANCELED.value
                    if stop_event.is_set()
                    else RuntimeStatus.STATE_REJECTED.value
                ),
                failure_stage=failure_stage,
                turns_used=0,
                model_calls=(
                    actor_bundle.counter.count
                    if actor_bundle is not None
                    else 0
                ),
                model_usage=model_usage,
                detail=detail[:500],
            )
            return NodeResult(
                ok=False,
                branch="canceled" if stop_event.is_set() else "fail",
                output={
                    "runtime_version": _RUNTIME_VERSION,
                    "runtime_budget": budget_wire,
                    "no_legacy_fallback": True,
                    "status": (
                        RuntimeStatus.CANCELED.value
                        if stop_event.is_set()
                        else RuntimeStatus.STATE_REJECTED.value
                    ),
                    "failure_stage": failure_stage,
                    "turns_used": 0,
                    "model_calls": (
                        actor_bundle.counter.count
                        if actor_bundle is not None
                        else 0
                    ),
                    "model_usage": model_usage,
                },
                error=detail,
            )
        runtime_evidence_summary = (
            _unavailable_runtime_evidence_summary(
                mission_id,
                run_id,
            )
        )
        session: _SessionPort | None = None
        turns_used = 0
        terminal_status = RuntimeStatus.STATE_REJECTED
        detail = ""
        accepted_event_ids: tuple[str, ...] = ()
        session_build = _SessionBuild(
            graph=self._graph,
            goal=self._goal,
            run_id=run_id,
            mission_id=mission_id,
            target=target,
            budget=self._budget,
            model=self._model,
            dependencies=self._dependencies,
            deadline=lambda: deadline[0],
            actor_bundle=actor_bundle,
            pause_controller=pause_controller,
        )
        try:
            if not self._wait_if_paused(
                pause_controller,
                stop_event,
                deadline,
            ):
                terminal_status = RuntimeStatus.CANCELED
                detail = "run canceled before vNext session composition"
            else:
                builder = (
                    self._dependencies.session_builder
                    or _build_live_session
                )
                session = builder(session_build)
                rejection_feedback = ""
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
                        rejection_feedback=rejection_feedback or None,
                    )
                    rejection_feedback = ""
                    turns_used += 1
                    terminal_status = result.status
                    # Keep the real per-turn failure reason (error_kind,
                    # rejection diagnostic, format error) instead of replacing
                    # it with a generic status string: the model and the
                    # product surface both need the actual cause.  Transport
                    # failures may carry sensitive provider payload after the
                    # attempt marker; that tail is stripped before exposure.
                    raw_detail = str(result.detail or "")
                    if "model transport failed" in raw_detail:
                        # Transport failures may carry sensitive provider
                        # payload (prompt or model response) inside the
                        # detail; only the typed prefix survives the public
                        # boundary.
                        marker = "attempt(s):"
                        if marker in raw_detail:
                            detail = raw_detail.split(marker, 1)[0] + marker
                        else:
                            detail = raw_detail
                    else:
                        detail = raw_detail or _public_runtime_detail(
                            result.status
                        )
                    redecision_budget_available = bool(
                        turn_index + 1 < self._budget.max_turns
                        and session.model_calls
                        < self._budget.max_model_calls
                        and not bounded_stop.is_set()
                    )
                    invalid_proposal_recovery_available = (
                        result.status is RuntimeStatus.INVALID_PROPOSAL
                        and redecision_budget_available
                    )
                    actor_error_recovery_available = (
                        result.status is RuntimeStatus.ACTOR_ERROR
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
                            target=target,
                        )
                    except Exception:
                        turn_snapshot = _public_turn_snapshot(
                            {},
                            target=target,
                        )
                    BUS.publish(
                        "live_agent_turn",
                        run_id=run_id,
                        mission_id=mission_id,
                        runtime_version=_RUNTIME_VERSION,
                        no_legacy_fallback=True,
                        turn_index=turn_index,
                        status=terminal_status.value,
                        runtime_status=result.status.value,
                        proposal_kind=result.proposal_kind,
                        decision_id=result.decision_id,
                        narration=str(
                            getattr(session, "latest_narration", "") or ""
                        )[:4096],
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
                    if invalid_proposal_recovery_available:
                        # Feed the real rejection reason back to the same
                        # model on the next turn so it can correct the exact
                        # failing tool call instead of repeating it blind.
                        rejection_feedback = raw_detail[:_MAX_REJECTION_FEEDBACK_CHARS]
                        continue
                    if actor_error_recovery_available:
                        rejection_feedback = (
                            "The previous model request timed out or failed. "
                            "Re-observe the current screen and state, account "
                            "for any completed tool work, and choose the "
                            "shortest useful next step."
                        )
                        continue
                    if terminal_status not in _CONTINUE_STATUSES:
                        break
                else:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = "vNext turn budget exhausted"
        except Exception:
            terminal_status = RuntimeStatus.STATE_REJECTED
            detail = "vNext live Agent failed closed"
        finally:
            model_usage = actor_bundle.usage_total
            runtime_state["model_usage"] = model_usage
            raw_completion_snapshot: dict[str, Any] = {
                "mission_id": mission_id,
                "run_id": run_id,
                "target_binding_id": target.binding_id,
                "target_hwnd": target.hwnd,
            }
            model_calls = actor_bundle.counter.count
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
                    )
                    if binding_error is not None:
                        terminal_status = RuntimeStatus.STATE_REJECTED
                        detail = binding_error
                try:
                    session.close()
                except Exception:
                    if terminal_status is not RuntimeStatus.MISSION_VERIFIED:
                        terminal_status = RuntimeStatus.STATE_REJECTED
                        cleanup_detail = "vNext session cleanup failed closed"
                        detail = (
                            f"{detail}; {cleanup_detail}"
                            if detail
                            else cleanup_detail
                        )
            try:
                actor_bundle.close()
            except Exception:
                if terminal_status is not RuntimeStatus.MISSION_VERIFIED:
                    terminal_status = RuntimeStatus.STATE_REJECTED
                    detail = (
                        f"{detail}; model resource cleanup failed"
                        if detail
                        else "model resource cleanup failed"
                    )
            snapshot = _public_completion_snapshot(
                raw_completion_snapshot,
                mission_id=mission_id,
                run_id=run_id,
                target=target,
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
            else:
                # The task did not finish: the user must see the Actor's own
                # words (what it did, what it found, what blocked it), not a
                # rigid system string.  The latest narration is the model's
                # real dialogue; the system detail stays in the trace.
                narration = (
                    str(getattr(session, "latest_narration", "") or "")
                    if session is not None
                    else ""
                ).strip()
                if narration:
                    runtime_state["user_result"] = narration[:16_384]
                    if not detail:
                        detail = narration[:500]
            output = {
                **snapshot,
                "runtime_version": _RUNTIME_VERSION,
                "runtime_budget": budget_wire,
                "no_legacy_fallback": True,
                "status": terminal_status.value,
                "failure_stage": classify_live_agent_failure_stage(
                    terminal_status,
                    detail,
                    lease_state=(
                        self._desktop_lease.snapshot()
                        if terminal_status is not RuntimeStatus.MISSION_VERIFIED
                        else {}
                    ),
                ),
                "turns_used": turns_used,
                "model_calls": model_calls,
                "accepted_event_ids": list(accepted_event_ids),
                "runtime_evidence": runtime_evidence_summary,
            }
            if model_usage.get("prompt_tokens") or model_usage.get(
                "completion_tokens"
            ):
                output["model_usage"] = model_usage
            BUS.publish(
                "live_agent_done",
                run_id=run_id,
                mission_id=mission_id,
                runtime_version=_RUNTIME_VERSION,
                runtime_budget=budget_wire,
                no_legacy_fallback=True,
                status=terminal_status.value,
                failure_stage=output["failure_stage"],
                turns_used=turns_used,
                model_calls=model_calls,
                model_usage=model_usage,
                detail=detail[:500],
                runtime_evidence=runtime_evidence_summary,
                target_binding_id=snapshot.get("target_binding_id"),
                target_hwnd=snapshot.get("target_hwnd"),
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
            "source": "vnext_actor_claim",
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
                "target_binding_id": snapshot.get("target_binding_id"),
                "target_hwnd": snapshot.get("target_hwnd"),
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


def _configured_max_attempts(api: Mapping[str, Any]) -> int:
    """Resolve the Actor transport attempt budget from config.api.

    ``api.max_retries`` is the number of retries after the first attempt, so
    the total attempt budget is retries + 1, clamped to the ModelRetryPolicy
    ceiling of 5.  Defaults to 1 attempt (no retry) when unset.
    """
    raw = api.get("max_retries")
    if raw is None:
        return 1
    try:
        retries = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, retries + 1))


def _shell_runner_factory() -> Any | None:
    """Return the vNext shell runner when shell execution is enabled.

    ``config.agent.shell_enabled`` gates the whole capability; when disabled
    the tool is simply not offered to the Actor (same as connector/status).
    The runner is the existing bounded shell primitive with its safety guard.
    """
    try:
        from ..action import shell as shell_module

        if shell_module.shell_enabled():
            return shell_module
    except Exception:
        return None
    return None


def prepare_live_vnext_execution(
    graph: ArpGraph,
    inputs: dict[str, Any],
    config_snapshot: dict[str, Any],
    *,
    run_manager: Any,
    thread_id: str,
    turn_id: str,
    history: tuple[dict[str, str], ...],
    _dependencies: _CompositionDependencies | None = None,
) -> ArpExecution:
    """Validate and register one default-unreachable live vNext execution."""

    dependencies = _dependencies or _CompositionDependencies(
        connector_factory=ConnectorGateway,
        config_snapshot=CONFIG.snapshot,
        shell_factory=_shell_runner_factory,
    )
    if type(config_snapshot) is not dict:
        raise ValueError("vNext live Agent config snapshot must be a dict")
    frozen_config = deepcopy(config_snapshot)
    goal = _validate_exact_graph(graph)
    budget = _freeze_budget(frozen_config)
    model = _freeze_model(frozen_config, budget)
    frozen_inputs = deepcopy(_plain_dict(inputs, "inputs"))
    if frozen_inputs:
        raise ValueError(
            "initial vNext Agent inputs must not pre-bind a desktop target"
        )
    if not thread_id or not turn_id:
        raise ValueError("vNext Agent execution requires thread and turn ids")
    frozen_history = tuple(deepcopy(history))
    desktop_lease = DesktopLeaseSlot(
        graph=graph,
        dependencies=dependencies,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    prepare = getattr(run_manager, "prepare", None)
    if not callable(prepare):
        raise TypeError("run_manager must provide prepare()")
    handler = _PreparedHandler(
        graph=graph,
        goal=goal,
        budget=budget,
        model=model,
        dependencies=dependencies,
        history=frozen_history,
        desktop_lease=desktop_lease,
        thread_id=thread_id,
        turn_id=turn_id,
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
            "thread_id": thread_id,
            "turn_id": turn_id,
            "desktop_lease": desktop_lease.snapshot(),
            "_desktop_lease": desktop_lease,
        }
    )
    return execution


def propose_live_flow_fragment(
    goal: str,
    *,
    base_fingerprint: str,
    document: Mapping[str, Any],
    catalog: list[dict[str, Any]],
    config_snapshot: dict[str, Any],
    stop_event: threading.Event | None = None,
    _dependencies: _CompositionDependencies | None = None,
) -> dict[str, Any]:
    """Build one inert editor proposal with the configured model/provider path.

    This auxiliary call has no desktop lease, connector tools, thread history,
    saved-document authority, or execution authority.
    """

    if type(goal) is not str or not goal.strip() or len(goal.strip()) > 4000:
        raise ValueError("flow fragment goal must be 1 to 4000 characters")
    if (
        type(base_fingerprint) is not str
        or not base_fingerprint.startswith("sha256:")
        or len(base_fingerprint) != 71
        or any(char not in "0123456789abcdef" for char in base_fingerprint[7:])
    ):
        raise ValueError("flow fragment base_fingerprint is invalid")
    if not isinstance(document, Mapping) or set(document) != {
        "node_count", "edge_count", "node_types"
    }:
        raise ValueError("flow fragment document summary fields mismatch")
    if not isinstance(catalog, list) or not 1 <= len(catalog) <= 32:
        raise ValueError("flow fragment catalog must contain 1 to 32 nodes")
    ports: dict[str, tuple[str, ...]] = {}
    param_names: dict[str, frozenset[str]] = {}
    sanitized_catalog: list[dict[str, Any]] = []
    for index, item in enumerate(catalog):
        if type(item) is not dict or set(item) != {
            "type", "label", "params", "output_ports"
        }:
            raise ValueError(f"flow fragment catalog item {index + 1} fields mismatch")
        node_type = item["type"]
        output_ports = item["output_ports"]
        if type(node_type) is not str or not node_type or node_type in ports:
            raise ValueError("flow fragment catalog node type is invalid or duplicated")
        if (
            not isinstance(output_ports, list)
            or not output_ports
            or any(type(port) is not str or not port for port in output_ports)
        ):
            raise ValueError(f"flow fragment catalog {node_type!r} output ports are invalid")
        if type(item["label"]) is not str:
            raise ValueError(f"flow fragment catalog {node_type!r} text is invalid")
        if type(item["params"]) is not dict or any(
            type(key) is not str or not key for key in item["params"]
        ):
            raise ValueError(f"flow fragment catalog {node_type!r} params are invalid")
        ports[node_type] = tuple(dict.fromkeys(output_ports))
        param_names[node_type] = frozenset(item["params"])
        sanitized_catalog.append(deepcopy(item))

    budget = _freeze_budget(config_snapshot)
    model = _freeze_model(config_snapshot, budget)
    dependencies = _dependencies or _CompositionDependencies()
    run_id = f"flow-fragment-{uuid.uuid4().hex[:24]}"
    mission_id = f"flow-fragment-proposal-{uuid.uuid4().hex[:24]}"
    deadline = dependencies.monotonic() + min(
        float(budget.max_runtime_s),
        model.total_timeout_s,
    )
    actor_bundle = _build_actor_resources(
        run_id=run_id,
        mission_id=mission_id,
        budget=budget,
        model=model,
        dependencies=dependencies,
        deadline=lambda: deadline,
        publish_run_telemetry=False,
        role_factory=FlowFragmentProposer,
    )
    try:
        fragment = actor_bundle.actor.propose_flow_fragment(
            {
                "request": goal.strip(),
                "document": deepcopy(dict(document)),
                "catalog": sanitized_catalog,
            },
            catalog_ports=ports,
            catalog_params=param_names,
            stop_event=stop_event,
        )
        return {
            "format": "flow-fragment-proposal/1",
            "base_fingerprint": base_fingerprint,
            "proposal_usage": {
                "model_calls": actor_bundle.counter.count,
                "tokens": actor_bundle.usage_total,
            },
            **fragment,
        }
    finally:
        actor_bundle.close()


__all__ = [
    "DesktopLeaseError",
    "DesktopLeaseSlot",
    "build_exact_live_vnext_graph",
    "prepare_live_vnext_execution",
    "propose_live_flow_fragment",
]
