"""Recall and graph expansion for Memory Graph."""
from __future__ import annotations

import re
import time
from typing import Any

from .store import MemoryStore
from .types import ACTION_KINDS, FACTUAL_KINDS, MemoryCandidate, TaskFrame

_VOLATILE_CN = ("当前", "现在", "此刻", "已打开", "已经打开", "前台", "窗口")
_STATE_CN = ("检查", "扫描", "找出", "哪个", "状态", "登录", "登录页", "在线")
_VOLATILE_EN = (
    "current",
    "currently",
    "now",
    "open window",
    "visible window",
    "foreground",
    "which window",
    "logged in",
    "login page",
    "status",
)


def norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def is_volatile_state_task(task: str) -> bool:
    text = norm(task)
    if not text:
        return False
    if any(k in text for k in _VOLATILE_EN):
        return True
    return any(k in text for k in _VOLATILE_CN) and any(k in text for k in _STATE_CN)


def words_for(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.;:!?，。；：！？/\\]+", (text or "").lower()) if len(w) >= 2]


def bigrams(text: str) -> set[str]:
    n = norm(text)
    return {n[i:i + 2] for i in range(len(n) - 1)}


def strong_match(query: str, title: str) -> bool:
    a, b = norm(query), norm(title)
    if len(a) < 3 or len(b) < 3:
        return False
    if a == b or a in b or b in a:
        return True
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return False
    return len(ba & bb) / min(len(ba), len(bb)) >= 0.7


def parse_task_frame(
    task: str,
    runtime_profile: dict[str, Any] | None = None,
    session_context: Any = None,
) -> TaskFrame:
    runtime_profile = runtime_profile or {}
    return TaskFrame(
        task=task or "",
        app=str(runtime_profile.get("app") or runtime_profile.get("window_title") or ""),
        coding=bool(runtime_profile.get("coding") or runtime_profile.get("notes_only")),
        volatile=is_volatile_state_task(task or ""),
        words=words_for(task or ""),
        bigrams=bigrams(task or ""),
    )


def _is_active(node: dict[str, Any]) -> bool:
    if node.get("status") in {"retired", "superseded"}:
        return False
    expires_at = node.get("expires_at")
    return not expires_at or int(expires_at) > int(time.time())


def passes_hard_filters(
    node: dict[str, Any],
    frame: TaskFrame,
    *,
    notes_only: bool = False,
) -> bool:
    if not node or not _is_active(node):
        return False
    kind = node.get("kind")
    manual = bool(node.get("manual") or node.get("payload", {}).get("manual"))
    if notes_only:
        return manual or kind in FACTUAL_KINDS
    if frame.coding and kind in ACTION_KINDS and not manual:
        return False
    if frame.volatile and kind in ACTION_KINDS and not manual:
        return False
    if is_volatile_state_task(str(node.get("title") or "")) and kind in ACTION_KINDS:
        return False
    return True


def _score_node(node: dict[str, Any], frame: TaskFrame, base: float = 0.0) -> tuple[float, list[str]]:
    haystack = " ".join(
        str(part or "")
        for part in (
            node.get("title"),
            node.get("summary"),
            node.get("body"),
            node.get("app"),
            " ".join(node.get("anchors") or []),
        )
    ).lower()
    score = float(base)
    reasons: list[str] = []
    if base:
        reasons.append("text")
    for word in frame.words:
        if word in haystack:
            score += 1.0
            reasons.append(f"word:{word[:20]}")
    if frame.bigrams:
        hb = bigrams(haystack)
        overlap = len(frame.bigrams & hb)
        if overlap >= 2:
            score += min(4.0, overlap / 3.0)
            reasons.append("bigram")
    if frame.app and frame.app.lower() in str(node.get("app") or "").lower():
        score += 1.5
        reasons.append("app")
    if strong_match(frame.task, str(node.get("title") or "")):
        score += 3.0
        reasons.append("strong")
    payload = node.get("payload") or {}
    score += float(node.get("confidence") or 0.0)
    successes = int(payload.get("successes") or 0)
    uses = int(payload.get("uses") or 0)
    if successes:
        score += min(successes, 3) * 0.5
        reasons.append("success")
    elif uses:
        score -= min(uses, 3) * 0.5
    if node.get("status") == "provisional":
        score -= 0.75
        reasons.append("provisional")
    return score, reasons


def recall(
    task: str,
    store: MemoryStore,
    runtime_profile: dict[str, Any] | None = None,
    session_context: Any = None,
    *,
    notes_only: bool = False,
    limit: int = 12,
) -> tuple[TaskFrame, list[MemoryCandidate]]:
    frame = parse_task_frame(task, runtime_profile, session_context)
    search_hits = {node["id"]: (node, score) for node, score in store.search(task, limit=limit * 2)}
    # Bigram/anchor fallback for CJK and small memories: score active nodes too.
    for node in store.list_nodes(limit=200):
        if node["id"] not in search_hits:
            search_hits[node["id"]] = (node, 0.0)
    candidates: list[MemoryCandidate] = []
    for node, base in search_hits.values():
        if not passes_hard_filters(node, frame, notes_only=notes_only):
            continue
        score, reasons = _score_node(node, frame, base)
        if score <= 0:
            continue
        candidates.append(MemoryCandidate(node=node, score=score, reasons=reasons))
    candidates.sort(key=lambda c: c.score, reverse=True)
    expanded = expand_graph(store, candidates[:limit])
    return frame, expanded


def expand_graph(store: MemoryStore, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    useful_edges = {"warning_for", "requires", "verified_by", "supported_by"}
    for candidate in candidates:
        related = []
        for edge in store.related(candidate.node["id"], useful_edges, direction="both", limit=12):
            node = edge.get("node")
            if node and _is_active(node):
                related.append(edge)
        candidate.related = related
    return candidates


def top_reusable_skill(
    task: str,
    store: MemoryStore,
    runtime_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, float]:
    frame, candidates = recall(task, store, runtime_profile, notes_only=False, limit=6)
    for candidate in candidates:
        node = candidate.node
        payload = node.get("payload") or {}
        script = payload.get("script") or ""
        if not script:
            for artifact in node.get("artifacts") or []:
                if artifact.get("kind") == "wps_script":
                    script = artifact.get("uri") or ""
                    break
        if (
            node.get("kind") == "skill"
            and node.get("status") == "active"
            and script
            and strong_match(frame.task, str(node.get("title") or ""))
        ):
            return node, candidate.score
    return None, 0.0
