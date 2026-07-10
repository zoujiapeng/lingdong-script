"""Persistent, local visual anchors for deterministic replay.

Replay anchors are operational cache data, not semantic memories.  They live
in ``memory_meta`` so they can accelerate scripts and ARP replay without
polluting retrieval or skill ranking.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import experience

_VERSION = 1
_META_PREFIX = "replay_anchor_profile:"
_MAX_ANCHORS = 240


def script_profile(path: str | Path) -> str:
    """Return a stable, non-path-leaking profile id for a replay script."""
    resolved = str(Path(path).resolve()).casefold()
    digest = hashlib.sha256(resolved.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"script:{Path(path).name}:{digest}"


def template_name(profile: str, anchor_key: str) -> str:
    digest = hashlib.sha256(f"{profile}\n{anchor_key}".encode("utf-8")).hexdigest()[:24]
    return f"replay-anchor-{digest}"


def _meta_key(profile: str) -> str:
    digest = hashlib.sha256(profile.encode("utf-8", errors="ignore")).hexdigest()
    return f"{_META_PREFIX}{digest}"


def _number_list(value: Any, size: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != size:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= item <= 1.0 for item in numbers):
        return None
    return numbers


def _normalize_anchor(key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    relative_bbox = _number_list(value.get("relative_bbox"), 4)
    click_offset = _number_list(value.get("click_offset"), 2)
    template = Path(str(value.get("template") or "")).name
    if not relative_bbox or not click_offset or not template.endswith(".png"):
        return None
    try:
        return {
            "key": str(key)[:160],
            "text": str(value.get("text") or "")[:300],
            "role": str(value.get("role") or "")[:80],
            "template": template[:180],
            "relative_bbox": relative_bbox,
            "click_offset": click_offset,
            "confidence": max(0.0, min(1.0, float(value.get("confidence") or 0.0))),
            "hits": max(0, int(value.get("hits") or 0)),
            "misses": max(0, int(value.get("misses") or 0)),
            "learned_at": max(0, int(value.get("learned_at") or 0)),
            "updated_at": max(0, int(value.get("updated_at") or 0)),
            "last_used_at": max(0, int(value.get("last_used_at") or 0)),
        }
    except (TypeError, ValueError):
        return None


def load_profile(profile: str, store=None) -> dict[str, dict[str, Any]]:
    profile = str(profile or "").strip()
    if not profile:
        return {}
    try:
        raw = (store or experience._store()).get_meta(_meta_key(profile), "")
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _VERSION:
        return {}
    values = payload.get("anchors")
    if not isinstance(values, dict):
        return {}
    anchors: dict[str, dict[str, Any]] = {}
    for raw_key, value in list(values.items())[:_MAX_ANCHORS]:
        key = str(raw_key).strip()[:160]
        normalized = _normalize_anchor(key, value)
        if key and normalized:
            anchors[key] = normalized
    return anchors


def save_profile(profile: str, anchors: dict[str, Any], store=None) -> int:
    profile = str(profile or "").strip()
    if not profile:
        return 0
    normalized: dict[str, dict[str, Any]] = {}

    def updated_at(item: tuple[str, Any]) -> int:
        try:
            return int((item[1] or {}).get("updated_at") or 0) if isinstance(item[1], dict) else 0
        except (TypeError, ValueError):
            return 0

    ranked = sorted(anchors.items(), key=updated_at, reverse=True)
    for raw_key, value in ranked[:_MAX_ANCHORS]:
        key = str(raw_key).strip()[:160]
        anchor = _normalize_anchor(key, value)
        if key and anchor:
            normalized[key] = anchor
    payload = {
        "version": _VERSION,
        "profile": profile[:300],
        "updated_at": int(time.time()),
        "anchors": normalized,
    }
    try:
        (store or experience._store()).set_meta(
            _meta_key(profile),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except (OSError, TypeError, ValueError):
        return 0
    return len(normalized)
