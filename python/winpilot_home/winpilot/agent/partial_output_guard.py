"""Generic protection for verified partial output on editable GUI surfaces."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..fast.action_contract import batch_action_args, batch_action_name


_DETERMINISTIC_OUTPUT_ACTIONS = {
    "type_text",
    "type_into_element",
    "clipboard_paste",
    "paste_image_file",
    "draw_strokes",
}
_POTENTIAL_OUTPUT_ACTIONS = _DETERMINISTIC_OUTPUT_ACTIONS | {"drag"}
_SELECTION_REPLACEMENT_ACTIONS = {
    "type_text",
    "type_into_element",
    "clipboard_paste",
    "paste_image_file",
}
_CONTENT_MUTATION_FLAGS = {
    "mutation_applied",
    "input_applied",
    "format_applied",
    "clipboard_replace_applied",
    "content_changed",
    "text_inserted",
    "image_inserted",
}
_RESET_LABELS = {
    "new document",
    "new file",
    "blank document",
    "blank file",
    "clear all",
    "erase all",
    "delete all",
    "reset",
    "start over",
    "新建文档",
    "新建文件",
    "空白文档",
    "清空全部",
    "全部清除",
    "清除全部",
    "全部删除",
    "删除全部",
    "重置",
    "重新开始",
}
_AMBIGUOUS_RESET_LABELS = {"clear", "new", "清空", "新建"}
_WHOLE_SURFACE_CONTEXT_KEYS = {
    "context",
    "description",
    "purpose",
    "scope",
    "semantic_context",
    "subgoal",
    "surface",
    "target_context",
    "_parent_context",
}
_WHOLE_SURFACE_CONTEXT_RE = re.compile(
    r"\b(?:whole|entire|all)\s+(?:canvas|drawing|document|file|page|workspace|"
    r"editor|content|output|surface)\b"
    r"|\b(?:canvas|drawing|document|file|page|workspace|editor|work\s+surface)\b"
    r"|(?:整个|全部)?(?:画布|图画|绘图|文档|文件|页面|编辑区|工作区|内容|输出)",
    re.IGNORECASE,
)
_LOCAL_CONTROL_CONTEXT_RE = re.compile(
    r"\b(?:search|filter|query|field|textbox|input|record|item|row|cell|form|"
    r"result|selection|history)\b"
    r"|搜索|查找|筛选|过滤|查询|输入框|字段|记录|条目|表单|单元格|结果",
    re.IGNORECASE,
)
_WHOLE_SURFACE_LABEL_RE = re.compile(
    r"\b(?:clear|erase|delete|reset)\s+(?:the\s+)?(?:whole|entire)?\s*"
    r"(?:canvas|drawing|document|page|workspace|editor|content|output|surface)\b"
    r"|\bnew\s+(?:blank\s+)?(?:canvas|drawing|document|file|page)\b"
    r"|(?:清空|清除|删除|重置)(?:整个|全部)?"
    r"(?:画布|图画|绘图|文档|文件|页面|编辑区|工作区|内容|输出)"
    r"|(?:新建|空白)(?:画布|图画|文档|文件|页面)",
    re.IGNORECASE,
)
_RESET_REQUEST_RE = re.compile(
    r"\b(?:start\s+over|start\s+again|begin\s+again|restart\s+from\s+scratch)\b"
    r"|\b(?:reset|restart)\s+(?:(?:the|this|current)\s+)?(?:whole|entire|all)?\s*"
    r"(?:canvas|drawing|document|file|page|workspace|work|content|output|surface)\b"
    r"|\b(?:clear|delete|erase|remove)\s+(?:all|everything)\b"
    r"|\b(?:clear|delete|erase|remove)\s+(?:the\s+)?(?:whole|entire)\s+"
    r"(?:canvas|drawing|document|file|page|workspace|content|output|surface)\b"
    r"|\bnew\s+(?:blank|empty)\s+(?:document|file|page)\b"
    r"|(?:清空|清除|删除|重置)(?:全部|所有|整个|当前)?"
    r"(?:画布|图画|绘图|文档|文件|页面|工作区|当前工作|内容|输出)"
    r"|全部删除|删除全部|全部清除|清除全部|从头开始|重新开始|撤销全部|新建空白",
    re.IGNORECASE,
)
_NEGATED_RESET_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without)\s+(?:reset|clear|delete|erase|start\s+over)\b"
    r"|(?:不要|别|禁止)(?:清空|删除|重置|重新开始|从头开始)",
    re.IGNORECASE,
)
_REVERSAL_REQUEST_RE = re.compile(
    r"\b(?:undo|revert|roll\s+back|rollback)\b"
    r"|\b(?:discard|reverse)\s+(?:that|this|the\s+(?:last|previous)\s+(?:change|edit|action))\b"
    r"|\bcorrect\s+(?:that|the\s+(?:last|previous)\s+(?:change|edit))\s+by\s+"
    r"(?:undoing|reverting)\b"
    r"|\u64a4\u9500|\u56de\u9000(?:\u4e0a\u4e00\u6b65|\u521a\u624d)?|"
    r"\u6062\u590d\u5230\u4e0a\u4e00\u6b65|\u64a4\u56de\u521a\u624d",
    re.IGNORECASE,
)
_NEGATED_REVERSAL_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without)\s+"
    r"(?:undo|revert|roll\s+back|rollback)\b"
    r"|(?:\u4e0d\u8981|\u522b|\u7981\u6b62)(?:\u64a4\u9500|\u56de\u9000|\u64a4\u56de)",
    re.IGNORECASE,
)


def _json_object(text: str) -> dict[str, Any]:
    try:
        value, _end = json.JSONDecoder().raw_decode(str(text or "").lstrip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalise_keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            value = re.split(r"[+,]", text)
    if not isinstance(value, (list, tuple)):
        return ()
    aliases = {"control": "ctrl", "ctl": "ctrl", "del": "delete", "back": "backspace"}
    return tuple(
        aliases.get(str(item or "").strip().casefold(), str(item or "").strip().casefold())
        for item in value
        if str(item or "").strip()
    )


def _action_name(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _flatten_actions(tool_name: str, args: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return only typed/structured child actions; free-form plans never authorize reset."""
    root = _action_name(tool_name)
    flattened: list[tuple[str, dict[str, Any]]] = [(root, dict(args or {}))]
    parent_context = str(
        args.get("subgoal") or args.get("context") or args.get("purpose") or ""
    ).strip()

    def with_parent_context(payload: dict[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        if parent_context and not any(
            str(value.get(key) or "").strip()
            for key in _WHOLE_SURFACE_CONTEXT_KEYS
        ):
            value["_parent_context"] = parent_context
        return value

    children: Iterable[Any] = ()
    if root == "run_action_batch":
        children = args.get("actions") or ()
    elif root == "execute_subgoal":
        children = args.get("actions") or ()
        clicks = [args.get("click"), *(args.get("clicks") or [])]
        for click in clicks:
            if isinstance(click, dict):
                flattened.append(("click_element", with_parent_context(click)))
        if isinstance(args.get("input"), dict):
            flattened.append(("type_into_element", dict(args["input"])))
        if isinstance(args.get("draw"), dict):
            flattened.append(("draw_strokes", dict(args["draw"])))
        if isinstance(args.get("drag"), dict):
            flattened.append(("drag", dict(args["drag"])))
        for value in args.get("draws") or ():
            if isinstance(value, dict):
                flattened.append(("draw_strokes", dict(value)))
        for value in args.get("drags") or ():
            if isinstance(value, dict):
                flattened.append(("drag", dict(value)))
    for child in children:
        if not isinstance(child, dict):
            continue
        name = _action_name(batch_action_name(child) or child.get("name"))
        if not name:
            continue
        payload = batch_action_args(child)
        if child.get("name") and not payload:
            payload = {
                key: value for key, value in child.items()
                if key not in {"action", "tool", "name", "params"}
            }
        flattened.append((name, with_parent_context(payload)))
    return flattened


def _is_hotkey(name: str, args: dict[str, Any], *required: str) -> bool:
    if name != "hotkey":
        return False
    keys = set(_normalise_keys(args.get("keys", args.get("value"))))
    return all(key in keys for key in required)


def _is_copy(name: str, args: dict[str, Any]) -> bool:
    return _is_hotkey(name, args, "ctrl", "c")


def _is_select_all(name: str, args: dict[str, Any]) -> bool:
    return _is_hotkey(name, args, "ctrl", "a")


def _is_delete(name: str, args: dict[str, Any]) -> bool:
    return name == "hotkey" and bool(
        set(_normalise_keys(args.get("keys", args.get("value"))))
        & {"delete", "backspace"}
    )


def _is_selection_replacement(name: str, args: dict[str, Any]) -> bool:
    """Return whether this input replaces an existing whole-surface selection."""
    return bool(
        name in _SELECTION_REPLACEMENT_ACTIONS
        or _is_hotkey(name, args, "ctrl", "v")
    )


def _is_undo(name: str, args: dict[str, Any]) -> bool:
    keys = set(_normalise_keys(args.get("keys", args.get("value"))))
    if name == "hotkey":
        return "ctrl" in keys and "z" in keys and "shift" not in keys
    if name in {"click", "click_element"}:
        return _label(args) in {"undo", "撤销"}
    return False


def _labels(args: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("label", "text", "accessible_name", "target_text", "name"):
        value = str(args.get(key) or "").strip().casefold()
        if value:
            labels.append(re.sub(r"\s+", " ", value).strip(" .:："))
    for key in ("fallback_text", "text_query", "anchor"):
        raw = args.get(key)
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in values:
            value = str(item or "").strip().casefold()
            if value:
                labels.append(re.sub(r"\s+", " ", value).strip(" .:："))
    return list(dict.fromkeys(labels))


def _label(args: dict[str, Any]) -> str:
    labels = _labels(args)
    return labels[0] if labels else ""


def _whole_surface_context(args: dict[str, Any]) -> bool:
    if any(args.get(key) is True for key in (
        "whole_surface",
        "clear_whole_surface",
        "discard_document",
        "new_document",
    )):
        return True
    values = [
        str(args.get(key) or "").strip()
        for key in _WHOLE_SURFACE_CONTEXT_KEYS
        if str(args.get(key) or "").strip()
    ]
    context = " ".join(values)
    if not context or _LOCAL_CONTROL_CONTEXT_RE.search(context):
        return False
    return bool(_WHOLE_SURFACE_CONTEXT_RE.search(context))


def _reset_label_targets_whole_surface(label: str, args: dict[str, Any]) -> bool:
    normalized = re.sub(
        r"\s+", " ", str(label or "").strip().casefold(),
    ).strip(" .:：")
    if normalized in _RESET_LABELS or _WHOLE_SURFACE_LABEL_RE.search(normalized):
        return True
    return normalized in _AMBIGUOUS_RESET_LABELS and _whole_surface_context(args)


def _is_reset_action(name: str, args: dict[str, Any]) -> bool:
    if _is_hotkey(name, args, "ctrl", "n"):
        return True
    return name in {"click", "click_element", "menu_click"} and any(
        _reset_label_targets_whole_surface(label, args) for label in _labels(args)
    )


def _is_document_surface_clear(name: str, args: dict[str, Any]) -> bool:
    """Mirror the controller's implicit Ctrl+A/Backspace document contract."""
    return bool(
        name == "type_into_element"
        and args.get("document_surface") is True
        and args.get("clear", args.get("replace", True))
    )


def _has_true_flag(value: Any, names: set[str]) -> bool:
    if isinstance(value, dict):
        if any(value.get(name) is True for name in names):
            return True
        return any(_has_true_flag(item, names) for item in value.values())
    if isinstance(value, list):
        return any(_has_true_flag(item, names) for item in value)
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _has_true_flag(json.loads(value), names)
        except (json.JSONDecodeError, TypeError):
            return False
    return False


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _explicit_reset_requested(texts: Iterable[str]) -> bool:
    for value in texts:
        text = str(value or "").strip()
        if text and not _NEGATED_RESET_RE.search(text) and _RESET_REQUEST_RE.search(text):
            return True
    return False


def _explicit_reversal_requested(texts: Iterable[str]) -> bool:
    """Return only an explicit user request to undo/revert a visible change.

    Generic words such as ``fix`` or ``correct`` are deliberately insufficient:
    they describe the desired result but do not authorize throwing away the
    current output.  This keeps the exemption narrow enough to be safe across
    editors, forms, canvases, and other mutable GUI surfaces.
    """

    for value in texts:
        text = str(value or "").strip()
        if (
            text
            and not _NEGATED_REVERSAL_RE.search(text)
            and _REVERSAL_REQUEST_RE.search(text)
        ):
            return True
    return False


class PartialOutputDestructiveGuard:
    """Track successful GUI mutations and fail closed before broad destructive actions."""

    def __init__(self, task: str) -> None:
        self.task = str(task or "")
        self.ledger: list[dict[str, Any]] = []
        self._pending_visual: dict[str, Any] | None = None
        self._pending_select_all: dict[str, int] | None = None
        self._element_labels: dict[int, dict[str, Any]] = {}
        self._authorized_destructive: dict[str, Any] | None = None
        self._blocked_destructive: dict[str, Any] | None = None
        # Reset wording in the original task is an authorization for the
        # initial setup phase, not a permanent exemption from output safety.
        # It is consumed by the first destructive use or expires after the
        # first successful write.  Runtime user directives are likewise
        # consumed once so retained directive history cannot authorize every
        # later clear operation.
        self._task_reset_authorization_available = _explicit_reset_requested((self.task,))
        self._consumed_user_reset_directives: set[str] = set()
        self._task_reversal_authorization_available = _explicit_reversal_requested(
            (self.task,),
        )
        self._consumed_user_reversal_directives: set[str] = set()
        self._pending_reversal: dict[str, Any] | None = None
        self._reversal_debts: dict[int, dict[str, Any]] = {}

    @staticmethod
    def _reset_directive_key(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    def _fresh_user_reset_directive(self, directives: Iterable[str]) -> str:
        for value in directives:
            key = self._reset_directive_key(value)
            if (
                key
                and key not in self._consumed_user_reset_directives
                and _explicit_reset_requested((value,))
            ):
                return key
        return ""

    def _fresh_user_reversal_directive(self, directives: Iterable[str]) -> str:
        for value in directives:
            key = self._reset_directive_key(value)
            if (
                key
                and key not in self._consumed_user_reversal_directives
                and _explicit_reversal_requested((value,))
            ):
                return key
        return ""

    def _expire_reset_authorizations(self, directives: Iterable[str] = ()) -> None:
        self._task_reset_authorization_available = False
        for value in directives:
            key = self._reset_directive_key(value)
            if key and _explicit_reset_requested((value,)):
                self._consumed_user_reset_directives.add(key)

    def _active(self, hwnd: int) -> dict[str, Any] | None:
        for entry in reversed(self.ledger):
            if int(entry.get("hwnd") or 0) == int(hwnd) and not entry.get("invalidated_by"):
                return entry
        return None

    def _pending_selection(self, hwnd: int, sequence: int) -> bool:
        pending = self._pending_select_all or {}
        return bool(
            int(pending.get("hwnd") or 0) == int(hwnd)
            and int(sequence) - int(pending.get("sequence") or 0) <= 4
        )

    def _resolved_element_label(
        self,
        args: dict[str, Any],
        *,
        hwnd: int,
        screen_epoch: int,
        require_current: bool,
    ) -> str:
        try:
            element_id = int(args.get("element_id"))
        except (TypeError, ValueError):
            return ""
        cached = self._element_labels.get(int(hwnd)) or {}
        if require_current and int(cached.get("screen_epoch", -1)) != int(screen_epoch):
            return ""
        labels = cached.get("labels") if isinstance(cached.get("labels"), dict) else {}
        return str(labels.get(element_id) or "").strip().casefold()

    def _intent(
        self,
        actions: list[tuple[str, dict[str, Any]]],
        selected: bool,
        *,
        hwnd: int,
        screen_epoch: int,
        require_current_element: bool = True,
    ) -> str:
        for name, payload in actions:
            if _is_document_surface_clear(name, payload):
                return "document_surface_clear"
            if _is_reset_action(name, payload):
                return "reset_or_clear"
            resolved = self._resolved_element_label(
                payload,
                hwnd=hwnd,
                screen_epoch=screen_epoch,
                require_current=require_current_element,
            )
            if (
                name in {"click", "click_element", "menu_click"}
                and _reset_label_targets_whole_surface(resolved, payload)
            ):
                return "reset_or_clear"
        undo_count = sum(1 for name, payload in actions if _is_undo(name, payload))
        if undo_count > 1:
            return "bulk_undo"
        if undo_count == 1:
            return "single_undo"
        for name, payload in actions:
            if _is_select_all(name, payload):
                selected = True
            elif selected and _is_delete(name, payload):
                return "select_all_delete"
            elif selected and _is_selection_replacement(name, payload):
                return "select_all_replace"
        return ""

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        hwnd: int,
        screen_epoch: int,
        sequence: int,
        frame_id: int,
        frame_epoch: int,
        frame_hwnd: int,
        user_directives: Iterable[str] = (),
    ) -> dict[str, Any] | None:
        entry = self._active(hwnd)
        if entry is None:
            return None
        actions = _flatten_actions(tool_name, args)
        intent = self._intent(
            actions,
            self._pending_selection(hwnd, sequence),
            hwnd=hwnd,
            screen_epoch=screen_epoch,
        )
        if not intent:
            return None
        authorized = self._authorized_destructive or {}
        if (
            int(authorized.get("hwnd") or 0) == int(hwnd)
            and int(authorized.get("sequence") or -1) == int(sequence)
            and str(authorized.get("intent") or "") == intent
        ):
            # The loop evaluates model-authored args and the final upgraded
            # payload in the same tool sequence. A one-shot authorization must
            # cover both inspections, then record_result consumes it.
            return None
        fresh_user_reset = self._fresh_user_reset_directive(user_directives)
        task_reset = self._task_reset_authorization_available
        if task_reset or fresh_user_reset:
            self._task_reset_authorization_available = False
            if fresh_user_reset:
                self._consumed_user_reset_directives.add(fresh_user_reset)
            self._blocked_destructive = None
            self._authorized_destructive = {
                "hwnd": int(hwnd),
                "sequence": int(sequence),
                "intent": intent,
                "source": "task" if task_reset else "runtime_user_directive",
            }
            return None
        fresh_pixels = bool(
            int(entry.get("pixel_frame_id") or 0) > 0
            and int(entry.get("screen_epoch") or -1) == int(screen_epoch)
            and int(frame_epoch) == int(screen_epoch)
            and int(frame_hwnd) == int(hwnd)
            and int(frame_id) >= int(entry.get("pixel_frame_id") or 0)
        )
        debt = self._reversal_debts.get(int(hwnd)) or {}
        reversal_debt_block = bool(
            intent == "single_undo"
            and fresh_pixels
            and not entry.get("local_undo_consumed")
            and int(debt.get("undo_sequence") or 0) > 0
            and int(entry.get("sequence") or 0) > int(debt.get("undo_sequence") or 0)
            and int(debt.get("forward_mutations") or 0) < 2
        )
        if reversal_debt_block:
            fresh_user_reversal = self._fresh_user_reversal_directive(user_directives)
            task_reversal = self._task_reversal_authorization_available
            if task_reversal or fresh_user_reversal:
                self._task_reversal_authorization_available = False
                if fresh_user_reversal:
                    self._consumed_user_reversal_directives.add(fresh_user_reversal)
                self._blocked_destructive = None
                self._authorized_destructive = {
                    "hwnd": int(hwnd),
                    "sequence": int(sequence),
                    "intent": intent,
                    "source": (
                        "task_reversal"
                        if task_reversal
                        else "runtime_user_reversal_directive"
                    ),
                }
                return None
        if (
            intent == "single_undo"
            and fresh_pixels
            and not entry.get("local_undo_consumed")
            and not reversal_debt_block
        ):
            self._blocked_destructive = None
            return None
        block_signature = (
            int(hwnd),
            int(entry.get("sequence") or 0),
            "net_progress_reversal" if reversal_debt_block else intent,
        )
        previous_block = self._blocked_destructive or {}
        guard_blocks = (
            int(previous_block.get("guard_blocks") or 0) + 1
            if previous_block.get("signature") == block_signature
            else 1
        )
        self._blocked_destructive = {
            "signature": block_signature,
            "guard_blocks": guard_blocks,
        }
        safe_stop = guard_blocks >= 2
        payload = {
            "ok": False,
            "verified": False,
            "blocked": True,
            "partial_output_destructive_guard": True,
            "destructive_action": intent,
            "protected_mutation": {
                key: entry.get(key)
                for key in ("sequence", "tool", "kind", "hwnd", "screen_epoch", "frame_id", "evidence")
            },
            "fresh_pixel_evidence": fresh_pixels,
            "user_reset_requested": False,
            "local_undo_available": bool(
                fresh_pixels
                and not entry.get("local_undo_consumed")
                and not reversal_debt_block
            ),
            "mutation_applied": False,
            "needs_observe": bool(intent == "single_undo" and not fresh_pixels),
            "strategy_change_required": True,
            "guard_blocks": guard_blocks,
            "safe_stop": safe_stop,
            "error": (
                "A successful partial-output mutation already exists in this window. "
                "The broad destructive action was not dispatched. Preserve the visible output; "
                "use one evidence-grounded local correction, or ask the user before clearing it."
            ),
        }
        if reversal_debt_block:
            payload.update({
                "net_progress_reversal_guard": True,
                "reversal_debt": {
                    key: debt.get(key)
                    for key in (
                        "undo_sequence",
                        "reversed_mutation_sequence",
                        "forward_mutations",
                        "confirmed_frame_id",
                    )
                },
                "fresh_user_reversal_requested": False,
            })
            payload["error"] = (
                "The previous visible mutation was already undone, and the first new "
                "forward mutation would now be immediately reversed as well. This would "
                "return the GUI to the same no-net-progress state, so the undo was not "
                "dispatched. Preserve the new output and continue with another forward "
                "correction, or obtain a fresh explicit user request to undo/revert it."
            )
        if safe_stop:
            payload["error"] += (
                " The same protected destructive action was proposed twice, so the run is "
                "stopping instead of risking the existing output."
            )
        return payload

    def record_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
        *,
        dispatched: bool,
        terminal: str | None,
        hwnd: int,
        screen_epoch: int,
        sequence: int,
        frame_id: int,
        user_directives: Iterable[str] = (),
    ) -> None:
        if not dispatched:
            return
        self._remember_element_labels(result_text, hwnd=hwnd, screen_epoch=screen_epoch)
        actions = _flatten_actions(tool_name, args)
        selected_before = self._pending_selection(hwnd, sequence)
        authorized = self._authorized_destructive or {}
        authorized_intent = (
            str(authorized.get("intent") or "")
            if int(authorized.get("hwnd") or 0) == int(hwnd)
            and int(authorized.get("sequence") or -1) == int(sequence)
            else ""
        )
        if authorized_intent:
            self._authorized_destructive = None
        intent = authorized_intent or self._intent(
            actions,
            selected_before,
            hwnd=hwnd,
            screen_epoch=screen_epoch,
        )
        active = self._active(hwnd)
        if intent == "single_undo" and active is not None:
            active["local_undo_consumed"] = True
            active["undo_attempt_sequence"] = int(sequence)

        data = _json_object(result_text)
        terminal_failed = terminal in {"fail", "error", "stopped"}
        success = bool(
            data.get("ok") is True
            and data.get("blocked") is not True
            and not terminal_failed
        )
        strategy_success = bool(
            not terminal_failed
            and data.get("ok") is not False
            and data.get("blocked") is not True
            and bool(str(result_text or "").strip())
        )
        if strategy_success:
            # A real dispatched observation or alternative action breaks the
            # consecutive-ignore sequence. Many evidence/strategy tools return
            # human-readable text rather than an {"ok": true} object, so the
            # absence of an explicit failure is the dispatcher-level success
            # contract for this counter only. Mutation recording below remains
            # deliberately stricter.
            self._blocked_destructive = None
        if not success:
            return
        if intent == "single_undo" and active is not None:
            # The operator was dispatched, but only the authoritative
            # post-action frame can prove that pixels were actually reversed.
            # Keep this pending instead of prematurely deleting the mutation.
            self._pending_reversal = {
                "entry": active,
                "tool": _action_name(tool_name),
                "hwnd": int(hwnd),
                "sequence": int(sequence),
            }
            self._task_reversal_authorization_available = False
            fresh_reversal = self._fresh_user_reversal_directive(user_directives)
            if fresh_reversal:
                self._consumed_user_reversal_directives.add(fresh_reversal)
        if intent in {
            "reset_or_clear",
            "document_surface_clear",
            "select_all_delete",
            "select_all_replace",
            "bulk_undo",
        }:
            self._expire_reset_authorizations(user_directives)
            self._reversal_debts.pop(int(hwnd), None)
            self._pending_reversal = None
            for entry in self.ledger:
                if int(entry.get("hwnd") or 0) == int(hwnd) and not entry.get("invalidated_by"):
                    entry["invalidated_by"] = intent
            self._pending_select_all = None
            return

        selected_after = selected_before
        for name, payload in actions:
            if _is_select_all(name, payload):
                selected_after = True
            elif _is_copy(name, payload):
                continue
            elif name not in {"wait", "observe", "read_text"}:
                selected_after = False
        self._pending_select_all = (
            {"hwnd": int(hwnd), "sequence": int(sequence)} if selected_after else None
        )

        output_actions = [name for name, payload in actions if name in _POTENTIAL_OUTPUT_ACTIONS]
        typed_flag = _has_true_flag(data, _CONTENT_MUTATION_FLAGS)
        verified = _has_true_flag(data, {"verified"})
        deterministic = any(name in _DETERMINISTIC_OUTPUT_ACTIONS for name in output_actions)
        if not output_actions and not typed_flag:
            return
        entry = {
            "sequence": int(sequence),
            "tool": _action_name(tool_name),
            "kind": output_actions[0] if output_actions else "typed_mutation",
            "hwnd": int(hwnd),
            "screen_epoch": int(screen_epoch),
            "frame_id": int(frame_id),
            "evidence": (
                "verified" if verified else "typed_mutation_flag" if typed_flag else "typed_action_success"
            ),
            "verified": bool(verified),
            "mutation_applied": bool(typed_flag or deterministic),
            "pixel_frame_id": 0,
            "pixel_change_ratio": None,
            "local_undo_consumed": False,
        }
        if deterministic or typed_flag or verified:
            self.ledger.append(entry)
            del self.ledger[:-8]
            self._blocked_destructive = None
            self._expire_reset_authorizations(user_directives)
        self._pending_visual = {"entry": entry, "recorded": entry in self.ledger}

    def _remember_element_labels(
        self,
        result_text: str,
        *,
        hwnd: int,
        screen_epoch: int,
    ) -> None:
        labels: dict[int, str] = {}
        data = _json_object(result_text)
        for record in _iter_dicts(data):
            raw_id = record.get("elem_id", record.get("element_id"))
            raw_label = record.get("text", record.get("label"))
            try:
                element_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            label = str(raw_label or "").strip().casefold()
            if label:
                labels[element_id] = re.sub(r"\s+", " ", label).strip(" .:：")
        for match in re.finditer(r'^\[(\d+)\]\s+\S+(?:\s+"([^"]+)")?', str(result_text or ""), re.MULTILINE):
            label = str(match.group(2) or "").strip().casefold()
            if label:
                labels[int(match.group(1))] = re.sub(r"\s+", " ", label).strip(" .:：")
        if labels:
            self._element_labels[int(hwnd)] = {
                "screen_epoch": int(screen_epoch),
                "labels": labels,
            }

    def record_post_action(
        self,
        action: str,
        *,
        hwnd: int,
        screen_epoch: int,
        frame_id: int,
        comparable: bool,
        change_ratio: float | None,
        threshold: float,
    ) -> None:
        normalized_action = _action_name(str(action).split(":", 1)[0])
        changed = bool(
            comparable
            and change_ratio is not None
            and float(change_ratio) > float(threshold)
        )
        pending_reversal = self._pending_reversal
        if (
            pending_reversal
            and normalized_action == pending_reversal.get("tool")
            and int(pending_reversal.get("hwnd") or 0) == int(hwnd)
        ):
            entry = pending_reversal["entry"]
            if changed:
                entry["invalidated_by"] = "single_undo"
                entry["undo_confirmed_frame_id"] = int(frame_id)
                self._reversal_debts[int(hwnd)] = {
                    "undo_sequence": int(pending_reversal.get("sequence") or 0),
                    "reversed_mutation_sequence": int(entry.get("sequence") or 0),
                    "forward_mutations": 0,
                    "confirmed_frame_id": int(frame_id),
                }
            self._pending_reversal = None

        pending = self._pending_visual
        if not pending:
            return
        entry = pending["entry"]
        if normalized_action != entry.get("tool") or int(entry.get("hwnd") or 0) != int(hwnd):
            return
        entry["frame_id"] = int(frame_id)
        entry["pixel_change_ratio"] = float(change_ratio) if change_ratio is not None else None
        if changed:
            entry["screen_epoch"] = int(screen_epoch)
            entry["pixel_frame_id"] = int(frame_id)
            if not pending.get("recorded"):
                entry["evidence"] = "post_action_pixels"
                entry["mutation_applied"] = True
                self.ledger.append(entry)
                del self.ledger[:-8]
            debt = self._reversal_debts.get(int(hwnd))
            if (
                debt
                and int(entry.get("sequence") or 0) > int(debt.get("undo_sequence") or 0)
                and not entry.get("reversal_debt_counted")
            ):
                entry["reversal_debt_counted"] = True
                debt["forward_mutations"] = int(debt.get("forward_mutations") or 0) + 1
                debt["last_forward_sequence"] = int(entry.get("sequence") or 0)
                debt["last_forward_frame_id"] = int(frame_id)
                if int(debt["forward_mutations"]) >= 2:
                    self._reversal_debts.pop(int(hwnd), None)
        self._pending_visual = None
