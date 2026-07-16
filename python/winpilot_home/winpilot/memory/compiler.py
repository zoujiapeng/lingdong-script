"""Compile completed agent runs into Memory Graph nodes."""
from __future__ import annotations

import re
from typing import Any

from .. import config
from ..utils.log import logger
from .applicability import build_applicability
from .experience import _link_prior_versions, script_sha256
from .migration import migrate_json_if_needed
from .retriever import is_volatile_state_task
from .store import MemoryStore

_JUNK_STEP = re.compile(r"\b(element_id|hwnd)\b")
_OLD_FORMAT = re.compile(r"^\w+\(\{.*\}\)$")
_COORD_HEAVY = re.compile(r"\b(x|y|x1|y1|x2|y2)=?\s*\d{2,}\b")


def _enabled() -> bool:
    return bool(config.CONFIG.get("agent", "experience_memory", default=True))


def _clean_steps(steps: list[str]) -> list[str]:
    clean = []
    for raw in steps or []:
        step = str(raw).strip()
        if not step:
            continue
        if _OLD_FORMAT.match(step) and _JUNK_STEP.search(step):
            continue
        if _COORD_HEAVY.search(step) and not any(q in step for q in ('"', "'")):
            continue
        clean.append(step[:300])
    return clean[:30]


def _conditions(task: str, app: str, script: str) -> list[str]:
    out = ["Use only for the same workflow goal, not just a shared keyword."]
    if app:
        out.append(f'Current app/window should match "{app}" or be intentionally equivalent.')
    if script:
        out.append("Replay only after observing the target screen in the current run.")
    out.append("Do not apply to volatile live-state questions.")
    return out


def _evidence(provisional: bool) -> list[str]:
    out = [
        "Before reuse, current screen/window evidence must satisfy the conditions.",
        "After replay/actions, verify final state with observe/read_text/VLM/nonvisual summary or deterministic checks before done.",
    ]
    if provisional:
        out.append("This procedure is provisional until a verified run proves it useful.")
    return out


def _verified_run(runner: Any) -> bool:
    return bool(
        (
            getattr(runner, "_done_verified", False)
            or (
                getattr(runner, "_had_verified_action", False)
                and getattr(runner, "_last_verify_ok", None) is not False
            )
        )
        and not getattr(runner, "_done_unverified", False)
    )


def ingest_run(runner: Any, *, store: MemoryStore | None = None) -> dict[str, Any]:
    """Create episode and reusable memory nodes from a finished AgentRunner.

    The process-level experience switch controls *automatic* ingestion.  An
    explicitly supplied store is an explicit compile/recovery request and must
    not be short-circuited by mutable local configuration; otherwise a caller
    cannot idempotently finish a run whose episode was written before the
    switch changed.  Normal AgentRunner ingestion does not pass a store and is
    additionally guarded by ``RuntimeProfile.experience_enabled``.
    """
    if store is None and not _enabled():
        return {}
    memory_store = store if store is not None else MemoryStore(config.MEMORY_DB_PATH)
    if memory_store.disabled:
        return {}
    migrate_json_if_needed(memory_store, config.MEMORY_PATH)
    task = str(getattr(runner, "task", "") or "")
    run_id = str(getattr(runner, "run_id", "") or "")
    status = str(getattr(runner, "status", "") or "")
    result_text = str(getattr(runner, "result_text", "") or "")
    steps = _clean_steps(list(getattr(runner, "_steps_log", []) or []))
    app_hint = ""
    try:
        snapshot = getattr(getattr(runner, "session", None), "snapshot", None)
        app_hint = str(getattr(snapshot, "title", "") or "")
    except Exception:
        app_hint = ""
    prior_for_run = [
        node
        for node in memory_store.list_nodes(include_inactive=True, limit=500)
        if run_id and str(node.get("source") or "") == run_id
    ]
    prior_episode = next((node for node in prior_for_run if node.get("kind") == "episode"), None)
    prior_recipe = next((node for node in prior_for_run if node.get("kind") in {"procedure", "skill"}), None)
    prior_warning = next((node for node in prior_for_run if node.get("kind") == "warning"), None)

    episode = prior_episode or memory_store.add_node(
        "episode",
        task[:500] or run_id or "agent run",
        summary=f"Run {status}: {task[:200]}",
        body=result_text[:2000],
        app=app_hint[:200],
        status="active",
        confidence=0.5,
        payload={
            "run_id": run_id,
            "status": status,
            "steps": steps,
            "usage": getattr(runner, "usage_total", {}),
        },
        source=run_id,
        manual=False,
        anchors=[task, app_hint],
    )

    created: dict[str, Any] = {"episode": episode}
    if prior_recipe:
        created["recipe"] = prior_recipe
    if prior_warning:
        created["warning"] = prior_warning
    is_coding = bool(getattr(runner, "_is_coding", False))
    if status == "done" and not prior_recipe and not is_coding and steps and not is_volatile_state_task(task):
        verified = _verified_run(runner)
        script = ""
        if verified:
            try:
                from ..recorder import trace as trace_mod

                script_path = trace_mod.export_automation_ir(run_id)
                script = script_path.name
            except (FileNotFoundError, ValueError) as exc:
                logger.debug("run has no replayable script: %s", exc)
            except OSError as exc:
                logger.warning("failed to export replay script: %s", exc)
        digest = script_sha256(script) if verified and script else ""
        kind = "skill" if verified and script and digest else "procedure"
        provisional = not (verified and script and digest)
        profile = getattr(runner, "runtime_profile", None)
        profile_data = (
            profile.to_dict()
            if callable(getattr(profile, "to_dict", None))
            else dict(profile or {}) if isinstance(profile, dict) else {}
        )
        stage_type = str(profile_data.get("stage_type") or "")
        node = memory_store.add_node(
            kind,
            task[:500],
            summary=task[:500],
            body="\n".join(steps),
            app=app_hint[:200],
            status="provisional" if provisional else "active",
            confidence=0.45 if provisional else 0.7,
            payload={
                "task": task[:200],
                "app": app_hint[:80],
                "steps": steps,
                "script": script,
                "uses": 0,
                "successes": 0,
                "provisional": provisional,
                "manual": False,
                "stage_type": stage_type,
                "parameters": {},
                "app_version": "",
                "verification": [],
                "applicability": build_applicability(
                    task,
                    app_hint,
                    stage_type=stage_type,
                ),
                "script_sha256": digest,
                "verification_state": "source_run_verified" if not provisional else "provisional",
                "verified_source_run_id": run_id if not provisional else "",
            },
            source=run_id,
            manual=False,
            conditions=_conditions(task, app_hint, script),
            evidence=_evidence(provisional),
            anchors=[task, app_hint] + steps[:3],
        )
        if node and script:
            memory_store.add_artifact(node["id"], "wps_script", script, {"run_id": run_id})
        if episode and node:
            memory_store.add_edge(node["id"], episode["id"], "derived_from")
        if node and not provisional:
            verification = memory_store.add_node(
                "observation",
                f"Source run verified: {task[:300]}",
                summary="The originating run completed with independently verified action evidence.",
                status="active",
                confidence=0.85,
                payload={
                    "run_id": run_id,
                    "done_verified": bool(getattr(runner, "_done_verified", False)),
                    "had_verified_action": bool(getattr(runner, "_had_verified_action", False)),
                    "script": script,
                    "script_sha256": digest,
                },
                source=run_id,
                manual=False,
                anchors=[task, app_hint, run_id],
            )
            if verification:
                memory_store.add_edge(node["id"], verification["id"], "verified_by")
        _link_prior_versions(memory_store, node, task, retire_previous=not provisional)
        created["recipe"] = node
    elif status in {"fail", "error"} and not prior_warning:
        used = []
        session = getattr(runner, "session", None)
        for rid in getattr(session, "used_memory_recipe_ids", []) or []:
            rid = str(rid)
            if rid and rid not in used:
                used.append(rid)
        if used:
            warning = memory_store.add_node(
                "warning",
                f"Run failed after memory reuse: {task[:120]}",
                summary=result_text[:500] or "Run failed after using a memory.",
                body=result_text[:2000],
                status="active",
                confidence=0.7,
                payload={"run_id": run_id, "status": status, "task": task[:200]},
                source=run_id,
                manual=False,
                anchors=[task],
            )
            for rid in used:
                if warning:
                    memory_store.add_edge(warning["id"], rid, "warning_for")
            created["warning"] = warning
    return created
