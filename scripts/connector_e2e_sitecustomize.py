"""Test-only connector trace sidecar loaded as ``sitecustomize``.

The Electron E2E runner puts a temporary copy of this file on ``PYTHONPATH``.
It activates only for the managed ``serve.py`` process and records private
raw -> canonical -> receipt evidence without changing model requests, tool
arguments, return values, retries, or Runtime control flow.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


PROBE_ENV = "WINPILOT_CONNECTOR_E2E_PROBE"
PROBE_FORMAT = "connector-e2e-probe/1"
_LOCK = threading.Lock()


def _member(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append(event: dict[str, Any]) -> None:
    raw_path = str(os.environ.get(PROBE_ENV) or "")
    if not raw_path:
        return
    try:
        path = Path(raw_path)
        wire = json.dumps(
            {"format": PROBE_FORMAT, "time_ns": time.time_ns(), **event},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(wire + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        return


def _install() -> None:
    from winpilot.integrations import connector_gateway as gateway_module
    from winpilot.orchestrator import live_agent as live_module
    from winpilot.vnext.model_roles import visual_actor as actor_module

    original_build = live_module._build_actor_resources
    original_propose = actor_module.LiveVisualActor.propose
    original_thread_messages = actor_module._thread_messages
    original_record = actor_module.LiveVisualActor._record_response
    original_call = gateway_module.ConnectorGateway.call
    original_arguments = gateway_module._arguments
    probe_context = threading.local()

    def build_actor_resources(*args: Any, **kwargs: Any) -> Any:
        prepared = original_build(*args, **kwargs)
        run_id = str(kwargs.get("run_id") or "")
        role = prepared.actor._role
        role._connector_e2e_run_id = run_id
        connector = getattr(role, "_connector_gateway", None)
        if connector is not None:
            connector._connector_e2e_run_id = run_id
        return prepared

    def propose(self: Any, *args: Any, **kwargs: Any) -> Any:
        previous = getattr(probe_context, "history_run_id", None)
        probe_context.history_run_id = str(
            getattr(self, "_connector_e2e_run_id", "") or ""
        )
        try:
            return original_propose(self, *args, **kwargs)
        finally:
            if previous is None:
                try:
                    del probe_context.history_run_id
                except AttributeError:
                    pass
            else:
                probe_context.history_run_id = previous

    def thread_messages(history: Any, goal: str) -> Any:
        messages = original_thread_messages(history, goal)
        run_id = str(getattr(probe_context, "history_run_id", "") or "")
        if run_id:
            projection = [
                {
                    "role": str(message.get("role") or ""),
                    "chars": len(str(message.get("content") or "")),
                    "content_sha256": _sha256(
                        str(message.get("content") or "")
                    ),
                }
                for message in messages
            ]
            history_projection = projection[:-1]
            _append(
                {
                    "phase": "actor_initial_history_projection",
                    "run_id": run_id,
                    "goal_sha256": _sha256(goal),
                    "provider_message_count": len(projection),
                    "history_message_count": len(history_projection),
                    "history_chars": sum(
                        int(message["chars"])
                        for message in history_projection
                    ),
                    "history_roles": [
                        message["role"] for message in history_projection
                    ],
                    "history_projection_sha256": _sha256(
                        json.dumps(
                            history_projection,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                    "provider_projection_sha256": _sha256(
                        json.dumps(
                            projection,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                }
            )
        return messages

    def record_response(self: Any, response: Any) -> None:
        original_record(self, response)
        try:
            message = actor_module.response_message(response)
            calls = _member(message, "tool_calls")
            if not isinstance(calls, (list, tuple)):
                return
            for call in calls:
                function = _member(call, "function")
                name = _member(function, "name") if function is not None else None
                raw = _member(function, "arguments") if function is not None else None
                if name not in actor_module._CONNECTOR_TOOLS or type(raw) is not str:
                    continue
                _append(
                    {
                        "phase": "raw_model_connector_call",
                        "run_id": str(
                            getattr(self, "_connector_e2e_run_id", "") or ""
                        ),
                        "tool": name,
                        "raw_arguments_json": raw,
                        "raw_arguments_sha256": _sha256(raw),
                    }
                )
        except Exception as exc:
            _append(
                {
                    "phase": "probe_error",
                    "where": "record_response",
                    "error_kind": type(exc).__name__,
                }
            )

    def capture_arguments(arguments_json: str) -> Any:
        try:
            parsed = original_arguments(arguments_json)
        except Exception as exc:
            if getattr(probe_context, "capture_gateway_arguments", False):
                probe_context.canonical_error_kind = type(exc).__name__
            raise
        if getattr(probe_context, "capture_gateway_arguments", False):
            probe_context.canonical_arguments = parsed[0]
            probe_context.canonical_arguments_sha256 = parsed[1]
        return parsed

    def call_connector(
        self: Any,
        ref: str,
        arguments_json: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        previous_capture = getattr(
            probe_context, "capture_gateway_arguments", False
        )
        probe_context.capture_gateway_arguments = True
        probe_context.canonical_arguments = None
        probe_context.canonical_arguments_sha256 = ""
        probe_context.canonical_error_kind = ""
        result: Any = None
        call_error_kind = ""
        try:
            result = original_call(self, ref, arguments_json, *args, **kwargs)
            return result
        except Exception as exc:
            call_error_kind = type(exc).__name__
            raise
        finally:
            receipt = result.get("receipt") if isinstance(result, dict) else None
            _append(
                {
                    "phase": "canonical_gateway_call",
                    "run_id": str(
                        getattr(self, "_connector_e2e_run_id", "") or ""
                    ),
                    "ref_sha256": _sha256(str(ref)),
                    "arguments_json": arguments_json,
                    "canonical_arguments": getattr(
                        probe_context, "canonical_arguments", None
                    ),
                    "canonical_arguments_sha256": str(
                        getattr(
                            probe_context,
                            "canonical_arguments_sha256",
                            "",
                        )
                        or ""
                    ),
                    "canonical_error_kind": str(
                        getattr(
                            probe_context, "canonical_error_kind", ""
                        )
                        or ""
                    ),
                    "call_error_kind": call_error_kind,
                    "receipt": receipt,
                }
            )
            probe_context.capture_gateway_arguments = previous_capture

    live_module._build_actor_resources = build_actor_resources
    actor_module.LiveVisualActor.propose = propose
    actor_module._thread_messages = thread_messages
    actor_module.LiveVisualActor._record_response = record_response
    gateway_module._arguments = capture_arguments
    gateway_module.ConnectorGateway.call = call_connector
    _append({"phase": "probe_installed", "pid": os.getpid()})


if (
    Path(sys.argv[0]).name.casefold() == "serve.py"
    and str(os.environ.get(PROBE_ENV) or "")
):
    try:
        working_directory = Path.cwd()
        if (working_directory / "winpilot").is_dir():
            sys.path.insert(0, str(working_directory))
        _install()
    except Exception as error:
        _append(
            {
                "phase": "probe_install_error",
                "error_kind": type(error).__name__,
                "error": str(error)[:1_000],
            }
        )
