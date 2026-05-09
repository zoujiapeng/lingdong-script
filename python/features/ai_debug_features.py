"""
AI 规划 + 调试功能注册
DSL生成 / 流程图转换 / 自主调试 / 系统诊断
"""
import json
import os
import sys
import time
from typing import Optional

from features.registry import feature, registry, P, TC, FeatureCategory as F
from features.state import get_logs


# ═══════════════════════════════════════════════════════════════
# AI 生成 DSL
# ═══════════════════════════════════════════════════════════════
@feature(
    name="ai_generate_dsl",
    display_name="AI生成DSL",
    description="【AI 规划】将自然语言任务描述转换为 Lobster DSL。用于自然语言到自动化流程的转换，生成的 DSL 可由 run_dsl 执行",
    category=F.AI,
    params=[
        P("task", "str", "自然语言任务描述", example="打开微信给张三发消息"),
        P("api_key", "str", "Anthropic API Key（可选，默认使用环境变量）",
          required=False, default=""),
        P("context", "str", "额外上下文（当前场景描述）", required=False, default=""),
    ],
    returns="dict{dsl, ast, input_tokens, output_tokens}",
    test_cases=[
        TC("basic_gen", {"task": "点击开始按钮", "api_key": ""},
           "success", validator=lambda r: r.get("success") is True),
    ],
)
def ai_generate_dsl(task: str, api_key: str = "", context: str = "") -> dict:
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    feature_ctx = _build_feature_context()

    system_prompt = f"""你是 Lobster 自动化系统的 DSL 规划引擎。
用一次响应生成完整的 DSL 执行计划。

可用 DSL 指令：
{feature_ctx}

规则：
- 只输出 DSL，不加任何解释或 markdown 代码块
- # 开头为注释
- 参数直接跟在指令后，空格分隔
- 优先使用文字匹配而非坐标

{('当前场景: ' + context) if context else ''}"""

    if not key:
        return {
            "dsl": f"# 模拟生成（无API Key）\n# 任务: {task}\nCLICK 开始\nWAIT 完成",
            "ast": None, "input_tokens": 0, "output_tokens": 0, "simulated": True,
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": task}],
        )
        dsl = msg.content[0].text.strip()
        from engine.dsl_parser import DSLParser
        try:
            ast = DSLParser.from_string(dsl).to_dict()
        except Exception:
            ast = None
        return {
            "dsl": dsl, "ast": ast,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "simulated": False,
        }
    except Exception as e:
        return {"dsl": "", "error": str(e), "simulated": False}


# ═══════════════════════════════════════════════════════════════
# DSL ↔ 流程图双向转换
# ═══════════════════════════════════════════════════════════════
@feature(
    name="dsl_to_graph",
    display_name="DSL转流程图（已废弃）",
    description="【已废弃】请使用 dsl_to_flow 替代。将 DSL 文本转换为前端 Canvas FlowScript JSON 格式",
    category=F.AI,
    params=[
        P("dsl", "str", "DSL文本", example="CLICK 开始\nWAIT 加载"),
        P("layout", "str", "布局: vertical|horizontal|auto", required=False, default="vertical"),
    ],
    returns="dict{meta, nodes, edges, variables, settings}",
    deprecated=True,
    test_cases=[
        TC("simple_dsl", {"dsl": "CLICK 开始\nWAIT 完成"}, "non_null",
           validator=lambda r: r["success"] and len(r["result"]["nodes"]) >= 2),
    ],
)
def dsl_to_graph(dsl: str, layout: str = "vertical") -> dict:
    """新版：使用 DSL→FlowScript 双向转换器，输出前端 FlowCanvas 兼容格式。"""
    from engine.dsl_flow_converter import dsl_to_flow
    return dsl_to_flow(dsl, layout)


@feature(
    name="graph_to_dsl",
    display_name="流程图转DSL",
    description="【可视化·反向】将前端 Canvas FlowScript JSON（nodes+edges）转换回 DSL 文本。编辑流程图后调用此工具获得可执行的 DSL",
    category=F.AI,
    params=[
        P("script", "string", "FlowScript JSON 字符串（含 nodes/edges）"),
    ],
    returns="str - DSL文本",
    test_cases=[
        TC("simple",
           {"script": '{"nodes":[{"id":"n1","type":"click","params":{"x":100,"y":200}},{"id":"n2","type":"wait","params":{"duration":2}}],"edges":[{"id":"e1","from_node":"n1","from_port":"output","to_node":"n2","to_port":"input"}],"meta":{"name":"test"},"variables":{},"settings":{}}'},
           "non_null", validator=lambda r: "CLICK" in str(r.get("result", ""))),
    ],
)
def graph_to_dsl(script: str) -> str:
    """新版：使用 DSL→FlowScript 双向转换器，从前端 Canvas FlowScript 格式生成 DSL。"""
    from engine.dsl_flow_converter import flow_to_dsl
    try:
        script_data = json.loads(script) if isinstance(script, str) else script
    except Exception as e:
        return f"# 解析错误: {e}"
    return flow_to_dsl(script_data)


# ═══════════════════════════════════════════════════════════════
# DSL↔FlowScript 转换工具（供 MCP / AI 直接调用）
# ═══════════════════════════════════════════════════════════════
@feature(
    name="dsl_to_flow",
    display_name="DSL转FlowScript",
    description="【双向转换·推荐】将 DSL 文本转换为前端 Canvas FlowScript JSON 格式（含 nodes/edges/布局）。替代已废弃的 dsl_to_graph。AI 生成 DSL 后调用此工具可自动获得流程图",
    category=F.AI,
    params=[
        P("dsl", "str", "DSL文本", example="CLICK 开始\nWAIT 加载"),
        P("layout", "str", "布局: vertical|horizontal", required=False, default="vertical"),
    ],
    returns="dict{meta, nodes, edges, variables, settings}",
)
def dsl_to_flow_feature(dsl: str, layout: str = "vertical") -> dict:
    from engine.dsl_flow_converter import dsl_to_flow
    return dsl_to_flow(dsl, layout)


@feature(
    name="flow_to_dsl",
    display_name="FlowScript转DSL",
    description="【双向转换】将前端 Canvas FlowScript JSON 转换回 DSL 文本。编辑流程图后调用此工具可得到可执行的 DSL",
    category=F.AI,
    params=[
        P("script", "string", "FlowScript JSON 字符串"),
    ],
    returns="str - DSL文本",
)
def flow_to_dsl_feature(script: str) -> str:
    from engine.dsl_flow_converter import flow_to_dsl
    try:
        data = json.loads(script) if isinstance(script, str) else script
    except Exception as e:
        return f"# 解析错误: {e}"
    return flow_to_dsl(data)


@feature(
    name="create_flow_from_dsl",
    display_name="从DSL创建流程",
    description="【AI→流程】将 DSL 文本转换为完整的流程图工作流并保存到文件。AI 生成 DSL 后调用此工具可直接创建可执行的流程文件",
    category=F.AI,
    params=[
        P("dsl", "str", "DSL文本", example="CLICK 开始\nWAIT 加载"),
        P("name", "str", "流程名称", required=False, default="未命名流程"),
    ],
    returns="dict{saved_to, flow}",
)
def create_flow_from_dsl(dsl: str, name: str = "未命名流程") -> dict:
    from engine.dsl_flow_converter import dsl_to_flow
    flow = dsl_to_flow(dsl)
    flow["meta"]["name"] = name
    flow["meta"]["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    flow["meta"]["updated_at"] = flow["meta"]["created_at"]
    filename = f"{name}.flow"
    with open(filename, "w", encoding="utf-8") as f:
        import json as j
        j.dump(flow, f, ensure_ascii=False, indent=2)
    return {"success": True, "saved_to": filename, "flow": flow}


# ═══════════════════════════════════════════════════════════════
# 调试功能
# ═══════════════════════════════════════════════════════════════
@feature(
    name="debug_get_logs",
    display_name="获取执行日志",
    description="【调试·日志】返回执行器最近的日志。用于诊断 DSL 执行失败原因、查看执行历史",
    category=F.DEBUG,
    params=[
        P("count", "number", "日志条数", required=False, default=50),
    ],
    returns="list[dict{ts, msg}]",
)
def debug_get_logs(count: int = 50):
    return get_logs(count)


@feature(
    name="debug_run_tests",
    display_name="运行测试",
    description="【调试·测试】运行所有注册功能的测试用例。用于系统升级后验证完整性和回归测试",
    category=F.DEBUG,
    params=[
        P("category", "str", "只测试指定类别（可选）", required=False, default=""),
        P("skip_ci", "boolean", "跳过需要真实屏幕的测试", required=False, default=True),
    ],
    returns="dict{passed, failed, skipped, details}",
    test_cases=[
        TC("self_test", {"category": "debug", "skip_ci": True}, "success",
           validator=lambda r: r["success"] and r["result"]["passed"] >= 0),
    ],
)
def debug_run_tests(category: str = "", skip_ci: bool = True) -> dict:
    cat = None
    if category:
        try:
            cat = F(category)
        except ValueError:
            pass
    return registry.run_tests(category=cat, skip_ci=skip_ci)


@feature(
    name="debug_list_features",
    display_name="列出功能",
    description="【调试·发现】返回所有注册功能的完整信息。format=markdown 返回 Claude 可读格式，format=mcp 返回 MCP schema。Claude Code 发现 Lobster 能力的入口",
    category=F.DEBUG,
    params=[
        P("category", "str", "过滤类别（可选）", required=False, default=""),
        P("format", "str", "输出格式: json|markdown|mcp", required=False, default="json"),
    ],
    returns="str or list - 功能列表",
    test_cases=[
        TC("list_all", {}, "non_null"),
        TC("list_json", {"format": "json"}, "non_null"),
        TC("list_md", {"format": "markdown"}, "non_null"),
    ],
)
def debug_list_features(category: str = "", format: str = "json"):
    cat = None
    if category:
        try:
            cat = F(category)
        except ValueError:
            pass
    features = registry.by_category(cat) if cat else registry.all()
    if format == "markdown":
        return registry.export_claude_context()
    elif format == "mcp":
        return registry.export_mcp_tools()
    return [f.to_dict() for f in features]


@feature(
    name="debug_validate_dsl",
    display_name="验证DSL语法",
    description="【调试·DSL验证】解析并验证 DSL 文本语法，返回 AST 和错误信息。推荐执行 DSL 前先验证",
    category=F.DEBUG,
    params=[
        P("dsl", "str", "DSL文本", example="CLICK 开始\nWAIT 完成"),
    ],
    returns="dict{valid, ast, error}",
    test_cases=[
        TC("valid", {"dsl": "CLICK 开始\nWAIT 完成"}, "non_null",
           validator=lambda r: r["success"] and r["result"]["valid"]),
        TC("invalid", {"dsl": "INVALID_CMD arg"}, "success",
           validator=lambda r: r["success"] and not r["result"]["valid"]),
    ],
)
def debug_validate_dsl(dsl: str) -> dict:
    from engine.dsl_parser import DSLParser
    try:
        ast = DSLParser.from_string(dsl)
        return {"valid": True, "ast": ast.to_dict(), "error": None}
    except Exception as e:
        return {"valid": False, "ast": None, "error": str(e)}


@feature(
    name="debug_system_info",
    display_name="系统诊断",
    description="【调试·诊断】返回系统状态、依赖检查、功能统计。用于排查环境问题时快速诊断",
    category=F.DEBUG,
    params=[],
    returns="dict - 系统诊断报告",
    test_cases=[TC("info", {}, "non_null")],
)
def debug_system_info() -> dict:
    deps = {}
    for pkg in ["pyautogui", "cv2", "pytesseract", "mss", "anthropic", "flask", "numpy"]:
        try:
            __import__(pkg)
            deps[pkg] = True
        except (ImportError, NameError):
            deps[pkg] = False

    all_features = registry.all()
    cat_counts = {}
    for f in all_features:
        k = f.category.value
        cat_counts[k] = cat_counts.get(k, 0) + 1

    return {
        "python_version": sys.version,
        "platform": sys.platform,
        "total_features": len(all_features),
        "features_by_category": cat_counts,
        "dependencies": deps,
        "mcp_tools_count": len(registry.export_mcp_tools()),
        "test_cases_count": sum(len(f.test_cases) for f in all_features),
    }


@feature(
    name="debug_generate_test_flow",
    display_name="生成测试流程图",
    description="【调试·自测】AI 自主生成测试场景的 DSL 和流程图，用于验证系统功能。使用场景：新增功能后自动生成测试流程、回归测试。这是系统自调试能力的体现",
    category=F.DEBUG,
    params=[
        P("scenario", "str", "测试场景描述", example="测试点击和等待功能"),
        P("api_key", "str", "API Key（可选）", required=False, default=""),
    ],
    returns="dict{dsl, graph, description}",
    test_cases=[
        TC("gen_test_flow", {"scenario": "基础点击测试"}, "success"),
    ],
)
def debug_generate_test_flow(scenario: str, api_key: str = "") -> dict:
    dsl_result = ai_generate_dsl(
        task=f"[测试场景] {scenario}\n要求：生成可验证功能的完整测试流程",
        api_key=api_key,
    )
    dsl = dsl_result.get("dsl", "CLICK 测试按钮\nWAIT 测试完成")
    graph = dsl_to_graph(dsl)
    return {
        "dsl": dsl,
        "graph": graph,
        "description": f"测试场景: {scenario}",
        "tokens_used": dsl_result.get("input_tokens", 0) + dsl_result.get("output_tokens", 0),
    }


# ── 模板管理 ──────────────────────────────────────────────────────

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'templates')


def _parse_template_metadata(filepath: str) -> dict:
    """Parse YAML-style metadata block from the top of a .dsl file.

    Expected format:
        # ---
        # name: 登录流程
        # aliases: ["登录", "login"]
        # tags: ["app", "login"]
        # keywords: ["账号", "密码"]
        # category: app
        # ---
    """
    import json, re
    meta = {"name": "", "description": "", "aliases": [], "tags": [], "keywords": [], "category": ""}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (FileNotFoundError, IOError):
        return meta

    # Find start/end markers
    start_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == '# ---':
            start_idx = i
            break
    if start_idx < 0:
        # Fallback: first comment line as description
        for line in lines:
            if line.startswith('# ') and not line.startswith('# ---'):
                meta["description"] = line.strip('# \n')
                break
        meta["name"] = os.path.splitext(os.path.basename(filepath))[0]
        return meta

    end_idx = -1
    for i in range(start_idx + 1, len(lines)):
        if lines[i].strip() == '# ---':
            end_idx = i
            break

    # Parse key: value lines
    for i in range(start_idx + 1, end_idx if end_idx > 0 else len(lines)):
        line = lines[i].lstrip('# ').strip()
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip()
            if key in ('aliases', 'tags', 'keywords'):
                try:
                    meta[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    meta[key] = [v.strip().strip('"\'') for v in val.strip('[]').split(',') if v.strip()]
            elif key == 'name':
                meta['name'] = val
            elif key == 'category':
                meta['category'] = val
            else:
                meta[key] = val

    if not meta.get("description"):
        for line in lines:
            if line.startswith('# ') and ':' not in line and not line.startswith('# ---'):
                meta["description"] = line.strip('# \n')
                break
    if not meta.get("name"):
        meta["name"] = os.path.splitext(os.path.basename(filepath))[0]
    return meta


@feature(
    name="list_templates",
    display_name="列出工作流模板",
    description="列出 templates/ 目录下的所有 DSL 工作流模板文件，返回模板名称、描述、标签、别名等元数据",
    category=F.DEBUG,
    params=[],
    returns="list[dict{name, description, aliases, tags, keywords, category}]",
)
def list_templates() -> list:
    """列出所有可用的工作流模板（含元数据）。"""
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    templates = []
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        if f.endswith('.dsl'):
            path = os.path.join(TEMPLATES_DIR, f)
            meta = _parse_template_metadata(path)
            templates.append({
                "name": meta.get("name", f.replace('.dsl', '')),
                "filename": f,
                "description": meta.get("description", ""),
                "aliases": meta.get("aliases", []),
                "tags": meta.get("tags", []),
                "keywords": meta.get("keywords", []),
                "category": meta.get("category", ""),
            })
    return templates


@feature(
    name="load_template",
    display_name="加载工作流模板",
    description="加载 templates/ 目录下的 DSL 模板文件内容，返回 DSL 文本、名称和元数据",
    category=F.DEBUG,
    params=[
        P("name", "str", "模板名称（不含 .dsl 后缀）", example="screenshot-workflow"),
    ],
    returns="dict{name, dsl, exists, aliases, tags, keywords, category}",
)
def load_template(name: str) -> dict:
    """加载指定名称的 DSL 模板文件内容。"""
    path = os.path.join(TEMPLATES_DIR, f"{name}.dsl")
    if not os.path.isfile(path):
        return {"name": name, "dsl": "", "exists": False, "error": f"模板 '{name}' 未找到"}
    with open(path, 'r', encoding='utf-8') as f:
        dsl = f.read()
    meta = _parse_template_metadata(path)
    return {
        "name": meta.get("name", name),
        "dsl": dsl,
        "exists": True,
        "description": meta.get("description", ""),
        "aliases": meta.get("aliases", []),
        "tags": meta.get("tags", []),
        "keywords": meta.get("keywords", []),
        "category": meta.get("category", ""),
    }


@feature(
    name="search_templates",
    display_name="搜索模板",
    description="搜索 templates/ 目录下的 DSL 工作流模板，按关键词返回匹配的模板列表",
    category=F.DEBUG,
    params=[
        P("query", "str", "搜索关键词"),
        P("limit", "int", "返回结果数量上限", required=False, default=5),
    ],
    returns="list[dict{name, description, aliases, tags, keywords, category, match_score}]",
)
def search_templates(query: str, limit: int = 5) -> list:
    """搜索模板，按名称、描述、别名、标签、关键词匹配度排序。"""
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    results = []
    q = query.lower().strip()
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        if not f.endswith('.dsl'):
            continue
        path = os.path.join(TEMPLATES_DIR, f)
        meta = _parse_template_metadata(path)
        name = meta.get("name", "")
        desc = meta.get("description", "")
        aliases = meta.get("aliases", [])
        tags = meta.get("tags", [])
        keywords = meta.get("keywords", [])

        score = 0
        if name.lower() == q: score = 100
        elif q in name.lower(): score = 70
        for a in aliases:
            if a.lower() == q: score = max(score, 95)
            elif q in a.lower(): score = max(score, 65)
        for k in keywords:
            if q in k.lower() or k.lower() in q: score = max(score, 60)
        for t_ in tags:
            if q in t_.lower(): score = max(score, 50)
        if q in desc.lower(): score = max(score, 40)

        if score > 0:
            results.append({
                "name": name,
                "filename": f,
                "description": desc,
                "aliases": aliases,
                "tags": tags,
                "keywords": keywords,
                "category": meta.get("category", ""),
                "match_score": score,
            })
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results[:limit]


@feature(
    name="search_flow_templates",
    display_name="搜索前端模板",
    description="【模板·前端】通过 Electron HTTP API 搜索前端模板注册表（含 HTN 复合模板 + 语义匹配），返回结构化候选列表。比 filesystem search_templates 更丰富（含自动注册的动态模板和语义评分）",
    category=F.AI,
    params=[
        P("query", "str", "搜索关键词（自然语言）", example="打开记事本输入文字"),
        P("max_results", "int", "最多返回结果数", required=False, default=5),
    ],
    returns="dict{htn, flat, autoSelect}",
)
def search_flow_templates(query: str, max_results: int = 5) -> dict:
    """搜索前端模板注册表，返回 HTN + 语义匹配结果。"""
    import urllib.request, urllib.error
    payload = json.dumps({"query": query, "maxResults": max_results}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:5877/api/search-templates",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.URLError as e:
        return {"error": f"无法连接前端窗口 (是否已启动?): {e.reason}", "htn": [], "flat": [], "autoSelect": None}
    except Exception as e:
        return {"error": str(e), "htn": [], "flat": [], "autoSelect": None}


# ── 内部工具 ──────────────────────────────────────────────────────
def _build_feature_context() -> str:
    lines = []
    for f in registry.all():
        if f.dsl_keyword:
            lines.append(f"{f.dsl_template} - {f.description}")
    lines += [
        "LOOP <标签> ... END - 循环执行子块",
        "IF <条件> ... [ELSE ...] END - 条件判断",
        "RUN <宏名称> - 执行内置宏",
        "WAIT <条件> - 等待: 文字:X / 图像:X / 稳定 / 变化 / 消失:X",
    ]
    return "\n".join(lines)
