"""
ToolRegistry — discovers @feature tools and wraps them as typed Tool interfaces.

Uses the existing FeatureRegistry singleton — no changes to the registry.
"""

from __future__ import annotations

from typing import Any

from core.interfaces import ToolResult
from features.registry import registry as _feature_registry

from .base import ToolWrapper


class ToolRegistry:
    """Typed registry of automation tools.

    Discovers tools from the @feature registry and wraps them with
    ToolWrapper for typed ToolResult returns.

    Usage:
        tools = ToolRegistry()
        tool = tools.get("click_target")
        result = tool.execute({"target": "确定"})
        print(result.success, result.data)
    """

    def __init__(self) -> None:
        # Lazy cache: name → ToolWrapper
        self._wrappers: dict[str, ToolWrapper] | None = None

    # ── Discovery ──────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Lazily load tool wrappers from the @feature registry."""
        if self._wrappers is not None:
            return

        self._wrappers = {}
        all_features = _feature_registry.all()
        for spec in all_features:
            try:
                wrapper = ToolWrapper(spec)
                self._wrappers[wrapper.name] = wrapper
            except Exception:
                pass

    def get(self, name: str) -> ToolWrapper | None:
        """Get a tool wrapper by name."""
        self._ensure_loaded()
        return self._wrappers.get(name)

    def list_tools(
        self,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[ToolWrapper]:
        """List available tools, optionally filtered by category or tag.

        Args:
            category: Filter by FeatureCategory name (e.g. "ACTION", "PERCEPTION").
            tag: Filter by tag string (e.g. "agent", "flow").

        Returns:
            List of matching ToolWrapper instances.
        """
        self._ensure_loaded()
        tools = list(self._wrappers.values())

        if category:
            tools = [t for t in tools if t.category.upper() == category.upper()]

        if tag:
            tools = [t for t in tools if tag in t.tags]

        return sorted(tools, key=lambda t: t.name)

    def search(self, query: str) -> list[ToolWrapper]:
        """Search tools by name or description (case-insensitive)."""
        self._ensure_loaded()
        q = query.lower()
        return [
            t for t in self._wrappers.values()
            if q in t.name.lower() or q in t.description.lower()
        ]

    # ── Execution ──────────────────────────────────────────────

    def execute(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        """Execute a tool by name with typed params.

        Args:
            name: Tool name (snake_case, e.g. "click_target").
            params: Parameter dict, or None for no-arg tools.

        Returns:
            ToolResult with typed fields.
        """
        tool = self.get(name)
        if tool is None:
            return ToolResult.fail(f"Tool '{name}' not found")
        return tool.execute(params or {})

    # ── Category helpers ───────────────────────────────────────

    @property
    def categories(self) -> list[str]:
        """All available tool categories."""
        self._ensure_loaded()
        cats: set[str] = set()
        for t in self._wrappers.values():
            if t.category:
                cats.add(t.category.upper())
        return sorted(cats)

    @property
    def count(self) -> int:
        """Total number of registered tools."""
        self._ensure_loaded()
        return len(self._wrappers)


# Module-level singleton
tool_registry = ToolRegistry()
