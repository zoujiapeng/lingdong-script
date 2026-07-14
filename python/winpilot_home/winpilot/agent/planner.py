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
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Callable, Any

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
_TEXT_ENTRY_GOAL_RE = re.compile(
    r"\b(?:type|input|enter|write|fill|paste)\b|"
    r"\u8f93\u5165|\u952e\u5165|\u5199\u5165|\u5f55\u5165|\u586b\u5199|\u7c98\u8d34",
    re.IGNORECASE,
)
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
        if (
            key in _VISIBLE_CHECK_KEYS
            and isinstance(value, str)
            and _TEXT_ENTRY_GOAL_RE.search(goal or "")
            and len(_compact_text(value)) > 40
        ):
            pieces = [
                piece.strip()
                for piece in re.split(r"[\r\n]+|[\u3001\uff0c\uff1b;]+", value)
                if piece.strip()
            ]
            if re.search(r"[\u4e00-\u9fff]", value):
                whitespace_pieces = [piece.strip() for piece in value.split() if piece.strip()]
                if len(whitespace_pieces) >= 2:
                    pieces = whitespace_pieces
            candidate = next((piece for piece in pieces if 4 <= len(piece) <= 100), "")
            if candidate and candidate != value:
                sanitized[key] = candidate
                value = candidate
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


_ACTION_GOAL_RE = re.compile(
    r"(click|press|tap|select|type|input|search|send|open|button|btn|"
    r"\u70b9\u51fb|\u6309\u4e0b|\u9009\u62e9|\u8f93\u5165|"
    r"\u641c\u7d22|\u53d1\u9001|\u6253\u5f00|\u6309\u94ae)",
    re.IGNORECASE,
)
_VERIFY_GOAL_RE = re.compile(
    r"(verify|confirm|appear|appears|visible|show|shows|state|result|"
    r"\u786e\u8ba4|\u51fa\u73b0|\u53ef\u89c1|\u663e\u793a|"
    r"\u6838\u9a8c|\u9a8c\u8bc1|\u72b6\u6001|\u7ed3\u679c)",
    re.IGNORECASE,
)
_STATE_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def _is_gui_action_goal(goal: str) -> bool:
    return bool(_ACTION_GOAL_RE.search(goal or ""))


def _visible_text_from_verify_step(step: dict) -> str | None:
    check = step.get("check") if isinstance(step, dict) else None
    if isinstance(check, dict):
        for key in _VISIBLE_CHECK_KEYS:
            value = check.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    goal = str(step.get("goal", "") if isinstance(step, dict) else "")
    if not _VERIFY_GOAL_RE.search(goal):
        return None
    for token in _STATE_TOKEN_RE.findall(goal):
        if _looks_like_state_token(token):
            return token
    return None


def _merge_adjacent_visible_verification_steps(steps: list[dict]) -> list[dict]:
    """Merge "click/type X" + "verify Y appears" into one controller routine.

    Keeping the visible result check on the action subgoal nudges the main
    model toward execute_subgoal, where the fast controller can locate, click,
    wait, and verify without LLM observe/click/observe round-trips.
    """
    merged: list[dict] = []
    i = 0
    while i < len(steps):
        current = dict(steps[i])
        next_step = steps[i + 1] if i + 1 < len(steps) else None
        visible_text = _visible_text_from_verify_step(next_step or {})
        if (
            next_step
            and visible_text
            and _is_gui_action_goal(str(current.get("goal", "")))
            and not current.get("check")
        ):
            current["check"] = {"text_appears": visible_text}
            if visible_text not in str(current.get("goal", "")):
                current["goal"] = (
                    f"{current.get('goal', '')} and verify {visible_text}"
                ).strip()[:_MAX_GOAL_CHARS]
            merged.append(current)
            i += 2
            continue
        merged.append(current)
        i += 1
    return merged


_MAX_STEPS = 6
_MAX_GOAL_CHARS = 180
# Sequence markers that hint at a multi-step task.
_SEQ_MARKERS = (
    "然后", "再", "接着", "随后", "之后", "最后", "依次",
    " then ", " next ", " after that ", " finally ", "\n",
)
_STEP_VERB_RE = re.compile(
    r"\b(?:open|launch|focus|click|press|type|enter|fill|paste|save|export|"
    r"verify|confirm|search|send|reply|drag|draw|close)\b|"
    r"打开|启动|聚焦|点击|按下|输入|粘贴|保存|另存为|导出|确认|验证|"
    r"搜索|发送|回复|拖动|绘制|关闭",
    re.IGNORECASE,
)

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
    # Count semantic sequence markers, not commas: policy/constraint prose often
    # contains long comma-separated forbidden-tool lists that are not steps.
    folded = f" {text.casefold()} "
    segments = sum(folded.count(marker) for marker in _SEQ_MARKERS)
    if segments >= 4:
        return True
    # Length and policy prose are not task complexity.  A verbose two-action
    # GUI instruction (open URL, click, confirm; no shell/DOM) used to pay for
    # an upfront planner, then spend more turns maintaining a needless plan.
    # Require at least four explicit action verbs when sequence markers alone
    # do not prove a long horizon.
    explicit_verbs = {
        str(match).casefold() for match in _STEP_VERB_RE.findall(text)
    }
    return len(explicit_verbs) >= 4


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
        data = _parse_loose_step_list(blob)
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
    return _merge_adjacent_visible_verification_steps(out)[:_MAX_STEPS]


_KNOWN_FOLDER_MARKERS = (
    (("桌面", "desktop"), "Desktop"),
    (("文档", "documents"), "Documents"),
    (("下载", "downloads"), "Downloads"),
    (("图片", "pictures"), "Pictures"),
)


def _task_grounded_file_path(task: str, proposed: str) -> str:
    """Replace a planner-invented known-folder parent with the real user path."""
    value = str(proposed or "").strip()
    if not value or value.casefold() in (task or "").casefold():
        return value
    try:
        name = PureWindowsPath(value).name
    except Exception:
        name = Path(value).name
    task_folded = (task or "").casefold()
    if not name or name.casefold() not in task_folded:
        return value

    for explicit in re.findall(r"[A-Za-z]:\\[^\s\"'，。；;]+", task or ""):
        if PureWindowsPath(explicit).name.casefold() == name.casefold():
            return explicit
    for markers, folder_name in _KNOWN_FOLDER_MARKERS:
        if any(marker in task_folded for marker in markers):
            return str(Path.home() / folder_name / name)
    return value


def _ground_plan_file_checks(task: str, steps: list[dict]) -> list[dict]:
    """Ground model-generated file checks in paths actually named by the task."""
    explicit_paths = list(dict.fromkeys(
        re.findall(r"[A-Za-z]:\\[^\s\"'，。；;]+", task or "")
    ))
    grounded: list[dict] = []
    for raw_step in steps:
        step = dict(raw_step)
        check = step.get("check")
        if isinstance(check, dict):
            check = dict(check)
            if isinstance(check.get("file_exists"), str):
                check["file_exists"] = _task_grounded_file_path(
                    task,
                    check["file_exists"],
                )
            contains = check.get("file_contains")
            if isinstance(contains, dict) and isinstance(contains.get("path"), str):
                contains = dict(contains)
                contains["path"] = _task_grounded_file_path(task, contains["path"])
                check["file_contains"] = contains
        goal = str(step.get("goal") or "")
        goal_key = _compact_text(goal)
        is_output_step = any(marker in goal_key for marker in (
            "save", "saveas", "export", "writeoutput",
            "保存", "另存为", "导出", "写入文件",
        ))
        if explicit_paths and is_output_step:
            matching = [
                path for path in explicit_paths
                if _compact_text(PureWindowsPath(path).name) in goal_key
            ]
            chosen = matching[0] if matching else explicit_paths[-1]
            check = dict(check) if isinstance(check, dict) else {}
            check["file_exists"] = chosen
        if isinstance(check, dict) and check:
            step["check"] = check
        grounded.append(step)
    return grounded


def _parse_loose_step_list(raw: str) -> list[str]:
    """Best-effort recovery for model-emitted list strings with bad quoting."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if not text:
        return []
    if "\n" in text:
        parts = [
            re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
            for line in text.splitlines()
        ]
    else:
        body = text.strip()
        if (body.startswith('"') and body.endswith('"')) or (body.startswith("'") and body.endswith("'")):
            body = body[1:-1]
        parts = re.split(r'"\s*,\s*"|\'\s*,\s*\'|[;；]\s*', body)
        if len(parts) <= 1 and '", "' in body:
            parts = body.split('", "')
        if len(parts) <= 1 and "," in body and body.count(",") <= 8:
            parts = re.split(r"\s*,\s*(?=[\"'A-Za-z0-9\u4e00-\u9fff])", body)
    out: list[str] = []
    for part in parts:
        item = str(part or "").strip().strip("[] \t\r\n\"'")
        if item:
            out.append(item[:_MAX_GOAL_CHARS])
    return out


def make_plan(
    client,
    model: str,
    task: str,
    temperature: float = 0.2,
    on_response: Callable[[Any], None] | None = None,
) -> list[dict]:
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
        if on_response is not None:
            on_response(resp)
        steps = _ground_plan_file_checks(
            task,
            _parse_steps(resp.choices[0].message.content or ""),
        )
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
        self.steps = _merge_adjacent_visible_verification_steps(self.steps)

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

    def apply_operations(self, operations: list[dict]) -> list[str]:
        """Atomically add, edit, remove, or reorder todo items.

        Indices are 1-based and are evaluated against the list produced by the
        preceding operation. Existing completion state moves with an item. An
        edited item becomes pending because its acceptance contract changed.
        A reopen operation explicitly invalidates prior completion evidence.
        """
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty array")
        candidate = deepcopy(self.steps)
        changes: list[str] = []

        def _index(raw: object, *, upper: int, field: str) -> int:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError(f"{field} must be a 1-based integer")
            value = raw
            if not 1 <= value <= upper:
                raise ValueError(f"{field}={value} is outside 1..{upper}")
            return value

        def _new_step(goal: object) -> dict:
            value = str(goal or "").strip()[:_MAX_GOAL_CHARS]
            if not value:
                raise ValueError("add/update requires a non-empty goal")
            parsed = Plan([value]).steps
            if not parsed:
                raise ValueError("goal produced no valid todo item")
            return parsed[0]

        for position, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                raise ValueError(f"operation {position} must be an object")
            op = str(operation.get("op") or "").strip().casefold()
            if op == "add":
                step = _new_step(operation.get("goal"))
                raw_index = operation.get("index")
                if raw_index is None:
                    insert_at = len(candidate) + 1
                else:
                    insert_at = _index(
                        raw_index,
                        upper=len(candidate) + 1,
                        field="index",
                    )
                candidate.insert(insert_at - 1, step)
                changes.append(f"added item {insert_at}")
                continue
            if op in {"update", "remove", "move", "reopen"}:
                if not candidate:
                    raise ValueError(f"operation {position} cannot {op} an empty plan")
                source = _index(
                    operation.get("index"), upper=len(candidate), field="index"
                )
                if op == "update":
                    candidate[source - 1] = _new_step(operation.get("goal"))
                    changes.append(f"updated item {source}; it is pending again")
                elif op == "remove":
                    candidate.pop(source - 1)
                    changes.append(f"removed item {source}")
                elif op == "move":
                    destination = _index(
                        operation.get("to_index"),
                        upper=len(candidate),
                        field="to_index",
                    )
                    step = candidate.pop(source - 1)
                    candidate.insert(destination - 1, step)
                    changes.append(f"moved item {source} to {destination}")
                else:
                    candidate[source - 1]["done"] = False
                    reason = str(operation.get("reason") or "").strip()
                    changes.append(
                        f"reopened item {source}"
                        + (f" ({reason[:120]})" if reason else "")
                    )
                continue
            raise ValueError(
                f"operation {position} has unsupported op={op!r}; "
                "use add, update, remove, move, or reopen"
            )

        self.steps = candidate
        return changes

    def render(self) -> str:
        lines = ["当前计划(todo)："]
        for i, step in enumerate(self.steps, start=1):
            mark = "x" if step["done"] else " "
            tag = " (含核验)" if step.get("check") else ""
            lines.append(f"[{mark}] {i}. {step['goal']}{tag}")
        lines.append(
            "Plan controls (when exposed): complete grounded items; add/update/remove/"
            "move/reopen items whenever evidence changes the route. Avoid a no-op restatement."
        )
        lines.append(
            "Plan state is guidance, not completion evidence. Without plan tools, "
            "continue from grounded evidence and finish with done."
        )
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        return [{"goal": s["goal"], "done": s["done"]} for s in self.steps]
