#!/usr/bin/env python3
"""
Lobster CLI - 命令行自动化工具

用法:
  lobster run "CLICK 开始"
  lobster run-file task.lobster
  lobster health / status / pause / resume / stop
  lobster call click-target target="确认"
  lobster list [category]
  lobster info <feature>
  lobster test [category]
  lobster mcp                    # 启动 MCP 服务端
"""
import sys, os, json, time
from pathlib import Path

if sys.platform == "win32":
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

BACKEND_URL = os.getenv("LOBSTER_URL", "http://localhost:7788")
FRONTEND_URL = os.getenv("LINGDONG_URL", "http://127.0.0.1:5877")

def _url(): return os.environ.get("LOBSTER_URL", BACKEND_URL)


def _req(method, path, **kw):
    if not HAS_REQUESTS:
        print("需要安装 requests 库: pip install requests", file=sys.stderr)
        sys.exit(1)
    try:
        kwargs = {"timeout": kw.pop("timeout", 15)}
        kwargs.update(kw)
        r = getattr(requests, method)(f"{_url()}{path}", **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"无法连接后端 ({_url()})", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 命令实现
# ═══════════════════════════════════════════════════════════════

def cmd_run(args):
    dsl = args[0] if args else ""
    if dsl == "-" and not sys.stdin.isatty():
        dsl = sys.stdin.read()
    if not dsl.strip():
        print("错误: DSL 内容为空", file=sys.stderr); sys.exit(1)
    data = _req("post", "/api/dsl/run", json={"dsl": dsl})
    print(f"任务已提交 (ID: {data['task_id']})")

def cmd_run_file(args):
    path = Path(args[0])
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr); sys.exit(1)
    cmd_run([path.read_text("utf-8")])

def cmd_parse(args):
    dsl = args[0] if args else ""
    try:
        from engine.dsl_parser import DSLParser
        ast = DSLParser.from_string(dsl)
        print(json.dumps(ast.to_dict(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"解析失败: {e}", file=sys.stderr); sys.exit(1)

def cmd_health(args):
    data = _req("get", "/api/health")
    print(f"后端: {data.get('status', '?')}")
    print(f"执行器: {data.get('executor_state', '?')}")
    print(f"功能数: {data.get('features', 0)}")
    print(f"模式: {'真实' if not data.get('simulation_mode') else '模拟'}")

def cmd_status(args):
    data = _req("get", "/api/executor/status")
    print(f"状态: {data['state']}")
    print(f"队列: {data['queue'].get('queued', 0)} 个")
    for h in data['queue'].get('history', [])[-5:]:
        print(f"  [{h['status']}] {h['task_id']} ({h['elapsed']}s)")

def cmd_pause(args):   print(f"已暂停 (状态: {_req('post','/api/executor/pause')['state']})")
def cmd_resume(args):  print(f"已恢复 (状态: {_req('post','/api/executor/resume')['state']})")
def cmd_stop(args):    print(f"已停止 (状态: {_req('post','/api/executor/stop')['state']})")

def cmd_logs(args):
    count = int(args[0]) if args else 50
    data = _req("get", f"/api/logs?count={count}")
    for e in data.get("logs", []):
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        print(f"[{ts}] {e['msg']}")

def cmd_restart(args):
    import subprocess
    print("重启后端...")
    if sys.platform == "win32":
        subprocess.run("taskkill /f /fi \"WINDOWTITLE eq python*server*\" 2>nul", shell=True)
    else:
        subprocess.run("pkill -f 'python.*server.py' 2>/dev/null", shell=True)
    time.sleep(1)
    port = args[0] if args else os.getenv("LOBSTER_PORT", "7788")
    env = os.environ.copy()
    env["LOBSTER_PORT"] = str(port)
    backend_dir = Path(__file__).resolve().parent.parent / "python"
    log_path = backend_dir / "server.log"
    kwargs = {"stdout": open(log_path, "w", encoding="utf-8"), "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["python", "server.py"], cwd=str(backend_dir), env=env, **kwargs)
    print(f"后端启动中, 日志: {log_path}")

def cmd_run_sync(args):
    dsl = args[0] if args else ""
    if dsl == "-" and not sys.stdin.isatty():
        dsl = sys.stdin.read()
    if not dsl.strip():
        print("错误: DSL 内容为空", file=sys.stderr); sys.exit(1)
    print("执行中...")
    data = _req("post", "/api/dsl/run-sync", json={"dsl": dsl, "timeout": 30}, timeout=40)
    for e in data.get("logs", []):
        print(f"  {e['msg']}")
    print(f"\n状态: {data.get('state','?')}  耗时: {data.get('elapsed',0)}s")
    if not data.get("success"):
        print(f"错误: {data.get('error','未知')}", file=sys.stderr)
        sys.exit(1)

def cmd_call(args):
    """lobster call <feature> [key=value ...]"""
    if not args:
        print("用法: lobster call <功能名> [key=value ...]", file=sys.stderr)
        sys.exit(1)
    name = args[0]
    kwargs = {}
    for a in args[1:]:
        if "=" in a:
            k, v = a.split("=", 1)
            kwargs[k.strip()] = v.strip()
        elif a.startswith("--") and "=" in a[2:]:
            k, v = a[2:].split("=", 1)
            kwargs[k.strip()] = v.strip()
    try:
        resp = requests.post(f"{_url()}/api/feature/{name}", json=kwargs, timeout=30)
        data = resp.json()
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr); sys.exit(1)
    if data.get("success"):
        result = data.get("result", "")
        if isinstance(result, (dict, list)):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result)
        ms = data.get("elapsed_ms", 0)
        print(f"\n# 耗时: {ms}ms")
    else:
        print(f"错误: {data.get('error', '未知')}", file=sys.stderr)
        avail = data.get("available", [])
        if avail:
            print(f"可用功能: {', '.join(avail)}", file=sys.stderr)
        sys.exit(1)

def cmd_list(args):
    category = args[0] if args else ""
    params = f"?category={category}" if category else ""
    data = _req("get", f"/api/features{params}")
    if not data:
        print("(无功能)")
        return
    cats = {}
    for f in data:
        cats.setdefault(f["category"], []).append(f)
    for cat, feats in cats.items():
        print(f"\n[{cat.upper()}]")
        for f in feats:
            dsl = f"  DSL: {f['dsl_keyword']}" if f.get("dsl_keyword") else ""
            print(f"  {f['name']:25s} {f['display_name']}{dsl}")

def cmd_info(args):
    if not args:
        print("用法: lobster info <功能名>", file=sys.stderr); sys.exit(1)
    data = _req("get", f"/api/feature/{args[0]}") if False else None
    # 从注册表获取 (不需要后端)
    try:
        import features._all  # noqa
        from features.registry import registry
        spec = registry.get(args[0])
        if not spec:
            print(f"功能 '{args[0]}' 不存在", file=sys.stderr)
            sys.exit(1)
        d = spec.to_dict()
        print(f"名称: {d['name']} ({d['display_name']})")
        print(f"描述: {d['description']}")
        print(f"类别: {d['category']}")
        print(f"MCP工具: {d['mcp_tool_name']}")
        print(f"HTTP: {d['http_endpoint']}")
        if d.get("dsl_keyword"):
            print(f"DSL: {d['dsl_template']}")
        print(f"\n参数 ({len(d['params'])}):")
        for p in d["params"]:
            req = "必填" if p["required"] else f"可选, 默认={p['default']}"
            print(f"  --{p['name']} ({p['type']}) {req}")
            print(f"    {p['description']}")
        print(f"\n返回值: {d['returns']}")
        if d.get("examples"):
            print(f"\n示例:")
            for ex in d["examples"]:
                print(f"  {ex}")
        if d.get("test_cases"):
            print(f"\n测试用例: {len(d['test_cases'])} 个")
    except Exception as e:
        print(f"获取失败: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_test(args):
    category = args[0] if args else ""
    data = _req("post", "/api/feature/debug_run_tests",
                json={"category": category, "skip_ci": True})
    if not data.get("success"):
        print(f"测试失败: {data.get('error', '未知')}", file=sys.stderr)
        sys.exit(1)
    result = data.get("result", {})
    passed, failed, skipped = result.get("passed", 0), result.get("failed", 0), result.get("skipped", 0)
    for d in result.get("details", []):
        icon = {"passed": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(d["status"], "?")
        print(f"  [{icon}] [{d['feature']}] {d['test']}")
    print(f"\n结果: {passed} 通过, {failed} 失败, {skipped} 跳过")
    if failed:
        sys.exit(1)

def cmd_mcp(args):
    """启动 MCP 服务端"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
    from mcp.server import serve
    serve()

def cmd_region_select(args):
    """用户交互式框选区域 (无需后端, 直接显示 tkinter 窗口)

    用法: lobster region-select [message]
         lobster region-select "请框选进度条区域"
    输出: {"x": 100, "y": 200, "w": 300, "h": 50}
    """
    message = args[0] if args else "请拖拽选择监控区域"
    try:
        from interaction.region_picker import RegionPicker
    except ImportError:
        print("错误: 无法导入 RegionPicker", file=sys.stderr)
        sys.exit(1)
    region = RegionPicker.pick(message)
    if region:
        x, y, w, h = region
        result = {"x": x, "y": y, "w": w, "h": h}
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps({"cancelled": True}))


def cmd_match(args):
    """搜索前端模板（匹配前端 HTN 注册表 + 语义匹配）

    调用 Electron 前端 :5877 的模板搜索引擎，返回结构化候选列表。
    用法: lobster match <自然语言查询>

    示例:
      lobster match 打开记事本
      lobster match 截图保存
    """
    query = " ".join(args) if args else ""
    if not query:
        print("用法: lobster match <自然语言查询>", file=sys.stderr)
        sys.exit(1)
    if not HAS_REQUESTS:
        print("需要安装 requests 库: pip install requests", file=sys.stderr)
        sys.exit(1)
    try:
        r = requests.post(f"{FRONTEND_URL}/api/search-templates",
                          json={"query": query, "maxResults": 5}, timeout=10)
        data = r.json()
    except requests.exceptions.ConnectionError:
        print(f"无法连接前端 ({FRONTEND_URL})。请确保灵动脚本已启动。", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        sys.exit(1)

    if data.get("error"):
        print(f"错误: {data['error']}")
        sys.exit(1)

    auto = data.get("autoSelect")
    htn = data.get("htn", [])
    flat = data.get("flat", [])

    if auto and flat:
        best = flat[0]
        print(f"⟐ 自动选择: {best.get('name', '?')} (评分: {best.get('score', '?')})")
        print(f"   {best.get('description', '')}\n")

    if htn:
        print(f"📋 HTN 复合模板 ({len(htn)}):")
        for t in htn:
            print(f"  [{t['name']}]({t.get('score','?')}) {t.get('description','')}")
            if t.get('steps'):
                steps = ' → '.join(s.get('description','')[:40] for s in t['steps'][:4])
                print(f"    ├ 步骤: {steps}{'…' if len(t['steps'])>4 else ''}")
    else:
        print("📋 HTN 复合模板: (无)")

    if flat:
        print(f"\n🏷️  语义匹配 ({len(flat)}):")
        for t in flat:
            print(f"  [{t['name']}]({t.get('score','?')}) {t.get('description','')}")
    else:
        print("\n🏷️  语义匹配: (无)")

    if not htn and not flat and not auto:
        print("未匹配到任何模板。尝试用 AI 生成新流程。")
        sys.exit(1)


def cmd_delegate(args):
    """委托另一个 Claude Code 实例处理任务

    打开新终端窗口启动另一个 Claude Code，向其发送任务并执行。
    用法: lobster delegate <任务描述>

    示例:
      lobster delegate 分析当前项目结构
      lobster delegate --model opus 检查所有测试用例
    """
    model = ""
    rest = list(args)
    if rest and rest[0] == "--model" and len(rest) >= 2:
        model = rest[1]
        rest = rest[2:]
    prompt = " ".join(rest) if rest else ""
    if not prompt:
        print("用法: lobster delegate [--model <模型>] <任务描述>", file=sys.stderr)
        sys.exit(1)

    # Use the feature via direct call
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
    try:
        from features.claude_code_features import claude_code_delegate
        result = claude_code_delegate(prompt=prompt, model=model, wait=False)
        if result.get("success"):
            print(f"✓ {result['message']}")
        else:
            print(f"✗ {result.get('error', '未知错误')}", file=sys.stderr)
            sys.exit(1)
    except ImportError:
        # Fallback: use subprocess directly
        import subprocess, tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(prompt)
        tmp.close()
        claude = "npx.cmd claude" if sys.platform == "win32" else "npx claude"
        shell_cmd = f'type "{tmp.name}" | {claude}'
        if model:
            shell_cmd += f" --model {model}"
        subprocess.Popen(
            ["cmd", "/k", shell_cmd] if sys.platform == "win32" else ["sh", "-c", shell_cmd],
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
        print(f"✓ 已启动 Claude Code 新实例")


def cmd_template(args):
    """社区模板市场 — 搜索/安装/管理模板

    用法:
      lobster template search <query>   搜索远程模板
      lobster template install <id>     安装模板到本地
      lobster template list             列出已安装模板
      lobster template publish <path>   准备发布模板
      lobster template refresh          刷新模板索引缓存

    模板源: https://github.com/{owner}/lobster-templates
    可通过 LOBSTER_TEMPLATE_SOURCE 环境变量指定自定义源。
    """
    from tools.template_market import search, install, list_installed, publish, fetch_index
    if not args or args[0] in ("-h", "--help"):
        print(cmd_template.__doc__ or __doc__)
        return
    sub = args[0]
    sub_args = args[1:]
    if sub == "search":
        q = " ".join(sub_args)
        if not q:
            print("用法: lobster template search <query>", file=sys.stderr); sys.exit(1)
        results = search(q)
        if not results:
            print("未找到匹配的模板")
            return
        print(f"📋 找到 {len(results)} 个模板:\n")
        for r in results:
            tags = ", ".join(r.get("tags", []))
            print(f"  [{r['id']}]")
            print(f"    名称: {r.get('name', '?')}")
            print(f"    说明: {r.get('description', '')}")
            if tags:
                print(f"    标签: {tags}")
            print()
    elif sub == "install":
        if not sub_args:
            print("用法: lobster template install <id>", file=sys.stderr); sys.exit(1)
        install(sub_args[0])
    elif sub == "list":
        installed = list_installed()
        if not installed:
            print("本地未安装任何模板")
            return
        print(f"📦 本地模板 ({len(installed)}):\n")
        for t in installed:
            size_kb = t["size"] / 1024
            print(f"  [{t['id']}] {t['name']}  ({size_kb:.1f} KB)")
            if t["description"]:
                print(f"    {t['description']}")
    elif sub == "publish":
        if not sub_args:
            print("用法: lobster template publish <path> [--name <name>]", file=sys.stderr); sys.exit(1)
        path = sub_args[0]
        name = None
        if "--name" in sub_args:
            idx = sub_args.index("--name")
            if idx + 1 < len(sub_args):
                name = sub_args[idx + 1]
        publish(path, name)
    elif sub == "refresh":
        fetch_index(force=True)
        print("✅ 模板索引已刷新")
    else:
        print(f"未知模板命令: {sub}", file=sys.stderr); sys.exit(1)


def cmd_exec(args):
    """直接执行 DSL (无需后端, 直接调用执行引擎)

    用法: lobster exec <dsl 代码>
         lobster exec "CLICK 开始"
         lobster exec script.lobster    # 自动检测文件
         echo "CLICK 开始" | lobster exec -

    这是新 Claude Code 最常用的命令:
    不需要 MCP 工具, 不需要后端服务器, 一行命令直接运行
    """
    dsl = args[0] if args else ""
    if dsl == "-" and not sys.stdin.isatty():
        dsl = sys.stdin.read()
    # 自动检测文件路径
    if dsl and not dsl.startswith(("CLICK", "WAIT", "LOOP", "IF", "SCREENSHOT",
                                    "TYPE", "LAUNCH", "FOCUS", "HOTKEY", "SET",
                                    "OCR_FIND", "OCR_EXTRACT", "REGION_SELECT",
                                    "SUBROUTINE", "CALL", "WHEN", "PARALLEL")):
        p = Path(dsl)
        if p.exists():
            dsl = p.read_text("utf-8")
    if not dsl.strip():
        print("用法: lobster exec \"CLICK 目标\"", file=sys.stderr)
        sys.exit(1)

    # 主路径: Robot Framework
    try:
        from engine.robot_runner import run_robot
        result = run_robot(dsl, task_name="CLI Automation")
        if result.get("success"):
            print(f"\n✓ 执行成功 (RF): {result.get('passed', 0)} passed, {result.get('failed', 0)} failed")
            if result.get("logs"):
                for log in result["logs"][-10:]:
                    print(f"  {log}")
            sys.exit(0)
    except Exception:
        pass  # fallback to DSLExecutor

    # 回退: 旧 DSLExecutor
    try:
        from engine.executor import DSLExecutor
        from interaction.actions import ActionHandler
        exe = DSLExecutor(action_handler=ActionHandler())
        # 把日志输出到控制台
        exe.on("log", lambda **kw: print(f"  {kw.get('message','')}", flush=True))
        exe.on("node_start", lambda **kw: print(f"  ▶ {kw['node_type']}: {str(kw.get('args',''))[:60]}", flush=True))
        result = exe.run_dsl_sync(dsl)
        success = result.get("success", True)
        state = result.get("state", "?")
        elapsed = result.get("elapsed", 0)
        if not success:
            print(f"\n✗ 执行失败: {result.get('error','未知错误')}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"\n✓ 执行完成: state={state}")
    except Exception as e:
        print(f"执行失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

COMMANDS = {
    "run": cmd_run, "run-file": cmd_run_file, "run-sync": cmd_run_sync,
    "exec": cmd_exec,
    "parse": cmd_parse, "health": cmd_health, "status": cmd_status,
    "match": cmd_match,
    "delegate": cmd_delegate,
    "pause": cmd_pause, "resume": cmd_resume, "stop": cmd_stop,
    "logs": cmd_logs, "restart": cmd_restart,
    "call": cmd_call, "list": cmd_list, "info": cmd_info, "test": cmd_test,
    "mcp": cmd_mcp,
    "region-select": cmd_region_select,
    "template": cmd_template,
}

HELP = """Lobster CLI v2 - 低Token AI自动化执行系统

用法: lobster <command> [参数...]

后端 (需运行 lobster restart 启动):
  run <dsl>                 执行 DSL
  run-file <file>           从文件执行 DSL
  run-sync <dsl>            同步执行 DSL (等待完成)
  restart [port]            重启后端

直接 (无需后端):
  exec <dsl>                直接执行 DSL
  parse <dsl>               解析 DSL 为 AST
  region-select [message]   用户框选区域 → JSON

状态:
  health                    检查后端状态
  status                    查看执行器状态
  pause / resume / stop     控制执行器
  logs [n]                  查看执行日志

模板:
  match <query>             搜索前端模板（HTN + 语义匹配）
  template search <query>   搜索社区模板市场（GitHub 源）
  template install <id>     从社区市场安装模板
  template list             列出本地已安装模板
  template publish <path>   准备发布模板到社区
  template refresh          刷新模板索引缓存

高级:
  delegate <prompt>         打开新终端启动另一个 Claude Code 实例
  delegate --model opus <p> 指定模型启动委托实例

功能:
  call <name> [key=val...]  调用注册功能
  list [category]           列出功能
  info <name>               功能详情
  test [category]           运行测试

集成:
  mcp                       启动 MCP 服务端

新 Claude Code 工作流:
  1. lobster region-select "框选监控区域"  → 获得 JSON 坐标
  2. lobster exec "LOOP ... IF PROGRESS {x},{y},{w},{h} ..."  → 执行监控

示例:
  lobster run "CLICK 开始"
  lobster exec "CLICK 确认"
  lobster region-select "请框选进度条区域"
"""

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(HELP)
        return
    cmd = sys.argv[1]
    if cmd in COMMANDS:
        COMMANDS[cmd](sys.argv[2:])
    elif cmd == "feature":
        # lobster feature <name> [key=val...]
        cmd_call(sys.argv[2:])
    else:
        print(f"未知命令: {cmd}\n{HELP}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
