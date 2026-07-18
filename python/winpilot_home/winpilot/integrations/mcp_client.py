"""Optional outbound MCP capability discovery for the unified agent.

WinPilot remains a GUI-first desktop agent.  This module only exposes tools
from servers the user explicitly configured; failure or absence of MCP never
removes the local Controller path.  Connections are short lived so a broken
server cannot poison later GUI runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import CONFIG, ROOT_DIR


_CACHE_TTL_S = 120.0
_cache_lock = threading.Lock()
_tool_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _configured_paths() -> list[Path]:
    paths: list[Path] = []
    raw_env = str(os.environ.get("WINPILOT_MCP_CONFIG") or "").strip()
    if raw_env:
        paths.extend(Path(item).expanduser() for item in raw_env.split(os.pathsep) if item.strip())
    configured = CONFIG.get("mcp", "config_paths", default=[])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        paths.extend(Path(str(item)).expanduser() for item in configured if str(item).strip())
    # Development/workspace convention plus an optional per-user convention.
    paths.extend([
        Path.cwd() / ".mcp.json",
        ROOT_DIR.parent.parent / ".mcp.json",
        Path(os.environ.get("USERPROFILE", "")) / ".mcp.json",
    ])
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def configured_servers() -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    for path in _configured_paths():
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = payload.get("mcpServers") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            continue
        for name, raw in values.items():
            if not isinstance(raw, dict) or not str(name).strip():
                continue
            entry = dict(raw)
            entry["_config_path"] = str(path)
            servers[str(name).strip()] = entry
    return servers


def _resolved_command(command: str) -> str:
    raw = str(command or "").strip()
    if os.name == "nt" and raw.casefold() in {"npx", "npm", "node", "pnpm", "yarn"}:
        return shutil.which(f"{raw}.cmd") or shutil.which(raw) or raw
    return shutil.which(raw) or raw


@asynccontextmanager
async def _connection(server: dict[str, Any]) -> AsyncIterator[tuple[Any, Any]]:
    url = str(server.get("url") or "").strip()
    if url:
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        headers = {
            str(key): str(value)
            for key, value in (server.get("headers") or {}).items()
        } if isinstance(server.get("headers"), dict) else {}
        async with httpx.AsyncClient(headers=headers or None, timeout=30.0) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                yield streams[0], streams[1]
        return

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = _resolved_command(str(server.get("command") or ""))
    if not command:
        raise ValueError("configured MCP server has neither url nor command")
    args = [str(item) for item in (server.get("args") or [])]
    env = dict(os.environ)
    if isinstance(server.get("env"), dict):
        env.update({str(key): str(value) for key, value in server["env"].items()})
    cwd = str(server.get("cwd") or "").strip() or None
    params = StdioServerParameters(
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    async with stdio_client(params) as streams:
        yield streams[0], streams[1]


async def _list_server_tools(name: str, server: dict[str, Any]) -> list[dict[str, Any]]:
    from mcp import ClientSession

    async with _connection(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            response = await session.list_tools()
            tools: list[dict[str, Any]] = []
            for tool in response.tools:
                payload = tool.model_dump(mode="json", exclude_none=True)
                tools.append({
                    "server": name,
                    "name": str(payload.get("name") or ""),
                    "description": str(payload.get("description") or ""),
                    "input_schema": payload.get("inputSchema") or payload.get("input_schema") or {},
                    "annotations": payload.get("annotations") or {},
                })
            return tools


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Tool dispatch is normally synchronous in a worker thread.  Keep a safe
    # fallback for callers that already own an event loop.
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # propagated on the caller thread
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=60)
    if thread.is_alive():
        raise TimeoutError("MCP operation exceeded 60 seconds")
    if error:
        raise error[0]
    return result.get("value")


def discover(query: str = "", server_name: str = "") -> dict[str, Any]:
    servers = configured_servers()
    requested_server = str(server_name or "").strip()
    selected = (
        {requested_server: servers[requested_server]}
        if requested_server in servers
        else {}
        if requested_server
        else servers
    )
    query_cf = str(query or "").casefold().strip()
    tools: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for name, server in selected.items():
        with _cache_lock:
            cached = _tool_cache.get(name)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
            listed = [dict(item) for item in cached[1]]
        else:
            try:
                listed = list(_run(_list_server_tools(name, server)) or [])
                with _cache_lock:
                    _tool_cache[name] = (time.monotonic(), [dict(item) for item in listed])
            except Exception as exc:
                failures.append({"server": name, "error": str(exc)[:500]})
                continue
        for tool in listed:
            haystack = f"{tool.get('name', '')} {tool.get('description', '')}".casefold()
            if not query_cf or all(token in haystack for token in query_cf.split()):
                tools.append(tool)
    return {
        "ok": True,
        "configured_server_count": len(servers),
        "queried_server_count": len(selected),
        "tools": tools[:200],
        "failures": failures,
        "gui_fallback_available": True,
    }


async def _call_server_tool(
    server_name: str,
    server: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    from mcp import ClientSession

    async with _connection(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool = next((item for item in listed.tools if item.name == tool_name), None)
            if tool is None:
                raise ValueError(f"MCP tool not found: {server_name}/{tool_name}")
            annotations = (
                tool.annotations.model_dump(mode="json", exclude_none=True)
                if tool.annotations is not None
                else {}
            )
            response = await session.call_tool(tool_name, arguments=arguments)
            content = [
                item.model_dump(mode="json", exclude_none=True)
                for item in (response.content or [])
            ]
            return {
                "ok": not bool(response.isError),
                "server": server_name,
                "tool": tool_name,
                "content": content,
                "structured_content": response.structuredContent,
                "annotations": annotations,
                "error": "MCP tool returned an error" if response.isError else "",
            }


def call(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    servers = configured_servers()
    server = servers.get(str(server_name or "").strip())
    if server is None:
        return {
            "ok": False,
            "error": f"MCP server is not explicitly configured: {server_name}",
            "gui_fallback_available": True,
        }
    discovery = discover(server_name=server_name)
    descriptor = next((
        item for item in discovery.get("tools", [])
        if str(item.get("name") or "") == str(tool_name or "")
    ), None)
    if descriptor is None:
        return {
            "ok": False,
            "error": f"MCP tool is unavailable: {server_name}/{tool_name}",
            "gui_fallback_available": True,
        }
    annotations = descriptor.get("annotations") or {}
    destructive = bool(
        annotations.get("destructiveHint") is True
        or annotations.get("destructive_hint") is True
    )
    if destructive and not allow_destructive:
        return {
            "ok": False,
            "blocked": True,
            "error": "MCP tool is marked destructive; explicit task-scoped authorization is required",
            "server": server_name,
            "tool": tool_name,
            "gui_fallback_available": True,
        }
    try:
        result = _run(_call_server_tool(
            server_name,
            server,
            str(tool_name or ""),
            dict(arguments or {}),
        ))
        return {**dict(result or {}), "gui_fallback_available": True}
    except Exception as exc:
        return {
            "ok": False,
            "server": server_name,
            "tool": tool_name,
            "error": str(exc)[:1000],
            "gui_fallback_available": True,
        }
