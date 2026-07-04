"""Unified WinPilot Agent loop.

One AgentRunner now owns desktop GUI work, Coding Runtime work, tool dispatch,
trace, usage accounting, and done verification. Provider-specific chat details
are centralized in ``winpilot.model_adapter``; this loop keeps the stable
function-calling history, context folding, and tool-result repair behaviors.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import CONFIG, TRACES_DIR
from ..memory import experience
from ..model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
)
from ..perception import fusion, intent, vlm
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS, logger
from . import playbooks, prompts, session as session_mod
from .context_guidance import ContextGuidanceMixin
from .done_verifier import DoneVerifierMixin
from .planner import planning_enabled
from .reflection import ReflectionMixin
from .run_state import ACTION_TOOLS as _RUN_ACTION_TOOLS, RunStateMachine
from .runtime_profile import build_runtime_profile
from .task_rules import (
    _blocks_store_search_action,
    _blocks_upload_media_analysis,
    _gui_only_forbids_shell,
    _gui_only_launch_uses_shell,
    _gui_only_shell_allowed,
    _is_coding_task,
    _is_communication_task,
    _is_drawing_task,
    _is_gui_only_task,
    _is_upload_publish_task,
    _quoted_payloads,
    _required_image_paths,
    _required_visible_payloads,
    _visible_state_targets,
)
from .tools import ToolResult, ToolSession, tool_schemas

_KEEP_FULL_OBSERVATIONS = 2
_API_RETRIES = 4
# 429/rate-limit backoff base seconds (exponential: 5, 10, 20...). Bursts of
# rapid LLM calls (e.g. drawing many strokes) can trip provider rate limits;
# a short retry isn't enough, so wait progressively longer.
_RATE_LIMIT_BACKOFF_S = 5.0
# Fold old observations only past this size (~12K tokens × ~4 chars/token).
# Below it, history stays append-only so the DeepSeek prefix cache hits ~100%.
_CONTEXT_FOLD_THRESHOLD_CHARS = 32000
_MAX_ASSISTANT_HISTORY_CHARS = 900
_ACTION_TOOLS = set(_RUN_ACTION_TOOLS)
_DRAW_COLOR_TERMS = {
    "blue": ("blue", "蓝色", "蓝"),
    "red": ("red", "红色", "红"),
    "green": ("green", "绿色", "绿"),
    "yellow": ("yellow", "黄色", "黄"),
    "black": ("black", "黑色", "黑"),
    "purple": ("purple", "紫色", "紫"),
    "orange": ("orange", "橙色", "橙"),
}
_DRAW_SHAPE_TERMS = {
    "triangle": ("triangle", "三角形", "三角"),
    "rectangle": ("rectangle", "square", "矩形", "正方形", "方形"),
    "circle": ("circle", "圆形", "圆"),
    "line": ("line", "直线", "线"),
}
_DRAW_REGION_TERMS = {
    "left": ("left", "左侧", "左边", "左"),
    "right": ("right", "右侧", "右边", "右"),
    "center": ("center", "middle", "中间", "中央", "中部"),
    "top": ("top", "上方", "上面", "顶部"),
    "bottom": ("bottom", "下方", "下面", "底部"),
}
_DRAW_NON_ACTION_TERMS = (
    "confirm", "verify", "verification", "check", "visible", "exist", "exists",
    "screenshot", "screen shot", "capture", "save",
    "确认", "核验", "验证", "检查", "可见", "存在", "截图", "截屏", "保存",
)
_DRAW_ACTION_RE = re.compile(r"(draw|paint|sketch|绘制|画)")


def _client():
    from openai import OpenAI

    api = CONFIG.get("api", default={})
    if not api.get("api_key"):
        raise RuntimeError("config.json 缺少 api.api_key")
    return OpenAI(base_url=api["base_url"], api_key=api["api_key"], timeout=120)


def _main_model_has_vision() -> bool:
    """True when the configured main model natively supports image input."""
    api = CONFIG.get("api", default={})
    return model_profile_from_config(api).supports_vision


def _find_term(text: str, term_map: dict[str, tuple[str, ...]]) -> str:
    haystack = (text or "").casefold()
    for value, terms in term_map.items():
        if any(term.casefold() in haystack for term in terms):
            return value
    return ""


def _find_terms(text: str, term_map: dict[str, tuple[str, ...]]) -> list[str]:
    haystack = (text or "").casefold()
    return [
        value
        for value, terms in term_map.items()
        if any(term.casefold() in haystack for term in terms)
    ]


def _parse_plan_draw_spec(text: str) -> dict[str, str]:
    lowered = (text or "").casefold()
    if any(term.casefold() in lowered for term in _DRAW_NON_ACTION_TERMS):
        return {}
    action_matches = list(_DRAW_ACTION_RE.finditer(text or ""))
    target_text = (text or "")[action_matches[-1].end():] if action_matches else (text or "")
    shape = _find_term(target_text, _DRAW_SHAPE_TERMS) or _find_term(text, _DRAW_SHAPE_TERMS)
    color = _find_term(target_text, _DRAW_COLOR_TERMS) or _find_term(text, _DRAW_COLOR_TERMS)
    if not (shape and color):
        return {}
    spec = {"shape": shape, "color": color}
    region = _find_term(text, _DRAW_REGION_TERMS)
    if region:
        spec["expected_region"] = region
    return spec


def _draw_spec_signature(spec: dict[str, Any]) -> tuple[str, str]:
    return (
        str(spec.get("shape") or "").casefold(),
        str(spec.get("color") or spec.get("colour") or "").casefold(),
    )


def _draw_spec_region(spec: dict[str, Any]) -> str:
    return str(spec.get("expected_region") or spec.get("region") or "").casefold()


def _draw_spec_key(spec: dict[str, Any]) -> tuple[str, str, str]:
    shape, color = _draw_spec_signature(spec)
    return shape, color, _draw_spec_region(spec)


def _sanitize_assistant(msg) -> dict[str, Any]:
    """Assistant message for history — WITHOUT reasoning_content."""
    content = msg.content or ""
    if len(content) > _MAX_ASSISTANT_HISTORY_CHARS:
        content = (
            content[:_MAX_ASSISTANT_HISTORY_CHARS]
            + "\n[assistant content truncated in history; full response is kept in the LLM trace]"
        )
    out: dict[str, Any] = {"role": "assistant", "content": content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return out


class AgentRunner(ContextGuidanceMixin, ReflectionMixin, DoneVerifierMixin):
    """One task execution. Designed to run on a background thread."""

    def __init__(self, task: str, stop_event: threading.Event | None = None,
                 session_context: list | None = None,
                 max_steps_override: int | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.session = ToolSession(stop_event=self.stop_event, task=task)
        self.session.run_id = self.run_id
        # Prior finished tasks this session (list[SessionEntry]) for continuity.
        self.session_context = session_context or []
        self.messages: list[dict] = []
        self._foldable: list[tuple[int, str]] = []  # (message index, summary line)
        self._steps_log: list[str] = []             # action recipe for experience memory
        self._hint_ids: list[str] = []              # injected recipe ids (score feedback)
        self._injected_playbooks: set[str] = set()  # avoid double-injecting playbooks
        self._had_verified_action = False           # any action passed external verify
        self._last_verify_ok: bool | None = None    # most recent verify outcome
        self._done_rejects = 0                       # done-gate bounce count
        self._done_unverified = False                # forced-accept after max rejects
        self._done_verified = False                  # done-gate judge genuinely passed
        self._recent_results: list[tuple[str, str]] = []  # tool outputs as done-gate evidence
        self._visible_evidence_ledger: list[dict[str, Any]] = []
        self._last_done_reject_reason = ""
        self._escalate_next = False                  # use stronger model on next _chat
        self._consecutive_unverified = 0             # steps without a verified=true action
        self._last_reflect_step = 0                  # step number of last reflect injection
        self.status = "pending"                     # running/done/fail/reply/stopped/error
        self.result_text = ""
        self.usage_total = empty_usage_total()
        self._model_adapter = None
        self._last_llm_model = ""
        self._interjections: list[str] = []          # mid-run user hints
        self._interject_lock = threading.Lock()
        self._llm_trace_index = 0                    # local forensic trace files
        self.max_steps_override = max_steps_override
        self.run_state = RunStateMachine()
        self.runtime_profile = build_runtime_profile(task)
        # Coding is now an in-process runtime capability of this AgentRunner,
        # not a separate sub-agent / runner loop.
        self._is_coding = self.runtime_profile.coding
        self._tool_history: list[tuple[str, dict[str, Any], str]] = []
        self._completion_rejects = 0

    def interject(self, text: str) -> None:
        """Queue a user hint to inject before the agent's next decision."""
        text = (text or "").strip()
        if text:
            with self._interject_lock:
                self._interjections.append(text)

    def _drain_interjections(self) -> None:
        """Append any queued user hints as user messages (valid after tool
        results) so the model sees them before the next API call."""
        with self._interject_lock:
            pending = self._interjections
            self._interjections = []
        for text in pending:
            self.messages.append({
                "role": "user",
                "content": f"[用户实时指示] {text}\n"
                           f"（用户在旁观察后给出的实时指示，请【优先、按字面】遵循；"
                           f"若与当前做法冲突，以用户指示为准。"
                           f"若用户表达停止/取消/不要这样做，就立即停下当前动作并据此调整，"
                           f"不要自行换一种方式硬继续原目标。）",
            })
            BUS.publish("chat", run_id=self.run_id, role="user", content=text,
                        interject=True)
            logger.info("收到用户插话: %s", text[:80])

    # ------------------------------------------------------------- tracing
    @staticmethod
    def _jsonable(obj: Any) -> Any:
        """Best-effort conversion for OpenAI SDK objects into JSON data."""
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {str(k): AgentRunner._jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [AgentRunner._jsonable(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return AgentRunner._jsonable(obj.model_dump())
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return AgentRunner._jsonable(obj.dict())
            except Exception:
                pass
        return str(obj)

    def _trace_debug(self, kind: str, payload: dict[str, Any]) -> None:
        """Write exact per-step forensic data to traces/<run_id>/.

        This is intentionally file-only, not a BUS event, so the web UI does
        not receive huge prompt payloads. It lets us reproduce what the LLM saw
        and why it made each decision after a failed desktop run.
        """
        if not CONFIG.get("agent", "llm_trace", default=True):
            return
        try:
            self._llm_trace_index += 1
            run_dir = TRACES_DIR / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "_", kind).strip("_") or "event"
            path = run_dir / f"{self._llm_trace_index:03d}_{safe_kind}.json"
            path.write_text(
                json.dumps(self._jsonable(payload), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("LLM trace write failed: %s", exc)

    # ------------------------------------------------------------- history
    def _append_tool_result(self, tool_call_id: str, result_text: str,
                            summary: str = "",
                            image_b64: str = "",
                            tool_name: str = "") -> None:
        # NOTE: mime API does not support multipart tool results (image_url in tool role).
        # Always use plain text; screenshot is injected as a separate user message via
        # _inject_observe_screenshot when image_b64 is empty (fallback path).
        content = result_text
        if tool_name in {
            "execute_subgoal",
            "run_action_batch",
            "zoom",
            "screenshot",
            "draw_strokes",
            "drag",
            "complete_subgoal",
            "revise_plan",
        }:
            content = self._compact_tool_evidence(tool_name, result_text)
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content})
        if summary:
            self._foldable.append((len(self.messages) - 1, summary))
            self._fold_old()

    def _fold_checkpoint(self, folded_summary: str) -> str:
        """Compact state that survives when an old bulky tool result is folded."""
        parts = ["[folded context checkpoint]", f"goal: {self.task[:200]}"]
        snapshot = self.session.snapshot
        if snapshot is not None:
            elements = getattr(snapshot, "elements", []) or []
            parts.append(
                f'screen: title="{getattr(snapshot, "title", "")[:80]}" '
                f'hwnd={getattr(snapshot, "hwnd", "")} elements={len(elements)}'
            )
        state = self.run_state.to_dict()
        if state.get("phase") and state.get("phase") != "idle":
            issue = str(state.get("last_issue") or "")[:180]
            parts.append(
                f'run_state: phase={state.get("phase")} '
                f'last_tool={state.get("last_tool")} issue="{issue}"'
            )
        if self.session.world_enabled:
            world_state = self.session.world.state()
            if world_state.get("last_change") or world_state.get("screen_watch"):
                parts.append(
                    "world: "
                    f'last_change="{str(world_state.get("last_change") or "")[:180]}" '
                    f'repeated={world_state.get("repeated")} '
                    f'identical_observes={world_state.get("identical_observes")}'
                )
        if self._recent_results:
            parts.append("recent_tool_evidence:")
            for name, text in self._recent_results[-3:]:
                one_line = re.sub(r"\s+", " ", str(text)).strip()[:260]
                parts.append(f"- {name}: {one_line}")
        parts.append(f"folded_item: {folded_summary}")
        return "\n".join(parts)[:1800]

    def _fold_old(self) -> None:
        """Compress old bulky observations — but ONLY when the context is large.

        Folding mutates a historical message, which breaks DeepSeek's prefix
        cache from that point once. So we avoid it entirely while the context
        is small (the common case → near-100% cache hits), and only start
        folding the oldest observations once total size crosses a threshold.
        The newest _KEEP_FULL_OBSERVATIONS are always kept full.
        """
        def total_chars() -> int:
            total = 0
            for m in self.messages:
                c = m.get("content") or ""
                total += len(c) if isinstance(c, str) else sum(
                    len(str(part)) for part in c
                )
            return total

        while (
            len(self._foldable) > _KEEP_FULL_OBSERVATIONS
            and total_chars() > int(CONFIG.get(
                "agent", "context_fold_threshold_chars",
                default=_CONTEXT_FOLD_THRESHOLD_CHARS,
            ))
        ):
            idx, summary = self._foldable.pop(0)
            content = self.messages[idx]["content"]
            already_folded = (
                isinstance(content, str)
                and (
                    content.startswith("[已折叠]")
                    or content.startswith("[folded context checkpoint]")
                )
            )
            if not already_folded:
                self.messages[idx]["content"] = self._fold_checkpoint(summary)

    def _inject_observe_screenshot(self) -> None:
        """After an observe, embed the current window screenshot into history as
        a user message so a native vision model can see the actual pixels
        without a separate VLM grounding pass.

        The injected message is registered in _foldable so it gets compressed
        alongside the paired observe tool result when history grows large.
        """
        if not _main_model_has_vision():
            return
        hwnd = self.session.hwnd
        if not hwnd:
            return
        try:
            b64 = capture_window_jpeg_b64(hwnd)
            if not b64:
                return
            self.messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "[当前窗口截图，结合上方文字元素列表使用]"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            })
            self._foldable.append((len(self.messages) - 1, "[已折叠 screenshot]"))
            self._fold_old()
        except Exception as exc:
            logger.debug("observe 截图注入失败: %s", exc)

    # ----------------------------------------------------------------- api
    def _chat(self, client, tools: list[dict]):
        """Run the (blocking) completion in a worker thread so a Stop can
        abandon an in-flight LLM call immediately, instead of waiting up to the
        120s request timeout. Returns the response, or None if stopped."""
        api = CONFIG.get("api", default={})
        model = api.get("model", "deepseek-v4-flash")
        # Escalate ONE decision to a stronger model when we just got stuck
        # (model-tier mitigation): the weak model flails, the strong model
        # picks the next move, then we drop back to cheap.
        if self._escalate_next:
            self._escalate_next = False
            esc = CONFIG.get("agent", "escalation_model", default="")
            if esc:
                logger.info("本步升级到更强模型: %s", esc)
                model = esc
            else:
                logger.warning("卡住需升级模型，但 agent.escalation_model 未配置，仍用 %s", model)

        box: dict[str, Any] = {}
        self._last_llm_model = model
        self._trace_debug("llm_request", {
            "run_id": self.run_id,
            "task": self.task,
            "model": model,
            "temperature": float(api.get("temperature", 0.3)),
            "max_tokens": int(api.get("max_tokens", 4096)),
            "messages": self.messages,
            "tools": tools,
        })

        def _is_rate_limit(exc: Exception) -> bool:
            text = str(exc).lower()
            return "429" in text or "too many requests" in text or "rate limit" in text

        def _backoff_sleep(seconds: float) -> bool:
            """Sleep in small slices so a Stop interrupts the wait. Returns
            False if a Stop was requested mid-wait."""
            waited = 0.0
            while waited < seconds:
                if self.stop_event.is_set():
                    return False
                time.sleep(0.2)
                waited += 0.2
            return True

        def worker() -> None:
            last_exc: Exception | None = None
            for attempt in range(1, _API_RETRIES + 1):
                if self.stop_event.is_set():
                    return
                try:
                    box["resp"] = client.chat.completions.create(
                        model=model,
                        messages=self.messages,
                        tools=tools,
                        temperature=float(api.get("temperature", 0.3)),
                        max_tokens=int(api.get("max_tokens", 4096)),
                    )
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning("API 调用失败 (第%d次): %s", attempt, exc)
                    if attempt < _API_RETRIES:
                        # Rate limits (429) need a much longer wait than a
                        # transient network blip — back off exponentially
                        # (5s, 10s, 20s...) so a burst of drawing strokes
                        # doesn't kill the run. Stop interrupts the wait.
                        if _is_rate_limit(exc):
                            if not _backoff_sleep(_RATE_LIMIT_BACKOFF_S * (2 ** (attempt - 1))):
                                return
                        else:
                            if not _backoff_sleep(1.5 * attempt):
                                return
            box["exc"] = RuntimeError(f"API 连续 {_API_RETRIES} 次失败: {last_exc}")

        thread = threading.Thread(target=worker, daemon=True, name="llm-call")
        thread.start()
        while thread.is_alive():
            if self.stop_event.is_set():
                logger.info("Stop 请求：放弃进行中的 LLM 调用")
                self._trace_debug("llm_abandoned", {
                    "run_id": self.run_id,
                    "model": model,
                    "reason": "stop_event",
                })
                return None  # abandon the in-flight request (it finishes unused)
            thread.join(timeout=0.2)
        if "exc" in box:
            self._trace_debug("llm_error", {
                "run_id": self.run_id,
                "model": model,
                "error": str(box["exc"]),
            })
            raise box["exc"]
        if box.get("resp") is not None:
            self._trace_debug("llm_response", {
                "run_id": self.run_id,
                "model": model,
                "response": box.get("resp"),
            })
        return box.get("resp")

    def _track_usage(self, resp) -> None:
        api = CONFIG.get("api", default={})
        profile = model_profile_from_config({
            **api,
            "model": self._last_llm_model or api.get("model", ""),
        })
        adapter = getattr(self, "_model_adapter", None)
        if adapter is not None and not self._last_llm_model:
            profile = getattr(adapter, "profile", profile)
        step_usage = accumulate_usage(self.usage_total, resp, profile=profile, api=api)
        if not step_usage:
            return
        BUS.publish(
            "agent_usage",
            run_id=self.run_id,
            step_usage=step_usage,
            total=dict(self.usage_total),
        )

    # ---------------------------------------------------------------- steps
    def _record_step(self, name: str, args: dict, replay: dict | None = None) -> None:
        """Log an intent-level step for experience memory.

        Prefer replay metadata (target text / command) over raw args: element
        ids and absolute coordinates are meaningless in a future session, while
        "click 保存(S)" generalizes across resolutions and layouts.
        """
        if name not in _ACTION_TOOLS:
            return
        meta = replay or {}
        line = ""
        if name == "click":
            target = meta.get("text") or ""
            line = f'click "{target}"' if target else (
                f'click ({meta["coords"][0]},{meta["coords"][1]})'
                if meta.get("coords") else "click")
        elif name == "type_text":
            text = str(meta.get("text_input") or args.get("text") or "")[:60]
            where = meta.get("element_text") or ""
            line = f'type_text "{text}"' + (f' 在"{where}"' if where else "")
        elif name == "hotkey":
            line = f'hotkey {"+".join(meta.get("keys") or args.get("keys") or [])}'
        elif name in ("launch", "run_shell"):
            line = f'{name} {str(meta.get("command") or args.get("command") or "")[:120]}'
        elif name == "focus_window":
            line = f'focus_window "{meta.get("title") or ""}"'
        elif name == "tray_click":
            line = f'tray_click "{meta.get("name") or args.get("name") or ""}"'
        elif name == "wait":
            cond = meta.get("condition") or args.get("condition") or {}
            line = f"wait {json.dumps(cond, ensure_ascii=False)}"
        elif name == "execute_subgoal":
            line = f'execute_subgoal "{str(args.get("subgoal") or meta.get("subgoal") or "")[:120]}"'
        if not line:  # scroll/drag stay positional by nature
            compact = {k: v for k, v in args.items() if k != "expect"}
            line = f"{name}({json.dumps(compact, ensure_ascii=False)})"
        self._steps_log.append(line)

    def _track_verification(self, tool_name: str, result_text: str) -> None:
        """Note external verify outcomes so a run whose actions never verified
        can't be recorded as trusted experience (anti fake-done)."""
        if tool_name not in _ACTION_TOOLS:
            return
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(data, dict) and "verified" in data:
            ok = bool(data["verified"])
            self._last_verify_ok = ok
            if ok:
                self._had_verified_action = True

    def _visible_evidence_targets(self) -> list[str]:
        targets = [*_visible_state_targets(self.task), *_required_visible_payloads(self.task)]
        seen: set[str] = set()
        out: list[str] = []
        for target in targets:
            target = str(target or "").strip()
            key = self._normalize_visible_text(target)
            if target and key and key not in seen:
                seen.add(key)
                out.append(target)
        return out

    def _visible_snippet(self, text: str, target: str, limit: int = 260) -> str:
        target_norm = self._normalize_visible_text(target)
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        negative_markers = (
            "no elements matched",
            "did not contain query",
            "does not contain query",
            "not contain query",
            "no match",
            "not found",
            "未匹配",
            "没有命中",
            "未找到",
            "没有找到",
        )
        matched_negative = False
        for line in lines:
            if target_norm and target_norm in self._normalize_visible_text(line):
                if any(marker in line.casefold() for marker in negative_markers):
                    matched_negative = True
                    continue
                return line[:limit]
        if matched_negative:
            return ""
        compact = re.sub(r"\s+", " ", text or "").strip()
        return compact[:limit]

    def _append_visible_evidence(self, source: str, target: str, snippet: str) -> None:
        target = str(target or "").strip()
        snippet = str(snippet or "").strip()
        if not target or not snippet:
            return
        key = (source, self._normalize_visible_text(target), self._normalize_visible_text(snippet))
        for item in self._visible_evidence_ledger:
            existing = (
                str(item.get("source") or ""),
                self._normalize_visible_text(str(item.get("target") or "")),
                self._normalize_visible_text(str(item.get("snippet") or "")),
            )
            if existing == key:
                return
        entry = {
            "source": source,
            "target": target,
            "snippet": snippet[:260],
        }
        self._visible_evidence_ledger.append(entry)
        self._visible_evidence_ledger = self._visible_evidence_ledger[-30:]
        if getattr(self, "session", None) is not None:
            self.session.visible_evidence_ledger = self._visible_evidence_ledger
        BUS.publish("visible_evidence", run_id=self.run_id, **entry)

    @staticmethod
    def _expect_visible_values(args: Any) -> list[str]:
        if not isinstance(args, dict):
            return []
        values: list[str] = []
        expect = args.get("expect")
        if isinstance(expect, dict):
            for key in ("text_appears", "text_contains", "window_title", "window_appears"):
                value = expect.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
        actions = args.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                payload = action
                if len(action) == 1 and not ("action" in action or "tool" in action):
                    only_args = next(iter(action.values()))
                    payload = only_args if isinstance(only_args, dict) else {}
                values.extend(AgentRunner._expect_visible_values(payload))
        return values

    def _track_visible_evidence(self, tool_name: str, args: dict, result_text: str) -> None:
        targets = self._visible_evidence_targets()
        if tool_name in {"observe", "read_text"}:
            haystack = self._normalize_visible_text(result_text)
            for target in targets:
                if self._normalize_visible_text(target) in haystack:
                    self._append_visible_evidence(
                        tool_name,
                        target,
                        self._visible_snippet(result_text, target),
                    )
            return
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        if isinstance(data, dict) and tool_name == "execute_subgoal":
            packet = data.get("evidence_packet")
            if isinstance(packet, dict):
                matched = packet.get("matched_texts") or []
                preview = str(packet.get("visible_text_preview") or "")
                if isinstance(matched, list):
                    for value in matched:
                        text = str(value or "").strip()
                        if text:
                            self._append_visible_evidence(
                                "execute_subgoal:evidence_packet",
                                text,
                                self._visible_snippet(preview, text) or f"subgoal matched visible text: {text}",
                            )
                visual_colors = packet.get("matched_visual_colors") or []
                if isinstance(visual_colors, list):
                    for value in visual_colors:
                        color = str(value or "").strip().casefold()
                        if color:
                            self._append_visible_evidence(
                                "execute_subgoal:visual_color",
                                color,
                                f"subgoal matched visual color: {color}",
                            )
        if not (isinstance(data, dict) and data.get("verified") is True):
            return
        for value in self._expect_visible_values(args):
            self._append_visible_evidence(
                f"{tool_name}:verified_expect",
                value,
                f"verified expect text appeared: {value}",
            )

    @staticmethod
    def _compact_visual_summary(summary: Any) -> Any:
        if not isinstance(summary, dict):
            return summary
        keep = {
            "width",
            "height",
            "blankish",
            "ink_ratio",
            "ink_bbox",
            "component_count",
            "likely_canvas_bbox",
        }
        out = {key: summary[key] for key in keep if key in summary}
        colors = summary.get("visible_colors")
        if isinstance(colors, dict):
            out["visible_colors"] = {
                name: {
                    key: value
                    for key, value in stats.items()
                    if key in {"pixels", "ratio", "bbox", "largest_bbox", "largest_area"}
                }
                for name, stats in colors.items()
                if isinstance(stats, dict)
            }
        likely = summary.get("likely_canvas_summary")
        if isinstance(likely, dict):
            out["likely_canvas_summary"] = AgentRunner._compact_visual_summary(likely)
        return out

    @staticmethod
    def _compact_tool_evidence(tool_name: str, result_text: str) -> str:
        if tool_name in {"complete_subgoal", "revise_plan"}:
            plan_items: list[dict[str, Any]] = []
            for line in (result_text or "").splitlines():
                match = re.match(r"\[(?P<done>[xX ])\]\s*(?P<index>\d+)\.\s*(?P<goal>.+)", line.strip())
                if not match:
                    continue
                plan_items.append({
                    "index": int(match.group("index")),
                    "done": match.group("done").casefold() == "x",
                    "goal": match.group("goal")[:180],
                })
            first_line = (result_text or "").splitlines()[0] if (result_text or "").splitlines() else ""
            compact: dict[str, Any] = {
                "tool": tool_name,
                "message": first_line[:240],
            }
            completed_match = re.search(r"子目标\s*(\d+)", first_line)
            if completed_match:
                compact["completed_index"] = int(completed_match.group(1))
            if "已核验" in first_line:
                compact["verified"] = True
            if "所有子目标均完成" in (result_text or ""):
                compact["all_done"] = True
            if plan_items:
                compact["completed_indices"] = [
                    item["index"] for item in plan_items if item["done"]
                ]
                compact["remaining_steps"] = [
                    item for item in plan_items if not item["done"]
                ][:6]
                compact["next_unfinished"] = next(
                    (item for item in plan_items if not item["done"]),
                    None,
                )
            compact["guidance"] = (
                "continue with next_unfinished; combine obvious adjacent drawing/click routines "
                "in one execute_subgoal when possible; do not revise_plan just to restate the same todo"
            )
            return json.dumps(compact, ensure_ascii=False, default=str)[:1000]
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return (result_text or "")[:1200]
        if not isinstance(data, dict):
            return (result_text or "")[:1200]
        if tool_name == "execute_subgoal":
            packet = data.get("evidence_packet") if isinstance(data.get("evidence_packet"), dict) else {}
            compact_actions: list[dict[str, Any]] = []
            for action in data.get("actions_executed") or []:
                if not isinstance(action, dict):
                    continue
                compact_action = {
                    key: action.get(key)
                    for key in (
                        "action",
                        "color",
                        "shape",
                        "requested_color",
                        "canvas_region",
                        "draw_region",
                        "button",
                        "label",
                        "expected_region",
                    )
                    if key in action
                }
                if action.get("strokes"):
                    compact_action["stroke_count"] = len(action.get("strokes") or [])
                if compact_action:
                    compact_actions.append(compact_action)
            compact = {
                "ok": data.get("ok"),
                "verified": data.get("verified"),
                "subgoal": data.get("subgoal"),
                "subgoal_route": data.get("subgoal_route"),
                "controller_state": data.get("controller_state"),
                "needs_llm_reason": data.get("needs_llm_reason"),
                "actions_executed": compact_actions,
                "matched_texts": packet.get("matched_texts"),
                "missing_texts": packet.get("missing_texts"),
                "matched_visual_colors": packet.get("matched_visual_colors"),
                "missing_visual_colors": packet.get("missing_visual_colors"),
                "visible_colors": packet.get("visible_colors"),
                "candidate_click_labels": packet.get("candidate_click_labels"),
                "fast_path_advice": data.get("fast_path_advice"),
                "auto_completed_plan_items": data.get("auto_completed_plan_items"),
                "plan_after_auto_complete": str(data.get("plan_after_auto_complete") or "")[:500],
                "metrics": data.get("metrics"),
            }
            return json.dumps(compact, ensure_ascii=False, default=str)[:1800]
        if tool_name == "run_action_batch":
            compact_results: list[dict[str, Any]] = []
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                child: dict[str, Any] = {
                    "action": item.get("action"),
                    "ok": item.get("ok"),
                }
                for key in ("verified", "elapsed_ms"):
                    if key in item:
                        child[key] = item[key]
                child_data = item.get("data")
                if not isinstance(child_data, dict):
                    child_data = {}
                    preview = item.get("preview")
                    if isinstance(preview, str):
                        try:
                            parsed_preview = json.loads(preview)
                            if isinstance(parsed_preview, dict):
                                child_data = parsed_preview
                        except (json.JSONDecodeError, TypeError):
                            child_data = {}
                for key in (
                    "verified",
                    "visual_change_detected",
                    "visual_change_ratio",
                    "verify_method",
                    "strokes_drawn",
                    "changed",
                    "stable",
                    "waited_ms",
                ):
                    if key in child_data:
                        child[key] = child_data[key]
                if "nonvisual_summary" in child_data:
                    child["nonvisual_summary"] = AgentRunner._compact_visual_summary(
                        child_data.get("nonvisual_summary")
                    )
                compact_results.append(child)
            compact = {
                "ok": data.get("ok"),
                "verified": data.get("verified"),
                "needs_observe": data.get("needs_observe"),
                "last_action": data.get("last_action"),
                "fast_path_advice": data.get("fast_path_advice"),
                "metrics": data.get("metrics"),
                "results": compact_results,
            }
            return json.dumps(compact, ensure_ascii=False, default=str)[:1600]
        if tool_name in {"draw_strokes", "drag"} and data.get("wrapped_by_fast_controller"):
            compact: dict[str, Any] = {
                "ok": data.get("ok"),
                "verified": data.get("verified"),
                "wrapped_by_fast_controller": data.get("wrapped_by_fast_controller"),
                "visual_change_detected": data.get("visual_change_detected"),
                "visual_change_ratio": data.get("visual_change_ratio"),
                "verify_method": data.get("verify_method"),
                "needs_observe": data.get("needs_observe"),
                "last_action": data.get("last_action"),
                "fast_path_advice": data.get("fast_path_advice"),
                "metrics": data.get("metrics"),
            }
            auto = data.get("auto_visual_verify")
            if isinstance(auto, dict):
                compact["auto_visual_verify"] = {
                    "source_action": auto.get("source_action"),
                    "region": auto.get("region"),
                    "verified": auto.get("verified"),
                    "visual_change_detected": auto.get("visual_change_detected"),
                    "visual_change_ratio": auto.get("visual_change_ratio"),
                    "verify_method": auto.get("verify_method"),
                    "nonvisual_summary": AgentRunner._compact_visual_summary(
                        auto.get("nonvisual_summary")
                    ),
                }
            if "nonvisual_summary" in data:
                compact["nonvisual_summary"] = AgentRunner._compact_visual_summary(
                    data.get("nonvisual_summary")
                )
            return json.dumps(compact, ensure_ascii=False, default=str)[:1600]
        if tool_name in {"zoom", "screenshot"}:
            compact = {
                "ok": data.get("ok"),
                "region": data.get("region"),
                "nonvisual_summary": AgentRunner._compact_visual_summary(
                    data.get("nonvisual_summary")
                ),
            }
            return json.dumps(compact, ensure_ascii=False, default=str)[:1600]
        return (result_text or "")[:1200]

    @staticmethod
    def _should_fast_wrap_visual_action(tool_name: str, args: dict[str, Any]) -> bool:
        """Route standalone GUI mutations through the fast controller.

        The model still decides the next subgoal, but routine visible actions
        should go through the controller so they get batching metadata,
        screen-sync, anchor upgrades, and needs-observe feedback even when the
        model did not explicitly call run_action_batch.
        """
        if tool_name not in {
            "click",
            "type_text",
            "hotkey",
            "scroll",
            "focus_window",
            "draw_strokes",
            "drag",
        }:
            return False
        return not isinstance(args.get("expect"), dict)

    @staticmethod
    def _normalise_fast_wrapped_action(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(args)
        payload["action"] = tool_name
        return payload

    @staticmethod
    def _fast_wrapped_result(tool_name: str, args: dict[str, Any], batch_result: ToolResult) -> ToolResult:
        try:
            data = json.loads(batch_result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        children = [item for item in (data.get("results") or []) if isinstance(item, dict)]
        action_aliases = {tool_name}
        if tool_name == "click":
            action_aliases.add("click_element")
        if tool_name == "type_text":
            action_aliases.add("type_into_element")
        source_child = next((item for item in children if item.get("action") in action_aliases), {})
        auto_child = next((item for item in children if item.get("action") == "auto_visual_verify"), {})
        source_data = source_child.get("data") if isinstance(source_child.get("data"), dict) else {}
        auto_data = auto_child.get("data") if isinstance(auto_child.get("data"), dict) else {}

        wrapped: dict[str, Any] = {
            "ok": bool(data.get("ok")) if "ok" in data else not bool(data.get("error")),
            "wrapped_by_fast_controller": True,
            "tool": tool_name,
            "source_action": source_child.get("action") or tool_name,
            "source_batch_ok": data.get("ok"),
            "source_batch_verified": data.get("verified"),
            "needs_observe": data.get("needs_observe"),
            "last_action": data.get("last_action"),
            "metrics": data.get("metrics"),
        }
        if data.get("fast_path_advice"):
            wrapped["fast_path_advice"] = data.get("fast_path_advice")
        if source_child:
            wrapped["source_action_ok"] = source_child.get("ok")
            if "verified" in source_child:
                wrapped["source_action_verified"] = source_child.get("verified")
        if "verified" in source_data:
            wrapped["source_action_verified"] = source_data.get("verified")
        if auto_child:
            wrapped["auto_visual_verify"] = auto_data or auto_child
            if "verified" in auto_data:
                wrapped["verified"] = bool(auto_data.get("verified"))
            elif "verified" in auto_child:
                wrapped["verified"] = bool(auto_child.get("verified"))
        elif "verified" in data:
            wrapped["verified"] = bool(data.get("verified"))
        for source in (auto_data, source_data, data):
            if not isinstance(source, dict):
                continue
            for key in (
                "visual_change_detected",
                "visual_change_ratio",
                "verify_method",
                "nonvisual_summary",
            ):
                if key in source and key not in wrapped:
                    wrapped[key] = source[key]
        wrapped["results"] = children
        return ToolResult(
            json.dumps(wrapped, ensure_ascii=False, default=str),
            summary=batch_result.summary,
            replay=batch_result.replay or {"tool": tool_name, **{k: v for k, v in args.items() if k != "expect"}},
        )

    def _dispatch_with_fast_visual_wrap(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if not self._should_fast_wrap_visual_action(tool_name, args):
            return self.session.dispatch(tool_name, args)
        batch_args = {
            "actions": [self._normalise_fast_wrapped_action(tool_name, args)],
        }
        if tool_name in {"draw_strokes", "drag"}:
            batch_args["sync"] = False
        batch_result = self.session.dispatch("run_action_batch", batch_args)
        return self._fast_wrapped_result(tool_name, args, batch_result)

    def _drawing_should_stop_mutating(self) -> bool:
        """Guard against post-completion overwork on canvas tasks.

        Once the plan is marked complete and the requested output image exists,
        more GUI mutation usually means the agent is chasing optional polish and
        can damage a valid result. It should switch to deterministic verify/done.
        """
        if not _is_drawing_task(self.task):
            return False
        try:
            if not (self.session.plan and self.session.plan.all_done()):
                return False
        except Exception:
            return False
        if not any(step.startswith(("drag(", "draw_strokes(")) for step in self._steps_log):
            return False
        paths = _required_image_paths(self.task)
        return bool(paths) and any(Path(path).exists() for path in paths)

    def _maybe_expand_execute_subgoal_draws_from_plan(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return args
        if not isinstance(args.get("draw"), dict) or args.get("draws"):
            return args
        current_draw = dict(args.get("draw") or {})
        if current_draw.get("strokes"):
            return args
        current_sig = _draw_spec_signature(current_draw)
        if not all(current_sig):
            return args
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return args
        extras: list[dict[str, str]] = []
        skipped_current = False
        for step in steps:
            if not isinstance(step, dict) or step.get("done"):
                continue
            spec = _parse_plan_draw_spec(str(step.get("goal") or ""))
            if not spec:
                if skipped_current:
                    break
                continue
            sig = _draw_spec_signature(spec)
            if sig == current_sig and not skipped_current:
                skipped_current = True
                if not current_draw.get("expected_region") and spec.get("expected_region"):
                    current_draw["expected_region"] = spec["expected_region"]
                continue
            if not skipped_current:
                continue
            if sig != current_sig and spec not in extras:
                extras.append(spec)
            if len(extras) >= 3:
                break
        if not extras:
            return args
        expanded = dict(args)
        expanded["draw"] = current_draw
        expanded["draws"] = [current_draw, *extras]
        expanded["subgoal"] = (
            str(args.get("subgoal") or "Draw requested shapes")
            + " (combined adjacent drawing plan items)"
        )[:240]
        self._trace_debug("execute_subgoal_draws_auto_combined", {
            "run_id": self.run_id,
            "original": args,
            "expanded": expanded,
        })
        return expanded

    def _maybe_reuse_recent_draw_evidence(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return None
        if args.get("draws") or not isinstance(args.get("draw"), dict):
            return None
        requested = dict(args.get("draw") or {})
        if requested.get("strokes"):
            return None
        requested_sig = _draw_spec_signature(requested)
        if not all(requested_sig):
            return None
        requested_region = _draw_spec_region(requested)

        for previous_tool, previous_args, previous_text in reversed(self._tool_history[-12:]):
            if previous_tool != "execute_subgoal":
                continue
            previous_specs = []
            if isinstance(previous_args.get("draws"), list):
                previous_specs.extend(
                    item for item in previous_args.get("draws", [])
                    if isinstance(item, dict)
                )
            if isinstance(previous_args.get("draw"), dict):
                previous_specs.append(previous_args["draw"])
            if not any(
                _draw_spec_signature(spec) == requested_sig
                and (not requested_region or _draw_spec_region(spec) == requested_region)
                for spec in previous_specs
            ):
                continue
            try:
                payload = json.loads(previous_text or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            packet = payload.get("evidence_packet")
            if not isinstance(packet, dict):
                continue
            matched_colors = {
                str(color).casefold()
                for color in (packet.get("matched_visual_colors") or [])
            }
            if requested_sig[1] not in matched_colors:
                continue
            missing_colors = {
                str(color).casefold()
                for color in (packet.get("missing_visual_colors") or [])
            }
            if requested_sig[1] in missing_colors:
                continue
            actions = payload.get("actions_executed") or []
            if not any(
                isinstance(action, dict)
                and action.get("action") == "draw_strokes"
                and _draw_spec_signature({
                    "shape": action.get("shape"),
                    "color": action.get("requested_color"),
                }) == requested_sig
                for action in actions
            ):
                continue
            result_payload = {
                "ok": True,
                "verified": True,
                "subgoal": str(args.get("subgoal") or "Draw requested shape"),
                "subgoal_route": "controller_evidence_reuse",
                "reused_recent_evidence": True,
                "controller_state": {
                    "phase": "verified",
                    "verified": True,
                    "needs_observe": False,
                    "needs_llm_reason": False,
                },
                "actions_executed": [],
                "evidence_packet": {
                    "matched_texts": [],
                    "missing_texts": [],
                    "matched_visual_colors": [requested_sig[1]],
                    "missing_visual_colors": [],
                    "reused_from": {
                        "tool": previous_tool,
                        "shape": requested_sig[0],
                        "color": requested_sig[1],
                        "region": requested_region,
                    },
                },
                "needs_llm_reason": False,
                "fast_path_advice": (
                    "Recent controller evidence already covered this draw. "
                    "Complete the matching plan item instead of redrawing."
                ),
            }
            self._trace_debug("execute_subgoal_draw_evidence_reused", {
                "run_id": self.run_id,
                "requested": requested,
                "previous_args": previous_args,
                "result": result_payload,
            })
            return ToolResult(
                json.dumps(result_payload, ensure_ascii=False, default=str),
                replay={
                    "tool": "execute_subgoal",
                    "subgoal": result_payload["subgoal"],
                    "reused_recent_evidence": True,
                },
            )
        return None

    def _maybe_reuse_recent_visual_color_verify(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return None
        if any(args.get(key) for key in ("input", "click", "clicks", "draw", "draws")):
            return None
        raw_verify = args.get("verify_texts") or args.get("verify") or []
        if isinstance(raw_verify, str):
            verify_texts = [raw_verify]
        elif isinstance(raw_verify, list):
            verify_texts = [str(item or "") for item in raw_verify]
        else:
            verify_texts = []
        colors: list[str] = []
        for text in verify_texts:
            for color in _find_terms(text, _DRAW_COLOR_TERMS):
                if color not in colors:
                    colors.append(color)
        if not colors:
            return None
        matched = self._recent_matched_visual_colors(colors)
        missing = [color for color in colors if color.casefold() not in matched]
        if missing:
            return None
        payload = {
            "ok": True,
            "verified": True,
            "subgoal": str(args.get("subgoal") or "Verify visual colors"),
            "subgoal_route": "controller_visual_evidence_reuse",
            "reused_recent_evidence": True,
            "controller_state": {
                "phase": "verified",
                "verified": True,
                "needs_observe": False,
                "needs_llm_reason": False,
            },
            "actions_executed": [],
            "evidence_packet": {
                "matched_texts": [],
                "missing_texts": [],
                "matched_visual_colors": colors,
                "missing_visual_colors": [],
                "reused_from": "recent execute_subgoal visual color evidence",
            },
            "needs_llm_reason": False,
            "fast_path_advice": (
                "Recent controller visual evidence already matched these colors. "
                "Complete the matching visual verification plan item instead of OCR-reading color words."
            ),
        }
        self._trace_debug("execute_subgoal_visual_color_verify_reused", {
            "run_id": self.run_id,
            "args": args,
            "colors": colors,
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            replay={
                "tool": "execute_subgoal",
                "subgoal": payload["subgoal"],
                "reused_recent_visual_colors": colors,
            },
        )

    @staticmethod
    def _draw_specs_from_args(args: dict[str, Any]) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        if isinstance(args.get("draws"), list):
            specs.extend(item for item in args["draws"] if isinstance(item, dict))
        elif isinstance(args.get("draw"), dict):
            specs.append(args["draw"])
        return specs

    def _verified_draw_keys_from_result(
        self,
        args: dict[str, Any],
        payload: dict[str, Any],
    ) -> set[tuple[str, str, str]]:
        if payload.get("verified") is not True:
            return set()
        packet = payload.get("evidence_packet")
        if not isinstance(packet, dict):
            return set()
        matched_colors = {
            str(color).casefold()
            for color in (packet.get("matched_visual_colors") or [])
        }
        if not matched_colors:
            return set()
        action_sigs = {
            _draw_spec_signature({
                "shape": action.get("shape"),
                "color": action.get("requested_color"),
            })
            for action in (payload.get("actions_executed") or [])
            if isinstance(action, dict) and action.get("action") == "draw_strokes"
        }
        verified: set[tuple[str, str, str]] = set()
        for spec in self._draw_specs_from_args(args):
            if spec.get("strokes"):
                continue
            sig = _draw_spec_signature(spec)
            if not all(sig):
                continue
            if sig in action_sigs and sig[1] in matched_colors:
                verified.add(_draw_spec_key(spec))
        return verified

    def _maybe_auto_complete_draw_plan_items(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return result
        if not isinstance(args.get("draws"), list) or len(args.get("draws") or []) < 2:
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict):
            return result
        verified_keys = self._verified_draw_keys_from_result(args, payload)
        if not verified_keys:
            return result
        verified_sigs = {(shape, color) for shape, color, _region in verified_keys}
        completed: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done") or step.get("check"):
                continue
            spec = _parse_plan_draw_spec(str(step.get("goal") or ""))
            if not spec:
                continue
            key = _draw_spec_key(spec)
            sig = _draw_spec_signature(spec)
            region = _draw_spec_region(spec)
            if key not in verified_keys and not (not region and sig in verified_sigs):
                continue
            if plan.complete(index):
                item = {"index": index, "goal": str(step.get("goal") or "")[:160]}
                completed.append(item)
                BUS.publish(
                    "agent_plan",
                    action="auto_complete",
                    index=index,
                    source="execute_subgoal.draws",
                    plan=plan.snapshot(),
                )
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        existing_advice = str(payload.get("fast_path_advice") or "").strip()
        auto_advice = (
            "Auto-completed drawing plan items "
            + ", ".join(str(item["index"]) for item in completed)
            + " from verified controller draw evidence; continue with next unfinished item."
        )
        payload["fast_path_advice"] = (existing_advice + " " + auto_advice).strip()
        self._trace_debug("execute_subgoal_draw_plan_auto_completed", {
            "run_id": self.run_id,
            "completed": completed,
            "args": args,
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal=result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    def _score_experience_feedback(self) -> None:
        """Score memory only when it was actually tried, not merely injected.

        Hints in the prompt are weak references; a successful run may have
        ignored them entirely. Positive feedback is therefore reserved for
        recipes whose reuse_skill replay succeeded and whose run also ended
        done. Failed replay attempts are negative even if live fallback later
        finishes the task. Failed/error runs with no replay still penalize
        injected auto-recipes, preserving the old poison-control behavior for
        bad hints.
        """
        if self.status not in ("done", "fail", "error"):
            return
        attempts = [
            attempt for attempt in getattr(self.session, "memory_recipe_attempts", [])
            if isinstance(attempt, dict) and str(attempt.get("id") or "")
        ]
        replay_ok_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts if attempt.get("replay_ok")
        ))
        replay_failed_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts
            if not attempt.get("replay_ok") and str(attempt["id"]) not in replay_ok_ids
        ))
        attempted_ids = list(dict.fromkeys(
            str(attempt["id"]) for attempt in attempts
        ))
        if self.status == "done":
            if replay_ok_ids:
                experience.feedback(replay_ok_ids, success=True)
            if replay_failed_ids:
                experience.feedback(replay_failed_ids, success=False)
        elif attempted_ids:
            experience.feedback(attempted_ids, success=False)
        else:
            experience.feedback(self._hint_ids, success=False)

    def _requires_run_attempt(self) -> bool:
        """True for coding clone/open-source tasks that must actually be run.

        This gate used to live in the old CodingRunner. It now belongs to the
        unified AgentRunner so coding tasks keep the same anti-fake-success
        behavior while sharing the same trace, planner, and done gate as GUI
        tasks.
        """
        if not self._is_coding:
            return False
        text = self.task.lower()
        has_repo = any(k in text for k in (
            "clone", "github", "repo", "repository", "open-source", "opensource",
            "克隆", "开源", "仓库",
        ))
        wants_run = any(k in text for k in (
            "run", "start", "launch", "serve", "build", "test", "verify",
            "跑起来", "运行", "启动", "打开", "构建", "测试", "验证",
        ))
        return has_repo and wants_run

    def _has_run_attempt(self) -> bool:
        markers = (
            "npm", "pnpm", "yarn", "bun", "node ", "python", "pip", "pytest",
            "cargo", "go ", "dotnet", "mvn", "gradle", "make", "cmake",
            "get-command", "--version", "test-netconnection", "netstat",
            "get-process", "invoke-webrequest", "curl", "start-process",
        )
        for name, args, _out in self._tool_history:
            if name != "run_shell":
                continue
            cmd = str(args.get("command") or "").lower()
            if "git clone" in cmd and not any(m in cmd for m in markers if m != "get-command"):
                continue
            if any(marker in cmd for marker in markers):
                return True
        return False

    def _reject_incomplete_run_task(self) -> bool:
        if not self._requires_run_attempt() or self._has_run_attempt():
            return False
        if self._completion_rejects >= 2:
            return False
        self._completion_rejects += 1
        self.messages.append({
            "role": "user",
            "content": (
                "[completion check failed] The user asked to clone and run/start/verify the project. "
                "You have not actually attempted dependency/runtime checks, install/build/run, or "
                "deterministic verification. Do not just summarize README commands. Continue with "
                "run_shell checks such as Get-Command for the required runtime, then install/build/run "
                "or fail with the exact missing prerequisite/output."
            ),
        })
        BUS.publish(
            "done_check",
            run_id=self.run_id,
            passed=False,
            reason="clone/run task has no real run/build/runtime verification attempt",
        )
        return True

    def _handle_tool_calls(self, tool_calls) -> str | None:
        """Dispatch each tool call; returns terminal status if any."""
        post_tool_hints: list[str] = []
        for tc in tool_calls:
            if self.stop_event.is_set():
                return "stopped"
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("参数必须是 JSON 对象")
            except (json.JSONDecodeError, ValueError) as exc:
                error = f"工具参数 JSON 解析失败: {exc}。请重新调用并给出合法 JSON。"
                self._append_tool_result(tc.id, error)
                self.run_state.before_tool(tc.function.name, {})
                self.run_state.after_tool(
                    tc.function.name,
                    {},
                    json.dumps({"ok": False, "error": error}, ensure_ascii=False),
                )
                BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                self._trace_debug("tool_arg_error", {
                    "run_id": self.run_id,
                    "tool_call_id": tc.id,
                    "tool": tc.function.name,
                    "raw_arguments": tc.function.arguments,
                    "error": str(exc),
                })
                BUS.publish("agent_tool", run_id=self.run_id, tool=tc.function.name,
                            error=str(exc))
                continue

            args = self._maybe_expand_execute_subgoal_draws_from_plan(tc.function.name, args)
            self.run_state.before_tool(tc.function.name, args)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            BUS.publish("agent_tool_start", run_id=self.run_id,
                        tool=tc.function.name, args=args)
            started = time.monotonic()
            blocked_store_action = _blocks_store_search_action(self.task, tc.function.name, args)
            blocked_upload_action = _blocks_upload_media_analysis(self.task, tc.function.name, args)
            blocked_gui_only_action = ""
            if _is_gui_only_task(self.task):
                strict_no_shell = _gui_only_forbids_shell(self.task)
                command = str(args.get("command", ""))
                condition = args.get("condition")
                if (
                    tc.function.name in {
                        "run_shell", "run_detached", "write_file", "read_file", "list_dir",
                        "apply_patch", "search_files",
                    }
                    and not (
                        tc.function.name == "run_shell"
                        and not strict_no_shell
                        and _gui_only_shell_allowed(command)
                    )
                ):
                    blocked_gui_only_action = (
                        f"GUI-only mode blocked {tc.function.name}: use GUI tools "
                        "such as launch/focus_window/observe/click/type_text/hotkey "
                        "for the work. Shell is allowed only for final Test-Path/"
                        "Get-Content/Select-String verification when the user did not forbid shell."
                    )
                elif tc.function.name == "launch" and _gui_only_launch_uses_shell(command):
                    blocked_gui_only_action = (
                        "GUI-only mode blocked launching a shell command. Launch the target app/URL "
                        "directly, then use focus_window/observe/click/type_text/hotkey."
                    )
                elif (
                    tc.function.name == "wait"
                    and strict_no_shell
                    and isinstance(condition, dict)
                    and condition.get("shell_true")
                ):
                    blocked_gui_only_action = (
                        "GUI-only mode blocked wait(shell_true) because the user forbade shell. "
                        "Use observe/read_text or GUI waits instead."
                    )
            if blocked_store_action or blocked_upload_action:
                result = ToolResult(blocked_store_action or blocked_upload_action)
            elif (
                self._drawing_should_stop_mutating()
                and tc.function.name in {"click", "drag", "draw_strokes", "type_text", "hotkey", "scroll"}
            ):
                result = ToolResult(
                    "Drawing/canvas task appears complete: all plan steps are marked done, "
                    "the requested output image exists, and visible drag evidence was recorded. "
                    "Do not keep mutating the canvas for optional polish. Use final Test-Path/"
                    "file_exists verification if needed, then call done."
                )
            elif blocked_gui_only_action:
                result = ToolResult(blocked_gui_only_action)
            else:
                result = self._maybe_reuse_recent_visual_color_verify(tc.function.name, args)
                if result is None:
                    result = self._maybe_reuse_recent_draw_evidence(tc.function.name, args)
                if result is None:
                    result = self._dispatch_with_fast_visual_wrap(tc.function.name, args)
            result = self._maybe_auto_complete_draw_plan_items(tc.function.name, args, result)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            state_terminal = None if result.terminal == "done" else result.terminal
            self.run_state.after_tool(tc.function.name, args, result.text, state_terminal)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            self._trace_debug("tool_result", {
                "run_id": self.run_id,
                "tool_call_id": tc.id,
                "tool": tc.function.name,
                "args": args,
                "result_text": result.text,
                "summary": result.summary,
                "terminal": result.terminal,
                "replay": result.replay,
                "elapsed_ms": elapsed_ms,
            })
            self._record_step(tc.function.name, args, result.replay)
            self._track_verification(tc.function.name, result.text)
            self._track_visible_evidence(tc.function.name, args, result.text)
            self._tool_history.append((tc.function.name, args, result.text or ""))
            self._tool_history = self._tool_history[-40:]
            # Keep recent tool outputs as evidence for the done-gate judge
            # (esp. run_shell stdout / verify results — deterministic proof
            # the GUI screen can't show).
            evidence_tools = {
                "run_shell",
                "run_detached",
                "search_files",
                "list_dir",
                "read_file",
                "search_text",
                "github_search",
                "apply_patch",
                "write_file",
                "list_windows",
                "scan_windows",
                "read_text",
                "find_app",
                "find_program",
                "run_action_batch",
                "execute_subgoal",
                "zoom",
                "screenshot",
            }
            if (
                tc.function.name in evidence_tools
                or '"verified"' in (result.text or "")
                or "visual_change_detected" in (result.text or "")
            ):
                if tc.function.name in {"run_action_batch", "execute_subgoal", "zoom", "screenshot", "draw_strokes", "drag"}:
                    evidence_text = self._compact_tool_evidence(tc.function.name, result.text)
                else:
                    limit = 1200 if tc.function.name in {"list_windows", "scan_windows", "read_text"} else 600
                    evidence_text = (result.text or "")[:limit]
                self._recent_results.append((tc.function.name, evidence_text))
                self._recent_results = self._recent_results[-6:]
            result_meta: dict[str, Any] = {}
            try:
                parsed_result = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed_result = {}
            if isinstance(parsed_result, dict):
                if tc.function.name == "execute_subgoal":
                    packet = parsed_result.get("evidence_packet")
                    if isinstance(packet, dict):
                        candidates = [
                            str(item)
                            for item in (packet.get("candidate_click_labels") or [])
                            if str(item or "").strip()
                        ][:8]
                        if parsed_result.get("needs_llm_reason") and candidates:
                            post_tool_hints.append(
                                "[controller recovery] execute_subgoal could not match the requested "
                                "anchor label, but the target region has visible candidate labels: "
                                f"{', '.join(candidates)}. Retry execute_subgoal with the best "
                                "semantically matching visible/localized label and the same verify_texts "
                                "before doing a full observe or any shell/source-code inspection."
                            )
                for key in (
                    "ok",
                    "verified",
                    "wrapped_by_fast_controller",
                    "visual_change_detected",
                    "visual_change_ratio",
                    "screen_delta",
                    "screen_watch",
                    "action_target",
                    "fast_path_advice",
                    "needs_observe",
                    "last_action",
                    "metrics",
                    "subgoal",
                    "subgoal_route",
                    "controller_state",
                    "evidence_packet",
                    "auto_completed_plan_items",
                    "plan_after_auto_complete",
                    "needs_llm_reason",
                    "element_cache",
                ):
                    if key in parsed_result:
                        result_meta[key] = parsed_result[key]
            BUS.publish(
                "agent_tool",
                run_id=self.run_id,
                tool=tc.function.name,
                args=args,
                result_preview=result.text[:300],
                result_meta=result_meta,
                elapsed_ms=elapsed_ms,
                terminal=None if result.terminal == "done" else result.terminal,
                replay=result.replay,
            )
            self._append_tool_result(tc.id, result.text, summary=result.summary, tool_name=tc.function.name)
            if result.is_observe:
                # Inject screenshot as separate user message (mime does not support
                # multipart tool results, so we cannot inline image_b64 in tool role).
                self._inject_observe_screenshot()
            if result.terminal:
                if result.terminal == "done" and self._reject_incomplete_run_task():
                    continue
                self.result_text = result.text
                return result.terminal
        for hint in post_tool_hints[-2:]:
            self.messages.append({"role": "user", "content": hint})
        return None

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        self.status = "running"
        max_steps = int(
            self.max_steps_override
            or self.runtime_profile.max_steps
            or CONFIG.get("agent", "max_steps", default=40)
        )
        plan_on = planning_enabled()
        # Coding tasks are excluded from action-replay reuse, but relevant
        # factual project notes (manual 'remember' entries) still help — inject
        # them read-only (empty hint_ids → never scored/evicted by this run).
        if self._is_coding:
            hints, self._hint_ids = experience.note_hints(self.task), []
        else:
            hints, self._hint_ids = experience.hints_with_ids(self.task)
        matched_books = playbooks.for_task(self.task)
        self._injected_playbooks = {b.name for b in matched_books}
        system = prompts.system_prompt(
            experience_hints=hints, vlm_enabled=vlm.is_configured(),
            planning_enabled=plan_on,
            playbook_text=playbooks.render_section(matched_books),
            style=str(CONFIG.get("agent", "prompt_style", default="full")),
            main_model_has_vision=_main_model_has_vision(),
            runtime_section=self.runtime_profile.prompt_section())
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": self.task},
        ]
        if _is_communication_task(self.task):
            payloads = _quoted_payloads(self.task)
            wants_decor = any(k in (self.task or "").lower() for k in (
                "表情", "emoji", "sticker", "表情包", "贴纸", "emoticon",
            ))
            if payloads:
                core = payloads[0][:80]
                extra = "如果能清楚看到表情/贴纸入口，再额外尝试一次装饰。" if wants_decor else ""
                hint_body = (
                    f"先把要发送的正文 '{core}' 真正送达并重新 observe/read_text 验证。"
                    "如果界面里正文已经出现在输入框，就优先完成发送，不要为了装饰项在发送区附近反复试探模糊图标。"
                    f"{extra}"
                )
            else:
                hint_body = (
                    "先打开目标联系人的聊天窗口，确认输入框可用，输入消息并发送。"
                    "发送后调用 read_text 确认消息出现在聊天记录中，再调用 done。"
                    "done 前必须有 read_text 的输出证据。"
                )
            self.messages.append({
                "role": "user",
                "content": f"[通信任务提示] {hint_body}",
            })
        # Session continuity: let referential follow-ups ("再来一次" etc.) resolve
        # against prior tasks this session. Injected right after the task so it's
        # in the cache-friendly prefix; gated by config.
        if _is_drawing_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Drawing/canvas task guidance] The goal is visible pixels. After the target app/window "
                    "is open, prefer execute_subgoal with draw={'shape':'triangle','color':'blue'} or "
                    "draws=[{'shape':'triangle','color':'blue','expected_region':'left'}, "
                    "{'shape':'rectangle','color':'red','expected_region':'right'}]. The controller waits "
                    "for a ready canvas, selects requested colors when possible, draws, and returns "
                    "matched_visual_colors/visible_colors. "
                    "If several requested shapes/colors are already known, combine them in one draws[] "
                    "routine and then complete the affected plan items from that controller evidence. "
                    "Do not pre-explore toolbar text or add "
                    "text_appears checks for tool names like Pencil/Brush/Triangle. Use draw_strokes/"
                    "run_action_batch only when coordinates are already grounded or execute_subgoal "
                    "returns needs_llm_reason. Verify by pixel/color evidence, screenshot/nonvisual "
                    "summary, and requested file existence if saving is required. Do not redraw just "
                    "for polish; if verified colors/pixels are present, complete the plan or done."
                ),
            })
        if _is_upload_publish_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Upload/publish task guidance] Treat the media path as a file to select in an "
                    "upload form, not content to play or analyze. Start by observing/focusing the "
                    "target app or creator/upload page. Use upload/post/publish-entry UI, choose the "
                    "file in the file picker, fill requested title/metadata, and stop before final "
                    "publish when requested. If login, QR, face, or phone verification blocks the "
                    "flow, report that exact blocker."
                ),
            })
        if _is_gui_only_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[GUI-only task guidance] The user explicitly asked for visible GUI operation. "
                    "Do not use shell/search_files/run_detached/read_file/write_file to perform or "
                    "prepare the work, and do not launch PowerShell/cmd as a workaround. "
                    "Use list_windows/find_app/find_program/launch/focus_window/"
                    "observe/click/type_text/hotkey/drag/scroll. Shell is only for final read-only "
                    "verification such as Test-Path/Get-Content/Select-String after the GUI work."
                ),
            })
        if (
            self.session_context
            and CONFIG.get("agent", "session_memory", default=True)
            and session_mod.should_include_context(self.task)
        ):
            ctx = session_mod.render_context(self.session_context)
            if ctx:
                self.messages.append({"role": "user", "content": ctx})
        # Proactive reuse: when this task strongly matches a past run that left a
        # verified replay script, direct the agent to reuse it first (it's the
        # whole point of skills; replay failure auto-falls-back to live actions).
        # Skipped for coding tasks — they are never in the reusable scope.
        if not self._is_coding:
            try:
                script, recipe = experience.reusable_script_for(self.task)
                if script and recipe:
                    self.messages.append({
                        "role": "user",
                        "content": (
                            f"⚡ 本任务与上次成功完成的「{recipe['task']}」高度一致，已有可复用流程脚本。"
                            f"**第一步请直接调用 reuse_skill 复用**（0-token 重放上次步骤）；"
                            "若重放中途某步失败，它会告诉你卡在哪，你再从那步起实时操作。"),
                    })
                    BUS.publish("memory", action="reuse_suggested", task=recipe["task"])
            except Exception as exc:
                logger.debug("复用建议注入失败: %s", exc)
        allow_shell_tools = not (
            _is_gui_only_task(self.task)
            and _gui_only_forbids_shell(self.task)
        )
        tools = tool_schemas(
            planning=plan_on,
            coding_runtime=self._is_coding,
            allow_shell=allow_shell_tools,
            allow_files=allow_shell_tools,
        )
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, has_experience=bool(hints),
                    agent="unified", runtime=self.runtime_profile.to_dict())
        try:
            raw_client = _client()
            client = build_model_adapter(raw_client, CONFIG.get("api", default={}))
            self._model_adapter = client
            self._maybe_plan_upfront(client)
            for step in range(1, max_steps + 1):
                if self.stop_event.is_set():
                    self.status = "stopped"
                    break
                self._drain_interjections()
                resp = self._chat(client, tools)
                if resp is None:        # Stop requested mid LLM-call → abandon
                    self.status = "stopped"
                    break
                self._track_usage(resp)
                msg = resp.choices[0].message
                reasoning = getattr(msg, "reasoning_content", None) or ""
                self.messages.append(_sanitize_assistant(msg))

                if not msg.tool_calls:
                    # Plain-text reply — the final answer. Emit it ONCE via the
                    # chat event (carrying any reasoning). The step event stays
                    # content-less so the chat pane doesn't show it twice.
                    self.result_text = msg.content or ""
                    BUS.publish("agent_step", run_id=self.run_id, step=step,
                                reasoning="", content="", n_tool_calls=0)
                    if self._reject_incomplete_run_task():
                        BUS.publish("chat", run_id=self.run_id, role="assistant",
                                    content=self.result_text[:500], reasoning=reasoning[:800])
                        continue
                    if self._recent_results:
                        if not self._verify_done(client, steps_left=max_steps - step):
                            continue
                        self.status = "done"
                    else:
                        self.status = "reply"
                    BUS.publish("chat", run_id=self.run_id, role="assistant",
                                content=self.result_text, reasoning=reasoning[:800])
                    break

                BUS.publish(
                    "agent_step",
                    run_id=self.run_id,
                    step=step,
                    reasoning=reasoning[:800],
                    content=(msg.content or "")[:800],
                    n_tool_calls=len(msg.tool_calls),
                )

                terminal = self._handle_tool_calls(msg.tool_calls)
                # Track consecutive steps without a verified action for reflect.
                if self._last_verify_ok is True:
                    self._consecutive_unverified = 0
                elif self._last_verify_ok is False or self._last_verify_ok is None:
                    # None means no verify-capable tool was called this step.
                    # Only increment on action tools, not purely observe steps.
                    action_names = {tc.function.name for tc in msg.tool_calls}
                    if action_names & _ACTION_TOOLS:
                        self._consecutive_unverified += 1
                if terminal == "done" and not self._verify_done(client, steps_left=max_steps - step):
                    self.run_state.terminal_rejected("done", self._last_done_reject_reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    continue  # done-gate rejected the claim → keep working
                if terminal:
                    self.status = terminal
                    break
                self._surface_world_advisory()
                self._maybe_inject_playbook()
                self._maybe_reflect(step)
            else:
                self.status = "fail"
                self.result_text = f"达到最大步数 {max_steps} 仍未完成"
        except Exception as exc:
            logger.exception("Agent 运行异常")
            self.status = "error"
            self.result_text = str(exc)
            BUS.publish("agent_error", run_id=self.run_id, error=str(exc))

        # Record a reusable recipe (+ export a replayable .wps.json) ONLY for
        # non-coding successes. Coding tasks are excluded from the reusable
        # scope entirely — solved fresh next time, guided by the playbook.
        if self.status == "done" and not self._is_coding:
            app_hint = self.session.snapshot.title if self.session.snapshot else ""
            # Trusted (→ reusable skill + linked script) when EITHER the done-gate
            # judge independently confirmed success, OR an action passed external
            # verify; and never when done was force-accepted after max rejects.
            verified_run = (
                (self._done_verified
                 or (self._had_verified_action and self._last_verify_ok is not False))
                and not self._done_unverified)
            # Reuse-like-scripts: a trusted success becomes a replayable .wps.json
            # linked to the recipe, so a future identical task can reuse_skill it.
            script_name = ""
            if verified_run:
                try:
                    from ..recorder import trace as _trace
                    script_path = _trace.export_script(self.run_id)
                    script_name = script_path.name
                except (FileNotFoundError, ValueError) as exc:
                    logger.debug("成功流程无可重放步骤，跳过导出: %s", exc)
                except OSError as exc:
                    logger.warning("成功流程导出脚本失败(IO): %s", exc)
            experience.record(self.task, app_hint, self._steps_log,
                              verified=verified_run, script=script_name)
        self._score_experience_feedback()

        try:
            self.session.close()
        except Exception:
            logger.debug("ToolSession close failed", exc_info=True)

        BUS.publish(
            "agent_done",
            run_id=self.run_id,
            status=self.status,
            result=self.result_text[:500],
            steps=len(self._steps_log),
            usage=dict(self.usage_total),
        )
        return {
            "run_id": self.run_id,
            "status": self.status,
            "result": self.result_text,
            "usage": dict(self.usage_total),
        }
