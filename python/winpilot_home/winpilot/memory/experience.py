"""Compatibility layer over the SQLite Memory Graph.

The old public API returned recipe-like dicts backed by
experience_memory.json. Those function names remain stable while storage,
retrieval, migration, and feedback are delegated to memory/* modules.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .. import config
from .applicability import build_applicability, evaluate
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
_TRUSTED_VERIFICATION_STATES = {"source_run_verified", "replay_verified"}
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
        "stage_obligations": list(payload.get("stage_obligations") or []),
        "app_version": str(payload.get("app_version") or ""),
        "failure_reason": str(payload.get("failure_reason") or ""),
        "applicability": dict(payload.get("applicability") or {}),
        "script_sha256": str(payload.get("script_sha256") or ""),
        "verification_state": str(payload.get("verification_state") or ""),
        "verified_at": payload.get("verified_at"),
        "verification_replay_id": str(payload.get("verification_replay_id") or ""),
    }
    return recipe


def _node_from_recipe_id(recipe_id: str) -> dict[str, Any] | None:
    return _store().get_node(recipe_id)


def enabled() -> bool:
    return bool(CONFIG.get("agent", "experience_memory", default=True))


def _script_path(script: str) -> Path | None:
    name = str(script or "").strip()
    if not name:
        return None
    root = Path(config.SCRIPTS_DIR).resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def script_sha256(script: str) -> str:
    path = _script_path(script)
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _script_integrity_ok(recipe: dict[str, Any] | None) -> bool:
    if not recipe:
        return False
    expected = str(recipe.get("script_sha256") or "").strip()
    actual = script_sha256(str(recipe.get("script") or ""))
    return bool(expected and actual and expected == actual)


def _replay_trust_ok(recipe: dict[str, Any] | None) -> bool:
    return bool(
        recipe
        and recipe.get("status") == "active"
        and not recipe.get("provisional")
        and recipe.get("verification_state") in _TRUSTED_VERIFICATION_STATES
        and _script_integrity_ok(recipe)
    )


def script_trust(script: str) -> dict[str, Any]:
    """Return trust metadata for an artifact listed in the frontend."""
    actual = script_sha256(script)
    best: dict[str, Any] | None = None
    trusted = False
    for node in _store().list_nodes(kinds={"skill"}, include_inactive=True, limit=500):
        recipe = _recipe_from_node(node)
        if not recipe or str(recipe.get("script") or "") != str(script or ""):
            continue
        candidate_trusted = bool(
            recipe.get("status") == "active"
            and not recipe.get("provisional")
            and recipe.get("verification_state") in _TRUSTED_VERIFICATION_STATES
            and recipe.get("script_sha256") == actual
        )
        if candidate_trusted:
            best = recipe
            trusted = True
            break
        if best is None or (
            recipe.get("status") == "active"
            and best.get("status") != "active"
        ):
            best = recipe
    return {
        "trust": "verified" if trusted else "draft",
        "memory_id": (best or {}).get("id"),
        "memory_status": (best or {}).get("status"),
        "verification_state": (best or {}).get("verification_state", ""),
        "verification_count": len((best or {}).get("verification") or []),
        "script_sha256": actual,
    }


def _link_prior_versions(
    store: MemoryStore,
    node: dict[str, Any] | None,
    task: str,
    *,
    retire_previous: bool,
) -> None:
    if not node:
        return

    def scope(value: dict[str, Any]) -> dict[str, Any]:
        payload = dict(value.get("payload") or {})
        applicability = dict(payload.get("applicability") or {})
        return {
            "app": str(applicability.get("app") or payload.get("app") or value.get("app") or ""),
            "stage_type": str(applicability.get("stage_type") or payload.get("stage_type") or ""),
            "parameters": dict(applicability.get("parameters") or payload.get("parameters") or {}),
            "required_anchors": list(applicability.get("required_anchors") or []),
            "safety_scope": str(applicability.get("safety_scope") or "desktop_gui"),
        }

    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())

    def same_text(left: str, right: str) -> bool:
        a, b = normalized(left), normalized(right)
        return bool(a and b and (a == b or a in b or b in a))

    current_scope = scope(node)
    for previous in store.list_nodes(kinds=ACTION_KINDS, include_inactive=True, limit=500):
        if previous.get("id") == node.get("id"):
            continue
        if str(previous.get("title") or "") != str(task or ""):
            continue
        previous_scope = scope(previous)
        if not same_text(current_scope["app"], previous_scope["app"]):
            continue
        if normalized(current_scope["stage_type"]) != normalized(previous_scope["stage_type"]):
            continue
        if current_scope["parameters"] != previous_scope["parameters"]:
            continue
        if {
            normalized(item) for item in current_scope["required_anchors"] if normalized(item)
        } != {
            normalized(item) for item in previous_scope["required_anchors"] if normalized(item)
        }:
            continue
        if normalized(current_scope["safety_scope"]) != normalized(previous_scope["safety_scope"]):
            continue
        store.add_edge(node["id"], previous["id"], "supersedes")
        if retire_previous and previous.get("status") not in {"retired", "superseded"}:
            store.retire_node(previous["id"], "superseded")


def record(
    task: str,
    app_hint: str,
    steps: list[str],
    verified: bool = False,
    script: str = "",
    conditions: list[str] | None = None,
    evidence: list[str] | None = None,
    *,
    verification: list[dict[str, Any]] | None = None,
    app_version: str = "",
    stage_type: str = "",
    parameters: dict[str, Any] | None = None,
    required_anchors: list[str] | None = None,
    source_run_id: str = "",
    source_verification: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not enabled() or not steps:
        return None
    if is_volatile_state_task(task):
        return None
    clean_steps = _clean_steps(steps)
    if not clean_steps:
        return None
    store = _store()
    source_proof = dict(source_verification or {})
    source_verified = bool(
        source_run_id
        and (
            source_proof.get("done_verified") is True
            or source_proof.get("had_verified_action") is True
        )
        and source_proof.get("done_unverified") is not True
    )
    candidate_digest = script_sha256(script) if script else ""
    trusted_source = bool(verified and source_verified and candidate_digest)
    digest = candidate_digest if trusted_source else ""
    provisional = not trusted_source
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
        "verification": list(verification or []),
        "app_version": str(app_version or ""),
        "stage_type": str(stage_type or ""),
        "parameters": dict(parameters or {}),
        "applicability": build_applicability(
            task,
            app_hint,
            app_version=app_version,
            stage_type=stage_type,
            parameters=parameters,
            required_anchors=required_anchors,
        ),
        "script_sha256": digest,
        "verification_state": "source_run_verified" if not provisional else "provisional",
        "verified_source_run_id": source_run_id if not provisional else "",
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
        source=source_run_id,
        manual=False,
        conditions=conds,
        evidence=evid,
        anchors=[task, app_hint] + clean_steps[:3],
    )
    if node and script:
        store.add_artifact(node["id"], "wps_script", script)
    if node and not provisional:
        observation = store.add_node(
            "observation",
            f"Source run verified: {task[:300]}",
            summary="The originating run supplied independently verified action evidence.",
            status="active",
            confidence=0.85,
            payload={
                "run_id": source_run_id,
                "script": script,
                "script_sha256": digest,
                **source_proof,
            },
            source=source_run_id,
            manual=False,
            anchors=[task, app_hint, source_run_id],
        )
        if observation:
            store.add_edge(node["id"], observation["id"], "verified_by")
    _link_prior_versions(store, node, task, retire_previous=not provisional)
    return _recipe_from_node(store.get_node(node["id"])) if node else None


def promote_skill(recipe_id: str, evidence: str = "", status: str = "verified") -> dict[str, Any] | None:
    """Deprecated fail-closed manual promotion hook.

    Text supplied by a caller is not execution evidence.  Use
    :func:`promote_skill_from_replay` with a completed replay result instead.
    """
    return None


def promote_skill_from_replay(
    recipe_id: str,
    replay_id: str,
    replay_result: dict[str, Any],
    *,
    expected_script: str = "",
    expected_script_sha256: str = "",
    expected_verification: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Promote a candidate only after machine-checked postflight evidence."""
    store = _store()
    node = store.get_node(recipe_id)
    if not node:
        return None
    payload = dict(node.get("payload") or {})
    script = str(payload.get("script") or _script(node)).strip()
    if not script:
        return None
    verification = payload.get("verification") or []
    postflight = replay_result.get("postflight") or {}
    checks = postflight.get("checks") or []
    digest = script_sha256(script)
    passed = bool(
        replay_id
        and node.get("kind") in ACTION_KINDS
        and node.get("status") not in {"retired", "superseded"}
        and replay_result.get("ok") is True
        and isinstance(verification, list)
        and verification
        and postflight.get("required") is True
        and postflight.get("ok") is True
        and checks
        and all(isinstance(item, dict) and item.get("ok") is True for item in checks)
        and digest
        and (not expected_script or script == expected_script)
        and (not expected_script_sha256 or digest == expected_script_sha256)
        and (
            expected_verification is None
            or verification == expected_verification
        )
    )
    if not passed:
        store.record_attempt(
            recipe_id,
            "verification_replay_failed",
            run_id=replay_id,
            status="evidence_failed",
            success=False,
            detail={
                "postflight": postflight,
                "script": script,
                "expected_script": expected_script,
                "expected_script_sha256": expected_script_sha256,
                "actual_script_sha256": digest,
            },
        )
        return None
    evidence_summary = json.dumps(
        {
            "replay_id": replay_id,
            "script_sha256": digest,
            "checks": checks,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    store.add_evidence(recipe_id, evidence_summary, required=True)
    store.record_attempt(
        recipe_id,
        "verification_replay_passed",
        run_id=replay_id,
        status="verified",
        success=True,
        detail={"postflight": postflight, "script": script, "script_sha256": digest},
    )
    payload.update({
        "script": script,
        "provisional": False,
        "successes": int(payload.get("successes") or 0) + 1,
        "uses": int(payload.get("uses") or 0) + 1,
        "script_sha256": digest,
        "verification_state": "replay_verified",
        "verification_replay_id": replay_id,
        "verified_at": int(time.time()),
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
    observation = store.add_node(
        "observation",
        f"Verification replay passed: {str(node.get('title') or recipe_id)[:300]}",
        summary=f"{len(checks)} structured postflight check(s) passed.",
        status="active",
        confidence=0.95,
        payload={
            "replay_id": replay_id,
            "script": script,
            "script_sha256": digest,
            "checks": checks,
        },
        source=replay_id,
        manual=False,
        anchors=[replay_id, script, digest],
    )
    if observation:
        store.add_edge(recipe_id, observation["id"], "verified_by")
    _link_prior_versions(store, updated, str(node.get("title") or ""), retire_previous=True)
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


def reusable_script_for(
    task: str,
    recipe_id: str = "",
    *,
    runtime_profile: Any = None,
    session_context: Any = None,
    parameters: dict[str, Any] | None = None,
    require_runtime_context: bool = False,
) -> tuple[str, dict | None]:
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
            and node.get("status") == "active"
            and strong_match(task, str(recipe.get("task") or ""))
            and evaluate(
                node,
                task,
                runtime_profile=runtime_profile,
                session_context=session_context,
                parameters=parameters,
                require_runtime_context=require_runtime_context,
            ).allowed
            and _replay_trust_ok(recipe)
        ):
            return str(recipe["script"]), recipe
        return "", recipe
    node, _score = top_reusable_skill(
        task,
        store,
        runtime_profile,
        session_context,
        parameters=parameters,
        require_runtime_context=require_runtime_context,
    )
    recipe = _recipe_from_node(node)
    if (
        recipe
        and recipe.get("script")
        and strong_match(task, str(recipe.get("task") or ""))
        and _replay_trust_ok(recipe)
    ):
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
        if (
            not recipe
            or not recipe.get("script")
            or recipe.get("provisional")
            or not _replay_trust_ok(recipe)
        ):
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
        decision = evaluate(
            recipe,
            str(recipe.get("task") or stage_type),
            runtime_profile={"stage_type": stage_type, "app_version": app_version},
            parameters=parameters,
            require_runtime_context=False,
        )
        if not decision.allowed:
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
        status="provisional" if payload["provisional"] else "active",
        confidence=0.65 if manual else 0.45,
        payload=payload,
        manual=manual,
        conditions=conds,
        evidence=evid,
        anchors=anchors or [task, app],
    )
    return _recipe_from_node(node) or {}


def update(recipe_id: str, patch: dict, *, preserve_trust: bool = False) -> dict | None:
    node = _store().get_node(recipe_id)
    if not node:
        return None
    payload = dict(node.get("payload") or {})
    node_patch: dict[str, Any] = {}
    action_memory = node.get("kind") in ACTION_KINDS
    trust_sensitive = {
        "task", "title", "app", "steps", "conditions", "evidence", "anchors",
        "stage_type", "parameters", "outputs", "verification", "stage_obligations",
        "app_version", "applicability", "script_sha256",
    }
    if action_memory and patch.get("provisional") is False and not preserve_trust:
        raise ValueError("action memory can only be promoted by verified replay")
    if action_memory and patch.get("status") == "active" and not preserve_trust:
        raise ValueError("action memory can only be activated by verified replay")
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
    for key in (
        "stage_type",
        "parameters",
        "outputs",
        "verification",
        "stage_obligations",
        "app_version",
        "failure_reason",
        "applicability",
        "script_sha256",
        "verification_state",
        "verification_replay_id",
        "verified_at",
    ):
        if key in patch:
            payload[key] = patch[key]
    if "provisional" in patch:
        payload["provisional"] = bool(patch["provisional"])
        node_patch["status"] = "provisional" if patch["provisional"] else "active"
    if action_memory and not preserve_trust and trust_sensitive.intersection(patch):
        payload["provisional"] = True
        payload["verification_state"] = "candidate_edited"
        payload.pop("verification_replay_id", None)
        payload.pop("verified_at", None)
        node_patch["status"] = "provisional"
    for key in ("status", "confidence", "kind"):
        if key in patch:
            node_patch[key] = patch[key]
    if action_memory and not preserve_trust and trust_sensitive.intersection(patch):
        node_patch["status"] = "provisional"
    if "conditions" in patch:
        node_patch["conditions"] = _string_list(patch["conditions"], _MAX_CONDITIONS)
    if "evidence" in patch:
        node_patch["evidence"] = _string_list(patch["evidence"], _MAX_EVIDENCE)
    if "anchors" in patch:
        node_patch["anchors"] = _string_list(patch["anchors"], 12)
    if "supersedes" in patch and patch["supersedes"]:
        old_id = str(patch["supersedes"])
        _store().add_edge(recipe_id, old_id, "supersedes")
        integrity_recipe = {**(_recipe_from_node(node) or {}), **payload}
        if not action_memory or (
            not payload.get("provisional")
            and node_patch.get("status", node.get("status")) == "active"
            and _replay_trust_ok(integrity_recipe)
        ):
            _store().retire_node(old_id, "superseded")
    if any(
        key in patch
        for key in ("task", "title", "app", "stage_type", "parameters", "app_version", "anchors")
    ):
        previous = dict(payload.get("applicability") or {})
        payload["applicability"] = build_applicability(
            str(payload.get("task") or node_patch.get("title") or node.get("title") or ""),
            str(payload.get("app") or node_patch.get("app") or node.get("app") or ""),
            app_version=str(payload.get("app_version") or ""),
            stage_type=str(payload.get("stage_type") or ""),
            parameters=payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {},
            required_anchors=(
                patch.get("anchors")
                if "anchors" in patch
                else list(previous.get("required_anchors") or [])
            ),
        )
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
