"""Small task-agnostic boundary around configured MCP servers."""
from __future__ import annotations

import json
from typing import Any, Mapping

from . import mcp_client


MAX_ARGUMENT_BYTES = 32 * 1024
MAX_DESCRIPTOR_BYTES = 32 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_RESULT_PREVIEW_BYTES = 8 * 1024
MAX_SEARCH_BYTES = 32 * 1024
MAX_SEARCH_TOOLS = 16
_MAX_NAME_CHARS = 256
_MAX_DESCRIPTION_CHARS = 2_048


class ConnectorGatewayError(ValueError):
    pass


def _dump(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConnectorGatewayError("connector data is not bounded JSON") from exc


def _name(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ConnectorGatewayError(f"{field} must be non-empty text")
    result = value.strip()
    if len(result) > _MAX_NAME_CHARS:
        raise ConnectorGatewayError(f"{field} is too long")
    return result


def _descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    connector = _name(raw.get("server"), "connector")
    tool = _name(raw.get("name"), "tool")
    description = str(raw.get("description") or "")
    schema = raw.get("input_schema")
    annotations = raw.get("annotations")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[:_MAX_DESCRIPTION_CHARS]
    if type(schema) is not dict or type(annotations) is not dict:
        raise ConnectorGatewayError(
            "connector tool metadata must contain JSON objects"
        )
    result = {
        "connector": connector,
        "tool": tool,
        "description": description,
        "input_schema": schema,
        "annotations": annotations,
    }
    if len(_dump(result)) > MAX_DESCRIPTOR_BYTES:
        raise ConnectorGatewayError("connector tool descriptor is too large")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON object key")
    return dict(pairs)


def _arguments(value: str) -> dict[str, Any]:
    if type(value) is not str:
        raise ConnectorGatewayError("connector arguments must be a JSON string")
    if len(value.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ConnectorGatewayError(
            f"connector arguments exceed {MAX_ARGUMENT_BYTES} bytes"
        )
    try:
        decoded = json.loads(
            value,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {item}")
            ),
            object_pairs_hook=_unique_object,
        )
    except (ValueError, RecursionError) as exc:
        raise ConnectorGatewayError(
            "connector arguments are not valid JSON"
        ) from exc
    if type(decoded) is not dict:
        raise ConnectorGatewayError(
            "connector arguments must decode to an object"
        )
    if len(_dump(decoded)) > MAX_ARGUMENT_BYTES:
        raise ConnectorGatewayError(
            f"connector arguments exceed {MAX_ARGUMENT_BYTES} bytes"
        )
    return decoded


def _bounded(value: Mapping[str, Any]) -> dict[str, Any]:
    wire = _dump(value)
    if len(wire) <= MAX_RESULT_BYTES:
        return json.loads(wire.decode("utf-8"))
    return {
        "ok": value.get("ok") is True,
        "truncated": True,
        "original_bytes": len(wire),
        "preview": wire[:MAX_RESULT_PREVIEW_BYTES].decode(
            "utf-8", errors="replace"
        ),
    }


class ConnectorGateway:
    """Expose hot-readable MCP discovery and one-attempt tool calls."""

    @property
    def available(self) -> bool:
        # The gateway stays available with an empty catalog so the same Actor
        # can create/update .mcp.json through its generic file/shell tools and
        # discover that server later in the same run.
        return True

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
                "ok": False,
                "tools": [],
                "error": f"connector discovery failed: {type(exc).__name__}",
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
        servers: list[dict[str, Any]] = []
        for raw in found.get("servers", []) if isinstance(found, dict) else []:
            if not isinstance(raw, Mapping):
                continue
            item = {
                "name": str(raw.get("name") or "")[:_MAX_NAME_CHARS],
                "tool_count": int(raw.get("tool_count") or 0),
            }
            instructions = str(raw.get("instructions") or "")[:4096]
            if instructions:
                item["instructions"] = instructions
            if item["name"]:
                servers.append(item)
        return {
            "ok": bool(isinstance(found, dict) and found.get("ok") is True),
            "configured_servers": (
                list(found.get("configured_servers", []))
                if isinstance(found, dict)
                else []
            ),
            "servers": servers,
            "tools": tools,
            "truncated_or_skipped": skipped,
            "failures": (
                list(found.get("failures", []))
                if isinstance(found, dict)
                else []
            ),
        }

    def call(
        self,
        connector: str,
        tool: str,
        arguments_json: str,
        *,
        timeout_s: float = 30.0,
        stop_event: Any | None = None,
    ) -> dict[str, Any]:
        try:
            connector_name = _name(connector, "connector")
            tool_name = _name(tool, "tool")
            arguments = _arguments(arguments_json)
        except ConnectorGatewayError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            outcome = mcp_client.call(
                connector_name,
                tool_name,
                arguments,
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        except Exception as exc:
            outcome = {
                "ok": False,
                "error": f"connector call failed: {type(exc).__name__}",
            }
        if type(outcome) is not dict:
            outcome = {"ok": False, "error": "connector returned invalid JSON"}
        try:
            return _bounded(outcome)
        except ConnectorGatewayError:
            return {"ok": False, "error": "connector returned invalid JSON"}


__all__ = [
    "ConnectorGateway",
    "ConnectorGatewayError",
    "MAX_ARGUMENT_BYTES",
    "MAX_RESULT_BYTES",
]
