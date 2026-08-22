"""Bounded, task-agnostic access to explicitly configured MCP connectors."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from typing import Any, Mapping

from . import mcp_client


TOOL_REF_PREFIX = "connector-tool/1:"
RECEIPT_FORMAT = "connector-receipt/1"
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_DESCRIPTOR_BYTES = 32 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_RESULT_PREVIEW_BYTES = 8 * 1024
MAX_SEARCH_BYTES = 24 * 1024
MAX_SEARCH_TOOLS = 8
_MAX_NAME_CHARS = 256
_MAX_DESCRIPTION_CHARS = 2_048
_MAX_REF_CHARS = 2_048
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ConnectorGatewayError(ValueError):
    pass


def _dump(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConnectorGatewayError("connector data is not bounded JSON") from exc


def _name(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ConnectorGatewayError(f"{field} must be non-empty text")
    value = value.strip()
    if len(value) > _MAX_NAME_CHARS:
        raise ConnectorGatewayError(f"{field} is too long")
    return value


def _descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    connector, tool = _name(raw.get("server"), "connector"), _name(raw.get("name"), "tool")
    description = str(raw.get("description") or "")
    schema, annotations = raw.get("input_schema"), raw.get("annotations")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise ConnectorGatewayError("connector tool description is too large")
    if type(schema) is not dict or type(annotations) is not dict:
        raise ConnectorGatewayError("connector tool metadata must contain JSON objects")
    identity = {
        "connector": connector, "tool": tool, "description": description,
        "input_schema": schema, "annotations": annotations,
    }
    identity_wire = _dump(identity)
    if len(identity_wire) > MAX_DESCRIPTOR_BYTES:
        raise ConnectorGatewayError("connector tool descriptor is too large")
    fingerprint = hashlib.sha256(identity_wire).hexdigest()
    ref_wire = _dump([connector, tool, fingerprint])
    ref = base64.urlsafe_b64encode(ref_wire).decode("ascii").rstrip("=")
    return {
        **identity, "fingerprint": fingerprint, "ref": TOOL_REF_PREFIX + ref,
    }


def _decode_ref(ref: str) -> tuple[str, str, str]:
    if (
        type(ref) is not str or len(ref) > _MAX_REF_CHARS
        or not ref.startswith(TOOL_REF_PREFIX)
    ):
        raise ConnectorGatewayError("invalid connector tool reference")
    encoded = ref[len(TOOL_REF_PREFIX):]
    try:
        wire = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(wire.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectorGatewayError("invalid connector tool reference") from exc
    if type(value) is not list or len(value) != 3:
        raise ConnectorGatewayError("invalid connector tool reference")
    connector, tool = _name(value[0], "connector"), _name(value[1], "tool")
    fingerprint = value[2]
    if type(fingerprint) is not str or not _SHA256.fullmatch(fingerprint):
        raise ConnectorGatewayError("invalid connector tool fingerprint")
    return connector, tool, fingerprint


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def _arguments(value: str) -> tuple[dict[str, Any], str]:
    if type(value) is not str:
        raise ConnectorGatewayError("connector arguments must be a JSON string")
    if len(value.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ConnectorGatewayError(f"connector arguments exceed {MAX_ARGUMENT_BYTES} bytes")
    try:
        decoded = json.loads(
            value,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {item}")
            ),
            object_pairs_hook=_unique_object,
        )
    except (ValueError, RecursionError) as exc:
        raise ConnectorGatewayError("connector arguments are not valid JSON") from exc
    if type(decoded) is not dict:
        raise ConnectorGatewayError("connector arguments must decode to an object")
    wire = _dump(decoded)
    if len(wire) > MAX_ARGUMENT_BYTES:
        raise ConnectorGatewayError(f"connector arguments exceed {MAX_ARGUMENT_BYTES} bytes")
    return decoded, hashlib.sha256(wire).hexdigest()


def _bounded(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    wire = _dump(value)
    digest = hashlib.sha256(wire).hexdigest()
    if len(wire) <= MAX_RESULT_BYTES:
        return json.loads(wire.decode("utf-8")), digest
    return {
        "truncated": True,
        "original_bytes": len(wire),
        "preview": wire[:MAX_RESULT_PREVIEW_BYTES].decode("utf-8", errors="replace"),
    }, digest


def _response(
    outcome: Mapping[str, Any], *, connector: str, tool: str,
    fingerprint: str, arguments_hash: str, status: str,
) -> dict[str, Any]:
    data, result_hash = _bounded(outcome)
    result: dict[str, Any] = {
        "ok": outcome.get("ok") is True,
        "result": {"trust": "untrusted_connector_result", "data": data},
        "receipt": {
            "format": RECEIPT_FORMAT,
            "call_id": f"connector-call-{uuid.uuid4().hex}",
            "connector": connector,
            "tool": tool,
            "tool_fingerprint": fingerprint,
            "arguments_sha256": arguments_hash,
            "result_sha256": result_hash,
            "status": status,
        },
    }
    if status == "blocked":
        for field in ("blocked", "error"):
            if field in outcome:
                result[field] = outcome[field]
    return result


class ConnectorGateway:
    """Lazy catalog plus one-attempt calls; connector data is always untrusted."""

    @property
    def available(self) -> bool:
        try:
            return bool(mcp_client.configured_servers())
        except Exception:
            return False

    def search(
        self,
        query: str = "",
        connector: str = "",
        *,
        timeout_s: float = 30.0,
        stop_event: Any | None = None,
    ) -> dict[str, Any]:
        try:
            found = mcp_client.discover(
                query=str(query or "")[:1_024],
                server_name=str(connector or "")[:_MAX_NAME_CHARS],
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        except Exception as exc:
            return {
                "ok": False, "trust": "untrusted_connector_catalog", "tools": [],
                "error": "connector discovery failed", "error_kind": type(exc).__name__,
            }
        tools: list[dict[str, Any]] = []
        skipped = 0
        for raw in found.get("tools", []) if isinstance(found, dict) else []:
            try:
                item = _descriptor(raw) if isinstance(raw, Mapping) else None
                if item is None or len(_dump([*tools, item])) > MAX_SEARCH_BYTES:
                    raise ConnectorGatewayError("connector catalog limit reached")
                tools.append(item)
            except ConnectorGatewayError:
                skipped += 1
            if len(tools) >= MAX_SEARCH_TOOLS:
                break
        return {
            "ok": bool(isinstance(found, dict) and found.get("ok") is True),
            "trust": "untrusted_connector_catalog",
            "tools": tools,
            "truncated_or_skipped": skipped,
        }

    def call(
        self,
        ref: str,
        arguments_json: str,
        *,
        timeout_s: float = 30.0,
        stop_event: Any | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        connector = tool = fingerprint = ""
        arguments_hash = hashlib.sha256(str(arguments_json).encode("utf-8")).hexdigest()
        try:
            connector, tool, fingerprint = _decode_ref(ref)
            arguments, arguments_hash = _arguments(arguments_json)
            try:
                found = mcp_client.discover(
                    server_name=connector,
                    timeout_s=timeout_s,
                    stop_event=stop_event,
                )
            except Exception as exc:
                raise ConnectorGatewayError("connector tool discovery failed") from exc
            if type(found) is not dict:
                raise ConnectorGatewayError("connector tool discovery failed")
            current = next((
                _descriptor(item) for item in found.get("tools", [])
                if isinstance(item, Mapping) and item.get("name") == tool
            ), None)
            if current is None:
                raise ConnectorGatewayError("connector tool is unavailable")
            if current["fingerprint"] != fingerprint:
                raise ConnectorGatewayError("connector tool reference is stale")
        except ConnectorGatewayError as exc:
            return _response(
                {"ok": False, "blocked": True, "error": str(exc)},
                connector=connector, tool=tool, fingerprint=fingerprint,
                arguments_hash=arguments_hash, status="blocked",
            )

        try:
            remaining = float(timeout_s) - (time.monotonic() - started)
            if remaining <= 0 or (
                stop_event is not None and stop_event.is_set()
            ):
                raise TimeoutError("connector call canceled or timed out")
            # This is the only remote tool attempt; ambiguous writes are not retried.
            outcome = mcp_client.call(
                connector,
                tool,
                arguments,
                timeout_s=remaining,
                stop_event=stop_event,
            )
        except Exception as exc:
            outcome = {
                "ok": False, "error": "connector call failed",
                "error_kind": type(exc).__name__,
            }
        if type(outcome) is not dict:
            outcome = {"ok": False, "error": "connector returned invalid JSON"}
        outcome = dict(outcome)
        outcome.pop("gui_fallback_available", None)
        try:
            _dump(outcome)
        except ConnectorGatewayError:
            outcome = {"ok": False, "error": "connector returned invalid JSON"}
        ok = outcome.get("ok") is True
        status = "succeeded" if ok else "failed"
        return _response(
            outcome,
            connector=connector, tool=tool, fingerprint=fingerprint,
            arguments_hash=arguments_hash, status=status,
        )


__all__ = [
    "ConnectorGateway", "ConnectorGatewayError", "MAX_ARGUMENT_BYTES",
    "MAX_RESULT_BYTES", "RECEIPT_FORMAT", "TOOL_REF_PREFIX",
]
