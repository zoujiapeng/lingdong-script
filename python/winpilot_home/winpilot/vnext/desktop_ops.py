"""vNext-owned capture / window-inventory / image-paste operation contracts.

This module is pure logic: it validates the Actor's arguments, calls the
injected physical backends, and formats bounded results.  The physical
Windows work (screen capture, window enumeration, clipboard write, key
input) lives in ``winpilot.action.clipboard_ops`` — the action layer is the
only product line that may cross into win32/desktop machinery, so the vNext
kernel stays free of direct win32 imports.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

CaptureBackend = Callable[..., dict[str, Any]]
WindowsBackend = Callable[[], dict[str, Any]]
PasteBackend = Callable[..., dict[str, Any]]

CAPTURE_FORMAT = "capture-screen/1"
WINDOWS_FORMAT = "windows-list/1"
PASTE_FORMAT = "paste-image/1"

_CAPTURE_OUTPUT_MAX_CHARS = 6000


def _bounded_text(value: Any, limit: int = _CAPTURE_OUTPUT_MAX_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def capture_screen(
    backend: CaptureBackend,
    *,
    path: str,
    window: bool,
) -> dict[str, Any]:
    """Delegate one capture request to its physical owner."""
    target = str(path or "").strip()
    try:
        result = backend(path=target, window=bool(window))
    except Exception as exc:
        return {
            "format": CAPTURE_FORMAT,
            "ok": False,
            "error": f"{type(exc).__name__}: {_bounded_text(str(exc))}",
        }
    if not isinstance(result, Mapping):
        return {
            "format": CAPTURE_FORMAT,
            "ok": False,
            "error": "capture backend returned a non-object result",
        }
    output = dict(result)
    output.setdefault("format", CAPTURE_FORMAT)
    return output


def list_windows(backend: WindowsBackend) -> dict[str, Any]:
    """Return visible windows and background apps from the backend."""
    try:
        result = backend()
    except Exception as exc:
        return {
            "format": WINDOWS_FORMAT,
            "ok": False,
            "error": f"{type(exc).__name__}: {_bounded_text(str(exc))}",
        }
    if not isinstance(result, Mapping):
        return {
            "format": WINDOWS_FORMAT,
            "ok": False,
            "error": "window inventory backend returned a non-object result",
        }
    output = dict(result)
    output.setdefault("format", WINDOWS_FORMAT)
    return output


def paste_image(backend: PasteBackend, *, path: str) -> dict[str, Any]:
    """Validate one paste request and delegate to the physical backend."""
    if not path or not path.strip():
        return {
            "format": PASTE_FORMAT,
            "ok": False,
            "error": "paste_image requires an image path",
        }
    try:
        result = backend(path=str(path).strip())
    except Exception as exc:
        return {
            "format": PASTE_FORMAT,
            "ok": False,
            "error": f"{type(exc).__name__}: {_bounded_text(str(exc))}",
        }
    if not isinstance(result, Mapping):
        return {
            "format": PASTE_FORMAT,
            "ok": False,
            "error": "paste backend returned a non-object result",
        }
    output = dict(result)
    output.setdefault("format", PASTE_FORMAT)
    return output


__all__ = [
    "CaptureBackend",
    "WindowsBackend",
    "PasteBackend",
    "CAPTURE_FORMAT",
    "WINDOWS_FORMAT",
    "PASTE_FORMAT",
    "capture_screen",
    "list_windows",
    "paste_image",
]
