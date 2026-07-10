"""Memory graph tool handlers for ToolSession."""
from __future__ import annotations

import json
from typing import Any

from ..memory import experience
from ..memory import retriever
from ..utils.log import BUS
from .tool_result import ToolResult


def _fmt(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    payload = node.get("payload") or {}
    return {
        "id": node.get("id"),
        "kind": node.get("kind"),
        "title": node.get("title"),
        "summary": node.get("summary"),
        "status": node.get("status"),
        "confidence": node.get("confidence"),
        "app": node.get("app"),
        "script": payload.get("script"),
        "conditions": (node.get("conditions") or [])[:3],
        "evidence": (node.get("evidence") or [])[:3],
    }


class MemoryToolMixin:
    def _t_remember(self, args: dict) -> ToolResult:
        note = str(args.get("note") or args.get("body") or args.get("summary") or "").strip()
        title = str(args.get("title") or note[:120]).strip()
        if not note and not title:
            return ToolResult("remember requires note/body/title.")
        conditions = args.get("conditions") or []
        evidence = args.get("evidence") or []
        anchors = args.get("anchors") or []
        kind = str(args.get("kind") or "fact").strip() or "fact"
        if kind not in {"fact", "preference", "rule", "summary"}:
            kind = "fact"
        recipe = experience.add(
            title or note,
            app=str(args.get("app", "")).strip(),
            steps=[note] if note else [],
            manual=True,
            conditions=conditions if isinstance(conditions, list) else [str(conditions)],
            evidence=evidence if isinstance(evidence, list) else [str(evidence)],
            kind=kind,
            title=title,
            body=note,
            summary=str(args.get("summary") or note[:500]),
            anchors=anchors if isinstance(anchors, list) else [str(anchors)],
        )
        BUS.publish("memory", action="remember", id=recipe.get("id"), note=(title or note)[:120])
        return ToolResult(_fmt({"ok": True, "memory": recipe}))

    def _t_forget(self, args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult("forget requires query.")
        removed = experience.delete_matching(query)
        if not removed:
            return ToolResult(_fmt({"ok": False, "message": f'no memory matched "{query}"'}))
        BUS.publish("memory", action="retire_matching", count=len(removed), query=query)
        return ToolResult(_fmt({"ok": True, "retired": [r.get("id") for r in removed]}))

    def _t_memory_search(self, args: dict) -> ToolResult:
        query = str(args.get("query") or self.task or "").strip()
        limit = int(args.get("limit") or 8)
        store = experience._store()
        _frame, candidates = retriever.recall(query, store, limit=max(1, min(limit, 20)))
        items = [_compact_node(c.node) | {"score": round(c.score, 3), "reasons": c.reasons} for c in candidates[:limit]]
        return ToolResult(_fmt({"ok": True, "query": query, "results": items}))

    def _t_memory_open(self, args: dict) -> ToolResult:
        memory_id = str(args.get("memory_id") or args.get("id") or "").strip()
        mode = str(args.get("mode") or "summary").strip()
        if not memory_id:
            return ToolResult("memory_open requires memory_id.")
        store = experience._store()
        node = store.get_node(memory_id)
        if not node:
            return ToolResult(_fmt({"ok": False, "error": "memory not found"}))
        store.record_attempt(memory_id, "expanded", run_id=getattr(self, "run_id", ""), status=mode)
        if mode == "summary":
            data = _compact_node(node)
        elif mode == "source":
            data = {"id": memory_id, "source": node.get("source"), "artifacts": node.get("artifacts") or []}
        elif mode == "lineage":
            data = {
                "id": memory_id,
                "node": _compact_node(node),
                "related": store.related(memory_id, direction="both", limit=30),
            }
        else:
            data = node
        return ToolResult(_fmt({"ok": True, "mode": mode, "memory": data}))

    def _t_memory_update(self, args: dict) -> ToolResult:
        memory_id = str(args.get("memory_id") or args.get("id") or "").strip()
        if not memory_id:
            return ToolResult("memory_update requires memory_id.")
        patch = {
            key: args[key]
            for key in (
                "title",
                "summary",
                "body",
                "status",
                "confidence",
                "conditions",
                "evidence",
                "anchors",
                "supersedes",
            )
            if key in args
        }
        if not patch:
            return ToolResult("memory_update requires at least one field.")
        updated = experience.update(memory_id, patch)
        if updated is not None:
            return ToolResult(_fmt({"ok": True, "memory": updated}))
        store = experience._store()
        node = store.update_node(memory_id, patch)
        return ToolResult(_fmt({"ok": bool(node), "memory": node}))

    def _t_memory_retire(self, args: dict) -> ToolResult:
        memory_id = str(args.get("memory_id") or args.get("id") or "").strip()
        if not memory_id:
            return ToolResult("memory_retire requires memory_id.")
        ok = experience._store().retire_node(memory_id)
        BUS.publish("memory", action="retire", id=memory_id, ok=ok)
        return ToolResult(_fmt({"ok": ok, "id": memory_id}))

    def _t_reuse_skill(self, args: dict) -> ToolResult:
        from ..config import SCRIPTS_DIR
        from ..recorder import trace as trace_mod

        task = str(args.get("task", "")).strip()
        memory_id = str(args.get("memory_id") or args.get("recipe_id") or "").strip()
        script, recipe = experience.reusable_script_for(task or self.task, memory_id)
        if not script:
            return ToolResult(
                "No matching verified replay script is available. Use live GUI actions."
            )
        script_path = SCRIPTS_DIR / script
        recipe_id = str((recipe or {}).get("id") or memory_id or "")

        def record_attempt(replay_ok: bool | None, event: str, status: str = "") -> None:
            if not recipe_id:
                return
            used = getattr(self, "used_memory_recipe_ids", None)
            if isinstance(used, list) and recipe_id not in used:
                used.append(recipe_id)
            if replay_ok is not None:
                attempts = getattr(self, "memory_recipe_attempts", None)
                if isinstance(attempts, list):
                    attempts.append({"id": recipe_id, "replay_ok": bool(replay_ok)})
            try:
                experience._store().record_attempt(
                    recipe_id,
                    event,
                    run_id=getattr(self, "run_id", ""),
                    status=status,
                    success=replay_ok,
                    detail={"task": task or self.task, "script": script},
                )
            except Exception:
                pass

        record_attempt(None, "used")
        if not script_path.exists():
            record_attempt(False, "replay_failed", "missing_script")
            return ToolResult(f"Replay script {script} is missing. Use live GUI actions.")
        BUS.publish("memory", action="reuse", task=(recipe or {}).get("task", task), id=recipe_id)
        try:
            result = trace_mod.replay(script_path, stop_event=self.stop_event)
        except Exception as exc:
            record_attempt(False, "replay_failed", "exception")
            return ToolResult(f"Replay failed with exception: {exc}. Use live GUI actions.")
        anchor_metrics = result.get("anchor_cache")
        if isinstance(anchor_metrics, dict) and anchor_metrics.get("enabled"):
            BUS.publish(
                "replay_anchor_cache",
                run_id=getattr(self, "run_id", ""),
                **anchor_metrics,
            )
        steps = result.get("results", [])
        if result.get("ok"):
            record_attempt(True, "replay_ok", "ok")
            return ToolResult(
                f"0-token replay succeeded for {len(steps)} step(s). "
                "Now observe/read_text or use deterministic checks to verify final state before done."
            )
        record_attempt(False, "replay_failed", "failed_step")
        failed = next((s for s in steps if not s.get("ok")), None)
        at = failed.get("index") if failed else len(steps)
        return ToolResult(
            f"Replay failed at step {at} ({failed.get('tool') if failed else '?'}). "
            f"The first {max(0, at - 1)} step(s) may already have run. observe current state, then finish with live actions."
        )
