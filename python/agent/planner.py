"""
Planner — 任务规划器

将自然语言目标分解为 Plan DAG，支持依赖关系、回退方案、路径选择。

架构:
  goal → [Rule Classifier] → path_hint → [LLM Decomposer] → Plan DAG → [Executor]

Plan DAG:
  PlanNode 之间通过 depends_on 表达依赖，执行器按拓扑序执行。
  每个节点有 fallback_tools 和 fallback_paths，失败时自动降级。
"""

from __future__ import annotations
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger("agent.planner")


# ═══════════════════════════════════════════════════════════════
#  Plan DAG 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class PlanNode:
    """计划中的单个执行节点。"""
    id: str                          # 唯一标识 "step_1", "step_2a"
    description: str                 # 自然语言描述 "打开百度首页"
    tool: str                        # 工具名 "browser_navigate"
    params: dict = field(default_factory=dict)  # 参数 {"url": "https://baidu.com"}
    path_level: int = 0              # 推荐访问模式优先级 (0=DIRECT_API ~ 6=DSL_GEN)
    depends_on: list[str] = field(default_factory=list)  # 依赖的前序节点ID
    fallback_tools: list[str] = field(default_factory=list)   # 备选工具 ["click_target", ...]
    fallback_paths: list[int] = field(default_factory=list)   # 备选路径 [2, 3, 4]
    expected: str = ""               # 预期结果描述，用于验证
    critical: bool = False           # 关键步骤（失败则终止整个计划）
    timeout: int = 60                # 单步超时（秒）

    # 执行状态
    status: str = "pending"          # pending | running | completed | failed | skipped
    result: dict = field(default_factory=dict)
    attempts: int = 0
    max_retries: int = 2
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "PlanNode":
        return PlanNode(**{k: v for k, v in d.items() if k in PlanNode.__dataclass_fields__})


@dataclass
class Plan:
    """完整的执行计划 DAG。"""
    goal: str                        # 原始目标
    nodes: list[PlanNode] = field(default_factory=list)  # 所有节点
    status: str = "pending"          # pending | running | completed | failed
    created_at: float = 0.0
    context: dict = field(default_factory=dict)  # 额外上下文
    _node_map: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._rebuild_map()

    def _rebuild_map(self):
        self._node_map = {n.id: n for n in self.nodes}

    def get_node(self, node_id: str) -> Optional[PlanNode]:
        return self._node_map.get(node_id)

    def add_node(self, node: PlanNode):
        self.nodes.append(node)
        self._node_map[node.id] = node

    def get_ready_nodes(self) -> list[PlanNode]:
        """返回所有依赖已满足的待执行节点（拓扑序就绪）。"""
        completed_ids = {n.id for n in self.nodes if n.status == "completed"}
        skipped_ids = {n.id for n in self.nodes if n.status == "skipped"}
        done_ids = completed_ids | skipped_ids
        return sorted(
            [n for n in self.nodes if n.status == "pending"
             and all(dep in done_ids for dep in n.depends_on)],
            key=lambda n: (n.path_level, n.id)
        )

    def has_pending(self) -> bool:
        return any(n.status == "pending" for n in self.nodes)

    def has_running(self) -> bool:
        return any(n.status == "running" for n in self.nodes)

    def summary(self) -> dict:
        counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
        for n in self.nodes:
            counts[n.status] = counts.get(n.status, 0) + 1
        return {
            "goal": self.goal,
            "total": len(self.nodes),
            "status": self.status,
            **counts,
        }

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "nodes": [n.to_dict() for n in self.nodes],
            "summary": self.summary(),
        }


# ═══════════════════════════════════════════════════════════════
#  规划器
# ═══════════════════════════════════════════════════════════════

# 步骤类型 → 推荐工具、备选工具、备选路径
_STEP_TYPE_MAP: dict[str, dict] = {
    # 启动程序
    "launch": {
        "tool": "launch_program",
        "path": 0,
        "fallback_tools": ["run_command", "software_add_node"],
        "fallback_paths": [1, 2],
        "critical": True,
    },
    # 点击
    "click": {
        "tool": "click_target",
        "path": 2,  # 默认 UIA（比 OCR 快）
        "fallback_tools": ["a11y_click_element", "image_click"],
        "fallback_paths": [3, 4],
    },
    # 文字点击（通过文本定位）
    "text_click": {
        "tool": "click_target",
        "path": 3,  # OCR
        "fallback_tools": ["ocr_find_text"],
        "fallback_paths": [2, 4],
    },
    # 图片点击
    "image_click": {
        "tool": "find_image",
        "path": 4,  # SoM
        "fallback_tools": ["click_target", "image_click"],
        "fallback_paths": [2, 3],
    },
    # 输入文字
    "type": {
        "tool": "type_clipboard",
        "path": 0,
        "fallback_tools": ["type_text", "hotkey"],
        "fallback_paths": [2, 3],
    },
    # 快捷键
    "hotkey": {
        "tool": "hotkey",
        "path": 0,
        "fallback_tools": ["type_text", "type_clipboard"],
        "fallback_paths": [2, 3],
    },
    # 等待
    "wait": {
        "tool": "wait_action",
        "path": 0,
        "fallback_tools": ["wait_screen_stable", "wait_screen_change"],
        "fallback_paths": [3, 4],
    },
    # 截图
    "screenshot": {
        "tool": "screenshot",
        "path": 4,
        "fallback_tools": ["browser_screenshot", "screenshot_region_live"],
        "fallback_paths": [1],
    },
    # 浏览器导航
    "browser_navigate": {
        "tool": "browser_navigate",
        "path": 1,
        "fallback_tools": ["browser_connect", "browser_new_tab"],
        "fallback_paths": [3, 4],
    },
    "browser_click": {
        "tool": "browser_click",
        "path": 1,
        "fallback_tools": ["click_target", "browser_press_key"],
        "fallback_paths": [2, 3],
    },
    "browser_type": {
        "tool": "browser_type",
        "path": 1,
        "fallback_tools": ["type_clipboard", "browser_execute_js"],
        "fallback_paths": [0, 3],
    },
    "browser_find": {
        "tool": "browser_find_elements",
        "path": 1,
        "fallback_tools": ["ocr_find_text", "find_element"],
        "fallback_paths": [3, 4],
    },
    # OCR
    "ocr_extract": {
        "tool": "ocr_extract_all",
        "path": 3,
        "fallback_tools": ["vlm_analyze"],
        "fallback_paths": [4, 6],
    },
    # 文件操作
    "file_read": {
        "tool": "file_read",
        "path": 0,
        "fallback_tools": ["run_command"],
        "fallback_paths": [2],
    },
    "file_write": {
        "tool": "file_write",
        "path": 0,
        "fallback_tools": ["file_edit", "file_append"],
        "fallback_paths": [2],
    },
    "file_save": {
        "tool": "file_write",
        "path": 0,
        "fallback_tools": ["run_command"],
        "fallback_paths": [2],
    },
    # 命令执行
    "run_command": {
        "tool": "run_command",
        "path": 0,
        "fallback_tools": ["launch_program"],
        "fallback_paths": [2],
    },
    # 窗口操作
    "focus": {
        "tool": "focus_window",
        "path": 0,
        "fallback_tools": ["list_windows", "a11y_get_tree"],
        "fallback_paths": [2, 3],
    },
    # 变量
    "set_variable": {
        "tool": "set_variable",
        "path": 0,
        "fallback_tools": [],
        "fallback_paths": [],
    },
    "get_variable": {
        "tool": "get_variable",
        "path": 0,
        "fallback_tools": [],
        "fallback_paths": [],
    },
    # 条件
    "condition": {
        "tool": "__condition__",
        "path": 0,
        "fallback_tools": [],
        "fallback_paths": [],
    },
    "check_condition": {
        "tool": "__check_condition__",
        "path": 0,
        "fallback_tools": [],
        "fallback_paths": [],
    },
    "wait_until": {
        "tool": "__wait_until__",
        "path": 0,
        "fallback_tools": ["wait_action"],
        "fallback_paths": [],
    },
    # 剪贴板
    "clipboard_set": {
        "tool": "clipboard_set_text",
        "path": 0,
        "fallback_tools": ["type_clipboard"],
        "fallback_paths": [2, 3],
    },
    "clipboard_get": {
        "tool": "clipboard_get_text",
        "path": 0,
        "fallback_tools": ["hotkey"],
        "fallback_paths": [2],
    },
    # DSL 生成
    "generate_flow_script": {
        "tool": "generate_flow_script",
        "path": 6,
        "fallback_tools": ["ai_plan_and_execute"],
        "fallback_paths": [6],
    },
}


def _get_step_mapping(step_type: str) -> dict:
    """获取步骤类型的工具映射，未知类型返回默认值。"""
    base = _STEP_TYPE_MAP.get(step_type, {
        "tool": step_type,
        "path": 0,
        "fallback_tools": [],
        "fallback_paths": [],
    })
    return dict(base)


def _build_params_from_step(step_type: str, step: dict) -> dict:
    """从结构化步骤字典提取工具参数。"""
    params = {}
    step_type = step_type.lower()

    if step_type in ("click", "text_click"):
        target = step.get("target", "")
        import re
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target):
            parts = target.split(",")
            params = {"x": int(parts[0]), "y": int(parts[1])}
        else:
            params = {"target": target}
    elif step_type == "image_click":
        params = {"target": step.get("target", ""), "similarity": 0.8}
    elif step_type == "type":
        params = {"text": step.get("text", "")}
    elif step_type == "hotkey":
        keys = step.get("keys", step.get("target", ""))
        params = {"keys": "+".join(keys) if isinstance(keys, list) else str(keys)}
    elif step_type == "wait":
        params = {"duration": step.get("duration", 1)}
    elif step_type in ("launch", "run_command", "file_read", "file_write", "file_save"):
        params = {"target": step.get("target", "")} if step_type == "launch" else {}
        if step_type == "launch":
            params = {"target": step.get("target", "")}
        elif step_type == "run_command":
            params = {"command": step.get("command", "")}
        elif step_type in ("file_read", "file_write", "file_save"):
            params = {"path": step.get("path", step.get("target", ""))}
            if step.get("content"):
                params["content"] = step["content"]
    elif step_type == "focus":
        params = {"title": step.get("title", step.get("target", ""))}
    elif step_type == "screenshot":
        params = {}
    elif step_type in ("browser_navigate", "browser_click", "browser_type",
                       "browser_find", "browser_wait_element", "browser_get_text"):
        params = {}
        if step_type == "browser_navigate":
            params["url"] = step.get("url", step.get("target", ""))
        elif step_type == "browser_click":
            params["selector"] = step.get("selector", step.get("target", ""))
        elif step_type in ("browser_type",):
            params["selector"] = step.get("selector", step.get("target", ""))
            params["text"] = step.get("text", "")
        elif step_type in ("browser_find", "browser_get_text"):
            params["selector"] = step.get("selector", step.get("target", ""))
        elif step_type == "browser_wait_element":
            params["selector"] = step.get("selector", step.get("target", ""))
            params["timeout"] = step.get("timeout", 10)
    elif step_type.startswith("browser_"):
        # 通用兜底：把除 url/selector/text 外的字段当参数
        for k in ("url", "selector", "text", "code", "port", "timeout", "index"):
            if k in step:
                params[k] = step[k]
    elif step_type in ("ocr_extract",):
        params = {"region": step.get("region", "")}
    elif step_type in ("clipboard_set",):
        params = {"text": step.get("text", step.get("content", ""))}
    elif step_type in ("clipboard_get",):
        params = {}
    elif step_type in ("check_condition", "wait_until"):
        params = {"condition": step.get("condition", ""), "timeout": step.get("timeout", 10)}
    elif step_type in ("set_variable", "get_variable"):
        params = {"name": step.get("name", ""), "value": step.get("value", "")}
    elif step_type == "condition":
        params = {
            "condition": step.get("condition", ""),
            "true_branch": step.get("true_branch", []),
            "false_branch": step.get("false_branch", []),
        }
    else:
        # 通用：直接透传
        for k in ("target", "text", "url", "path", "command", "keys", "name", "value", "timeout"):
            if k in step:
                params[k] = step[k]

    return params


class Planner:
    """任务规划器：将自然语言目标转换为 Plan DAG。

    用法:
        planner = Planner()
        plan = planner.plan("打开百度搜索Python")
        # plan.nodes 包含完整的执行计划
    """

    def __init__(self, api_key: str = "", api_base: str = "", model: str = ""):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self._gfs_available = False

        # 检查 generate_flow_script 是否可用
        try:
            from features.flow_features import generate_flow_script
            self._gfs_available = True
        except ImportError:
            pass

    def _apply_path_classification(self, plan: "Plan", goal: str):
        """规则分类目标 → path_hint，存入 plan.context。"""
        try:
            from agent.tool_path_selector import select_best_path
            suggested = select_best_path(goal)
            if suggested is not None:
                plan.context["path_hint"] = suggested.label_cn
                plan.context["path_level"] = suggested.value
            return suggested
        except Exception:
            return None

    def _steps_to_nodes(self, plan: "Plan", steps: list[dict], offset: int = 0) -> "Plan":
        """将结构化步骤列表转换为 PlanNodes 并添加到 plan。"""
        for i, step in enumerate(steps):
            step_type = step.get("type", "")
            step_label = (step.get("target") or step.get("text")
                          or step.get("title") or step.get("name") or step_type)
            mapping = _get_step_mapping(step_type)
            params = _build_params_from_step(step_type, step)

            step_path = step.get("path_level")
            path_level = step_path if isinstance(step_path, int) and 0 <= step_path <= 6 else mapping["path"]

            step_deps = step.get("depends_on")
            if step_deps and isinstance(step_deps, list):
                depends_on = [f"step_{j}" for j in step_deps if isinstance(j, int) and j >= 1]
            elif (offset + i) > 0:
                depends_on = [f"step_{offset + i}"]
            else:
                depends_on = []

            plan.add_node(PlanNode(
                id=f"step_{offset + i + 1}",
                description=step.get("pseudo", f"{step_type}: {step_label}"),
                tool=mapping["tool"],
                params=params,
                path_level=path_level,
                depends_on=depends_on,
                fallback_tools=mapping["fallback_tools"],
                fallback_paths=mapping["fallback_paths"],
                expected=step.get("expected", ""),
                critical=mapping.get("critical", False),
                timeout=step.get("timeout", 60),
            ))
        return plan

    def plan_from_steps(self, goal: str, steps: list[dict], context: Optional[dict] = None) -> "Plan":
        """从预生成的结构化步骤创建 Plan（不调用 LLM）。"""
        start = time.time()
        plan = Plan(goal=goal, created_at=start, context=context or {})
        self._apply_path_classification(plan, goal)
        self._steps_to_nodes(plan, steps, offset=0)
        logger.info("Plan created from %d pre-generated steps in %.2fs", len(plan.nodes), time.time() - start)
        return plan

    def plan(self, goal: str, context: Optional[dict] = None) -> "Plan":
        """规划：分类目标 → 生成步骤 → 构建 Plan DAG。"""
        start = time.time()
        plan = Plan(goal=goal, created_at=start, context=context or {})

        # ── Phase 1: 规则分类 ──
        path_hint = self._apply_path_classification(plan, goal)
        logger.info("Planning: goal=%s, path_hint=%s",
                     goal[:60], plan.context.get("path_hint", "auto"))

        # ── Phase 2: LLM 分解（复用 generate_flow_script）──
        structured_steps = self._generate_steps(goal, path_hint)

        if not structured_steps:
            logger.warning("No steps generated for goal, creating minimal plan")
            plan.add_node(PlanNode(
                id="step_1",
                description=f"执行: {goal[:100]}",
                tool="__agent_decide__",
                params={"goal": goal},
                path_level=plan.context.get("path_level", 0),
                fallback_paths=[2, 3, 4],
                critical=True,
            ))
            return plan

        # ── Phase 3: 结构化步骤 → PlanNode ──
        self._steps_to_nodes(plan, structured_steps, offset=0)
        logger.info("Plan created: %d nodes in %.2fs", len(plan.nodes), time.time() - start)
        return plan

    def _generate_steps(self, goal: str, path_hint) -> list[dict]:
        """生成结构化步骤列表。

        优先使用 generate_flow_script（项目内置的 LLM 分解），
        不可用时返回空列表。
        """
        if not self._gfs_available:
            logger.warning("generate_flow_script not available, skipping LLM decomposition")
            return []

        try:
            from features.flow_features import generate_flow_script

            # 将路径提示注入目标前缀
            enhanced_goal = goal
            if path_hint is not None:
                enhanced_goal = f"[路径: {path_hint.label_cn}] {goal}"

            result = generate_flow_script(enhanced_goal)
            if not result.get("success"):
                logger.warning("generate_flow_script failed: %s", result.get("error", "unknown"))
                return []

            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                steps = result_data.get("structured_steps", [])
            elif isinstance(result_data, list):
                steps = result_data
            else:
                steps = []

            if not steps and isinstance(result_data, dict):
                # 尝试嵌套取值
                inner = result_data.get("result", {})
                if isinstance(inner, dict):
                    steps = inner.get("structured_steps", [])

            logger.info("Generated %d structured steps", len(steps))
            return steps if isinstance(steps, list) else []

        except Exception as e:
            logger.warning("generate_flow_script error: %s", e)
            return []


# ═══════════════════════════════════════════════════════════════
#  PlanExecutor — Plan DAG 执行器
# ═══════════════════════════════════════════════════════════════

class PlanExecutor:
    """Plan DAG 执行器。

    串行执行就绪节点，带 verify + fallback + replan 循环。

    用法:
        executor = PlanExecutor(agent, plan)
        result = executor.execute()
    """

    def __init__(self, agent, plan: Plan,
                 emit_event=None,
                 stop_event=None,
                 log_func=None):
        self.agent = agent
        self.plan = plan
        self._emit = emit_event or (lambda e, d: None)
        self._stop = stop_event
        self._log = log_func or (lambda lv, msg: None)

    def execute(self) -> dict:
        """执行 Plan DAG，返回执行结果。"""
        self.plan.status = "running"
        self._emit("plan_state", {"plan": self.plan.to_dict()})
        self._log("info", f"开始执行计划: {self.plan.goal[:60]} ({len(self.plan.nodes)} 步)")

        start = time.time()

        while self.plan.has_pending():
            if self._stop and self._stop.is_set():
                self._log("info", "停止请求，终止计划执行")
                self.plan.status = "stopped"
                break

            ready = self.plan.get_ready_nodes()
            if not ready and self.plan.has_pending():
                # 有节点待执行但没有就绪的 → 依赖环
                blocked = [n.id for n in self.plan.nodes if n.status == "pending"]
                self._log("error", f"依赖阻塞: {blocked} — 标记为 failed")
                for n in self.plan.nodes:
                    if n.status == "pending":
                        n.status = "failed"
                        n.error = "dependency blocked"
                self.plan.status = "failed"
                break

            for node in ready:
                if self._stop and self._stop.is_set():
                    break
                self._execute_node(node)
                self._emit("plan_state", {"plan": self.plan.to_dict()})

            # 检查是否所有节点都完成或被跳过
            all_terminal = all(
                n.status in ("completed", "failed", "skipped")
                for n in self.plan.nodes
            )
            terminal_statuses = {n.status for n in self.plan.nodes}
            if all_terminal:
                has_failure = "failed" in terminal_statuses
                self.plan.status = "completed" if not has_failure else "completed_with_errors"

        elapsed = time.time() - start
        summary = self.plan.summary()
        self._log("info",
                   f"计划执行完毕: {summary['completed']}/{summary['total']} 成功, "
                   f"{summary['failed']} 失败, {summary['skipped']} 跳过 ({elapsed:.1f}s)")

        result = {
            "success": summary.get("failed", 0) == 0,
            "plan": self.plan.to_dict(),
            "summary": summary,
            "elapsed_ms": round(elapsed * 1000),
        }
        self._emit("plan_state", {"plan": self.plan.to_dict(), "final": True, "result": result})
        return result

    def _emit_step(self, node: PlanNode, status: str = "running", result: dict = None):
        """发射 step_complete IPC 事件（前端进度展示兼容）。"""
        try:
            step_num = int(node.id.split("_")[-1])
        except (ValueError, IndexError):
            step_num = 0
        self._emit("step_complete", {
            "step_num": step_num,
            "tool": node.tool,
            "params": node.params,
            "status": status,
            "result": result or {},
        })

    def _execute_node(self, node: PlanNode) -> None:
        """执行单个节点（含重试 + fallback）。"""
        node.status = "running"
        node.attempts += 1
        self._log("info", f"[{node.id}] {node.description} — tool={node.tool}")
        self._emit_step(node, "running")

        # ── 特殊节点处理 ──
        if node.tool == "__agent_decide__":
            # 让 LLM 自行决策（兜底）
            try:
                result = self.agent.run_text_protocol(node.params.get("goal", ""))
                node.result = result
                node.status = "completed" if result.get("success") else "failed"
                if not result.get("success"):
                    node.error = result.get("summary", "agent decision failed")
            except Exception as e:
                node.result = {"success": False, "error": str(e)}
                node.status = "failed"
                node.error = str(e)
            self._emit_step(node, node.status, node.result)
            return

        if node.tool == "__condition__":
            self._execute_condition_node(node)
            self._emit_step(node, node.status, node.result)
            return

        if node.tool in ("__check_condition__", "__wait_until__"):
            self._execute_special_step(node)
            self._emit_step(node, node.status, node.result)
            return

        # ── 正常工具执行 ──
        # 注意: _supervised_execute 内部已有重试逻辑（GUI 2次, 其他1次），
        # PlanExecutor 层不再额外重试，只负责 fallback 链。
        try:
            result = self.agent.supervisor.execute(node.tool, node.params)
            node.result = result

            if result.get("success", False):
                node.status = "completed"
                self._log("info", f"[{node.id}] ✓ 成功 ({result.get('result', '')})")
                self._emit_step(node, "completed", result)
                return
            else:
                node.error = result.get("error", "unknown error")
                self._log("warn",
                           f"[{node.id}] ✗ 失败: {node.error[:100]}")

        except Exception as e:
            node.result = {"success": False, "error": str(e)}
            node.error = str(e)
            self._log("error", f"[{node.id}] ✗ 异常: {str(e)[:100]}")

        # ── 所有重试失败 → 尝试 fallback 链 ──
        if node.fallback_tools:
            for i, fb_tool in enumerate(node.fallback_tools):
                if self._stop and self._stop.is_set():
                    break

                fb_path = node.fallback_paths[i] if i < len(node.fallback_paths) else node.path_level
                self._log("info",
                           f"[{node.id}] fallback {i + 1}/{len(node.fallback_tools)}: {fb_tool} (L{fb_path})")
                self._emit("step_attempt", {
                    "node_id": node.id,
                    "attempt": f"fallback_{i + 1}",
                    "tool": fb_tool,
                    "path_level": fb_path,
                })

                try:
                    fb_params = self._adapt_params_for_tool(node.params, node.tool, fb_tool)
                    result = self.agent.supervisor.execute(fb_tool, fb_params)
                    node.result = result

                    if result.get("success", False):
                        node.status = "completed"
                        node.tool = fb_tool  # 记录实际使用的工具
                        self._log("info", f"[{node.id}] fallback ✓ {fb_tool} 成功")
                        self._emit_step(node, "completed", result)
                        return
                except Exception as e:
                    self._log("warn",
                               f"[{node.id}] fallback {fb_tool} 异常: {str(e)[:100]}")

        # ── 最终状态 ──
        if node.status != "completed":
            if node.critical:
                node.status = "failed"
                self._log("error", f"[{node.id}] 关键步骤失败，终止计划")
                self.plan.status = "failed"
            else:
                node.status = "skipped"
                self._log("warn",
                           f"[{node.id}] 非关键步骤失败，跳过 ({node.tool} fallback 均失败)")

        self._emit_step(node, node.status, node.result)

    @staticmethod
    def _adapt_params_for_tool(original_params: dict, from_tool: str, to_tool: str) -> dict:
        """在 fallback 工具间适配参数。"""
        if to_tool in ("click_target", "a11y_click_element", "image_click"):
            # 如果是点击类 fallback，尽量提供 target
            if "target" in original_params:
                return {"target": original_params["target"]}
            if "x" in original_params and "y" in original_params:
                return {"target": f"{original_params['x']},{original_params['y']}"}
            return original_params

        if to_tool in ("ocr_find_text",):
            if "target" in original_params:
                return {"query": original_params["target"]}
            return original_params

        if to_tool == "type_text" and from_tool in ("type_clipboard",):
            return original_params

        if to_tool == "hotkey" and from_tool in ("type_text", "type_clipboard"):
            text = original_params.get("text", original_params.get("target", ""))
            if len(text) <= 3:
                return {"keys": text}
            return original_params

        return original_params

    def _execute_special_step(self, node: PlanNode):
        """执行特殊步骤（check_condition / wait_until）。"""
        try:
            from features.action_map import _eval_condition
            condition = node.params.get("condition", "")
            timeout = node.params.get("timeout", 10)

            if node.tool == "__check_condition__":
                ok = _eval_condition(condition)
                node.status = "completed" if ok else "failed"
                node.result = {"success": ok, "condition": condition}

            elif node.tool == "__wait_until__":
                import time
                deadline = time.time() + timeout
                ok = False
                while time.time() < deadline:
                    if _eval_condition(condition):
                        ok = True
                        break
                    time.sleep(0.5)
                node.status = "completed" if ok else "failed"
                node.result = {"success": ok, "condition": condition, "timeout": timeout}

        except Exception as e:
            node.status = "failed"
            node.result = {"success": False, "error": str(e)}
            node.error = str(e)

    def _execute_condition_node(self, node: PlanNode):
        """执行条件分支节点（if/else）。"""
        try:
            cond_text = node.params.get("condition", "")
            true_branch = node.params.get("true_branch", [])
            false_branch = node.params.get("false_branch", [])

            # 评估条件
            cond_met = False
            try:
                from features.registry import registry
                if "屏幕" in cond_text and "变化" in cond_text:
                    r = registry.execute("wait_screen_change", timeout=3)
                    cond_met = bool(r.get("result", {}).get("matched", False))
                else:
                    from features.action_map import _eval_condition
                    cond_met = _eval_condition(cond_text)
            except Exception:
                cond_met = False

            branch = true_branch if cond_met else false_branch
            self._log("info",
                       f"条件 '{cond_text}' = {cond_met}, {len(branch)} 个分支步骤")

            # 执行分支步骤
            branch_errors = 0
            for i, bs in enumerate(branch):
                bs_type = bs.get("type", "")
                mapping = _get_step_mapping(bs_type)
                bs_params = _build_params_from_step(bs_type, bs)

                try:
                    r = self.agent.supervisor.execute(mapping["tool"], bs_params)
                    if not r.get("success", False):
                        branch_errors += 1
                except Exception:
                    branch_errors += 1

            node.status = "completed"
            node.result = {
                "success": True,
                "condition": cond_text,
                "cond_met": cond_met,
                "branch_errors": branch_errors,
            }

        except Exception as e:
            node.status = "failed"
            node.result = {"success": False, "error": str(e)}
            node.error = str(e)
