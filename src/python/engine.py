#!/usr/bin/env python3
"""
灵动脚本 - 自动化执行引擎
基于 pyautogui + OpenCV 的跨平台自动化执行引擎
"""

import sys
import json
import time
import logging
import threading
import select
import traceback
from datetime import datetime
from typing import Any, Optional

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
except ImportError:
    print(json.dumps({"type": "log", "level": "error", "message": "pyautogui not installed. Run: pip install pyautogui"}))
    sys.exit(1)

try:
    import cv2
    import numpy as np
except ImportError:
    print(json.dumps({"type": "log", "level": "error", "message": "OpenCV not installed. Run: pip install opencv-python"}))
    cv2 = None  # type: ignore

# ============================================================
# Execution Engine
# ============================================================

class ExecutionEngine:
    def __init__(self):
        self.status: str = 'idle'
        self.variables: dict = {}
        self.current_node_id: Optional[str] = None
        self._paused = False
        self._stopped = False
        self._node_map: dict = {}
        self._edges: list = []
        self._nodes: list = []
        self._script: dict = {}
        self._flow_thread: threading.Thread | None = None

    def log(self, level: str, message: str, node_id: Optional[str] = None):
        entry = {
            "type": "log",
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "node_id": node_id,
        }
        print(json.dumps(entry), flush=True)

    def node_status(self, node_id: str, status: str, message: Optional[str] = None):
        msg = {
            "type": "node_status",
            "node_id": node_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }
        if message:
            msg["message"] = message
        print(json.dumps(msg), flush=True)

    def get_node_by_id(self, node_id: str) -> Optional[dict]:
        return self._node_map.get(node_id)

    def get_output_edges(self, node_id: str, port: str = 'output') -> list:
        return [e for e in self._edges if e['from_node'] == node_id and e.get('from_port', 'output') == port]

    # ================================================================
    # Node Executors
    # ================================================================

    def execute_click(self, node: dict, params: dict) -> str:
        x, y = params['x'], params['y']
        ox = params.get('offsetX', 0)
        oy = params.get('offsetY', 0)
        target_x = x + ox
        target_y = y + oy

        method = params.get('method', 'single')
        pyautogui.moveTo(target_x, target_y, duration=0.2)

        if method == 'single':
            pyautogui.click()
        elif method == 'double':
            pyautogui.doubleClick()
        elif method == 'right':
            pyautogui.rightClick()
        elif method == 'middle':
            pyautogui.middleClick()

        delay = params.get('delay_after', 0.5)
        if delay > 0:
            time.sleep(delay)

        self.log('info', f'点击: ({target_x}, {target_y}) [{method}]', node['id'])
        return 'output'

    def _match_one_target(self, template_img, params: dict, timeout: float) -> tuple:
        """Try to find a single template on screen within timeout. Returns (found, positions)."""
        multi_scale = params.get('multi_scale', True)
        region = params.get('region')
        similarity = params.get('similarity', 90) / 100.0
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self._stopped:
                return False, []
            screenshot = pyautogui.screenshot(region=(
                region['x'], region['y'], region['w'], region['h']
            ) if region else None)
            haystack = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            scales = [1.0]
            if multi_scale:
                scales = [0.7, 0.85, 1.0, 1.15, 1.3]

            for scale in scales:
                if scale != 1.0:
                    w = int(template_img.shape[1] * scale)
                    h = int(template_img.shape[0] * scale)
                    if w < 10 or h < 10:
                        continue
                    scaled_template = cv2.resize(template_img, (w, h))
                else:
                    scaled_template = template_img

                result = cv2.matchTemplate(haystack, scaled_template, cv2.TM_CCOEFF_NORMED)
                locations = np.where(result >= similarity)
                positions = list(zip(*locations[::-1]))

                if positions:
                    offset_x = params.get('offset_x', 0)
                    offset_y = params.get('offset_y', 0)
                    match_all = params.get('match_all', False)
                    region_x = region['x'] if region else 0
                    region_y = region['y'] if region else 0
                    h, w = scaled_template.shape[:2]
                    result_positions = []
                    if match_all:
                        for (px, py) in positions:
                            cx = region_x + px + w // 2 + offset_x
                            cy = region_y + py + h // 2 + offset_y
                            result_positions.append((cx, cy))
                    else:
                        px, py = positions[0]
                        cx = region_x + px + w // 2 + offset_x
                        cy = region_y + py + h // 2 + offset_y
                        result_positions.append((cx, cy))
                    return True, result_positions

            time.sleep(0.2)
        return False, []

    def _load_template_image(self, target: dict) -> any:
        """Load a single target image from path or base64. Returns None on failure."""
        b64 = target.get('base64', '')
        path = target.get('path', '')
        if b64:
            import base64
            img_data = base64.b64decode(b64.split(',')[1] if ',' in b64 else b64)
            nparr = np.frombuffer(img_data, np.uint8)
            return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif path:
            return cv2.imread(path)
        return None

    def execute_image_click(self, node: dict, params: dict) -> str:
        if cv2 is None:
            self.log('error', 'OpenCV 不可用，无法执行图像识别', node['id'])
            return 'fail'

        targets = params.get('targets', [])
        if not targets:
            self.log('error', '未设置目标图片', node['id'])
            return 'fail'

        timeout = params.get('timeout', 5)
        all_found = True

        for idx, target in enumerate(targets):
            fname = target.get('path', f'目标{idx+1}').split('\\')[-1].split('/')[-1]
            self.log('info', f'寻找目标 #{idx+1}: {fname}', node['id'])

            template = self._load_template_image(target)
            if template is None:
                self.log('warn', f'无法加载图片: {fname}，跳过', node['id'])
                continue

            found, positions = self._match_one_target(template, params, timeout)
            if found:
                method = params.get('method', 'single')
                for (cx, cy) in positions:
                    pyautogui.moveTo(cx, cy, duration=0.15)
                    if method == 'single':
                        pyautogui.click()
                    elif method == 'double':
                        pyautogui.doubleClick()
                    elif method == 'right':
                        pyautogui.rightClick()
                    elif method == 'middle':
                        pyautogui.middleClick()
                    self.log('info', f'已点击 #{idx+1}: ({cx}, {cy})', node['id'])

                delay = params.get('delay_after', 0.5)
                if delay > 0:
                    time.sleep(delay)
            else:
                self.log('warn', f'目标 #{idx+1}: {fname} 超时未出现，继续下一张', node['id'])
                all_found = False

        if all_found:
            self.log('info', '所有目标图片已依次点击完成')
            return 'output'
        else:
            self.log('warn', '部分目标图片未找到，已完成剩余操作')
            return 'output'

    def execute_wait(self, node: dict, params: dict) -> str:
        wait_type = params.get('wait_type', 'time')

        if wait_type == 'time':
            duration = params.get('duration', 1)
            self.log('info', f'等待 {duration} 秒', node['id'])
            time.sleep(duration)
            return 'output'

        elif wait_type == 'image':
            if cv2 is None:
                self.log('error', 'OpenCV 不可用', node['id'])
                return 'output'
            template_path = params.get('template_path', '')
            template_b64 = params.get('template_base64')
            similarity = params.get('similarity', 90) / 100.0
            timeout = params.get('timeout', 10)
            start = time.time()
            self.log('info', f'等待图片出现，超时 {timeout} 秒', node['id'])
            while time.time() - start < timeout:
                if self._stopped:
                    return 'fail'
                while self._paused and not self._stopped:
                    time.sleep(0.1)
                # Take screenshot and match template
                screenshot = pyautogui.screenshot()
                screen_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                screen_gray = cv2.cvtColor(screen_cv, cv2.COLOR_BGR2GRAY)

                if template_b64:
                    import base64
                    img_data = base64.b64decode(template_b64.split(',')[1] if ',' in template_b64 else template_b64)
                    nparr = np.frombuffer(img_data, np.uint8)
                    template = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                elif template_path:
                    template = cv2.imread(template_path)
                else:
                    self.log('error', '未设置目标图片', node['id'])
                    return 'output'
                if template is None:
                    self.log('error', '无法加载模板图片', node['id'])
                    return 'output'
                template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

                result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val >= similarity:
                    self.log('info', f'图片已出现 (相似度: {max_val:.2%})', node['id'])
                    return 'output'
                time.sleep(0.3)
            self.log('info', '图片等待超时', node['id'])
            return 'output'

        elif wait_type == 'window':
            import pygetwindow as gw
            title = params.get('window_title', '')
            timeout = params.get('timeout', 10)
            start = time.time()
            while time.time() - start < timeout:
                if self._stopped:
                    return 'fail'
                try:
                    windows = gw.getWindowsWithText(title)
                    if windows:
                        self.log('info', f'窗口 "{title}" 已出现', node['id'])
                        return 'output'
                except:
                    pass
                time.sleep(0.5)
            self.log('warn', f'等待窗口 "{title}" 超时', node['id'])
            return 'output'

        return 'output'

    def execute_input_text(self, node: dict, params: dict) -> str:
        text = params.get('text', '')
        click_before = params.get('click_before', True)
        clear_before = params.get('clear_before', True)
        x = params.get('x')
        y = params.get('y')

        if click_before and x is not None and y is not None:
            pyautogui.click(x, y)
            time.sleep(0.2)

        if clear_before:
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.press('backspace')
            time.sleep(0.1)

        # Use clipboard + Ctrl+V to bypass Chinese IME
        import subprocess, base64
        encoded = base64.b64encode(text.encode('utf-16-le')).decode()
        ps_script = '[System.Text.Encoding]::Unicode.GetString([System.Convert]::FromBase64String(\"{}\")) | Set-Clipboard'.format(encoded)
        subprocess.run(['powershell', '-NoProfile', '-Command', ps_script],
                       shell=False, capture_output=True, timeout=5)
        time.sleep(0.05)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.1)

        delay = params.get('delay_after', 0.3)
        if delay > 0:
            time.sleep(delay)

        self.log('info', f'输入文本: "{text[:30]}{"..." if len(text) > 30 else ""}"', node['id'])
        return 'output'

    def execute_keyboard(self, node: dict, params: dict) -> str:
        keys = params.get('keys', [])
        modifiers = params.get('modifiers', [])

        if modifiers:
            modifier_keys = {
                'ctrl': 'ctrl', 'alt': 'alt', 'shift': 'shift', 'win': 'win'
            }
            mapped_mods = [modifier_keys.get(m, m) for m in modifiers]
            mapped_keys = [k.lower() for k in keys]

            if mapped_keys:
                pyautogui.hotkey(*mapped_mods, mapped_keys[0])
            else:
                pyautogui.hotkey(*mapped_mods)
        else:
            for key in keys:
                try:
                    pyautogui.press(key.lower())
                except:
                    pyautogui.write(key)
                time.sleep(0.05)

        delay = params.get('delay_after', 0.2)
        if delay > 0:
            time.sleep(delay)

        self.log('info', f'按键: {"+".join(modifiers + keys)}', node['id'])
        return 'output'

    def execute_swipe(self, node: dict, params: dict) -> str:
        sx, sy = params['start_x'], params['start_y']
        ex, ey = params['end_x'], params['end_y']
        duration = params.get('duration', 0.5)

        pyautogui.moveTo(sx, sy, duration=0.1)
        pyautogui.drag(ex - sx, ey - sy, duration=duration, button='left')

        delay = params.get('delay_after', 0.3)
        if delay > 0:
            time.sleep(delay)

        self.log('info', f'滑动: ({sx},{sy}) -> ({ex},{ey})', node['id'])
        return 'output'

    def _check_image_exists(self, params: dict) -> bool:
        """Check if a template image exists on screen (used by condition loops)."""
        if cv2 is None:
            return False
        template_path = params.get('template_path', '')
        template_b64 = params.get('template_base64')
        similarity = params.get('similarity', 85) / 100.0
        try:
            screenshot = pyautogui.screenshot()
            screen_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            screen_gray = cv2.cvtColor(screen_cv, cv2.COLOR_BGR2GRAY)
            if template_b64:
                import base64
                img_data = base64.b64decode(template_b64.split(',')[1] if ',' in template_b64 else template_b64)
                nparr = np.frombuffer(img_data, np.uint8)
                template = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            elif template_path:
                template = cv2.imread(template_path)
            else:
                return False
            if template is None:
                return False
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            return max_val >= similarity
        except:
            return False

    def _check_window_exists(self, params: dict) -> bool:
        """Check if a window with given title exists (used by condition loops)."""
        try:
            import pygetwindow as gw
            title = params.get('window_title', '')
            if not title:
                return False
            windows = gw.getWindowsWithText(title)
            return len(windows) > 0
        except:
            return False

    def execute_condition(self, node: dict, params: dict) -> str:
        cond_type = params.get('condition_type', 'always_true')

        if cond_type == 'always_true':
            return 'yes'

        elif cond_type == 'image_exists':
            if cv2 is None:
                self.log('error', 'OpenCV 不可用', node['id'])
                return 'no'
            similarity = params.get('similarity', 85) / 100.0
            screenshot = pyautogui.screenshot()
            haystack = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            template_b64 = params.get('template_base64')
            if template_b64:
                import base64
                img_data = base64.b64decode(template_b64.split(',')[1] if ',' in template_b64 else template_b64)
                nparr = np.frombuffer(img_data, np.uint8)
                template = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            elif params.get('template_path'):
                template = cv2.imread(params['template_path'])
            else:
                return 'no'

            if template is None:
                return 'no'

            result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            found = max_val >= similarity
            self.log('info', f'条件-图片判断: {"是" if found else "否"} (相似度: {max_val:.2f})', node['id'])
            return 'yes' if found else 'no'

        elif cond_type == 'window_exists':
            try:
                import pygetwindow as gw
                title = params.get('window_title', '')
                windows = gw.getWindowsWithText(title)
                found = len(windows) > 0
                self.log('info', f'条件-窗口判断: {"是" if found else "否"}', node['id'])
                return 'yes' if found else 'no'
            except:
                return 'no'

        elif cond_type in ('variable_equals', 'variable_greater', 'variable_less'):
            var_name = params.get('variable_name', '')
            var_value = params.get('variable_value', '')
            actual = self.variables.get(var_name, '')

            try:
                actual_num = float(actual)
                expected_num = float(var_value)
                if cond_type == 'variable_equals':
                    result = actual_num == expected_num
                elif cond_type == 'variable_greater':
                    result = actual_num > expected_num
                else:
                    result = actual_num < expected_num
            except (ValueError, TypeError):
                if cond_type == 'variable_equals':
                    result = str(actual) == str(var_value)
                else:
                    result = False

            self.log('info', f'条件-变量判断: {var_name}={actual} {"==" if cond_type == "variable_equals" else ">" if cond_type == "variable_greater" else "<"} {var_value} -> {result}', node['id'])
            return 'yes' if result else 'no'

        return 'yes'

    def _follow_flow(self, start_id: str):
        """Execute nodes from start_id following edges until a dead end."""
        current = start_id
        while current and not self._stopped:
            while self._paused and not self._stopped:
                time.sleep(0.1)
            if self._stopped:
                return
            output_port = self.execute_node(current)
            if output_port == '':
                break
            next_edges = self.get_output_edges(current, output_port)
            if not next_edges:
                next_edges = self.get_output_edges(current, 'output')
            if not next_edges:
                break
            current = next_edges[0]['to_node']

    def execute_loop(self, node: dict, params: dict) -> str:
        loop_type = params.get('loop_type', 'count')

        if loop_type == 'count':
            count = params.get('count', 10)
            self.log('info', f'开始循环 {count} 次', node['id'])
            for i in range(count):
                if self._stopped:
                    return 'loop_end'
                while self._paused and not self._stopped:
                    time.sleep(0.1)
                self.log('info', f'循环第 {i+1}/{count} 次', node['id'])
                # Execute loop body
                body_edges = self.get_output_edges(node['id'], 'loop_body')
                if body_edges:
                    self._follow_flow(body_edges[0]['to_node'])
            self.log('info', '循环结束', node['id'])

        elif loop_type == 'infinite':
            self.log('info', '开始无限循环', node['id'])
            i = 0
            while not self._stopped:
                while self._paused and not self._stopped:
                    time.sleep(0.1)
                if self._stopped:
                    break
                body_edges = self.get_output_edges(node['id'], 'loop_body')
                if body_edges:
                    self._follow_flow(body_edges[0]['to_node'])
                i += 1
                if i > 100000:
                    break

        elif loop_type == 'condition':
            cond_params = params.get('condition', {}) or {}
            cond_type = cond_params.get('condition_type', 'always_true')
            self.log('info', f'开始条件循环 (类型: {cond_type})', node['id'])
            max_iter = params.get('count', 100)
            for i in range(max_iter):
                if self._stopped:
                    return 'loop_end'
                while self._paused and not self._stopped:
                    time.sleep(0.1)
                # Evaluate condition
                condition_met = False
                if cond_type == 'always_true':
                    condition_met = True
                elif cond_type == 'image_exists':
                    condition_met = self._check_image_exists(cond_params)
                    self.log('info', f'图片条件: {"满足" if condition_met else "不满足"}', node['id'])
                elif cond_type == 'window_exists':
                    condition_met = self._check_window_exists(cond_params)
                    self.log('info', f'窗口条件: {"满足" if condition_met else "不满足"}', node['id'])
                else:
                    condition_met = True

                if not condition_met:
                    self.log('info', f'条件不满足，退出循环 (第 {i+1} 次)', node['id'])
                    break

                self.log('info', f'条件循环第 {i+1} 次', node['id'])
                body_edges = self.get_output_edges(node['id'], 'loop_body')
                if body_edges:
                    self._follow_flow(body_edges[0]['to_node'])
            self.log('info', '条件循环结束', node['id'])

        return 'loop_end'

    def execute_screenshot(self, node: dict, params: dict) -> str:
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'screenshot_{timestamp}.png'
        pyautogui.screenshot(filename)
        self.log('info', f'截图已保存: {filename}', node['id'])
        return 'output'

    def execute_variable_set(self, node: dict, params: dict) -> str:
        name = params.get('name', '')
        value = params.get('value', '')
        self.variables[name] = value
        self.log('info', f'设置变量: {name} = "{value}"', node['id'])
        return 'output'

    def execute_variable_get(self, node: dict, params: dict) -> str:
        name = params.get('name', '')
        value = self.variables.get(name, '')
        self.log('info', f'读取变量: {name} = "{value}"', node['id'])
        return 'output'

    # ================================================================
    # Flow Executor
    # ================================================================

    def execute_node(self, node_id: str) -> str:
        """Execute a single node and return the output port to follow."""
        node = self.get_node_by_id(node_id)
        if not node:
            self.log('error', f'节点未找到: {node_id}')
            return ''

        if node.get('disabled'):
            self.log('info', f'跳过已禁用的节点: {node.get("type")}', node_id)
            return 'output'

        self.current_node_id = node_id
        self.node_status(node_id, 'running')
        ntype = node['type']
        params = node.get('params', {})
        settings = self._script.get('settings', {})
        max_retries = settings.get('max_retries', 0)
        retry_delay = settings.get('retry_delay', 1)

        default_delay = settings.get('default_delay', 0)

        try:
            for attempt in range(max_retries + 1):
                try:
                    if ntype == 'start':
                        result = 'output'
                    elif ntype == 'end':
                        result = ''
                    elif ntype == 'click':
                        result = self.execute_click(node, params)
                    elif ntype == 'image_click':
                        result = self.execute_image_click(node, params)
                    elif ntype == 'wait':
                        result = self.execute_wait(node, params)
                    elif ntype == 'input_text':
                        result = self.execute_input_text(node, params)
                    elif ntype == 'keyboard':
                        result = self.execute_keyboard(node, params)
                    elif ntype == 'swipe':
                        result = self.execute_swipe(node, params)
                    elif ntype == 'condition':
                        result = self.execute_condition(node, params)
                    elif ntype == 'loop':
                        result = self.execute_loop(node, params)
                    elif ntype == 'screenshot':
                        result = self.execute_screenshot(node, params)
                    elif ntype == 'variable_set':
                        result = self.execute_variable_set(node, params)
                    elif ntype == 'variable_get':
                        result = self.execute_variable_get(node, params)
                    elif ntype == 'sub_script':
                        self.log('info', '子脚本执行（预留功能）', node_id)
                        result = 'output'
                    else:
                        self.log('warn', f'未知节点类型: {ntype}', node_id)
                        result = 'output'

                    self.node_status(node_id, 'success')
                    if default_delay > 0:
                        time.sleep(default_delay / 1000.0)
                    return result

                except Exception as e:
                    self.log('error', f'执行出错 (尝试 {attempt+1}/{max_retries+1}): {str(e)}', node_id)
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                    else:
                        error_handling = settings.get('error_handling', 'stop')
                        self.node_status(node_id, 'error', str(e))
                        if error_handling == 'stop':
                            raise
                        elif error_handling == 'continue':
                            return 'output'
                        elif error_handling == 'retry':
                            return self.execute_node(node_id)

        except Exception as e:
            self.node_status(node_id, 'error', str(e))
            self.log('error', f'节点执行失败: {str(e)}', node_id)
            return ''

        return 'output'

    def run_flow(self):
        """Execute the entire flow from start node."""
        self.status = 'running'
        self.log('info', '脚本开始执行')

        start_nodes = [n for n in self._nodes if n['type'] == 'start']
        if not start_nodes:
            self.log('error', '未找到开始节点')
            self.status = 'error'
            return

        self._follow_flow(start_nodes[0]['id'])

        if self._stopped:
            self.log('info', '脚本已停止')
            self.status = 'stopped'
        else:
            self.log('info', '脚本执行完成')
            self.status = 'completed'

        self.current_node_id = None
        print(json.dumps({
            "type": "execution_done",
            "status": self.status,
            "timestamp": datetime.now().isoformat(),
        }), flush=True)

    # ================================================================
    # Main Entry Point
    # ================================================================

    def load_and_run(self, script_data: dict):
        """Load script and run it (blocking)."""
        self._script = script_data
        self._nodes = script_data.get('nodes', [])
        self._edges = script_data.get('edges', [])
        self._node_map = {n['id']: n for n in self._nodes}
        self.variables = dict(script_data.get('variables', {}))
        self.run_flow()

    def load_and_run_async(self, script_data: dict):
        """Load script and run it in a background thread."""
        self._script = script_data
        self._nodes = script_data.get('nodes', [])
        self._edges = script_data.get('edges', [])
        self._node_map = {n['id']: n for n in self._nodes}
        self.variables = dict(script_data.get('variables', {}))
        self._stopped = False
        self._paused = False
        self._flow_thread = threading.Thread(target=self.run_flow, daemon=True)
        self._flow_thread.start()

    def handle_message(self, msg: dict):
        """Handle control messages from parent process."""
        msg_type = msg.get('type', '')
        if msg_type == 'pause':
            self._paused = True
            self.status = 'paused'
            self.log('info', '引擎已暂停')
        elif msg_type == 'resume':
            self._paused = False
            self.status = 'running'
            self.log('info', '引擎已恢复')
        elif msg_type == 'stop':
            self._stopped = True
            self.status = 'stopped'
            self.log('info', '引擎正在停止...')
        elif msg_type == 'status':
            print(json.dumps({
                "type": "status_report",
                "status": self.status,
                "current_node_id": self.current_node_id,
                "variables": self.variables,
            }), flush=True)


def _stdin_reader(queue):
    """Read lines from stdin in a background thread."""
    try:
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8')
        for line in sys.stdin:
            line = line.strip()
            if line:
                try:
                    queue.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except (EOFError, OSError):
        pass

def main():
    import queue as q
    engine = ExecutionEngine()
    engine.log('info', '引擎已启动，等待指令...')

    msg_queue = q.Queue()
    reader_thread = threading.Thread(target=_stdin_reader, args=(msg_queue,), daemon=True)
    reader_thread.start()

    while True:
        try:
            try:
                msg = msg_queue.get(timeout=0.3)
            except q.Empty:
                if not reader_thread.is_alive():
                    break
                continue

            msg_type = msg.get('type', '')
            if msg_type == 'execute':
                script_data = msg.get('payload', {}).get('script', {})
                engine.load_and_run_async(script_data)
            else:
                engine.handle_message(msg)
        except (EOFError, KeyboardInterrupt):
            break

    engine.log('info', '引擎进程退出')


if __name__ == '__main__':
    main()
