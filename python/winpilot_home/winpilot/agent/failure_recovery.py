"""Deterministic recovery routing for classified desktop-agent failures."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .failure_classifier import (
    FailureClassification,
    FailureClassifier,
    FailureType,
    parse_tool_json,
)


class FailureRecoveryStatus(str, Enum):
    LOCAL_RECOVERY_SUGGESTED = "local_recovery_suggested"
    NEEDS_LLM = "needs_llm"
    POST_TOOL_HINTS = "post_tool_hints"


@dataclass
class FailureRecoveryResult:
    status: FailureRecoveryStatus
    classification: FailureClassification
    post_tool_hints: list[str] = field(default_factory=list)
    local_actions: list[dict[str, Any]] = field(default_factory=list)
    llm_required: bool = False
    retryable: bool = True
    result_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "classification": self.classification.to_dict(),
            "post_tool_hints": self.post_tool_hints,
            "local_actions": self.local_actions,
            "llm_required": bool(self.llm_required),
            "retryable": bool(self.retryable),
            "result_meta": self.result_meta,
        }


def _root_result_is_proven_success(data: dict[str, Any]) -> bool:
    """A verified root tool result should not carry stale inner failure advice."""
    if not isinstance(data, dict):
        return False
    if data.get("ok") is True and data.get("verified") is True:
        return True
    auto_done = data.get("auto_done")
    if isinstance(auto_done, dict) and auto_done.get("ok") is True and auto_done.get("auto_done") is True:
        return True
    return False


def _root_result_is_nonmutating_negative_observation(data: dict[str, Any]) -> bool:
    """A read-only verify miss is evidence, not a recovery failure."""
    if not isinstance(data, dict):
        return False
    actions = data.get("actions_executed")
    if not isinstance(actions, list) or not actions:
        return False
    mutating = {
        "click",
        "click_element",
        "type_text",
        "type_into_element",
        "hotkey",
        "drag",
        "draw_strokes",
        "run_action_batch",
    }
    if any(isinstance(item, dict) and item.get("action") in mutating for item in actions):
        return False
    packet = data.get("evidence_packet")
    return bool(
        isinstance(packet, dict)
        and packet.get("missing_texts")
        and not packet.get("matched_texts")
    )


class FailureRecoveryLayer:
    """Turns classifications into local recovery attempts and LLM hints."""

    def __init__(
        self,
        classifier: FailureClassifier | None = None,
        *,
        consecutive_llm_threshold: int = 3,
    ) -> None:
        self.classifier = classifier or FailureClassifier()
        self.consecutive_llm_threshold = max(1, int(consecutive_llm_threshold))
        self._last_signature = ""
        self._consecutive_count = 0

    def handle_tool_result(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | None,
        result_text: str,
        task: str = "",
        session: Any = None,
        recent_results: list[tuple[str, str]] | None = None,
        visible_evidence_ledger: list[dict[str, Any]] | None = None,
        tool_history: list[tuple[str, dict[str, Any], str]] | None = None,
    ) -> FailureRecoveryResult | None:
        data = parse_tool_json(result_text)
        if _root_result_is_proven_success(data):
            self._last_signature = ""
            self._consecutive_count = 0
            return None
        if _root_result_is_nonmutating_negative_observation(data):
            self._last_signature = ""
            self._consecutive_count = 0
            return None
        classification = self.classifier.classify_tool_result(
            tool_name,
            args or {},
            result_text,
            task=task,
            recent_results=recent_results,
            visible_evidence_ledger=visible_evidence_ledger,
            tool_history=tool_history,
        )
        if classification is None:
            self._last_signature = ""
            self._consecutive_count = 0
            return None

        classification = self._apply_consecutive_policy(classification)
        local_actions = self._attempt_local_recovery(classification, session)
        hints = self._post_tool_hints(classification, tool_name, args or {}, local_actions)
        status = self._status_for(classification, local_actions, hints)
        return FailureRecoveryResult(
            status=status,
            classification=classification,
            post_tool_hints=hints,
            local_actions=local_actions,
            llm_required=classification.llm_required,
            retryable=classification.retryable,
            result_meta={
                "failure_type": classification.failure_type.value,
                "strategy": classification.strategy,
                "consecutive_count": self._consecutive_count,
                "local_action_count": len(local_actions),
            },
        )

    def _apply_consecutive_policy(
        self,
        classification: FailureClassification,
    ) -> FailureClassification:
        signature = f"{classification.failure_type.value}:{classification.normalized_reason}"
        if signature == self._last_signature:
            self._consecutive_count += 1
        else:
            self._last_signature = signature
            self._consecutive_count = 1

        if (
            classification.failure_type in {
                FailureType.WINDOW_FOCUS_ERROR,
                FailureType.WINDOW_NOT_FOUND,
            }
            and self._consecutive_count >= self.consecutive_llm_threshold
        ):
            return replace(
                classification,
                llm_required=True,
                retryable=False,
                retry_budget=0,
                normalized_reason=(
                    f"{classification.normalized_reason}; repeated "
                    f"{self._consecutive_count} consecutive times"
                ),
            )
        return classification

    def _status_for(
        self,
        classification: FailureClassification,
        local_actions: list[dict[str, Any]],
        hints: list[str],
    ) -> FailureRecoveryStatus:
        if classification.llm_required:
            return FailureRecoveryStatus.NEEDS_LLM
        if local_actions or classification.retryable:
            return FailureRecoveryStatus.LOCAL_RECOVERY_SUGGESTED
        if hints:
            return FailureRecoveryStatus.POST_TOOL_HINTS
        return FailureRecoveryStatus.NEEDS_LLM

    def _attempt_local_recovery(
        self,
        classification: FailureClassification,
        session: Any,
    ) -> list[dict[str, Any]]:
        if session is None or classification.llm_required:
            return []
        if classification.failure_type == FailureType.WINDOW_FOCUS_ERROR:
            return self._recover_focus(session, observe_after=True)
        if classification.failure_type == FailureType.WINDOW_NOT_FOUND:
            return self._recover_window(session)
        if classification.failure_type == FailureType.WAIT_TIMEOUT:
            return self._recover_wait(session, classification)
        return []

    def _recover_focus(self, session: Any, *, observe_after: bool) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        target = None
        try:
            context = session._action_target_context()
        except Exception as exc:
            context = {"error": str(exc)}
        if isinstance(context, dict):
            target = context.get("target_hwnd")
            actions.append({
                "action": "inspect_window_context",
                "ok": bool(target),
                "target_hwnd": target,
                "foreground_hwnd": context.get("foreground_hwnd"),
                "foreground_matches_target": context.get("foreground_matches_target"),
            })
        self._list_windows_probe(session, actions)
        if not target:
            target = self._find_target_window(session, actions)
        if not target:
            return actions
        self._focus_target(session, int(target), actions)
        if observe_after and self._last_action_ok(actions, "focus_window"):
            self._observe_target(session, int(target), actions, action_name="observe_after_focus")
        return actions

    def _recover_window(self, session: Any) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        self._list_windows_probe(session, actions)
        target = self._find_target_window(session, actions)
        if target:
            self._focus_target(session, int(target), actions)
            if self._last_action_ok(actions, "focus_window"):
                self._observe_target(session, int(target), actions, action_name="observe_after_window_recovery")
        return actions

    def _recover_wait(
        self,
        session: Any,
        classification: FailureClassification,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        try:
            controller = session._fast_controller()
            watcher = controller.watcher
        except Exception as exc:
            return [{"action": "dynamic_wait_probe", "ok": False, "error": str(exc)[:160]}]

        reason_blob = json.dumps(classification.evidence, ensure_ascii=False, default=str).casefold()
        try:
            if "wait_change" in reason_blob or "sync_change" in reason_blob:
                delta = watcher.wait_for_change(timeout_s=2.5, min_ratio=0.0005)
                actions.append({
                    "action": "wait_change",
                    "ok": bool(getattr(delta, "changed", False)),
                    "delta": delta.to_dict() if hasattr(delta, "to_dict") else {},
                })
            else:
                result = watcher.wait_for_stable(stable_ms=250, timeout_s=2.5)
                actions.append({
                    "action": "wait_stable",
                    "ok": bool(result.get("stable")),
                    "stable": bool(result.get("stable")),
                    "delta": result.get("delta", {}),
                })
        except Exception as exc:
            actions.append({"action": "dynamic_wait_probe", "ok": False, "error": str(exc)[:160]})
        return actions

    def _list_windows_probe(self, session: Any, actions: list[dict[str, Any]]) -> None:
        try:
            result, via = self._dispatch_probe(session, "list_windows", {})
            if result is None:
                actions.append({"action": "list_windows", "ok": False, "error": "list_windows unavailable"})
                return
            preview = str(getattr(result, "text", "") or "")[:600]
            actions.append({
                "action": "list_windows",
                "ok": bool(preview.strip()),
                "via": via,
                "preview": preview,
            })
        except Exception as exc:
            actions.append({"action": "list_windows", "ok": False, "error": str(exc)[:160]})

    def _find_target_window(self, session: Any, actions: list[dict[str, Any]]) -> int | None:
        try:
            finder = getattr(session, "_find_target_window", None)
            target = finder() if callable(finder) else None
            if target:
                remember = getattr(session, "_remember_target", None)
                if callable(remember):
                    remember(int(target))
            actions.append({
                "action": "find_target_window",
                "ok": bool(target),
                "target_hwnd": int(target) if target else None,
                "target_hint": str(getattr(session, "target_hint", "") or "")[:120],
            })
            return int(target) if target else None
        except Exception as exc:
            actions.append({"action": "find_target_window", "ok": False, "error": str(exc)[:160]})
            return None

    def _focus_target(self, session: Any, target: int, actions: list[dict[str, Any]]) -> None:
        try:
            result, via = self._dispatch_probe(session, "focus_window", {"hwnd": int(target)})
            if result is None:
                actions.append({"action": "focus_window", "ok": False, "error": "focus_window unavailable"})
                return
            payload = parse_tool_json(getattr(result, "text", "") or "")
            actions.append({
                "action": "focus_window",
                "ok": bool(payload.get("ok")) if payload else True,
                "target_hwnd": int(target),
                "via": via,
                "result": payload or {"preview": str(getattr(result, "text", ""))[:180]},
            })
        except Exception as exc:
            actions.append({"action": "focus_window", "ok": False, "target_hwnd": int(target), "error": str(exc)[:160]})

    def _observe_target(
        self,
        session: Any,
        target: int,
        actions: list[dict[str, Any]],
        *,
        action_name: str,
    ) -> None:
        try:
            result, via = self._dispatch_probe(
                session,
                "observe",
                {"hwnd": int(target), "include_icons": False, "max_elements": 80},
            )
            if result is None:
                actions.append({"action": action_name, "ok": False, "error": "observe unavailable"})
                return
            preview = str(getattr(result, "text", "") or "")[:300]
            actions.append({
                "action": action_name,
                "ok": bool(preview.strip()),
                "target_hwnd": int(target),
                "via": via,
                "preview": preview,
            })
        except Exception as exc:
            actions.append({"action": action_name, "ok": False, "target_hwnd": int(target), "error": str(exc)[:160]})

    def _dispatch_probe(self, session: Any, name: str, args: dict[str, Any]) -> tuple[Any | None, str]:
        dispatcher = getattr(session, "dispatch", None)
        if callable(dispatcher):
            return dispatcher(name, args), "dispatch"
        handler = getattr(session, f"_t_{name}", None)
        if callable(handler):
            return handler(args), "handler"
        return None, "unavailable"

    @staticmethod
    def _last_action_ok(actions: list[dict[str, Any]], action_name: str) -> bool:
        for action in reversed(actions):
            if action.get("action") == action_name:
                return bool(action.get("ok"))
        return False

    def _post_tool_hints(
        self,
        classification: FailureClassification,
        tool_name: str,
        args: dict[str, Any],
        local_actions: list[dict[str, Any]],
    ) -> list[str]:
        cands = classification.evidence.get("candidate_click_labels")
        candidate_text = ""
        if isinstance(cands, list) and cands:
            candidate_text = " Candidate labels: " + ", ".join(str(item) for item in cands[:6]) + "."
        missing_literals = classification.evidence.get("missing_literals")
        literals_text = ""
        if isinstance(missing_literals, list) and missing_literals:
            literals_text = " Required missing literals: " + ", ".join(str(item) for item in missing_literals[:6]) + "."
        missing_texts = classification.evidence.get("missing_texts")
        missing_texts_note = ""
        if isinstance(missing_texts, list) and missing_texts:
            missing_texts_note = " Missing verification texts: " + ", ".join(str(item) for item in missing_texts[:6]) + "."
        local_text = ""
        if local_actions:
            succeeded = [item.get("action") for item in local_actions if item.get("ok")]
            failed = [item.get("action") for item in local_actions if not item.get("ok")]
            local_text = (
                f" Local recovery probes succeeded={succeeded[:4]} failed={failed[:4]}."
            )
        if classification.llm_required:
            prefix = "[failure recovery: llm required]"
            tail = "Local retry budget is exhausted; use the structured evidence before replanning."
        elif classification.failure_type == FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED:
            prefix = "[failure recovery: reverify first]"
            tail = "Re-run a targeted verification/observe or dynamic wait before choosing a new business action."
        else:
            prefix = "[failure recovery: local strategy]"
            tail = "Retry the same local route or reverify with the listed strategy before broad observe/replan."
        hint = (
            f"{prefix} {classification.failure_type.value} after {tool_name}: "
            f"{classification.normalized_reason}. Strategy={classification.strategy}. "
            f"{classification.patch_hint}{candidate_text}{missing_texts_note}{literals_text}"
            f"{local_text} {tail}"
        )
        if args:
            hint += f" Original args keys={sorted(str(k) for k in args.keys())[:8]}."
        return [hint[:1400]]


def attach_recovery_metadata(
    result_text: str,
    recovery: FailureRecoveryResult | None,
) -> str:
    """Merge recovery metadata into JSON tool results without dropping fields."""
    if recovery is None:
        return result_text
    try:
        data = json.loads((result_text or "").strip())
    except (json.JSONDecodeError, TypeError):
        return result_text
    if not isinstance(data, dict) or not data:
        return result_text
    data.setdefault("failure_classification", recovery.classification.to_dict())
    data.setdefault("failure_recovery", {
        "status": recovery.status.value,
        "llm_required": recovery.llm_required,
        "retryable": recovery.retryable,
        "local_actions": recovery.local_actions,
        "post_tool_hint_count": len(recovery.post_tool_hints),
        "result_meta": recovery.result_meta,
    })
    return json.dumps(data, ensure_ascii=False, default=str)
