"""Optional outbound MCP capability discovery for the unified agent.

WinPilot remains a GUI-first desktop agent.  This module only exposes tools
from servers the user explicitly configured; failure or absence of MCP never
removes the local Controller path.  Connections are short lived so a broken
server cannot poison later GUI runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import CONFIG, ROOT_DIR


_CACHE_TTL_S = 120.0
_cache_lock = threading.Lock()
_tool_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_ENV_REFERENCE = re.compile(
    r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}"
)


def _config_value(value: Any) -> str:
    text = str(value)

    def replace(matched: re.Match[str]) -> str:
        name = matched.group(1)
        if name not in os.environ:
            raise ValueError(f"MCP environment variable is not set: {name}")
        return os.environ[name]

    return _ENV_REFERENCE.sub(replace, text)


def _configured_paths() -> list[Path]:
    # Later paths win on duplicate server names. Keep conventions lowest and
    # the explicit per-process override highest so a run cannot be silently
    # redirected by a workspace or user file with the same server name.
    paths: list[Path] = [
        Path.cwd() / ".mcp.json",
        ROOT_DIR.parent.parent / ".mcp.json",
        Path(os.environ.get("USERPROFILE", "")) / ".mcp.json",
    ]
    configured = CONFIG.get("mcp", "config_paths", default=[])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        paths.extend(Path(str(item)).expanduser() for item in configured if str(item).strip())
    raw_env = str(os.environ.get("WINPILOT_MCP_CONFIG") or "").strip()
    if raw_env:
        paths.extend(
            Path(item).expanduser()
            for item in raw_env.split(os.pathsep)
            if item.strip()
        )
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


def _server_cache_key(name: str, server: dict[str, Any]) -> tuple[str, str]:
    wire = json.dumps(
        server,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return name, hashlib.sha256(wire).hexdigest()


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
            str(key): _config_value(value)
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
        env.update({
            str(key): _config_value(value)
            for key, value in server["env"].items()
        })
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


def _tool_descriptor(server_name: str, tool: Any) -> dict[str, Any]:
    payload = tool.model_dump(mode="json", exclude_none=True)
    return {
        "server": server_name,
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "input_schema": (
            payload.get("inputSchema") or payload.get("input_schema") or {}
        ),
        "annotations": payload.get("annotations") or {},
    }


async def _list_server_tools(name: str, server: dict[str, Any]) -> list[dict[str, Any]]:
    from mcp import ClientSession

    async with _connection(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            response = await session.list_tools()
            return [_tool_descriptor(name, tool) for tool in response.tools]


async def _bounded(
    coro: Any,
    *,
    timeout_s: float,
    stop_event: Any | None,
) -> Any:
    task = asyncio.create_task(coro)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.01, float(timeout_s))
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise TimeoutError("MCP operation canceled")
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("MCP operation timed out")
            done, _ = await asyncio.wait(
                {task},
                timeout=min(0.05, remaining),
            )
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _run(coro: Any, *, timeout_s: float = 60.0, stop_event: Any | None = None):
    def execute() -> Any:
        return asyncio.run(
            _bounded(coro, timeout_s=timeout_s, stop_event=stop_event)
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return execute()
    # Tool dispatch is normally synchronous. Keep the sync API safe for a
    # caller that already owns an event loop.
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result["value"] = execute()
        except BaseException as exc:  # propagated on the caller thread
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=max(0.1, float(timeout_s) + 1.0))
    if thread.is_alive():
        raise TimeoutError("MCP operation did not stop after its deadline")
    if error:
        raise error[0]
    return result.get("value")


def discover(
    query: str = "",
    server_name: str = "",
    *,
    timeout_s: float = 30.0,
    stop_event: Any | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
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
        remaining = float(timeout_s) - (time.monotonic() - started)
        if remaining <= 0 or (stop_event is not None and stop_event.is_set()):
            failures.append({"server": name, "error": "connector discovery canceled or timed out"})
            break
        cache_key = _server_cache_key(name, server)
        with _cache_lock:
            cached = _tool_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
            listed = [dict(item) for item in cached[1]]
        else:
            try:
                listed = list(_run(
                    _list_server_tools(name, server),
                    timeout_s=remaining,
                    stop_event=stop_event,
                ) or [])
                with _cache_lock:
                    _tool_cache[cache_key] = (
                        time.monotonic(),
                        [dict(item) for item in listed],
                    )
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
    expected_descriptor: dict[str, Any],
) -> dict[str, Any]:
    from mcp import ClientSession

    async with _connection(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool = next((item for item in listed.tools if item.name == tool_name), None)
            if tool is None:
                raise ValueError(f"MCP tool not found: {server_name}/{tool_name}")
            actual_descriptor = _tool_descriptor(server_name, tool)
            if actual_descriptor != expected_descriptor:
                raise PermissionError(
                    "MCP tool descriptor changed before the physical call"
                )
            annotations = actual_descriptor["annotations"]
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
    timeout_s: float = 30.0,
    stop_event: Any | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    servers = configured_servers()
    server = servers.get(str(server_name or "").strip())
    if server is None:
        return {
            "ok": False,
            "error": f"MCP server is not explicitly configured: {server_name}",
            "gui_fallback_available": True,
        }
    discovery = discover(
        server_name=server_name,
        timeout_s=timeout_s,
        stop_event=stop_event,
    )
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
    try:
        remaining = float(timeout_s) - (time.monotonic() - started)
        if remaining <= 0 or (stop_event is not None and stop_event.is_set()):
            raise TimeoutError("MCP call canceled or timed out before execution")
        result = _run(_call_server_tool(
            server_name,
            server,
            str(tool_name or ""),
            dict(arguments or {}),
            dict(descriptor),
        ), timeout_s=remaining, stop_event=stop_event)
        return {**dict(result or {}), "gui_fallback_available": True}
    except Exception as exc:
        return {
            "ok": False,
            "server": server_name,
            "tool": tool_name,
            "error": str(exc)[:1000],
            "gui_fallback_available": True,
        }
