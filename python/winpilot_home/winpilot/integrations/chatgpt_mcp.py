"""MCP bridge that lets a logged-in ChatGPT/Codex client drive WinPilot.

The bridge deliberately points in one direction: the desktop client is the
planner and invokes local WinPilot tools. It never reads ChatGPT credentials,
cookies, or private desktop-app protocols, and it does not turn a ChatGPT
subscription into an OpenAI API key.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..action import executor
from ..agent.runtime_profile import build_runtime_profile
from ..agent.tools import ToolSession
from ..orchestrator.contracts import ArpGraph, ArpNodeKind, validate_graph
from ..recorder import trace as trace_mod
from ..utils.log import BUS


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ChatGPTBridgeHooks:
    """Server-owned operations used by the protocol adapter."""

    status: Callable[[], JsonDict]
    start_graph: Callable[[ArpGraph, JsonDict], JsonDict]
    get_run: Callable[[str], JsonDict]
    cancel_run: Callable[[str], JsonDict]
    decide_gate: Callable[[str, str, bool, str], JsonDict]
    busy: Callable[[], bool]
    enabled: Callable[[], bool]
    mutation_allowed: Callable[[], bool]


def _json_object(text: str) -> JsonDict | None:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _compact_element(value: Any) -> JsonDict | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("elem_id", "text", "role", "source", "bbox", "center", "score", "reason")
        if value.get(key) is not None
    }


def _compact_screen_delta(value: Any) -> JsonDict | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "changed", "ratio", "regions", "elapsed_ms", "capture_latency_ms",
            "delta_source", "transient_observed", "changed_in_target_region",
            "foreground_matches_target",
        )
        if value.get(key) is not None
    }


def _compact_controller_item(item: Any) -> JsonDict:
    if not isinstance(item, dict):
        return {"preview": str(item)[:600]}
    compact = {
        key: item.get(key)
        for key in ("index", "action", "ok", "verified", "elapsed_ms", "error", "terminal")
        if item.get(key) is not None
    }
    data = item.get("data")
    if isinstance(data, dict):
        detail = {
            key: data.get(key)
            for key in ("via", "ok", "blocked", "error", "verified", "verify_detail", "needs_llm_reason")
            if data.get(key) is not None
        }
        screen_delta = _compact_screen_delta(data.get("screen_delta"))
        if screen_delta:
            detail["screen_delta"] = screen_delta
        element = _compact_element(data.get("resolved_element"))
        if element:
            detail["resolved_element"] = element
        locator = data.get("anchor_locator")
        if isinstance(locator, dict):
            detail["anchor_locator"] = {
                key: locator.get(key)
                for key in ("found", "score", "cache_hit", "reason")
                if locator.get(key) is not None
            }
        input_locator = data.get("input_locator")
        if isinstance(input_locator, dict):
            detail["input_locator"] = {
                "found": input_locator.get("found"),
                "score": input_locator.get("score"),
                "reason": input_locator.get("reason"),
                "candidate": _compact_element(input_locator.get("candidate")),
            }
        compact["detail"] = detail
    elif item.get("preview"):
        compact["preview"] = str(item["preview"])[:800]
    return compact


def _compact_controller_payload(payload: JsonDict) -> JsonDict:
    compact = {
        key: payload.get(key)
        for key in (
            "ok", "error", "blocked", "verified", "verify_detail", "subgoal",
            "subgoal_route", "missing", "missing_texts", "missing_visual_colors",
            "visible_colors", "needs_llm_reason", "controller_state", "failed_at", "phase",
        )
        if payload.get(key) is not None
    }
    if payload.get("visible_text"):
        compact["visible_text"] = str(payload["visible_text"])[:2000]
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        compact["metrics"] = {
            key: metrics.get(key)
            for key in (
                "elapsed_ms", "action_count", "failed_at", "observe_count", "verify_count",
                "screen_wait_count", "auto_observe_count", "internal_action_count",
                "compound_round_trips_saved", "controller_round_trips_saved", "avg_tool_latency_ms",
            )
            if metrics.get(key) is not None
        }
    evidence = payload.get("evidence_packet")
    if isinstance(evidence, dict):
        compact["evidence_packet"] = {
            key: evidence.get(key)
            for key in (
                "matched_texts", "missing_texts", "verified_expect_texts", "visible_colors",
                "matched_visual_colors", "missing_visual_colors", "candidate_click_labels",
            )
            if evidence.get(key) is not None
        }
        if evidence.get("visible_text_preview"):
            compact["evidence_packet"]["visible_text_preview"] = str(
                evidence["visible_text_preview"]
            )[:1600]
    results = payload.get("results")
    if isinstance(results, list):
        compact["results"] = [_compact_controller_item(item) for item in results[:30]]
        if len(results) > 30:
            compact["results_truncated"] = len(results) - 30
    return compact


class ChatGPTDesktopBridge:
    """Stateful, single-desktop adapter behind the MCP tool surface."""

    def __init__(self, hooks: ChatGPTBridgeHooks) -> None:
        self.hooks = hooks
        self._lock = threading.RLock()
        self._session: ToolSession | None = None
        self._session_id = ""

    def _new_session(self, task: str = "ChatGPT desktop control") -> ToolSession:
        session = ToolSession(task=task, runtime_profile=build_runtime_profile(task))
        self._session_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        session.run_id = None
        return session

    def _get_session(self, task: str = "") -> ToolSession:
        if self._session is None:
            self._session = self._new_session(task or "ChatGPT desktop control")
        elif task:
            self._session.task = task
        return self._session

    def reset(self) -> JsonDict:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._session = self._new_session()
            return {"ok": True, "session_id": self._session_id}

    def _guard(self, *, mutating: bool) -> JsonDict | None:
        if not self.hooks.enabled():
            return {"ok": False, "error": "ChatGPT desktop bridge is disabled in WinPilot settings."}
        if mutating and not self.hooks.mutation_allowed():
            return {"ok": False, "error": "Desktop mutation is disabled for the ChatGPT bridge."}
        if mutating and self.hooks.busy():
            return {
                "ok": False,
                "error": "WinPilot already has an active agent, replay, recording, or ARP run.",
                "retryable": True,
            }
        return None

    def dispatch(self, name: str, args: JsonDict, *, task: str = "", mutating: bool = False) -> JsonDict:
        guard = self._guard(mutating=mutating)
        if guard:
            return guard
        with self._lock:
            session = self._get_session(task)
            run_id = None
            started = time.monotonic()
            if mutating:
                run_id = f"mcp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
                session.run_id = run_id
                BUS.publish(
                    "agent_start",
                    run_id=run_id,
                    task=task or f"ChatGPT desktop: {name}",
                    source="chatgpt_mcp",
                )
                BUS.publish("agent_tool_start", run_id=run_id, tool=name, source="chatgpt_mcp")
            result = session.dispatch(name, args)
            parsed = _json_object(result.text)
            if parsed is not None:
                payload = dict(parsed)
                payload.setdefault("ok", result.terminal != "fail")
            else:
                payload = {
                    "ok": result.terminal != "fail" and not result.text.startswith("Unknown tool:"),
                    "text": result.text,
                }
            if name in {"execute_subgoal", "run_action_batch"}:
                payload = _compact_controller_payload(payload)
            elif isinstance(payload.get("text"), str) and len(payload["text"]) > 32_000:
                payload["text"] = payload["text"][:32_000]
                payload["text_truncated"] = True
            payload["bridge_session_id"] = self._session_id
            payload["tool"] = name
            if result.summary:
                payload["summary"] = result.summary
            if isinstance(result.replay, dict):
                payload["replay"] = {
                    key: result.replay.get(key)
                    for key in ("tool", "action", "subgoal", "elapsed_ms", "verified")
                    if result.replay.get(key) is not None
                }
            if run_id:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                status = "done" if payload.get("ok") else "failed"
                BUS.publish(
                    "agent_tool",
                    run_id=run_id,
                    tool=name,
                    elapsed_ms=elapsed_ms,
                    result_preview=json.dumps(payload, ensure_ascii=False, default=str)[:800],
                    result_meta={
                        "verified": payload.get("verified"),
                        "needs_llm_reason": payload.get("needs_llm_reason"),
                    },
                    source="chatgpt_mcp",
                )
                BUS.publish(
                    "agent_done",
                    run_id=run_id,
                    status=status,
                    result={
                        "tool": name,
                        "ok": bool(payload.get("ok")),
                        "verified": payload.get("verified"),
                    },
                    source="chatgpt_mcp",
                )
                session.run_id = None
                payload["run_id"] = run_id
            return payload

    def visual_capture(
        self,
        *,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
        quality: int,
    ) -> tuple[JsonDict, bytes | None]:
        guard = self._guard(mutating=False)
        if guard:
            return guard, None
        quality = max(35, min(int(quality), 90))
        roi_values = (x, y, width, height)
        if any(value is not None for value in roi_values):
            if not all(value is not None for value in roi_values):
                return {"ok": False, "error": "ROI capture requires x, y, width, and height together."}, None
            assert x is not None and y is not None and width is not None and height is not None
            if width <= 0 or height <= 0 or width * height > 8_500_000:
                return {"ok": False, "error": "ROI dimensions are invalid or exceed 8.5 megapixels."}, None
            result = executor.zoom_region(x, y, width, height, quality=quality)
        else:
            result = executor.screenshot_base64(quality=quality)
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "screen capture failed")}, None
        encoded = str(result.pop("base64", ""))
        if not encoded:
            return {**result, "ok": False, "error": "screen capture returned no image bytes"}, None
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid screenshot payload: {exc}"}, None
        return {
            **result,
            "ok": True,
            "purpose": "Strong visual fallback; prefer observe_desktop for the normal low-latency loop.",
        }, raw

    def validate_graph(self, graph: JsonDict, *, reject_api_agent: bool = True) -> JsonDict:
        try:
            parsed = ArpGraph.model_validate(graph)
        except Exception as exc:
            return {"valid": False, "errors": [str(exc)], "warnings": []}
        validation = validate_graph(parsed).model_dump(mode="json")
        if reject_api_agent:
            api_nodes = [node.id for node in parsed.nodes if node.kind == ArpNodeKind.AGENT_GOAL]
            if api_nodes:
                validation["valid"] = False
                validation["errors"].append(
                    "ChatGPT-hosted runs cannot contain agent_goal nodes because ChatGPT is already the planner: "
                    + ", ".join(api_nodes)
                )
        return validation

    def start_graph(self, graph: JsonDict, inputs: JsonDict | None = None) -> JsonDict:
        guard = self._guard(mutating=True)
        if guard:
            return guard
        validation = self.validate_graph(graph, reject_api_agent=True)
        if not validation.get("valid"):
            return {"ok": False, "validation": validation, "error": "; ".join(validation.get("errors") or [])}
        parsed = ArpGraph.model_validate(graph)
        return self.hooks.start_graph(parsed, dict(inputs or {}))

    def list_skills(self) -> JsonDict:
        guard = self._guard(mutating=False)
        if guard:
            return guard
        return {"ok": True, "skills": trace_mod.list_scripts()}

    def replay_skill(self, script: str) -> JsonDict:
        script = script.strip()
        if not script:
            return {"ok": False, "error": "script is required"}
        stamp = uuid.uuid4().hex[:8]
        graph = {
            "format": "arp/1",
            "id": f"chatgpt-skill-{stamp}",
            "name": f"Replay {script}",
            "nodes": [
                {"id": "start", "kind": "start"},
                {"id": "skill", "kind": "skill_replay", "config": {"script": script}},
                {"id": "end", "kind": "end"},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "skill"},
                {"id": "e2", "source": "skill", "target": "end"},
            ],
        }
        return self.start_graph(graph)


def build_chatgpt_mcp(hooks: ChatGPTBridgeHooks):
    """Create the official MCP SDK server and its Streamable HTTP ASGI app."""
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations

    bridge = ChatGPTDesktopBridge(hooks)
    mcp = FastMCP(
        name="WinPilot GUI Agent + ARP",
        instructions=(
            "You are the high-level planner. Prefer get_gui_agent_state and observe_desktop, then send a "
            "short concrete routine to execute_gui_subgoal or a small deterministic graph to run_arp_graph. "
            "Use capture_visual_fallback only for complex or unknown visual states. Do not repeat successful "
            "actions when controller evidence says verified. This server never requires an OpenAI API key."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )

    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    focus_hint = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mutate = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @mcp.tool(
        name="get_gui_agent_state",
        title="Get GUI agent state",
        description="Read WinPilot runtime, current run, world/perception status, and controller capabilities.",
        annotations=read_only,
    )
    def get_gui_agent_state() -> JsonDict:
        return hooks.status()

    @mcp.tool(
        name="list_desktop_windows",
        title="List desktop windows",
        description="List visible top-level Windows windows before choosing a target.",
        annotations=read_only,
    )
    def list_desktop_windows() -> JsonDict:
        return bridge.dispatch("list_windows", {}, mutating=False)

    @mcp.tool(
        name="focus_desktop_window",
        title="Focus desktop window",
        description="Focus a window by hwnd or title and retain it as the controller target.",
        annotations=focus_hint,
    )
    def focus_desktop_window(hwnd: int | None = None, title: str | None = None) -> JsonDict:
        return bridge.dispatch(
            "focus_window",
            {key: value for key, value in {"hwnd": hwnd, "title": title}.items() if value is not None},
            task=f"Focus {title or hwnd or 'window'}",
            mutating=True,
        )

    @mcp.tool(
        name="launch_app",
        title="Launch app or URL",
        description="Launch a local application, installed program, or URL before focusing and observing it.",
        annotations=mutate,
    )
    def launch_app(command: str) -> JsonDict:
        return bridge.dispatch("launch", {"command": command}, task=f"Launch {command}", mutating=True)

    @mcp.tool(
        name="observe_desktop",
        title="Observe desktop UI",
        description=(
            "Run the low-cost UIA/OCR/CV perception ladder on the target window. Prefer changed_only after an "
            "action and query for long element lists; this is the normal perception loop."
        ),
        annotations=read_only,
    )
    def observe_desktop(
        hwnd: int | None = None,
        query: str | None = None,
        max_elements: int = 120,
        full: bool = False,
        changed_only: bool = False,
        include_icons: bool = False,
    ) -> JsonDict:
        args = {
            "max_elements": max(1, min(int(max_elements), 240)),
            "full": bool(full),
            "changed_only": bool(changed_only),
            "include_icons": bool(include_icons),
        }
        if hwnd is not None:
            args["hwnd"] = hwnd
        if query:
            args["query"] = query
        return bridge.dispatch("observe", args, mutating=False)

    @mcp.tool(
        name="capture_visual_fallback",
        title="Capture visual fallback",
        description=(
            "Return a JPEG to ChatGPT vision for a complex or unknown state. Leave ROI fields empty for the "
            "primary screen, or provide x/y/width/height together. Do not use this in the routine inner loop."
        ),
        annotations=read_only,
        structured_output=False,
    )
    def capture_visual_fallback(
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        quality: int = 70,
    ) -> list[Any]:
        metadata, raw = bridge.visual_capture(
            x=x, y=y, width=width, height=height, quality=quality,
        )
        return [metadata] if raw is None else [metadata, Image(data=raw, format="jpeg")]

    @mcp.tool(
        name="execute_gui_subgoal",
        title="Execute GUI subgoal",
        description=(
            "Delegate one short concrete routine to the low-latency controller. Supply input, click/clicks, "
            "draw/draws, or verify_texts; the controller locates visual anchors, batches actions, watches "
            "transient changes, and verifies locally without an LLM/API round trip."
        ),
        annotations=mutate,
    )
    def execute_gui_subgoal(
        subgoal: str,
        target_window: JsonDict | None = None,
        input: JsonDict | None = None,
        click: JsonDict | None = None,
        clicks: list[JsonDict] | None = None,
        draw: JsonDict | None = None,
        draws: list[JsonDict] | None = None,
        verify_texts: list[str] | None = None,
        constraints: JsonDict | None = None,
    ) -> JsonDict:
        args: JsonDict = {"subgoal": subgoal}
        optional = {
            "target_window": target_window,
            "input": input,
            "click": click,
            "clicks": clicks,
            "draw": draw,
            "draws": draws,
            "verify_texts": verify_texts,
            "constraints": constraints,
        }
        args.update({key: value for key, value in optional.items() if value is not None})
        return bridge.dispatch("execute_subgoal", args, task=subgoal, mutating=True)

    @mcp.tool(
        name="execute_action_batch",
        title="Execute action batch",
        description=(
            "Execute a short grounded batch inside one controller round trip. Actions may include observe, "
            "click_element, type_into_element, hotkey, wait_change, wait_stable, read_text, and visual checks."
        ),
        annotations=mutate,
    )
    def execute_action_batch(
        actions: list[JsonDict],
        subgoal: str = "ChatGPT controller batch",
        stop_on_error: bool = True,
        sync: bool | JsonDict = True,
    ) -> JsonDict:
        return bridge.dispatch(
            "run_action_batch",
            {"actions": actions, "subgoal": subgoal, "stop_on_error": stop_on_error, "sync": sync},
            task=subgoal,
            mutating=True,
        )

    @mcp.tool(
        name="validate_arp_graph",
        title="Validate ARP graph",
        description="Validate an arp/1 graph intended for ChatGPT-hosted planning without executing it.",
        annotations=read_only,
    )
    def validate_arp_graph(graph: JsonDict) -> JsonDict:
        return bridge.validate_graph(graph, reject_api_agent=True)

    @mcp.tool(
        name="run_arp_graph",
        title="Run ARP graph",
        description=(
            "Start a deterministic arp/1 workflow. Use action_batch, skill_replay, condition, delay, gates, "
            "and variables. agent_goal is rejected because this ChatGPT conversation is the planner."
        ),
        annotations=mutate,
    )
    def run_arp_graph(graph: JsonDict, inputs: JsonDict | None = None) -> JsonDict:
        return bridge.start_graph(graph, inputs)

    @mcp.tool(
        name="get_gui_run",
        title="Get GUI run",
        description="Read one Agent or ARP run projection, evidence, timeline, and latency metrics.",
        annotations=read_only,
    )
    def get_gui_run(run_id: str) -> JsonDict:
        return hooks.get_run(run_id)

    @mcp.tool(
        name="cancel_gui_run",
        title="Cancel GUI run",
        description="Cancel an active ARP run by id.",
        annotations=mutate,
    )
    def cancel_gui_run(run_id: str) -> JsonDict:
        return hooks.cancel_run(run_id)

    @mcp.tool(
        name="decide_human_gate",
        title="Decide human gate",
        description="Approve or reject the exact human-gate node where an ARP run is paused.",
        annotations=mutate,
    )
    def decide_human_gate(run_id: str, node_id: str, approved: bool, comment: str = "") -> JsonDict:
        return hooks.decide_gate(run_id, node_id, approved, comment)

    @mcp.tool(
        name="list_gui_skills",
        title="List GUI skills",
        description="List locally recorded reusable GUI skills and their step counts.",
        annotations=read_only,
    )
    def list_gui_skills() -> JsonDict:
        return bridge.list_skills()

    @mcp.tool(
        name="replay_gui_skill",
        title="Replay GUI skill",
        description="Replay one verified local skill through the ARP runtime without an LLM round trip.",
        annotations=mutate,
    )
    def replay_gui_skill(script: str) -> JsonDict:
        return bridge.replay_skill(script)

    @mcp.tool(
        name="reset_gui_context",
        title="Reset GUI context",
        description="Clear cached target window, elements, and world state before starting an unrelated desktop task.",
        annotations=focus_hint,
    )
    def reset_gui_context() -> JsonDict:
        return bridge.reset()

    return mcp, mcp.streamable_http_app(), bridge
