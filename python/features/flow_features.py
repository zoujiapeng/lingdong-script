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
    # ── 浏览器 CDP 操作（基于 Playwright，精确控制网页元素） ──
    {"type": "browser_navigate",     "pseudo": '浏览器导航 "URL"',           "desc": "通过 CDP 控制浏览器导航到指定 URL"},
    {"type": "browser_click",        "pseudo": '浏览器点击 "CSS选择器"',     "desc": "通过 CSS 选择器点击页面元素"},
    {"type": "browser_type",         "pseudo": '浏览器输入 "CSS选择器" "文本"', "desc": "在页面元素中输入文本"},
    {"type": "browser_find",         "pseudo": '浏览器查找 "CSS选择器"',     "desc": "查找页面元素是否存在"},
    {"type": "browser_wait_element", "pseudo": '浏览器等待 "CSS选择器" [超时]', "desc": "等待页面元素出现"},
    {"type": "browser_get_text",     "pseudo": '浏览器获取文本 "CSS选择器"', "desc": "获取页面元素的文本内容"},
    {"type": "browser_screenshot",   "pseudo": "浏览器截图",                  "desc": "截取浏览器当前页面截图"},
    {"type": "browser_exec_js",      "pseudo": '浏览器执行JS "代码"',        "desc": "在浏览器中执行 JavaScript"},
]

CONTROL_FLOW_CAPS = [
    {"type": "condition", "pseudo": "如果 <条件> 则 <动作>",  "desc": "条件分支"},
    {"type": "loop",      "pseudo": "循环 N次",               "desc": "重复执行后续步骤 N 次"},
    {"type": "sub_flow",  "pseudo": '子流程 "名称"',          "desc": "调用子流程"},
]


def _build_commands_section() -> str:
    lines = ["AVAILABLE COMMANDS (pseudocode format → structured type):"]
    for cap in AGENT_CAPABILITIES:
        if cap["type"].startswith("browser_"):
            continue  # grouped in separate section
        lines.append(f"- {cap['pseudo']} — {cap['desc']}")
    lines.append("")
    lines.append("BROWSER AUTOMATION (Playwright CDP — use for ALL web tasks):")
    for cap in AGENT_CAPABILITIES:
        if cap["type"].startswith("browser_"):
            lines.append(f"- {cap['pseudo']} — {cap['desc']}")
    lines.append("")
    lines.append("CONTROL FLOW (indentation-based block):")
    for cap in CONTROL_FLOW_CAPS:
        lines.append(f"- {cap['pseudo']} — {cap['desc']}")
    return "\n".join(lines)


def _build_structured_types_section() -> str:
    linear = "|".join(c["type"] for c in AGENT_CAPABILITIES)
    browser_linear = "|".join(c["type"] for c in AGENT_CAPABILITIES if c["type"].startswith("browser_"))
    return "\n".join([
        "STRUCTURED STEPS format rules:",
        f'- Linear actions: {{"type": "{linear}", ...params}}',
        f'- Browser actions: {{"type": "{browser_linear}", ...params}}',
        '- Condition: {"type": "condition", "condition": "...", "true_branch": [...], "false_branch": [...]}',
        '- Loop: {"type": "loop", "count": N, "body": [...]}',
        '- Sub-flow: {"type": "sub_flow", "name": "...", "body": [...]}',
        "- Matches pseudocode exactly: every pseudocode line has a corresponding structured_step",
        "",
        "Browser structured steps example:",
        '  {"type": "browser_navigate", "url": "https://example.com"}',
        '  {"type": "browser_click", "selector": "#search-button"}',
        '  {"type": "browser_type", "selector": "#search-input", "text": "query"}',
        '  {"type": "browser_wait_element", "selector": ".result", "timeout": 10}',
        '  {"type": "browser_get_text", "selector": ".title"}',
    ])


def _build_dsl_reference() -> str:
    """动态从注册表构建完整的 DSL 关键字参考（让 LLM 知道所有可用指令）。"""
    try:
        from features.registry import registry
        import features._all  # noqa: F401
        # Group by category
        cats = {}
        for f in registry.all():
            if not f.dsl_keyword or f.deprecated or f.hidden:
                continue
            cat = cats.setdefault(f.category.value, [])
            cat.append(f)

        lines = ["", "=== COMPLETE LOBSTER DSL REFERENCE (all available commands) ===", ""]
        for cat_name in ("action", "perception", "control", "system", "ai", "macro", "debug"):
            if cat_name not in cats:
                continue
            lines.append(f"[{cat_name.upper()}]")
            for f in sorted(cats[cat_name], key=lambda x: x.dsl_keyword):
                params = " ".join(p.name for p in f.params[:3])
                if len(f.params) > 3:
                    params += " ..."
                lines.append(f"  {f.dsl_keyword}  {params}  # {f.description[:60]}")
            lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


# Build the prompt at module load time so it includes the full registry
_DSL_REFERENCE = _build_dsl_reference()

GENERATION_PROMPT = f"""You are an automation script generator. Given a user's goal, generate a step-by-step pseudocode script, structured steps, and LOBSTER DSL.

{_build_commands_section()}

IMPORTANT RULES:
1. Do NOT use `运行命令` to launch GUI apps (notepad, calc, mspaint, chrome, explorer) — use `启动` instead
2. Use `运行命令` ONLY for CLI commands that exit after completion (git, npm, dir, echo)
3. Do NOT use numbering (1. 2. 3.) in the pseudocode. Each step on its own line, no prefixes.
4. If the user specifies explicit time delays (like "N秒后" "等N秒" "隔N秒") — strictly follow them by adding a `等待 N` step before the corresponding action
5. After launching a GUI app, always add a `等待 1-2秒` step to let it open, then optionally `聚焦 "title"` to ensure focus.
6. For window titles in `聚焦`, use a fuzzy keyword that works across locales. E.g. `聚焦 "记事本"` instead of `聚焦 "无标题 - 记事本"`, and `聚焦 "计算器"` instead of `聚焦 "Calculator"`.
7. Use specific click targets that appear as visible text on screen. Never use vague descriptions like "空白处" or "当前位置" — use a concrete nearby text target instead.
8. When using `双击` or `右键单击`, do NOT wrap the target in quotes in the pseudocode. Write `双击 回收站` not `双击 '回收站'`. (Quotes are optional in structured_steps targets.)
9. When launching a program, you can use Chinese names directly (e.g. `启动 "微信"`, `启动 "记事本"`, `启动 "微星小飞机"`). The system will auto-resolve them to full paths. Do NOT use `运行命令 "where 程序名"` for program path lookup — just use the Chinese or common name directly.
10. NEVER make up placeholder usernames, passwords, or credentials. If the user's goal mentions "登录" (login) but doesn't specify credentials, DO NOT fabricate example values like "your_username" or "your_password". Instead, generate steps that ask the user to enter their credentials manually, or skip the login step if credentials are not provided.
11. For ALL web/website/browser tasks: use the BROWSER AUTOMATION commands (浏览器导航, 浏览器点击, 浏览器输入 etc.) which control the browser via CDP (Playwright) for precise element targeting. Do NOT use generic keyboard/mouse automation for browsing the web.
12. When the goal involves multiple phases (wait → do A → do B), generate the steps in the exact order specified by the user, preserving all delays and dependencies between phases.
13. BROWSER WORKFLOW RULES:
    a. CRITICAL — browser_* commands (浏览器导航, 浏览器点击 etc.) control an ALREADY-RUNNING browser via CDP. They do NOT open the browser themselves. Therefore, for ANY task involving web browsing, you MUST include  as a step BEFORE the first browser_* command. Without this step the browser won't open and all commands will fail.
    b. First step for ANY browser task: 启动 "浏览器" → 等待 2-3. Only THEN use browser_navigate.
    c. Use 浏览器点击 "CSS选择器" to click buttons/links (figure out selector from context: id, class, role, text).
    d. Use 浏览器输入 "CSS选择器" "文本" for input fields.
    e. After navigation/submission, use 浏览器等待 "CSS选择器" [超时秒数] for results.
    f. Use 浏览器获取文本 "CSS选择器" to read page content if needed.
    g. Example CORRECT sequence: 启动 "浏览器" → 等待 2 → 浏览器导航 "URL" → 浏览器等待 "#kw" → 浏览器输入 "#kw" "搜索词" → 浏览器点击 "#su".
    h. NEVER type URLs in the browser address bar via keyboard — always use 浏览器导航.
14. BROWSER AUTOMATION commands use Playwright CDP — pixel-perfect targeting, auto-wait for visibility, reliable input. They are the CORRECT way to interact with web pages, NOT generic mouse/keyboard automation.
15. LOGIN WORKFLOW — 当用户说"登录"时，必须生成完整的登录步骤序列（不只是点击登录按钮）：
    a. 点击登录按钮/链接后，必须等待登录表单出现
    b. 如果用户没有提供凭据，生成步骤注明"等待用户输入凭据"，不要编造值
    c. 如果提供了凭据，使用 浏览器输入 填写用户名/密码字段
    d. 填写后点击提交/登录按钮
    e. 等待登录完成后的页面跳转
    正确示例：浏览器导航 → 浏览器等待元素 → 浏览器点击登录 → 浏览器等待输入框 → 等待用户输入凭据 → 浏览器点击提交 → 浏览器等待登录完成
16. FLOW COMPLETENESS — 流程必须详尽完整，包含所有必要中间步骤：
    a. "搜索X" = 打开目标网站操作，不是用搜索引擎搜
    b. 每个浏览器操作后必须跟 浏览器等待 让页面加载
    c. 每个点击后都要等待页面响应
    d. 反例（太简单，缺少等待和中间步骤）：浏览器导航 → 浏览器点击 → 完成
    e. 正例（完整）：浏览器导航 → 浏览器等待 → 浏览器点击 → 浏览器等待新元素 → 浏览器输入 → 浏览器点击提交 → 浏览器等待结果

BASIC DSL SYNTAX (common commands):
  CLICK {{target}}           — click on text/coordinate
  WAIT {{seconds}}           — wait for N seconds
  TYPE {{text}}              — type text
  LAUNCH {{program}}         — launch GUI application
  HOTKEY {{keys}}            — e.g. HOTKEY ctrl+c
  FOCUS {{title}}            — focus window by title
  SCREENSHOT {{dest}}        — capture screenshot
  SCROLL {{direction}}       — scroll up/down
  DRAG {{x1}} {{y1}} TO {{x2}} {{y2}}  — drag from-to
  IF {{condition}}           — conditional branch, ends with END
  LOOP {{N}}                 — repeat N times, ends with END

+ ALL commands below are also available as DSL keywords (use the keyword as the first token):
{_DSL_REFERENCE}

COMPLETE EXAMPLE — 对于"5秒后打开浏览器搜索deepseek然后在里面登录，完成后5秒打开记事本"这个目标，正确的输出是：

{{
  "explanation": "等待5秒后打开浏览器，导航到deepseek官网，进行登录操作（由于未提供具体凭据，仅点击登录按钮并等待用户输入），完成后等待5秒再打开记事本。",
  "pseudocode": "等待 5
启动 浏览器
等待 2
浏览器导航 https://www.deepseek.com
浏览器等待 .login-btn
浏览器点击 .login-btn
浏览器等待 #username-input
浏览器输入 #username-input 等待用户输入凭据
浏览器点击 .submit-btn
浏览器等待 .user-center
等待 5
启动 记事本",
  "dsl": "WAIT 5
LAUNCH 浏览器
WAIT 2
BROWSER_NAV https://www.deepseek.com
BROWSER_WAIT .login-btn
BROWSER_CLICK .login-btn
BROWSER_WAIT #username-input
BROWSER_TYPE #username-input 等待用户输入凭据
BROWSER_CLICK .submit-btn
BROWSER_WAIT .user-center
WAIT 5
LAUNCH 记事本",
  "structured_steps": [
    {{"type": "wait", "duration": 5}},
    {{"type": "launch", "target": "浏览器"}},
    {{"type": "wait", "duration": 2}},
    {{"type": "browser_navigate", "url": "https://www.deepseek.com"}},
    {{"type": "browser_wait_element", "selector": ".login-btn", "timeout": 10}},
    {{"type": "browser_click", "selector": ".login-btn"}},
    {{"type": "browser_wait_element", "selector": "#username-input", "timeout": 10}},
    {{"type": "browser_type", "selector": "#username-input", "text": "等待用户输入凭据"}},
    {{"type": "browser_click", "selector": ".submit-btn"}},
    {{"type": "browser_wait_element", "selector": ".user-center", "timeout": 15}},
    {{"type": "wait", "duration": 5}},
    {{"type": "launch", "target": "记事本"}}
  ]
}}

Now generate the JSON for the user's goal. Respond ONLY with a JSON object (no markdown, no extra text):
{{
  "explanation": "brief explanation of what the script does",
  "pseudocode": "the step-by-step pseudocode (flat lines, for frontend display)",
  "dsl": "the equivalent Lobster DSL text (one action per line)",
  "structured_steps": [
    {{
      "type": "wait",
      "duration": 5
    }},
    {{
      "type": "launch",
      "target": "浏览器"
    }},
    {{
      "type": "wait",
      "duration": 2
    }},
    {{
      "type": "browser_navigate",
      "url": "https://www.deepseek.com"
    }},
    {{
      "type": "browser_wait_element",
      "selector": ".login-btn",
      "timeout": 10
    }},
    {{
      "type": "browser_click",
      "selector": ".login-btn"
    }},
    {{
      "type": "browser_wait_element",
      "selector": "#username-input",
      "timeout": 10
    }},
    {{
      "type": "browser_type",
      "selector": "#username-input",
      "text": "等待用户输入凭据"
    }},
    {{
      "type": "browser_click",
      "selector": ".submit-btn"
    }},
    {{
      "type": "browser_wait_element",
      "selector": ".user-center",
      "timeout": 15
    }},
    {{
      "type": "wait",
      "duration": 5
    }},
    {{
      "type": "launch",
      "target": "记事本"
    }}
  ]
}}

{_build_structured_types_section()}"""


def _steps_to_dsl(steps: list) -> str:
    """将 structured_steps 转换为 Lobster DSL 文本。"""
    lines = []

    def _render_step(step: dict, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        t = step.get("type", "")
        if t == "click" or t == "text_click":
            target = step.get("target", "")
            return [f'{prefix}CLICK {target}'] if target else [f'{prefix}CLICK ']
        elif t == "image_click":
            return [f'{prefix}CLICK img:{step.get("target", "")}']
        elif t == "type":
            text = step.get("text", "")
            return [f'{prefix}TYPE {text}'] if text else [f'{prefix}TYPE ']
        elif t == "hotkey":
            keys = step.get("keys", [])
            if isinstance(keys, list):
                return [f'{prefix}HOTKEY {"+".join(keys)}']
            return [f'{prefix}HOTKEY {keys}']
        elif t == "wait":
            return [f'{prefix}WAIT {step.get("duration", 1)}']
        elif t == "scroll":
            return [f'{prefix}SCROLL {step.get("target", "down")}']
        elif t == "launch":
            return [f'{prefix}LAUNCH {step.get("target", "")}']
        elif t == "focus":
            return [f'{prefix}FOCUS {step.get("title", step.get("target", ""))}']
        elif t == "screenshot":
            return [f'{prefix}SCREENSHOT']
        elif t == "run_command":
            cmd = step.get("command", "")
            return [f'{prefix}RUN {cmd}']
        elif t == "mouse_move":
            return [f'{prefix}MOVEMOUSE {step.get("x", 0)} {step.get("y", 0)}']
        elif t == "drag":
            src = step.get("from_target", step.get("target", ""))
            dst = step.get("to_target", step.get("target", ""))
            return [f'{prefix}DRAG {src} TO {dst}']
        elif t == "condition":
            result = [f'{prefix}IF {step.get("condition", "")}']
            for s in step.get("true_branch", []):
                result.extend(_render_step(s, indent + 1))
            if step.get("false_branch"):
                result.append(f'{prefix}ELSE')
                for s in step["false_branch"]:
                    result.extend(_render_step(s, indent + 1))
            result.append(f'{prefix}END')
            return result
        elif t == "loop":
            result = [f'{prefix}LOOP {step.get("count", 10)}']
            for s in step.get("body", []):
                result.extend(_render_step(s, indent + 1))
            result.append(f'{prefix}END')
            return result
        elif t == "sub_flow":
            return [f'{prefix}CALL {step.get("name", "")}']
        elif t == "browser_navigate":
            return [f'{prefix}BROWSER_NAV {step.get("url", step.get("target", ""))}']
        elif t == "browser_click":
            return [f'{prefix}BROWSER_CLICK {step.get("selector", step.get("target", ""))}']
        elif t == "browser_type":
            sel = step.get("selector", step.get("target", ""))
            text = step.get("text", "")
            return [f'{prefix}BROWSER_TYPE {sel} "{text}"']
        elif t == "browser_find":
            return [f'{prefix}BROWSER_FIND {step.get("selector", step.get("target", ""))}']
        elif t == "browser_wait_element":
            sel = step.get("selector", step.get("target", ""))
            timeout = step.get("timeout", 5)
            return [f'{prefix}BROWSER_WAIT {sel} {timeout}']
        elif t == "browser_get_text":
            return [f'{prefix}BROWSER_TEXT {step.get("selector", step.get("target", ""))}']
        elif t == "browser_screenshot":
            return [f'{prefix}BROWSER_SCREENSHOT']
        elif t == "browser_exec_js":
            return [f'{prefix}BROWSER_JS {step.get("code", "")}']
        elif t == "file_read":
            return [f'{prefix}FILEREAD {step.get("path", "")}']
        elif t == "file_write":
            return [f'{prefix}FILEWRITE {step.get("path", "")} {step.get("content", "")}']
        else:
            # Unknown step type — use raw text or skip
            raw = step.get("raw", "")
            return [f'{prefix}{raw}'] if raw else []

    for step in steps:
        lines.extend(_render_step(step, 0))

    return "\n".join(lines)


def _steps_to_pseudocode(steps: list) -> str:
    """将 structured_steps 转换为中文伪代码文本（用于前端展示）。"""
    lines = []

    def _render_step(step: dict, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        t = step.get("type", "")
        if t == "click" or t == "text_click":
            target = step.get("target", "")
            return [f'{prefix}点击 {target}'] if target else [f'{prefix}点击 ']
        elif t == "image_click":
            return [f'{prefix}点击图片 {step.get("target", "")}']
        elif t == "type":
            text = step.get("text", "")
            return [f'{prefix}输入 {text}'] if text else [f'{prefix}输入 ']
        elif t == "hotkey":
            keys = step.get("keys", [])
            if isinstance(keys, list):
                return [f'{prefix}按键 {"+".join(keys)}']
            return [f'{prefix}按键 {keys}']
        elif t == "wait":
            return [f'{prefix}等待 {step.get("duration", 1)}']
        elif t == "scroll":
            return [f'{prefix}滚动 {step.get("target", "down")}']
        elif t == "launch":
            return [f'{prefix}启动 {step.get("target", "")}']
        elif t == "focus":
            return [f'{prefix}聚焦 {step.get("title", step.get("target", ""))}']
        elif t == "screenshot":
            return [f'{prefix}截图']
        elif t == "run_command":
            cmd = step.get("command", "")
            return [f'{prefix}运行命令 {cmd}']
        elif t == "mouse_move":
            return [f'{prefix}移动鼠标 {step.get("x", 0)} {step.get("y", 0)}']
        elif t == "drag":
            src = step.get("from_target", step.get("target", ""))
            dst = step.get("to_target", step.get("target", ""))
            return [f'{prefix}拖拽 {src} 到 {dst}']
        elif t == "browser_navigate":
            return [f'{prefix}浏览器导航 {step.get("url", step.get("target", ""))}']
        elif t == "browser_click":
            return [f'{prefix}浏览器点击 {step.get("selector", step.get("target", ""))}']
        elif t == "browser_type":
            sel = step.get("selector", step.get("target", ""))
            text = step.get("text", "")
            return [f'{prefix}浏览器输入 {sel} {text}']
        elif t == "browser_find":
            return [f'{prefix}浏览器查找 {step.get("selector", step.get("target", ""))}']
        elif t == "browser_wait_element":
            sel = step.get("selector", step.get("target", ""))
            timeout = step.get("timeout", 5)
            return [f'{prefix}浏览器等待 {sel}']
        elif t == "browser_get_text":
            return [f'{prefix}浏览器获取文本 {step.get("selector", step.get("target", ""))}']
        elif t == "browser_screenshot":
            return [f'{prefix}浏览器截图']
        elif t == "browser_exec_js":
            return [f'{prefix}浏览器执行JS {step.get("code", "")}']
        elif t == "file_read":
            return [f'{prefix}读取文件 {step.get("path", "")}']
        elif t == "file_write":
            return [f'{prefix}写入文件 {step.get("path", "")} {step.get("content", "")}']
        elif t == "condition":
            result = [f'{prefix}如果 {step.get("condition", "")}']
            for s in step.get("true_branch", []):
                result.extend(_render_step(s, indent + 1))
            if step.get("false_branch"):
                result.append(f'{prefix}否则')
                for s in step["false_branch"]:
                    result.extend(_render_step(s, indent + 1))
            result.append(f'{prefix}结束')
            return result
        elif t == "loop":
            result = [f'{prefix}循环 {step.get("count", 10)}']
            for s in step.get("body", []):
                result.extend(_render_step(s, indent + 1))
            result.append(f'{prefix}结束')
            return result
        elif t == "sub_flow":
            return [f'{prefix}子流程 {step.get("name", "")}']
        else:
            raw = step.get("raw", "")
            return [f'{prefix}{raw}'] if raw else []

    for step in steps:
        lines.extend(_render_step(step, 0))

    return "\n".join(lines)


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

        # Handle auth/HTTP errors with clear messages
        if resp.status_code == 401:
            return {"success": False, "error": "API 密钥无效或已过期，请在设置中更新 DeepSeek API 密钥"}
        if resp.status_code == 402:
            return {"success": False, "error": "API 账户余额不足，请充值后重试"}
        if resp.status_code == 429:
            return {"success": False, "error": "API 请求频率过高，请稍后重试"}
        if not resp.ok:
            try:
                detail = resp.json().get("error", {}).get("message", resp.reason)
            except Exception:
                detail = resp.reason
            return {"success": False, "error": f"API 请求失败 ({resp.status_code}): {detail}"}

        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        pseudocode = parsed.get("pseudocode", "")
        explanation = parsed.get("explanation", "")
        dsl_text = parsed.get("dsl", "")
        structured_steps = parsed.get("structured_steps", [])

        # Normalize: if LLM returned a list of lines, join them
        if isinstance(pseudocode, list):
            pseudocode = "\n".join(str(line) for line in pseudocode)
        if isinstance(dsl_text, list):
            dsl_text = "\n".join(str(line) for line in dsl_text)

        if not pseudocode:
            return {"success": False, "error": "AI 未能生成伪代码"}

        result = {
            "pseudocode": pseudocode,
            "explanation": explanation,
            "model": model,
        }

        # ── DSL field (LLM-generated or fallback-converted) ──
        if dsl_text:
            result["dsl"] = dsl_text
        elif isinstance(structured_steps, list) and len(structured_steps) > 0:
            result["dsl"] = _steps_to_dsl(structured_steps)

        # ── Pseudocode fallback: if LLM generated # UNKNOWN placeholders, regenerate ──
        if "# UNKNOWN" in pseudocode and isinstance(structured_steps, list) and len(structured_steps) > 0:
            fallback = _steps_to_pseudocode(structured_steps)
            if fallback:
                result["pseudocode"] = fallback
                pseudocode = fallback

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
    except _req.exceptions.Timeout:
        return {"success": False, "error": "AI 请求超时（25 秒），请检查网络连接或 API 地址配置"}
    except _req.exceptions.ConnectionError:
        return {"success": False, "error": f"无法连接到 API 服务器 ({api_base})，请检查 API 地址配置"}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"AI 返回了非 JSON 格式数据，请检查模型是否支持 response_format: {e}"}
    except Exception as e:
        err_msg = str(e)
        if "401" in err_msg or "Unauthorized" in err_msg:
            return {"success": False, "error": "API 密钥无效或已过期，请在设置中更新 DeepSeek API 密钥"}
        return {"success": False, "error": f"生成失败: {err_msg[:200]}"}
