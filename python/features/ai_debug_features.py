"""
AI 规划 + 调试功能注册
DSL生成 / 流程图转换 / 自主调试 / 系统诊断
"""
import json
import os
import sys
import time
from pathlib import Path
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
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    api_base = (os.getenv("LLM_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL") or "deepseek-chat"
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
        # 使用 OpenAI 兼容接口（兼容 MIMO / DeepSeek / GPT / Claude API 等）
        import requests as _req
        resp = _req.post(
            f"{api_base}/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        dsl = raw.strip()
        # 移除可能的 markdown 代码块包裹
        if dsl.startswith("```"):
            dsl = dsl.split("\n", 1)[1] if "\n" in dsl else dsl
            dsl = dsl.rsplit("```", 1)[0].strip()
        from engine.dsl_parser import DSLParser
        try:
            ast = DSLParser.from_string(dsl).to_dict()
        except Exception:
            ast = None
        usage = data.get("usage", {})
        return {
            "dsl": dsl, "ast": ast,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
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
        P("script", "string", "FlowScript JSON 字符串（含 nodes/edges）", example='{"nodes":[],"edges":[]}'),
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
        P("script", "string", "FlowScript JSON 字符串", example='{"nodes":[],"edges":[]}'),
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


def _parse_flow_metadata(filepath: str) -> dict:
    """Extract metadata from a .flow JSON file (from script.meta)."""
    meta = {"name": "", "description": "", "aliases": [], "tags": [], "keywords": [], "category": ""}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        script_meta = data.get("meta", {}) if isinstance(data, dict) else {}
        meta["name"] = script_meta.get("name", script_meta.get("title", os.path.splitext(os.path.basename(filepath))[0]))
        meta["description"] = script_meta.get("description", "")
        meta["aliases"] = script_meta.get("aliases", [])
        meta["tags"] = script_meta.get("tags", [])
        meta["keywords"] = script_meta.get("keywords", [])
        meta["category"] = script_meta.get("category", "")
        # Count node types for preview
        nodes = data.get("nodes", []) if isinstance(data, dict) else []
        meta["node_count"] = len(nodes)
        type_counts: dict[str, int] = {}
        for n in nodes:
            t = n.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        meta["node_types"] = type_counts
    except (FileNotFoundError, IOError, json.JSONDecodeError):
        pass
    return meta


def _collect_templates():
    """Scan templates/ for both .dsl and .flow files, return unified metadata list."""
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    templates = []
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        path = os.path.join(TEMPLATES_DIR, f)
        if f.endswith('.dsl'):
            meta = _parse_template_metadata(path)
            templates.append({
                "name": meta.get("name", f.replace('.dsl', '')),
                "filename": f,
                "filetype": "dsl",
                "description": meta.get("description", ""),
                "aliases": meta.get("aliases", []),
                "tags": meta.get("tags", []),
                "keywords": meta.get("keywords", []),
                "category": meta.get("category", ""),
                "node_count": 0,
                "node_types": {},
            })
        elif f.endswith('.flow'):
            meta = _parse_flow_metadata(path)
            templates.append({
                "name": meta.get("name", f.replace('.flow', '')),
                "filename": f,
                "filetype": "flow",
                "description": meta.get("description", ""),
                "aliases": meta.get("aliases", []),
                "tags": meta.get("tags", []),
                "keywords": meta.get("keywords", []),
                "category": meta.get("category", ""),
                "node_count": meta.get("node_count", 0),
                "node_types": meta.get("node_types", {}),
            })
    return templates


@feature(
    name="list_templates",
    display_name="列出工作流模板",
    description="列出 templates/ 目录下的所有 DSL 工作流模板文件，返回模板名称、描述、标签、别名等元数据",
    category=F.DEBUG,
    params=[],
    returns="list[dict{name, description, aliases, tags, keywords, category}]",
)
def list_templates() -> list:
    """列出所有可用的工作流模板（含元数据）。同时扫描 .dsl 和 .flow 文件。"""
    return _collect_templates()


@feature(
    name="load_template",
    display_name="加载工作流模板",
    description="加载 templates/ 目录下的模板文件内容（支持 .dsl 和 .flow），返回 DSL 文本或 FlowScript JSON、名称和元数据",
    category=F.DEBUG,
    params=[
        P("name", "str", "模板名称（不含后缀）", example="screenshot-workflow"),
    ],
    returns="dict{name, dsl_or_json, filetype, exists, description, category, tags, node_count}",
)
def load_template(name: str) -> dict:
    """加载指定名称的模板文件。优先 .dsl，其次 .flow。"""
    dsl_path = os.path.join(TEMPLATES_DIR, f"{name}.dsl")
    flow_path = os.path.join(TEMPLATES_DIR, f"{name}.flow")

    # Try .dsl first
    if os.path.isfile(dsl_path):
        with open(dsl_path, 'r', encoding='utf-8') as f:
            content = f.read()
        meta = _parse_template_metadata(dsl_path)
        return {
            "name": meta.get("name", name),
            "dsl_or_json": content,
            "filetype": "dsl",
            "exists": True,
            "description": meta.get("description", ""),
            "aliases": meta.get("aliases", []),
            "tags": meta.get("tags", []),
            "keywords": meta.get("keywords", []),
            "category": meta.get("category", ""),
            "node_count": 0,
        }

    # Try .flow as fallback
    if os.path.isfile(flow_path):
        with open(flow_path, 'r', encoding='utf-8') as f:
            content = f.read()
        meta = _parse_flow_metadata(flow_path)
        return {
            "name": meta.get("name", name),
            "dsl_or_json": content,
            "filetype": "flow",
            "exists": True,
            "description": meta.get("description", ""),
            "aliases": meta.get("aliases", []),
            "tags": meta.get("tags", []),
            "keywords": meta.get("keywords", []),
            "category": meta.get("category", ""),
            "node_count": meta.get("node_count", 0),
        }

    return {"name": name, "dsl_or_json": "", "filetype": "", "exists": False, "error": f"模板 '{name}' 未找到"}


@feature(
    name="search_templates",
    display_name="搜索模板",
    description="搜索 templates/ 目录下的 DSL 工作流模板，按关键词返回匹配的模板列表",
    category=F.DEBUG,
    params=[
        P("query", "str", "搜索关键词", example="打卡"),
        P("limit", "int", "返回结果数量上限", required=False, default=5),
    ],
    returns="list[dict{name, description, aliases, tags, keywords, category, match_score}]",
)
def search_templates(query: str, limit: int = 5) -> list:
    """搜索模板，按名称、描述、别名、标签、关键词匹配度排序。同时搜索 .dsl 和 .flow 文件。"""
    templates = _collect_templates()
    if not templates:
        return []
    results = []
    q = query.lower().strip()
    for t in templates:
        name = t.get("name", "")
        desc = t.get("description", "")
        aliases = t.get("aliases", [])
        tags = t.get("tags", [])
        keywords = t.get("keywords", [])

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
                "filename": t.get("filename", ""),
                "filetype": t.get("filetype", ""),
                "description": desc,
                "aliases": aliases,
                "tags": tags,
                "keywords": keywords,
                "category": t.get("category", ""),
                "node_count": t.get("node_count", 0),
                "match_score": score,
            })
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results[:limit]


@feature(
    name="export_template_to_file",
    display_name="导出模板到文件",
    description="将当前工作流模板导出为 .flow 文件（含元数据），保存到指定路径。支持分类、标签等元数据编辑。",
    category=F.DEBUG,
    params=[
        P("filepath", "str", "导出目标路径（含 .flow 后缀）", example="C:/Users/me/Desktop/my-flow.flow"),
        P("name", "str", "模板名称", example="我的自动流程"),
        P("description", "str", "模板描述", required=False, default=""),
        P("category", "str", "模板分类: app|system|image|web|custom", required=False, default="custom"),
        P("tags", "str", "标签（英文逗号分隔）", required=False, default=""),
        P("aliases", "str", "别名（英文逗号分隔）", required=False, default=""),
        P("flow_json", "str", "可选的完整 FlowScript JSON 字符串。为空时提示前端提供当前脚本。", required=False, default=""),
    ],
    returns="dict{success, path, error}",
)
def export_template_to_file(filepath: str, name: str = "", description: str = "", category: str = "custom",
                            tags: str = "", aliases: str = "", flow_json: str = "") -> dict:
    """导出模板到 .flow 文件。"""
    try:
        # If flow_json provided, parse it; otherwise create a minimal shell
        if flow_json:
            script = json.loads(flow_json)
        else:
            script = {"nodes": [], "edges": [], "meta": {}}
        if isinstance(script, dict):
            if "meta" not in script:
                script["meta"] = {}
            script["meta"]["name"] = name or os.path.splitext(os.path.basename(filepath))[0]
            script["meta"]["description"] = description
            script["meta"]["category"] = category
            script["meta"]["tags"] = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            script["meta"]["aliases"] = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []
        os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        return {"success": True, "path": filepath, "error": ""}
    except Exception as e:
        return {"success": False, "path": "", "error": str(e)}


@feature(
    name="import_template_from_file",
    display_name="从文件导入模板",
    description="从 .flow 文件导入模板到 templates/ 目录。读取文件并复制到 templates/ 下，返回元数据。",
    category=F.DEBUG,
    params=[
        P("filepath", "str", "源文件路径（.flow 格式）", example="C:/Users/me/Desktop/my-flow.flow"),
        P("copy_to_templates", "boolean", "是否复制到 templates/ 目录", required=False, default=True),
    ],
    returns="dict{success, name, description, category, tags, node_count, error}",
)
def import_template_from_file(filepath: str, copy_to_templates: bool = True) -> dict:
    """从 .flow 文件导入模板。"""
    try:
        if not os.path.isfile(filepath):
            return {"success": False, "error": f"文件未找到: {filepath}"}
        with open(filepath, 'r', encoding='utf-8') as f:
            script = json.load(f)
        meta = script.get("meta", {}) if isinstance(script, dict) else {}
        name = meta.get("name", os.path.splitext(os.path.basename(filepath))[0])

        if copy_to_templates and os.path.isdir(TEMPLATES_DIR):
            dest = os.path.join(TEMPLATES_DIR, f"{name}.flow")
            import shutil
            shutil.copy2(filepath, dest)

        nodes = script.get("nodes", []) if isinstance(script, dict) else []
        return {
            "success": True,
            "name": name,
            "description": meta.get("description", ""),
            "category": meta.get("category", ""),
            "tags": meta.get("tags", []),
            "aliases": meta.get("aliases", []),
            "node_count": len(nodes),
            "error": "",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="search_flow_templates",
    display_name="搜索本地流程模板",
    description="【模板】确定性搜索本地 Flow/DSL/JSON 模板，不依赖前端窗口或另一套规划器。",
    category=F.AI,
    params=[
        P("query", "str", "搜索关键词（自然语言）", example="打开记事本输入文字"),
        P("max_results", "int", "最多返回结果数", required=False, default=5),
    ],
    returns="dict{htn, flat, autoSelect}",
)
def search_flow_templates(query: str, max_results: int = 5) -> dict:
    """Search local templates without depending on renderer state."""
    needle = str(query or "").strip().casefold()
    limit = max(1, min(int(max_results or 5), 50))
    if not needle:
        return {"error": "query is required", "htn": [], "flat": [], "autoSelect": None}
    candidates = []
    for path in sorted(Path(TEMPLATES_DIR).glob("*")):
        if path.suffix.casefold() not in {".flow", ".dsl", ".json"} or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = path.stem
        description = ""
        if path.suffix.casefold() in {".flow", ".json"}:
            try:
                parsed = json.loads(content)
                meta = parsed.get("meta") if isinstance(parsed, dict) else {}
                if isinstance(meta, dict):
                    name = str(meta.get("name") or name)
                    description = str(meta.get("description") or "")
            except json.JSONDecodeError:
                pass
        haystack = f"{name} {description} {path.name} {content[:2000]}".casefold()
        if needle not in haystack:
            continue
        score = 1.0 if needle == name.casefold() else 0.8 if needle in name.casefold() else 0.5
        candidates.append({
            "id": path.stem,
            "name": name,
            "description": description,
            "path": str(path),
            "format": path.suffix.lstrip("."),
            "score": score,
        })
    flat = sorted(candidates, key=lambda item: (-item["score"], item["name"]))[:limit]
    return {
        "htn": [],
        "flat": flat,
        "autoSelect": flat[0] if flat and flat[0]["score"] >= 0.8 else None,
    }


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
