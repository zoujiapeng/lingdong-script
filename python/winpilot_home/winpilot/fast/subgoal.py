"""Controller-first subgoal executor.

This is not a second agent. It is a deterministic routine layer under the
unified AgentRunner/ToolSession: the LLM supplies a compact subgoal and the
local GUI controller performs the low-level click/type/wait/verify steps.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import win32gui

from ..action.verify import _match_visible_text
from ..agent.task_rules import _required_literal_tokens, _required_visible_payloads, _visible_state_targets
from ..agent.tool_result import ToolResult
from ..perception import ocr, vision_cv, win32
from ..perception import reducer
from ..perception.fusion import Snapshot
from ..perception.model import UIElement
from ..utils.screenshot import Region, window_region
from .controller import FastGuiController


@dataclass
class SubgoalRequest:
    subgoal: str
    target_window: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    click: dict[str, Any] = field(default_factory=dict)
    clicks: list[dict[str, Any]] = field(default_factory=list)
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
    clicks: list[dict[str, Any]] = []
    if click_spec:
        clicks.append(click_spec)
    raw_clicks = args.get("clicks") or args.get("buttons") or []
    if isinstance(raw_clicks, list):
        clicks.extend(item for item in raw_clicks if isinstance(item, dict))
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
            "clicks": clicks,
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
        clicks=clicks,
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


def _verified_expect_texts(actions: list[dict[str, Any]], data: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    results = data.get("results") or []
    if not isinstance(results, list):
        return matched
    for action, result in zip(actions, results):
        if not isinstance(result, dict):
            continue
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        verified = result.get("verified") is True or result_data.get("verified") is True
        expect = action.get("expect") if isinstance(action.get("expect"), dict) else {}
        text = expect.get("text_appears")
        if verified and isinstance(text, str) and text.strip():
            matched.append(text.strip())
    return _unique(matched)


def _rough_region(window: Region, hint: str) -> Region | None:
    hint = (hint or "").strip().casefold().replace("_", "-").replace(" ", "-")
    if not hint:
        return None
    left, top, width, height = window.left, window.top, window.width, window.height
    if hint in {"top", "upper"}:
        return Region(left, top, width, max(80, int(height * 0.46))).clamp_to_screen()
    if hint in {"bottom", "lower"}:
        h = max(80, int(height * 0.46))
        return Region(left, top + height - h, width, h).clamp_to_screen()
    if hint == "left":
        trim = min(96, max(0, int(height * 0.08))) if height >= 420 else 0
        return Region(left, top + trim, max(100, int(width * 0.36)), max(80, height - trim)).clamp_to_screen()
    if hint == "right":
        trim = min(96, max(0, int(height * 0.08))) if height >= 420 else 0
        w = max(100, int(width * 0.36))
        return Region(left + width - w, top + trim, w, max(80, height - trim)).clamp_to_screen()
    if hint in {"top-right", "upper-right"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left + width - w, top, w, h).clamp_to_screen()
    if hint in {"top-left", "upper-left"}:
        return Region(left, top, max(100, int(width * 0.55)), max(80, int(height * 0.48))).clamp_to_screen()
    if hint in {"bottom-right", "lower-right"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left + width - w, top + height - h, w, h).clamp_to_screen()
    if hint in {"bottom-left", "lower-left"}:
        w = max(100, int(width * 0.55))
        h = max(80, int(height * 0.48))
        return Region(left, top + height - h, w, h).clamp_to_screen()
    if hint in {"center", "middle"}:
        w = max(140, int(width * 0.82))
        h = max(100, int(height * 0.72))
        return Region(left + (width - w) // 2, top + (height - h) // 2, w, h).clamp_to_screen()
    return None


def _click_terms(request: SubgoalRequest) -> list[str]:
    terms: list[str] = []
    for spec in request.clicks or ([request.click] if request.click else []):
        for key in ("label", "text", "name"):
            value = str(spec.get(key) or "").strip()
            if value:
                terms.append(value)
        fallback = spec.get("fallback_text") or spec.get("fallback") or []
        if isinstance(fallback, str):
            fallback = [fallback]
        if isinstance(fallback, list):
            terms.extend(str(item or "").strip() for item in fallback if str(item or "").strip())
    return _unique(terms)


def _region_hint(request: SubgoalRequest) -> str:
    for spec in request.clicks or ([request.click] if request.click else []):
        value = str(spec.get("expected_region") or "").strip()
        if value:
            return value
    return ""


def _elements_contain_terms(elements: list[UIElement], terms: list[str]) -> bool:
    if not terms:
        return bool(elements)
    return any(
        _scope_mentions(term, element.text)
        for term in terms
        for element in elements
    )


_CHROME_TEXT_RE = re.compile(r"^(?:win!?|chat\(?|https?://|127\.0\.0\.1|ch\b)", re.IGNORECASE)


def _point_in_region(element: UIElement, region: Region) -> bool:
    try:
        return region.left <= int(element.cx) <= region.right and region.top <= int(element.cy) <= region.bottom
    except (TypeError, ValueError):
        return False


def _visible_region_text_candidates(
    snapshot: Snapshot | None,
    request: SubgoalRequest,
    limit: int = 16,
) -> list[str]:
    if snapshot is None:
        return []
    hint = _region_hint(request)
    region = _rough_region(snapshot.region, hint) if hint else snapshot.region
    if region is None:
        region = snapshot.region
    # Skip browser/title chrome for candidate hints; the raw snapshot remains in
    # trace, this list is only a recovery nudge after an anchor miss.
    top_cutoff = snapshot.region.top + max(72, int(snapshot.region.height * 0.08))
    texts: list[str] = []
    for element in snapshot.elements:
        text = str(getattr(element, "text", "") or "").strip()
        if not text or len(text) > 42:
            continue
        if _CHROME_TEXT_RE.search(text):
            continue
        if not _point_in_region(element, region):
            continue
        if getattr(element, "cy", 0) < top_cutoff:
            continue
        width = int(getattr(element, "width", 0) or 0)
        height = int(getattr(element, "height", 0) or 0)
        if width > 280 or height > 56:
            continue
        texts.append(text)
    return _unique(texts)[:limit]


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
        session_hwnd = getattr(self.session, "hwnd", None)
        if session_hwnd:
            return session_hwnd
        try:
            foreground = int(win32gui.GetForegroundWindow())
            if foreground:
                try:
                    setattr(self.session, "hwnd", foreground)
                except Exception:
                    pass
                return foreground
        except Exception:
            pass
        return None

    def _ensure_observed(self, request: SubgoalRequest) -> tuple[Snapshot | None, list[dict[str, Any]], bool]:
        prelude: list[dict[str, Any]] = []
        skip_click_auto_observe = False
        hwnd = self._target_hwnd(request)
        if hwnd:
            try:
                focus = self.session.dispatch("focus_window", {"hwnd": hwnd})
                prelude.append({"action": "focus_window", "ok": not bool(focus.terminal), "preview": focus.text[:240]})
            except Exception:
                pass
        if getattr(self.session, "snapshot", None) is None:
            targeted = self._targeted_initial_snapshot(request)
            if targeted is not None:
                self.session.snapshot = targeted
                targeted_meta = targeted.dim_stats.get("targeted_initial", {})
                skip_click_auto_observe = targeted_meta.get("label_matched") is False
                prelude.append({
                    "action": "targeted_observe",
                    "ok": True,
                    "preview": targeted.to_text(max_elements=12)[:320],
                    "label_matched": targeted_meta.get("label_matched"),
                    "dim_stats": targeted.dim_stats,
                })
            else:
                observe = self.session.dispatch("observe", {})
                prelude.append({"action": "observe", "ok": not bool(observe.terminal), "preview": observe.text[:320]})
        snapshot = getattr(self.session, "snapshot", None)
        return snapshot, prelude, skip_click_auto_observe

    def _targeted_initial_snapshot(self, request: SubgoalRequest) -> Snapshot | None:
        hwnd = self._target_hwnd(request)
        hint = _region_hint(request)
        if not hwnd or not hint:
            return None
        try:
            full_region = window_region(hwnd)
        except Exception:
            return None
        roi = _rough_region(full_region, hint)
        if roi is None:
            return None
        terms = _click_terms(request)
        dim_stats: dict[str, dict[str, Any]] = {}
        elements: list[UIElement] = []
        started = time.monotonic()
        ocr_elements = ocr.collect(roi)
        dim_stats["ocr"] = {
            "count": len(ocr_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "targeted_initial_roi",
            "roi": [roi.left, roi.top, roi.width, roi.height],
        }
        elements.extend(ocr_elements)
        started = time.monotonic()
        cv_elements = vision_cv.detect_icon_candidates(roi)
        dim_stats["cv"] = {
            "count": len(cv_elements),
            "ms": int((time.monotonic() - started) * 1000),
            "scope": "targeted_initial_roi",
            "roi": [roi.left, roi.top, roi.width, roi.height],
        }
        elements.extend(cv_elements)
        label_matched = _elements_contain_terms(elements, terms)
        if not label_matched and not elements:
            return None
        for idx, element in enumerate(elements, start=1):
            element.elem_id = idx
            if "targeted_initial_roi" not in element.states:
                element.states.append("targeted_initial_roi")
        dim_stats["targeted_initial"] = {
            "count": len(elements),
            "ms": 0,
            "scope": "targeted_initial_roi",
            "label_matched": label_matched,
            "term_count": len(terms),
        }
        return Snapshot(
            hwnd=int(hwnd),
            title=win32._window_text(int(hwnd)),
            region=full_region,
            elements=elements,
            dim_stats=dim_stats,
        )

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
        click_specs = request.clicks or ([request.click] if request.click else [])
        changed_query_text = ""
        if not input_spec and click_specs and len(request.verify_texts) == 1:
            changed_query_text = request.verify_texts[0]
        for index, click_spec in enumerate(click_specs):
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
            if changed_query_text:
                actions.append({
                    "action": "observe",
                    "changed_only": True,
                    "query": changed_query_text,
                    "max_elements": 80,
                })
            else:
                actions.append({"action": "read_text"})
        return actions

    def execute(self, args: dict[str, Any]) -> ToolResult:
        request = parse_subgoal_request(args, task=getattr(self.session, "task", ""))
        snapshot, prelude, skip_click_auto_observe = self._ensure_observed(request)
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
        if skip_click_auto_observe:
            for action in actions:
                if action.get("action") == "click_element":
                    action["skip_auto_observe"] = True
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
        expect_matched = _verified_expect_texts(actions, batch)
        matched = [
            text for text in request.verify_texts
            if _match_visible_text(text, visible_text)
            or any(_match_visible_text(text, verified) for verified in expect_matched)
        ]
        missing = [text for text in request.verify_texts if text not in matched]
        verified = bool(batch.get("ok")) and not missing if request.verify_texts else bool(batch.get("verified"))
        recovery_candidates = _visible_region_text_candidates(snapshot, request)
        evidence_packet = {
            "matched_texts": matched,
            "missing_texts": missing,
            "verified_expect_texts": expect_matched,
            "candidate_click_labels": recovery_candidates,
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
        if verified and not missing:
            payload["fast_path_advice"] = (
                "The controller already executed and verified this GUI subgoal"
                " with matched_texts in evidence_packet. Complete the matching"
                " plan item or call done instead of repeating observe/read_text"
                " for the same evidence."
            )
        if missing:
            payload["error"] = "subgoal routine completed but required visible text evidence is missing"
            payload["blocked"] = True
            if recovery_candidates:
                payload["fast_path_advice"] = (
                    "The requested anchor label was not verified. For GUI tasks,"
                    " retry execute_subgoal with one of evidence_packet.candidate_click_labels"
                    " that best matches the intended visible state; do not inspect"
                    " source files or shell output just to find a GUI label."
                )
                payload["recovery_candidates"] = {
                    "candidate_click_labels": recovery_candidates,
                    "expected_visible_texts": request.verify_texts,
                }
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={"tool": "execute_subgoal", "subgoal": request.subgoal, "actions": actions},
        )
