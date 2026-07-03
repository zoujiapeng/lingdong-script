"""Planning and terminal state tool handlers for ToolSession."""
from __future__ import annotations

import re
import sys

from ..action import verify
from ..utils.log import BUS
from . import planner
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


_HISTORICAL_VISIBLE_SOURCES = {
    "observe",
    "read_text",
    "click:verified_expect",
    "type_text:verified_expect",
    "hotkey:verified_expect",
    "launch:verified_expect",
    "run_action_batch:verified_expect",
}


def _normalize_visible_text(text: str) -> str:
    return re.sub(r"[\s_\-:：]+", "", text or "").casefold()


def _accepts_historical_visible_state(text: str) -> bool:
    haystack = (text or "").casefold()
    markers = (
        "appeared", "has appeared", "have appeared", "ever appeared",
        "showed up", "confirmed earlier",
        "曾出现", "曾经出现", "出现过", "都曾出现", "曾经可见",
        "都出现过", "已经出现", "曾显示",
    )
    return any(marker in haystack for marker in markers)


def _visible_check_values(check: dict | None) -> list[str]:
    if not isinstance(check, dict):
        return []
    values: list[str] = []
    for key in ("text_appears", "text_contains", "window_title", "window_appears"):
        value = check.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


class PlanToolMixin:
    def _historical_visible_subgoal_ok(
        self,
        check: dict | None,
        goal: str = "",
    ) -> tuple[bool, str]:
        if not (
            _accepts_historical_visible_state(getattr(self, "task", ""))
            or _accepts_historical_visible_state(goal)
        ):
            return False, "historical visible evidence is not allowed for this task"
        targets = _visible_check_values(check)
        if not targets:
            return False, "subgoal check has no visible text target"
        ledger = getattr(self, "visible_evidence_ledger", [])
        seen = {
            _normalize_visible_text(str(item.get("target") or ""))
            for item in ledger
            if item.get("source") in _HISTORICAL_VISIBLE_SOURCES
        }
        missing = [
            target for target in targets
            if _normalize_visible_text(target) not in seen
        ]
        if missing:
            return False, "historical visible evidence missing: " + ", ".join(missing)
        return True, "historical visible evidence: " + ", ".join(targets)

    def _t_revise_plan(self, args: dict) -> ToolResult:
        steps = args.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return ToolResult("revise_plan 需要非空的 steps 数组")
        self.plan = planner.Plan(steps)
        self._fail_softened = False
        BUS.publish("agent_plan", action="revise", plan=self.plan.snapshot())
        return ToolResult(f"已更新计划：\n{self.plan.render()}")

    def _t_complete_subgoal(self, args: dict) -> ToolResult:
        verify_mod = _tools_attr("verify", verify)
        if self.plan is None or not self.plan.active():
            return ToolResult("当前没有计划，可先调用 revise_plan 制定")
        index = int(args.get("index", -1))
        if not (1 <= index <= len(self.plan.steps)):
            return ToolResult(f"子目标序号 {index} 无效。\n{self.plan.render()}")
        check_expect = self.plan.check_for(index)
        if check_expect:
            goal = str(self.plan.steps[index - 1].get("goal") or "")
            historical_ok, historical_detail = self._historical_visible_subgoal_ok(
                check_expect,
                goal,
            )
            outcome = verify_mod.check(check_expect, hwnd=self.hwnd, timeout_s=4.0)
            BUS.publish("agent_plan", action="subgoal_check", index=index,
                        verified=outcome.verified, detail=outcome.detail)
            if historical_ok and not outcome.verified:
                BUS.publish("agent_plan", action="subgoal_check", index=index,
                            verified=True, detail=historical_detail,
                            source="historical_visible_evidence")
            if not outcome.verified and not historical_ok:
                return ToolResult(
                    f"子目标 {index} 核验未通过（{outcome.detail}）——尚未真正完成，"
                    "请先达成该子目标再标完成，不要跳步。")
        self.plan.complete(index)
        BUS.publish("agent_plan", action="complete", index=index,
                    plan=self.plan.snapshot())
        if self.plan.all_done():
            return ToolResult(
                f"已完成子目标 {index}（已核验）。所有子目标均完成——若任务确已达成请调用 done。\n"
                f"{self.plan.render()}")
        return ToolResult(f"已完成子目标 {index}{'（已核验）' if check_expect else ''}。\n"
                          f"{self.plan.render()}")

    def _t_done(self, args: dict) -> ToolResult:
        summary = str(args.get("summary", ""))
        return ToolResult(f"任务完成: {summary}", terminal="done")

    def _t_fail(self, args: dict) -> ToolResult:
        reason = str(args.get("reason", ""))
        if self.replan_on_fail and not self._fail_softened:
            self._fail_softened = True
            BUS.publish("agent_recovery", reason=reason[:200])
            return ToolResult(
                f"在放弃前请再换一种思路尝试一次（你给的失败原因：{reason}）。"
                "建议：重新 observe 确认当前真实界面；检查是否有未处理的弹窗/遮挡；"
                "能否改用 run_shell 直接命令行完成；是否漏看了某个元素或其它窗口。"
                "若确认所有可行办法都已尝试且无解，再次调用 fail 即真正结束。",
                terminal=None,
            )
        return ToolResult(f"任务失败: {reason}", terminal="fail")
