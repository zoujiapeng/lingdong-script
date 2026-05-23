"""
Software control features — control 灵动脚本 via its HTTP API (127.0.0.1:5877).

Allows the agent to create and manipulate flowcharts programmatically,
without needing to simulate mouse clicks on the UI.
"""

import logging
from functools import wraps

import requests

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger(__name__)

_API_BASE = "http://127.0.0.1:5877"


def _api_post(endpoint: str, body: dict = None, timeout: int = 5) -> dict:
    """Call the software HTTP API and return a standardised result dict."""
    try:
        r = requests.post(f"{_API_BASE}{endpoint}", json=body or {}, timeout=timeout)
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                data = {}
            return {"success": True, "result": data}
        else:
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except requests.ConnectionError:
        return {"success": False, "error": "无法连接到灵动脚本 (127.0.0.1:5877)，请确认软件已启动"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="software_new_script",
    display_name="新建脚本",
    description="【软件操控】在灵动脚本中新建一个空白流程图",
    category=F.CONTROL,
    params=[],
    returns="dict{success, result}",
    tags=["software"],
)
def software_new_script() -> dict:
    return _api_post("/api/new-script")


@feature(
    name="software_add_node",
    display_name="添加节点",
    description="【软件操控】在流程图中添加一个自动化节点",
    category=F.CONTROL,
    params=[
        P("type", "str", "节点类型: click, input_text, wait, image_click, click_text, keyboard, swipe, screenshot, condition, loop 等", example="click"),
        P("x", "int", "节点在画布上的 X 坐标", required=False, default=300),
        P("y", "int", "节点在画布上的 Y 坐标", required=False, default=200),
        P("params", "object", "节点参数字典", required=False, default={}),
    ],
    returns="dict{success, result}",
    tags=["software"],
)
def software_add_node(type: str, x: int = 300, y: int = 200, params: dict = None) -> dict:
    return _api_post("/api/add-node", {"type": type, "x": x, "y": y, "params": params or {}})


@feature(
    name="software_add_edge",
    display_name="添加连线",
    description="【软件操控】连接流程图中的两个节点",
    category=F.CONTROL,
    params=[
        P("from_node", "str", "起始节点 ID", example="node_001"),
        P("to_node", "str", "目标节点 ID", example="node_002"),
        P("from_port", "str", "起始端口", required=False, default="output"),
        P("to_port", "str", "目标端口", required=False, default="input"),
    ],
    returns="dict{success, result}",
    tags=["software"],
)
def software_add_edge(from_node: str, to_node: str, from_port: str = "output", to_port: str = "input") -> dict:
    return _api_post("/api/add-edge", {
        "from_node": from_node, "to_node": to_node,
        "from_port": from_port, "to_port": to_port,
    })


@feature(
    name="software_load_script",
    display_name="加载脚本",
    description="【软件操控】加载完整脚本到流程图。script_json 需包含 nodes, edges, meta 字段",
    category=F.CONTROL,
    params=[
        P("script_json", "object", "完整的脚本 JSON（包含 nodes, edges, meta）", example={"nodes":[],"edges":[],"meta":{"name":"test"}}),
    ],
    returns="dict{success, result}",
    tags=["software"],
)
def software_load_script(script_json: dict) -> dict:
    return _api_post("/api/load-script", {"script": script_json})


@feature(
    name="software_auto_layout",
    display_name="自动排版",
    description="【软件操控】自动排版流程图布局",
    category=F.CONTROL,
    params=[],
    returns="dict{success, result}",
    tags=["software"],
)
def software_auto_layout() -> dict:
    return _api_post("/api/auto-layout")


@feature(
    name="software_set_view",
    display_name="切换视图",
    description="【软件操控】切换软件视图模式",
    category=F.CONTROL,
    params=[
        P("mode", "str", "视图模式: canvas | pseudocode | agent | settings", example="canvas"),
    ],
    returns="dict{success, result}",
    tags=["software"],
)
def software_set_view(mode: str) -> dict:
    return _api_post("/api/set-view-mode", {"mode": mode})


@feature(
    name="software_get_state",
    display_name="获取软件状态",
    description="【软件操控】获取灵动脚本当前状态（节点列表、视图模式等）",
    category=F.CONTROL,
    params=[],
    returns="dict{success, result: object}",
    tags=["software"],
)
def software_get_state() -> dict:
    return _api_post("/api/get-state")


@feature(
    name="software_save_script",
    display_name="保存脚本",
    description="【软件操控】保存当前脚本到文件",
    category=F.CONTROL,
    params=[],
    returns="dict{success, result}",
    tags=["software"],
)
def software_save_script() -> dict:
    return _api_post("/api/save-script")


@feature(
    name="software_run_script",
    display_name="运行脚本",
    description="【软件操控】执行当前流程图脚本（触发引擎运行）",
    category=F.CONTROL,
    params=[
        P("script_json", "object", "要执行的完整脚本 JSON；为空时执行当前画布脚本", required=False, default=None),
    ],
    returns="dict{success, result}",
    tags=["software"],
)
def software_run_script(script_json: dict = None) -> dict:
    if script_json is None:
        state = _api_post("/api/get-state", {}, timeout=3)
        if not state.get("success"):
            return state
        script_json = state.get("result") or {}
    return _api_post("/api/engine/start", {"script": script_json}, timeout=3)
