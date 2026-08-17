"""Prevent new model transports from bypassing the Runtime retry contract."""
from __future__ import annotations

import ast
from pathlib import Path


PRODUCT_ROOT = Path(__file__).parents[1] / "winpilot"
ALLOWED_TRANSPORT_CALLS = {
    (Path("model_adapter.py"), ("send_prepared_chat_completion",)),
    (Path("modeling/chat.py"), ("invoke_chat_completion", "send_once")),
}


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    values: list[str] = []
    while isinstance(node, ast.Attribute):
        values.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        values.append(node.id)
    return tuple(reversed(values))


class _TransportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[str] = []
        self.chat_calls: list[tuple[tuple[str, ...], int]] = []
        self.responses_calls: list[tuple[tuple[str, ...], int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        chain = _attribute_chain(node.func)
        owner = tuple(self.functions)
        if len(chain) >= 3 and chain[-3:] == ("chat", "completions", "create"):
            self.chat_calls.append((owner, node.lineno))
        if len(chain) >= 2 and chain[-2:] in {
            ("responses", "create"),
            ("responses", "stream"),
        }:
            self.responses_calls.append((owner, node.lineno))
        self.generic_visit(node)


def test_chat_completion_transport_has_a_small_explicit_owner_set() -> None:
    found: set[tuple[Path, tuple[str, ...]]] = set()
    for source in PRODUCT_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        visitor = _TransportVisitor()
        visitor.visit(tree)
        found.update(
            (source.relative_to(PRODUCT_ROOT), owner)
            for owner, _lineno in visitor.chat_calls
        )
    assert found == ALLOWED_TRANSPORT_CALLS


def test_product_package_has_no_unowned_responses_transport() -> None:
    offenders = []
    for source in PRODUCT_ROOT.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        visitor = _TransportVisitor()
        visitor.visit(tree)
        offenders.extend(
            (source.relative_to(PRODUCT_ROOT), owner, lineno)
            for owner, lineno in visitor.responses_calls
        )
    assert offenders == []
