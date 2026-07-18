"""ToolSession wrappers for optional outbound MCP providers."""
from __future__ import annotations

from .tool_result import ToolResult


class McpToolMixin:
    def _t_discover_mcp_tools(self, args: dict) -> ToolResult:
        from ..integrations import mcp_client

        result = mcp_client.discover(
            query=str(args.get("query") or ""),
            server_name=str(args.get("server") or ""),
        )
        if result.get("ok") is True and not result.get("tools"):
            result["fast_path_advice"] = (
                "No configured MCP tool matched this capability. Do not repeat the same "
                "discovery with synonyms; continue through the local GUI/controller route."
            )
        return ToolResult(self._fmt(result))

    def _t_call_mcp_tool(self, args: dict) -> ToolResult:
        from ..integrations import mcp_client

        raw_arguments = args.get("arguments")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        result = mcp_client.call(
            str(args.get("server") or ""),
            str(args.get("tool") or ""),
            arguments,
            allow_destructive=args.get("allow_destructive") is True,
        )
        return ToolResult(self._fmt(result))
