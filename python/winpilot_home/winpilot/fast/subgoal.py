"""Controller-first subgoal executor.

This is not a second agent. It is a deterministic routine layer under the
unified AgentRunner/ToolSession: the LLM supplies a compact subgoal and the
local GUI controller performs the low-level click/type/wait/verify steps.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..action.verify import _match_visible_text
from ..agent.task_rules import _required_literal_tokens, _required_visible_payloads, _visible_state_targets
from ..agent.tool_result import ToolResult
from ..perception import reducer
from ..perception.fusion import Snapshot
from .controller import FastGuiController


@dataclass
class SubgoalRequest:
    subgoal: str
    target_window: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    click: dict[str, Any] = field(default_factory=dict)
    verify_texts: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = re.sub(r"[\s_\-:：]+", "", value or "").casefold()
        if value and key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _scope_mentions(target: str, *parts: Any) -> bool:
    key = re.sub(r"[\s_\-:\uff1a]+", "", str(target or "")).casefold()
    if not key:
        return False
    haystack = "\n".join(str(part or "") for part in parts)
    haystack_key = re.sub(r"[\s_\-:\uff1a]+", "", haystack).casefold()
    return key in haystack_key


def parse_subgoal_request(args: dict[str, Any], task: str = "") -> SubgoalRequest:
    input_spec = _as_dict(args.get("input") or args.get("type") or args.get("text_input"))
    click_spec = _as_dict(args.get("click") or args.get("button") or args.get("press"))
    if not input_spec and args.get("text"):
        input_spec = {"text": args.get("text"), "label": args.get("input_label") or args.get("label") or ""}
    if not click_spec and args.get("button_label"):
        click_spec = {"label": args.get("button_label"), "role": "button"}
    verify_texts = _as_text_list(args.get("verify_texts") or args.get("verify") or [])
    if not verify_texts and input_spec.get("text"):
        verify_texts.append(str(input_spec.get("text") or ""))
    # Task-level evidence still belongs to the final done gate. A routine should
    # only inherit task literals that are actually part of this subgoal; otherwise
    # an input-only routine can be marked failed because a later button/state
    # requirement has not happened yet.
    subgoal_text = str(args.get("subgoal") or args.get("goal") or "").strip()
    scope_text = json.dumps(
        {
            "subgoal": subgoal_text,
            "input": input_spec,
            "click": click_spec,
            "verify_texts": verify_texts,
        },
        ensure_ascii=False,
        default=str,
    )
    for target in [*_required_visible_payloads(task), *_required_literal_tokens(task), *_visible_state_targets(task)]:
        if _scope_mentions(target, scope_text):
            verify_texts.append(target)
    return SubgoalRequest(
        subgoal=subgoal_text or str(task or "").strip(),
        target_window=_as_dict(args.get("target_window") or {}),
        input=input_spec,
        click=click_spec,
        verify_texts=_unique(verify_texts),
        constraints=_as_dict(args.get("constraints") or {}),
    )


def _pick_text(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("action") in {"read_text", "observe"}:
            chunks.append(str(item.get("preview") or ""))
        child_data = item.get("data")
        if isinstance(child_data, dict):
            for key in ("text", "body", "visible_text", "preview"):
                value = child_data.get(key)
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks)


class SubgoalExecutor:
    """Execute one high-level GUI routine inside the fast controller."""

    def __init__(self, session, controller: FastGuiController) -> None:
        self.session = session
        self.controller = controller

    def _target_hwnd(self, request: SubgoalRequest) -> int | None:
        target = request.target_window or {}
        hwnd = target.get("hwnd")
        try:
            if hwnd:
                return int(hwnd)
        except (TypeError, ValueError):
            pass
        return getattr(self.session, "hwnd", None)

    def _ensure_observed(self, request: SubgoalRequest) -> tuple[Snapshot | None, list[dict[str, Any]]]:
        prelude: list[dict[str, Any]] = []
        hwnd = self._target_hwnd(request)
        if hwnd:
            try:
                focus = self.session.dispatch("focus_window", {"hwnd": hwnd})
                prelude.append({"action": "focus_window", "ok": not bool(focus.terminal), "preview": focus.text[:240]})
            except Exception:
                pass
        if getattr(self.session, "snapshot", None) is None:
            observe = self.session.dispatch("observe", {})
            prelude.append({"action": "observe", "ok": not bool(observe.terminal), "preview": observe.text[:320]})
        snapshot = getattr(self.session, "snapshot", None)
        return snapshot, prelude

    @staticmethod
    def _anchor_fields(spec: dict[str, Any], default_role: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("label", "expected_region", "visual_anchor", "fallback_text", "fallback", "role"):
            if key in spec and spec.get(key) not in (None, "", []):
                out[key] = spec[key]
        if default_role and not out.get("role"):
            out["role"] = default_role
        return out

    def _build_actions(self, request: SubgoalRequest) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        input_spec = request.input
        if input_spec:
            text = input_spec.get("text", input_spec.get("value", input_spec.get("text_input", "")))
            if text is not None and str(text) != "":
                action = self._anchor_fields(input_spec, default_role="edit")
                action.update({
                    "action": "type_into_element",
                    "text": str(text),
                    "clear": input_spec.get("clear", input_spec.get("replace", True)),
                })
                expect_text = input_spec.get("expect_text")
                if expect_text is True or (expect_text is None and str(text) in request.verify_texts):
                    action["expect"] = {"text_appears": str(text)}
                elif isinstance(expect_text, str) and expect_text.strip():
                    action["expect"] = {"text_appears": expect_text.strip()}
                actions.append(action)
        click_spec = request.click
        if click_spec:
            label = click_spec.get("label") or click_spec.get("text") or click_spec.get("name")
            if label or click_spec.get("anchor"):
                action = self._anchor_fields(click_spec, default_role="button")
                if label and not action.get("label"):
                    action["label"] = str(label)
                action.update({
                    "action": "click_element",
                    "button": click_spec.get("button", "left"),
                    "double": bool(click_spec.get("double", False)),
                })
                actions.append(action)
        wait_spec = request.constraints.get("wait") if isinstance(request.constraints, dict) else None
        if wait_spec is False:
            pass
        else:
            actions.append({"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0})
        if request.verify_texts:
            actions.append({"action": "read_text"})
        return actions

    def execute(self, args: dict[str, Any]) -> ToolResult:
        request = parse_subgoal_request(args, task=getattr(self.session, "task", ""))
        snapshot, prelude = self._ensure_observed(request)
        recent_changes = {}
        try:
            recent_changes = self.controller.watcher.status()
        except Exception:
            recent_changes = {}
        reduction = reducer.reduce_snapshot(
            snapshot,
            task=getattr(self.session, "task", ""),
            query=request.subgoal,
            recent_changes=recent_changes,
        ) if snapshot is not None else {}
        actions = self._build_actions(request)
        if not actions:
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "subgoal": request.subgoal,
                "subgoal_route": "controller_first",
                "controller_state": {"phase": "blocked", "needs_llm_reason": True},
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": request.verify_texts,
                    "perception_reducer": reduction,
                },
                "error": "execute_subgoal needs an input.text routine, a click.label routine, or verify_texts.",
                "needs_llm_reason": True,
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        batch = self.controller.execute(
            actions,
            stop_on_error=True,
            sync=False,
            subgoal=request.subgoal,
        )
        visible_text = _pick_text(batch)
        matched = [text for text in request.verify_texts if _match_visible_text(text, visible_text)]
        missing = [text for text in request.verify_texts if text not in matched]
        verified = bool(batch.get("ok")) and not missing if request.verify_texts else bool(batch.get("verified"))
        evidence_packet = {
            "matched_texts": matched,
            "missing_texts": missing,
            "visible_text_preview": visible_text[:1200],
            "perception_reducer": reduction,
            "prelude": prelude,
        }
        payload = {
            "ok": bool(batch.get("ok")) and (not missing if request.verify_texts else True),
            "verified": bool(verified),
            "subgoal": request.subgoal,
            "subgoal_route": "controller_first",
            "controller_state": {
                **(batch.get("controller_state") or {}),
                "needs_llm_reason": bool(missing or not batch.get("ok")),
            },
            "actions_executed": actions,
            "results": batch.get("results") or [],
            "metrics": batch.get("metrics") or {},
            "evidence_packet": evidence_packet,
            "needs_llm_reason": bool(missing or not batch.get("ok")),
        }
        if missing:
            payload["error"] = "subgoal routine completed but required visible text evidence is missing"
            payload["blocked"] = True
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={"tool": "execute_subgoal", "subgoal": request.subgoal, "actions": actions},
        )
