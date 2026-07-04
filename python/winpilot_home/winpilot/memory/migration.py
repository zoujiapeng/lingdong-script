"""Migration from legacy experience_memory.json into Memory Graph."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import config
from ..utils.log import logger
from .store import MemoryStore, new_id

MIGRATION_KEY = "legacy_json_migrated"


def _as_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text[:300])
    return out


def _default_conditions(recipe: dict[str, Any]) -> list[str]:
    app = str(recipe.get("app") or "").strip()
    if recipe.get("manual"):
        return [
            "Use only when this user-provided memory is relevant to the current task.",
            "Current GUI/file/tool evidence has priority over this memory.",
        ]
    out = ["Use only for the same workflow goal, not just a shared keyword."]
    if app:
        out.append(f'Current app/window should match "{app}" or be intentionally equivalent.')
    if recipe.get("script"):
        out.append("Replay only after the current target screen has been observed.")
    out.append("Do not apply to volatile live-state questions.")
    return out[:5]


def _default_evidence(recipe: dict[str, Any]) -> list[str]:
    if recipe.get("manual"):
        return [
            "Verify against current files, UI, or deterministic tool output before relying on it.",
        ]
    evidence = [
        "Before reuse, observe/list_windows or equivalent evidence must confirm the current target.",
        "After replay/actions, verify final state with observe/read_text/VLM/nonvisual summary or deterministic checks before done.",
    ]
    if recipe.get("provisional"):
        evidence.append("This memory is provisional until a later verified run proves it useful.")
    return evidence[:5]


def _kind_for(recipe: dict[str, Any]) -> str:
    if recipe.get("manual"):
        text = (str(recipe.get("task") or "") + " " + " ".join(_as_list(recipe.get("steps")))).lower()
        if any(k in text for k in ("prefer", "preference", "like to", "always use")):
            return "preference"
        return "fact"
    if recipe.get("script") and not recipe.get("provisional"):
        return "skill"
    return "procedure"


def _anchors(recipe: dict[str, Any]) -> list[str]:
    anchors = []
    for key in ("task", "app"):
        text = str(recipe.get(key) or "").strip()
        if text:
            anchors.append(text[:200])
    for step in _as_list(recipe.get("steps"))[:3]:
        if len(step) <= 80:
            anchors.append(step)
    return anchors


def migrate_json_if_needed(
    store: MemoryStore,
    json_path: str | Path | None = None,
    *,
    force: bool = False,
) -> int:
    """Idempotently migrate legacy recipes into graph nodes.

    Returns the number of newly imported nodes. The JSON file is never deleted.
    """
    if store.disabled:
        return 0
    path = Path(json_path or config.MEMORY_PATH)
    marker = f"{path.resolve()}:{int(path.stat().st_mtime) if path.exists() else 0}"
    if not force and store.get_meta(MIGRATION_KEY) == marker:
        return 0
    if not path.exists():
        store.set_meta(MIGRATION_KEY, marker)
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("legacy memory migration skipped: cannot read json: %s", exc)
        return 0
    if not isinstance(data, list):
        store.set_meta(MIGRATION_KEY, marker)
        return 0
    imported = 0
    for raw in data:
        if not isinstance(raw, dict):
            continue
        recipe = dict(raw)
        node_id = str(recipe.get("id") or new_id())[:32]
        if store.get_node(node_id, hydrate=False):
            continue
        kind = _kind_for(recipe)
        steps = _as_list(recipe.get("steps"))
        title = str(recipe.get("task") or recipe.get("title") or "legacy memory")[:500]
        app = str(recipe.get("app") or "")[:200]
        conditions = _as_list(recipe.get("conditions") or recipe.get("applicability"))
        evidence = _as_list(recipe.get("evidence") or recipe.get("evidence_requirements"))
        if not conditions:
            conditions = _default_conditions(recipe)
        if not evidence:
            evidence = _default_evidence(recipe)
        status = "provisional" if recipe.get("provisional") else "active"
        confidence = 0.35 if recipe.get("provisional") else 0.65
        if recipe.get("successes"):
            confidence = min(0.95, confidence + min(int(recipe.get("successes") or 0), 5) * 0.05)
        node = store.add_node(
            kind,
            title,
            summary=title,
            body="\n".join(steps),
            app=app,
            status=status,
            confidence=confidence,
            payload={**recipe, "legacy_json": True, "migrated_at": int(time.time())},
            source=str(path),
            manual=bool(recipe.get("manual")),
            node_id=node_id,
            conditions=conditions,
            evidence=evidence,
            anchors=_anchors(recipe),
        )
        script = str(recipe.get("script") or "")
        if node and script:
            store.add_artifact(node["id"], "wps_script", script, {"legacy_field": "script"})
        if node:
            imported += 1
    store.set_meta(MIGRATION_KEY, marker)
    return imported
