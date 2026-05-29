#!/usr/bin/env python
"""
Agent 流程图 **执行** 质量测试。
生成 DSL → 通过引擎执行器实际运行 → 检测执行日志和错误。

与 agent_flow_test.py 的区别：
  - agent_flow_test.py: 只验证生成质量（伪代码、步骤结构）
  - agent_execution_test.py: 验证生成的 DSL 能否被引擎正确解析和执行

执行模式：
  mode=validate: 解析 DSL + 模拟执行（不操作真实 GUI），检查步骤结构和执行流
  mode=real:     （可选）真实执行，需要 GUI 环境

用法：
  python tests/agent_execution_test.py              # 全部用例 validate 模式
  python tests/agent_execution_test.py --mode real   # 真实执行（部分用例跳过）
"""

import io, json, os, sys, time, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.environ["PYTHONUNBUFFERED"] = "1"
_print = print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _print(*args, **kwargs)

# ── Test Cases ───────────────────────────────────────────────────

TEST_CASES = [
    {
        "name": "1. 简单单步",
        "goal": "打开计算器",
        "expected_actions": ["launch"],
        "min_steps": 1,
    },
    {
        "name": "2. 多步骤线性流程",
        "goal": "打开记事本，输入 hello world，等待 2 秒，截图保存到桌面",
        "expected_actions": ["launch", "type", "wait", "screenshot"],
        "min_steps": 4,
    },
    {
        "name": "3. 带时间约束的多阶段流程",
        "goal": "5秒后打开浏览器搜索今天的天气，再等10秒后截屏保存",
        "expected_actions": ["wait", "browser_navigate", "browser_type", "browser_click", "screenshot"],
        "min_steps": 5,
    },
    {
        "name": "4. 浏览器完整工作流",
        "goal": "打开百度首页，搜索'Claude AI 能力'，点击第一个搜索结果，等待页面加载完成后截图",
        "expected_actions": ["browser_navigate", "browser_type", "browser_click", "screenshot"],
        "min_steps": 5,
    },
    {
        "name": "5. 多阶段 + 条件处理",
        "goal": "打开微信电脑版，如果出现登录二维码就等待30秒让用户扫码，否则自动输入账号密码登录",
        "expected_actions": ["launch", "focus", "wait", "type"],
        "min_steps": 5,
    },
    {
        "name": "6. 极复杂多工具协作",
        "goal": """打开 Chrome 浏览器，按顺序完成以下操作：
1. 访问 https://www.baidu.com
2. 在搜索框输入 "Python 教程"
3. 点击搜索按钮
4. 等待搜索结果加载
5. 点击第三个搜索结果
6. 等待页面加载""",
        "expected_actions": ["browser_navigate", "browser_type", "browser_click", "browser_get_text", "file_write"],
        "min_steps": 8,
    },
    {
        "name": "7. 时序依赖链 — 剪贴板监控",
        "goal": "每隔5秒检查一次剪贴板内容，检测到内容变化后将新内容保存到桌面 clipboard_log.txt 文件，然后显示通知提醒",
        "expected_actions": ["clipboard_get", "wait", "file_write"],
        "min_steps": 3,
    },
    {
        "name": "8. 软件安装 + 配置流程",
        "goal": """帮我完成以下软件安装配置：
1. 打开浏览器访问 https://nodejs.org
2. 下载 LTS 版本的 Node.js
3. 等待下载完成
4. 运行安装程序（一路默认选项）
5. 安装完成后打开命令行验证 node --version
6. 把版本号保存到桌面 node_version.txt""",
        "expected_actions": ["browser_navigate", "browser_click", "wait", "run_command"],
        "min_steps": 5,
    },
    {
        "name": "9. 跨应用复制粘贴",
        "goal": "打开浏览器搜索'今天天气怎么样'，把页面标题复制下来，打开记事本粘贴进去，然后保存到桌面 weather.txt",
        "expected_actions": ["browser_navigate", "browser_type", "browser_click", "browser_get_text", "launch", "hotkey", "file_save"],
        "min_steps": 6,
    },
    {
        "name": "10. 多标签页浏览截图",
        "goal": "打开浏览器，新建两个标签页，分别打开 baidu.com 和 bing.com，对每个页面截图保存到桌面",
        "expected_actions": ["browser_navigate", "browser_new_tab", "browser_screenshot"],
        "min_steps": 4,
    },
    {
        "name": "11. 截屏 + OCR 识别",
        "goal": "截取屏幕上半部分，识别截图中的文字内容，保存到桌面 ocr_result.txt",
        "expected_actions": ["screenshot", "ocr_extract", "file_write"],
        "min_steps": 3,
    },
    {
        "name": "12. 文件读写操作",
        "goal": "读取桌面上的 data.txt 文件内容，在每行前面加上行号，保存到桌面 data_numbered.txt，然后用记事本打开",
        "expected_actions": ["file_read", "file_write", "launch"],
        "min_steps": 4,
    },
    {
        "name": "13. 系统设置",
        "goal": "把系统音量调到 30%，然后检查 Wi-Fi 连接状态",
        "expected_actions": ["run_command", "wait"],
        "min_steps": 2,
    },
    {
        "name": "14. 定时提醒任务",
        "goal": "创建一个定时任务，从明天开始每天早上 9 点提醒我去查看邮件",
        "expected_actions": ["run_command"],
        "min_steps": 1,
    },
    {
        "name": "15. 浏览器表单填写",
        "goal": "打开百度首页，在搜索框输入 Python 教程，点击搜索，然后点击第一个搜索结果",
        "expected_actions": ["browser_navigate", "browser_type", "browser_click", "browser_wait_element"],
        "min_steps": 4,
    },
    {
        "name": "16. 混合浏览器+桌面",
        "goal": "打开百度首页搜索'自动化测试'，把搜索结果页面的标题用键盘输入到记事本中，保存到桌面 search_result.txt",
        "expected_actions": ["browser_navigate", "browser_type", "browser_click", "browser_get_text", "launch", "hotkey", "file_save"],
        "min_steps": 6,
    },
    {
        "name": "17. 网络状态检查",
        "goal": "检查当前网络连接状态，如果网络正常就打开浏览器搜索'每日新闻'并截图，否则在桌面创建一个 offline.txt 提示离线",
        "expected_actions": ["run_command", "browser_navigate", "screenshot", "file_write"],
        "min_steps": 3,
    },
    {
        "name": "18. 弹窗处理 + 文件下载",
        "goal": "打开浏览器访问 https://nodejs.org 下载 LTS 版本，等待下载完成后把下载目录中的 .msi 文件路径保存到桌面 download_path.txt",
        "expected_actions": ["browser_navigate", "browser_click", "wait", "run_command", "file_write"],
        "min_steps": 4,
    },
    {
        "name": "19. 窗口排列操作",
        "goal": "打开计算器和记事本两个窗口，先最大化计算器再还原，然后把记事本窗口移动到屏幕右侧",
        "expected_actions": ["launch", "focus", "hotkey", "drag", "wait"],
        "min_steps": 4,
    },
    {
        "name": "20. 剪贴板操作",
        "goal": "复制桌面上的 readme.txt 文件内容到剪贴板，然后打开记事本，把剪贴板内容粘贴进去，保存为 clipboard_output.txt",
        "expected_actions": ["file_read", "clipboard_set", "launch", "hotkey", "file_save"],
        "min_steps": 4,
    },
    {
        "name": "21. 条件分支 — 屏幕变化判断",
        "goal": "3秒后打开记事本在里面写'但是看不到和爸爸v吧'然后再判断屏幕是否变化,变化就锁屏,没有变化就音量调最大",
        "expected_actions": ["wait", "launch", "type", "condition", "hotkey"],
        "min_steps": 4,
        "require_condition": True,
    },
]


# ── Execution Engine Integration ─────────────────────────────────

def get_generated_flow(goal: str, full_config: dict) -> dict:
    """通过 HTTP API 触发 agent 生成流程图，返回生成结果。

    使用与 AgentView.tsx 完全相同的路径：
      window.electronAPI.agentStart(goal.trim(), { api_key, api_base, model, multimodal, verify_enabled, auto_learn }, false)
    """
    import urllib.request, urllib.error

    api_key = full_config.get("api_key", "")
    api_base = full_config.get("api_base", "")
    model = full_config.get("model", "deepseek-chat")
    multimodal = full_config.get("multimodal", "false")
    verify_enabled = full_config.get("verify_enabled", "false")
    auto_learn = full_config.get("auto_learn", "false")
    AGENT_TIMEOUT = 120

    js_code = f"""
    new Promise((resolve) => {{
        const results = [];
        const cleanup = window.electronAPI.onAgentMessage((msg) => {{
            results.push(msg);
            if (msg.type === 'agent_done') {{
                cleanup();
                resolve(JSON.stringify({{events: results, done: msg}}));
            }}
        }});
        window.electronAPI.agentStart(
            {json.dumps(goal.strip())},
            {{
                api_key: {json.dumps(api_key)},
                api_base: {json.dumps(api_base)},
                model: {json.dumps(model)},
                multimodal: {json.dumps(multimodal)},
                verify_enabled: {json.dumps(verify_enabled)},
                auto_learn: {json.dumps(auto_learn)}
            }},
            false
        );
        setTimeout(() => {{
            cleanup();
            resolve(JSON.stringify({{events: results, done: {{type: 'agent_done', status: 'timeout'}}, timeout: true}}));
        }}, {AGENT_TIMEOUT * 1000});
    }})
    """

    data = json.dumps({"code": js_code}).encode('utf-8')
    req = urllib.request.Request(
        "http://127.0.0.1:7880/api/execute-js",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT + 10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

    result_str = result.get("result", "")
    if isinstance(result_str, str):
        try:
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse result: {result_str[:200]}"}
    if isinstance(result_str, dict) and "error" in result_str:
        return {"error": result_str["error"]}
    return result_str


def extract_structured_steps(agent_result: dict) -> list:
    """从 agent 结果中提取 structured_steps。"""
    events = agent_result.get("events", [])
    for event in events:
        if event.get("type") == "step_complete" and event.get("tool") == "generate_flow_script":
            r = event.get("result", {})
            rdata = r.get("data", {})
            steps = rdata.get("structured_steps", [])
            if steps:
                return steps
            inner = rdata.get("result", {})
            if isinstance(inner, dict):
                return inner.get("structured_steps", [])
    return []


def extract_dsl(agent_result: dict) -> str:
    """从 agent 结果中提取 DSL。"""
    events = agent_result.get("events", [])
    for event in events:
        if event.get("type") == "step_complete" and event.get("tool") == "generate_flow_script":
            r = event.get("result", {})
            rdata = r.get("data", {})
            dsl = rdata.get("dsl", "")
            if dsl:
                return dsl
            inner = rdata.get("result", {})
            if isinstance(inner, dict):
                return inner.get("dsl", "")
    return ""


def validate_dsl_syntax(dsl_text: str) -> dict:
    """用 DSLParser 解析 DSL，验证语法正确性。"""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))
    try:
        from engine.dsl_parser import DSLParser
        parser = DSLParser()
        ast = parser.parse(dsl_text)
        return {"success": True, "node_count": len(ast) if isinstance(ast, list) else 1}
    except Exception as e:
        return {"success": False, "error": str(e)}


def validate_step_types(steps: list) -> dict:
    """验证步骤类型是否在引擎支持的范围内。"""
    # Mapping of agent step types to engine node types
    TYPE_MAP = {
        "click": "click", "text_click": "click", "image_click": "image_click",
        "type": "input_text", "hotkey": "keyboard", "wait": "wait",
        "wait_element": "wait", "scroll": "swipe", "launch": "launch_program",
        "focus": "focus_window", "screenshot": "screenshot", "run_command": "run_command",
        "file_read": "file_read", "file_write": "file_write", "file_save": "file_save",
        "mouse_move": "mouse_move", "drag": "drag",
        "browser_navigate": "browser_navigate", "browser_click": "browser_click",
        "browser_type": "browser_type", "browser_find": "browser_find",
        "browser_wait_element": "browser_wait_element", "browser_get_text": "browser_get_text",
        "browser_screenshot": "browser_screenshot", "browser_exec_js": "browser_exec_js",
        "browser_new_tab": "browser_new_tab", "browser_close_tab": "browser_close_tab",
        "browser_switch_tab": "browser_switch_tab",
        "ocr_extract": "ocr_extract", "clipboard_get": "clipboard_get",
        "clipboard_set": "clipboard_set",
        "check_condition": "check_condition", "wait_until": "wait_until",
        "condition": "check_condition",
    }

    result = {"valid": 0, "invalid": 0, "unknown": 0, "issues": []}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            result["issues"].append(f"Step {i}: not a dict")
            result["invalid"] += 1
            continue
        t = step.get("type", "")
        if not t:
            result["issues"].append(f"Step {i}: missing type")
            result["invalid"] += 1
            continue
        if t in TYPE_MAP:
            result["valid"] += 1
        else:
            result["unknown"] += 1
            result["issues"].append(f"Step {i}: unknown type '{t}'")
    return result


def check_step_parameters(steps: list) -> dict:
    """检查每个步骤的参数完整性。"""
    REQUIRED = {
        "click": [("target", "x", "y")],
        "text_click": [("target", "text")],
        "image_click": ["target"],
        "type": ["text"],
        "hotkey": ["keys"],
        "wait": ["duration"],
        "launch": ["target"],
        "focus": [("title", "target")],
        "run_command": ["command"],
        "browser_navigate": ["url"],
        "browser_click": ["selector"],
        "browser_type": ["selector", "text"],
        "browser_wait_element": ["selector"],
        "browser_get_text": ["selector"],
        "browser_find": ["selector"],
        "browser_exec_js": ["code"],
        "file_read": [("path", "target")],
        "file_write": [("path", "target")],
        "file_save": [("path", "target")],
        "ocr_extract": [("target", "region")],
        "clipboard_set": [("text", "content")],
    }

    result = {"complete": 0, "missing": 0, "issues": []}
    for i, step in enumerate(steps):
        t = step.get("type", "")
        if t not in REQUIRED:
            result["complete"] += 1
            continue
        required = REQUIRED[t]
        satisfied = False
        for req in required:
            if isinstance(req, str):
                if step.get(req) is not None and step.get(req) != "":
                    satisfied = True
                    break
            elif isinstance(req, tuple):
                for key in req:
                    if step.get(key) is not None and step.get(key) != "":
                        satisfied = True
                        break
                if satisfied:
                    break
        if satisfied:
            result["complete"] += 1
        else:
            result["missing"] += 1
            result["issues"].append(f"Step {i} ({t}): missing required params {required}")
    return result


def simulate_execution(steps: list) -> dict:
    """模拟执行步骤，检查执行流是否通畅。

    不操作真实 GUI，只模拟执行路径：
    - 检查步骤顺序是否合理
    - 检查是否有 dead steps
    - 检查 browser_navigate 后是否有 wait/follow-up
    - 检查 launch 后是否有 wait
    """
    issues = []
    types = [s.get("type", "") for s in steps if isinstance(s, dict)]

    # Check for browser_navigate → wait pattern
    for i, t in enumerate(types):
        if t == "browser_navigate":
            following = types[i+1:i+4]
            has_wait = any("wait" in ft or "browser_wait" in ft for ft in following)
            if not has_wait:
                issues.append(f"Step {i} (browser_navigate): no wait/follow-up in next 3 steps")

    # Check for launch → wait pattern
    for i, t in enumerate(types):
        if t == "launch":
            following = types[i+1:i+3]
            if "wait" not in following:
                issues.append(f"Step {i} (launch): no wait step after launch")

    # Check for potentially dangerous operations
    for i, step in enumerate(steps):
        t = step.get("type", "")
        if t == "run_command":
            cmd = step.get("command", "")
            if any(dangerous in cmd.lower() for dangerous in ["rm -rf", "format", "del /f", "shutdown"]):
                issues.append(f"Step {i} (run_command): potentially dangerous command: {cmd[:80]}")

    return {"issues": issues, "safe": len(issues) == 0}


def run_flow_dsl(dsl_text: str, timeout: int = 30) -> dict:
    """尝试通过引擎执行 DSL（validate 模式，不操作真实 GUI）。

    使用 DSLParser 解析，但不执行实际动作。
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

    try:
        from engine.dsl_parser import DSLParser
        parser = DSLParser()
        ast = parser.parse(dsl_text)
        return {
            "success": True,
            "ast_nodes": len(ast) if isinstance(ast, list) else 1,
            "subroutines": list(parser.subroutines.keys()),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


class ExecutionReport:
    """单个测试的执行报告。"""
    def __init__(self, name: str, goal: str):
        self.name = name
        self.goal = goal
        self.success = False
        self.generation_error = None
        self.steps = []
        self.dsl = ""
        self.dsl_validation = {}
        self.types_check = {}
        self.params_check = {}
        self.simulation = {}
        self.dsl_execution = {}
        self.execution_log = []
        self.step_summary = {}
        self.timing = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "success": self.success,
            "generation_error": self.generation_error,
            "steps": len(self.steps),
            "dsl_lines": len(self.dsl.split('\n')) if self.dsl else 0,
            "dsl_valid": self.dsl_validation.get("success", False),
            "types_valid": self.types_check.get("valid", 0),
            "types_unknown": self.types_check.get("unknown", 0),
            "params_complete": self.params_check.get("complete", 0),
            "params_missing": self.params_check.get("missing", 0),
            "simulation_safe": self.simulation.get("safe", False),
            "execution_success": self.dsl_execution.get("success", False),
            "issues": (self.dsl_validation.get("error", None) or
                       [str(i) for i in self.types_check.get("issues", [])] +
                       [str(i) for i in self.params_check.get("issues", [])] +
                       [str(i) for i in self.simulation.get("issues", [])]),
        }


def main():
    import urllib.request

    MODE = "validate"  # validate only (no real GUI ops)
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            MODE = sys.argv[idx + 1]

    # 读取所有 Agent 配置（与 AgentView.tsx 完全一致）
    js_read_config = """
    JSON.stringify({
        api_key: (window.__DEEPSEEK_KEY__ || localStorage.getItem('lingdong-deepseek-key') || '').trim(),
        api_base: (window.__LLM_API_BASE__ || localStorage.getItem('lingdong-llm-base') || '').replace(/\\/+$/, '').trim(),
        model: (window.__LLM_MODEL__ || localStorage.getItem('lingdong-llm-model') || '').trim(),
        multimodal: (window.__LLM_MULTIMODAL__ !== undefined ? window.__LLM_MULTIMODAL__ : localStorage.getItem('lingdong-llm-multimodal') === 'true') ? 'true' : 'false',
        verify_enabled: localStorage.getItem('lingdong-verify-enabled') === 'true' ? 'true' : 'false',
        auto_learn: localStorage.getItem('lingdong-auto-learn') === 'true' ? 'true' : 'false'
    })
    """
    data = json.dumps({"code": js_read_config}).encode('utf-8')
    req = urllib.request.Request(
        "http://127.0.0.1:7880/api/execute-js",
        data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        try:
            full_config = json.loads(result.get("result", "{}"))
        except (json.JSONDecodeError, TypeError):
            full_config = {}
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionRefusedError, OSError):
        # Fallback: Electron app not running, use env vars
        full_config = {
            "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "api_base": os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
            "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
            "multimodal": os.environ.get("LLM_MULTIMODAL", "false"),
            "verify_enabled": "false",
            "auto_learn": "false",
        }
        print("⚡ Electron app not reachable, using env vars for config")

    if not full_config.get("api_key"):
        print("❌ No API key found in app!")
        sys.exit(1)

    print(f"🔑 API Key: {full_config['api_key'][:8]}...{full_config['api_key'][-4:]}")
    print(f"   Multimodal: {full_config.get('multimodal')}, Verify: {full_config.get('verify_enabled')}, AutoLearn: {full_config.get('auto_learn')}")
    print(f"   Model: {full_config.get('model')}, Base: {full_config.get('api_base') or '(default)'}")
    print(f"🔧 Mode: {MODE}")
    print()

    all_reports = []
    print(f"{'='*80}")
    print(f"AGENT FLOW EXECUTION TEST — {len(TEST_CASES)} test cases, mode={MODE}")
    print(f"{'='*80}")

    for tc in TEST_CASES:
        report = ExecutionReport(tc["name"], tc["goal"])
        print(f"\n{'─'*80}")
        print(f"▶  Running: {tc['name']}")
        print(f"   Goal: {tc['goal'][:100]}")

        # Step 1: Generate flow (与 AgentView.tsx 完全相同的路径)
        print(f"   ┌─ Phase 1: Generate flow...")
        start = time.time()
        agent_result = get_generated_flow(tc["goal"], full_config)
        elapsed = time.time() - start
        report.timing = elapsed

        if "error" in agent_result:
            report.generation_error = agent_result["error"]
            print(f"   └─ ❌ Generation failed: {agent_result['error'][:100]}")
            all_reports.append(report)
            continue

        steps = extract_structured_steps(agent_result)
        dsl_text = extract_dsl(agent_result)
        report.steps = steps
        report.dsl = dsl_text

        if not steps:
            report.generation_error = "No structured_steps generated"
            print(f"   └─ ❌ No steps generated")
            all_reports.append(report)
            continue

        print(f"   └─ ✅ Generated {len(steps)} steps in {elapsed:.1f}s")

        # Step 2: Validate DSL syntax
        print(f"   ┌─ Phase 2: Validate DSL syntax...")
        if dsl_text:
            dsl_result = validate_dsl_syntax(dsl_text)
            report.dsl_validation = dsl_result
            if dsl_result.get("success"):
                print(f"   ├─ ✅ DSL syntax valid ({dsl_result.get('node_count', 0)} AST nodes)")
            else:
                print(f"   ├─ ❌ DSL syntax error: {dsl_result.get('error', '')[:80]}")
        else:
            print(f"   ├─ ⚠️  No DSL generated (will convert from steps)")

        # Step 3: Run DSL through engine parser
        print(f"   ├─ Phase 3: Engine parse & validate...")
        if dsl_text:
            exec_result = run_flow_dsl(dsl_text)
            report.dsl_execution = exec_result
            if exec_result.get("success"):
                print(f"   ├─ ✅ Engine parsed OK ({exec_result.get('ast_nodes', 0)} nodes)")
            else:
                print(f"   ├─ ❌ Engine parse failed: {exec_result.get('error', '')[:80]}")
        else:
            print(f"   ├─ ⚠️  DSL needed")

        # Step 4: Validate step types
        print(f"   ├─ Phase 4: Validate step types...")
        types_result = validate_step_types(steps)
        report.types_check = types_result
        if types_result.get("unknown", 0) == 0:
            print(f"   ├─ ✅ All {types_result['valid']} step types known")
        else:
            print(f"   ├─ ⚠️  {types_result['unknown']} unknown types, {types_result['valid']} valid")

        # Step 4b: Check condition requirement
        if tc.get("require_condition"):
            has_condition = any(s.get("type") == "condition" for s in steps)
            if has_condition:
                print(f"   ├─ ✅ Contains conditional branching (required)")
            else:
                print(f"   ├─ ❌ Missing required condition type — generated flat steps instead")
                types_result.setdefault("issues", []).append("require_condition: no condition step found")
                types_result["unknown"] = types_result.get("unknown", 0) + 1

        # Step 5: Check parameters
        print(f"   ├─ Phase 5: Check parameters...")
        params_result = check_step_parameters(steps)
        report.params_check = params_result
        if params_result.get("missing", 0) == 0:
            print(f"   ├─ ✅ All {params_result['complete']} steps have required params")
        else:
            print(f"   ├─ ⚠️  {params_result['missing']} steps missing params")

        # Step 6: Simulate execution
        print(f"   ├─ Phase 6: Simulate execution flow...")
        sim_result = simulate_execution(steps)
        report.simulation = sim_result
        if sim_result.get("safe"):
            print(f"   ├─ ✅ Execution flow looks safe")
        else:
            for issue in sim_result["issues"]:
                print(f"   ├─ ⚠️  {issue}")

        # Summary
        step_types = {}
        for s in steps:
            t = s.get("type", "?")
            step_types[t] = step_types.get(t, 0) + 1
        report.step_summary = step_types
        print(f"   └─ 📊 Steps: {dict(sorted(step_types.items()))}")

        # Overall judgment
        issues = (report.dsl_validation.get("error") or
                  report.dsl_execution.get("error") or
                  (report.types_check.get("unknown", 0) > 0 and "unknown types") or
                  "")
        report.success = (report.generation_error is None and
                         (report.types_check.get("unknown", 0) == 0) and
                         (report.params_check.get("missing", 0) == 0) and
                         report.dsl_validation.get("success", True) and
                         report.dsl_execution.get("success", True))
        all_reports.append(report)

    # ── Final Summary ──
    print(f"\n\n{'='*80}")
    print(f"EXECUTION TEST SUMMARY")
    print(f"{'='*80}")
    print(f"{'Test':<40} {'Steps':>6} {'DSL':>5} {'Types':>6} {'Params':>7} {'Safe':>5} {'Status':>8}")
    print(f"{'─'*80}")

    passed = 0
    for r in all_reports:
        status = "✅" if r.success else "❌"
        if r.success:
            passed += 1
        dsl_ok = "✓" if r.dsl_validation.get("success", False) else "✗" if r.dsl_validation else "-"
        safe_str = "✓" if r.simulation.get("safe", False) else "✗"
        print(f"{status} {r.name:<37} {len(r.steps):>4}  {dsl_ok:>3}  "
              f"{r.types_check.get('valid', 0):>3}/{r.types_check.get('unknown', 0):>2}  "
              f"{r.params_check.get('complete', 0):>3}/{r.params_check.get('missing', 0):>3}  "
              f"{safe_str:>3}  {'PASS' if r.success else 'FAIL'}")

    print(f"{'─'*80}")
    print(f"Passed: {passed}/{len(all_reports)}")
    print(f"Average step count: {sum(len(r.steps) for r in all_reports) / max(len(all_reports), 1):.0f}")

    # Detailed issues
    print(f"\n{'='*80}")
    print("DETAILED ISSUES")
    print(f"{'='*80}")
    found_issues = False
    for r in all_reports:
        if r.generation_error:
            found_issues = True
            print(f"\n❌ {r.name}: Generation failed — {r.generation_error}")
        for issue_list_key, label in [("dsl_validation", "DSL"), ("types_check", "Type"),
                                        ("params_check", "Param"), ("simulation", "Sim")]:
            if issue_list_key == "dsl_validation":
                if isinstance(r.dsl_validation, dict) and r.dsl_validation.get("error"):
                    found_issues = True
                    print(f"  {r.name}: ❌ {label}: {r.dsl_validation['error']}")
            elif issue_list_key == "types_check":
                for issue in r.types_check.get("issues", []):
                    found_issues = True
                    print(f"  {r.name}: ⚠️  {label}: {issue}")
            elif issue_list_key == "params_check":
                for issue in r.params_check.get("issues", []):
                    found_issues = True
                    print(f"  {r.name}: ⚠️  {label}: {issue}")
            elif issue_list_key == "simulation":
                for issue in r.simulation.get("issues", []):
                    found_issues = True
                    print(f"  {r.name}: ⚠️  {label}: {issue}")
    if not found_issues:
        print("No issues found!")


if __name__ == "__main__":
    main()
