"""What a strategy can reach by the attribute paths the source check accepts (security
review of PR 61, round 4, H2).

The check refuses names, so a module on the allowlist is only as safe as everything a
plain attribute path reaches from it: ``enum.bltns`` was the builtins module, and
``typing`` and ``functools`` evaluated string annotations. This walk starts from every
allowlisted module and every engine module (whose public names a strategy can read),
follows every attribute name the check accepts on an imported object (no leading
underscore, no forbidden name), descends into modules, classes and other objects four
levels deep, and fails on any path to the builtins module or a forbidden builtin, to a
frame or a code object, to a module that reaches those (``sys``, ``gc``, ``types``,
``inspect``, ``typing``, ``functools``, ``copy``), or to a helper that evaluates text or
writes by name. A new allowlist entry is judged by what it reaches, not by its name.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types

from norse_research import sources

SENSITIVE_MODULES = frozenset({
    "builtins", "gc", "sys", "types", "typing", "functools", "inspect", "copy", "importlib",
    "os", "posix", "io", "_io", "marshal", "ctypes", "subprocess", "socket", "pathlib",
    "shutil", "threading", "_thread", "weakref", "enum", "dataclasses", "zipfile", "tempfile",
    "glob", "codecs", "warnings", "contextlib", "re", "random", "pickle", "operator",
    "traceback", "linecache", "ast", "code", "pdb", "runpy",
})
SENSITIVE_BUILTINS = {
    id(getattr(builtins, name)): name
    for name in ("eval", "exec", "compile", "getattr", "setattr", "delattr", "open",
                 "__import__", "vars", "globals", "locals", "breakpoint", "input",
                 "__build_class__")
}
# Helpers that evaluate text, write by name, or hand out a class the engine does not own.
SENSITIVE_NAMES = frozenset({
    "get_type_hints", "singledispatch", "singledispatchmethod", "ForwardRef", "update_wrapper",
    "wraps", "global_enum", "attrgetter", "methodcaller", "literal_eval", "ZipFile", "BytesIO",
    "BadZipFile", "make_dataclass", "bltns",
})
DEPTH = 4


def accepted(name: str) -> bool:
    return not (name.startswith("_") or name in sources.FORBIDDEN_NAMES
                or name in sources.FORBIDDEN_BUILTINS)


def roots() -> list[tuple[str, object]]:
    importlib.import_module("norse_research.sim")
    names = sorted(sources.ALLOWED_IMPORTS)
    names += sorted(n for n in sys.modules if n.startswith("norse_research.sim."))
    return [(name, importlib.import_module(name)) for name in names]


def walk() -> dict[str, str]:
    """Every sensitive object reached, with the shortest accepted path to it."""
    found: dict[str, str] = {}
    seen: set[int] = set()
    queue: list[tuple[object, str, int]] = []
    for name, module in roots():
        seen.add(id(module))
        queue.append((module, name, 0))
    while queue:
        obj, path, depth = queue.pop(0)
        for attr in sorted(dir(obj)):
            if not accepted(attr):
                continue
            try:
                value = getattr(obj, attr)
            except Exception:
                continue
            here = f"{path}.{attr}"
            if isinstance(value, types.ModuleType) and value.__name__ in SENSITIVE_MODULES:
                found.setdefault(f"module {value.__name__}", here)
            if id(value) in SENSITIVE_BUILTINS:
                found.setdefault(f"builtin {SENSITIVE_BUILTINS[id(value)]}", here)
            if isinstance(value, types.FrameType | types.CodeType | types.TracebackType):
                found.setdefault(f"{type(value).__name__}", here)
            if attr in SENSITIVE_NAMES:
                found.setdefault(f"name {attr}", here)
            descend = isinstance(value, types.ModuleType | type) or not callable(value)
            if descend and depth + 1 < DEPTH and id(value) not in seen:
                seen.add(id(value))
                queue.append((value, here, depth + 1))
    return found


def test_no_accepted_attribute_path_reaches_the_builtins_eval_frames_or_their_modules():
    assert walk() == {}


def test_the_allowlist_is_what_strategies_need():
    """#62's strategies import ``__future__``, ``collections.abc`` and the engine; the
    example strategies ``math``; the harness tests' synthetic ones ``datetime``."""
    assert sources.ALLOWED_IMPORTS == {"__future__", "collections.abc", "datetime", "math",
                                       "norse_research.formats", "norse_research.sim"}


def test_the_walk_finds_what_round_4_found_before_the_fix():
    """The walk has teeth: with the modules round 4 removed back on the list, it finds
    the routes the review reported."""
    saved = sources.ALLOWED_IMPORTS
    try:
        sources.ALLOWED_IMPORTS = saved | {"enum", "typing", "functools", "fractions",
                                           "dataclasses", "statistics"}
        found = walk()
    finally:
        sources.ALLOWED_IMPORTS = saved
    assert found.get("module builtins", "").startswith("enum.bltns"), found
    assert "name get_type_hints" in found and "name singledispatch" in found, found
    assert "module types" in found, found
