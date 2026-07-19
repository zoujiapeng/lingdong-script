"""Planning and terminal state tool handlers for ToolSession."""
from __future__ import annotations

import re
import sys

from ..action import verify
from ..utils.log import BUS
from . import planner
from .task_rules import _is_multi_source_research_task
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


_HISTORICAL_VISIBLE_SOURCES = {
    "observe",
    "read_text",
    "execute_subgoal:evidence_packet",
    "click:verified_expect",
    "type_text:verified_expect",
    "hotkey:verified_expect",
    "launch:verified_expect",
    "run_action_batch:verified_expect",
}
_CURRENT_CONTROLLER_VISIBLE_SOURCES = {
    "observe",
    "read_text",
    "execute_subgoal:evidence_packet",
    "execute_subgoal:visual_color",
}
_FILE_CHECK_KEYS = {"file_exists"}
_SOURCE_COLLECTION_GOAL_RE = re.compile(
    r"\b(?:collect|extract|record|capture|gather)\b|收集|提取|记录|整理.*(?:字段|信息)",
    re.IGNORECASE,
)
_SOURCE_NAVIGATION_GOAL_RE = re.compile(
    r"\b(?:open|navigate|visit|load)\b|\u6253\u5f00|\u8fdb\u5165|\u8bbf\u95ee|\u5bfc\u822a",
    re.IGNORECASE,
)
_SOURCE_TOKEN_STOP = {
    "collect", "extract", "record", "capture", "gather", "product", "page",
    "official", "source", "url", "name", "connection", "battery", "life",
    "weight", "chrome", "logitech", "from", "fields", "information",
    "open", "navigate", "visit", "load", "verify", "title", "contains",
}


def _research_goal_matches_record(session, goal: str) -> bool:
    records = session.world.working_set()
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", goal or "")
        if token.casefold() not in _SOURCE_TOKEN_STOP
    }
    if not records or not tokens:
        return False
    for record in records:
        identity = " ".join((
            str(record.get("subject") or ""),
            str(record.get("source_title") or ""),
            str(record.get("source_url") or ""),
        )).casefold()
        if any(token in identity for token in tokens):
            return True
    return False


def _research_source_completion_error(session, goal: str) -> str:
    if not _is_multi_source_research_task(getattr(session, "task", "")):
        return ""
    if not _SOURCE_COLLECTION_GOAL_RE.search(goal or ""):
        return ""
    if _research_goal_matches_record(session, goal):
        return ""
    records = session.world.working_set()
    if not records:
        return "No source-bound facts have been recorded for this collection step."
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", goal or "")
        if token.casefold() not in _SOURCE_TOKEN_STOP
    }
    if not tokens:
        return "" if records else "No source-bound facts have been recorded."
    for record in records:
        identity = " ".join((
            str(record.get("subject") or ""),
            str(record.get("source_title") or ""),
            str(record.get("source_url") or ""),
        )).casefold()
        if any(token in identity for token in tokens):
            return ""
    return (
        "The working set has no source record matching this collection step "
        f"(distinctive tokens: {', '.join(sorted(tokens))}). Navigate to that "
        "source and call record_facts before completing the step."
    )
_FORM_OR_QUIZ_CONTEXT_RE = re.compile(
    r"("
    r"answer\s+(?:the\s+)?question|question\s*\d+|\bq\s*\d+\b|"
    r"quiz|exam|survey|form|multiple\s+choice|single\s+choice|"
    r"\u7b54\u9898|\u9898\u76ee|\u7b2c\s*\d+\s*\u9898|"
    r"\u5355\u9009|\u591a\u9009|\u5224\u65ad|\u8003\u8bd5|"
    r"\u95ee\u5377|\u8868\u5355|\u4f5c\u4e1a"
    r")",
    re.IGNORECASE,
)
_SUBMIT_OR_SAVE_RE = re.compile(
    r"("
    r"submit|turn\s+in|hand\s+in|save|"
    r"\u63d0\u4ea4|\u4ea4\u5377|\u4fdd\u5b58"
    r")",
    re.IGNORECASE,
)
_COMPLETION_RATE_RE = re.compile(
    r"(?:\u5b8c\u6210\u7387|completion\s*(?:rate)?)\s*[:\uff1a]?\s*(\d{1,3})\s*%",
    re.IGNORECASE,
)
def _is_coding_runtime(session) -> bool:
    profile = getattr(session, "runtime_profile", None)
    return bool(getattr(profile, "coding", False))


def _looks_like_form_or_quiz_context(task: str, goal: str) -> bool:
    return bool(_FORM_OR_QUIZ_CONTEXT_RE.search(f"{task}\n{goal}"))


def _looks_like_submit_or_save_goal(goal: str) -> bool:
    return bool(_SUBMIT_OR_SAVE_RE.search(goal or ""))


def _latest_completion_rate(ledger: list[dict] | None) -> int | None:
    if not isinstance(ledger, list):
        return None
    for item in reversed(ledger):
        if not isinstance(item, dict):
            continue
        text = f"{item.get('target') or ''}\n{item.get('snippet') or ''}"
        match = _COMPLETION_RATE_RE.search(text)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        return max(0, min(100, value))
    return None


def _file_check_paths(check: dict | None) -> list[str]:
    if not isinstance(check, dict):
        return []
    paths: list[str] = []
    for key in _FILE_CHECK_KEYS:
        value = check.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    file_contains = check.get("file_contains")
    if isinstance(file_contains, dict):
        path = file_contains.get("path")
        if isinstance(path, str) and path.strip():
            paths.append(path.strip())
    return paths


def _spurious_agent_schedule_file_check(
    task: str,
    goal: str,
    check: dict | None,
) -> tuple[bool, str]:
    """Ignore Windows Task Scheduler file checks for WinPilot schedules."""
    paths = _file_check_paths(check)
    if not paths:
        return False, ""
    text = f"{task}\n{goal}".casefold()
    mentions_winpilot_schedule = (
        ("winpilot" in text and ("schedule" in text or "计划任务" in text or "定时" in text))
        or "schedule_task" in text
        or "list_scheduled_tasks" in text
        or "cancel_scheduled_task" in text
        or "agent scheduled task" in text
        or "scheduled agent" in text
    )
    if not mentions_winpilot_schedule:
        return False, ""
    invented_windows_paths = [
        path for path in paths
        if path.replace("/", "\\").casefold().startswith(r"c:\windows\system32\tasks")
    ]
    if not invented_windows_paths:
        return False, ""
    return (
        True,
        "ignored planner-invented Windows Task Scheduler file check for "
        "a WinPilot internal scheduled task: " + ", ".join(invented_windows_paths),
    )

_COLOR_ALIASES = {
    "blue": {"blue", "蓝", "蓝色"},
    "red": {"red", "红", "红色"},
    "green": {"green", "绿", "绿色"},
    "yellow": {"yellow", "黄", "黄色"},
    "black": {"black", "黑", "黑色"},
    "purple": {"purple", "紫", "紫色"},
    "orange": {"orange", "橙", "橙色"},
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


def _color_keys_in_text(text: str) -> set[str]:
    compact = _normalize_visible_text(text)
    keys: set[str] = set()
    for color, aliases in _COLOR_ALIASES.items():
        if any(_normalize_visible_text(alias) in compact for alias in aliases):
            keys.add(color)
    return keys


class PlanToolMixin:
    def _current_controller_visible_subgoal_ok(
        self,
        check: dict | None,
    ) -> tuple[bool, str]:
        targets = _visible_check_values(check)
        if not targets:
            return False, "subgoal check has no visible text target"
        ledger = getattr(self, "visible_evidence_ledger", [])
        current_epoch = int(getattr(self, "_screen_epoch", 0) or 0)
        recent = []
        for item in ledger[-8:]:
            source = str(item.get("source") or "")
            if source not in _CURRENT_CONTROLLER_VISIBLE_SOURCES:
                continue
            if source in {"observe", "read_text"}:
                # Raw reads are current evidence only for the exact desktop
                # mutation epoch they observed. A later GUI action invalidates
                # them automatically; legacy entries without an epoch remain
                # historical-only.
                if "screen_epoch" not in item:
                    continue
                if int(item.get("screen_epoch") or 0) != current_epoch:
                    continue
            recent.append(item)
        seen = {
            _normalize_visible_text(str(item.get("target") or ""))
            for item in recent
            if item.get("source") != "execute_subgoal:visual_color"
        }
        seen_visual_colors = {
            str(item.get("target") or "").casefold()
            for item in recent
            if item.get("source") == "execute_subgoal:visual_color"
        }
        missing = [
            target for target in targets
            if _normalize_visible_text(target) not in seen
            and not (_color_keys_in_text(target) & seen_visual_colors)
        ]
        if missing:
            return False, "controller evidence missing: " + ", ".join(missing)
        return True, "current controller evidence: " + ", ".join(targets)

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

    def _unchecked_subgoal_blocker(self, goal: str) -> str:
        """Return a reason when a no-check subgoal is unsafe to mark done."""
        # Coding/file/shell work has its own deterministic final evidence gate.
        # Its action counter includes successful run_shell/write_file calls and
        # must not be mislabeled as pending *GUI* evidence between plan steps.
        # Final done verification still requires real file/process/test output.
        if _is_coding_runtime(self):
            return ""
        pending = int(getattr(getattr(self, "run_state", None), "unverified_actions", 0) or 0)
        if pending > 0:
            return (
                f"{pending} GUI action(s) are still unverified. Use observe/read_text "
                "or an explicit check before marking this subgoal complete."
            )
        task = str(getattr(self, "task", "") or "")
        completion_rate = _latest_completion_rate(getattr(self, "visible_evidence_ledger", []))
        if _looks_like_submit_or_save_goal(goal) and completion_rate is not None and completion_rate < 100:
            return (
                f"recent page evidence says completion_rate={completion_rate}%, "
                "so a save/submit/finish subgoal cannot be marked complete yet."
            )
        if _looks_like_form_or_quiz_context(task, goal):
            detail = (
                "form/question workflows need explicit current evidence. "
                "Revise this plan step with a check, or verify current question/selection/"
                "completion text with observe/read_text/execute_subgoal first."
            )
            if completion_rate is not None:
                detail += f" Latest completion_rate evidence is {completion_rate}%."
            return detail
        if _looks_like_submit_or_save_goal(goal):
            return (
                "save/submit/finish subgoals need explicit current evidence "
                "such as visible success text, final state, or a verified file/result."
            )
        return ""

    def _t_revise_plan(self, args: dict) -> ToolResult:
        steps = args.get("steps")
        operations = args.get("operations")
        has_steps = "steps" in args
        has_operations = "operations" in args
        if has_steps == has_operations:
            return ToolResult(
                "revise_plan requires exactly one field: steps or operations"
            )
        if has_steps and not isinstance(steps, list):
            return ToolResult("revise_plan steps must be an array (it may be empty)")
        if has_operations and (
            not isinstance(operations, list) or not operations
        ):
            return ToolResult("revise_plan operations must be a non-empty array")

        changes: list[str] = []
        skip_working_set_restore = False
        if has_steps:
            assert isinstance(steps, list)
            previously_done = [
                (
                    str(step.get("goal") or "").strip().casefold(),
                    step.get("check"),
                )
                for step in (self.plan.steps if self.plan is not None else [])
                if step.get("done")
            ]
            replacement = planner.Plan(steps)
            for index, step in enumerate(replacement.steps, start=1):
                goal_key = str(step.get("goal") or "").strip().casefold()
                match = next((
                    position
                    for position, (prior_goal, prior_check) in enumerate(previously_done)
                    if prior_goal == goal_key and prior_check == step.get("check")
                ), None)
                if match is not None:
                    replacement.complete(index)
                    previously_done.pop(match)
            self.plan = replacement
            changes.append("replaced the ordered todo list")
        else:
            assert isinstance(operations, list)
            skip_working_set_restore = any(
                isinstance(operation, dict)
                and str(operation.get("op") or "").strip().casefold() == "reopen"
                for operation in operations
            )
            candidate_plan = self.plan if self.plan is not None else planner.Plan([])
            try:
                changes = candidate_plan.apply_operations(operations)
            except ValueError as exc:
                return ToolResult(f"revise_plan operations rejected atomically: {exc}")
            self.plan = candidate_plan

        restored: list[int] = []
        if (
            not skip_working_set_restore
            and _is_multi_source_research_task(getattr(self, "task", ""))
        ):
            for index, step in enumerate(self.plan.steps, start=1):
                goal = str(step.get("goal") or "")
                if (
                    (_SOURCE_COLLECTION_GOAL_RE.search(goal) or _SOURCE_NAVIGATION_GOAL_RE.search(goal))
                    and _research_goal_matches_record(self, goal)
                ):
                    self.plan.complete(index)
                    restored.append(index)
        self._fail_softened = False
        BUS.publish(
            "agent_plan",
            run_id=getattr(self, "run_id", ""),
            action="revise",
            plan=self.plan.snapshot(),
            restored_from_working_set=restored,
        )
        restored_text = (
            f"\nRestored completed source steps from working set: {restored}."
            if restored else ""
        )
        return ToolResult(
            f"Plan updated ({'; '.join(changes)}).\n"
            f"{self.plan.render()}{restored_text}"
        )

    def _t_complete_subgoal(self, args: dict) -> ToolResult:
        verify_mod = _tools_attr("verify", verify)
        if self.plan is None or not self.plan.active():
            return ToolResult("当前没有计划，可先调用 revise_plan 制定")
        raw_index = args.get("index", -1)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            return ToolResult("complete_subgoal index must be a 1-based integer")
        index = raw_index
        if not (1 <= index <= len(self.plan.steps)):
            return ToolResult(f"子目标序号 {index} 无效。\n{self.plan.render()}")
        if self.plan.steps[index - 1].get("done"):
            BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                        action="complete", index=index,
                        plan=self.plan.snapshot(), already_done=True)
            if self.plan.all_done():
                return ToolResult(
                    f"Subgoal {index} was already completed; all subgoals are complete.\n"
                    f"{self.plan.render()}",
                    terminal="done",
                )
            return ToolResult(
                f"Subgoal {index} was already completed.\n{self.plan.render()}"
            )
        check_expect = self.plan.check_for(index)
        goal = str(self.plan.steps[index - 1].get("goal") or "")
        source_error = _research_source_completion_error(self, goal)
        if source_error:
            BUS.publish(
                "agent_plan",
                run_id=getattr(self, "run_id", ""),
                action="subgoal_check",
                index=index,
                verified=False,
                detail=source_error,
                source="working_set_source_guard",
            )
            return ToolResult(
                f"Subgoal {index} is not complete: {source_error}\n{self.plan.render()}"
            )
        ignored_file_check, ignored_detail = _spurious_agent_schedule_file_check(
            getattr(self, "task", ""),
            goal,
            check_expect,
        )
        if ignored_file_check:
            BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                        action="subgoal_check", index=index,
                        verified=True, detail=ignored_detail,
                        source="ignored_spurious_file_check")
            check_expect = None
        if check_expect:
            current_ok, current_detail = self._current_controller_visible_subgoal_ok(check_expect)
            if current_ok:
                BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                            action="subgoal_check", index=index,
                            verified=True, detail=current_detail,
                            source="current_controller_evidence")
            else:
                outcome = None
            historical_ok, historical_detail = self._historical_visible_subgoal_ok(
                check_expect,
                goal,
            )
            if not current_ok:
                outcome = verify_mod.check(check_expect, hwnd=self.hwnd, timeout_s=4.0)
                BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                            action="subgoal_check", index=index,
                            verified=outcome.verified, detail=outcome.detail)
            if historical_ok and not current_ok and outcome is not None and not outcome.verified:
                BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                            action="subgoal_check", index=index,
                            verified=True, detail=historical_detail,
                            source="historical_visible_evidence")
            if (
                not current_ok
                and outcome is not None
                and not outcome.verified
                and not historical_ok
            ):
                return ToolResult(
                    f"子目标 {index} 核验未通过（{outcome.detail}）——尚未真正完成，"
                    "请先达成该子目标再标完成，不要跳步。")
        elif not ignored_file_check:
            blocker = self._unchecked_subgoal_blocker(goal)
            if blocker:
                BUS.publish(
                    "agent_plan",
                    run_id=getattr(self, "run_id", ""),
                    action="subgoal_check",
                    index=index,
                    verified=False,
                    detail=blocker,
                    source="unchecked_subgoal_guard",
                )
                return ToolResult(
                    f"Subgoal {index} has no check and is not safe to mark complete: "
                    f"{blocker}"
                )
        self.plan.complete(index)
        BUS.publish("agent_plan", run_id=getattr(self, "run_id", ""),
                    action="complete", index=index,
                    plan=self.plan.snapshot())
        if self.plan.all_done():
            return ToolResult(
                f"已完成子目标 {index}（已核验）。所有子目标均完成，进入完成确认。\n"
                f"{self.plan.render()}",
                terminal="done",
            )
        return ToolResult(f"已完成子目标 {index}{'（已核验）' if check_expect else ''}。\n"
                          f"{self.plan.render()}")

    def _t_done(self, args: dict) -> ToolResult:
        summary = str(args.get("summary", ""))
        return ToolResult(f"任务完成: {summary}", terminal="done")

    def _t_blocked(self, args: dict) -> ToolResult:
        """Pause honestly when the real UI cannot be grounded safely."""
        reason = str(args.get("reason", "") or "当前真实界面无法可靠判断").strip()
        evidence = str(args.get("evidence", "") or "").strip()
        question = str(args.get("question", "") or "请确认界面状态或告诉我下一步。").strip()
        BUS.publish(
            "agent_blocked",
            run_id=getattr(self, "run_id", None),
            reason=reason[:300],
            evidence=evidence[:600],
            question=question[:300],
        )
        body = f"任务已安全暂停：{reason}"
        if evidence:
            body += f"\n当前证据：{evidence}"
        if question:
            body += f"\n需要用户确认：{question}"
        return ToolResult(body, terminal="reply")

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
