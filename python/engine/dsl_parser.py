"""
DSL Parser: 将 Lobster DSL 文本解析为 AST
支持: CLICK, WAIT, LOOP, IF, END, RUN
       SUBROUTINE, CALL, IMPORT, RETURN
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional
from enum import Enum


class NodeType(str, Enum):
    CLICK = "CLICK"
    TYPE = "TYPE"
    LAUNCH = "LAUNCH"
    WAIT = "WAIT"
    LOOP = "LOOP"
    IF = "IF"
    BREAK = "BREAK"
    END = "END"
    RUN = "RUN"
    SEQUENCE = "SEQUENCE"
    SUBROUTINE = "SUBROUTINE"
    CALL = "CALL"
    IMPORT = "IMPORT"
    RETURN = "RETURN"
    WHEN = "WHEN"
    PARALLEL = "PARALLEL"
    WITH = "WITH"
    SCREENSHOT = "SCREENSHOT"
    WAITSCREEN = "WAITSCREEN"
    SCREENSTABLE = "SCREENSTABLE"
    HOTKEY = "HOTKEY"
    FOCUS = "FOCUS"
    SET = "SET"
    OCR_FIND = "OCR_FIND"
    OCR_EXTRACT = "OCR_EXTRACT"
    REGION_SELECT = "REGION_SELECT"
    IMAGE_FIND = "IMAGE_FIND"
    TEMPLATE_CAPTURE = "TEMPLATE_CAPTURE"
    PROGRESS_WAIT = "PROGRESS_WAIT"
    PROGRESS_INIT = "PROGRESS_INIT"
    PROGRESS_RELOCATE = "PROGRESS_RELOCATE"
    IMAGE_CLICK = "IMAGE_CLICK"
    # 合并自灵动脚本的额外指令
    SWIPE = "SWIPE"
    SCROLL = "SCROLL"
    DELAY = "DELAY"
    WAIT_RANDOM = "WAIT_RANDOM"
    # 前端节点类型映射（补充）
    POPUP_CLOSE = "POPUP_CLOSE"
    COLOR_CHECK = "COLOR_CHECK"
    REGION_WAIT = "REGION_WAIT"
    SCENE_CONDITION = "SCENE_CONDITION"
    VLM_CONDITION = "VLM_CONDITION"
    FINDCANVAS = "FINDCANVAS"
    DRAG = "DRAG"
    # Registry 关键字回退（无对应枚举成员时）
    REGISTRY_FEATURE = "REGISTRY_FEATURE"


# 字典派发：简单关键字 → NodeType（替代 ~30 个 elif 分支）
_SIMPLE_NODE_TYPES: dict[str, NodeType] = {
    "CLICK": NodeType.CLICK,
    "TYPE": NodeType.TYPE,
    "LAUNCH": NodeType.LAUNCH,
    "BREAK": NodeType.BREAK,
    "SCREENSHOT": NodeType.SCREENSHOT,
    "WAITSCREEN": NodeType.WAITSCREEN,
    "SCREENSTABLE": NodeType.SCREENSTABLE,
    "HOTKEY": NodeType.HOTKEY,
    "FOCUS": NodeType.FOCUS,
    "SET": NodeType.SET,
    "OCR_FIND": NodeType.OCR_FIND,
    "OCR_EXTRACT": NodeType.OCR_EXTRACT,
    "REGION_SELECT": NodeType.REGION_SELECT,
    "IMAGE_FIND": NodeType.IMAGE_FIND,
    "TEMPLATE_CAPTURE": NodeType.TEMPLATE_CAPTURE,
    "PROGRESS_WAIT": NodeType.PROGRESS_WAIT,
    "PROGRESS_INIT": NodeType.PROGRESS_INIT,
    "PROGRESS_RELOCATE": NodeType.PROGRESS_RELOCATE,
    "IMAGE_CLICK": NodeType.IMAGE_CLICK,
    "SWIPE": NodeType.SWIPE,
    "SCROLL": NodeType.SCROLL,
    "DELAY": NodeType.DELAY,
    "WAIT": NodeType.WAIT,
    "WAIT_RANDOM": NodeType.WAIT_RANDOM,
    "POPUP_CLOSE": NodeType.POPUP_CLOSE,
    "COLOR_CHECK": NodeType.COLOR_CHECK,
    "REGION_WAIT": NodeType.REGION_WAIT,
    "SCENE_CONDITION": NodeType.SCENE_CONDITION,
    "FINDCANVAS": NodeType.FINDCANVAS,
    "DRAG": NodeType.DRAG,
    "RUN": NodeType.RUN,
}

@dataclass
class ASTNode:
    type: NodeType
    args: str = ""
    children: List[ASTNode] = field(default_factory=list)
    else_children: List[ASTNode] = field(default_factory=list)
    line: int = 0
    params: List[str] = field(default_factory=list)      # SUBROUTINE param names
    call_args: List[str] = field(default_factory=list)    # CALL arg expressions
    event_key: str = ""                                   # WHEN trigger type
    config: str = ""                                      # WHEN config string
    loop_type: str = ""                                   # ""=LOOP, "while"=WHILE, "until"=UNTIL

    def to_dict(self) -> dict:
        d = {
            "type": self.type.value,
            "args": self.args,
            "line": self.line,
            "children": [c.to_dict() for c in self.children],
            "else_children": [c.to_dict() for c in self.else_children],
        }
        if self.params:
            d["params"] = self.params
        if self.call_args:
            d["call_args"] = self.call_args
        if self.event_key:
            d["event_key"] = self.event_key
            d["config"] = self.args
        if self.loop_type:
            d["loop_type"] = self.loop_type
        return d


class DSLParseError(Exception):
    def __init__(self, msg: str, line: int = 0):
        super().__init__(f"[Line {line}] {msg}")
        self.line = line


class DSLParser:
    """
    Lobster DSL 解析器
    语法规则:
      CLICK <target>
      WAIT <condition>
      LOOP [label] ... END
      IF <condition> ... [ELSE ...] END
      RUN <macro_name>
      SUBROUTINE name(param1, param2) ... END
      CALL name(arg1, arg2)
      IMPORT "<path>"
      RETURN [value]
    """

    KEYWORDS = {"CLICK", "TYPE", "LAUNCH", "WAIT", "LOOP", "IF", "BREAK", "ELSE", "END", "RUN",
                "SUBROUTINE", "CALL", "IMPORT", "RETURN", "WHEN",
                "PARALLEL", "WITH",
                "SCREENSHOT", "WAITSCREEN", "SCREENSTABLE",
                "HOTKEY", "FOCUS", "SET", "OCR_FIND", "OCR_EXTRACT",
                "REGION_SELECT", "IMAGE_FIND", "TEMPLATE_CAPTURE",
                "PROGRESS_WAIT", "PROGRESS_INIT", "PROGRESS_RELOCATE", "IMAGE_CLICK",
                "SWIPE", "SCROLL", "DELAY", "WHILE", "UNTIL", "ELIF", "ELSEIF",
                "POPUP_CLOSE", "COLOR_CHECK", "REGION_WAIT", "SCENE_CONDITION", "VLM_CONDITION",
                "FINDCANVAS", "DRAG", "VLM_ANALYZE"}
    _registry_loaded = False

    @classmethod
    def _load_registry_keywords(cls):
        """从 Feature Registry 动态加载 DSL 关键字。"""
        if cls._registry_loaded:
            return
        cls._registry_loaded = True
        try:
            import features._all  # 触发 @feature 装饰器，填充 registry
            from features.registry import registry
            for f in registry.all():
                if f.dsl_keyword and not f.deprecated:
                    kw = f.dsl_keyword.upper()
                    cls.KEYWORDS.add(kw)
        except ImportError:
            pass
    # 小写 → 大写映射（兼容灵动脚本的伪代码格式）
    LOWERCASE_MAP = {kw.lower(): kw for kw in KEYWORDS}

    @classmethod
    def _rebuild_lowercase_map(cls):
        cls.LOWERCASE_MAP = {kw.lower(): kw for kw in cls.KEYWORDS}

    def __init__(self):
        self._tokens: List[tuple[int, str, str]] = []
        self._pos = 0
        self.subroutines: dict[str, ASTNode] = {}

    def parse(self, source: str) -> ASTNode:
        self._load_registry_keywords()
        self._rebuild_lowercase_map()
        self._tokens = self._tokenize(source)
        self._pos = 0
        self.subroutines = {}
        root = ASTNode(type=NodeType.SEQUENCE, args="root")
        root.children = self._parse_block(end_triggers=set())
        return root

    # ── tokenizer ────────────────────────────────────────────────
    def _tokenize(self, source: str) -> List[tuple[int, str, str]]:
        tokens = []
        # 预处理：将缩进块转为 END 终止格式（兼容灵动脚本的缩进语法）
        source = self._indent_to_end(source)
        for lineno, raw in enumerate(source.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            # 去除行尾冒号（兼容伪代码语法 LOOP 5:）
            stripped_line = re.sub(r":\s*$", "", line).strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)", stripped_line)
            if not m:
                raise DSLParseError(f"无法识别的语法: '{line}'", lineno)
            keyword, args = m.group(1), m.group(2).strip()
            # 小写转大写（兼容灵动脚本的伪代码）
            if keyword.lower() in self.LOWERCASE_MAP:
                keyword = self.LOWERCASE_MAP[keyword.lower()]
            if keyword not in self.KEYWORDS:
                raise DSLParseError(f"未知指令: '{keyword}'", lineno)
            tokens.append((lineno, keyword, args))
        return tokens

    @staticmethod
    def _indent_to_end(source: str) -> str:
        """将缩进块转换为 END 终止格式。
        输入:
          LOOP 5
            CLICK 确认
            WAIT 1
          CLICK 完成
        输出:
          LOOP 5
          CLICK 确认
          WAIT 1
          END
          CLICK 完成
        """
        lines = source.splitlines()
        if not lines:
            return source
        # 检测是否有缩进（至少一行以空格开头）
        has_indent = any(line.startswith((" ", "\t")) for line in lines if line.strip())
        # 如果已经使用了 END 终止符，跳过缩进转换（避免重复添加 END）
        has_end = any(line.strip().upper() == "END" for line in lines)
        if not has_indent or has_end:
            return source
        result = []
        indent_stack = []  # (indent_level, needs_end)
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                result.append(line)
                continue
            indent = len(line) - len(line.lstrip())
            upper = stripped.upper().lstrip()
            # ELSE/ELIF：只弹出比当前缩进深的级别（闭合上级块），不弹同级（自身 IF 块）
            is_else = upper in ("ELSE",) or upper.startswith(("ELSE ", "ELIF ", "ELSEIF "))
            if is_else:
                while indent_stack and indent < indent_stack[-1][0]:
                    if indent_stack[-1][1]:
                        result.append("END")
                    indent_stack.pop()
                result.append(stripped)
                continue
            # 弹出所有比当前缩进大或相等的栈顶（闭合块）
            while indent_stack and indent <= indent_stack[-1][0]:
                if indent_stack[-1][1]:  # needs_end
                    result.append("END")
                indent_stack.pop()
            # 判断当前行是否开启新块
            is_block_start = any(upper.startswith(kw) for kw in
                                 ["LOOP", "IF ", "SUBROUTINE", "WHEN", "PARALLEL", "WITH",
                                  "WHILE", "UNTIL"])
            indent_stack.append((indent, is_block_start))
            result.append(stripped)
        # 闭合所有剩余块
        while indent_stack:
            if indent_stack[-1][1]:
                result.append("END")
            indent_stack.pop()
        return "\n".join(result)

    # ── recursive descent parser ──────────────────────────────────
    def _peek(self) -> Optional[tuple[int, str, str]]:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self) -> tuple[int, str, str]:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _parse_block(self, end_triggers: set) -> List[ASTNode]:
        """解析节点块直到遇到 end_triggers、END、ELSE 或 WITH"""
        nodes: List[ASTNode] = []
        while self._pos < len(self._tokens):
            tok = self._peek()
            if tok is None:
                break
            lineno, keyword, args = tok
            if keyword in end_triggers or keyword == "END":
                break
            if keyword in ("ELSE", "ELIF", "ELSEIF"):
                break
            self._consume()

            # ── 字典派发：简单关键字 → 直接构建 ASTNode ──
            if keyword in _SIMPLE_NODE_TYPES:
                nargs = "" if keyword == "BREAK" else args
                nodes.append(ASTNode(_SIMPLE_NODE_TYPES[keyword], nargs, line=lineno))

            elif keyword in ("VLM_CONDITION", "VLM_ANALYZE"):
                nodes.append(ASTNode(NodeType.VLM_CONDITION, args, line=lineno))

            # ── 复杂结构关键字（含嵌套块解析）──
            elif keyword in ("LOOP", "WHILE", "UNTIL"):
                node = ASTNode(NodeType.LOOP, args, line=lineno)
                # 支持 LOOP WHILE condition / LOOP UNTIL condition 语法
                args_lower = args.strip().lower()
                if args_lower.startswith("while "):
                    node.args = args[6:].strip()
                    node.loop_type = "while"
                elif args_lower.startswith("until "):
                    node.args = args[6:].strip()
                    node.loop_type = "until"
                node.children = self._parse_block({"END"})
                self._expect("END", lineno)
                nodes.append(node)

            elif keyword == "IF":
                node = ASTNode(NodeType.IF, args, line=lineno)
                node.children = self._parse_block({"END", "ELSE", "ELIF", "ELSEIF"})
                nxt = self._peek()
                if nxt and nxt[1] == "ELSE":
                    self._consume()
                    node.else_children = self._parse_block({"END"})
                elif nxt and nxt[1] in ("ELIF", "ELSEIF"):
                    # ELIF condition → 递归拆解为 IF+ELSE
                    self._consume()
                    elif_node = ASTNode(NodeType.IF, nxt[2], line=lineno)
                    elif_node.children = self._parse_block({"END", "ELSE"})
                    nxt2 = self._peek()
                    if nxt2 and nxt2[1] == "ELSE":
                        self._consume()
                        elif_node.else_children = self._parse_block({"END"})
                    node.else_children = [elif_node]
                self._expect("END", lineno)
                nodes.append(node)

            elif keyword == "SUBROUTINE":
                name, params = self._parse_sub_header(args, lineno)
                if name in self.subroutines:
                    raise DSLParseError(f"子程序 '{name}' 重复定义", lineno)
                node = ASTNode(NodeType.SUBROUTINE, name, line=lineno)
                node.params = params
                node.children = self._parse_block({"END"})
                self._expect("END", lineno)
                self.subroutines[name] = node
                nodes.append(node)

            elif keyword == "CALL":
                name, call_args = self._parse_call_expr(args, lineno)
                node = ASTNode(NodeType.CALL, name, line=lineno)
                node.call_args = call_args
                nodes.append(node)

            elif keyword == "IMPORT":
                path = args.strip().strip('"').strip("'")
                if not path:
                    raise DSLParseError("IMPORT 需要文件路径", lineno)
                nodes.append(ASTNode(NodeType.IMPORT, path, line=lineno))

            elif keyword == "RETURN":
                nodes.append(ASTNode(NodeType.RETURN, args.strip(), line=lineno))

            elif keyword == "WHEN":
                event_key, config_str = self._parse_when_header(args, lineno)
                node = ASTNode(NodeType.WHEN, config_str, line=lineno)
                node.event_key = event_key
                node.children = self._parse_block({"END"})
                self._expect("END", lineno)
                nodes.append(node)

            elif keyword == "WITH":
                node = ASTNode(NodeType.WITH, args, line=lineno)
                node.children = self._parse_block({"END"})
                self._expect("END", lineno)
                nodes.append(node)

            elif keyword == "PARALLEL":
                node = ASTNode(NodeType.PARALLEL, args, line=lineno)
                # 专用分支解析：WITH 在此处是分支分隔符，不是块语句
                self._parse_parallel_branches(node, lineno)
                nodes.append(node)

            else:
                # Registry 回退：带 dsl_keyword 的 @feature 自动处理
                nt = getattr(NodeType, keyword, NodeType.REGISTRY_FEATURE)
                nodes.append(ASTNode(nt, args, line=lineno))

        return nodes

    def _parse_parallel_branches(self, node: ASTNode, ref_line: int):
        """解析 PARALLEL 分支：WITH 是分支容器（自包含，自己管自己的 END）"""
        while self._pos < len(self._tokens):
            tok = self._peek()
            if tok is None:
                break
            lineno, keyword, _ = tok
            if keyword == "END":
                self._consume()
                break
            if keyword == "WITH":
                self._consume()
                # WITH 是自包含分支：解析内容 + 自己的 END
                children = self._parse_block({"END"})
                self._expect("END", lineno)
                idx = len(node.children)
                node.children.append(ASTNode(
                    NodeType.SEQUENCE, f"branch_{idx}", line=lineno, children=children))
                continue
            # 非 WITH 分支，遇到 END 或 WITH 时停止
            children = self._parse_block({"END", "WITH"})
            idx = len(node.children)
            node.children.append(ASTNode(
                NodeType.SEQUENCE, f"branch_{idx}", line=lineno, children=children))
        if not node.children:
            raise DSLParseError("PARALLEL 块不能为空", ref_line)

    def _expect(self, keyword: str, ref_line: int):
        tok = self._peek()
        if tok is None or tok[1] != keyword:
            got = tok[1] if tok else "EOF"
            raise DSLParseError(
                f"期望 '{keyword}'，但得到 '{got}'（从第{ref_line}行的块开始）",
                ref_line,
            )
        self._consume()

    def _parse_when_header(self, text: str, lineno: int):
        """WHEN FILE_CREATED "config" → ('FILE_CREATED', 'config')"""
        parts = text.strip().split(None, 1)
        if not parts:
            raise DSLParseError("WHEN 需要事件类型，如: WHEN FILE_CREATED path", lineno)
        event_key = parts[0]
        config_str = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
        return event_key, config_str

    # ── sub / call helpers ───────────────────────────────────────
    def _parse_sub_header(self, text: str, lineno: int):
        """SUBROUTINE name(param1, param2) → ('name', ['param1', 'param2'])"""
        m = re.match(r"^(\w+)\s*\((.*)\)\s*$", text)
        if not m:
            raise DSLParseError(f"子程序格式错误: '{text}'，应为 name(param1, param2)", lineno)
        name = m.group(1)
        raw = m.group(2).strip()
        params = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        return name, params

    def _parse_call_expr(self, text: str, lineno: int):
        """CALL name(arg1, arg2) → ('name', ['arg1', 'arg2'])
        也支持 CALL name（无参数时）"""
        m = re.match(r"^(\w+)\s*\((.*)\)\s*$", text)
        if m:
            name = m.group(1)
            raw = m.group(2).strip()
            args = [a.strip().strip("\"'") for a in raw.split(",") if a.strip()] if raw else []
            return name, args
        # 兼容 CALL name（无括号）
        name = text.strip().split()[0] if text.strip() else text.strip()
        return name, []

    # ── utility ───────────────────────────────────────────────────
    @staticmethod
    def from_string(source: str) -> ASTNode:
        return DSLParser().parse(source)


# ── 快速测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    sample = """
SUBROUTINE login(user, pass)
  CLICK 用户名
  TYPE {user}
  CLICK 密码
  TYPE {pass}
  CLICK 登录
  WAIT 加载完成
END

CLICK 启动应用
WAIT 初始化
CALL login("admin", "123456")
CLICK 开始使用
"""
    ast = DSLParser.from_string(sample)
    print(json.dumps(ast.to_dict(), ensure_ascii=False, indent=2))
