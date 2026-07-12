"""Local failure classification for WinPilot desktop-agent tool results.

The classifier is intentionally deterministic. It reads the structured
controller evidence already emitted by ToolResult.text and maps common GUI
automation failures to local recovery strategies before involving the LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from .task_rules import (
        _required_literal_tokens,
        _required_visible_payloads,
        _visible_state_targets,
    )
except Exception:  # pragma: no cover - defensive for partial imports
    def _required_literal_tokens(_task: str) -> list[str]:
        return []

    def _required_visible_payloads(_task: str) -> list[str]:
        return []

    def _visible_state_targets(_task: str) -> list[str]:
        return []


class FailureType(str, Enum):
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    CONTROL_NOT_FOUND = "CONTROL_NOT_FOUND"
    OCR_MISREAD = "OCR_MISREAD"
    CLICK_COORD_OFFSET = "CLICK_COORD_OFFSET"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    WINDOW_FOCUS_ERROR = "WINDOW_FOCUS_ERROR"
    TASK_CONSTRAINT_LOST = "TASK_CONSTRAINT_LOST"
    ACTION_SUCCEEDED_VERIFICATION_FAILED = "ACTION_SUCCEEDED_VERIFICATION_FAILED"


@dataclass
class FailureClassification:
    failure_type: FailureType
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    llm_required: bool = False
    retryable: bool = True
    retry_budget: int = 1
    normalized_reason: str = ""
    patch_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "confidence": round(float(self.confidence), 3),
            "evidence": self.evidence,
            "strategy": self.strategy,
            "llm_required": bool(self.llm_required),
            "retryable": bool(self.retryable),
            "retry_budget": int(self.retry_budget),
            "normalized_reason": self.normalized_reason,
            "patch_hint": self.patch_hint,
        }


_STRATEGIES: dict[FailureType, tuple[str, str, int]] = {
    FailureType.WINDOW_NOT_FOUND: (
        "find_app_list_or_scan_windows_launch_focus_observe",
        "Enumerate windows and app/process/title/class aliases, run find_app/list_windows/scan_windows, launch if absent, focus_window, update session.hwnd/target_hint, then observe.",
        2,
    ),
    FailureType.WINDOW_FOCUS_ERROR: (
        "enumerate_match_focus_reobserve",
        "Match the intended window by title/process/class/alias, focus_window (or activation fallback), then observe before retrying the original GUI action.",
        2,
    ),
    FailureType.CONTROL_NOT_FOUND: (
        "candidate_labels_query_roi_anchor_cache",
        "Use candidate_click_labels, observe(query), expanded ROI, anchor fallback, input_locator, and ElementCache refresh before asking the LLM for a new plan.",
        2,
    ),
    FailureType.OCR_MISREAD: (
        "alternate_perception_normalized_text_roi_template",
        "Retry verification with UIA/Win32 first, normalized text matching, lower OCR threshold, targeted ROI, then CV/template only if text perception remains weak.",
        2,
    ),
    FailureType.CLICK_COORD_OFFSET: (
        "recompute_bbox_center_anchor_delta_verify",
        "Do not repeat the same coordinates. Re-locate the element, click bbox center or anchor-relative point, and verify screen_delta inside the target region.",
        1,
    ),
    FailureType.WAIT_TIMEOUT: (
        "dynamic_wait_polling_screen_diff_or_control_state",
        "Use wait_change/wait_stable, expect polling, screen diff, or control-state polling. Do not loop with bare sleep.",
        2,
    ),
    FailureType.TASK_CONSTRAINT_LOST: (
        "restore_required_literals_from_task_plan_evidence",
        "Recover required literals, visible payloads, and target window from the original task, plan, visible_evidence_ledger, recent_results, and tool_history.",
        1,
    ),
    FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED: (
        "reverify_perception_wait_or_adjust_overstrict_expect",
        "Treat the action as possibly successful. First check perception miss, insufficient wait, or overly strict verify condition; continue planning only when business evidence is truly absent.",
        1,
    ),
}


_TEXT_NOT_FOUND_RE = re.compile(
    r"(not\s+found|missing|could\s+not\s+find|did\s+not\s+find|"
    r"text\s+not\s+found|not\s+visible|verify.*failed|"
    r"\u672a\u627e\u5230|\u627e\u4e0d\u5230|\u672a\u8bc6\u522b)",
    re.IGNORECASE,
)
_WINDOW_NOT_FOUND_RE = re.compile(
    r"(no\s+visible\s+or\s+running\s+windows?\s+match|"
    r"no\s+target\s+window|unable\s+to\s+determine\s+target\s+window|"
    r"requires\s+a\s+live\s+hwnd|matching\s+title/query/name|"
    r"requires\s+an\s+existing\s+target\s+window|"
    r"\u65e0\u6cd5\u786e\u5b9a\u76ee\u6807\u7a97\u53e3|\u65e0\u76ee\u6807\u7a97\u53e3)",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(
    r"(timeout|timed\s*out|deadline|not\s+satisfied|wait.*failed|"
    r"\u8d85\u65f6|\u7b49\u5f85.*\u5931\u8d25|\u672a\u6ee1\u8db3)",
    re.IGNORECASE,
)


def parse_tool_json(text: str | dict[str, Any] | None) -> dict[str, Any]:
    """Return the first JSON object found in a tool result, if any."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        return {}
    raw = text.strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return parse_tool_json(value)
    return {}


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _values_for_key(value: Any, key: str) -> list[Any]:
    return [item[key] for item in _iter_dicts(value) if key in item]


def _first_dict(value: Any, key: str) -> dict[str, Any]:
    for item in _iter_dicts(value):
        child = item.get(key)
        if isinstance(child, dict):
            return child
    return {}


def _child_payloads(data: dict[str, Any]) -> list[dict[str, Any]]:
    payloads = [data]
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        child = {"action": item.get("action"), "ok": item.get("ok")}
        if "verified" in item:
            child["verified"] = item.get("verified")
        data_child = item.get("data")
        if isinstance(data_child, dict):
            child.update(data_child)
        elif isinstance(item.get("preview"), str):
            child.update(parse_tool_json(item.get("preview") or ""))
        payloads.append(child)
    return payloads


def _truthy_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().casefold()
        if low in {"true", "yes", "1"}:
            return True
        if low in {"false", "no", "0"}:
            return False
    return None


def _compact_reason(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"[\s_\-:：,，。.;；!！?？\"'`]+", "", (value or "").casefold())


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _task_literals(task: str) -> list[str]:
    values: list[str] = []
    values.extend(_required_visible_payloads(task or ""))
    values.extend(_visible_state_targets(task or ""))
    values.extend(_required_literal_tokens(task or ""))
    return _dedupe(values)


def _evidence_text(
    result_text: str,
    recent_results: list[tuple[str, str]] | None,
    visible_evidence_ledger: list[dict[str, Any]] | None,
    tool_history: list[tuple[str, dict[str, Any], str]] | None,
) -> str:
    chunks = [result_text or ""]
    for _name, text in recent_results or []:
        chunks.append(str(text or ""))
    for item in visible_evidence_ledger or []:
        if not isinstance(item, dict):
            continue
        chunks.append(str(item.get("target") or ""))
        chunks.append(str(item.get("snippet") or ""))
    for _name, _args, text in tool_history or []:
        chunks.append(str(text or "")[:1200])
    return "\n".join(chunks)


def _has_failed_signal(data: dict[str, Any], text: str) -> bool:
    if not data:
        return bool(_WINDOW_NOT_FOUND_RE.search(text) or _TEXT_NOT_FOUND_RE.search(text))
    for item in _child_payloads(data):
        if item.get("blocked") is True or item.get("error"):
            return True
        if _truthy_bool(item.get("ok")) is False or _truthy_bool(item.get("verified")) is False:
            return True
    return bool(_TIMEOUT_RE.search(text) or _TEXT_NOT_FOUND_RE.search(text))


def _candidate_labels(data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for raw in _values_for_key(data, "candidate_click_labels"):
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item or "").strip())
    return _dedupe(values)[:8]


def _missing_texts(data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for raw in _values_for_key(data, "missing_texts"):
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item or "").strip())
        elif isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return _dedupe(values)[:12]


def _strategy_for(failure_type: FailureType) -> tuple[str, str, int]:
    return _STRATEGIES[failure_type]


def _classification(
    failure_type: FailureType,
    confidence: float,
    evidence: dict[str, Any],
    reason: str,
    *,
    llm_required: bool = False,
    retryable: bool = True,
    retry_budget: int | None = None,
) -> FailureClassification:
    strategy, patch_hint, default_budget = _strategy_for(failure_type)
    if retry_budget is None:
        retry_budget = default_budget
    return FailureClassification(
        failure_type=failure_type,
        confidence=confidence,
        evidence=evidence,
        strategy=strategy,
        llm_required=llm_required,
        retryable=retryable,
        retry_budget=retry_budget,
        normalized_reason=_compact_reason(reason),
        patch_hint=patch_hint,
    )


class FailureClassifier:
    """Deterministic classifier over ToolResult text and local run evidence."""

    def classify_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        result_text: str,
        *,
        task: str = "",
        recent_results: list[tuple[str, str]] | None = None,
        visible_evidence_ledger: list[dict[str, Any]] | None = None,
        tool_history: list[tuple[str, dict[str, Any], str]] | None = None,
    ) -> FailureClassification | None:
        args = args or {}
        text = result_text or ""
        data = parse_tool_json(text)
        payloads = _child_payloads(data) if data else []

        # Semantic format routines deliberately return postcondition_pending
        # until the saved document can be inspected. Reclassifying that handoff
        # as a failed action makes recovery repeat toggle shortcuts such as
        # Ctrl+B, undoing a successful mutation.
        if (
            data.get("ok") is True
            and data.get("needs_llm_reason") is False
            and not data.get("error")
            and (
                data.get("format_applied") is True
                or data.get("mutation_skipped") is True
                or (
                    data.get("mutation_applied") is True
                    and data.get("postcondition_pending") is True
                )
            )
        ):
            return None

        focus = self._classify_window_focus(data, text)
        if focus is not None:
            return focus

        window = self._classify_window_not_found(data, text)
        if window is not None:
            return window

        wait = self._classify_wait_timeout(tool_name, data, text)
        if wait is not None:
            return wait

        control = self._classify_control_not_found(data, text)
        if control is not None:
            return control

        click_offset = self._classify_click_offset(tool_name, args, data)
        if click_offset is not None:
            return click_offset

        action_verify = self._classify_action_succeeded_verify_failed(data, text)
        if action_verify is not None:
            return action_verify

        ocr = self._classify_ocr_misread(data, text)
        if ocr is not None:
            return ocr

        task_constraint = self._classify_task_constraint_lost(
            tool_name,
            data,
            text,
            task=task,
            recent_results=recent_results,
            visible_evidence_ledger=visible_evidence_ledger,
            tool_history=tool_history,
        )
        if task_constraint is not None:
            return task_constraint

        if data and not _has_failed_signal(data, text):
            return None
        if payloads:
            errors = [item.get("error") for item in payloads if item.get("error")]
            if errors:
                return _classification(
                    FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED,
                    0.45,
                    {"errors": errors[:3]},
                    errors[0],
                    retryable=True,
                    retry_budget=1,
                )
        return None

    def _classify_window_focus(
        self,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        if not data:
            return None
        scope_guard = _first_dict(data, "scope_guard")
        screen_deltas = [
            delta for delta in _values_for_key(data, "screen_delta")
            if isinstance(delta, dict)
        ]
        action_targets = [
            target for target in _values_for_key(data, "action_target")
            if isinstance(target, dict)
        ]
        foreground_false = any(
            _truthy_bool(delta.get("foreground_matches_target")) is False
            for delta in screen_deltas
        )
        action_foreground_false = False
        for target in action_targets:
            after = target.get("after") if isinstance(target.get("after"), dict) else {}
            before = target.get("before") if isinstance(target.get("before"), dict) else {}
            if (
                _truthy_bool(after.get("foreground_matches_target")) is False
                or _truthy_bool(before.get("foreground_matches_target")) is False
            ):
                action_foreground_false = True
                break
        if not scope_guard and not foreground_false and not action_foreground_false:
            return None
        evidence = {
            "scope_guard": scope_guard,
            "screen_delta": screen_deltas[:2],
            "action_target": action_targets[:2],
        }
        reason = (
            "foreground window does not match target"
            if not scope_guard
            else scope_guard.get("error") or "scope_guard blocked foreground mismatch"
        )
        return _classification(
            FailureType.WINDOW_FOCUS_ERROR,
            0.94 if scope_guard else 0.9,
            evidence,
            reason,
            llm_required=False,
            retryable=True,
            retry_budget=2,
        )

    def _classify_window_not_found(
        self,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        needs_target = any(_truthy_bool(value) is True for value in _values_for_key(data, "needs_target_window"))
        if not needs_target and not _WINDOW_NOT_FOUND_RE.search(text):
            return None
        evidence = {
            "needs_target_window": needs_target,
            "controller_state": _first_dict(data, "controller_state"),
            "error": data.get("error") if isinstance(data, dict) else "",
        }
        return _classification(
            FailureType.WINDOW_NOT_FOUND,
            0.92 if needs_target else 0.82,
            evidence,
            evidence.get("error") or "target window is unavailable",
            llm_required=False,
            retryable=True,
            retry_budget=2,
        )

    def _classify_wait_timeout(
        self,
        tool_name: str,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        wait_children: list[dict[str, Any]] = []
        for item in _child_payloads(data):
            action = str(item.get("action") or "").casefold()
            if action not in {"wait_change", "wait_stable", "sync_change", "sync_stable", "wait"}:
                continue
            changed_false = action.endswith("change") and _truthy_bool(item.get("changed")) is False
            stable_false = action.endswith("stable") and _truthy_bool(item.get("stable")) is False
            ok_false = _truthy_bool(item.get("ok")) is False
            if changed_false or stable_false or ok_false:
                wait_children.append(item)
        structured_failure_text = " ".join(
            str(value or "")
            for key in ("error", "reason", "stage", "verify_detail")
            for value in _values_for_key(data, key)
            if not isinstance(value, (dict, list))
        )
        explicit_timeout = bool(_TIMEOUT_RE.search(
            structured_failure_text if data else text
        ))
        if wait_children or (
            tool_name in {"wait", "run_action_batch"}
            and explicit_timeout
        ):
            return _classification(
                FailureType.WAIT_TIMEOUT,
                0.88 if wait_children else 0.72,
                {
                    "wait_results": wait_children[:3],
                    "metrics": data.get("metrics") if isinstance(data, dict) else {},
                },
                "dynamic wait condition did not become satisfied",
                llm_required=False,
                retryable=True,
                retry_budget=2,
            )
        return None

    def _classify_control_not_found(
        self,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        if not data:
            return None
        locator_evidence: dict[str, Any] = {}
        missing_locator = False
        for key in ("anchor_locator", "initial_anchor_locator", "input_locator"):
            locators = [value for value in _values_for_key(data, key) if isinstance(value, dict)]
            if not locators:
                continue
            locator_evidence[key] = locators[:2]
            if any(_truthy_bool(locator.get("found")) is False for locator in locators):
                missing_locator = True
        candidates = _candidate_labels(data)
        controller_state = _first_dict(data, "controller_state")
        controller_phase = str(controller_state.get("phase") or "").casefold()
        structured_failure_text = " ".join(
            str(value or "")
            for key in ("error", "reason", "stage", "verify_detail")
            for value in _values_for_key(data, key)
            if not isinstance(value, (dict, list))
        )
        text_control_failure = bool(re.search(
            r"(control|element|anchor|input).*(not\s+found|missing|failed)|"
            r"(not\s+found|missing|failed).*(control|element|anchor|input)",
            structured_failure_text,
            re.IGNORECASE,
        ))
        candidate_failure = bool(
            candidates
            and _has_failed_signal(data, text)
            and (
                _truthy_bool(data.get("blocked")) is True
                or controller_phase in {"blocked", "failed", "error", "locating"}
                or text_control_failure
            )
        )
        if not (
            missing_locator
            or candidate_failure
            or "locat" in controller_phase
            or text_control_failure
        ):
            return None
        evidence = {
            **locator_evidence,
            "candidate_click_labels": candidates,
            "controller_state": controller_state,
        }
        confidence = 0.9 if missing_locator else (0.82 if candidates else 0.68)
        return _classification(
            FailureType.CONTROL_NOT_FOUND,
            confidence,
            evidence,
            "control or anchor locator did not resolve",
            llm_required=False,
            retryable=True,
            retry_budget=2,
        )

    def _classify_click_offset(
        self,
        tool_name: str,
        args: dict[str, Any],
        data: dict[str, Any],
    ) -> FailureClassification | None:
        if not data:
            return None
        click_like = tool_name in {"click", "drag", "mouse_down", "mouse_up"} or any(
            str(item.get("action") or "") in {"click", "click_element", "drag"}
            for item in _child_payloads(data)
        )
        if not click_like:
            return None
        if not any(key in args for key in ("x", "y", "element_id")) and tool_name == "click":
            return None
        screen_delta = _first_dict(data, "screen_delta")
        changed = _truthy_bool(screen_delta.get("changed"))
        changed_in_target = _truthy_bool(screen_delta.get("changed_in_target_region"))
        foreground_match = _truthy_bool(screen_delta.get("foreground_matches_target"))
        if foreground_match is False:
            return None
        verified_false = any(_truthy_bool(item.get("verified")) is False for item in _child_payloads(data))
        if (changed is True and changed_in_target is False) or (verified_false and changed is False):
            return _classification(
                FailureType.CLICK_COORD_OFFSET,
                0.76,
                {"screen_delta": screen_delta, "args": {k: args.get(k) for k in ("x", "y", "element_id")}},
                "click did not produce evidence in the intended target region",
                llm_required=False,
                retryable=True,
                retry_budget=1,
            )
        return None

    def _classify_action_succeeded_verify_failed(
        self,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        if not data:
            return None
        missing = _missing_texts(data)
        action_ok = any(_truthy_bool(item.get("ok")) is True for item in _child_payloads(data))
        verified_false = any(_truthy_bool(item.get("verified")) is False for item in _child_payloads(data))
        visual_change = any(
            _truthy_bool(item.get("visual_change_detected")) is True
            for item in _child_payloads(data)
        )
        screen_delta = _first_dict(data, "screen_delta")
        if screen_delta and _truthy_bool(screen_delta.get("changed")) is True:
            visual_change = True
        controller_state = _first_dict(data, "controller_state")
        normal_observe_handoff = bool(
            not missing
            and _truthy_bool(data.get("needs_observe")) is True
            and str(controller_state.get("phase") or "").casefold() == "needs_observe"
            and not data.get("verify_detail")
            and not data.get("verify_method")
        )
        if normal_observe_handoff:
            return None
        structured_failure_text = " ".join(
            str(value or "")
            for key in ("error", "reason", "verify_detail")
            for value in _values_for_key(data, key)
            if not isinstance(value, (dict, list))
        )
        if action_ok and verified_false and (
            missing or visual_change or _TEXT_NOT_FOUND_RE.search(structured_failure_text)
        ):
            return _classification(
                FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED,
                0.86 if missing else 0.74,
                {
                    "missing_texts": missing,
                    "screen_delta": screen_delta,
                    "verify_detail": data.get("verify_detail"),
                    "verify_method": data.get("verify_method"),
                },
                "action appears to have succeeded but verification evidence is absent or too strict",
                llm_required=False,
                retryable=True,
                retry_budget=1,
            )
        return None

    def _classify_ocr_misread(
        self,
        data: dict[str, Any],
        text: str,
    ) -> FailureClassification | None:
        if not data:
            return None
        verify_details = [
            str(value or "") for value in _values_for_key(data, "verify_detail")
            if str(value or "").strip()
        ]
        verify_methods = [
            str(value or "").casefold() for value in _values_for_key(data, "verify_method")
        ]
        method_ocr_or_empty = not verify_methods or any(
            method in {"", "ocr", "ocr-fullscreen", "ocr_fullscreen"} for method in verify_methods
        )
        details_text = " ".join(verify_details)
        missing = _missing_texts(data)
        if method_ocr_or_empty and (missing or (verify_details and _TEXT_NOT_FOUND_RE.search(details_text))):
            return _classification(
                FailureType.OCR_MISREAD,
                0.72,
                {
                    "missing_texts": missing,
                    "verify_detail": verify_details[:3],
                    "verify_method": verify_methods[:3],
                },
                "text verification failed through OCR or no perception method",
                llm_required=False,
                retryable=True,
                retry_budget=2,
            )
        return None

    def _classify_task_constraint_lost(
        self,
        tool_name: str,
        data: dict[str, Any],
        text: str,
        *,
        task: str,
        recent_results: list[tuple[str, str]] | None,
        visible_evidence_ledger: list[dict[str, Any]] | None,
        tool_history: list[tuple[str, dict[str, Any], str]] | None,
    ) -> FailureClassification | None:
        literals = _task_literals(task)
        if not literals:
            return None
        evidence_blob = _normalize_visible_text(
            _evidence_text(text, recent_results, visible_evidence_ledger, tool_history)
        )
        missing = [
            literal for literal in literals
            if _normalize_visible_text(literal) not in evidence_blob
        ]
        if not missing:
            return None
        failed = _has_failed_signal(data, text)
        controller_state = _first_dict(data, "controller_state")
        normal_observe_handoff = bool(
            tool_name == "run_action_batch"
            and _truthy_bool(data.get("ok")) is True
            and _truthy_bool(data.get("needs_observe")) is True
            and str(controller_state.get("phase") or "").casefold() == "needs_observe"
            and not _missing_texts(data)
            and not data.get("verify_detail")
            and not data.get("verify_method")
        )
        if normal_observe_handoff:
            return None
        read_only_tools = {
            "observe",
            "read_text",
            "coordinate_diagnostics",
            "screenshot",
            "zoom",
            "list_windows",
            "scan_windows",
            "find_app",
            "find_program",
        }
        if tool_name in read_only_tools and not failed:
            return None
        claimed_ok = any(_truthy_bool(item.get("ok")) is True for item in _child_payloads(data))
        root_verified = _truthy_bool(data.get("verified")) is True
        # Missing final-task literals are not a failure during preparatory GUI
        # steps.  A successful focus/click/wait commonly occurs before the
        # literal is typed, so treating any nested ``ok`` as proof that the
        # task should already be complete creates false recovery/replan events.
        # Only completed controller routines and verified text entry are
        # expected to carry the final literal evidence on a successful result.
        evidence_tools = {"execute_subgoal", "run_action_batch", "type_text"}
        if tool_name not in evidence_tools:
            return None
        # A child action being verified only proves that click/type operation,
        # not that the entire user task has reached its final visible state.
        # Batch-level task-constraint checks therefore require root-level
        # verification; concrete action/verify failures are classified above.
        if tool_name == "run_action_batch" and not root_verified:
            return None
        if not failed and not root_verified:
            return None
        has_recovery_source = bool(task or visible_evidence_ledger or recent_results or tool_history)
        return _classification(
            FailureType.TASK_CONSTRAINT_LOST,
            0.78 if claimed_ok or root_verified else 0.66,
            {
                "required_literals": literals,
                "missing_literals": missing,
                "has_task_source": bool(task),
                "visible_evidence_count": len(visible_evidence_ledger or []),
                "recent_result_count": len(recent_results or []),
            },
            "required task literal or visible payload is missing from evidence",
            llm_required=not has_recovery_source,
            retryable=has_recovery_source,
            retry_budget=1 if has_recovery_source else 0,
        )


def classify_failure(
    tool_name: str,
    args: dict[str, Any] | None,
    result_text: str,
    **kwargs: Any,
) -> FailureClassification | None:
    return FailureClassifier().classify_tool_result(tool_name, args, result_text, **kwargs)
