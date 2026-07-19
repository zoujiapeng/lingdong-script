"""Factual classification of desktop tool failures.

The classifier reads only result evidence.  It does not choose the next tool,
rewrite an action, or turn a task phrase into a recovery playbook.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class FailureType(str, Enum):
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    CONTROL_NOT_FOUND = "CONTROL_NOT_FOUND"
    OCR_MISREAD = "OCR_MISREAD"
    CLICK_COORD_OFFSET = "CLICK_COORD_OFFSET"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    WINDOW_FOCUS_ERROR = "WINDOW_FOCUS_ERROR"
    VISUAL_GROUNDING_REQUIRED = "VISUAL_GROUNDING_REQUIRED"
    ACTION_SUCCEEDED_VERIFICATION_FAILED = "ACTION_SUCCEEDED_VERIFICATION_FAILED"
    VISUAL_ACTION_NO_EFFECT = "VISUAL_ACTION_NO_EFFECT"


@dataclass
class FailureClassification:
    failure_type: FailureType
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    normalized_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "confidence": round(float(self.confidence), 3),
            "evidence": self.evidence,
            "normalized_reason": self.normalized_reason,
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


def _classification(
    failure_type: FailureType,
    confidence: float,
    evidence: dict[str, Any],
    reason: str,
) -> FailureClassification:
    return FailureClassification(
        failure_type=failure_type,
        confidence=confidence,
        evidence=evidence,
        normalized_reason=_compact_reason(reason),
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

        visual_no_effect = self._classify_visual_action_no_effect(data)
        if visual_no_effect is not None:
            return visual_no_effect

        action_verify = self._classify_action_succeeded_verify_failed(data, text)
        if action_verify is not None:
            return action_verify

        ocr = self._classify_ocr_misread(data, text)
        if ocr is not None:
            return ocr

        if data and not _has_failed_signal(data, text):
            return None
        if payloads:
            grounding_payload = next(
                (item for item in payloads if item.get("visual_grounding_required") is True),
                None,
            )
            if grounding_payload is not None:
                return _classification(
                    FailureType.VISUAL_GROUNDING_REQUIRED,
                    0.99,
                    {
                        "blocked": True,
                        "visual_grounding_required": True,
                    },
                    str(grounding_payload.get("error") or "fresh real frame required"),
                )
            errors = [item.get("error") for item in payloads if item.get("error")]
            if errors:
                return _classification(
                    FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED,
                    0.45,
                    {"errors": errors[:3]},
                    errors[0],
                )
        return None

    def _classify_visual_action_no_effect(
        self,
        data: dict[str, Any],
    ) -> FailureClassification | None:
        if not data:
            return None
        failures: list[dict[str, Any]] = []
        for item in _child_payloads(data):
            payload = item.get("data") if isinstance(item.get("data"), dict) else item
            if (
                payload.get("failure_type") == "VISUAL_ACTION_NO_EFFECT"
                or (
                    item.get("action") in {"drag", "draw_strokes"}
                    and _truthy_bool(payload.get("visual_change_detected")) is False
                )
            ):
                failures.append({
                    "action": item.get("action"),
                    "visual_change_ratio": payload.get("visual_change_ratio"),
                    "failure_subtype": payload.get("failure_subtype"),
                    "error": payload.get("error") or item.get("error"),
                })
        if not failures:
            return None
        return _classification(
            FailureType.VISUAL_ACTION_NO_EFFECT,
            0.97,
            {"visual_action_failures": failures[:4]},
            "visual input was issued but its intended region did not change",
        )

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
            )
        return None

def classify_failure(
    tool_name: str,
    args: dict[str, Any] | None,
    result_text: str,
    **kwargs: Any,
) -> FailureClassification | None:
    return FailureClassifier().classify_tool_result(tool_name, args, result_text, **kwargs)
