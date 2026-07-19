"""Planning, world-model, and playbook guidance for AgentRunner."""
from __future__ import annotations

from ..config import CONFIG
from ..utils.log import BUS, logger
from . import playbooks
from .gui_plan_helpers import _pure_open_goal_matches_bound_target
from .planner import Plan, make_plan, should_plan_upfront


class ContextGuidanceMixin:
    def _native_visual_authority_active(self) -> bool:
        """Whether current decisions already receive authoritative live pixels."""

        check = getattr(self, "_native_visual_context_enabled", None)
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            return False

    def _append_advisory_once(self, key: str, prefix: str, note: str) -> None:
        """Keep changing guidance while suppressing identical prompt repeats."""
        cache = getattr(self, "_last_context_advisories", None)
        if not isinstance(cache, dict):
            cache = {}
            self._last_context_advisories = cache
        compact = " ".join(str(note or "").split()).strip()
        if not compact:
            cache.pop(key, None)
            return
        content = f"[{prefix}] {compact}"
        if cache.get(key) == compact and any(
            message.get("content") == content
            for message in self.messages[-24:]
        ):
            return
        cache[key] = compact
        self.messages.append({"role": "user", "content": content})

    def _maybe_plan_upfront(self, client) -> None:
        """Create a provisional plan for long-horizon or full desktop stages."""
        if self._native_visual_authority_active():
            # A plan authored before interacting with the live scene is a
            # speculative GUI script.  Native visual execution plans implicitly
            # by selecting its next action from each fresh frame instead.
            return
        forced_by_stage = bool(
            getattr(
                getattr(self, "runtime_profile", None),
                "plan_upfront",
                False,
            )
        )
        if not forced_by_stage and not should_plan_upfront(
            self.task,
            task_intent=self.task_intent,
        ):
            return
        api = CONFIG.get("api", default={})
        model = api.get("model", "deepseek-v4-flash")
        steps = make_plan(
            client,
            model,
            self.task,
            runtime_context=self._planning_runtime_context(),
            on_response=lambda response: self._track_aux_model_response(
                response, purpose="planner", model=model,
            ),
        )
        if not steps:
            return
        self.session.plan = Plan(steps)
        if int(getattr(self.session, "hwnd", 0) or 0):
            for index, step in enumerate(self.session.plan.steps, start=1):
                if step.get("done"):
                    continue
                if not _pure_open_goal_matches_bound_target(
                    str(step.get("goal") or ""),
                    str(getattr(self.session, "target_hint", "") or ""),
                ):
                    break
                if self.session.plan.complete(index):
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source="prebound_target_window",
                        plan=self.session.plan.snapshot(),
                    )
                break
        self.messages.append({"role": "user", "content": self.session.plan.render()})
        BUS.publish("agent_plan", run_id=self.run_id, action="init",
                    plan=self.session.plan.snapshot())

    def _planning_runtime_context(self) -> str:
        """Compact facts already visible before the provisional plan call."""
        intent = getattr(self, "task_intent", {}) or {}
        ambiguous_fields = (
            list(intent.get("ambiguous_fields") or [])
            if isinstance(intent, dict)
            else list(getattr(intent, "ambiguous_fields", []) or [])
        )
        unresolved = [
            " ".join(str(field or "").split())
            for field in ambiguous_fields
            if str(field or "").strip()
        ]
        snapshot = getattr(getattr(self, "session", None), "snapshot", None)
        parts: list[str] = []
        if unresolved:
            parts.append(
                "Unresolved facts to discover from the live environment: "
                + ", ".join(dict.fromkeys(unresolved))
            )
        session = getattr(self, "session", None)
        bound_hwnd = int(getattr(session, "hwnd", 0) or 0)
        target_hint = " ".join(
            str(getattr(session, "target_hint", "") or "").split()
        )
        if bound_hwnd:
            parts.append(
                "Current target is already bound and open: "
                f"{target_hint or '(title unavailable)'} (hwnd={bound_hwnd})"
            )
        if snapshot is None:
            return "\n".join(parts)[:2400]
        visible: list[str] = []
        seen: set[str] = set()
        for element in list(getattr(snapshot, "elements", []) or []):
            value = " ".join(str(getattr(element, "text", "") or "").split())
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            visible.append(value[:100])
            if len(visible) >= 24:
                break
        title = " ".join(str(getattr(snapshot, "title", "") or "").split())
        parts.append(f"Current window title: {title or '(untitled)'}")
        if visible:
            parts.append("Currently visible labels: " + " | ".join(visible))
        return "\n".join(parts)[:2400]

    def _surface_world_advisory(self) -> None:
        """Append compact world/visual advisories when they carry signal."""
        if not self.session.world_enabled:
            return
        BUS.publish("world", run_id=self.run_id, **self.session.world.state())
        if self._native_visual_authority_active():
            # These summaries are derived from UIA/OCR/change heuristics.  They
            # remain telemetry, but must not compete with authoritative pixels.
            return
        visual_note = self.session.take_visual_advisory()
        self._append_advisory_once("visual", "visual state warning", visual_note)
        screen_note = self.session.world.screen_advisory()
        self._append_advisory_once("screen", "screen watch", screen_note)
        run_note = self.run_state.guidance()
        self._append_advisory_once("run_state", "run state", run_note)
        note = self.session.world.advisory()
        self._append_advisory_once("world", "系统观察", note)
        working_set = self.session.world.working_set_advisory()
        self._append_advisory_once("working_set", "working set", working_set)
        self._maybe_structured_replan()

    def _maybe_structured_replan(self) -> None:
        """Inject one stronger recovery prompt when the world model detects stuckness."""
        if (
            not self.session.world_enabled
            or self._native_visual_authority_active()
        ):
            return
        stuck, why = self.session.world.take_stuck_signal()
        if not stuck:
            return
        self.messages.append({
            "role": "user",
            "content": (
                f"[run state] No progress was detected: {why}. "
                "Re-evaluate the current evidence before choosing the next action."
            ),
        })
        BUS.publish("agent_recovery", run_id=self.run_id, kind="structured_replan", reason=why)
        logger.info("触发结构化重规划: %s", why)
        self._escalate_next = True

    def _maybe_inject_playbook(self) -> None:
        """Inject a matching playbook once when a known modal/window appears."""
        if self._native_visual_authority_active():
            return
        title = self.session.snapshot.title if self.session.snapshot else ""
        book = playbooks.for_window_title(title, exclude=self._injected_playbooks)
        if book is None:
            return
        self._injected_playbooks.add(book.name)
        self.messages.append({
            "role": "user",
            "content": (
                f"[情境提示] 检测到「{title}」相关界面，适用以下方法：\n"
                f"{playbooks.render_playbook(book)}"
            ),
        })
        BUS.publish("playbook", run_id=self.run_id, name=book.name, trigger=title[:60])
        logger.info("运行中注入 playbook: %s (窗口: %s)", book.name, title[:40])
