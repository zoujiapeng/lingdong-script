"""Reflection checkpoint helpers for AgentRunner.

These methods are pure context-management helpers: no extra LLM call, no tool
dispatch. Keeping them outside ``loop.py`` makes the runner loop easier to read.
"""
from __future__ import annotations

from ..config import CONFIG
from ..utils.log import BUS, logger


class ReflectionMixin:
    def _build_reflection(self, step: int) -> str:
        """Generate a structured reflection note from current run state."""
        recent_actions = self._steps_log[-6:] if self._steps_log else []
        world_state = self.session.world.state() if self.session.world_enabled else {}
        window_title = self.session.snapshot.title if self.session.snapshot else "未知窗口"
        plan_summary = ""
        if self.session.plan and self.session.plan.active():
            plan_summary = f"\n当前计划进度：{self.session.plan.render()}"

        verified_count = sum(
            1 for _name, out in self._recent_results
            if '"verified": true' in out.lower()
        )
        unverified_note = (
            f"（最近 {self._consecutive_unverified} 步没有 verified=true 的动作，注意核验）"
            if self._consecutive_unverified >= 3 else ""
        )

        parts = [
            f"[反思检查点 第{step}步] 当前窗口：「{window_title}」",
            f"最近动作：{' → '.join(recent_actions) or '（无）'}",
            f"已验证动作数：{verified_count}{unverified_note}",
        ]
        if world_state.get("repeated_action"):
            parts.append(f"警告：检测到重复动作 {world_state['repeated_action']}")
        if plan_summary:
            parts.append(plan_summary)
        parts.append(
            "请检查：① 当前是否在正确的窗口/界面？② 上一步动作是否真正生效（observe 验证了吗）？"
            "③ 接下来最关键的一步是什么？"
        )
        return "\n".join(parts)

    def _maybe_reflect(self, step: int) -> None:
        """Inject a reflection checkpoint at configured intervals."""
        native_check = getattr(self, "_native_visual_context_enabled", None)
        if callable(native_check):
            try:
                if native_check():
                    return
            except Exception:
                pass
        reflect_interval = int(CONFIG.get("agent", "reflect_interval", default=8))
        if reflect_interval <= 0:
            return
        steps_since_last = step - self._last_reflect_step
        if steps_since_last < 3:
            return
        should_reflect = (
            (step > 1 and step % reflect_interval == 0)
            or self._consecutive_unverified >= 3
        )
        if not should_reflect:
            return
        note = self._build_reflection(step)
        self.messages.append({"role": "user", "content": note})
        self._last_reflect_step = step
        BUS.publish("agent_reflect", run_id=self.run_id, step=step,
                    consecutive_unverified=self._consecutive_unverified)
        logger.info("反思检查点注入 step=%d unverified=%d", step, self._consecutive_unverified)
