"""
流程图脚本生成功能
Agent 内部调用：将自然语言目标转为可执行的流程图脚本（伪代码）
"""
import json
import os
import time
from features.registry import feature, P, TC, FeatureCategory as F, registry

# ── Agent step type capabilities table ───────────────────────────────
# This drives both the LLM prompt AND is the source of truth for what
# the controller's _execute_step_action can handle.
# When adding a new step type, add it here + implement handler in controller.py.

AGENT_CAPABILITIES = [
    {"type": "click",       "pseudo": '点击 "目标"',       "desc": "点击屏幕上的文字/坐标目标"},
    {"type": "text_click",  "pseudo": '文字点击 "文本"',   "desc": "通过 OCR 查找文字并点击"},
    {"type": "image_click", "pseudo": '点击图片 "模板名"', "desc": "通过图像模板匹配并点击"},
    {"type": "type",        "pseudo": '输入 "文本"',       "desc": "输入文字（中文自动用剪贴板绕过输入法）"},
    {"type": "hotkey",      "pseudo": "按键 ctrl+c",        "desc": "按下快捷键组合"},
    {"type": "wait",        "pseudo": "等待 N秒",          "desc": "等待指定秒数"},
    {"type": "scroll",      "pseudo": "滚动 up/down",      "desc": "滚动页面"},
    {"type": "launch",      "pseudo": '启动 "程序"',       "desc": "启动 GUI 应用程序（非阻塞）"},
    {"type": "focus",       "pseudo": '聚焦 "窗口标题"',   "desc": "将窗口带到前台"},
    {"type": "screenshot",  "pseudo": "截图",               "desc": "截取屏幕截图"},
    {"type": "run_command", "pseudo": '运行命令 "命令" [超时=N]', "desc": "执行 CLI 命令并等待完成（npm/git/编译等）"},
    {"type": "file_read",   "pseudo": '读取文件 "路径"',   "desc": "读取文件内容"},
    {"type": "file_write",  "pseudo": '写入文件 "路径" "内容"', "desc": "写入文件"},
    {"type": "file_save",   "pseudo": '保存文件 "路径"',   "desc": "在文件保存对话框中输入路径并按 Enter（处理 Ctrl+S 后的保存对话框）"},
    {"type": "mouse_move",  "pseudo": "移动鼠标 x y",       "desc": "移动鼠标到指定坐标"},
    {"type": "drag",        "pseudo": "拖拽 \"来源\" \"目标\"", "desc": "从起始位置拖拽到目标位置"},
]

CONTROL_FLOW_CAPS = [
    {"type": "condition", "pseudo": "如果 <条件> 则 <动作>",  "desc": "条件分支"},
    {"type": "loop",      "pseudo": "循环 N次",               "desc": "重复执行后续步骤 N 次"},
    {"type": "sub_flow",  "pseudo": '子流程 "名称"',          "desc": "调用子流程"},
]


def _build_commands_section() -> str:
    lines = ["AVAILABLE COMMANDS (pseudocode format → structured type):"]
    for cap in AGENT_CAPABILITIES:
        lines.append(f"- {cap['pseudo']} — {cap['desc']}")
    lines.append("")
    lines.append("CONTROL FLOW (indentation-based block):")
    for cap in CONTROL_FLOW_CAPS:
        lines.append(f"- {cap['pseudo']} — {cap['desc']}")
    return "\n".join(lines)


def _build_structured_types_section() -> str:
    linear = "|".join(c["type"] for c in AGENT_CAPABILITIES)
    return "\n".join([
        "STRUCTURED STEPS format rules:",
        f'- Linear actions: {{"type": "{linear}", ...params}}',
        '- Condition: {"type": "condition", "condition": "...", "true_branch": [...], "false_branch": [...]}',
        '- Loop: {"type": "loop", "count": N, "body": [...]}',
        '- Sub-flow: {"type": "sub_flow", "name": "...", "body": [...]}',
        "- Matches pseudocode exactly: every pseudocode line has a corresponding structured_step",
    ])


GENERATION_PROMPT = f"""You are an automation script generator. Given a user's goal, generate a step-by-step pseudocode script AND a structured steps representation.

{_build_commands_section()}

IMPORTANT RULES:
1. Do NOT use `运行命令` to launch GUI apps (notepad, calc, mspaint, chrome, explorer) — use `启动` instead
2. Use `运行命令` ONLY for CLI commands that exit after completion (git, npm, dir, echo)
3. Do NOT use numbering (1. 2. 3.) in the pseudocode. Each step on its own line, no prefixes.
4. After launching a GUI app, always add a `等待 1-2秒` step to let it open, then optionally `聚焦 "title"` to ensure focus.
5. For window titles in `聚焦`, use a fuzzy keyword that works across locales. E.g. `聚焦 "记事本"` instead of `聚焦 "无标题 - 记事本"`, and `聚焦 "计算器"` instead of `聚焦 "Calculator"`.
6. Use specific click targets that appear as visible text on screen. Never use vague descriptions like "空白处" or "当前位置" — use a concrete nearby text target instead.
7. When using `双击` or `右键单击`, do NOT wrap the target in quotes in the pseudocode. Write `双击 回收站` not `双击 '回收站'`. (Quotes are optional in structured_steps targets.)

Respond ONLY with a JSON object (no markdown, no extra text):
{{
  "explanation": "brief explanation of what the script does",
  "pseudocode": "the step-by-step pseudocode (flat lines, for frontend display)",
  "structured_steps": [
    {{
      "type": "launch",
      "target": "notepad.exe"
    }},
    {{
      "type": "wait",
      "duration": 2
    }},
    {{
      "type": "type",
      "text": "Hello"
    }},
    {{
      "type": "condition",
      "condition": "ocr_contains('确定')",
      "true_branch": [{{"type": "click", "target": "确定"}}],
      "false_branch": [{{"type": "hotkey", "keys": ["ctrl", "s"]}}]
    }},
    {{
      "type": "loop",
      "count": 3,
      "body": [{{"type": "click", "target": "下一步"}}]
    }}
  ]
}}

{_build_structured_types_section()}"""


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
    multimodal_env = os.environ.get("LLM_MULTIMODAL", "")
    multimodal = multimodal_env.lower() in ("true", "1", "yes")

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
            timeout=25,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        pseudocode = parsed.get("pseudocode", "")
        explanation = parsed.get("explanation", "")
        structured_steps = parsed.get("structured_steps", [])

        # Normalize: if LLM returned a list of lines, join them
        if isinstance(pseudocode, list):
            pseudocode = "\n".join(str(line) for line in pseudocode)

        if not pseudocode:
            return {"success": False, "error": "AI 未能生成伪代码"}

        result = {
            "pseudocode": pseudocode,
            "explanation": explanation,
            "model": model,
        }

        # If LLM provided structured_steps, pass them through for supervised execution
        if isinstance(structured_steps, list) and len(structured_steps) > 0:
            # Validate: each step must have a "type" field
            valid = [s for s in structured_steps if isinstance(s, dict) and s.get("type")]
            if len(valid) == len(structured_steps):
                result["structured_steps"] = valid
                # Build node_mapping: step_index → step_type + label
                node_mapping = {}
                for idx, s in enumerate(valid):
                    label = s.get("target") or s.get("text") or s.get("title") or s.get("name") or s.get("type", "")
                    node_mapping[str(idx)] = {
                        "step_type": s["type"],
                        "label": label[:60],
                    }
                result["node_mapping"] = node_mapping

        return {
            "success": True,
            "result": result,
        }
    except ImportError:
        return {"success": False, "error": "需要 requests 库"}
    except Exception as e:
        return {"success": False, "error": f"生成失败: {e}"}
