"""Strategy sources, checked by parsing alone, before anything runs (G1.5; T13).

A strategy module is never executed until its run's open entry is pushed, and then only
in a fresh interpreter (``norse_research.runner``). Before that, the harness and the
genesis step read it as text:

* every module under a hypothesis's strategy paths may import only the allowlist and
  modules inside that hypothesis's own paths (security review N12), may not name
  ``open``, ``eval``, ``getattr``, ``setattr``, ``type`` and their kind at all, called or
  not (security review of PR 61, round 2), and may not touch a dunder attribute or a
  name such as ``sys``, ``modules``, ``mro``, ``attrgetter`` or ``gi_frame`` through which
  a module it may not import, a class's bases or a frame could be reached (L4, N9; PR
  61, round 4);
* nothing a module imports may be changed or reached behind: no assignment to or
  deletion of an imported name's attributes or items, no subclass of a class from this
  package, and no ``import norse_research.<x>`` or bare ``norse_research``, which would
  reach every module the runner has loaded (security review round 3, E1 and E2);
* a private attribute is read or written only on the first parameter, ``self`` or
  ``cls``, of the method it sits in, a method defined directly in a class body; those
  names are bound nowhere else; a class's own methods are not reached through the class
  (``C.m(x)``); and no dunder, nor a private name from outside the strategy's own paths,
  is imported by name;
* the pinned module declares ``CONFIGURATIONS``, ``PARAMETERS`` and ``DEFAULTS`` as
  literals assigned once, and defines the pinned function at its top level once, so
  what enters the configuration hash is read from the committed text (M4, N1, N4).

Python cannot be sandboxed from inside itself: this stops accidents and makes evasion
visible in the diff, and the runner stands behind it, with its audit hook for files,
sockets, processes, frames and the garbage collector, and by sealing the engine, so
that an engine module, class or table changed through a name this check cannot follow
fails the run. What a string can name is not parsed here: ``str.format`` field names
walk attributes, dunders and closure cells included, and read what they reach (round 4,
L1), which can show the rows a strategy already holds but not change the run.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Any

from .ledger import LedgerError

# What strategies need, and nothing that reaches more (security review of PR 61, round
# 4, H2): ``enum`` held the builtins module as ``enum.bltns``, ``typing`` and
# ``functools`` evaluate string annotations, and ``fractions``, ``statistics`` and
# ``dataclasses`` reached those or ``types`` and ``random``. ``math`` and ``datetime``
# stay because the walk in test_strategy_reach.py finds nothing sensitive through them;
# that test judges any new entry by what it reaches, not by its name.
ALLOWED_IMPORTS = frozenset(
    {
        "__future__", "collections.abc", "datetime", "math",
        "norse_research.formats",
        # The simulation engine (G1.9). H1's and H2's strategy paths may not overlap
        # (genesis, review N12), so the engine both share sits in the harness's paths
        # and is reached through this one entry. It opens no file, socket or process.
        "norse_research.sim",
    }
)
# Refused wherever the name appears, called or not (security review of PR 61, round 2):
# ``s = setattr``, ``map(setattr, ...)`` or ``imp = __import__`` would otherwise undo
# every other rule. ``type`` and ``__build_class__`` build a class at run time (round 4,
# A2). A name alone does not keep a builtin out of reach: round 4 found the builtins
# module as ``enum.bltns`` and in every frame's ``f_builtins``, so the allowlist is held
# to what test_strategy_reach.py's walk shows harmless, and frames are refused below
# and by the runner's audit hook.
FORBIDDEN_BUILTINS = frozenset(
    {"open", "__import__", "eval", "exec", "compile", "breakpoint", "input", "getattr",
     "setattr", "delattr", "vars", "globals", "locals", "type", "__build_class__"}
)
# ``mro`` is the one way to a class's bases without a dunder; the bases of the archive's
# row types (``formats.Kline``, ``formats.Funding``) are not sealed engine classes.
# ``update_wrapper``, ``wraps`` and ``global_enum`` take attribute or module names as
# strings and write into what they find: ``update_wrapper(f, x, (), ("__globals__",))``
# rewrites the namespace of the module ``f`` was defined in, which no run-time seal can
# stop, since a function's globals are that namespace itself. The frame and code names
# reach every caller's frame from a generator, coroutine or traceback, and through it
# the engine's live run, its locals and the runner's own state (round 4, M1); the runner's
# audit hook refuses the four ways in to a frame as well, before any of them returns.
FRAME_NAMES = frozenset(
    {"gi_frame", "cr_frame", "ag_frame", "tb_frame", "f_back", "f_locals", "f_globals",
     "f_builtins", "f_code", "gi_code", "cell_contents"}
)
# Helpers that build source text from names a strategy chooses and compile it, as
# ``dataclass`` does for ``__eq__`` from each field name (security review round 5, B-H1:
# one ``__annotations__`` entry replaced the fill price). The runner also refuses every
# compile of text that is not a file once the engine is sealed.
COMPILING_HELPERS = frozenset(
    {"dataclass", "field", "make_dataclass", "namedtuple", "NamedTuple"}
)
FORBIDDEN_NAMES = frozenset(
    {"sys", "modules", "os", "io", "builtins", "importlib", "subprocess", "socket",
     "pathlib", "shutil", "inspect", "attrgetter", "methodcaller", "gc", "ctypes", "mro",
     "update_wrapper", "wraps", "global_enum"}
) | FRAME_NAMES | COMPILING_HELPERS
DECLARED = ("CONFIGURATIONS", "PARAMETERS", "DEFAULTS")


class StrategySourceRefused(LedgerError, PermissionError):
    """A strategy's committed source fails the checks made before anything runs."""


def inside(path: str, roots: Sequence[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def module_name_for(rel_path: str) -> str:
    """The dotted name a strategy file at ``rel_path`` is loaded under."""
    stem = rel_path.removesuffix(".py").removesuffix("/__init__")
    if stem.startswith("research/"):
        stem = stem.removeprefix("research/")
    return stem.replace("/", ".")


def _module_paths(name: str) -> list[str]:
    stem = name.replace(".", "/")
    return [f"research/{stem}.py", f"research/{stem}/__init__.py", f"{stem}.py",
            f"{stem}/__init__.py"]


def disallowed_imports(
    source: str, module: str, is_package: bool = False, allowed_paths: Sequence[str] = ()
) -> list[str]:
    """What a strategy module's source reaches outside the allowlist."""

    def allowed(name: str) -> bool:
        return name in ALLOWED_IMPORTS or any(
            inside(p, allowed_paths) for p in _module_paths(name)
        )

    problems = []
    package = module if is_package else module.rpartition(".")[0]
    tree = ast.parse(source)
    imported = _imported_names(tree, package)
    problems += _engine_tampering(tree, imported)
    problems += _private_reach(tree)
    problems += _class_calls(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {a.name}" for a in node.names if not allowed(a.name)
            ]
            # ``import a.b`` binds ``a``, the package, not ``a.b``: ``import collections.abc``
            # hands over ``collections.namedtuple`` (security review round 5, B-L1). For an
            # allowlisted module the package must be allowed itself, or the name bound with
            # ``as``, or the module imported from (``from collections.abc import Mapping``).
            # A strategy's own modules are its own tree, as before (review N12).
            problems += [
                f"line {node.lineno}: import {a.name} binds {a.name.split('.')[0]}; use "
                f"from {a.name} import ..."
                for a in node.names
                if a.asname is None and a.name in ALLOWED_IMPORTS and "." in a.name
                and not allowed(a.name.split(".")[0])
            ]
        elif isinstance(node, ast.ImportFrom):
            base = _resolved_base(node, package)
            if not allowed(base) and not all(allowed(f"{base}.{a.name}") for a in node.names):
                problems.append(f"line {node.lineno}: from {base or '.'} import ...")
            problems += [
                f"line {node.lineno}: import of {a.name}"
                for a in node.names if a.name in FORBIDDEN_NAMES
            ]
            # A dunder from any module (``from collections import __builtins__`` hands
            # over the builtins, ``exec`` among them), and a private name from any module
            # outside the strategy's own paths, the same reach as ``module._x``.
            own = any(inside(p, allowed_paths) for p in _module_paths(base))
            problems += [
                f"line {node.lineno}: import of private name {a.name}"
                for a in node.names
                if a.name.startswith("__") or (a.name.startswith("_") and not own)
            ]
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS:
            problems.append(f"line {node.lineno}: builtin {node.id}, called or not")
        elif isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") or node.attr in FORBIDDEN_NAMES
        ):
            problems.append(f"line {node.lineno}: attribute {node.attr}")
        elif isinstance(node, ast.Name) and (
            (node.id.startswith("__") and node.id != "__name__") or node.id in FORBIDDEN_NAMES
        ):
            # Every bare dunder but ``__name__``: ``__builtins__`` hands over the builtins,
            # and ``__annotations__`` in a class body chose the text ``dataclass`` compiled
            # (security review round 5, B-H1).
            problems.append(f"line {node.lineno}: name {node.id}")
    return problems


PACKAGE = "norse_research"


def _resolved_base(node: ast.ImportFrom, package: str) -> str:
    base = node.module or ""
    if node.level:
        parts = package.split(".") if package else []
        parent = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        base = ".".join([*parent, *([node.module] if node.module else [])])
    return base


def _imported_names(tree: ast.AST, package: str) -> dict[str, str]:
    """Every name an import in the module binds, with the module it came from."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolved_base(node, package)
            for alias in node.names:
                names[alias.asname or alias.name] = f"{base}.{alias.name}"
    return names


def _root(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _engine_tampering(tree: ast.AST, imported: dict[str, str]) -> list[str]:
    """What would change, or reach behind, code the strategy imports (security review
    round 3, E1 and E2).

    * Assigning to or deleting an attribute or item of an imported name, as in
      ``execution._apply = ...`` or ``model.Costs.total = 0.0``, would let one line in a
      hypothesis file change the shared engine's fills, costs or funding for its run
      while the ledger certifies the registered rules (E1).
    * Subclassing a class imported from this package would let a strategy hand the
      engine an altered rule object; the engine's interface needs no subclass (E1).
    * ``import norse_research.<x>`` binds the package root, whose attributes include
      every module the runner has already imported (the loader, the store, the runner
      itself), and the bare name ``norse_research`` reaches them; strategies use
      ``from norse_research.<x> import ...`` instead (E2).
    """
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {a.name} binds the package root; use "
                f"from {a.name} import ..."
                for a in node.names if a.name == PACKAGE or a.name.startswith(PACKAGE + ".")
            ]
        elif isinstance(node, ast.Name) and node.id == PACKAGE:
            problems.append(f"line {node.lineno}: name {PACKAGE}")
        elif isinstance(node, ast.Attribute | ast.Subscript):
            root = _root(node)
            if root not in imported:
                continue
            if isinstance(node.ctx, ast.Store | ast.Del):
                problems.append(f"line {node.lineno}: changes {root}, which is imported")
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                root = _root(base)
                if root in imported and imported[root].split(".")[0] == PACKAGE:
                    problems.append(
                        f"line {node.lineno}: class {node.name} subclasses {root}, imported "
                        "from this package"
                    )
    return problems


SELF_NAMES = frozenset({"self", "cls"})
# The named-tuple API, public despite its underscore (it keeps clear of field names):
# each builds a new object or reads a constant, so reading one changes nothing.
NAMED_TUPLE_API = frozenset({"_asdict", "_field_defaults", "_fields", "_make", "_replace"})


def _is_staticmethod(decorator: ast.expr) -> bool:
    return (isinstance(decorator, ast.Name) and decorator.id == "staticmethod") or (
        isinstance(decorator, ast.Attribute) and decorator.attr == "staticmethod"
    )


def _private_reach(tree: ast.AST) -> list[str]:
    """A private attribute is read or written only on ``self`` or ``cls`` (security
    review of PR 61, closing the residual stated in round 2). An object the engine lends
    a strategy keeps its state behind private attributes, the book behind a
    ``BookView`` or the rows behind a ``SeriesView``, and a name cannot say whose object
    it holds: ``view._book.cash = 1e9`` and ``v = view; v._book`` are refused alike,
    while a strategy's own ``self._x`` is not. So that ``self`` and ``cls`` mean a
    method's own object, they may be bound only as the first parameter of a method
    defined in a class body (not a static method): never assigned, deleted, imported,
    caught, matched or declared global, and never another parameter or a lambda's.
    Reading the named-tuple API (``row._replace``, ``Kline._fields``) is not private."""
    firsts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and not any(
                    _is_staticmethod(d) for d in item.decorator_list
                ):
                    params = [*item.args.posonlyargs, *item.args.args]
                    if params:
                        firsts.add(id(params[0]))
    problems = _private_attributes(tree)
    for node in ast.walk(tree):
        bound: list[str] = []
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound = [node.id]
        elif isinstance(node, ast.arg) and id(node) not in firsts:
            bound = [node.arg]
        elif isinstance(node, ast.alias):
            bound = [node.asname or node.name.split(".")[0]]
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            bound = [node.name] if node.name else []
        elif isinstance(node, ast.MatchMapping):
            bound = [node.rest] if node.rest else []
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bound = list(node.names)
        problems += [
            f"line {node.lineno}: {name} bound other than as a method's first parameter"
            for name in bound if name in SELF_NAMES
        ]
    return problems


def _private_attributes(tree: ast.AST) -> list[str]:
    """Each private attribute read or written other than on the first parameter of the
    method it sits in, a method defined directly in a class body of this file (security
    review of PR 61, the rule at its root). A function defined at module level, or
    anywhere outside a class body, does not qualify, whatever its parameter is called;
    a function or lambda nested inside a method sees that method's parameter."""
    problems: list[str] = []

    def visit(node: ast.AST, allowed: frozenset[str], class_body: bool) -> None:
        if isinstance(node, ast.ClassDef):
            for child in [*node.bases, *node.keywords, *node.decorator_list]:
                visit(child, allowed, False)
            for child in node.body:
                visit(child, allowed, True)
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in [*node.decorator_list, node.args]:
                visit(child, allowed, False)
            params = [*node.args.posonlyargs, *node.args.args]
            method = class_body and params and not any(
                _is_staticmethod(d) for d in node.decorator_list)
            inner = frozenset({params[0].arg}) if method else allowed
            for child in node.body:
                visit(child, inner, False)
            return
        if isinstance(node, ast.Attribute):
            private = node.attr.startswith("_") and not node.attr.startswith("__")
            api = node.attr in NAMED_TUPLE_API and isinstance(node.ctx, ast.Load)
            mine = isinstance(node.value, ast.Name) and node.value.id in allowed & SELF_NAMES
            if private and not api and not mine:
                problems.append(
                    f"line {node.lineno}: private attribute {node.attr} other than on the "
                    "first parameter of the method it sits in"
                )
        for child in ast.iter_child_nodes(node):
            visit(child, allowed, False)

    visit(tree, frozenset(), False)
    return problems


def _class_calls(tree: ast.AST) -> list[str]:
    """Each plain method of a class defined in this file reached through the class,
    ``C.m(x)`` or ``f = C.m``, which would bind ``self`` to whatever is passed (security
    review of PR 61): a method is called on an instance, ``self.m()`` or ``C().m()``.
    Class and static methods, and class attributes that are not methods, stay
    reachable; a method inherited from another class of this file counts as the
    class's own."""
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    def plain_methods(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name not in classes or name in seen:
            return set()
        node = classes[name]
        own = {
            item.name for item in node.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and not any(_is_staticmethod(d) or (isinstance(d, ast.Name) and d.id == "classmethod")
                        for d in item.decorator_list)
        }
        for base in node.bases:
            if isinstance(base, ast.Name):
                own |= plain_methods(base.id, seen | {name})
        return own

    methods = {name: plain_methods(name) for name in classes}
    return [
        f"line {node.lineno}: method {node.attr} reached through class {node.value.id}, which "
        "would bind self to whatever is passed"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.attr in methods.get(node.value.id, ())
    ]


def strategy_declarations(source: str, function: str) -> tuple[frozenset, frozenset, dict]:
    """``CONFIGURATIONS``, ``PARAMETERS`` and ``DEFAULTS`` as literals at the top of the
    module, read without executing it, and the pinned function defined there once."""
    tree = ast.parse(source)
    found: dict[str, Any] = {}
    defined = 0
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names = [node.name]
            defined += node.name == function and isinstance(node, ast.FunctionDef)
        for name in names:
            if name == function and not isinstance(node, ast.FunctionDef):
                raise StrategySourceRefused(f"{function} is bound by something other than its def")
            if name in DECLARED:
                if name in found or not isinstance(node, ast.Assign) or len(targets) != 1:
                    raise StrategySourceRefused(f"{name} must be assigned once, as a literal")
                try:
                    found[name] = ast.literal_eval(node.value)
                except ValueError:
                    raise StrategySourceRefused(f"{name} must be a literal") from None
    if defined != 1:
        raise StrategySourceRefused(f"the module defines no function {function} at its top level")
    if "CONFIGURATIONS" not in found or "PARAMETERS" not in found:
        raise StrategySourceRefused("a strategy module declares CONFIGURATIONS and PARAMETERS")
    defaults = found.get("DEFAULTS", {})
    if not isinstance(defaults, dict):
        raise StrategySourceRefused("DEFAULTS is a dict literal")
    return frozenset(found["CONFIGURATIONS"]), frozenset(found["PARAMETERS"]), defaults


def check_strategy_sources(git, commit: str, paths: Sequence[str]) -> None:
    """Every module under a hypothesis's strategy ``paths`` at ``commit`` passes."""
    listing = git.text("ls-tree", "-r", "--name-only", "-z", commit, "--", *paths)
    for rel in sorted(p for p in listing.split("\0") if p.endswith(".py")):
        problems = disallowed_imports(
            git.blob(commit, rel).decode(), module_name_for(rel), rel.endswith("__init__.py"),
            paths,
        )
        if problems:
            raise StrategySourceRefused(f"{rel} reaches outside the strategy allowlist: {problems}")
