"""Coding sub-agent loop.

A self-contained tool-calling loop with file/repo tools (coding_tools.py),
routed to for coding tasks. The GUI agent (loop.AgentRunner / tools.py) is left
completely untouched and lean. Reuses loop.py's LLM client, history sanitizer,
pricing, and the stop-aware threaded chat pattern; emits the same BUS events
(agent_start/step/tool/usage/done + chat) so the existing webui renders it.

Coding tasks are NOT recorded into the reusable experience system (a run_shell
sequence for one repo is meaningless to replay elsewhere) — there is simply no
experience.record call here.
"""
from __future__ import annotations

import json
import platform
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import CONFIG, TRACES_DIR
from ..utils.log import BUS, logger
from . import loop as _loop
from .coding_tools import CodingSession, coding_tool_schemas

_CODING_SYSTEM = """你是 WinPilot 的【编程子agent】——一名在工作区目录里干活的命令行工程师。\
你通过文件/仓库工具完成编程任务，不操作图形界面。

# 工作流（必须遵守）
1. 先 list_dir / read_file / search_text 摸清项目结构与技术栈（package.json/requirements.txt/go.mod 等），再动手
2. 改代码：小改用 apply_patch（精确、唯一的查找替换；find 要带足够上下文保证唯一）；
   整文件重写用 write_file。改完务必 read_file 复看，确认改对了
3. 用 run_shell 装依赖/构建/测试/运行/git——以真实退出码和输出判断成败，**绝不假设成功**
4. 遇报错：看 stderr/traceback 定位到 文件:行号 → 读上下文 → 提出修复 → 改 → 重跑验证（红→绿闭环），别盲改
5. 克隆 GitHub/开源项目但用户没给 URL：先 github_search(项目名) 找仓库，选最匹配的 clone_url；
   不要在本地目录里找一个还没克隆的项目，也不要停下来问用户网址
6. 同一处试 2 次没好就换角度（依赖版本/环境变量/路径/编码）
7. 完成调 done(summary)；确认无解调 fail(reason)
   - done 的 summary 里【务必写清】：项目完整路径、入口文件、如何运行（启动命令），
     方便之后"打开/运行它"时能直接找到

# 注意
- PowerShell 不支持 && 链式，多命令用 ; 分隔；切目录用 run_shell 的 cwd 参数
- 改/删/覆盖文件前先 read_file 确认是目标文件；不可逆危险操作(删大量数据/push --force)先 fail 说明
- 你是无人值守的：信息不全就按该生态最常见约定决断，不要停下反问

工作区根目录：{workspace}
本机：{os}"""


_CODING_SYSTEM += """

Safety boundary for generic coding tasks:
- Prefer read-only inspection and runnable verification (`--help`, `--version`, tests, build).
- Do not rewrite config/credential files such as `.env`, tokens, secrets, or credentials unless the user explicitly asked to configure/edit them.
- If a command needs a missing API key or credential, report the exact required variable and stop or mark partial success; do not invent or rewrite config.
"""


def workspace_dir() -> Path:
    ws = CONFIG.get("coding", "workspace", default="") or ""
    return Path(ws).expanduser() if ws else Path.home()


class CodingRunner:
    """One coding task execution. Same public interface as AgentRunner
    (run / run_id / status / result_text / interject) so the router + IPC
    adapters treat them interchangeably."""

    def __init__(self, task: str, stop_event: threading.Event | None = None,
                 session_context: list | None = None) -> None:
        self.task = task
        self.run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.stop_event = stop_event or threading.Event()
        self.workspace = workspace_dir()
        self.session = CodingSession(self.workspace, self.stop_event, task=task)
        self.messages: list[dict] = []
        self.status = "pending"
        self.result_text = ""
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cache_hit_tokens": 0, "cost_cny": 0.0}
        self._interjections: list[str] = []
        self._lock = threading.Lock()
        self._llm_trace_index = 0
        self._tool_history: list[tuple[str, dict[str, Any], str]] = []
        self._completion_rejects = 0

    def interject(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            with self._lock:
                self._interjections.append(text)

    def _drain(self) -> None:
        with self._lock:
            pending = self._interjections
            self._interjections = []
        for text in pending:
            self.messages.append({
                "role": "user",
                "content": f"[用户实时指示] {text}\n（优先按字面遵循；要求停止/取消就立即停下，不要自行换方式硬推进）",
            })
            BUS.publish("chat", run_id=self.run_id, role="user", content=text, interject=True)

    def _trace_debug(self, kind: str, payload: dict[str, Any]) -> None:
        if not CONFIG.get("agent", "llm_trace", default=True):
            return
        try:
            self._llm_trace_index += 1
            run_dir = TRACES_DIR / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "_", kind).strip("_") or "event"
            path = run_dir / f"{self._llm_trace_index:03d}_{safe_kind}.json"
            path.write_text(
                json.dumps(_loop.AgentRunner._jsonable(payload), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("coding LLM trace write failed: %s", exc)

    def _chat(self, client, tools: list[dict]):
        """Stop-aware threaded completion (abandons on stop) — same pattern as
        loop.AgentRunner._chat so Stop is responsive during the LLM call."""
        api = CONFIG.get("api", default={})
        model = api.get("model", "deepseek-v4-flash")
        box: dict[str, Any] = {}
        self._trace_debug("llm_request", {
            "run_id": self.run_id,
            "task": self.task,
            "agent": "coding",
            "model": model,
            "temperature": float(api.get("temperature", 0.2)),
            "max_tokens": int(api.get("max_tokens", 4096)),
            "messages": self.messages,
            "tools": tools,
        })

        def worker() -> None:
            last_exc: Exception | None = None
            for attempt in range(1, _loop._API_RETRIES + 1):
                if self.stop_event.is_set():
                    return
                try:
                    box["resp"] = client.chat.completions.create(
                        model=model, messages=self.messages, tools=tools,
                        temperature=float(api.get("temperature", 0.2)),
                        max_tokens=int(api.get("max_tokens", 4096)))
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning("coding API 调用失败 (第%d次): %s", attempt, exc)
                    if attempt < _loop._API_RETRIES:
                        time.sleep(1.5 * attempt)
            box["exc"] = RuntimeError(f"API 连续 {_loop._API_RETRIES} 次失败: {last_exc}")

        thread = threading.Thread(target=worker, daemon=True, name="coding-llm")
        thread.start()
        while thread.is_alive():
            if self.stop_event.is_set():
                self._trace_debug("llm_abandoned", {
                    "run_id": self.run_id,
                    "agent": "coding",
                    "model": model,
                    "reason": "stop_event",
                })
                return None
            thread.join(timeout=0.2)
        if "exc" in box:
            self._trace_debug("llm_error", {
                "run_id": self.run_id,
                "agent": "coding",
                "model": model,
                "error": str(box["exc"]),
            })
            raise box["exc"]
        if box.get("resp") is not None:
            self._trace_debug("llm_response", {
                "run_id": self.run_id,
                "agent": "coding",
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
        miss = max(0, prompt - cache_hit)
        cost = (miss / 1e6 * _loop._PRICE_CACHE_MISS_PER_M
                + cache_hit / 1e6 * _loop._PRICE_CACHE_HIT_PER_M
                + completion / 1e6 * _loop._PRICE_OUTPUT_PER_M)
        self.usage_total["prompt_tokens"] += prompt
        self.usage_total["completion_tokens"] += completion
        self.usage_total["cache_hit_tokens"] += cache_hit
        self.usage_total["cost_cny"] = round(self.usage_total["cost_cny"] + cost, 4)
        BUS.publish("agent_usage", run_id=self.run_id, total=dict(self.usage_total))

    def _handle_tool_calls(self, tool_calls) -> str | None:
        for tc in tool_calls:
            if self.stop_event.is_set():
                return "stopped"
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("参数必须是 JSON 对象")
            except (json.JSONDecodeError, ValueError) as exc:
                self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": f"工具参数 JSON 解析失败: {exc}。请重新调用并给出合法 JSON。"})
                self._trace_debug("tool_arg_error", {
                    "run_id": self.run_id,
                    "agent": "coding",
                    "tool_call_id": tc.id,
                    "tool": tc.function.name,
                    "raw_arguments": tc.function.arguments,
                    "error": str(exc),
                })
                BUS.publish("agent_tool", run_id=self.run_id, tool=tc.function.name, error=str(exc))
                continue
            started = time.monotonic()
            result = self.session.dispatch(tc.function.name, args)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._trace_debug("tool_result", {
                "run_id": self.run_id,
                "agent": "coding",
                "tool_call_id": tc.id,
                "tool": tc.function.name,
                "args": args,
                "result_text": result.text,
                "summary": result.summary,
                "terminal": result.terminal,
                "elapsed_ms": elapsed_ms,
            })
            self._tool_history.append((tc.function.name, args, result.text or ""))
            self._tool_history = self._tool_history[-40:]
            BUS.publish("agent_tool", run_id=self.run_id, tool=tc.function.name, args=args,
                        result_preview=result.text[:300],
                        elapsed_ms=elapsed_ms,
                        terminal=result.terminal)
            # The UI may show a preview/summary, but the model must receive the
            # full tool result. Otherwise a successful search/read can look
            # "folded" to the LLM and it will repeat or hallucinate missing data.
            self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                  "content": result.text})
            if result.terminal:
                if result.terminal == "done" and self._reject_incomplete_run_task():
                    continue
                self.result_text = result.text
                return result.terminal
        return None

    def _requires_run_attempt(self) -> bool:
        text = self.task.lower()
        has_repo = any(k in text for k in (
            "clone", "github", "repo", "repository", "open-source", "opensource",
            "\u514b\u9686", "\u5f00\u6e90", "\u4ed3\u5e93",
        ))
        wants_run = any(k in text for k in (
            "run", "start", "launch", "serve", "build", "test", "verify",
            "\u8dd1\u8d77\u6765", "\u8fd0\u884c", "\u542f\u52a8", "\u6253\u5f00",
            "\u6784\u5efa", "\u6d4b\u8bd5", "\u9a8c\u8bc1",
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

    def run(self) -> dict[str, Any]:
        self.status = "running"
        max_steps = int(CONFIG.get("coding", "max_steps", default=30))
        system = _CODING_SYSTEM.format(workspace=self.workspace, os=platform.platform())
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": self.task}]
        tools = coding_tool_schemas()
        BUS.publish("agent_start", run_id=self.run_id, task=self.task,
                    max_steps=max_steps, agent="coding", has_experience=False)
        try:
            client = _loop._client()
            for step in range(1, max_steps + 1):
                if self.stop_event.is_set():
                    self.status = "stopped"
                    break
                self._drain()
                resp = self._chat(client, tools)
                if resp is None:
                    self.status = "stopped"
                    break
                self._track_usage(resp)
                msg = resp.choices[0].message
                reasoning = getattr(msg, "reasoning_content", None) or ""
                self.messages.append(_loop._sanitize_assistant(msg))

                if not msg.tool_calls:
                    BUS.publish("agent_step", run_id=self.run_id, step=step,
                                reasoning="", content="", n_tool_calls=0)
                    proposed = msg.content or ""
                    if self._reject_incomplete_run_task():
                        BUS.publish("chat", run_id=self.run_id, role="assistant",
                                    content=proposed[:500], reasoning=reasoning[:800])
                        continue
                    self.status = "reply"
                    self.result_text = proposed
                    BUS.publish("chat", run_id=self.run_id, role="assistant",
                                content=self.result_text, reasoning=reasoning[:800])
                    break

                BUS.publish("agent_step", run_id=self.run_id, step=step,
                            reasoning=reasoning[:800], content=(msg.content or "")[:800],
                            n_tool_calls=len(msg.tool_calls))
                terminal = self._handle_tool_calls(msg.tool_calls)
                if terminal:
                    self.status = terminal
                    break
            else:
                self.status = "fail"
                self.result_text = f"达到最大步数 {max_steps} 仍未完成"
        except Exception as exc:
            logger.exception("coding agent 运行异常")
            self.status = "error"
            self.result_text = str(exc)
            BUS.publish("agent_error", run_id=self.run_id, error=str(exc))

        BUS.publish("agent_done", run_id=self.run_id, status=self.status,
                    result=self.result_text[:500], steps=0, usage=dict(self.usage_total))
        return {"run_id": self.run_id, "status": self.status,
                "result": self.result_text, "usage": dict(self.usage_total)}
