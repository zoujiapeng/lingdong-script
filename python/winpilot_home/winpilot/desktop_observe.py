"""Read-only desktop observation endpoints for external agent hosts.

These routes only READ. They never enqueue a physical action, never bind a
window, and never take the desktop queue. They exist so an external host (for
example the DSH plugin in `packages/dsh-plugin-lingdong`) can observe the very
desktop the runtime acts on, using the runtime's own enumeration instead of a
second implementation that would drift.

The one exception to "read" is stated where it lives: the verification route
evaluates the runtime's own predicates, and one supported predicate
(`shell_true`) runs a command. It still produces no physical input and takes no
desktop queue.

Every dependency is imported lazily inside its handler so that a missing or
broken optional backend degrades one endpoint instead of preventing the server
from starting.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter(prefix="/api/v2/desktop", tags=["desktop-observe"])

# Bound every list so an external caller can never pull an unbounded payload.
_MAX_RESULTS = 200

# Scalar JSON types; anything else in a perceived element is not projected.
_SCALARS = (str, int, float, bool, type(None))


def _element_dict(item: Any) -> dict[str, Any]:
    """Project one perceived element onto JSON-safe fields.

    The perception model's own ``to_dict()`` is used rather than
    ``dataclasses.asdict``: an element's ``extra`` map carries the live UIA
    control handle it was read from, which is neither serializable nor something
    an external caller should hold. The scalar part of ``extra`` - automation
    id, class name, control type - is kept, because that is what makes a control
    addressable on a later call.
    """
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        try:
            record = to_dict()
        except Exception:  # pragma: no cover - third party element type
            record = None
        if isinstance(record, dict):
            extra = getattr(item, "extra", None)
            if isinstance(extra, dict):
                scalars = {
                    key: value
                    for key, value in extra.items()
                    if isinstance(key, str) and isinstance(value, _SCALARS)
                }
                if scalars:
                    record["extra"] = scalars
            return record
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return {
            key: value
            for key, value in dataclasses.asdict(item).items()
            if isinstance(value, _SCALARS) or isinstance(value, list)
        }
    return {"unprojected": type(item).__name__, "value": str(item)[:200]}


def _elements_dicts(items: Any) -> list[dict[str, Any]]:
    """Project a bounded element list, tolerating a backend that returns none."""
    if not isinstance(items, list):
        return []
    return [_element_dict(item) for item in items]


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
        "elements": _elements_dicts(bounded),
    }


@router.get("/window")
def window(hwnd: int = Query(..., gt=0)) -> dict[str, Any]:
    """One window's live facts: title, class, rectangle, process, state."""
    try:
        from .action.clipboard_ops import window_info_backend

        result = window_info_backend(int(hwnd))
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"window read unavailable: {exc}") from exc
    if not result.get("ok"):
        raise HTTPException(404, str(result.get("error") or "window not found"))
    return result


@router.get("/snapshot")
def snapshot(
    hwnd: int = Query(..., gt=0),
    limit: int = Query(200, ge=1, le=_MAX_RESULTS),
    max_side: int = Query(1120, ge=64, le=2048),
) -> dict[str, Any]:
    """One window's accessibility tree and its pixels, from the same moment.

    A caller that reads the tree and the image in two calls is looking at two
    frames: the coordinates it derives from the first may already be stale in
    the second. This returns both together, plus the window facts that make the
    image's frame identifiable, so a model can locate a control semantically and
    still know exactly which pixels it just saw.

    The image is a JPEG encoded from the window's own rectangle at capture time.
    A minimized window has no meaningful pixels, so its image is null and the
    window state says why instead of returning a stale or blank frame.
    """
    try:
        from .action.clipboard_ops import window_info_backend

        info = window_info_backend(int(hwnd))
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"window read unavailable: {exc}") from exc
    if not info.get("ok"):
        raise HTTPException(404, str(info.get("error") or "window not found"))
    facts = dict(info.get("window") or {})

    try:
        from .perception import uia

        collected = uia.collect(int(hwnd))
    except Exception as exc:  # pragma: no cover - optional dependency
        collected = []
        facts["accessibility_error"] = f"{type(exc).__name__}: {exc}"
    bounded = collected[:limit]

    facts["accessibility_count"] = len(collected)
    facts["accessibility_returned"] = len(bounded)

    image: dict[str, Any] | None = None
    if facts.get("minimized"):
        facts["image_omitted"] = "window is minimized"
    else:
        image = _encode_window_image(int(hwnd), int(max_side))
        if image is None:
            facts["image_omitted"] = "window capture produced no image"

    return {
        "ok": True,
        "window": facts,
        "elements": _elements_dicts(bounded),
        "image": image,
    }


def _encode_window_image(hwnd: int, max_side: int) -> dict[str, Any] | None:
    """Return the window's rectangle as a bounded JPEG, or None.

    Reuses the runtime's own window capture helper, then decodes the encoded
    bytes to report the dimensions the caller will actually receive rather than
    re-deriving them from the scaling rule (which could drift from the helper).
    """
    import base64
    import hashlib

    try:
        import cv2
        import numpy as np

        from .utils.image_capture import capture_window_jpeg_b64

        encoded = capture_window_jpeg_b64(int(hwnd), max_side=int(max_side))
        if not encoded:
            return None
        raw = base64.b64decode(encoded)
        decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        height, width = decoded.shape[:2]
    except Exception:  # pragma: no cover - platform dependent
        return None
    return {
        "content_type": "image/jpeg",
        "width": int(width),
        "height": int(height),
        "max_side": int(max_side),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": encoded,
    }


@router.post("/verify")
def verify(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Evaluate one bounded predicate set against the live desktop.

    This is the runtime's own evaluator - the same `action.verify.check` the
    offline replayer runs before and after a step - not a second opinion about
    it. Every key the caller may use is the evaluator's own supported set, so an
    unknown key is refused by name instead of being read as a false predicate.

    The answer is three-valued by construction:

    - ``satisfied: true`` - every condition held within the timeout;
    - ``satisfied: false`` - a condition did not hold, or the set was not
      evaluable (an unsupported key, a visual condition with no window);
    - a 400 refusal - the request itself was not a predicate set at all.

    One supported key is not a pure read: ``shell_true`` runs a command and
    reads its exit status. It is allowed because it is the strongest signal a
    caller can ask for, and it is named here rather than hidden.
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be one JSON object")
    expect = payload.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise HTTPException(400, "expect must be a non-empty object of conditions")
    hwnd = payload.get("hwnd")
    if hwnd is not None:
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "hwnd must be an integer") from exc
        if hwnd <= 0:
            raise HTTPException(400, "hwnd must be positive")
    try:
        timeout_s = float(payload.get("timeout_s") or 6.0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "timeout_s must be a number") from exc
    timeout_s = max(0.2, min(timeout_s, 120.0))

    try:
        from .action import verify as verify_module
    except Exception as exc:  # pragma: no cover - optional dependency
        raise HTTPException(503, f"verification backend unavailable: {exc}") from exc

    allowed = set(verify_module.SUPPORTED_CONDITIONS) | {"timeout_s", "allow_preexisting"}
    unknown = sorted(key for key in expect if key not in allowed)
    if unknown:
        raise HTTPException(
            400,
            "unsupported condition(s): " + ", ".join(unknown)
            + "; supported: " + ", ".join(sorted(verify_module.SUPPORTED_CONDITIONS)),
        )

    try:
        outcome = verify_module.check(expect, hwnd=hwnd, timeout_s=timeout_s)
    except Exception as exc:  # pragma: no cover - platform dependent
        raise HTTPException(503, f"verification failed: {exc}") from exc

    result = {"ok": True, "satisfied": bool(outcome.verified), "hwnd": hwnd, "timeout_s": timeout_s}
    result.update(outcome.to_dict())
    return result
