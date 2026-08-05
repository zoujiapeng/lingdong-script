"""Executable ARP graph runtime with retries, cancellation, and human gates."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..utils.log import BUS, logger
from .contracts import ArpGraph, ArpNode, ArpNodeKind, ArpRunStatus, validate_graph
from .evidence import evidence_matches, has_verified_evidence, normalize_evidence, parse_requirement


@dataclass
class NodeResult:
    ok: bool = True
    branch: str = "next"
    output: Any = None
    evidence: list[Any] = field(default_factory=list)
    error: str = ""


NodeHandler = Callable[[ArpNode, dict[str, Any], threading.Event], NodeResult]


class PauseController:
    """Cooperative pause gate shared by an ARP run and its child runner.

    Pauses take effect only at explicit safe boundaries.  A currently running
    atomic desktop action is allowed to finish before the next model/tool step
    blocks, avoiding half-finished drags or held mouse buttons.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    def pause(self) -> None:
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_if_paused(self, stop_event: threading.Event) -> bool:
        with self._condition:
            while self._paused and not stop_event.is_set():
                self._condition.wait(timeout=0.1)
        return not stop_event.is_set()


class _CombinedStopEvent:
    """Event-like view that stops for either run cancellation or node timeout."""

    def __init__(self, parent: threading.Event, local: threading.Event) -> None:
        self.parent = parent
        self.local = local

    def is_set(self) -> bool:
        return self.parent.is_set() or self.local.is_set()

    def set(self) -> None:
        self.local.set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self.parent.wait(0.05)
        return True


def _json_preview(
    value: Any,
    limit: int = 500,
    *,
    _depth: int = 0,
    _budget: list[int] | None = None,
) -> Any:
    """Return bounded JSON-safe data without flattening useful structure."""
    budget = _budget if _budget is not None else [400]
    if budget[0] <= 0:
        return "<truncated>"
    budget[0] -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if _depth >= 8:
        return "<max-depth>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if budget[0] <= 0:
                out["_truncated"] = True
                break
            out[str(key)] = _json_preview(
                item,
                limit,
                _depth=_depth + 1,
                _budget=budget,
            )
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if budget[0] <= 0:
                out.append("<truncated>")
                break
            out.append(_json_preview(
                item,
                limit,
                _depth=_depth + 1,
                _budget=budget,
            ))
        return out
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _interaction_state_preview(value: Any) -> Any:
    """Preserve bounded audit-critical interaction fields in run snapshots.

    Interaction plans can be structurally large.  A single shared traversal
    budget used to consume the whole snapshot before the causal action and
    verifier fields near the end of the state mapping were reached.  Each
    critical field receives its own bounded budget; remaining diagnostic state
    is still included through the normal compact preview.
    """
    if not isinstance(value, dict):
        return _json_preview(value, limit=1800, _budget=[220])
    critical_keys = (
        "session_id",
        "status",
        "mode",
        "frame_id",
        "turn_index",
        "model_calls",
        "replan_count",
        "budget",
        "target_binding",
        "interaction_region",
        "turn_owner",
        "baseline_artifact_id",
        "current_artifact_id",
        "last_mutating_plan",
        "last_executed_action",
        "last_local_verification",
        "planned_action",
        "executed_action",
        "local_verification",
        "external_change",
        "action_preflight",
        "completion_evidence_id",
        "failure_stage",
    )
    out: dict[str, Any] = {}
    for key in critical_keys:
        if key in value:
            out[key] = _json_preview(value[key], limit=1800, _budget=[140])
    remaining = {key: item for key, item in value.items() if key not in out}
    extras = _json_preview(remaining, limit=1800, _budget=[220])
    if isinstance(extras, dict):
        out.update(extras)
    return out


def _typed_snapshot_list(
    snapshot: Any,
    list_key: str,
    *,
    item_limit: int,
    item_budget: int,
) -> dict[str, Any]:
    """Bound nested payloads without changing typed list items into strings.

    Runtime views are an API contract, not a log line.  A single large evidence
    metadata object must therefore be truncated inside that record; it must not
    consume a shared budget and turn later evidence/artifact objects into the
    literal string ``"<truncated>"``.
    """
    source = snapshot if isinstance(snapshot, dict) else {}
    out: dict[str, Any] = {}
    for key, value in source.items():
        if key == list_key:
            continue
        out[str(key)] = _json_preview(value, item_limit, _budget=[80])
    items: list[dict[str, Any]] = []
    raw_items = source.get(list_key)
    for index, item in enumerate(raw_items if isinstance(raw_items, list) else []):
        if isinstance(item, dict):
            preview = _json_preview(item, item_limit, _budget=[max(20, item_budget)])
            if isinstance(preview, dict):
                items.append(preview)
                continue
        items.append({
            "_invalid_item": True,
            "index": index,
            "value": _json_preview(item, item_limit, _budget=[20]),
        })
    out[list_key] = items
    return out


class ArpExecution:
    def __init__(
        self,
        graph: ArpGraph,
        handlers: dict[ArpNodeKind, NodeHandler] | None = None,
        *,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.graph = graph
        self.handlers = handlers or {}
        self.inputs = dict(inputs or {})
        self.run_id = run_id or f"arp-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.status = ArpRunStatus.QUEUED
        self.current_node_id: str | None = None
        self.variables = {**graph.variables, **self.inputs}
        self.outputs: dict[str, Any] = {}
        self.evidence: list[Any] = []
        self.history: list[dict[str, Any]] = []
        # Handler-owned state lives for the duration of one graph execution.
        # It is intentionally omitted from to_dict because it may hold live
        # controller or replay session objects.
        self.runtime_state: dict[str, Any] = {}
        self.result: Any = None
        self.error = ""
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.stop_event = threading.Event()
        self.pause_controller = PauseController()
        self._manual_paused = False
        self._lock = threading.RLock()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            contract_data = self.graph.metadata.get("task_contract")
            contract_data = contract_data if isinstance(contract_data, dict) else {}
            artifact_store = self.runtime_state.get("artifact_store")
            evidence_registry = self.runtime_state.get("evidence_registry")
            artifact_snapshot = (
                artifact_store.snapshot()
                if artifact_store is not None and callable(getattr(artifact_store, "snapshot", None))
                else {"run_id": self.run_id, "count": 0, "artifacts": []}
            )
            contract_evidence = (
                evidence_registry.snapshot()
                if evidence_registry is not None and callable(getattr(evidence_registry, "snapshot", None))
                else {"run_id": self.run_id, "count": 0, "records": []}
            )
            stage_trace = []
            for record in self.history:
                if record.get("kind") != "task_stage":
                    continue
                output = record.get("output") if isinstance(record.get("output"), dict) else {}
                artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
                interaction_state = (
                    output.get("interaction_state")
                    if isinstance(output.get("interaction_state"), dict)
                    else {}
                )
                runtime_metrics = (
                    record.get("runtime_metrics")
                    if isinstance(record.get("runtime_metrics"), dict)
                    else {}
                )
                stage_trace.append({
                    "stage_id": output.get("stage_id") or record.get("node_id"),
                    "stage_type": output.get("stage_type"),
                    "status": output.get("status") or ("done" if record.get("ok") else "failed"),
                    "attempts": record.get("attempts"),
                    "tools": output.get("tools") or [],
                    "model_tools": output.get("model_tools") or [],
                    "controller_tools": output.get("controller_tools") or [],
                    "required_capabilities": output.get("required_capabilities") or [],
                    "verifier_requirements": output.get("verifier_requirements") or [],
                    "exposed_capabilities": output.get("exposed_capabilities") or [],
                    "missing_capabilities": output.get("missing_capabilities") or [],
                    "satisfiable": bool(output.get("satisfiable")),
                    "adapter": output.get("adapter") or "",
                    "model_role": output.get("model_role") or "none",
                    "selected_evidence_providers": (
                        output.get("selected_evidence_providers") or {}
                    ),
                    "artifact_id": output.get("artifact_id") or artifact.get("artifact_id") or "",
                    "model_calls": int(runtime_metrics.get("model_calls") or output.get("model_calls") or 0),
                    "model_elapsed_ms": int(runtime_metrics.get("model_elapsed_ms") or output.get("model_elapsed_ms") or 0),
                    "controller_elapsed_ms": int(runtime_metrics.get("controller_elapsed_ms") or output.get("controller_elapsed_ms") or 0),
                    "capture_elapsed_ms": int(runtime_metrics.get("capture_elapsed_ms") or output.get("capture_elapsed_ms") or 0),
                    "budget": runtime_metrics.get("budget") or output.get("budget") or interaction_state.get("budget") or {},
                    "failure_type": output.get("failure_type") or "",
                    "recovery": output.get("recovery") or {},
                    "error": record.get("error") or output.get("error") or "",
                    "interaction_state": interaction_state,
                })
            return {
                "run_id": self.run_id,
                "kind": "arp",
                "runtime_version": str(
                    self.runtime_state.get("runtime_version") or "legacy"
                ),
                "runtime_budget": _json_preview(
                    self.runtime_state.get("runtime_budget") or {},
                ),
                "no_legacy_fallback": (
                    self.runtime_state.get("no_legacy_fallback") is True
                ),
                "goal_admission": _json_preview(
                    self.runtime_state.get("goal_admission") or {},
                ),
                "thread_id": str(
                    self.runtime_state.get("thread_id") or ""
                ),
                "turn_id": str(
                    self.runtime_state.get("turn_id") or ""
                ),
                "desktop_lease": _json_preview(
                    self.runtime_state.get("desktop_lease") or {},
                ),
                "runtime_lifecycle": _json_preview(
                    self.runtime_state.get("runtime_lifecycle") or {},
                ),
                "trace_status": str(
                    self.runtime_state.get("trace_status") or ""
                ),
                "trace_error": str(
                    self.runtime_state.get("trace_error") or ""
                ),
                "graph_id": self.graph.id,
                "graph_name": self.graph.name,
                "status": self.status.value,
                "manual_paused": self._manual_paused,
                "current_node_id": self.current_node_id,
                "variables": dict(self.variables),
                "execution_context": _json_preview(
                    self.inputs.get("execution_context") or {},
                ),
                "outputs": {key: _json_preview(value) for key, value in self.outputs.items()},
                "evidence": [_json_preview(item) for item in self.evidence[-30:]],
                "history": list(self.history[-200:]),
                "result": _json_preview(self.result),
                "user_result": (
                    str(self.runtime_state.get("user_result") or "")[:16_384]
                    or None
                ),
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "experience_policy": str(
                    self.runtime_state.get("experience_policy")
                    or contract_data.get("experience_policy")
                    or self.graph.metadata.get("experience_policy")
                    or "allow"
                ),
                "skill_replay_node_count": sum(
                    1 for node in self.graph.nodes
                    if node.kind == ArpNodeKind.SKILL_REPLAY
                ),
                "task_contract": _json_preview(self.graph.metadata.get("task_contract")),
                "satisfiability": _json_preview(self.graph.metadata.get("satisfiability")),
                "current_stage": _json_preview(self.runtime_state.get("current_stage")),
                "interaction_state": _interaction_state_preview(
                    self.runtime_state.get("interaction_state") or {},
                ),
                "artifacts": _typed_snapshot_list(
                    artifact_snapshot,
                    "artifacts",
                    item_limit=1200,
                    item_budget=140,
                ),
                "contract_evidence": _typed_snapshot_list(
                    contract_evidence,
                    "records",
                    item_limit=1200,
                    item_budget=180,
                ),
                "completion_contract": _json_preview(
                    self.runtime_state.get("completion_contract"),
                    limit=1800,
                    _budget=[240],
                ),
                "stage_trace": [
                    _json_preview(item, limit=1800, _budget=[160])
                    for item in stage_trace
                ],
            }

    def audit_snapshot(self) -> dict[str, Any]:
        """Return the bounded, restart-safe subset consumed by run history."""
        snapshot = self.to_dict()
        keys = (
            "run_id",
            "kind",
            "runtime_version",
            "runtime_budget",
            "no_legacy_fallback",
            "goal_admission",
            "thread_id",
            "turn_id",
            "desktop_lease",
            "runtime_lifecycle",
            "trace_status",
            "trace_error",
            "graph_id",
            "graph_name",
            "status",
            "current_node_id",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "experience_policy",
            "execution_context",
            "skill_replay_node_count",
            "task_contract",
            "satisfiability",
            "current_stage",
            "interaction_state",
            "artifacts",
            "contract_evidence",
            "completion_contract",
            "stage_trace",
        )
        return {key: snapshot.get(key) for key in keys}

    def cancel(self) -> None:
        self.stop_event.set()
        self._manual_paused = False
        self.pause_controller.resume()
        with self._lock:
            if self.status == ArpRunStatus.QUEUED:
                self.status = ArpRunStatus.CANCELED
                self.finished_at = time.time()

    def pause(self) -> bool:
        with self._lock:
            if self.status not in {ArpRunStatus.QUEUED, ArpRunStatus.RUNNING}:
                return False
            self._manual_paused = True
            self.pause_controller.pause()
            self.status = ArpRunStatus.PAUSED
        BUS.publish("arp_run_paused", run_id=self.run_id, status="paused")
        return True

    def resume(self) -> bool:
        with self._lock:
            if not self._manual_paused or self.status != ArpRunStatus.PAUSED:
                return False
            self._manual_paused = False
            self.status = (
                ArpRunStatus.RUNNING if self.started_at is not None else ArpRunStatus.QUEUED
            )
            status = self.status.value
            self.pause_controller.resume()
        BUS.publish("arp_run_resumed", run_id=self.run_id, status=status)
        return True

    def _wait_if_paused(self) -> bool:
        return self.pause_controller.wait_if_paused(self.stop_event)

    def _context(self, last: NodeResult | None = None) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graph": self.graph.model_dump(mode="json"),
            "inputs": self.inputs,
            "variables": self.variables,
            "outputs": self.outputs,
            "evidence": self.evidence,
            "runtime_state": self.runtime_state,
            "pause_controller": self.pause_controller,
            "last": {
                "ok": last.ok,
                "branch": last.branch,
                "output": last.output,
                "error": last.error,
            } if last else None,
        }

    def _lookup(self, path: str, last: NodeResult | None) -> Any:
        current: Any = self._context(last)
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _condition(self, node: ArpNode, last: NodeResult | None) -> NodeResult:
        source = str(node.config.get("source") or "last.ok")
        operator = str(node.config.get("operator") or "truthy").casefold()
        expected = node.config.get("value")
        actual = self._lookup(source, last)
        if operator == "always_true":
            matched = True
        elif operator == "always_false":
            matched = False
        elif operator in {"eq", "equals", "=="}:
            matched = actual == expected
        elif operator in {"ne", "not_equals", "!="}:
            matched = actual != expected
        elif operator == "contains":
            matched = str(expected) in str(actual)
        elif operator in {"gt", ">"}:
            matched = float(actual) > float(expected)
        elif operator in {"gte", ">="}:
            matched = float(actual) >= float(expected)
        elif operator in {"lt", "<"}:
            matched = float(actual) < float(expected)
        elif operator in {"lte", "<="}:
            matched = float(actual) <= float(expected)
        elif operator in {"falsy", "false"}:
            matched = not bool(actual)
        else:
            matched = bool(actual)
        return NodeResult(ok=True, branch="yes" if matched else "no", output={"actual": actual, "matched": matched})

    def _evidence_gate(self, node: ArpNode) -> NodeResult:
        required = [
            parse_requirement(item)
            for item in node.config.get("required", [])
            if isinstance(item, dict) or str(item).strip()
        ]
        records = [normalize_evidence(item, run_id=self.run_id) for item in self.evidence]
        missing = [
            requirement.model_dump(mode="json")
            for requirement in required
            if not any(evidence_matches(record, requirement) for record in records)
        ]
        passed = not missing
        return NodeResult(
            ok=passed,
            branch="pass" if passed else "fail",
            output={
                "required": [item.model_dump(mode="json") for item in required],
                "missing": missing,
            },
            evidence=[{
                "predicate": "evidence.gate",
                "source": "arp_runtime",
                "node_id": node.id,
                "value": [item.predicate for item in required],
                "verified": True,
            }] if passed else [],
            error="" if not missing else "missing typed evidence: " + ", ".join(
                str(item.get("predicate") or "") for item in missing
            ),
        )

    def _task_contract_completion(self, node: ArpNode) -> NodeResult | None:
        """Evaluate a typed task contract at the only terminal boundary.

        Legacy and user-authored ARP graphs without ``task_contract`` metadata
        retain the generic evidence policy.  Once a graph declares a task
        contract, however, reaching END is not completion until every exact
        predicate/stage/provider obligation has been verified.
        """
        if "task_contract" not in self.graph.metadata:
            return None

        from pydantic import ValidationError

        from ..tasking.artifacts import ArtifactStore
        from ..tasking.completion import CompletionContractVerifier
        from ..tasking.contracts import TaskContract
        from ..tasking.evidence import EvidenceRegistry

        raw_contract = self.graph.metadata.get("task_contract")
        if not isinstance(raw_contract, dict) or not raw_contract:
            verdict = {
                "ok": False,
                "run_id": self.run_id,
                "contract_id": "",
                "obligations": [],
                "final_conditions": [],
                "missing": [],
                "contract_errors": ["task_contract metadata is missing or invalid"],
                "artifact_refs": {},
                "primary_artifact_id": "",
                "reason": "invalid task completion contract: task_contract metadata is missing or invalid",
            }
            self.runtime_state["completion_contract"] = verdict
            return NodeResult(ok=False, branch="fail", output=verdict, error=verdict["reason"])
        try:
            contract = TaskContract.model_validate(raw_contract)
        except (ValidationError, TypeError, ValueError) as exc:
            verdict = {
                "ok": False,
                "run_id": self.run_id,
                "contract_id": str(raw_contract.get("task_id") or ""),
                "obligations": [],
                "final_conditions": [],
                "missing": [],
                "contract_errors": [f"task_contract validation failed: {exc}"],
                "artifact_refs": {},
                "primary_artifact_id": "",
                "reason": "invalid task completion contract: task_contract validation failed",
            }
            self.runtime_state["completion_contract"] = verdict
            return NodeResult(ok=False, branch="fail", output=verdict, error=verdict["reason"])

        runtime_errors: list[str] = []
        registry = self.runtime_state.get("evidence_registry")
        if not isinstance(registry, EvidenceRegistry):
            registry = EvidenceRegistry(self.run_id)
            self.runtime_state["evidence_registry"] = registry
            if contract.obligations:
                runtime_errors.append("run-scoped evidence registry was not initialized")
        elif registry.run_id != self.run_id:
            runtime_errors.append("evidence registry belongs to a different run")
            registry = EvidenceRegistry(self.run_id)
            self.runtime_state["evidence_registry"] = registry

        store = self.runtime_state.get("artifact_store")
        if not isinstance(store, ArtifactStore) or store.run_id != self.run_id:
            store = None
            if any(obligation.artifact_ref for obligation in contract.obligations):
                runtime_errors.append("run-scoped artifact store is unavailable")

        verdict = CompletionContractVerifier(contract, registry, store).verify()
        if runtime_errors:
            verdict["ok"] = False
            verdict["contract_errors"] = [
                *runtime_errors,
                *list(verdict.get("contract_errors") or []),
            ]
            verdict["reason"] = "task completion runtime is incomplete: " + "; ".join(runtime_errors)
        self.runtime_state["completion_contract"] = verdict
        record = registry.record(
            "contract.complete",
            provider="completion_contract_verifier",
            verified=bool(verdict.get("ok")),
            stage_id=node.id,
            value=bool(verdict.get("ok")),
            metadata=verdict,
        )
        return NodeResult(
            ok=bool(verdict.get("ok")),
            branch="next" if verdict.get("ok") else "fail",
            output=verdict,
            evidence=[record],
            error="" if verdict.get("ok") else str(verdict.get("reason") or "task contract incomplete"),
        )

    def _execute_builtin(self, node: ArpNode, last: NodeResult | None) -> NodeResult | None:
        if node.kind == ArpNodeKind.START:
            return NodeResult(ok=True)
        if node.kind == ArpNodeKind.END:
            contract_result = self._task_contract_completion(node)
            if contract_result is not None:
                return contract_result
            if last is not None and last.ok and last.branch == "reply":
                return NodeResult(
                    ok=True,
                    branch="reply",
                    output=last.output,
                )
            if self.graph.policy.require_evidence_on_done and not has_verified_evidence(self.evidence):
                return NodeResult(ok=False, error="run reached end without verified evidence")
            return NodeResult(ok=True)
        if node.kind == ArpNodeKind.CONDITION:
            return self._condition(node, last)
        if node.kind == ArpNodeKind.DELAY:
            duration_ms = max(0, int(node.config.get("duration_ms") or 0))
            if self.stop_event.wait(duration_ms / 1000.0):
                return NodeResult(ok=False, branch="canceled", error="run canceled")
            return NodeResult(ok=True, output={"waited_ms": duration_ms})
        if node.kind == ArpNodeKind.SET_VARIABLE:
            key = str(node.config.get("name") or "").strip()
            if not key:
                return NodeResult(ok=False, error="set_variable requires config.name")
            value = node.config.get("value")
            self.variables[key] = value
            return NodeResult(ok=True, output={key: value})
        if node.kind == ArpNodeKind.EVIDENCE_GATE:
            return self._evidence_gate(node)
        return None

    def _execute_node(self, node: ArpNode, last: NodeResult | None) -> NodeResult:
        builtin = self._execute_builtin(node, last)
        if builtin is not None:
            return builtin
        handler = self.handlers.get(node.kind)
        if handler is None:
            return NodeResult(ok=False, error=f"no handler registered for {node.kind.value}")
        raw_timeout = node.timeout_s or node.config.get("timeout_s")
        if not raw_timeout:
            return handler(node, self._context(last), self.stop_event)

        timeout_s = max(0.05, float(raw_timeout))
        local_stop = threading.Event()
        combined_stop = _CombinedStopEvent(self.stop_event, local_stop)
        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["result"] = handler(node, self._context(last), combined_stop)  # type: ignore[arg-type]
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(
            target=worker,
            daemon=True,
            name=f"arp-node-{node.id[-12:]}",
        )
        thread.start()
        deadline = time.monotonic() + timeout_s
        while thread.is_alive() and not self.stop_event.is_set():
            if self.pause_controller.paused:
                paused_at = time.monotonic()
                if not self._wait_if_paused():
                    break
                deadline += time.monotonic() - paused_at
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                local_stop.set()
                BUS.publish(
                    "arp_node_timeout",
                    run_id=self.run_id,
                    node_id=node.id,
                    timeout_s=timeout_s,
                )
                return NodeResult(
                    ok=False,
                    branch="timeout",
                    error=f"node {node.id!r} timed out after {timeout_s:g}s",
                )
            thread.join(timeout=min(0.05, remaining))
        if self.stop_event.is_set():
            local_stop.set()
            return NodeResult(ok=False, branch="canceled", error="run canceled")
        if "error" in box:
            raise box["error"]
        result = box.get("result")
        return result if isinstance(result, NodeResult) else NodeResult(
            ok=False,
            error=f"node handler returned invalid result: {type(result).__name__}",
        )

    def _next_node(self, node_id: str, branch: str, *, ok: bool) -> str | None:
        outgoing = [edge for edge in self.graph.edges if edge.source == node_id]
        if not outgoing:
            return None
        exact = next((edge for edge in outgoing if edge.branch == branch), None)
        if exact:
            return exact.target
        if not ok:
            if self.graph.policy.fail_fast:
                return None
            default = next((edge for edge in outgoing if edge.branch == "default"), None)
            return default.target if default else None
        default = next((edge for edge in outgoing if edge.branch in {"next", "default", "output"}), None)
        return default.target if default else None

    def _publish_run_done(self) -> None:
        user_result = self.runtime_state.get("user_result")
        if (
            type(user_result) is not str
            or not user_result.strip()
            or len(user_result) > 16_384
        ):
            user_result = None
        BUS.publish(
            "arp_run_done",
            run_id=self.run_id,
            runtime_version=str(
                self.runtime_state.get("runtime_version") or "legacy"
            ),
            status=self.status.value,
            result=_json_preview(self.result),
            error=self.error,
            evidence_count=len(self.evidence),
            runtime_snapshot=self.audit_snapshot(),
            failure_stage=str(
                self.runtime_state.get("failure_stage") or ""
            ),
            **(
                {"user_result": user_result}
                if user_result is not None
                else {}
            ),
        )

    def run(self, *, publish_done: bool = True) -> dict[str, Any]:
        validation = validate_graph(self.graph)
        if not validation.valid:
            self.status = ArpRunStatus.FAILED
            self.error = "; ".join(validation.errors)
            self.finished_at = time.time()
            if publish_done:
                self._publish_run_done()
            return self.to_dict()

        node_map = {node.id: node for node in self.graph.nodes}
        visits: dict[str, int] = {}
        current = validation.entry_node
        last: NodeResult | None = None
        self.status = ArpRunStatus.PAUSED if self._manual_paused else ArpRunStatus.RUNNING
        self.started_at = time.time()
        BUS.publish(
            "arp_run_start",
            run_id=self.run_id,
            runtime_version=str(
                self.runtime_state.get("runtime_version") or "legacy"
            ),
            runtime_budget=_json_preview(
                self.runtime_state.get("runtime_budget") or {},
            ),
            no_legacy_fallback=(
                self.runtime_state.get("no_legacy_fallback") is True
            ),
            goal_admission=_json_preview(
                self.runtime_state.get("goal_admission") or {},
            ),
            graph_id=self.graph.id,
            graph_name=self.graph.name,
            node_count=len(self.graph.nodes),
            task_contract=self.graph.metadata.get("task_contract"),
            satisfiability=self.graph.metadata.get("satisfiability"),
            experience_policy=str(
                self.runtime_state.get("experience_policy")
                or self.graph.metadata.get("experience_policy")
                or "allow"
            ),
        )

        try:
            while current:
                if self.stop_event.is_set():
                    self.status = ArpRunStatus.CANCELED
                    self.error = "run canceled"
                    break
                if not self._wait_if_paused():
                    self.status = ArpRunStatus.CANCELED
                    self.error = "run canceled"
                    break
                node = node_map[current]
                self.current_node_id = current
                visits[current] = visits.get(current, 0) + 1
                if visits[current] > self.graph.policy.max_node_visits:
                    raise RuntimeError(f"node {current!r} exceeded max visits")
                BUS.publish(
                    "arp_node_start",
                    run_id=self.run_id,
                    node_id=node.id,
                    node_kind=node.kind.value,
                    node_name=node.name,
                    attempt=1,
                )

                if node.disabled:
                    result = NodeResult(ok=True, branch="next", output={"skipped": True})
                    attempts = 1
                else:
                    result = NodeResult(ok=False, error="not executed")
                    attempts = 0
                    delay_ms = node.retry.delay_ms
                    while attempts < node.retry.max_attempts:
                        attempts += 1
                        if attempts > 1:
                            BUS.publish(
                                "arp_node_retry",
                                run_id=self.run_id,
                                node_id=node.id,
                                attempt=attempts,
                                previous_error=result.error,
                            )
                            if self.stop_event.wait(delay_ms / 1000.0):
                                break
                            delay_ms = int(delay_ms * node.retry.backoff)
                        result = self._execute_node(node, last)
                        if result.ok or result.branch not in {"next", "fail"}:
                            break

                self.outputs[node.id] = result.output
                if result.evidence:
                    normalized_evidence = [
                        normalize_evidence(
                            evidence,
                            run_id=self.run_id,
                            node_id=node.id,
                        ).model_dump(mode="json")
                        for evidence in result.evidence
                    ]
                    self.evidence.extend(normalized_evidence)
                    for evidence in normalized_evidence:
                        BUS.publish(
                            "arp_evidence",
                            run_id=self.run_id,
                            node_id=node.id,
                            node_kind=node.kind.value,
                            evidence=evidence,
                            passed=True,
                        )
                raw_output = result.output if isinstance(result.output, dict) else {}
                failure_stage = raw_output.get("failure_stage")
                if type(failure_stage) is str and failure_stage.strip():
                    self.runtime_state["failure_stage"] = failure_stage.strip()
                raw_interaction_state = (
                    raw_output.get("interaction_state")
                    if isinstance(raw_output.get("interaction_state"), dict)
                    else {}
                )
                raw_budget = raw_output.get("budget") or raw_interaction_state.get("budget")
                runtime_metrics = {
                    "model_calls": int(raw_output.get("model_calls") or 0),
                    "model_elapsed_ms": int(raw_output.get("model_elapsed_ms") or 0),
                    "controller_elapsed_ms": int(raw_output.get("controller_elapsed_ms") or 0),
                    "capture_elapsed_ms": int(raw_output.get("capture_elapsed_ms") or 0),
                    "budget": dict(raw_budget) if isinstance(raw_budget, dict) else {},
                }
                record = {
                    "node_id": node.id,
                    "kind": node.kind.value,
                    "name": node.name,
                    "ok": result.ok,
                    "branch": result.branch,
                    "attempts": attempts,
                    "error": result.error,
                    "output": _json_preview(result.output),
                    "runtime_metrics": runtime_metrics if node.kind == ArpNodeKind.TASK_STAGE else {},
                    "finished_at": time.time(),
                }
                self.history.append(record)
                BUS.publish("arp_node_done", run_id=self.run_id, **record)

                if node.kind == ArpNodeKind.END:
                    if not result.ok:
                        raise RuntimeError(result.error or "end evidence policy failed")
                    self.status = (
                        ArpRunStatus.REPLY
                        if result.branch == "reply"
                        else ArpRunStatus.DONE
                    )
                    self.result = result.output if result.output is not None else last.output if last else None
                    break
                next_node = self._next_node(node.id, result.branch, ok=result.ok)
                if not result.ok and next_node is None:
                    raise RuntimeError(result.error or f"node {node.id} failed")
                if next_node is None:
                    raise RuntimeError(f"node {node.id!r} has no route for branch {result.branch!r}")
                current = next_node
                last = result
            else:
                if self.status == ArpRunStatus.RUNNING:
                    raise RuntimeError("graph terminated without an end node")
        except Exception as exc:
            logger.exception("ARP run failed: %s", self.run_id)
            self.status = ArpRunStatus.CANCELED if self.stop_event.is_set() else ArpRunStatus.FAILED
            self.error = str(exc)
        finally:
            action_batch_session = self.runtime_state.pop("action_batch_session", None)
            close_action_batch_session = getattr(action_batch_session, "close", None)
            if callable(close_action_batch_session):
                try:
                    close_action_batch_session()
                except Exception:
                    logger.debug("action batch session cleanup failed", exc_info=True)
            adapter = self.runtime_state.get("communication_adapter")
            close_adapter = getattr(adapter, "close", None)
            if callable(close_adapter):
                try:
                    close_adapter()
                except Exception:
                    logger.debug("communication adapter cleanup failed", exc_info=True)
            self.current_node_id = None
            self.finished_at = time.time()
            if publish_done:
                self._publish_run_done()
        return self.to_dict()


class ArpRunManager:
    def __init__(self, max_runs: int = 100) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, ArpExecution] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._current_id: str | None = None
        self._max_runs = max(20, int(max_runs))

    def _prune_locked(self) -> None:
        if len(self._runs) <= self._max_runs:
            return
        terminal = sorted(
            (
                run for run in self._runs.values()
                if run.status not in {ArpRunStatus.QUEUED, ArpRunStatus.RUNNING, ArpRunStatus.PAUSED}
            ),
            key=lambda run: run.finished_at or run.created_at,
        )
        for run in terminal[:max(0, len(self._runs) - self._max_runs)]:
            self._runs.pop(run.run_id, None)
            self._threads.pop(run.run_id, None)
            if self._current_id == run.run_id:
                self._current_id = None

    def start(
        self,
        graph: ArpGraph,
        handlers: dict[ArpNodeKind, NodeHandler] | None = None,
        *,
        inputs: dict[str, Any] | None = None,
        on_start: Callable[[ArpExecution], None] | None = None,
        on_finish: Callable[[ArpExecution], None] | None = None,
    ) -> ArpExecution:
        with self._lock:
            if self.busy():
                raise RuntimeError("an ARP run is already active")
            execution = self.prepare(graph, handlers, inputs=inputs)

            def worker() -> None:
                self.run_prepared(execution, on_start=on_start, on_finish=on_finish)

            thread = threading.Thread(target=worker, daemon=True, name=f"arp-{execution.run_id[-6:]}")
            self._threads[execution.run_id] = thread
            thread.start()
        return execution

    def prepare(
        self,
        graph: ArpGraph,
        handlers: dict[ArpNodeKind, NodeHandler] | None = None,
        *,
        inputs: dict[str, Any] | None = None,
    ) -> ArpExecution:
        """Register a queued execution without creating another runtime thread."""
        validation = validate_graph(graph)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        execution = ArpExecution(graph, handlers, inputs=inputs)
        with self._lock:
            self._runs[execution.run_id] = execution
            self._current_id = execution.run_id
        return execution

    def run_prepared(
        self,
        execution: ArpExecution,
        *,
        on_start: Callable[[ArpExecution], None] | None = None,
        on_finish: Callable[[ArpExecution], None] | None = None,
    ) -> dict[str, Any]:
        """Run a prepared execution in the caller's job/thread boundary."""
        if execution.stop_event.is_set():
            execution.cancel()
            return execution.to_dict()
        if on_start:
            on_start(execution)
        finalize_before_done = bool(
            on_finish
            and execution.runtime_state.get("runtime_version") == "vnext"
        )
        try:
            execution.run(publish_done=not finalize_before_done)
        finally:
            if on_finish:
                try:
                    on_finish(execution)
                except Exception as exc:
                    logger.exception("ARP on_finish callback failed")
                    if finalize_before_done:
                        with execution._lock:
                            detail = str(
                                execution.runtime_state.get("trace_error")
                                or exc
                            )
                            execution.status = ArpRunStatus.FAILED
                            execution.error = (
                                "vNext runtime finalization failed: "
                                f"{detail}"
                            )
                            execution.finished_at = time.time()
                            execution.runtime_state["no_legacy_fallback"] = True
                            if str(
                                execution.runtime_state.get("trace_status")
                                or ""
                            ) in {"", "recording"}:
                                execution.runtime_state[
                                    "trace_status"
                                ] = "finalization_failed"
                            execution.runtime_state["runtime_lifecycle"] = {
                                "phase": "finalization",
                                "terminal": True,
                                "status": ArpRunStatus.FAILED.value,
                                "detail": execution.error,
                                "trace_status": str(
                                    execution.runtime_state.get("trace_status")
                                    or "finalization_failed"
                                ),
                                "no_legacy_fallback": True,
                            }
            if finalize_before_done:
                execution._publish_run_done()
            with self._lock:
                self._threads.pop(execution.run_id, None)
                self._prune_locked()
        return execution.to_dict()

    def busy(self) -> bool:
        with self._lock:
            return any(
                run.status in {ArpRunStatus.QUEUED, ArpRunStatus.RUNNING, ArpRunStatus.PAUSED}
                for run in self._runs.values()
            )

    def get(self, run_id: str) -> ArpExecution | None:
        with self._lock:
            return self._runs.get(run_id)

    def current(self) -> ArpExecution | None:
        with self._lock:
            return self._runs.get(self._current_id or "")

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)
            return [run.to_dict() for run in runs[:limit]]

    def cancel(self, run_id: str) -> bool:
        execution = self.get(run_id)
        if not execution:
            return False
        execution.cancel()
        return True

    def pause(self, run_id: str) -> bool:
        execution = self.get(run_id)
        return execution.pause() if execution else False

    def resume(self, run_id: str) -> bool:
        execution = self.get(run_id)
        return execution.resume() if execution else False


ARP_RUNS = ArpRunManager()
