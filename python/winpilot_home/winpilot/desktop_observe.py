"""Read-only desktop observation endpoints for external agent hosts.

These routes only READ. They never enqueue a physical action, never bind a
window, and never take the desktop queue. They exist so an external host (for
example the DSH plugin in `packages/dsh-plugin-lingdong`) can observe the very
desktop the runtime acts on, using the runtime's own enumeration instead of a
second implementation that would drift.

Every dependency is imported lazily inside its handler so that a missing or
broken optional backend degrades one endpoint instead of preventing the server
from starting.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v2/desktop", tags=["desktop-observe"])

# Bound every list so an external caller can never pull an unbounded payload.
_MAX_RESULTS = 200


@router.get("/screen")
def screen_geometry() -> dict[str, Any]:
    """Virtual-screen origin and size in physical pixels."""
    try:
        from .action import executor as action_executor

        vx, vy, vw, vh = action_executor._virtual_screen_geometry()
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"screen geometry unavailable: {exc}") from exc
    return {"ok": True, "virtual": {"x": vx, "y": vy, "width": vw, "height": vh}}


@router.get("/cursor")
def cursor_position() -> dict[str, Any]:
    """Current physical cursor position."""
    try:
        from .action import executor as action_executor

        result = action_executor.cursor_position()
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"cursor position unavailable: {exc}") from exc
    return result


@router.get("/windows")
def windows() -> dict[str, Any]:
    """Visible windows plus background applications, as the runtime sees them."""
    try:
        from .action.clipboard_ops import list_windows_backend

        return list_windows_backend()
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"window enumeration unavailable: {exc}") from exc


@router.get("/apps")
def apps(
    query: str = "",
    max_results: int = Query(20, ge=1, le=_MAX_RESULTS),
    refresh: bool = False,
) -> dict[str, Any]:
    """Installed-app candidates for a human name, or the bounded index itself."""
    try:
        from .perception import apps as perception_apps

        if query.strip():
            return {
                "ok": True,
                "query": query,
                "candidates": perception_apps.resolve(
                    query, max_results, force_refresh=refresh
                ),
            }
        entries = perception_apps._index(force_refresh=refresh)
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"app discovery unavailable: {exc}") from exc
    return {"ok": True, "count": len(entries), "apps": entries[:max_results]}


@router.get("/elements")
def elements(
    hwnd: int = Query(..., gt=0),
    limit: int = Query(200, ge=1, le=_MAX_RESULTS),
) -> dict[str, Any]:
    """Accessibility elements of one window, when the UIA backend is available."""
    try:
        from .perception import uia
    except Exception as exc:  # pragma: no cover - optional dependency
        raise HTTPException(503, f"UIA backend unavailable: {exc}") from exc
    try:
        collected = uia.collect(int(hwnd))
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"UIA collection failed: {exc}") from exc
    bounded = collected[:limit]
    return {
        "ok": True,
        "hwnd": int(hwnd),
        "count": len(collected),
        "returned": len(bounded),
        "elements": [
            dataclasses.asdict(item) if dataclasses.is_dataclass(item) else item
            for item in bounded
        ],
    }
