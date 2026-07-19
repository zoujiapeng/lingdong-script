"""Select perception strategy from runtime intent, never product vocabulary.

Raw task text and window titles are deliberately not classified here.  The
compiler/runtime contract owns semantic intent; live target binding and causal
window deltas own application identity.  This module only projects those
structured signals into a perception mode.
"""
from __future__ import annotations

from typing import Any

_RUNTIME_MODES = frozenset({
    "normal", "pixel_text", "visual", "structured_ui", "vlm_first",
})
_VISUAL_OPERATIONS = frozenset({"draw", "capture", "send", "interact"})


def _structured_intent(task: Any) -> dict[str, Any] | None:
    if hasattr(task, "model_dump"):
        payload = task.model_dump(mode="json")
        return payload if isinstance(payload, dict) else None
    if isinstance(task, dict):
        return dict(task)
    return None


def perception_mode(task: Any, window_title: str = "", *, runtime_mode: str = "") -> str:
    """Return an explicit mode or one derived from structured intent.

    ``window_title`` remains in the public signature for compatibility but is
    not semantic evidence.  A raw string task always selects ``normal``.
    """
    if runtime_mode in _RUNTIME_MODES:
        return runtime_mode
    structured = _structured_intent(task)
    if structured is None:
        return "normal"

    operations_raw = structured.get("operation") or []
    if isinstance(operations_raw, str):
        operations_raw = [operations_raw]
    operations = {
        str(getattr(item, "value", item) or "").strip().casefold()
        for item in operations_raw
    }
    interaction = structured.get("interaction") or {}
    if hasattr(interaction, "model_dump"):
        interaction = interaction.model_dump(mode="json")
    if not isinstance(interaction, dict):
        interaction = {}
    if bool(interaction.get("active")) or operations.intersection(_VISUAL_OPERATIONS):
        return "visual"
    return "normal"


def vision_priority_task(task: Any, window_title: str = "") -> bool:
    """Legacy bool accessor for non-normal structured perception.

    Kept for call-sites that haven't migrated yet. New code should call
    perception_mode() directly.
    """
    return perception_mode(task, window_title) != "normal"
