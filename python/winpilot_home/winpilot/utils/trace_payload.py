"""Compact, non-mutating payloads before diagnostic trace persistence."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any


_FRAME_ID_RE = re.compile(r"Visual closed-loop frame\s*#(\d+)", re.IGNORECASE)
_RAW_FRAME_KEYS = {
    "base64",
    "b64",
    "frame_b64",
    "image_b64",
    "screenshot_b64",
}
_PRIVATE_TEXT_KEYS = {"user_result"}


def _frame_id_in_text(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _FRAME_ID_RE.search(value)
    return int(match.group(1)) if match else None


def _frame_metadata(
    value: Any,
    *,
    frame_id: int | None,
    assume_encoded: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    encoded = value
    if not assume_encoded:
        comma = value.find(",")
        if comma <= 0:
            return None
        header = value[:comma].casefold()
        if not header.startswith("data:image/") or ";base64" not in header:
            return None
        encoded = value[comma + 1:]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            raw = base64.b64decode(padded, validate=False)
        except (binascii.Error, ValueError):
            raw = encoded.encode("utf-8", errors="replace")
    return {
        "frame_present": True,
        "frame_id": frame_id,
        "frame_byte_count": len(raw),
        "frame_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _list_frame_id(values: list[Any], fallback: int | None) -> int | None:
    frame_id = fallback
    for item in values:
        candidate = None
        if isinstance(item, str):
            candidate = _frame_id_in_text(item)
        elif isinstance(item, dict):
            candidate = _frame_id_in_text(item.get("text"))
        if candidate is not None:
            frame_id = candidate
    return frame_id


def sanitize_trace_payload(value: Any, *, frame_id: int | None = None) -> Any:
    """Return a trace-safe copy with inline frames replaced by tiny metadata.

    The original object is never changed: the live API request retains its
    image parts, while JSONL/debug traces keep only presence, identity, decoded
    byte count, and a content digest.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _frame_metadata(value, frame_id=frame_id) or value
    if isinstance(value, (list, tuple)):
        local_frame_id = _list_frame_id(list(value), frame_id)
        return [
            sanitize_trace_payload(item, frame_id=local_frame_id)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    local_frame_id = frame_id
    for key in ("visual_frame_id", "frame_id"):
        candidate = value.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            local_frame_id = candidate
            break

    out: dict[str, Any] = {}
    for key, child in value.items():
        key_text = str(key)
        key_norm = key_text.casefold()
        if key_norm in _PRIVATE_TEXT_KEYS and isinstance(child, str):
            encoded = child.encode("utf-8", errors="replace")
            out[key_text] = {
                "private_text_redacted": True,
                "text_char_count": len(child),
                "text_sha256": hashlib.sha256(encoded).hexdigest(),
            }
            continue
        if key_norm in {"image_url", "input_image"}:
            url = child.get("url") if isinstance(child, dict) else child
            metadata = _frame_metadata(url, frame_id=local_frame_id)
            if metadata is not None:
                out[key_text] = metadata
                continue
        if key_norm in _RAW_FRAME_KEYS:
            metadata = _frame_metadata(
                child,
                frame_id=local_frame_id,
                assume_encoded=True,
            )
            if metadata is not None:
                trace_key = "frame"
                suffix = 2
                while trace_key in out:
                    trace_key = f"frame_{suffix}"
                    suffix += 1
                out[trace_key] = metadata
                continue
        out[key_text] = sanitize_trace_payload(child, frame_id=local_frame_id)
    return out
