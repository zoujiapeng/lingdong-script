"""Compatibility layer over the SQLite Memory Graph.

The old public API returned recipe-like dicts backed by
experience_memory.json. Those function names remain stable while storage,
retrieval, migration, and feedback are delegated to memory/* modules.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .. import config
from . import feedback as feedback_mod
from .migration import migrate_json_if_needed
from .retriever import is_volatile_state_task, strong_match, top_reusable_skill
from .runtime import prepare_run_memory
from .store import MemoryStore
from .types import ACTION_KINDS, FACTUAL_KINDS

CONFIG = config.CONFIG
MEMORY_PATH = config.MEMORY_PATH
MEMORY_DB_PATH = config.MEMORY_DB_PATH

_DEFAULT_MEMORY_PATH = config.ROOT_DIR / "experience_memory.json"
_DEFAULT_MEMORY_DB_PATH = config.ROOT_DIR / "memory.db"
_MAX_RECIPES = 50
_MAX_CONDITIONS = 5
_MAX_EVIDENCE = 5
_COMPAT_KINDS = ACTION_KINDS | FACTUAL_KINDS | {"summary"}
_store_cache: tuple[Path, MemoryStore] | None = None

_JUNK_STEP = re.compile(r"\b(element_id|hwnd)\b")
_OLD_FORMAT = re.compile(r"^\w+\(\{.*\}\)$")


def _effective_db_path() -> Path:
    db_path = Path(MEMORY_DB_PATH)
    mem_path = Path(MEMORY_PATH)
    if db_path != _DEFAULT_MEMORY_DB_PATH:
        return db_path
    if mem_path != _DEFAULT_MEMORY_PATH:
        return mem_path.with_suffix(".db")
    return db_path


def _store() -> MemoryStore:
    global _store_cache
    path = _effective_db_path()
    if _store_cache is None or _store_cache[0] != path:
        _store_cache = (path, MemoryStore(path))
    store = _store_cache[1]
    migrate_json_if_needed(store, MEMORY_PATH)
    return store


def _string_list(values: Any, max_items: int, max_chars: int = 220) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text[:max_chars])
        if len(out) >= max_items:
            break
    return out


def _default_conditions(recipe: dict[str, Any]) -> list[str]:
    app = str(recipe.get("app") or "").strip()
    if recipe.get("manual"):
        return [
            "Use only when this user-provided note is relevant to the current task.",
            "Current GUI/file/tool evidence has priority over this memory.",
        ]
    out = ["Use only for the same workflow goal, not just a shared keyword."]
    if app:
        out.append(f'Current target app/window should match "{app}" or be intentionally equivalent.')
    if recipe.get("script"):
        out.append("Replay only after the target screen has been observed in the current run.")
    out.append("Do not apply to live-state questions about current foreground, login, status, or visibility.")
    return out[:_MAX_CONDITIONS]


def _default_evidence(recipe: dict[str, Any]) -> list[str]:
    if recipe.get("manual"):
        return [
            "Treat as a hint; verify against current files, UI, or deterministic output before relying on it.",
        ]
    out = [
        "Before reuse, observe/list_windows or equivalent evidence must confirm the current target.",
        "After replay/actions, verify final state with observe/read_text/VLM/nonvisual summary or deterministic checks before done.",
    ]
    if recipe.get("provisional"):
        out.append("This recipe is provisional until a later verified run proves it useful.")
    return out[:_MAX_EVIDENCE]


def _clean_steps(steps: list[Any]) -> list[str]:
    clean = []
    for raw in steps or []:
        step = str(raw).strip()
        if _OLD_FORMAT.match(step) and _JUNK_STEP.search(step):
            continue
        if step:
            clean.append(step[:300])
    return clean[:30]


def _script(node: dict[str, Any]) -> str:
    payload = node.get("payload") or {}
    script = str(payload.get("script") or "")
    if script:
        return script
    for artifact in node.get("artifacts") or []:
        if artifact.get("kind") == "wps_script":
            return str(artifact.get("uri") or "")
    return ""


def _recipe_from_node(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node or node.get("status") in {"retired", "superseded"}:
        return None
    payload = dict(node.get("payload") or {})
    steps = payload.get("steps")
    if not isinstance(steps, list):
        body = str(node.get("body") or "")
        steps = [line.strip() for line in body.splitlines() if line.strip()]
    manual = bool(node.get("manual") or payload.get("manual") or node.get("kind") in FACTUAL_KINDS)
    provisional = bool(payload.get("provisional") or node.get("status") == "provisional")
    recipe = {
        "id": node.get("id", ""),
        "task": payload.get("task") or node.get("title") or node.get("summary") or "",
        "app": payload.get("app") or node.get("app") or "",
        "steps": _clean_steps(steps),
        "uses": int(payload.get("uses") or 0),
        "successes": int(payload.get("successes") or 0),
        "provisional": provisional,
        "manual": manual,
        "script": payload.get("script") or _script(node),
        "conditions": _string_list(node.get("conditions"), _MAX_CONDITIONS)
        or _default_conditions(payload),
        "evidence": _string_list(node.get("evidence"), _MAX_EVIDENCE)
        or _default_evidence(payload),
        "kind": node.get("kind"),
        "status": node.get("status"),
        "confidence": node.get("confidence"),
        "stage_type": str(payload.get("stage_type") or ""),
        "parameters": dict(payload.get("parameters") or {}),
        "outputs": list(payload.get("outputs") or []),
        "verification": list(payload.get("verification") or []),
        "app_version": str(payload.get("app_version") or ""),
        "failure_reason": str(payload.get("failure_reason") or ""),
    }
    return recipe


def _node_from_recipe_id(recipe_id: str) -> dict[str, Any] | None:
    return _store().get_node(recipe_id)


def enabled() -> bool:
    return bool(CONFIG.get("agent", "experience_memory", default=True))


def record(
    task: str,
    app_hint: str,
    steps: list[str],
    verified: bool = True,
    script: str = "",
    conditions: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any] | None:
    if not enabled() or not steps:
        return None
    if is_volatile_state_task(task):
        return None
    clean_steps = _clean_steps(steps)
    if not clean_steps:
        return None
    store = _store()
    # Preserve old replacement semantics for identical task recipes.
    for node in store.list_nodes(kinds=ACTION_KINDS, include_inactive=True, limit=500):
        if str(node.get("title") or "") == task:
            store.delete_node(node["id"])
    provisional = not (verified and script)
    # A recorded script is a skill candidate even before it is trusted. Its
    # provisional status keeps retrieval from replaying it until promotion.
    kind = "skill" if script else "procedure"
    payload = {
        "task": task[:200],
        "app": app_hint[:80],
        "steps": clean_steps,
        "ts": int(time.time()),
        "provisional": provisional,
        "manual": False,
        "script": script,
        "uses": 0,
        "successes": 0,
    }
    conds = _string_list(conditions, _MAX_CONDITIONS) or _default_conditions(payload)
    evid = _string_list(evidence, _MAX_EVIDENCE) or _default_evidence(payload)
    node = store.add_node(
        kind,
        task[:500],
        summary=task[:500],
        body="\n".join(clean_steps),
        app=app_hint[:200],
        status="provisional" if provisional else "active",
        confidence=0.45 if provisional else 0.7,
        payload=payload,
        manual=False,
        conditions=conds,
        evidence=evid,
        anchors=[task, app_hint] + clean_steps[:3],
    )
    if node and script:
        store.add_artifact(node["id"], "wps_script", script)
    return _recipe_from_node(store.get_node(node["id"])) if node else None


def promote_skill(recipe_id: str, evidence: str = "", status: str = "verified") -> dict[str, Any] | None:
    """Promote a replayable candidate only after explicit external evidence."""
    store = _store()
    node = store.get_node(recipe_id)
    if not node:
        return None
    payload = dict(node.get("payload") or {})
    script = str(payload.get("script") or _script(node)).strip()
    if not script:
        return None
    if evidence.strip():
        store.add_evidence(recipe_id, evidence.strip(), required=True)
    store.record_attempt(recipe_id, "manual_verify", status=status, success=True)
    payload.update({
        "script": script,
        "provisional": False,
        "successes": int(payload.get("successes") or 0) + 1,
        "uses": int(payload.get("uses") or 0) + 1,
    })
    updated = store.update_node(
        recipe_id,
        {
            "kind": "skill",
            "status": "active",
            "confidence": min(0.98, float(node.get("confidence") or 0.5) + 0.1),
            "payload": payload,
        },
    )
    return _recipe_from_node(updated)


def feedback(recipe_ids: list[str], success: bool) -> None:
    if not enabled() or not recipe_ids:
        return
    feedback_mod.apply_feedback_to_ids(_store(), recipe_ids, success)


def hints_with_ids(task: str, notes_only: bool = False, run_id: str = "") -> tuple[str, list[str]]:
    if not enabled():
        return "", []
    pack = prepare_run_memory(
        task,
        {"notes_only": notes_only, "coding": notes_only},
        run_id=run_id,
        store=_store(),
    )
    ids = [
        rid for rid in pack.selected_ids
        if (get(rid) is not None or (_store().get_node(rid) or {}).get("kind") == "warning")
    ]
    return pack.text, ids


def hints_for(task: str) -> str:
    return hints_with_ids(task)[0]


def note_hints(task: str) -> str:
    return hints_with_ids(task, notes_only=True)[0]


def reusable_script_for(task: str, recipe_id: str = "") -> tuple[str, dict | None]:
    if not enabled() or is_volatile_state_task(task):
        return "", None
    store = _store()
    if recipe_id:
        node = store.get_node(recipe_id)
        recipe = _recipe_from_node(node)
        if (
            recipe
            and recipe.get("script")
            and not recipe.get("provisional")
            and node
            and node.get("kind") == "skill"
        ):
            return str(recipe["script"]), recipe
        return "", recipe
    node, _score = top_reusable_skill(task, store)
    recipe = _recipe_from_node(node)
    if recipe and recipe.get("script") and strong_match(task, str(recipe.get("task") or "")):
        return str(recipe["script"]), recipe
    return "", None


def reusable_stage_script_for(
    stage_type: str,
    parameters: dict[str, Any],
    recipe_id: str = "",
    *,
    app_version: str = "",
) -> tuple[str, dict | None]:
    """Return a parameterized stage skill only when its preconditions match."""
    if not enabled():
        return "", None

    def matches(recipe: dict[str, Any] | None) -> bool:
        if not recipe or not recipe.get("script") or recipe.get("provisional"):
            return False
        if str(recipe.get("stage_type") or "") != str(stage_type):
            return False
        recorded_version = str(recipe.get("app_version") or "")
        if recorded_version and recorded_version != app_version:
            return False
        expected = dict(recipe.get("parameters") or {})
        for key, value in expected.items():
            if key in {"max_steps", "original_task"} or value in (None, "", [], {}):
                continue
            if key not in parameters or str(parameters[key]) != str(value):
                return False
        return True

    if recipe_id:
        recipe = _recipe_from_node(_store().get_node(recipe_id))
        return (str(recipe.get("script") or ""), recipe) if matches(recipe) else ("", recipe)

    candidates: list[dict[str, Any]] = []
    for node in _store().list_nodes(kinds={"skill"}, include_inactive=False, limit=200):
        recipe = _recipe_from_node(node)
        if matches(recipe):
            candidates.append(recipe or {})
    if not candidates:
        return "", None
    candidates.sort(
        key=lambda item: (
            int(item.get("successes") or 0),
            float(item.get("confidence") or 0),
            int(item.get("uses") or 0),
        ),
        reverse=True,
    )
    recipe = candidates[0]
    return str(recipe.get("script") or ""), recipe


def all_recipes() -> list[dict]:
    if not enabled():
        return []
    recipes = []
    for node in _store().list_nodes(kinds=_COMPAT_KINDS, limit=_MAX_RECIPES):
        recipe = _recipe_from_node(node)
        if recipe:
            recipes.append(recipe)
    return recipes


def get(recipe_id: str) -> dict | None:
    return _recipe_from_node(_node_from_recipe_id(recipe_id))


def add(
    task: str,
    app: str = "",
    steps: list[str] | None = None,
    manual: bool = True,
    conditions: list[str] | None = None,
    evidence: list[str] | None = None,
    *,
    kind: str = "",
    title: str = "",
    body: str = "",
    summary: str = "",
    anchors: list[str] | None = None,
) -> dict:
    chosen_kind = kind or ("fact" if manual else "procedure")
    clean_steps = _clean_steps(steps or ([task] if manual else []))
    payload = {
        "task": (title or task or "")[:200],
        "app": (app or "")[:80],
        "steps": clean_steps,
        "ts": int(time.time()),
        "manual": manual,
        "provisional": chosen_kind in ACTION_KINDS and not manual,
        "uses": 0,
        "successes": 0,
    }
    conds = _string_list(conditions, _MAX_CONDITIONS) or _default_conditions(payload)
    evid = _string_list(evidence, _MAX_EVIDENCE) or _default_evidence(payload)
    node = _store().add_node(
        chosen_kind,
        (title or task or "")[:500],
        summary=summary or (task or "")[:500],
        body=body or "\n".join(clean_steps),
        app=app[:200],
        status="active",
        confidence=0.65 if manual else 0.45,
        payload=payload,
        manual=manual,
        conditions=conds,
        evidence=evid,
        anchors=anchors or [task, app],
    )
    return _recipe_from_node(node) or {}


def update(recipe_id: str, patch: dict) -> dict | None:
    node = _store().get_node(recipe_id)
    if not node:
        return None
    payload = dict(node.get("payload") or {})
    node_patch: dict[str, Any] = {}
    if "task" in patch or "title" in patch:
        value = str(patch.get("title") if "title" in patch else patch.get("task"))
        node_patch["title"] = value[:500]
        node_patch["summary"] = value[:1000]
        payload["task"] = value[:200]
    if "summary" in patch:
        node_patch["summary"] = str(patch["summary"])[:1000]
    if "body" in patch:
        node_patch["body"] = str(patch["body"])[:20000]
    if "app" in patch:
        value = str(patch["app"])[:200]
        node_patch["app"] = value
        payload["app"] = value[:80]
    if "steps" in patch:
        payload["steps"] = _clean_steps(patch.get("steps") or [])
        node_patch["body"] = "\n".join(payload["steps"])
    for key in ("stage_type", "parameters", "outputs", "verification", "app_version", "failure_reason"):
        if key in patch:
            payload[key] = patch[key]
    if "provisional" in patch:
        payload["provisional"] = bool(patch["provisional"])
        node_patch["status"] = "provisional" if patch["provisional"] else "active"
    for key in ("status", "confidence", "kind"):
        if key in patch:
            node_patch[key] = patch[key]
    if "conditions" in patch:
        node_patch["conditions"] = _string_list(patch["conditions"], _MAX_CONDITIONS)
    if "evidence" in patch:
        node_patch["evidence"] = _string_list(patch["evidence"], _MAX_EVIDENCE)
    if "anchors" in patch:
        node_patch["anchors"] = _string_list(patch["anchors"], 12)
    if "supersedes" in patch and patch["supersedes"]:
        old_id = str(patch["supersedes"])
        _store().add_edge(recipe_id, old_id, "supersedes")
        _store().retire_node(old_id, "superseded")
    node_patch["payload"] = payload
    updated = _store().update_node(recipe_id, node_patch)
    return _recipe_from_node(updated)


def delete(recipe_id: str) -> bool:
    return _store().delete_node(recipe_id)


def delete_matching(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return []
    removed = []
    for node in _store().list_nodes(kinds=_COMPAT_KINDS, limit=500):
        recipe = _recipe_from_node(node)
        if not recipe:
            continue
        haystack = (
            recipe.get("task", "")
            + recipe.get("app", "")
            + " ".join(recipe.get("steps", []))
        ).lower()
        if q in haystack:
            _store().retire_node(recipe["id"])
            removed.append(recipe)
    return removed


def clear() -> None:
    _store().clear()
