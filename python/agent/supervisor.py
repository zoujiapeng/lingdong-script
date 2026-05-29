"""
Supervisor — 工具执行的监督层。

被所有三种执行模式 (text_protocol, langgraph, planned) 共享。
流水线: SoM解引用 → UIA定位 → 视觉定位 → 窗口焦点 → 执行+重试 → 对话框 → 屏幕稳定
"""

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.supervisor")


class Supervisor:
    """工具执行监督层，封装重试、视觉定位、窗口焦点、对话框处理等。"""

    # GUI 动作集合 (受益于窗口焦点 + 重试 + 屏幕稳定)
    GUI_ACTIONS = frozenset({
        "click_target", "right_click", "hover", "mouse_move",
        "type_text", "type_clipboard",
        "hotkey", "scroll", "drag", "swipe",
        "triple_click", "middle_click", "select_text",
    })

    # 不需要截图的工具 (读取类操作)
    NO_SCREENSHOT_TOOLS = frozenset({
        "list_windows", "get_active_window", "find_element", "ocr_find_text",
        "ocr_extract_all", "get_variable", "file_read", "file_list",
        "battery_status", "network_status", "detect_process",
        "find_processes",
    })

    def __init__(self, emit_event: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None,
                 auto_learn: bool = False,
                 multimodal: bool = False,
                 api_key: str = "",
                 api_base: str = "",
                 model: str = ""):
        self._emit = emit_event or (lambda e, d: None)
        self._stop_event = stop_event or threading.Event()
        self.auto_learn = auto_learn
        self.multimodal = multimodal
        self.api_key = api_key
        self.api_base = api_base
        self.model = model

        # Runtime state
        self._last_launched: str = ""
        self._last_som_map: dict[int, dict] = {}
        self._last_som_ts: float = 0.0

    # ── 窗口焦点安全 ──────────────────────────────────────────

    def ensure_safe_window(self, tool_name: str, args: dict) -> dict:
        """GUI 操作前确认目标窗口已激活。"""
        try:
            target_hint = (
                args.get("target_window") or
                args.get("target") or
                args.get("title") or
                self._last_launched or ""
            )
            if not target_hint:
                return {"success": True, "focused": True}

            from features.registry import registry as r
            result = r.execute("get_active_window")
            active_data = result if isinstance(result, dict) else {}
            active_data = active_data.get("result", active_data)
            active_title = ""
            if isinstance(active_data, dict):
                active_title = active_data.get("title", "")

            if target_hint.lower() in active_title.lower():
                return {"success": True, "focused": True}

            r.execute("focus_window", title=target_hint)
            time.sleep(0.3)
            return {"success": True, "focused": True}
        except Exception as e:
            logger.warning("Window focus check failed: %s", e)
            return {"success": False, "focused": False, "error": str(e)}

    # ── 屏幕稳定 ─────────────────────────────────────────────

    @staticmethod
    def wait_screen_stable(timeout: float = 0.5):
        """等待屏幕稳定。多模态模式下视觉理解已经很快，无需长等待。"""
        time.sleep(min(timeout, 0.5))

    # ── 对话框自动处理 ────────────────────────────────────────

    def auto_handle_dialogs(self) -> bool:
        """自动关闭可见对话框。"""
        try:
            time.sleep(0.3)
            from agent.dialog_handler import handle_any_dialog
            result = handle_any_dialog()
            if result != "not_found":
                logger.info("Dialog handled: %s", result)
                return True
        except Exception:
            pass
        return False

    # ── 视觉定位 ──────────────────────────────────────────────

    def apply_visual_locator(self, name: str, args: dict) -> dict:
        """经验库 → VLM 兜底，注入坐标到 args。VLM 连续失败会自动禁用。"""
        if not self.auto_learn and not self.multimodal:
            return args
        if getattr(self, '_vl_disabled', False):
            return args
        self._vl_fail_count = getattr(self, '_vl_fail_count', 0)

        _MAP = {
            "click_target":   ("target", "click"),
            "right_click":    ("target", "click"),
            "hover":          ("target", "click"),
            "mouse_move":     ("target", "click"),
            "type_text":      ("text",   "type"),
            "type_clipboard": ("text",   "type"),
            "drag":           ("from_target", "drag"),
            "scroll":         ("target", "scroll"),
        }

        if name not in _MAP:
            return args

        target_param, step_type = _MAP[name]
        target_text = args.get(target_param, "")

        if not target_text or not isinstance(target_text, str):
            return args
        if re.match(r'^-?\d+$', target_text.strip()):
            return args
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target_text.strip()):
            return args
        x_val, y_val = args.get("x", -1), args.get("y", -1)
        if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
            if x_val >= 0 and y_val >= 0:
                return args

        import pyautogui as _pg
        import tempfile
        ss_path = os.path.join(
            tempfile.gettempdir(),
            f"lg_vl_{int(time.time())}_{hash(target_text) & 0xFFFF}.png",
        )
        try:
            _pg.screenshot(ss_path)
        except Exception as exc:
            logger.warning("Visual locator screenshot failed: %s", exc)
            return args

        try:
            from agent import visual_locator as _vl
            vlm_config = None
            if self.multimodal and self.api_key:
                vlm_config = {
                    "api_key": self.api_key,
                    "api_base": self.api_base or "https://api.deepseek.com/v1",
                    "model": self.model or "deepseek-chat",
                }

            result = _vl.locate(
                target_text=target_text,
                step_type=step_type,
                screenshot_path=ss_path,
                use_vlm=self.multimodal,
                vlm_config=vlm_config,
                window_title="",
                threshold=0.75,
            )

            if result.get("success"):
                self._vl_fail_count = 0
                coords = result["result"]
                args = dict(args)
                cx = coords.get("center_x", coords.get("x", 0))
                cy = coords.get("center_y", coords.get("y", 0))
                args["x"] = int(cx)
                args["y"] = int(cy)
                if name == "click_target":
                    args["target"] = f"{cx},{cy}"
                self._emit("log", {
                    "level": "info",
                    "message": f"视觉定位: 「{target_text}」→ ({cx}, {cy}) [{coords.get('method', '?')}]",
                })
            else:
                self._emit("log", {
                    "level": "debug",
                    "message": f"视觉定位未命中: 「{target_text}」({result.get('error', '无匹配')})",
                })
        except Exception as exc:
            logger.warning("Visual locator error (non-fatal): %s", exc)
            self._vl_fail_count = getattr(self, '_vl_fail_count', 0) + 1
            if self._vl_fail_count >= 3:
                self._vl_disabled = True
                self._emit("log", {
                    "level": "warning",
                    "message": "VLM 连续异常已自动禁用",
                })
        finally:
            try:
                if os.path.exists(ss_path):
                    os.remove(ss_path)
            except Exception:
                pass

        return args

    # ── 类型转换执行 ───────────────────────────────────────────

    @staticmethod
    def execute_with_type_conv(name: str, args: dict) -> dict:
        """参数类型转换 + 注册表执行。"""
        from features.registry import registry
        spec = registry.get(name)
        if spec:
            converted = {}
            for p in spec.params:
                val = args.get(p.name)
                if val is None:
                    continue
                if p.type in ("number", "int"):
                    try:
                        converted[p.name] = int(val) if p.type == "int" else float(val)
                    except (ValueError, TypeError):
                        converted[p.name] = val
                elif p.type == "boolean":
                    if isinstance(val, str):
                        converted[p.name] = val.lower() in ("true", "1", "yes")
                    else:
                        converted[p.name] = bool(val)
                elif p.type == "array":
                    if isinstance(val, str):
                        try:
                            converted[p.name] = json.loads(val)
                        except json.JSONDecodeError:
                            converted[p.name] = [v.strip() for v in val.split(",") if v.strip()]
                    else:
                        converted[p.name] = val
                else:
                    converted[p.name] = str(val).strip("\"'") if not isinstance(val, str) else val.strip("\"'")
        else:
            converted = args
        return registry.execute(name, **converted)

    # ── 核心监督流水线 ─────────────────────────────────────────

    def execute(self, name: str, args: dict) -> dict:
        """带完整监督层的工具执行。

        流水线:
          1. SoM 编号解引用（#N → 坐标）
          2. UIA 语义定位
          3. 视觉定位 (auto_learn / multimodal)
          4. GUI 操作 → 窗口焦点安全
          5. 执行 + 重试 (GUI: 2次退避重试, 其他: 1次)
          6. GUI 操作后 → 对话框自动处理
          7. GUI 操作后 → 等待屏幕稳定
          8. 失败诊断截图
        """
        is_gui = name in self.GUI_ACTIONS

        # ── 0. SoM 编号解引用（#N → 坐标） ──
        if is_gui and self._last_som_map:
            som_target = args.get("target", "") or args.get("text", "") or args.get("from_target", "")
            if isinstance(som_target, str) and som_target.startswith("#"):
                try:
                    idx = int(som_target.lstrip("#").strip())
                    if idx in self._last_som_map:
                        m = self._last_som_map[idx]
                        args = dict(args)
                        args["target"] = f"{m['x']},{m['y']}"
                        self._emit("log", {
                            "level": "info",
                            "message": f"SoM#{idx}: 「{m['text']}」→ {m['x']},{m['y']}",
                        })
                except (ValueError, TypeError):
                    pass

        # ── 1a. UIA 语义定位 ──
        if is_gui and name in ("click_target", "right_click", "hover",
                                "mouse_move", "drag"):
            target = args.get("target", "") or args.get("from_target", "")
            if target and not re.match(r'^-?\d+\s*,\s*-?\d+$', str(target).strip()):
                try:
                    uia_match = self.resolve_via_a11y(target)
                    if uia_match:
                        args = dict(args)
                        args["target"] = f"{uia_match['x']},{uia_match['y']}"
                        self._emit("log", {
                            "level": "info",
                            "message": f"UIA定位: 「{target}」→ {uia_match['x']},{uia_match['y']} ({uia_match['role']})",
                        })
                except Exception as e:
                    logger.warning("UIA resolution error: %s", e)

        # ── 1b. 视觉定位 ──
        if is_gui and (self.auto_learn or self.multimodal):
            try:
                args = self.apply_visual_locator(name, args)
            except Exception as e:
                logger.warning("Visual locator error: %s", e)

        # ── 2a. 防重复启动拦截 ──
        # click_target 目标匹配已运行程序 → 转 launch_program 聚焦
        if name == "click_target":
            target = args.get("target", "") or ""
            if target and not re.match(r'^-?\d+\s*,\s*-?\d+$', str(target).strip()):
                try:
                    from features.program_registry import get_program_registry
                    reg = get_program_registry()
                    names = [item["name"] for item in reg.get_name_list()]
                    match = next((n for n in names if target.lower() in n.lower() or n.lower() in target.lower()), None)
                    if match:
                        import psutil
                        for p in psutil.process_iter(["name"]):
                            try:
                                pn = (p.info["name"] or "").lower()
                                if match.lower() in pn or pn.replace(".exe","") in match.lower():
                                    self._emit("log", {
                                        "level": "info",
                                        "message": f"防重复: 「{target}」→ 程序已在运行，聚焦已有窗口",
                                    })
                                    return self.execute_with_type_conv("launch_program", {"target": match})
                            except Exception:
                                pass
                except Exception:
                    pass

        # ── 2b. 强制去重: 任何GUI操作前检查已知程序是否重复启动 ──
        if is_gui and name in ("click_target", "launch_program", "hotkey"):
            launch_hint = args.get("target", "") or args.get("text", "") or ""
            if launch_hint:
                try:
                    import psutil
                    hint_lower = launch_hint.lower()
                    running_names = set()
                    for p in psutil.process_iter(["name"]):
                        try:
                            pn = (p.info["name"] or "").lower().replace(".exe", "")
                            if pn:
                                running_names.add(pn)
                        except Exception:
                            pass
                    # 如果尝试启动的程序名匹配已运行进程 → 聚焦
                    for rn in running_names:
                        if len(rn) >= 2 and (rn in hint_lower or hint_lower in rn):
                            self._emit("log", {
                                "level": "info",
                                "message": f"强制去重: 「{launch_hint}」匹配运行中进程「{rn}」，转为聚焦",
                            })
                            return self.execute_with_type_conv("launch_program", {"target": rn})
                except Exception:
                    pass

        # ── 2b. 窗口焦点 ──
        if is_gui:
            self.ensure_safe_window(name, args)

        # ── 3. 执行 + 重试 ──
        max_retries = 2 if is_gui else 1
        result = {"success": False, "error": "no_attempt"}
        for attempt in range(max_retries + 1):
            if self._stop_event and self._stop_event.is_set():
                self._emit("log", {"level": "info", "message": f"停止请求，跳过 {name} 重试"})
                return {"success": False, "error": "stopped"}

            if attempt > 0:
                delay = 2 ** (attempt - 1)
                self._emit("log", {"level": "info", "message": f"重试 {name} ({attempt}/{max_retries}, {delay}s)"})
                time.sleep(delay)

            try:
                result = self.execute_with_type_conv(name, args)
                if result.get("success", False):
                    break
            except Exception as e:
                result = {"success": False, "error": str(e)}
                if attempt >= max_retries:
                    break

        # ── 4. 对话框处理 ──
        if is_gui or name in ("screenshot", "hotkey", "launch_program"):
            self.auto_handle_dialogs()

        # ── 5. 屏幕稳定 ──
        if is_gui:
            self.wait_screen_stable()

        # ── 6. 失败诊断 ──
        if not result.get("success") and is_gui:
            try:
                b64 = self.capture_b64()
                a11y = self.capture_a11y_summary()
                self._emit("log", {
                    "level": "info",
                    "message": f"故障诊断: {name} 失败 — 当前画面(base64): data:image/jpeg;base64,{b64[:50]}..."
                })
                if a11y:
                    self._emit("log", {"level": "info", "message": a11y})
            except Exception:
                pass

        # ── 跟踪启动的程序 ──
        if name == "launch_program":
            target = args.get("target") or args.get("name") or ""
            if target:
                self._last_launched = target

        return result

    # ── 截图 ──────────────────────────────────────────────────

    def capture_b64(self, max_size: int = 0, quality: int = 60) -> str:
        """全屏截图 → base64 JPEG。

        quality: JPEG 压缩质量（50=快速但略模糊，60=默认，85=高质量）
        多模态模型对压缩不敏感，可以用 50 提升速度。
        """
        import cv2
        from perception.vision import ScreenCapture
        img = ScreenCapture.capture()
        if max_size:
            h, w = img.shape[:2]
            if max(h, w) > max_size:
                scale = max_size / max(h, w)
                new_w, new_h = int(w * scale), int(h * scale)
                img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        q = min(max(quality, 30), 95)
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        return base64.b64encode(buf).decode()

    # ── UIA 控件摘要 ──────────────────────────────────────────

    @staticmethod
    def capture_a11y_summary(max_items: int = 30) -> str:
        """UIA 控件树摘要 → 结构化文本。"""
        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=2)
            if not result.get("success"):
                return ""
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            def _walk(node, depth=0):
                items = []
                if depth > 3:
                    return items
                name = node.get("name", "") or node.get("text", "") or ""
                role = node.get("control_type", node.get("role", ""))
                rect = node.get("bounding_rectangle", node.get("rect", {}))
                if isinstance(rect, dict):
                    x, y, w, h = rect.get("x", 0), rect.get("y", 0), rect.get("w", 0), rect.get("h", 0)
                else:
                    x = y = w = h = 0
                if name and role and w > 0 and h > 0 and w < 1500 and h < 500:
                    items.append(f"  {role} \"{name}\" ({x},{y} {w}x{h})")
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        items.extend(_walk(child, depth + 1))
                return items

            visible = _walk(tree)[:max_items]
            if not visible:
                return ""
            return "[UI Controls]\n" + "\n".join(visible)
        except Exception:
            return ""

    # ── UIA 语义定位 ──────────────────────────────────────────

    def resolve_via_a11y(self, target_text: str) -> dict | None:
        """UIA 无障碍树解析语义目标→屏幕坐标。"""
        if not target_text or not isinstance(target_text, str):
            return None
        target_text = target_text.strip().strip("\"'")
        if not target_text:
            return None

        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=4)
            if not result.get("success"):
                return None
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            best: dict | None = None
            best_name_len = 9999

            def _search(node, depth=0):
                nonlocal best, best_name_len
                if depth > 6:
                    return
                name = node.get("name", "") or node.get("text", "") or ""
                role = node.get("control_type", node.get("role", ""))
                rect = node.get("bounding_rectangle", node.get("rect", {}))
                if isinstance(rect, dict):
                    x, y, w_, h_ = rect.get("x", 0), rect.get("y", 0), rect.get("w", 0), rect.get("h", 0)
                else:
                    x = y = w_ = h_ = 0

                if name and role and w_ > 0 and h_ > 0 and w_ < 2000 and h_ < 1000:
                    nl = name.lower()
                    tl = target_text.lower()
                    if nl == tl or (tl in nl and len(name) < best_name_len):
                        best = {
                            "x": int(x + w_ // 2),
                            "y": int(y + h_ // 2),
                            "w": int(w_),
                            "h": int(h_),
                            "name": name,
                            "role": role,
                        }
                        best_name_len = len(name)
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        _search(child, depth + 1)

            _search(tree)
            return best
        except Exception:
            return None
