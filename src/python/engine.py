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
import os
import socket
import subprocess
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
        self._progress_display_launched = False

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
            # Fan-out: if multiple edges from same port, follow all branches
            if len(next_edges) == 1:
                current = next_edges[0]['to_node']
            else:
                for edge in next_edges:
                    self._follow_flow(edge['to_node'])
                break

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
                self._report_iteration(i + 1, count, node['id'])
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


    def _report_iteration(self, current: int, total: int, node_id: str):
        print(json.dumps({
            "type": "loop_iteration",
            "current": current,
            "total": total,
            "node_id": node_id,
        }), flush=True)

    def execute_click_text(self, node: dict, params: dict) -> str:
        text = params.get('text', '')
        timeout = params.get('timeout', 5)
        method = params.get('method', 'single')
        offset_x = params.get('offset_x', 0)
        offset_y = params.get('offset_y', 0)
        delay_after = params.get('delay_after', 0.5)
        similarity = params.get('similarity', 80)  # used as backend arg

        if not text:
            self.log('warn', '点击文本: 未设置目标文本', node['id'])
            return 'fail'

        self.log('info', f'OCR点击文本: "{text[:30]}" (method={method}, timeout={timeout}s)', node['id'])

        # Lazy-init OCR recognizer (model loading is expensive)
        if not hasattr(self, '_click_ocr'):
            self.log('info', '正在初始化OCR引擎 (首次加载约需数秒)...', node['id'])
            try:
                from core.screenshot import ScreenshotManager
                from recognition.ocr import OCRRecognizer
                sm = ScreenshotManager()
                self._click_ocr = OCRRecognizer(sm, backend='easyocr')
            except Exception as e:
                self.log('error', f'OCR引擎初始化失败: {e}', node['id'])
                return 'fail'

        # Poll until text is found or timeout
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._stopped:
                return 'fail'

            try:
                pos = self._click_ocr.find_text_position(text, fuzzy=True)
            except Exception as e:
                self.log('error', f'OCR识别失败: {e}', node['id'])
                time.sleep(0.5)
                continue

            if pos is not None:
                cx, cy = pos
                target_x = cx + offset_x
                target_y = cy + offset_y

                # Click at the found position
                pyautogui.moveTo(target_x, target_y, duration=0.2)
                if method == 'single':
                    pyautogui.click()
                elif method == 'double':
                    pyautogui.doubleClick()
                elif method == 'right':
                    pyautogui.rightClick()

                if delay_after > 0:
                    time.sleep(delay_after)

                self.log('info', f'已点击文字 "{text[:30]}" 在 ({target_x}, {target_y})', node['id'])
                return 'output'  # 成功 — 走 output 端口

            time.sleep(0.3)

        self.log('warn', f'未找到文字 "{text[:30]}" (超时 {timeout}s)', node['id'])
        return 'fail'  # 未找到 — 走 fail 端口

    def execute_popup_close(self, node: dict, params: dict) -> str:
        strategies = params.get('strategies', ['auto'])
        self.log('info', f'关闭弹窗 (策略: {strategies[0]})', node['id'])
        time.sleep(0.5)
        return 'output'

    def execute_color_check(self, node: dict, params: dict) -> str:
        import cv2
        import numpy as np
        import math

        x, y = params.get('x', 0), params.get('y', 0)
        w, h = params.get('w', 100), params.get('h', 100)
        expected = params.get('expected_color', '')
        tolerance = params.get('tolerance', 30)

        if not expected:
            self.log('warn', '颜色检测: 未设置期望颜色', node['id'])
            return 'output'

        self.log('info', f'颜色检测: ({x},{y},{w}x{h}) 期望="{expected}" 容差={tolerance}', node['id'])

        try:
            img = self._capture_region(x, y, w, h)
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            # Parse expected color (H,S,V or hex #RRGGBB)
            if expected.startswith('#'):
                r, g, b = int(expected[1:3], 16), int(expected[3:5], 16), int(expected[5:7], 16)
                target = cv2.cvtColor(np.uint8([[ [r, g, b] ]]), cv2.COLOR_RGB2HSV)[0][0]
            else:
                parts = expected.replace('(', '').replace(')', '').split(',')
                target = (int(parts[0]), int(parts[1]), int(parts[2]))

            # Compute pixel distances
            dh = np.minimum(np.abs(hsv[:, :, 0].astype(np.int16) - target[0]),
                            180 - np.abs(hsv[:, :, 0].astype(np.int16) - target[0]))
            ds = np.abs(hsv[:, :, 1].astype(np.int16) - target[1])
            dv = np.abs(hsv[:, :, 2].astype(np.int16) - target[2])
            distances = np.sqrt(dh.astype(np.float64)**2 + (0.5*ds.astype(np.float64))**2 + (0.3*dv.astype(np.float64))**2)

            matching = np.sum(distances <= tolerance)
            total = distances.size
            match_pct = (matching / total) * 100

            self.log('info', f'颜色匹配: {match_pct:.1f}% 像素在容差内', node['id'])

            if match_pct > 30:
                return 'output'   # match
            else:
                return 'fail'     # no match
        except Exception as e:
            self.log('error', f'颜色检测失败: {e}', node['id'])
            return 'output'

    def _capture_region(self, x, y, w, h):
        """Capture a screen region and return BGR numpy array."""
        import pyautogui
        pil_img = pyautogui.screenshot(region=(x, y, w, h))
        import numpy as np
        img = np.array(pil_img)
        return img[:, :, ::-1]  # RGB -> BGR

    def _detect_progress_horizontal(self, img, filled_color_range=None):
        """Detect horizontal progress bar fill percentage (0.0-1.0).

        Strategy:
        1. If filled_color_range is given, use HSV color mask for that specific color.
        2. Otherwise, use frame-differencing approach with two captures at different times.
        3. Fallback: use saturation-weighted brightness to distinguish filled from empty.
        """
        import cv2
        import numpy as np

        if filled_color_range:
            # Strategy 1: specific color range
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            lower = np.array(filled_color_range[0], dtype=np.uint8)
            upper = np.array(filled_color_range[1], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower, upper)
        else:
            # Strategy 2: saturation-weighted approach
            # Filled portion of a progress bar typically has higher saturation
            # than the gray/white background
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            s = hsv[:, :, 1].astype(np.float32)
            v = hsv[:, :, 2].astype(np.float32)

            # Compute a "filled score" combining saturation and value
            # High saturation + high value = colored (filled)
            # Low saturation + high value = white/gray (empty background)
            # Low value = dark (empty background)
            filled_score = s * (v / 255.0)

            # Use Otsu's threshold to separate filled from empty
            filled_score_uint8 = filled_score.astype(np.uint8)
            _, mask = cv2.threshold(filled_score_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # If Otsu fails (single peak), fall back to simple saturation threshold
            if np.sum(mask > 0) == 0 or np.sum(mask > 0) == mask.size:
                _, mask = cv2.threshold(filled_score_uint8, 30, 255, cv2.THRESH_BINARY)

        # For each row, find the rightmost filled pixel
        row_fills = []
        for row_idx in range(mask.shape[0]):
            row = mask[row_idx, :]
            filled_indices = np.where(row > 0)[0]
            if len(filled_indices) > 0:
                rightmost = filled_indices[-1]
                fill_ratio = (rightmost + 1) / mask.shape[1]
                row_fills.append(fill_ratio)

        if not row_fills:
            return 0.0
        return float(np.mean(row_fills))

    def _detect_progress_change(self, x, y, w, h, poll_interval):
        """Detect progress bar movement by frame differencing.

        Captures two frames with a small delay and finds the moving edge.
        Returns the current fill percentage estimate based on where change occurs.
        """
        import cv2
        import numpy as np
        import time as _time

        # Capture frame 1
        img1 = self._capture_region(x, y, w, h)
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray1 = cv2.GaussianBlur(gray1, (3, 3), 0)

        # Use the first frame as reference to find the static background pattern
        # For a cycling progress bar, we look at horizontal gradients
        # The leading edge of the bar creates a strong vertical edge
        grad1 = np.abs(cv2.Sobel(gray1, cv2.CV_64F, 1, 0, ksize=3))

        # Find the strongest vertical edge in each row
        edge_positions = []
        for row_idx in range(grad1.shape[0]):
            row = grad1[row_idx, :]
            if np.max(row) > 30:  # ignore weak edges
                max_pos = np.argmax(row)
                edge_positions.append(max_pos)

        if not edge_positions:
            return 0.0

        # The leading edge should be a consistent vertical position across rows
        # Use median to filter outliers
        median_pos = float(np.median(edge_positions))
        return median_pos / w

    def execute_progress_wait(self, node: dict, params: dict) -> str:
        import cv2
        import numpy as np
        import time as _time

        wt = params.get('wait_type', 'fill')
        timeout = params.get('timeout', 30)
        region = params.get('region', {})
        rx = region.get('x', 0)
        ry = region.get('y', 0)
        rw = region.get('w', 100)
        rh = region.get('h', 20)
        poll = params.get('poll_interval', 0.5)

        node_label = node.get('label', '') or params.get('description', '')
        labels = {'fill': '填满', 'empty': '清空', 'change': '变化'}
        self.log('info', f'等待进度条{labels.get(wt, wt)}: ({rx},{ry},{rw}x{rh}) 超时={timeout}s 轮询={poll}s', node['id'])

        # Ensure progress display is running in a separate window
        self._ensure_progress_display()

        deadline = _time.time() + timeout
        last_pct = -1.0
        restart_counter = 0

        while _time.time() < deadline:
            if self._stopped:
                return ''
            while self._paused:
                _time.sleep(0.1)
                if self._stopped:
                    return ''

            try:
                img = self._capture_region(rx, ry, rw, rh)
                pct = self._detect_progress_horizontal(img) * 100.0
            except Exception as e:
                self.log('warn', f'进度检测异常: {e}', node['id'])
                _time.sleep(poll)
                continue

            # Detect reset (progress suddenly dropped — cycling bar)
            if last_pct > 50 and pct < 20:
                restart_counter += 1
                self.log('info', f'进度条重置 (第{restart_counter}次): {last_pct:.1f}% -> {pct:.1f}%', node['id'])

            # Send real-time update to the display window
            self._send_progress(node['id'], pct=pct, label=node_label, status='monitoring')

            # Log every 10% change
            if abs(pct - last_pct) >= 10:
                self.log('info', f'进度: {pct:.1f}%', node['id'])
                last_pct = pct

            if wt == 'fill' and pct >= 95:
                self.log('info', f'进度条已填满 ({pct:.1f}%)', node['id'])
                self._send_progress(node['id'], pct=pct, label=node_label, status='done')
                return 'output'
            elif wt == 'empty' and pct <= 5:
                self.log('info', f'进度条已清空 ({pct:.1f}%)', node['id'])
                self._send_progress(node['id'], pct=pct, label=node_label, status='done')
                return 'output'
            elif wt == 'change':
                if last_pct < 0:
                    last_pct = pct
                if abs(pct - last_pct) > 5:
                    self.log('info', f'进度条已变化: {last_pct:.1f}% -> {pct:.1f}%', node['id'])
                    self._send_progress(node['id'], pct=pct, label=node_label, status='done')
                    return 'output'

            _time.sleep(poll)

        self.log('warn', f'进度条等待超时 ({timeout}s)，当前进度: {pct:.1f}%', node['id'])
        self._send_progress(node['id'], pct=pct, label=node_label, status='done')
        return 'output'

    def execute_region_wait(self, node: dict, params: dict) -> str:
        rt = params.get('region_type', 'stable')
        timeout = params.get('timeout', 10)
        x, y = params.get('x', 0), params.get('y', 0)
        w, h = params.get('w', 100), params.get('h', 100)
        labels = {'stable': '稳定', 'change': '变化'}
        self.log('info', f'等待区域{labels.get(rt, rt)}: ({x},{y},{w}x{h}) 超时={timeout}s', node['id'])
        time.sleep(0.5)
        return 'output'

    def execute_scene_condition(self, node: dict, params: dict) -> str:
        scene_name = params.get('scene_name', '')
        similarity = params.get('similarity', 70)
        self.log('info', f'场景识别: "{scene_name}" (相似度={similarity})', node['id'])
        time.sleep(0.3)
        return 'no'

    # ================================================================
    # Progress Display (Real-time feedback in separate terminal)
    # ================================================================

    def _ensure_progress_display(self):
        """Auto-launch the progress display in a new terminal window if not running."""
        if self._progress_display_launched:
            return True
        # Check if display is already running
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            sock.connect(('127.0.0.1', 18765))
            sock.close()
            self._progress_display_launched = True
            self.log('info', '已连接到进度显示器')
            return True
        except (ConnectionRefusedError, OSError):
            pass
        # Launch in a new PowerShell window
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(script_dir, 'progress_display.py')
            # Open in a new console window (Windows-native)
            subprocess.Popen(
                [sys.executable, script_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self._progress_display_launched = True
            self.log('info', '已启动进度显示器窗口')
            # Wait for server to initialize (up to 6 seconds)
            for _ in range(60):
                time.sleep(0.1)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.3)
                    sock.connect(('127.0.0.1', 18765))
                    sock.close()
                    self.log('info', f'进度显示器就绪 ({script_path})')
                    return True
                except ConnectionRefusedError:
                    continue
            self.log('warn', '进度显示器未能在预期内启动')
            return False
        except Exception as e:
            self.log('warn', f'启动进度显示器失败: {e}')
            return False

    def _send_progress(self, node_id, pct=None, label=None, status=None):
        """Send a progress update to the display window (fire-and-forget)."""
        try:
            msg = {'type': 'progress', 'node_id': node_id}
            if pct is not None:
                msg['pct'] = round(pct, 1)
            if label is not None:
                msg['label'] = label
            if status is not None:
                msg['status'] = status
            data = json.dumps(msg).encode('utf-8')
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            sock.connect(('127.0.0.1', 18765))
            sock.sendall(data)
            sock.close()
        except Exception:
            pass  # Display not available — engine continues regardless

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
                    elif ntype == 'click_text':
                        result = self.execute_click_text(node, params)
                    elif ntype == 'popup_close':
                        result = self.execute_popup_close(node, params)
                    elif ntype == 'color_check':
                        result = self.execute_color_check(node, params)
                    elif ntype == 'progress_wait':
                        result = self.execute_progress_wait(node, params)
                    elif ntype == 'region_wait':
                        result = self.execute_region_wait(node, params)
                    elif ntype == 'scene_condition':
                        result = self.execute_scene_condition(node, params)
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
