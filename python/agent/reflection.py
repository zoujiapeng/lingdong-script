"""
ReflectionEngine — Agent 自我反思引擎

提供两个层级的反思：
  - per-step: 每步执行后轻量分析，提取即时洞察
  - post-task: 任务完成后深度分析，提取规则+偏好+经验

两阶段架构 (参考 claude-reflect):
  Stage 1: 快速模式匹配 (PatternAnalyzer, 零 LLM 成本)
  Stage 2: LLM 深度分析 (按需, best-effort)

复用已有的 _call_llm 方法，强制 JSON 输出。
所有反思都是 best-effort 的，失败不中断主任务。
"""

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.reflection")

# ── 反思系统提示词 ──

STEP_REFLECTION_SYSTEM_PROMPT = (
    "You are a reflection engine. Analyze a single agent step and determine "
    "if there's a useful insight or behavioral rule to extract.\n"
    "Respond ONLY with a JSON object. No markdown, no explanation.\n"
    "Format: {\"insight\": \"brief analysis of what happened\", "
    "\"rule\": \"actionable rule to follow in future\" or null, "
    "\"confidence\": 0.0-1.0}"
)

TASK_REFLECTION_SYSTEM_PROMPT = (
    "You are a deep reflection engine. Analyze a completed agent task and "
    "extract behavioral rules, user preferences, and lessons learned.\n"
    "Respond ONLY with a JSON object. No markdown, no explanation.\n"
    "Format: {\n"
    '  "overall_assessment": "one-sentence summary",\n'
    '  "rules": [{"rule": "if X then Y", "trigger": "context", '
    '"confidence": 0.8, "category": "approach"}],\n'
    '  "preferences": [{"key": "preference_name", "value": "observed_value", '
    '"confidence": 0.7}],\n'
    '  "insights": ["key takeaway 1"],\n'
    '  "mistakes": ["what went wrong"]\n'
    "}"
)


class ReflectionEngine:
    """Agent 反思引擎。

    Args:
        call_llm_fn: 复用 agent._call_llm 方法
        emit_fn: 复用 agent._emit 方法 (发送 IPC 事件)
    """

    def __init__(self,
                 call_llm_fn: Callable[[list[dict]], Optional[dict]],
                 emit_fn: Optional[Callable[[str, dict], None]] = None):
        self._call_llm = call_llm_fn
        self._emit = emit_fn or (lambda t, d: None)

    # ── Per-step reflection (two-stage) ──

    def reflect_step(self, step: dict, goal: str) -> dict:
        """分析单步执行结果，提取即时洞察。

        两阶段:
          Stage 1: 快速模式匹配错误信息 (零成本)
          Stage 2: LLM 深度分析 (仅当 Stage 1 有信号时)

        Returns:
            dict with keys: insight, rule (or None), confidence
            或空 dict (无有价值信息时)
        """
        tool = step.get("tool", "unknown")
        success = step.get("result", {}).get("success", False)
        error = step.get("result", {}).get("error", "")
        args = step.get("args", {})

        # 简单步骤或完全成功的步骤可能不产生洞察
        if success and not error:
            return {}

        # ── Stage 1: Pattern pre-analysis (zero LLM cost) ──
        try:
            from agent.memory import PatternAnalyzer
            signal = PatternAnalyzer.analyze(error)
            has_quick_signal = signal["type"] is not None and signal["confidence"] >= 0.5
        except Exception:
            signal = {}
            has_quick_signal = False

        # ── Stage 2: LLM call (only if warranted) ──
        # 条件: 错误信息 > 20字符 (有实质内容) 或 quick signal 认为有价值
        if not has_quick_signal and len(error) < 20 and not (error and not success):
            return {}  # 无实质内容, 跳过 LLM

        prompt = (
            f"Task goal: {goal[:200]}\n"
            f"Executed tool: {tool}\n"
            f"Arguments: {json.dumps(args, ensure_ascii=False)[:200]}\n"
            f"Status: {'success' if success else 'failure'}\n"
            f"Error: {error[:200] if error else 'none'}\n\n"
            f"Analyze this step. If a useful rule can be extracted "
            f"(e.g., 'When doing X, always do Y first'), output it. "
            f"Otherwise return empty insight."
        )
        result = self._llm_json_call(
            messages=[
                {"role": "system", "content": STEP_REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        if not result or not result.get("insight"):
            return {}

        # 如果 Quick Signal 有置信度，与 LLM 结果合并
        if has_quick_signal and result.get("confidence", 0) < signal.get("confidence", 0):
            result["confidence"] = (result["confidence"] + signal["confidence"]) / 2
            result["_quick_signal"] = signal.get("type")

        return result

    # ── Post-task reflection ──

    def reflect_task(self, goal: str, steps: list, success: bool,
                     elapsed_ms: int) -> dict:
        """分析完整任务执行，提取规则、偏好和经验。

        Returns:
            dict with keys: overall_assessment, rules[], preferences[],
            insights[], mistakes[]
        """
        if not steps:
            return {}

        # 构建步骤摘要（取最近 15 步）
        step_lines = []
        for s in steps[-15:]:
            status = "OK" if s.get("result", {}).get("success") else "FAIL"
            tool = s.get("tool", "?")
            step_lines.append(f"  Step {s.get('step_num')}: {tool} ({status})")

        elapsed_sec = elapsed_ms / 1000 if elapsed_ms else 0
        prompt = (
            f"Task analysis:\n"
            f"Goal: {goal[:300]}\n"
            f"Success: {'yes' if success else 'no'}\n"
            f"Total steps: {len(steps)}\n"
            f"Elapsed: {elapsed_sec:.1f}s\n"
            f"Steps:\n" + "\n".join(step_lines) +
            "\n\nAnalyze this completed task and extract practical knowledge."
        )

        result = self._llm_json_call(
            messages=[
                {"role": "system", "content": TASK_REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        if not result:
            return {}
        return result

    # ── LLM call helper ──

    def _llm_json_call(self, messages: list[dict]) -> dict:
        """调用 LLM 并解析 JSON 响应。

        Returns:
            解析后的 dict，失败返回空 dict
        """
        try:
            response = self._call_llm(messages)
            if not response:
                return {}
            choices = response.get("choices", [])
            if not choices:
                return {}
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                return {}
            return self._parse_json(content)
        except Exception as e:
            logger.debug("Reflection LLM call failed: %s", e)
            return {}

    @staticmethod
    def _parse_json(text: str) -> dict:
        """宽容解析 LLM 的 JSON 输出。"""
        if not text:
            return {}

        # 尝试直接解析
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 提取 ```json ... ``` 围栏
        import re
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 提取第一个 { ... } 块
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        return {}

    @staticmethod
    def format_reflection_result(step_num: int, reflection: dict) -> dict:
        """格式化 per-step 反思结果为 IPC 事件。"""
        return {
            "step_num": step_num,
            "insight": reflection.get("insight", ""),
            "rule": reflection.get("rule"),
            "confidence": reflection.get("confidence", 0),
        }

    @staticmethod
    def format_learning_result(reflection: dict, stats: dict) -> dict:
        """格式化 post-task 学习结果为 IPC 事件。"""
        return {
            "rules_count": len(reflection.get("rules", [])),
            "prefs_count": len(reflection.get("preferences", [])),
            "insights": reflection.get("insights", [])[:5],
            "mistakes": reflection.get("mistakes", [])[:5],
            "stats": stats,
        }
