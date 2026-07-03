"""Experience-memory tool handlers for ToolSession."""
from __future__ import annotations

from ..memory import experience
from ..utils.log import BUS
from .tool_result import ToolResult


class MemoryToolMixin:
    def _t_remember(self, args: dict) -> ToolResult:
        note = str(args.get("note", "")).strip()
        if not note:
            return ToolResult("remember 需要 note 内容")
        conditions = args.get("conditions") or []
        evidence = args.get("evidence") or []
        recipe = experience.add(
            note,
            app=str(args.get("app", "")).strip(),
            steps=[note],
            conditions=conditions if isinstance(conditions, list) else [str(conditions)],
            evidence=evidence if isinstance(evidence, list) else [str(evidence)],
        )
        BUS.publish("memory", action="remember", id=recipe["id"], note=note[:120])
        return ToolResult(f"已记住（id={recipe['id']}）: {note}")

    def _t_forget(self, args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult("forget 需要 query 关键词")
        removed = experience.delete_matching(query)
        if not removed:
            return ToolResult(f'没有匹配 "{query}" 的记忆条目')
        names = "；".join(r.get("task", "")[:60] for r in removed)
        BUS.publish("memory", action="forget", count=len(removed), query=query)
        return ToolResult(f"已删除 {len(removed)} 条记忆: {names}")

    def _t_reuse_skill(self, args: dict) -> ToolResult:
        from ..config import SCRIPTS_DIR
        from ..recorder import trace as trace_mod

        task = str(args.get("task", "")).strip()
        recipe_id = str(args.get("recipe_id", "")).strip()
        script, recipe = experience.reusable_script_for(task or self.task, recipe_id)
        if not script:
            return ToolResult(
                "没有可复用的成功流程脚本（该任务无匹配的已验证历史）。请改用实时操作。")
        script_path = SCRIPTS_DIR / script
        if not script_path.exists():
            return ToolResult(f"复用脚本 {script} 已丢失，请实时操作。")
        if recipe and recipe.get("id"):
            used = getattr(self, "used_memory_recipe_ids", None)
            if isinstance(used, list) and recipe["id"] not in used:
                used.append(recipe["id"])
        BUS.publish("memory", action="reuse", task=(recipe or {}).get("task", task))
        try:
            result = trace_mod.replay(script_path, stop_event=self.stop_event)
        except Exception as exc:
            return ToolResult(f"复用重放异常: {exc}。请改用实时操作。")
        steps = result.get("results", [])
        if result.get("ok"):
            return ToolResult(
                f"✅ 已 0-token 复用上次成功流程（{len(steps)}步全部重放成功）。"
                "请 observe 确认最终结果是否符合预期，符合即可 done。")
        failed = next((s for s in steps if not s.get("ok")), None)
        at = failed.get("index") if failed else len(steps)
        return ToolResult(
            f"⚠️ 复用重放进行到第 {at} 步（{failed.get('tool') if failed else '?'}）失败，"
            f"前 {at-1} 步已执行。请先 observe 看当前界面，从第 {at} 步起改用实时操作完成剩余部分。")
