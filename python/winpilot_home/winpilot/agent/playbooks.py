"""Playbook layer: app/situation-specific tactics as data, not prompt code.

Phase 3-H: the per-app tricks that used to be welded into prompts.py
(file-dialog method, tray wakeup, media playback) now live in
``playbooks/*.json``. Adding knowledge for a new app means dropping a JSON
file, not editing code.

Injection has two paths (the second is the anti-regression guard):
- task-keyword match at run start → into the system prompt
- runtime window-title trigger → injected mid-run by the loop the moment a
  matching dialog appears, covering tasks the keywords didn't anticipate

With ``agent.playbooks_enabled=false`` no playbook is injected. This keeps the
switch intuitive and prevents disabled guidance runs from silently receiving
the old monolithic prompt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CONFIG, ROOT_DIR
from ..utils.log import logger

PLAYBOOKS_DIR = ROOT_DIR / "playbooks"

_HOME = Path.home()
_PATH_VARS = {
    "{desktop}": str(_HOME / "Desktop"),
    "{documents}": str(_HOME / "Documents"),
    "{downloads}": str(_HOME / "Downloads"),
}


@dataclass(frozen=True)
class Playbook:
    name: str
    description: str
    content: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    window_triggers: tuple[str, ...] = field(default_factory=tuple)
    conditions: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)


def _render(content: str) -> str:
    """Substitute path placeholders. str.replace, NOT str.format — playbook
    bodies contain JSON examples with braces that format() would choke on."""
    for var, value in _PATH_VARS.items():
        content = content.replace(var, value)
    return content


def _tuple_of_strings(values, limit: int = 5) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(_render(text)[:220])
        if len(out) >= limit:
            break
    return tuple(out)


def _default_conditions(data: dict) -> tuple[str, ...]:
    name = str(data.get("name") or "playbook")
    keywords = ", ".join(str(k) for k in data.get("keywords", [])[:4])
    triggers = ", ".join(str(t) for t in data.get("window_triggers", [])[:4])
    conditions = [
        "Use only when the current task and observed UI match this situation.",
        f"Playbook name: {name}.",
    ]
    if keywords:
        conditions.append(f"Task keyword anchors: {keywords}.")
    if triggers:
        conditions.append(f"Runtime window/title anchors: {triggers}.")
    return tuple(conditions[:5])


def _default_evidence(_data: dict) -> tuple[str, ...]:
    return (
        "Before applying, observe or otherwise confirm the current UI matches the playbook situation.",
        "After applying, verify the requested result with current screen/text/VLM/nonvisual summary or deterministic output before done.",
    )


_cache: tuple[float, list[Playbook]] | None = None  # (dir mtime, books)


def load_all() -> list[Playbook]:
    """Load every playbooks/*.json, cached on directory mtime.

    for_window_title runs every agent step — without the cache that would be
    O(steps × files) disk reads. Editing/adding a JSON bumps the dir mtime on
    Windows, which invalidates the cache automatically.
    """
    global _cache
    if not PLAYBOOKS_DIR.exists():
        return []
    try:
        mtime = max([PLAYBOOKS_DIR.stat().st_mtime]
                    + [p.stat().st_mtime for p in PLAYBOOKS_DIR.glob("*.json")])
    except OSError:
        mtime = 0.0
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    books: list[Playbook] = []
    for path in sorted(PLAYBOOKS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            books.append(Playbook(
                name=str(data.get("name") or path.stem),
                description=str(data.get("description", "")),
                content=_render(str(data["content"])),
                keywords=tuple(str(k).lower() for k in data.get("keywords", [])),
                window_triggers=tuple(str(t) for t in data.get("window_triggers", [])),
                conditions=_tuple_of_strings(data.get("conditions")) or _default_conditions(data),
                evidence=_tuple_of_strings(data.get("evidence")) or _default_evidence(data),
            ))
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("playbook %s 加载失败: %s", path.name, exc)
    _cache = (mtime, books)
    return books


def enabled() -> bool:
    return bool(CONFIG.get("agent", "playbooks_enabled", default=False))


def for_task(task: str) -> list[Playbook]:
    """Playbooks whose keywords appear in the task text.

    When the switch is off, return no playbooks.
    """
    if not enabled():
        return []
    books = load_all()
    task_lower = (task or "").lower()
    upload_publish = any(k in task_lower for k in (
        "upload", "publish", "post", "creator", "draft",
        "\u4e0a\u4f20", "\u53d1\u5e03", "\u6295\u7a3f", "\u4f5c\u54c1",
        "\u6807\u9898", "\u8349\u7a3f",
    ))
    matched = [b for b in books if any(k in task_lower for k in b.keywords)]
    if upload_publish:
        matched = [b for b in matched if b.name != "media-playback"]
    return matched


def for_window_title(title: str, exclude: set[str]) -> Playbook | None:
    """Playbook whose runtime trigger matches a window title (mid-run rescue
    for tasks the start-time keywords didn't anticipate).

    When several triggers match, the LONGEST (most specific) one wins — e.g.
    "你要如何打开此文件" must hit the media chooser playbook via "你要如何打开",
    not the file-dialog playbook via the broad "打开".
    """
    if not enabled() or not title:
        return None
    best: tuple[int, Playbook] | None = None
    for book in load_all():
        if book.name in exclude:
            continue
        for trigger in book.window_triggers:
            if trigger in title and (best is None or len(trigger) > best[0]):
                best = (len(trigger), book)
    return best[1] if best else None


def render_section(books: list[Playbook]) -> str:
    """Concatenated playbook bodies for prompt injection ('' when none)."""
    if not books:
        return ""
    return "\n\n" + "\n\n".join(render_playbook(b) for b in books)


def render_playbook(book: Playbook) -> str:
    """Render one playbook with applicability and evidence guardrails."""
    parts = [f"# Playbook: {book.name}"]
    if book.description:
        parts.append(f"Description: {book.description}")
    if book.conditions:
        parts.append("Conditions:\n" + "\n".join(f"- {item}" for item in book.conditions))
    if book.evidence:
        parts.append("Evidence required:\n" + "\n".join(f"- {item}" for item in book.evidence))
    parts.append(book.content)
    return "\n".join(parts)
