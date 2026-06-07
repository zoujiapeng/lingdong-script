"""
System prompt constants and model detection for the agent runtime.
Extracted from agent.py to reduce module size.
"""

from __future__ import annotations


# ── Multimodal Model Detection ─────────────────────────────

_MULTIMODAL_MODELS = frozenset({
    "mimo-v2-omni", "mimo-omni", "mimo-v2", "mimo-pro",
    "gpt-4o", "gpt-4o-mini", "gpt-4v", "gpt-4-vision",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-4", "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
    "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash",
    "qwen-vl", "qwen2-vl", "glm-4v", "glm-4v-plus",
    "deepseek-vl2", "deepseek-vl",
})

_MULTIMODAL_PREFIXES = ("mimo-", "gpt-4", "claude-3", "claude-4",
                        "claude-opus", "claude-sonnet", "claude-haiku",
                        "gemini-", "qwen-vl", "qwen2-vl", "glm-4v",
                        "deepseek-vl")


def is_multimodal_model(model_name: str) -> bool:
    """Detect if a model supports image understanding."""
    name = model_name.strip().lower()
    if name in _MULTIMODAL_MODELS:
        return True
    for prefix in _MULTIMODAL_PREFIXES:
        if name.startswith(prefix):
            return True
    if "deepseek" in name and not any(vl in name for vl in ("vl", "vision")):
        return False
    if "vision" in name or "multimodal" in name or "omni" in name:
        return True
    return False


# ── System Prompt Constants ────────────────────────────────

SCRIPTED_MODE_INSTRUCTIONS = """
[脚本执行模式已开启]

你也可以编写 Python 脚本来完成桌面操作，使用 `run_python_script(script="...")` 工具。

什么时候用脚本：
- 需要启动程序 → 等待 → 操作 → 验证 的完整流程
- 需要条件判断或循环的重复操作
- 简单的点击/输入不适合的场景

脚本注意事项：
- 系统已预装 pywinauto, pyautogui, psutil, time, os, subprocess 等常见库，直接 import 即可
- 不需要 pip install，系统会自动处理依赖
- 每个脚本建议控制在 50 行以内
- 脚本中适当加 time.sleep() 等待界面响应
- 每个脚本执行完成后用 print() 输出结果报告
"""

CHAT_MODE_INSTRUCTIONS = """
[对话模式已开启]

你可以边执行任务边与用户对话。当用户发来消息时：
- 如果是询问状态 → 简洁回答当前进展
- 如果是指引反馈 → 调整执行策略后继续
- 如果是新任务 → 完成当前步骤后执行新任务

对话模式下，你可以用自然语言回复用户，不需要每次都用工具。
用户消息在「执行结果」之后出现。按需处理即可。
"""

TEXT_PROTOCOL_FORMAT_INSTRUCTIONS = """

[Text protocol format - strict]
Use one or more tool blocks. Prefer this ASCII format because it is robust for
DeepSeek and other text-only models:

Tool: click
Args:
  target: #3

Tool: type_text
Args:
  text: hello

When the task is finished:
Done:
Summary: what was completed

Chinese labels are also accepted: tools/args can be written as 工具/参数/总结.
Do not return JSON unless the tool argument itself is JSON text.
"""
