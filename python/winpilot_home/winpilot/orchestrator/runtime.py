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


@dataclass
class NodeResult:
    ok: bool = True
    branch: str = "next"
    output: Any = None
    evidence: list[Any] = field(default_factory=list)
    error: str = ""


NodeHandler = Callable[[ArpNode, dict[str, Any], threading.Event], NodeResult]


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
        self._lock = threading.RLock()
        self._gate_condition = threading.Condition(self._lock)
        self._gate_decisions: dict[str, dict[str, Any]] = {}

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "kind": "arp",
                "graph_id": self.graph.id,
                "graph_name": self.graph.name,
                "status": self.status.value,
                "current_node_id": self.current_node_id,
                "variables": dict(self.variables),
                "outputs": {key: _json_preview(value) for key, value in self.outputs.items()},
                "evidence": [_json_preview(item) for item in self.evidence[-30:]],
                "history": list(self.history[-200:]),
                "result": _json_preview(self.result),
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }

    def cancel(self) -> None:
        self.stop_event.set()
        with self._gate_condition:
            self._gate_condition.notify_all()

    def decide_gate(self, node_id: str, approved: bool, comment: str = "") -> bool:
        with self._gate_condition:
            if self.current_node_id != node_id or self.status != ArpRunStatus.PAUSED:
                return False
            self._gate_decisions[node_id] = {
                "approved": bool(approved),
                "comment": str(comment or ""),
                "decided_at": time.time(),
            }
            self._gate_condition.notify_all()
            return True

    def _context(self, last: NodeResult | None = None) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graph": self.graph.model_dump(mode="json"),
            "inputs": self.inputs,
            "variables": self.variables,
            "outputs": self.outputs,
            "evidence": self.evidence,
            "runtime_state": self.runtime_state,
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

    def _human_gate(self, node: ArpNode) -> NodeResult:
        if node.config.get("approved") is True:
            return NodeResult(
                ok=True,
                branch="approved",
                output={"approved": True, "mode": "preapproved"},
                evidence=[{
                    "type": "human_gate",
                    "node_id": node.id,
                    "approved": True,
                    "mode": "preapproved",
                }],
            )
        timeout_s = float(node.timeout_s or node.config.get("timeout_s") or 3600)
        with self._gate_condition:
            self.status = ArpRunStatus.PAUSED
            BUS.publish(
                "arp_human_gate",
                run_id=self.run_id,
                node_id=node.id,
                message=str(node.config.get("message") or node.name or "Approval required"),
                status="waiting",
            )
            deadline = time.monotonic() + timeout_s
            while node.id not in self._gate_decisions and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.status = ArpRunStatus.RUNNING
                    return NodeResult(ok=False, branch="timeout", error="human approval timed out")
                self._gate_condition.wait(timeout=min(remaining, 0.5))
            self.status = ArpRunStatus.RUNNING
            if self.stop_event.is_set():
                return NodeResult(ok=False, branch="canceled", error="run canceled")
            decision = self._gate_decisions.pop(node.id)
        approved = bool(decision.get("approved"))
        BUS.publish(
            "arp_human_gate",
            run_id=self.run_id,
            node_id=node.id,
            status="approved" if approved else "rejected",
            comment=decision.get("comment", ""),
        )
        return NodeResult(
            ok=approved,
            branch="approved" if approved else "rejected",
            output=decision,
            evidence=[{
                "type": "human_gate",
                "node_id": node.id,
                "approved": True,
                "comment": decision.get("comment", ""),
            }] if approved else [],
            error="" if approved else "human rejected the gate",
        )

    def _evidence_gate(self, node: ArpNode) -> NodeResult:
        required = [str(item).casefold() for item in node.config.get("required", []) if str(item).strip()]
        haystack = json.dumps(self.evidence, ensure_ascii=False, default=str).casefold()
        missing = [item for item in required if item not in haystack]
        passed = not missing
        return NodeResult(
            ok=passed,
            branch="pass" if passed else "fail",
            output={"required": required, "missing": missing},
            evidence=[{
                "type": "evidence_gate",
                "node_id": node.id,
                "required": required,
                "passed": True,
            }] if passed else [],
            error="" if not missing else "missing evidence: " + ", ".join(missing),
        )

    def _execute_builtin(self, node: ArpNode, last: NodeResult | None) -> NodeResult | None:
        if node.kind == ArpNodeKind.START:
            return NodeResult(ok=True)
        if node.kind == ArpNodeKind.END:
            if self.graph.policy.require_evidence_on_done and not self.evidence:
                return NodeResult(ok=False, error="run reached end without required evidence")
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
        if node.kind == ArpNodeKind.HUMAN_GATE:
            return self._human_gate(node)
        return None

    def _execute_node(self, node: ArpNode, last: NodeResult | None) -> NodeResult:
        builtin = self._execute_builtin(node, last)
        if builtin is not None:
            return builtin
        handler = self.handlers.get(node.kind)
        if handler is None:
            return NodeResult(ok=False, error=f"no handler registered for {node.kind.value}")
        return handler(node, self._context(last), self.stop_event)

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

    def run(self) -> dict[str, Any]:
        validation = validate_graph(self.graph)
        if not validation.valid:
            self.status = ArpRunStatus.FAILED
            self.error = "; ".join(validation.errors)
            self.finished_at = time.time()
            BUS.publish("arp_run_done", run_id=self.run_id, status=self.status.value, error=self.error)
            return self.to_dict()

        node_map = {node.id: node for node in self.graph.nodes}
        visits: dict[str, int] = {}
        current = validation.entry_node
        last: NodeResult | None = None
        self.status = ArpRunStatus.RUNNING
        self.started_at = time.time()
        BUS.publish(
            "arp_run_start",
            run_id=self.run_id,
            graph_id=self.graph.id,
            graph_name=self.graph.name,
            node_count=len(self.graph.nodes),
        )

        try:
            while current:
                if self.stop_event.is_set():
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
                    self.evidence.extend(result.evidence)
                    for evidence in result.evidence:
                        BUS.publish(
                            "arp_evidence",
                            run_id=self.run_id,
                            node_id=node.id,
                            node_kind=node.kind.value,
                            evidence=evidence,
                            passed=True,
                        )
                record = {
                    "node_id": node.id,
                    "kind": node.kind.value,
                    "name": node.name,
                    "ok": result.ok,
                    "branch": result.branch,
                    "attempts": attempts,
                    "error": result.error,
                    "output": _json_preview(result.output),
                    "finished_at": time.time(),
                }
                self.history.append(record)
                BUS.publish("arp_node_done", run_id=self.run_id, **record)

                if node.kind == ArpNodeKind.END:
                    if not result.ok:
                        raise RuntimeError(result.error or "end evidence policy failed")
                    self.status = ArpRunStatus.DONE
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
            self.current_node_id = None
            self.finished_at = time.time()
            BUS.publish(
                "arp_run_done",
                run_id=self.run_id,
                status=self.status.value,
                result=_json_preview(self.result),
                error=self.error,
                evidence_count=len(self.evidence),
            )
        return self.to_dict()


class ArpRunManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, ArpExecution] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._current_id: str | None = None

    def start(
        self,
        graph: ArpGraph,
        handlers: dict[ArpNodeKind, NodeHandler] | None = None,
        *,
        inputs: dict[str, Any] | None = None,
        on_start: Callable[[ArpExecution], None] | None = None,
        on_finish: Callable[[ArpExecution], None] | None = None,
    ) -> ArpExecution:
        execution = ArpExecution(graph, handlers, inputs=inputs)
        validation = validate_graph(graph)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        with self._lock:
            if self.busy():
                raise RuntimeError("an ARP run is already active")
            self._runs[execution.run_id] = execution
            self._current_id = execution.run_id
            if on_start:
                on_start(execution)

            def worker() -> None:
                try:
                    execution.run()
                finally:
                    if on_finish:
                        try:
                            on_finish(execution)
                        except Exception:
                            logger.exception("ARP on_finish callback failed")

            thread = threading.Thread(target=worker, daemon=True, name=f"arp-{execution.run_id[-6:]}")
            self._threads[execution.run_id] = thread
            thread.start()
        return execution

    def busy(self) -> bool:
        with self._lock:
            return any(thread.is_alive() for thread in self._threads.values())

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

    def decide_gate(self, run_id: str, node_id: str, approved: bool, comment: str = "") -> bool:
        execution = self.get(run_id)
        return execution.decide_gate(node_id, approved, comment) if execution else False


ARP_RUNS = ArpRunManager()
