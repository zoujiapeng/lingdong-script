"""DSL Parser — parse automation pseudo-code into an AST for execution.

Grammar:
    LINE := ACTION | LOOP | IF | WHILE | UNTIL | BLOCK_COMMENT
    ACTION := "click" args
            | "clickImage" args
            | "type" args
            | "wait" args
            | "key" args
            | "swipe" args
            | "screenshot" args
            | "set" var "=" value
            | "call" script_name

    LOOP := "LOOP" count ":" NEWLINE INDENT lines DEDENT
    IF := "IF" condition ":" NEWLINE INDENT lines DEDENT
         ["ELSE:" NEWLINE INDENT lines DEDENT]
    WHILE := "WHILE" condition ":" NEWLINE INDENT lines DEDENT
    UNTIL := "UNTIL" condition ":" NEWLINE INDENT lines DEDENT

    condition := text:/img:/color:/var: prefixed expressions

Comments start with # or //
"""
import re
from typing import Any, Optional


class ASTNode:
    """Base class for all AST nodes."""

    def __init__(self, node_type: str, **kwargs):
        self.node_type = node_type
        self.children: list[ASTNode] = []
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"ASTNode({self.node_type}, {self.__dict__})"


class DSLParser:
    """Parse automation pseudo-code into an AST.

    The parser handles indentation-based blocks (Python-like syntax)
    and generates a tree of ASTNode objects for execution.
    """

    def __init__(self):
        self._lines: list[str] = []
        self._pos = 0

    def parse(self, code: str) -> ASTNode:
        """Parse DSL code into an AST.

        Args:
            code: Multi-line DSL string.

        Returns:
            Root AST node containing all statements.
        """
        # Normalize line endings and split
        code = code.replace('\r\n', '\n').replace('\r', '\n')
        self._lines = code.split('\n')
        self._pos = 0

        root = ASTNode('program')
        root.children = self._parse_block(0)

        return root

    def _parse_block(self, expected_indent: int) -> list[ASTNode]:
        """Parse a block of indented lines."""
        nodes = []

        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith('#') or stripped.startswith('//'):
                self._pos += 1
                continue

            # Calculate indentation
            indent = len(line) - len(line.lstrip())
            if indent < expected_indent:
                break  # Dedent: end of block
            if indent > expected_indent:
                # This shouldn't happen in well-formed code
                raise SyntaxError(
                    f"Unexpected indentation at line {self._pos + 1}: "
                    f"expected {expected_indent}, got {indent}"
                )

            # Parse the statement
            saved_pos = self._pos
            node = self._parse_statement(stripped)
            if node:
                nodes.append(node)

            # Block statements (if/loop/while/until) manage pos internally;
            # only advance if the statement didn't advance it.
            if self._pos == saved_pos:
                self._pos += 1

        return nodes

    def _parse_statement(self, line: str) -> Optional[ASTNode]:
        """Parse a single statement line."""
        # Block statements
        if line.upper().startswith('LOOP ') and line.endswith(':'):
            return self._parse_loop(line)
        if line.upper().startswith('IF ') and line.endswith(':'):
            return self._parse_if(line)
        if line.upper().startswith('WHILE ') and line.endswith(':'):
            return self._parse_while(line)
        if line.upper().startswith('UNTIL ') and line.endswith(':'):
            return self._parse_until(line)
        if line.upper().startswith('ELSEIF ') and line.endswith(':'):
            raise SyntaxError("ELSEIF must follow IF block")
        if line.upper().startswith('ELSE'):
            raise SyntaxError("ELSE must follow IF block")

        # Action statements
        return self._parse_action(line)

    def _parse_action(self, line: str) -> Optional[ASTNode]:
        """Parse a single action command."""
        # Try to match known action patterns
        patterns = [
            (r'^click\s+(\d+)\s*,?\s*(\d+)(?:\s+.*)?$', 'click', self._make_click_node),
            (r'^clickImage\s+(.+)$', 'image_click', self._make_image_click_node),
            (r'^type\s+(.+)$', 'input_text', self._make_input_text_node),
            (r'^wait\s+([\d.]+)\s*$', 'wait', lambda m: ASTNode('wait', duration=float(m.group(1)))),
            (r'^wait\s+image\s+(.+)$', 'wait_image', self._make_wait_image_node),
            (r'^wait\s+([\d.]+)\s*-\s*([\d.]+)\s*$', 'wait_random',
             lambda m: ASTNode('wait', duration_min=float(m.group(1)), duration_max=float(m.group(2)))),
            (r'^key\s+(.+)$', 'keyboard', self._make_keyboard_node),
            (r'^swipe\s+(\d+)\s*,?\s*(\d+)\s+(\d+)\s*,?\s*(\d+)\s*$', 'swipe',
             lambda m: ASTNode('swipe', x1=int(m.group(1)), y1=int(m.group(2)),
                               x2=int(m.group(3)), y2=int(m.group(4)))),
            (r'^screenshot\s*$', 'screenshot', lambda m: ASTNode('screenshot')),
            (r'^screenshot\s+(.+)$', 'screenshot',
             lambda m: ASTNode('screenshot', path=m.group(1).strip().strip('"\''))),
            (r'^set\s+(\w+)\s*=\s*(.+)$', 'variable_set',
             lambda m: ASTNode('variable_set', name=m.group(1), value=m.group(2).strip())),
            (r'^call\s+(.+)$', 'sub_script',
             lambda m: ASTNode('sub_script', name=m.group(1).strip())),
            (r'^scroll\s*(-?\d+)\s*$', 'scroll',
             lambda m: ASTNode('scroll', clicks=int(m.group(1)))),
            (r'^delay\s+([\d.]+)\s*$', 'delay',
             lambda m: ASTNode('delay', duration=float(m.group(1)))),
        ]

        for pattern, node_type, factory in patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                node = factory(match)
                if node:
                    node.raw_line = line
                return node

        # Unknown statement: create a noop node
        return ASTNode('noop', raw_line=line)

    def _detect_block_indent(self) -> int:
        """Look ahead to find the indentation of the next non-empty, non-comment line.
        Returns the current indent + 1 as fallback if no body line found.
        """
        current_indent = self._lines[self._pos].rstrip() if self._pos < len(self._lines) else 0
        current_indent = len(current_indent) - len(current_indent.lstrip()) if current_indent else 0
        for i in range(self._pos + 1, len(self._lines)):
            line = self._lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith('//'):
                continue
            body_indent = len(line) - len(line.lstrip())
            if body_indent > current_indent:
                return body_indent
            break
        return current_indent + 4  # fallback

    def _parse_loop(self, line: str) -> ASTNode:
        """Parse a LOOP block: LOOP count: ..."""
        match = re.match(r'LOOP\s+(\d+|inf|infinite)\s*:', line, re.IGNORECASE)
        if not match:
            # Try LOOP WHILE condition:
            cond_match = re.match(r'LOOP\s+WHILE\s+(.+)\s*:', line, re.IGNORECASE)
            if cond_match:
                return self._make_while_loop(cond_match.group(1).strip())
            # Default to infinite loop
            count = -1  # infinite
        else:
            count_str = match.group(1)
            count = -1 if count_str.lower() in ('inf', 'infinite') else int(count_str)

        block_indent = self._detect_block_indent()
        self._pos += 1
        body = self._parse_block(block_indent)

        return ASTNode('loop', count=count, body=body)

    def _parse_if(self, line: str) -> ASTNode:
        """Parse an IF/ELSE block."""
        match = re.match(r'IF\s+(.+)\s*:', line, re.IGNORECASE)
        if not match:
            raise SyntaxError(f"Invalid IF syntax: {line}")
        condition = match.group(1).strip()

        current_indent = len(self._lines[self._pos]) - len(self._lines[self._pos].lstrip())
        block_indent = self._detect_block_indent()
        self._pos += 1
        body = self._parse_block(block_indent)

        # Check for ELSE or ELSEIF
        else_body = []
        while self._pos < len(self._lines):
            peek = self._lines[self._pos].strip()
            if not peek or peek.startswith('#') or peek.startswith('//'):
                self._pos += 1
                continue

            peek_indent = len(self._lines[self._pos]) - len(self._lines[self._pos].lstrip())
            if peek_indent < current_indent:
                break
            if peek_indent > current_indent:
                break

            if peek.upper().startswith('ELIF ') or peek.upper().startswith('ELSEIF '):
                self._pos += 1
                else_body = self._parse_block(block_indent)
                condition2 = re.match(r'(?:ELIF|ELSEIF)\s+(.+)\s*:', peek, re.IGNORECASE)
                cond2 = condition2.group(1).strip() if condition2 else condition
                return ASTNode('if', condition=condition, body=body,
                               else_body=else_body, else_condition=cond2)

            if peek.upper() == 'ELSE:' or peek.upper().startswith('ELSE'):
                self._pos += 1
                else_body = self._parse_block(block_indent)
            break

        return ASTNode('if', condition=condition, body=body, else_body=else_body)

    def _parse_while(self, line: str) -> ASTNode:
        """Parse a WHILE block."""
        match = re.match(r'WHILE\s+(.+)\s*:', line, re.IGNORECASE)
        if not match:
            raise SyntaxError(f"Invalid WHILE syntax: {line}")
        condition = match.group(1).strip()

        current_indent = len(self._lines[self._pos]) - len(self._lines[self._pos].lstrip())
        block_indent = self._detect_block_indent()
        self._pos += 1
        body = self._parse_block(block_indent)

        return ASTNode('while', condition=condition, body=body)

    def _parse_until(self, line: str) -> ASTNode:
        """Parse an UNTIL block (execute body until condition is true)."""
        match = re.match(r'UNTIL\s+(.+)\s*:', line, re.IGNORECASE)
        if not match:
            raise SyntaxError(f"Invalid UNTIL syntax: {line}")
        condition = match.group(1).strip()

        current_indent = len(self._lines[self._pos]) - len(self._lines[self._pos].lstrip())
        block_indent = self._detect_block_indent()
        self._pos += 1
        body = self._parse_block(block_indent)

        return ASTNode('until', condition=condition, body=body)

    def _make_while_loop(self, condition: str) -> ASTNode:
        """Create a while-loop node."""
        current_indent = len(self._lines[self._pos]) - len(self._lines[self._pos].lstrip())
        block_indent = self._detect_block_indent()
        self._pos += 1
        body = self._parse_block(block_indent)
        return ASTNode('loop', count=-1, condition=condition, body=body, loop_type='condition')

    def _make_click_node(self, match) -> ASTNode:
        x = int(match.group(1))
        y = int(match.group(2))
        # Parse optional offset and button
        rest = match.group(0)
        offset_x = 0
        offset_y = 0
        button = 'left'
        clicks = 1
        if 'offset' in rest:
            om = re.search(r'offset\s*[:=]\s*(-?\d+)\s*,?\s*(-?\d+)', rest)
            if om:
                offset_x, offset_y = int(om.group(1)), int(om.group(2))
        if 'right' in rest.lower():
            button = 'right'
        if 'double' in rest.lower():
            clicks = 2
        return ASTNode('click', x=x, y=y, offset_x=offset_x, offset_y=offset_y,
                       button=button, clicks=clicks)

    def _make_image_click_node(self, match) -> ASTNode:
        rest = match.group(1).strip()
        node = ASTNode('image_click')
        # Could be a path or path with params
        if rest.startswith('"') or rest.startswith("'"):
            node.path = rest[1:-1]
        else:
            node.path = rest.split()[0] if rest else ''
        if 'offset' in rest:
            om = re.search(r'offset\s*[:=]\s*(-?\d+)\s*,?\s*(-?\d+)', rest)
            if om:
                node.offset_x, node.offset_y = int(om.group(1)), int(om.group(2))
        return node

    def _make_input_text_node(self, match) -> ASTNode:
        text = match.group(1).strip()
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith("'") and text.endswith("'")):
            text = text[1:-1]
        return ASTNode('input_text', text=text)

    def _make_wait_image_node(self, match) -> ASTNode:
        path = match.group(1).strip()
        if (path.startswith('"') and path.endswith('"')) or \
           (path.startswith("'") and path.endswith("'")):
            path = path[1:-1]
        return ASTNode('wait', wait_type='image', path=path)

    def _make_keyboard_node(self, match) -> ASTNode:
        rest = match.group(1).strip()
        keys = rest.replace('+', ' ').split()
        modifiers = []
        main_keys = []
        mod_set = {'ctrl', 'alt', 'shift', 'win', 'control'}
        for k in keys:
            if k.lower() in mod_set:
                modifiers.append(k.lower())
            else:
                main_keys.append(k)
        return ASTNode('keyboard', keys=main_keys, modifiers=modifiers)

    def format_ast(self, node: ASTNode, indent: int = 0) -> str:
        """Format an AST node as a readable string (for debugging)."""
        prefix = '  ' * indent
        result = f"{prefix}{node.node_type}"

        if hasattr(node, 'raw_line') and node.raw_line:
            result += f"  # {node.raw_line}"

        result += '\n'

        for child in node.children:
            result += self.format_ast(child, indent + 1)

        if hasattr(node, 'body') and node.body:
            for child in node.body:
                result += self.format_ast(child, indent + 1)

        if hasattr(node, 'else_body') and node.else_body:
            result += f"{prefix}  ELSE:\n"
            for child in node.else_body:
                result += self.format_ast(child, indent + 2)

        return result
