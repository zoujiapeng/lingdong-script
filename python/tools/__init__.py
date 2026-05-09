"""
tools — Typed tool abstraction layer over the @feature registry.

Provides ToolWrapper (wraps FeatureSpec → typed Tool interface)
and ToolRegistry (discovery + execution with ToolResult returns).

The existing registry.execute() still works unchanged — this is
an optional typed layer on top.
"""
from .base import ToolWrapper
from .registry import ToolRegistry

__all__ = ["ToolWrapper", "ToolRegistry"]
