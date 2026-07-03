"""Agent loop: deepseek tool-calling with WinPilot-specific adaptations.

DeepSeek adaptations (per official docs + live testing):
- ``reasoning_content`` must be stripped before sending history back
- append-only history keeps the prompt-cache prefix warm; old bulky tool
  results (observe / read_text / list_windows) are folded to one-line
  summaries, keeping only the 2 most recent full observations
- malformed tool-call JSON is bounced back to the model for self-repair
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
from .task_rules import (
    _blocks_store_search_action,
    _blocks_upload_media_analysis,
    _gui_only_shell_allowed,
    _is_coding_task,
    _is_communication_task,
    _is_drawing_task,
    _is_gui_only_task,
    _is_upload_publish_task,
    _quoted_payloads,
    _required_image_paths,
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
# DeepSeek pricing (¥ per 1M tokens). Adjust if the published rates change.
_PRICE_CACHE_MISS_PER_M = 2.0
_PRICE_CACHE_HIT_PER_M = 0.2
_PRICE_OUTPUT_PER_M = 8.0
_ACTION_TOOLS = set(_RUN_ACTION_TOOLS)


def _client():
    from openai import OpenAI

    api = CONFIG.get("api", default={})
    if not api.get("api_key"):
        raise RuntimeError("config.json 缺少 api.api_key")
    return OpenAI(base_url=api["base_url"], api_key=api["api_key"], timeout=120)


def _main_model_has_vision() -> bool:
    """True when the main API model natively supports image input.

    Plain MiMo chat models such as mimo-v2.5-pro reject image input on the
    OpenAI-compatible endpoint. Only explicitly multimodal/vision model names
    should receive image_url content. DeepSeek chat models are text-only in
    the API path we tested.
    """
    api = {
        "provider": CONFIG.get("api", "provider", default="auto"),
        "base_url": CONFIG.get("api", "base_url", default=""),
        "model": CONFIG.get("api", "model", default=""),
    }
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
        self._escalate_next = False                  # use stronger model on next _chat
        self._consecutive_unverified = 0             # steps without a verified=true action
        self._last_reflect_step = 0                  # step number of last reflect injection
        self.status = "pending"                     # running/done/fail/reply/stopped/error
        self.result_text = ""
        self.usage_total = empty_usage_total()
        self._interjections: list[str] = []          # mid-run user hints
        self._interject_lock = threading.Lock()
        self._llm_trace_index = 0                    # local forensic trace files
        self.max_steps_override = max_steps_override
        self.run_state = RunStateMachine()
        # Coding tasks are excluded from the reusable experience system.
        self._is_coding = _is_coding_task(task)

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
                            image_b64: str = "") -> None:
        # NOTE: mime API does not support multipart tool results (image_url in tool role).
        # Always use plain text; screenshot is injected as a separate user message via
        # _inject_observe_screenshot when image_b64 is empty (fallback path).
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
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
        profile = getattr(client, "profile", model_profile_from_config(api))
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
        self._trace_debug("llm_request", {
            "run_id": self.run_id,
            "task": self.task,
            "provider": profile.provider,
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
                    "provider": profile.provider,
                    "model": model,
                    "reason": "stop_event",
                })
                return None  # abandon the in-flight request (it finishes unused)
            thread.join(timeout=0.2)
        if "exc" in box:
            self._trace_debug("llm_error", {
                "run_id": self.run_id,
                "provider": profile.provider,
                "model": model,
                "error": str(box["exc"]),
            })
            raise box["exc"]
        if box.get("resp") is not None:
            self._trace_debug("llm_response", {
                "run_id": self.run_id,
                "provider": profile.provider,
                "model": model,
                "response": box.get("resp"),
            })
        return box.get("resp")

    def _track_usage(self, resp) -> None:
        api = CONFIG.get("api", default={})
        adapter = getattr(self, "_model_adapter", None)
        profile = getattr(adapter, "profile", model_profile_from_config(api))
        step_usage = accumulate_usage(
            self.usage_total,
            resp,
            profile=profile,
            api=api,
        )
        if not step_usage:
            return
        BUS.publish("agent_usage", run_id=self.run_id,
                    step_usage=step_usage,
                    total=dict(self.usage_total))

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
        try:
            data = json.loads(result_text or "{}")
        except (json.JSONDecodeError, TypeError):
            return (result_text or "")[:1200]
        if not isinstance(data, dict):
            return (result_text or "")[:1200]
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
        """Let the fast controller verify standalone visual mutations.

        The model often calls draw/drag directly instead of using
        run_action_batch. Wrapping only these high-value visual mutations gives
        them the same cheap region-level verification without turning every
        click or keystroke into noisy visual evidence.
        """
        if tool_name not in {"draw_strokes", "drag"}:
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
        source_child = next((item for item in children if item.get("action") == tool_name), {})
        auto_child = next((item for item in children if item.get("action") == "auto_visual_verify"), {})
        source_data = source_child.get("data") if isinstance(source_child.get("data"), dict) else {}
        auto_data = auto_child.get("data") if isinstance(auto_child.get("data"), dict) else {}

        wrapped: dict[str, Any] = {
            "ok": bool(data.get("ok")) if "ok" in data else not bool(data.get("error")),
            "wrapped_by_fast_controller": True,
            "tool": tool_name,
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
            "sync": False,
        }
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

    def _handle_tool_calls(self, tool_calls) -> str | None:
        """Dispatch each tool call; returns terminal status if any."""
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

            self.run_state.before_tool(tc.function.name, args)
            BUS.publish("run_state", run_id=self.run_id, **self.run_state.to_dict())
            BUS.publish("agent_tool_start", run_id=self.run_id,
                        tool=tc.function.name, args=args)
            started = time.monotonic()
            blocked_store_action = _blocks_store_search_action(self.task, tc.function.name, args)
            blocked_upload_action = _blocks_upload_media_analysis(self.task, tc.function.name, args)
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
            elif (
                _is_gui_only_task(self.task)
                and tc.function.name in {
                    "run_shell", "run_detached", "write_file", "read_file", "list_dir",
                    "apply_patch", "search_files",
                }
                and not (
                    tc.function.name == "run_shell"
                    and _gui_only_shell_allowed(str(args.get("command", "")))
                )
            ):
                result = ToolResult(
                    f"GUI-only mode blocked {tc.function.name}: use GUI tools "
                    "such as launch/focus_window/observe/click/type_text/hotkey "
                    "for the work. Shell is allowed only for final Test-Path/"
                    "Get-Content/Select-String verification."
                )
            else:
                result = self._dispatch_with_fast_visual_wrap(tc.function.name, args)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.run_state.after_tool(tc.function.name, args, result.text, result.terminal)
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
            # Keep recent tool outputs as evidence for the done-gate judge
            # (esp. run_shell stdout / verify results — deterministic proof
            # the GUI screen can't show).
            evidence_tools = {
                "run_shell",
                "run_detached",
                "search_files",
                "list_windows",
                "scan_windows",
                "read_text",
                "find_app",
                "find_program",
                "run_action_batch",
                "zoom",
                "screenshot",
            }
            if (
                tc.function.name in evidence_tools
                or '"verified"' in (result.text or "")
                or "visual_change_detected" in (result.text or "")
            ):
                if tc.function.name in {"run_action_batch", "zoom", "screenshot", "draw_strokes", "drag"}:
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
                    "controller_state",
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
                terminal=result.terminal,
                replay=result.replay,
            )
            self._append_tool_result(tc.id, result.text, summary=result.summary)
            if result.is_observe:
                # Inject screenshot as separate user message (mime does not support
                # multipart tool results, so we cannot inline image_b64 in tool role).
                self._inject_observe_screenshot()
            if result.terminal:
                self.result_text = result.text
                return result.terminal
        return None

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        self.status = "running"
        max_steps = int(self.max_steps_override or CONFIG.get("agent", "max_steps", default=40))
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
            main_model_has_vision=_main_model_has_vision())
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
                    "[Drawing/canvas task guidance] The goal is visible pixels, not just tool selection. "
                    "Work in batches: observe ONCE to locate the canvas/work area, plan the whole figure "
                    "(all strokes), then commit them in as few calls as possible using draw_strokes "
                    "(many separate strokes in one call) rather than one drag per LLM round-trip. Once canvas "
                    "coordinates are grounded, use run_action_batch for the immediate action+verification "
                    "sequence, or call draw_strokes directly; standalone draw_strokes is automatically "
                    "wrapped by the fast controller. Do not pre-explore toolbars unless direct strokes "
                    "fail or the active mode is clearly wrong. For simple "
                    "shapes or marks, prefer direct strokes on the located canvas before spending steps on "
                    "toolbar shape menus; use toolbar/tool selection mainly when direct strokes report no "
                    "visual change or the current tool is clearly wrong. Do not attach text_appears expects "
                    "such as Pencil/Brush/Triangle to toolbar clicks; many drawing tool icons have no stable "
                    "OCR text. Verify by canvas pixel change and nonvisual summaries first. For simple "
                    "requested figures, a coherent visible outline plus visual_change_detected=true is "
                    "enough; if the tool result includes fast_path_advice, call done instead of adding a "
                    "separate zoom/observe round. Only zoom/observe again when the result says "
                    "needs_observe=true, visual_change_detected=false, or the task requires semantic visual "
                    "inspection. Do not redraw just to make the result thicker or prettier. "
                    "Read the tool result: visual_change_detected=true means the strokes likely changed the "
                    "visible surface; false means you probably drew on a toolbar, popup, inactive surface, "
                    "or with a non-drawing mode. If a stroke batch reports visual_change_detected=false, do "
                    "NOT start probing colors or coordinates — first make sure a drawing tool (Pencil/Brush) "
                    "is actually selected by clicking it, click once on the canvas to focus it, then retry. "
                    "If a popup, color "
                    "panel, or text toolbar blocks the canvas, press Esc or click back on the canvas and "
                    "continue. Do not call done until the drawing is visibly non-empty and any requested "
                    "output image file has been saved and verified to exist. If the user requested "
                    "specific colors, select those colors in the GUI and verify the saved image has "
                    "visible pixels in those colors before done."
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
                    "prepare the work. Use list_windows/find_app/find_program/launch/focus_window/"
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
        tools = tool_schemas(planning=plan_on)
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, has_experience=bool(hints))
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
        # Score feedback for whatever recipes were injected this run.
        if self.status in ("done", "fail", "error"):
            experience.feedback(self._hint_ids, success=(self.status == "done"))

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
