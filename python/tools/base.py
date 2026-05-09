"""
ToolWrapper — wraps @feature registry FeatureSpec into typed Tool interface.

Converts the raw dict return from registry.execute() into a typed ToolResult.
"""

from __future__ import annotations

from typing import Any

from core.interfaces import ToolResult


class ToolWrapper:
    """Wraps a FeatureSpec into a typed Tool interface.

    Usage:
        spec = registry.get("click_target")
        tool = ToolWrapper(spec)
        result = tool.execute({"target": "确定", "timeout": 10})
        # result is a ToolResult with typed fields
    """

    def __init__(self, spec: Any) -> None:
        """Wrap a FeatureSpec (or any object with name/params/handler).

        Args:
            spec: A FeatureSpec-like object with attributes:
                  name, display_name, description, params (list of ParamSpec),
                  handler (callable), category, tags.
        """
        self._spec = spec

    # ── Properties ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Tool name (snake_case), matches the @feature name."""
        return getattr(self._spec, "name", "unknown")

    @property
    def display_name(self) -> str:
        return getattr(self._spec, "display_name", self.name)

    @property
    def description(self) -> str:
        return getattr(self._spec, "description", "")

    @property
    def category(self) -> str:
        cat = getattr(self._spec, "category", None)
        return cat.name if hasattr(cat, "name") else str(cat)

    @property
    def schema(self) -> dict:
        """JSON Schema describing accepted parameters.

        Auto-generated from the FeatureSpec's ParamSpec list.
        """
        params = getattr(self._spec, "params", [])
        properties: dict[str, dict] = {}
        required: list[str] = []

        for p in params:
            ptype = getattr(p, "type", "string")
            js_type = self._param_type_to_js(ptype)
            prop: dict = {"type": js_type, "description": getattr(p, "description", "")}

            default = getattr(p, "default", None)
            if default is not None:
                prop["default"] = default

            example = getattr(p, "example", None)
            if example:
                prop["example"] = example

            properties[getattr(p, "name", "unknown")] = prop

            if getattr(p, "required", True):
                required.append(getattr(p, "name", "unknown"))

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    @property
    def tags(self) -> list[str]:
        return list(getattr(self._spec, "tags", []))

    @property
    def handler(self) -> Any:
        return getattr(self._spec, "handler", None)

    # ── Execution ──────────────────────────────────────────────

    def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given parameters.

        Args:
            params: Parameter dict matching the tool's schema.

        Returns:
            ToolResult with the execution outcome.
        """
        handler = self.handler
        if handler is None:
            return ToolResult.fail(f"Tool '{self.name}' has no handler")

        try:
            result = handler(**params)

            # registry.execute() returns dict with {"success": bool, "result": ..., "error": ...}
            if isinstance(result, dict):
                success = bool(result.get("success", False))
                data = result.get("result", result)
                error = result.get("error", None)
                return ToolResult(
                    success=success,
                    data=data,
                    error=str(error) if error else None,
                )

            # Tool returns raw value — wrap it
            return ToolResult.ok(data=result)

        except TypeError as e:
            return ToolResult.fail(f"Parameter error: {e}")
        except Exception as e:
            return ToolResult.fail(str(e))

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _param_type_to_js(ptype: str) -> str:
        """Map @feature param type strings to JSON Schema types."""
        mapping = {
            "str": "string",
            "string": "string",
            "number": "number",
            "int": "integer",
            "float": "number",
            "boolean": "boolean",
            "bool": "boolean",
            "dict": "object",
            "object": "object",
            "list": "array",
            "array": "array",
        }
        return mapping.get(ptype.lower(), "string")

    def __repr__(self) -> str:
        return f"ToolWrapper({self.name})"
