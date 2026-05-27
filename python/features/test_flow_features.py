"""
保存测试流程到 tests/flows/ 目录，用于批量测试和回归测试
"""
import json
import os
import time
from pathlib import Path
from features.registry import feature, P, TC, FeatureCategory as F

# tests/flows/ 目录位于项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_FLOWS_DIR = PROJECT_ROOT / "tests" / "flows"


@feature(
    name="save_test_flow",
    display_name="保存测试流程",
    description="将当前流程保存到 tests/flows/ 目录，用于批量测试和回归测试",
    category=F.DEBUG,
    params=[
        P("flow_data", "str", "流程 JSON 数据"),
        P("test_name", "str", "测试名称（用于文件名）"),
        P("tags", "list[str]", "测试标签，如 ['smoke', 'regression']", required=False, default=[]),
    ],
    returns="dict{success, path}",
    tags=["test", "flow"],
)
def save_test_flow(flow_data: str, test_name: str, tags: list = None) -> dict:
    """保存测试流程到 tests/flows/ 目录"""
    if not flow_data or not test_name:
        return {"success": False, "error": "缺少 flow_data 或 test_name"}

    os.makedirs(str(TEST_FLOWS_DIR), exist_ok=True)

    # Sanitize filename
    safe_name = "".join(c for c in test_name if c.isalnum() or c in " _-").strip()
    if not safe_name:
        safe_name = f"test_{int(time.time())}"

    # Parse flow data if it's a JSON string
    if isinstance(flow_data, str):
        try:
            parsed = json.loads(flow_data)
        except json.JSONDecodeError:
            return {"success": False, "error": "flow_data 不是有效的 JSON"}
    else:
        parsed = flow_data

    flow_path = str(TEST_FLOWS_DIR / f"{safe_name}.flow")
    with open(flow_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Also save metadata if tags provided
    if tags:
        meta_path = str(TEST_FLOWS_DIR / f"{safe_name}.meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "test_name": safe_name,
                "tags": tags,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            }, f, ensure_ascii=False, indent=2)

    return {"success": True, "path": flow_path}


@feature(
    name="list_test_flows",
    display_name="列出测试流程",
    description="列出 tests/flows/ 目录下的所有测试流程，按保存时间排序",
    category=F.DEBUG,
    params=[
        P("tag", "str", "按标签筛选", required=False, default=""),
        P("limit", "int", "返回数量上限", required=False, default=50),
    ],
    returns="list[dict] - 测试流程列表",
    tags=["test", "flow"],
)
def list_test_flows(tag: str = "", limit: int = 50) -> list:
    """列出 tests/flows/ 目录下的测试流程"""
    if not os.path.isdir(str(TEST_FLOWS_DIR)):
        return []

    results = []
    for f in sorted(os.listdir(str(TEST_FLOWS_DIR)), reverse=True):
        if not f.endswith('.flow'):
            continue
        base = f[:-5]  # Remove .flow
        meta_path = TEST_FLOWS_DIR / f"{base}.meta.json"
        meta = {}
        if os.path.exists(str(meta_path)):
            try:
                with open(str(meta_path)) as mf:
                    meta = json.load(mf)
            except Exception:
                pass

        tags_list = meta.get("tags", [])
        if tag and tag not in tags_list:
            continue

        results.append({
            "name": base,
            "path": str(TEST_FLOWS_DIR / f),
            "tags": tags_list,
            "saved_at": meta.get("saved_at", ""),
        })

        if len(results) >= limit:
            break

    return results


@feature(
    name="run_test_flow",
    display_name="运行测试流程",
    description="从 tests/flows/ 目录加载并执行指定测试流程",
    category=F.DEBUG,
    params=[
        P("name", "str", "测试名称（不带 .flow 后缀）"),
    ],
    returns="dict{success, result}",
    tags=["test", "flow"],
)
def run_test_flow(name: str) -> dict:
    """从 tests/flows/ 目录加载并执行测试流程"""
    flow_path = TEST_FLOWS_DIR / f"{name}.flow"
    if not os.path.exists(str(flow_path)):
        return {"success": False, "error": f"测试流程不存在: {name}"}

    try:
        with open(str(flow_path), "r", encoding="utf-8") as f:
            flow_data = json.load(f)

        dsl_script = flow_data.get("dsl") or flow_data.get("script")
        if dsl_script:
            from engine.executor import DSLExecutor
            exe = DSLExecutor(None)
            result = exe.run_dsl_sync(dsl_script)
            return {"success": True, "result": result}

        return {"success": False, "error": "测试流程不含可执行的 DSL 数据"}
    except Exception as e:
        return {"success": False, "error": str(e)}
