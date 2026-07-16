"""Planning, world-model, and playbook guidance for AgentRunner."""
from __future__ import annotations

from ..config import CONFIG
from ..utils.log import BUS, logger
from . import playbooks
from .planner import Plan, make_plan, planning_enabled, should_plan_upfront


class ContextGuidanceMixin:
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
        """Create an upfront plan for clearly multi-step tasks."""
        if not should_plan_upfront(
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
            on_response=lambda response: self._track_aux_model_response(
                response, purpose="planner", model=model,
            ),
        )
        if not steps:
            return
        self.session.plan = Plan(steps)
        self.messages.append({"role": "user", "content": self.session.plan.render()})
        BUS.publish("agent_plan", run_id=self.run_id, action="init",
                    plan=self.session.plan.snapshot())

    def _surface_world_advisory(self) -> None:
        """Append compact world/visual advisories when they carry signal."""
        if not self.session.world_enabled:
            return
        BUS.publish("world", run_id=self.run_id, **self.session.world.state())
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
        if not self.session.world_enabled:
            return
        stuck, why = self.session.world.take_stuck_signal()
        if not stuck:
            return
        plan_hint = ("用 revise_plan 重列一个【不同思路】的计划"
                     if planning_enabled() else "明确换一个完全不同的方法")
        self.messages.append({
            "role": "user",
            "content": (
                f"[强制重规划] 检测到卡住：{why}。停止重复当前做法，先想清楚再动：\n"
                f"1) 现在卡在哪一步/哪个子目标？2) 已经试过什么、为什么没用？"
                f"3) 有什么【完全不同】的办法（换工具/换路径/重新观察；"
                f"仅在当前工具列表确实暴露时才考虑 run_shell 或 find_program）？\n"
                f"然后{plan_hint}并按新计划执行。"),
        })
        BUS.publish("agent_recovery", run_id=self.run_id, kind="structured_replan", reason=why)
        logger.info("触发结构化重规划: %s", why)
        self._escalate_next = True

    def _maybe_inject_playbook(self) -> None:
        """Inject a matching playbook once when a known modal/window appears."""
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
