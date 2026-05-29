#!/usr/bin/env python
"""
Agent 流程图生成质量测试套件。
测试方式：通过 Electron HTTP API 触发 agent-start（与用户操作路径完全一致），
收集 agent-message 事件结果，做全面校验。

用法：
  python tests/agent_flow_test.py [--api-url http://127.0.0.1:7880] [--goal "自定义目标"]

默认会运行所有预设的复杂测试用例，输出每个用例的质量报告。
"""

import io, json, os, sys, time, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
# Ensure unbuffered output so background task captures progress in real-time
os.environ["PYTHONUNBUFFERED"] = "1"
_print = print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _print(*args, **kwargs)

API_URL = "http://127.0.0.1:7880"


def api_call(endpoint, payload, timeout=30):
    """Call the Electron HTTP API."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f"{API_URL}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}


def execute_js(code, timeout=30):
    """Execute JavaScript in the renderer and return the result."""
    result = api_call("/api/execute-js", {"code": code}, timeout=timeout)
    if "result" in result:
        return result["result"]
    return result


def get_api_key():
    """Get the API key from the app's localStorage."""
    key = execute_js(
        "localStorage.getItem('lingdong-deepseek-key') || window.__DEEPSEEK_KEY__ || ''"
    )
    return key


def get_api_config():
    """Get all API config from the app."""
    config_str = execute_js("JSON.stringify({"
        "apiBase: localStorage.getItem('lingdong-api-base') || '',"
        "model: localStorage.getItem('lingdong-llm-model') || 'deepseek-chat',"
        "multimodal: localStorage.getItem('lingdong-llm-multimodal') || 'false',"
        "auto_learn: localStorage.getItem('lingdong-auto-learn') || 'false'"
    "})")
    return json.loads(config_str)


def trigger_agent_generation(goal, api_key, config=None):
    """
    Trigger agent flowchart generation via execute-js.
    This goes through the exact same code path as the user typing in the Agent dialog:
      renderer → electronAPI.agentStart() → IPC → main.ts startAgentProcess() → python -m agent

    Returns the complete generation result including pseudocode, dsl, structured_steps.
    """
    if config is None:
        config = {}

    api_base = config.get("apiBase", "")
    model = config.get("model", "deepseek-chat")

    # Use a longer timeout for the API call since agent generation can take 30-90s
    AGENT_TIMEOUT = 120  # 2 minutes max for generation

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
            {json.dumps(goal)},
            {{api_key: {json.dumps(api_key)}, api_base: {json.dumps(api_base)}, model: {json.dumps(model)}}},
            false
        );
        // Safety timeout: resolve anyway after timeout
        setTimeout(() => {{
            cleanup();
            resolve(JSON.stringify({{events: results, done: {{type: 'agent_done', status: 'timeout'}}, timeout: true}}));
        }}, {AGENT_TIMEOUT * 1000});
    }})
    """

    result_str = execute_js(js_code, timeout=AGENT_TIMEOUT + 10)
    if isinstance(result_str, str):
        try:
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse result: {result_str[:200]}"}
    if isinstance(result_str, dict) and "error" in result_str:
        return {"error": result_str["error"]}
    return result_str


# ── Validation Functions ──────────────────────────────────────────

# Step type aliases (must match flow_features.py)
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
    "clipboard_get_text": "clipboard_get",
    "clipboard_set_text": "clipboard_set",
    "read_clipboard": "clipboard_get",
    "ocr_text": "ocr_extract",
    "ocr_read": "ocr_extract",
}

# Complete set of known step types (must match AGENT_CAPABILITIES in flow_features.py)
KNOWN_STEP_TYPES = {
    "click", "text_click", "image_click", "type", "hotkey", "wait", "wait_element",
    "scroll", "launch", "focus", "screenshot", "run_command", "file_read", "file_write",
    "file_save", "mouse_move", "drag",
    "browser_navigate", "browser_click", "browser_type", "browser_find",
    "browser_wait_element", "browser_get_text", "browser_screenshot", "browser_exec_js",
    "browser_new_tab", "browser_close_tab", "browser_switch_tab",
    "ocr_extract", "clipboard_get", "clipboard_set",
    "check_condition", "wait_until",
    # Tool-call type (for @feature tools not in standard list)
    "tool_call",
}

# Types that should NOT appear in flattened linear steps
CONTROL_FLOW_TYPES = {"condition", "loop", "sub_flow"}

# Type-specific required parameters
TYPE_REQUIRED_PARAMS = {
    "click": [("target", "x", "y")],  # need target OR coordinates
    "type": ["text"],
    "wait": ["duration"],
    "hotkey": ["keys"],
    "launch": ["target"],
    "focus": [("title", "target")],
    "scroll": ["target"],
    "run_command": ["command"],
    "browser_navigate": ["url"],
    "browser_click": ["selector"],
    "browser_type": ["selector", "text"],
    "browser_wait_element": ["selector"],
    "browser_get_text": ["selector"],
    "browser_find": ["selector"],
    "browser_exec_js": ["code"],
    "browser_screenshot": [],  # no required params
    "mouse_move": [("x",), ("y",)],
    "drag": [("from_target", "target")],
    "image_click": ["target"],
    "text_click": [("target", "text")],
    "check_condition": [("condition", "target")],
    "wait_until": [("condition", "target")],
    "screenshot": [],
    "file_read": [("path", "target")],
    "file_write": [("path", "target")],
    "file_save": [("path", "target")],
}


def validate_structured_step(step, index):
    """Validate a single structured step. Returns list of issues."""
    issues = []

    if not isinstance(step, dict):
        return [f"Step {index}: not a dict ({type(step).__name__})"]

    step_type = step.get("type", "")
    if not step_type:
        issues.append(f"Step {index}: missing 'type' field")
        return issues

    # Normalize via aliases
    if step_type in STEP_TYPE_ALIASES:
        step["type"] = STEP_TYPE_ALIASES[step_type]
        step_type = step["type"]

    # Check for control flow violation
    if step_type in CONTROL_FLOW_TYPES:
        issues.append(f"Step {index} ({step_type}): FLATTENING RULE VIOLATION — control flow type should not appear in linear steps")
        return issues

    # Check if type is known
    if step_type not in KNOWN_STEP_TYPES:
        issues.append(f"Step {index}: unknown type '{step_type}' — not in capabilities list")
        return issues

    # Check required params
    if step_type in TYPE_REQUIRED_PARAMS:
        required_list = TYPE_REQUIRED_PARAMS[step_type]
        if required_list:
            # Check if at least one param group is satisfied
            satisfied = False
            for req in required_list:
                if isinstance(req, str):
                    if step.get(req) is not None and step.get(req) != "":
                        satisfied = True
                        break
                elif isinstance(req, tuple):
                    # OR-group: at least one of these must be present
                    for key in req:
                        if step.get(key) is not None and step.get(key) != "":
                            satisfied = True
                            break
                    if satisfied:
                        break
            if not satisfied:
                issues.append(f"Step {index} ({step_type}): missing required params (need one of: {required_list})")

    return issues


def validate_pseudocode_vs_structured(pseudocode, steps):
    """Check that pseudocode lines roughly match structured steps count."""
    if not pseudocode or not steps:
        return []

    pseudo_lines = [l.strip() for l in pseudocode.split('\n') if l.strip() and not l.strip().startswith('#')]

    if len(pseudo_lines) != len(steps):
        return [f"Pseudocode lines ({len(pseudo_lines)}) vs structured_steps ({len(steps)}) count mismatch"]

    return []


def validate_dsl_vs_structured(dsl_text, steps):
    """Check that DSL lines roughly match structured steps count."""
    if not dsl_text or not steps:
        return []

    dsl_lines = [l.strip() for l in dsl_text.split('\n') if l.strip()]

    if len(dsl_lines) != len(steps):
        return [f"DSL lines ({len(dsl_lines)}) vs structured_steps ({len(steps)}) count mismatch"]

    return []


def analyze_flow_structure(steps):
    """Analyze the flow for structural issues."""
    issues = []
    step_types = [s.get("type", "") for s in steps if isinstance(s, dict)]

    if not step_types:
        return ["No steps to analyze"]

    # Check for browser flow completeness
    if any(t.startswith("browser_") for t in step_types):
        # Browser workflow should start with browser_navigate
        browser_indices = [i for i, t in enumerate(step_types) if t.startswith("browser_")]
        first_browser = browser_indices[0]
        if step_types[first_browser] != "browser_navigate" and first_browser > 0:
            # Check if there's a browser_navigate at all
            if "browser_navigate" not in step_types:
                issues.append("Browser flow: missing browser_navigate step")

        # Check for browser_wait after navigation
        nav_indices = [i for i, t in enumerate(step_types) if t == "browser_navigate"]
        for ni in nav_indices:
            following = step_types[ni+1:ni+4] if ni+4 <= len(step_types) else step_types[ni+1:]
            has_wait = any("wait" in t for t in following)
            if not has_wait:
                issues.append(f"Browser: no wait step after browser_navigate (step {ni})")

    # Check for app launch → wait pattern
    launch_indices = [i for i, t in enumerate(step_types) if t == "launch"]
    for li in launch_indices:
        following = step_types[li+1:li+3] if li+3 <= len(step_types) else step_types[li+1:]
        has_wait = any(t == "wait" for t in following)
        if not has_wait:
            issues.append(f"Launch without following wait step (step {li})")

    # Check for sequences of only waits
    if len(step_types) == 1 and step_types[0] == "wait":
        issues.append("Flow is only a single wait step — likely incomplete")

    # Check total step count
    if len(step_types) < 2:
        issues.append("Very short flow (less than 2 steps)")

    return issues


def extract_generation_data(result):
    """Extract generation data from the agent result, handling nested structures."""
    data = {}

    # Navigate the result structure
    events = result.get("events", [])

    # Show raw events for debugging
    data["_raw_events"] = []
    for event in events:
        ev_type = event.get("type", "")
        ev_info = {"type": ev_type}
        if ev_type == "error":
            ev_info["message"] = event.get("message", str(event))
            data["_raw_events"].append(ev_info)
        elif ev_type == "log":
            ev_info["level"] = event.get("level", "")
            ev_info["message"] = event.get("message", "")
            data["_raw_events"].append(ev_info)
        elif ev_type == "agent_done":
            ev_info["status"] = event.get("status", "")
            ev_info["fail_reason"] = event.get("fail_reason", "")
            data["_raw_events"].append(ev_info)
        elif ev_type == "step_complete":
            ev_info["tool"] = event.get("tool", "")
            ev_info["status"] = event.get("status", "")
            r = event.get("result", {})
            if isinstance(r, dict):
                ev_info["result_success"] = r.get("success", r.get("data", {}).get("success", None))
                ev_info["result_error"] = r.get("error", r.get("data", {}).get("error", ""))
            data["_raw_events"].append(ev_info)

    # Find step_complete with generate_flow_script
    for event in events:
        if event.get("type") == "step_complete" and event.get("tool") == "generate_flow_script":
            r = event.get("result", {})
            rdata = r.get("data", {})

            # Handle nested result structure
            if isinstance(rdata, dict):
                data["success"] = r.get("success", False)
                data["pseudocode"] = rdata.get("pseudocode", "")
                data["explanation"] = rdata.get("explanation", "")
                data["dsl"] = rdata.get("dsl", "")
                data["structured_steps"] = rdata.get("structured_steps", [])
                data["warnings"] = rdata.get("warnings", [])

                # Check alternate nesting (result.result level)
                if not data["pseudocode"]:
                    inner = rdata.get("result", {})
                    if isinstance(inner, dict):
                        data["pseudocode"] = inner.get("pseudocode", "")
                        data["explanation"] = inner.get("explanation", "")
                        data["dsl"] = inner.get("dsl", "")
                        data["structured_steps"] = inner.get("structured_steps", [])
                        data["warnings"] = inner.get("warnings", [])
            elif isinstance(rdata, str):
                data["raw"] = rdata

            break

    # Check done status
    done = result.get("done", {})
    data["status"] = done.get("status", "unknown")
    data["summary"] = done.get("summary", "")
    data["timeout"] = result.get("timeout", False)

    return data


def validate_generation(data):
    """Full validation of generated flow data. Returns a report dict."""
    report = {
        "passed": 0,
        "failed": 0,
        "warnings": 0,
        "issues": [],
        "stats": {}
    }

    # 1. Basic success check
    if data.get("success"):
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["issues"].append("❌ Generation failed")

    pseudo = data.get("pseudocode", "")
    dsl_text = data.get("dsl", "")
    steps = data.get("structured_steps", [])

    report["stats"]["pseudocode_lines"] = len([l for l in pseudo.split('\n') if l.strip()]) if pseudo else 0
    report["stats"]["dsl_lines"] = len([l for l in dsl_text.split('\n') if l.strip()]) if dsl_text else 0
    report["stats"]["structured_steps_count"] = len(steps)
    report["stats"]["has_pseudocode"] = bool(pseudo)
    report["stats"]["has_dsl"] = bool(dsl_text)
    report["stats"]["has_structured_steps"] = bool(steps)

    # 2. Pseudocode present
    if pseudo:
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["issues"].append("❌ No pseudocode generated")

    # 3. Structured steps present
    if steps and len(steps) > 0:
        report["passed"] += 1
    else:
        report["failed"] += 1
        report["issues"].append("❌ No structured_steps generated")
        return report  # Can't validate further

    # 4. Validate each structured step
    step_issue_count = 0
    for i, step in enumerate(steps):
        step_issues = validate_structured_step(step, i)
        for issue in step_issues:
            report["issues"].append(f"  {issue}")
            step_issue_count += 1

    if step_issue_count == 0:
        report["passed"] += 1
    else:
        report["failed"] += 1

    # 5. Unsupported control flow types
    unsupported = {"condition", "loop", "sub_flow"}
    control_flow_issues = [s for s in steps if isinstance(s, dict) and s.get("type") in unsupported]
    if control_flow_issues:
        report["issues"].append(f"⚠️  Found {len(control_flow_issues)} control flow steps (flattening rule violation)")
        report["warnings"] += 1
    else:
        report["passed"] += 1

    # 6. Pseudocode vs steps consistency
    consistency_issues = validate_pseudocode_vs_structured(pseudo, steps)
    for issue in consistency_issues:
        report["issues"].append(f"⚠️  {issue}")
        report["warnings"] += 1
    if not consistency_issues:
        report["passed"] += 1
    else:
        report["warnings"] += 1  # Not a failure, just a warning

    # 7. DSL vs steps consistency
    dsl_issues = validate_dsl_vs_structured(dsl_text, steps)
    for issue in dsl_issues:
        report["issues"].append(f"⚠️  {issue}")
        report["warnings"] += 1
    if not dsl_issues:
        report["passed"] += 1

    # 8. Flow structure analysis
    flow_issues = analyze_flow_structure(steps)
    for issue in flow_issues:
        report["issues"].append(f"⚠️  {issue}")
        report["warnings"] += 1
    if not flow_issues:
        report["passed"] += 1

    return report


def print_report(test_name, data, report):
    """Pretty-print the test report."""
    print(f"\n{'='*60}")
    print(f"📋 Test: {test_name}")
    print(f"{'='*60}")

    # Print generation info
    print(f"\n📝 Generation Status: {data.get('status', 'unknown')}")
    if not data.get("success") and data.get("_raw_events"):
        for ev in data["_raw_events"]:
            if ev["type"] == "error":
                print(f"❌ Agent Error: {ev.get('message', '')[:200]}")
            elif ev["type"] == "step_complete" and ev.get("result_error"):
                print(f"❌ Step Error: {ev['result_error'][:200]}")
    if data.get("explanation"):
        print(f"💡 {data['explanation'][:200]}")

    print(f"\n📊 Stats:")
    for key, val in report.get("stats", {}).items():
        print(f"  {key}: {val}")

    # Print scores
    total = report["passed"] + report["failed"]
    score = (report["passed"] / total * 100) if total > 0 else 0
    print(f"\n🏆 Score: {report['passed']}/{total} passed ({score:.0f}%)")
    print(f"   Warnings: {report['warnings']}")

    # Print pseudocode preview
    pseudo = data.get("pseudocode", "")
    if pseudo:
        lines = [l for l in pseudo.split('\n') if l.strip()]
        print(f"\n📜 Pseudocode ({len(lines)} lines):")
        for line in lines[:15]:
            print(f"  {line}")
        if len(lines) > 15:
            print(f"  ... and {len(lines) - 15} more lines")

    # Print structured steps summary
    steps = data.get("structured_steps", [])
    if steps:
        step_counts = {}
        for s in steps:
            t = s.get("type", "unknown")
            step_counts[t] = step_counts.get(t, 0) + 1
        print(f"\n🔧 Steps summary ({len(steps)} total):")
        for t, c in sorted(step_counts.items()):
            print(f"  {t}: {c}")

    # Print issues
    if report["issues"]:
        print(f"\n⚠️  Issues ({len(report['issues'])}):")
        for issue in report["issues"]:
            print(f"  {issue}")

    if report["failed"] == 0 and report["warnings"] == 0:
        print(f"\n✅ PERFECT — no issues found!")
    elif report["failed"] == 0:
        print(f"\n✅ Generation successful with {report['warnings']} warnings")
    else:
        print(f"\n❌ {report['failed']} validation failures")

    return score


# ── Test Cases ────────────────────────────────────────────────────

TEST_CASES = [
    {
        "name": "1. 简单单步",
        "goal": "打开计算器",
        "expected_steps": ["launch"],
    },
    {
        "name": "2. 多步骤线性流程",
        "goal": "打开记事本，输入 hello world，等待 2 秒，截图保存到桌面",
        "expected_steps": ["launch", "type", "wait", "screenshot"],
    },
    {
        "name": "3. 带时间约束的多阶段流程",
        "goal": "5秒后打开浏览器搜索今天的天气，再等10秒后截屏保存",
        "expected_steps": ["wait", "browser_navigate", "browser_type", "browser_click", "wait", "screenshot"],
    },
    {
        "name": "4. 浏览器完整工作流",
        "goal": "打开百度首页，搜索'Claude AI 能力'，点击第一个搜索结果，等待页面加载完成后截图",
        "expected_steps": ["browser_navigate", "browser_wait_element", "browser_type", "browser_click", "browser_wait_element", "browser_click", "browser_wait_element", "screenshot"],
    },
    {
        "name": "5. 多阶段 + 条件处理",
        "goal": "打开微信电脑版，如果出现登录二维码就等待30秒让用户扫码，否则自动输入账号密码登录",
        "expected_steps": None,  # Complex - just validate
    },
    {
        "name": "6. 极复杂多工具协作",
        "goal": """打开 Chrome 浏览器，按顺序完成以下操作：
1. 访问 https://www.baidu.com
2. 在搜索框输入 "Python 教程"
3. 点击搜索按钮
4. 等待搜索结果加载
5. 点击第三个搜索结果
6. 等待页面加载
7. 截取当前页面截图保存到桌面
8. 获取页面标题文字
9. 把标题文字写入到桌面上的 result.txt 文件中
10. 最后用记事本打开 result.txt""",
        "expected_steps": None,
    },
    {
        "name": "7. 时序依赖链 — 剪贴板监控",
        "goal": "每隔5秒检查一次剪贴板内容，检测到内容变化后将新内容保存到桌面 clipboard_log.txt 文件，然后显示通知提醒",
        "expected_steps": None,
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
        "expected_steps": None,
    },
    # ── 新测试用例：真实用户场景 ─────────────────────────────────
    {
        "name": "9. 跨应用复制粘贴",
        "goal": "打开浏览器搜索'今天天气怎么样'，把页面标题复制下来，打开记事本粘贴进去，然后保存到桌面 weather.txt",
        "expected_steps": None,
    },
    {
        "name": "10. 多标签页浏览截图",
        "goal": "打开浏览器，新建两个标签页，分别打开 baidu.com 和 bing.com，对每个页面截图保存到桌面",
        "expected_steps": None,
    },
    {
        "name": "11. 截屏 + OCR 识别",
        "goal": "截取屏幕上半部分，识别截图中的文字内容，保存到桌面 ocr_result.txt",
        "expected_steps": None,
    },
    {
        "name": "12. 文件读写操作",
        "goal": "读取桌面上的 data.txt 文件内容，在每行前面加上行号，保存到桌面 data_numbered.txt，然后用记事本打开",
        "expected_steps": None,
    },
    {
        "name": "13. 系统设置",
        "goal": "把系统音量调到 30%，然后检查 Wi-Fi 连接状态",
        "expected_steps": None,
    },
    {
        "name": "14. 定时提醒任务",
        "goal": "创建一个定时任务，从明天开始每天早上 9 点提醒我去查看邮件",
        "expected_steps": None,
    },
    {
        "name": "15. 浏览器表单填写",
        "goal": "打开百度首页，在搜索框输入 Python 教程，点击搜索，然后点击第一个搜索结果",
        "expected_steps": None,
    },
    {
        "name": "16. 混合浏览器+桌面",
        "goal": "打开百度首页搜索'自动化测试'，把搜索结果页面的标题用键盘输入到记事本中，保存到桌面 search_result.txt",
        "expected_steps": None,
    },
    {
        "name": "17. 网络状态检查",
        "goal": "检查当前网络连接状态，如果网络正常就打开浏览器搜索'每日新闻'并截图，否则在桌面创建一个 offline.txt 提示离线",
        "expected_steps": None,
    },
    {
        "name": "18. 弹窗处理 + 文件下载",
        "goal": "打开浏览器访问 https://nodejs.org 下载 LTS 版本，等待下载完成后把下载目录中的 .msi 文件路径保存到桌面 download_path.txt",
        "expected_steps": None,
    },
    {
        "name": "19. 窗口排列操作",
        "goal": "打开计算器和记事本两个窗口，先最大化计算器再还原，然后把记事本窗口移动到屏幕右侧",
        "expected_steps": None,
    },
    {
        "name": "20. 剪贴板操作",
        "goal": "复制桌面上的 readme.txt 文件内容到剪贴板，然后打开记事本，把剪贴板内容粘贴进去，保存为 clipboard_output.txt",
        "expected_steps": None,
    },
]


# ── Main ──────────────────────────────────────────────────────────

def main():
    api_key = get_api_key()
    config = get_api_config()

    if not api_key:
        print("❌ No API key found in localStorage!")
        print("Please set the API key in the app's Settings panel first.")
        sys.exit(1)

    print(f"🔑 API Key found: {api_key[:8]}...{api_key[-4:]}")
    print(f"⚙️  Config: model={config.get('model', 'default')}, "
          f"multimodal={config.get('multimodal', 'false')}")

    # Check if specific goals were passed (capture all --goal values)
    if "--goal" in sys.argv:
        idxs = [i for i, a in enumerate(sys.argv) if a == "--goal"]
        test_cases = []
        for i in idxs:
            if i + 1 < len(sys.argv):
                test_cases.append({"name": f"Custom-{len(test_cases)+1}", "goal": sys.argv[i+1]})
    else:
        test_cases = TEST_CASES

    all_scores = []

    for tc in test_cases:
        print(f"\n\n{'#'*60}")
        print(f"# Running: {tc['name']}")
        print(f"{'#'*60}")
        print(f"Goal: {tc['goal'][:120]}")

        start_time = time.time()
        result = trigger_agent_generation(tc["goal"], api_key, config)
        elapsed = time.time() - start_time

        data = extract_generation_data(result)

        print(f"\n⏱️  Generation time: {elapsed:.1f}s")
        print(f"✅ Success: {data.get('success', False)}")
        print(f"📊 Steps: {len(data.get('structured_steps', []))}")

        report = validate_generation(data)
        score = print_report(tc["name"], data, report)
        all_scores.append((tc["name"], score, report["passed"], report["passed"] + report["failed"], report["warnings"], data.get("summary", "")))

    # Summary
    print(f"\n\n{'='*60}")
    print(f"📊 OVERALL SUMMARY")
    print(f"{'='*60}")
    print(f"{'Test':<40} {'Score':>8} {'Warn':>6}")
    print(f"{'-'*54}")
    for name, score, passed, total, warnings, summary in all_scores:
        status = "✅" if score == 100 else "⚠️" if score >= 70 else "❌"
        print(f"{status} {name:<37} {passed}/{total:>4} ({score:3.0f}%) {warnings:>3}w")

    avg = sum(s for _, s, _, _, _, _ in all_scores) / len(all_scores) if all_scores else 0
    print(f"\nAverage Score: {avg:.0f}%")


if __name__ == "__main__":
    main()
