"""
DSL ↔ FlowScript 双向转换器

将 Lobster DSL（大写 KEYWORD 格式）与前端 FlowScript JSON（nodes+edges 格式）互相转换。
这是单引擎架构的核心桥接层。

DSL 示例:
  CLICK 100 200
  WAIT 2
  LOOP 5
    CLICK 确认
  END

FlowScript 示例:
  { "nodes": [{"id":"n1","type":"click","params":{"x":100,"y":200},...}],
    "edges": [{"id":"e1","from_node":"n1","to_node":"n2",...}] }
"""

from __future__ import annotations
import json
import re
from typing import Any, Optional

from engine.dsl_parser import DSLParser, ASTNode, NodeType


# ═══════════════════════════════════════════════════════════════
# 动态 DSL ↔ Flow 映射（从 Feature Registry 构建）
# ═══════════════════════════════════════════════════════════════

# 结构关键字映射（无对应 @feature，由 flow converter 内部处理）
# 这些关键字不在 registry 中，必须硬编码
_BASE_DSL_TO_FLOW = {
    "LOOP": "loop",
    "IF": "condition",
    "ELSE": "condition",
    "END": "end",
    "BREAK": "break",
    "RETURN": "end",
    "SUBROUTINE": "sub_script",
    "IMPORT": "import",
    "WHEN": "when",
    "PARALLEL": "parallel",
    "WITH": "with",
    "WHILE": "loop",
    "UNTIL": "loop",
    "ELIF": "condition",
    "ELSEIF": "condition",
    "SEQUENCE": "sequence",
    "TEMPLATE_CAPTURE": "screenshot",
}

# 用于 @feature 关键字的兼容降级映射（registry flow_type 优先）
# 所有带 dsl_keyword 的 @feature 都已声明 flow_type，此处仅作兜底
_BASIC_DSL_TO_FLOW = {
    "CLICK": "click", "TYPE": "input_text", "HOTKEY": "keyboard",
    "SWIPE": "swipe", "SCROLL": "swipe", "SCREENSHOT": "screenshot",
    "SET": "variable_set", "INPUT_TEXT": "input_text",
    "CLICK_IMAGE": "image_click",
    "CLICK_TEXT": "click_text",
    "IMAGE_CLICK": "image_click",
    "PROGRESS_WAIT": "progress_wait", "OCR_FIND": "click_text",
    "POPUP_CLOSE": "popup_close", "COLOR_CHECK": "color_check",
    "REGION_WAIT": "region_wait", "SCENE_CONDITION": "scene_condition",
    "CALL": "sub_script", "WAIT": "wait", "DELAY": "wait",
    "SHUTDOWN": "shutdown", "REBOOT": "reboot",
    "LOGOFF": "logoff", "HIBERNATE": "hibernate",
    "LOCK": "lock_workstation", "VOLUME": "volume_set", "MUTE": "volume_mute",
    "LAUNCH": "launch_program", "KILL": "kill_process",
    "WAIT_PROCESS": "wait_process", "WAIT_EXIT": "wait_process",
    "WALLPAPER": "set_wallpaper", "DISPLAY_OFF": "display_off",
    "SLEEP": "system_sleep", "DESKTOP": "show_desktop",
    "BATTERY": "battery_status", "EMPTY_BIN": "empty_recycle_bin",
    "BRIGHTNESS": "display_brightness",
    "CLIPBOARD_SET": "clipboard_set", "CLIPBOARD_GET": "clipboard_get",
    "MINIMIZE": "window_control", "MAXIMIZE": "window_control",
    "CLOSE_WINDOW": "window_control", "RESIZE_WINDOW": "window_control",
    "WAIT_RANDOM": "wait", "FOCUS": "click", "RUN": "sub_script",
    "WAITSCREEN": "region_wait", "SCREENSTABLE": "region_wait",
    "IMAGE_FIND": "condition", "OCR_EXTRACT": "screenshot",
    "PROGRESS_INIT": "progress_wait", "PROGRESS_RELOCATE": "progress_wait",
    "REGION_SELECT": "screenshot",
    "FINDCANVAS": "condition",
    "DRAG": "click",
}

_dsl_to_flow_cache: dict[str, str] | None = None
_flow_to_dsl_cache: dict[str, str] | None = None


def get_dsl_to_flow_type() -> dict[str, str]:
    """获取 DSL 关键字 → Flow 节点类型的完整映射。

    优先级：registry.flow_type > _BASIC_DSL_TO_FLOW > _BASE_DSL_TO_FLOW
    """
    global _dsl_to_flow_cache
    if _dsl_to_flow_cache is None:
        mapping = dict(_BASE_DSL_TO_FLOW)
        mapping.update(_BASIC_DSL_TO_FLOW)
        # 从 registry 补充（flow_type 优先于硬编码映射）
        try:
            from features.registry import registry
            for f in registry.all():
                if f.dsl_keyword and not f.deprecated:
                    kw = f.dsl_keyword.upper()
                    if f.flow_type:
                        mapping[kw] = f.flow_type  # flow_type 是权威来源
                    elif kw not in mapping:
                        mapping[kw] = "tool_call"  # 无映射的 DSL 关键字走通用 tool_call
        except ImportError:
            pass
        _dsl_to_flow_cache = mapping
    return _dsl_to_flow_cache


def get_flow_to_dsl_type() -> dict[str, str]:
    """获取 Flow 节点类型 → DSL 关键字的映射。"""
    global _flow_to_dsl_cache
    if _flow_to_dsl_cache is None:
        dsl_to_flow = get_dsl_to_flow_type()
        flow_to_dsl = {}
        for kw, ftype in dsl_to_flow.items():
            if ftype not in flow_to_dsl:
                flow_to_dsl[ftype] = kw
        _flow_to_dsl_cache = flow_to_dsl
    return _flow_to_dsl_cache

# 需要分支端口（yes/no）的节点
BRANCH_TYPES = {"IF", "IMAGE_FIND", "SCENE_CONDITION"}

# 需要 loop_body/loop_end 端口
LOOP_TYPES = {"LOOP"}

# 无动作/跳过
NOOP_TYPES = {"SEQUENCE", "SUBROUTINE", "IMPORT", "WHEN", "PARALLEL", "WITH",
              "TEMPLATE_CAPTURE", "END"}


def dsl_to_flow(dsl_text: str, layout: str = "vertical") -> dict:
    """将 DSL 文本转换为 FlowScript JSON（前端 Canvas 格式）。

    Args:
        dsl_text: 大写 DSL 文本
        layout: 布局方向 ("vertical" | "horizontal")

    Returns:
        { "meta": {...}, "nodes": [...], "edges": [...], "variables": {}, "settings": {...} }
    """
    try:
        ast = DSLParser.from_string(dsl_text)
    except Exception as e:
        return _error_flow_script(str(e))

    nodes: list[dict] = []
    edges: list[dict] = []
    counter = [0]
    x_origin = 300
    y_origin = 0
    step_x = 200
    step_y = 80

    def _nid() -> str:
        counter[0] += 1
        return f"n_{counter[0]}"

    def _eid() -> str:
        counter[0] += 1
        return f"e_{counter[0]}"

    def _add_node(ntype: str, x: int, y: int, args: str,
                  parent_ast: Optional[ASTNode] = None) -> str:
        uid = _nid()
        flow_type = get_dsl_to_flow_type().get(ntype, "tool_call")
        params = _dsl_args_to_params(ntype, args, parent_ast)
        label = _dsl_label(ntype, args)

        node = {
            "id": uid,
            "type": flow_type,
            "x": x,
            "y": y,
            "params": params,
            "status": "idle",
        }
        if label:
            node["label"] = label
        nodes.append(node)
        return uid

    def _add_edge(frm: str, to: str, frm_port: str = "output",
                  to_port: str = "input", label: str = "",
                  animated: bool = False):
        edges.append({
            "id": _eid(),
            "from_node": frm,
            "from_port": frm_port,
            "to_node": to,
            "to_port": to_port,
            "label": label,
            "animated": animated,
        })

    def _walk_ast(node: ASTNode, prev_id: str | None, x: int, y: int):
        """递归遍历 AST 生成节点和边。"""
        nt = node.type.value if hasattr(node.type, 'value') else str(node.type)

        if nt in NOOP_TYPES:
            # SEQUENCE: 遍历子节点
            last_id = prev_id
            cy = y
            for child in (node.children or []):
                last_id, cy = _walk_ast(child, last_id, x, cy)
            return last_id, cy

        if nt == "BREAK":
            # BREAK 在 DSL 中是循环内的关键字，用特殊 port 标记
            uid = _add_node("BREAK", x, y, node.args)
            if prev_id:
                _add_edge(prev_id, uid)
            return uid, y + step_y

        # 创建当前节点
        uid = _add_node(nt, x, y, node.args, parent_ast=node)
        if prev_id:
            _add_edge(prev_id, uid)

        cy = y + step_y

        # 分支节点（IF）
        if nt in BRANCH_TYPES:
            # true 分支
            if node.children:
                first_child_id = None
                last_id = None
                for child in node.children:
                    last_id, cy = _walk_ast(child, last_id, x + step_x, cy)
                    if first_child_id is None:
                        first_child_id = last_id
                if first_child_id:
                    _add_edge(uid, first_child_id, frm_port="yes",
                              label="是")

            # false 分支 (else_children)
            if node.else_children:
                else_first = None
                else_last = None
                else_y = y + step_y
                for child in node.else_children:
                    else_last, else_y = _walk_ast(child, else_last,
                                                  x + step_x * 2, else_y)
                    if else_first is None:
                        else_first = else_last
                if else_first:
                    _add_edge(uid, else_first, frm_port="no",
                              label="否")

            return uid, cy

        # 循环节点（LOOP）
        if nt in LOOP_TYPES:
            if node.children:
                body_first = None
                body_last = None
                for child in node.children:
                    body_last, cy = _walk_ast(child, body_last,
                                              x + step_x, cy)
                    if body_first is None:
                        body_first = body_last
                if body_first:
                    _add_edge(uid, body_first, frm_port="loop_body",
                              animated=True)
                # loop_end 指向循环外
                edge_to_end = _find_next_edge(prev_id, uid, edges)
            return uid, cy

        # 普通节点
        return uid, cy

    def _find_next_edge(prev_id, cur_id, edge_list):
        """找当前节点的下一个连接（用于 loop_end）。"""
        for e in edge_list:
            if e["from_node"] == prev_id and e["to_node"] == cur_id:
                return e
        return None

    # 创建 start 节点
    start_id = _nid()
    nodes.append({
        "id": start_id, "type": "start",
        "x": x_origin, "y": 0,
        "params": {"label": "开始"}, "status": "idle",
    })

    # 遍历 AST
    _walk_ast(ast, start_id, x_origin, y_origin)

    # 如果没有显式 end 节点，添加默认 end
    has_end = any(n["type"] == "end" for n in nodes)
    if not has_end:
        end_id = _nid()
        last_y = max((n.get("y", 0) for n in nodes), default=0) + step_y
        nodes.append({
            "id": end_id, "type": "end",
            "x": x_origin, "y": last_y,
            "params": {"label": "结束"}, "status": "idle",
        })
        # 找最后一个节点连接 end
        output_nodes = [n for n in nodes if n["type"] not in ("end", "start")]
        if output_nodes:
            last_node = output_nodes[-1]
            _add_edge(last_node["id"], end_id)

    return {
        "meta": {
            "id": "",
            "name": "从 DSL 导入",
            "description": "由 DSL 文本自动生成的流程图",
            "created_at": "",
            "updated_at": "",
            "version": "1.0",
            "author": "dsl_flow_converter",
        },
        "nodes": nodes,
        "edges": edges,
        "variables": {},
        "settings": _default_settings(),
    }


def _dsl_args_to_params(ntype: str, args: str,
                         parent_ast: Optional[ASTNode] = None) -> dict:
    """将 DSL arguments 字符串转为 FlowNode params 字典。"""
    params: dict = {}

    if ntype == "CLICK" or ntype == "FOCUS":
        parts = args.split()
        coords = [p for p in parts if p.lstrip("-").isdigit()]
        params = {
            "x": int(coords[0]) if len(coords) > 0 else 0,
            "y": int(coords[1]) if len(coords) > 1 else 0,
            "method": "single",
            "delay_after": 0.5,
        }
        # 检查后缀修饰
        if any(kw in args.upper() for kw in (" RIGHT", " DOUBLE", " R")):
            if "RIGHT" in args.upper() or " R" in args.upper():
                params["method"] = "right"
            if "DOUBLE" in args.upper():
                params["method"] = "double"

    elif ntype == "WAIT":
        try:
            dur = float(args.strip())
            params = {"wait_type": "time", "duration": dur}
        except ValueError:
            # 非数字条件 → 交给 action_handler
            params = {"wait_type": "time", "duration": 1.0}

    elif ntype == "TYPE":
        text = args.strip().strip("\"'")
        params = {
            "text": text,
            "click_before": False,
            "clear_before": False,
            "delay_after": 0.5,
        }

    elif ntype == "INPUT_TEXT":
        # INPUT_TEXT x y "text"
        nums = re.findall(r"-?\d+", args)
        text_match = re.search(r"\"([^\"]*)\"", args)
        params = {
            "text": text_match.group(1) if text_match else args.strip().strip("\"'"),
            "x": int(nums[0]) if len(nums) > 0 else 0,
            "y": int(nums[1]) if len(nums) > 1 else 0,
            "click_before": True,
            "clear_before": False,
            "delay_after": 0.5,
        }

    elif ntype == "CLICK_TEXT":
        text = args.strip().strip("\"'")
        params = {
            "text": text,
            "similarity": 80,
            "method": "single",
            "offset_x": 0,
            "offset_y": 0,
            "timeout": 5,
            "delay_after": 0.5,
        }

    elif ntype == "HOTKEY":
        keys_parts = re.split(r"[\s+]", args.strip())
        mods = {"ctrl", "alt", "shift", "win"}
        modifiers = [k.lower() for k in keys_parts if k.lower() in mods]
        keys = [k.lower() for k in keys_parts if k.lower() not in mods and k]
        params = {"keys": keys or keys_parts, "modifiers": modifiers,
                   "delay_after": 0.5}

    elif ntype == "SWIPE":
        nums = re.findall(r"-?\d+", args)
        params = {
            "start_x": int(nums[0]) if len(nums) > 0 else 0,
            "start_y": int(nums[1]) if len(nums) > 1 else 0,
            "end_x": int(nums[2]) if len(nums) > 2 else 0,
            "end_y": int(nums[3]) if len(nums) > 3 else 0,
            "duration": 0.5,
            "delay_after": 0.5,
        }

    elif ntype == "SCROLL":
        nums = re.findall(r"-?\d+", args)
        clicks = int(nums[0]) if nums else 3
        params = {
            "start_x": 0, "start_y": 0,
            "end_x": 0, "end_y": -clicks * 50,
            "duration": 0.3,
            "delay_after": 0.5,
        }

    elif ntype == "SCREENSHOT":
        params = {}

    elif ntype == "SET":
        parts = args.strip().split(None, 1)
        name = parts[0] if parts else ""
        value = parts[1].strip().strip("\"'") if len(parts) > 1 else ""
        params = {"name": name, "value": value}

    elif ntype in ("IMAGE_CLICK", "CLICK_IMAGE"):
        paths = re.findall(r'"([^"]*)"', args)
        params = {
            "targets": [{"path": p} for p in paths],
            "similarity": 90, "timeout": 5,
            "method": "single", "offset_x": 0, "offset_y": 0,
            "delay_after": 0.5, "multi_scale": False, "match_all": False,
        }

    elif ntype == "PROGRESS_WAIT":
        parts = args.split()
        params = {
            "wait_type": parts[0] if parts else "fill",
            "timeout": 30, "poll_interval": 0.5,
        }
        # 解析空格分隔的坐标: fill x y w h [timeout]
        if len(parts) >= 5:
            try:
                params["region"] = {
                    "x": int(parts[1]), "y": int(parts[2]),
                    "w": int(parts[3]), "h": int(parts[4]),
                }
                if len(parts) > 5:
                    params["timeout"] = int(parts[5])
            except ValueError:
                pass

    elif ntype == "OCR_FIND":
        text = args.strip().strip("\"'")
        params = {
            "text": text, "similarity": 80,
            "method": "single", "offset_x": 0, "offset_y": 0,
            "timeout": 5, "delay_after": 0.5,
        }

    elif ntype == "DELAY":
        nums = re.findall(r"-?\d+\.?\d*", args)
        dur = float(nums[0]) if nums else 1.0
        params = {"wait_type": "time", "duration": dur}

    elif ntype == "WAIT_RANDOM":
        nums = re.findall(r"-?\d+\.?\d*", args)
        min_d = float(nums[0]) if len(nums) > 0 else 0.5
        max_d = float(nums[1]) if len(nums) > 1 else 2.0
        params = {"wait_type": "time", "duration": (min_d + max_d) / 2}

    elif ntype == "POPUP_CLOSE":
        strat = args.strip() or "auto"
        params = {"strategies": [strat], "timeout": 10, "delay_after": 0.5}

    elif ntype == "COLOR_CHECK":
        nums = re.findall(r"-?\d+", args)
        parts = args.split()
        params = {
            "x": int(nums[0]) if len(nums) > 0 else 0,
            "y": int(nums[1]) if len(nums) > 1 else 0,
            "w": int(nums[2]) if len(nums) > 2 else 100,
            "h": int(nums[3]) if len(nums) > 3 else 100,
            "expected_color": parts[4] if len(parts) > 4 else "",
            "tolerance": int(nums[5]) if len(nums) > 5 else 30,
        }

    elif ntype in ("SHUTDOWN", "REBOOT", "LOGOFF", "HIBERNATE"):
        nums = re.findall(r"\d+", args)
        params = {
            "delay_seconds": int(nums[0]) if nums else 0,
            "confirm": True,
        }

    elif ntype == "LOCK":
        params = {"confirm": True}

    elif ntype == "VOLUME":
        nums = re.findall(r"\d+", args)
        params = {"level": int(nums[0]) if nums else 50}

    elif ntype == "MUTE":
        a = args.strip().lower()
        if a in ("on", "true", "1"):
            params = {"mute": True}
        elif a in ("off", "false", "0"):
            params = {"mute": False}
        else:
            params = {"mute": None}

    elif ntype == "LAUNCH":
        params = {"target": args, "args": "", "wait": False}

    elif ntype == "KILL":
        parts = args.split()
        params = {"name": parts[0] if parts else args, "force": "force" in args.lower() or "-f" in args}

    elif ntype == "WAIT_PROCESS":
        parts = args.split()
        name = ""
        timeout = 30
        if parts:
            name = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                timeout = int(parts[1])
        params = {"name": name, "timeout": timeout, "wait_for": "start"}

    elif ntype == "WAIT_EXIT":
        parts = args.split()
        name = ""
        timeout = 60
        if parts:
            name = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                timeout = int(parts[1])
        params = {"name": name, "timeout": timeout, "wait_for": "exit"}

    elif ntype == "WALLPAPER":
        parts = args.split(None, 1)
        path = parts[0] if parts else args
        style = "fill"
        if len(parts) > 1:
            s = parts[1].strip().lower()
            if s in ("fill", "fit", "stretch", "tile", "center"):
                style = s
        params = {"path": path, "style": style}

    elif ntype == "DISPLAY_OFF":
        params = {"confirm": True}

    elif ntype == "SLEEP":
        params = {"confirm": True}

    elif ntype == "DESKTOP":
        params = {}

    elif ntype == "BATTERY":
        params = {}

    elif ntype == "EMPTY_BIN":
        a = args.strip().lower()
        params = {"confirm": a not in ("no", "false", "0")}

    elif ntype == "BRIGHTNESS":
        nums = re.findall(r"\d+", args)
        params = {"level": int(nums[0]) if nums else 80}

    elif ntype == "CLIPBOARD_SET":
        params = {"text": args}

    elif ntype == "CLIPBOARD_GET":
        params = {}

    elif ntype in ("MINIMIZE", "MAXIMIZE", "CLOSE_WINDOW", "RESIZE_WINDOW"):
        op_map = {"MINIMIZE": "minimize", "MAXIMIZE": "maximize", "CLOSE_WINDOW": "close", "RESIZE_WINDOW": "resize"}
        title = args
        width, height = 800, 600
        if ntype == "RESIZE_WINDOW":
            nums = re.findall(r"\d+", args)
            if len(nums) >= 2:
                width, height = int(nums[0]), int(nums[1])
                rest = args
                for n in nums[:2]:
                    rest = rest.replace(str(n), "", 1)
                title = rest.strip()
        params = {"operation": op_map[ntype], "title": title, "width": width, "height": height}
        if ntype != "RESIZE_WINDOW":
            params.pop("width", None)
            params.pop("height", None)

    elif ntype == "REGION_WAIT":
        nums = re.findall(r"-?\d+", args)
        parts = args.split()
        region_type = "stable"
        if any(t in args.lower() for t in ("change",)):
            region_type = "change"
        params = {
            "region_type": region_type,
            "x": int(nums[0]) if len(nums) > 0 else 0,
            "y": int(nums[1]) if len(nums) > 1 else 0,
            "w": int(nums[2]) if len(nums) > 2 else 100,
            "h": int(nums[3]) if len(nums) > 3 else 100,
            "timeout": int(nums[4]) if len(nums) > 4 else 30,
            "poll_interval": 0.5,
        }
        # region 整合
        if len(nums) >= 4:
            params["region"] = {"x": params["x"], "y": params["y"], "w": params["w"], "h": params["h"]}

    elif ntype == "SCENE_CONDITION":
        parts = args.rsplit(None, 1)
        params = {
            "scene_name": parts[0] if parts else args,
            "similarity": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 70,
        }

    elif ntype == "LOOP":
        loop_type = "count"
        count = 10
        if parent_ast and getattr(parent_ast, "loop_type", None) == "while":
            loop_type = "condition"
        elif parent_ast and getattr(parent_ast, "loop_type", None) == "until":
            loop_type = "condition"
        else:
            try:
                count = int(args.strip())
            except ValueError:
                count = 10
        params = {"loop_type": loop_type, "count": count}

    elif ntype == "IF":
        params = {"condition_type": "always_true"}

    elif ntype == "RUN":
        name = args.strip().strip("\"'")
        params = {"script_id": name, "script_name": name}

    elif ntype == "CALL":
        m = re.match(r"(\w+)\s*\(", args)
        name = m.group(1) if m else args.strip()
        params = {"script_id": name, "script_name": name}

    elif ntype == "RETURN":
        params = {"label": args.strip() or "结束"}

    elif ntype in ("WAITSCREEN", "SCREENSTABLE"):
        timeout = 30
        try:
            timeout = float(args.strip())
        except ValueError:
            pass
        params = {
            "region_type": "change" if ntype == "WAITSCREEN" else "stable",
            "x": 0, "y": 0, "w": 1920, "h": 1080,
            "timeout": timeout, "poll_interval": 0.5,
        }

    elif ntype == "IMAGE_FIND":
        paths = re.findall(r'"([^"]*)"', args)
        params = {
            "condition_type": "image_exists",
            "template_path": paths[0] if paths else "",
            "similarity": 90,
        }

    elif ntype in ("FILEDIALOG_SAVE", "FILEDIALOG_OPEN", "FILEDIALOG_FOLDER"):
        params = {"path": args.strip().strip('"').strip("'")}

    else:
        params = {"_raw_args": args}

    return params


def _dsl_label(ntype: str, args: str) -> str:
    """为 DSL 节点生成友好标签。"""
    labels = {
        "CLICK": f"点击 {args}",
        "WAIT": f"等待 {args}",
        "TYPE": f"输入",
        "LOOP": f"循环 {args}",
        "IF": f"条件判断",
        "SWIPE": "滑动",
        "SCROLL": "滚动",
        "HOTKEY": f"按键 {args}",
        "SCREENSHOT": "截图",
        "SET": f"变量设置",
        "INPUT_TEXT": "输入文本",
        "CLICK_IMAGE": "点击图片",
        "CLICK_TEXT": "点击文本",
        "IMAGE_CLICK": "点击图片",
        "PROGRESS_WAIT": "等待进度",
        "OCR_FIND": f"点击文本",
        "DELAY": f"延时 {args}s",
        "WAIT_RANDOM": f"随机延时",
        "FOCUS": f"焦点 {args}",
        "RUN": f"运行 {args}",
        "CALL": f"调用 {args}",
        "RETURN": "结束",
        "WAITSCREEN": f"等待画面变化",
        "SCREENSTABLE": f"等待画面稳定",
        "IMAGE_FIND": f"查找图片",
        "FINDCANVAS": "检测画布",
        "DRAG": "拖拽",
        "BREAK": "跳出循环",
        "SHUTDOWN": "关机" + (f" ({args}s)" if args.strip() else ""),
        "REBOOT": "重启" + (f" ({args}s)" if args.strip() else ""),
        "LOGOFF": "注销" + (f" ({args}s)" if args.strip() else ""),
        "HIBERNATE": "休眠" + (f" ({args}s)" if args.strip() else ""),
        "FILEDIALOG_SAVE": "保存文件对话框",
        "FILEDIALOG_OPEN": "打开文件对话框",
        "FILEDIALOG_FOLDER": "选择文件夹对话框",
    }
    return labels.get(ntype, "")


def _default_settings() -> dict:
    return {
        "default_delay": 0.3,
        "error_handling": "stop",
        "max_retries": 2,
        "retry_delay": 1.0,
        "log_level": "info",
    }


def _error_flow_script(error_msg: str) -> dict:
    """解析失败时返回包含错误信息的空 FlowScript。"""
    return {
        "meta": {"name": "解析错误", "description": error_msg},
        "nodes": [
            {"id": "n_start", "type": "start", "x": 300, "y": 0,
             "params": {"label": "开始"}, "status": "idle"},
            {"id": "n_error", "type": "end", "x": 300, "y": 100,
             "params": {"label": f"错误: {error_msg}"}, "status": "error"},
        ],
        "edges": [
            {"id": "e1", "from_node": "n_start", "from_port": "output",
             "to_node": "n_error", "to_port": "input", "label": ""},
        ],
        "variables": {},
        "settings": _default_settings(),
    }


# ═══════════════════════════════════════════════════════════════
# FlowScript → DSL
# ═══════════════════════════════════════════════════════════════

# FlowNode type → DSL 关键字映射（动态构建）
# 使用 get_flow_to_dsl_type() 获取

# DSL 中需要 END 的块语句
BLOCK_KEYWORDS = {"LOOP", "IF", "WHILE", "UNTIL", "SUBROUTINE", "WHEN"}


def flow_to_dsl(script: dict) -> str:
    """将 FlowScript JSON 转换为 DSL 文本。

    Args:
        script: FlowScript 字典 {nodes, edges, variables, settings}

    Returns:
        大写 DSL 文本
    """
    nodes_map: dict[str, dict] = {}
    for n in script.get("nodes", []):
        nodes_map[n["id"]] = n

    # 构建出边邻接表 {from_id: {port: [edge, ...]}}
    outgoing: dict[str, dict[str, list[dict]]] = {}
    for e in script.get("edges", []):
        frm = e["from_node"]
        port = e.get("from_port", "output")
        outgoing.setdefault(frm, {}).setdefault(port, []).append(e)

    # 循环和条件的前驱节点缓存
    loop_bodies: dict[str, list[str]] = {}  # loop_node_id → [body_node_ids]
    condition_branches: dict[str, dict] = {}  # cond_node_id → {yes: [...], no: [...]}

    def _get_edges(frm: str, port: str = "output") -> list[dict]:
        return outgoing.get(frm, {}).get(port, [])

    def _get_first_edge(frm: str, port: str = "output") -> Optional[dict]:
        edges = _get_edges(frm, port)
        return edges[0] if edges else None

    def _node_to_dsl(node: dict) -> str:
        """单个节点 → DSL 行。"""
        ntype = node["type"]
        p = node.get("params", {})

        if ntype == "start":
            return ""
        if ntype == "end":
            return "RETURN"

        if ntype == "click":
            x, y = p.get("x", 0), p.get("y", 0)
            method = p.get("method", "single")
            suffix = ""
            if method == "double":
                suffix = " DOUBLE"
            elif method == "right":
                suffix = " RIGHT"
            return f"CLICK {x} {y}{suffix}"

        if ntype == "wait":
            wt = p.get("wait_type", "time")
            if wt == "time":
                dur = p.get("duration", 1)
                return f"WAIT {dur}"
            # 非时间类型委托
            return f"WAIT {p.get('description', '') or p.get('template_path', dur)}"

        if ntype == "input_text":
            text = p.get("text", "")
            return f'TYPE "{text}"'

        if ntype == "keyboard":
            keys = p.get("keys", [])
            mods = p.get("modifiers", [])
            parts = mods + [k for k in keys if k not in mods]
            return f"HOTKEY {'+'.join(parts)}"

        if ntype == "swipe":
            sx = p.get("start_x", 0)
            sy = p.get("start_y", 0)
            ex = p.get("end_x", 0)
            ey = p.get("end_y", 0)
            return f"SWIPE {sx} {sy} {ex} {ey}"

        if ntype == "image_click":
            targets = p.get("targets", [])
            paths = " ".join(f'"{t["path"]}"' for t in targets if t.get("path"))
            return f"IMAGE_CLICK {paths}"

        if ntype == "click_text":
            text = p.get("text", "")
            return f'OCR_FIND "{text}"'

        if ntype == "screenshot":
            return "SCREENSHOT"

        if ntype == "variable_set":
            name = p.get("name", "")
            value = p.get("value", "")
            return f'SET {name} "{value}"'

        if ntype == "variable_get":
            name = p.get("name", "")
            return f"SET {name}"

        if ntype == "popup_close":
            strategies = p.get("strategies", [])
            strat = strategies[0] if strategies else "auto"
            return f"POPUP_CLOSE {strat}"

        if ntype == "color_check":
            x = p.get("x", 0); y = p.get("y", 0)
            w = p.get("w", 100); h = p.get("h", 100)
            color = p.get("expected_color", "")
            tol = p.get("tolerance", 30)
            return f"COLOR_CHECK {x} {y} {w} {h} {color} {tol}"

        if ntype == "progress_wait":
            region = p.get("region", {})
            wt = p.get("wait_type", "fill")
            x, y = region.get("x", 0), region.get("y", 0)
            w, h = region.get("w", 100), region.get("h", 100)
            return f"PROGRESS_WAIT {wt} {x} {y} {w} {h}"

        if ntype == "region_wait":
            rt = p.get("region_type", "stable")
            region = p.get("region", {})
            x = region.get("x", p.get("x", 0))
            y = region.get("y", p.get("y", 0))
            w = region.get("w", p.get("w", 100))
            h = region.get("h", p.get("h", 100))
            timeout = p.get("timeout", 30)
            return f"REGION_WAIT {x} {y} {w} {h} {rt} {timeout}"

        if ntype == "scene_condition":
            scene = p.get("scene_name", "")
            sim = p.get("similarity", 70)
            return f"SCENE_CONDITION {scene} {sim}"

        if ntype in ("shutdown", "reboot", "logoff", "hibernate"):
            delay = p.get("delay_seconds", 0)
            kw = {"shutdown": "SHUTDOWN", "reboot": "REBOOT", "logoff": "LOGOFF", "hibernate": "HIBERNATE"}
            return f"{kw[ntype]} {delay}" if delay else kw[ntype]

        # 扩展系统指令 flow→DSL
        if ntype == "lock_workstation":
            return "LOCK"
        if ntype == "volume_set":
            return f"VOLUME {p.get('level', 50)}"
        if ntype == "volume_mute":
            m = p.get("mute")
            if m is None:
                return "MUTE"
            return f"MUTE {'ON' if m else 'OFF'}"
        if ntype == "launch_program":
            return f"LAUNCH {p.get('target', '')}"
        if ntype == "kill_process":
            return f"KILL {p.get('name', '')}"
        if ntype == "wait_process":
            wf = p.get("wait_for", "start")
            name = p.get("name", "")
            timeout = p.get("timeout", 30)
            kw = "WAIT_PROCESS" if wf == "start" else "WAIT_EXIT"
            return f"{kw} {name} {timeout}"
        if ntype == "focus_window":
            return f"FOCUS {p.get('title', '')}"
        if ntype == "set_wallpaper":
            return f"WALLPAPER {p.get('path', '')}"
        if ntype == "display_off":
            return "DISPLAY_OFF"
        if ntype == "system_sleep":
            return "SLEEP"
        if ntype == "show_desktop":
            return "DESKTOP"
        if ntype == "battery_status":
            return "BATTERY"
        if ntype == "empty_recycle_bin":
            return "EMPTY_BIN"
        if ntype == "display_brightness":
            return f"BRIGHTNESS {p.get('level', 80)}"
        if ntype == "clipboard_set":
            return f"CLIPBOARD_SET {p.get('text', '')}"
        if ntype == "clipboard_get":
            return "CLIPBOARD_GET"
        if ntype == "window_control":
            op = p.get("operation", "minimize")
            op_map = {"minimize": "MINIMIZE", "maximize": "MAXIMIZE", "close": "CLOSE_WINDOW", "resize": "RESIZE_WINDOW"}
            title = p.get("title", "")
            width = p.get("width", 800)
            height = p.get("height", 600)
            dsl_kw = op_map.get(op, "MINIMIZE")
            if op == "resize":
                return f"{dsl_kw} {width} {height} {title}".strip()
            return f"{dsl_kw} {title}".strip()

        if ntype == "condition":
            ct = p.get("condition_type", "always_true")
            if ct == "image_exists":
                path = p.get("template_path", "")
                return f'IMAGE_FIND "{path}"'
            if ct == "color_match":
                x = p.get("x", 0); y = p.get("y", 0)
                color = p.get("expected_color", "")
                return f"COLOR_CHECK {x} {y} {color}"
            return "IF true"

        if ntype == "loop":
            lt = p.get("loop_type", "count")
            if lt == "count":
                count = p.get("count", 10)
                return f"LOOP {count}"
            if lt == "infinite":
                return "LOOP 9999"
            return "LOOP 10"

        if ntype == "sub_script":
            name = p.get("script_name", "") or p.get("script_id", "")
            return f'CALL {name}'

        if ntype == "tool_call":
            # 将 tool_call 节点转换为 DSL（用于 Python 引擎路径）
            feature_name = p.get("feature_name", "")
            feature_params = p.get("params", {})
            path = feature_params.get("path", "") if isinstance(feature_params, dict) else ""
            if feature_name == "filedialog_save":
                return f'FILEDIALOG_SAVE "{path}"'
            elif feature_name == "filedialog_open":
                return f'FILEDIALOG_OPEN "{path}"'
            elif feature_name == "filedialog_folder":
                return f'FILEDIALOG_FOLDER "{path}"'
            return f"# TOOL_CALL: {feature_name}"

        # 文件对话框类型（来自 DSL→Flow 转换后的类型）
        if ntype == "filedialog_save":
            path = p.get("path") or p.get("_raw_args", "")
            path = path.strip('"').strip("'")
            return f'FILEDIALOG_SAVE "{path}"'
        if ntype == "filedialog_open":
            path = p.get("path") or p.get("_raw_args", "")
            path = path.strip('"').strip("'")
            return f'FILEDIALOG_OPEN "{path}"'
        if ntype == "filedialog_folder":
            path = p.get("path") or p.get("_raw_args", "")
            path = path.strip('"').strip("'")
            return f'FILEDIALOG_FOLDER "{path}"'

        if ntype == "web_browser":
            action = p.get("action", "navigate")
            url = p.get("url", "")
            selector = p.get("selector", "")
            text = p.get("text", "")
            wait_sec = p.get("wait_seconds", 1)
            timeout = p.get("timeout", 30)
            if action == "navigate":
                return f'BROWSER_NAV {url}'
            elif action == "click":
                return f'BROWSER_CLICK {selector}'
            elif action == "input_text":
                return f'BROWSER_TYPE {selector} "{text}"'
            elif action == "screenshot":
                return f'BROWSER_SCREENSHOT {selector}'
            elif action == "wait":
                return f'BROWSER_WAIT {selector} {timeout}'
            elif action == "extract":
                return f'BROWSER_TEXT {selector}'
            elif action == "scroll":
                return f'BROWSER_SCROLL {selector}'
            return f'BROWSER_NAV {url}'

        # 回退
        return f"# UNKNOWN: {ntype}"

    def _walk_graph(node_id: str, visited: set,
                    indent: int = 0) -> tuple[list[str], str | None]:
        """遍历图，生成 DSL 行列表。

        Returns:
            (lines, next_node_to_process)
        """
        if not node_id or node_id in visited:
            return [], None

        node = nodes_map.get(node_id)
        if not node:
            return [], None

        ntype = node["type"]
        if ntype == "end":
            return ["RETURN"], None

        visited.add(node_id)

        # 循环节点
        if ntype == "loop":
            body_edge = _get_first_edge(node_id, "loop_body")
            end_edge = _get_first_edge(node_id, "loop_end")

            lines = [_node_to_dsl(node)]
            indent += 1
            body_lines = []
            if body_edge and body_edge["to_node"] not in visited:
                bl, _ = _walk_graph(body_edge["to_node"], visited, indent)
                body_lines = bl
            indent -= 1
            lines.extend("  " * indent + l for l in body_lines)
            lines.append("  " * (indent) + "END")

            # 继续 loop_end
            if end_edge:
                rest, _ = _walk_graph(end_edge["to_node"], visited, indent)
                lines.extend(rest)
            return lines, None

        # 条件节点
        if ntype == "condition":
            yes_edge = _get_first_edge(node_id, "yes")
            no_edge = _get_first_edge(node_id, "no")

            lines = [_node_to_dsl(node)]
            indent += 1
            if yes_edge and yes_edge["to_node"] not in visited:
                yl, _ = _walk_graph(yes_edge["to_node"], visited, indent)
                lines.extend("  " * indent + l for l in yl)
            if no_edge and no_edge["to_node"] not in visited:
                lines.append("  " * (indent - 1) + "ELSE")
                nl, _ = _walk_graph(no_edge["to_node"], visited, indent)
                lines.extend("  " * indent + l for l in nl)
            indent -= 1
            lines.append("  " * indent + "END")

            # 条件结束后继续
            output_edge = _get_first_edge(node_id, "output")
            if output_edge and output_edge["to_node"] not in visited:
                rest, _ = _walk_graph(output_edge["to_node"], visited, indent)
                lines.extend(rest)
            return lines, None

        # 普通节点
        line = _node_to_dsl(node)
        if not line:
            # start 节点：继续遍历
            output_edge = _get_first_edge(node_id, "output")
            if output_edge:
                return _walk_graph(output_edge["to_node"], visited, indent)
            return [], None

        lines = [line]
        output_edge = _get_first_edge(node_id, "output")
        if output_edge and output_edge["to_node"] not in visited:
            rest, _ = _walk_graph(output_edge["to_node"], visited, indent)
            lines.extend(rest)

        return lines, None

    # 找 start 节点
    start_node = None
    for n in script.get("nodes", []):
        if n["type"] == "start":
            start_node = n
            break

    if not start_node:
        return "# 错误: 未找到 start 节点"

    visited: set[str] = set()
    lines, _ = _walk_graph(start_node["id"], visited)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 验证/测试
# ═══════════════════════════════════════════════════════════════

def roundtrip_verify(dsl_text: str) -> dict:
    """验证 DSL → FlowScript → DSL 往返是否语义等价。

    Returns:
        {"original": ..., "roundtrip": ..., "match": bool}
    """
    flow = dsl_to_flow(dsl_text)
    if not flow.get("nodes"):
        return {"original": dsl_text, "roundtrip": "", "match": False,
                "error": "dsl_to_flow 失败"}

    roundtrip = flow_to_dsl(flow)

    # 规范化后比较（去空行、去注释）
    def normalize(text: str) -> str:
        lines = []
        for l in text.strip().splitlines():
            l = l.strip()
            if l and not l.startswith("#"):
                lines.append(l)
        return "\n".join(lines)

    orig_norm = normalize(dsl_text)
    rt_norm = normalize(roundtrip)
    return {
        "original": dsl_text,
        "roundtrip": roundtrip,
        "match": orig_norm == rt_norm,
    }
