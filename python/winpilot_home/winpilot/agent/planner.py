"""Planning / todo layer: optional task decomposition for long-horizon tasks.

Design (negative-optimization guards):
- Upfront planning fires only for genuinely multi-step tasks (conservative
  heuristic); trivial tasks like "计算器 12×34" skip the extra LLM call.
- The plan is injected ONCE into the stable context prefix (cache-friendly);
  progress is carried forward by ``complete_subgoal`` tool results in the
  append-only history — the plan block is never rewritten per step.
- The model can also self-initiate / revise a plan mid-task via ``revise_plan``.
"""
from __future__ import annotations

import json
import re

from ..config import CONFIG
from ..utils.log import logger

# Valid keys for a subgoal completion check (mirror verify.SUPPORTED_CONDITIONS;
# imported lazily to avoid an import cycle at module load).
def _valid_check(check) -> dict | None:
    """Keep a check only if it's a dict of recognized verify conditions —
    an unknown key would make complete_subgoal a permanent blocker."""
    if not isinstance(check, dict) or not check:
        return None
    try:
        from ..action.verify import SUPPORTED_CONDITIONS
        known = set(SUPPORTED_CONDITIONS)
    except Exception:
        return check  # can't validate → trust it
    if all(k in known for k in check):
        return check
    logger.warning("规划 check 含未知条件 %s，已忽略该子目标的核验", list(check))
    return None


_VISIBLE_CHECK_KEYS = {"text_appears", "text_contains"}
_RESULT_CONTEXT_MARKERS = (
    "appear", "appears", "appeared", "visible", "shown", "show", "shows",
    "verify", "verified", "confirm", "confirmed", "result", "state",
    "出现", "显示", "可见", "确认", "核验", "验证", "结果", "状态", "生效",
)
_BUTTON_NOUN_PATTERN = r"(?:button|btn|menu|link|按钮|按键|菜单|链接)"
_CLICK_VERB_PATTERN = r"(?:click|press|tap|select|点击|按下|按|选择)"


def _compact_text(text: str) -> str:
    return re.sub(r"[\s_\-:/\\|()（）\[\]【】\"'“”‘’<>]+", "", text or "").casefold()


def _looks_like_state_token(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if "_" in value or any(ch.isdigit() for ch in value):
        return True
    ascii_letters = [ch for ch in value if ch.isascii() and ch.isalpha()]
    return len(ascii_letters) >= 5 and all(ch.upper() == ch for ch in ascii_letters)


def _occurrence_looks_like_button_label(goal: str, pos: int, value_len: int) -> bool:
    before = goal[max(0, pos - 18):pos]
    after = goal[pos + value_len:pos + value_len + 18]
    if re.search(rf"^\s*{_BUTTON_NOUN_PATTERN}", after, re.IGNORECASE):
        return True
    return bool(
        re.search(
            rf"{_CLICK_VERB_PATTERN}\s*(?:the\s+)?[\"'“”‘’]?\s*$",
            before,
            re.IGNORECASE,
        )
    )


def _value_has_result_context(goal: str, value: str) -> bool:
    haystack = (goal or "").casefold()
    needle = (value or "").strip().casefold()
    if not haystack or not needle:
        return False
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            return False
        context = haystack[max(0, pos - 18): pos + len(needle) + 18]
        if (
            any(marker in context for marker in _RESULT_CONTEXT_MARKERS)
            and not _occurrence_looks_like_button_label(goal, pos, len(needle))
        ):
            return True
        start = pos + max(1, len(needle))


def _check_echoes_button_label(goal: str, key: str, value: str) -> bool:
    """Return True when a text check is just the clicked control label.

    A common planning failure is:
      {"goal":"click Apply/Input button", "check":{"text_appears":"Apply/Input"}}
    The label is already visible before the action, so complete_subgoal can
    become a permanent blocker. Keep distinctive state tokens and values with
    explicit result/verification context.
    """
    if key not in _VISIBLE_CHECK_KEYS:
        return False
    label = (value or "").strip()
    if not label or not goal:
        return False
    if _looks_like_state_token(label) or _value_has_result_context(goal, label):
        return False
    if _compact_text(label) not in _compact_text(goal):
        return False
    escaped = re.escape(label)
    patterns = (
        rf"{escaped}\s*{_BUTTON_NOUN_PATTERN}",
        rf"{_CLICK_VERB_PATTERN}\s*(?:the\s+)?[\"'“”‘’]?\s*{escaped}",
    )
    return any(re.search(pattern, goal, re.IGNORECASE) for pattern in patterns)


def _sanitize_check_for_goal(goal: str, check) -> dict | None:
    valid = _valid_check(check)
    if not valid:
        return None
    sanitized = dict(valid)
    dropped: list[str] = []
    for key, value in list(sanitized.items()):
        if isinstance(value, str) and _check_echoes_button_label(goal, key, value):
            dropped.append(f"{key}={value!r}")
            sanitized.pop(key, None)
    if dropped:
        logger.warning(
            "规划 check 把按钮/菜单标签当成结果，已忽略: goal=%r dropped=%s",
            goal,
            dropped,
        )
    return sanitized or None


_MAX_STEPS = 6
_MAX_GOAL_CHARS = 60
# Sequence markers that hint at a multi-step task.
_SEQ_MARKERS = ("然后", "再", "接着", "之后", "最后", "依次", "并", "，", ",", "、", ";", "；", "\n")

_PLAN_SYSTEM = (
    "你是任务规划助手。把用户的 Windows 操作任务拆解为 2-6 个有序、可独立验证的子目标。"
    "输出一个 JSON 数组。每项可以是一句简短中文子目标(动宾短语)，"
    "或一个对象 {\"goal\":\"子目标\", \"check\":{验证条件}} —— 当某子目标有【确定性完成信号】时"
    "尽量带上 check(用于客观核验是否真完成)，可用键: "
    '{"file_exists":"路径"} {"file_contains":{"path":"..","text":".."}} '
    '{"process_running":"exe名"} {"window_appears":"标题"} {"text_appears":"界面文字"}。'
    "不要把点击前就存在的按钮/菜单/链接自身文字当成 text_appears check；点击类子目标只有在有点击后的新文字、状态、文件、进程或窗口时才带 check。"
    "无确定性信号的子目标用纯字符串即可，不要编造 check。不要编号、不要解释。"
    '例: ["打开记事本", {"goal":"另存为到桌面a.txt", "check":{"file_exists":"C:\\\\Users\\\\me\\\\Desktop\\\\a.txt"}}]'
)


def planning_enabled() -> bool:
    return bool(CONFIG.get("agent", "planning_enabled", default=True))


def should_plan_upfront(task: str) -> bool:
    """Conservative: only auto-plan tasks that clearly span multiple steps."""
    if not planning_enabled():
        return False
    text = (task or "").strip()
    if len(text) >= 45:
        return True
    # Count total occurrences (not mere presence) so "a,b,c,d"-style short
    # enumerations don't trigger an unnecessary planning call.
    segments = sum(text.count(marker) for marker in _SEQ_MARKERS)
    return segments >= 4


def _parse_steps(raw: str) -> list[dict]:
    """Extract subgoals (each {goal, check}) from model output, tolerantly.

    Accepts items as plain strings or {"goal","check"} objects; coerces to a
    uniform list of dicts. Returns [] on failure.
    """
    if not raw:
        return []
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    blob = match.group(0) if match else raw
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if isinstance(item, dict):
            goal = str(item.get("goal", "")).strip()[:_MAX_GOAL_CHARS]
            check = _sanitize_check_for_goal(goal, item.get("check"))
        else:
            goal, check = str(item).strip()[:_MAX_GOAL_CHARS], None
        if goal:
            out.append({"goal": goal, "check": check})
    return out[:_MAX_STEPS]


def make_plan(client, model: str, task: str, temperature: float = 0.2) -> list[dict]:
    """One focused LLM call -> ordered subgoals (each {goal, check}). [] on failure."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content": task},
            ],
            temperature=temperature,
            max_tokens=500,
        )
        steps = _parse_steps(resp.choices[0].message.content or "")
        logger.info("规划生成 %d 个子目标 (%d 个带核验)",
                    len(steps), sum(1 for s in steps if s.get("check")))
        return steps
    except Exception as exc:  # planning is best-effort; never block the run
        logger.warning("规划调用失败，退回无计划模式: %s", exc)
        return []


class Plan:
    """Ordered subgoals with completion flags + optional verification checks."""

    def __init__(self, steps: list) -> None:
        self.steps: list[dict] = []
        for s in steps:
            if isinstance(s, dict):
                self.steps.append({
                    "goal": str(s.get("goal", "")).strip()[:_MAX_GOAL_CHARS],
                    "done": False,
                    "check": _sanitize_check_for_goal(
                        str(s.get("goal", "")).strip()[:_MAX_GOAL_CHARS],
                        s.get("check"),
                    ),
                })
            else:
                self.steps.append({"goal": str(s).strip()[:_MAX_GOAL_CHARS],
                                   "done": False, "check": None})
        self.steps = [s for s in self.steps if s["goal"]]

    def active(self) -> bool:
        return bool(self.steps)

    def all_done(self) -> bool:
        return bool(self.steps) and all(s["done"] for s in self.steps)

    def check_for(self, index: int) -> dict | None:
        """The verification expect for a 1-based subgoal, if any."""
        if 1 <= index <= len(self.steps):
            return self.steps[index - 1].get("check")
        return None

    def complete(self, index: int) -> bool:
        """Mark a 1-based subgoal done. Returns False if index is invalid."""
        if 1 <= index <= len(self.steps):
            self.steps[index - 1]["done"] = True
            return True
        return False

    def render(self) -> str:
        lines = ["当前计划(todo)："]
        for i, step in enumerate(self.steps, start=1):
            mark = "x" if step["done"] else " "
            tag = " (含核验)" if step.get("check") else ""
            lines.append(f"[{mark}] {i}. {step['goal']}{tag}")
        lines.append(
            "完成一项就调用 complete_subgoal(index=序号)；计划不合适就调用 revise_plan 重列。"
        )
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        return [{"goal": s["goal"], "done": s["done"]} for s in self.steps]
