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
from ..perception import fusion, vision_cv, vlm
from ..utils.log import BUS, logger
from . import playbooks, prompts, session as session_mod
from .planner import Plan, make_plan, planning_enabled, should_plan_upfront
from .tools import ToolResult, ToolSession, tool_schemas

_KEEP_FULL_OBSERVATIONS = 2
_API_RETRIES = 3
# Fold old observations only past this size (~12K tokens × ~4 chars/token).
# Below it, history stays append-only so the DeepSeek prefix cache hits ~100%.
_CONTEXT_FOLD_THRESHOLD_CHARS = 48000
# DeepSeek pricing (¥ per 1M tokens). Adjust if the published rates change.
_PRICE_CACHE_MISS_PER_M = 2.0
_PRICE_CACHE_HIT_PER_M = 0.2
_PRICE_OUTPUT_PER_M = 8.0
_ACTION_TOOLS = {"launch", "click", "type_text", "hotkey", "scroll", "drag",
                 "focus_window", "wait", "tray_click", "run_shell"}


def _is_gui_only_task(task: str) -> bool:
    text = (task or "").casefold()
    return (
        bool(re.search(r"\bgui[- ]only\b", text))
        or "must use gui" in text
        or "gui方式" in text
        or "gui 方式" in text
        or "可见gui" in text
        or "可见 gui" in text
        or "通过可见" in text
        or "必须通过可见" in text
        or "必须通过gui" in text
        or "必须通过 gui" in text
        or "不要用脚本" in text
        or "不能用脚本" in text
        or "别用脚本" in text
        or "不要使用脚本" in text
    )


_DRAWING_TASK_RE = re.compile(
    r"draw|drawing|paint|sketch|canvas|whiteboard|"
    r"\u753b|\u753b\u56fe|\u7ed8\u56fe|\u6d82\u9e26|\u753b\u677f|\u767d\u677f",
    re.IGNORECASE,
)


def _is_drawing_task(task: str) -> bool:
    return bool(_DRAWING_TASK_RE.search(task or ""))


def _required_image_paths(task: str) -> list[str]:
    paths = re.findall(
        r"[A-Za-z]:\\[^\r\n\"'<>|]+?\.(?:png|jpg|jpeg|bmp|gif)",
        task or "",
        flags=re.IGNORECASE,
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for path in paths:
        value = path.strip().rstrip(".,;:，。；：)")
        key = value.casefold()
        if value and key not in seen:
            cleaned.append(value)
            seen.add(key)
    return cleaned


_DRAWING_COLOR_TERMS = {
    "red": ("red", "\u7ea2", "\u7ea2\u8272"),
    "blue": ("blue", "\u84dd", "\u84dd\u8272"),
    "green": ("green", "\u7eff", "\u7eff\u8272"),
    "yellow": ("yellow", "\u9ec4", "\u9ec4\u8272"),
    "orange": ("orange", "\u6a59", "\u6a59\u8272"),
    "purple": ("purple", "\u7d2b", "\u7d2b\u8272"),
    "black": ("black", "\u9ed1", "\u9ed1\u8272"),
}


def _required_drawing_colors(task: str) -> list[str]:
    text = (task or "").casefold()
    colors: list[str] = []
    for color, terms in _DRAWING_COLOR_TERMS.items():
        if any(term in text for term in terms):
            colors.append(color)
    return colors


def _gui_only_shell_allowed(command: str) -> bool:
    low = (command or "").casefold()
    verification_markers = ("test-path", "get-content", "select-string")
    mutation_or_ui_automation = (
        "set-content", "add-content", "out-file", "new-item", "copy-item",
        "move-item", "remove-item", "start-process", "sendkeys", "clipboard",
        "windows.automation", "wscript.shell", "shell.application",
    )
    return (
        any(marker in low for marker in verification_markers)
        and not any(marker in low for marker in mutation_or_ui_automation)
    )


def _is_coding_task(task: str) -> bool:
    """Coding/software tasks are EXCLUDED from the reusable experience system
    and routed to the coding sub-agent. A recorded run for one repo/bug is
    meaningless to replay on another — solved fresh each time.

    Detection (intent-aware, so "打开/玩 X游戏" stays a GUI task while "做一个
    X游戏" goes to the coding agent):
    - an open/launch/play intent at the start → never coding (GUI)
    - any strong, unambiguous coding signal → coding
    - a "build/write a <software-noun>" pattern → coding
    """
    text = (task or "").strip()
    low = text.lower()
    if _OPEN_INTENT.match(text):
        return False
    if any(k in low for k in _CODING_STRONG):
        return True
    return bool(_CODING_BUILD.search(text))


_COMMUNICATION_HINTS = (
    "发信息", "发消息", "发送", "回复", "私信", "聊天", "邮件",
    "message", "send", "reply", "email", "chat",
)


def _is_communication_task(task: str) -> bool:
    text = (task or "").lower()
    return any(hint in text for hint in _COMMUNICATION_HINTS)


def _quoted_payloads(task: str) -> list[str]:
    matches = re.findall(r'"([^"]{1,120})"|“([^”]{1,120})”|「([^」]{1,120})」', task or "")
    payloads: list[str] = []
    for groups in matches:
        value = next((g for g in groups if g), "").strip()
        if value:
            payloads.append(value)
    return payloads


# Open/launch/play intents → GUI, even when the object is a game/app/program.
_OPEN_INTENT = re.compile(r"^(打开|开启|启动|运行|玩|播放|关闭|切换|最小化|最大化|登录|搜索)")
# Strong, unambiguous coding signals (substring match, lowercased).
_CODING_STRONG = (
    "调试", "debug", "重构", "refactor", "仓库", "repo", "git", "github", "clone",
    "克隆", "bug", "报错", "异常", "traceback", "编译", "build ", "单元测试", "pytest",
    "npm", "pnpm", "yarn", "cargo", "dotnet", ".net", "代码", "源码", "函数", "脚本",
    "这个项目", "项目里", "写代码", "写个程序", "写一个程序", "实现一个", "api 接口", "接口",
    "数据库", "前端", "后端", "部署",
)
# "build/write a <software>" → coding (window between verb and noun for Chinese).
_CODING_BUILD = re.compile(
    r"(做|写|开发|实现|生成|创建|编写|搭建|搭|建|帮我做|帮我写)"
    r"[^。,，;；\n]{0,12}"
    r"(游戏|小游戏|网页|网站|页面|程序|软件|脚本|应用|小程序|html|网址|界面|插件|工具|"
    r"爬虫|机器人|\bbot\b|\bapp\b|计算器|时钟|待办|todo|博客|商城|登录页|表单)",
    re.IGNORECASE,
)


def _client():
    from openai import OpenAI

    api = CONFIG.get("api", default={})
    if not api.get("api_key"):
        raise RuntimeError("config.json 缺少 api.api_key")
    return OpenAI(base_url=api["base_url"], api_key=api["api_key"], timeout=120)


def _required_visible_payloads(task: str) -> list[str]:
    """Extract literal text that a GUI writing/typing task must make visible."""
    text = task or ""
    low = text.lower()
    line_re = (
        r"\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+"
        r"\u884c\s*[=:\uff1a\uff1d]\s*([^\uff0c,\u3002\uff1b;\r\n]+)"
    )
    payloads = [m.strip() for m in re.findall(line_re, text) if m.strip()]
    if any(trigger in low for trigger in ("type", "input", "write", "fill")):
        payloads.extend(_quoted_payloads(text))
    seen: set[str] = set()
    unique: list[str] = []
    for payload in payloads:
        payload = payload.strip()
        if payload and payload not in seen:
            unique.append(payload)
            seen.add(payload)
    return unique


def _gui_search_payloads(task: str) -> list[str]:
    text = task or ""
    payloads: list[str] = []
    patterns = (
        r"(?:search|look up)\s+([A-Za-z0-9][^,.;\r\n]{1,80})",
        r"(?:\u641c\u7d22|\u67e5\u627e)\s*([^\uff0c,\u3002\uff1b;\r\n]{1,80})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            value = str(match).strip()
            value = re.split(
                r"\s+(?:and|then|in|on|at)\s+|(?:\u5e76|\u7136\u540e|\u6253\u5f00|\u64ad\u653e)",
                value,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            if value:
                payloads.append(value.strip('"“” '))
    seen: set[str] = set()
    out: list[str] = []
    for payload in payloads:
        key = payload.casefold()
        if key not in seen:
            out.append(payload)
            seen.add(key)
    return out


def _is_gui_search_task(task: str) -> bool:
    text = (task or "").lower()
    wants_open = any(k in text for k in ("open", "打开", "启动", "进入"))
    wants_search = any(k in text for k in ("search", "look up", "搜索", "查找"))
    app_surface = any(k in text for k in (
        "client", "store", "app", "steam", "douyin", "抖音", "网易云", "music",
        "客户端", "商店", "应用", "页面",
    ))
    return wants_open and wants_search and app_surface


def _is_store_search_task(task: str) -> bool:
    text = (task or "").lower()
    return _is_gui_search_task(task) and any(k in text for k in (
        "store", "shop", "\u5546\u5e97", "\u9875\u9762", "page",
    ))


def _blocks_store_search_action(task: str, tool_name: str, args: dict) -> str:
    if not _is_store_search_task(task):
        return ""
    command = str(args.get("command") or args.get("target") or "").casefold()
    if tool_name in {"launch", "run_shell"} and any(
        bad in command for bad in ("rungameid", "install/", "steam://run", "steam://install")
    ):
        return (
            "Store/search navigation task blocked a launch/install/run protocol. "
            "The user asked to search/open a store/page, not start or install the item. "
            "Open the app/store/search UI or a store/search deep link, then verify the "
            "requested query/page is visible."
        )
    return ""


def _is_upload_publish_task(task: str) -> bool:
    text = (task or "").casefold()
    explicit = any(k in text for k in (
        "upload", "publish", "post", "creator", "draft",
        "\u4e0a\u4f20", "\u53d1\u5e03", "\u6295\u7a3f", "\u4f5c\u54c1",
        "\u6807\u9898", "\u8349\u7a3f",
    ))
    media_file = any(ext in text for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"))
    platform_or_form = any(k in text for k in (
        "douyin", "tiktok", "creator", "upload", "publish", "post", "draft",
        "\u6296\u97f3", "\u521b\u4f5c\u8005", "\u53d1\u5e03", "\u4e0a\u4f20", "\u6807\u9898",
    ))
    return explicit or (media_file and platform_or_form)


def _blocks_upload_media_analysis(task: str, tool_name: str, args: dict) -> str:
    if not _is_upload_publish_task(task):
        return ""
    command = str(
        args.get("command")
        or args.get("script")
        or args.get("target")
        or args.get("name")
        or ""
    ).casefold()
    media_terms = (".mp4", ".mov", ".avi", ".mkv", ".webm")
    analysis_or_playback = (
        "ffprobe", "ffmpeg", "wmplayer", "windows media player", "opencv",
        "cv2", "extract frame", "showinfo", "start-process", "media player",
        "vlc", "potplayer", "mpv", "mplayer",
    )
    media_player_target = any(
        marker in command
        for marker in ("wmplayer", "windows media player", "media player", "vlc", "potplayer", "mpv")
    )
    has_media_file = any(term in command for term in media_terms)
    readonly_media_check = any(
        marker in command
        for marker in ("test-path", "get-item", "get-childitem", "resolve-path")
    )
    media_analysis_target = (
        has_media_file
        and any(marker in command for marker in analysis_or_playback)
    )
    if (
        tool_name in {"run_shell", "run_detached", "launch", "find_program"}
        and (
            media_player_target
            or media_analysis_target
            or (tool_name in {"run_shell", "run_detached", "launch"} and has_media_file and not readonly_media_check)
        )
    ):
        return (
            "Upload/publish task blocked media playback/analysis. The media file is an "
            "upload payload, not something to play, inspect, or understand. Verify the file "
            "exists if needed, then open the requested app/platform upload UI, choose the file, "
            "fill metadata, and stop before final publish."
        )
    return ""


def _sanitize_assistant(msg) -> dict[str, Any]:
    """Assistant message for history — WITHOUT reasoning_content."""
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
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


class AgentRunner:
    """One task execution. Designed to run on a background thread."""

    def __init__(self, task: str, stop_event: threading.Event | None = None,
                 session_context: list | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.session = ToolSession(stop_event=self.stop_event, task=task)
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
        self.status = "pending"                     # running/done/fail/reply/stopped/error
        self.result_text = ""
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cache_hit_tokens": 0, "cost_cny": 0.0}
        self._interjections: list[str] = []          # mid-run user hints
        self._interject_lock = threading.Lock()
        self._llm_trace_index = 0                    # local forensic trace files
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
                            summary: str = "") -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
        if summary:
            self._foldable.append((len(self.messages) - 1, summary))
            self._fold_old()

    def _fold_old(self) -> None:
        """Compress old bulky observations — but ONLY when the context is large.

        Folding mutates a historical message, which breaks DeepSeek's prefix
        cache from that point once. So we avoid it entirely while the context
        is small (the common case → near-100% cache hits), and only start
        folding the oldest observations once total size crosses a threshold.
        The newest _KEEP_FULL_OBSERVATIONS are always kept full.
        """
        def total_chars() -> int:
            return sum(len(str(m.get("content") or "")) for m in self.messages)

        while (
            len(self._foldable) > _KEEP_FULL_OBSERVATIONS
            and total_chars() > _CONTEXT_FOLD_THRESHOLD_CHARS
        ):
            idx, summary = self._foldable.pop(0)
            if not self.messages[idx]["content"].startswith("[已折叠]"):
                self.messages[idx]["content"] = summary

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
        self._trace_debug("llm_request", {
            "run_id": self.run_id,
            "task": self.task,
            "model": model,
            "temperature": float(api.get("temperature", 0.3)),
            "max_tokens": int(api.get("max_tokens", 4096)),
            "messages": self.messages,
            "tools": tools,
        })

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
                        time.sleep(1.5 * attempt)
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
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        cache_miss = max(0, prompt - cache_hit)
        step_cost = (
            cache_miss / 1_000_000 * _PRICE_CACHE_MISS_PER_M
            + cache_hit / 1_000_000 * _PRICE_CACHE_HIT_PER_M
            + completion / 1_000_000 * _PRICE_OUTPUT_PER_M
        )
        self.usage_total["prompt_tokens"] += prompt
        self.usage_total["completion_tokens"] += completion
        self.usage_total["cache_hit_tokens"] += cache_hit
        self.usage_total["cost_cny"] = round(self.usage_total["cost_cny"] + step_cost, 4)
        BUS.publish("agent_usage", run_id=self.run_id,
                    step_usage={
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "cache_hit_tokens": cache_hit,
                        "cost_cny": round(step_cost, 4),
                    },
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
        if not any(step.startswith("drag(") for step in self._steps_log):
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

            BUS.publish("agent_tool_start", run_id=self.run_id,
                        tool=tc.function.name, args=args)
            started = time.monotonic()
            blocked_store_action = _blocks_store_search_action(self.task, tc.function.name, args)
            blocked_upload_action = _blocks_upload_media_analysis(self.task, tc.function.name, args)
            if blocked_store_action or blocked_upload_action:
                result = ToolResult(blocked_store_action or blocked_upload_action)
            elif (
                self._drawing_should_stop_mutating()
                and tc.function.name in {"click", "drag", "type_text", "hotkey", "scroll"}
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
                result = self.session.dispatch(tc.function.name, args)
            elapsed_ms = int((time.monotonic() - started) * 1000)
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
            }
            if (
                tc.function.name in evidence_tools
                or '"verified"' in (result.text or "")
                or "visual_change_detected" in (result.text or "")
            ):
                limit = 1200 if tc.function.name in {"list_windows", "scan_windows", "read_text"} else 600
                self._recent_results.append((tc.function.name, (result.text or "")[:limit]))
                self._recent_results = self._recent_results[-6:]
            BUS.publish(
                "agent_tool",
                run_id=self.run_id,
                tool=tc.function.name,
                args=args,
                result_preview=result.text[:300],
                elapsed_ms=elapsed_ms,
                terminal=result.terminal,
                replay=result.replay,
            )
            self._append_tool_result(tc.id, result.text, summary=result.summary)
            if result.terminal:
                self.result_text = result.text
                return result.terminal
        return None

    # ------------------------------------------------------------- done-gate
    def _verify_done(self, client, steps_left: int = 99) -> bool:
        """Independently verify a ``done`` claim before accepting it (kills
        "fake done"). Re-observes the real screen and asks a skeptical judge
        call whether the task is genuinely complete. Returns True to accept,
        False to bounce the agent back to keep working.

        Fail-open: judge errors / unparseable output accept the done (never
        deadlock). Capped at config done_max_rejects bounces, after which the
        done is accepted but flagged unverified (won't be trusted as a skill).
        """
        if not CONFIG.get("agent", "verify_done", default=True):
            return True
        max_rejects = int(CONFIG.get("agent", "done_max_rejects", default=2))
        if self._done_rejects >= max_rejects or steps_left <= 2:
            # out of bounce budget (or out of step budget) → accept, mark unverified
            self._done_unverified = True
            return True

        # Fresh perception of the real final state (don't trust the model's belief).
        screen = ""
        try:
            hwnd = self.session.hwnd
            snap = fusion.observe(hwnd, include_icons=False) if hwnd else self.session.snapshot
            screen = snap.to_text(50) if snap else ""
        except Exception as exc:  # perception must never break the gate
            logger.debug("done 核验 observe 失败: %s", exc)

        recent = "；".join(self._steps_log[-6:]) or "(无动作记录)"
        evidence = "\n".join(f"- {name}: {out}" for name, out in self._recent_results) \
            or "(无)"
        if _is_drawing_task(self.task):
            stats = []
            for path in _required_image_paths(self.task):
                try:
                    item = vision_cv.image_ink_stats(path)
                    item["color_stats"] = vision_cv.image_color_stats(path)
                    stats.append(item)
                except Exception as exc:
                    stats.append({"path": path, "error": str(exc)})
            if stats:
                evidence += "\n- image_ink_stats: " + json.dumps(
                    stats, ensure_ascii=False
                )[:1200]
        required_payloads = _required_visible_payloads(self.task)
        if required_payloads:
            haystack = f"{screen}\n{evidence}"
            missing = [payload for payload in required_payloads if payload not in haystack]
            if missing:
                reason = (
                    "写入/输入任务缺少最终可见证据：最终 observe/read_text 或确定性证据里没有出现 "
                    + "、".join(missing[:3])
                )
                BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                self._done_rejects += 1
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[完成校验未通过] {reason}。请先在目标窗口真正输入这些文本，"
                        "然后重新 observe/read_text 验证文本可见，再调用 done。"
                    ),
                })
                logger.info("done 被快速核验拒绝(输入文本缺失): %s", reason)
                return False
        if _is_drawing_task(self.task):
            saw_drag = any(step.startswith("drag(") for step in self._steps_log)
            saw_visual_change = "visual_change_detected\": true" in evidence.lower()
            image_paths = _required_image_paths(self.task)
            missing_paths = [path for path in image_paths if not Path(path).exists()]
            required_colors = _required_drawing_colors(self.task)
            if not saw_drag:
                reason = (
                    "Drawing/canvas task has no recorded drag stroke. Tool selection or clicks "
                    "are not enough evidence that visible drawing occurred."
                )
                BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                self._done_rejects += 1
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[done check failed] {reason} Use drag on the canvas/work surface, "
                        "observe the result, then save/verify before done."
                    ),
                })
                return False
            if image_paths and missing_paths:
                reason = (
                    "Drawing/canvas task requested output image file(s), but they do not exist: "
                    + ", ".join(missing_paths[:3])
                )
                BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                self._done_rejects += 1
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[done check failed] {reason}. Save through the GUI, handle any "
                        "Save As/replace dialog, then verify file_exists before done."
                    ),
                })
                return False
            if image_paths and required_colors:
                missing_colors: dict[str, list[str]] = {}
                for path in image_paths:
                    try:
                        stats = vision_cv.image_color_stats(path)
                    except Exception as exc:
                        stats = {"path": path, "error": str(exc)}
                    colors = stats.get("colors", {}) if isinstance(stats, dict) else {}
                    for color in required_colors:
                        if not colors.get(color, {}).get("visible"):
                            missing_colors.setdefault(path, []).append(color)
                if missing_colors:
                    reason = (
                        "Drawing/canvas task requested specific colors, but the saved image lacks "
                        "visible color evidence: "
                        + "; ".join(
                            f"{Path(path).name}: {', '.join(colors)}"
                            for path, colors in list(missing_colors.items())[:3]
                        )
                    )
                    BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                    self._done_rejects += 1
                    self.messages.append({
                        "role": "user",
                        "content": (
                            f"[done check failed] {reason}. Use the drawing app's visible color "
                            "controls to select the requested colors, add visible strokes/shapes "
                            "in those colors, save again, and verify before done."
                        ),
                    })
                    return False
            if not saw_visual_change:
                self.messages.append({
                    "role": "user",
                    "content": (
                        "[drawing evidence warning] A drag occurred, but recent evidence does not "
                        "show visual_change_detected=true. Before done, observe or add another "
                        "visible stroke inside the canvas and verify the saved image exists."
                    ),
                })
        if _is_gui_search_task(self.task):
            payloads = _gui_search_payloads(self.task)
            missing = [
                payload for payload in payloads
                if payload.casefold() not in screen.casefold()
            ]
            if missing:
                reason = (
                    "GUI search/navigation task lacks final on-screen evidence. "
                    "The target search text/page is not visible in the current app window: "
                    + ", ".join(missing[:3])
                    + ". Local files, installed-game manifests, or shell output cannot replace "
                    "opening the requested app/client and searching inside its GUI."
                )
                BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                self._done_rejects += 1
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[done check failed] {reason} Open/focus the requested app, use its "
                        "visible search field or page navigation, then observe/read_text until "
                        "the target text is visible before done."
                    ),
                })
                return False
        if _is_communication_task(self.task):
            payloads = _quoted_payloads(self.task)
            if payloads and not any(payload in screen for payload in payloads):
                reason = (
                    "通信任务缺少最终可见证据：目标窗口当前画面没有出现要发送的消息文本，"
                    "不能把 click/type_text/hotkey 的 ok 当成发送成功"
                )
                BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
                self._done_rejects += 1
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[完成核验未通过] {reason}。请先打开目标会话/输入区，"
                        "发送后重新 observe 或 read_text，确认消息出现在目标会话中再 done。"
                    ),
                })
                logger.info("done 被快速核验拒绝(通信任务): %s", reason)
                return False
        api = CONFIG.get("api", default={})
        judge_model = (CONFIG.get("agent", "escalation_model", default="")
                       or api.get("done_judge_model") or api.get("model", "deepseek-v4-flash"))
        sys_p = (
            "你是严格、怀疑的任务完成核验员。判断用户任务是否【真正达成】，不要轻信 agent 的自述。"
            "重要：文件/文件夹/进程/命令类任务，以【确定性证据】(run_shell 输出、Test-Path/Exists 结果、"
            "verified 字段)为准——这类结果在桌面/后台,GUI 界面上【本来就看不到】,"
            "**绝不能因为‘最终界面没显示’就判未完成**。只有可视交互(网页填表/界面内容)才看界面。"
            "同理，窗口清点/状态检查类任务应优先相信 list_windows、scan_windows、read_text 的真实工具输出；"
            "scan_windows 可能会恢复原前台窗口，所以不能仅因当前前台不是目标窗口就判失败。"
            "对发消息/发送聊天/邮件/评论等对外通信任务，click/type_text/hotkey 的 ok 或 verified "
            "只证明动作执行，不证明消息已送达；必须有目标会话/收件上下文中的最终可见证据"
            "（例如聊天记录出现要发送的文本、输入框清空并记录出现、或应用明确发送成功状态）才可判 done。"
            '只输出 JSON: {"done": true/false, "reason": "简短中文理由"}。')
        user_p = (
            f"用户任务：{self.task}\n\n"
            f"agent 声称完成：{self.result_text[:200]}\n\n"
            f"已执行动作(最近)：{recent}\n\n"
            f"确定性证据(工具真实输出/验证结果)：\n{evidence}\n\n"
            f"当前前台界面元素(仅供参考,文件/进程类勿据此判负)：\n{screen[:1000]}")
        self._trace_debug("done_check_request", {
            "run_id": self.run_id,
            "model": judge_model,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_p},
            ],
        })
        try:
            resp = client.chat.completions.create(
                model=judge_model,
                messages=[{"role": "system", "content": sys_p},
                          {"role": "user", "content": user_p}],
                temperature=0.0, max_tokens=800,
            )
            self._trace_debug("done_check_response", {
                "run_id": self.run_id,
                "model": judge_model,
                "response": resp,
            })
            raw = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                is_done = False
                reason = "完成核验未返回可解析 JSON，不能接受为完成"
            else:
                try:
                    verdict = json.loads(m.group(0))
                    is_done = bool(verdict.get("done", False))
                    reason = str(verdict.get("reason", ""))[:200]
                except json.JSONDecodeError:
                    is_done = False
                    reason = "完成核验返回的 JSON 损坏，不能接受为完成"
        except Exception as exc:  # fail-open — never deadlock on judge errors
            logger.warning("done 核验调用失败，放行: %s", exc)
            self._trace_debug("done_check_error", {
                "run_id": self.run_id,
                "model": judge_model,
                "error": str(exc),
            })
            return True

        BUS.publish("done_check", run_id=self.run_id, passed=is_done, reason=reason)
        if is_done:
            self._done_verified = True  # an independent judge confirmed success
            return True
        self._done_rejects += 1
        self.messages.append({
            "role": "user",
            "content": f"[完成核验未通过] {reason}。任务尚未真正达成，请不要直接调 done——"
                       "先把缺的部分补完（必要时重新 observe / 用确定性 expect 验证），完成后再调 done。",
        })
        logger.info("done 被核验拦截(第%d次): %s", self._done_rejects, reason)
        return False

    # --------------------------------------------------------------- planning
    def _maybe_plan_upfront(self, client) -> None:
        """For clearly multi-step tasks, decompose once and inject the plan into
        the stable context prefix (cache-friendly — added before the loop, never
        rewritten). Trivial tasks skip this to avoid the extra LLM call."""
        if not should_plan_upfront(self.task):
            return
        api = CONFIG.get("api", default={})
        steps = make_plan(client, api.get("model", "deepseek-v4-flash"), self.task)
        if not steps:
            return
        self.session.plan = Plan(steps)
        self.messages.append({"role": "user", "content": self.session.plan.render()})
        BUS.publish("agent_plan", run_id=self.run_id, action="init",
                    plan=self.session.plan.snapshot())

    # ------------------------------------------------------------ world model
    def _surface_world_advisory(self) -> None:
        """Append a one-line world advisory to the tail of history (cache-safe,
        append-only) only when there's something worth telling the model, and
        publish compact world state for the UI/trace regardless."""
        if not self.session.world_enabled:
            return
        BUS.publish("world", run_id=self.run_id, **self.session.world.state())
        visual_note = self.session.take_visual_advisory()
        if visual_note:
            self.messages.append({
                "role": "user",
                "content": f"[visual state warning] {visual_note}",
            })
        note = self.session.world.advisory()
        if note:
            self.messages.append({"role": "user", "content": f"[系统观察] {note}"})
        self._maybe_structured_replan()

    def _maybe_structured_replan(self) -> None:
        """On the FIRST detection of being stuck, inject a structured reflection
        that forces a real change of approach (deeper than a one-line nudge):
        name the blocker, what's been tried, and a different method — and revise
        the plan. Edge-triggered (once per stuck episode) to avoid spam."""
        if not self.session.world_enabled:
            return
        stuck, why = self.session.world.take_stuck_signal()
        if not stuck:
            return
        plan_hint = ("用 revise_plan 重列一个【不同思路】的计划"
                     if planning_enabled() else "明确换一个完全不同的方法")
        self.messages.append({
            "role": "user",
            "content": (
                f"[强制重规划] 检测到卡住：{why}。停止重复当前做法，先想清楚再动：\n"
                f"1) 现在卡在哪一步/哪个子目标？2) 已经试过什么、为什么没用？"
                f"3) 有什么【完全不同】的办法（换工具/换路径/run_shell 命令行/find_program/重新 observe）？\n"
                f"然后{plan_hint}并按新计划执行。"),
        })
        BUS.publish("agent_recovery", run_id=self.run_id, kind="structured_replan", reason=why)
        logger.info("触发结构化重规划: %s", why)
        self._escalate_next = True  # let a stronger model pick the new approach

    def _maybe_inject_playbook(self) -> None:
        """Mid-run playbook rescue: if the current window title matches a
        playbook trigger (e.g. a save-as dialog appeared for a task whose text
        never mentioned saving), inject that playbook once."""
        title = self.session.snapshot.title if self.session.snapshot else ""
        book = playbooks.for_window_title(title, exclude=self._injected_playbooks)
        if book is None:
            return
        self._injected_playbooks.add(book.name)
        self.messages.append({
            "role": "user",
            "content": f"[情境提示] 检测到「{title}」相关界面，适用以下方法：\n{book.content}",
        })
        BUS.publish("playbook", run_id=self.run_id, name=book.name, trigger=title[:60])
        logger.info("运行中注入 playbook: %s (窗口: %s)", book.name, title[:40])

    # ------------------------------------------------------------------ run
    def run(self) -> dict[str, Any]:
        self.status = "running"
        max_steps = int(CONFIG.get("agent", "max_steps", default=40))
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
            style=str(CONFIG.get("agent", "prompt_style", default="full")))
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
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"[通信任务提示] 先把要发送的正文 '{core}' 真正送达并重新 observe/read_text 验证。"
                        "如果界面里正文已经出现在输入框，就优先完成发送，不要为了装饰项在发送区附近反复试探模糊图标。"
                        f"{extra}"
                    ),
                })
        # Session continuity: let referential follow-ups ("再来一次" etc.) resolve
        # against prior tasks this session. Injected right after the task so it's
        # in the cache-friendly prefix; gated by config.
        if _is_drawing_task(self.task):
            self.messages.append({
                "role": "user",
                "content": (
                    "[Drawing/canvas task guidance] The goal is visible pixels, not just tool selection. "
                    "Use observe to locate the canvas/work surface, then use drag strokes inside that "
                    "surface. After each drag, read the tool result: visual_change_detected=true means "
                    "the stroke likely changed the visible surface; false means you probably dragged on "
                    "a toolbar, popup, inactive surface, or with a non-drawing mode. If a popup, color "
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
        if self.session_context and CONFIG.get("agent", "session_memory", default=True):
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
            client = _client()
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
                if terminal == "done" and not self._verify_done(client, steps_left=max_steps - step):
                    continue  # done-gate rejected the claim → keep working
                if terminal:
                    self.status = terminal
                    break
                self._surface_world_advisory()
                self._maybe_inject_playbook()
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
