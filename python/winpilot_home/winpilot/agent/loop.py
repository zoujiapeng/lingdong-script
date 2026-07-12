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
from ..memory import compiler, experience
from ..model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
)
from ..perception import fusion, intent, vlm
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS, logger
from ..utils.screenshot import window_region
from . import playbooks, prompts, session as session_mod
from .context_guidance import ContextGuidanceMixin
from .done_verifier import (
    DoneVerifierMixin,
    _deterministic_arithmetic_verdict,
    _docx_document_evidence,
    _docx_task_format_errors,
    _explicit_saved_paths,
)
from .failure_recovery import FailureRecoveryLayer, attach_recovery_metadata
from .gui_plan_helpers import (
    _DRAW_COLOR_TERMS,
    _draw_spec_key,
    _draw_spec_region,
    _draw_spec_signature,
    _drawing_plan_check_allows_visual_completion,
    _drawing_task_requests_file_output,
    _drawing_visual_confirmation_colors,
    _drawing_visual_confirmation_goal,
    _extract_draw_specs_from_task_text,
    _find_terms,
    _looks_like_pure_open_plan_goal,
    _parse_plan_draw_spec,
    _schema_profile_for_task,
)
from .planner import planning_enabled
from .reflection import ReflectionMixin
from .run_state import ACTION_TOOLS as _RUN_ACTION_TOOLS, RunStateMachine
from .runtime_profile import RuntimeProfile, build_runtime_profile
from .task_rules import (
    _communication_mode,
    _document_format_specs as _task_document_format_specs,
    _exact_document_text_block as _task_exact_document_text_block,
    _blocks_store_search_action,
    _blocks_upload_media_analysis,
    _gui_only_forbids_shell,
    _gui_only_launch_uses_shell,
    _gui_only_shell_allowed,
    _is_coding_task,
    _is_communication_task,
    _is_desktop_icon_drag_task,
    _is_drawing_task,
    _is_arithmetic_task,
    _is_gui_only_task,
    _is_multi_source_research_task,
    _is_upload_publish_task,
    _quoted_payloads,
    _required_image_paths,
    _required_literal_tokens,
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


def _clipboard_target_tokens(task: str) -> list[str]:
    """Return placeholder tokens requested as destinations, not protected literals."""
    text = str(task or "")
    targets: list[str] = []
    all_tokens = list(dict.fromkeys(re.findall(r"\[\[[^\]\r\n]{1,100}\]\]", text)))
    for token in all_tokens:
        position = text.find(token)
        left_boundary = max(
            text.rfind(marker, 0, position) for marker in (".", "。", ";", "；", "\n")
        )
        right_candidates = [
            index for marker in (".", "。", ";", "；", "\n")
            if (index := text.find(marker, position + len(token))) >= 0
        ]
        right_boundary = min(right_candidates) if right_candidates else len(text)
        clause = text[left_boundary + 1:right_boundary]
        mutates = bool(re.search(
            r"replace|paste|insert|fill|write|替换|粘贴|填入|写入",
            clause,
            re.IGNORECASE,
        ))
        protects = bool(re.search(
            r"keep|preserve|unchanged|do not (?:change|replace)|保留|保持|不变|不要(?:修改|替换)",
            clause,
            re.IGNORECASE,
        ))
        if mutates and not protects:
            targets.append(token)
    return targets or all_tokens


def _clipboard_fingerprints(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        fingerprint = value.get("clipboard_text_fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            found.add(fingerprint)
        for nested in value.values():
            found.update(_clipboard_fingerprints(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_clipboard_fingerprints(nested))
    return found


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
                 max_steps_override: int | None = None,
                 runtime_profile: RuntimeProfile | None = None,
                 allow_shell_tools: bool | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.runtime_profile = runtime_profile or build_runtime_profile(task)
        self.session = ToolSession(
            stop_event=self.stop_event,
            task=task,
            runtime_profile=self.runtime_profile,
        )
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
        self.session.visible_evidence_ledger = self._visible_evidence_ledger
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
        self.allow_shell_tools = allow_shell_tools
        self.run_state = RunStateMachine()
        self.session.run_state = self.run_state
        # Coding is now an in-process runtime capability of this AgentRunner,
        # not a separate sub-agent / runner loop.
        self._is_coding = self.runtime_profile.coding
        self._tool_history: list[tuple[str, dict[str, Any], str]] = []
        self._completion_rejects = 0
        self._truncated_response_retries = 0
        self._failure_recovery_layer = FailureRecoveryLayer()
        self._started_wall_time = time.time()
        self._new_document_plan_state: dict[int, dict[str, Any]] = {}
        self._delivered_text_mutations: list[dict[str, Any]] = []
        self._applied_format_mutations: list[dict[str, Any]] = []
        self._tool_sequence = 0

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
        if tool_name and tool_name not in {"observe", "read_text"}:
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
            region = window_region(hwnd)
            max_side = 1120
            scale = min(1.0, max_side / max(1, region.width, region.height))
            submitted_width = max(1, int(region.width * scale))
            submitted_height = max(1, int(region.height * scale))
            self.messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "[当前窗口截图；结合上方文字元素列表使用。截图是目标窗口裁剪，不是全屏。"
                            f"物理屏幕区域 left={region.left}, top={region.top}, "
                            f"width={region.width}, height={region.height}；"
                            f"提交图片约 {submitted_width}x{submitted_height}。"
                            "若从图片估计相对坐标，必须按窗口区域缩放并加 left/top，"
                            "最终 click 坐标始终是物理屏幕坐标。]"
                        ),
                    },
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
        decision_started = time.monotonic()
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
                BUS.publish(
                    "agent_decision",
                    run_id=self.run_id,
                    model=model,
                    ok=False,
                    canceled=True,
                    elapsed_ms=int((time.monotonic() - decision_started) * 1000),
                )
                return None  # abandon the in-flight request (it finishes unused)
            thread.join(timeout=0.2)
        if "exc" in box:
            self._trace_debug("llm_error", {
                "run_id": self.run_id,
                "model": model,
                "error": str(box["exc"]),
            })
            BUS.publish(
                "agent_decision",
                run_id=self.run_id,
                model=model,
                ok=False,
                elapsed_ms=int((time.monotonic() - decision_started) * 1000),
            )
            raise box["exc"]
        if box.get("resp") is not None:
            self._trace_debug("llm_response", {
                "run_id": self.run_id,
                "model": model,
                "response": box.get("resp"),
            })
        BUS.publish(
            "agent_decision",
            run_id=self.run_id,
            model=model,
            ok=box.get("resp") is not None,
            elapsed_ms=int((time.monotonic() - decision_started) * 1000),
        )
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

    def _track_aux_model_response(self, resp, *, purpose: str, model: str) -> None:
        previous_model = self._last_llm_model
        self._last_llm_model = str(model or previous_model or "")
        try:
            self._track_usage(resp)
        finally:
            self._last_llm_model = previous_model
        BUS.publish(
            "agent_model_call",
            run_id=self.run_id,
            purpose=str(purpose),
            model=str(model),
            accounted=True,
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
        targets = [
            *_visible_state_targets(self.task),
            *_required_visible_payloads(self.task),
            *_required_literal_tokens(self.task),
        ]
        plan = getattr(self.session, "plan", None)
        for step in (getattr(plan, "steps", None) or []):
            if not isinstance(step, dict) or step.get("done"):
                continue
            check = step.get("check")
            if not isinstance(check, dict):
                continue
            for key in ("text_appears", "text_contains", "window_title", "window_appears"):
                value = check.get(key)
                if isinstance(value, str) and value.strip():
                    targets.append(value.strip())
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
        matched_task_echo = False
        metadata_markers = (
            "why=", "perception trace:", "screen watch bulletin:",
            "uncertainties:", "recommended_next_controller_actions:",
            "recent_changes=", "top_candidates:", "raw element sample",
        )
        for line in lines:
            if target_norm and target_norm in self._normalize_visible_text(line):
                if any(marker in line.casefold() for marker in metadata_markers):
                    continue
                if any(marker in line.casefold() for marker in negative_markers):
                    matched_negative = True
                    continue
                if self._looks_like_task_echo_line(line, target):
                    matched_task_echo = True
                    continue
                return line[:limit]
        if matched_negative or matched_task_echo:
            return ""
        return ""

    def _looks_like_task_echo_line(self, line: str, target: str) -> bool:
        """Reject a control surface echoing the user's own command as evidence."""
        line_norm = self._normalize_visible_text(line)
        target_norm = self._normalize_visible_text(target)
        task_norm = self._normalize_visible_text(self.task)
        if not line_norm or not target_norm or target_norm not in line_norm:
            return False
        if len(line_norm) < max(24, len(target_norm) * 2):
            return False
        return line_norm in task_norm or task_norm in line_norm

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
            "screen_epoch": int(getattr(self.session, "_screen_epoch", 0) or 0),
            "tool_sequence": int(self._tool_sequence),
            "hwnd": int(getattr(self.session, "hwnd", 0) or 0),
            "at": time.time(),
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
            if data.get("ok") is not True or data.get("verified") is not True:
                return
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
            if not (isinstance(data, dict) and tool_name == "run_action_batch"):
                return
        if isinstance(data, dict) and tool_name == "run_action_batch":
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("action") not in {"observe", "read_text"}:
                    continue
                preview = str(item.get("preview") or "")
                child_data = item.get("data") if isinstance(item.get("data"), dict) else {}
                if isinstance(child_data, dict):
                    preview = "\n".join(
                        part for part in [
                            preview,
                            str(child_data.get("text") or ""),
                            str(child_data.get("body") or ""),
                            str(child_data.get("visible_text") or ""),
                        ]
                        if part
                    )
                haystack = self._normalize_visible_text(preview)
                for target in targets:
                    if self._normalize_visible_text(target) in haystack:
                        self._append_visible_evidence(
                            f"run_action_batch:{item.get('action')}",
                            target,
                            self._visible_snippet(preview, target),
                        )
            return
        for value in self._expect_visible_values(args):
            self._append_visible_evidence(
                f"{tool_name}:verified_expect",
                value,
                f"verified expect text appeared: {value}",
            )

    @staticmethod
    def _strong_auto_done_target(target: str) -> bool:
        value = str(target or "").strip()
        if len(value) < 4:
            return False
        return (
            "_" in value
            or any(ch.isdigit() for ch in value)
            or (len(value) >= 6 and value.upper() == value and any(ch.isalpha() for ch in value))
        )

    def _auto_done_visible_targets(self) -> list[str]:
        if _is_arithmetic_task(self.task):
            return []
        return [
            target
            for target in self._visible_evidence_targets()
            if self._strong_auto_done_target(target)
        ]

    @staticmethod
    def _auto_done_evidence_source(source: str) -> bool:
        return source in {"observe", "read_text", "execute_subgoal:evidence_packet"} or source in {
            "run_action_batch:observe",
            "run_action_batch:read_text",
        }

    def _visible_targets_proven_by_controller_evidence(self, targets: list[str]) -> tuple[bool, list[str]]:
        seen = {
            self._normalize_visible_text(str(item.get("target") or ""))
            for item in self._visible_evidence_ledger
            if self._auto_done_evidence_source(str(item.get("source") or ""))
        }
        missing = [
            target
            for target in targets
            if self._normalize_visible_text(target) not in seen
        ]
        return not missing, missing

    @staticmethod
    def _controller_observation_text(result_text: str) -> str:
        """Extract only post-action observe/read_text evidence from a batch."""
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        packet = payload.get("evidence_packet")
        if isinstance(packet, dict):
            visible = str(packet.get("visible_text_preview") or "").strip()
            if visible:
                parts.append(visible)
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or item.get("action") not in {"observe", "read_text"}:
                continue
            text = str(item.get("full_text") or item.get("preview") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _controller_has_successful_mutation(result_text: str) -> bool:
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        mutation_actions = {
            "click", "click_element", "type_text", "type_into_element", "hotkey",
            "drag", "draw_strokes", "scroll", "clipboard_paste", "select_color",
        }
        return any(
            isinstance(item, dict)
            and item.get("action") in mutation_actions
            and item.get("ok") is True
            for item in (payload.get("results") or [])
        )

    def _maybe_auto_done_from_visible_evidence(
        self,
        tool_name: str,
        result: ToolResult,
        ledger_len_before: int,
    ) -> ToolResult:
        if self._is_coding or result.terminal:
            return result
        if tool_name not in {"observe", "read_text", "execute_subgoal", "run_action_batch"}:
            return result
        if _is_arithmetic_task(self.task):
            if tool_name in {"execute_subgoal", "run_action_batch"}:
                mutation_seen = self._controller_has_successful_mutation(result.text)
            else:
                mutation_seen = any(
                    (
                        history_tool in {"execute_subgoal", "run_action_batch"}
                        and self._controller_has_successful_mutation(history_text)
                    )
                    or (
                        history_tool in {"type_text", "hotkey", "click"}
                        and '"ok": true' in str(history_text or "").casefold()
                    )
                    for history_tool, _history_args, history_text in self._tool_history
                )
            if mutation_seen:
                # Observe/read_text already carry the newly sampled state.  A
                # controller result needs an independent fresh sample because
                # its compact packet may omit the display value.
                if tool_name in {"observe", "read_text"}:
                    screen = result.text
                else:
                    screen = self._controller_observation_text(result.text)
                    if not screen:
                        screen = self._fresh_done_screen()
                arithmetic_verdict = _deterministic_arithmetic_verdict(
                    self.task,
                    screen,
                )
                if arithmetic_verdict is not None and arithmetic_verdict[0]:
                    reason = arithmetic_verdict[1]
                    self._done_verified = True
                    self.run_state.accept_verification(tool_name, reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    BUS.publish(
                        "done_check",
                        run_id=self.run_id,
                        passed=True,
                        reason=reason,
                    )
                    try:
                        arithmetic_payload = json.loads(result.text or "{}")
                    except (json.JSONDecodeError, TypeError):
                        arithmetic_payload = {}
                    if not isinstance(arithmetic_payload, dict):
                        arithmetic_payload = {}
                    arithmetic_payload["auto_done"] = {
                        "ok": True,
                        "reason": reason,
                        "source": "deterministic_fresh_arithmetic",
                    }
                    if tool_name in {"observe", "read_text"}:
                        arithmetic_payload["fresh_evidence_preview"] = screen[:1000]
                    BUS.publish(
                        "agent_auto_done",
                        run_id=self.run_id,
                        tool=tool_name,
                        reason=reason,
                    )
                    return ToolResult(
                        json.dumps(arithmetic_payload, ensure_ascii=False, default=str),
                        is_observe=result.is_observe,
                        terminal="done",
                        summary=result.summary,
                        replay=result.replay,
                        image_b64=result.image_b64,
                    )
        parsed_payload: dict[str, Any] = {}
        if tool_name in {"execute_subgoal", "run_action_batch"}:
            try:
                parsed_payload = json.loads(result.text or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed_payload = {}
            if (
                isinstance(parsed_payload, dict)
                and parsed_payload.get("ok") is True
                and parsed_payload.get("verified") is True
                and parsed_payload.get("saved_paths")
            ):
                _recent, artifact_evidence = self._done_evidence()
                artifact_screen = self._fresh_done_screen()
                artifact_verdict = self._deterministic_saved_file_verdict(
                    artifact_screen,
                    artifact_evidence,
                )
                if artifact_verdict is not None and artifact_verdict[0]:
                    reason = artifact_verdict[1]
                    self._done_verified = True
                    self.run_state.accept_verification(tool_name, reason)
                    BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
                    BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                    parsed_payload["auto_done"] = {
                        "ok": True,
                        "reason": reason,
                        "source": "deterministic_saved_artifact",
                    }
                    BUS.publish(
                        "agent_auto_done",
                        run_id=self.run_id,
                        tool=tool_name,
                        reason=reason,
                    )
                    return ToolResult(
                        json.dumps(parsed_payload, ensure_ascii=False, default=str),
                        is_observe=result.is_observe,
                        terminal="done",
                        summary=result.summary,
                        replay=result.replay,
                        image_b64=result.image_b64,
                    )
            if (
                not isinstance(parsed_payload, dict)
                or parsed_payload.get("ok") is not True
                or parsed_payload.get("verified") is not True
            ):
                return result
        new_entries = self._visible_evidence_ledger[ledger_len_before:]
        if not any(
            self._auto_done_evidence_source(str(item.get("source") or ""))
            for item in new_entries
        ):
            return result
        targets = self._auto_done_visible_targets()
        if not targets:
            return result
        proven, missing = self._visible_targets_proven_by_controller_evidence(targets)
        if not proven:
            self._trace_debug("auto_done_visible_evidence_wait", {
                "run_id": self.run_id,
                "tool": tool_name,
                "targets": targets,
                "missing": missing,
            })
            return result
        explicit_paths = _explicit_saved_paths(self.task)
        if explicit_paths:
            missing_paths: list[str] = []
            for path in explicit_paths:
                try:
                    if (
                        not path.is_file()
                        or path.stat().st_size <= 0
                        or path.stat().st_mtime < self._started_wall_time - 2.0
                    ):
                        missing_paths.append(str(path))
                except OSError:
                    missing_paths.append(str(path))
            if missing_paths:
                self._trace_debug("auto_done_output_file_wait", {
                    "run_id": self.run_id,
                    "tool": tool_name,
                    "targets": targets,
                    "missing_paths": missing_paths,
                })
                return result
        payload = {
            "ok": True,
            "auto_done": True,
            "reason": "all strong task-visible targets were proven by real observe/read_text/controller evidence",
            "targets": targets,
        }
        try:
            original = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            original = {}
        if isinstance(original, dict):
            original["auto_done"] = payload
            text = json.dumps(original, ensure_ascii=False, default=str)
        else:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        BUS.publish(
            "agent_auto_done",
            run_id=self.run_id,
            tool=tool_name,
            targets=targets,
            reason=payload["reason"],
        )
        self.run_state.accept_verification(tool_name, payload["reason"])
        BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
        return ToolResult(
            text,
            is_observe=result.is_observe,
            terminal="done",
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
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
        visual_compaction_tools = {
            "execute_subgoal", "run_action_batch", "draw_strokes", "drag",
            "zoom", "screenshot",
        }
        # Keep fresh, reasonably sized deterministic output intact. Immediate
        # lossy compaction previously reduced results such as
        # {ok, exit_code, stdout} to only {ok}; the model then repeated commands
        # because it literally could not see FILE_EXISTS/line counts/content.
        # Historical context folding still handles old bulky messages later.
        if tool_name not in visual_compaction_tools and len(result_text or "") <= 6000:
            return result_text or ""
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return (result_text or "")[:6000]
        if not isinstance(data, dict):
            return (result_text or "")[:6000]
        if tool_name in {"run_shell", "run_detached"}:
            compact = {
                key: data.get(key)
                for key in (
                    "ok", "verified", "exit_code", "timeout", "pid",
                    "process_running", "error",
                )
                if data.get(key) is not None
            }
            for key, limit in (("stdout", 5000), ("stderr", 2000), ("output_preview", 3000)):
                value = data.get(key)
                if value in (None, ""):
                    continue
                text = str(value)
                compact[key] = (
                    text if len(text) <= limit
                    else text[:limit] + f"...[truncated {len(text) - limit} chars]"
                )
            return json.dumps(compact, ensure_ascii=False, default=str)[:8500]
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
                        "semantic_part",
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
                "structured_scene_verified": data.get("structured_scene_verified"),
                "subgoal": data.get("subgoal"),
                "subgoal_route": data.get("subgoal_route"),
                "controller_state": data.get("controller_state"),
                "needs_llm_reason": data.get("needs_llm_reason"),
                "input_applied": data.get("input_applied"),
                "spreadsheet_table_applied": data.get("spreadsheet_table_applied"),
                "spreadsheet_row_format_applied": data.get("spreadsheet_row_format_applied"),
                "exact_save_as_applied": data.get("exact_save_as_applied"),
                "format_applied": data.get("format_applied"),
                "format_verified": data.get("format_verified"),
                "format_already_applied": data.get("format_already_applied"),
                "mutation_skipped": data.get("mutation_skipped"),
                "save_dispatched": data.get("save_dispatched"),
                "saved_paths": data.get("saved_paths"),
                "artifact_format_errors": data.get("artifact_format_errors"),
                "scene_structure": {
                    key: (packet.get("scene_structure") or {}).get(key)
                    for key in (
                        "required",
                        "ok",
                        "creature_scene",
                        "primitive_count",
                        "semantic_part_count",
                        "matched_visual_color_count",
                        "structured_scene_verified",
                        "incremental",
                        "prior_primitive_count",
                        "new_primitive_count",
                        "errors",
                    )
                    if isinstance(packet.get("scene_structure"), dict)
                    and key in packet.get("scene_structure", {})
                },
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
            if data.get("failure_classification"):
                compact["failure_classification"] = data.get("failure_classification")
            if data.get("failure_recovery"):
                compact["failure_recovery"] = data.get("failure_recovery")
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
                preview = item.get("preview")
                if item.get("action") in {"observe", "read_text"} and isinstance(preview, str) and preview.strip():
                    child["preview"] = preview[:500]
                if "nonvisual_summary" in child_data:
                    child["nonvisual_summary"] = AgentRunner._compact_visual_summary(
                        child_data.get("nonvisual_summary")
                    )
                compact_results.append(child)
            compact = {
                "ok": data.get("ok"),
                "verified": data.get("verified"),
                "spreadsheet_table_applied": data.get("spreadsheet_table_applied"),
                "spreadsheet_row_format_applied": data.get("spreadsheet_row_format_applied"),
                "exact_save_as_applied": data.get("exact_save_as_applied"),
                "mutation_skipped": data.get("mutation_skipped"),
                "needs_observe": data.get("needs_observe"),
                "last_action": data.get("last_action"),
                "fast_path_advice": data.get("fast_path_advice"),
                "metrics": data.get("metrics"),
                "results": compact_results,
            }
            if data.get("failure_classification"):
                compact["failure_classification"] = data.get("failure_classification")
            if data.get("failure_recovery"):
                compact["failure_recovery"] = data.get("failure_recovery")
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
            if data.get("failure_classification"):
                compact["failure_classification"] = data.get("failure_classification")
            if data.get("failure_recovery"):
                compact["failure_recovery"] = data.get("failure_recovery")
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
        compact = {
            key: data.get(key)
            for key in (
                "ok", "verified", "error", "blocked", "tool",
                "wrapped_by_fast_controller", "source_action",
                "source_action_ok", "source_action_verified",
                "needs_observe", "last_action", "fast_path_advice", "metrics",
            )
            if data.get(key) is not None
        }
        delta = data.get("screen_delta")
        if isinstance(delta, dict):
            compact["screen_delta"] = {
                key: delta.get(key)
                for key in (
                    "changed", "ratio", "elapsed_ms", "capture_latency_ms",
                    "delta_source", "transient_observed",
                    "changed_in_target_region", "foreground_matches_target",
                )
                if delta.get(key) is not None
            }
        children = data.get("results")
        if isinstance(children, list):
            compact["results"] = [
                {
                    key: item.get(key)
                    for key in ("action", "ok", "verified", "elapsed_ms")
                    if isinstance(item, dict) and item.get(key) is not None
                }
                for item in children[:8]
                if isinstance(item, dict)
            ]
        return json.dumps(compact or data, ensure_ascii=False, default=str)[:1800]

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
        save_path = str(args.get("text") or "").strip()
        file_dialog_path = bool(
            tool_name == "type_text"
            and self._looks_like_absolute_output_path(save_path)
            and self._file_dialog_is_active()
        )
        if file_dialog_path:
            actions = [{
                "action": "type_into_element",
                "text": save_path,
                "clear": True,
                "expected_region": "bottom",
                "upgraded_from": "type_text",
            }]
            actions.extend([
                {
                    "action": "hotkey",
                    "keys": ["Enter"],
                    "expect": {"file_exists": save_path},
                },
                {"action": "wait_stable", "stable_ms": 250, "timeout_s": 2.0},
            ])
        else:
            actions = [self._normalise_fast_wrapped_action(tool_name, args)]
        batch_args = {
            "actions": actions,
        }
        if len(actions) > 1:
            batch_args["subgoal"] = f"Submit file dialog path and verify saved output: {save_path}"
        if tool_name in {"draw_strokes", "drag"}:
            batch_args["sync"] = False
        batch_result = self.session.dispatch("run_action_batch", batch_args)
        return self._fast_wrapped_result(tool_name, args, batch_result)

    @staticmethod
    def _looks_like_absolute_output_path(value: str) -> bool:
        text = str(value or "").strip().strip('"')
        return bool(
            re.match(r"^[A-Za-z]:[\\/]", text)
            and re.search(r"\.[A-Za-z0-9]{1,8}$", text)
        )

    def _file_dialog_is_active(self) -> bool:
        snapshot = getattr(self.session, "snapshot", None)
        if snapshot is None:
            return False
        title = str(getattr(snapshot, "title", "") or "").casefold()
        texts = [
            str(getattr(element, "text", "") or "").casefold()
            for element in (getattr(snapshot, "elements", None) or [])
            if str(getattr(element, "text", "") or "").strip()
        ]
        combined = "\n".join(texts)
        title_match = any(marker in title for marker in (
            "save as", "另存为", "保存为", "open file", "打开文件",
        ))
        filename_field = any(marker in combined for marker in (
            "file name", "filename", "文件名称", "文件名",
        ))
        submit_control = any(marker in combined for marker in (
            "save", "保存", "另存为",
        ))
        return title_match or (filename_field and submit_control)

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

    def _drawing_destructive_clear_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if not _is_drawing_task(self.task) or re.search(
            r"\b(?:clear|reset|erase|redraw)\b|清空|重置|擦除|重画",
            self.task,
            re.IGNORECASE,
        ):
            return None
        if tool_name != "run_action_batch":
            return None
        actions = [action for action in (args.get("actions") or []) if isinstance(action, dict)]
        saw_select_all = False
        destructive = False
        for action in actions:
            if action.get("action") != "hotkey":
                continue
            action_args = dict(action.get("params") or {})
            action_args.update({key: value for key, value in action.items() if key != "params"})
            keys = {str(key).strip().casefold() for key in action_args.get("keys") or []}
            if "a" in keys and keys.intersection({"ctrl", "control", "ctl"}):
                saw_select_all = True
            if saw_select_all and keys.intersection({"delete", "backspace"}):
                destructive = True
                break
        if not destructive:
            return None
        verified_scene = False
        for history_tool, _history_args, history_text in reversed(self._tool_history):
            if history_tool != "execute_subgoal":
                continue
            try:
                payload = json.loads(history_text or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            scene = (payload.get("evidence_packet") or {}).get("scene_structure") or {}
            if payload.get("structured_scene_verified") or scene.get("structured_scene_verified"):
                verified_scene = True
                break
        if not verified_scene:
            return None
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "blocked": True,
            "destructive_clear_blocked": True,
            "mutation_applied": False,
            "fast_path_advice": (
                "A verified drawing already exists. The requested select-all/delete clear was blocked "
                "to preserve it. Add only missing details or finish; do not erase the canvas."
            ),
        }, ensure_ascii=False))

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
        extras: list[dict[str, str]] = []
        skipped_current = False
        if steps:
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
            task_specs = _extract_draw_specs_from_task_text(self.task)
            current_index = next(
                (
                    idx for idx, spec in enumerate(task_specs)
                    if _draw_spec_signature(spec) == current_sig
                    and (
                        not _draw_spec_region(current_draw)
                        or not _draw_spec_region(spec)
                        or _draw_spec_region(spec) == _draw_spec_region(current_draw)
                    )
                ),
                None,
            )
            if current_index is None:
                return args
            if not current_draw.get("expected_region") and _draw_spec_region(task_specs[current_index]):
                current_draw["expected_region"] = _draw_spec_region(task_specs[current_index])
            for spec in task_specs[current_index + 1:]:
                if _draw_spec_signature(spec) != current_sig and spec not in extras:
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

    @staticmethod
    def _looks_like_spreadsheet_task(task: str) -> bool:
        text = str(task or "")
        return bool(re.search(
            r"\b(?:spreadsheet|workbook|worksheet|cells?|cell\s+range)\b|"
            r"工作簿|工作表|表格|单元格|"
            r"=[A-Z]{1,3}\d+\s*[*+\-/]|=SUM\s*\(",
            text,
            re.IGNORECASE,
        ))

    @staticmethod
    def _document_format_specs(task: str) -> list[dict[str, Any]]:
        return [dict(spec) for spec in _task_document_format_specs(task)]

    @staticmethod
    def _exact_document_text_block(task: str) -> str:
        return _task_exact_document_text_block(task)

    def _maybe_upgrade_execute_subgoal_document_input(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name != "execute_subgoal":
            return args
        if self._looks_like_spreadsheet_task(self.task):
            return args
        input_spec = args.get("input") if isinstance(args.get("input"), dict) else {}
        intent_scope = json.dumps({
            "subgoal": args.get("subgoal"),
            "click": args.get("click"),
            "clicks": args.get("clicks"),
            "input_label": input_spec.get("label") or input_spec.get("role"),
        }, ensure_ascii=False, default=str).casefold()
        blank_document_ready = bool(getattr(self.session, "_new_blank_applied", False))
        mistaken_blank_navigation = bool(
            blank_document_ready
            and re.search(
                r"\b(?:new|blank|create|document\s+title|title\s+field|template)\b|"
                r"新建|空白|创建|文档标题|标题栏|模板",
                intent_scope,
                re.IGNORECASE,
            )
        )
        if not input_spec and not mistaken_blank_navigation and not re.search(
            r"\b(?:type|input|enter|write)\b|\u8f93\u5165|\u952e\u5165|\u5199\u5165|\u5f55\u5165",
            intent_scope,
        ):
            return args
        block = self._exact_document_text_block(self.task)
        if not block:
            return args
        supplied_text = str(
            input_spec.get("text")
            or input_spec.get("value")
            or input_spec.get("text_input")
            or ""
        )
        input_label = str(input_spec.get("label") or input_spec.get("role") or "").casefold()
        block_lines = [line for line in block.splitlines() if line.strip()]
        supplied_lines = [line for line in supplied_text.splitlines() if line.strip()]
        normalize_line = lambda value: re.sub(
            r"[\s\.,;:!?。；：！？、]+$",
            "",
            str(value or "").strip().casefold(),
        )
        normalized_block_lines = {normalize_line(line) for line in block_lines if normalize_line(line)}
        normalized_supplied_lines = {
            normalize_line(line) for line in supplied_lines if normalize_line(line)
        }
        line_overlap = len(normalized_block_lines & normalized_supplied_lines)
        supplied_matches_block = bool(
            supplied_text
            and block_lines
            and (
                (
                    block_lines[0] in supplied_text
                    and block_lines[-1] in supplied_text
                )
                or (
                    len(block_lines) >= 2
                    and line_overlap / len(block_lines) >= 0.6
                )
            )
        )
        document_label = bool(re.search(
            r"\b(?:document|body|editor|page)\b|\u6587\u6863|\u6b63\u6587|\u7f16\u8f91\u533a|\u9875\u9762",
            input_label,
        ))
        home_multiline_document_input = bool(
            getattr(self.session, "_document_surface_ready", None) is False
            and len(supplied_lines) >= 2
            and block_lines
            and normalize_line(block_lines[0]) == normalize_line(supplied_lines[0])
        )
        if input_spec and not (
            supplied_matches_block or document_label or home_multiline_document_input
        ):
            return args
        lines = block_lines
        upgraded: dict[str, Any] = {
            "subgoal": "Enter the user's exact paragraph block into the focused document surface.",
            "input": {
                "text": block,
                "document_surface": True,
                "clear": True,
            },
            "verify_texts": list(dict.fromkeys([
                *lines[:1],
                *[line for line in lines if re.search(r"[A-Za-z]+[_-][A-Za-z0-9_-]+|\d{3,}", line)],
                *lines[-1:],
            ]))[:4],
            "constraints": {"wait": True},
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        self._trace_debug("execute_subgoal_document_input_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
            "line_count": len(lines),
        })
        return upgraded

    def _maybe_upgrade_pending_clipboard_replace(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return args

        # Cheap models often arrive in the destination editor and then keep
        # clicking its body or Find menu. Recover the pending cross-app
        # delivery from observable GUI history: each successful Ctrl+C opens
        # one delivery and each controller replacement consumes one. The
        # target tokens still come from the user's task, not an app rule.
        task_tokens = _clipboard_target_tokens(self.task)
        successful_copy_events = 0
        copied_fingerprints: set[str] = set()
        completed_deliveries = 0
        for history_tool, history_args, history_text in self._tool_history:
            try:
                history_payload = json.loads(history_text or "{}")
            except (json.JSONDecodeError, TypeError):
                history_payload = {}
            history_ok = not isinstance(history_payload, dict) or history_payload.get("ok", True)
            candidate_actions: list[dict[str, Any]] = []
            if history_tool == "hotkey":
                candidate_actions.append(history_args)
            elif history_tool == "run_action_batch":
                candidate_actions.extend(
                    action for action in (history_args.get("actions") or [])
                    if isinstance(action, dict) and action.get("action") == "hotkey"
                )
            for action in candidate_actions:
                action_args = dict(action.get("params") or {})
                action_args.update({key: value for key, value in action.items() if key != "params"})
                keys = {
                    str(key).strip().lower()
                    for key in (action_args.get("keys") or [])
                }
                if (
                    history_ok
                    and "c" in keys
                    and keys.intersection({"ctrl", "control", "ctl"})
                ):
                    successful_copy_events += 1
                    copied_fingerprints.update(_clipboard_fingerprints(history_payload))
            if "clipboard_replace_applied" in (history_text or ""):
                if isinstance(history_payload, dict) and history_payload.get("clipboard_replace_applied"):
                    completed_deliveries += 1
        successful_copies = (
            len(copied_fingerprints) if copied_fingerprints else successful_copy_events
        )

        semantic = json.dumps({
            "subgoal": args.get("subgoal"),
            "click": args.get("click"),
            "actions": args.get("actions"),
        }, ensure_ascii=False, default=str)
        destination_interaction = bool(re.search(
            r"document|editor|editing\s+area|body|find|replace|"
            r"文档|编辑|正文|查找|替换|点击",
            semantic,
            re.IGNORECASE,
        ))
        source_interaction = bool(re.search(
            r"address\s*bar|browser|navigate|url|copy|ctrl.?[lc]|"
            r"地址栏|浏览器|导航|复制",
            semantic,
            re.IGNORECASE,
        ))
        history_pending = (
            bool(task_tokens)
            and successful_copies > completed_deliveries
            and completed_deliveries < len(task_tokens)
            and destination_interaction
            and not source_interaction
        )

        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None)
        pending = next(
            (step for step in steps if not step.get("done")),
            None,
        ) if isinstance(steps, list) else None
        goal = str(pending.get("goal") or "") if isinstance(pending, dict) else ""
        token_match = re.search(r"\[\[[^\]\r\n]{1,100}\]\]", goal)
        plan_pending = bool(
            token_match
            and re.search(r"\breplac(?:e|ing)\b|\bpaste\b|替换|粘贴", goal, re.IGNORECASE)
            and any(
                bool(step.get("done"))
                and re.search(r"\bcopy\b|复制", str(step.get("goal") or ""), re.IGNORECASE)
                for step in (steps or [])
            )
        )
        only_focuses_editor = bool(re.search(
            r"document\s+(?:editing\s+)?(?:area|body|editor)|"
            r"editing\s+area|focus.{0,30}document|"
            r"文档编辑区|正文|聚焦.{0,20}文档|点击.{0,20}文档",
            semantic,
            re.IGNORECASE,
        )) and not re.search(
            r"\breplac(?:e|ing)\b|替换|\bclipboard\b|剪贴板",
            semantic,
            re.IGNORECASE,
        )
        if not history_pending and not (plan_pending and only_focuses_editor):
            return args
        token = task_tokens[completed_deliveries] if history_pending else token_match.group(0)
        upgraded: dict[str, Any] = {
            "subgoal": f"Use GUI Find and Replace to replace {token} with the current clipboard content",
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        if tool_name == "run_action_batch":
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "clipboard_replace"
        self._trace_debug("pending_clipboard_replace_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "pending_goal": goal,
            "clipboard_copies": successful_copies,
            "completed_deliveries": completed_deliveries,
            "history_pending": history_pending,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_upgrade_execute_subgoal_formats(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"} or args.get("format") or args.get("formats"):
            return args
        if self._looks_like_spreadsheet_task(self.task):
            return args
        if any(args.get(key) for key in ("input", "draw", "draws")):
            return args
        if (
            args.get("verify_texts")
            and not any(args.get(key) for key in ("click", "clicks", "format", "formats"))
            and re.search(
                r"\b(?:verify|confirm|check)\b|\u9a8c\u8bc1|\u6838\u9a8c|\u786e\u8ba4|\u68c0\u67e5",
                str(args.get("subgoal") or "").casefold(),
            )
        ):
            return args
        formats = self._document_format_specs(self.task)
        if not formats:
            return args
        # Ctrl+B is a toggle, not an idempotent setter. When an existing DOCX
        # is the explicit target, inspect its current direct formatting and do
        # not flip bold off when the requested state is already satisfied.
        for path in _explicit_saved_paths(self.task):
            try:
                if path.suffix.casefold() != ".docx" or not path.is_file():
                    continue
                _text, paragraphs, package_error = _docx_document_evidence(path)
            except OSError:
                continue
            if package_error or not paragraphs or paragraphs[0].get("bold_all") is not True:
                continue
            for spec in formats:
                if str(spec.get("range") or "").casefold() == "first_line":
                    spec.pop("bold", None)
            break
        click_spec = args.get("click") if isinstance(args.get("click"), dict) else {}
        click_specs = args.get("clicks") if isinstance(args.get("clicks"), list) else []
        model_scope = json.dumps({
            "subgoal": args.get("subgoal"),
            "click_label": click_spec.get("label") or click_spec.get("text") or click_spec.get("name"),
            "click_labels": [
                item.get("label") or item.get("text") or item.get("name")
                for item in click_specs
                if isinstance(item, dict)
            ],
        }, ensure_ascii=False, default=str).casefold()
        if not re.search(
            r"\b(?:format(?:ting|ted)?|bold|italic|align|center|centre|justify|heading|title)\b|"
            r"\bfirst\s+(?:line|paragraph)\b|"
            r"\u683c\u5f0f|\u52a0\u7c97|\u659c\u4f53|\u5bf9\u9f50|\u5c45\u4e2d|\u4e24\u7aef\u5bf9\u9f50|"
            r"\u6807\u9898|\u7b2c\u4e00(?:\u884c|\u6bb5)|\u9996(?:\u884c|\u6bb5)",
            model_scope,
            re.IGNORECASE,
        ):
            return args
        navigation_without_format = bool(re.search(
            r"\b(?:new|create|open|launch|blank|template|save\s+as)\b|"
            r"\u65b0\u5efa|\u521b\u5efa|\u6253\u5f00|\u542f\u52a8|\u7a7a\u767d|\u6a21\u677f|\u53e6\u5b58\u4e3a",
            model_scope,
        ))
        new_document_tab = bool(
            re.search(r"\btab\b|\u9009\u9879\u5361", model_scope)
            and re.search(
                r"\b(?:new|blank|document|template)\b|\u65b0\u5efa|\u7a7a\u767d|\u6587\u6863|\u6a21\u677f",
                model_scope,
            )
            and not re.search(
                r"\b(?:format(?:ting)?|bold|center|align)\b|\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50",
                model_scope,
            )
        )
        if navigation_without_format or new_document_tab:
            return args
        if re.search(
            r"\b(?:type|input|enter|write|insert|paste)\b|"
            r"\u8f93\u5165|\u952e\u5165|\u5199\u5165|\u5f55\u5165|\u63d2\u5165|\u7c98\u8d34",
            model_scope,
        ):
            return args
        if not re.search(
            r"\b(?:format(?:ting)?|bold|center|align|heading|title|first\s+(?:line|paragraph)|body)\b|"
            r"\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50|"
            r"\u7b2c\u4e00(?:\u884c|\u6bb5)|\u9996(?:\u884c|\u6bb5)|\u6b63\u6587|\u6807\u9898|"
            r"\u540e(?:\u7eed|\u9762|\u4e09)?\u6bb5|\u5176\u4ed6\u6bb5\u843d",
            model_scope,
        ):
            return args
        save_after = bool(re.search(
            r"\b(?:save|ctrl\s*\+\s*s)\b|\u4fdd\u5b58",
            str(self.task or "").casefold(),
        ))
        save_as_requested = bool(re.search(
            r"\bsave\s+as\b|\u53e6\u5b58\u4e3a|\u53e6\u5b58",
            str(self.task or "").casefold(),
        ))
        save_path = ""
        if save_as_requested:
            explicit_paths = _explicit_saved_paths(self.task)
            if explicit_paths:
                save_path = str(explicit_paths[0])
        upgraded = {
            "subgoal": "Apply all explicitly requested document text formatting in one local routine.",
            "formats": formats,
            "constraints": {
                "wait": bool(save_after),
                "save_after": bool(save_after),
            },
        }
        if save_path:
            upgraded["constraints"]["save_path"] = save_path
        if tool_name == "run_action_batch":
            # Preserve a non-empty actions array for schema compatibility, but
            # FastToolMixin will replace these fragile model-authored cursor
            # movements with the semantic formats above before execution.
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "document_format"
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        self._trace_debug("execute_subgoal_format_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_deliver_clipboard_after_focus(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        """Continue a pending GUI copy into a named destination document.

        This sits below planner/tool-choice quality. A model may choose a bare
        focus_window followed by dozens of clicks instead of execute_subgoal;
        once the requested destination document is foreground, the controller
        can safely consume the already copied clipboard value itself.
        """
        if tool_name != "focus_window":
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or not payload.get("ok"):
            return result
        focus_data = payload
        title = str(payload.get("title") or payload.get("foreground_title") or "")
        if not title:
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                data = item.get("data")
                if not isinstance(data, dict):
                    continue
                candidate_title = str(
                    data.get("title")
                    or data.get("foreground_title")
                    or (data.get("post_state") or {}).get("foreground_title")
                    or ""
                )
                if candidate_title:
                    focus_data = data
                    title = candidate_title
                    break
        named_targets = re.findall(
            r"[A-Za-z0-9_.-]+\.(?:docx?|xlsx?|pptx?|odt|ods|rtf|txt|md|csv)",
            self.task,
            re.IGNORECASE,
        )
        if not title or not any(name.casefold() in title.casefold() for name in named_targets):
            return result
        hwnd = focus_data.get("hwnd") or payload.get("hwnd") or args.get("hwnd")
        candidate = {
            "subgoal": "Focus the destination document editor for the pending clipboard delivery",
            "target_window": {"hwnd": hwnd} if hwnd else {},
            # The semantic override replaces this placeholder action before
            # controller execution; a non-empty batch satisfies the external
            # tool contract without relying on natural-language-only routing.
            "actions": [{"action": "observe"}],
        }
        upgraded = self._maybe_upgrade_pending_clipboard_replace("run_action_batch", candidate)
        if upgraded is candidate or upgraded.get("subgoal") == candidate["subgoal"]:
            return result
        self._trace_debug("clipboard_delivery_triggered_after_focus", {
            "run_id": self.run_id,
            "focus_result": payload,
            "delivery": upgraded,
        })
        delivery = self._dispatch_with_fast_visual_wrap("run_action_batch", upgraded)
        try:
            delivery_payload = json.loads(delivery.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return delivery
        if isinstance(delivery_payload, dict):
            delivery_payload["auto_triggered_after_focus"] = True
            delivery_payload["focused_destination_title"] = title
            delivery.text = json.dumps(delivery_payload, ensure_ascii=False)
        return delivery

    def _duplicate_clipboard_delivery_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return None
        tokens = _clipboard_target_tokens(self.task)
        if not tokens:
            return None
        semantic = json.dumps(args, ensure_ascii=False, default=str)
        if not any(token in semantic for token in tokens):
            return None
        if not re.search(r"replace|paste|替换|粘贴", semantic, re.IGNORECASE):
            return None
        copy_events = 0
        copied_fingerprints: set[str] = set()
        deliveries = 0
        verified_deliveries = 0
        for history_tool, history_args, history_text in self._tool_history:
            try:
                payload = json.loads(history_text or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("clipboard_replace_applied"):
                deliveries += 1
                if payload.get("verified") is True:
                    verified_deliveries += 1
            candidate_actions = []
            if history_tool == "hotkey":
                candidate_actions = [history_args]
            elif history_tool == "run_action_batch":
                candidate_actions = list(history_args.get("actions") or [])
            for action in candidate_actions:
                if not isinstance(action, dict) or (
                    history_tool == "run_action_batch" and action.get("action") != "hotkey"
                ):
                    continue
                action_args = dict(action.get("params") or {})
                action_args.update({key: value for key, value in action.items() if key != "params"})
                keys = {str(key).strip().casefold() for key in action_args.get("keys") or []}
                if "c" in keys and keys.intersection({"ctrl", "control", "ctl"}):
                    if not isinstance(payload, dict) or payload.get("ok", True):
                        copy_events += 1
                        copied_fingerprints.update(_clipboard_fingerprints(payload))
        copies = len(copied_fingerprints) if copied_fingerprints else copy_events
        if deliveries <= 0 or copies > deliveries:
            return None
        return ToolResult(json.dumps({
            "ok": verified_deliveries > 0,
            "blocked": True,
            "duplicate_clipboard_delivery": True,
            "mutation_applied": True,
            "verified": verified_deliveries > 0,
            "fast_path_advice": (
                "This clipboard delivery was already verified and no newer Ctrl+C copy exists. "
                "Do not mutate the placeholder again. Save the destination and continue with "
                "the next distinct transfer."
                if verified_deliveries > 0
                else "This clipboard delivery was already applied. Read/observe it before saving."
            ),
        }, ensure_ascii=False))

    def _maybe_upgrade_spreadsheet_action_batch(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Collapse model-authored cell-by-cell batches into one table paste."""
        if tool_name != "run_action_batch" or not self._looks_like_spreadsheet_task(self.task):
            return args
        if args.get("semantic_override"):
            return args
        actions = args.get("actions") if isinstance(args.get("actions"), list) else []
        scope = f"{args.get('subgoal') or ''} {json.dumps(actions, ensure_ascii=False, default=str)}"
        if re.search(
            r"\bbold\b.{0,60}\brow\s*\d+|\brow\s*\d+.{0,60}\bbold\b|"
            r"加粗.{0,40}第?\s*\d+\s*行|第?\s*\d+\s*行.{0,40}加粗",
            scope,
            re.IGNORECASE,
        ):
            applied_rows = getattr(self.session, "_spreadsheet_bold_rows_applied", set())
            paths = _explicit_saved_paths(self.task)
            saved_paths = getattr(self.session, "_exact_save_paths_applied", set())
            if not isinstance(saved_paths, set):
                saved_paths = set()
            if (
                isinstance(applied_rows, set)
                and applied_rows
                and paths
                and str(paths[0]).casefold() not in saved_paths
            ):
                save_seed = dict(args)
                save_seed["subgoal"] = f"Save As the exact requested path: {paths[0]}"
                self._trace_debug("spreadsheet_format_repeat_advanced_to_save", {
                    "run_id": self.run_id,
                    "applied_rows": sorted(applied_rows),
                    "save_path": str(paths[0]),
                })
                return self._maybe_upgrade_exact_save_as(tool_name, save_seed)
            upgraded = dict(args)
            upgraded["semantic_override"] = "spreadsheet_row_format"
            self._trace_debug("spreadsheet_row_format_batch_auto_upgraded", {
                "run_id": self.run_id,
                "original_action_count": len(actions),
                "subgoal": args.get("subgoal"),
            })
            return upgraded
        type_count = sum(
            1 for action in actions
            if isinstance(action, dict)
            and str(action.get("action") or action.get("tool") or "") in {"type_text", "type_into_element"}
        )
        table_intent = bool(re.search(
            r"\b(?:all|complete|entire|whole)\s+table\b|"
            r"\btable\s+(?:data|content)\b|\brow\s+by\s+row\b|"
            r"全部(?:表格|数据)|整个表格|表格(?:数据|内容)|逐行",
            scope,
            re.IGNORECASE,
        ))
        if not table_intent and type_count < 4:
            return args
        upgraded = dict(args)
        upgraded["semantic_override"] = "spreadsheet_table"
        self._trace_debug("spreadsheet_action_batch_auto_upgraded", {
            "run_id": self.run_id,
            "original_action_count": len(actions),
            "type_action_count": type_count,
            "subgoal": args.get("subgoal"),
        })
        return upgraded

    def _maybe_upgrade_exact_save_as(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return args
        paths = _explicit_saved_paths(self.task)
        if not paths:
            return args
        click_spec = args.get("click") if isinstance(args.get("click"), dict) else {}
        scope = " ".join([
            str(args.get("subgoal") or ""),
            str(click_spec.get("label") or click_spec.get("text") or click_spec.get("name") or ""),
            json.dumps(args.get("actions") or [], ensure_ascii=False, default=str),
        ])
        if not re.search(
            r"\bsave\s+as\b|\bsave\b|\bfile\s+menu\b|"
            r"另存为|另存|保存|文件菜单|点击.{0,12}文件",
            scope,
            re.IGNORECASE,
        ):
            return args
        save_path = str(paths[0])
        upgraded: dict[str, Any] = {
            "subgoal": f"Save As the exact requested path: {save_path}",
            "input": {
                "label": "File name",
                "text": save_path,
                "clear": True,
            },
            "click": {"label": "Save", "fallback_text": ["保存", "Save"]},
            "verify_texts": [Path(save_path).name],
            "constraints": {
                "open_save_as": True,
                "save_path": save_path,
                "wait": True,
            },
        }
        if isinstance(args.get("target_window"), dict):
            upgraded["target_window"] = dict(args["target_window"])
        if tool_name == "run_action_batch":
            upgraded["actions"] = list(args.get("actions") or [])
            upgraded["semantic_override"] = "exact_save_as"
        self._trace_debug("exact_save_as_auto_upgraded", {
            "run_id": self.run_id,
            "original": args,
            "upgraded": upgraded,
        })
        return upgraded

    def _maybe_auto_complete_document_format_plan_items(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {"execute_subgoal", "run_action_batch"} or not args.get("formats"):
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or payload.get("format_applied") is not True:
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        completed: list[dict[str, Any]] = []
        fresh_paths = self._fresh_explicit_output_paths() if payload.get("save_dispatched") else []
        format_errors: list[str] = []
        format_verified = False
        for path in fresh_paths:
            if path.suffix.casefold() != ".docx":
                continue
            try:
                _document_text, paragraphs, package_error = _docx_document_evidence(path)
                if package_error:
                    format_errors.append(package_error)
                    continue
                errors = _docx_task_format_errors(self.task, paragraphs)
            except (OSError, ValueError, KeyError):
                continue
            if errors:
                format_errors.extend(errors)
                continue
            format_verified = True
            break
        if fresh_paths:
            payload["saved_paths"] = [str(path) for path in fresh_paths]
        if format_errors:
            payload["artifact_format_errors"] = list(dict.fromkeys(format_errors))[:8]
            payload["verified"] = False
            payload["needs_llm_reason"] = True
            payload["postcondition_pending"] = False
            controller_state = payload.get("controller_state")
            if isinstance(controller_state, dict):
                controller_state["needs_llm_reason"] = True
                controller_state["verified"] = False
            payload["fast_path_advice"] = (
                "The document was saved, but deterministic DOCX inspection found formatting "
                f"mismatches: {'; '.join(payload['artifact_format_errors'])}. Do not repeat the "
                "identical toggle routine. Re-observe the current document state and use a corrective, "
                "state-aware formatting action."
            )
            return ToolResult(
                json.dumps(payload, ensure_ascii=False, default=str),
                is_observe=result.is_observe,
                terminal=result.terminal,
                summary=result.summary,
                replay=result.replay,
                image_b64=result.image_b64,
            )
        if format_verified:
            payload["format_verified"] = True
            payload["verified"] = True
            payload["postcondition_pending"] = False
            packet = payload.get("evidence_packet")
            if not isinstance(packet, dict):
                packet = {}
                payload["evidence_packet"] = packet
            packet["format_verified"] = True
            packet["saved_paths"] = [str(path) for path in fresh_paths]
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            folded = goal.casefold()
            is_save_or_verify = bool(re.search(
                r"\bsave\b|\bexport\b|\bverify\b|\u4fdd\u5b58|\u53e6\u5b58|\u5bfc\u51fa|\u9a8c\u8bc1|\u6838\u9a8c|\u786e\u8ba4",
                folded,
            ))
            if is_save_or_verify:
                if not (payload.get("save_dispatched") and fresh_paths and format_verified):
                    break
                if re.search(r"\b(?:window|title)\b|\u7a97\u53e3|\u6807\u9898", folded):
                    current_title = str(
                        getattr(getattr(self.session, "snapshot", None), "title", "") or ""
                    )
                    if not current_title or not any(path.name in current_title for path in fresh_paths):
                        break
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "saved_docx_artifact",
                    })
                continue
            if not re.search(
                r"\b(?:select|format|bold|center|align|heading|title|paragraph|body)\b|"
                r"\u9009\u4e2d|\u683c\u5f0f|\u52a0\u7c97|\u5c45\u4e2d|\u5bf9\u9f50|\u7b2c\u4e00\u884c|\u6b63\u6587|\u6bb5\u843d|\u6807\u9898",
                folded,
            ):
                break
            if plan.complete(index):
                completed.append({"index": index, "goal": goal[:160], "source": "format_routine"})
        if not completed and not fresh_paths and not format_errors:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        if format_verified and plan.all_done():
            payload["plan_all_done"] = True
            payload["auto_done"] = True
            payload["auto_done_reason"] = (
                "saved DOCX passed deterministic text-format checks and the target window title "
                "still identifies the exact output file"
            )
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + (
                " Formatting and Save were applied in one controller batch and the saved DOCX "
                "artifact passed deterministic formatting checks. Do not repeat formatting."
                if format_verified else
                " Formatting selection and shortcuts were applied in one controller batch. "
                "Continue directly to Save, then rely on artifact verification instead of repeating formatting."
            )
        ).strip()
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if payload.get("auto_done") else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

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

    def _maybe_reuse_recent_draw_batch(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return None
        current_specs = self._draw_specs_from_args(args)
        if len(current_specs) < 2:
            return None
        for previous_tool, previous_args, previous_text in reversed(self._tool_history[-10:]):
            if previous_tool != "execute_subgoal":
                continue
            if self._draw_specs_from_args(previous_args) != current_specs:
                continue
            try:
                previous_payload = json.loads(previous_text or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(previous_payload, dict) or previous_payload.get("verified") is not True:
                continue
            packet = previous_payload.get("evidence_packet")
            if not isinstance(packet, dict) or packet.get("missing_visual_colors"):
                continue
            matched_colors = list(packet.get("matched_visual_colors") or [])
            payload = {
                "ok": True,
                "verified": True,
                "subgoal": str(args.get("subgoal") or "Reuse verified drawing batch"),
                "subgoal_route": "controller_batch_evidence_reuse",
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
                    "matched_visual_colors": matched_colors,
                    "missing_visual_colors": [],
                    "reused_from": "recent identical verified execute_subgoal draw batch",
                    "scene_structure": packet.get("scene_structure") or {},
                },
                "needs_llm_reason": False,
                "fast_path_advice": (
                    "This identical multi-primitive scene was already executed and verified. "
                    "Do not redraw it; complete the plan or call done."
                ),
            }
            self._trace_debug("execute_subgoal_draw_batch_evidence_reused", {
                "run_id": self.run_id,
                "draw_count": len(current_specs),
                "matched_colors": matched_colors,
            })
            return ToolResult(
                json.dumps(payload, ensure_ascii=False, default=str),
                replay={
                    "tool": "execute_subgoal",
                    "subgoal": payload["subgoal"],
                    "reused_recent_draw_batch": True,
                },
            )
        return None

    def _filter_repeated_draw_batch(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Turn a repeated full drawing retry into a missing-color repair.

        A text model may ignore a partial-success hint and resubmit the entire
        scene. Replaying successful geometry is destructive when a palette
        click changes the canvas layout, so the runtime enforces the same
        recovery boundary locally.
        """
        if tool_name != "execute_subgoal" or not _is_drawing_task(self.task):
            return args
        current_specs = self._draw_specs_from_args(args)
        if len(current_specs) < 2:
            return args
        for previous_tool, previous_args, previous_text in reversed(self._tool_history[-8:]):
            if previous_tool != "execute_subgoal":
                continue
            previous_specs = self._draw_specs_from_args(previous_args)
            if previous_specs != current_specs:
                continue
            try:
                previous_payload = json.loads(previous_text or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(previous_payload, dict):
                continue
            packet = previous_payload.get("evidence_packet")
            if not isinstance(packet, dict):
                continue
            missing_colors = {
                str(color).casefold()
                for color in (packet.get("missing_visual_colors") or [])
                if str(color).strip()
            }
            matched_colors = {
                str(color).casefold()
                for color in (packet.get("matched_visual_colors") or [])
                if str(color).strip()
            }
            if not missing_colors or not matched_colors:
                continue
            repairs = [
                spec
                for spec in current_specs
                if str(spec.get("color") or spec.get("colour") or "").casefold()
                in missing_colors
            ]
            if not repairs or len(repairs) >= len(current_specs):
                continue
            filtered = dict(args)
            filtered.pop("draw", None)
            filtered["draws"] = repairs
            filtered["subgoal"] = (
                str(args.get("subgoal") or "Repair drawing")
                + " (runtime narrowed retry to missing colors: "
                + ", ".join(sorted(missing_colors))
                + ")"
            )[:320]
            self._trace_debug("execute_subgoal_draw_retry_filtered", {
                "run_id": self.run_id,
                "original_draw_count": len(current_specs),
                "repair_draw_count": len(repairs),
                "missing_colors": sorted(missing_colors),
                "matched_colors": sorted(matched_colors),
            })
            return filtered
        return args

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
        packet = payload.get("evidence_packet") if isinstance(payload.get("evidence_packet"), dict) else {}
        matched_visual_colors = {
            str(color).casefold()
            for color in (packet.get("matched_visual_colors") or [])
        }
        if matched_visual_colors and not _drawing_task_requests_file_output(self.task):
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                if not _drawing_visual_confirmation_goal(goal):
                    continue
                if not _drawing_plan_check_allows_visual_completion(step.get("check"), self.task):
                    continue
                needed_colors = _drawing_visual_confirmation_colors(goal, step.get("check"), self.task)
                if not needed_colors:
                    continue
                if any(color.casefold() not in matched_visual_colors for color in needed_colors):
                    continue
                if plan.complete(index):
                    item = {
                        "index": index,
                        "goal": goal[:160],
                        "source": "visual_color_confirmation",
                    }
                    completed.append(item)
                    BUS.publish(
                        "agent_plan",
                        action="auto_complete",
                        index=index,
                        source="execute_subgoal.visual_color_confirmation",
                        plan=plan.snapshot(),
                    )
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        if plan.all_done() and payload.get("verified") is True:
            payload["plan_all_done"] = True
        existing_advice = str(payload.get("fast_path_advice") or "").strip()
        auto_advice = (
            "Auto-completed drawing plan items "
            + ", ".join(str(item["index"]) for item in completed)
            + " from verified controller draw evidence; "
            + ("plan is complete." if plan.all_done() else "continue with next unfinished item.")
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
            terminal="done" if payload.get("plan_all_done") else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @staticmethod
    def _payload_has_verified_drag(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("verified") is not True:
            return False
        state = payload.get("controller_state")
        if isinstance(state, dict) and state.get("last_action") == "drag":
            return True
        if payload.get("source_action") == "drag" and payload.get("visual_change_detected") is True:
            return True
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or item.get("action") != "drag":
                continue
            data = item.get("data")
            if isinstance(data, dict) and (
                data.get("verified") is True or data.get("visual_change_detected") is True
            ):
                return True
            preview = str(item.get("preview") or "")
            if '"visual_change_detected": true' in preview.casefold() or '"verified": true' in preview.casefold():
                return True
        return False

    @staticmethod
    def _looks_like_desktop_drag_plan_goal(goal: str) -> bool:
        text = str(goal or "")
        low = text.casefold()
        has_drag = any(term in low for term in ("drag", "move", "icon", "folder"))
        has_drag = has_drag or any(term in text for term in ("拖动", "移动", "图标", "文件夹", "整理", "排列"))
        return has_drag

    def _maybe_auto_complete_desktop_drag_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {"run_action_batch", "drag"} or not _is_desktop_icon_drag_task(self.task):
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not self._payload_has_verified_drag(payload):
            return result

        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        completed: list[dict[str, Any]] = []
        if steps:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                if not self._looks_like_desktop_drag_plan_goal(goal):
                    continue
                if plan.complete(index):
                    item = {"index": index, "goal": goal[:160], "source": tool_name}
                    completed.append(item)
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source=f"{tool_name}.verified_drag",
                        plan=plan.snapshot(),
                    )
                break
            if not completed:
                return result
            payload["auto_completed_plan_items"] = completed
            payload["plan_after_auto_complete"] = plan.render()
            if plan.all_done():
                payload["plan_all_done"] = True
        else:
            payload["plan_all_done"] = True

        existing_advice = str(payload.get("fast_path_advice") or "").strip()
        auto_advice = (
            "Auto-completed desktop drag plan item from verified controller drag "
            "evidence; "
            + ("plan is complete." if payload.get("plan_all_done") else "continue with next unfinished item.")
        )
        payload["fast_path_advice"] = (existing_advice + " " + auto_advice).strip()
        self._trace_debug("desktop_drag_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
            "plan_all_done": payload.get("plan_all_done"),
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if payload.get("plan_all_done") else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    def _maybe_auto_complete_open_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if tool_name not in {
            "open_app", "launch", "focus_window", "execute_subgoal", "run_action_batch",
        }:
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if not isinstance(payload, dict) or not payload.get("ok"):
            return result
        if (
            tool_name in {"execute_subgoal", "run_action_batch"}
            and payload.get("subgoal_route") != "open_app_capability"
        ):
            return result
        if not (payload.get("verified") or payload.get("target_hwnd")):
            return result
        completed: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            if not _looks_like_pure_open_plan_goal(goal):
                break
            if plan.complete(index):
                item = {"index": index, "goal": goal[:160], "source": tool_name}
                completed.append(item)
                BUS.publish(
                    "agent_plan",
                    run_id=self.run_id,
                    action="auto_complete",
                    index=index,
                    source=f"{tool_name}.verified_target",
                    plan=plan.snapshot(),
                )
            break
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + " Verified launch/focus already completed the matching open-window "
              "plan item; continue with the next unfinished item."
        ).strip()
        self._trace_debug("open_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
        })
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal=result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @staticmethod
    def _plan_text_key(value: object) -> str:
        text = re.sub(r"^\s*\d+[.)、]\s*", "", str(value or ""))
        text = re.sub(r"(?:已完成|完成后|然后|接着|最后)", "", text)
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).casefold()

    @classmethod
    def _subgoal_matches_plan_goal(cls, subgoal: str, goal: str) -> bool:
        left = cls._plan_text_key(subgoal)
        right = cls._plan_text_key(goal)
        if min(len(left), len(right)) < 3:
            return False
        if left in right or right in left:
            return True
        left_pairs = {left[index:index + 2] for index in range(len(left) - 1)}
        right_pairs = {right[index:index + 2] for index in range(len(right) - 1)}
        if not left_pairs or not right_pairs:
            return False
        overlap = len(left_pairs & right_pairs) / max(1, min(len(left_pairs), len(right_pairs)))
        return overlap >= 0.72

    def _plan_check_satisfied_by_subgoal(
        self,
        check: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> bool:
        if not check:
            return True
        if not isinstance(check, dict):
            return False
        file_path = check.get("file_exists")
        if isinstance(file_path, str) and file_path.strip():
            try:
                path = Path(file_path)
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
                if path.stat().st_mtime < self._started_wall_time - 2.0:
                    return False
            except OSError:
                return False
        packet = payload.get("evidence_packet")
        matched = {
            self._plan_text_key(value)
            for value in ((packet or {}).get("matched_texts") or [])
            if isinstance(packet, dict)
        }
        for key in ("text_appears", "text_contains", "window_appears", "window_title"):
            value = check.get(key)
            if isinstance(value, str) and self._plan_text_key(value) not in matched:
                title = str(getattr(getattr(self.session, "snapshot", None), "title", "") or "")
                if self._plan_text_key(value) not in self._plan_text_key(title):
                    return False
        supported = {
            "file_exists", "text_appears", "text_contains", "window_appears", "window_title",
        }
        return all(key in supported for key in check)

    def _fresh_explicit_output_paths(self) -> list[Path]:
        paths: list[Path] = []
        for path in _explicit_saved_paths(self.task):
            try:
                if (
                    path.is_file()
                    and path.stat().st_size > 0
                    and path.stat().st_mtime >= self._started_wall_time - 2.0
                ):
                    paths.append(path)
            except OSError:
                continue
        return paths

    def _maybe_auto_complete_verified_subgoal_plan_items(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        if _is_drawing_task(self.task):
            return result
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            return result
        if tool_name != "execute_subgoal" and not (
            isinstance(payload, dict) and payload.get("auto_triggered_after_focus")
        ):
            return result
        plan = getattr(self.session, "plan", None)
        steps = getattr(plan, "steps", None) or []
        if not steps:
            return result
        if not isinstance(payload, dict) or payload.get("verified") is not True:
            return result
        subgoal = str(
            payload.get("subgoal")
            if payload.get("auto_triggered_after_focus")
            else args.get("subgoal") or payload.get("subgoal") or ""
        )
        completed: list[dict[str, Any]] = []
        clipboard_token = str(payload.get("clipboard_replace_token") or "").strip()
        if payload.get("clipboard_replace_applied") and clipboard_token:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                if clipboard_token not in goal and not re.search(
                    r"replace|paste|替换|粘贴",
                    goal,
                    re.IGNORECASE,
                ):
                    continue
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "verified_clipboard_delivery",
                    })
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source="controller.clipboard_replace_verified",
                        plan=plan.snapshot(),
                    )
                break
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            goal = str(step.get("goal") or "")
            if not self._subgoal_matches_plan_goal(subgoal, goal):
                break
            if not self._plan_check_satisfied_by_subgoal(step.get("check"), payload):
                break
            if plan.complete(index):
                completed.append({"index": index, "goal": goal[:160], "source": "verified_subgoal"})
                BUS.publish(
                    "agent_plan",
                    run_id=self.run_id,
                    action="auto_complete",
                    index=index,
                    source="execute_subgoal.verified",
                    plan=plan.snapshot(),
                )
            break

        fresh_paths = self._fresh_explicit_output_paths()
        if fresh_paths:
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or step.get("done"):
                    continue
                goal = str(step.get("goal") or "")
                key = self._plan_text_key(goal)
                if not any(term in key for term in (
                    "确认文件", "核验文件", "验证文件", "保存成功", "fileexists", "verifysaved",
                )):
                    continue
                if step.get("check") and not self._plan_check_satisfied_by_subgoal(step.get("check"), payload):
                    continue
                if plan.complete(index):
                    completed.append({
                        "index": index,
                        "goal": goal[:160],
                        "source": "fresh_output_file",
                        "path": str(fresh_paths[0]),
                    })
                    BUS.publish(
                        "agent_plan",
                        run_id=self.run_id,
                        action="auto_complete",
                        index=index,
                        source="execute_subgoal.fresh_output_file",
                        plan=plan.snapshot(),
                    )
        if not completed:
            return result
        payload["auto_completed_plan_items"] = completed
        payload["plan_after_auto_complete"] = plan.render()
        if plan.all_done():
            payload["plan_all_done"] = True
        payload["fast_path_advice"] = (
            str(payload.get("fast_path_advice") or "").strip()
            + " Verified controller evidence automatically advanced matching plan items; "
            + ("the plan is complete." if plan.all_done() else "continue with the next unfinished item.")
        ).strip()
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            is_observe=result.is_observe,
            terminal="done" if plan.all_done() else result.terminal,
            summary=result.summary,
            replay=result.replay,
            image_b64=result.image_b64,
        )

    @classmethod
    def _looks_like_new_blank_plan_goal(cls, goal: str) -> bool:
        key = cls._plan_text_key(goal)
        wants_new = any(term in key for term in (
            "new", "create", "blank", "新建", "创建", "新增",
        ))
        document_kind = any(term in key for term in (
            "document", "doc", "workbook", "spreadsheet", "presentation",
            "文档", "文字", "工作簿", "表格", "演示文稿", "空白",
        ))
        return wants_new and document_kind

    @staticmethod
    def _looks_like_unsaved_document_title(title: str) -> bool:
        text = re.sub(r"\s+", "", str(title or "").casefold())
        if not text or any(term in text for term in (
            "新建文档", "newdocument", "template", "模板", "homepage", "首页",
        )):
            return False
        return bool(re.search(
            r"(?:untitled|document|book|workbook|presentation|"
            r"文字文稿|演示文稿|工作簿|无标题|未命名|文档)\s*\d*",
            text,
            re.IGNORECASE,
        ))

    def _current_new_document_plan_item(self) -> tuple[int, dict[str, Any]] | None:
        plan = getattr(self.session, "plan", None)
        for index, step in enumerate(getattr(plan, "steps", None) or [], start=1):
            if not isinstance(step, dict) or step.get("done"):
                continue
            if self._looks_like_new_blank_plan_goal(str(step.get("goal") or "")):
                return index, step
            break
        return None

    @staticmethod
    def _text_mutation_literals(tool_name: str, args: dict[str, Any]) -> list[str]:
        values: list[str] = []
        if tool_name in {"type_text", "type_into_element"}:
            values.append(str(args.get("text") or args.get("text_input") or args.get("value") or ""))
        elif tool_name == "execute_subgoal":
            input_spec = args.get("input") if isinstance(args.get("input"), dict) else {}
            values.append(str(
                input_spec.get("text")
                or input_spec.get("text_input")
                or input_spec.get("value")
                or ""
            ))
        elif tool_name == "run_action_batch":
            for action in args.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                name = str(action.get("action") or action.get("tool") or "")
                action_args = action
                if not name and len(action) == 1:
                    name, nested = next(iter(action.items()))
                    action_args = nested if isinstance(nested, dict) else {"text": nested}
                if name in {"type_text", "type_into_element"}:
                    values.append(str(
                        action_args.get("text")
                        or action_args.get("text_input")
                        or action_args.get("value")
                        or ""
                    ))
        return [value for value in values if value]

    def _task_allows_repeated_text_mutation(self) -> bool:
        text = str(self.task or "").casefold()
        return bool(re.search(
            r"\b(?:twice|repeat|again|each|both|every)\b|"
            r"重复|两次|二次|两遍|分别|各自|每个|每一|都输入|再输入一次",
            text,
        ))

    def _duplicate_text_mutation_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if self._task_allows_repeated_text_mutation():
            return None
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        for literal in self._text_mutation_literals(tool_name, args):
            key = re.sub(r"\s+", " ", literal).strip()
            if len(key) < 8:
                continue
            previous = next((
                item
                for item in reversed(self._delivered_text_mutations)
                if item.get("text") == key
                and int(item.get("hwnd") or 0) == hwnd
                and self._tool_sequence - int(item.get("sequence") or 0) <= 12
                and time.monotonic() - float(item.get("at") or 0.0) <= 120.0
            ), None)
            if previous is None:
                continue
            payload = {
                "ok": True,
                "verified": False,
                "mutation_skipped": True,
                "text_already_delivered": True,
                "text_length": len(literal),
                "target_hwnd": hwnd or None,
                "original_tool": tool_name,
                "needs_observe": True,
                "controller_state": {
                    "phase": "needs_observe",
                    "verified": False,
                    "needs_observe": True,
                    "needs_llm_reason": False,
                },
                "fast_path_advice": (
                    "The exact text was already delivered successfully to this target window "
                    "during the current run. The duplicate mutation was skipped. Do not type it "
                    "again; use read_text/observe or continue with formatting/save."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        return None

    @staticmethod
    def _format_mutation_key(args: dict[str, Any]) -> str:
        specs = args.get("formats")
        if not isinstance(specs, list):
            single = args.get("format")
            specs = [single] if isinstance(single, dict) else []
        normalized = [spec for spec in specs if isinstance(spec, dict)]
        if not normalized:
            return ""
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _duplicate_format_mutation_result(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult | None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return None
        key = self._format_mutation_key(args)
        if not key:
            return None
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        previous = next((
            item
            for item in reversed(self._applied_format_mutations)
            if item.get("key") == key
            and int(item.get("hwnd") or 0) == hwnd
            and self._tool_sequence - int(item.get("sequence") or 0) <= 16
            and time.monotonic() - float(item.get("at") or 0.0) <= 180.0
        ), None)
        if previous is None:
            return None
        save_dispatched = bool(previous.get("save_dispatched"))
        payload = {
            "ok": True,
            "verified": False,
            "format_applied": True,
            "format_already_applied": True,
            "mutation_skipped": True,
            "save_dispatched": save_dispatched,
            "postcondition_pending": True,
            "needs_llm_reason": False,
            "target_hwnd": hwnd or None,
            "controller_state": {
                "phase": "needs_observe",
                "verified": False,
                "needs_observe": True,
                "needs_llm_reason": False,
            },
            "fast_path_advice": (
                "The identical rich-text formatting routine already ran successfully in this target "
                "window, so the duplicate toggle shortcuts were skipped. "
                + (
                    "Save was already dispatched; verify the saved artifact or call done."
                    if save_dispatched else
                    "Do not format again; issue only the requested Save action, then verify the artifact."
                )
            ),
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    def _remember_applied_format_mutation(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
    ) -> None:
        if tool_name not in {"execute_subgoal", "run_action_batch"}:
            return
        key = self._format_mutation_key(args)
        if not key:
            return
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("format_applied") is not True
            or payload.get("mutation_skipped") is True
        ):
            return
        self._applied_format_mutations.append({
            "key": key,
            "hwnd": int(getattr(self.session, "hwnd", 0) or 0),
            "sequence": self._tool_sequence,
            "at": time.monotonic(),
            "save_dispatched": bool(payload.get("save_dispatched")),
        })
        self._applied_format_mutations = self._applied_format_mutations[-12:]

    def _remember_delivered_text_mutations(
        self,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
    ) -> None:
        literals = self._text_mutation_literals(tool_name, args)
        if not literals:
            return
        try:
            payload = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("blocked") is True:
            return
        delivered = True
        if tool_name == "execute_subgoal":
            delivered = payload.get("input_applied") is True
        elif tool_name == "run_action_batch":
            delivered = any(
                isinstance(item, dict)
                and item.get("action") in {"type_text", "type_into_element"}
                and item.get("ok") is True
                for item in (payload.get("results") or [])
            )
        if not delivered:
            return
        hwnd = int(getattr(self.session, "hwnd", 0) or 0)
        now = time.monotonic()
        for literal in literals:
            key = re.sub(r"\s+", " ", literal).strip()
            if len(key) < 8:
                continue
            self._delivered_text_mutations.append({
                "text": key,
                "hwnd": hwnd,
                "sequence": self._tool_sequence,
                "at": now,
                "tool": tool_name,
            })
        self._delivered_text_mutations = self._delivered_text_mutations[-16:]

    def _track_new_document_plan_attempt(self, tool_name: str, args: dict[str, Any]) -> None:
        current = self._current_new_document_plan_item()
        if current is None:
            return
        index, step = current
        state = self._new_document_plan_state.setdefault(index, {
            "goal": str(step.get("goal") or ""),
            "baseline_title": "",
            "attempted": False,
        })
        snapshot = getattr(self.session, "snapshot", None)
        current_title = str(getattr(snapshot, "title", "") or "")
        if current_title and not state.get("baseline_title"):
            state["baseline_title"] = current_title

        if tool_name not in {"execute_subgoal", "run_action_batch", "click", "hotkey"}:
            return
        semantic = json.dumps(args, ensure_ascii=False, default=str)
        key = self._plan_text_key(semantic)
        explicit_new = any(term in key for term in (
            "new", "create", "blank", "新建", "创建", "空白文档",
        ))
        if tool_name == "hotkey":
            keys = {str(item).casefold() for item in (args.get("keys") or [])}
            explicit_new = explicit_new or (
                "n" in keys and bool(keys & {"ctrl", "control", "control_l", "control_r"})
            )
        if explicit_new:
            state["attempted"] = True

    def _maybe_auto_complete_new_document_plan_item(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResult:
        current = self._current_new_document_plan_item()
        if current is None:
            return result
        index, step = current
        state = self._new_document_plan_state.setdefault(index, {
            "goal": str(step.get("goal") or ""),
            "baseline_title": "",
            "attempted": False,
        })
        snapshot = getattr(self.session, "snapshot", None)
        current_title = str(getattr(snapshot, "title", "") or "")
        if current_title and not state.get("baseline_title"):
            state["baseline_title"] = current_title
            return result
        baseline = str(state.get("baseline_title") or "")
        if (
            not state.get("attempted")
            or not current_title
            or self._plan_text_key(current_title) == self._plan_text_key(baseline)
            or not self._looks_like_unsaved_document_title(current_title)
        ):
            return result

        plan = getattr(self.session, "plan", None)
        if plan is None or not plan.complete(index):
            return result
        completed = {
            "index": index,
            "goal": str(step.get("goal") or "")[:160],
            "source": "observed_new_document_title",
            "baseline_title": baseline,
            "current_title": current_title,
        }
        BUS.publish(
            "agent_plan",
            run_id=self.run_id,
            action="auto_complete",
            index=index,
            source="observed_new_document_title",
            plan=plan.snapshot(),
        )
        self._new_document_plan_state.pop(index, None)
        guidance = (
            f"Observed a newly created editable document title change from {baseline!r} "
            f"to {current_title!r}; plan item {index} is complete. Continue with the next "
            "unfinished item and do not click New again."
        )
        try:
            payload = json.loads(result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            prior = payload.get("auto_completed_plan_items")
            completed_items = list(prior) if isinstance(prior, list) else []
            completed_items.append(completed)
            payload["auto_completed_plan_items"] = completed_items
            payload["plan_after_auto_complete"] = plan.render()
            payload["fast_path_advice"] = (
                str(payload.get("fast_path_advice") or "").strip() + " " + guidance
            ).strip()
            text = json.dumps(payload, ensure_ascii=False, default=str)
        else:
            text = (
                (result.text or "").rstrip()
                + "\n\n[plan auto-complete] "
                + guidance
                + "\n"
                + plan.render()
            )
        self._trace_debug("new_document_plan_auto_completed", {
            "run_id": self.run_id,
            "tool": tool_name,
            "args": args,
            "completed": completed,
        })
        return ToolResult(
            text,
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
        text = self.task.casefold()
        has_repo = bool(re.search(
            r"\b(?:clone|github|repo|repository|open[- ]?source)\b|"
            r"克隆|开源|仓库",
            text,
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
            self._tool_sequence += 1
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
                BUS.publish("agent_tool", run_id=self.run_id, action_id=tc.id,
                            tool=tc.function.name, error=str(exc))
                continue

            args = self._maybe_expand_execute_subgoal_draws_from_plan(tc.function.name, args)
            args = self._maybe_upgrade_exact_save_as(tc.function.name, args)
            args = self._maybe_upgrade_spreadsheet_action_batch(tc.function.name, args)
            args = self._maybe_upgrade_pending_clipboard_replace(tc.function.name, args)
            args = self._maybe_upgrade_execute_subgoal_document_input(tc.function.name, args)
            args = self._maybe_upgrade_execute_subgoal_formats(tc.function.name, args)
            args = self._filter_repeated_draw_batch(tc.function.name, args)
            self._track_new_document_plan_attempt(tc.function.name, args)
            self.run_state.before_tool(tc.function.name, args)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            BUS.publish("agent_tool_start", run_id=self.run_id,
                        action_id=tc.id, tool=tc.function.name, args=args)
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
                        "apply_patch", "search_files", "search_text",
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
                result = self._drawing_destructive_clear_result(tc.function.name, args)
                if result is None:
                    result = self._duplicate_clipboard_delivery_result(tc.function.name, args)
                if result is None:
                    result = self._duplicate_text_mutation_result(tc.function.name, args)
                if result is None:
                    result = self._duplicate_format_mutation_result(tc.function.name, args)
                if result is None:
                    result = self._maybe_reuse_recent_visual_color_verify(tc.function.name, args)
                if result is None:
                    result = self._maybe_reuse_recent_draw_batch(tc.function.name, args)
                if result is None:
                    result = self._maybe_reuse_recent_draw_evidence(tc.function.name, args)
                if result is None:
                    result = self._dispatch_with_fast_visual_wrap(tc.function.name, args)
            result = self._maybe_deliver_clipboard_after_focus(tc.function.name, args, result)
            result = self._maybe_auto_complete_new_document_plan_item(tc.function.name, args, result)
            result = self._maybe_auto_complete_open_plan_item(tc.function.name, args, result)
            result = self._maybe_auto_complete_verified_subgoal_plan_items(tc.function.name, args, result)
            result = self._maybe_auto_complete_document_format_plan_items(tc.function.name, args, result)
            result = self._maybe_auto_complete_draw_plan_items(tc.function.name, args, result)
            result = self._maybe_auto_complete_desktop_drag_plan_item(tc.function.name, args, result)
            failure_recovery = None
            raw_result_text = result.text
            self._remember_delivered_text_mutations(
                tc.function.name,
                args,
                raw_result_text,
            )
            self._remember_applied_format_mutation(
                tc.function.name,
                args,
                raw_result_text,
            )
            try:
                failure_recovery = self._failure_recovery_layer.handle_tool_result(
                    tool_name=tc.function.name,
                    args=args,
                    result_text=result.text or "",
                    task=self.task,
                    session=self.session,
                    recent_results=self._recent_results,
                    visible_evidence_ledger=self._visible_evidence_ledger,
                    tool_history=self._tool_history,
                )
                if failure_recovery is not None:
                    result.text = attach_recovery_metadata(result.text or "", failure_recovery)
                    post_tool_hints.extend(failure_recovery.post_tool_hints)
                    BUS.publish(
                        "agent_recovery",
                        run_id=self.run_id,
                        tool=tc.function.name,
                        failure_recovery=failure_recovery.to_dict(),
                    )
            except Exception as exc:
                self._trace_debug("failure_recovery_error", {
                    "run_id": self.run_id,
                    "tool_call_id": tc.id,
                    "tool": tc.function.name,
                    "args": args,
                    "error": str(exc),
                })
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
                "raw_result_text": raw_result_text if raw_result_text != result.text else None,
                "summary": result.summary,
                "terminal": result.terminal,
                "replay": result.replay,
                "elapsed_ms": elapsed_ms,
                "failure_recovery": failure_recovery.to_dict() if failure_recovery else None,
            })
            ledger_len_before = len(self._visible_evidence_ledger)
            pending_controller_events = list(
                getattr(self.session, "_pending_controller_events", []) or []
            )
            self.session._pending_controller_events = []
            for event in pending_controller_events:
                if not isinstance(event, dict):
                    continue
                event_tool = str(event.get("tool") or "")
                event_args = event.get("args") if isinstance(event.get("args"), dict) else {}
                event_text = str(event.get("result_text") or "")
                if not event_tool:
                    continue
                self._record_step(event_tool, event_args, None)
                self._track_verification(event_tool, event_text)
                self._track_visible_evidence(event_tool, event_args, event_text)
                self._tool_history.append((event_tool, event_args, event_text))
                try:
                    event_data = json.loads(event_text or "{}")
                except (json.JSONDecodeError, TypeError):
                    event_data = {}
                packet = event_data.get("evidence_packet") if isinstance(event_data.get("evidence_packet"), dict) else {}
                visible_preview = str(packet.get("visible_text_preview") or "").strip()
                if visible_preview:
                    verify_texts = event_args.get("verify_texts")
                    query = str(verify_texts[0] or "") if isinstance(verify_texts, list) and verify_texts else ""
                    self._tool_history.append(("read_text", {"query": query}, visible_preview))
            self._record_step(tc.function.name, args, result.replay)
            self._track_verification(tc.function.name, result.text)
            self._track_visible_evidence(tc.function.name, args, result.text)
            result = self._maybe_auto_done_from_visible_evidence(
                tc.function.name,
                result,
                ledger_len_before,
            )
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
                "open_app",
                "run_action_batch",
                "execute_subgoal",
                "schedule_task",
                "list_scheduled_tasks",
                "cancel_scheduled_task",
                "coordinate_diagnostics",
                "coordinate_click_test",
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
                    limit = 1200 if tc.function.name in {
                        "list_windows",
                        "scan_windows",
                        "read_text",
                        "schedule_task",
                        "cancel_scheduled_task",
                        "coordinate_diagnostics",
                        "coordinate_click_test",
                    } else 600
                    if tc.function.name == "list_scheduled_tasks":
                        limit = 2400
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
                    "auto_done",
                    "structured_scene_verified",
                    "format_applied",
                    "format_verified",
                    "format_already_applied",
                    "mutation_skipped",
                    "save_dispatched",
                    "saved_paths",
                    "artifact_format_errors",
                    "failure_classification",
                    "failure_recovery",
                ):
                    if key in parsed_result:
                        result_meta[key] = parsed_result[key]
            if failure_recovery is not None:
                result_meta.setdefault(
                    "failure_classification",
                    failure_recovery.classification.to_dict(),
                )
                result_meta.setdefault(
                    "failure_recovery",
                    {
                        "status": failure_recovery.status.value,
                        "llm_required": failure_recovery.llm_required,
                        "retryable": failure_recovery.retryable,
                        "local_actions": failure_recovery.local_actions,
                        "post_tool_hint_count": len(failure_recovery.post_tool_hints),
                        "result_meta": failure_recovery.result_meta,
                    },
                )
            BUS.publish(
                "agent_tool",
                run_id=self.run_id,
                action_id=tc.id,
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
    def _prebind_initial_gui_target(self) -> None:
        """Open/focus a leading, explicit GUI application before LLM step 1."""
        if self._is_coding:
            return
        task = str(self.task or "").strip()
        if not re.match(
            r"^(?:严格.*?[:：]\s*)?(?:open|launch|start|run|打开|启动|运行|在|用)\s*",
            task,
            re.IGNORECASE,
        ):
            return
        from ..fast.subgoal import _initial_task_app_target

        target = _initial_task_app_target(task)
        if not target:
            return
        args = {"name": target}
        action_id = f"prebind-{uuid.uuid4().hex[:8]}"
        self.run_state.before_tool("open_app", args)
        BUS.publish(
            "agent_tool_start",
            run_id=self.run_id,
            action_id=action_id,
            tool="open_app",
            args=args,
            internal=True,
        )
        started = time.monotonic()
        result = self.session.dispatch("open_app", args)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self.run_state.after_tool("open_app", args, result.text)
        BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
        BUS.publish(
            "agent_tool",
            run_id=self.run_id,
            action_id=action_id,
            tool="open_app",
            result=result.text,
            elapsed_ms=elapsed_ms,
            internal=True,
        )
        self._record_step("open_app", args, result.replay)
        self._tool_history.append(("open_app", args, result.text or ""))
        self.messages.append({
            "role": "user",
            "content": (
                f"[controller target prebind] Requested leading application {target!r}. "
                f"open_app result: {(result.text or '')[:800]}. "
                "Continue in that bound window; do not inspect or mutate unrelated foreground windows."
            ),
        })

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
            communication_mode = _communication_mode(self.task)
            wants_decor = any(k in (self.task or "").lower() for k in (
                "表情", "emoji", "sticker", "表情包", "贴纸", "emoticon",
            ))
            if communication_mode == "draft":
                hint_body = (
                    "This is a draft-only communication task. Open the requested conversation, "
                    "put the requested text in the message input, and stop with it visibly drafted. "
                    "Do not click Send and do not press Enter."
                )
            elif payloads:
                core = payloads[0][:80]
                extra = "如果能清楚看到表情/贴纸入口，再额外尝试一次装饰。" if wants_decor else ""
                hint_body = (
                    f"先把要发送的正文 '{core}' 真正送达并重新 observe/read_text 验证。"
                    "如果界面里正文已经出现在输入框，就优先完成发送，不要为了装饰项在发送区附近反复试探模糊图标。"
                    f"{extra}"
                )
            elif communication_mode == "bounded_chat":
                hint_body = (
                    "这是普通聊天任务，按一轮往返有界执行：先打开目标聊天并发送一句自然开场，"
                    "等待对方出现一条新的可见回复，再基于对方回复自然回复一次。"
                    "第二条发送内容通过 read_text 出现在聊天记录后立即 done；"
                    "不要第一条发送后就结束，也不要无限等待。"
                )
            elif communication_mode == "continuous_chat":
                hint_body = (
                    "这是明确的持续聊天任务：等待可见新回复并基于最新内容继续回复。"
                    "每轮先用 read_text 确认新回复，再发送一条相关回复并重新验证。"
                    "只有用户实时要求停止或任务给定的结束条件成立时才结束；不要自行调用 done。"
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
                    "is open, prefer execute_subgoal with draw={'shape':'triangle','color':'blue'} for simple work. "
                    "For a detailed object or scene, submit one draws[] routine made from 8-30 primitives. "
                    "Each primitive supports shape triangle/rectangle/circle/ellipse/line/check, a semantic "
                    "part label, box coordinates, and normalized_strokes, where every coordinate is 0..1000 "
                    "relative to the detected canvas. For lines, box=[x1,y1,x2,y2] and box_mode='endpoints'; "
                    "for closed shapes, box=[x,y,width,height]. Label every detailed primitive with part. "
                    "For people/animals, use separate overlapping head and body shapes, keep facial details "
                    "inside the head, and connect limbs/tail to the body; avoid detached decoration. Use several "
                    "requested colors and include outline, face, limbs, and small details appropriate to the user's object. Never pass an object name such "
                    "as cat as shape, and never approximate a complex object with one freeform mark. The controller waits "
                    "for a ready canvas, selects requested colors when possible, draws, and returns "
                    "matched_visual_colors/visible_colors. "
                    "If several requested shapes/colors are already known, combine them in one draws[] "
                    "routine and then complete the affected plan items from that controller evidence. "
                    "Do not pre-explore toolbar text or add "
                    "text_appears checks for tool names like Pencil/Brush/Triangle. Use draw_strokes/"
                    "run_action_batch only when coordinates are already grounded or execute_subgoal "
                    "returns needs_llm_reason. In run_action_batch, strokes means physical screen pixels; "
                    "for 0..1000 canvas-relative points use normalized_strokes so the controller maps them "
                    "through the latest canvas region. Verify by pixel/color evidence, screenshot/nonvisual "
                    "summary, and requested file existence if saving is required. For a detailed or "
                    "quality-sensitive result, call vlm_verify with the user's visible criteria; a "
                    "structured primitive plan or pixel/color change is not semantic proof. Never add "
                    "save/export work unless the user requested an output file. Do not redraw after a "
                    "passing semantic verdict; complete the plan or done."
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
                    "Use open_app/list_windows/find_app/find_program/launch/focus_window/"
                    "observe/click/type_text/hotkey/drag/scroll. Shell is only for final read-only "
                    "verification such as Test-Path/Get-Content/Select-String after the GUI work."
                ),
            })
        if _is_multi_source_research_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Multi-source research guidance] Treat each page/source as a separate record. "
                    "After a source page exposes useful fields, call record_facts(subject, facts, "
                    "source_url, source_title, evidence) before navigating away. Use explicit "
                    "not-visible values instead of guessing. The per-run working set will be returned "
                    "after every update; use it to fill the destination in the requested entity order, "
                    "and never attach a field or URL from one source to another subject. Do not keep "
                    "observing a completed source once its required fields are recorded."
                ),
            })
            explicit_output_paths = _explicit_saved_paths(self.task)
            if explicit_output_paths:
                rendered_paths = "\n".join(
                    f"- {path}" for path in explicit_output_paths[:3]
                )
                self.messages.append({
                    "role": "user",
                    "content": (
                        "[Exact GUI output-path requirement] The task names exact output file paths:\n"
                        f"{rendered_paths}\n"
                        "Use the application's Save As/Export UI, focus the real filename field, "
                        "replace it with the exact full path, and submit the dialog. A different "
                        "auto-generated filename is not success. Do not complete the save plan item "
                        "until file_exists evidence for the exact path is true."
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
        is_gui_only_task = _is_gui_only_task(self.task)
        allow_shell_tools = self.allow_shell_tools
        if allow_shell_tools is None:
            allow_shell_tools = not (
                is_gui_only_task
                and _gui_only_forbids_shell(self.task)
            )
        # GUI-only runs may keep a tightly checked read-only shell verifier, but
        # file/search tools invite non-GUI shortcuts and hide fake success.
        allow_file_tools = (not is_gui_only_task) and allow_shell_tools
        tools = tool_schemas(
            planning=plan_on,
            coding_runtime=self._is_coding,
            allow_shell=allow_shell_tools,
            allow_files=allow_file_tools,
            profile=_schema_profile_for_task(self.task, is_coding=self._is_coding),
        )
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, has_experience=bool(hints),
                    agent="unified", runtime=self.runtime_profile.to_dict())
        try:
            raw_client = _client()
            client = build_model_adapter(raw_client, CONFIG.get("api", default={}))
            self._model_adapter = client
            self._prebind_initial_gui_target()
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
                    finish_reason = str(getattr(resp.choices[0], "finish_reason", "") or "")
                    if finish_reason == "length" and self._truncated_response_retries < 2:
                        self._truncated_response_retries += 1
                        # Do not carry a multi-thousand-token half-written file or
                        # pseudo tool call into every later prompt. It is neither a
                        # valid tool result nor useful evidence.
                        self.messages[-1] = {
                            "role": "assistant",
                            "content": "[model output truncated before a valid tool call; partial content omitted]",
                        }
                        self.messages.append({
                            "role": "user",
                            "content": (
                                "Your previous response hit the output limit before issuing a valid tool call. "
                                "Do not resend the whole artifact as prose or XML. Call a real tool now. "
                                "For a large file, write a concise working skeleton first, then use apply_patch "
                                "in small chunks and verify the resulting file."
                            ),
                        })
                        BUS.publish(
                            "agent_recovery",
                            run_id=self.run_id,
                            kind="truncated_tool_call",
                            attempt=self._truncated_response_retries,
                        )
                        continue
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

        # Record a reusable recipe (+ export canonical Automation IR) ONLY for
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
            # A trusted success becomes replayable canonical Automation IR
            # linked to the recipe, so a future identical task can reuse_skill it.
            script_name = ""
            if verified_run:
                try:
                    from ..recorder import trace as _trace
                    script_path = _trace.export_automation_ir(self.run_id)
                    script_name = script_path.name
                except (FileNotFoundError, ValueError) as exc:
                    logger.debug("成功流程无可重放步骤，跳过导出: %s", exc)
                except OSError as exc:
                    logger.warning("成功流程导出脚本失败(IO): %s", exc)
            experience.record(self.task, app_hint, self._steps_log,
                              verified=verified_run, script=script_name)
        try:
            compiler.ingest_run(self)
        except Exception as exc:
            logger.debug("memory graph ingest failed: %s", exc)
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
