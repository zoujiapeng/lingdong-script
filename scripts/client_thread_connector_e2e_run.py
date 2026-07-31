#!/usr/bin/env python3
"""Exercise one real Electron Thread with chat, a connector, and Notepad.

This runner owns a cold-start Electron process so its managed Python runtime
inherits a temporary ``WINPILOT_MCP_CONFIG``.  Every user message is still
entered and submitted by :mod:`client_e2e_run` through the visible renderer's
real Windows mouse/keyboard path.  Connector and file outcomes are verified by
independent append-only/file-system oracles, never by the Agent's ``done``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import psutil
import win32con
import win32gui
import win32process


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402


FIXTURE_SCRIPT = SCRIPT_DIR / "connector_e2e_fixture.py"
PROBE_SCRIPT = SCRIPT_DIR / "connector_e2e_sitecustomize.py"
ELECTRON_EXE = WORKSPACE / "node_modules" / "electron" / "dist" / "electron.exe"
VITE_SCRIPT = WORKSPACE / "node_modules" / "vite" / "bin" / "vite.js"
TSC_SCRIPT = WORKSPACE / "node_modules" / "typescript" / "bin" / "tsc"
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "client-thread-connector-e2e"
FIXTURE_KEY = "verification"
ORACLE_SOURCE = "append_only_connector_and_server_thread_oracle"

CONNECTOR_READ_TASK = (
    "请使用已连接的 {connector} 服务读取名为 verification 的值，然后只把读取到的值回复给我。"
)
CHAT_PROMPTS = (
    "你好，我们开始一段连续对话。请用一句自然的话回应。",
    "你认为清晨和傍晚各有什么优点？请简短回答。",
    "用一句话解释为什么适当休息有助于专注。",
    "如果周末只能安排一项轻松活动，你会建议什么？",
    "请用一个简短比喻描述耐心。",
    "怎样礼貌地提醒朋友别忘了带伞？",
    "用一句话概括阅读纸质书的感受。",
    "给第一次做饭的人一条最实用的建议。",
    "如果今天很忙，怎样安排五分钟的休息？",
    "请用一句自然的话描述雨后空气。",
    "学习新技能时，先练习还是先看完整教程？简短说说。",
    "给久坐的人一句温和的活动提醒。",
    "用一句话说说整理桌面的好处。",
    "如何简短地感谢别人耐心解释？",
    "请给阴天写一句不超过二十字的描述。",
    "为什么把大任务拆小通常更容易开始？",
    "给准备早睡的人一条简单建议。",
    "用一句话描述一杯温水带来的感觉。",
    "如果计划临时改变，怎样保持从容？",
    "我们已经聊了很多轮，请自然地收个尾。",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connector_read_task(server_name: str) -> str:
    server_name = str(server_name or "").strip()
    if not server_name:
        raise ValueError("connector server name is required")
    return CONNECTOR_READ_TASK.format(connector=server_name)


def connector_to_notepad_task(
    fixture: FixtureEnvironment,
    output_file: Path,
) -> str:
    return (
        f"从 {fixture.server_name} 读 verification，"
        "用鼠标键盘将值原样存入记事本文件 "
        f"{output_file.resolve()}。"
    )


def connector_to_current_notepad_task(fixture: FixtureEnvironment) -> str:
    return (
        f"从 {fixture.server_name} 读 verification，"
        "用鼠标键盘将当前记事本文档全文替换为该值并保存。"
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class FixtureEnvironment:
    config_path: Path
    oracle_path: Path
    probe_path: Path
    probe_module_dir: Path
    server_name: str
    token: str = field(repr=False)

    @property
    def value_sha256(self) -> str:
        return _sha256_text(self.token)


def build_fixture_environment(
    directory: Path,
    *,
    token: str = "",
) -> FixtureEnvironment:
    """Create a one-server config whose secret exists only in the temp tree."""

    directory.mkdir(parents=True, exist_ok=True)
    token = token or f"cne2e_{secrets.token_hex(12)}"
    server_name = f"connector-e2e-{secrets.token_hex(4)}"
    oracle_path = directory / "connector-invocations.jsonl"
    probe_path = directory / "connector-probe.jsonl"
    probe_module_dir = directory / "probe-module"
    probe_module_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROBE_SCRIPT, probe_module_dir / "sitecustomize.py")
    config_path = directory / "mcp.json"
    config = {
        "mcpServers": {
            server_name: {
                "command": sys.executable,
                "args": ["-u", str(FIXTURE_SCRIPT.resolve())],
                "cwd": str(WORKSPACE.resolve()),
                "env": {
                    "WINPILOT_CONNECTOR_E2E_TOKEN": token,
                    "WINPILOT_CONNECTOR_E2E_ORACLE": str(oracle_path.resolve()),
                },
            }
        }
    }
    _write_json(config_path, config)
    return FixtureEnvironment(
        config_path=config_path,
        oracle_path=oracle_path,
        probe_path=probe_path,
        probe_module_dir=probe_module_dir,
        server_name=server_name,
        token=token,
    )


def read_invocation_evidence(
    fixture: FixtureEnvironment,
    *,
    after_time_ns: int = 0,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    if fixture.oracle_path.is_file():
        for line_number, line in enumerate(
            fixture.oracle_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"line {line_number}: invalid JSON")
                continue
            if not isinstance(event, dict):
                errors.append(f"line {line_number}: event is not an object")
                continue
            if int(event.get("time_ns") or 0) >= int(after_time_ns):
                events.append(event)
    physical_arguments_sha256 = _sha256_text(
        json.dumps(
            {"key": FIXTURE_KEY},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    matching = [
        event
        for event in events
        if event.get("format") == "connector-e2e-invocation/1"
        and event.get("tool") == "read_value"
        and event.get("key") == FIXTURE_KEY
        and event.get("physical_arguments_sha256") == physical_arguments_sha256
        and event.get("value_sha256") == fixture.value_sha256
    ]
    return {
        "passed": bool(matching and not errors),
        "source": "append_only_fixture_invocation_log",
        "event_count": len(events),
        "matching_invocation_count": len(matching),
        "value_sha256": fixture.value_sha256,
        "physical_arguments_sha256": physical_arguments_sha256,
        "parse_errors": errors,
    }


def _read_connector_probe_events(
    fixture: FixtureEnvironment,
) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    if fixture.probe_path.is_file():
        for line_number, line in enumerate(
            fixture.probe_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"line {line_number}: invalid JSON")
                continue
            if not isinstance(event, dict):
                errors.append(f"line {line_number}: event is not an object")
                continue
            if event.get("phase") in {"probe_error", "probe_install_error"}:
                errors.append(
                    f"line {line_number}: {event.get('phase')} "
                    f"{event.get('error_kind') or ''}".rstrip()
                )
            events.append(event)
    else:
        errors.append("probe log is missing")
    return events, errors


def read_connector_probe_evidence(
    fixture: FixtureEnvironment,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Project private raw/canonical/receipt probe records into hashes only."""

    events, errors = _read_connector_probe_events(fixture)
    raw_calls = [
        event
        for event in events
        if event.get("phase") == "raw_model_connector_call"
        and event.get("run_id") == run_id
        and event.get("tool") == "call_connector_tool"
    ]
    gateway_calls = [
        event
        for event in events
        if event.get("phase") == "canonical_gateway_call"
        and event.get("run_id") == run_id
    ]
    expected_hash = _sha256_text(
        json.dumps(
            {"key": FIXTURE_KEY},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    matching_raw = 0
    for event in raw_calls:
        raw = event.get("raw_arguments_json")
        if type(raw) is not str or _sha256_text(raw) != event.get(
            "raw_arguments_sha256"
        ):
            continue
        try:
            outer = json.loads(raw)
            inner = outer.get("arguments_json") if isinstance(outer, dict) else None
            decoded = json.loads(inner) if type(inner) is str else None
            canonical_wire = json.dumps(
                decoded,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            isinstance(outer, dict)
            and set(outer) == {"ref", "arguments_json"}
            and decoded == {"key": FIXTURE_KEY}
            and _sha256_text(canonical_wire) == expected_hash
        ):
            matching_raw += 1
    matching_gateway = 0
    for event in gateway_calls:
        receipt = event.get("receipt")
        if (
            event.get("canonical_arguments") == {"key": FIXTURE_KEY}
            and event.get("canonical_arguments_sha256") == expected_hash
            and isinstance(receipt, dict)
            and receipt.get("format") == "connector-receipt/1"
            and receipt.get("arguments_sha256") == expected_hash
            and receipt.get("status") == "succeeded"
        ):
            matching_gateway += 1
    passed = bool(
        not errors
        and raw_calls
        and len(raw_calls) == len(gateway_calls)
        and matching_raw == len(raw_calls)
        and matching_gateway == len(gateway_calls)
    )
    return {
        "source": "test_only_raw_canonical_receipt_probe",
        "passed": passed,
        "run_id": run_id,
        "raw_call_count": len(raw_calls),
        "matching_raw_call_count": matching_raw,
        "gateway_call_count": len(gateway_calls),
        "matching_gateway_call_count": matching_gateway,
        "arguments_sha256": expected_hash,
        "repeated_call_count": max(0, len(gateway_calls) - 1),
        "parse_errors": errors,
    }


def read_history_projection_evidence(
    fixture: FixtureEnvironment,
    *,
    run_id: str,
    task_sha256: str,
) -> dict[str, Any]:
    """Read the exact bounded initial messages without exposing their text."""

    events, errors = _read_connector_probe_events(fixture)
    matching = [
        event
        for event in events
        if event.get("phase") == "actor_initial_history_projection"
        and event.get("run_id") == run_id
    ]
    event = matching[-1] if matching else {}
    history_count = int(event.get("history_message_count") or 0)
    provider_count = int(event.get("provider_message_count") or 0)
    roles = list(event.get("history_roles") or [])
    valid = bool(
        len(matching) == 1
        and event.get("goal_sha256") == task_sha256
        and provider_count == history_count + 1
        and len(roles) == history_count
        and all(role in {"user", "assistant"} for role in roles)
        and not errors
    )
    return {
        "source": "test_only_actual_actor_initial_history_projection",
        "passed": valid,
        "event_count": len(matching),
        "goal_sha256": event.get("goal_sha256"),
        "provider_message_count": provider_count,
        "history_message_count": history_count,
        "history_chars": int(event.get("history_chars") or 0),
        "history_roles": roles,
        "history_projection_sha256": event.get(
            "history_projection_sha256"
        ),
        "provider_projection_sha256": event.get(
            "provider_projection_sha256"
        ),
        "parse_errors": errors,
    }


def history_text_only_evidence(
    result: dict[str, Any],
    fixture: FixtureEnvironment,
) -> dict[str, Any]:
    """Prove a history prefill turn did not use connectors or the desktop."""

    run_id = str((result.get("result") or {}).get("run_id") or "")
    probe_events, errors = _read_connector_probe_events(fixture)
    raw_connector_calls = [
        event
        for event in probe_events
        if event.get("phase") == "raw_model_connector_call"
        and event.get("run_id") == run_id
    ]
    gateway_calls = [
        event
        for event in probe_events
        if event.get("phase") == "canonical_gateway_call"
        and event.get("run_id") == run_id
    ]
    watched_trace_types = {
        "desktop_lease_requested",
        "desktop_lease_bound",
        "vnext_gui_observation",
    }
    trace_counts = {name: 0 for name in sorted(watched_trace_types)}
    trace_errors: list[str] = []
    for trace_id in result.get("bound_trace_ids") or []:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            trace_errors.append(f"{path.name}: missing")
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                trace_errors.append(f"{path.name}:{line_number}: invalid JSON")
                continue
            if not isinstance(event, dict) or str(event.get("run_id") or "") != run_id:
                continue
            event_type = str(event.get("type") or "")
            if event_type in trace_counts:
                trace_counts[event_type] += 1
    gui = result.get("gui_execution_trace") or {}
    execution = result.get("execution_evidence") or {}
    applied = list(gui.get("applied_gui_actions") or [])
    forbidden = list(gui.get("forbidden_events") or [])
    gui_errors = list(gui.get("parse_errors") or [])
    tool_attempts = list(execution.get("tool_attempts") or [])
    passed = bool(
        run_id
        and not errors
        and not trace_errors
        and not raw_connector_calls
        and not gateway_calls
        and not applied
        and not forbidden
        and not gui_errors
        and not tool_attempts
        and not any(trace_counts.values())
    )
    return {
        "source": "test_only_text_turn_side_effect_oracle",
        "passed": passed,
        "raw_connector_call_count": len(raw_connector_calls),
        "gateway_call_count": len(gateway_calls),
        "applied_gui_actions": applied,
        "forbidden_gui_event_count": len(forbidden),
        "runtime_tool_attempt_count": len(tool_attempts),
        "trace_event_counts": trace_counts,
        "parse_errors": [*errors, *trace_errors, *gui_errors],
    }


def _runtime_json(
    driver: client_harness.ElectronClientDriver,
    path: str,
    *,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{driver._runtime_url}{path}",  # noqa: SLF001 - test-only audit channel
        headers={
            "Authorization": (
                f"Bearer {driver._runtime_token}"  # noqa: SLF001
            )
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("runtime audit response is not an object")
    return value


def thread_evidence(
    driver: client_harness.ElectronClientDriver,
    result: dict[str, Any],
    *,
    expected_thread_id: str = "",
    expected_run_ids: tuple[str, ...] = (),
    expected_result: str | None = None,
    require_result: bool = True,
) -> dict[str, Any]:
    run_id = str((result.get("result") or {}).get("run_id") or "")
    errors: list[str] = []
    try:
        detail = _runtime_json(
            driver,
            f"/api/v2/arp/runs/{urllib.parse.quote(run_id, safe='')}",
        ).get("run")
        detail = detail if isinstance(detail, dict) else {}
        thread_id = str(detail.get("thread_id") or "")
        turn_id = str(detail.get("turn_id") or "")
        snapshot = _runtime_json(
            driver,
            f"/api/v2/threads/{urllib.parse.quote(thread_id, safe='')}",
        ).get("thread")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        turns = [
            item for item in snapshot.get("turns") or []
            if isinstance(item, dict)
        ]
    except Exception as error:
        return {
            "source": "server_owned_thread_snapshot",
            "passed": False,
            "errors": [f"{type(error).__name__}: {error}"],
        }
    matching = next(
        (
            item for item in turns
            if str(item.get("run_id") or "") == run_id
            and str(item.get("turn_id") or "") == turn_id
        ),
        None,
    )
    if not run_id or not thread_id or not turn_id or matching is None:
        errors.append("run/thread/turn binding is incomplete")
    if expected_thread_id and thread_id != expected_thread_id:
        errors.append("frontend did not preserve the same server Thread")
    observed_run_ids = [str(item.get("run_id") or "") for item in turns]
    required = [*expected_run_ids, run_id]
    if required and observed_run_ids[-len(required):] != required:
        errors.append("server Thread run order differs from submitted order")
    if len(set(observed_run_ids)) != len(observed_run_ids):
        errors.append("server Thread contains duplicate run ids")
    result_text = str((matching or {}).get("result") or "")
    if require_result and not result_text.strip():
        errors.append("server Thread recorded an empty Agent response")
    if expected_result is not None and result_text != expected_result:
        errors.append("Agent response does not exactly match connector value")
    status = str((matching or {}).get("status") or "")
    if require_result and status not in {"done", "reply"}:
        errors.append(f"Thread turn is not terminal-success: {status or '<empty>'}")
    return {
        "source": "server_owned_thread_snapshot",
        "passed": not errors,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "run_id": run_id,
        "turn_count": len(turns),
        "response_chars": len(result_text),
        "response_sha256": _sha256_text(result_text),
        "exact_result_match": (
            result_text == expected_result if expected_result is not None else None
        ),
        "errors": errors,
    }


def _case_spec(task: str, *, timeout: int, expected: str = "terminal") -> dict[str, Any]:
    return {
        "task": task,
        "max_steps": 30,
        "timeout": int(timeout),
        "runtime_version": "vnext",
        "expected": expected,
    }


def chat_prompt(index: int) -> str:
    if index < 0:
        raise ValueError("chat turn index must be non-negative")
    prompt = CHAT_PROMPTS[index % len(CHAT_PROMPTS)]
    cycle = index // len(CHAT_PROMPTS)
    return prompt if cycle == 0 else f"第 {index + 1} 轮：{prompt}"


def contextual_chat_prompt(
    index: int,
    total_turns: int,
    marker: str,
) -> tuple[str, str | None, str]:
    """Place one late, short-range context probe in an otherwise natural chat."""

    if total_turns >= 3 and index == total_turns - 3:
        return (
            f"我临时把今晚散步的代号记作 {marker}，请简短确认你记住了。",
            None,
            "marker_introduction",
        )
    if total_turns >= 3 and index == total_turns - 1:
        return (
            "我刚才给今晚散步起的代号是什么？只回复代号。",
            marker,
            "marker_recall",
        )
    return chat_prompt(index), None, "ordinary_chat"


@dataclass
class ThreadSequence:
    thread_id: str = ""
    run_ids: list[str] = field(default_factory=list)

    @property
    def last_run_id(self) -> str:
        return self.run_ids[-1] if self.run_ids else ""

    def accept(self, verdict: dict[str, Any]) -> None:
        if verdict.get("passed") is not True:
            raise RuntimeError("cannot advance Thread state from a failed verdict")
        thread_id = str(verdict.get("thread_id") or "")
        run_id = str(verdict.get("run_id") or "")
        if self.thread_id and thread_id != self.thread_id:
            raise RuntimeError("Thread changed while accepting a turn")
        self.thread_id = thread_id
        self.run_ids.append(run_id)


def _run_thread_case(
    name: str,
    task: str,
    *,
    output_root: Path,
    identity: client_harness.ClientIdentity,
    sequence: ThreadSequence,
    timeout: int,
    expected: str = "terminal",
    expected_result: str | None = None,
    extra_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    completion_probe: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    before_submit: Callable[
        [client_harness.ElectronClientDriver, dict[str, Any]],
        dict[str, Any] | None,
    ]
    | None = None,
    require_thread_result: bool = True,
) -> dict[str, Any]:
    driver_box: dict[str, client_harness.ElectronClientDriver] = {}

    def capture_driver(
        driver: client_harness.ElectronClientDriver,
        client_result: dict[str, Any],
    ) -> dict[str, Any]:
        driver_box["driver"] = driver
        extra = before_submit(driver, client_result) if before_submit else None
        return {
            "client_window": driver.window_lifecycle_state(),
            **(dict(extra or {})),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        thread = thread_evidence(
            driver_box["driver"],
            result,
            expected_thread_id=sequence.thread_id,
            expected_run_ids=tuple(sequence.run_ids),
            expected_result=expected_result,
            require_result=require_thread_result,
        )
        extra = dict(extra_verifier(result) or {}) if extra_verifier else {
            "passed": True,
            "source": "no_additional_oracle_required",
        }
        passed = thread.get("passed") is True and extra.get("passed") is True
        return {
            "source": ORACLE_SOURCE,
            "passed": passed,
            "status": "passed" if passed else "failed",
            "thread": thread,
            "external": extra,
        }

    # The base harness prints its full local diagnostic report, which may
    # contain the random connector value. Keep it in the private artifact tree.
    with contextlib.redirect_stdout(io.StringIO()):
        result = client_harness.run_case(
            name,
            _case_spec(task, timeout=timeout, expected=expected),
            output_root=output_root,
            reset_session=not bool(sequence.run_ids),
            before_submit=capture_driver,
            independent_verifier=verify,
            independent_completion_probe=completion_probe,
            required_context_run_id=sequence.last_run_id,
            expected_client_identity=identity,
        )
    verdict = (result.get("task_verdict") or {}).get("thread") or {}
    if result.get("passed") is True:
        sequence.accept(verdict)
    return result


def _public_case(result: dict[str, Any]) -> dict[str, Any]:
    verdict = result.get("task_verdict") or {}
    thread = verdict.get("thread") or {}
    external = verdict.get("external") or {}
    run = result.get("result") or {}
    before_submit = result.get("before_submit_handoff") or {}
    execution = result.get("execution_evidence") or {}
    return {
        "name": result.get("name"),
        "passed": result.get("passed") is True,
        "run_id": run.get("run_id"),
        "outcome": run.get("outcome"),
        "status": run.get("status"),
        "duration_seconds": result.get("duration_seconds"),
        "thread_id": thread.get("thread_id"),
        "turn_id": thread.get("turn_id"),
        "turn_count": thread.get("turn_count"),
        "response_chars": thread.get("response_chars"),
        "response_sha256": thread.get("response_sha256"),
        "exact_result_match": thread.get("exact_result_match"),
        "task_sha256": _sha256_text(str(result.get("task") or "")),
        "window_lifecycle": {
            "before_submit": before_submit.get("client_window"),
            "before_harness_restore": result.get(
                "client_terminal_before_harness_restore"
            ),
        },
        "terminal_evidence": {
            "events": list(execution.get("terminal_events") or []),
            "parse_errors": list(execution.get("parse_errors") or []),
        },
        "external": external,
        "usage": usage_evidence(result),
        "runner_error": result.get("runner_error"),
    }


def usage_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Project cache/token telemetry from the run trace without model text."""

    run_id = str((result.get("result") or {}).get("run_id") or "")
    latest: dict[str, Any] = {}
    event_count = 0
    errors: list[str] = []
    for trace_id in result.get("bound_trace_ids") or []:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            errors.append(f"{path.name}: {type(error).__name__}")
            continue
        for line_number, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"{path.name}:{line_number}: invalid JSON")
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "agent_usage"
                and str(event.get("run_id") or "") == run_id
            ):
                total = event.get("total")
                if isinstance(total, dict):
                    latest = total
                    event_count += 1
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "cache_creation_tokens",
        "reasoning_tokens",
    )
    return {
        "observed": bool(event_count),
        "event_count": event_count,
        "total": {key: int(latest.get(key) or 0) for key in fields},
        "parse_errors": errors,
    }


def aggregate_usage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "prompt_tokens",
        "completion_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "cache_creation_tokens",
        "reasoning_tokens",
    )
    totals = {key: 0 for key in fields}
    observed_cases = 0
    event_count = 0
    for case in cases:
        usage = case.get("usage") or {}
        if usage.get("observed") is True:
            observed_cases += 1
        event_count += int(usage.get("event_count") or 0)
        current = usage.get("total") or {}
        for key in fields:
            totals[key] += int(current.get(key) or 0)
    return {
        "observed_case_count": observed_cases,
        "event_count": event_count,
        "totals": totals,
    }


def _workspace_electron_pids() -> list[int]:
    root = str(WORKSPACE.resolve()).casefold()
    found: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            if str(process.info.get("name") or "").casefold() != "electron.exe":
                continue
            command = " ".join(process.info.get("cmdline") or []).casefold()
            cwd = str(process.info.get("cwd") or "").casefold()
            if root in command or cwd == root or cwd.startswith(root + os.sep):
                found.append(int(process.info["pid"]))
        except (psutil.Error, OSError):
            continue
    return sorted(set(found))


def _terminate_owned_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        root = psutil.Process(process.pid)
        owned = root.children(recursive=True) + [root]
    except psutil.Error:
        process.terminate()
        return
    for item in reversed(owned):
        try:
            item.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(owned, timeout=8)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def _url_ready(url: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=1.0) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _workspace_vite_listener() -> dict[str, Any]:
    """Bind a reused dev server to this workspace, or fail closed."""

    listeners: list[int] = []
    try:
        connections = psutil.net_connections(kind="tcp")
    except psutil.Error as error:
        return {"passed": False, "error": f"{type(error).__name__}: {error}"}
    for connection in connections:
        address = connection.laddr
        port = int(getattr(address, "port", 0) or 0)
        if port == 5200 and connection.status == psutil.CONN_LISTEN and connection.pid:
            listeners.append(int(connection.pid))
    if len(set(listeners)) != 1:
        return {
            "passed": False,
            "error": "port 5200 does not have one identifiable listener",
            "listener_pids": sorted(set(listeners)),
        }
    pid = listeners[0]
    try:
        process = psutil.Process(pid)
        cwd = Path(process.cwd()).resolve()
        command = " ".join(process.cmdline()).casefold()
    except (psutil.Error, OSError) as error:
        return {
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "listener_pids": [pid],
        }
    expected = WORKSPACE.resolve()
    passed = bool(cwd == expected and "vite" in command)
    return {
        "passed": passed,
        "listener_pids": [pid],
        "workspace_cwd": cwd == expected,
        "vite_command": "vite" in command,
        **({} if passed else {"error": "port 5200 is not this workspace's Vite"}),
    }


@dataclass
class ManagedClient:
    process: subprocess.Popen[Any]
    identity: client_harness.ClientIdentity
    driver: client_harness.ElectronClientDriver
    electron_log: Any
    vite_process: subprocess.Popen[Any] | None = None
    vite_log: Any | None = None

    def close(self) -> None:
        _terminate_owned_process(self.process)
        _terminate_owned_process(self.vite_process)
        for stream in (self.electron_log, self.vite_log):
            if stream is not None:
                stream.close()


def launch_managed_client(
    directory: Path,
    fixture: FixtureEnvironment,
) -> ManagedClient:
    """Cold-start a visible client with the fixture config in its environment."""

    existing = _workspace_electron_pids()
    if existing:
        raise RuntimeError(
            "connector E2E requires no pre-existing workspace Electron; "
            f"found PIDs {existing}"
        )
    if (
        not ELECTRON_EXE.is_file()
        or not VITE_SCRIPT.is_file()
        or not TSC_SCRIPT.is_file()
    ):
        raise RuntimeError("Electron/Vite workspace dependencies are unavailable")
    directory.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is unavailable")
    with (directory / "main-compile.log").open("xb") as compile_log:
        compiled = subprocess.run(
            [node, str(TSC_SCRIPT), "-p", "tsconfig.main.json"],
            cwd=str(WORKSPACE),
            stdin=subprocess.DEVNULL,
            stdout=compile_log,
            stderr=subprocess.STDOUT,
            timeout=120,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            check=False,
        )
    if compiled.returncode != 0:
        raise RuntimeError("current Electron main sources did not compile")
    vite_process: subprocess.Popen[Any] | None = None
    vite_log = None
    if not _url_ready("http://127.0.0.1:5200"):
        vite_log = (directory / "vite.log").open("xb")
        vite_process = subprocess.Popen(
            [node, str(VITE_SCRIPT), "--host", "127.0.0.1", "--port", "5200"],
            cwd=str(WORKSPACE),
            # Vite exits when its stdin reaches EOF. Keep a private pipe open
            # for the lifetime of this owned dev server.
            stdin=subprocess.PIPE,
            stdout=vite_log,
            stderr=subprocess.STDOUT,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not _url_ready("http://127.0.0.1:5200"):
            if vite_process.poll() is not None:
                break
            time.sleep(0.25)
        if not _url_ready("http://127.0.0.1:5200"):
            _terminate_owned_process(vite_process)
            vite_log.close()
            raise RuntimeError("managed Vite server did not become ready")
    vite_audit = _workspace_vite_listener()
    if vite_audit.get("passed") is not True:
        _terminate_owned_process(vite_process)
        if vite_log is not None:
            vite_log.close()
        raise RuntimeError(f"renderer dev-server identity is invalid: {vite_audit}")

    electron_log = (directory / "electron.log").open("xb")
    environment = dict(os.environ)
    environment["WINPILOT_MCP_CONFIG"] = str(fixture.config_path.resolve())
    environment["WINPILOT_CONNECTOR_E2E_PROBE"] = str(
        fixture.probe_path.resolve()
    )
    inherited_pythonpath = str(environment.get("PYTHONPATH") or "")
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (
            str(fixture.probe_module_dir.resolve()),
            inherited_pythonpath,
        )
        if item
    )
    environment["ELECTRON_DISABLE_SECURITY_WARNINGS"] = "true"
    try:
        process = subprocess.Popen(
            [str(ELECTRON_EXE.resolve()), "."],
            cwd=str(WORKSPACE),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=electron_log,
            stderr=subprocess.STDOUT,
            creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        )
        deadline = time.monotonic() + 150
        driver = None
        last_error = ""
        while time.monotonic() < deadline and process.poll() is None:
            try:
                candidate = client_harness.ElectronClientDriver(
                    directory / "electron-bootstrap"
                )
                if candidate.identity.pid != process.pid:
                    raise RuntimeError("visible Electron is not the managed process")
                driver = candidate
                break
            except Exception as error:
                last_error = f"{type(error).__name__}: {error}"
                time.sleep(0.25)
        if driver is None:
            raise RuntimeError(f"managed Electron did not become ready: {last_error}")
        runtime_process = None
        for child in psutil.Process(process.pid).children(recursive=True):
            try:
                command = child.cmdline()
            except psutil.Error:
                continue
            if any(Path(arg).name.casefold() == "serve.py" for arg in command):
                runtime_process = child
                break
        inherited = (
            str(runtime_process.environ().get("WINPILOT_MCP_CONFIG") or "")
            if runtime_process is not None
            else ""
        )
        if Path(inherited).resolve() != fixture.config_path.resolve():
            raise RuntimeError("managed runtime did not inherit the temporary MCP config")
        return ManagedClient(
            process=process,
            identity=driver.identity,
            driver=driver,
            electron_log=electron_log,
            vite_process=vite_process,
            vite_log=vite_log,
        )
    except Exception:
        if "process" in locals():
            _terminate_owned_process(process)
        electron_log.close()
        _terminate_owned_process(vite_process)
        if vite_log is not None:
            vite_log.close()
        raise


def _notepad_windows() -> dict[int, int]:
    values: dict[int, int] = {}

    def collect(hwnd: int, _extra: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            name = psutil.Process(pid).name().casefold()
        except psutil.Error:
            return True
        if name == "notepad.exe":
            values[int(hwnd)] = int(pid)
        return True

    win32gui.EnumWindows(collect, None)
    return values


@dataclass
class OwnedNotepad:
    process: subprocess.Popen[Any]
    hwnd: int
    process_id: int

    def close(self) -> dict[str, Any]:
        try:
            if win32gui.IsWindow(self.hwnd):
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            deadline = time.monotonic() + 5
            while win32gui.IsWindow(self.hwnd) and time.monotonic() < deadline:
                time.sleep(0.1)
            window_closed = not win32gui.IsWindow(self.hwnd)
            remaining = _notepad_windows()
            shared_window_count = sum(
                process_id == self.process_id for process_id in remaining.values()
            )
            forced_process_termination = False
            if (
                not window_closed
                and self.process.pid == self.process_id
                and shared_window_count == 1
            ):
                _terminate_owned_process(self.process)
                forced_process_termination = True
                deadline = time.monotonic() + 3
                while win32gui.IsWindow(self.hwnd) and time.monotonic() < deadline:
                    time.sleep(0.1)
                window_closed = not win32gui.IsWindow(self.hwnd)
            elif window_closed and (
                self.process.pid != self.process_id or shared_window_count == 0
            ):
                _terminate_owned_process(self.process)
            return {
                "passed": window_closed,
                "window_closed": window_closed,
                "forced_process_termination": forced_process_termination,
                "shared_process_window_count": shared_window_count,
            }
        except Exception as error:
            return {
                "passed": False,
                "window_closed": False,
                "error": f"{type(error).__name__}: {error}",
            }


def launch_notepad(path: Path | None = None) -> OwnedNotepad:
    before = set(_notepad_windows())
    command = ["notepad.exe"]
    if path is not None:
        command.append(str(path.resolve()))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        windows = _notepad_windows()
        fresh = [hwnd for hwnd in windows if hwnd not in before]
        if path is not None:
            expected_name = path.name.casefold()
            fresh = [
                hwnd
                for hwnd in fresh
                if expected_name in win32gui.GetWindowText(hwnd).casefold()
            ]
        if fresh:
            hwnd = fresh[0]
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return OwnedNotepad(process, hwnd, windows[hwnd])
        if process.poll() is not None:
            break
        time.sleep(0.2)
    _terminate_owned_process(process)
    raise RuntimeError("a fresh owned Notepad window did not appear")


def _read_text_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError("saved file is not UTF-8 or UTF-16 text")


def file_evidence(
    path: Path,
    fixture: FixtureEnvironment,
    *,
    after_time_ns: int,
) -> dict[str, Any]:
    invocation = read_invocation_evidence(fixture, after_time_ns=after_time_ns)
    try:
        text, encoding = _read_text_file(path)
        raw = path.read_bytes()
        exact = text == fixture.token
        modified_after_start = path.stat().st_mtime_ns >= after_time_ns
        file_part = {
            "exists": True,
            "exact_value": exact,
            "modified_after_start": modified_after_start,
            "encoding": encoding,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    except Exception as error:
        file_part = {
            "exists": path.is_file(),
            "exact_value": False,
            "modified_after_start": False,
            "error": f"{type(error).__name__}: {error}",
        }
    passed = bool(
        invocation.get("passed") is True
        and file_part.get("exact_value") is True
        and file_part.get("modified_after_start") is True
    )
    return {
        "source": "append_only_connector_log_and_saved_file",
        "passed": passed,
        "invocation": invocation,
        "file": file_part,
    }


def stable_runtime_identity(
    driver: client_harness.ElectronClientDriver,
) -> dict[str, Any]:
    """Project only stable, non-secret fields used to compare cold-start arms."""

    status = _runtime_json(driver, "/api/status")
    runtime = status.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    git = runtime.get("git")
    git = git if isinstance(git, dict) else {}
    source = runtime.get("source")
    source = source if isinstance(source, dict) else {}
    vnext = status.get("vnext")
    vnext = vnext if isinstance(vnext, dict) else {}
    model_profile = status.get("model_profile")
    return {
        "model_profile": (
            dict(model_profile) if isinstance(model_profile, dict) else {}
        ),
        "runtime_contract_version": runtime.get("contract_version"),
        "runtime_git": {
            "branch": git.get("branch"),
            "loaded_commit": git.get("loaded_commit"),
            "current_commit": git.get("current_commit"),
            "dirty": git.get("dirty"),
        },
        "runtime_source": {
            "loaded_stamp": source.get("loaded_stamp"),
            "current_stamp": source.get("current_stamp"),
            "drifted": source.get("drifted"),
        },
        "vnext": {
            "live_canary_enabled": vnext.get("live_canary_enabled"),
            "budget": dict(vnext.get("budget") or {}),
        },
    }


def _run_connector_to_notepad_case(
    *,
    name: str,
    task: str,
    output_file: Path,
    fixture: FixtureEnvironment,
    output_root: Path,
    identity: client_harness.ClientIdentity,
    sequence: ThreadSequence,
    timeout: int,
    preexisting_text: str | None = None,
) -> dict[str, Any]:
    """Run one complete connector-to-Notepad turn and wait for Agent terminal."""

    if preexisting_text is None:
        output_file.unlink(missing_ok=True)
        document_setup = {"mode": "blank_untitled"}
        notepad = launch_notepad()
    else:
        if preexisting_text == fixture.token:
            raise ValueError("preexisting Notepad text must differ from target")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(preexisting_text, encoding="utf-8")
        initial_raw = output_file.read_bytes()
        document_setup = {
            "mode": "preopened_existing_file",
            "initial_bytes": len(initial_raw),
            "initial_sha256": hashlib.sha256(initial_raw).hexdigest(),
            "initial_differs_from_target": True,
        }
        notepad = launch_notepad(output_file)
    notepad_started_ns = time.time_ns()
    try:

        def notepad_before(
            driver: client_harness.ElectronClientDriver,
            _result: dict[str, Any],
        ) -> dict[str, Any]:
            if not win32gui.IsWindow(notepad.hwnd):
                raise RuntimeError("owned Notepad closed before submission")
            notepad_visible = bool(win32gui.IsWindowVisible(notepad.hwnd))
            electron_foreground = win32gui.GetForegroundWindow() == driver.identity.hwnd
            if preexisting_text is not None:
                current_raw = output_file.read_bytes()
                current_sha256 = hashlib.sha256(current_raw).hexdigest()
                title_matches = output_file.name.casefold() in win32gui.GetWindowText(
                    notepad.hwnd
                ).casefold()
                precondition_passed = bool(
                    current_sha256 == document_setup["initial_sha256"]
                    and title_matches
                )
                document_setup.update(
                    {
                        "pre_submit_sha256": current_sha256,
                        "title_matches_document": title_matches,
                        "precondition_passed": precondition_passed,
                    }
                )
                if not precondition_passed:
                    raise RuntimeError("prepared Notepad document changed before submit")
            if not notepad_visible or not electron_foreground:
                raise RuntimeError(
                    "Notepad was not visibly prepositioned behind Electron"
                )
            return {
                "notepad_hwnd": notepad.hwnd,
                "notepad_process_id": notepad.process_id,
                "notepad_visible": notepad_visible,
                "electron_foreground": electron_foreground,
                "target_prepositioned_behind_client": True,
            }

        def notepad_verifier(result: dict[str, Any]) -> dict[str, Any]:
            evidence = file_evidence(
                output_file,
                fixture,
                after_time_ns=notepad_started_ns,
            )
            gui_trace = result.get("gui_execution_trace") or {}
            actions = list(gui_trace.get("applied_gui_actions") or [])
            input_path = bool(
                gui_trace.get("passed") is True
                and "type_text" in actions
                and set(actions) & {"click", "double_click", "hotkey"}
            )
            evidence["gui_input"] = {
                "passed": input_path,
                "trace_passed": gui_trace.get("passed") is True,
                "applied_actions": actions,
                "required": "type_text plus click/double_click/hotkey",
            }
            evidence["document_setup"] = document_setup
            evidence["passed"] = bool(
                evidence.get("passed") is True and input_path
            )
            return evidence

        result = _run_thread_case(
            name,
            task,
            output_root=output_root,
            identity=identity,
            sequence=sequence,
            timeout=timeout,
            expected="terminal",
            extra_verifier=notepad_verifier,
            before_submit=notepad_before,
            require_thread_result=True,
        )
    finally:
        cleanup = notepad.close()
    verdict = result.get("task_verdict") or {}
    external = verdict.get("external") or {}
    external["notepad_cleanup"] = cleanup
    verdict["external"] = external
    result["task_verdict"] = verdict
    return result


def run_thread_connector_e2e(
    *,
    output_root: Path,
    chat_turns: int = 20,
    timeout: int = 180,
    include_notepad: bool = True,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suite_dir = output_root / f"{timestamp}-thread-connector"
    private_dir = suite_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    sequence = ThreadSequence()
    public_cases: list[dict[str, Any]] = []
    conversation_marker = f"walk_{secrets.token_hex(5)}"
    context_probe_passed = False
    managed: ManagedClient | None = None
    failure = ""
    with tempfile.TemporaryDirectory(
        prefix="connector-runtime-",
        dir=str(suite_dir),
    ) as temp_name:
        fixture = build_fixture_environment(Path(temp_name))
        try:
            managed = launch_managed_client(private_dir / "managed-client", fixture)
            for index in range(max(0, int(chat_turns))):
                prompt, expected_result, chat_kind = contextual_chat_prompt(
                    index,
                    int(chat_turns),
                    conversation_marker,
                )
                result = _run_thread_case(
                    f"chat-{index + 1:02d}",
                    prompt,
                    output_root=private_dir / "client",
                    identity=managed.identity,
                    sequence=sequence,
                    timeout=timeout,
                    expected_result=expected_result,
                )
                public_case = _public_case(result)
                public_case["chat_kind"] = chat_kind
                public_cases.append(public_case)
                if chat_kind == "marker_recall":
                    context_probe_passed = bool(
                        result.get("passed") is True
                        and public_case.get("exact_result_match") is True
                    )
                print(
                    json.dumps(
                        {
                            "event": "chat_turn_finished",
                            "turn": index + 1,
                            "passed": result.get("passed") is True,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if result.get("passed") is not True:
                    raise RuntimeError(f"chat turn {index + 1} failed")

            connector_started_ns = time.time_ns()

            def connector_verifier(_result: dict[str, Any]) -> dict[str, Any]:
                return read_invocation_evidence(
                    fixture,
                    after_time_ns=connector_started_ns,
                )

            connector_result = _run_thread_case(
                "connector-read",
                connector_read_task(fixture.server_name),
                output_root=private_dir / "client",
                identity=managed.identity,
                sequence=sequence,
                timeout=timeout,
                expected_result=fixture.token,
                extra_verifier=connector_verifier,
            )
            public_cases.append(_public_case(connector_result))
            if connector_result.get("passed") is not True:
                raise RuntimeError("connector exact-read turn failed")

            if include_notepad:
                output_file = (
                    Path.home()
                    / f"cne2e-{secrets.token_hex(3)}.txt"
                )
                try:
                    notepad_result = _run_connector_to_notepad_case(
                        name="connector-to-notepad",
                        task=connector_to_notepad_task(fixture, output_file),
                        output_file=output_file,
                        fixture=fixture,
                        output_root=private_dir / "client",
                        identity=managed.identity,
                        sequence=sequence,
                        timeout=timeout,
                    )
                    public_cases.append(_public_case(notepad_result))
                    if notepad_result.get("passed") is not True:
                        raise RuntimeError("connector-to-Notepad turn failed")
                finally:
                    output_file.unlink(missing_ok=True)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
        finally:
            if managed is not None:
                managed.close()

        report = {
            "kind": "visible_electron_thread_connector_e2e",
            "entrypoint": "electron_client_windows_uia",
            "submission_transport": "windows_sendinput_and_native_mouse",
            "temporary_mcp_config": True,
            "fixture": {
                "server_count": 1,
                "tool_count": 1,
                "read_only_annotated": True,
                "value_sha256": fixture.value_sha256,
            },
            "chat_turns_requested": int(chat_turns),
            "same_thread_id": sequence.thread_id,
            "accepted_run_count": len(sequence.run_ids),
            "unique_run_count": len(set(sequence.run_ids)),
            "context_probe": {
                "enabled": int(chat_turns) >= 3,
                "marker_sha256": _sha256_text(conversation_marker),
                "retained_history_distance_turns": 2 if int(chat_turns) >= 3 else None,
                "passed": context_probe_passed if int(chat_turns) >= 3 else None,
            },
            "usage": aggregate_usage(public_cases),
            "cases": public_cases,
            "private_artifacts": {
                "directory": "private",
                "classification": "local_private_do_not_publish",
            },
            "failure": failure,
            "passed": bool(
                not failure
                and len(public_cases) == int(chat_turns) + 1 + int(include_notepad)
                and all(item.get("passed") is True for item in public_cases)
                and len(sequence.run_ids) == len(public_cases)
                and (int(chat_turns) < 3 or context_probe_passed)
            ),
            "finished_at": datetime.now().astimezone().isoformat(),
        }
    report_path = suite_dir / "report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    return report


def _wait_for_runtime_idle(
    driver: client_harness.ElectronClientDriver,
    *,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    last: dict[str, Any] = {}
    error = ""
    while time.monotonic() <= deadline:
        try:
            last = _runtime_json(driver, "/api/status")
            if (
                last.get("running") is False
                and last.get("arp_running") is False
                and last.get("agent_running") is False
            ):
                return {
                    "passed": True,
                    "running": False,
                    "arp_running": False,
                    "agent_running": False,
                }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    return {
        "passed": False,
        "running": last.get("running"),
        "arp_running": last.get("arp_running"),
        "agent_running": last.get("agent_running"),
        "error": error or "runtime remained active after target turn",
    }


def _target_outcome(case: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(case, dict):
        return {
            "external_task_pass": False,
            "self_terminal_pass": False,
            "evidence_complete": False,
            "full_pass": False,
        }
    external = case.get("external") or {}
    usage = case.get("usage") or {}
    windows = case.get("window_lifecycle") or {}
    terminal = case.get("terminal_evidence") or {}
    invocation = external.get("invocation") or {}
    connector_chain = case.get("connector_chain") or {}
    history_projection = case.get("history_projection") or {}
    file_part = external.get("file") or {}
    gui_input = external.get("gui_input") or {}
    before_submit = windows.get("before_submit") or {}
    before_restore = windows.get("before_harness_restore") or {}
    external_task_pass = external.get("passed") is True
    self_terminal_pass = bool(
        case.get("outcome") == "terminal"
        and case.get("status") == "done"
        and "vnext_canary_done" in (terminal.get("events") or [])
        and not terminal.get("parse_errors")
    )
    evidence_complete = bool(
        usage.get("observed") is True
        and not usage.get("parse_errors")
        and invocation.get("passed") is True
        and invocation.get("physical_arguments_sha256")
        and connector_chain.get("passed") is True
        and history_projection.get("passed") is True
        and file_part.get("exists") is True
        and file_part.get("modified_after_start") is True
        and gui_input.get("trace_passed") is True
        and before_submit.get("visible") is True
        and before_submit.get("minimized") is False
        and before_submit.get("foreground") is True
        and before_restore.get("visible") is True
        and before_restore.get("minimized") is False
    )
    return {
        "external_task_pass": external_task_pass,
        "self_terminal_pass": self_terminal_pass,
        "evidence_complete": evidence_complete,
        "full_pass": bool(
            case.get("passed") is True
            and external_task_pass
            and self_terminal_pass
            and evidence_complete
        ),
    }


def _run_history_depth_arm(
    *,
    arm_name: str,
    fixture: FixtureEnvironment,
    task: str,
    output_file: Path,
    initial_document: str,
    history_prompt_indexes: tuple[int, ...],
    output_root: Path,
    timeout: int,
) -> dict[str, Any]:
    arm_dir = output_root / arm_name
    private_dir = arm_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    sequence = ThreadSequence()
    public_chats: list[dict[str, Any]] = []
    target_case: dict[str, Any] | None = None
    runtime_identity: dict[str, Any] = {}
    managed: ManagedClient | None = None
    idle = {"passed": False, "error": "target turn was not started"}
    failure = ""
    client_pid = 0
    control_fingerprints = {
        "task_sha256": _sha256_text(task),
        "fixture_config_sha256": hashlib.sha256(
            fixture.config_path.read_bytes()
        ).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(
            FIXTURE_SCRIPT.read_bytes()
        ).hexdigest(),
        "output_path_sha256": _sha256_text(str(output_file.resolve())),
        "initial_document_sha256": _sha256_text(initial_document),
    }
    try:
        managed = launch_managed_client(private_dir / "managed-client", fixture)
        client_pid = int(managed.identity.pid)
        managed.driver.wait_for_runtime_ready()
        runtime_identity = stable_runtime_identity(managed.driver)
        for position, prompt_index in enumerate(history_prompt_indexes, start=1):
            result = _run_thread_case(
                f"history-chat-{position:02d}",
                chat_prompt(prompt_index),
                output_root=private_dir / "client",
                identity=managed.identity,
                sequence=sequence,
                timeout=timeout,
            )
            public_chat = _public_case(result)
            public_chat["text_only_evidence"] = history_text_only_evidence(
                result,
                fixture,
            )
            public_chats.append(public_chat)
            print(
                json.dumps(
                    {
                        "event": "history_ab_chat_finished",
                        "arm": arm_name,
                        "turn": position,
                        "passed": result.get("passed") is True,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if result.get("passed") is not True:
                raise RuntimeError(f"history chat turn {position} failed")
            if public_chat["text_only_evidence"].get("passed") is not True:
                raise RuntimeError(
                    f"history chat turn {position} used connector or desktop"
                )
        target_result = _run_connector_to_notepad_case(
            name="paired-connector-to-notepad",
            task=task,
            output_file=output_file,
            fixture=fixture,
            output_root=private_dir / "client",
            identity=managed.identity,
            sequence=sequence,
            timeout=timeout,
            preexisting_text=initial_document,
        )
        target_case = _public_case(target_result)
        target_case["connector_chain"] = read_connector_probe_evidence(
            fixture,
            run_id=str(target_case.get("run_id") or ""),
        )
        target_case["history_projection"] = read_history_projection_evidence(
            fixture,
            run_id=str(target_case.get("run_id") or ""),
            task_sha256=str(target_case.get("task_sha256") or ""),
        )
        idle = _wait_for_runtime_idle(managed.driver)
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if managed is not None:
            idle = _wait_for_runtime_idle(managed.driver)
    finally:
        output_file.unlink(missing_ok=True)
        if managed is not None:
            managed.close()
    process_cleanup = {
        "passed": bool(
            managed is not None
            and managed.process.poll() is not None
            and client_pid not in _workspace_electron_pids()
        ),
        "managed_client_exited": bool(
            managed is not None and managed.process.poll() is not None
        ),
        "workspace_client_pid_absent": client_pid not in _workspace_electron_pids(),
    }
    outcome = _target_outcome(target_case)
    notepad_cleanup = (
        (((target_case or {}).get("external") or {}).get("notepad_cleanup")) or {}
    )
    test_cleanup_pass = bool(target_case and notepad_cleanup.get("passed") is True)
    arm = {
        "arm": arm_name,
        "cold_start": True,
        "client_pid": client_pid,
        "frontend_history_turns": len(history_prompt_indexes),
        "history_prompt_order_sha256": _sha256_text(
            json.dumps(list(history_prompt_indexes), separators=(",", ":"))
        ),
        "runtime_identity": runtime_identity,
        "control_fingerprints": control_fingerprints,
        "thread_id": (
            str((target_case or {}).get("thread_id") or "")
            or sequence.thread_id
        ),
        "accepted_run_count": len(sequence.run_ids),
        "history_cases": public_chats,
        "history_usage": aggregate_usage(public_chats),
        "history_text_only_pass": bool(
            len(public_chats) == len(history_prompt_indexes)
            and all(
                (case.get("text_only_evidence") or {}).get("passed") is True
                for case in public_chats
            )
        ),
        "target": target_case,
        "target_outcome": outcome,
        "test_cleanup_pass": test_cleanup_pass,
        "runtime_idle_after_target": idle,
        "process_cleanup": process_cleanup,
        "failure": failure,
        "passed": bool(
            not failure
            and len(public_chats) == len(history_prompt_indexes)
            and all(case.get("passed") is True for case in public_chats)
            and outcome.get("full_pass") is True
            and test_cleanup_pass
            and idle.get("passed") is True
            and process_cleanup.get("passed") is True
        ),
    }
    _write_json(arm_dir / "report.json", arm)
    return arm


def _history_ab_controls(
    fresh: dict[str, Any],
    history: dict[str, Any],
    *,
    expected: dict[str, str],
    history_turns: int,
) -> dict[str, bool]:
    fresh_target = fresh.get("target") or {}
    history_target = history.get("target") or {}
    fresh_fingerprints = fresh.get("control_fingerprints") or {}
    history_fingerprints = history.get("control_fingerprints") or {}
    fresh_projection = fresh_target.get("history_projection") or {}
    history_projection = history_target.get("history_projection") or {}
    fresh_setup = ((fresh_target.get("external") or {}).get("document_setup") or {})
    history_setup = (
        (history_target.get("external") or {}).get("document_setup") or {}
    )
    return {
        "same_task_sha256": bool(
            fresh_target.get("task_sha256")
            and fresh_target.get("task_sha256")
            == history_target.get("task_sha256")
            == expected["task_sha256"]
            == fresh_fingerprints.get("task_sha256")
            == history_fingerprints.get("task_sha256")
        ),
        "same_fixture_config": bool(
            fresh_fingerprints.get("fixture_config_sha256")
            == history_fingerprints.get("fixture_config_sha256")
            == expected["fixture_config_sha256"]
        ),
        "same_fixture_source": bool(
            fresh_fingerprints.get("fixture_source_sha256")
            == history_fingerprints.get("fixture_source_sha256")
            == expected["fixture_source_sha256"]
        ),
        "same_output_path": bool(
            fresh_fingerprints.get("output_path_sha256")
            == history_fingerprints.get("output_path_sha256")
            == expected["output_path_sha256"]
        ),
        "same_initial_document": bool(
            fresh_fingerprints.get("initial_document_sha256")
            == history_fingerprints.get("initial_document_sha256")
            == expected["initial_document_sha256"]
        ),
        "same_observed_preopened_document": bool(
            fresh_setup.get("mode") == "preopened_existing_file"
            and history_setup.get("mode") == "preopened_existing_file"
            and fresh_setup.get("initial_differs_from_target") is True
            and history_setup.get("initial_differs_from_target") is True
            and fresh_setup.get("precondition_passed") is True
            and history_setup.get("precondition_passed") is True
            and fresh_setup.get("title_matches_document") is True
            and history_setup.get("title_matches_document") is True
            and fresh_setup.get("initial_sha256")
            == history_setup.get("initial_sha256")
            == expected["initial_document_sha256"]
        ),
        "both_notepad_cleanups_passed": bool(
            fresh.get("test_cleanup_pass") is True
            and history.get("test_cleanup_pass") is True
        ),
        "both_arm_postconditions_passed": bool(
            not fresh.get("failure")
            and not history.get("failure")
            and (fresh.get("runtime_idle_after_target") or {}).get("passed") is True
            and (history.get("runtime_idle_after_target") or {}).get("passed")
            is True
            and (fresh.get("process_cleanup") or {}).get("passed") is True
            and (history.get("process_cleanup") or {}).get("passed") is True
        ),
        "same_runtime_model_identity": bool(
            fresh.get("runtime_identity")
            and fresh.get("runtime_identity") == history.get("runtime_identity")
        ),
        "separate_cold_clients": bool(
            fresh.get("client_pid")
            and history.get("client_pid")
            and fresh.get("client_pid") != history.get("client_pid")
        ),
        "fresh_target_is_turn_1": fresh_target.get("turn_count") == 1,
        "history_target_is_expected_turn": (
            history_target.get("turn_count") == history_turns + 1
        ),
        "separate_threads": bool(
            fresh.get("thread_id")
            and history.get("thread_id")
            and fresh.get("thread_id") != history.get("thread_id")
        ),
        "fresh_actual_history_is_empty": bool(
            fresh_projection.get("passed") is True
            and fresh_projection.get("history_message_count") == 0
        ),
        "history_actual_history_is_observed": bool(
            history_projection.get("passed") is True
            and int(history_projection.get("history_message_count") or 0) > 0
        ),
        "history_prefill_is_text_only": (
            history.get("history_text_only_pass") is True
        ),
    }


def _history_ab_cleanup_interference(arms: list[dict[str, Any]]) -> bool:
    for arm in arms:
        target = arm.get("target")
        if not target:
            continue
        cleanup = ((target.get("external") or {}).get("notepad_cleanup")) or {}
        if cleanup.get("passed") is not True:
            return True
    return False


def _history_ab_comparison(
    *,
    fresh_pass: bool,
    history_pass: bool,
    cleanup_interference: bool,
    controls_pass: bool,
) -> str:
    if cleanup_interference:
        return "pair_invalid_test_interference"
    if not controls_pass:
        return "pair_incomplete_before_paired_target"
    if fresh_pass and history_pass:
        return "both_passed_no_negative_history_signal_in_this_pair"
    if fresh_pass and not history_pass:
        return "history_arm_regressed_in_this_pair"
    if not fresh_pass and history_pass:
        return "fresh_arm_failed_history_arm_passed_order_or_sampling_signal"
    return "both_failed_or_pair_incomplete"


def run_history_depth_ab_e2e(
    *,
    output_root: Path,
    history_turns: int = 20,
    timeout: int = 180,
    seed: int | None = None,
    arm_first: str = "random",
) -> dict[str, Any]:
    """Compare a fresh target turn with the same turn after frontend history."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suite_dir = output_root / f"{timestamp}-history-depth-ab"
    private_dir = suite_dir / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    history_turns = max(0, int(history_turns))
    seed = int(seed) if seed is not None else secrets.randbits(64)
    if arm_first not in {"random", "fresh", "history"}:
        raise ValueError("arm_first must be random, fresh, or history")
    prompt_indexes = list(range(history_turns))
    random.Random(seed).shuffle(prompt_indexes)
    selected_arm_first = arm_first
    if arm_first == "random":
        order_digest = hashlib.sha256(f"{seed}:arm-order".encode()).digest()
        selected_arm_first = "fresh" if order_digest[0] % 2 == 0 else "history"
    initial_document = f"cne2e-initial-{_sha256_text(f'{seed}:initial')[:16]}"
    arms: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="connector-history-ab-",
        dir=str(suite_dir),
    ) as temp_name:
        output_file = Path(temp_name) / "current.txt"
        fixture = build_fixture_environment(Path(temp_name))
        task = connector_to_current_notepad_task(fixture)
        fixture_config_sha256 = hashlib.sha256(
            fixture.config_path.read_bytes()
        ).hexdigest()
        fixture_source_sha256 = hashlib.sha256(FIXTURE_SCRIPT.read_bytes()).hexdigest()
        arm_specs = {
            "fresh": ("fresh-thread-first-turn", ()),
            "history": (
                f"frontend-history-{history_turns}-turns",
                tuple(prompt_indexes),
            ),
        }
        order = [
            selected_arm_first,
            "history" if selected_arm_first == "fresh" else "fresh",
        ]
        try:
            for key in order:
                arm_name, indexes = arm_specs[key]
                arm = _run_history_depth_arm(
                    arm_name=arm_name,
                    fixture=fixture,
                    task=task,
                    output_file=output_file,
                    initial_document=initial_document,
                    history_prompt_indexes=indexes,
                    output_root=private_dir,
                    timeout=timeout,
                )
                arms.append(arm)
                cleanup = (
                    (((arm.get("target") or {}).get("external") or {}).get(
                        "notepad_cleanup"
                    ))
                    or {}
                )
                if arm.get("target") and cleanup.get("passed") is not True:
                    break
        finally:
            if fixture.oracle_path.is_file():
                shutil.copy2(
                    fixture.oracle_path,
                    private_dir / "connector-invocations.jsonl",
                )
            if fixture.probe_path.is_file():
                shutil.copy2(
                    fixture.probe_path,
                    private_dir / "connector-probe.jsonl",
                )
            output_file.unlink(missing_ok=True)

        fresh = next(
            (arm for arm in arms if arm.get("arm") == "fresh-thread-first-turn"),
            {},
        )
        history = next(
            (
                arm
                for arm in arms
                if arm.get("arm") == f"frontend-history-{history_turns}-turns"
            ),
            {},
        )
        fresh_target = fresh.get("target") or {}
        history_target = history.get("target") or {}
        expected_fingerprints = {
            "task_sha256": _sha256_text(task),
            "fixture_config_sha256": fixture_config_sha256,
            "fixture_source_sha256": fixture_source_sha256,
            "output_path_sha256": _sha256_text(str(output_file.resolve())),
            "initial_document_sha256": _sha256_text(initial_document),
        }
        controls = _history_ab_controls(
            fresh,
            history,
            expected=expected_fingerprints,
            history_turns=history_turns,
        )
        controls_pass = all(controls.values())
        fresh_pass = (fresh.get("target_outcome") or {}).get("full_pass") is True
        history_pass = (history.get("target_outcome") or {}).get("full_pass") is True
        cleanup_interference = _history_ab_cleanup_interference(arms)
        comparison = _history_ab_comparison(
            fresh_pass=fresh_pass,
            history_pass=history_pass,
            cleanup_interference=cleanup_interference,
            controls_pass=controls_pass,
        )
        report = {
            "kind": "visible_electron_frontend_history_depth_ab",
            "entrypoint": "electron_client_windows_uia",
            "submission_transport": "windows_sendinput_and_native_mouse",
            "arm_order": [arm.get("arm") for arm in arms],
            "requested_arm_first": arm_first,
            "selected_arm_first": selected_arm_first,
            "history_prompt_seed": seed,
            "frontend_history_turns": history_turns,
            "configured_history_limits": {
                "max_messages": 12,
                "max_chars": 16_384,
            },
            "fixture": {
                "server_count": 1,
                "tool_count": 1,
                "read_only_annotated": True,
                "config_sha256": fixture_config_sha256,
                "source_sha256": fixture_source_sha256,
                "value_sha256": fixture.value_sha256,
                "server_name_sha256": _sha256_text(fixture.server_name),
            },
            "task_sha256": _sha256_text(task),
            "output_path_sha256": _sha256_text(str(output_file.resolve())),
            "document_setup": {
                "mode": "preopened_existing_file",
                "initial_document_sha256": _sha256_text(initial_document),
            },
            "controls": controls,
            "controls_pass": controls_pass,
            "arms": arms,
            "notepad_cleanup_interference": cleanup_interference,
            "comparison": comparison,
            "connector_argument_chain": {
                "raw_model_arguments_private_probe": True,
                "canonical_gateway_receipt_private_probe": True,
                "physical_mcp_arguments_hashed": True,
                "runtime_telemetry_changed": False,
            },
            "limitations": [
                "one pair is a pilot, not a causal or statistical result",
                "provider cache, desktop warm-up, and repeated-task order effects require counterbalanced pairs",
                "actual target history comes from the test-only provider-message projection",
                "history content can prime behavior as well as increase depth",
            ],
            "private_artifacts": {
                "directory": "private",
                "classification": "local_private_do_not_publish",
            },
            "passed": bool(
                controls_pass
                and len(arms) == 2
                and all(arm.get("passed") is True for arm in arms)
            ),
            "finished_at": datetime.now().astimezone().isoformat(),
        }
    report_path = suite_dir / "report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-turns", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-notepad", action="store_true")
    parser.add_argument("--history-depth-ab", action="store_true")
    parser.add_argument("--ab-history-turns", type=int, default=20)
    parser.add_argument("--ab-seed", type=int)
    parser.add_argument(
        "--ab-first",
        choices=("random", "fresh", "history"),
        default="random",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.history_depth_ab:
        report = run_history_depth_ab_e2e(
            output_root=args.output_root.resolve(),
            history_turns=max(0, args.ab_history_turns),
            timeout=max(30, args.timeout),
            seed=args.ab_seed,
            arm_first=args.ab_first,
        )
    else:
        report = run_thread_connector_e2e(
            output_root=args.output_root.resolve(),
            chat_turns=max(0, args.chat_turns),
            timeout=max(30, args.timeout),
            include_notepad=not args.skip_notepad,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
