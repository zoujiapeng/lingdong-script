"""
流程图脚本生成功能
Agent 内部调用：将自然语言目标转为可执行的流程图脚本（伪代码）
"""
import json
import os
import re
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
    {"type": "wait_element","pseudo": '等待元素 "目标" 超时 N',"desc": "等待界面元素出现（OCR/图像），超时则继续"},
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
    {"type": "browser_new_tab",     "pseudo": '浏览器新建标签页 "URL"',     "desc": "新建浏览器标签页并导航到 URL"},
    {"type": "browser_close_tab",   "pseudo": "浏览器关闭当前标签页",         "desc": "关闭当前浏览器标签页"},
    {"type": "browser_switch_tab",  "pseudo": '浏览器切换标签页 "索引/标题"', "desc": "切换到指定标签页"},
    {"type": "ocr_extract",         "pseudo": 'OCR提取 "区域"',       "desc": "从屏幕区域提取文字（OCR）"},
    {"type": "clipboard_get",       "pseudo": "读取剪贴板内容",             "desc": "获取系统剪贴板文本内容"},
    {"type": "clipboard_set",       "pseudo": '设置剪贴板 "文本"',          "desc": "将文本写入系统剪贴板"},
]

CONTROL_FLOW_CAPS = [
    {"type": "check_condition", "pseudo": '检查条件 "条件"',  "desc": "检查条件是否成立（等待/判断用）"},
    {"type": "wait_until",      "pseudo": '等待直到 "条件"',  "desc": "等待条件成立，超时则继续"},
    {"type": "condition",       "pseudo": '如果 "条件" ... 否则 ...', "desc": "条件分支：true_branch 和 false_branch 各包含子步骤数组"},
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
    return "\n".join(lines)


def _build_structured_types_section() -> str:
    linear = "|".join(c["type"] for c in AGENT_CAPABILITIES)
    browser_linear = "|".join(c["type"] for c in AGENT_CAPABILITIES if c["type"].startswith("browser_"))
    control_linear = "|".join(c["type"] for c in CONTROL_FLOW_CAPS)
    return "\n".join([
        "STRUCTURED STEPS format rules:",
        f'- Steps must be from: {linear} (flat JSON objects, one per step)',
        f'- Condition/control types: {control_linear} (use condition for branching with true_branch/false_branch)',
        f'- Browser: {browser_linear}',
        "- Each pseudo line maps to exactly one structured step",
        "- NO loop/sub_flow types — flatten loops into linear steps",
        "",
        "Condition step example (if/else branching):",
        '  {"type": "condition", "condition": "屏幕是否变化", "true_branch": [{"type": "hotkey", "keys": ["win", "l"]}], "false_branch": [{"type": "hotkey", "keys": ["volume_up"]}]}',
        '  {"type": "condition", "condition": "弹窗是否出现", "true_branch": [{"type": "click", "target": "确定"}]}',
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
    a. IMPORTANT: browser_* commands use CDP (Chrome DevTools Protocol) which auto-starts the browser on first use. You do NOT need to launch the browser before browser_navigate — go straight to 浏览器导航 "URL".
    b. First step for ANY browser task: 浏览器导航 "URL". No need to manually open the browser first.
    c. Use 浏览器点击 "CSS选择器" to click buttons/links (figure out selector from context: id, class, role, text).
    d. Use 浏览器输入 "CSS选择器" "文本" for input fields.
    e. After navigation/submission, use 浏览器等待 "CSS选择器" [超时秒数] for results.
    f. Use 浏览器获取文本 "CSS选择器" to read page content if needed.
    g. Example CORRECT sequence: 浏览器导航 "https://www.baidu.com" → 浏览器等待 "#kw" → 浏览器输入 "#kw" "搜索词" → 浏览器点击 "#su".
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

17. CONDITIONAL BRANCHING — Use the "condition" step type for if/else logic:
    a. "condition" type requires: "condition" (description string), "true_branch" (array of steps), and optional "false_branch" (array of steps)
    b. Example: "如果屏幕变化则锁屏，否则音量最大" →
       {{"type": "condition", "condition": "屏幕是否变化",
         "true_branch": [{{"type": "hotkey", "keys": ["win", "l"]}}],
         "false_branch": [{{"type": "hotkey", "keys": ["volume_up"]}}]
       }}
    c. Example: "如果出现弹窗则关闭" →
       {{"type": "condition", "condition": "弹窗是否出现",
         "true_branch": [{{"type": "click", "target": "确定"}}]
       }}
    d. Conditions CAN be nested inside branches, but keep it simple — one level is preferred.
    e. Pseudocode format for condition: "如果 {{条件}}" / "否则" / "结束" on separate lines
    f. DSL output for condition: IF {{条件}} / [true_branch steps] / ELSE / [false_branch steps] / END
    g. IMPORTANT: Do NOT use "loop" or "sub_flow" types — those remain unsupported. Flatten loops by repeating steps.

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

+ ALL commands below are also available as DSL keywords (use the keyword as the first token):
{_DSL_REFERENCE}

━━━ EXAMPLE 1: Browser workflow (flat, no condition) — for "5秒后打开浏览器搜索deepseek然后在里面登录，完成后5秒打开记事本":

{{
  "explanation": "等待5秒后打开浏览器，导航到deepseek官网，进行登录操作（由于未提供具体凭据，仅点击登录按钮并等待用户输入），完成后等待5秒再打开记事本。",
  "pseudocode": "等待 5
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

━━━ EXAMPLE 2: Conditional branching — for "3秒后打开记事本写'hello'然后判断屏幕是否变化,变化就锁屏":

{{
  "explanation": "等待3秒，打开记事本并输入hello，然后判断屏幕是否变化——变化则按下Win+L锁屏，否则不做额外操作。",
  "pseudocode": "等待 3
启动 记事本
输入 hello
如果 屏幕是否变化
  按键 win+l
结束",
  "dsl": "WAIT 3
LAUNCH 记事本
TYPE hello
IF 屏幕是否变化
HOTKEY win+l
END",
  "structured_steps": [
    {{"type": "wait", "duration": 3}},
    {{"type": "launch", "target": "记事本"}},
    {{"type": "type", "text": "hello"}},
    {{"type": "condition", "condition": "屏幕是否变化",
      "true_branch": [
        {{"type": "hotkey", "keys": ["win", "l"]}}
      ]
    }}
  ]
}}

━━━ EXAMPLE 3: Condition with false_branch — for "判断屏幕是否变化,变化就锁屏,没有变化就音量最大":

{{
  "explanation": "检查屏幕是否发生变化，变化则锁屏，无变化则调大音量。",
  "pseudocode": "如果 屏幕是否变化
  按键 win+l
否则
  按键 volume_up
结束",
  "dsl": "IF 屏幕是否变化
HOTKEY win+l
ELSE
HOTKEY volume_up
END",
  "structured_steps": [
    {{"type": "condition", "condition": "屏幕是否变化",
      "true_branch": [{{"type": "hotkey", "keys": ["win", "l"]}}],
      "false_branch": [{{"type": "hotkey", "keys": ["volume_up"]}}]
    }}
  ]
}}

Now generate the JSON for the user's goal.

CRITICAL: Before you respond, analyze the user's goal. If it contains ANY conditional logic (如果/否则/判断/是否/条件/depending on/if/if else/whether), you MUST use "condition" type steps in structured_steps. Tasks like "判断屏幕变化" "出现弹窗" "检查是否" ALWAYS require condition type.

Respond ONLY with a JSON object (no markdown, no extra text).

{_build_structured_types_section()}"""


# ── Step type aliases for normalization ────────────────────────────
# LLM occasionally produces variant type names. Map non-standard names to canonical ones.
# NOTE: browser_screenshot IS valid (maps to BROWSER_SCREENSHOT DSL) — do NOT alias it.
STEP_TYPE_ALIASES = {
    "screen_shot": "screenshot",
    "screen_capture": "screenshot",
    "run": "run_command",
    "navigate": "browser_navigate",
    "input_text": "type",
    "wait_for_element": "wait_element",
    "wait_element_appear": "wait_element",
    "wait_for": "wait",
    "sleep": "wait",
}

# Canonical step types from AGENT_CAPABILITIES + CONTROL_FLOW_CAPS
KNOWN_STEP_TYPES = {c["type"] for c in AGENT_CAPABILITIES} | {c["type"] for c in CONTROL_FLOW_CAPS}


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
        elif t == "wait_element":
            target = step.get("target", step.get("text", ""))
            timeout = step.get("timeout", 10)
            return [f'{prefix}WAIT_UNTIL element:{target} {timeout}']
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
            return [f'{prefix}BROWSER_NAVIGATE {step.get("url", step.get("target", ""))}']
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
        elif t == "browser_new_tab":
            return [f'{prefix}BROWSER_NEW_TAB {step.get("url", step.get("target", ""))}']
        elif t == "browser_close_tab":
            return [f'{prefix}BROWSER_CLOSE_TAB']
        elif t == "browser_switch_tab":
            return [f'{prefix}BROWSER_SWITCH_TAB {step.get("target", step.get("index", ""))}']
        elif t == "ocr_extract":
            region = step.get("target", step.get("region", ""))
            return [f'{prefix}OCR {region}']
        elif t == "clipboard_get":
            return [f'{prefix}CLIPBOARD_GET']
        elif t == "clipboard_set":
            text = step.get("text", step.get("content", ""))
            return [f'{prefix}CLIPBOARD_SET {text}']
        elif t == "file_read":
            return [f'{prefix}FILEREAD {step.get("path", "")}']
        elif t == "file_write":
            return [f'{prefix}FILEWRITE {step.get("path", "")} {step.get("content", "")}']
        elif t == "file_save":
            return [f'{prefix}FILESAVE {step.get("path", "")}']
        elif t == "check_condition":
            return [f'{prefix}CHECK {step.get("condition", "")}']
        elif t == "wait_until":
            return [f'{prefix}WAIT_UNTIL {step.get("condition", "")} {step.get("timeout", 10)}']
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
        if t == "text_click":
            return [f'{prefix}文字点击 "{step.get("target", "")}"']
        elif t == "click":
            target = step.get("target", "")
            return [f'{prefix}点击 {target}'] if target else [f'{prefix}点击 ']
        elif t == "image_click":
            return [f'{prefix}点击图片 "{step.get("target", "")}"']
        elif t == "type":
            text = step.get("text", "")
            return [f'{prefix}输入 "{text}"'] if text else [f'{prefix}输入 ']
        elif t == "hotkey":
            keys = step.get("keys", [])
            if isinstance(keys, list):
                return [f'{prefix}按键 {"+".join(keys)}']
            return [f'{prefix}按键 {keys}']
        elif t == "wait":
            return [f'{prefix}等待 {step.get("duration", 1)}']
        elif t == "wait_element":
            target = step.get("target", step.get("text", ""))
            timeout = step.get("timeout", 10)
            return [f'{prefix}等待元素 "{target}" 超时 {timeout}秒']
        elif t == "scroll":
            return [f'{prefix}滚动 {step.get("target", "down")}']
        elif t == "launch":
            return [f'{prefix}启动 "{step.get("target", "")}"']
        elif t == "focus":
            return [f'{prefix}聚焦 "{step.get("title", step.get("target", ""))}"']
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
        elif t == "browser_new_tab":
            return [f'{prefix}浏览器新建标签页 {step.get("url", step.get("target", ""))}']
        elif t == "browser_close_tab":
            return [f'{prefix}浏览器关闭标签页']
        elif t == "browser_switch_tab":
            return [f'{prefix}浏览器切换标签页 {step.get("target", step.get("index", ""))}']
        elif t == "ocr_extract":
            region = step.get("target", step.get("region", ""))
            return [f'{prefix}OCR提取 {region}']
        elif t == "clipboard_get":
            return [f'{prefix}读取剪贴板内容']
        elif t == "clipboard_set":
            text = step.get("text", step.get("content", ""))
            return [f'{prefix}设置剪贴板 "{text}"']
        elif t == "file_read":
            return [f'{prefix}读取文件 {step.get("path", "")}']
        elif t == "file_write":
            return [f'{prefix}写入文件 {step.get("path", "")} {step.get("content", "")}']
        elif t == "file_save":
            return [f'{prefix}保存文件 {step.get("path", "")}']
        elif t == "check_condition":
            return [f'{prefix}检查条件 {step.get("condition", "")}']
        elif t == "wait_until":
            return [f'{prefix}等待直到 {step.get("condition", "")} 超时{step.get("timeout", 10)}秒']
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
                "max_tokens": 4096,
                "temperature": 0.1,
            },
            timeout=60,
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
        try:
            resp_json = resp.json()
        except json.JSONDecodeError:
            # Extreme fallback: sanitize raw text by keeping only printable ASCII + JSON structure chars
            import re as _re3
            raw_text = resp.text
            safe = ''.join(ch for ch in raw_text if ch.isprintable() or ch in '\n\r\t{}[]",:')
            resp_json = json.loads(safe)
        raw = resp_json["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            return {"success": False, "error": f"AI 返回了非字符串内容 (type={type(raw).__name__})"}

        # Parse LLM's JSON output with multi-strategy fallback.
        # Strategy 1: strict=False tolerates unescaped control chars in JSON strings
        # (DeepSeek's json_object mode sometimes doesn't fully escape them).
        # Strategy 2: strip only actual control chars (0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f, 0x7f-0x9f)
        # and retry — keeps all Unicode including Chinese.
        # Strategy 3: progressive brace-matching on raw content.
        parsed = None
        parse_errors = []

        # Strategy 1: try with strict=False
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            parse_errors.append(f"strict: {e}")
            try:
                parsed = json.loads(raw, strict=False)
            except json.JSONDecodeError as e:
                parse_errors.append(f"nonstrict: {e}")

        # Strategy 2: strip control chars only, keep Unicode
        if parsed is None:
            sanitized = ''.join(ch for ch in raw
                                if ch >= ' ' or ch in '\n\r\t')
            try:
                parsed = json.loads(sanitized)
            except json.JSONDecodeError as e:
                parse_errors.append(f"sanitized: {e}")

        # Strategy 3: brace-matched extraction on raw content
        if parsed is None:
            for i in range(len(raw)):
                if raw[i] == '{':
                    depth = 0
                    for j in range(i, len(raw)):
                        if raw[j] == '{':
                            depth += 1
                        elif raw[j] == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    parsed = json.loads(raw[i:j+1], strict=False)
                                    break
                                except json.JSONDecodeError:
                                    break
                    if parsed:
                        break

        if parsed is None:
            return {"success": False, "error": f"AI 返回了非 JSON 格式数据: {'; '.join(parse_errors[:2])}"}
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
        if isinstance(structured_steps, list) and len(structured_steps) > 0:
            _has_control = any(isinstance(s, dict) and s.get("type") in ("condition", "loop") for s in structured_steps)
            # Always regenerate DSL from steps when branching, since LLM's flat DSL can't represent it
            if _has_control:
                result["dsl"] = _steps_to_dsl(structured_steps)
            elif dsl_text:
                result["dsl"] = dsl_text
            else:
                result["dsl"] = _steps_to_dsl(structured_steps)
        elif dsl_text:
            result["dsl"] = dsl_text

        # ── Pseudocode fallback: regenerate when LLM's text can't represent branching ──
        if isinstance(structured_steps, list) and len(structured_steps) > 0:
            _should_regenerate = "# UNKNOWN" in pseudocode
            # Always regenerate if structured_steps contain condition/loop — LLM's flat text can't represent branching
            if not _should_regenerate and any(isinstance(s, dict) and s.get("type") in ("condition", "loop") for s in structured_steps):
                _should_regenerate = True
            if _should_regenerate:
                fallback = _steps_to_pseudocode(structured_steps)
                if fallback:
                    result["pseudocode"] = fallback
                    pseudocode = fallback

        # If LLM provided structured_steps, pass them through for supervised execution
        if isinstance(structured_steps, list) and len(structured_steps) > 0:
            _warnings = list(result.get("warnings", []))

            # Normalize step types using aliases
            for s in structured_steps:
                if isinstance(s, dict) and s.get("type") in STEP_TYPE_ALIASES:
                    old_type = s["type"]
                    s["type"] = STEP_TYPE_ALIASES[old_type]
                    _warnings.append(f"Step type '{old_type}' normalized to '{s['type']}'")

            # Validate: each step must have a "type" field
            valid = [s for s in structured_steps if isinstance(s, dict) and s.get("type")]
            if len(valid) < len(structured_steps):
                filtered = len(structured_steps) - len(valid)
                _warnings.append(f"{filtered} step(s) filtered out due to missing 'type' field")

            # Check for unsupported control flow types (condition IS supported, loop/sub_flow are not)
            _unsupported = {"loop", "sub_flow"}
            for s in valid:
                if s["type"] in _unsupported:
                    _warnings.append(f"Step contains unsupported type '{s['type']}' — flattening rule was violated")
                elif s["type"] not in KNOWN_STEP_TYPES:
                    _warnings.append(f"Unknown step type '{s['type']}' — may not be executable")

            if _warnings:
                result["warnings"] = _warnings
            result["structured_steps"] = valid
            # Build node_mapping: step_index → step_type + label (for canvas highlighting)
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
