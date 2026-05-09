"""
执行层: 状态机驱动的 DSL 执行器
支持: 暂停/恢复/停止, 循环, 条件, 子流程, 事件回调
      子程序(CALL/SUBROUTINE), IMPORT, RETURN, 变量插值
"""

from __future__ import annotations
import os
import re
import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Callable, Dict, Optional, Any
from dataclasses import dataclass, field

from engine.dsl_parser import ASTNode, NodeType, DSLParser


class ExecutorState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    FINISHED = "finished"


@dataclass
class ExecutionContext:
    variables: Dict[str, Any] = field(default_factory=dict)
    loop_count: Dict[str, int] = field(default_factory=dict)
    max_loops: int = 9999
    retry_limit: int = 3
    timeout: float = 30.0
    max_call_depth: int = 50


class ExecutionError(Exception):
    pass


class ReturnSignal(Exception):
    """从子程序返回的信号，携带返回值"""
    def __init__(self, value: Any = None):
        self.value = value
        super().__init__()


class DSLExecutor:
    """
    状态机执行器
    事件系统: on_node_start, on_node_done, on_error, on_state_change
    """

    def __init__(self, action_handler=None):
        self._state = ExecutorState.IDLE
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._action_handler = action_handler
        self._ctx: Optional[ExecutionContext] = None  # 当前执行上下文引用

        # 层次化 DSL 支持
        self._subroutines: dict[str, ASTNode] = {}
        self._scope_stack: list[dict[str, Any]] = [{}]  # 作用域链
        self._call_depth = 0

        # 线程安全
        self._action_lock = threading.Lock()

        # 异质执行：handler 注册表
        self._handlers: dict[str, Any] = {"default": self._action_handler}

        # 事件回调
        self._callbacks: Dict[str, list[Callable]] = {
            "node_start": [],
            "node_done": [],
            "error": [],
            "state_change": [],
            "log": [],
        }

    # ── 作用域管理 ────────────────────────────────────────────────
    def _current_scope(self) -> dict:
        return self._scope_stack[-1]

    def _push_scope(self, bindings: Optional[dict] = None):
        self._scope_stack.append(bindings or {})

    def _pop_scope(self) -> dict:
        return self._scope_stack.pop()

    def _resolve_var(self, name: str) -> Optional[Any]:
        for scope in reversed(self._scope_stack):
            if name in scope:
                return scope[name]
        return None

    def _set_var(self, name: str, value: Any):
        """设置变量（在当前作用域），同步到 ctx.variables 供 ActionHandler 访问"""
        self._current_scope()[name] = value
        if self._ctx is not None:
            self._ctx.variables[name] = value

    def _interpolate(self, text: str) -> str:
        """替换 {var} 为变量值，支持 {var + N} {var - N} 等内联算术"""
        def replacer(m):
            var_name = m.group(1)
            val = self._resolve_var(var_name)
            if val is not None:
                # 处理内联算术: {var + 10} 或 {var - 5}
                if m.lastindex and m.lastindex >= 2 and m.group(2) and m.group(3):
                    num = int(m.group(3))
                    ival = int(val)
                    return str(ival + num if m.group(2) == "+" else ival - num)
                return str(val)
            return m.group(0)  # 保留未解析的占位符
        # 先匹配带算术的 {var + N} / {var - N}，再匹配简单 {var}
        result = re.sub(r"\{(\w+)\s*([\+\-])\s*(\d+)\}", replacer, text)
        result = re.sub(r"\{(\w+)\}", replacer, result)
        return result

    # ── 公开控制 API ─────────────────────────────────────────────
    def run_dsl(self, source: str, ctx: Optional[ExecutionContext] = None):
        """解析并异步执行 DSL"""
        if self._state == ExecutorState.RUNNING:
            raise ExecutionError("执行器已在运行中")
        if self._state in (ExecutorState.ERROR, ExecutorState.FINISHED):
            self._reset_state_no_run()
        try:
            parser = DSLParser()
            ast = parser.parse(source)
        except Exception as e:
            return {"success": False, "error": str(e)}
        self._subroutines = parser.subroutines
        ctx = ctx or ExecutionContext()
        self._reset_state(ctx)
        self._thread = threading.Thread(
            target=self._run_thread, args=(ast, ctx), daemon=True
        )
        self._thread.start()
        return {"success": True, "state": self._state.value, "thread": True}

    def run_dsl_sync(self, source: str, ctx: Optional[ExecutionContext] = None):
        """同步执行 DSL（阻塞），返回结果 dict"""
        if self._state in (ExecutorState.ERROR, ExecutorState.FINISHED):
            self._reset_state_no_run()
        try:
            parser = DSLParser()
            ast = parser.parse(source)
        except Exception as e:
            return {"success": False, "error": f"DSL 解析失败: {e}"}
        self._subroutines = parser.subroutines
        ctx = ctx or ExecutionContext()
        self._reset_state(ctx)
        try:
            self._execute_node(ast, ctx)
            if not self._stop_flag.is_set():
                self._set_state(ExecutorState.FINISHED)
            return {"success": True, "state": self._state.value}
        except ReturnSignal:
            if not self._stop_flag.is_set():
                self._set_state(ExecutorState.FINISHED)
            return {"success": True, "state": self._state.value}
        except Exception as e:
            self._set_state(ExecutorState.ERROR)
            return {"success": False, "error": str(e)}

    def _reset_state(self, ctx: ExecutionContext):
        self._stop_flag.clear()
        self._pause_event.set()
        self._scope_stack = [dict(ctx.variables)]
        self._call_depth = 0
        self._ctx = ctx
        self._set_state(ExecutorState.RUNNING)

    def _reset_state_no_run(self):
        """从 ERROR/FINISHED 重置回 IDLE，不清除 scope"""
        self._stop_flag.clear()
        self._pause_event.set()
        self._set_state(ExecutorState.IDLE)

    def reset(self):
        """公开 API：将执行器重置为 IDLE 状态，清除作用域"""
        self._stop_flag.clear()
        self._pause_event.set()
        self._scope_stack = [{}]
        self._call_depth = 0
        self._set_state(ExecutorState.IDLE)

    def pause(self):
        if self._state == ExecutorState.RUNNING:
            self._pause_event.clear()
            self._set_state(ExecutorState.PAUSED)
        return self._state.value

    def resume(self):
        if self._state == ExecutorState.PAUSED:
            self._pause_event.set()
            self._set_state(ExecutorState.RUNNING)
        return self._state.value

    def stop(self):
        self._stop_flag.set()
        self._pause_event.set()
        self._set_state(ExecutorState.STOPPED)
        return self._state.value

    @property
    def state(self) -> ExecutorState:
        return self._state

    # ── 事件注册 ─────────────────────────────────────────────────
    def on(self, event: str, callback: Callable):
        self._callbacks[event].append(callback)

    def register_handler(self, name: str, handler: Callable):
        """注册异质执行 handler，可在 WITH <name> 中使用"""
        self._handlers[name] = handler

    def _emit(self, event: str, **kwargs):
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception as e:
                self._log(f"  [emit] {event} callback error: {e}")

    # ── 内部执行 ─────────────────────────────────────────────────
    def _run_thread(self, ast: ASTNode, ctx: ExecutionContext):
        try:
            self._execute_node(ast, ctx)
            if not self._stop_flag.is_set():
                self._set_state(ExecutorState.FINISHED)
        except ReturnSignal:
            # 顶层 RETURN 忽略
            if not self._stop_flag.is_set():
                self._set_state(ExecutorState.FINISHED)
        except Exception as e:
            self._emit("error", message=str(e), traceback=traceback.format_exc())
            self._set_state(ExecutorState.ERROR)

    def _check_control(self):
        self._pause_event.wait()
        if self._stop_flag.is_set():
            raise ExecutionError("执行已被用户停止")

    def _execute_node(self, node: ASTNode, ctx: ExecutionContext):
        self._check_control()

        if node.type == NodeType.SEQUENCE:
            for child in node.children:
                self._execute_node(child, ctx)

        elif node.type == NodeType.CLICK:
            self._exec_click(node, ctx)

        elif node.type == NodeType.TYPE:
            self._exec_type(node, ctx)

        elif node.type == NodeType.LAUNCH:
            self._exec_launch(node, ctx)

        elif node.type == NodeType.WAIT:
            self._exec_wait(node, ctx)

        elif node.type == NodeType.LOOP:
            self._exec_loop(node, ctx)

        elif node.type == NodeType.IF:
            self._exec_if(node, ctx)

        elif node.type == NodeType.SWIPE:
            self._exec_swipe(node, ctx)

        elif node.type == NodeType.SCROLL:
            self._exec_scroll(node, ctx)

        elif node.type == NodeType.DELAY:
            self._exec_delay(node, ctx)

        elif node.type == NodeType.WAIT_RANDOM:
            self._exec_wait_random(node, ctx)

        elif node.type == NodeType.RUN:
            self._exec_run(node, ctx)

        elif node.type == NodeType.CALL:
            self._exec_call(node, ctx)

        elif node.type == NodeType.IMPORT:
            self._exec_import(node, ctx)

        elif node.type == NodeType.BREAK:
            raise BreakLoop()

        elif node.type == NodeType.RETURN:
            raise ReturnSignal(node.args)

        elif node.type == NodeType.WHEN:
            self._exec_when(node, ctx)

        elif node.type == NodeType.PARALLEL:
            self._exec_parallel(node, ctx)

        elif node.type == NodeType.WITH:
            self._exec_with(node, ctx)

        elif node.type == NodeType.SCREENSHOT:
            self._exec_screenshot(node, ctx)

        elif node.type == NodeType.WAITSCREEN:
            self._exec_waitscreen(node, ctx)

        elif node.type == NodeType.SCREENSTABLE:
            self._exec_screenstable(node, ctx)

        elif node.type == NodeType.HOTKEY:
            self._exec_hotkey(node, ctx)

        elif node.type == NodeType.FOCUS:
            self._exec_focus(node, ctx)

        elif node.type == NodeType.SET:
            self._exec_set(node, ctx)

        elif node.type == NodeType.VARIABLE_INC:
            self._exec_variable_inc(node, ctx)

        elif node.type == NodeType.WAIT_FILE:
            self._exec_wait_file(node, ctx)

        elif node.type == NodeType.OCR_FIND:
            self._exec_ocr_find(node, ctx)

        elif node.type == NodeType.OCR_EXTRACT:
            self._exec_ocr_extract(node, ctx)

        elif node.type == NodeType.REGION_SELECT:
            self._exec_region_select(node, ctx)

        elif node.type == NodeType.IMAGE_FIND:
            self._exec_image_find(node, ctx)

        elif node.type == NodeType.IMAGE_CLICK:
            self._exec_image_click(node, ctx)

        elif node.type == NodeType.TEMPLATE_CAPTURE:
            self._exec_template_capture(node, ctx)

        elif node.type == NodeType.PROGRESS_WAIT:
            self._exec_progress_wait(node, ctx)
        elif node.type == NodeType.PROGRESS_INIT:
            self._exec_progress_init(node, ctx)
        elif node.type == NodeType.PROGRESS_RELOCATE:
            self._exec_progress_relocate(node, ctx)

        elif node.type == NodeType.POPUP_CLOSE:
            self._exec_popup_close(node, ctx)
        elif node.type == NodeType.COLOR_CHECK:
            self._exec_color_check(node, ctx)
        elif node.type == NodeType.REGION_WAIT:
            self._exec_region_wait(node, ctx)
        elif node.type == NodeType.SCENE_CONDITION:
            self._exec_scene_condition(node, ctx)
        elif node.type == NodeType.FINDCANVAS:
            self._exec_findcanvas(node, ctx)

        elif node.type == NodeType.DRAG:
            self._exec_drag(node, ctx)

        elif node.type == NodeType.VLM_CONDITION:
            self._exec_vlm_condition(node, ctx)

        elif node.type == NodeType.SHUTDOWN:
            self._exec_shutdown(node, ctx)
        elif node.type == NodeType.REBOOT:
            self._exec_reboot(node, ctx)
        elif node.type == NodeType.LOGOFF:
            self._exec_logoff(node, ctx)
        elif node.type == NodeType.HIBERNATE:
            self._exec_hibernate(node, ctx)

        # ── 扩展系统指令 ──
        elif node.type == NodeType.LOCK:
            self._exec_lock(node, ctx)
        elif node.type == NodeType.VOLUME:
            self._exec_volume(node, ctx)
        elif node.type == NodeType.MUTE:
            self._exec_mute(node, ctx)
        elif node.type == NodeType.KILL:
            self._exec_kill(node, ctx)
        elif node.type == NodeType.WAIT_PROCESS:
            self._exec_wait_process(node, ctx)
        elif node.type == NodeType.WAIT_EXIT:
            self._exec_wait_exit(node, ctx)
        elif node.type == NodeType.WALLPAPER:
            self._exec_wallpaper(node, ctx)
        elif node.type == NodeType.DISPLAY_OFF:
            self._exec_display_off(node, ctx)
        elif node.type == NodeType.SLEEP:
            self._exec_sleep(node, ctx)
        elif node.type == NodeType.DESKTOP:
            self._exec_desktop(node, ctx)
        elif node.type == NodeType.BATTERY:
            self._exec_battery(node, ctx)
        elif node.type == NodeType.EMPTY_BIN:
            self._exec_empty_bin(node, ctx)
        elif node.type == NodeType.BRIGHTNESS:
            self._exec_brightness(node, ctx)
        elif node.type == NodeType.CLIPBOARD_SET:
            self._exec_clipboard_set(node, ctx)
        elif node.type == NodeType.CLIPBOARD_GET:
            self._exec_clipboard_get(node, ctx)
        elif node.type in (NodeType.MINIMIZE, NodeType.MAXIMIZE, NodeType.CLOSE_WINDOW, NodeType.RESIZE_WINDOW):
            self._exec_window_control(node, ctx)

        # SUBROUTINE 定义直接跳过（已在 parser 注册）

    # ── 指令执行 ─────────────────────────────────────────────────
    def _exec_click(self, node: ASTNode, ctx: ExecutionContext):
        raw = node.args
        button = "left"
        if raw.upper().endswith(" RIGHT"):
            button = "right"
            raw = raw[:-6]
        elif raw.upper().endswith(" R"):
            button = "right"
            raw = raw[:-2]
        target = self._interpolate(raw)
        self._emit("node_start", node_type="CLICK", args=target, line=node.line)
        self._log(f"CLICK ({button}): {target}")
        self._call_action("click", target=target, button=button, ctx=ctx)
        self._emit("node_done", node_type="CLICK", args=target, line=node.line)

    def _exec_drag(self, node: ASTNode, ctx: ExecutionContext):
        """DRAG from_x from_y TO to_x to_y — 从起点拖拽到终点"""
        raw = self._interpolate(node.args)
        # 支持 "x1 y1 TO x2 y2" 和 "x1,y1 TO x2,y2"
        import re
        parts = re.split(r"[\s,]+", raw.strip())
        self._emit("node_start", node_type="DRAG", args=raw, line=node.line)
        self._log(f"DRAG: {raw}")
        # 查找 TO 分隔
        try:
            to_idx = None
            for i, p in enumerate(parts):
                if p.upper() == "TO":
                    to_idx = i
                    break
            if to_idx and to_idx >= 2 and len(parts) > to_idx + 2:
                x1, y1 = int(parts[0]), int(parts[1])
                x2, y2 = int(parts[to_idx+1]), int(parts[to_idx+2])
                self._call_action("drag", from_target=f"{x1},{y1}",
                    to_target=f"{x2},{y2}", ctx=ctx)
            else:
                self._log(f"  DRAG 格式错误: {raw}")
        except (ValueError, IndexError):
            self._log(f"  DRAG 格式错误: {raw}")
        self._emit("node_done", node_type="DRAG", args=raw, line=node.line)

    def _exec_type(self, node: ASTNode, ctx: ExecutionContext):
        text = self._interpolate(node.args)
        self._emit("node_start", node_type="TYPE", args=text, line=node.line)
        self._log(f"TYPE: {text}")
        self._call_action("type", text=text, ctx=ctx)
        self._emit("node_done", node_type="TYPE", args=text, line=node.line)

    def _exec_launch(self, node: ASTNode, ctx: ExecutionContext):
        target = self._interpolate(node.args)
        self._emit("node_start", node_type="LAUNCH", args=target, line=node.line)
        self._log(f"LAUNCH: {target}")
        self._call_action("launch", target=target, ctx=ctx)
        self._emit("node_done", node_type="LAUNCH", args=target, line=node.line)

    def _exec_wait(self, node: ASTNode, ctx: ExecutionContext):
        condition = self._interpolate(node.args)
        self._emit("node_start", node_type="WAIT", args=condition, line=node.line)
        self._log(f"WAIT: {condition}")
        # 数字条件 => 实际 sleep（无论有无 handler）
        try:
            seconds = float(condition)
            time.sleep(seconds)
            self._emit("node_done", node_type="WAIT", args=condition, line=node.line)
            return
        except ValueError:
            pass
        self._call_action("wait", condition=condition, timeout=ctx.timeout, ctx=ctx)
        self._emit("node_done", node_type="WAIT", args=condition, line=node.line)

    def _exec_swipe(self, node: ASTNode, ctx: ExecutionContext):
        """SWIPE x1 y1 x2 y2 — 滑动（兼容空格和逗号分隔）"""
        args = self._interpolate(node.args)
        import re
        parts = re.split(r"[\s,]+", args.strip())
        self._emit("node_start", node_type="SWIPE", args=args, line=node.line)
        self._log(f"SWIPE: {args}")
        if len(parts) >= 4:
            self._call_action("swipe",
                x1=int(parts[0]), y1=int(parts[1]),
                x2=int(parts[2]), y2=int(parts[3]),
                ctx=ctx)
        self._emit("node_done", node_type="SWIPE", args=args, line=node.line)

    def _exec_scroll(self, node: ASTNode, ctx: ExecutionContext):
        """SCROLL clicks — 滚动"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="SCROLL", args=args, line=node.line)
        self._log(f"SCROLL: {args}")
        try:
            clicks = int(args)
            self._call_action("scroll", clicks=clicks, ctx=ctx)
        except ValueError:
            pass
        self._emit("node_done", node_type="SCROLL", args=args, line=node.line)

    def _exec_delay(self, node: ASTNode, ctx: ExecutionContext):
        """DELAY seconds — 设置全局延迟"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="DELAY", args=args, line=node.line)
        self._log(f"DELAY: {args}")
        try:
            self._call_action("delay", duration=float(args), ctx=ctx)
        except ValueError:
            pass
        self._emit("node_done", node_type="DELAY", args=args, line=node.line)

    def _exec_wait_random(self, node: ASTNode, ctx: ExecutionContext):
        """WAIT_RANDOM min max — 随机等待 min~max 秒"""
        import random
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="WAIT_RANDOM", args=args, line=node.line)
        self._log(f"WAIT_RANDOM: {args}")
        parts = args.split()
        try:
            min_s = float(parts[0]) if len(parts) > 0 else 0
            max_s = float(parts[1]) if len(parts) > 1 else min_s
            if min_s > max_s:
                min_s, max_s = max_s, min_s
            duration = random.uniform(min_s, max_s)
            self._log(f"  随机等待 {duration:.1f}s ({min_s}-{max_s})")
            time.sleep(duration)
        except ValueError:
            self._log(f"  无法解析 '{args}'，默认等待 1s")
            time.sleep(1)
        self._emit("node_done", node_type="WAIT_RANDOM", args=args, line=node.line)

    def _exec_loop(self, node: ASTNode, ctx: ExecutionContext):
        raw = node.args or ""
        tag = raw
        loop_type = getattr(node, "loop_type", "")

        # WHILE 语义：当条件为真时循环
        if loop_type == "while":
            self._emit("node_start", node_type="LOOP", args=f"WHILE {raw}", line=node.line)
            self._log(f"WHILE: {raw}")
            while not self._stop_flag.is_set():
                self._check_control()
                cond = self._interpolate(raw)
                result = self._resolve_condition(cond, ctx)
                if not result:
                    break
                for child in node.children:
                    self._execute_node(child, ctx)
            self._emit("node_done", node_type="LOOP", args=f"WHILE {raw}", line=node.line)
            return

        # UNTIL 语义：执行直到条件为真
        if loop_type == "until":
            self._emit("node_start", node_type="LOOP", args=f"UNTIL {raw}", line=node.line)
            self._log(f"UNTIL: {raw}")
            while not self._stop_flag.is_set():
                self._check_control()
                for child in node.children:
                    self._execute_node(child, ctx)
                cond = self._interpolate(raw)
                result = self._resolve_condition(cond, ctx)
                if result:
                    break
            self._emit("node_done", node_type="LOOP", args=f"UNTIL {raw}", line=node.line)
            return

        # 标准 LOOP 语义
        try:
            max_iter = int(raw)
            tag = f"_loop_{raw}"
        except ValueError:
            max_iter = ctx.max_loops
        ctx.loop_count[tag] = 0
        self._emit("node_start", node_type="LOOP", args=node.args, line=node.line)
        self._log(f"LOOP 开始: {raw} ({max_iter}次)")

        while ctx.loop_count[tag] < max_iter:
            self._check_control()
            ctx.loop_count[tag] += 1
            self._log(f"  LOOP [{raw}] 第 {ctx.loop_count[tag]} 次")
            try:
                for child in node.children:
                    self._execute_node(child, ctx)
            except BreakLoop:
                break

        self._emit("node_done", node_type="LOOP", args=node.args, line=node.line)

    def _exec_if(self, node: ASTNode, ctx: ExecutionContext):
        condition = self._interpolate(node.args)
        self._emit("node_start", node_type="IF", args=condition, line=node.line)
        self._log(f"IF: {condition}")
        result = self._resolve_condition(condition, ctx)
        branch = node.children if result else node.else_children
        for child in branch:
            self._execute_node(child, ctx)
        self._emit("node_done", node_type="IF", args=condition, line=node.line)

    def _exec_run(self, node: ASTNode, ctx: ExecutionContext):
        macro = self._interpolate(node.args)
        self._emit("node_start", node_type="RUN", args=macro, line=node.line)
        self._log(f"RUN 宏: {macro}")
        self._call_action("run_macro", macro_name=macro, ctx=ctx)
        self._emit("node_done", node_type="RUN", args=macro, line=node.line)

    def _exec_screenshot(self, node: ASTNode, ctx: ExecutionContext):
        dest = self._interpolate(node.args) if node.args else ""
        self._emit("node_start", node_type="SCREENSHOT", args=dest, line=node.line)
        self._log(f"SCREENSHOT: {dest or '(default)'}")
        result = self._call_action("screenshot", dest=dest, ctx=ctx)
        self._set_var("_screenshot", result)
        self._emit("node_done", node_type="SCREENSHOT", args=dest, line=node.line)

    def _exec_waitscreen(self, node: ASTNode, ctx: ExecutionContext):
        args = self._interpolate(node.args)
        # 语法: WAITSCREEN timeout(秒) 或 WAITSCREEN
        timeout = float(args) if args else 30.0
        self._emit("node_start", node_type="WAITSCREEN", args=str(timeout), line=node.line)
        self._log(f"WAITSCREEN: 等待屏幕变化 (超时{timeout}s)")
        result = self._call_action("wait_screen_change", timeout=timeout, ctx=ctx)
        self._set_var("_screen_changed", result)
        self._emit("node_done", node_type="WAITSCREEN", args=str(timeout), line=node.line)

    def _exec_screenstable(self, node: ASTNode, ctx: ExecutionContext):
        args = self._interpolate(node.args)
        timeout = float(args) if args else 30.0
        self._emit("node_start", node_type="SCREENSTABLE", args=str(timeout), line=node.line)
        self._log(f"SCREENSTABLE: 等待屏幕静止 (超时{timeout}s)")
        result = self._call_action("wait_screen_stable", timeout=timeout, ctx=ctx)
        self._set_var("_screen_stable", result)
        self._emit("node_done", node_type="SCREENSTABLE", args=str(timeout), line=node.line)

    def _exec_hotkey(self, node: ASTNode, ctx: ExecutionContext):
        keys = [k.strip() for k in node.args.split(",")]
        self._emit("node_start", node_type="HOTKEY", args=str(keys), line=node.line)
        self._log(f"HOTKEY: {keys}")
        self._call_action("hotkey", keys=keys, ctx=ctx)
        self._emit("node_done", node_type="HOTKEY", args=str(keys), line=node.line)

    def _exec_focus(self, node: ASTNode, ctx: ExecutionContext):
        title = self._interpolate(node.args)
        self._emit("node_start", node_type="FOCUS", args=title, line=node.line)
        self._log(f"FOCUS: {title}")
        self._call_action("focus_window", title=title, ctx=ctx)
        self._emit("node_done", node_type="FOCUS", args=title, line=node.line)

    def _exec_set(self, node: ASTNode, ctx: ExecutionContext):
        """SET var_name value — 设置变量"""
        parts = node.args.split(None, 1)
        if len(parts) >= 1:
            name = parts[0]
            value = self._interpolate(parts[1]) if len(parts) > 1 else ""
            self._set_var(name, value)
            self._log(f"SET {name} = {value}")

    def _exec_variable_inc(self, node: ASTNode, ctx: ExecutionContext):
        """VARIABLE_INC name [step=1] — 递增变量"""
        parts = node.args.split(None, 1)
        if parts:
            name = parts[0]
            step = 1
            if len(parts) > 1:
                try:
                    step = int(self._interpolate(parts[1]))
                except ValueError:
                    step = 1
            current = self._resolve_var(name)
            if current is None:
                current = 0
            self._set_var(name, int(current) + step)
            self._log(f"VARIABLE_INC {name} += {step} -> {self._resolve_var(name)}")

    def _exec_wait_file(self, node: ASTNode, ctx: ExecutionContext):
        """WAIT_FILE path [timeout=60] — 等待文件出现"""
        parts = node.args.split(None, 1)
        if not parts:
            self._log("WAIT_FILE: 缺少路径", level="warn")
            return
        path = parts[0].strip('"').strip("'")
        timeout = 60.0
        if len(parts) > 1:
            try:
                timeout = float(parts[1])
            except ValueError:
                pass
        deadline = time.time() + timeout
        self._log(f"WAIT_FILE: 等待文件 '{path}' (超时 {timeout}s)")
        while time.time() < deadline:
            self._check_control()
            if os.path.exists(path):
                self._log(f"WAIT_FILE: 文件已存在 '{path}'")
                return
            time.sleep(0.5)
        self._log(f"WAIT_FILE: 超时 '{path}'", level="warn")

    def _exec_ocr_find(self, node: ASTNode, ctx: ExecutionContext):
        query = self._interpolate(node.args)
        self._emit("node_start", node_type="OCR_FIND", args=query, line=node.line)
        self._log(f"OCR_FIND: {query}")
        result = self._call_action("ocr_find", query=query, ctx=ctx)
        if isinstance(result, dict):
            self._set_var("_ocr_found", result.get("found", False))
            self._set_var("_ocr_text", result.get("text", ""))
            self._set_var("_ocr_x", result.get("center_x"))
            self._set_var("_ocr_y", result.get("center_y"))
            self._set_var("_ocr_confidence", result.get("confidence"))
        self._emit("node_done", node_type="OCR_FIND", args=query, line=node.line)

    def _exec_ocr_extract(self, node: ASTNode, ctx: ExecutionContext):
        self._emit("node_start", node_type="OCR_EXTRACT", args="", line=node.line)
        self._log("OCR_EXTRACT: 提取屏幕所有文字")
        result = self._call_action("ocr_extract", ctx=ctx)
        self._set_var("_ocr_texts", result)
        self._emit("node_done", node_type="OCR_EXTRACT", args="", line=node.line)

    def _exec_region_select(self, node: ASTNode, ctx: ExecutionContext):
        """REGION_SELECT var_name [message] — 用户交互式框选区域"""
        parts = node.args.split(None, 1)
        var_name = parts[0] if parts else "_selected_region"
        message = parts[1] if len(parts) > 1 else "请拖拽选择监控区域"
        self._emit("node_start", node_type="REGION_SELECT", args=var_name, line=node.line)
        self._log(f"REGION_SELECT: 请在屏幕框选区域 (变量={var_name})")
        result = self._call_action("region_select", message=message, ctx=ctx)
        if result:
            region_str = f"{result[0]},{result[1]},{result[2]},{result[3]}"
            self._set_var(var_name, region_str)
            self._set_var("_selected_region", region_str)
            self._log(f"  区域已选择: {region_str} → ${var_name}")
        else:
            self._set_var(var_name, "")
            self._set_var("_selected_region", "")
            self._log("  区域选择已取消")
        self._emit("node_done", node_type="REGION_SELECT", args=var_name, line=node.line)

    def _exec_image_find(self, node: ASTNode, ctx: ExecutionContext):
        """IMAGE_FIND template_name [AT x,y,w,h] — 图像模板匹配，设置变量"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="IMAGE_FIND", args=args, line=node.line)
        self._log(f"IMAGE_FIND: {args}")
        result = self._call_action("image_find", args=args, ctx=ctx)
        if isinstance(result, dict):
            self._set_var("_image_found", result.get("found", False))
            self._set_var("_image_x", result.get("center_x"))
            self._set_var("_image_y", result.get("center_y"))
            self._set_var("_image_confidence", result.get("confidence"))
        self._emit("node_done", node_type="IMAGE_FIND", args=args, line=node.line)

    def _exec_image_click(self, node: ASTNode, ctx: ExecutionContext):
        """
        IMAGE_CLICK target1, target2 [sim=0.9] [method=right]
        多目标优先级图像匹配 + 点击
        """
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="IMAGE_CLICK", args=args, line=node.line)
        self._log(f"IMAGE_CLICK: {args}")

        # 分离目标和选项: "icon_a, icon_b sim=0.85 method=right" → targets + kwargs
        parts = args.split()
        targets_str = ""
        kwargs = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                kwargs[k.lower()] = v
            else:
                targets_str += " " + p if targets_str else p

        if not targets_str:
            self._log("IMAGE_CLICK: 需要至少一个模板名")
            self._emit("node_done", node_type="IMAGE_CLICK", args="error", line=node.line)
            return

        # 调用 action handler
        result = self._call_action(
            "image_click",
            targets=targets_str,
            similarity=float(kwargs.get("sim", kwargs.get("similarity", 0.9))),
            method=kwargs.get("method", "single"),
            timeout=float(kwargs.get("timeout", 5)),
            offset_x=int(kwargs.get("offset_x", 0)),
            offset_y=int(kwargs.get("offset_y", 0)),
            match_all=kwargs.get("match_all", "").lower() in ("true", "1"),
        )

        if isinstance(result, dict):
            self._set_var("_click_success", result.get("success", False))
            self._set_var("_click_target", result.get("target", ""))
            self._set_var("_click_x", result.get("x"))
            self._set_var("_click_y", result.get("y"))
            self._set_var("_click_confidence", result.get("confidence"))

        self._emit("node_done", node_type="IMAGE_CLICK", args=args, line=node.line)

    def _exec_template_capture(self, node: ASTNode, ctx: ExecutionContext):
        """TEMPLATE_CAPTURE var_name template_name — 从变量取区域截图存为模板"""
        args = self._interpolate(node.args)
        parts = args.split(None, 1)
        if len(parts) < 2:
            self._log("TEMPLATE_CAPTURE: 需要 var_name 和 template_name")
            return
        var_name, template_name = parts[0], parts[1]
        region_str = self._resolve_var(var_name) or ""
        self._emit("node_start", node_type="TEMPLATE_CAPTURE", args=f"{template_name} ← ${var_name}", line=node.line)
        self._log(f"TEMPLATE_CAPTURE: {template_name} 从 {var_name}={region_str}")
        result = self._call_action("template_capture", template_name=template_name, region=region_str, ctx=ctx)
        if isinstance(result, dict):
            self._set_var("_template_saved", result.get("success", False))
            self._set_var("_template_path", result.get("path", ""))
        self._emit("node_done", node_type="TEMPLATE_CAPTURE", args=template_name, line=node.line)

    def _exec_progress_wait(self, node: ASTNode, ctx: ExecutionContext):
        """
        PROGRESS_WAIT region threshold [timeout]
        轮询检测进度条百分比，达标或超时后继续执行。
        支持遮挡检测：连续 3 次 occluded=true 则报错。
        支持自动重定位（通过 --relocate 参数启用）。
        """
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="PROGRESS_WAIT", args=args, line=node.line)
        self._log(f"PROGRESS_WAIT: {args}")

        parts = args.split()
        if len(parts) < 2:
            self._log("PROGRESS_WAIT: 需要 region 和 threshold")
            self._emit("node_done", node_type="PROGRESS_WAIT", args="error", line=node.line)
            return

        region_str = parts[0]
        threshold = float(parts[1])
        timeout = float(parts[2]) if len(parts) >= 3 else 30.0

        reg_parts = region_str.split(",")
        if len(reg_parts) != 4:
            self._log(f"PROGRESS_WAIT: 无效区域 '{region_str}'，需要 x,y,w,h")
            self._emit("node_done", node_type="PROGRESS_WAIT", args="error", line=node.line)
            return

        region = tuple(int(p.strip()) for p in reg_parts)
        from perception.vision import ColorDetector

        start = time.time()
        last_log = 0.0
        occluded_count = 0
        relocate_attempted = False

        while time.time() - start < timeout:
            analysis = ColorDetector.analyze_progress_bar(region)
            ratio = analysis["ratio"]
            occluded = analysis.get("occluded", False)
            elapsed = time.time() - start

            # 每 2 秒输出一次进度
            if elapsed - last_log >= 2.0:
                status = f"  进度: {ratio:.1%}"
                if occluded:
                    status += " [被遮挡]"
                status += f" ({elapsed:.0f}s / {timeout:.0f}s)"
                self._log(status)
                last_log = elapsed

            # 遮挡检测：连续 3 次 occluded 则尝试重定位或报错
            if occluded:
                occluded_count += 1
                if occluded_count >= 3:
                    # 尝试重定位一次
                    if not relocate_attempted:
                        self._log(f"  进度条可能被遮挡，尝试自动重定位...")
                        new_region = ColorDetector.relocate_progress_bar()
                        if new_region:
                            region = new_region
                            region_str = f"{region[0]},{region[1]},{region[2]},{region[3]}"
                            self._log(f"  重定位成功: {region_str}")
                            relocate_attempted = True
                            occluded_count = 0
                            continue
                    # 重定位失败或已尝试过
                    self._log(f"  错误: 进度条被遮挡且无法恢复")
                    self._emit("node_done", node_type="PROGRESS_WAIT",
                               args="error:occluded", line=node.line)
                    raise ExecutionError(
                        f"进度条被遮挡 (区域 {region_str})，已连续 {occluded_count} 次检测不到有效信号"
                    )
            else:
                occluded_count = 0  # 一旦恢复正常就重置计数

            if ratio >= threshold:
                self._log(f"  达标: {ratio:.1%} ≥ {threshold:.0%} (耗时 {elapsed:.1f}s)")
                self._emit("node_done", node_type="PROGRESS_WAIT",
                           args=f"done:{ratio:.1%}", line=node.line)
                return

            time.sleep(0.5)

        # 超时
        last_occluded = analysis.get("occluded", False)
        occluded_msg = " [被遮挡]" if last_occluded else ""
        self._log(f"  超时: 最终进度 {ratio:.1%}{occluded_msg} < {threshold:.0%} ({timeout:.0f}s)")
        self._emit("node_done", node_type="PROGRESS_WAIT",
                   args=f"timeout:{ratio:.1%}", line=node.line)

    def _exec_progress_init(self, node: ASTNode, ctx: ExecutionContext):
        """
        PROGRESS_INIT var_name — 框选进度条区域 + 初始化模板/基线
        把区域坐标存到变量 var_name 中，同时初始化 ColorDetector 的遮挡检测和模板。
        """
        var_name = node.args.strip() or "_progress_region"
        self._emit("node_start", node_type="PROGRESS_INIT", args=var_name, line=node.line)
        self._log(f"PROGRESS_INIT: 请在屏幕框选进度条区域 (→ ${var_name})")

        result = self._call_action("region_select",
                                     message="请框选进度条区域", ctx=ctx)
        if not result:
            self._set_var(var_name, "")
            self._log("  区域选择已取消")
            self._emit("node_done", node_type="PROGRESS_INIT", args="cancelled", line=node.line)
            return

        x, y, w, h = result
        region_str = f"{x},{y},{w},{h}"
        self._set_var(var_name, region_str)
        self._set_var("_selected_region", region_str)

        # 初始化进度条基线 + 模板
        from perception.vision import ColorDetector
        init_result = ColorDetector.init_progress_bar((x, y, w, h))
        if init_result.get("success"):
            self._log(f"  进度条初始化完成: {region_str}, "
                      f"初始进度={init_result['ratio']:.1%}, "
                      f"饱和度基线={init_result['baseline_saturation']}")
        else:
            self._log(f"  进度条初始化完成 (无模板): {region_str}")

        self._emit("node_done", node_type="PROGRESS_INIT",
                   args=f"ok:{region_str}", line=node.line)

    def _exec_progress_relocate(self, node: ASTNode, ctx: ExecutionContext):
        """
        PROGRESS_RELOCATE [var_name] — 用模板匹配重新定位进度条
        更新变量中的区域坐标，返回新坐标。
        """
        args = self._interpolate(node.args)
        parts = args.split()
        var_name = parts[0] if parts else "_progress_region"
        search_region = None
        if len(parts) >= 2:
            sr = parts[1].split(",")
            if len(sr) == 4:
                search_region = tuple(int(p.strip()) for p in sr)

        self._emit("node_start", node_type="PROGRESS_RELOCATE",
                   args=f"{var_name} 搜索中...", line=node.line)
        self._log(f"PROGRESS_RELOCATE: 尝试重新定位进度条")

        from perception.vision import ColorDetector
        new_region = ColorDetector.relocate_progress_bar(search_region)
        if new_region:
            region_str = f"{new_region[0]},{new_region[1]},{new_region[2]},{new_region[3]}"
            self._set_var(var_name, region_str)
            self._set_var("_relocated_region", region_str)
            self._log(f"  重定位成功: {region_str}")
            self._emit("node_done", node_type="PROGRESS_RELOCATE",
                       args=f"ok:{region_str}", line=node.line)
        else:
            self._log(f"  重定位失败: 未找到进度条")
            self._emit("node_done", node_type="PROGRESS_RELOCATE",
                       args="failed", line=node.line)

    def _exec_popup_close(self, node: ASTNode, ctx: ExecutionContext):
        """POPUP_CLOSE [策略] — 关闭弹窗"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="POPUP_CLOSE", args=args, line=node.line)
        self._log(f"POPUP_CLOSE: {args or 'auto'}")
        self._call_action("popup_close", strategy=args or "auto", ctx=ctx)
        self._emit("node_done", node_type="POPUP_CLOSE", args=args, line=node.line)

    def _exec_color_check(self, node: ASTNode, ctx: ExecutionContext):
        """COLOR_CHECK x y w h color [tolerance] — 检查区域颜色"""
        args = self._interpolate(node.args)
        parts = args.split()
        self._emit("node_start", node_type="COLOR_CHECK", args=args, line=node.line)
        self._log(f"COLOR_CHECK: {args}")
        if len(parts) >= 5:
            self._call_action("color_check",
                x=int(parts[0]), y=int(parts[1]),
                w=int(parts[2]), h=int(parts[3]),
                expected_color=parts[4],
                tolerance=int(parts[5]) if len(parts) > 5 else 30,
                ctx=ctx)
        self._emit("node_done", node_type="COLOR_CHECK", args=args, line=node.line)

    def _exec_region_wait(self, node: ASTNode, ctx: ExecutionContext):
        """REGION_WAIT x,y,w,h type timeout — 等待区域稳定/变化"""
        args = self._interpolate(node.args)
        parts = re.split(r"[\s,]+", args.strip())
        self._emit("node_start", node_type="REGION_WAIT", args=args, line=node.line)
        self._log(f"REGION_WAIT: {args}")
        if len(parts) >= 4:
            region_type = parts[4] if len(parts) > 4 else "stable"
            timeout = float(parts[5]) if len(parts) > 5 else 30.0
            self._call_action("region_wait",
                x=int(parts[0]), y=int(parts[1]),
                w=int(parts[2]), h=int(parts[3]),
                region_type=region_type, timeout=timeout, ctx=ctx)
        self._emit("node_done", node_type="REGION_WAIT", args=args, line=node.line)

    def _exec_scene_condition(self, node: ASTNode, ctx: ExecutionContext):
        """SCENE_CONDITION scene_name [similarity] — 场景识别条件"""
        args = self._interpolate(node.args)
        parts = args.rsplit(None, 1)
        scene_name = parts[0] if parts else args
        similarity = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 70
        self._emit("node_start", node_type="SCENE_CONDITION", args=args, line=node.line)
        self._log(f"SCENE_CONDITION: {scene_name} (相似度={similarity})")
        result = self._call_action("check_condition",
            condition=f"scene:{scene_name}", similarity=similarity, ctx=ctx)
        self._emit("node_done", node_type="SCENE_CONDITION", args=args, line=node.line)

    def _exec_findcanvas(self, node: ASTNode, ctx: ExecutionContext):
        """FINDCANVAS [tolerance=N] [region=x,y,w,h] — 检测白色画布区域"""
        args = self._interpolate(node.args)
        # 解析 key=value 参数
        kwargs = {}
        for part in args.split():
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip().lower()
                if k == "tolerance":
                    kwargs["color_tolerance"] = float(v)
                elif k == "region":
                    kwargs["region"] = v
        self._emit("node_start", node_type="FINDCANVAS", args=args, line=node.line)
        self._log(f"FINDCANVAS: 检测画布区域 (tolerance={kwargs.get('color_tolerance', 30)})")
        result = self._call_action("find_canvas", **kwargs, ctx=ctx)
        if isinstance(result, dict) and result.get("success"):
            c = result["canvas"]
            center = result["center"]
            self._set_var("canvas_x", c["x"])
            self._set_var("canvas_y", c["y"])
            self._set_var("canvas_w", c["w"])
            self._set_var("canvas_h", c["h"])
            self._set_var("canvas_left", c["x"])
            self._set_var("canvas_top", c["y"])
            self._set_var("canvas_right", c["x"] + c["w"])
            self._set_var("canvas_bottom", c["y"] + c["h"])
            self._set_var("canvas_center_x", center["x"])
            self._set_var("canvas_center_y", center["y"])
            # 预计算 1/4 和 3/4 位置（方便相对定位）
            self._set_var("canvas_x1", c["x"] + c["w"] // 4)
            self._set_var("canvas_x2", c["x"] + c["w"] // 2)
            self._set_var("canvas_x3", c["x"] + c["w"] * 3 // 4)
            self._set_var("canvas_y1", c["y"] + c["h"] // 4)
            self._set_var("canvas_y2", c["y"] + c["h"] // 2)
            self._set_var("canvas_y3", c["y"] + c["h"] * 3 // 4)
            self._log(f"  画布: ({c['x']},{c['y']}) {c['w']}x{c['h']} 中心=({center['x']},{center['y']})")
        else:
            err = result.get("error", "检测失败") if isinstance(result, dict) else "未知错误"
            self._log(f"  FINDCANVAS 失败: {err}")
        self._emit("node_done", node_type="FINDCANVAS", args=args, line=node.line)

    def _exec_vlm_condition(self, node: ASTNode, ctx: ExecutionContext):
        """VLM_CONDITION 描述 — 用VLM视觉模型判断画面是否符合描述。
        截图 → 调用 Qwen2.5-VL → 解析 → 设置 _vlm_matched, _vlm_reason"""
        description = self._interpolate(node.args)
        self._emit("node_start", node_type="VLM_CONDITION", args=description, line=node.line)
        self._log(f"VLM_CONDITION: {description}")

        # 模拟模式（无 action_handler）时跳过实际 VLM 调用
        if self._action_handler is None:
            self._log(f"  模拟: VLM_CONDITION({description}) → false")
            self._set_var("_vlm_matched", False)
            self._set_var("_vlm_reason", "模拟模式")
            self._set_var("_vlm_description", "")
            self._emit("node_done", node_type="VLM_CONDITION", args=description, line=node.line)
            return

        try:
            from features.vlm_features import _screenshot_base64, _call_vlm
            image_b64 = _screenshot_base64()
            prompt = (
                "请判断当前画面是否符合以下描述。只回答[是]或[否]，然后简要说明理由。\n"
                f"描述: {description}"
            )
            result = _call_vlm("qwen2.5-vl:3b", prompt, image_b64,
                               temperature=0.05, max_tokens=128)
            content = result.get("content", "")
            is_match = content.startswith("是") or "符合" in content[:10]

            self._set_var("_vlm_matched", is_match)
            self._set_var("_vlm_reason", content)
            self._set_var("_vlm_description", content)

            self._log(f"  VLM 判断: {'匹配 ✓' if is_match else '不匹配 ✗'}")
            self._log(f"  VLM 理由: {content[:120]}")
        except Exception as e:
            self._set_var("_vlm_matched", False)
            self._set_var("_vlm_reason", f"VLM 调用失败: {e}")
            self._log(f"  VLM 调用失败: {e}")

        self._emit("node_done", node_type="VLM_CONDITION", args=description, line=node.line)

    # ── 系统指令执行 ─────────────────────────────────────────────
    def _exec_shutdown(self, node: ASTNode, ctx: ExecutionContext):
        """SHUTDOWN [delay_seconds] — 关闭计算机"""
        args = self._interpolate(node.args)
        delay = 0
        try:
            delay = int(args)
        except ValueError:
            pass
        self._emit("node_start", node_type="SHUTDOWN", args=str(delay), line=node.line)
        self._log(f"SHUTDOWN: {delay}s 后关机")
        import subprocess
        subprocess.run(["shutdown", "/s", "/t", str(delay)], shell=False)
        self._emit("node_done", node_type="SHUTDOWN", args=str(delay), line=node.line)

    def _exec_reboot(self, node: ASTNode, ctx: ExecutionContext):
        """REBOOT [delay_seconds] — 重启计算机"""
        args = self._interpolate(node.args)
        delay = 0
        try:
            delay = int(args)
        except ValueError:
            pass
        self._emit("node_start", node_type="REBOOT", args=str(delay), line=node.line)
        self._log(f"REBOOT: {delay}s 后重启")
        import subprocess
        subprocess.run(["shutdown", "/r", "/t", str(delay)], shell=False)
        self._emit("node_done", node_type="REBOOT", args=str(delay), line=node.line)

    def _exec_logoff(self, node: ASTNode, ctx: ExecutionContext):
        """LOGOFF — 注销当前用户"""
        self._emit("node_start", node_type="LOGOFF", args="", line=node.line)
        self._log("LOGOFF: 注销当前用户")
        import subprocess
        subprocess.run(["shutdown", "/l"], shell=False)
        self._emit("node_done", node_type="LOGOFF", args="", line=node.line)

    def _exec_hibernate(self, node: ASTNode, ctx: ExecutionContext):
        """HIBERNATE — 休眠"""
        self._emit("node_start", node_type="HIBERNATE", args="", line=node.line)
        self._log("HIBERNATE: 计算机进入休眠")
        import subprocess
        subprocess.run(["shutdown", "/h"], shell=False)
        self._emit("node_done", node_type="HIBERNATE", args="", line=node.line)

    # ── 扩展系统指令执行 ─────────────────────────────────────────
    def _exec_lock(self, node: ASTNode, ctx: ExecutionContext):
        """LOCK — 锁定工作站"""
        self._emit("node_start", node_type="LOCK", args="", line=node.line)
        self._log("LOCK: 锁定工作站")
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        self._emit("node_done", node_type="LOCK", args="", line=node.line)

    def _exec_volume(self, node: ASTNode, ctx: ExecutionContext):
        """VOLUME level — 设置音量"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="VOLUME", args=args, line=node.line)
        self._log(f"VOLUME: {args}%")
        try:
            level = int(args) if args.strip() else 50
            level = max(0, min(100, level))
            from features.system_control_features import volume_set
            volume_set(level)
        except Exception:
            pass
        self._emit("node_done", node_type="VOLUME", args=args, line=node.line)

    def _exec_mute(self, node: ASTNode, ctx: ExecutionContext):
        """MUTE [ON|OFF] — 静音切换"""
        args = self._interpolate(node.args).strip().lower()
        self._emit("node_start", node_type="MUTE", args=args, line=node.line)
        self._log(f"MUTE: {args or 'toggle'}")
        from features.system_control_features import volume_mute
        if args in ("on", "true", "1"):
            volume_mute(mute=True)
        elif args in ("off", "false", "0"):
            volume_mute(mute=False)
        else:
            volume_mute(mute=None)
        self._emit("node_done", node_type="MUTE", args=args, line=node.line)

    def _exec_kill(self, node: ASTNode, ctx: ExecutionContext):
        """KILL name — 终止进程"""
        args = self._interpolate(node.args)
        parts = args.split()
        name = parts[0] if parts else args
        force = "force" in args.lower() or "-f" in args
        self._emit("node_start", node_type="KILL", args=name, line=node.line)
        self._log(f"KILL: {name} {'(force)' if force else ''}")
        import subprocess
        flags = ["/f"] if force else []
        subprocess.run(["taskkill"] + flags + ["/im", name], capture_output=True, timeout=10)
        self._emit("node_done", node_type="KILL", args=name, line=node.line)

    def _exec_wait_process(self, node: ASTNode, ctx: ExecutionContext):
        """WAIT_PROCESS name timeout — 等待进程启动"""
        args = self._interpolate(node.args)
        parts = args.split()
        name = parts[0] if parts else args
        timeout = 30
        if len(parts) > 1 and parts[1].isdigit():
            timeout = int(parts[1])
        self._emit("node_start", node_type="WAIT_PROCESS", args=name, line=node.line)
        self._log(f"WAIT_PROCESS: {name} (timeout={timeout}s)")
        from features.system_control_features import wait_process_start
        wait_process_start(name, timeout)
        self._emit("node_done", node_type="WAIT_PROCESS", args=name, line=node.line)

    def _exec_wait_exit(self, node: ASTNode, ctx: ExecutionContext):
        """WAIT_EXIT name timeout — 等待进程退出"""
        args = self._interpolate(node.args)
        parts = args.split()
        name = parts[0] if parts else args
        timeout = 60
        if len(parts) > 1 and parts[1].isdigit():
            timeout = int(parts[1])
        self._emit("node_start", node_type="WAIT_EXIT", args=name, line=node.line)
        self._log(f"WAIT_EXIT: {name} (timeout={timeout}s)")
        from features.system_control_features import wait_process_exit
        wait_process_exit(name, timeout)
        self._emit("node_done", node_type="WAIT_EXIT", args=name, line=node.line)

    def _exec_wallpaper(self, node: ASTNode, ctx: ExecutionContext):
        """WALLPAPER path — 设置壁纸"""
        args = self._interpolate(node.args)
        path = args.strip()
        self._emit("node_start", node_type="WALLPAPER", args=path, line=node.line)
        self._log(f"WALLPAPER: {path}")
        from features.system_control_features import set_wallpaper
        set_wallpaper(path)
        self._emit("node_done", node_type="WALLPAPER", args=path, line=node.line)

    def _exec_display_off(self, node: ASTNode, ctx: ExecutionContext):
        """DISPLAY_OFF — 关闭显示器"""
        self._emit("node_start", node_type="DISPLAY_OFF", args="", line=node.line)
        self._log("DISPLAY_OFF: 关闭显示器")
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        self._emit("node_done", node_type="DISPLAY_OFF", args="", line=node.line)

    def _exec_sleep(self, node: ASTNode, ctx: ExecutionContext):
        """SLEEP — 睡眠"""
        self._emit("node_start", node_type="SLEEP", args="", line=node.line)
        self._log("SLEEP: 计算机进入睡眠")
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        self._emit("node_done", node_type="SLEEP", args="", line=node.line)

    def _exec_desktop(self, node: ASTNode, ctx: ExecutionContext):
        """DESKTOP — 显示桌面"""
        self._emit("node_start", node_type="DESKTOP", args="", line=node.line)
        self._log("DESKTOP: 显示桌面")
        import ctypes
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)  # Win key down
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)  # D key down
        ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)  # D key up
        ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)  # Win key up
        self._emit("node_done", node_type="DESKTOP", args="", line=node.line)

    def _exec_battery(self, node: ASTNode, ctx: ExecutionContext):
        """BATTERY — 获取电池状态"""
        self._emit("node_start", node_type="BATTERY", args="", line=node.line)
        self._log("BATTERY: 获取电池状态")
        from features.system_control_features import battery_status
        result = battery_status()
        self._emit("node_done", node_type="BATTERY", args=str(result), line=node.line)

    def _exec_empty_bin(self, node: ASTNode, ctx: ExecutionContext):
        """EMPTY_BIN — 清空回收站"""
        self._emit("node_start", node_type="EMPTY_BIN", args="", line=node.line)
        self._log("EMPTY_BIN: 清空回收站")
        import ctypes
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0)
        self._emit("node_done", node_type="EMPTY_BIN", args="", line=node.line)

    def _exec_brightness(self, node: ASTNode, ctx: ExecutionContext):
        """BRIGHTNESS level — 设置亮度"""
        args = self._interpolate(node.args)
        self._emit("node_start", node_type="BRIGHTNESS", args=args, line=node.line)
        self._log(f"BRIGHTNESS: {args}%")
        try:
            level = int(args) if args.strip() else 80
            from features.system_control_features import display_brightness
            display_brightness(level)
        except Exception:
            pass
        self._emit("node_done", node_type="BRIGHTNESS", args=args, line=node.line)

    def _exec_clipboard_set(self, node: ASTNode, ctx: ExecutionContext):
        """CLIPBOARD_SET text — 设置剪贴板"""
        text = self._interpolate(node.args)
        self._emit("node_start", node_type="CLIPBOARD_SET", args=text, line=node.line)
        self._log(f"CLIPBOARD_SET: 写入 {len(text)} 字符")
        from features.clipboard_features import clipboard_set_text
        clipboard_set_text(text)
        self._emit("node_done", node_type="CLIPBOARD_SET", args=text, line=node.line)

    def _exec_clipboard_get(self, node: ASTNode, ctx: ExecutionContext):
        """CLIPBOARD_GET — 读取剪贴板"""
        self._emit("node_start", node_type="CLIPBOARD_GET", args="", line=node.line)
        self._log("CLIPBOARD_GET: 读取剪贴板")
        from features.clipboard_features import clipboard_get_text
        result = clipboard_get_text()
        self._emit("node_done", node_type="CLIPBOARD_GET", args=str(result), line=node.line)

    def _exec_window_control(self, node: ASTNode, ctx: ExecutionContext):
        """MINIMIZE|MAXIMIZE|CLOSE_WINDOW|RESIZE_WINDOW — 窗口操作"""
        ntype = node.type.value
        args = self._interpolate(node.args)
        self._emit("node_start", node_type=ntype, args=args, line=node.line)
        self._log(f"{ntype}: {args}")
        try:
            import win32gui, win32con
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

            # 查找窗口
            hwnd = None
            if title:
                def cb(h, ctx):
                    if title.lower() in win32gui.GetWindowText(h).lower():
                        ctx.append(h)
                handles = []
                win32gui.EnumWindows(cb, handles)
                hwnd = handles[0] if handles else None
            else:
                hwnd = win32gui.GetForegroundWindow()

            if hwnd:
                cmd_map = {
                    "MINIMIZE": win32con.SW_MINIMIZE,
                    "MAXIMIZE": win32con.SW_MAXIMIZE,
                    "CLOSE_WINDOW": win32con.SW_CLOSE if hasattr(win32con, "SW_CLOSE") else 0,
                }
                if ntype == "MINIMIZE":
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                elif ntype == "MAXIMIZE":
                    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                elif ntype == "CLOSE_WINDOW":
                    win32gui.SendMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                elif ntype == "RESIZE_WINDOW":
                    rect = win32gui.GetWindowRect(hwnd)
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.05)
                    win32gui.MoveWindow(hwnd, rect[0], rect[1], width, height, True)
        except ImportError:
            pass
        self._emit("node_done", node_type=ntype, args=args, line=node.line)

    # ── 层次化 DSL 执行 ──────────────────────────────────────────
    def _exec_call(self, node: ASTNode, ctx: ExecutionContext):
        name = node.args
        if name not in self._subroutines:
            raise ExecutionError(f"未定义的子程序: '{name}'")

        if self._call_depth >= ctx.max_call_depth:
            raise ExecutionError(f"调用深度超过限制({ctx.max_call_depth}): '{name}'")
        self._call_depth += 1

        sub = self._subroutines[name]
        # 在调用者作用域中解析参数，绑定到子程序的参数名
        bindings = {}
        for i, param in enumerate(sub.params):
            if i < len(node.call_args):
                raw = node.call_args[i]
                resolved = self._interpolate(raw)
                bindings[param] = resolved
            else:
                bindings[param] = None

        self._push_scope(bindings)
        self._log(f"CALL {name}({', '.join(f'{k}={v}' for k, v in bindings.items())})")
        try:
            for child in sub.children:
                self._execute_node(child, ctx)
        except ReturnSignal as ret:
            self._log(f"  RETURN {sub.name} -> {ret.value}")
            return ret.value
        finally:
            self._pop_scope()
            self._call_depth -= 1

    def _exec_parallel(self, node: ASTNode, ctx: ExecutionContext):
        """PARALLEL 并行执行：每个分支独立线程"""
        branches = node.children
        if not branches:
            return
        self._log(f"PARALLEL: {len(branches)} 个分支并行执行")

        errors = []
        with ThreadPoolExecutor(max_workers=len(branches)) as pool:
            futures = {}
            for i, branch in enumerate(branches):
                future = pool.submit(self._execute_branch, branch, ctx, i)
                futures[future] = i

            for future in as_completed(futures):
                branch_idx = futures[future]
                try:
                    future.result()
                    self._log(f"  分支 {branch_idx} 完成")
                except Exception as e:
                    errors.append(f"分支 {branch_idx}: {e}")
                    self._log(f"  分支 {branch_idx} 失败: {e}")

        if errors:
            self._log(f"PARALLEL 完成，{len(errors)} 个分支有错误")

    def _execute_branch(self, node: ASTNode, ctx: ExecutionContext, branch_idx: int):
        """在线程池中执行一个并行分支"""
        for child in node.children:
            self._execute_node(child, ctx)

    def _exec_with(self, node: ASTNode, ctx: ExecutionContext):
        """WITH <handler_name> 异质执行：临时切换 action_handler"""
        handler_name = node.args
        handler = self._handlers.get(handler_name)
        if handler is None:
            self._log(f"  [WITH] 未知 handler '{handler_name}'，使用默认")
            for child in node.children:
                self._execute_node(child, ctx)
        else:
            old_handler = self._action_handler
            self._action_handler = handler
            self._log(f"  [WITH] 切换 handler 到 '{handler_name}'")
            try:
                for child in node.children:
                    self._execute_node(child, ctx)
            finally:
                self._action_handler = old_handler
                self._log(f"  [WITH] 恢复 handler 到默认")

    def _exec_import(self, node: ASTNode, ctx: ExecutionContext):
        path = node.args
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        if not os.path.exists(path):
            raise ExecutionError(f"IMPORT 文件不存在: '{path}'")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        parser = DSLParser()
        parser.parse(source)
        count = len(parser.subroutines)
        self._subroutines.update(parser.subroutines)
        self._log(f"IMPORT '{os.path.basename(path)}' ({count} 个子程序)")

    def _exec_when(self, node: ASTNode, ctx: ExecutionContext):
        """WHEN 事件注册：设置 watcher，不阻塞主流程"""
        try:
            from engine.triggers import get_trigger_engine, list_trigger_types
            engine = get_trigger_engine()
            if self._action_handler:
                engine.set_action_handler(self._action_handler)
            engine.set_executor(self)  # 传入 executor 引用，使触发器可复用 _execute_node

            event_key = node.event_key
            config_str = node.args

            # 查找注册的 trigger type 匹配 event_key
            trigger_types = list_trigger_types()
            matched = [t for t in trigger_types if t["event_key"] == event_key]
            if not matched:
                self._log(f"  [WHEN] 未知事件类型: {event_key}")
                return
            spec_name = matched[0]["name"]

            # 构建配置 dict
            config = {}
            recurring = False
            if event_key == "TIMER_INTERVAL":
                config["interval"] = float(config_str) if config_str else 10.0
                recurring = True
            elif config_str:
                config["path"] = config_str
                config["title"] = config_str
                config["name"] = config_str

            inst = engine.register(spec_name, config, node.children, recurring=recurring)
            self._log(f"  [WHEN] {event_key} 已注册 (id={inst.id})")
        except Exception as e:
            self._log(f"  [WHEN] 注册失败: {e}")

    # ── 条件解析 ─────────────────────────────────────────────────
    def _resolve_condition(self, condition: str, ctx: ExecutionContext) -> bool:
        """解析条件表达式为布尔值。
        支持:
          - 字面量 true/false
          - ${var} 变量引用
          - text:"..." / img:"..." / color:"..." / var:"name" 前缀条件
          其余委托给 action_handler。"""
        c = condition.strip()
        cl = c.lower()
        if cl == "true":
            return True
        if cl == "false":
            return False

        # ${var} 变量引用
        if c.startswith("${") and c.endswith("}"):
            var_name = c[2:-1]
            val = self._resolve_var(var_name)
            return bool(val)

        # 前缀条件直接委托给 action_handler
        if any(c.startswith(p) for p in ("text:", "img:", "color:", "var:")):
            return bool(self._call_action("check_condition", condition=condition, ctx=ctx))

        # vlm:"描述" — 内联 VLM 条件判断
        if c.startswith("vlm:"):
            desc = c[4:].strip().strip('"').strip("'")
            return self._eval_vlm_condition(desc)

        # 委托给 action_handler
        return bool(self._call_action("check_condition", condition=condition, ctx=ctx))

    def _eval_vlm_condition(self, description: str) -> bool:
        """截图 + VLM 判断画面是否符合描述，返回布尔值"""
        # 模拟模式（无 action_handler）时跳过实际 VLM 调用
        if self._action_handler is None:
            self._log(f"  模拟: VLM条件({description}) → false")
            return False
        try:
            from features.vlm_features import _screenshot_base64, _call_vlm
            self._log(f"  VLM 条件: {description}")
            image_b64 = _screenshot_base64()
            prompt = (
                "请判断当前画面是否符合以下描述。只回答[是]或[否]，然后简要说明理由。\n"
                f"描述: {description}"
            )
            result = _call_vlm("qwen2.5-vl:3b", prompt, image_b64,
                               temperature=0.05, max_tokens=128)
            content = result.get("content", "")
            is_match = content.startswith("是") or "符合" in content[:10]
            self._log(f"  VLM 判断: {'匹配 ✓' if is_match else '不匹配 ✗'} — {content[:80]}")
            return is_match
        except Exception as e:
            self._log(f"  VLM 内联条件失败: {e}")
            return False

    # ── 动作调用 ─────────────────────────────────────────────────
    def _call_action(self, action: str, **kwargs) -> Any:
        with self._action_lock:
            if self._action_handler is None:
                self._log(f"  [模拟] {action}({kwargs})")
                return True
            retry_limit = kwargs.pop("retry_limit", 3)
            ctx = kwargs.pop("ctx", None)
            retry_limit = ctx.retry_limit if ctx else retry_limit
            last_err = None
            for attempt in range(1, retry_limit + 1):
                try:
                    return self._action_handler(action, **kwargs)
                except Exception as e:
                    last_err = e
                    self._log(f"  [重试 {attempt}/{retry_limit}] {action} 失败: {e}")
                    time.sleep(0.5 * attempt)
            # 所有重试失败 → 启动自愈管道
            return self._heal_action(action, kwargs, last_err, ctx)

    def _heal_action(self, action: str, context: dict, error: Exception,
                     ctx: Optional[ExecutionContext]) -> Any:
        """使用自愈管道尝试恢复"""
        self._log(f"  [自愈] {action} 所有重试失败，尝试恢复策略...")
        try:
            from engine.healing import get_healing_pipeline
            pipeline = get_healing_pipeline(self._action_handler)
            heal_context = {
                "action": action,
                "target": context.get("target", ""),
                "region": context.get("region"),
                "error": str(error),
            }
            result = pipeline.heal(action, str(error), heal_context,
                                   retry_action=lambda c: self._action_handler(action, **c))
            if result.success:
                self._log(f"  [自愈] [{result.strategy}] 恢复成功")
                return result.result
            self._log(f"  [自愈] 所有策略失败: {result.error}")
        except Exception as heal_err:
            self._log(f"  [自愈] 管道异常: {heal_err}")
        raise ExecutionError(f"动作 '{action}' 在重试和自愈后失败: {error}")

    def _set_state(self, state: ExecutorState):
        self._state = state
        self._emit("state_change", state=state.value)

    def _log(self, msg: str):
        self._emit("log", message=msg)


class BreakLoop(Exception):
    """用于从 LOOP 内部跳出的控制流异常"""
    def __str__(self):
        return "BREAK 只能在 LOOP 内部使用"
