"""DSL Executor — execute the AST produced by the DSL parser."""
import time
import sys
from typing import Any, Optional
import pyautogui

from .parser import ASTNode, DSLParser
from .resolver import ConditionResolver
from core.screenshot import ScreenshotManager
from core.template_cache import TemplateCache
from recognition.ocr import OCRRecognizer
from recognition.matcher import MultiScaleMatcher
from interaction.click_smart import SmartClicker
from interaction.human_behavior import HumanBehaviorSimulator
from logic.variables import VariableSystem
from logic.retry import RetryMechanism
from logic.timeout import TimeoutControl


class DSLExecutor:
    """Execute DSL AST nodes by dispatching to engine functions.

    Provides the runtime for the pseudo-code language.
    """

    def __init__(self, screenshot_mgr: Optional[ScreenshotManager] = None,
                 variables: Optional[VariableSystem] = None):
        self.screenshot = screenshot_mgr or ScreenshotManager()
        self.variables = variables or VariableSystem()
        self.ocr = OCRRecognizer(self.screenshot)
        self.matcher = MultiScaleMatcher(self.screenshot)
        self.clicker = SmartClicker(self.screenshot)
        self.human = HumanBehaviorSimulator()
        self.resolver = ConditionResolver(
            self.screenshot, self.ocr, self.matcher,
            variables=self.variables
        )
        self.retry = RetryMechanism()
        self.timeout = TimeoutControl()
        self._stopped = False
        self._paused = False
        self._log_func = None

    def set_logger(self, log_func: callable):
        """Set a logging function: log_func(level, message)."""
        self._log_func = log_func

    def _log(self, level: str, message: str):
        if self._log_func:
            self._log_func(level, message)
        else:
            print(f"[{level.upper()}] {message}", flush=True)

    def execute(self, code_or_ast: Any) -> bool:
        """Execute DSL code (string) or an already-parsed AST.

        Args:
            code_or_ast: DSL string or ASTNode.

        Returns:
            True if execution completed successfully.
        """
        if isinstance(code_or_ast, str):
            parser = DSLParser()
            ast = parser.parse(code_or_ast)
        elif isinstance(code_or_ast, ASTNode):
            ast = code_or_ast
        else:
            raise TypeError("Expected str or ASTNode")

        self._stopped = False
        self._paused = False

        try:
            self._execute_node(ast)
            return not self._stopped
        except Exception as e:
            self._log('error', f'DSL execution failed: {e}')
            return False

    def stop(self):
        self._stopped = True

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def _check_controls(self):
        """Check pause/stop state."""
        while self._paused and not self._stopped:
            time.sleep(0.1)
        if self._stopped:
            raise StopIteration("Execution stopped")

    def _execute_node(self, node: ASTNode):
        """Dispatch an AST node to the appropriate executor."""
        if self._stopped:
            return
        self._check_controls()

        node_type = node.node_type

        if node_type == 'program':
            for child in node.children:
                self._execute_node(child)

        elif node_type == 'noop':
            pass

        elif node_type == 'click':
            x = node.x + getattr(node, 'offset_x', 0)
            y = node.y + getattr(node, 'offset_y', 0)
            button = getattr(node, 'button', 'left')
            self._log('info', f'Click: ({x}, {y}) [{button}]')
            pyautogui.click(x, y, button=button)

        elif node_type == 'image_click':
            path = getattr(node, 'path', '')
            tmpl = TemplateCache().get(path)
            if tmpl is not None:
                found, positions, _ = self.matcher.find_best(
                    template_img=tmpl, similarity=0.85
                )
                if found:
                    cx, cy = positions[0]
                    ox = getattr(node, 'offset_x', 0)
                    oy = getattr(node, 'offset_y', 0)
                    pyautogui.click(cx + ox, cy + oy)
                    self._log('info', f'Image click: {path} at ({cx}, {cy})')
                else:
                    self._log('warn', f'Image not found: {path}')

        elif node_type == 'input_text':
            text = getattr(node, 'text', '')
            pyautogui.write(text)
            self._log('info', f'Type: "{text[:30]}"')

        elif node_type == 'wait':
            duration = getattr(node, 'duration', 1)
            self._log('info', f'Wait: {duration}s')
            time.sleep(duration)

        elif node_type == 'wait_image':
            path = getattr(node, 'path', '')
            timeout = getattr(node, 'timeout', 10)
            self._log('info', f'Wait for image: {path}')
            start = time.time()
            while time.time() - start < timeout:
                if self._stopped:
                    return
                self._check_controls()
                found, _, _ = self.matcher.find_best(
                    template_path=path, similarity=0.85
                )
                if found:
                    self._log('info', 'Image appeared')
                    return
                time.sleep(0.3)
            self._log('warn', f'Image wait timeout: {path}')

        elif node_type == 'wait_random':
            dmin = getattr(node, 'duration_min', 0.5)
            dmax = getattr(node, 'duration_max', 2.0)
            import random
            d = random.uniform(dmin, dmax)
            time.sleep(d)

        elif node_type == 'keyboard':
            keys = getattr(node, 'keys', [])
            modifiers = getattr(node, 'modifiers', [])
            if modifiers:
                pyautogui.hotkey(*modifiers, *keys)
            else:
                for k in keys:
                    pyautogui.press(k)
            self._log('info', f'Key: {"+".join(modifiers + keys)}')

        elif node_type == 'swipe':
            pyautogui.drag(
                getattr(node, 'x2', 0) - getattr(node, 'x1', 0),
                getattr(node, 'y2', 0) - getattr(node, 'y1', 0),
                duration=0.3
            )

        elif node_type == 'screenshot':
            import datetime
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            path = getattr(node, 'path', None) or f'screenshot_{ts}.png'
            pyautogui.screenshot(path)
            self._log('info', f'Screenshot: {path}')

        elif node_type == 'variable_set':
            name = node.name
            value = self.variables.interpolate(str(node.value))
            self.variables.set(name, value)
            self._log('info', f'Set {name} = {value}')

        elif node_type == 'sub_script':
            name = getattr(node, 'name', '')
            self._log('info', f'Call sub-script: {name}')

        elif node_type == 'delay':
            pyautogui.PAUSE = getattr(node, 'duration', 0.5)

        elif node_type == 'scroll':
            pyautogui.scroll(getattr(node, 'clicks', -3))

        # Block statements
        elif node_type == 'loop':
            count = getattr(node, 'count', -1)
            body = getattr(node, 'body', [])
            condition = getattr(node, 'condition', None)

            if condition:
                # LOOP WHILE condition:
                while not self._stopped:
                    self._check_controls()
                    if not self.resolver.resolve(condition):
                        break
                    for child in body:
                        self._execute_node(child)
            elif count < 0:
                # Infinite loop
                while not self._stopped:
                    self._check_controls()
                    for child in body:
                        self._execute_node(child)
            else:
                for _ in range(count):
                    if self._stopped:
                        break
                    self._check_controls()
                    for child in body:
                        self._execute_node(child)

        elif node_type == 'if':
            condition = node.condition
            body = getattr(node, 'body', [])
            else_body = getattr(node, 'else_body', [])

            if self.resolver.resolve(condition):
                for child in body:
                    self._execute_node(child)
            elif else_body:
                for child in else_body:
                    self._execute_node(child)

        elif node_type == 'while':
            body = getattr(node, 'body', [])
            while not self._stopped:
                self._check_controls()
                if not self.resolver.resolve(node.condition):
                    break
                for child in body:
                    self._execute_node(child)

        elif node_type == 'until':
            body = getattr(node, 'body', [])
            while not self._stopped:
                self._check_controls()
                for child in body:
                    self._execute_node(child)
                if self.resolver.resolve(node.condition):
                    break

        else:
            self._log('warn', f'Unknown node type: {node_type}')
