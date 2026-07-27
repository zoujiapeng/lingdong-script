"""Independent local oracle for the minimal live vNext canary.

The browser target reports only browser-owned ``isTrusted`` events and DOM
state.  This server independently validates the complete ordered sequence; a
runtime completion claim or a lone JavaScript success event is insufficient.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "vnext_canary_target.html"
STATE_LOCK = threading.Lock()
STATE: dict[str, Any] = {"ready": True, "events": [], "started_at": time.time()}
SCENARIOS = frozenset({"click_double", "scroll_roundtrip", "type_hotkey"})
INPUT_EVENT_TYPES = frozenset({
    "pointerdown", "pointerup", "click", "dblclick", "wheel",
    "scroll_state", "scroll_checkpoint", "keydown", "keyup",
    "beforeinput", "input", "scenario_success", "wrong_input",
})


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:320]
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:96]]
    if isinstance(value, dict):
        return {
            str(key)[:96]: _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:96]
        }
    return str(value)[:320]


def _events_of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [item for item in events if item.get("type") == event_type]


def _details(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("details")
    return value if isinstance(value, dict) else {}


def _validate_click_double(
    events: list[dict[str, Any]],
    success: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    pointer_down = [
        str(_details(item).get("target_id") or "")
        for item in _events_of(events, "pointerdown")
    ]
    pointer_up = [
        str(_details(item).get("target_id") or "")
        for item in _events_of(events, "pointerup")
    ]
    clicks = _events_of(events, "click")
    click_targets = [str(_details(item).get("target_id") or "") for item in clicks]
    click_details = [int(_details(item).get("detail") or 0) for item in clicks]
    double_clicks = _events_of(events, "dblclick")
    if pointer_down != ["single", "double", "double"]:
        errors.append(f"pointerdown sequence mismatch: {pointer_down!r}")
    if pointer_up != ["single", "double", "double"]:
        errors.append(f"pointerup sequence mismatch: {pointer_up!r}")
    if click_targets != ["single", "double", "double"]:
        errors.append(f"click target sequence mismatch: {click_targets!r}")
    if click_details != [1, 1, 2]:
        errors.append(f"click detail sequence mismatch: {click_details!r}")
    if len(double_clicks) != 1:
        errors.append(f"expected exactly one dblclick, observed {len(double_clicks)}")
    elif (
        _details(double_clicks[0]).get("target_id") != "double"
        or int(_details(double_clicks[0]).get("detail") or 0) != 2
    ):
        errors.append("dblclick did not bind the DOUBLE target with detail=2")
    final = _details(success)
    counts = final.get("counts") if isinstance(final.get("counts"), dict) else {}
    if (
        final.get("final_dom_state") != "complete"
        or int(counts.get("single_click") or 0) != 1
        or int(counts.get("double_click_events") or 0) != 2
        or int(counts.get("dblclick") or 0) != 1
    ):
        errors.append("click/double-click final DOM state is not exact")
    return errors


def _validate_scroll_roundtrip(
    events: list[dict[str, Any]],
    success: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    wheels = _events_of(events, "wheel")
    directions = [str(_details(item).get("direction") or "") for item in wheels]
    if not directions or "down" not in directions or "up" not in directions:
        errors.append(f"wheel directions do not cover down and up: {directions!r}")
    elif (
        any(direction not in {"down", "up"} for direction in directions)
        or directions.index("up") <= directions.index("down")
        or "down" in directions[directions.index("up"):]
    ):
        errors.append(f"wheel directions are not one down-then-up transaction: {directions!r}")
    checkpoints = _events_of(events, "scroll_checkpoint")
    if len(checkpoints) != 1:
        errors.append(f"expected exactly one lower checkpoint, observed {len(checkpoints)}")
    elif int(_details(checkpoints[0]).get("checkpoint_count") or 0) != 1:
        errors.append("lower checkpoint count is not one")
    final = _details(success)
    if (
        final.get("final_dom_state") != "complete"
        or final.get("phase") != "complete"
        or int(final.get("checkpoint_count") or 0) != 1
        or int(final.get("scroll_top") or 0) > 4
        or int(final.get("wheel_count") or 0) != len(wheels)
    ):
        errors.append("scroll final DOM state is not the exact completed roundtrip")
    return errors


def _validate_type_hotkey(
    events: list[dict[str, Any]],
    success: dict[str, Any],
    ready: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    pointer_down = [
        str(_details(item).get("target_id") or "")
        for item in _events_of(events, "pointerdown")
    ]
    pointer_up = [
        str(_details(item).get("target_id") or "")
        for item in _events_of(events, "pointerup")
    ]
    if pointer_down != ["editor"] or pointer_up != ["editor"]:
        errors.append(
            "editor pointerdown/pointerup sequence must each occur exactly once"
        )
    clicks = _events_of(events, "click")
    if (
        len(clicks) != 1
        or _details(clicks[0]).get("target_id") != "editor"
        or int(_details(clicks[0]).get("detail") or 0) != 1
    ):
        errors.append("editor must receive exactly one trusted single click")
    keydowns = _events_of(events, "keydown")
    keyups = _events_of(events, "keyup")
    select_down = [item for item in keydowns if _details(item).get("chord") == "ctrl+a"]
    select_up = [item for item in keyups if _details(item).get("chord") == "ctrl+a"]
    submit_down = [item for item in keydowns if _details(item).get("chord") == "ctrl+enter"]
    submit_up = [item for item in keyups if _details(item).get("chord") == "ctrl+enter"]
    if tuple(map(len, (select_down, select_up, submit_down, submit_up))) != (1, 1, 1, 1):
        errors.append("Ctrl+A and Ctrl+Enter must each have one keydown and one keyup")
    if any(_details(item).get("repeat") is True for item in keydowns + keyups):
        errors.append("repeated keyboard events are forbidden")
    before_inputs = _events_of(events, "beforeinput")
    inputs = _events_of(events, "input")
    expected_token = str(_details(ready).get("expected_token") or "")
    initial_value = str(_details(ready).get("initial_value") or "")
    if (
        not before_inputs
        or len(before_inputs) != len(inputs)
        or len(before_inputs) > max(1, len(expected_token))
    ):
        errors.append("beforeinput/input event counts are not exact and bounded")
    before_details = [_details(item) for item in before_inputs]
    input_details = [_details(item) for item in inputs]
    if any(
        details.get("target_id") != "editor"
        or details.get("input_type") != "insertText"
        or type(details.get("data")) is not str
        or not details.get("data")
        for details in before_details + input_details
    ):
        errors.append("text input contains paste, deletion, correction, or non-editor data")
    before_data = [str(details.get("data") or "") for details in before_details]
    input_data = [str(details.get("data") or "") for details in input_details]
    if before_data != input_data or "".join(input_data) != expected_token:
        errors.append("insertText data does not form the exact token once")
    expected_values: list[str] = []
    prefix = ""
    for chunk in input_data:
        prefix += chunk
        expected_values.append(prefix)
    observed_values = [str(details.get("value") or "") for details in input_details]
    observed_before = [
        str(details.get("value_before") or "")
        for details in before_details
    ]
    expected_before = [initial_value, *expected_values[:-1]] if expected_values else []
    if observed_values != expected_values or observed_before != expected_before:
        errors.append("text value progression contains replacement or correction")
    final = _details(success)
    if (
        not expected_token
        or final.get("final_dom_state") != "complete"
        or str(final.get("expected_token") or "") != expected_token
        or str(final.get("final_value") or "") != expected_token
        or int(final.get("select_all_count") or 0) != 1
        or int(final.get("submit_count") or 0) != 1
        or int(final.get("click_count") or 0) != 1
    ):
        errors.append("type/hotkey final DOM state is not exact")
    return errors


def validate_case_events(
    events: list[dict[str, Any]],
    *,
    case_id: str,
    scenario: str,
    seed: int,
    page_id: str,
) -> dict[str, Any]:
    """Validate one immutable case without consulting runtime claims."""

    errors: list[str] = []
    if scenario not in SCENARIOS:
        errors.append(f"unsupported scenario: {scenario!r}")
    if not case_id or not page_id or seed <= 0:
        errors.append("case/page/seed identity is incomplete")
    if not events:
        errors.append("no browser events were received")
    sequences = [int(item.get("event_seq") or 0) for item in events]
    if sequences != list(range(1, len(events) + 1)):
        errors.append(f"event sequence is not exact and contiguous: {sequences!r}")
    for item in events:
        identity = (
            str(item.get("case_id") or ""),
            str(item.get("scenario") or ""),
            int(item.get("seed") or 0),
            str(item.get("page_id") or ""),
        )
        if identity != (case_id, scenario, seed, page_id):
            errors.append(f"event identity mismatch at seq={item.get('event_seq')}")
        if item.get("type") in INPUT_EVENT_TYPES and item.get("type") != "page_ready":
            if item.get("trusted") is not True:
                errors.append(f"untrusted input event at seq={item.get('event_seq')}")
    ready_events = _events_of(events, "page_ready")
    if len(ready_events) != 1:
        errors.append(f"expected one page_ready event, observed {len(ready_events)}")
        ready = {}
    else:
        ready = ready_events[0]
        if ready.get("trusted") is not False:
            errors.append("page_ready must not masquerade as trusted user input")
    wrong = _events_of(events, "wrong_input")
    if wrong:
        errors.append(f"wrong or duplicate input was recorded {len(wrong)} time(s)")
    successes = _events_of(events, "scenario_success")
    if len(successes) != 1:
        errors.append(f"expected one scenario_success event, observed {len(successes)}")
        success = {}
    else:
        success = successes[0]
        final = _details(success)
        if (
            success.get("trusted") is not True
            or final.get("succeeded") is not True
            or final.get("violations") != []
        ):
            errors.append("scenario_success is not trusted, exact, and violation-free")
    if success:
        if scenario == "click_double":
            errors.extend(_validate_click_double(events, success))
        elif scenario == "scroll_roundtrip":
            errors.extend(_validate_scroll_roundtrip(events, success))
        elif scenario == "type_hotkey":
            errors.extend(_validate_type_hotkey(events, success, ready))
    return {
        "passed": not errors,
        "source": "independent_vnext_canary_browser_oracle",
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "page_id": page_id,
        "event_count": len(events),
        "trusted_input_count": sum(
            item.get("trusted") is True and item.get("type") in INPUT_EVENT_TYPES
            for item in events
        ),
        "wrong_input_count": len(wrong),
        "success_event_count": len(successes),
        "errors": errors,
    }


def _state_snapshot(
    *,
    case_id: str = "",
    scenario: str = "",
    seed: int = 0,
    page_id: str = "",
) -> dict[str, Any]:
    with STATE_LOCK:
        events = json.loads(json.dumps(STATE.get("events") or []))
        started_at = STATE.get("started_at")
    if case_id:
        events = [
            item for item in events
            if str(item.get("case_id") or "") == case_id
        ]
    if events:
        sample = events[0]
        scenario = scenario or str(sample.get("scenario") or "")
        seed = seed or int(sample.get("seed") or 0)
        page_id = page_id or str(sample.get("page_id") or "")
    oracle = validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    return {
        "ready": True,
        "success": oracle["passed"],
        "started_at": started_at,
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "page_id": page_id,
        "events": events,
        "oracle": oracle,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "WinPilotVNextCanaryOracle/1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/state":
            query = parse_qs(parsed.query, keep_blank_values=False)
            self._send_json(200, _state_snapshot(
                case_id=str((query.get("case") or [""])[0] or "")[:160],
                scenario=str((query.get("scenario") or [""])[0] or "")[:80],
                seed=int((query.get("seed") or ["0"])[0] or 0),
                page_id=str((query.get("page") or [""])[0] or "")[:240],
            ))
            return
        if parsed.path in {"/", "/vnext_canary_target.html"}:
            data = TARGET.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/event":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= 32_768:
                raise ValueError("event payload size is outside the bounded contract")
            event = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            safe = {
                "case_id": str(event.get("case_id") or "")[:160],
                "scenario": str(event.get("scenario") or "")[:80],
                "seed": int(event.get("seed") or 0),
                "page_id": str(event.get("page_id") or "")[:240],
                "event_seq": int(event.get("event_seq") or 0),
                "type": str(event.get("type") or "")[:80],
                "trusted": event.get("trusted") is True,
                "details": _safe_value(event.get("details") or {}),
                "received_at": time.time(),
            }
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        with STATE_LOCK:
            STATE["events"].append(safe)
            STATE["events"] = STATE["events"][-4000:]
        self._send_json(200, {"ok": True, "event_seq": safe["event_seq"]})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8891
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"ready": True, "port": port}), flush=True)
    server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
