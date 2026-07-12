"""Lightweight world model: minimal cross-step state for stuck detection and
change awareness — WITHOUT any extra perception cost.

It consumes only the snapshots and actions the agent already produces (no new
screen captures) and surfaces a short advisory *only* when something is worth
telling the model: an action that changed nothing, or repeated identical
actions. Deliberately tiny — this is decision-support state, not a knowledge
graph. The negative-optimization guard for this module is: never call into
perception here, and stay silent when there is nothing noteworthy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# Tools whose repetition / no-visible-effect signals being stuck. Intentionally
# excludes read-only tools (observe/read_text/list_windows/find_app/search_files/
# color_probe/find_image/vlm_describe) and ``wait`` (legitimately changes nothing).
_ACTION_TOOLS = {
    "launch", "click", "type_text", "hotkey", "scroll", "drag",
    "draw_strokes", "focus_window", "tray_click", "run_shell", "run_action_batch",
}
_STUCK_REPEAT = 3        # same action this many times in a row -> stuck
_STUCK_NO_CHANGE = 3     # this many post-action observes with no change -> stuck
_MAX_HISTORY = 12
_MAX_DELTA_ITEMS = 4     # how many added/removed element names to surface


def _args_hash(name: str, args: dict) -> str:
    compact = {k: v for k, v in (args or {}).items() if k != "expect"}
    blob = name + "|" + json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]  # noqa: S324 (non-crypto id)


@dataclass(frozen=True)
class _ObserveState:
    title: str
    fingerprint: frozenset[str]
    count: int


class WorldModel:
    """Per-run, in-memory only. Fed by ToolSession.dispatch; read by the loop."""

    def __init__(self) -> None:
        self._actions: list[tuple[str, str]] = []   # (tool_name, args_hash)
        self._last_observe: _ObserveState | None = None
        self._identical_observes = 0
        self._action_since_observe = False
        self._last_change_line = ""
        self._stuck_announced = False                # edge-trigger guard for replan
        self._screen_bulletin: dict = {}
        self._screen_notice_seq = 0
        self._screen_notice_sent_seq = 0
        self._working_facts: dict[str, dict] = {}
        self._working_revision = 0
        self._working_sent_revision = 0
        self._visual_no_progress = 0
        self._last_visual_outcome: dict = {}

    # ---------------------------------------------------------------- feed
    @staticmethod
    def _fingerprint(snapshot) -> frozenset[str]:
        """Structural signature of a snapshot: the set of text-bearing elements.

        Position-only changes (e.g. a moving progress bar) intentionally do not
        register — stuck detection cares about structural progress.
        """
        fp: set[str] = set()
        for element in snapshot.elements:
            text = (getattr(element, "text", "") or "").strip()
            if text:
                fp.add(f"{element.role}:{text}")
        return frozenset(fp)

    def record_action(self, name: str, args: dict) -> None:
        if name not in _ACTION_TOOLS:
            return
        self._action_since_observe = True
        self._actions.append((name, _args_hash(name, args)))
        if len(self._actions) > _MAX_HISTORY:
            self._actions = self._actions[-_MAX_HISTORY:]
        # NOTE: do NOT re-arm the stuck signal here. It re-arms only on a real
        # structural screen change (observe_snapshot), so a replan that swaps to
        # a different action but still makes no progress can't re-fire the
        # structured-replan prompt every step.

    def record_action_outcome(self, name: str, payload: dict) -> None:
        """Track visual progress reported by already-executed actions.

        This intentionally consumes only controller evidence; it never captures
        the screen itself. A no-change visual mutation is stronger evidence than
        OCR/UIA structural sameness for canvases and owner-drawn applications.
        """
        entries = [payload] if isinstance(payload, dict) else []
        entries.extend(
            item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else {}
            for item in (payload.get("results") or [])
            if isinstance(item, dict)
        ) if isinstance(payload, dict) else None
        for entry in entries:
            if not isinstance(entry, dict) or "visual_change_detected" not in entry:
                continue
            changed = entry.get("visual_change_detected") is True
            self._last_visual_outcome = {
                "action": name,
                "changed": changed,
                "ratio": entry.get("visual_change_ratio"),
                "failure_type": entry.get("failure_type") or "",
            }
            if changed:
                self._visual_no_progress = 0
                self._stuck_announced = False
            else:
                self._visual_no_progress += 1
                self._last_change_line = "visual action produced no pixel change in its target region"

    def observe_snapshot(self, snapshot) -> None:
        fingerprint = self._fingerprint(snapshot)
        prev = self._last_observe
        acted = self._action_since_observe
        self._action_since_observe = False

        same = (
            prev is not None
            and prev.title == snapshot.title
            and prev.fingerprint == fingerprint
        )
        if same:
            # Only count "no change" against progress when an action happened
            # between the two observes (back-to-back observes are not stuck).
            if acted:
                self._identical_observes += 1
                self._last_change_line = "动作后界面无可见变化"
        else:
            self._identical_observes = 0
            self._last_change_line = self._describe_delta(prev, snapshot.title, fingerprint)
            self._stuck_announced = False  # the screen changed = progress
        self._last_observe = _ObserveState(snapshot.title, fingerprint, len(snapshot.elements))

    def record_screen_bulletin(self, status: dict) -> None:
        """Record the fast screen watcher bulletin without triggering capture."""
        if not isinstance(status, dict):
            return
        sequence = int(status.get("sequence") or 0)
        compact = {
            "sequence": sequence,
            "running": bool(status.get("running")),
            "last_changed": bool(status.get("last_changed")),
            "last_ratio": float(status.get("last_ratio") or 0.0),
            "changed_regions": list(status.get("changed_regions") or [])[:3],
            "recent_changed_regions": list(status.get("recent_changed_regions") or [])[:3],
            "action_changed_regions": list(status.get("action_changed_regions") or [])[:3],
            "last_change_age_ms": int(status.get("last_change_age_ms") or 0),
            "last_action_change_age_ms": int(status.get("last_action_change_age_ms") or 0),
            "samples": int(status.get("samples") or 0),
            "capture_latency_ms": int(status.get("capture_latency_ms") or 0),
            "diff_latency_ms": int(status.get("diff_latency_ms") or 0),
        }
        self._screen_bulletin = compact
        if compact["last_changed"] and sequence > 0:
            self._screen_notice_seq = sequence

    def record_facts(
        self,
        subject: str,
        facts: dict,
        source_url: str = "",
        source_title: str = "",
        evidence: list | None = None,
    ) -> dict:
        """Store source-bound facts for this run without creating memory."""
        key = " ".join(str(subject or "").split()).casefold()[:160]
        if not key:
            raise ValueError("subject is required")
        clean_facts: dict[str, str] = {}
        for raw_key, raw_value in list(facts.items())[:24]:
            name = " ".join(str(raw_key or "").split())[:80]
            value = " ".join(str(raw_value or "").split())[:700]
            if name and value:
                clean_facts[name] = value
        if not clean_facts:
            raise ValueError("facts must contain at least one non-empty value")
        prior = self._working_facts.get(key, {})
        merged = {**(prior.get("facts") or {}), **clean_facts}
        record = {
            "subject": " ".join(str(subject).split())[:160],
            "source_url": str(source_url or prior.get("source_url") or "")[:1000],
            "source_title": " ".join(str(source_title or prior.get("source_title") or "").split())[:300],
            "facts": merged,
            "evidence": [
                " ".join(str(item or "").split())[:300]
                for item in (evidence or prior.get("evidence") or [])[:12]
                if str(item or "").strip()
            ],
        }
        self._working_facts[key] = record
        self._working_revision += 1
        return dict(record)

    def working_set(self) -> list[dict]:
        return [dict(item) for item in self._working_facts.values()]

    def working_set_advisory(self) -> str:
        """Emit the compact research table once after each update."""
        if self._working_revision <= self._working_sent_revision:
            return ""
        self._working_sent_revision = self._working_revision
        lines = ["Per-run source-bound working set (do not mix subjects):"]
        for record in self.working_set()[:10]:
            fields = "; ".join(
                f"{key}={value}"
                for key, value in list((record.get("facts") or {}).items())[:12]
            )
            source = str(record.get("source_url") or record.get("source_title") or "")
            lines.append(f"- {record['subject']} | {fields} | source={source}")
        return "\n".join(lines)[:6000]

    @staticmethod
    def _describe_delta(prev: _ObserveState | None, title: str,
                        fingerprint: frozenset[str]) -> str:
        if prev is None:
            return ""
        parts: list[str] = []
        if prev.title != title:
            parts.append(f'标题 "{prev.title}"→"{title}"')
        added = sorted(t for t in fingerprint - prev.fingerprint)[:_MAX_DELTA_ITEMS]
        gone = sorted(t for t in prev.fingerprint - fingerprint)[:_MAX_DELTA_ITEMS]
        if added:
            parts.append("新增 " + ", ".join(added))
        if gone:
            parts.append("消失 " + ", ".join(gone))
        return "；".join(parts)

    # ---------------------------------------------------------------- read
    def repeated_action_count(self) -> int:
        """How many times the most recent action repeats consecutively."""
        if not self._actions:
            return 0
        last = self._actions[-1]
        count = 0
        for item in reversed(self._actions):
            if item == last:
                count += 1
            else:
                break
        return count

    def prospective_repeat_count(self, name: str, args: dict) -> int:
        """Return the consecutive count if ``name/args`` ran next."""
        candidate = (name, _args_hash(name, args))
        count = 1
        for item in reversed(self._actions):
            if item != candidate:
                break
            count += 1
        return count

    def is_stuck(self) -> tuple[bool, str]:
        if self._visual_no_progress >= 2:
            return True, f"{self._visual_no_progress} consecutive visual actions produced no pixel change"
        repeats = self.repeated_action_count()
        if repeats >= _STUCK_REPEAT:
            return True, f"连续 {repeats} 次重复同一动作，状态未推进"
        if self._identical_observes >= _STUCK_NO_CHANGE:
            return True, f"连续 {self._identical_observes} 次动作后界面均无变化"
        return False, ""

    def take_stuck_signal(self) -> tuple[bool, str]:
        """Edge-triggered stuck signal for forcing a structured replan: returns
        (True, why) only the FIRST time stuck is detected in an episode, then
        stays quiet until progress resets it (avoids replan-spam every step)."""
        stuck, why = self.is_stuck()
        if stuck and not self._stuck_announced:
            self._stuck_announced = True
            return True, why
        return False, ""

    def advisory(self) -> str:
        """Mild, non-blocking hint surfaced to the model; empty when nothing
        noteworthy. (Hard 'stuck' handling is edge-triggered via
        take_stuck_signal → structured replan in the loop.)"""
        if self._identical_observes >= 2 and self._last_change_line:
            return f"提示：{self._last_change_line}，请确认上一步是否点对了目标。"
        if self._visual_no_progress:
            return "Visual action did not change its target region. Re-observe focus/tool/canvas before retrying."
        return ""

    def screen_advisory(self) -> str:
        """Return one compact watcher notice per changed screen sequence."""
        if self._screen_notice_seq <= self._screen_notice_sent_seq:
            return ""
        self._screen_notice_sent_seq = self._screen_notice_seq
        status = self._screen_bulletin
        regions = status.get("changed_regions") or status.get("recent_changed_regions") or []
        roi = ""
        if regions:
            first = regions[0]
            try:
                left = int(first.get("left", 0))
                top = int(first.get("top", 0))
                width = int(first.get("width", 0))
                height = int(first.get("height", 0))
                roi = f" roi=({left},{top},{width}x{height})"
            except AttributeError:
                roi = ""
        ratio = float(status.get("last_ratio") or 0.0)
        return (
            f"screen_watch changed: ratio={ratio:.4f}{roi}. "
            "If the next action depends on loaded/changed UI, use observe/read_text/VLM "
            "on the target area or wait_stable before acting."
        )

    def state(self) -> dict:
        """Compact state for the UI / trace (not sent to the LLM)."""
        return {
            "actions": len(self._actions),
            "repeated": self.repeated_action_count(),
            "identical_observes": self._identical_observes,
            "last_change": self._last_change_line,
            "last_title": self._last_observe.title if self._last_observe else "",
            "screen_watch": dict(self._screen_bulletin),
            "visual_no_progress": self._visual_no_progress,
            "last_visual_outcome": dict(self._last_visual_outcome),
            "working_set_count": len(self._working_facts),
            "working_set_subjects": [
                item.get("subject", "") for item in self.working_set()[:10]
            ],
        }
