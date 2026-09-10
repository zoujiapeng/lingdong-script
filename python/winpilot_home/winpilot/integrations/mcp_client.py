"""Generic MCP discovery and calls for explicitly configured servers.

The client accepts Claude-compatible ``.mcp.json`` files, keeps one live
session per exact server configuration, and reconnects after a broken call.
It does not interpret tool semantics or create service-specific adapters.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
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
_tool_cache: dict[
    tuple[str, str], tuple[float, list[dict[str, Any]], str]
] = {}
_ENV_REFERENCE = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")


def _config_value(value: Any) -> str:
    text = str(value)

    def replace(matched: re.Match[str]) -> str:
        name = matched.group(1)
        if name not in os.environ:
            raise ValueError(f"MCP environment variable is not set: {name}")
        return os.environ[name]

    return _ENV_REFERENCE.sub(replace, text)


def _configured_paths() -> list[Path]:
    # Later paths win. The explicit process override stays highest priority.
    paths: list[Path] = [
        Path.cwd() / ".mcp.json",
        ROOT_DIR.parent.parent / ".mcp.json",
        Path(os.environ.get("USERPROFILE", "")) / ".mcp.json",
    ]
    configured = CONFIG.get("mcp", "config_paths", default=[])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        paths.extend(
            Path(str(item)).expanduser()
            for item in configured
            if str(item).strip()
        )
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
            if entry.get("disabled") is True:
                continue
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
    if os.name == "nt" and raw.casefold() in {
        "npx", "npm", "node", "pnpm", "yarn", "uvx", "uv",
    }:
        return shutil.which(f"{raw}.cmd") or shutil.which(raw) or raw
    return shutil.which(raw) or raw


@asynccontextmanager
async def _connection(server: dict[str, Any]) -> AsyncIterator[tuple[Any, Any]]:
    url = str(server.get("url") or "").strip()
    if url:
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        headers = (
            {
                str(key): _config_value(value)
                for key, value in server.get("headers", {}).items()
            }
            if isinstance(server.get("headers"), dict)
            else {}
        )
        token_env = str(server.get("bearer_token_env_var") or "").strip()
        if token_env:
            if token_env not in os.environ:
                raise ValueError(
                    f"MCP environment variable is not set: {token_env}"
                )
            headers["Authorization"] = f"Bearer {os.environ[token_env]}"
        async with httpx.AsyncClient(
            headers=headers or None,
            timeout=30.0,
        ) as client:
            async with streamable_http_client(
                url,
                http_client=client,
            ) as streams:
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


class _SessionWorker:
    """Own one MCP context in the same asyncio task for its full lifetime."""

    def __init__(self, name: str, server: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        self.name = name
        self.server = server
        self.instructions = ""
        self.ready: asyncio.Future[None] = loop.create_future()
        self.queue: asyncio.Queue[
            tuple[str, Any, asyncio.Future[Any]]
        ] = asyncio.Queue()
        self.task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        try:
            from mcp import ClientSession

            async with _connection(self.server) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self.instructions = str(
                        getattr(initialized, "instructions", "") or ""
                    )[:8192]
                    if not self.ready.done():
                        self.ready.set_result(None)
                    while True:
                        operation, payload, result = await self.queue.get()
                        if operation == "close":
                            if not result.done():
                                result.set_result(None)
                            break
                        if result.cancelled():
                            continue
                        try:
                            if operation == "list":
                                value = await _all_tools(session)
                            elif operation == "call":
                                tool, arguments = payload
                                value = await session.call_tool(
                                    tool,
                                    arguments=arguments,
                                )
                            else:
                                raise ValueError("unknown MCP session operation")
                        except BaseException as exc:
                            if not result.done():
                                result.set_exception(exc)
                        else:
                            if not result.done():
                                result.set_result(value)
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            while not self.queue.empty():
                _operation, _payload, result = self.queue.get_nowait()
                if not result.done():
                    result.set_exception(exc)

    async def request(self, operation: str, payload: Any = None) -> Any:
        await self.ready
        if self.task.done():
            await self.task
            raise RuntimeError("MCP session closed")
        result = asyncio.get_running_loop().create_future()
        await self.queue.put((operation, payload, result))
        return await result

    async def close(self) -> None:
        if self.task.done():
            await asyncio.gather(self.task, return_exceptions=True)
            return
        result = asyncio.get_running_loop().create_future()
        await self.queue.put(("close", None, result))
        await result
        await asyncio.gather(self.task, return_exceptions=True)


class _SessionPool:
    """One event-loop thread owning all long-lived MCP transports."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], _SessionWorker] = {}

    async def get(
        self,
        name: str,
        server: dict[str, Any],
    ) -> _SessionWorker:
        key = _server_cache_key(name, server)
        current = self._sessions.get(key)
        if current is not None:
            return current
        stale = [item for item in self._sessions if item[0] == name]
        for old_key in stale:
            old = self._sessions.pop(old_key)
            await old.close()
        worker = _SessionWorker(name, server)
        self._sessions[key] = worker
        try:
            await worker.ready
        except BaseException:
            self._sessions.pop(key, None)
            await asyncio.gather(worker.task, return_exceptions=True)
            raise
        return worker

    async def invalidate(self, name: str, server: dict[str, Any]) -> None:
        live = self._sessions.pop(_server_cache_key(name, server), None)
        if live is not None:
            await live.close()

    async def close(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for live in sessions:
            try:
                await live.close()
            except Exception:
                pass


_pool = _SessionPool()
_loop_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _pool_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def worker() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        thread = threading.Thread(
            target=worker,
            name="winpilot-mcp",
            daemon=True,
        )
        thread.start()
        ready.wait(timeout=2.0)
        if not loop.is_running():
            raise RuntimeError("MCP session loop failed to start")
        _loop, _loop_thread = loop, thread
        return loop


def _run(coro: Any, *, timeout_s: float = 60.0, stop_event: Any | None = None):
    loop = _pool_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    deadline = time.monotonic() + max(0.01, float(timeout_s))
    while True:
        if stop_event is not None and stop_event.is_set():
            future.cancel()
            raise TimeoutError("MCP operation canceled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            future.cancel()
            raise TimeoutError("MCP operation timed out")
        try:
            return future.result(timeout=min(0.05, remaining))
        except concurrent.futures.TimeoutError:
            continue


def close_sessions() -> None:
    """Close live MCP sessions; safe to call repeatedly in tests/shutdown."""

    global _loop, _loop_thread
    with _loop_lock:
        loop, thread = _loop, _loop_thread
    if loop is None or not loop.is_running():
        return
    try:
        future = asyncio.run_coroutine_threadsafe(_pool.close(), loop)
        future.result(timeout=5.0)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5.0)
    with _loop_lock:
        if _loop is loop:
            _loop = None
            _loop_thread = None


atexit.register(close_sessions)


async def _all_tools(session: Any) -> list[Any]:
    tools: list[Any] = []
    cursor: str | None = None
    for _page in range(50):
        response = await session.list_tools(cursor=cursor)
        tools.extend(response.tools)
        cursor = getattr(response, "nextCursor", None)
        if not cursor or len(tools) >= 2000:
            break
    return tools[:2000]


async def _list_server_tools(
    name: str,
    server: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    live = await _pool.get(name, server)
    tools = await live.request("list")
    return [_tool_descriptor(name, tool) for tool in tools], live.instructions


def _query_score(query: str, tool: dict[str, Any]) -> int:
    if not query:
        return 1
    name = str(tool.get("name") or "").casefold()
    description = str(tool.get("description") or "").casefold()
    full = f"{name} {description}"
    tokens = tuple(dict.fromkeys(re.findall(r"[\w.-]+", query.casefold())))
    if name == query.casefold():
        return 10_000
    score = 1000 if query.casefold() in name else 0
    score += sum(120 for token in tokens if token in name)
    score += sum(20 for token in tokens if token in description)
    return score if score else (5 if query.casefold() in full else 0)


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
    query_text = str(query or "").strip()
    ranked: list[tuple[int, str, str, dict[str, Any]]] = []
    failures: list[dict[str, str]] = []
    server_details: list[dict[str, Any]] = []
    if requested_server and not selected:
        failures.append({
            "server": requested_server,
            "error": "MCP server is not configured",
        })
    for name, server in selected.items():
        remaining = float(timeout_s) - (time.monotonic() - started)
        if remaining <= 0 or (stop_event is not None and stop_event.is_set()):
            failures.append({
                "server": name,
                "error": "connector discovery canceled or timed out",
            })
            break
        cache_key = _server_cache_key(name, server)
        with _cache_lock:
            cached = _tool_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
            listed = [dict(item) for item in cached[1]]
            instructions = cached[2]
        else:
            try:
                raw_listed = _run(
                    _list_server_tools(name, server),
                    timeout_s=remaining,
                    stop_event=stop_event,
                )
                if isinstance(raw_listed, tuple):
                    listed, instructions = raw_listed
                else:  # convenient for injected test doubles
                    listed, instructions = raw_listed, ""
                listed = [dict(item) for item in (listed or [])]
                instructions = str(instructions or "")[:8192]
                with _cache_lock:
                    _tool_cache[cache_key] = (
                        time.monotonic(),
                        [dict(item) for item in listed],
                        instructions,
                    )
            except Exception as exc:
                try:
                    _run(_pool.invalidate(name, server), timeout_s=2.0)
                except Exception:
                    pass
                failures.append({"server": name, "error": str(exc)[:500]})
                continue
        server_details.append({
            "name": name,
            "tool_count": len(listed),
            **({"instructions": instructions} if instructions else {}),
        })
        for tool in listed:
            score = _query_score(query_text, tool)
            if score:
                ranked.append((
                    -score,
                    str(tool.get("server") or "").casefold(),
                    str(tool.get("name") or "").casefold(),
                    tool,
                ))
    ranked.sort(key=lambda item: item[:3])
    return {
        "ok": True,
        "configured_server_count": len(servers),
        "configured_servers": sorted(servers),
        "queried_server_count": len(selected),
        "servers": server_details,
        "tools": [item[3] for item in ranked[:200]],
        "failures": failures,
    }


async def _call_server_tool(
    server_name: str,
    server: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    live = await _pool.get(server_name, server)
    response = await live.request("call", (tool_name, arguments))
    content = [
        item.model_dump(mode="json", exclude_none=True)
        for item in (response.content or [])
    ]
    return {
        "ok": not bool(response.isError),
        "server": server_name,
        "tool": tool_name,
        "content": content,
        "structured_content": getattr(response, "structuredContent", None),
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
    servers = configured_servers()
    name = str(server_name or "").strip()
    tool = str(tool_name or "").strip()
    server = servers.get(name)
    if server is None:
        return {"ok": False, "error": f"MCP server is not configured: {name}"}
    if not tool:
        return {"ok": False, "error": "MCP tool name is empty"}
    try:
        return dict(_run(
            _call_server_tool(name, server, tool, dict(arguments or {})),
            timeout_s=timeout_s,
            stop_event=stop_event,
        ) or {})
    except Exception as exc:
        try:
            _run(_pool.invalidate(name, server), timeout_s=2.0)
        except Exception:
            pass
        return {
            "ok": False,
            "server": name,
            "tool": tool,
            "error": str(exc)[:1000],
        }


__all__ = ["call", "close_sessions", "configured_servers", "discover"]
