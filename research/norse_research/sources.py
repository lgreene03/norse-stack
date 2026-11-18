"""Strategy sources, checked by parsing alone, before anything runs (G1.5; T13).

A strategy module is never executed until its run's open entry is pushed, and then only
in a fresh interpreter (``norse_research.runner``). Before that, the harness and the
genesis step read it as text:

* every module under a hypothesis's strategy paths may import only the allowlist and
  modules inside that hypothesis's own paths (security review N12), may not call
  ``open``, ``eval``, ``getattr`` and their kind, and may not touch a dunder attribute or
  a name such as ``sys``, ``modules`` or ``attrgetter`` through which a module it may not
  import could be reached (L4, N9);
* the pinned module declares ``CONFIGURATIONS``, ``PARAMETERS`` and ``DEFAULTS`` as
  literals assigned once, and defines the pinned function at its top level once, so
  what enters the configuration hash is read from the committed text (M4, N1, N4).

Python cannot be sandboxed from inside itself: this stops accidents and makes evasion
visible in the diff, and the runner's audit hook stands behind it.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Any

from .ledger import LedgerError

ALLOWED_IMPORTS = frozenset(
    {
        "__future__", "abc", "bisect", "collections", "collections.abc", "dataclasses",
        "datetime", "decimal", "enum", "fractions", "functools", "heapq", "itertools",
        "math", "numbers", "operator", "statistics", "typing",
        "norse_research.formats",
        # The simulation engine (G1.9). H1's and H2's strategy paths may not overlap
        # (genesis, review N12), so the engine both share sits in the harness's paths
        # and is reached through this one entry. It opens no file, socket or process.
        "norse_research.sim",
    }
)
FORBIDDEN_CALLS = frozenset(
    {"open", "__import__", "eval", "exec", "compile", "breakpoint", "input", "getattr",
     "setattr", "delattr", "vars", "globals", "locals"}
)
FORBIDDEN_NAMES = frozenset(
    {"sys", "modules", "os", "io", "builtins", "importlib", "subprocess", "socket",
     "pathlib", "shutil", "inspect", "attrgetter", "methodcaller", "gc", "ctypes"}
)
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
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {a.name}" for a in node.names if not allowed(a.name)
            ]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parent = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parent, *([node.module] if node.module else [])])
            if not allowed(base) and not all(allowed(f"{base}.{a.name}") for a in node.names):
                problems.append(f"line {node.lineno}: from {base or '.'} import ...")
            problems += [
                f"line {node.lineno}: import of {a.name}"
                for a in node.names if a.name in FORBIDDEN_NAMES
            ]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            problems.append(f"line {node.lineno}: call to {node.func.id}()")
        elif isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") or node.attr in FORBIDDEN_NAMES
        ):
            problems.append(f"line {node.lineno}: attribute {node.attr}")
        elif isinstance(node, ast.Name) and (
            node.id in ("__builtins__", "__loader__", "__spec__") or node.id in FORBIDDEN_NAMES
        ):
            problems.append(f"line {node.lineno}: name {node.id}")
    return problems


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
