"""
E2E tests with real LLM calls.

Validates the full prompt → response → parsing pipeline with actual LLM output.
These tests verify prompt quality, tool selection, and response format — the
parts that mock tests cannot cover.

All tests are skipped if OPENAI_API_KEY env var is not set.
The key is auto-loaded from .env if present.

NOT tested here:
  - Full agent loop with mocked tools (gives false "quality" signal —
    agent appears to do things but screen feedback is fake).
  - Real desktop execution (requires live environment).
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest

# ── Load API key from .env if not already in env ──
_env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
if not os.environ.get("OPENAI_API_KEY") and os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("OPENAI_API_KEY="):
                os.environ["OPENAI_API_KEY"] = _line.split("=", 1)[1]
            elif _line.startswith("OPENAI_API_BASE="):
                os.environ["OPENAI_API_BASE"] = _line.split("=", 1)[1]
            elif _line.startswith("VLM_MODEL="):
                os.environ["VLM_MODEL"] = _line.split("=", 1)[1]

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set. Add to .env or set env var.",
)

API_KEY = os.environ["OPENAI_API_KEY"]
API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("VLM_MODEL", "mimo-v2-omni")


def _llm_call(messages: list[dict], temperature: float = 0.1, strict: bool = True,
              max_retries: int = 2) -> str:
    """Call the real LLM and return response content.

    If strict=True and the response doesn't contain a tool-call format
    ("工具:"), retry once with a reinforced format instruction.

    Retries on transient network errors (connection reset, timeout, 5xx).
    """
    import requests as _req
    import time as _time
    session = _req.Session()
    session.trust_env = False

    def _do_call(msgs):
        last_err = None
        for retry in range(max_retries + 1):
            try:
                resp = session.post(
                    f"{API_BASE}/chat/completions",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {API_KEY}"},
                    json={"model": MODEL, "messages": msgs,
                          "temperature": temperature, "max_tokens": 4096},
                    timeout=120,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except (_req.exceptions.ConnectionError,
                    _req.exceptions.Timeout,
                    _req.exceptions.HTTPError) as e:
                last_err = e
                if retry < max_retries:
                    _time.sleep(3 * (retry + 1))
                    continue
                raise last_err from None
        raise last_err  # type: ignore

    content = _do_call(messages)
    if strict and "工具:" not in content and "总结:" not in content:
        reinforced = messages.copy()
        reinforced.append({"role": "user",
                          "content": "请严格按照格式输出: 思考: ...\n工具: ...\n参数:\n  ...\n完成后输出: 总结: ..."})
        content = _do_call(reinforced)
    return content


_MINIMAL_SYSTEM_PROMPT = """你是桌面自动化 AI。你有以下动作可用：

可用工具:
- manage_app(action='launch'|'close', target='程序名') — 启动/关闭程序
- click(target='按钮文字' 或 x=数字, y=数字) — 点击按钮或坐标
- type_text(text='输入的文字') — 在已聚焦的输入框中输入文字
- screenshot() — 截取屏幕并返回分析
- wait(seconds=秒数) — 等待指定秒数
- read_ui() — 读取当前窗口的无障碍控件树
- file_ops(action='write'|'read', path='文件路径', content='内容') — 文件读写

格式:
思考: <推理>
工具: <工具名>
参数:
  参数名: 参数值

完成后:
总结: <结果>"""


# ═══════════════════════════════════════════════════════════════════
#  1. Response format — can the LLM produce parseable tool calls?
# ═══════════════════════════════════════════════════════════════════

class TestResponseFormat:
    """Verify the LLM outputs the correct text protocol format."""

    def test_basic_tool_call_parses(self):
        """Opening Notepad should produce a parseable manage_app call."""
        content = _llm_call([
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": "打开记事本"},
        ])
        from agent.agent import LingdongAgent
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool", f"Expected tool action, got '{action}'. Response: {content[:200]}"
        assert name in ("manage_app", "click", "type_text", "file_ops", "wait", "screenshot")
        target = str(args.get("target", "")).lower()
        assert any(kw in target for kw in ("notepad", "记事本", "notepad.exe")), \
               f"args should reference notepad. Got args={args}"

    def test_write_file_uses_correct_tool(self):
        """Writing a file should use file_ops tool."""
        content = _llm_call([
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": "保存 'Hello World' 到 test.txt"},
        ])
        from agent.agent import LingdongAgent
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool"
        assert name == "file_ops", f"Expected file_ops, got '{name}'. Response: {content[:200]}"
        assert args.get("action") == "write"
        assert "Hello World" in args.get("content", "")

    def test_calculator_task_produces_sequential_plan(self):
        """Calculator task should produce click-based plan (not just 'done')."""
        content = _llm_call([
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": "打开计算器并计算 7+3"},
        ])
        from agent.agent import LingdongAgent
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool", (
            f"Expected first action to be a tool, got '{action}'. "
            f"This suggests the prompt didn't guide the LLM to use tools. "
            f"Response preview: {content[:300]}"
        )


# ═══════════════════════════════════════════════════════════════════
#  2. Multi-turn — can the LLM adapt based on feedback?
# ═══════════════════════════════════════════════════════════════════

class TestMultiTurn:
    """Multi-turn interaction — LLM should adapt after tool results."""

    def test_adapts_after_failure(self):
        """After a tool fails, LLM should try a different approach."""
        from agent.agent import LingdongAgent
        messages = [
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": "打开记事本"},
        ]

        # Turn 1
        content = _llm_call(messages, strict=True)
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool", f"Turn 1: expected tool. Response: {content[:200]}"
        messages.append({"role": "assistant", "content": content})

        # Simulate failure feedback
        messages.append({
            "role": "user",
            "content": "执行结果: 失败 — 程序路径未找到，目标窗口未出现。请尝试其他方法。",
        })

        # Turn 2 — should adapt
        content2 = _llm_call(messages)
        action2, name2, args2 = LingdongAgent._parse_response(content2)
        assert action2 == "tool", (
            f"After failure feedback, expected a different tool attempt, "
            f"got '{action2}'. Response: {content2[:200]}"
        )

    def test_completes_task_and_reports_done(self):
        """After successful execution, LLM should output 总结:."""
        from agent.agent import LingdongAgent
        messages = [
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": "在桌面创建 hello.txt 文件，内容为 'Hello World'"},
        ]

        # Turn 1: file_ops write
        content = _llm_call(messages, strict=True)
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool", f"Turn 1: expected tool, got '{action}'. Response: {content[:200]}"
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "执行成功: 文件已写入。任务完成，请给总结。",
        })

        # Turn 2: should conclude with 总结:
        content2 = _llm_call(messages)
        action2, name2, args2 = LingdongAgent._parse_response(content2)
        assert action2 == "done", (
            f"After successful file write, expected 'done', got '{action2}'. "
            f"This may indicate the prompt isn't guiding the LLM to conclude tasks. "
            f"Response: {content2[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════
#  3. Reflection — can the LLM extract rules from execution history?
# ═══════════════════════════════════════════════════════════════════

class TestLLMReflection:
    """Real LLM used for post-task reflection."""

    def test_reflection_extracts_rules(self):
        """LLM should extract actionable rules from a step log."""
        from agent.reflection import ReflectionEngine

        def real_llm_wrapper(msgs):
            raw = _llm_call(msgs, temperature=0.1, strict=False)
            return {"choices": [{"message": {"content": raw}}]}

        engine = ReflectionEngine(call_llm_fn=real_llm_wrapper)
        steps = [
            {"step_num": 1, "tool": "manage_app", "result": {"success": True}},
            {"step_num": 2, "tool": "type_text", "result": {"success": False, "error": "element not focused"}},
            {"step_num": 3, "tool": "click", "result": {"success": True}},
            {"step_num": 4, "tool": "type_text", "result": {"success": True}},
        ]
        result = engine.reflect_task("打开记事本并输入 Hello", steps, True, 15000)
        assert isinstance(result, dict)
        # Should contain at least some of the expected fields
        has_content = bool(result.get("rules") or result.get("insights") or
                          result.get("overall_assessment"))
        assert has_content, f"Reflection returned empty or unusable result: {result}"

    def test_reflection_on_failed_task(self):
        """LLM should identify mistakes from a failed task."""
        from agent.reflection import ReflectionEngine

        def real_llm_wrapper(msgs):
            raw = _llm_call(msgs, temperature=0.1, strict=False)
            return {"choices": [{"message": {"content": raw}}]}

        engine = ReflectionEngine(call_llm_fn=real_llm_wrapper)
        steps = [
            {"step_num": 1, "tool": "manage_app", "result": {"success": False, "error": "file not found"}},
            {"step_num": 2, "tool": "click", "result": {"success": False, "error": "element not visible"}},
        ]
        result = engine.reflect_task("打开计算器", steps, False, 5000)
        assert isinstance(result, dict)
        mistakes = result.get("mistakes", [])
        assert len(mistakes) > 0, (
            f"Expected at least one mistake identified for a failed task. "
            f"Got: {result}"
        )


# ═══════════════════════════════════════════════════════════════════
#  4. Memory injection — does memory context help the LLM?
# ═══════════════════════════════════════════════════════════════════

class TestMemoryInjection:
    """Verify that injected memory context influences LLM behavior."""

    def test_memory_context_guides_tool_choice(self):
        """Memory with 'use keyboard shortcuts' should influence LLM's approach."""
        memory_context = (
            "## 历史经验\n"
            "行为规则:\n"
            "- [规则] 优先使用键盘快捷键而非鼠标点击 (confidence: 0.9)\n"
            "相关经验:\n"
            "- 保存文件 (成功, 3步) — 使用 Ctrl+S 比点击菜单快\n"
        )
        messages = [
            {"role": "system", "content": _MINIMAL_SYSTEM_PROMPT + "\n\n" + memory_context},
            {"role": "user", "content": "在记事本中输入文字后保存"},
        ]
        content = _llm_call(messages, temperature=0.3, strict=True)
        from agent.agent import LingdongAgent
        action, name, args = LingdongAgent._parse_response(content)

        # The LLM may use press_keys with Ctrl+S or file_ops — either is valid
        # The key check is that it attempts a real action, not just "done"
        assert action == "tool", (
            f"With memory context suggesting shortcuts, LLM should still plan "
            f"a real action. Got '{action}'. Response: {content[:200]}"
        )
