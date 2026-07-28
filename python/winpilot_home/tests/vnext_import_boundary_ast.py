"""Small AST data-flow helper for the live-vNext import boundary tests.

This is intentionally not a general Python interpreter.  It models only the
pieces needed to identify static imports and simple propagation of Python's two
dynamic-import entry points.  In particular, it keeps source-order bindings per
scope, treats function locals as Python does, and conservatively joins branches.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_BUILTIN_IMPORT = "builtin.__import__"
_BUILTINS_MODULE = "module.builtins"
_IMPORT_MODULE = "importlib.import_module"
_IMPORTLIB_MODULE = "module.importlib"
_MAX_CONSTANT_ALTERNATIVES = 64


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@dataclass(frozen=True, slots=True)
class StaticImport:
    """One syntactic ``import`` or imported member."""

    module: str
    name: str | None
    asname: str | None
    lineno: int


def _resolved_from_base(
    module_name: str,
    node: ast.ImportFrom,
) -> str:
    if not node.level:
        return node.module or ""
    package = module_name.rpartition(".")[0]
    parts = package.split(".") if package else []
    keep = max(0, len(parts) - node.level + 1)
    prefix = ".".join(parts[:keep])
    return ".".join(
        part for part in (prefix, node.module or "") if part
    )


def _collect_static_imports(
    tree: ast.AST,
    *,
    module_name: str,
) -> tuple[StaticImport, ...]:
    found: list[StaticImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                StaticImport(
                    module=alias.name,
                    name=None,
                    asname=alias.asname,
                    lineno=node.lineno,
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            base = _resolved_from_base(module_name, node)
            found.extend(
                StaticImport(
                    module=base,
                    name=alias.name,
                    asname=alias.asname,
                    lineno=node.lineno,
                )
                for alias in node.names
            )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class DynamicImportCall:
    """A call through a value known to retain dynamic-import capability."""

    kind: str
    targets: tuple[str, ...]
    target_unknown: bool
    lineno: int
    col_offset: int

    def can_reach(self, prefix: str) -> bool:
        """Return whether any statically known target reaches ``prefix``."""

        return any(_matches_prefix(target, prefix) for target in self.targets)


@dataclass(frozen=True, slots=True)
class ImportAnalysis:
    """Import facts for one source module."""

    module_name: str
    filename: str
    static_imports: tuple[StaticImport, ...]
    dynamic_calls: tuple[DynamicImportCall, ...]

    def static_bindings(
        self,
        prefix: str,
    ) -> tuple[tuple[str, str | None, str | None], ...]:
        """Return normalized static bindings that can reach ``prefix``."""

        bindings: list[tuple[str, str | None, str | None]] = []
        for imported in self.static_imports:
            if _matches_prefix(imported.module, prefix):
                bindings.append(
                    (imported.module, imported.name, imported.asname)
                )
                continue
            if imported.name is None:
                continue
            candidate = f"{imported.module}.{imported.name}".strip(".")
            if _matches_prefix(candidate, prefix):
                bindings.append((candidate, None, imported.asname))
        return tuple(
            sorted(
                bindings,
                key=lambda item: (
                    item[0],
                    item[1] or "",
                    item[2] or "",
                ),
            )
        )

    def static_modules(self, prefix: str) -> tuple[str, ...]:
        """Return distinct module bases from matching normalized bindings."""

        return tuple(
            sorted(
                {
                    module
                    for module, _name, _asname in self.static_bindings(prefix)
                }
            )
        )

    def dynamic_calls_reaching(
        self,
        *prefixes: str,
    ) -> tuple[DynamicImportCall, ...]:
        """Return calls with at least one statically known forbidden target."""

        return tuple(
            call
            for call in self.dynamic_calls
            if any(call.can_reach(prefix) for prefix in prefixes)
        )


@dataclass(frozen=True, slots=True)
class _Value:
    capabilities: frozenset[str] = frozenset()
    constants: frozenset[object] = frozenset()
    functions: frozenset[int] = frozenset()
    unknown: bool = True


_UNKNOWN = _Value()
_BUILTIN_IMPORT_VALUE = _Value(
    capabilities=frozenset({_BUILTIN_IMPORT}),
    unknown=False,
)
_BUILTINS_MODULE_VALUE = _Value(
    capabilities=frozenset({_BUILTINS_MODULE}),
    unknown=False,
)
_IMPORT_MODULE_VALUE = _Value(
    capabilities=frozenset({_IMPORT_MODULE}),
    unknown=False,
)
_IMPORTLIB_MODULE_VALUE = _Value(
    capabilities=frozenset({_IMPORTLIB_MODULE}),
    unknown=False,
)


def _constant(value: object) -> _Value:
    try:
        hash(value)
    except TypeError:
        return _UNKNOWN
    return _Value(constants=frozenset({value}), unknown=False)


def _merge_values(left: _Value, right: _Value) -> _Value:
    constants = left.constants | right.constants
    if len(constants) > _MAX_CONSTANT_ALTERNATIVES:
        constants = frozenset()
        constant_overflow = True
    else:
        constant_overflow = False
    return _Value(
        capabilities=left.capabilities | right.capabilities,
        constants=constants,
        functions=left.functions | right.functions,
        unknown=left.unknown or right.unknown or constant_overflow,
    )


def _function_value(identifier: int) -> _Value:
    return _Value(functions=frozenset({identifier}), unknown=False)


def _merge_environments(
    *environments: dict[str, _Value],
) -> dict[str, _Value]:
    names = {
        name
        for environment in environments
        for name in environment
    }
    merged: dict[str, _Value] = {}
    for name in names:
        value: _Value | None = None
        for environment in environments:
            branch_value = environment.get(name, _UNKNOWN)
            value = (
                branch_value
                if value is None
                else _merge_values(value, branch_value)
            )
        merged[name] = value or _UNKNOWN
    return merged


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _bound_names(element)
        }
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return set()


class _FunctionLocalCollector(ast.NodeVisitor):
    """Collect names Python makes local to one function scope."""

    def __init__(self) -> None:
        self.locals: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.locals.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.locals.add(
                alias.asname or alias.name.split(".", 1)[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.locals.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.locals.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.locals.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.locals.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.locals.add(node.name)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.locals.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.locals.add(node.rest)
        self.generic_visit(node)


def _argument_names(arguments: ast.arguments) -> set[str]:
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    } | (
        {arguments.vararg.arg} if arguments.vararg is not None else set()
    ) | (
        {arguments.kwarg.arg} if arguments.kwarg is not None else set()
    )


def _function_locals(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    collector = _FunctionLocalCollector()
    for statement in node.body:
        collector.visit(statement)
    return (
        collector.locals
        | _argument_names(node.args)
    ) - collector.globals - collector.nonlocals


@dataclass(slots=True)
class _FunctionRecord:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    closure_environment: dict[str, _Value]
    defaults: dict[str, _Value]


class _Analyzer:
    def __init__(
        self,
        *,
        module_name: str,
        filename: str,
    ) -> None:
        self.module_name = module_name
        self.filename = filename
        self.static_imports: list[StaticImport] = []
        self.dynamic_calls: list[DynamicImportCall] = []
        self._functions: dict[int, _FunctionRecord] = {}
        self._next_function_id = 1
        self._active_functions: set[int] = set()
        self._potentially_scanned_functions: set[int] = set()

    def run(self, tree: ast.Module) -> ImportAnalysis:
        self.static_imports.extend(
            _collect_static_imports(
                tree,
                module_name=self.module_name,
            )
        )
        environment = {"__import__": _BUILTIN_IMPORT_VALUE}
        self._statements(tree.body, environment)
        self._scan_registered_functions()
        return ImportAnalysis(
            module_name=self.module_name,
            filename=self.filename,
            static_imports=tuple(self.static_imports),
            dynamic_calls=tuple(dict.fromkeys(self.dynamic_calls)),
        )

    def _scan_registered_functions(self) -> None:
        while True:
            pending = sorted(
                set(self._functions)
                - self._potentially_scanned_functions
            )
            if not pending:
                return
            for identifier in pending:
                self._potentially_scanned_functions.add(identifier)
                self._invoke_function(identifier, [], [])

    def _bind_import(
        self,
        node: ast.Import,
        environment: dict[str, _Value],
    ) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            if alias.name == "importlib" or (
                alias.asname is None
                and alias.name.startswith("importlib.")
            ):
                value = _IMPORTLIB_MODULE_VALUE
            elif alias.name == "builtins":
                value = _BUILTINS_MODULE_VALUE
            else:
                value = _UNKNOWN
            environment[bound_name] = value

    def _bind_from_import(
        self,
        node: ast.ImportFrom,
        environment: dict[str, _Value],
    ) -> None:
        base = _resolved_from_base(self.module_name, node)
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            if base == "importlib" and alias.name == "import_module":
                value = _IMPORT_MODULE_VALUE
            elif base == "builtins" and alias.name == "__import__":
                value = _BUILTIN_IMPORT_VALUE
            elif base == "importlib" and alias.name == "__name__":
                value = _constant("importlib")
            else:
                value = _UNKNOWN
            environment[bound_name] = value

    def _assign(
        self,
        target: ast.AST,
        value: _Value,
        environment: dict[str, _Value],
    ) -> None:
        if isinstance(target, ast.Name):
            environment[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._assign(element, _UNKNOWN, environment)
            return
        if isinstance(target, ast.Starred):
            self._assign(target.value, _UNKNOWN, environment)
            return
        if isinstance(target, ast.Attribute):
            self._expression(target.value, environment)
            return
        if isinstance(target, ast.Subscript):
            self._expression(target.value, environment)
            self._expression(target.slice, environment)

    def _attribute_value(
        self,
        owner: _Value,
        attribute: str,
    ) -> _Value:
        result = _UNKNOWN
        found = False
        if (
            _IMPORTLIB_MODULE in owner.capabilities
            and attribute == "import_module"
        ):
            result = _IMPORT_MODULE_VALUE
            found = True
        if (
            _BUILTINS_MODULE in owner.capabilities
            and attribute == "__import__"
        ):
            result = (
                _BUILTIN_IMPORT_VALUE
                if not found
                else _merge_values(result, _BUILTIN_IMPORT_VALUE)
            )
            found = True
        if found and owner.unknown:
            return _merge_values(result, _UNKNOWN)
        return result if found else _UNKNOWN

    def _formatted_values(
        self,
        node: ast.FormattedValue,
        environment: dict[str, _Value],
    ) -> tuple[set[str], bool]:
        value = self._expression(node.value, environment)
        spec = ""
        if node.format_spec is not None:
            spec_value = self._expression(node.format_spec, environment)
            specs = {
                item
                for item in spec_value.constants
                if isinstance(item, str)
            }
            if spec_value.unknown or len(specs) != 1:
                return set(), True
            spec = next(iter(specs))
        rendered: set[str] = set()
        unknown = value.unknown
        for item in value.constants:
            try:
                if node.conversion == ord("s"):
                    item = str(item)
                elif node.conversion == ord("r"):
                    item = repr(item)
                elif node.conversion == ord("a"):
                    item = ascii(item)
                elif node.conversion != -1:
                    unknown = True
                    continue
                rendered.add(format(item, spec))
            except (TypeError, ValueError):
                unknown = True
        return rendered, unknown

    def _joined_string(
        self,
        node: ast.JoinedStr,
        environment: dict[str, _Value],
    ) -> _Value:
        alternatives = {""}
        unknown = False
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(
                part.value, str
            ):
                pieces = {part.value}
                part_unknown = False
            elif isinstance(part, ast.FormattedValue):
                pieces, part_unknown = self._formatted_values(
                    part,
                    environment,
                )
            else:
                self._expression(part, environment)
                pieces = set()
                part_unknown = True
            unknown = unknown or part_unknown
            if not pieces:
                alternatives = set()
                continue
            alternatives = {
                left + right
                for left in alternatives
                for right in pieces
            }
            if len(alternatives) > _MAX_CONSTANT_ALTERNATIVES:
                alternatives = set()
                unknown = True
        return _Value(
            constants=frozenset(alternatives),
            unknown=unknown,
        )

    def _call(
        self,
        node: ast.Call,
        environment: dict[str, _Value],
    ) -> _Value:
        function = self._expression(node.func, environment)
        argument_values = [
            self._expression(argument, environment)
            for argument in node.args
        ]
        keyword_values = [
            (
                keyword.arg,
                self._expression(keyword.value, environment),
            )
            for keyword in node.keywords
        ]

        kinds = function.capabilities & {
            _BUILTIN_IMPORT,
            _IMPORT_MODULE,
        }
        if kinds:
            target_value: _Value | None = (
                argument_values[0] if argument_values else None
            )
            for name, value in keyword_values:
                if name == "name":
                    target_value = (
                        value
                        if target_value is None
                        else _merge_values(target_value, value)
                    )
            if target_value is not None:
                targets = tuple(
                    sorted(
                        item
                        for item in target_value.constants
                        if isinstance(item, str)
                    )
                )
                target_unknown = (
                    target_value.unknown
                    or len(targets) != len(target_value.constants)
                )
            else:
                targets = ()
                target_unknown = True
            for kind in sorted(kinds):
                self.dynamic_calls.append(
                    DynamicImportCall(
                        kind=kind,
                        targets=targets,
                        target_unknown=target_unknown,
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                    )
                )

        for identifier in sorted(function.functions):
            self._invoke_function(
                identifier,
                argument_values,
                keyword_values,
            )

        # Preserve capability through a statically resolvable getattr.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(argument_values) >= 2
        ):
            attributes = {
                item
                for item in argument_values[1].constants
                if isinstance(item, str)
            }
            result = _UNKNOWN
            found = False
            for attribute in attributes:
                candidate = self._attribute_value(
                    argument_values[0],
                    attribute,
                )
                if candidate.capabilities:
                    result = (
                        candidate
                        if not found
                        else _merge_values(result, candidate)
                    )
                    found = True
            if found:
                if (
                    argument_values[1].unknown
                    or len(attributes)
                    != len(argument_values[1].constants)
                ):
                    result = _merge_values(result, _UNKNOWN)
                return result
        return _UNKNOWN

    def _lambda(
        self,
        node: ast.Lambda,
        environment: dict[str, _Value],
    ) -> _Value:
        defaults = self._argument_defaults(node.args, environment)
        return _function_value(
            self._register_function(node, environment, defaults)
        )

    def _comprehension(
        self,
        node: (
            ast.ListComp
            | ast.SetComp
            | ast.DictComp
            | ast.GeneratorExp
        ),
        environment: dict[str, _Value],
    ) -> _Value:
        nested = dict(environment)
        for generator in node.generators:
            self._expression(generator.iter, nested)
            for name in _bound_names(generator.target):
                nested[name] = _UNKNOWN
            for condition in generator.ifs:
                self._expression(condition, nested)
        if isinstance(node, ast.DictComp):
            self._expression(node.key, nested)
            self._expression(node.value, nested)
        else:
            self._expression(node.elt, nested)
        return _UNKNOWN

    def _expression(
        self,
        node: ast.AST | None,
        environment: dict[str, _Value],
    ) -> _Value:
        if node is None:
            return _UNKNOWN
        if isinstance(node, ast.Name):
            return environment.get(node.id, _UNKNOWN)
        if isinstance(node, ast.Constant):
            return _constant(node.value)
        if isinstance(node, ast.Attribute):
            owner = self._expression(node.value, environment)
            return self._attribute_value(owner, node.attr)
        if isinstance(node, ast.Call):
            return self._call(node, environment)
        if isinstance(node, ast.BinOp):
            left = self._expression(node.left, environment)
            right = self._expression(node.right, environment)
            if isinstance(node.op, ast.Add):
                strings = {
                    left_item + right_item
                    for left_item in left.constants
                    if isinstance(left_item, str)
                    for right_item in right.constants
                    if isinstance(right_item, str)
                }
                overflow = len(strings) > _MAX_CONSTANT_ALTERNATIVES
                return _Value(
                    constants=(
                        frozenset()
                        if overflow
                        else frozenset(strings)
                    ),
                    unknown=left.unknown or right.unknown or overflow,
                )
            return _UNKNOWN
        if isinstance(node, ast.JoinedStr):
            return self._joined_string(node, environment)
        if isinstance(node, ast.IfExp):
            self._expression(node.test, environment)
            body_environment = dict(environment)
            otherwise_environment = dict(environment)
            body = self._expression(node.body, body_environment)
            otherwise = self._expression(
                node.orelse,
                otherwise_environment,
            )
            environment.clear()
            environment.update(
                _merge_environments(
                    body_environment,
                    otherwise_environment,
                )
            )
            self._rebase_closure_environments(
                (body_environment, otherwise_environment),
                environment,
            )
            return _merge_values(body, otherwise)
        if isinstance(node, ast.NamedExpr):
            value = self._expression(node.value, environment)
            self._assign(node.target, value, environment)
            return value
        if isinstance(node, ast.Lambda):
            return self._lambda(node, environment)
        if isinstance(
            node,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            return self._comprehension(node, environment)

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expression(child, environment)
        return _UNKNOWN

    def _argument_defaults(
        self,
        arguments: ast.arguments,
        environment: dict[str, _Value],
    ) -> dict[str, _Value]:
        defaults: dict[str, _Value] = {}
        positional = (
            *arguments.posonlyargs,
            *arguments.args,
        )
        if arguments.defaults:
            names = positional[-len(arguments.defaults):]
            for argument, default in zip(
                names,
                arguments.defaults,
                strict=True,
            ):
                defaults[argument.arg] = self._expression(
                    default,
                    environment,
                )
        for argument, default in zip(
            arguments.kwonlyargs,
            arguments.kw_defaults,
            strict=True,
        ):
            if default is not None:
                defaults[argument.arg] = self._expression(
                    default,
                    environment,
                )
        return defaults

    def _function_definition_expressions(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        environment: dict[str, _Value],
    ) -> dict[str, _Value]:
        for decorator in node.decorator_list:
            self._expression(decorator, environment)
        defaults = self._argument_defaults(node.args, environment)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            self._expression(argument.annotation, environment)
        if node.args.vararg is not None:
            self._expression(node.args.vararg.annotation, environment)
        if node.args.kwarg is not None:
            self._expression(node.args.kwarg.annotation, environment)
        self._expression(node.returns, environment)
        return defaults

    def _register_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        environment: dict[str, _Value],
        defaults: dict[str, _Value],
    ) -> int:
        identifier = self._next_function_id
        self._next_function_id += 1
        self._functions[identifier] = _FunctionRecord(
            node=node,
            closure_environment=environment,
            defaults=defaults,
        )
        return identifier

    def _bind_call_arguments(
        self,
        arguments: ast.arguments,
        defaults: dict[str, _Value],
        positional_values: list[_Value],
        keyword_values: list[tuple[str | None, _Value]],
        environment: dict[str, _Value],
    ) -> None:
        positional_parameters = [
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
            )
        ]
        keyword_parameters = [
            argument.arg for argument in arguments.kwonlyargs
        ]
        provided: dict[str, _Value] = {}
        for name, value in zip(
            positional_parameters,
            positional_values,
        ):
            provided[name] = value
        has_unknown_keywords = False
        for name, value in keyword_values:
            if name is None:
                has_unknown_keywords = True
                continue
            if name in provided:
                provided[name] = _merge_values(provided[name], value)
            else:
                provided[name] = value

        for name in (*positional_parameters, *keyword_parameters):
            if name in provided:
                environment[name] = provided[name]
            elif name in defaults:
                environment[name] = defaults[name]
            else:
                environment[name] = _UNKNOWN
            if has_unknown_keywords:
                environment[name] = _merge_values(
                    environment[name],
                    _UNKNOWN,
                )
        if arguments.vararg is not None:
            environment[arguments.vararg.arg] = _UNKNOWN
        if arguments.kwarg is not None:
            environment[arguments.kwarg.arg] = _UNKNOWN

    def _invoke_function(
        self,
        identifier: int,
        positional_values: list[_Value],
        keyword_values: list[tuple[str | None, _Value]],
    ) -> None:
        if identifier in self._active_functions:
            return
        record = self._functions.get(identifier)
        if record is None:
            return
        node = record.node
        nested = dict(record.closure_environment)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name in _function_locals(node):
                nested[name] = _UNKNOWN
            arguments = node.args
        else:
            for name in _argument_names(node.args):
                nested[name] = _UNKNOWN
            arguments = node.args
        self._bind_call_arguments(
            arguments,
            record.defaults,
            positional_values,
            keyword_values,
            nested,
        )
        self._active_functions.add(identifier)
        try:
            if isinstance(node, ast.Lambda):
                self._expression(node.body, nested)
            else:
                self._statements(node.body, nested)
        finally:
            self._active_functions.remove(identifier)

    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        environment: dict[str, _Value],
    ) -> None:
        defaults = self._function_definition_expressions(
            node,
            environment,
        )
        identifier = self._register_function(
            node,
            environment,
            defaults,
        )
        environment[node.name] = _function_value(identifier)

    def _class(
        self,
        node: ast.ClassDef,
        environment: dict[str, _Value],
    ) -> None:
        for expression in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
        ):
            self._expression(expression, environment)
        environment[node.name] = _UNKNOWN
        nested = dict(environment)
        self._statements(node.body, nested)

    def _branch(
        self,
        body: Iterable[ast.stmt],
        environment: dict[str, _Value],
    ) -> dict[str, _Value]:
        branch = dict(environment)
        self._statements(body, branch)
        return branch

    def _rebase_closure_environments(
        self,
        previous: Iterable[dict[str, _Value]],
        current: dict[str, _Value],
    ) -> None:
        previous_ids = {
            id(environment)
            for environment in previous
            if environment is not current
        }
        if not previous_ids:
            return
        for record in self._functions.values():
            if id(record.closure_environment) in previous_ids:
                record.closure_environment = current

    def _statements(
        self,
        statements: Iterable[ast.stmt],
        environment: dict[str, _Value],
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.Import):
                self._bind_import(statement, environment)
            elif isinstance(statement, ast.ImportFrom):
                self._bind_from_import(statement, environment)
            elif isinstance(statement, ast.Assign):
                value = self._expression(statement.value, environment)
                for target in statement.targets:
                    self._assign(target, value, environment)
            elif isinstance(statement, ast.AnnAssign):
                self._expression(statement.annotation, environment)
                if statement.value is not None:
                    value = self._expression(
                        statement.value,
                        environment,
                    )
                    self._assign(statement.target, value, environment)
            elif isinstance(statement, ast.AugAssign):
                self._expression(statement.target, environment)
                self._expression(statement.value, environment)
                self._assign(statement.target, _UNKNOWN, environment)
            elif isinstance(statement, ast.Expr):
                self._expression(statement.value, environment)
            elif isinstance(statement, (ast.Return, ast.Raise)):
                self._expression(
                    getattr(statement, "value", None)
                    or getattr(statement, "exc", None),
                    environment,
                )
                if isinstance(statement, ast.Raise):
                    self._expression(statement.cause, environment)
            elif isinstance(statement, ast.Assert):
                self._expression(statement.test, environment)
                self._expression(statement.msg, environment)
            elif isinstance(
                statement,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                self._function(statement, environment)
            elif isinstance(statement, ast.ClassDef):
                self._class(statement, environment)
            elif isinstance(statement, ast.If):
                self._expression(statement.test, environment)
                body = self._branch(statement.body, environment)
                otherwise = self._branch(
                    statement.orelse,
                    environment,
                )
                environment.clear()
                environment.update(_merge_environments(body, otherwise))
                self._rebase_closure_environments(
                    (body, otherwise),
                    environment,
                )
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                self._expression(statement.iter, environment)
                body = dict(environment)
                self._assign(statement.target, _UNKNOWN, body)
                self._statements(statement.body, body)
                joined = _merge_environments(environment, body)
                otherwise = self._branch(statement.orelse, joined)
                environment.clear()
                environment.update(
                    _merge_environments(joined, otherwise)
                )
                self._rebase_closure_environments(
                    (body, joined, otherwise),
                    environment,
                )
            elif isinstance(statement, ast.While):
                self._expression(statement.test, environment)
                body = self._branch(statement.body, environment)
                joined = _merge_environments(environment, body)
                otherwise = self._branch(statement.orelse, joined)
                environment.clear()
                environment.update(
                    _merge_environments(joined, otherwise)
                )
                self._rebase_closure_environments(
                    (body, joined, otherwise),
                    environment,
                )
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    self._expression(
                        item.context_expr,
                        environment,
                    )
                    if item.optional_vars is not None:
                        self._assign(
                            item.optional_vars,
                            _UNKNOWN,
                            environment,
                        )
                self._statements(statement.body, environment)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                attempted = self._branch(statement.body, environment)
                successful = self._branch(
                    statement.orelse,
                    attempted,
                )
                branches = [environment, attempted, successful]
                for handler in statement.handlers:
                    handled = dict(environment)
                    self._expression(handler.type, handled)
                    if handler.name:
                        handled[handler.name] = _UNKNOWN
                    self._statements(handler.body, handled)
                    branches.append(handled)
                joined = _merge_environments(*branches)
                self._rebase_closure_environments(
                    branches,
                    joined,
                )
                self._statements(statement.finalbody, joined)
                environment.clear()
                environment.update(joined)
                self._rebase_closure_environments(
                    (joined,),
                    environment,
                )
            elif isinstance(statement, ast.Match):
                self._expression(statement.subject, environment)
                branches = [environment]
                for case in statement.cases:
                    branch = dict(environment)
                    for name in _bound_names(case.pattern):
                        branch[name] = _UNKNOWN
                    self._expression(case.guard, branch)
                    self._statements(case.body, branch)
                    branches.append(branch)
                environment.clear()
                environment.update(_merge_environments(*branches))
                self._rebase_closure_environments(
                    branches[1:],
                    environment,
                )
            elif isinstance(statement, ast.Delete):
                for target in statement.targets:
                    self._assign(target, _UNKNOWN, environment)
            elif isinstance(statement, (ast.Global, ast.Nonlocal)):
                continue


def analyze_source(
    source: str,
    *,
    module_name: str,
    filename: str = "<source>",
) -> ImportAnalysis:
    """Analyze one Python source string without importing or executing it."""

    tree = ast.parse(source, filename=filename)
    return _Analyzer(
        module_name=module_name,
        filename=filename,
    ).run(tree)


def analyze_path(
    path: Path,
    *,
    module_name: str,
) -> ImportAnalysis:
    """Read and analyze one UTF-8 Python source file."""

    return analyze_source(
        path.read_text(encoding="utf-8"),
        module_name=module_name,
        filename=str(path),
    )
