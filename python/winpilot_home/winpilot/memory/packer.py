"""Memory Pack rendering for LLM context."""
from __future__ import annotations

from typing import Any

from .store import MemoryStore
from .types import ACTION_KINDS, FACTUAL_KINDS, MemoryCandidate, MemoryPack

MAX_PACK_CHARS = 4800
MAX_STEPS = 8


def _steps(node: dict[str, Any]) -> list[str]:
    payload = node.get("payload") or {}
    steps = payload.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    if not steps:
        body = str(node.get("body") or "").strip()
        steps = [line.strip() for line in body.splitlines() if line.strip()]
    return [str(step).strip()[:300] for step in steps if str(step).strip()]


def _script(node: dict[str, Any]) -> str:
    payload = node.get("payload") or {}
    script = str(payload.get("script") or "")
    if script:
        return script
    for artifact in node.get("artifacts") or []:
        if artifact.get("kind") == "wps_script":
            return str(artifact.get("uri") or "")
    return ""


def _bullets(label: str, items: list[str]) -> list[str]:
    if not items:
        return []
    out = [f"{label}:"]
    out.extend(f"  - {item}" for item in items[:5])
    return out


def _render_steps(node: dict[str, Any]) -> list[str]:
    steps = _steps(node)
    if not steps:
        return ["  (no reusable action steps)"]
    shown = steps[:MAX_STEPS]
    lines = [f"  {i + 1}. {step}" for i, step in enumerate(shown)]
    if len(steps) > len(shown):
        lines.append(f"  ... {len(steps) - len(shown)} more step(s) kept in memory")
    return lines


def _warning_nodes(candidate: MemoryCandidate) -> list[dict[str, Any]]:
    warnings = []
    for edge in candidate.related:
        node = edge.get("node") or {}
        if node.get("kind") == "warning" or edge.get("kind") == "warning_for":
            warnings.append(node)
    return warnings


def pack(
    task: str,
    candidates: list[MemoryCandidate],
    store: MemoryStore,
    *,
    run_id: str = "",
    max_chars: int = MAX_PACK_CHARS,
) -> MemoryPack:
    actions: list[MemoryCandidate] = []
    facts: list[MemoryCandidate] = []
    warnings: list[dict[str, Any]] = []
    other_available = False
    for candidate in candidates:
        node = candidate.node
        kind = node.get("kind")
        if kind in ACTION_KINDS and len(actions) < 2:
            actions.append(candidate)
            for warning in _warning_nodes(candidate):
                if warning and warning.get("id") not in {w.get("id") for w in warnings}:
                    warnings.append(warning)
        elif kind in FACTUAL_KINDS and len(facts) < 4:
            facts.append(candidate)
        elif kind == "warning" and len(warnings) < 2:
            warnings.append(node)
        elif kind in {"raw_artifact", "chunk"}:
            other_available = True
    warnings = warnings[:2]
    if not actions and not facts and not warnings and not other_available:
        return MemoryPack(text="", selected_ids=[], scores={}, candidates=candidates)

    lines = [
        "[Memory Pack]",
        "Long-term memory is a hint only. Current screen, files, and tool results have priority.",
        "Replay success is not done evidence; verify final state before calling done.",
    ]
    selected_ids: list[str] = []
    scores: dict[str, Any] = {}

    for candidate in actions:
        node = candidate.node
        selected_ids.append(node["id"])
        scores[node["id"]] = {
            "score": round(candidate.score, 3),
            "reasons": candidate.reasons,
            "kind": node.get("kind"),
        }
        title = node.get("title") or node.get("summary") or node["id"]
        script = _script(node)
        tag = "reusable skill candidate" if node.get("kind") == "skill" and script else "memory procedure"
        lines.append("")
        lines.append(f"[{tag}] {title} (id:{node['id']}, app:{node.get('app', '')}, score:{candidate.score:.1f})")
        lines.extend(_bullets("Conditions", node.get("conditions") or []))
        lines.extend(_bullets("Evidence required", node.get("evidence") or []))
        lines.append("Steps preview:")
        lines.extend(_render_steps(node))
        if script and node.get("kind") == "skill" and node.get("status") == "active":
            lines.append(
                f'If the current observed screen satisfies the conditions, call reuse_skill(memory_id="{node["id"]}") first.'
            )

    for warning in warnings:
        if warning.get("id") not in selected_ids:
            selected_ids.append(warning["id"])
        scores[warning["id"]] = {"score": 0.0, "reasons": ["graph-warning"], "kind": "warning"}
        lines.append("")
        lines.append(f"[warning] {warning.get('title') or warning.get('summary') or warning['id']}")
        body = str(warning.get("summary") or warning.get("body") or "").strip()
        if body:
            lines.append(body[:500])

    for candidate in facts:
        node = candidate.node
        selected_ids.append(node["id"])
        scores[node["id"]] = {
            "score": round(candidate.score, 3),
            "reasons": candidate.reasons,
            "kind": node.get("kind"),
        }
        title = node.get("title") or node.get("summary") or node["id"]
        lines.append("")
        lines.append(f"[memory {node.get('kind')}] {title} (id:{node['id']}, score:{candidate.score:.1f})")
        lines.extend(_bullets("Conditions", node.get("conditions") or []))
        lines.extend(_bullets("Evidence required", node.get("evidence") or []))
        body = str(node.get("summary") or node.get("body") or "").strip()
        if body:
            lines.append("Notes:")
            lines.append(f"  {body[:700]}")
        else:
            lines.append("Steps/notes:")
            lines.extend(_render_steps(node))

    if other_available:
        lines.append("")
        lines.append("[memory_open available] Raw artifacts/chunks were not injected; use memory_open for details if needed.")

    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 80].rstrip() + "\n[Memory Pack truncated to fit context budget]"
    store.record_pack(task, text, selected_ids, scores, run_id=run_id)
    return MemoryPack(text=text, selected_ids=selected_ids, scores=scores, candidates=candidates)
