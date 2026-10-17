"""One semantic Visual Actor backed by the shared model call Runtime."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...modeling import (
    ModelAttempt,
    ModelCallRuntime,
    ModelRetryPolicy,
    invoke_chat_completion,
)
from ..actor import (
    ACTOR_PROPOSAL_FORMAT,
    InvalidActorProposal,
)
from ..runtime.contracts import (
    APPROVED_DESKTOP_ACTIONS,
    APPROVED_RUNTIME_TOOLS,
    TASK_STATE_UPDATE_SCHEMA,
    ActorTaskState,
    ActorTaskStateError,
    ActorTaskStateUpdate,
    ActorToolView,
    ActorTurnDecision,
    ActorTurnInput,
)
from .common import (
    LiveModelRoleError,
    member,
    narration_content,
    require_empty_content,
    require_jpeg,
    require_positive_int,
    require_positive_timeout,
    require_text,
    response_message,
    strict_json_object,
)


VISUAL_ACTOR_MODEL_ROLE = "vnext_visual_actor"
_PROVIDER_REJECTION_PREFIX = (
    "provider rejected model response: finish_reason="
)
VISUAL_ACTOR_SYSTEM_CONTRACT = """\
读 original_goal、当前截图、task_state、previous_action、tools。
每回合只做一个决定：一个 GUI 动作，或一批互不依赖的非 GUI 工具。简述你在做什么。
坐标就是这张图的像素，它也是唯一的权威视觉状态。
打开或切换应用用 launch_application；截图用 copy_screen_to_clipboard，不要调系统工具。
用 task_state_update.todos 维护用户可见的清单（pending / in_progress / completed）。
pointer_held 会保持到你显式松开。
目标已达成、或用户只是在聊天，就用 reply_to_user 回话；任何工具都改不了的外部阻塞，用 blocked。
聊天与操作照常进行，runtime 不做分类或路由。"""

_TOOL_DESCRIPTIONS = {
    "move_mouse": (
        "把真实指针移到当前图片上的一个点并停在那里。"
        "duration_ms 控制平滑悬停或游戏式移动。"
    ),
    "click": (
        "在当前图片上的一个点单击一次，可指定鼠标键。"
        "press_duration_ms 控制按住时长。"
    ),
    "double_click": "在当前图片上的一个点双击，可指定鼠标键。",
    "drag": (
        "按顺序经过若干点拖拽一次，点写成 `x1,y1 x2,y2 ...`。"
        "第一对是按下点，最后一对是松手点，坐标都是提交图片的像素。"
        "held_keys 会在同一手势里按住；keep_holding 保持按下，留到后续回合再松。"
    ),
    "scroll": (
        "在一个点上滚动，amount 是原生滚轮增量：amount < 0 向下，"
        "amount > 0 向上，120 是普通的一格。整页滚动通常用 360 到 720。"
    ),
    "type_text": (
        "把指定的字面字符输入到当前获得焦点的可编辑控件；"
        "已选中的文字会被替换。"
    ),
    "hotkey": (
        "通过物理键盘按下一个键或一个有序组合键；它不会输入字面文字。"
        "repeat 表示同一动作里重复按几次。常见键名有 volumeup、volumedown、"
        "volumemute、方向键、功能键和浏览器键。音量键是相对当前系统音量调节，"
        "要看下一张图判断结果。"
    ),
    "input_sequence": (
        "执行一段原子的、接近人手的鼠标/键盘时间线。用分号分隔："
        "`move x,y [duration_ms]`；`mouse_down [button] [x,y]`；"
        "`mouse_up [button] [x,y]`；`click [button] [x,y]`；"
        "`key_down key`；`key_up key`；`press key`；`type_text \"literal\"`；"
        "`wheel amount`；`hwheel amount`；`wait duration_ms`。"
        "滚轮量是原生增量（120 是普通一格）。"
        "整段手势（点输入框再粘贴或输入）用它一次提交，"
        "不要拆成一回合一步。"
        "相对原始/游戏移动用 `move_by dx,dy duration_ms`。"
        "绝对坐标和相对增量都基于当前图片。"
        "本序列新按下的键会自动释放；已存在的 pointer_held 保持到显式 mouse_up 或拖拽松手。"
    ),
    "launch_application": (
        "按显示名、别名或路径打开/恢复一个已安装的应用或文件、URL 关联；"
        "用户说的名字可以直接传，runtime 会在本地应用索引（含桌面快捷方式和商店应用）里解析，"
        "解析失败由工具自己报告。若该应用已在运行，会激活已有实例而不是再开一个。"
    ),
    "observe": (
        "在不产生物理输入的情况下取一张新的整桌面画面。"
        "用于跳出重复的本地工具链、刷新视觉上下文、重新考虑下一步。"
    ),
    "claim_obligation_done": (
        "回复用户或结束，不再做任何改动。把简短回答或完成结果放进 concise_claim。"
    ),
    "blocked": "只有在任何给定动作都无法推进时才报告外部阻塞。",
}

_MODEL_TOOL_NAMES = {
    "hotkey": "press_key",
    "observe": "refresh_desktop",
    "claim_obligation_done": "reply_to_user",
    "copy_current_view": "copy_screen_to_clipboard",
}
_RUNTIME_TOOL_NAMES = {
    model_name: runtime_name
    for runtime_name, model_name in _MODEL_TOOL_NAMES.items()
}
_STRICT_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "type",
    }
)
_LOCALLY_ENFORCED_SCHEMA_KEYS = frozenset({"not", "uniqueItems"})
_FLOW_FRAGMENT_REQUEST_CHARS = 16_384
_STANDALONE_CONNECTOR_CALL_ALLOWANCE = 4
_CONNECTOR_SEARCH_TOOL = "search_connector_tools"
_CONNECTOR_CALL_TOOL = "call_connector_tool"
_CONNECTOR_TOOLS = frozenset({_CONNECTOR_SEARCH_TOOL, _CONNECTOR_CALL_TOOL})
_FLOW_FRAGMENT_TOOL = "propose_flow_fragment"
_SHELL_TOOL = "run_shell"
_DETACHED_SHELL_TOOL = "run_detached"
_WRITE_FILE_TOOL = "write_file"
_LOCAL_UTILITY_TOOLS = frozenset({_DETACHED_SHELL_TOOL, _WRITE_FILE_TOOL})
_STANDALONE_SHELL_CALL_ALLOWANCE = 4
_SHELL_MAX_COMMAND_CHARS = 16_384
# 同一个动作在画面没有任何变化的情况下重复到这一次数，就由 runtime 拒绝执行。
# 这是机械层的截断，不是给模型的规则：人失败三四次就会换招，runtime 只负责把
# "这一招已经无效"这件事说清楚，换不换招仍由模型决定。
_REPEAT_ACTION_LIMIT = 3
# 连续这么多帧，画面改动比例都低于地板（0.25%），说明这些动作对屏幕没有任何
# 影响。地板值来自 config 里那条从没被读过的 visual_preflight_change_ratio：
# 光标闪烁、时钟跳秒都在 0.0005 以下，换页/弹窗在 0.16 以上，中间是真空。
_NO_PROGRESS_RATIO = 0.0025
_NO_PROGRESS_LIMIT = 4
_READ_STATE_TOOL = "read_state"
_WINDOW_TEXT_TOOL = "read_window_text"
_STANDALONE_WINDOW_TEXT_ALLOWANCE = 2
_WINDOW_TEXT_MAX_CHARS = 6000
# Text attached to a completed action's result is bounded much tighter than an
# explicit read: it arrives every turn whether or not the model wants it.
_POST_ACTION_WINDOW_TEXT_CHARS = 600
# The model's own most recent plan survives into the next turn, bounded.
_KEPT_PLAN_CHARS = 1200
_COPY_CURRENT_VIEW_TOOL = _MODEL_TOOL_NAMES["copy_current_view"]
_STANDALONE_COPY_CURRENT_VIEW_ALLOWANCE = 2
_LIST_WINDOWS_TOOL = "list_windows"
_STANDALONE_LIST_WINDOWS_ALLOWANCE = 1
_ACTIVATE_WINDOW_TOOL = "activate_window"
_STANDALONE_ACTIVATE_WINDOW_ALLOWANCE = 2
# Optional tool-schema fields and their JSON defaults.  The model may omit
# them; the runtime fills the default so the tool schema and the validator
# never disagree.
_OPTIONAL_TOOL_DEFAULTS = {
    _DETACHED_SHELL_TOOL: {"name": "task"},
    _CONNECTOR_SEARCH_TOOL: {"query": "", "connector": ""},
    _WINDOW_TEXT_TOOL: {"window": ""},
}

@dataclass(slots=True)
class _VisualExchange:
    """One pending provider-native tool transcript awaiting its GUI result."""

    messages: tuple[dict[str, Any], ...]
    tool_call_id: str
    runtime_tool_name: str
    result_content: dict[str, Any] | None = None


def _text_only_tool_transcript(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Keep this run's tool conversation without old screenshots.

    The model's own short plan and reasoning for the *latest* completed action is
    kept: without it the model has no record of what it had already decided, so
    it re-derives the same first step from the same goal and the same-looking
    frame and repeats it.  Measured on QQ: 40 identical "click the input box"
    plans in a row, each with the model's own reasoning saying "and then type".
    Older prose is still dropped, because only the latest decision can still be
    acted on.
    """

    transcript: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            transcript.append({
                "role": "user",
                "content": (
                    "Earlier desktop frame omitted; use the current JPEG as "
                    "the only authoritative visual state."
                ),
            })
            continue
        if role not in {"assistant", "tool"}:
            continue
        transcript.append(deepcopy(dict(message)))
    return _keep_latest_plan(tuple(transcript))


def _keep_latest_plan(
    transcript: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """Strip plan text from every assistant turn except the most recent one."""

    last_assistant = -1
    for index, item in enumerate(transcript):
        if item.get("role") == "assistant":
            last_assistant = index
    kept: list[dict[str, Any]] = []
    for index, item in enumerate(transcript):
        entry = dict(item)
        if entry.get("role") == "assistant" and index != last_assistant:
            entry.pop("reasoning_content", None)
            entry["content"] = ""
        elif entry.get("role") == "assistant":
            reasoning = entry.get("reasoning_content")
            if isinstance(reasoning, str):
                entry["reasoning_content"] = reasoning[:_KEPT_PLAN_CHARS]
            content = entry.get("content")
            if isinstance(content, str):
                entry["content"] = content[:_KEPT_PLAN_CHARS]
        kept.append(entry)
    return tuple(kept)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _repeated_tool_result(
    result: Mapping[str, Any],
    *,
    same_as_tool_call_id: str,
    repeat_count: int,
) -> dict[str, Any]:
    """Mark an identical result while keeping the latest feedback usable."""

    repeated = dict(result)
    repeated.update({
        "result_unchanged": True,
        "new_tool_information": False,
        "same_as_tool_call_id": same_as_tool_call_id,
        "repeat_count": repeat_count,
        "message": (
            "Executed; this tool returned the same output as the referenced "
            "call."
        ),
    })
    if result.get("error") and "ok" not in repeated:
        repeated["ok"] = False
    return repeated


def _project_tool_result(
    seen: dict[tuple[str, str], tuple[str, int]],
    *,
    name: str,
    call_id: str,
    result: Mapping[str, Any],
    model_calls_remaining: int | None = None,
) -> dict[str, Any]:
    """Annotate repeated execution without choosing the model's context."""

    comparable = dict(result)
    comparable.pop("full_output_path", None)
    try:
        key = (name, _canonical_json(comparable))
    except (TypeError, ValueError):
        projected = dict(result)
        if model_calls_remaining is not None:
            projected["model_calls_remaining"] = model_calls_remaining
        return projected
    previous = seen.get(key)
    if previous is None:
        seen[key] = (call_id, 0)
        projected = dict(result)
        if model_calls_remaining is not None:
            projected["model_calls_remaining"] = model_calls_remaining
        return projected
    first_call_id, previous_count = previous
    repeat_count = previous_count + 1
    seen[key] = (first_call_id, repeat_count)
    projected = _repeated_tool_result(
        result,
        same_as_tool_call_id=first_call_id,
        repeat_count=repeat_count,
    )
    if model_calls_remaining is not None:
        projected["model_calls_remaining"] = model_calls_remaining
    return projected


def _projected_tool_message(
    seen: dict[tuple[str, str], tuple[str, int]],
    *,
    name: str,
    call_id: str,
    result: Mapping[str, Any],
    model_calls_remaining: int | None = None,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": _canonical_json(
            _project_tool_result(
                seen,
                name=name,
                call_id=call_id,
                result=result,
                model_calls_remaining=model_calls_remaining,
            )
        ),
    }


def _model_visible_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Hide recorder-only fields from the model's native tool result."""

    return {
        key: value
        for key, value in result.items()
        if not str(key).startswith("_")
    }


def _finish_reason(response: Any) -> str:
    choices = member(response, "choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        return ""
    return str(member(choices[0], "finish_reason") or "").casefold()


def _provider_rejection_detail(response: Any) -> str:
    """Classify a provider-side refusal without inspecting response prose."""

    reason = _finish_reason(response)
    if reason in {"", "stop", "tool_calls", "length"}:
        return ""
    reason = re.sub(r"[^a-z0-9._-]+", "_", reason).strip("_")[:80]
    return _PROVIDER_REJECTION_PREFIX + (reason or "unknown")


def _truncation_messages(response: Any) -> list[dict[str, Any]]:
    """Retry without feeding an incomplete tool payload back to the model."""

    del response
    return [
        {
            "role": "user",
            "content": "Output was truncated. Continue with a shorter tool call.",
        },
    ]


def _actionable_tool_result(
    name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep tool failures in-band so the same model can change course."""

    output = dict(result)
    failed = bool(
        output.get("ok") is False
        or output.get("error")
        or output.get("timeout") is True
        or output.get("stopped") is True
    )
    if not failed or output.get("next_step"):
        return output
    if output.get("timeout") is True:
        next_step = (
            "this attempt timed out; use any partial output and current state "
            "to choose the next step"
        )
    elif name == _SHELL_TOOL and output.get("exit_code") not in (None, 0):
        next_step = "use the output to correct the command or choose another tool"
    elif output.get("stopped") is True:
        next_step = "do not retry unless the run continues"
    else:
        next_step = "change the arguments, inspect current state, or choose another tool"
    output["next_step"] = next_step
    return output


def _actionable_tool_error(name: str, exc: Exception) -> dict[str, Any]:
    detail = str(exc).strip() or type(exc).__name__
    return _actionable_tool_result(
        name,
        {"ok": False, "error": detail[:2000]},
    )


def _tool_function_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool, Mapping) else None
    name = function.get("name") if isinstance(function, Mapping) else None
    return name if type(name) is str else ""


def _decision_id(turn: ActorTurnInput, image_b64: str) -> str:
    digest = hashlib.sha256()
    digest.update(turn.to_json().encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(image_b64.encode("ascii")).digest())
    return f"visual-actor-{digest.hexdigest()[:24]}"


def _initial_decision_id(
    goal: str,
    history: Sequence[Mapping[str, Any]],
) -> str:
    digest = hashlib.sha256()
    for message in history:
        digest.update(_canonical_json(message).encode("utf-8"))
        digest.update(b"\0")
    digest.update(goal.encode("utf-8"))
    return f"visual-actor-initial-{digest.hexdigest()[:24]}"


def _thread_messages(
    history: Sequence[Mapping[str, Any]],
    goal: str,
) -> list[dict[str, str]]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise LiveModelRoleError("Actor thread history must be a sequence")
    messages: list[dict[str, str]] = []
    for raw in history:
        if not isinstance(raw, Mapping) or set(raw) != {"role", "content"}:
            raise LiveModelRoleError(
                "Actor thread history messages require only role and content"
            )
        role = raw.get("role")
        content = raw.get("content")
        if role not in {"user", "assistant"} or type(content) is not str:
            raise LiveModelRoleError(
                "Actor thread history supports only text user and assistant messages"
            )
        if not content.strip():
            raise LiveModelRoleError("Actor thread history content must not be empty")
        messages.append({"role": role, "content": content})
    # Preserve the native prefix supplied by the Thread owner.  The Actor
    # validates its shape but does not choose which successful turns the model
    # may see or silently rebuild the provider's cache prefix.
    messages.append({"role": "user", "content": goal})
    return messages


def _strict_provider_schema(
    raw: dict[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    """Project the Broker schema into OpenAI's strict supported subset.

    ``not`` and ``uniqueItems`` are enforced again by Runtime's canonical
    Broker argument admission before mutation. They cannot be sent in a strict
    Structured Outputs schema, so only those two assertions are omitted here.
    Unknown keywords fail closed instead of reaching the provider.
    """
    unsupported = set(raw) - (
        _STRICT_SCHEMA_KEYS | _LOCALLY_ENFORCED_SCHEMA_KEYS
    )
    if unsupported:
        raise LiveModelRoleError(
            f"{path} has unsupported strict schema keywords: "
            f"{sorted(unsupported)!r}"
        )
    if "not" in raw and raw["not"] != {"const": 0}:
        raise LiveModelRoleError(
            f"{path}.not is not the locally enforced nonzero assertion"
        )
    if "uniqueItems" in raw and raw["uniqueItems"] is not True:
        raise LiveModelRoleError(
            f"{path}.uniqueItems must be true when present"
        )

    projected = {
        key: value
        for key, value in raw.items()
        if key not in _LOCALLY_ENFORCED_SCHEMA_KEYS
    }
    properties = projected.get("properties")
    if properties is not None:
        if type(properties) is not dict or any(
            type(name) is not str or type(schema) is not dict
            for name, schema in properties.items()
        ):
            raise LiveModelRoleError(
                f"{path}.properties must map names to schemas"
            )
        projected["properties"] = {
            name: _strict_provider_schema(
                schema,
                path=f"{path}.properties.{name}",
            )
            for name, schema in properties.items()
        }
    items = projected.get("items")
    if items is not None:
        if type(items) is not dict:
            raise LiveModelRoleError(f"{path}.items must be a schema")
        projected["items"] = _strict_provider_schema(
            items,
            path=f"{path}.items",
        )
    alternatives = projected.get("anyOf")
    if alternatives is not None:
        if (
            type(alternatives) is not list
            or not alternatives
            or any(type(schema) is not dict for schema in alternatives)
        ):
            raise LiveModelRoleError(
                f"{path}.anyOf must be a non-empty schema array"
            )
        projected["anyOf"] = [
            _strict_provider_schema(
                schema,
                path=f"{path}.anyOf[{index}]",
            )
            for index, schema in enumerate(alternatives)
        ]
    if projected.get("type") == "object":
        if type(properties) is not dict:
            raise LiveModelRoleError(
                f"{path} object schema requires properties"
            )
        required = projected.get("required")
        if type(required) is not list or len(required) != len(set(required)):
            raise LiveModelRoleError(
                f"{path} required must be a de-duplicated list"
            )
        missing_required = set(required) - set(properties)
        if missing_required:
            raise LiveModelRoleError(
                f"{path} required names are not properties: "
                f"{sorted(missing_required)!r}"
            )
        optional_without_default = {
            name
            for name, schema in properties.items()
            if name not in required and "default" not in schema
        }
        if optional_without_default:
            raise LiveModelRoleError(
                f"{path} optional properties need an explicit default: "
                f"{sorted(optional_without_default)!r}"
            )
        if projected.get("additionalProperties") is not False:
            raise LiveModelRoleError(
                f"{path} must forbid extras"
            )
    return projected


def _actor_parameters_from_schema(
    name: str,
    raw: Mapping[str, Any],
    *,
    requires_local_intent: bool,
) -> dict[str, Any]:
    del requires_local_intent
    if (
        type(raw) is not dict
        or raw.get("type") != "object"
        or type(raw.get("properties")) is not dict
    ):
        raise LiveModelRoleError(
            f"Actor tool {name!r} requires an object JSON schema"
        )
    parameters = dict(raw)
    properties = dict(raw["properties"])
    required = raw.get("required", [])
    if type(required) is not list or any(type(item) is not str for item in required):
        raise LiveModelRoleError(
            f"Actor tool {name!r} has invalid required fields"
        )
    required = list(required)
    if "task_state_update" in properties or "task_state_update" in required:
        raise LiveModelRoleError(
            f"Actor tool {name!r} shadows task_state_update"
        )
    if "local_intent" in properties or "local_intent" in required:
        raise LiveModelRoleError(
            f"Actor tool {name!r} shadows Actor-owned metadata"
        )
    # Completed tool/result exchanges are retained without old images or
    # prose. Keep the model-owned scratchpad available for facts that should
    # not be forced back through OCR. This is context, not a runtime plan.
    properties["task_state_update"] = {
        **deepcopy(TASK_STATE_UPDATE_SCHEMA),
        "default": {"scratchpad": None},
    }
    parameters["properties"] = properties
    parameters["required"] = required
    parameters["additionalProperties"] = False
    return _strict_provider_schema(
        parameters,
        path=f"Actor tool {name!r}",
    )


def _actor_parameters(
    tool: ActorToolView,
    *,
    pointer_held: bool,
) -> dict[str, Any]:
    raw = tool.to_dict()["schema"]
    if tool.name == "drag":
        raw_properties = dict(raw["properties"])
        properties = {
            "points": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "description": (
                    "Ordered submitted-image coordinate pairs written as "
                    "x1,y1 x2,y2 ..."
                ),
            },
            **{
                name: raw_properties[name]
                for name in ("keep_holding", "button", "held_keys")
                if name in raw_properties
            },
        }
        raw = {
            **raw,
            "properties": properties,
            "required": ["points"],
        }
    return _actor_parameters_from_schema(
        tool.name,
        raw,
        requires_local_intent=tool.requires_local_intent,
    )


def _foreground_label_outline(
    reader: Any,
    turn: ActorTurnInput,
) -> list[str] | None:
    """前台窗口里真实存在的控件文字，压成一份很短的清单。

    QQ 空间那种大图查看器，下载按钮只是一个 16 像素的图标，在整屏截图里
    认不出来；而它的可访问名字就是"下载图片"。这是事实，不是规则。
    """

    if reader is None or not callable(reader):
        return None
    try:
        result = reader({"window": "", "turn": turn})
    except TypeError:
        try:
            result = reader(turn)
        except Exception:
            return None
    except Exception:
        return None
    if not isinstance(result, Mapping):
        return None
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    labels: list[str] = []
    for line in text.splitlines():
        label = line.strip().lstrip("-*#").strip()
        if not label or len(label) > 24:
            continue
        if label in labels:
            continue
        labels.append(label)
        if len(labels) >= 40:
            break
    return labels or None


def _foreground_window_fact(inventory: Any) -> dict[str, Any] | None:
    """Return the one window the desktop is currently showing, if the backend says.

    ``list_windows`` already marks the foreground row; this only projects the
    same snapshot the turn already fetched, so it adds no extra Windows call and
    no failure path: an inventory without a foreground row simply omits the fact.
    """

    if not isinstance(inventory, Mapping):
        return None
    rows = inventory.get("windows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    for row in rows:
        if not isinstance(row, Mapping) or row.get("foreground") is not True:
            continue
        fact: dict[str, Any] = {}
        title = str(row.get("title") or "").strip()
        if title:
            fact["title"] = title[:200]
        process_name = str(row.get("process_name") or "").strip()
        if process_name:
            fact["process_name"] = process_name[:80]
        class_name = str(row.get("class_name") or "").strip()
        if class_name:
            fact["class_name"] = class_name[:120]
        hwnd = row.get("hwnd")
        if type(hwnd) is int and hwnd > 0:
            fact["hwnd"] = hwnd
        return fact or None
    return None


def _tool_definitions(turn: ActorTurnInput) -> list[dict[str, Any]]:
    names = tuple(tool.name for tool in turn.tools)
    if not names or not set(names) <= APPROVED_RUNTIME_TOOLS:
        raise LiveModelRoleError(
            "Visual Actor tools are outside the minimal Runtime surface"
        )
    return [
        {
            "type": "function",
            "function": {
                "name": _MODEL_TOOL_NAMES.get(tool.name, tool.name),
                "description": (
                    "Continue the held drag through ordered points text "
                    "written as `x1,y1 x2,y2 ...`; the current physical cursor "
                    "is already the start. keep_holding decides whether to "
                    "remain pressed."
                    if tool.name == "drag" and turn.pointer_held
                    else _TOOL_DESCRIPTIONS[tool.name]
                ),
                "parameters": _actor_parameters(
                    tool,
                    pointer_held=turn.pointer_held,
                ),
                "strict": True,
            },
        }
        for tool in turn.tools
    ]


def _decode_arguments(raw: Any) -> dict[str, Any]:
    return strict_json_object(raw, "Actor tool arguments")


def _canonical_drag_arguments(
    arguments: Mapping[str, Any],
    *,
    pointer_held: bool,
) -> dict[str, Any]:
    """Parse one provider-stable path value into the canonical point list."""

    raw_path = arguments.get("points")
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    path_pattern = rf"\s*{number}\s*,\s*{number}(?:\s+{number}\s*,\s*{number})*\s*"
    if type(raw_path) is not str or re.fullmatch(path_pattern, raw_path) is None:
        raise InvalidActorProposal(
            "drag points must be `x1,y1 x2,y2 ...`"
        )
    values = [float(value) for value in re.findall(number, raw_path)]
    canonical_path = [
        [
            int(values[index]) if values[index].is_integer() else values[index],
            (
                int(values[index + 1])
                if values[index + 1].is_integer()
                else values[index + 1]
            ),
        ]
        for index in range(0, len(values), 2)
    ]
    minimum = 1 if pointer_held else 2
    if len(canonical_path) < minimum:
        raise InvalidActorProposal(
            f"drag points requires at least {minimum} coordinate pair"
            f"{'s' if minimum != 1 else ''}"
        )
    canonical = {
        key: value
        for key, value in arguments.items()
        if key != "points"
    }
    canonical["image_path"] = canonical_path
    canonical.setdefault("button", "left")
    # Keep the Actor semantic: it forwards the model's compact key spelling.
    # The desktop adapter already owns forgiving physical-key normalization.
    if not canonical.get("held_keys"):
        canonical.pop("held_keys", None)
    canonical.setdefault("duration_ms", 400)
    return canonical


def _canonical_desktop_arguments(
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    canonical = dict(arguments)
    if name in {"click", "double_click"}:
        canonical.setdefault("button", "left")
        canonical.setdefault("press_duration_ms", 35)
    elif name == "move_mouse":
        canonical.setdefault("duration_ms", 0)
    return canonical


def _flow_fragment_tool_definition(
    allowed_node_types: Sequence[str],
) -> dict[str, Any]:
    node_types = list(dict.fromkeys(allowed_node_types))
    if not node_types or any(type(item) is not str or not item for item in node_types):
        raise LiveModelRoleError("flow fragment node catalog is invalid")
    return {
        "type": "function",
        "function": {
            "name": _FLOW_FRAGMENT_TOOL,
            "description": (
                "Propose one editable, non-running flow fragment using only "
                "the supplied node catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 1000},
                    "nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "type": {"type": "string", "enum": node_types},
                                "label": {"type": "string", "maxLength": 120},
                                "params_json": {"type": "string", "minLength": 2, "maxLength": 4096},
                            },
                            "required": ["local_id", "type", "label", "params_json"],
                            "additionalProperties": False,
                        },
                    },
                    "edges": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string", "minLength": 1, "maxLength": 64},
                                "target": {"type": "string", "minLength": 1, "maxLength": 64},
                                "source_port": {"type": "string", "minLength": 1, "maxLength": 64},
                            },
                            "required": ["source", "target", "source_port"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "nodes", "edges"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _flow_fragment_from_response(
    response: Any,
    catalog_ports: Mapping[str, Sequence[str]],
    catalog_params: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    message = response_message(response)
    require_empty_content(message)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        raise LiveModelRoleError("flow fragment proposal requires exactly one tool call")
    call = calls[0]
    function = member(call, "function")
    if member(call, "type") != "function" or function is None:
        raise LiveModelRoleError("flow fragment proposal must be a function call")
    if member(function, "name") != _FLOW_FRAGMENT_TOOL:
        raise LiveModelRoleError("Actor returned an unapproved flow fragment tool")
    arguments = _decode_arguments(member(function, "arguments"))
    if set(arguments) != {"summary", "nodes", "edges"}:
        raise LiveModelRoleError("flow fragment proposal fields mismatch")
    summary = arguments["summary"]
    raw_nodes = arguments["nodes"]
    raw_edges = arguments["edges"]
    if type(summary) is not str or len(summary) > 1000:
        raise LiveModelRoleError("flow fragment summary is invalid")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= 32:
        raise LiveModelRoleError("flow fragment must contain 1 to 32 nodes")
    if not isinstance(raw_edges, list) or len(raw_edges) > 64:
        raise LiveModelRoleError("flow fragment contains too many edges")

    nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    node_types: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping) or set(raw) != {
            "local_id", "type", "label", "params_json"
        }:
            raise LiveModelRoleError(f"flow fragment node {index + 1} fields mismatch")
        local_id = raw["local_id"]
        node_type = raw["type"]
        label = raw["label"]
        params_json = raw["params_json"]
        if type(local_id) is not str or not local_id.strip() or len(local_id) > 64:
            raise LiveModelRoleError(f"flow fragment node {index + 1} id is invalid")
        local_id = local_id.strip()
        if local_id in ids:
            raise LiveModelRoleError("flow fragment contains duplicate node ids")
        if type(node_type) is not str or node_type not in catalog_ports:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} type is unavailable")
        if type(label) is not str or len(label) > 120:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} label is invalid")
        if type(params_json) is not str or not 2 <= len(params_json) <= 4096:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} params are invalid")
        params = strict_json_object(
            params_json,
            f"flow fragment node {local_id!r} params_json",
        )
        unknown_params = set(params) - catalog_params[node_type]
        if unknown_params:
            raise LiveModelRoleError(
                f"flow fragment node {local_id!r} uses unavailable params: "
                + ", ".join(sorted(unknown_params))
            )
        ids.add(local_id)
        node_types[local_id] = node_type
        nodes.append({
            "local_id": local_id,
            "type": node_type,
            "label": label.strip(),
            "params": params,
        })

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    seen_branches: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, Mapping) or set(raw) != {
            "source", "target", "source_port"
        }:
            raise LiveModelRoleError(f"flow fragment edge {index + 1} fields mismatch")
        source, target, source_port = (
            raw["source"], raw["target"], raw["source_port"]
        )
        if not all(type(item) is str and item.strip() for item in (source, target, source_port)):
            raise LiveModelRoleError(f"flow fragment edge {index + 1} is invalid")
        source, target, source_port = source.strip(), target.strip(), source_port.strip()
        if source not in ids or target not in ids:
            raise LiveModelRoleError("flow fragment edge references an unknown node")
        if source == target:
            raise LiveModelRoleError("flow fragment edge cannot target its source node")
        if source_port not in catalog_ports[node_types[source]]:
            raise LiveModelRoleError(
                f"flow fragment node {source!r} has no {source_port!r} output"
            )
        key = (source, target, source_port)
        if key in seen_edges:
            raise LiveModelRoleError("flow fragment contains a duplicate edge")
        branch = (source, source_port)
        if branch in seen_branches:
            raise LiveModelRoleError(
                "flow fragment output branch can target only one node"
            )
        seen_edges.add(key)
        seen_branches.add(branch)
        edges.append({"source": source, "target": target, "source_port": source_port})

    outgoing = {local_id: [] for local_id in ids}
    indegree = {local_id: 0 for local_id in ids}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    roots = [local_id for local_id, degree in indegree.items() if degree == 0]
    ordered = 0
    while roots:
        current = roots.pop()
        ordered += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                roots.append(target)
    if ordered != len(nodes):
        raise LiveModelRoleError("flow fragment must not contain a directed cycle")
    if len(nodes) > 1:
        if not edges:
            raise LiveModelRoleError(
                "multi-node flow fragment requires internal edges"
            )
        neighbours = {local_id: set() for local_id in ids}
        for edge in edges:
            neighbours[edge["source"]].add(edge["target"])
            neighbours[edge["target"]].add(edge["source"])
        pending = [next(iter(ids))]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(neighbours[current] - visited)
        if visited != ids:
            raise LiveModelRoleError(
                "flow fragment must not contain disconnected nodes"
            )
    return {"summary": summary.strip(), "nodes": nodes, "edges": edges}


def _runtime_name_from_model_call(name: str, raw_arguments: Any) -> str:
    del raw_arguments
    return _RUNTIME_TOOL_NAMES.get(name, name)


def _proposal_from_response(
    response: Any,
    allowed_names: frozenset[str],
    available_runtime_names: frozenset[str],
    *,
    pointer_held: bool,
) -> ActorTurnDecision:
    message = response_message(response)
    narration = narration_content(message)
    tool_calls = member(message, "tool_calls")
    if (
        isinstance(tool_calls, (str, bytes))
        or not isinstance(tool_calls, Sequence)
        or len(tool_calls) != 1
    ):
        raise LiveModelRoleError(
            "Visual Actor must return exactly one tool call"
        )
    call = tool_calls[0]
    call_type = member(call, "type")
    if call_type != "function":
        raise LiveModelRoleError("Visual Actor tool call must be a function")
    function = member(call, "function")
    if function is None:
        raise LiveModelRoleError("Visual Actor tool call has no function")
    name = member(function, "name")
    if type(name) is not str or name not in allowed_names:
        raise LiveModelRoleError("Visual Actor returned an unapproved tool")
    arguments = _decode_arguments(member(function, "arguments"))
    try:
        state_update = ActorTaskStateUpdate.from_dict(
            arguments.pop("task_state_update", None)
        )
    except ActorTaskStateError:
        state_update = ActorTaskStateUpdate()
    local_intent = arguments.pop("local_intent", None)
    runtime_name = _RUNTIME_TOOL_NAMES.get(name, name)
    if runtime_name not in available_runtime_names:
        raise LiveModelRoleError("Visual Actor returned an unavailable action")
    if runtime_name == "drag":
        arguments = _canonical_drag_arguments(
            arguments,
            pointer_held=pointer_held,
        )
    elif runtime_name in {"click", "double_click", "move_mouse"}:
        arguments = _canonical_desktop_arguments(runtime_name, arguments)
    if runtime_name in APPROVED_DESKTOP_ACTIONS:
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "desktop_action",
            "action": runtime_name,
            "args": arguments,
            "local_intent": local_intent or "",
        }
    elif runtime_name == "observe":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "observe",
            "observation_kind": "full",
            "image_region": None,
            "question": arguments.get("question"),
        }
    elif runtime_name == "claim_obligation_done":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "claim_obligation_done",
            **arguments,
        }
    elif runtime_name == "blocked":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "blocked",
            **arguments,
        }
    else:  # pragma: no cover - guarded by the exact minimal tool set
        raise LiveModelRoleError("Visual Actor returned an unsupported outcome")
    return ActorTurnDecision(
        proposal=raw_proposal,
        task_state_update=state_update,
        narration=narration,
    )


def _history_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str] | None:
    """Project one valid model call into compact next-turn context."""

    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    raw_arguments = (
        member(function, "arguments") if function is not None else None
    )
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(raw_arguments) is not str
    ):
        return None
    try:
        arguments = _decode_arguments(raw_arguments)
    except LiveModelRoleError:
        return None
    arguments.pop("task_state_update", None)
    arguments.pop("local_intent", None)
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _canonical_json(arguments),
                },
            }
        ],
    }
    return assistant, call_id, _runtime_name_from_model_call(
        name, raw_arguments
    )


def _tool_format_repair(
    response: Any,
    format_error: str,
) -> tuple[dict[str, Any], str] | None:
    """Extract one failed tool call so the same model can repair it.

    The structured format error is returned to the same model before any
    physical input is executed.  Repaired proposals are parsed again and may
    receive further exact feedback while the shared model-call budget remains;
    the runtime never replays after input.
    """
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = member(function, "arguments") if function is not None else None
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(arguments) is not str
    ):
        return None
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        ],
    }
    return assistant, call_id


def _connector_tool_definitions(gateway: Any) -> list[dict[str, Any]]:
    if (
        gateway is None
        or not callable(getattr(gateway, "search", None))
        or not callable(getattr(gateway, "call", None))
    ):
        return []
    search_schema = _actor_parameters_from_schema(
        _CONNECTOR_SEARCH_TOOL,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "工具名或能力关键词。多个词各自排序；知道确切工具名就用原名。"
                    ),
                },
                "connector": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "已配置的 MCP 服务器名；留空表示全部搜索。"
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        requires_local_intent=False,
    )
    call_schema = _actor_parameters_from_schema(
        _CONNECTOR_CALL_TOOL,
        {
            "type": "object",
            "properties": {
                "connector": {"type": "string", "minLength": 1},
                "tool": {"type": "string", "minLength": 1},
                "arguments_json": {"type": "string", "minLength": 2},
            },
            "required": ["connector", "tool", "arguments_json"],
            "additionalProperties": False,
        },
        requires_local_intent=False,
    )
    return [
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_SEARCH_TOOL,
                "description": (
                    "Search tools from MCP servers configured in .mcp.json. "
                    "Use an empty query for a compact catalog. If no server "
                    "is configured and the user asked to add one, use the "
                    "generic shell/file tools to create .mcp.json, then "
                    "search again; npx -y and uvx can install stdio servers "
                    "on first launch."
                ),
                "parameters": search_schema,
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_CALL_TOOL,
                "description": (
                    "Call one MCP tool by the connector and tool names "
                    "returned by search_connector_tools."
                ),
                "parameters": call_schema,
                "strict": True,
            },
        },
    ]


def _shell_tool_definitions(runner: Any) -> list[dict[str, Any]]:
    """Expose the generic local primitives implemented by the active runner."""

    definitions = [
        {
            "type": "function",
            "function": {
                "name": _SHELL_TOOL,
                "description": (
                    "执行 PowerShell 或 cmd，做代码、文件、进程和取命令输出。"
                    "每次调用都是新的 shell 进程，目录切换不会保留；"
                    "需要某个目录就传 cwd。powershell 命令本来就在 PowerShell 里跑，"
                    "再嵌套 powershell -Command/-File 会另起进程，继承不到当前变量，"
                    "也继承不到 DPI 感知，窗口坐标会被缩放。"
                    "ok 和 exit_code 只说明子进程，不构成鼠标键盘的物理回执；"
                    "真实键鼠输入用桌面输入工具。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _SHELL_MAX_COMMAND_CHARS,
                            "description": "命令行。",
                        },
                        "shell": {
                            "type": "string",
                            "enum": ["powershell", "cmd"],
                        },
                        "timeout_s": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "cwd": {
                            "type": "string",
                            "minLength": 1,
                            "description": "这条命令的工作目录。",
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]
    if callable(getattr(runner, "run_detached", None)):
        definitions.append({
            "type": "function",
            "function": {
                "name": _DETACHED_SHELL_TOOL,
                "description": (
                    "把一段 PowerShell 脚本作为独立后台进程启动，用于 GUI 程序、"
                    "服务或其它长时间任务。立刻返回启动回执和可轮询的日志/状态路径，"
                    "不代表脚本已经跑完。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _SHELL_MAX_COMMAND_CHARS,
                            "description": "字面 PowerShell 脚本内容。",
                        },
                        "name": {
                            "type": "string",
                            "default": "task",
                            "description": "简短的进程标签。",
                        },
                    },
                    "required": ["script"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        })
    if callable(getattr(runner, "write_file", None)):
        definitions.append({
            "type": "function",
            "function": {
                "name": _WRITE_FILE_TOOL,
                "description": (
                    "把字面 UTF-8 内容写入文件，不需要 shell 转义。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "description": "绝对路径或当前目录下的路径。",
                        },
                        "content": {
                            "type": "string",
                            "description": "完整的字面文件内容。",
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        })
    return definitions


def _window_text_tool_definitions() -> list[dict[str, Any]]:
    """One read-only window-text tool that can name any window on the desktop.

    The tool used to read "whatever owns focus", which quietly made every text
    read a focus change: a value living in a second window could only be read by
    activating it first.  The model decides which window it means - a handle
    from ``list_windows`` or a title fragment - and focus is only the fallback
    when it names none.
    """

    return [
        {
            "type": "function",
            "function": {
                "name": _WINDOW_TEXT_TOOL,
                "description": (
                    "读取某个窗口在屏幕上的真实文字，不动鼠标键盘。"
                    "window 接受 list_windows 的句柄或标题片段；不传则读当前焦点窗口。"
                    "名字对不上时返回候选窗口而不是乱猜。"
                    "返回的文字就是真实标签，即使它在截图里小到看不清。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window": {
                            "type": "string",
                            "maxLength": 256,
                            "description": (
                                "Window handle from list_windows, or a title "
                                "fragment. Empty reads the focused window."
                            ),
                            "default": "",
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _copy_current_view_tool_definitions() -> list[dict[str, Any]]:
    """Expose the current submitted frame as a path-free clipboard action."""

    return [
        {
            "type": "function",
            "function": {
                "name": _COPY_CURRENT_VIEW_TOOL,
                "description": (
                    "把这一回合你正在看的画面原样放进系统剪贴板。"
                    "它只复制，不粘贴、不重新截图。"
                    "要在目标画面可见时调用，然后切到目标处按 Ctrl+V。"
                ),
                "parameters": _actor_parameters_from_schema(
                    _COPY_CURRENT_VIEW_TOOL,
                    {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    requires_local_intent=False,
                ),
                "strict": True,
            },
        },
    ]


def _activate_window_tool_definitions() -> list[dict[str, Any]]:
    """One tool that can bring a named window to the front.

    Needed because an application may own several windows with the same title
    and the one in front can be a blank full-screen placeholder, leaving every
    click and keystroke with no visible effect. A human fixes that by switching
    windows; without this tool the model cannot express "switch to that one".
    """

    return [
        {
            "type": "function",
            "function": {
                "name": _ACTIVATE_WINDOW_TOOL,
                "description": (
                    "把一个窗口提到最前：最小化的先还原，抬到同级窗口之上，并尝试聚焦。"
                    "window 是 list_windows 的句柄，或标题、进程名的一部分。"
                    "当眼前的画面属于同一应用的另一个窗口时用它，"
                    "例如一个和真实面板同名的全屏空白窗口。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window": {
                            "type": "string",
                            "maxLength": 256,
                            "description": (
                                "Window handle from list_windows, or a title or "
                                "process-name fragment."
                            ),
                        },
                    },
                    "required": ["window"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _list_windows_tool_definitions() -> list[dict[str, Any]]:
    """One generic read-only window inventory tool."""

    return [
        {
            "type": "function",
            "function": {
                "name": _LIST_WINDOWS_TOOL,
                "description": "列出可见窗口和后台应用。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _window_text_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only window-text tool call."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _WINDOW_TEXT_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor window-text tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor window-text tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    if type(arguments) is not dict:
        raise LiveModelRoleError("Actor window-text arguments must be a JSON object")
    # Only the window selector survives validation; an unknown or mistyped
    # selector reads the focused window instead of failing the turn, because a
    # read has no physical effect and a rejection here would only cost a turn.
    raw_window = arguments.get("window", "")
    if raw_window is None:
        raw_window = ""
    if type(raw_window) is int:
        raw_window = str(raw_window)
    if type(raw_window) is not str:
        raw_window = ""
    arguments = {"window": raw_window.strip()[:256]}
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor window-text content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_window_text(
    reader: Any,
    turn: ActorTurnInput,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one window's visible text through the injected reader.

    ``arguments['window']`` is the model's own selector: a handle or a title
    fragment, empty for the focused window.  The reader resolves it and reports
    candidates when the name does not identify exactly one window.
    """
    if reader is None or not callable(reader):
        return _actionable_tool_result(
            _WINDOW_TEXT_TOOL,
            {"ok": False, "error": "window-text reader is unavailable"},
        )
    request = {
        "window": str((arguments or {}).get("window") or "").strip()[:256],
        "turn": turn,
    }
    try:
        result = reader(request)
    except TypeError:
        # A reader written against the earlier one-argument contract still
        # works; the selector simply has no effect.
        try:
            result = reader(turn)
        except Exception as exc:
            return _actionable_tool_error(_WINDOW_TEXT_TOOL, exc)
    except Exception as exc:
        return _actionable_tool_error(_WINDOW_TEXT_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _WINDOW_TEXT_TOOL,
            TypeError("window-text reader returned a non-object result"),
        )
    return _actionable_tool_result(_WINDOW_TEXT_TOOL, result)


def _owned_tool_exchange(
    response: Any,
    name: str,
    *,
    expected_arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only/utility tool call (generic pattern).

    ``expected_arguments`` maps each field name to the required type
    (``str`` or ``bool``).  Fields declared optional in the tool schema (with
    a JSON ``default``) may be omitted by the model; the default is filled in
    before the type check so the tool schema and the runtime validator agree.
    The model may also attach the optional ``task_state_update`` scratchpad
    field to any call; it is popped before validation so utility tools do not
    need their own scratchpad schema.
    """
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    call_name = member(function, "name") if function is not None else None
    if call_name != name:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError(f"Actor {name} tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError(f"Actor {name} tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    arguments.pop("task_state_update", None)
    for field in set(arguments) - set(expected_arguments):
        arguments.pop(field, None)
    for field, expected_type in expected_arguments.items():
        if field not in arguments:
            # Optional schema field with a default: fill the default so the
            # model is never punished for omitting an optional argument.
            default = _OPTIONAL_TOOL_DEFAULTS.get(name, {}).get(field)
            if default is not None:
                arguments[field] = default
                continue
            raise LiveModelRoleError(
                f"{name} is missing required field {field!r}"
            )
        if type(arguments[field]) is not expected_type:
            raise LiveModelRoleError(
                f"{name}.{field} must be {expected_type.__name__}"
            )
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError(f"Actor {name} content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


# 坐标按网格归并后再算签名：模型反复点同一个地方时坐标会在几像素内抖动，
# 逐字节比较就永远识别不出"这是同一个动作"。
_SIGNATURE_POINT_GRID = 16


def _quantized_argument(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(round(float(value) / _SIGNATURE_POINT_GRID)) * _SIGNATURE_POINT_GRID
    if isinstance(value, Mapping):
        return {
            str(key): _quantized_argument(item)
            for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_quantized_argument(item) for item in value]
    return value


def _proposal_signature_value(proposal: Mapping[str, Any]) -> str:
    """同一动作 + 同一参数（坐标按网格归并）的稳定标识。"""

    return _canonical_json(
        {
            "action": str(proposal.get("action") or proposal.get("kind") or ""),
            "args": _quantized_argument(proposal.get("args") or {}),
        }
    )


def _clipboard_facts(reader: Any) -> dict[str, Any] | None:
    """Read the clipboard's formats as plain facts, or None when unavailable."""

    if reader is None:
        return None
    try:
        snapshot = reader()
    except Exception:
        return None
    if not isinstance(snapshot, Mapping) or not snapshot.get("ok"):
        return None
    formats = [
        str(name)[:64]
        for name in list(snapshot.get("formats") or [])[:12]
        if str(name).strip()
    ]
    if not formats:
        return {"empty": True}
    return {
        "formats": formats,
        "has_image": bool(snapshot.get("has_image")),
    }


def _with_clipboard_facts(
    result: dict[str, Any],
    reader: Any,
) -> dict[str, Any]:
    """Attach the post-copy clipboard state to a copy_current_view result."""

    facts = _clipboard_facts(reader)
    if facts is None or result.get("ok") is False:
        return result
    enriched = dict(result)
    enriched["clipboard"] = facts
    return enriched


def _read_state_tool_definitions() -> list[dict[str, Any]]:
    """One read-only tool that returns the facts a screen frame cannot show.

    The clipboard and the last few mechanical receipts are state the model
    needs in order to stop repeating an action that already worked.  It names
    facts only; it never judges whether the task is done.
    """

    return [
        {
            "type": "function",
            "function": {
                "name": _READ_STATE_TOOL,
                "description": (
                    "读截图看不出来的物理状态：剪贴板现在装着什么，"
                    "以及本次运行最近三次工具回执。"
                    "当你拿不准某次复制、粘贴或按键到底有没有生效时用它，"
                    "不要重复同一个动作。"
                ),
                "parameters": _actor_parameters_from_schema(
                    _READ_STATE_TOOL,
                    {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    requires_local_intent=False,
                ),
                "strict": True,
            },
        },
    ]


def _call_read_state(
    *,
    clipboard_reader: Any,
    receipts: Sequence[Mapping[str, Any]],
    windows_reader: Any,
) -> dict[str, Any]:
    """Return clipboard formats, foreground window and recent receipts."""

    result: dict[str, Any] = {"ok": True}
    facts = _clipboard_facts(clipboard_reader)
    result["clipboard"] = facts if facts is not None else {"unavailable": True}
    if windows_reader is not None:
        try:
            inventory = windows_reader()
        except Exception:
            inventory = None
        foreground = (
            _foreground_window_fact(inventory)
            if isinstance(inventory, Mapping)
            else None
        )
        if foreground is not None:
            result["foreground_window"] = foreground
    result["recent"] = [dict(item) for item in list(receipts)[-3:]]
    return _actionable_tool_result(_READ_STATE_TOOL, result)


def _call_copy_current_view(
    copy_current_view_runner: Any,
    *,
    image_b64: str,
    decision_id: str,
) -> dict[str, Any]:
    """Copy the exact frame already supplied to this model turn."""
    if copy_current_view_runner is None or not callable(copy_current_view_runner):
        return _actionable_tool_result(
            _COPY_CURRENT_VIEW_TOOL,
            {"ok": False, "error": "current-view clipboard is unavailable"},
        )
    try:
        result = copy_current_view_runner(
            image_b64=image_b64,
            decision_id=decision_id,
        )
    except Exception as exc:
        return _actionable_tool_error(_COPY_CURRENT_VIEW_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _COPY_CURRENT_VIEW_TOOL,
            TypeError("current-view clipboard returned a non-object result"),
        )
    return _actionable_tool_result(_COPY_CURRENT_VIEW_TOOL, result)


def _call_activate_window(
    activate_runner: Any,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the injected owner to raise one window and report what happened."""

    if activate_runner is None or not callable(activate_runner):
        return _actionable_tool_result(
            _ACTIVATE_WINDOW_TOOL,
            {"ok": False, "error": "window activation is unavailable"},
        )
    request = {"window": str((arguments or {}).get("window") or "").strip()[:256]}
    try:
        result = activate_runner(request)
    except Exception as exc:
        return _actionable_tool_error(_ACTIVATE_WINDOW_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _ACTIVATE_WINDOW_TOOL,
            TypeError("window activation returned a non-object result"),
        )
    return _actionable_tool_result(_ACTIVATE_WINDOW_TOOL, result)


def _call_list_windows(
    windows_reader: Any,
) -> dict[str, Any]:
    """Return the window/application inventory through the injected reader."""
    if windows_reader is None or not callable(windows_reader):
        return _actionable_tool_result(
            _LIST_WINDOWS_TOOL,
            {"ok": False, "error": "window inventory is unavailable"},
        )
    try:
        result = windows_reader()
    except Exception as exc:
        return _actionable_tool_error(_LIST_WINDOWS_TOOL, exc)
    if isinstance(result, Mapping):
        return _actionable_tool_result(_LIST_WINDOWS_TOOL, result)
    if not isinstance(result, list):
        return _actionable_tool_error(
            _LIST_WINDOWS_TOOL,
            TypeError("window inventory returned a non-list result"),
        )
    bounded: list[dict[str, Any]] = []
    for item in result[:24]:
        if not isinstance(item, Mapping):
            continue
        bounded.append({
            key: item.get(key)
            for key in (
                "hwnd",
                "title",
                "class_name",
                "pid",
                "process_name",
                "rect",
                "foreground",
                "visible",
                "minimized",
            )
            if key in item
        })
    return {"ok": True, "windows": bounded, "count": len(bounded)}


def _connector_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name not in _CONNECTOR_TOOLS:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor connector tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor connector tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    arguments.pop("task_state_update", None)
    expected = (
        {"query", "connector"}
        if name == _CONNECTOR_SEARCH_TOOL
        else {"connector", "tool", "arguments_json"}
    )
    for field, default in _OPTIONAL_TOOL_DEFAULTS.get(name, {}).items():
        arguments.setdefault(field, default)
    for field in set(arguments) - expected:
        arguments.pop(field, None)
    if not expected.issubset(arguments) or not all(
        type(arguments[key]) is str for key in expected
    ):
        raise LiveModelRoleError(
            f"{name} requires {sorted(expected)!r} text fields"
        )
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor connector content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_connector(
    gateway: Any,
    name: str,
    arguments: Mapping[str, Any],
    *,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    if gateway is None:
        return _actionable_tool_result(
            name,
            {"ok": False, "error": "connector gateway is unavailable"},
        )
    try:
        if name == _CONNECTOR_SEARCH_TOOL:
            result = gateway.search(
                query=arguments["query"],
                connector=arguments["connector"],
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        elif name == _CONNECTOR_CALL_TOOL:
            result = gateway.call(
                connector=arguments["connector"],
                tool=arguments["tool"],
                arguments_json=arguments["arguments_json"],
                timeout_s=timeout_s,
                stop_event=stop_event,
            )
        else:  # pragma: no cover - exact connector tool set above
            return _actionable_tool_result(
                name,
                {"ok": False, "error": "unsupported connector tool"},
            )
    except Exception as exc:
        return _actionable_tool_error(name, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            name,
            TypeError("connector gateway returned a non-object result"),
        )
    return _actionable_tool_result(name, result)


def _shell_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned ``run_shell`` tool call (connector pattern)."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _SHELL_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor shell tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor shell tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    command = arguments.get("command")
    if type(command) is not str or not command.strip():
        raise LiveModelRoleError("run_shell requires a non-empty command")
    if len(command) > _SHELL_MAX_COMMAND_CHARS:
        raise LiveModelRoleError("run_shell command exceeds the bounded budget")
    shell_name = arguments.get("shell")
    if shell_name not in (None, "powershell", "cmd"):
        raise LiveModelRoleError("run_shell shell must be powershell or cmd")
    timeout_s = arguments.get("timeout_s")
    if timeout_s is not None and (
        type(timeout_s) is not int or timeout_s < 1
    ):
        raise LiveModelRoleError("run_shell timeout_s must be a positive integer")
    cwd = arguments.get("cwd")
    if cwd is not None and (type(cwd) is not str or not cwd.strip()):
        raise LiveModelRoleError("run_shell cwd must be a non-empty string")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor shell content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_shell(
    runner: Any,
    arguments: Mapping[str, Any],
    *,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one shell command through the injected runner."""
    if runner is None or not callable(getattr(runner, "run", None)):
        return _actionable_tool_result(
            _SHELL_TOOL,
            {"ok": False, "error": "shell runner is unavailable"},
        )
    requested_cwd = arguments.get("cwd")
    cwd = (
        str(requested_cwd).strip()
        if type(requested_cwd) is str and requested_cwd.strip()
        else None
    )
    try:
        result = runner.run(
            command=str(arguments.get("command") or ""),
            shell=str(arguments.get("shell") or "powershell"),
            timeout_s=float(arguments.get("timeout_s") or 30),
            cwd=cwd,
            stop_event=stop_event,
        )
    except Exception as exc:
        return _actionable_tool_error(_SHELL_TOOL, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            _SHELL_TOOL,
            TypeError("shell runner returned a non-object result"),
        )
    output = dict(result)
    output["cwd"] = (
        str(Path(cwd).expanduser().resolve())
        if cwd is not None
        else str(Path.cwd())
    )
    # A child process exiting cleanly says nothing about any synthetic desktop
    # input it may have attempted.  Keep that mechanical distinction explicit
    # instead of letting exit code 0 impersonate a RawDevice receipt.
    output["completion_scope"] = "child_process_exit_only"
    output["physical_input_receipt"] = False
    stdout = output.pop("stdout", "")
    stderr = output.pop("stderr", "")
    if not isinstance(output.get("output"), str) or not output.get("output"):
        parts = [
            value
            for value in (stdout, stderr)
            if isinstance(value, str) and value
        ]
        output["output"] = "\n".join(parts) or "(no output)"
    # stdout/stderr duplicate the combined output from the native runner.
    # The runner owns the one generic output boundary and supplies a full path
    # when it truncates; do not discard another slice here.
    return _actionable_tool_result(_SHELL_TOOL, output)


def _local_utility_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Parse one generic detached-process or literal-file call."""

    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    function = member(calls[0], "function")
    name = member(function, "name") if function is not None else None
    if name == _DETACHED_SHELL_TOOL:
        return _owned_tool_exchange(
            response,
            _DETACHED_SHELL_TOOL,
            expected_arguments={"script": str, "name": str},
        )
    if name == _WRITE_FILE_TOOL:
        return _owned_tool_exchange(
            response,
            _WRITE_FILE_TOOL,
            expected_arguments={"path": str, "content": str},
        )
    return None


def _call_local_utility(
    runner: Any,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one local primitive through the same injected shell backend."""

    method_name = (
        "run_detached" if name == _DETACHED_SHELL_TOOL else "write_file"
    )
    method = getattr(runner, method_name, None) if runner is not None else None
    if not callable(method):
        return _actionable_tool_result(
            name,
            {"ok": False, "error": f"{name} runner is unavailable"},
        )
    try:
        if name == _DETACHED_SHELL_TOOL:
            result = method(
                script=str(arguments.get("script") or ""),
                name=str(arguments.get("name") or "task"),
            )
        elif name == _WRITE_FILE_TOOL:
            result = method(
                path=str(arguments.get("path") or ""),
                content=str(arguments.get("content") or ""),
            )
        else:  # pragma: no cover - exact local utility set above
            raise LiveModelRoleError(f"unsupported local utility: {name}")
    except Exception as exc:
        return _actionable_tool_error(name, exc)
    if not isinstance(result, Mapping):
        return _actionable_tool_error(
            name,
            TypeError(f"{name} runner returned a non-object result"),
        )
    return _actionable_tool_result(name, result)


def _initial_calls_from_response(
    response: Any,
) -> list[dict[str, Any]]:
    """Return the raw tool-call list of the initial text turn."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if calls is None:
        return []
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise LiveModelRoleError("Actor initial tool calls must be a sequence")
    return list(calls)


def _visual_batch_call(
    call: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Reuse the existing single-tool parsers for one non-GUI batch item."""

    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [deepcopy(call)],
            },
        }],
    }
    exchange = None
    if name == _SHELL_TOOL:
        exchange = _shell_exchange(response)
    elif name in _LOCAL_UTILITY_TOOLS:
        exchange = _local_utility_exchange(response)
    elif name == _WINDOW_TEXT_TOOL:
        exchange = _window_text_exchange(response)
    elif name == _COPY_CURRENT_VIEW_TOOL:
        exchange = _owned_tool_exchange(
            response,
            _COPY_CURRENT_VIEW_TOOL,
            expected_arguments={},
        )
    elif name == _LIST_WINDOWS_TOOL:
        exchange = _owned_tool_exchange(
            response,
            _LIST_WINDOWS_TOOL,
            expected_arguments={},
        )
    elif name in _CONNECTOR_TOOLS:
        exchange = _connector_exchange(response)
    if exchange is None:
        raise LiveModelRoleError(
            f"tool batch rejected: {name!r} is not an independent non-GUI "
            "query; send a GUI/outcome tool in a turn of its own"
        )
    _assistant, call_id, parsed_name, arguments = exchange
    return call_id, parsed_name, arguments


def _execute_visual_batch_call(
    name: str,
    arguments: Mapping[str, Any],
    *,
    shell_runner: Any,
    connector_gateway: Any,
    window_text_reader: Any,
    windows_reader: Any,
    copy_current_view_runner: Any,
    image_b64: str,
    decision_id: str,
    turn: ActorTurnInput,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one model-selected non-GUI batch item."""
    if name == _SHELL_TOOL:
        return _call_shell(
            shell_runner,
            arguments,
            stop_event=stop_event,
        )
    if name in _LOCAL_UTILITY_TOOLS:
        return _call_local_utility(shell_runner, name, arguments)
    if name == _WINDOW_TEXT_TOOL:
        return _call_window_text(window_text_reader, turn, arguments)
    if name == _COPY_CURRENT_VIEW_TOOL:
        return _call_copy_current_view(
            copy_current_view_runner,
            image_b64=image_b64,
            decision_id=decision_id,
        )
    if name == _LIST_WINDOWS_TOOL:
        return _call_list_windows(windows_reader)
    return _call_connector(
        connector_gateway,
        name,
        arguments,
        timeout_s=timeout_s,
        stop_event=stop_event,
    )


class FlowFragmentProposer:
    """One inert editor proposal with no desktop or conversation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._called = False
        self._state_lock = threading.Lock()

    def propose_flow_fragment(
        self,
        request: Mapping[str, Any],
        *,
        catalog_ports: Mapping[str, Sequence[str]],
        catalog_params: Mapping[str, frozenset[str]],
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise LiveModelRoleError("flow fragment request must be an object")
        canonical_request = _canonical_json(request)
        if len(canonical_request) > _FLOW_FRAGMENT_REQUEST_CHARS:
            raise LiveModelRoleError("flow fragment request exceeds the bounded context")
        ports = {
            key: tuple(value)
            for key, value in catalog_ports.items()
            if type(key) is str and key
        }
        if len(ports) != len(catalog_ports) or any(
            not value or any(type(port) is not str or not port for port in value)
            for value in ports.values()
        ):
            raise LiveModelRoleError("flow fragment output-port catalog is invalid")
        params = {
            key: frozenset(value)
            for key, value in catalog_params.items()
            if type(key) is str and key and isinstance(value, frozenset)
        }
        if set(params) != set(ports) or any(
            any(type(name) is not str or not name for name in names)
            for names in params.values()
        ):
            raise LiveModelRoleError("flow fragment param catalog is invalid")
        with self._state_lock:
            if self._called:
                raise LiveModelRoleError("flow fragment proposer is call-once")
            self._called = True
        outcome = invoke_chat_completion(
            self._client,
            decision_id=_initial_decision_id(canonical_request, ()),
            model_role=VISUAL_ACTOR_MODEL_ROLE,
            purpose="flow_fragment_proposal",
            provider_request={
                "model": self._model,
                "messages": [{"role": "user", "content": canonical_request}],
                "tools": [_flow_fragment_tool_definition(tuple(ports))],
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_completion_tokens": self._max_output_tokens,
            },
            policy=ModelRetryPolicy(
                max_attempts=self._max_attempts,
                total_timeout_s=self._total_timeout_s,
            ),
            stop_event=stop_event,
            on_attempt=self._on_attempt,
            runtime=self._runtime,
        )
        if self._on_response is not None:
            try:
                self._on_response(outcome.response)
            except Exception:
                pass
        return _flow_fragment_from_response(outcome.response, ports, params)


class LiveVisualActor:
    """One model choosing from a bounded native exchange and generic tools."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 4096,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        on_activity: Callable[[Mapping[str, Any]], None] | None = None,
        connector_gateway: Any | None = None,
        shell_runner: Any | None = None,
        window_text_reader: Any | None = None,
        copy_current_view_runner: Any | None = None,
        clipboard_reader: Any | None = None,
        windows_reader: Any | None = None,
        activate_window_runner: Any | None = None,
        history: Sequence[Mapping[str, Any]] = (),
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        if on_activity is not None and not callable(on_activity):
            raise LiveModelRoleError("on_activity must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._on_activity = on_activity
        self._connector_gateway = (
            connector_gateway
            if (
                callable(getattr(connector_gateway, "search", None))
                and callable(getattr(connector_gateway, "call", None))
            )
            else None
        )
        self._shell_runner = (
            shell_runner
            if callable(getattr(shell_runner, "run", None))
            else None
        )
        self._window_text_reader = (
            window_text_reader if callable(window_text_reader) else None
        )
        self._copy_current_view_runner = (
            copy_current_view_runner
            if callable(copy_current_view_runner)
            else None
        )
        self._clipboard_reader = (
            clipboard_reader if callable(clipboard_reader) else None
        )
        self._recent_receipts: list[dict[str, Any]] = []
        self._last_proposal_signature = ""
        self._last_frame_sha = ""
        self._repeat_count = 1
        self._no_progress_streak = 0
        self._windows_reader = (
            windows_reader if callable(windows_reader) else None
        )
        self._activate_window_runner = (
            activate_window_runner
            if callable(activate_window_runner)
            else None
        )
        if isinstance(history, (str, bytes)) or not isinstance(
            history, Sequence
        ):
            raise LiveModelRoleError("Actor thread history must be a sequence")
        self._thread_history = tuple(deepcopy(history))
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._previous_visual_exchange: _VisualExchange | None = None
        # Keep the completed action/result chain for task continuity. Old
        # screenshots, narration, and private reasoning are stripped above;
        # the current JPEG remains the only visual truth.
        self._completed_visual_history: list[dict[str, Any]] = []
        self._pending_task_state: ActorTaskStateUpdate | None = None
        self._current_task_state = ActorTaskState.initial()
        self._latest_progress_note = ""
        self._state_lock = threading.Lock()

    @property
    def task_progress(self) -> dict[str, Any]:
        return self._current_task_state.to_dict()

    @property
    def latest_narration(self) -> str:
        return self._latest_progress_note

    def _window_text_after_action(self) -> dict[str, Any] | None:
        """The focused window's own text right after one completed action.

        A 1120px-wide frame can make a calculator display, a text field or an
        editor line unreadable even when the application publishes that text
        through accessibility.  Returning it with the action result is a fact
        about what the screen now contains, not a judgment about the goal, and
        it costs no extra model turn.  Any failure - no reader, an unreadable
        window, an empty result - simply omits the fact; physical input has
        already happened and must never be turned into an error here.
        """

        reader = self._window_text_reader
        if reader is None:
            return None
        try:
            raw = reader({"window": ""})
        except Exception:
            return None
        if not isinstance(raw, Mapping):
            return None
        if raw.get("ok") is False or raw.get("error"):
            return None
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        text = text[:_POST_ACTION_WINDOW_TEXT_CHARS]
        # Accessibility text can contain a lone surrogate from a broken glyph.
        # Serializing that would raise inside the turn, so replace it here where
        # the cost is one lost character instead of a failed action result.
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            text = text.encode("utf-8", "replace").decode("utf-8")
        fact: dict[str, Any] = {
            "text": text,
            "truncated": len(str(raw.get("text") or "")) > _POST_ACTION_WINDOW_TEXT_CHARS,
        }
        title = str(raw.get("window_title") or "").strip()
        if title:
            fact["window_title"] = title[:200]
        hwnd = raw.get("window_hwnd")
        if type(hwnd) is int and hwnd > 0:
            fact["window_hwnd"] = hwnd
        return fact

    @property
    def standalone_model_call_allowance(self) -> int:
        """Finite fallback for direct role users outside the live Runtime."""

        local_allowance = (
            _STANDALONE_CONNECTOR_CALL_ALLOWANCE
            if self._connector_gateway is not None
            else 0
        ) + (
            _STANDALONE_SHELL_CALL_ALLOWANCE
            if self._shell_runner is not None
            else 0
        ) + (
            _STANDALONE_WINDOW_TEXT_ALLOWANCE
            if self._window_text_reader is not None
            else 0
        ) + (
            _STANDALONE_COPY_CURRENT_VIEW_ALLOWANCE
            if self._copy_current_view_runner is not None
            else 0
        ) + (
            _STANDALONE_LIST_WINDOWS_ALLOWANCE
            if self._windows_reader is not None
            else 0
        ) + (
            _STANDALONE_ACTIVATE_WINDOW_ALLOWANCE
            if self._activate_window_runner is not None
            else 0
        )
        return 3 + local_allowance

    def _record_response(self, response: Any) -> None:
        if self._on_response is not None:
            try:
                self._on_response(response)
            except Exception:
                # Usage telemetry is a sidecar and never changes task semantics.
                pass
        try:
            narration = narration_content(response_message(response))
        except Exception:
            return
        if narration:
            self._latest_progress_note = narration[:4000]
            self._emit_activity(kind="message", message=narration)

    def _emit_activity(self, **activity: Any) -> None:
        if self._on_activity is None:
            return
        try:
            self._on_activity(activity)
        except Exception:
            # User-facing progress is a projection, never an execution owner.
            pass

    def _record_tool_start(self, call: Any) -> None:
        function = member(call, "function")
        name = member(function, "name") if function is not None else None
        if type(name) is not str or not name:
            return
        raw_arguments = member(function, "arguments")
        try:
            arguments = _decode_arguments(raw_arguments)
        except LiveModelRoleError:
            arguments = {}
        state_update = arguments.pop("task_state_update", None)
        try:
            patch = ActorTaskStateUpdate.from_dict(state_update)
        except ActorTaskStateError:
            patch = ActorTaskStateUpdate()
        if patch.changes_state:
            self._pending_task_state = (
                patch
                if self._pending_task_state is None
                else self._pending_task_state.merged_with(patch)
            )
            try:
                self._current_task_state = self._current_task_state.apply(patch)
            except ActorTaskStateError:
                pass
            visible_state = (
                patch.scratchpad
                if patch.scratchpad is not None
                else _canonical_json(
                    {"todos": [item.to_dict() for item in patch.todos or ()]}
                )
            )
            self._emit_activity(
                kind="task_state",
                message=visible_state[:8192],
                task_state=visible_state[:8192],
                task_state_scratchpad=self._current_task_state.scratchpad[:8192],
                task_state_todos=[
                    item.to_dict()
                    for item in self._current_task_state.todos
                ],
            )
        local_intent = arguments.pop("local_intent", None)
        runtime_name = _runtime_name_from_model_call(name, raw_arguments)
        command = arguments.get("command") if name == _SHELL_TOOL else None
        if type(command) is str and command.strip():
            message = command.strip()
        elif type(local_intent) is str and local_intent.strip():
            message = local_intent.strip()
        elif arguments:
            message = _canonical_json(arguments)
        else:
            message = runtime_name
        activity: dict[str, Any] = {
            "kind": "tool_start",
            "tool": runtime_name,
            "message": message[:4096],
            # This is the replay payload for non-pointer utilities. Pointer
            # actions keep using the canonical physical-attempt event.
            "arguments": arguments,
        }
        if type(command) is str and command.strip():
            activity["command"] = command.strip()[:16_384]
        self._emit_activity(**activity)

    def _record_tool_result(
        self,
        name: str,
        result: Mapping[str, Any],
    ) -> None:
        failed = bool(
            result.get("ok") is False
            or result.get("error")
            or result.get("timeout") is True
            or result.get("stopped") is True
        )
        error = result.get("error")
        output = (
            result.get("output")
            or result.get("stdout")
            or result.get("stderr")
        )
        details: list[str] = []
        for candidate in (error, output):
            if type(candidate) is not str or not candidate.strip():
                continue
            candidate = candidate.strip()
            if candidate not in details:
                details.append(candidate)
        detail = "\n".join(details)
        if not detail:
            exit_code = result.get("exit_code")
            detail = (
                f"exit code {exit_code}"
                if exit_code is not None
                else ("failed" if failed else "completed")
            )
        next_step = result.get("next_step")
        if failed and type(next_step) is str and next_step.strip():
            detail = f"{detail}\nNext: {next_step.strip()}"
        activity: dict[str, Any] = {
            "kind": "tool_result",
            "tool": _RUNTIME_TOOL_NAMES.get(name, name),
            "message": detail.strip()[:4096],
            "ok": not failed,
        }
        self._recent_receipts.append(
            {
                "tool": _RUNTIME_TOOL_NAMES.get(name, name),
                "ok": not failed,
                "detail": detail.strip()[:240],
            }
        )
        del self._recent_receipts[:-3]
        replay_frame = result.get("_replay_frame")
        if name == _COPY_CURRENT_VIEW_TOOL and type(replay_frame) is str:
            activity["arguments"] = {"replay_frame": replay_frame}
        self._emit_activity(
            **activity,
        )

    def _model_call_allowance(self, model_call_limit: int | None) -> int:
        # The production Runtime always supplies the task's remaining model
        # budget. Direct role users retain a finite fallback for malformed or
        # endlessly repeated provider output.
        if model_call_limit is None:
            model_call_limit = self.standalone_model_call_allowance
        if type(model_call_limit) is not int or model_call_limit < 1:
            raise LiveModelRoleError("Actor model-call allowance is invalid")
        return model_call_limit

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
        _model_call_limit: int | None = None,
        rejection_feedback: str | None = None,
    ) -> ActorTurnDecision:
        if type(turn) is not ActorTurnInput:
            raise LiveModelRoleError(
                "Visual Actor requires canonical ActorTurnInput"
            )
        self._current_task_state = turn.task_state
        if not (
            turn.frame.image_available
            and turn.frame.authoritative
            and turn.frame.fresh
        ):
            raise LiveModelRoleError(
                "Visual Actor requires the current authoritative fresh frame"
            )
        image_b64, submitted_size = require_jpeg(
            image_b64, image_media_type, submitted_size
        )
        terminal_tools = _tool_definitions(turn)
        shell_tools = (
            _shell_tool_definitions(self._shell_runner)
            if self._shell_runner is not None
            else []
        )
        window_text_tools = (
            _window_text_tool_definitions()
            if self._window_text_reader is not None
            else []
        )
        copy_current_view_tools = (
            _copy_current_view_tool_definitions()
            if self._copy_current_view_runner is not None
            else []
        )
        windows_tools = (
            _list_windows_tool_definitions()
            if self._windows_reader is not None
            else []
        )
        activate_tools = (
            _activate_window_tool_definitions()
            if self._activate_window_runner is not None
            else []
        )
        read_state_tools = (
            _read_state_tool_definitions()
            if (self._clipboard_reader is not None or self._windows_reader is not None)
            else []
        )
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        turn_context = turn.to_dict()
        turn_context.pop("tools")
        # Give the coordinate extent the same explicit name used by the
        # runtime envelope.  A generic ``submitted_size`` was easy to confuse
        # with a window or file size in an otherwise dense JSON object.
        context = {
            "submitted_image_size": list(submitted_size),
            **turn_context,
        }
        # A whole-screen frame cannot show applications collapsed into the
        # notification area.  Add the backend's compact, current inventory as
        # an environment fact; failure to read this optional hint never blocks
        # the visual turn.  Reuse the same snapshot if the model also asks for
        # list_windows during this turn.
        turn_windows_reader = self._windows_reader
        if self._windows_reader is not None:
            try:
                window_inventory = self._windows_reader()
            except Exception:
                window_inventory = None
            if window_inventory is not None:
                turn_windows_reader = (
                    lambda inventory=window_inventory: inventory
                )
            if isinstance(window_inventory, Mapping):
                compact_background: list[dict[str, str]] = []
                items = window_inventory.get("background_applications")
                if isinstance(items, Sequence) and not isinstance(
                    items, (str, bytes)
                ):
                    for item in items[:24]:
                        if not isinstance(item, Mapping):
                            continue
                        projected = {
                            key: str(item.get(key) or "").strip()
                            for key in ("process_name", "title", "state")
                        }
                        projected = {
                            key: value
                            for key, value in projected.items()
                            if value
                        }
                        if projected:
                            compact_background.append(projected)
                if compact_background:
                    context["background_applications"] = compact_background
                # Which window the desktop is actually showing right now. A
                # full-desktop frame is an image with no caption, so a click that
                # brings another app forward silently changes what every later
                # frame is about while the picture keeps looking "plausible".
                # Measured: a QQ task whose click summoned the music player kept
                # clicking the old coordinates for ~40 turns, still narrating
                # "the QQ chat is open". The foreground identity is a fact the
                # runtime already has; naming it costs one short object.
                foreground = _foreground_window_fact(window_inventory)
                if foreground is not None:
                    context["foreground_window"] = foreground
        clipboard_facts = _clipboard_facts(self._clipboard_reader)
        if clipboard_facts is not None:
            context["clipboard"] = clipboard_facts
        # 这一步的动作有没有让画面变化：用上一帧到这一帧的改动比例判断，
        # 只累计一个连续计数，供下面把"连续没变化"作为事实说出来。
        ratio = getattr(turn.frame, "change_ratio_from_previous", None)
        if isinstance(ratio, (int, float)) and float(ratio) < _NO_PROGRESS_RATIO:
            self._no_progress_streak += 1
        else:
            self._no_progress_streak = 0
        # 画面真的动了的时候不多花一次 UIA 读取；只有上一步没推动画面，
        # 才把前台窗口里真实存在的控件文字摆到模型眼前。
        if self._no_progress_streak and self._window_text_reader is not None:
            foreground_labels = _foreground_label_outline(
                self._window_text_reader, turn
            )
            if foreground_labels:
                context["foreground_labels"] = foreground_labels
        previous_action = context.get("previous_action")
        if (
            isinstance(previous_action, dict)
            and previous_action.get("input_completed") is False
            and previous_action.get("error_kind")
        ):
            previous_action["next_step"] = (
                "The physical action did not run. Use the current image and "
                "error_kind to change arguments or choose another tool."
            )
        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            "data:image/jpeg;base64,"
                            f"{image_b64}"
                        )
                    },
                },
                {
                    "type": "text",
                    "text": _canonical_json(context),
                },
            ],
        }
        native_context: list[dict[str, Any]] = list(
            deepcopy(
                _thread_messages(
                    self._thread_history,
                    turn.mission.original_goal,
                )
            )
        )
        previous_exchange = self._previous_visual_exchange
        completed_result: dict[str, Any] | None = None
        # Rejection feedback means the pending proposal never completed and
        # must not be attached to any earlier same-named physical action.
        if not rejection_feedback and previous_exchange is not None:
            if previous_exchange.runtime_tool_name == "observe":
                # Reaching this fresh authoritative frame completes the
                # preceding observation call without a physical transition.
                completed_result = {"desktop_observation": "available"}
            elif (
                turn.previous_action is not None
                and turn.previous_action.action
                == previous_exchange.runtime_tool_name
            ):
                completed_result = {
                    "previous_action": turn.previous_action.to_dict()
                }
                # The frame is 1120px wide, so a calculator display, a form
                # field or an editor line can be too small to read reliably,
                # while the same application publishes that text through
                # accessibility. Attach the focused window's own text to the
                # action's result so the model sees what its action produced
                # without spending a whole turn asking for it. It is a fact,
                # never a verdict, and an unreadable window omits it.
                window_text = self._window_text_after_action()
                if window_text is not None:
                    completed_result["window_text"] = window_text
        if previous_exchange is not None and completed_result is not None:
            previous_exchange.result_content = completed_result
            self._completed_visual_history.extend(
                deepcopy(previous_exchange.messages)
            )
            self._completed_visual_history.append(
                {
                    "role": "tool",
                    "tool_call_id": previous_exchange.tool_call_id,
                    "content": _canonical_json(
                        previous_exchange.result_content
                    ),
                }
            )
        self._previous_visual_exchange = None
        native_context.extend(deepcopy(self._completed_visual_history))
        if rejection_feedback:
            native_context.append(
                {
                    "role": "user",
                    "content": (
                        "Runtime feedback from the previous attempt: "
                        f"{rejection_feedback}. Use the current screen and "
                        "return one valid tool call or an independent tool batch."
                    ),
                }
            )
        current_exchange = [user_message]
        provider_request = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": VISUAL_ACTOR_SYSTEM_CONTRACT,
                },
                *native_context,
                *current_exchange,
            ],
            "tools": [
                *terminal_tools,
                *shell_tools,
                *window_text_tools,
                *copy_current_view_tools,
                *read_state_tools,
                *windows_tools,
                *activate_tools,
                *connector_tools,
            ],
            # MiMo's documented Chat Completions surface supports only
            # ``auto``. One GUI/outcome call ends the turn; independent local
            # queries may share one model-selected batch.
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = None
        decision = None
        model_call_allowance = self._model_call_allowance(_model_call_limit)
        seen_tool_results: dict[tuple[str, str], tuple[str, int]] = {}
        for model_call_index in range(model_call_allowance):
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_decision_id(turn, image_b64),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="propose_action_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=self._max_attempts,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            model_calls_remaining = model_call_allowance - model_call_index - 1
            if _finish_reason(outcome.response) == "length":
                truncation = _truncation_messages(outcome.response)
                current_exchange.extend(truncation)
                provider_request["messages"].extend(truncation)
                continue
            provider_rejection = _provider_rejection_detail(outcome.response)
            if provider_rejection:
                raise InvalidActorProposal(provider_rejection)
            try:
                visual_calls = _initial_calls_from_response(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            envelopes: list[tuple[str, str, dict[str, Any]]] = []
            if len(visual_calls) > 1:
                try:
                    envelopes = [
                        _visual_batch_call(call) for call in visual_calls
                    ]
                except LiveModelRoleError as exc:
                    # Do not fail the whole run on one malformed batch. Report
                    # the offending calls back to the same model as in-band
                    # tool errors so it can resend the GUI/outcome call alone,
                    # matching how ordinary tool failures are handled.
                    assistant = {
                        "role": "assistant",
                        "content": (
                            member(response_message(outcome.response), "content")
                            or ""
                        ),
                        "tool_calls": [
                            {
                                "id": member(call, "id"),
                                "type": "function",
                                "function": {
                                    "name": member(
                                        member(call, "function"), "name"
                                    ),
                                    "arguments": member(
                                        member(call, "function"), "arguments"
                                    ),
                                },
                            }
                            for call in visual_calls
                        ],
                    }
                    rejection_messages: list[dict[str, Any]] = [assistant]
                    for call in visual_calls:
                        call_id = str(member(call, "id") or "")
                        call_name = str(
                            member(member(call, "function"), "name") or ""
                        )
                        rejection = {
                            "ok": False,
                            "error": str(exc),
                            "next_step": (
                                "send a GUI/outcome tool in a turn of its own; "
                                "only independent non-GUI queries may share one "
                                "batch"
                            ),
                        }
                        self._record_tool_result(call_name, rejection)
                        rejection_messages.append(
                            _projected_tool_message(
                                seen_tool_results,
                                name=call_name,
                                call_id=call_id,
                                result=rejection,
                                model_calls_remaining=model_calls_remaining,
                            )
                        )
                    current_exchange.extend(rejection_messages)
                    provider_request["messages"].extend(rejection_messages)
                    continue
            if envelopes:
                message = response_message(outcome.response)
                content = member(message, "content")
                if content is not None and type(content) is not str:
                    raise InvalidActorProposal(
                        "Actor visual content must be text or null"
                    )
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [
                        {
                            "id": member(call, "id"),
                            "type": "function",
                            "function": {
                                "name": member(member(call, "function"), "name"),
                                "arguments": member(
                                    member(call, "function"), "arguments"
                                ),
                            },
                        }
                        for call in visual_calls
                    ],
                }
                reasoning = member(message, "reasoning_content")
                if type(reasoning) is str:
                    assistant["reasoning_content"] = reasoning
                for call in visual_calls:
                    self._record_tool_start(call)

                def execute_visual(
                    envelope: tuple[str, str, dict[str, Any]],
                ) -> dict[str, Any]:
                    _call_id, name, arguments = envelope
                    result = _execute_visual_batch_call(
                        name,
                        arguments,
                        shell_runner=self._shell_runner,
                        connector_gateway=self._connector_gateway,
                        window_text_reader=self._window_text_reader,
                        windows_reader=turn_windows_reader,
                        copy_current_view_runner=self._copy_current_view_runner,
                        image_b64=image_b64,
                        decision_id=_decision_id(turn, image_b64),
                        turn=turn,
                        timeout_s=min(30.0, self._total_timeout_s),
                        stop_event=stop_event,
                    )
                    if name == _COPY_CURRENT_VIEW_TOOL:
                        return _with_clipboard_facts(
                            result, self._clipboard_reader
                        )
                    return result

                with ThreadPoolExecutor(
                    thread_name_prefix="visual-actor-tool",
                ) as executor:
                    results = list(executor.map(execute_visual, envelopes))
                batch_messages: list[dict[str, Any]] = [assistant]
                for call, envelope, result in zip(
                    visual_calls, envelopes, results, strict=True
                ):
                    call_id, name, _arguments = envelope
                    self._record_tool_result(name, result)
                    batch_messages.append(
                        _projected_tool_message(
                            seen_tool_results,
                            name=name,
                            call_id=call_id,
                            result=_model_visible_tool_result(result),
                            model_calls_remaining=model_calls_remaining,
                        )
                    )
                current_exchange.extend(batch_messages)
                provider_request["messages"].extend(batch_messages)
                continue
            shell = None
            if self._shell_runner is not None:
                try:
                    shell = _shell_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if shell is not None:
                assistant, call_id, name, arguments = shell
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_shell(
                    self._shell_runner,
                    arguments,
                    stop_event=stop_event,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            local_utility = None
            if self._shell_runner is not None:
                try:
                    local_utility = _local_utility_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if local_utility is not None:
                assistant, call_id, name, arguments = local_utility
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_local_utility(
                    self._shell_runner,
                    name,
                    arguments,
                )
                self._record_tool_result(name, result)
                utility_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(utility_messages)
                provider_request["messages"].extend(utility_messages)
                continue
            window_text = None
            if self._window_text_reader is not None:
                try:
                    window_text = _window_text_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if window_text is not None:
                assistant, call_id, name, arguments = window_text
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_window_text(
                    self._window_text_reader,
                    turn,
                    arguments,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            copy_current_view = None
            if self._copy_current_view_runner is not None:
                try:
                    copy_current_view = _owned_tool_exchange(
                        outcome.response,
                        _COPY_CURRENT_VIEW_TOOL,
                        expected_arguments={},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if copy_current_view is not None:
                assistant, call_id, name, _arguments = copy_current_view
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _with_clipboard_facts(
                    _call_copy_current_view(
                        self._copy_current_view_runner,
                        image_b64=image_b64,
                        decision_id=_decision_id(turn, image_b64),
                    ),
                    self._clipboard_reader,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=_model_visible_tool_result(result),
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            windows = None
            if self._windows_reader is not None:
                try:
                    windows = _owned_tool_exchange(
                        outcome.response,
                        _LIST_WINDOWS_TOOL,
                        expected_arguments={},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if windows is not None:
                assistant, call_id, name, arguments = windows
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_list_windows(turn_windows_reader)
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            activate = None
            if self._activate_window_runner is not None:
                try:
                    activate = _owned_tool_exchange(
                        outcome.response,
                        _ACTIVATE_WINDOW_TOOL,
                        expected_arguments={"window": str},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if activate is not None:
                assistant, call_id, name, arguments = activate
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_activate_window(
                    self._activate_window_runner,
                    arguments,
                )
                self._record_tool_result(name, result)
                connector_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            read_state = None
            if read_state_tools:
                try:
                    read_state = _owned_tool_exchange(
                        outcome.response,
                        _READ_STATE_TOOL,
                        expected_arguments={},
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if read_state is not None:
                assistant, call_id, name, _arguments = read_state
                self._record_tool_start(
                    _initial_calls_from_response(outcome.response)[0]
                )
                result = _call_read_state(
                    clipboard_reader=self._clipboard_reader,
                    receipts=self._recent_receipts,
                    windows_reader=turn_windows_reader,
                )
                self._record_tool_result(name, result)
                state_messages = [
                    assistant,
                    _projected_tool_message(
                        seen_tool_results,
                        name=name,
                        call_id=call_id,
                        result=result,
                        model_calls_remaining=model_calls_remaining,
                    ),
                ]
                current_exchange.extend(state_messages)
                provider_request["messages"].extend(state_messages)
                continue
            try:
                connector = _connector_exchange(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            if connector is None:
                try:
                    decision = _proposal_from_response(
                        outcome.response,
                        frozenset(
                            {
                                *(
                                    _tool_function_name(tool)
                                    for tool in terminal_tools
                                ),
                                *(
                                    _MODEL_TOOL_NAMES.get(tool.name, tool.name)
                                    for tool in turn.tools
                                ),
                            }
                        ),
                        frozenset(tool.name for tool in turn.tools),
                        pointer_held=turn.pointer_held,
                    )
                    signature = _proposal_signature_value(decision.proposal)
                    self._last_proposal_signature = signature
                    if self._no_progress_streak >= _NO_PROGRESS_LIMIT:
                        # 连续 4 帧画面没有任何实质变化：这一步不论换成什么
                        # 动作，都还没证明能推进任务。runtime 不再执行，只把
                        # 这个事实说清楚；怎么换招仍然是模型自己的事。
                        stale_message = [
                            {
                                "role": "user",
                                "content": (
                                    "runtime 没有执行这次调用：最近连续 "
                                    f"{self._no_progress_streak} 次动作之后，"
                                    "画面没有任何实质变化（改动低于 0.25%）。"
                                    "这条路目前是死的，换一个不同的动作、"
                                    "工具或目标。"
                                ),
                            }
                        ]
                        current_exchange.extend(stale_message)
                        provider_request["messages"].extend(stale_message)
                        self._no_progress_streak = 0
                        continue
                    self._record_tool_start(
                        _initial_calls_from_response(outcome.response)[0]
                    )
                    if self._pending_task_state is not None:
                        decision = ActorTurnDecision(
                            proposal=decision.proposal,
                            task_state_update=self._pending_task_state,
                            narration=decision.narration,
                        )
                    self._pending_task_state = None
                    break
                except InvalidActorProposal as exc:
                    if model_calls_remaining <= 0:
                        raise
                    repair = _tool_format_repair(
                        outcome.response,
                        str(exc),
                    )
                    if repair is None:
                        # No single malformed call to attach feedback to (for
                        # example the model answered with prose or an empty
                        # turn).  Nudge the same model once per remaining model
                        # call instead of failing the whole run.
                        nudge = [
                            {
                                "role": "user",
                                "content": (
                                    "Your last reply contained no usable tool "
                                    f"call ({str(exc)[:300]}). Reply now with "
                                    "exactly one tool call: a GUI action, an "
                                    "independent non-GUI query, reply_to_user "
                                    "or blocked."
                                ),
                            }
                        ]
                        current_exchange.extend(nudge)
                        provider_request["messages"].extend(nudge)
                        continue
                    assistant, call_id = repair
                    repair_messages = [
                        assistant,
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _canonical_json(
                                {
                                    "tool_format_error": str(exc),
                                    "model_calls_remaining": (
                                        model_calls_remaining
                                    ),
                                    "action": (
                                        "return exactly one valid tool call "
                                        "with the required fields"
                                    ),
                                }
                            ),
                        },
                    ]
                    current_exchange.extend(repair_messages)
                    provider_request["messages"].extend(repair_messages)
                    continue
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            assistant, call_id, name, arguments = connector
            self._record_tool_start(
                _initial_calls_from_response(outcome.response)[0]
            )
            result = _call_connector(
                self._connector_gateway,
                name,
                arguments,
                timeout_s=min(30.0, self._total_timeout_s),
                stop_event=stop_event,
            )
            self._record_tool_result(name, result)
            connector_messages = [
                assistant,
                _projected_tool_message(
                    seen_tool_results,
                    name=name,
                    call_id=call_id,
                    result=result,
                    model_calls_remaining=model_calls_remaining,
                ),
            ]
            current_exchange.extend(connector_messages)
            provider_request["messages"].extend(connector_messages)
        if outcome is None or decision is None:
            raise LiveModelRoleError(
                "Actor visual turn exhausted its model-call allowance"
            )
        exchange = _history_exchange(outcome.response)
        if exchange is None:
            self._previous_visual_exchange = None
        else:
            self._previous_visual_exchange = _VisualExchange(
                messages=_text_only_tool_transcript(
                    [*current_exchange, exchange[0]]
                ),
                tool_call_id=exchange[1],
                runtime_tool_name=exchange[2],
            )
        return decision


__all__ = [
    "VISUAL_ACTOR_MODEL_ROLE",
    "VISUAL_ACTOR_SYSTEM_CONTRACT",
    "LiveVisualActor",
]
