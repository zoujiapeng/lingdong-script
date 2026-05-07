"""
流程图脚本生成功能
Agent 内部调用：将自然语言目标转为可执行的流程图脚本（伪代码）
"""
import json
import os
import time
from features.registry import feature, P, TC, FeatureCategory as F

GENERATION_PROMPT = """You are an automation script generator. Given a user's goal, generate a step-by-step pseudocode script.

AVAILABLE COMMANDS:
- 点击 (x, y) — click at coordinates
- 输入 "text" [位置 (x,y)] — type text, optionally at a position
- 按键 键1[+键2+...] — keyboard shortcut, e.g. 按键 ctrl+c
- 等待 N秒 — wait N seconds
- 滚动 (x, y) 次数 [方向] — scroll at position
- 文字点击 "text" [相似度=N] — find text via OCR and click
- 点击图片 "path" [相似度=N] — find image and click
- 截图 [区域] [保存到 "path"] — take screenshot
- 运行命令 "command" [超时=N] — run a CLI command and WAIT for it to finish (use for scripts, compilers, git, npm, etc.)
- 启动 "program" — LAUNCH a GUI application (notepad, calc, chrome) and continue immediately (NON-BLOCKING)
- 读取文件 "path" — read file content
- 写入文件 "path" "content" — write file content
- 聚焦 "title" — bring a window to front by title
- 如果 条件 → 执行 A 否则 执行 B — conditional
- 循环 N次 → ... — repeat N times
- 子流程 "name" — call a sub-flow

IMPORTANT RULES:
1. Do NOT use `运行命令` to launch GUI apps (notepad, calc, mspaint, chrome, explorer) — use `启动` instead
2. Use `运行命令` ONLY for CLI commands that exit after completion (git, npm, dir, echo)
3. Do NOT use numbering (1. 2. 3.) in the pseudocode. Each step on its own line, no prefixes.
4. After launching a GUI app, always add a `等待 1-2秒` step to let it open, then optionally `聚焦 "title"` to ensure focus.

Respond ONLY with a JSON object (no markdown, no extra text):
{
  "explanation": "brief explanation of what the script does",
  "pseudocode": "the step-by-step pseudocode"
}"""


@feature(
    name="generate_flow_script",
    display_name="生成流程图脚本",
    description="【Agent 内部】将自然语言目标转为可执行的流程图脚本（伪代码）。适用于需要多步骤操作、定时任务、条件判断的复杂场景。返回生成的伪代码",
    category=F.SYSTEM,
    params=[
        P("goal", "str", "目标描述", example="每天早上9点打开记事本输入当天日期"),
    ],
    returns="dict{success, pseudocode, explanation} - 生成的脚本",
    tags=["agent", "flow", "generation"],
)
def generate_flow_script(goal: str) -> dict:
    if not goal.strip():
        return {"success": False, "error": "goal 不能为空"}

    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    api_base = (os.environ.get("LLM_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "deepseek-chat"

    if not api_key:
        return {"success": False, "error": "未配置 API 密钥（设置 ANTHROPIC_API_KEY 环境变量或在应用设置中配置）"}

    try:
        import requests as _req

        resp = _req.post(
            f"{api_base}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": GENERATION_PROMPT},
                    {"role": "user", "content": goal},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 2048,
                "temperature": 0.1,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        pseudocode = parsed.get("pseudocode", "")
        explanation = parsed.get("explanation", "")

        # Normalize: if LLM returned a list of lines, join them
        if isinstance(pseudocode, list):
            pseudocode = "\n".join(str(line) for line in pseudocode)

        if not pseudocode:
            return {"success": False, "error": "AI 未能生成伪代码"}

        return {
            "success": True,
            "result": {
                "pseudocode": pseudocode,
                "explanation": explanation,
                "model": model,
            },
        }
    except ImportError:
        return {"success": False, "error": "需要 requests 库"}
    except Exception as e:
        return {"success": False, "error": f"生成失败: {e}"}
