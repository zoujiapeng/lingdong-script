"""Typed applicability contracts for reusable action memory.

Human-readable conditions remain useful in prompts, but replay eligibility is
decided from this structured contract.  The checks intentionally fail closed
when a runtime reuse path has not observed the app/window required by a skill.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_IGNORED_PARAMETERS = {"max_steps", "original_task"}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return dict(result) if isinstance(result, Mapping) else {}
    return {}


def _value(source: Any, *names: str) -> Any:
    data = _mapping(source)
    for name in names:
        value = data.get(name) if data else getattr(source, name, None)
        if value not in (None, "", [], {}):
            return value
    return None


def _normal(text: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").casefold())


def _bigrams(text: Any) -> set[str]:
    value = _normal(text)
    return {value[index:index + 2] for index in range(len(value) - 1)}


def task_intent_matches(current: str, expected: str) -> bool:
    left, right = _normal(current), _normal(expected)
    if len(left) < 3 or len(right) < 3:
        return False
    if left == right or left in right or right in left:
        return True
    left_pairs, right_pairs = _bigrams(left), _bigrams(right)
    if not left_pairs or not right_pairs:
        return False
    return len(left_pairs & right_pairs) / min(len(left_pairs), len(right_pairs)) >= 0.7


def _same_context(actual: Any, expected: Any) -> bool:
    left, right = _normal(actual), _normal(expected)
    return bool(left and right and (left == right or left in right or right in left))


def _clean_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(parameters or {}).items()
        if str(key) not in _IGNORED_PARAMETERS and value not in (None, "", [], {})
    }


def build_applicability(
    task: str,
    app: str = "",
    *,
    app_version: str = "",
    stage_type: str = "",
    parameters: Mapping[str, Any] | None = None,
    required_anchors: list[str] | None = None,
) -> dict[str, Any]:
    """Build the persisted, software-agnostic applicability contract."""
    return {
        "task_intent": str(task or "").strip()[:500],
        "app": str(app or "").strip()[:200],
        "app_version": str(app_version or "").strip()[:80],
        "stage_type": str(stage_type or "").strip()[:100],
        "parameters": _clean_parameters(parameters),
        "required_anchors": [
            str(item).strip()[:200]
            for item in (required_anchors or [])
            if str(item).strip()
        ][:12],
        "safety_scope": "desktop_gui",
    }


def runtime_context(
    runtime_profile: Any = None,
    session_context: Any = None,
    *,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = _mapping(runtime_profile)
    session = _mapping(session_context)
    snapshot = _value(session_context, "snapshot")
    snapshot_data = _mapping(snapshot)
    app = (
        profile.get("app")
        or profile.get("window_title")
        or session.get("app")
        or session.get("window_title")
        or session.get("title")
        or snapshot_data.get("title")
        or getattr(snapshot, "title", "")
    )
    anchors: list[str] = []
    for source in (
        profile.get("anchors"),
        session.get("anchors"),
        session.get("target_hint"),
        snapshot_data.get("title"),
        getattr(snapshot, "title", ""),
    ):
        values = source if isinstance(source, (list, tuple, set)) else [source]
        for item in values:
            text = str(item or "").strip()
            if text and text not in anchors:
                anchors.append(text)
    return {
        "app": str(app or "").strip(),
        "app_version": str(
            profile.get("app_version") or session.get("app_version") or ""
        ).strip(),
        "stage_type": str(
            profile.get("stage_type") or session.get("stage_type") or ""
        ).strip(),
        "parameters": _clean_parameters(parameters or profile.get("parameters") or {}),
        "anchors": anchors,
        "safety_scope": str(profile.get("safety_scope") or "desktop_gui").strip(),
    }


@dataclass(frozen=True)
class ApplicabilityDecision:
    allowed: bool
    reasons: tuple[str, ...]
    expected: dict[str, Any]
    current: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "expected": self.expected,
            "current": self.current,
        }


def evaluate(
    record: Mapping[str, Any],
    task: str,
    *,
    runtime_profile: Any = None,
    session_context: Any = None,
    parameters: Mapping[str, Any] | None = None,
    require_runtime_context: bool = False,
) -> ApplicabilityDecision:
    payload = dict(record.get("payload") or record)
    expected = dict(payload.get("applicability") or {})
    if not expected:
        expected = build_applicability(
            str(payload.get("task") or record.get("title") or ""),
            str(payload.get("app") or record.get("app") or ""),
            app_version=str(payload.get("app_version") or ""),
            stage_type=str(payload.get("stage_type") or ""),
            parameters=payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else {},
        )
    current = runtime_context(runtime_profile, session_context, parameters=parameters)
    reasons: list[str] = []

    expected_task = str(expected.get("task_intent") or "")
    if not task_intent_matches(task, expected_task):
        reasons.append("task_intent_mismatch")

    expected_app = str(expected.get("app") or "")
    if expected_app:
        if not current["app"]:
            if require_runtime_context:
                reasons.append("current_app_not_observed")
        elif not _same_context(current["app"], expected_app):
            reasons.append("app_mismatch")

    expected_version = str(expected.get("app_version") or "")
    if expected_version:
        if not current["app_version"]:
            if require_runtime_context:
                reasons.append("app_version_unknown")
        elif _normal(current["app_version"]) != _normal(expected_version):
            reasons.append("app_version_mismatch")

    expected_stage = str(expected.get("stage_type") or "")
    if expected_stage:
        if not current["stage_type"]:
            if require_runtime_context:
                reasons.append("stage_type_missing")
        elif _normal(current["stage_type"]) != _normal(expected_stage):
            reasons.append("stage_type_mismatch")

    expected_scope = str(expected.get("safety_scope") or "")
    if expected_scope and _normal(current["safety_scope"]) != _normal(expected_scope):
        reasons.append("safety_scope_mismatch")

    for key, value in _clean_parameters(expected.get("parameters") or {}).items():
        if key not in current["parameters"]:
            if require_runtime_context:
                reasons.append(f"parameter_missing:{key}")
        elif str(current["parameters"][key]) != str(value):
            reasons.append(f"parameter_mismatch:{key}")

    current_anchors = current["anchors"]
    for anchor in expected.get("required_anchors") or []:
        if not any(_same_context(item, anchor) for item in current_anchors):
            reasons.append(f"anchor_missing:{str(anchor)[:80]}")

    return ApplicabilityDecision(not reasons, tuple(reasons), expected, current)
